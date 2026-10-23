"""Pure PV power estimates and energy queries over explicit time intervals."""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo

from energy_manager.forecast.solar_geometry import calculate_solar_geometry_features
from energy_manager.models import SolarArrayConfig


def _utc(timestamp: datetime) -> datetime:
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
    return timestamp.astimezone(UTC)


def _interval_bounds(start: datetime, end: datetime) -> tuple[datetime, datetime]:
    start, end = _utc(start), _utc(end)
    if end <= start:
        raise ValueError("interval end must be after start")
    return start, end


@dataclass(frozen=True)
class WeatherInterval:
    start: datetime
    end: datetime
    solar_irradiation_j_m2: float | None

    def __post_init__(self) -> None:
        _interval_bounds(self.start, self.end)
        value = self.solar_irradiation_j_m2
        if value is not None and (not math.isfinite(value) or value < 0):
            raise ValueError("irradiation must be finite and nonnegative, or None")


@dataclass(frozen=True)
class PvPowerModel:
    """P = G * (base_coefficient + incidence_coefficient * max(0, cos(angle)))."""

    base_coefficient: float
    incidence_coefficient: float
    max_power_w: float | None = None

    def __post_init__(self) -> None:
        for value in (self.base_coefficient, self.incidence_coefficient):
            if not math.isfinite(value) or value < 0:
                raise ValueError("power model coefficients must be finite and nonnegative")
        if self.max_power_w is not None:
            if not math.isfinite(self.max_power_w) or self.max_power_w <= 0:
                raise ValueError("max_power_w must be finite and positive, or None")

    def predict(
        self, irradiance_w_m2: float | None, incidence_cosine: float | None,
    ) -> float | None:
        if irradiance_w_m2 is None or incidence_cosine is None:
            return None
        if not math.isfinite(irradiance_w_m2) or irradiance_w_m2 < 0:
            return None
        if not math.isfinite(incidence_cosine) or not -1 <= incidence_cosine <= 1:
            return None
        factor = self.base_coefficient + self.incidence_coefficient * max(0.0, incidence_cosine)
        power = irradiance_w_m2 * factor
        if not math.isfinite(power):
            return None
        return min(power, self.max_power_w) if self.max_power_w is not None else power


@dataclass(frozen=True)
class PowerInterval:
    start: datetime
    end: datetime
    mean_power_w: float | None

    def __post_init__(self) -> None:
        _interval_bounds(self.start, self.end)
        value = self.mean_power_w
        if value is not None and (not math.isfinite(value) or value < 0):
            raise ValueError("power must be finite and nonnegative, or None")


@dataclass(frozen=True)
class IntervalEnergyForecast:
    start: datetime
    end: datetime
    energy_wh: float | None
    covered_seconds: float
    expected_seconds: float
    reason: str
    data_quality: str

    @property
    def mean_power_w(self) -> float | None:
        if self.energy_wh is None:
            return None
        return self.energy_wh * 3600 / self.expected_seconds


def _ordered_intervals(intervals: Sequence) -> list:
    ordered = sorted(intervals, key=lambda item: _utc(item.start))
    for left, right in zip(ordered, ordered[1:]):
        if _utc(left.end) > _utc(right.start):
            raise ValueError("forecast intervals must not overlap")
    return ordered


def predict_power_series(
    weather: Sequence[WeatherInterval], model: PvPowerModel, latitude_deg: float,
    longitude_deg: float, array: SolarArrayConfig,
) -> list[PowerInterval]:
    """Convert interval irradiation to mean power; geometry uses each interval midpoint."""
    result = []
    for interval in _ordered_intervals(weather):
        start, end = _interval_bounds(interval.start, interval.end)
        geometry = calculate_solar_geometry_features(
            start + (end - start) / 2, latitude_deg, longitude_deg,
            array.azimuth_deg, array.tilt_deg,
        )
        irradiation = interval.solar_irradiation_j_m2
        seconds = (end - start).total_seconds()
        irradiance = irradiation / seconds if irradiation is not None else None
        power = model.predict(irradiance, geometry.panel_incidence_cosine)
        result.append(PowerInterval(start, end, power))
    return result


def energy_between(
    series: Sequence[PowerInterval], start: datetime, end: datetime,
) -> IntervalEnergyForecast:
    """Integrate only [start, end); partial coverage never becomes a complete forecast."""
    start, end = _interval_bounds(start, end)
    energy_wh = 0.0
    covered = timedelta()
    for interval in _ordered_intervals(series):
        left = max(start, _utc(interval.start))
        right = min(end, _utc(interval.end))
        if right <= left or interval.mean_power_w is None:
            continue
        duration = right - left
        covered += duration
        energy_wh += interval.mean_power_w * duration.total_seconds() / 3600
    expected_seconds = (end - start).total_seconds()
    complete = covered == end - start
    return IntervalEnergyForecast(
        start, end, energy_wh if complete else None, covered.total_seconds(), expected_seconds,
        "complete" if complete else "incomplete_coverage",
        "heuristic" if complete else "unavailable",
    )


def local_day_bounds(day: date, zone: tzinfo) -> tuple[datetime, datetime]:
    """Return UTC boundaries of a local calendar day, including 23/25-hour DST days."""
    start = datetime.combine(day, time.min, zone)
    end = datetime.combine(day + timedelta(days=1), time.min, zone)
    return _interval_bounds(start, end)


def energy_for_day(
    series: Sequence[PowerInterval], day: date, zone: tzinfo,
) -> IntervalEnergyForecast:
    start, end = local_day_bounds(day, zone)
    return energy_between(series, start, end)


def forecast_horizon_end(now: datetime, zone: tzinfo, days_ahead: int = 2) -> datetime:
    """End of the requested future local day; default is the end of the day after tomorrow."""
    if not isinstance(days_ahead, int) or days_ahead < 0:
        raise ValueError("days_ahead must be a nonnegative integer")
    day = _utc(now).astimezone(zone).date() + timedelta(days=days_ahead)
    return local_day_bounds(day, zone)[1]
