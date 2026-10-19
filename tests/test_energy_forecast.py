from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from energy_manager.forecast.forecast import InputPowerHistory
from energy_manager.forecast.forecast_data_collection import update_input_history
from energy_manager.models import BatteryState


def test_statistics_and_minimum_sample_count():
    history = InputPowerHistory(600, 2, 61)
    first = history.update(0, 100)
    assert first.mean_w == 100
    assert first.standard_deviation_w == 0
    assert not first.has_minimum_samples
    result = history.update(10, 300)
    assert result.sample_count == 2
    assert result.mean_w == 200
    assert result.standard_deviation_w == 100
    assert (result.min_w, result.max_w) == (100, 300)
    assert result.span_seconds == 10
    assert result.latest_age_seconds == 0
    assert result.has_minimum_samples


def test_window_boundary_and_expiration_without_new_data():
    history = InputPowerHistory(600, 1, 61)
    history.update(0, 800)
    boundary = history.update(600, None)
    assert boundary.sample_count == 1
    assert boundary.latest_age_seconds == 600
    expired = history.update(601, None)
    assert expired.sample_count == 0
    assert expired.mean_w is None
    assert expired.standard_deviation_w is None
    assert expired.min_w is None
    assert expired.max_w is None
    assert expired.latest_age_seconds is None
    assert not expired.has_minimum_samples


@pytest.mark.parametrize("power", [None, -1, float("nan"), float("inf"), -float("inf")])
def test_invalid_input_is_skipped(power):
    history = InputPowerHistory(600, 1, 61)
    assert history.update(0, power).sample_count == 0
    assert history.update(10, 0).mean_w == 0


def test_capacity_and_duplicate_timestamps():
    history = InputPowerHistory(600, 2, 3)
    for now in range(10):
        result = history.update(now, now * 100)
    assert result.sample_count == 3
    assert result.mean_w == 800
    assert history.update(9, 0) == result


@pytest.mark.parametrize("now", [-1, float("nan"), float("inf")])
def test_invalid_or_backwards_time_is_rejected(now):
    history = InputPowerHistory(600, 1, 61)
    history.update(0, 100)
    with pytest.raises(ValueError):
        history.update(now, 100)


@pytest.mark.parametrize("window,minimum,maximum", [(0, 1, 2), (600, 0, 2), (600, 3, 2)])
def test_invalid_history_configuration(window, minimum, maximum):
    with pytest.raises(ValueError):
        InputPowerHistory(window, minimum, maximum)


@pytest.mark.parametrize("age,expected_count", [(0, 1), (120, 1), (121, 0), (-1, 0)])
def test_adapter_filters_stale_and_future_measurements(age, expected_count):
    now = datetime(2026, 10, 15, tzinfo=UTC)
    measured_at = now - timedelta(seconds=age)
    battery = BatteryState(40, 800, 130, measured_at, measured_at)
    state = {}
    with patch("energy_manager.forecast.forecast_data_collection.datetime") as clock:
        clock.now.return_value = now
        update_input_history(battery, state, 120)
    assert state["input_statistics"].sample_count == expected_count


def test_history_updates_independently_of_csv_interval_and_survives_missing_input():
    now = datetime.now(UTC)
    battery = BatteryState(40, 800, 130, now, now)
    state = {"last_log_at": 0}
    with patch(
        "energy_manager.forecast.forecast_data_collection.time.monotonic",
        side_effect=[0, 10, 611],
    ):
        update_input_history(battery, state, 120)
        update_input_history(battery, state, 120)
        assert state["input_statistics"].sample_count == 2
        update_input_history(None, state, 120)
    assert state["input_statistics"].sample_count == 0
    assert state["last_log_at"] == 0
