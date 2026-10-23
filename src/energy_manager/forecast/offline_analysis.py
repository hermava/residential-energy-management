"""Build leak-free hourly forecast/measurement pairs for offline analysis."""

import csv
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from energy_manager.forecast.forecast import estimate_battery_input_power_w_from_irradiation
from energy_manager.forecast.power_forecast import PvPowerModel
from energy_manager.forecast.solar_geometry import calculate_solar_geometry_features

OFFLINE_MAX_GAP_SECONDS = 60.0

PAIR_FIELDS = (
    "target_start", "target_end", "forecast_at", "forecast_lead_hours",
    "solar_irradiation_j_m2", "sunshine_seconds", "cloud_cover_percent",
    "solar_altitude_deg", "solar_azimuth_deg", "panel_incidence_cosine",
    "day_of_year_sin", "day_of_year_cos", "actual_battery_input_wh",
)
MODEL_FEATURES = (
    "solar_irradiation_j_m2", "sunshine_seconds", "cloud_cover_percent",
    "solar_altitude_deg", "panel_incidence_cosine", "day_of_year_sin",
    "day_of_year_cos", "forecast_lead_hours",
)
BASELINE_WH_PER_J_M2 = 1000 * 0.8515 / 3_600_000


@dataclass(frozen=True)
class ModelEvaluation:
    status: str
    train_rows: int
    test_rows: int
    baseline_mae_wh: float | None = None
    feature_model_mae_wh: float | None = None
    baseline_rmse_wh: float | None = None
    feature_model_rmse_wh: float | None = None


@dataclass(frozen=True)
class PowerModelScore:
    mae_w: float
    rmse_w: float
    bias_w: float
    active_mae_w: float | None
    model: PvPowerModel | None = None


@dataclass(frozen=True)
class PowerModelComparison:
    status: str
    train_rows: int
    test_rows: int
    train_days: int
    test_days: int
    legacy_hourly: PowerModelScore | None = None
    irradiance_only: PowerModelScore | None = None
    irradiance_geometry: PowerModelScore | None = None


@dataclass(frozen=True)
class _PowerSample:
    start: datetime
    end: datetime
    irradiance_w_m2: float
    incidence_cosine: float
    actual_mean_w: float


def _power_samples(rows: list[dict]) -> list[_PowerSample]:
    samples = []
    for row in rows:
        try:
            start = datetime.fromisoformat(row["target_start"])
            end = datetime.fromisoformat(row["target_end"])
            if start.utcoffset() is None or end.utcoffset() is None:
                continue
            start, end = start.astimezone(UTC), end.astimezone(UTC)
            seconds = (end - start).total_seconds()
            irradiation = float(row["solar_irradiation_j_m2"])
            incidence = float(row["panel_incidence_cosine"])
            actual_wh = float(row["actual_battery_input_wh"])
            if "forecast_at" in row:
                retrieved = datetime.fromisoformat(row["forecast_at"])
                if retrieved.utcoffset() is None or retrieved > start:
                    continue
        except (KeyError, TypeError, ValueError):
            continue
        values = (seconds, irradiation, incidence, actual_wh)
        if not all(math.isfinite(value) for value in values):
            continue
        if seconds <= 0 or irradiation < 0 or actual_wh < 0 or not -1 <= incidence <= 1:
            continue
        samples.append(_PowerSample(
            start, end, irradiation / seconds, max(0.0, incidence), actual_wh * 3600 / seconds,
        ))
    return sorted(samples, key=lambda sample: sample.start)


def _fit_power_samples(
    samples: list[_PowerSample], include_geometry: bool, max_power_w: float | None,
) -> PvPowerModel | None:
    """Solve nonnegative least squares with two features, without a night-time intercept."""
    scale = max((sample.irradiance_w_m2 for sample in samples), default=0.0)
    if scale == 0:
        return None
    features = []
    for sample in samples:
        x = sample.irradiance_w_m2 / scale
        z = x * sample.incidence_cosine if include_geometry else 0
        features.append((x, z, sample.actual_mean_w))
    xx = math.fsum(x * x for x, _, _ in features)
    xz = math.fsum(x * z for x, z, _ in features)
    zz = math.fsum(z * z for _, z, _ in features)
    xy = math.fsum(x * y for x, _, y in features)
    zy = math.fsum(z * y for _, z, y in features)
    candidates = [(0.0, 0.0), (xy / xx, 0.0)]
    if zz > 0:
        candidates.append((0.0, zy / zz))
    determinant = xx * zz - xz * xz
    if determinant > 1e-12 * xx * zz:
        base = (xy * zz - zy * xz) / determinant
        incidence = (zy * xx - xy * xz) / determinant
        if base >= 0 and incidence >= 0:
            candidates.append((base, incidence))
    base, incidence = min(candidates, key=lambda pair: math.fsum(
        (pair[0] * x + pair[1] * z - y) ** 2 for x, z, y in features
    ))
    return PvPowerModel(base / scale, incidence / scale, max_power_w)


