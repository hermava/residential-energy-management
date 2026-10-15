import csv
import math
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import requests

from energy_manager.adapters.home_assistant import HomeAssistantAdapter
from energy_manager.exceptions import EntityUnavailableError
from energy_manager.forecast.correction import calculate_correction
from energy_manager.forecast.energy_forecast import InputPowerHistory
from energy_manager.models import (
    BatteryState,
    ForecastSensorConfig,
    ForecastSourceConfig,
    LoadConfig,
)
from energy_manager.settings import (
    FORECAST_CORRECTION_LOG_PATH,
    FORECAST_INPUT_HISTORY_SECONDS,
    FORECAST_LOG_INTERVAL_SECONDS,
    FORECAST_LOG_PATH,
    FORECAST_MIN_HISTORY_SAMPLES,
    UPDATE_INTERVAL_SECONDS,
)


def update_input_history(
    battery_state: BatteryState | None, state: dict, max_age_seconds: float,
) -> None:
    history = state.get("input_history")
    if history is None:
        history = InputPowerHistory(
            FORECAST_INPUT_HISTORY_SECONDS, FORECAST_MIN_HISTORY_SAMPLES,
            math.ceil(FORECAST_INPUT_HISTORY_SECONDS / UPDATE_INTERVAL_SECONDS) + 1,
        )
        state["input_history"] = history
    power_w = None
    if battery_state is not None:
        age = (datetime.now(UTC) - battery_state.reported_at).total_seconds()
        if 0 <= age <= max_age_seconds:
            power_w = battery_state.input_power_w
    state["input_statistics"] = history.update(time.monotonic(), power_w)


@dataclass(frozen=True)
class ForecastSnapshot:
    timestamp: datetime
    soc_percent: float | None
    battery_input_w: float | None
    battery_output_w: float | None
    solar_next_hour_wh: float | None
    solar_today_remaining_wh: float | None
    solar_tomorrow_wh: float | None
    weather_condition: str | None
    cloud_coverage_percent: float | None
    temperature_c: float | None
    solar_provider: str | None
    weather_provider: str | None


def _get_sensor(source: ForecastSourceConfig | None, name: str) -> ForecastSensorConfig | None:
    if source is None:
        return None
    return source.values.get(name)


def _read_state(
    ha: HomeAssistantAdapter, sensor: ForecastSensorConfig | None,
    max_age_seconds: float | None = None,
) -> str | None:
    if sensor is None:
        return None
    try:
        reading = ha.get_entity_state(sensor.entity_id)
        value = reading.state
    except (EntityUnavailableError, requests.RequestException):
        return None
    if value in {"unknown", "unavailable"}:
        return None
    if max_age_seconds is not None:
        age = (datetime.now(UTC) - reading.reported_at).total_seconds()
        if not 0 <= age <= max_age_seconds:
            return None
    return value


def _read_number(
    ha: HomeAssistantAdapter, sensor: ForecastSensorConfig | None,
    max_age_seconds: float | None = None,
) -> float | None:
    value = _read_state(ha, sensor, max_age_seconds)
    if value is None:
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except ValueError:
        return None


def _read_forecast_power(ha: HomeAssistantAdapter, config: LoadConfig) -> float | None:
    sensor = _get_sensor(config.forecast_sources.get("solar"), "power_now")
    if sensor is None:
        return None
    try:
        reading = ha.get_entity_state(sensor.entity_id)
        age = (datetime.now(UTC) - reading.reported_at).total_seconds()
        value = float(reading.state)
    except (EntityUnavailableError, requests.RequestException, ValueError):
        return None
    if not 0 <= age <= config.forecast_correction.max_age_seconds:
        return None
    unit = sensor.unit.strip().lower() if sensor.unit else ""
    if unit not in {"w", "kw"}:
        raise ValueError(f"Unsupported power unit: {sensor.unit}")
    value *= 1000 if unit == "kw" else 1
    return value if math.isfinite(value) and value >= 0 else None


def update_forecast_correction(
    ha: HomeAssistantAdapter, config: LoadConfig, battery_state: BatteryState | None, state: dict,
) -> None:
    now = time.monotonic()
    for key in ("correction_actual_history", "correction_predicted_history"):
        if key not in state:
            state[key] = InputPowerHistory(
                FORECAST_INPUT_HISTORY_SECONDS, FORECAST_MIN_HISTORY_SAMPLES,
                math.ceil(FORECAST_INPUT_HISTORY_SECONDS / UPDATE_INTERVAL_SECONDS) + 1,
            )
    predicted_w = _read_forecast_power(ha, config)
    actual_w = battery_state.input_power_w if battery_state is not None else None
    if battery_state is not None:
        age = (datetime.now(UTC) - battery_state.reported_at).total_seconds()
        if not 0 <= age <= config.battery.max_age_seconds:
            actual_w = None
    if actual_w is None or not math.isfinite(actual_w) or actual_w < 0 or predicted_w is None:
        actual_w = predicted_w = None
    actual = state["correction_actual_history"].update(now, actual_w)
    predicted = state["correction_predicted_history"].update(now, predicted_w)
    state["correction_actual_statistics"] = actual
    state["correction_predicted_statistics"] = predicted
    state["solar_power_now_w"] = predicted_w
    state["correction"] = calculate_correction(actual, predicted, config.forecast_correction)


