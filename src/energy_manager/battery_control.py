import time
from datetime import UTC, datetime, timedelta

from energy_manager.adapters.marstek_mqtt import MarstekMqttAdapter, MarstekStatus
from energy_manager.recovery import _save_recovery_state
from energy_manager.settings import (
    COMMAND_DEADBAND_W,
    MIN_COMMAND_INTERVAL_SECONDS,
    MIN_TEST_COMMAND_POWER_W,
    RECOVERY_CONFIRM_TOLERANCE_W,
    RECOVERY_COOLDOWN_SECONDS,
    RECOVERY_DURATION_SECONDS,
    RECOVERY_ENABLED,
    RECOVERY_RETRY_SECONDS,
    RECOVERY_SOC_MARGIN_PERCENT,
    RECOVERY_TARGET_W,
)


def _get_marstek_status(marstek: MarstekMqttAdapter) -> MarstekStatus | None:
    try:
        return marstek.get_status()
    except TimeoutError:
        return None


def _is_low_soc(config, status: MarstekStatus) -> bool:
    return status.battery_state.soc_percent <= config.battery.min_soc_percent


def _is_high_soc(config, status: MarstekStatus) -> bool:
    return status.battery_state.soc_percent >= config.battery.control_inhibit_soc_percent


def _clear_recovery(recovery_state: dict, event: str) -> None:
    recovery_state["active_until"] = None
    recovery_state["restore_target_w"] = None
    recovery_state["restore_sent_at"] = None
    recovery_state["last_event"] = event
    _save_recovery_state(recovery_state)


def _handle_low_soc(
    marstek: MarstekMqttAdapter,
    status: MarstekStatus,
    last_command_w: float,
    last_command_time: float,
    recovery_state: dict,
    now: float,
) -> tuple[float, float, bool, str]:
    if recovery_state["active_until"] is not None:
        _clear_recovery(recovery_state, "recovery_aborted_low_soc")
    target_difference_w = status.target_power_w - MIN_TEST_COMMAND_POWER_W
    if target_difference_w <= COMMAND_DEADBAND_W:
        return last_command_w, last_command_time, False, "low_soc"
    if now - last_command_time < MIN_COMMAND_INTERVAL_SECONDS:
        return last_command_w, last_command_time, False, "low_soc_wait"
    marstek.set_output_power(MIN_TEST_COMMAND_POWER_W)
    return MIN_TEST_COMMAND_POWER_W, now, True, "low_soc_restore_sent"


def _handle_active_recovery(
    marstek: MarstekMqttAdapter,
    status: MarstekStatus,
    target_power_w: float,
    last_command_w: float,
    last_command_time: float,
    recovery_state: dict,
    now: float,
    wall_now: datetime,
) -> tuple[float, float, bool, str]:
    deadline = datetime.fromisoformat(recovery_state["active_until"])
    restore_target = recovery_state["restore_target_w"]
    restoring = restore_target is not None
    interrupted_by_load = target_power_w > RECOVERY_TARGET_W
    if not restoring and wall_now < deadline and not interrupted_by_load:
        return last_command_w, last_command_time, False, "recovery_hold"
    if restoring and abs(status.target_power_w - restore_target) <= RECOVERY_CONFIRM_TOLERANCE_W:
        _clear_recovery(recovery_state, "recovery_restore_confirmed")
        return status.target_power_w, last_command_time, False, "recovery_restore_confirmed"
    restore_sent_at = recovery_state["restore_sent_at"]
    retry_allowed = restore_sent_at is None
    if restore_sent_at is not None:
        retry_allowed = (
            (wall_now - datetime.fromisoformat(restore_sent_at)).total_seconds()
            >= RECOVERY_RETRY_SECONDS
        )
    if not retry_allowed:
        return last_command_w, last_command_time, False, "recovery_awaiting_confirmation"
    command_target_w = max(MIN_TEST_COMMAND_POWER_W, float(round(target_power_w)))
    command_reason = (
        "recovery_interrupted_by_load" if interrupted_by_load else "recovery_restore_sent"
    )
    recovery_state["restore_target_w"] = command_target_w
    recovery_state["restore_sent_at"] = wall_now.isoformat()
    recovery_state["last_event"] = command_reason
    _save_recovery_state(recovery_state)
    marstek.set_output_power(command_target_w)
    return command_target_w, now, True, command_reason


def _can_start_recovery(
    config,
    status: MarstekStatus,
    target_power_w: float,
    seconds_since_last_command: float,
    recovery_state: dict,
    recovery_requested: bool,
    wall_now: datetime,
) -> bool:
    if not RECOVERY_ENABLED or not recovery_requested:
        return False
    if seconds_since_last_command < MIN_COMMAND_INTERVAL_SECONDS:
        return False
    soc_percent = status.battery_state.soc_percent
    if soc_percent <= config.battery.min_soc_percent + RECOVERY_SOC_MARGIN_PERCENT:
        return False
    if soc_percent >= config.battery.control_inhibit_soc_percent - RECOVERY_SOC_MARGIN_PERCENT:
        return False
    if target_power_w >= RECOVERY_TARGET_W:
        return False
    last_attempt_at = recovery_state["last_attempt_at"]
    if last_attempt_at is None:
        return True
    seconds_since_last_attempt = (
        wall_now - datetime.fromisoformat(last_attempt_at)
    ).total_seconds()
    return seconds_since_last_attempt >= RECOVERY_COOLDOWN_SECONDS


