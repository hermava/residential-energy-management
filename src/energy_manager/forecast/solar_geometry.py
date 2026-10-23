"""Solar-position features for empirical forecast analysis."""

import math
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class SolarGeometryFeatures:
    solar_altitude_deg: float
    solar_azimuth_deg: float
    panel_incidence_cosine: float
    day_of_year_sin: float
    day_of_year_cos: float


def calculate_solar_geometry_features(
    timestamp: datetime, latitude_deg: float, longitude_deg: float,
    panel_azimuth_deg: float, panel_tilt_deg: float,
) -> SolarGeometryFeatures:
    """Return geometry features; incidence is a predictor, not an irradiance correction."""
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    if not -90 <= latitude_deg <= 90 or not -180 <= longitude_deg <= 180:
        raise ValueError("latitude and longitude must be valid degrees")
    if not 0 <= panel_azimuth_deg < 360 or not 0 <= panel_tilt_deg <= 90:
        raise ValueError("panel azimuth or tilt is outside valid degrees")
    local_time = timestamp
    day_fraction = (
        local_time.timetuple().tm_yday - 1
        + (local_time.hour - 12 + local_time.minute / 60 + local_time.second / 3600) / 24
    )
    gamma = 2 * math.pi * day_fraction / 365.2425
    equation_minutes = 229.18 * (
        0.000075 + 0.001868 * math.cos(gamma) - 0.032077 * math.sin(gamma)
        - 0.014615 * math.cos(2 * gamma) - 0.040849 * math.sin(2 * gamma)
    )
    declination = (
        0.006918 - 0.399912 * math.cos(gamma) + 0.070257 * math.sin(gamma)
        - 0.006758 * math.cos(2 * gamma) + 0.000907 * math.sin(2 * gamma)
        - 0.002697 * math.cos(3 * gamma) + 0.00148 * math.sin(3 * gamma)
    )
    utc_offset_hours = local_time.utcoffset().total_seconds() / 3600
    clock_minutes = local_time.hour * 60 + local_time.minute + local_time.second / 60
    solar_minutes = clock_minutes + equation_minutes + 4 * longitude_deg - 60 * utc_offset_hours
    hour_angle = math.radians(solar_minutes / 4 - 180)
    latitude = math.radians(latitude_deg)
    cos_zenith = (
        math.sin(latitude) * math.sin(declination)
        + math.cos(latitude) * math.cos(declination) * math.cos(hour_angle)
    )
    altitude = math.asin(min(1.0, max(-1.0, cos_zenith)))
    azimuth = math.atan2(
        math.sin(hour_angle),
        math.cos(hour_angle) * math.sin(latitude)
        - math.tan(declination) * math.cos(latitude),
    ) + math.pi
    tilt = math.radians(panel_tilt_deg)
    relative_azimuth = azimuth - math.radians(panel_azimuth_deg)
    incidence_cosine = (
        math.sin(altitude) * math.cos(tilt)
        + math.cos(altitude) * math.sin(tilt) * math.cos(relative_azimuth)
    )
    seasonal_angle = 2 * math.pi * (local_time.timetuple().tm_yday - 1) / 365.2425
    return SolarGeometryFeatures(
        solar_altitude_deg=math.degrees(altitude),
        solar_azimuth_deg=math.degrees(azimuth) % 360,
        panel_incidence_cosine=min(1.0, max(-1.0, incidence_cosine)),
        day_of_year_sin=math.sin(seasonal_angle),
        day_of_year_cos=math.cos(seasonal_angle),
    )