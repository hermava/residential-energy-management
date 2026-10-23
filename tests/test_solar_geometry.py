from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from energy_manager.forecast.solar_geometry import calculate_solar_geometry_features


def test_solar_geometry_is_symmetric_about_solar_noon():
    zone = ZoneInfo("Europe/Berlin")
    morning = calculate_solar_geometry_features(
        datetime(2026, 6, 21, 10, 24, tzinfo=zone), 48.7, 9.1, 143, 60,
    )
    afternoon = calculate_solar_geometry_features(
        datetime(2026, 6, 21, 16, 24, tzinfo=zone), 48.7, 9.1, 143, 60,
    )
    assert morning.solar_altitude_deg == pytest.approx(afternoon.solar_altitude_deg, abs=0.5)
    assert morning.solar_azimuth_deg < 180
    assert afternoon.solar_azimuth_deg > 180


def test_panel_facing_sun_has_greater_incidence_feature():
    zone = ZoneInfo("Europe/Berlin")
    southeast = calculate_solar_geometry_features(
        datetime(2026, 6, 21, 9, tzinfo=zone), 48.7, 9.1, 143, 60,
    )
    west = calculate_solar_geometry_features(
        datetime(2026, 6, 21, 9, tzinfo=zone), 48.7, 9.1, 270, 60,
    )
    assert southeast.panel_incidence_cosine > west.panel_incidence_cosine


@pytest.mark.parametrize("timestamp", [
    datetime(2026, 6, 21, 12),
    datetime(2026, 6, 21, 12, tzinfo=ZoneInfo("Europe/Berlin")),
])
def test_solar_geometry_rejects_naive_timestamps(timestamp):
    if timestamp.tzinfo is not None:
        timestamp = timestamp.replace(tzinfo=None)
    with pytest.raises(ValueError, match="timezone-aware"):
        calculate_solar_geometry_features(timestamp, 48.7, 9.1, 143, 60)


@pytest.mark.parametrize("latitude,longitude,azimuth,tilt", [
    (91, 9, 143, 60), (48, 181, 143, 60), (48, 9, 360, 60), (48, 9, 143, 91),
])
def test_solar_geometry_rejects_invalid_coordinates(latitude, longitude, azimuth, tilt):
    with pytest.raises(ValueError):
        calculate_solar_geometry_features(
            datetime(2026, 6, 21, 12, tzinfo=ZoneInfo("UTC")),
            latitude, longitude, azimuth, tilt,
        )