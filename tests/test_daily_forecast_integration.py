import csv
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest
import requests

from energy_manager.exceptions import EntityUnavailableError
from energy_manager.forecast import forecast_data_collection as daily_forecast
from energy_manager.forecast import forecast_input
from energy_manager.forecast.forecast import (
    CorrectionResult,
    DailyBatteryInputForecast,
    InputPowerHistory,
)
from energy_manager.models import (
    BatteryConfig,
    BatteryState,
    EntityState,
    ForecastSensorConfig,
    ForecastSourceConfig,
    LoadConfig,
    OutputConfig,
)


@pytest.fixture
def setup_forecast(monkeypatch, tmp_path):
    now = datetime(2026, 10, 15, 12, tzinfo=UTC)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz) if tz else now.replace(tzinfo=None)
    monkeypatch.setattr(daily_forecast, "datetime", Clock)
    monkeypatch.setattr(forecast_input, "datetime", Clock)
    monkeypatch.setattr(daily_forecast, "FORECAST_DAILY_LOG_PATH", tmp_path / "forecast.csv")
    monkeypatch.setattr(daily_forecast.time, "monotonic", lambda: 0)
    monkeypatch.setattr(
        daily_forecast, "get_brightsky_forecast_settings",
        lambda: (48.5, 9.5, "Europe/Berlin", 50000),
    )
    config = LoadConfig(
        {}, {}, {}, {"forecast_status_today": OutputConfig("sensor.custom_forecast")},
        BatteryConfig(2240, 20, 800, 120, 95),
        {
            "daylight": ForecastSourceConfig("test", {
                "sunset": ForecastSensorConfig("sensor.sunset"),
            }),
            "solar": ForecastSourceConfig("test", {
                "today_remaining": ForecastSensorConfig("sensor.remaining", "kWh"),
            }),
        },
    )
    readings = {"sensor.sunset": "2026-10-15T16:00:00+00:00", "sensor.remaining": "2"}
    ha = Mock()
    ha.get_entity_state.side_effect = lambda entity: EntityState(entity, readings[entity], now, now)
    battery = BatteryState(80, 500, 800, now, now)
    return now, config, readings, ha, battery, tmp_path


def test_forecast_uses_sunset_correction_and_configured_output(setup_forecast):
    now, config, readings, ha, battery, path = setup_forecast
    state = {"correction": CorrectionResult("ratio_of_means_v1", "available", 0.5, 0.5)}
    state["bright_sky_points"] = [
        forecast_input.BrightSkyForecastPoint(
            retrieved_at=now,
            forecast_at=now + timedelta(hours=index + 1),
            forecast_date="2026-10-15",
            source_id=2,
            source_name="Test forecast",
            sunshine_seconds=0,
            solar_irradiation_j_m2=90_000,
            cloud_cover_percent=100,
        )
        for index in range(4)
    ]
    daily_forecast.update_daily_forecast(ha, config, battery, state)
    result = state["daily_forecast"]
    assert result.horizon_hours == 4
    assert result.consumption_wh == 520  # Actual battery output is not added to 130 W.
    assert result.effective_pv_wh == pytest.approx(125.55)
    assert result.balance_energy_wh == pytest.approx(1397.55)
    assert result.assessment == "limited"
    call = ha.publish_state.call_args
    assert call.args == ("sensor.custom_forecast", "limited")
    assert call.kwargs["attributes"]["data_quality"] == "heuristic"
    assert call.kwargs["attributes"]["sunset_at"] == readings["sensor.sunset"]
    with (path / "forecast_2026-10-15.csv").open() as file:
        row = next(csv.DictReader(file))
    assert float(row["effective_pv_wh"]) == pytest.approx(125.55)
    assert row["timestamp"] == now.isoformat()
    assert row["correction_status"] == "not_applied_brightsky_model"


def test_tomorrow_and_day_after_ampels_publish_when_outputs_are_configured(setup_forecast):
    now, config, _, ha, battery, _ = setup_forecast
    config = replace(config, outputs={
        "forecast_status_today": OutputConfig("sensor.today"),
        "forecast_status_tomorrow": OutputConfig("sensor.tomorrow"),
        "forecast_status_day_after_tomorrow": OutputConfig("sensor.day_after"),
    })
    state = {
        "bright_sky_daily_forecasts": {
            "tomorrow": DailyBatteryInputForecast(
                "yellow", "low", "five_day_historical_fit", 7_200_000,
                24, 24, 1743.4,
            ),
            "day_after_tomorrow": DailyBatteryInputForecast(
                "red", "low", "five_day_historical_fit", 3_600_000,
                24, 24, 891.9,
            ),
        },
    }

    daily_forecast.update_daily_forecast(ha, config, battery, state)

    published = {call.args[0]: call.args[1] for call in ha.publish_state.call_args_list}
    assert published["sensor.tomorrow"] == "yellow"
    assert published["sensor.day_after"] == "red"