def _start_recovery(
    marstek: MarstekMqttAdapter,
    recovery_state: dict,
    now: float,
    wall_now: datetime,
) -> tuple[float, float, bool, str]:
    recovery_state["active_until"] = (
        wall_now + timedelta(seconds=RECOVERY_DURATION_SECONDS)
    ).isoformat()
    recovery_state["last_attempt_at"] = wall_now.isoformat()
    recovery_state["restore_target_w"] = None
    recovery_state["restore_sent_at"] = None
    recovery_state["last_event"] = "recovery_started"
    _save_recovery_state(recovery_state)
    marstek.set_output_power(RECOVERY_TARGET_W)
    return RECOVERY_TARGET_W, now, True, "recovery_started"


def _apply_normal_control(
    marstek: MarstekMqttAdapter,
    target_power_w: float,
    last_command_w: float,
    last_command_time: float,
    now: float,
) -> tuple[float, float, bool, str]:
    command_target_w = max(MIN_TEST_COMMAND_POWER_W, float(round(target_power_w)))
    if abs(command_target_w - last_command_w) < COMMAND_DEADBAND_W:
        return last_command_w, last_command_time, False, "deadband"
    if now - last_command_time < MIN_COMMAND_INTERVAL_SECONDS:
        return last_command_w, last_command_time, False, "minimum_hold_time"
    marstek.set_output_power(command_target_w)
    return command_target_w, now, True, "command_sent"


def _apply_marstek_control(
    config,
    marstek: MarstekMqttAdapter,
    status: MarstekStatus | None,
    target_power_w: float,
    last_command_w: float | None,
    last_command_time: float | None,
    recovery_state: dict,
    recovery_requested: bool,
) -> tuple[
    float | None,
    float | None,
    bool,
    str,
    float | None,
    bool,
]:
    if status is None:
        return last_command_w, last_command_time, False, "battery_unavailable", None, True
    now = time.monotonic()
    wall_now = datetime.now(UTC)
    low_soc = _is_low_soc(config, status)
    high_soc = _is_high_soc(config, status)
    control_inhibited = low_soc or high_soc
    if last_command_w is None:
        last_command_w = status.target_power_w
        last_command_time = now
        if recovery_state["active_until"] is None and not low_soc:
            return last_command_w, last_command_time, False, "initializing", 0.0, control_inhibited
    assert last_command_time is not None
    seconds_since_last_command = now - last_command_time
    if low_soc:
        last_command_w, last_command_time, command_sent, command_reason = _handle_low_soc(
            marstek, status, last_command_w, last_command_time, recovery_state, now
        )
        return (
            last_command_w,
            last_command_time,
            command_sent,
            command_reason,
            seconds_since_last_command,
            True,
        )
    if high_soc:
        return (
            last_command_w,
            last_command_time,
            False,
            "high_soc",
            seconds_since_last_command,
            True,
        )
    if recovery_state["active_until"] is not None:
        last_command_w, last_command_time, command_sent, command_reason = _handle_active_recovery(
            marstek,
            status,
            target_power_w,
            last_command_w,
            last_command_time,
            recovery_state,
            now,
            wall_now,
        )
        return (
            last_command_w,
            last_command_time,
            command_sent,
            command_reason,
            seconds_since_last_command,
            False,
        )
    if _can_start_recovery(
        config,
        status,
        target_power_w,
        seconds_since_last_command,
        recovery_state,
        recovery_requested,
        wall_now,
    ):
        last_command_w, last_command_time, command_sent, command_reason = _start_recovery(
            marstek, recovery_state, now, wall_now
        )
        return (
            last_command_w,
            last_command_time,
            command_sent,
            command_reason,
            seconds_since_last_command,
            False,
        )
    last_command_w, last_command_time, command_sent, command_reason = _apply_normal_control(
        marstek, target_power_w, last_command_w, last_command_time, now
    )
    return (
        last_command_w,
        last_command_time,
        command_sent,
        command_reason,
        seconds_since_last_command,
        False,
    )


def _abort_recovery_on_ha_failure(
    config,
    marstek: MarstekMqttAdapter,
    recovery_state: dict,
    last_command_w: float | None,
    last_command_time: float | None,
) -> tuple[float | None, float | None]:
    if recovery_state["active_until"] is None:
        return last_command_w, last_command_time
    status = _get_marstek_status(marstek)
    if status is None:
        print("Recovery: HA unavailable; MARSTEK unavailable; restore pending")
        return last_command_w, last_command_time
    if status.target_power_w <= MIN_TEST_COMMAND_POWER_W + COMMAND_DEADBAND_W:
        _clear_recovery(recovery_state, "ha_failure_restore_confirmed")
        return status.target_power_w, last_command_time
    if _is_high_soc(config, status):
        print("Recovery: HA unavailable; high SOC; restore pending")
        return last_command_w, last_command_time
    now = time.monotonic()
    if last_command_time is not None and now - last_command_time < MIN_COMMAND_INTERVAL_SECONDS:
        return last_command_w, last_command_time
    recovery_state["restore_target_w"] = MIN_TEST_COMMAND_POWER_W
    recovery_state["restore_sent_at"] = datetime.now(UTC).isoformat()
    recovery_state["last_event"] = "restoring_after_ha_failure"
    _save_recovery_state(recovery_state)
    marstek.set_output_power(MIN_TEST_COMMAND_POWER_W)
    return MIN_TEST_COMMAND_POWER_W, now
