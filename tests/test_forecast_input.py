import csv
import json
from datetime import date, datetime, timedelta
from unittest.mock import Mock

import pytest

from energy_manager import settings
from energy_manager.config import get_brightsky_forecast_settings
from energy_manager.forecast import forecast_data_collection, forecast_input
from energy_manager.forecast.forecast_input import BrightSkyForecastPoint
from energy_manager.models import OutputConfig


def test_brightsky_settings_read_private_location_from_environment(monkeypatch):
    monkeypatch.setenv("BRIGHTSKY_LATITUDE", "48.5")
    monkeypatch.setenv("BRIGHTSKY_LONGITUDE", "9.5")
    monkeypatch.setenv("BRIGHTSKY_TIMEZONE", "Europe/Berlin")
    monkeypatch.setenv("BRIGHTSKY_MAX_DISTANCE_M", "12000")
    assert get_brightsky_forecast_settings() == (
        48.5, 9.5, "Europe/Berlin", 12000,
    )


def test_brightsky_settings_are_optional_and_validate_coordinates(monkeypatch):
    monkeypatch.delenv("BRIGHTSKY_LATITUDE", raising=False)
    monkeypatch.delenv("BRIGHTSKY_LONGITUDE", raising=False)
    assert get_brightsky_forecast_settings() is None
    monkeypatch.setenv("BRIGHTSKY_LATITUDE", "91")
    monkeypatch.setenv("BRIGHTSKY_LONGITUDE", "9")
    with pytest.raises(RuntimeError, match="outside valid ranges"):
        get_brightsky_forecast_settings()


def test_brightsky_forecast_uses_configured_location_and_filters_sources(monkeypatch):
    now = datetime.fromisoformat("2026-10-19T08:30:00+02:00")
    response = Mock()
    response.json.return_value = {
        "sources": [
            {"id": 1, "observation_type": "historical", "station_name": "Past"},
            {"id": 2, "observation_type": "forecast", "station_name": "Forecast"},
        ],
        "weather": [
            {"timestamp": "2026-10-19T08:00:00+02:00", "source_id": 2},
            {
                "timestamp": "2026-10-19T09:00:00+02:00", "source_id": 1,
                "sunshine": 900, "solar": 100000, "cloud_cover": 5,
            },
            {
                "timestamp": "2026-10-19T09:00:00+02:00", "source_id": 2,
                "sunshine": 900, "solar": 100000, "cloud_cover": 5,
            },
        ],
    }
    monkeypatch.setattr(
        forecast_input, "get_brightsky_forecast_settings",
        lambda: (48.7, 9.1, "Europe/Berlin", 50000),
    )
    get = Mock(return_value=response)
    monkeypatch.setattr(forecast_input.requests, "get", get)

    points = forecast_input.collect_brightsky_forecast(now)

    assert len(points) == 1
    assert points[0].retrieved_at == now
    assert points[0].forecast_at == datetime.fromisoformat("2026-10-19T09:00:00+02:00")
    assert points[0].forecast_date == "2026-10-19"
    assert points[0].source_name == "Forecast"
    assert points[0].sunshine_seconds == 900
    assert points[0].solar_irradiation_j_m2 == 100000
    assert points[0].cloud_cover_percent == 5
    params = get.call_args.kwargs["params"]
    assert params["lat"] == 48.7
    assert params["lon"] == 9.1
    assert params["last_date"].startswith("2026-10-22T00:00")
    assert params["units"] == "si"


def test_brightsky_forecast_without_location_does_not_request(monkeypatch):
    monkeypatch.setattr(forecast_input, "get_brightsky_forecast_settings", lambda: None)
    get = Mock()
    monkeypatch.setattr(forecast_input.requests, "get", get)
    assert forecast_input.collect_brightsky_forecast() == []
    get.assert_not_called()


def test_brightsky_collection_respects_hourly_interval(monkeypatch, tmp_path):
    point = BrightSkyForecastPoint(
        retrieved_at=datetime.fromisoformat("2026-10-19T08:30:00+02:00"),
        forecast_at=datetime.fromisoformat("2026-10-19T09:00:00+02:00"),
        forecast_date="2026-10-19",
        source_id=2,
        source_name="Forecast",
        sunshine_seconds=900.0,
        solar_irradiation_j_m2=100000.0,
        cloud_cover_percent=5.0,
    )
    collect = Mock(return_value=[point])
    monkeypatch.setattr(forecast_data_collection, "collect_brightsky_forecast", collect)
    path = tmp_path / "forecast.csv"
    monkeypatch.setattr(forecast_data_collection, "BRIGHTSKY_FORECAST_LOG_PATH", path)
    monkeypatch.setattr(forecast_data_collection, "BRIGHTSKY_FORECAST_INTERVAL_SECONDS", 3600)
    monkeypatch.setattr(forecast_data_collection, "time", Mock(monotonic=Mock(return_value=0)))
    state = {}

    forecast_data_collection.update_brightsky_forecast_collection(state)
    forecast_data_collection.update_brightsky_forecast_collection(state)

    assert collect.call_count == 1
    with (tmp_path / "forecast_2026-10-19.csv").open(newline="") as file:
        row = next(csv.DictReader(file))
    assert row["retrieved_at"] == point.retrieved_at.isoformat()
    assert row["forecast_at"] == point.forecast_at.isoformat()
    assert row["solar_irradiation_j_m2"] == "100000.0"