def test_power_forecast_now_and_tomorrow_energy_publish_with_units(setup_forecast):
    now, config, _, ha, battery, _ = setup_forecast
    config = replace(config, outputs={
        "forecast_status_today": OutputConfig("sensor.today"),
        "battery_input_forecast_tomorrow_kwh": OutputConfig("sensor.tomorrow_kwh"),
        "battery_input_forecast_now_w": OutputConfig("sensor.power_forecast_now"),
    })
    state = {
        "bright_sky_input_power_now_w": 244.18,
        "bright_sky_input_power_now_irradiation_j_m2": 1_000_000,
        "bright_sky_retrieved_at": now.isoformat(),
        "bright_sky_daily_forecasts": {
            "tomorrow": DailyBatteryInputForecast(
                "yellow", "low", "five_day_historical_fit", 7_200_000,
                24, 24, 1743.4,
            ),
        },
    }

    daily_forecast.update_daily_forecast(ha, config, battery, state)

    published = {call.args[0]: call for call in ha.publish_state.call_args_list}
    now_call = published["sensor.power_forecast_now"]
    assert now_call.args[1] == 244.18
    assert now_call.kwargs["attributes"]["unit_of_measurement"] == "W"
    now_attributes = now_call.kwargs["attributes"]
    assert now_attributes["actual_battery_input_w"] == 500
    assert now_attributes["error_w"] == pytest.approx(255.82)
    tomorrow_call = published["sensor.tomorrow_kwh"]
    assert tomorrow_call.args[1] == pytest.approx(1.7434)
    assert tomorrow_call.kwargs["attributes"]["unit_of_measurement"] == "kWh"


@pytest.mark.parametrize("value,reason", [
    ("unknown", "missing_or_invalid_data"),
    ("unavailable", "missing_or_invalid_data"),
    ("invalid", "missing_or_invalid_data"),
    ("2026-10-15T16:00:00", "missing_or_invalid_data"),
    ("2026-10-16T16:00:00+00:00", "daylight_period_ended"),
    ("2026-10-14T16:00:00+00:00", "daylight_period_ended"),
    ("2026-10-15T11:00:00+00:00", "daylight_period_ended"),
])
def test_invalid_or_non_today_sunset_never_uses_tomorrows_horizon(setup_forecast, value, reason):
    _, config, readings, ha, battery, _ = setup_forecast
    readings["sensor.sunset"] = value
    state = {}
    daily_forecast.update_daily_forecast(ha, config, battery, state)
    assert state["daily_forecast"].assessment == "unavailable"
    assert state["daily_forecast"].reason == reason
    assert ha.publish_state.call_args.args[1] == "unavailable"


def test_horizon_uses_local_calendar_date_even_when_utc_date_differs():
    now = datetime(2026, 10, 15, 22, 30, tzinfo=UTC)  # October 16 in Berlin.
    sunset = datetime(2026, 10, 16, 16, tzinfo=UTC)
    assert daily_forecast._today_horizon(now, sunset) == 17.5
    offset_sunset = datetime.fromisoformat("2026-10-16T18:00:00+02:00")
    assert daily_forecast._today_horizon(now, offset_sunset) == 17.5


def test_missing_correction_uses_original_with_explicit_status(setup_forecast):
    now, config, _, ha, battery, _ = setup_forecast
    state = {
        "correction": CorrectionResult("ratio_of_means_v1", "insufficient_samples"),
        "bright_sky_points": [
            forecast_input.BrightSkyForecastPoint(
                retrieved_at=now,
                forecast_at=now + timedelta(hours=index + 1),
                forecast_date="2026-10-15",
                source_id=2,
                source_name="Test forecast",
                sunshine_seconds=0,
                solar_irradiation_j_m2=90_000,
                cloud_cover_percent=100,
            )
            for index in range(4)
        ],
    }
    daily_forecast.update_daily_forecast(ha, config, battery, state)
    result = state["daily_forecast"]
    assert result.effective_pv_wh == result.original_pv_wh == pytest.approx(125.55)
    assert result.correction_factor is None
    assert ha.publish_state.call_args.kwargs["attributes"]["correction_status"] == (
        "not_applied_brightsky_model"
    )


