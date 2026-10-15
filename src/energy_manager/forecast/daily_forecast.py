"""Adapt configured signals into today's advisory energy balance."""

import time
from dataclasses import asdict
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from energy_manager.adapters.home_assistant import HomeAssistantAdapter
from energy_manager.forecast.energy_forecast import DailyEnergyForecast, forecast_daily_energy
from energy_manager.forecast.forecast_data import (
    _get_sensor,
    _read_energy_wh,
    _read_state,
    append_forecast_row,
)
from energy_manager.ha_reporting import _publish_energy_forecast
from energy_manager.models import BatteryState, LoadConfig
from energy_manager.settings import FORECAST_DAILY_LOG_PATH, FORECAST_LOG_INTERVAL_SECONDS

LOCAL_TIMEZONE = ZoneInfo("Europe/Berlin")


def _read_sunset(ha: HomeAssistantAdapter, config: LoadConfig) -> datetime | None:
    source = config.forecast_sources.get("daylight")
    value = _read_state(ha, _get_sensor(source, "sunset"))
    if value is None:
        return None
    try:
        sunset = datetime.fromisoformat(value)
    except ValueError:
        return None
    return sunset.astimezone(UTC) if sunset.tzinfo is not None else None


def _today_horizon(now: datetime, sunset: datetime | None) -> float | None:
    if sunset is None:
        return None
    if sunset.astimezone(LOCAL_TIMEZONE).date() != now.astimezone(LOCAL_TIMEZONE).date():
        return 0.0
    return max(0.0, (sunset - now).total_seconds() / 3600)


def update_daily_forecast(
    ha: HomeAssistantAdapter, config: LoadConfig, battery_state: BatteryState | None, state: dict,
) -> None:
    monotonic_now = time.monotonic()
    last_update = state.get("last_daily_forecast_at")
    if last_update is not None and monotonic_now - last_update < FORECAST_LOG_INTERVAL_SECONDS:
        return
    now = datetime.now(UTC)
    sunset = _read_sunset(ha, config)
    horizon = _today_horizon(now, sunset)
    solar = config.forecast_sources.get("solar")
    remaining = _read_energy_wh(
        ha, _get_sensor(solar, "today_remaining"), config.energy_forecast.max_age_seconds,
    )
    soc = None
    if battery_state is not None:
        age = (now - battery_state.reported_at).total_seconds()
        if 0 <= age <= config.battery.max_age_seconds:
            soc = battery_state.soc_percent
    correction = state.get("correction")
    factor = correction.factor if correction is not None else None
    statistics = state.get("input_statistics")
    input_mean = None
    if statistics is not None and statistics.has_minimum_samples:
        age = statistics.latest_age_seconds
        if age is not None and 0 <= age <= config.energy_forecast.input_history_max_age_seconds:
            input_mean = statistics.mean_w
    forecast = forecast_daily_energy(
        soc, remaining, horizon, config.battery.capacity_wh, config.energy_forecast, factor,
        input_mean_w=input_mean,
    )
    if horizon == 0:
        forecast = DailyEnergyForecast("unavailable", "unavailable", "daylight_period_ended")
    context = {
        "timestamp": now.isoformat(),
        "sunset_at": sunset.isoformat() if sunset is not None else None,
        "soc_percent": soc,
        "correction_status": correction.status if correction is not None else "unavailable",
        "consumption_w": config.energy_forecast.consumption_w,
        "input_efficiency": config.energy_forecast.input_efficiency,
        "output_efficiency": config.energy_forecast.output_efficiency,
        "surplus_min_soc_percent": config.energy_forecast.surplus_min_soc_percent,
        "surplus_target_soc_percent": config.energy_forecast.surplus_target_soc_percent,
        "reserve_soc_percent": config.energy_forecast.reserve_soc_percent,
        "input_mean_w": input_mean,
        "input_sample_count": statistics.sample_count if statistics is not None else 0,
        "input_latest_age_seconds": (
            statistics.latest_age_seconds if statistics is not None else None
        ),
    }
    state["daily_forecast"] = forecast
    append_forecast_row({**context, **asdict(forecast)}, now, FORECAST_DAILY_LOG_PATH)
    _publish_energy_forecast(config, ha, forecast, context)
    state["last_daily_forecast_at"] = monotonic_now
