"""Regression checks for the V5 structural extraction; no hardware required.

Run from repository root: python -m pytest tests/test_refactor_regression.py -q
"""
from __future__ import annotations

import csv
import json
import time
from collections import deque
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
import requests

from energy_manager import battery_control, csv_logging, oscillation, recovery, runtime


def battery(soc=40.0, output=130.0, solar=200.0):
    return SimpleNamespace(
        soc_percent=soc,
        output_power_w=output,
        input_power_w=solar,
    )


def status(soc=40.0, target=130.0, output=130.0, solar=200.0):
    return SimpleNamespace(
        battery_state=battery(soc, output, solar),
        target_power_w=target,
        received_at=datetime(2026, 10, 6, 8, 0, tzinfo=UTC),
        output_1_power_w=output / 2,
        output_2_power_w=output / 2,
        charging_setting="cs",
        discharge_setting="cd",
        discharge_depth_percent=85,
        scene="scene",
        discharge_setting_mode="mode",
        temperature_low=23,
        temperature_high=26,
        raw_payload="payload",
        raw_values={key: 0 for key in (
            "p1", "p2", "o1", "o2", "st", "bs", "tc", "tf"
        )},
    )


def config():
    return SimpleNamespace(battery=SimpleNamespace(
        min_soc_percent=20,
        control_inhibit_soc_percent=95,
    ))


def empty_recovery():
    return {
        "active_until": None,
        "last_attempt_at": None,
        "last_event": "idle",
        "restore_target_w": None,
        "restore_sent_at": None,
    }


def control(*, soc=40, device_target=130, controller_target=130,
            previous_target=130, hold_seconds=120, recovery_requested=False,
            recovery_state=None):
    device = Mock()
    item = status(soc=soc, target=device_target)
    state = recovery_state or empty_recovery()
    with patch.object(battery_control, "_save_recovery_state"):
        result = battery_control._apply_marstek_control(
            config(), device, item, controller_target,
            previous_target, time.monotonic() - hold_seconds,
            state, recovery_requested,
        )
    return result, device, state


def test_initialize_from_real_device_target():
    device = Mock()
    result = battery_control._apply_marstek_control(
        config(), device, status(target=135), 200, None, None,
        empty_recovery(), False,
    )
    assert result[0] == 135
    assert result[3] == "initializing"
    device.set_output_power.assert_not_called()


def test_low_soc_inhibits_new_command():
    result, device, _ = control(soc=20, controller_target=500)
    assert result[3] == "low_soc"
    assert result[5] is True
    device.set_output_power.assert_not_called()


def test_high_soc_inhibits_new_command():
    result, device, _ = control(soc=97, controller_target=500)
    assert result[3] == "high_soc"
    device.set_output_power.assert_not_called()


def test_minimum_command_floor_and_deadband():
    result, device, _ = control(controller_target=41, previous_target=120, device_target=120)
    assert result[3] == "deadband"
    device.set_output_power.assert_not_called()
    result, device, _ = control(controller_target=41, previous_target=200, device_target=200)
    assert result[3] == "command_sent"
    device.set_output_power.assert_called_once_with(120.0)


def test_minimum_interval_still_applies():
    result, device, _ = control(controller_target=500, hold_seconds=5)
    assert result[3] == "minimum_hold_time"
    device.set_output_power.assert_not_called()


def test_normal_command_after_hold():
    result, device, _ = control(controller_target=500)
    assert result[3] == "command_sent"
    assert result[2] is True
    device.set_output_power.assert_called_once_with(500.0)


def test_recovery_starts_at_800_w_and_persists_intent():
    result, device, state = control(
        controller_target=41, recovery_requested=True,
    )
    assert result[3] == "recovery_started"
    assert state["active_until"] is not None
    assert state["last_attempt_at"] is not None
    device.set_output_power.assert_called_once_with(800.0)


