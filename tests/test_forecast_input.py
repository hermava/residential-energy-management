import csv
from datetime import datetime
from unittest.mock import Mock

import pytest

from energy_manager.config import get_brightsky_forecast_settings
from energy_manager.forecast import forecast_data_collection, forecast_input
from energy_manager.forecast.forecast_input import BrightSkyForecastPoint


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
    assert params["last_date"].startswith("2026-10-21T23:59")
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