import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from energy_manager import export_energy, settings
from energy_manager.daily_energy import LOCAL_TIMEZONE
from energy_manager.export_energy import (
    EXPORT_OUTPUTS,
    ExportEnergy,
    estimate_export_power,
    read_export_energy,
    save_export_energy,
    update_export_energy,
)
from energy_manager.ha_reporting import _publish_export_energy
from energy_manager.models import (
    BatteryState,
    LoadEstimate,
    OutputConfig,
    PowerContribution,
    PowerContributionStatus,
    PowerContributionType,
)


@pytest.fixture
def clock(monkeypatch, tmp_path):
    current = [datetime(2026, 11, 6, 12, tzinfo=UTC)]
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return current[0].astimezone(tz)
    monkeypatch.setattr(export_energy, "datetime", Clock)
    monkeypatch.setattr(settings, "EXPORT_ENERGY_STATE_PATH", tmp_path / "export.json")
    return current


def load_estimate(status=PowerContributionStatus.VALID):
    contribution = PowerContribution(
        "consumer", 150, PowerContributionType.MEASURED, "sensor.consumer", status,
    )
    return LoadEstimate(150, (contribution,))


@pytest.mark.parametrize("output,load,expected", [
    (250, 180, 70), (100, 180, 0), (180, 180, 0), (800, 0, 800),
    (None, 100, None), (float("nan"), 100, None), (float("inf"), 100, None),
    (-1, 100, None), (200, float("nan"), None), (200, -1, None),
])
def test_export_power_uses_only_output_minus_current_load(output, load, expected):
    assert estimate_export_power(output, load) == expected


def test_trapezoidal_integration_and_import_never_reduces_counters(clock):
    now = clock[0]
    energy = ExportEnergy(now.date(), now)
    energy.update(now, 40, True, 30)
    energy.update(now + timedelta(seconds=30), 80, True, 30)
    assert energy.today_wh == energy.total_wh == pytest.approx(0.5)
    assert energy.total_complete
    assert not energy.today_complete  # No earlier part of the first day is invented.
    energy.update(now + timedelta(seconds=60), 0, True, 30)
    energy.update(now + timedelta(seconds=90), 0, True, 30)
    assert energy.today_wh == energy.total_wh == pytest.approx(0.5 + 1 / 3)


def test_midnight_splits_today_but_total_keeps_entire_interval():
    midnight = datetime(2026, 11, 7, tzinfo=LOCAL_TIMEZONE).astimezone(UTC)
    start = midnight - timedelta(seconds=10)
    energy = ExportEnergy(start.astimezone(LOCAL_TIMEZONE).date(), start)
    energy.update(start, 40, True, 30)
    energy.today_wh, energy.total_wh = 100, 500
    energy.update(midnight + timedelta(seconds=10), 80, True, 30)
    assert energy.day.isoformat() == "2026-11-07"
    assert energy.today_wh == pytest.approx(70 * 10 / 3600)
    assert energy.total_wh == pytest.approx(500 + 60 * 20 / 3600)
    assert energy.today_complete


@pytest.mark.parametrize("month,day,hours", [(3, 29, 23), (10, 25, 25)])
def test_dst_days_use_actual_elapsed_time(month, day, hours):
    start = datetime(2026, month, day, tzinfo=LOCAL_TIMEZONE).astimezone(UTC)
    end = datetime(2026, month, day + 1, tzinfo=LOCAL_TIMEZONE).astimezone(UTC)
    energy = ExportEnergy(start.astimezone(LOCAL_TIMEZONE).date(), start)
    for seconds in range(0, int((end - start).total_seconds()) + 1, 30):
        energy.update(start + timedelta(seconds=seconds), 100, True, 30)
    assert energy.total_wh == pytest.approx(100 * hours)
    assert energy.today_wh == 0
    assert energy.today_complete