def fit_power_model(
    rows: list[dict], include_geometry: bool = True, max_power_w: float | None = None,
) -> PvPowerModel | None:
    """Fit normalized interval pairs; callers decide how much training data is sufficient."""
    PvPowerModel(0, 0, max_power_w)
    return _fit_power_samples(_power_samples(rows), include_geometry, max_power_w)


def _score_power_model(
    samples: list[_PowerSample], model: PvPowerModel | None, max_power_w: float | None,
) -> PowerModelScore:
    predicted, actual, active_errors = [], [], []
    for sample in samples:
        power = (
            estimate_battery_input_power_w_from_irradiation(sample.irradiance_w_m2 * 3600)
            if model is None else model.predict(sample.irradiance_w_m2, sample.incidence_cosine)
        )
        if max_power_w is not None:
            power = min(power, max_power_w)
        predicted.append(power)
        actual.append(sample.actual_mean_w)
        if sample.irradiance_w_m2 > 0 or sample.actual_mean_w > 0:
            active_errors.append(abs(power - sample.actual_mean_w))
    bias = math.fsum(p - a for p, a in zip(predicted, actual)) / len(actual)
    active_mae = math.fsum(active_errors) / len(active_errors) if active_errors else None
    return PowerModelScore(
        _mae(actual, predicted), _rmse(actual, predicted), bias, active_mae, model,
    )


def evaluate_power_models(
    rows: list[dict], minimum_training_rows: int = 30, holdout_fraction: float = 0.25,
    timezone_name: str = "Europe/Berlin", max_power_w: float | None = None,
) -> PowerModelComparison:
    """Compare models on the same later local days; never split a target day across sets."""
    if minimum_training_rows < 1 or not 0 < holdout_fraction < 1:
        raise ValueError("invalid power model evaluation settings")
    PvPowerModel(0, 0, max_power_w)
    zone = ZoneInfo(timezone_name)
    samples = _power_samples(rows)
    days = sorted({sample.start.astimezone(zone).date() for sample in samples})
    test_days = max(1, math.ceil(len(days) * holdout_fraction))
    cutoff = days[-test_days] if days else None
    boundary = datetime.combine(cutoff, datetime.min.time(), zone).astimezone(UTC) if days else None
    train = [sample for sample in samples if sample.end <= boundary]
    test = [sample for sample in samples if sample.start.astimezone(zone).date() >= cutoff]
    counts = (len(train), len(test), len(days) - test_days if days else 0, test_days if days else 0)
    if len(train) < minimum_training_rows or not test:
        return PowerModelComparison("insufficient_training_data", *counts)
    baseline = _fit_power_samples(train, False, max_power_w)
    geometry = _fit_power_samples(train, True, max_power_w)
    if baseline is None or geometry is None:
        return PowerModelComparison("insufficient_irradiation", *counts)
    return PowerModelComparison(
        "available", *counts, _score_power_model(test, None, max_power_w),
        _score_power_model(test, baseline, max_power_w),
        _score_power_model(test, geometry, max_power_w),
    )


def integrate_power_interval(
    samples: list[tuple[datetime, float]], start: datetime, end: datetime,
    max_gap_seconds: float = OFFLINE_MAX_GAP_SECONDS,
) -> float | None:
    """Trapezoid-integrate a fully covered interval; reject gaps and bad values."""
    ordered = sorted(samples)
    energy_wh = 0.0
    cursor = start
    for (left_at, left_w), (right_at, right_w) in zip(ordered, ordered[1:]):
        if right_at <= start or left_at >= end:
            continue
        gap = (right_at - left_at).total_seconds()
        if gap <= 0 or gap > max_gap_seconds or not all(
            map(_finite_nonnegative, (left_w, right_w))
        ):
            return None
        segment_start = max(left_at, start)
        segment_end = min(right_at, end)
        if segment_end <= segment_start:
            continue
        if segment_start > cursor + timedelta(seconds=1):
            return None
        start_fraction = (segment_start - left_at).total_seconds() / gap
        end_fraction = (segment_end - left_at).total_seconds() / gap
        start_power = left_w + start_fraction * (right_w - left_w)
        end_power = left_w + end_fraction * (right_w - left_w)
        energy_wh += (
            (start_power + end_power) / 2
            * (segment_end - segment_start).total_seconds() / 3600
        )
        cursor = segment_end
        if cursor >= end - timedelta(seconds=1):
            return energy_wh
    return None


