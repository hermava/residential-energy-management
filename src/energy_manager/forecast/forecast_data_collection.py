"""Persist forecast inputs for later comparison and model calibration."""

import csv
import math
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from energy_manager.adapters.home_assistant import HomeAssistantAdapter
from energy_manager.forecast.forecast import (
    DailyEnergyForecast,
    InputPowerHistory,
    calculate_correction,
    forecast_daily_energy,
)
from energy_manager.forecast.forecast_input import (
    ForecastSnapshot,
    _get_sensor,
    _read_energy_wh,
    _read_state,
    collect_brightsky_forecast,
    collect_forecast_snapshot,
    read_forecast_power,
)
from energy_manager.ha_reporting import _publish_energy_forecast
from energy_manager.models import BatteryState, LoadConfig
from energy_manager.settings import (
    BRIGHTSKY_FORECAST_INTERVAL_SECONDS,
    BRIGHTSKY_FORECAST_LOG_PATH,
    BRIGHTSKY_FORECAST_RETRY_SECONDS,
    FORECAST_DAILY_LOG_PATH,
    FORECAST_INPUT_HISTORY_SECONDS,
    FORECAST_LOG_INTERVAL_SECONDS,
    FORECAST_LOG_PATH,
    FORECAST_MIN_HISTORY_SAMPLES,
    UPDATE_INTERVAL_SECONDS,
)

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
        "correction_raw_factor": correction.raw_factor if correction is not None else None,
        "correction_actual_mean_w": (
            state["correction_actual_statistics"].mean_w
            if "correction_actual_statistics" in state else None
        ),
        "correction_predicted_mean_w": (
            state["correction_predicted_statistics"].mean_w
            if "correction_predicted_statistics" in state else None
        ),
        "correction_sample_count": (
            state["correction_actual_statistics"].sample_count
            if "correction_actual_statistics" in state else 0
        ),
        "correction_span_seconds": (
            state["correction_actual_statistics"].span_seconds
            if "correction_actual_statistics" in state else 0.0
        ),
        "correction_latest_age_seconds": (
            state["correction_actual_statistics"].latest_age_seconds
            if "correction_actual_statistics" in state else None
        ),
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
    predicted_w = read_forecast_power(ha, config)
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


def append_forecast_snapshot(snapshot: ForecastSnapshot, base_path: Path) -> None:
    row = asdict(snapshot)
    row["timestamp"] = snapshot.timestamp.isoformat()
    append_forecast_row(row, snapshot.timestamp, base_path)


def append_forecast_row(row: dict, timestamp: datetime, base_path: Path) -> None:
    log_path = base_path.with_name(f"{base_path.stem}_{timestamp:%Y-%m-%d}{base_path.suffix}")
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
    state["last_log_at"] = now


def update_brightsky_forecast_collection(state: dict) -> None:
    now = time.monotonic()
    last_attempt = state.get("brightsky_forecast_last_attempt")
    interval = (
        BRIGHTSKY_FORECAST_RETRY_SECONDS
        if state.get("brightsky_forecast_last_error")
        else BRIGHTSKY_FORECAST_INTERVAL_SECONDS
    )
    if last_attempt is not None and now - last_attempt < interval:
        return
    state["brightsky_forecast_last_attempt"] = now
    try:
        points = collect_brightsky_forecast()
    except Exception:
        state["brightsky_forecast_last_error"] = True
        raise
    for point in points:
        row = asdict(point)
        row["retrieved_at"] = point.retrieved_at.isoformat()
        row["forecast_at"] = point.forecast_at.isoformat()
        append_forecast_row(row, point.retrieved_at, BRIGHTSKY_FORECAST_LOG_PATH)
    state.pop("brightsky_forecast_last_error", None)
    state["brightsky_forecast_last_success"] = datetime.now().isoformat()