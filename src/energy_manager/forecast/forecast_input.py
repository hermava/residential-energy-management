"""Collect and normalize forecast inputs from external providers."""

import math
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import requests

from energy_manager.adapters.home_assistant import HomeAssistantAdapter
from energy_manager.config import get_brightsky_forecast_settings
from energy_manager.exceptions import EntityUnavailableError
from energy_manager.models import (
    BatteryState,
    ForecastSensorConfig,
    ForecastSourceConfig,
    LoadConfig,
)

BRIGHTSKY_WEATHER_URL = "https://api.brightsky.dev/weather"


@dataclass(frozen=True)
class BrightSkyForecastPoint:
    retrieved_at: datetime
    forecast_at: datetime
    forecast_date: str
    source_id: int
    source_name: str | None
    sunshine_seconds: float | None
    solar_irradiation_j_m2: float | None
    cloud_cover_percent: float | None


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
    return source.values.get(name) if source is not None else None


def _read_state(
    ha: HomeAssistantAdapter, sensor: ForecastSensorConfig | None,
    max_age_seconds: float | None = None,
) -> str | None:
    if sensor is None:
        return None
    try:
        reading = ha.get_entity_state(sensor.entity_id)
    except (EntityUnavailableError, requests.RequestException):
        return None
    if reading.state in {"unknown", "unavailable"}:
        return None
    if max_age_seconds is not None:
        age = (datetime.now(UTC) - reading.reported_at).total_seconds()
        if not 0 <= age <= max_age_seconds:
            return None
    return reading.state


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


def read_forecast_power(ha: HomeAssistantAdapter, config: LoadConfig) -> float | None:
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


def collect_brightsky_forecast(now: datetime | None = None) -> list[BrightSkyForecastPoint]:
    settings = get_brightsky_forecast_settings()
    if settings is None:
        return []
    latitude, longitude, timezone_name, max_distance = settings
    zone = ZoneInfo(timezone_name)
    retrieved_at = now or datetime.now(zone)
    if retrieved_at.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    local_now = retrieved_at.astimezone(zone)
    final_day = local_now.date() + timedelta(days=2)
    last_date = datetime.combine(final_day + timedelta(days=1), time.min, zone)
    response = requests.get(
        BRIGHTSKY_WEATHER_URL,
        params={
            "date": local_now.isoformat(),
            "last_date": last_date.isoformat(),
            "lat": latitude,
            "lon": longitude,
            "max_dist": max_distance,
            "tz": timezone_name,
            "units": "si",
        },
        timeout=15,
    )
    response.raise_for_status()
    payload = response.json()
    sources = {
        source["id"]: source
        for source in payload.get("sources", [])
        if source.get("observation_type") == "forecast"
    }
    points = []
    for record in payload.get("weather", []):
        source = sources.get(record.get("source_id"))
        if source is None:
            continue
        forecast_at = datetime.fromisoformat(record["timestamp"])
        if forecast_at < local_now:
            continue
        points.append(BrightSkyForecastPoint(
            retrieved_at=retrieved_at,
            forecast_at=forecast_at,
            forecast_date=(forecast_at - timedelta(hours=1)).astimezone(zone).date().isoformat(),
            source_id=source["id"],
            source_name=source.get("station_name"),
            sunshine_seconds=_optional_float(record.get("sunshine")),
            solar_irradiation_j_m2=_optional_float(record.get("solar")),
            cloud_cover_percent=_optional_float(record.get("cloud_cover")),
        ))
    return sorted(points, key=lambda point: (point.forecast_at, point.source_id))


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None