@dataclass(frozen=True)
class CorrectionSnapshot:
    timestamp: datetime
    solar_power_now_w: float | None
    actual_mean_w: float | None
    predicted_mean_w: float | None
    sample_count: int
    span_seconds: float
    latest_age_seconds: float | None
    method: str
    status: str
    raw_factor: float | None
    factor: float | None
    solar_today_remaining_wh: float | None
    corrected_today_remaining_wh: float | None


def _collect_correction_snapshot(snapshot: ForecastSnapshot, state: dict) -> CorrectionSnapshot:
    result = state["correction"]
    actual = state["correction_actual_statistics"]
    predicted = state["correction_predicted_statistics"]
    remaining = snapshot.solar_today_remaining_wh
    valid_energy = remaining is not None and math.isfinite(remaining) and remaining >= 0
    corrected = remaining * result.factor if valid_energy and result.factor is not None else None
    return CorrectionSnapshot(
        snapshot.timestamp, state.get("solar_power_now_w"), actual.mean_w, predicted.mean_w,
        actual.sample_count, actual.span_seconds, actual.latest_age_seconds,
        result.method, result.status, result.raw_factor, result.factor, remaining, corrected,
    )


def _read_energy_wh(
    ha: HomeAssistantAdapter, sensor: ForecastSensorConfig | None,
    max_age_seconds: float | None = None,
) -> float | None:
    value = _read_number(ha, sensor, max_age_seconds)
    if value is None:
        return None
    unit = sensor.unit.strip().lower() if sensor and sensor.unit else ""
    if unit == "wh":
        return value
    if unit == "kwh":
        return value * 1000.0
    raise ValueError(f"Unsupported energy unit: {sensor.unit}")


def _read_percent(ha: HomeAssistantAdapter, sensor: ForecastSensorConfig | None) -> float | None:
    value = _read_number(ha, sensor)
    if value is None:
        return None
    unit = sensor.unit.strip().lower() if sensor and sensor.unit else ""
    if unit in {"%", "percent"}:
        return value
    if unit in {"fraction", "ratio"}:
        return value * 100.0
    raise ValueError(f"Unsupported percentage unit: {sensor.unit}")


def _read_temperature_c(
    ha: HomeAssistantAdapter, sensor: ForecastSensorConfig | None,
) -> float | None:
    value = _read_number(ha, sensor)
    if value is None:
        return None
    unit = sensor.unit.strip().lower() if sensor and sensor.unit else ""
    if unit in {"°c", "c", "celsius"}:
        return value
    if unit in {"°f", "f", "fahrenheit"}:
        return (value - 32.0) * 5.0 / 9.0
    raise ValueError(f"Unsupported temperature unit: {sensor.unit}")


def collect_forecast_snapshot(
    ha: HomeAssistantAdapter,
    config: LoadConfig,
    battery_state: BatteryState | None,
) -> ForecastSnapshot:
    solar = config.forecast_sources.get("solar")
    weather = config.forecast_sources.get("weather")
    return ForecastSnapshot(
        timestamp=datetime.now(UTC),
        soc_percent=battery_state.soc_percent if battery_state else None,
        battery_input_w=battery_state.input_power_w if battery_state else None,
        battery_output_w=battery_state.output_power_w if battery_state else None,
        solar_next_hour_wh=_read_energy_wh(ha, _get_sensor(solar, "next_hour")),
        solar_today_remaining_wh=_read_energy_wh(ha, _get_sensor(solar, "today_remaining")),
        solar_tomorrow_wh=_read_energy_wh(ha, _get_sensor(solar, "tomorrow")),
        weather_condition=_read_state(ha, _get_sensor(weather, "condition")),
        cloud_coverage_percent=_read_percent(ha, _get_sensor(weather, "cloud_coverage")),
        temperature_c=_read_temperature_c(ha, _get_sensor(weather, "temperature")),
        solar_provider=solar.provider if solar else None,
        weather_provider=weather.provider if weather else None,
    )


def append_forecast_snapshot(
    snapshot: ForecastSnapshot | CorrectionSnapshot, base_path: Path,
) -> None:
    row = asdict(snapshot)
    row["timestamp"] = snapshot.timestamp.isoformat()
    append_forecast_row(row, snapshot.timestamp, base_path)


def append_forecast_row(row: dict, timestamp: datetime, base_path: Path) -> None:
    log_path = base_path.with_name(
        f"{base_path.stem}_{timestamp:%Y-%m-%d}{base_path.suffix}"
    )
    log_path.parent.mkdir(parents=True, exist_ok=True)
    file_exists = log_path.exists()
    with log_path.open("a", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=row.keys())
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)

def update_forecast_logging(
    ha: HomeAssistantAdapter,
    config: LoadConfig,
    battery_state: BatteryState | None,
    state: dict,
) -> None:
    now = time.monotonic()
    last_log_at = state.get("last_log_at")
    if last_log_at is not None and now - last_log_at < FORECAST_LOG_INTERVAL_SECONDS:
        return
    snapshot = collect_forecast_snapshot(ha, config, battery_state)
    append_forecast_snapshot(snapshot, FORECAST_LOG_PATH)
    if "correction" in state:
        correction = _collect_correction_snapshot(snapshot, state)
        append_forecast_snapshot(correction, FORECAST_CORRECTION_LOG_PATH)
    state["last_log_at"] = now
