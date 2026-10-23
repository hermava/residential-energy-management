"""Offline forecast evaluation by lead time and fixed local planning time."""

import math
from dataclasses import asdict
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from energy_manager.forecast.forecast import estimate_battery_input_power_w_from_irradiation
from energy_manager.forecast.offline_analysis import (
    _fit_power_samples,
    _power_samples,
    _score_power_model,
    select_latest_forecasts,
)
from energy_manager.forecast.power_forecast import PvPowerModel, local_day_bounds

LEAD_BOUNDS_HOURS = ((0, 1), (1, 3), (3, 12), (12, 24), (24, 48), (48, math.inf))
MODEL_NAMES = ("legacy_hourly", "irradiance_only", "irradiance_geometry")


def _fit_before(
    rows: list[dict], cutoff: datetime, minimum_rows: int, max_power_w: float | None,
) -> tuple[dict[str, PvPowerModel | None], dict]:
    samples = [sample for sample in _power_samples(rows) if sample.end <= cutoff]
    models = {"legacy_hourly": None}
    status = "insufficient_training_data"
    if len(samples) >= minimum_rows:
        fitted = {
            "irradiance_only": _fit_power_samples(samples, False, max_power_w),
            "irradiance_geometry": _fit_power_samples(samples, True, max_power_w),
        }
        status = "available" if all(fitted.values()) else "insufficient_irradiation"
        models.update({name: model for name, model in fitted.items() if model is not None})
    return models, {
        "model_status": status, "train_rows": len(samples), "trained_through": cutoff.isoformat(),
        "parameters": {name: asdict(model) for name, model in models.items() if model is not None},
    }


def _hourly_group(
    rows: list[dict], training: list[dict], boundary: datetime, minimum_rows: int,
    max_power_w: float | None, lower: float, upper: float,
) -> dict:
    selected = [row for row in select_latest_forecasts(rows, lower, upper)
                if datetime.fromisoformat(row["target_start"]) >= boundary
                and _power_samples([row])]
    available_at = [datetime.fromisoformat(row["forecast_at"]) for row in selected]
    cutoff = min([boundary, *available_at])
    models, fit = _fit_before(training, cutoff, minimum_rows, max_power_w)
    samples = _power_samples(selected)
    scores = {name: None for name in MODEL_NAMES}
    if samples:
        scores.update({name: asdict(_score_power_model(samples, model, max_power_w))
                       for name, model in models.items()})
    return {
        "minimum_lead_hours": lower,
        "maximum_lead_hours": upper if math.isfinite(upper) else None,
        "status": "available" if samples else "no_test_intervals",
        "test_rows": len(samples), **fit, "scores": scores,
    }


def _decision_at(
    day: date, days_ahead: int, decision_time: time, zone: ZoneInfo,
) -> datetime | None:
    naive = datetime.combine(day - timedelta(days=days_ahead), decision_time)
    local = naive.replace(tzinfo=zone)
    stamp = local.astimezone(UTC)
    if stamp.astimezone(zone).replace(tzinfo=None) != naive:
        return None
    if local.replace(fold=1).utcoffset() != local.utcoffset():
        return None
    return stamp


def _daily_case(
    day: date, days_ahead: int, decision_time: time, zone: ZoneInfo,
    snapshots: dict, actuals: dict[datetime, float], maximum_age: float,
) -> tuple[dict, list[dict]]:
    start, end = local_day_bounds(day, zone)
    expected = (end - start).total_seconds() / 3600
    targets = [start + timedelta(hours=index) for index in range(math.ceil(expected))]
    decision = _decision_at(day, days_ahead, decision_time, zone)
    case = {
        "target_date": day.isoformat(), "target_start": start.isoformat(),
        "target_end": end.isoformat(), "decision_at": decision.isoformat() if decision else None,
        "forecast_at": None, "snapshot_age_hours": None, "expected_hours": expected,
        "covered_forecast_hours": 0,
        "covered_measurement_hours": sum(target in actuals for target in targets),
        "actual_kwh": None, "predicted_kwh": None,
    }
    available = [stamp for stamp in snapshots if decision is not None and stamp <= decision]
    if decision is None or not available:
        case["status"] = "invalid_local_decision_time" if decision is None else "no_forecast"
        return case, []
    retrieved = max(available)
    age = (decision - retrieved).total_seconds() / 3600
    case.update({"forecast_at": retrieved.isoformat(), "snapshot_age_hours": age})
    forecast = snapshots[retrieved]
    selected = [forecast[target] for target in targets if target in forecast]
    valid = [row for row in selected if _power_samples([{**row, "actual_battery_input_wh": 0}])]
    case["covered_forecast_hours"] = len(valid)
    if age > maximum_age:
        case["status"] = "stale_forecast"
    elif len(valid) != expected:
        case["status"] = "incomplete_forecast"
    elif case["covered_measurement_hours"] != expected:
        case["status"] = "incomplete_measurements"
    else:
        case["status"] = "available"
        case["actual_kwh"] = math.fsum(actuals[target] for target in targets) / 1000
    return case, valid


