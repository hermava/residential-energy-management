"""Persist forecast inputs for later comparison and model calibration."""

import json
import math
import time
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from energy_manager import settings
from energy_manager.adapters.home_assistant import HomeAssistantAdapter
from energy_manager.config import get_brightsky_forecast_settings
from energy_manager.csv_logging import append_csv_row
from energy_manager.forecast.forecast import (
    DailyBatteryInputForecast,
    DailyEnergyForecast,
    InputPowerHistory,
    calculate_correction,
    estimate_battery_input_power_w_from_irradiation,
    estimate_daily_battery_input_from_irradiation,
    estimate_interval_battery_input_wh,
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
    BRIGHTSKY_FORECAST_STATE_PATH,
    FORECAST_DAILY_LOG_PATH,
    FORECAST_INPUT_HISTORY_SECONDS,
    FORECAST_LOG_INTERVAL_SECONDS,
    FORECAST_LOG_PATH,
    FORECAST_MIN_HISTORY_SAMPLES,
    UPDATE_INTERVAL_SECONDS,
)

LOCAL_TIMEZONE = ZoneInfo("Europe/Berlin")


def _load_brightsky_state() -> dict:
    try:
        return json.loads(BRIGHTSKY_FORECAST_STATE_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def _save_brightsky_state(state: dict) -> None:
    BRIGHTSKY_FORECAST_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = BRIGHTSKY_FORECAST_STATE_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(state), encoding="utf-8")
    temporary.replace(BRIGHTSKY_FORECAST_STATE_PATH)


def _current_brightsky_input_power(
    points: list, now: datetime,
) -> tuple[float | None, float | None]:
    for point in points:
        interval_start = point.forecast_at - timedelta(hours=1)
        if interval_start <= now < point.forecast_at and point.solar_irradiation_j_m2 is not None:
            return (
                estimate_battery_input_power_w_from_irradiation(point.solar_irradiation_j_m2),
                point.solar_irradiation_j_m2,
            )
    return None, None


def _remember_tomorrow_forecast(
    forecasts: dict[str, DailyBatteryInputForecast], retrieved_at: datetime,
) -> None:
    settings = get_brightsky_forecast_settings()
    if settings is None:
        return
    zone = ZoneInfo(settings[2])
    target_day = (retrieved_at.astimezone(zone).date() + timedelta(days=1)).isoformat()
    tomorrow = forecasts.get("tomorrow")
    if tomorrow is None or tomorrow.estimated_battery_input_wh is None:
        return
    saved = _load_brightsky_state()
    predictions = saved.setdefault("daily_predictions", {})
    predictions[target_day] = {
        "forecast_at": retrieved_at.isoformat(),
        "estimated_input_wh": tomorrow.estimated_battery_input_wh,
    }
    cutoff = (retrieved_at.astimezone(zone).date() - timedelta(days=7)).isoformat()
    saved["daily_predictions"] = {day: value for day, value in predictions.items() if day >= cutoff}
    _save_brightsky_state(saved)


def record_completed_brightsky_comparison(
    now: datetime, completed_day, actual_input_wh: float, complete: bool,
    outputs: dict, ha: HomeAssistantAdapter, state: dict,
) -> None:
    if completed_day is None or now.astimezone(LOCAL_TIMEZONE).date() <= completed_day:
        return
    saved = _load_brightsky_state()
    day = completed_day.isoformat()
    prediction = saved.get("daily_predictions", {}).get(day)
    if prediction is None or day in saved.get("daily_comparisons", {}):
        return
    forecast_wh = prediction["estimated_input_wh"]
    actual_wh = actual_input_wh
    comparison = {
        "forecast_at": prediction["forecast_at"],
        "forecast_input_wh": forecast_wh,
        "actual_input_wh": actual_wh,
        "error_wh": actual_wh - forecast_wh,
        "complete": complete,
    }
    saved.setdefault("daily_comparisons", {})[day] = comparison
    _save_brightsky_state(saved)
    state["bright_sky_last_daily_comparison"] = comparison
    output = outputs.get("battery_input_forecast_tomorrow_error_kwh")
    if output is not None:
        ha.publish_state(
            output.entity_id,
            comparison["error_wh"] / 1000,
            attributes={
                "friendly_name": "Residential Battery Input Forecast Error Tomorrow",
                "unit_of_measurement": "kWh",
                "device_class": "energy",
                "forecast_date": day,
                "forecast_at": prediction["forecast_at"],
                "forecast_input_kwh": forecast_wh / 1000,
                "actual_input_kwh": actual_wh / 1000,
                "error_definition": "actual_minus_forecast",
                "daily_energy_complete": complete,
            },
        )