@pytest.mark.parametrize("failed_signal", ["sensor.remaining", "sensor.sunset"])
def test_ha_failure_publishes_unavailable_instead_of_reusing_old_forecast(
    setup_forecast, failed_signal,
):
    now, config, readings, ha, battery, _ = setup_forecast
    def read(entity):
        if entity == failed_signal:
            raise requests.ConnectionError()
        return EntityState(entity, readings[entity], now, now)
    ha.get_entity_state.side_effect = read
    state = {"daily_forecast": Mock(assessment="surplus_expected")}
    daily_forecast.update_daily_forecast(ha, config, battery, state)
    assert state["daily_forecast"].assessment == "unavailable"


def test_stale_pv_and_stale_battery_are_rejected(setup_forecast):
    now, config, readings, ha, battery, _ = setup_forecast
    ha.get_entity_state.side_effect = lambda entity: EntityState(
        entity, readings[entity], now - timedelta(hours=2), now,
    )
    state = {}
    daily_forecast.update_daily_forecast(ha, config, battery, state)
    assert state["daily_forecast"].assessment == "unavailable"
    ha.get_entity_state.side_effect = lambda entity: EntityState(entity, readings[entity], now, now)
    stale_battery = replace(battery, reported_at=now - timedelta(minutes=5))
    state = {}
    daily_forecast.update_daily_forecast(ha, config, stale_battery, state)
    assert state["daily_forecast"].assessment == "unavailable"


def test_sixty_second_interval_and_optional_output(setup_forecast, monkeypatch):
    _, config, _, ha, battery, _ = setup_forecast
    config = replace(config, outputs={})
    state = {}
    daily_forecast.update_daily_forecast(ha, config, battery, state)
    ha.publish_state.assert_not_called()
    ha.get_entity_state.reset_mock()
    monkeypatch.setattr(daily_forecast.time, "monotonic", lambda: 10)
    daily_forecast.update_daily_forecast(ha, config, battery, state)
    ha.get_entity_state.assert_not_called()
    monkeypatch.setattr(daily_forecast.time, "monotonic", lambda: 60)
    daily_forecast.update_daily_forecast(ha, config, battery, state)
    assert ha.get_entity_state.call_count == 2


def test_missing_entity_and_configuration_are_optional(setup_forecast):
    _, config, _, ha, battery, _ = setup_forecast
    ha.get_entity_state.side_effect = EntityUnavailableError("missing")
    state = {}
    daily_forecast.update_daily_forecast(ha, config, battery, state)
    assert state["daily_forecast"].assessment == "unavailable"
    ha.get_entity_state.reset_mock()
    state = {}
    daily_forecast.update_daily_forecast(ha, replace(config, forecast_sources={}), battery, state)
    ha.get_entity_state.assert_not_called()
    assert state["daily_forecast"].assessment == "unavailable"


@pytest.mark.parametrize("count,age,expected", [
    (10, 0, "reserve_at_risk"), (9, 0, "unavailable"), (10, 121, "unavailable"),
])
def test_risk_requires_enough_fresh_input_samples(setup_forecast, count, age, expected):
    _, config, readings, ha, battery, path = setup_forecast
    readings["sensor.remaining"] = "0.1"
    history = InputPowerHistory(600, 10, 61)
    for index in range(count):
        stats = history.update(index * 10, 0)
    stats = replace(stats, latest_age_seconds=age)
    state = {"input_statistics": stats}
    now = datetime(2026, 10, 15, 12, tzinfo=UTC)
    state["bright_sky_points"] = [
        forecast_input.BrightSkyForecastPoint(
            retrieved_at=now,
            forecast_at=now + timedelta(hours=index + 1),
            forecast_date="2026-10-15",
            source_id=2,
            source_name="Test forecast",
            sunshine_seconds=0,
            solar_irradiation_j_m2=0,
            cloud_cover_percent=100,
        )
        for index in range(4)
    ]
    daily_forecast.update_daily_forecast(ha, config, replace(battery, soc_percent=20), state)
    assert state["daily_forecast"].assessment == expected
    call = ha.publish_state.call_args
    assert call.args[1] == expected
    assert call.kwargs["attributes"]["input_sample_count"] == count
    with (path / "forecast_2026-10-15.csv").open() as file:
        row = next(csv.DictReader(file))
    assert row["assessment"] == expected
    assert float(row["reserve_change_wh"]) == pytest.approx(-479.6)


def test_today_forecast_is_unavailable_without_complete_brightsky_irradiation(
    setup_forecast,
):
    _, config, _, ha, battery, _ = setup_forecast
    state = {"bright_sky_points": []}

    daily_forecast.update_daily_forecast(ha, config, battery, state)

    assert state["daily_forecast"].assessment == "unavailable"
    assert state["daily_forecast"].reason == "missing_or_invalid_data"
    assert ha.publish_state.call_args.kwargs["attributes"]["pv_input_source"] == (
        "brightsky_solar_irradiation"
    )