def _energy_prediction(
    rows: list[dict], model: PvPowerModel | None, max_power_w: float | None,
) -> float:
    powers = []
    for row in rows:
        irradiation = float(row["solar_irradiation_j_m2"])
        power = (
            estimate_battery_input_power_w_from_irradiation(irradiation)
            if model is None else model.predict(
                irradiation / 3600, float(row["panel_incidence_cosine"]),
            )
        )
        powers.append(min(power, max_power_w) if max_power_w is not None else power)
    return math.fsum(powers) / 1000


def _daily_scores(cases: list[dict], names: dict) -> dict:
    complete = [case for case in cases if case["status"] == "available"]
    scores = {name: None for name in MODEL_NAMES}
    for name in names:
        errors = [case["predicted_kwh"][name] - case["actual_kwh"] for case in complete]
        if errors:
            scores[name] = {
                "mae_kwh": math.fsum(abs(error) for error in errors) / len(errors),
                "rmse_kwh": math.sqrt(math.fsum(error ** 2 for error in errors) / len(errors)),
                "bias_kwh": math.fsum(errors) / len(errors),
            }
    return scores


def _daily_group(
    rows: list[dict], training: list[dict], days: list[date], boundary: datetime,
    decision_time: time, zone: ZoneInfo, days_ahead: int, minimum_rows: int,
    max_power_w: float | None, maximum_age: float,
) -> dict:
    snapshots, actuals = {}, {}
    for row in rows:
        retrieved = datetime.fromisoformat(row["forecast_at"])
        start = datetime.fromisoformat(row["target_start"])
        snapshots.setdefault(retrieved, {})[start] = row
        if _power_samples([row]):
            actuals[start] = float(row["actual_battery_input_wh"])
    pairs = [_daily_case(day, days_ahead, decision_time, zone, snapshots, actuals, maximum_age)
             for day in days]
    decisions = [datetime.fromisoformat(case["decision_at"]) for case, _ in pairs
                 if case["decision_at"] is not None]
    models, fit = _fit_before(training, min([boundary, *decisions]), minimum_rows, max_power_w)
    cases = []
    for case, forecast in pairs:
        if case["status"] == "available":
            case["predicted_kwh"] = {name: _energy_prediction(forecast, model, max_power_w)
                                     for name, model in models.items()}
        cases.append(case)
    counts = {}
    for case in cases:
        counts[case["status"]] = counts.get(case["status"], 0) + 1
    return {
        "days_ahead": days_ahead, "test_days": len(cases),
        "complete_days": counts.get("available", 0), "coverage_counts": counts,
        **fit, "scores": _daily_scores(cases, models), "cases": cases,
    }


def evaluate_forecast_horizons(
    rows: list[dict], timezone_name: str = "Europe/Berlin", decision_time: time = time(18),
    maximum_snapshot_age_hours: float = 2, minimum_training_rows: int = 30,
    holdout_fraction: float = 0.25, max_power_w: float | None = None,
) -> dict:
    """Backtest archived hourly versions; freeze each fit before its earliest issue time."""
    if decision_time.tzinfo is not None or decision_time.second or decision_time.microsecond:
        raise ValueError("decision time must be a local wall-clock time with minute precision")
    if not math.isfinite(maximum_snapshot_age_hours) or maximum_snapshot_age_hours <= 0:
        raise ValueError("maximum snapshot age must be finite and positive")
    if minimum_training_rows < 1 or not 0 < holdout_fraction < 1:
        raise ValueError("invalid horizon evaluation settings")
    PvPowerModel(0, 0, max_power_w)
    zone = ZoneInfo(timezone_name)
    latest = select_latest_forecasts(rows)
    days = sorted({sample.start.astimezone(zone).date() for sample in _power_samples(latest)})
    report = {
        "status": "insufficient_target_days", "timezone_name": timezone_name,
        "decision_time_local": decision_time.isoformat(timespec="minutes"),
        "maximum_snapshot_age_hours": maximum_snapshot_age_hours,
        "bias_definition": "prediction_minus_measurement", "test_start": None,
        "hourly_by_lead": [], "daily_at_decision_time": [],
    }
    if len(days) < 2:
        return report
    test_days = days[-max(1, math.ceil(len(days) * holdout_fraction)):]
    boundary = local_day_bounds(test_days[0], zone)[0]
    training = [row for row in latest if datetime.fromisoformat(row["target_end"]) <= boundary]
    report.update({
        "status": "available", "test_start": boundary.isoformat(),
        "hourly_by_lead": [_hourly_group(
            rows, training, boundary, minimum_training_rows, max_power_w, lower, upper,
        ) for lower, upper in LEAD_BOUNDS_HOURS],
        "daily_at_decision_time": [_daily_group(
            rows, training, test_days, boundary, decision_time, zone, offset,
            minimum_training_rows, max_power_w, maximum_snapshot_age_hours,
        ) for offset in (1, 2)],
    })
    return report