def _daily_brightsky_forecasts(
    points: list, now: datetime, battery_capacity_wh: float = 2240.0,
) -> dict[str, DailyBatteryInputForecast]:
    settings = get_brightsky_forecast_settings()
    if settings is None:
        return {}
    timezone_name = settings[2]
    zone = ZoneInfo(timezone_name)
    local_now = now.astimezone(zone)
    forecasts = {}
    for offset, key in ((1, "tomorrow"), (2, "day_after_tomorrow")):
        forecast_day = local_now.date() + timedelta(days=offset)
        day_start = datetime.combine(forecast_day, datetime.min.time(), zone)
        day_end = datetime.combine(forecast_day + timedelta(days=1), datetime.min.time(), zone)
        expected_hours = int(
            (day_end.astimezone(UTC) - day_start.astimezone(UTC)).total_seconds() / 3600
        )
        values_by_hour = {}
        for point in points:
            if point.forecast_date == forecast_day.isoformat():
                values_by_hour.setdefault(point.forecast_at, point.solar_irradiation_j_m2)
        values = [value for value in values_by_hour.values() if value is not None]
        irradiation = sum(values) if values else None
        forecasts[key] = estimate_daily_battery_input_from_irradiation(
            irradiation, len(values), expected_hours, battery_capacity_wh,
        )
    return forecasts


def _remaining_brightsky_input(points: list, now: datetime, sunset: datetime | None):
    if sunset is None or sunset <= now:
        return None, 0.0, 0
    expected_seconds = (sunset - now).total_seconds()
    intervals = {}
    for point in points:
        if point.solar_irradiation_j_m2 is None:
            continue
        interval_start = point.forecast_at - timedelta(hours=1)
        offset_seconds = (interval_start - now).total_seconds()
        intervals.setdefault(offset_seconds, point.solar_irradiation_j_m2)
    return estimate_interval_battery_input_wh(
        list(intervals.items()), expected_seconds,
    )


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
    solar_remaining_comparison = _read_energy_wh(
        ha, _get_sensor(solar, "today_remaining"),
    )
    bright_points = state.get("bright_sky_points", [])
    remaining, irradiation_covered_seconds, irradiation_intervals = _remaining_brightsky_input(
        bright_points, now, sunset,
    )
    soc = None
    if battery_state is not None:
        age = (now - battery_state.reported_at).total_seconds()
        if 0 <= age <= config.battery.max_age_seconds:
            soc = battery_state.soc_percent
    correction = state.get("correction")
    factor = None
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
        "correction_status": (
            "not_applied_brightsky_model" if remaining is not None else "unavailable"
        ),
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
        "pv_input_source": "brightsky_solar_irradiation",
        "brightsky_estimated_remaining_battery_input_wh": remaining,
        "brightsky_irradiation_covered_seconds": irradiation_covered_seconds,
        "brightsky_irradiation_intervals": irradiation_intervals,
        "solar_provider_today_remaining_comparison_wh": solar_remaining_comparison,
        "bright_sky_input_power_now_w": state.get("bright_sky_input_power_now_w"),
        "bright_sky_input_power_now_irradiation_j_m2": state.get(
            "bright_sky_input_power_now_irradiation_j_m2",
        ),
        "consumption_w": config.energy_forecast.consumption_w,
        "surplus_min_soc_percent": config.energy_forecast.surplus_min_soc_percent,
        "surplus_target_soc_percent": config.energy_forecast.surplus_target_soc_percent,
        "reserve_soc_percent": config.energy_forecast.reserve_soc_percent,
        "input_mean_w": input_mean,
        "input_sample_count": statistics.sample_count if statistics is not None else 0,
        "input_latest_age_seconds": (
            statistics.latest_age_seconds if statistics is not None else None
        ),
    }
    bright_sky_forecasts = state.get("bright_sky_daily_forecasts", {})
    for key, input_forecast in bright_sky_forecasts.items():
        context[f"{key}_assessment"] = input_forecast.assessment
        context[f"{key}_data_quality"] = input_forecast.data_quality
        context[f"{key}_reason"] = input_forecast.reason
        context[f"{key}_solar_irradiation_j_m2"] = input_forecast.solar_irradiation_j_m2
        context[f"{key}_estimated_battery_input_wh"] = input_forecast.estimated_battery_input_wh
        context[f"{key}_available_hours"] = input_forecast.available_hours
        context[f"{key}_expected_hours"] = input_forecast.expected_hours
    state["daily_forecast"] = forecast
    append_csv_row({**context, **asdict(forecast)}, now, FORECAST_DAILY_LOG_PATH)
    _publish_energy_forecast(config, ha, forecast, context)
    power_output = config.outputs.get("battery_input_forecast_now_w")
    if power_output is not None:
        actual_input_w = battery_state.input_power_w if battery_state is not None else None
        predicted_input_w = state.get("bright_sky_input_power_now_w")
        ha.publish_state(
            power_output.entity_id,
            predicted_input_w if predicted_input_w is not None else "unavailable",
            attributes={
                "friendly_name": "Residential Battery Input Power Forecast Now",
                "unit_of_measurement": "W",
                "device_class": "power",
                "forecast_horizon": "current Bright Sky hour",
                "actual_battery_input_w": actual_input_w,
                "error_w": (
                    actual_input_w - predicted_input_w
                    if actual_input_w is not None and predicted_input_w is not None else None
                ),
                "solar_irradiation_j_m2": state.get(
                    "bright_sky_input_power_now_irradiation_j_m2",
                ),
                "data_quality": "low" if predicted_input_w is not None else "unavailable",
            },
        )
    for key, forecast in bright_sky_forecasts.items():
        output = config.outputs.get(f"forecast_status_{key}")
        if output is not None:
            ha.publish_state(
                output.entity_id,
                forecast.assessment,
                attributes={
                    "friendly_name": (
                        f"Residential Battery Input Forecast {key.replace('_', ' ').title()}"
                    ),
                    **asdict(forecast),
                    "forecast_date": (now.astimezone(LOCAL_TIMEZONE).date() + timedelta(
                        days=1 if key == "tomorrow" else 2,
                    )).isoformat(),
                },
            )
    _publish_brightsky_energy_forecasts(ha, config, bright_sky_forecasts, now, state)
    state["last_daily_forecast_at"] = monotonic_now