def test_recovery_holds_until_deadline():
    state = empty_recovery()
    state["active_until"] = (datetime.now(UTC) + timedelta(seconds=40)).isoformat()
    result, device, _ = control(
        device_target=800, controller_target=41, recovery_state=state,
    )
    assert result[3] == "recovery_hold"
    device.set_output_power.assert_not_called()


def test_expired_recovery_requests_restoration_and_confirmation():
    state = empty_recovery()
    state["active_until"] = (datetime.now(UTC) - timedelta(seconds=10)).isoformat()
    result, device, state = control(
        device_target=800, controller_target=41, recovery_state=state,
    )
    assert result[3] == "recovery_restore_sent"
    assert state["restore_target_w"] == 120.0
    assert state["active_until"] is not None
    device.set_output_power.assert_called_once_with(120.0)
    result, device, state = control(
        device_target=120, controller_target=41, recovery_state=state,
    )
    assert result[3] == "recovery_restore_confirmed"
    assert state["active_until"] is None
    device.set_output_power.assert_not_called()


def test_oscillation_window_stable():
    history = deque((i * 10.0, 130.0 + i % 2, 130.0, 200.0)
                    for i in range(13))
    detected, reason, peak, reversals, solar_range = oscillation._assess_oscillation(history)
    assert detected is False
    assert reason == "stable"
    assert peak == 1.0
    assert reversals == 0
    assert solar_range == 0


def test_oscillation_window_detects_large_reversals():
    values = [100, 140, 105, 144, 100, 138, 104, 144, 100, 135, 101, 140, 102]
    history = deque((i * 10.0, output, 130.0, 200.0)
                    for i, output in enumerate(values))
    detected, reason, peak, reversals, _ = oscillation._assess_oscillation(history)
    assert detected is True
    assert reason == "oscillation"
    assert peak == 44
    assert reversals >= 3


def test_changed_target_is_not_comparable():
    history = deque((i * 10.0, 100 if i % 2 else 140,
                     130 if i < 7 else 400, 200.0)
                    for i in range(13))
    detected, reason, *_ = oscillation._assess_oscillation(history)
    assert detected is None
    assert reason == "target_changing"


def test_measurement_gap_is_unavailable():
    history = deque((i * 10.0 + (30 if i >= 6 else 0),
                     130, 130, 200) for i in range(13))
    detected, reason, *_ = oscillation._assess_oscillation(history)
    assert detected is None
    assert reason == "measurement_gap"


def test_csv_v5_columns_and_json_are_unchanged(tmp_path):
    filename = tmp_path / "test.csv"
    with patch.object(csv_logging, "_get_daily_log_path", return_value=(filename, False)):
        csv_logging._append_marstek_log(
            status=status(), controller_target_w=41,
            control_inhibited=False, last_command_w=130,
            command_sent=False, command_reason="deadband",
            seconds_since_last_command=60,
            quality_issues=[{"source": "washer", "status": "stale"}],
            measurement_diagnostics={"washer": {"age_seconds": 31.0}},
            oscillation_assessment={
                "oscillation_state": "on", "oscillation_detected": True,
                "oscillation_reason": "oscillation", "output_range_w": 42.0,
                "direction_reversals": 5, "input_range_w": 7.0,
            },
            recovery_info={
                "recovery_state": "idle", "recovery_until": None,
                "recovery_last_attempt": None, "recovery_event": "idle",
                "recovery_restore_target_w": None,
            },
        )
    with filename.open() as f:
        row = next(csv.DictReader(f))
    assert row["command_reason"] == "deadband"
    assert row["oscillation_detected"] == "True"
    assert row["recovery_state"] == "idle"
    assert json.loads(row["quality_issues"])[0]["status"] == "stale"
    assert json.loads(row["measurement_diagnostics"])["washer"]["age_seconds"] == 31.0