def build_hourly_training_rows(
    forecast_paths: list[Path], measurement_paths: list[Path],
    max_gap_seconds: float = OFFLINE_MAX_GAP_SECONDS, latitude_deg: float | None = None,
    longitude_deg: float | None = None, panel_azimuth_deg: float | None = None,
    panel_tilt_deg: float | None = None,
) -> list[dict[str, str | float]]:
    forecasts = _load_forecasts(
        forecast_paths, latitude_deg, longitude_deg, panel_azimuth_deg, panel_tilt_deg,
    )
    measurements = _load_measurements(measurement_paths)
    rows = []
    for target_start, candidates in sorted(forecasts.items()):
        available = [row for row in candidates if row["retrieved_at"] <= target_start]
        if not available:
            continue
        forecast = max(available, key=lambda row: row["retrieved_at"])
        target_end = target_start + timedelta(hours=1)
        actual_wh = integrate_power_interval(
            measurements, target_start, target_end, max_gap_seconds,
        )
        if actual_wh is None:
            continue
        row = {
            "target_start": target_start.isoformat(),
            "target_end": target_end.isoformat(),
            "forecast_at": forecast["retrieved_at"].isoformat(),
            "forecast_lead_hours": (target_start - forecast["retrieved_at"]).total_seconds() / 3600,
            "actual_battery_input_wh": actual_wh,
        }
        row.update({field: forecast.get(field, "") for field in PAIR_FIELDS[4:-1]})
        rows.append(row)
    return rows


def evaluate_hourly_feature_model(
    rows: list[dict], minimum_training_rows: int = 30, holdout_fraction: float = 0.25,
    ridge: float = 1.0,
) -> ModelEvaluation:
    if minimum_training_rows < 2 or not 0 < holdout_fraction < 1 or ridge < 0:
        raise ValueError("invalid offline model evaluation settings")
    complete_rows = [row for row in rows if all(_finite(row.get(name)) for name in MODEL_FEATURES)
                     and _finite(row.get("actual_battery_input_wh"))]
    complete_rows.sort(key=lambda row: datetime.fromisoformat(row["target_start"]))
    split = int(len(complete_rows) * (1 - holdout_fraction))
    train, test = complete_rows[:split], complete_rows[split:]
    if len(train) < minimum_training_rows or not test:
        return ModelEvaluation("insufficient_training_data", len(train), len(test))
    means = [sum(float(row[name]) for row in train) / len(train) for name in MODEL_FEATURES]
    scales = []
    for name, mean in zip(MODEL_FEATURES, means):
        variance = sum((float(row[name]) - mean) ** 2 for row in train) / len(train)
        scales.append(math.sqrt(variance) or 1.0)
    train_x = [[1.0] + [
        (float(row[name]) - mean) / scale
        for name, mean, scale in zip(MODEL_FEATURES, means, scales)
    ] for row in train]
    train_y = [float(row["actual_battery_input_wh"]) for row in train]
    coefficients = _ridge_fit(train_x, train_y, ridge)
    model_predictions = [_dot(coefficients, [1.0] + [
        (float(row[name]) - mean) / scale
        for name, mean, scale in zip(MODEL_FEATURES, means, scales)
    ]) for row in test]
    actual = [float(row["actual_battery_input_wh"]) for row in test]
    baseline = [max(
        0.0, 40.4 + float(row["solar_irradiation_j_m2"]) * BASELINE_WH_PER_J_M2,
    ) for row in test]
    return ModelEvaluation(
        "available", len(train), len(test), _mae(actual, baseline),
        _mae(actual, model_predictions), _rmse(actual, baseline), _rmse(actual, model_predictions),
    )