def _publish_brightsky_energy_forecasts(
    ha: HomeAssistantAdapter, config: LoadConfig,
    forecasts: dict[str, DailyBatteryInputForecast], now: datetime, state: dict,
) -> None:
    for offset, key in ((1, "tomorrow"), (2, "day_after_tomorrow")):
        output = config.outputs.get(f"battery_input_forecast_{key}_kwh")
        if output is None:
            continue
        forecast = forecasts.get(key)
        energy_wh = forecast.estimated_battery_input_wh if forecast is not None else None
        ha.publish_state(
            output.entity_id,
            energy_wh / 1000 if energy_wh is not None else "unavailable",
            attributes={
                "friendly_name": (
                    f"Residential Battery Input Energy Forecast {key.replace('_', ' ').title()}"
                ),
                "unit_of_measurement": "kWh",
                "device_class": "energy",
                "forecast_date": (
                    now.astimezone(LOCAL_TIMEZONE).date() + timedelta(days=offset)
                ).isoformat(),
                "forecast_at": state.get("bright_sky_retrieved_at"),
                "data_quality": forecast.data_quality if energy_wh is not None else "unavailable",
                "model": forecast.model if forecast is not None else None,
            },
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
    append_csv_row(row, snapshot.timestamp, base_path)


def update_forecast_logging(
    ha: HomeAssistantAdapter,
    config: LoadConfig,
    battery_state: BatteryState | None,
    state: dict,
) -> None:
    if not settings.CSV_LOGGING_ENABLED:
        return
    now = time.monotonic()
    last_log_at = state.get("last_log_at")
    if last_log_at is not None and now - last_log_at < FORECAST_LOG_INTERVAL_SECONDS:
        return
    snapshot = collect_forecast_snapshot(ha, config, battery_state)
    append_forecast_snapshot(snapshot, FORECAST_LOG_PATH)
    state["last_log_at"] = now


def update_brightsky_forecast_collection(
    state: dict, battery_capacity_wh: float = 2240.0, solar_array=None,
) -> None:
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
        points = collect_brightsky_forecast(solar_array=solar_array)
    except Exception:
        state["brightsky_forecast_last_error"] = True
        raise
    for point in points:
        row = asdict(point)
        row["retrieved_at"] = point.retrieved_at.isoformat()
        row["forecast_at"] = point.forecast_at.isoformat()
        append_csv_row(row, point.retrieved_at, BRIGHTSKY_FORECAST_LOG_PATH)
    retrieved_at = points[0].retrieved_at if points else datetime.now(UTC)
    state["bright_sky_points"] = points
    state["bright_sky_daily_forecasts"] = _daily_brightsky_forecasts(
        points, retrieved_at, battery_capacity_wh,
    )
    state["bright_sky_retrieved_at"] = retrieved_at.isoformat()
    now_power_w, now_irradiation = _current_brightsky_input_power(points, retrieved_at)
    state["bright_sky_input_power_now_w"] = now_power_w
    state["bright_sky_input_power_now_irradiation_j_m2"] = now_irradiation
    if points:
        _remember_tomorrow_forecast(state["bright_sky_daily_forecasts"], retrieved_at)
    state.pop("brightsky_forecast_last_error", None)
    state["brightsky_forecast_last_success"] = datetime.now().isoformat()