@pytest.mark.parametrize("status", list(PowerContributionStatus))
def test_invalid_load_contributions_still_integrate_and_persist_quality(clock, status):
    state = {}
    for _ in range(2):
        now = clock[0]
        battery = BatteryState(40, 700, 250, now, now)
        energy = update_export_energy(battery, load_estimate(status), state, 120)
        clock[0] += timedelta(seconds=10)
    assert energy.power_w == 100  # Neither the 700 W PV input nor 130 W forecast is added.
    assert energy.today_wh == energy.total_wh == pytest.approx(100 * 10 / 3600)
    assert energy.today_load_valid == (status is PowerContributionStatus.VALID)
    assert energy.total_load_valid == energy.today_load_valid
    restored = read_export_energy(settings.EXPORT_ENERGY_STATE_PATH, clock[0])
    assert restored.today_load_valid == energy.today_load_valid
    assert restored.total_load_valid == energy.total_load_valid


def test_quality_is_sticky_for_each_period_but_independent_of_coverage(clock):
    now = clock[0]
    energy = ExportEnergy(now.date(), now, today_complete=True)
    energy.update(now, 100, False, 30)
    energy.update(now + timedelta(seconds=10), 100, True, 30)
    assert energy.today_complete and energy.total_complete
    assert not energy.today_load_valid and not energy.total_load_valid


def test_quality_resets_only_for_new_day_and_accounts_for_boundary_sample():
    midnight = datetime(2026, 11, 7, tzinfo=LOCAL_TIMEZONE).astimezone(UTC)
    start = midnight - timedelta(seconds=10)
    energy = ExportEnergy(start.astimezone(LOCAL_TIMEZONE).date(), start)
    energy.update(start, 100, False, 30)
    energy.update(midnight + timedelta(seconds=10), 100, True, 30)
    assert not energy.today_load_valid  # The interval includes a degraded previous sample.
    next_day = midnight + timedelta(days=1)
    energy.update(next_day, 100, True, 30)
    assert energy.today_load_valid
    assert not energy.total_load_valid
    assert not energy.today_complete  # A full day of missing observations is not filled.


@pytest.mark.parametrize("bad", [None, -1, float("nan"), float("inf")])
def test_invalid_output_interrupts_integration_without_bridging(clock, bad):
    now = clock[0]
    energy = ExportEnergy(now.date(), now, today_complete=True)
    energy.update(now, 100, True, 30)
    energy.update(now + timedelta(seconds=10), bad, True, 30)
    assert energy.power_w is None
    energy.update(now + timedelta(seconds=20), 100, True, 30)
    assert energy.today_wh == energy.total_wh == 0
    assert not energy.today_complete and not energy.total_complete
    energy.update(now + timedelta(seconds=30), 100, True, 30)
    assert energy.total_wh == pytest.approx(100 * 10 / 3600)


def test_long_gap_is_skipped_then_integration_resumes(clock):
    now = clock[0]
    energy = ExportEnergy(now.date(), now)
    energy.update(now, 100, True, 30)
    energy.update(now + timedelta(minutes=5), 100, True, 30)
    assert energy.total_wh == 0
    energy.update(now + timedelta(minutes=5, seconds=10), 100, True, 30)
    assert energy.total_wh == pytest.approx(100 * 10 / 3600)
    assert not energy.total_complete


def test_clock_rollback_and_duplicates_do_not_count_overlapping_intervals(clock):
    now = clock[0]
    energy = ExportEnergy(now.date(), now)
    energy.update(now, 100, True, 30)
    energy.update(now + timedelta(seconds=10), 100, True, 30)
    energy.update(now + timedelta(seconds=10), 100, True, 30)
    energy.update(now - timedelta(seconds=10), 100, True, 30)
    energy.update(now + timedelta(seconds=20), 100, True, 30)
    assert energy.total_wh == pytest.approx(100 * 10 / 3600)
    assert not energy.total_complete
    energy.update(now + timedelta(seconds=30), 100, True, 30)
    assert energy.total_wh == pytest.approx(100 * 20 / 3600)


def test_restart_preserves_counters_and_start_without_integrating_downtime(clock):
    now = clock[0]
    path = settings.EXPORT_ENERGY_STATE_PATH
    energy = ExportEnergy(now.date(), now, 123, 456, True)
    energy.update(now, 100, True, 30)
    save_export_energy(energy, path)
    restored = read_export_energy(path, now)
    restored.update(now + timedelta(seconds=10), 800, True, 30)
    assert restored.today_wh == 123 and restored.total_wh == 456
    assert restored.started_at == now
    assert not restored.today_complete and not restored.total_complete
    assert not path.with_suffix(".tmp").exists()
    restored.update(now + timedelta(days=2), 100, True, 30)
    assert restored.today_wh == 0 and restored.total_wh == 456