def write_training_rows(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=PAIR_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def find_training_input_files(data_dir: Path) -> tuple[list[Path], list[Path]]:
    """Find legacy and organized logs; analysis exports are not scanned."""
    forecasts, measurements = [], []
    for folder in (data_dir, data_dir / "logs" / "forecast", data_dir / "logs" / "marstek"):
        forecasts.extend(folder.glob("brightsky_forecast_*.csv"))
        for pattern in ("marstek_status_closed_loop_V5_*.csv", "pv_input_history_*.csv"):
            measurements.extend(folder.glob(pattern))
    return sorted(set(forecasts)), sorted(set(measurements))


def _load_forecasts(
    paths: list[Path], latitude_deg: float | None, longitude_deg: float | None,
    panel_azimuth_deg: float | None, panel_tilt_deg: float | None,
) -> dict[datetime, list[dict]]:
    grouped: dict[datetime, list[dict]] = {}
    for path in paths:
        with path.open(newline="", encoding="utf-8") as file:
            for row in csv.DictReader(file):
                try:
                    forecast_at = datetime.fromisoformat(row["forecast_at"])
                    retrieved_at = datetime.fromisoformat(row["retrieved_at"])
                    start = forecast_at - timedelta(hours=1)
                    irradiation = float(row["solar_irradiation_j_m2"])
                except (KeyError, TypeError, ValueError):
                    continue
                if not _finite_nonnegative(irradiation):
                    continue
                normalized = {key: _optional_number(row.get(key)) for key in PAIR_FIELDS[4:-1]}
                normalized.update({
                    "retrieved_at": retrieved_at,
                    "solar_irradiation_j_m2": irradiation,
                })
                if all(value is not None for value in (
                    latitude_deg, longitude_deg, panel_azimuth_deg, panel_tilt_deg,
                )):
                    midpoint = start + timedelta(minutes=30)
                    geometry = calculate_solar_geometry_features(
                        midpoint, latitude_deg, longitude_deg,
                        panel_azimuth_deg, panel_tilt_deg,
                    )
                    normalized.update({
                        "solar_altitude_deg": geometry.solar_altitude_deg,
                        "solar_azimuth_deg": geometry.solar_azimuth_deg,
                        "panel_incidence_cosine": max(0.0, geometry.panel_incidence_cosine),
                        "day_of_year_sin": geometry.day_of_year_sin,
                        "day_of_year_cos": geometry.day_of_year_cos,
                    })
                grouped.setdefault(start, []).append(normalized)
    return grouped


def _load_measurements(paths: list[Path]) -> list[tuple[datetime, float]]:
    samples = {}
    for path in paths:
        with path.open(newline="", encoding="utf-8") as file:
            for row in csv.DictReader(file):
                try:
                    timestamp = datetime.fromisoformat(row["timestamp"])
                    power = float(row["input_power_w"])
                except (KeyError, TypeError, ValueError):
                    continue
                if timestamp.utcoffset() is None:
                    continue
                timestamp = timestamp.astimezone(UTC)
                if timestamp in samples and samples[timestamp] != power:
                    if not (math.isnan(samples[timestamp]) and math.isnan(power)):
                        raise ValueError(
                            f"Conflicting input measurements at {timestamp.isoformat()}"
                        )
                samples[timestamp] = power
    return sorted(samples.items())


def _optional_number(value: str | None) -> float | str:
    if value in (None, ""):
        return ""
    try:
        number = float(value)
    except ValueError:
        return ""
    return number if _finite(number) else ""


def _finite_nonnegative(value: float) -> bool:
    return _finite(value) and value >= 0


def _finite(value: object) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _ridge_fit(features: list[list[float]], targets: list[float], ridge: float) -> list[float]:
    columns = len(features[0])
    matrix = [[sum(row[i] * row[j] for row in features) for j in range(columns)]
              for i in range(columns)]
    vector = [sum(row[i] * target for row, target in zip(features, targets))
              for i in range(columns)]
    for index in range(1, columns):
        matrix[index][index] += ridge
    for pivot in range(columns):
        divisor = matrix[pivot][pivot]
        if abs(divisor) < 1e-12:
            raise ValueError("offline model features are singular")
        for column in range(pivot, columns):
            matrix[pivot][column] /= divisor
        vector[pivot] /= divisor
        for row in range(columns):
            if row == pivot:
                continue
            factor = matrix[row][pivot]
            for column in range(pivot, columns):
                matrix[row][column] -= factor * matrix[pivot][column]
            vector[row] -= factor * vector[pivot]
    return vector


def _dot(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right))


def _mae(actual: list[float], predicted: list[float]) -> float:
    return sum(abs(a - p) for a, p in zip(actual, predicted)) / len(actual)


def _rmse(actual: list[float], predicted: list[float]) -> float:
    return math.sqrt(sum((a - p) ** 2 for a, p in zip(actual, predicted)) / len(actual))
