import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from energy_manager.daily_energy import (
    LOCAL_TIMEZONE,
    DailyEnergy,
    read_daily_energy,
    save_daily_energy,
    update_daily_energy,
)
from energy_manager.ha_reporting import _publish_daily_energy
from energy_manager.models import BatteryState, OutputConfig


def test_input_and_output_trapezoidal_integration():
    now = datetime(2026, 10, 15, 10, tzinfo=UTC)
    energy = DailyEnergy(now.date())
    energy.update(now, 100, 200, 30)
    energy.update(now + timedelta(seconds=20), 300, 400, 30)
    assert energy.input_wh == pytest.approx(200 * 20 / 3600)
    assert energy.output_wh == pytest.approx(300 * 20 / 3600)
    assert not energy.complete  # Starting at midday does not create earlier data.


def test_midnight_splits_varying_power_and_resets_totals():
    midnight = datetime(2026, 10, 16, tzinfo=LOCAL_TIMEZONE).astimezone(UTC)
    energy = DailyEnergy((midnight - timedelta(seconds=10)).astimezone(LOCAL_TIMEZONE).date())
    energy.update(midnight - timedelta(seconds=10), 100, 200, 30)
    energy.input_wh = energy.output_wh = 1000
    energy.update(midnight + timedelta(seconds=10), 300, 400, 30)
    assert energy.day.isoformat() == "2026-10-16"
    assert energy.input_wh == pytest.approx(250 * 10 / 3600)
    assert energy.output_wh == pytest.approx(350 * 10 / 3600)
    assert energy.complete


@pytest.mark.parametrize("day,hours", [(29, 23), (25, 25)])
def test_dst_days_use_elapsed_utc_time(day, hours):
    month = 3 if day == 29 else 10
    start = datetime(2026, month, day, tzinfo=LOCAL_TIMEZONE).astimezone(UTC)
    end = datetime(2026, month, day + 1, tzinfo=LOCAL_TIMEZONE).astimezone(UTC)
    assert (end - start).total_seconds() == hours * 3600
    energy = DailyEnergy(start.astimezone(LOCAL_TIMEZONE).date())
    for seconds in range(0, hours * 3600, 30):
        energy.update(start + timedelta(seconds=seconds), 100, 200, 30)
    assert energy.input_wh == pytest.approx(100 * (hours - 30 / 3600))
    assert energy.output_wh == pytest.approx(200 * (hours - 30 / 3600))


@pytest.mark.parametrize("bad", [None, -1, float("nan"), float("inf")])
def test_missing_or_invalid_values_do_not_bridge_gaps(bad):
    now = datetime(2026, 10, 15, 10, tzinfo=UTC)
    energy = DailyEnergy(now.date(), complete=True)
    energy.update(now, 100, 200, 30)
    energy.update(now + timedelta(seconds=10), bad, 200, 30)
    energy.update(now + timedelta(seconds=20), 100, 200, 30)
    assert energy.input_wh == energy.output_wh == 0
    assert not energy.complete


def test_large_gap_is_excluded_and_valid_integration_resumes():
    now = datetime(2026, 10, 15, 10, tzinfo=UTC)
    energy = DailyEnergy(now.date(), complete=True)
    energy.update(now, 100, 200, 30)
    energy.update(now + timedelta(minutes=5), 100, 200, 30)
    assert energy.input_wh == energy.output_wh == 0
    energy.update(now + timedelta(minutes=5, seconds=10), 100, 200, 30)
    assert energy.input_wh == pytest.approx(100 / 360)
    assert not energy.complete


def test_duplicate_or_backwards_time_does_not_double_count():
    now = datetime(2026, 10, 15, 10, tzinfo=UTC)
    energy = DailyEnergy(now.date())
    energy.update(now, 100, 200, 30)
    energy.update(now, 100, 200, 30)
    energy.update(now - timedelta(seconds=10), 100, 200, 30)
    energy.update(now + timedelta(seconds=10), 100, 200, 30)
    assert energy.input_wh == pytest.approx(100 / 360)


def test_persistence_retains_totals_without_integrating_downtime(tmp_path):
    now = datetime(2026, 10, 15, 10, tzinfo=UTC)
    path = tmp_path / "energy.json"
    energy = DailyEnergy(now.date(), 123, 456, True)
    energy.update(now, 100, 200, 30)
    save_daily_energy(energy, path)
    restored = read_daily_energy(path, now)
    restored.update(now + timedelta(seconds=10), 800, 800, 30)
    assert restored.input_wh == 123
    assert restored.output_wh == 456
    assert not restored.complete
    assert not path.with_suffix(".tmp").exists()
    restored.update(now + timedelta(days=1), 100, 200, 30)
    assert restored.input_wh == restored.output_wh == 0


def test_absent_or_invalid_state_file(tmp_path):
    path = tmp_path / "energy.json"
    now = datetime.now(UTC)
    assert read_daily_energy(path, now).input_wh == 0
    path.write_text(json.dumps({"day": "2026-10-15", "input_wh": -1, "output_wh": 10}))
    with pytest.raises(ValueError):
        read_daily_energy(path, now)


def test_update_persists_and_filters_stale_measurements(tmp_path):
    now = datetime.now(UTC)
    battery = BatteryState(40, 100, 200, now - timedelta(minutes=10), now)
    path = tmp_path / "energy.json"
    with patch("energy_manager.daily_energy.DAILY_ENERGY_STATE_PATH", path):
        energy = update_daily_energy(battery, {}, 120)
    assert energy._previous is None
    assert path.exists()


def test_publishes_configured_entities_in_kwh_with_quality():
    config = SimpleNamespace(outputs={
        "battery_input_energy_today": OutputConfig("sensor.custom_input_energy"),
        "battery_output_energy_today": OutputConfig("sensor.custom_output_energy"),
    })
    energy = DailyEnergy(datetime(2026, 10, 15).date(), 1500, 2300)
    ha = Mock()
    _publish_daily_energy(config, ha, energy)
    calls = ha.publish_state.call_args_list
    assert calls[0].args == ("sensor.custom_input_energy", "1.500000")
    assert calls[1].args == ("sensor.custom_output_energy", "2.300000")
    attributes = calls[0].kwargs["attributes"]
    assert attributes["unit_of_measurement"] == "kWh"
    assert attributes["state_class"] == "total_increasing"
    assert attributes["complete"] is False
    config.outputs = {}
    ha.reset_mock()
    _publish_daily_energy(config, ha, energy)
    ha.publish_state.assert_not_called()
