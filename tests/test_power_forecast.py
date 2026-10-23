from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from energy_manager.forecast.power_forecast import (
    PowerInterval,
    PvPowerModel,
    WeatherInterval,
    energy_between,
    energy_for_day,
    forecast_horizon_end,
    local_day_bounds,
    predict_power_series,
)
from energy_manager.models import SolarArrayConfig


def test_power_model_uses_geometry_without_a_night_time_intercept():
    model = PvPowerModel(1.5, 0.8)
    assert model.predict(100, 0.5) == pytest.approx(190)
    assert model.predict(100, -0.5) == pytest.approx(150)
    assert model.predict(0, 0.5) == 0
    assert PvPowerModel(1.5, 0.8, 160).predict(100, 0.5) == 160


@pytest.mark.parametrize("irradiance,incidence", [
    (None, 0.5), (100, None), (-1, 0.5), (float("nan"), 0.5),
    (100, float("inf")), (100, 1.1),
])
def test_missing_or_invalid_inputs_do_not_create_power(irradiance, incidence):
    assert PvPowerModel(1, 1).predict(irradiance, incidence) is None


@pytest.mark.parametrize("base,incidence,limit", [
    (-1, 1, None), (1, -1, None), (float("inf"), 1, None), (1, 1, 0),
])
def test_model_rejects_invalid_parameters(base, incidence, limit):
    with pytest.raises(ValueError):
        PvPowerModel(base, incidence, limit)


def test_series_normalizes_irradiation_over_arbitrary_intervals():
    start = datetime(2026, 10, 20, 10, tzinfo=UTC)
    middle, end = start + timedelta(minutes=30), start + timedelta(hours=2)
    weather = [WeatherInterval(middle, end, 100 * 5400),
               WeatherInterval(start, middle, 50 * 1800)]
    series = predict_power_series(weather, PvPowerModel(2, 0), 48.5, 9.5, SolarArrayConfig(143, 60))
    assert [interval.mean_power_w for interval in series] == [100, 200]
    forecast = energy_between(series, start, end)
    assert forecast.energy_wh == 350
    assert forecast.mean_power_w == 175


def test_query_clips_both_edges_and_sorts_intervals():
    start = datetime(2026, 10, 20, 10, tzinfo=UTC)
    series = [PowerInterval(start + timedelta(hours=1), start + timedelta(hours=2), 200),
              PowerInterval(start, start + timedelta(hours=1), 100)]
    forecast = energy_between(series, start + timedelta(minutes=30), start + timedelta(minutes=90))
    assert forecast.energy_wh == 150
    assert forecast.covered_seconds == forecast.expected_seconds == 3600
    assert forecast.data_quality == "heuristic"


def test_tomorrow_contains_no_energy_from_neighboring_days():
    zone = ZoneInfo("Europe/Berlin")
    today, tomorrow, day_after = date(2026, 10, 20), date(2026, 10, 21), date(2026, 10, 22)
    series = [PowerInterval(*local_day_bounds(day, zone), power)
              for day, power in ((today, 1000), (tomorrow, 100), (day_after, 200))]
    assert energy_for_day(series, tomorrow, zone).energy_wh == 2400
    assert energy_for_day(series, day_after, zone).energy_wh == 4800
    now = datetime(2026, 10, 20, 10, tzinfo=zone)
    end = forecast_horizon_end(now, zone)
    assert end == datetime(2026, 10, 23, tzinfo=zone).astimezone(UTC)
    assert (end - now.astimezone(UTC)).total_seconds() > 48 * 3600


@pytest.mark.parametrize("day,hours", [(date(2026, 3, 29), 23), (date(2026, 10, 25), 25)])
def test_calendar_energy_uses_elapsed_time_across_dst(day, hours):
    zone = ZoneInfo("Europe/Berlin")
    start, end = local_day_bounds(day, zone)
    local_start, local_end = start.astimezone(zone), end.astimezone(zone)
    result = energy_for_day([PowerInterval(local_start, local_end, 100)], day, zone)
    assert result.expected_seconds == hours * 3600
    assert result.energy_wh == hours * 100


@pytest.mark.parametrize("power", [None, 0])
def test_known_zero_and_missing_power_are_distinct(power):
    start = datetime(2026, 10, 20, tzinfo=UTC)
    end = start + timedelta(hours=1)
    forecast = energy_between([PowerInterval(start, end, power)], start, end)
    assert forecast.energy_wh == (None if power is None else 0)
    assert forecast.covered_seconds == (0 if power is None else 3600)
    assert forecast.reason == ("incomplete_coverage" if power is None else "complete")


def test_partial_coverage_is_unavailable_instead_of_a_smaller_day_total():
    start = datetime(2026, 10, 20, tzinfo=UTC)
    series = [PowerInterval(start, start + timedelta(hours=1), 100),
              PowerInterval(start + timedelta(hours=2), start + timedelta(hours=3), 200)]
    result = energy_between(series, start, start + timedelta(hours=3))
    assert result.energy_wh is None
    assert result.mean_power_w is None
    assert result.covered_seconds == 7200
    assert result.expected_seconds == 10800
    assert result.data_quality == "unavailable"


def test_empty_series_and_small_gaps_do_not_create_a_complete_forecast():
    start = datetime(2026, 10, 20, tzinfo=UTC)
    end = start + timedelta(hours=1)
    assert energy_between([], start, end).energy_wh is None
    series = [PowerInterval(start + timedelta(microseconds=1), end, 100)]
    assert energy_between(series, start, end).energy_wh is None


def test_missing_weather_stays_missing_in_the_series():
    start = datetime(2026, 10, 20, tzinfo=UTC)
    end = start + timedelta(hours=1)
    series = predict_power_series(
        [WeatherInterval(start, end, None)], PvPowerModel(1, 1),
        48.5, 9.5, SolarArrayConfig(143, 60),
    )
    assert series[0].mean_power_w is None
    assert energy_between(series, start, end).energy_wh is None


def test_overlaps_cannot_double_count_energy():
    start = datetime(2026, 10, 20, tzinfo=UTC)
    end = start + timedelta(hours=1)
    with pytest.raises(ValueError, match="overlap"):
        energy_between([PowerInterval(start, end, 100)] * 2, start, end)
    with pytest.raises(ValueError, match="overlap"):
        predict_power_series(
            [WeatherInterval(start, end, 0)] * 2,
            PvPowerModel(1, 1), 48.5, 9.5, SolarArrayConfig(143, 60),
        )


@pytest.mark.parametrize("kind", [PowerInterval, WeatherInterval])
def test_interval_requires_aware_and_increasing_times(kind):
    start = datetime(2026, 10, 20)
    with pytest.raises(ValueError, match="timezone"):
        kind(start, start + timedelta(hours=1), 0)
    with pytest.raises(ValueError, match="after start"):
        kind(start.replace(tzinfo=UTC), start.replace(tzinfo=UTC), 0)