@pytest.mark.parametrize("csv_enabled", [True, False])
def test_brightsky_collection_keeps_forecasts_and_json_state(monkeypatch, tmp_path, csv_enabled):
    now = datetime.fromisoformat("2026-10-19T08:30:00+02:00")
    tomorrow = datetime.fromisoformat("2026-10-20T00:00:00+02:00")
    points = [BrightSkyForecastPoint(
        retrieved_at=now, forecast_at=tomorrow + timedelta(hours=hour),
        forecast_date="2026-10-20", source_id=2, source_name="Forecast",
        sunshine_seconds=900.0, solar_irradiation_j_m2=100000.0, cloud_cover_percent=5.0,
    ) for hour in range(1, 25)]
    monkeypatch.setattr(settings, "CSV_LOGGING_ENABLED", csv_enabled)
    collect = Mock(return_value=points)
    monkeypatch.setattr(forecast_data_collection, "collect_brightsky_forecast", collect)
    monkeypatch.setattr(
        forecast_data_collection, "get_brightsky_forecast_settings",
        lambda: (48.5, 9.5, "Europe/Berlin", 50000),
    )
    monkeypatch.setattr(forecast_data_collection, "time", Mock(monotonic=Mock(return_value=0)))
    monkeypatch.setattr(
        forecast_data_collection, "BRIGHTSKY_FORECAST_LOG_PATH", tmp_path / "logs" / "sky.csv",
    )
    state_path = tmp_path / "state" / "forecast.json"
    monkeypatch.setattr(forecast_data_collection, "BRIGHTSKY_FORECAST_STATE_PATH", state_path)
    state = {}
    forecast_data_collection.update_brightsky_forecast_collection(state)
    forecast_data_collection.update_brightsky_forecast_collection(state)
    collect.assert_called_once()
    assert state["bright_sky_points"] == points
    estimate = state["bright_sky_daily_forecasts"]["tomorrow"].estimated_battery_input_wh
    assert estimate is not None
    saved = json.loads(state_path.read_text())
    assert saved["daily_predictions"]["2026-10-20"]["estimated_input_wh"] == estimate
    assert "brightsky_forecast_last_success" in state
    assert (tmp_path / "logs").exists() is csv_enabled
    assert bool(list(tmp_path.rglob("*.csv"))) is csv_enabled


def test_brightsky_daily_estimates_group_tomorrow_and_day_after(monkeypatch):
    now = datetime.fromisoformat("2026-10-19T08:30:00+02:00")
    monkeypatch.setattr(
        forecast_data_collection, "get_brightsky_forecast_settings",
        lambda: (48.7, 9.1, "Europe/Berlin", 50000),
    )
    points = []
    for day_offset, daily_irradiation in ((1, 340000), (2, 150000)):
        day = (now.date() + timedelta(days=day_offset)).isoformat()
        day_start = datetime.fromisoformat(f"{day}T00:00:00+02:00")
        for hour in range(1, 25):
            stamp = day_start + timedelta(hours=hour)
            points.append(BrightSkyForecastPoint(
                retrieved_at=now,
                forecast_at=stamp,
                forecast_date=(stamp - timedelta(hours=1)).date().isoformat(),
                source_id=7,
                source_name="Test forecast",
                sunshine_seconds=None,
                solar_irradiation_j_m2=daily_irradiation,
                cloud_cover_percent=None,
            ))

    results = forecast_data_collection._daily_brightsky_forecasts(points, now)

    assert results["tomorrow"].assessment == "yellow"
    assert results["tomorrow"].available_hours == 24
    assert results["tomorrow"].estimated_battery_input_wh == pytest.approx(1970.4666666666667)
    assert results["day_after_tomorrow"].assessment == "red"
    assert results["day_after_tomorrow"].available_hours == 24


def test_brightsky_daily_estimates_mark_missing_hours_unavailable(monkeypatch):
    now = datetime.fromisoformat("2026-10-19T08:30:00+02:00")
    monkeypatch.setattr(
        forecast_data_collection, "get_brightsky_forecast_settings",
        lambda: (48.7, 9.1, "Europe/Berlin", 50000),
    )
    result = forecast_data_collection._daily_brightsky_forecasts([], now)
    assert result["tomorrow"].assessment == "unavailable"
    assert result["tomorrow"].reason == "missing_or_invalid_irradiation"
    assert result["day_after_tomorrow"].assessment == "unavailable"


def test_completed_day_publishes_actual_minus_forecast_error(monkeypatch, tmp_path):
    path = tmp_path / "brightsky_forecast_state.json"
    path.write_text(json.dumps({
        "daily_predictions": {
            "2026-10-19": {
                "forecast_at": "2026-10-18T23:00:00+02:00",
                "estimated_input_wh": 1800,
            },
        },
    }))
    monkeypatch.setattr(forecast_data_collection, "BRIGHTSKY_FORECAST_STATE_PATH", path)
    ha = Mock()
    state = {}

    forecast_data_collection.record_completed_brightsky_comparison(
        datetime.fromisoformat("2026-10-20T00:00:10+02:00"), date(2026, 10, 19),
        1500, True,
        {"battery_input_forecast_tomorrow_error_kwh": OutputConfig("sensor.forecast_error")},
        ha, state,
    )

    assert state["bright_sky_last_daily_comparison"]["error_wh"] == -300
    assert state["bright_sky_last_daily_comparison"]["complete"] is True
    call = ha.publish_state.call_args
    assert call.args == ("sensor.forecast_error", -0.3)
    assert call.kwargs["attributes"]["error_definition"] == "actual_minus_forecast"
    saved = json.loads(path.read_text())
    assert saved["daily_comparisons"]["2026-10-19"]["actual_input_wh"] == 1500
    forecast_data_collection.record_completed_brightsky_comparison(
        datetime.fromisoformat("2026-10-20T00:00:20+02:00"), date(2026, 10, 19),
        1500, True,
        {"battery_input_forecast_tomorrow_error_kwh": OutputConfig("sensor.forecast_error")},
        ha, state,
    )
    assert ha.publish_state.call_count == 1
