import csv
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import requests

from energy_manager.adapters.home_assistant import HomeAssistantAdapter
from energy_manager.exceptions import EntityUnavailableError
from energy_manager.models import (
    BatteryState,
    ForecastSensorConfig,
    ForecastSourceConfig,
    LoadConfig,
)
from energy_manager.settings import FORECAST_LOG_INTERVAL_SECONDS, FORECAST_LOG_PATH


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


def _read_state(ha: HomeAssistantAdapter, sensor: ForecastSensorConfig | None) -> str | None:
    if sensor is None:
        return None
    try:
        value = ha.get_entity_state(sensor.entity_id).state
    except (EntityUnavailableError, requests.RequestException):
        return None
    if value in {"unknown", "unavailable"}:
        return None
    return value


def _read_number(ha: HomeAssistantAdapter, sensor: ForecastSensorConfig | None) -> float | None:
    value = _read_state(ha, sensor)
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _read_energy_wh(ha: HomeAssistantAdapter, sensor: ForecastSensorConfig | None) -> float | None:
    value = _read_number(ha, sensor)
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


def append_forecast_snapshot(snapshot: ForecastSnapshot, base_path: Path) -> None:
    log_path = base_path.with_name(
        f"{base_path.stem}_{snapshot.timestamp:%Y-%m-%d}{base_path.suffix}"
    )
    log_path.parent.mkdir(parents=True, exist_ok=True)
    file_exists = log_path.exists()
    row = asdict(snapshot)
    row["timestamp"] = snapshot.timestamp.isoformat()
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
    state["last_log_at"] = now