@pytest.mark.parametrize("field,value", [
    ("today_wh", -1), ("total_wh", float("nan")), ("total_wh", True),
    ("today_wh", 999), ("today_load_valid", "false"), ("total_load_valid", 1),
    ("started_at", "2026-11-06T12:00:00"), ("day", "2026-11-05"),
])
def test_invalid_persisted_state_is_rejected_without_reset(clock, field, value):
    now = clock[0]
    path = settings.EXPORT_ENERGY_STATE_PATH
    save_export_energy(ExportEnergy(now.date(), now, 123, 456), path)
    data = json.loads(path.read_text())
    data[field] = value
    path.write_text(json.dumps(data))
    original = path.read_bytes()
    with pytest.raises(ValueError):
        update_export_energy(None, load_estimate(), {}, 120)
    assert path.read_bytes() == original


@pytest.mark.parametrize("age", [None, -1, 121])
def test_missing_stale_and_future_battery_data_make_power_unavailable(clock, age):
    now = clock[0]
    battery = None if age is None else BatteryState(
        40, 100, 250, now - timedelta(seconds=age), now,
    )
    energy = update_export_energy(battery, load_estimate(), {}, 120)
    assert energy.power_w is None and energy.total_wh == 0
    assert not energy.total_complete


def test_first_start_at_midnight_can_cover_the_whole_day(clock):
    now = datetime(2026, 11, 7, tzinfo=LOCAL_TIMEZONE)
    energy = read_export_energy(settings.EXPORT_ENERGY_STATE_PATH, now)
    assert energy.today_complete
    assert energy.total_complete
    assert energy.today_wh == energy.total_wh == 0


def test_ha_publishes_three_configurable_sensors_and_period_quality(clock):
    now = clock[0]
    energy = ExportEnergy(now.date(), now, 123, 456, True, False, False, False)
    energy.power_w = 70
    config = SimpleNamespace(outputs={key: OutputConfig(f"sensor.{key}") for key in EXPORT_OUTPUTS})
    ha = Mock()
    issues = [{"source": "sensor.consumer", "status": "stale"}]
    _publish_export_energy(config, ha, energy, issues)
    calls = {call.args[0]: call for call in ha.publish_state.call_args_list}
    power = calls["sensor.estimated_export_power"]
    today = calls["sensor.estimated_export_energy_today"]
    total = calls["sensor.estimated_export_energy_total"]
    assert power.args[1] == "70.0"
    assert today.args[1] == "0.123000" and total.args[1] == "0.456000"
    assert power.kwargs["attributes"]["quality_issues"] == issues
    assert power.kwargs["attributes"]["state_class"] == "measurement"
    assert today.kwargs["attributes"]["complete"]
    assert not total.kwargs["attributes"]["complete"]
    for call in (today, total):
        attributes = call.kwargs["attributes"]
        assert attributes["unit_of_measurement"] == "kWh"
        assert attributes["device_class"] == "energy"
        assert attributes["state_class"] == "total_increasing"
        assert attributes["estimated"]
        assert not attributes["load_contributions_valid"]
    energy.power_w = None
    _publish_export_energy(config, ha, energy, [])
    assert ha.publish_state.call_args_list[-3].args[1] == "unavailable"


@pytest.mark.parametrize("key", EXPORT_OUTPUTS)
def test_each_ha_output_is_independently_optional(clock, key):
    now = clock[0]
    energy = ExportEnergy(now.date(), now)
    config = SimpleNamespace(outputs={key: OutputConfig("sensor.custom")})
    ha = Mock()
    _publish_export_energy(config, ha, energy, [])
    assert ha.publish_state.call_count == 1
    assert ha.publish_state.call_args.args[0] == "sensor.custom"
    config.outputs = {}
    ha.reset_mock()
    _publish_export_energy(config, ha, energy, [])
    ha.publish_state.assert_not_called()