def test_recovery_info_pending_until_confirmed():
    state = empty_recovery()
    state["active_until"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    info = recovery._recovery_info(state)
    assert info["recovery_state"] == "restore_pending"


@pytest.mark.parametrize("correction_error", [None, ValueError("bad correction source")])
@pytest.mark.parametrize("daily_error", [None, OSError("state save failed")])
@pytest.mark.parametrize(
    "forecast_error", [None, OSError("log failed"), requests.ConnectionError()],
)
def test_runtime_wires_cycle_without_hardware(correction_error, daily_error, forecast_error):
    cfg = config()
    cfg.outputs = {"battery_input_energy_today": Mock()}
    daily_update = Mock(side_effect=daily_error)
    daily_publish = Mock()
    daily_forecast = Mock(side_effect=forecast_error)
    # This is a mocked orchestration test: it checks cross-module wiring
    # without HA, MQTT or device state changes.
    ha = Mock()
    device = Mock()
    estimate = SimpleNamespace(total_power_w=41.0, contributions=[])
    result_assessment = {
        "oscillation_state": "off", "oscillation_detected": False,
        "oscillation_reason": "stable", "output_range_w": 1.0,
        "direction_reversals": 0, "input_range_w": 0.0,
    }
    fields = {"active_until": None}
    history = deque()
    oscillation_state = {"last_detected_at": None}
    forecast_state = {"last_log_at": None}
    cfg.battery.max_age_seconds = 120
    forecast_update = Mock()
    st = status()
    patches = [
        patch.object(runtime, "_collect_ha_inputs", return_value=({}, {})),
        patch.object(runtime, "estimate_load", return_value=estimate),
        patch.object(runtime, "_get_quality_diagnostics", return_value=([], {})),
        patch.object(runtime, "_publish_load_estimate"),
        patch.object(runtime, "_get_marstek_status", return_value=st),
        patch.object(runtime, "calculate_battery_output_target", return_value=41),
        patch.object(
            runtime, 
            "_apply_marstek_control", 
            return_value=(130, 123.0, False, "deadband", 60, False)
            ),
        patch.object(runtime, "_update_oscillation_monitor", return_value=result_assessment),
        patch.object(runtime, "_recovery_info", return_value={"recovery_state": "idle"}),
        patch.object(runtime, "_append_marstek_log"),
        patch.object(runtime, "_publish_battery_states"),
        patch.object(runtime, "_publish_controller_target"),
        patch.object(runtime, "_publish_oscillation_monitor"),
        patch.object(runtime, "_publish_recovery_status"),
        patch.object(runtime, "update_daily_energy", daily_update),
        patch.object(runtime, "_publish_daily_energy", daily_publish),
        patch.object(runtime, "update_daily_forecast", daily_forecast),
        patch.object(runtime, "_print_cycle_status"),
        patch.object(runtime, "update_forecast_logging"),
        patch.object(runtime, "update_input_history", forecast_update),
        patch.object(runtime, "update_forecast_correction", side_effect=correction_error),
    ]
    from contextlib import ExitStack
    with ExitStack() as stack:
        mocks = [stack.enter_context(p) for p in patches]
        returned = runtime._run_cycle(
            cfg, ha, device, 130, 0.0, history,
            oscillation_state, fields, forecast_state,
        )
        assert returned == (130, 123.0)
        mocks[9].assert_called_once()
        mocks[12].assert_called_once()
        mocks[13].assert_called_once()
        mocks[-3].assert_called_once_with(ha, cfg, st.battery_state, forecast_state)
        mocks[-1].assert_called_once_with(ha, cfg, st.battery_state, forecast_state)
        forecast_update.assert_called_once_with(st.battery_state, forecast_state, 120)
        daily_update.assert_called_once_with(st.battery_state, forecast_state, 120)
        daily_forecast.assert_called_once_with(ha, cfg, st.battery_state, forecast_state)
        if daily_error is None:
            daily_publish.assert_called_once_with(cfg, ha, daily_update.return_value)
        else:
            daily_publish.assert_not_called()
        assert oscillation_state["recovery_trigger_ready"] is False
