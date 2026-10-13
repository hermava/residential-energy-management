# Structural extraction from run_energy_manager.py (V5).
# Keep behavior/settings unchanged; see REFACTORING_NOTES.md.
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

def _abort_recovery_on_ha_failure(
    config, marstek: MarstekMqttAdapter, recovery_state: dict,
    last_command_w: float | None, last_command_time: float | None,
) -> tuple[float | None, float | None]:
    """Best-effort fallback when HA is unreachable during 400 W recovery.

    Since current load cannot be trusted, request the existing 130 W floor.
    If battery state is missing or SOC-inhibited, leave restoration pending.
    """
    if recovery_state["active_until"] is None:
        return last_command_w, last_command_time
    status = _get_marstek_status(marstek)
    if status is None:
        print("Recovery: HA unavailable; MARSTEK unavailable; restore pending")
        return last_command_w, last_command_time
    battery = status.battery_state
    if not (config.battery.min_soc_percent < battery.soc_percent
            < config.battery.control_inhibit_soc_percent):
        print("Recovery: HA unavailable; SOC-inhibited; restore pending")
        return last_command_w, last_command_time
    # A write is not proof of acceptance. Keep state until MARSTEK reports
    # the requested fallback target in a later get_status() response.
    if abs(status.target_power_w - MIN_TEST_COMMAND_POWER_W) <= RECOVERY_CONFIRM_TOLERANCE_W:
        recovery_state["active_until"] = None
        recovery_state["restore_target_w"] = None
        recovery_state["restore_sent_at"] = None
        recovery_state["last_event"] = "ha_failure_restore_confirmed"
        _save_recovery_state(recovery_state)
        return MIN_TEST_COMMAND_POWER_W, time.monotonic()
    sent_at = recovery_state["restore_sent_at"]
    if (sent_at is None or
        (datetime.now(UTC) - datetime.fromisoformat(sent_at)).total_seconds()
            >= RECOVERY_RETRY_SECONDS):
        recovery_state["restore_target_w"] = MIN_TEST_COMMAND_POWER_W
        recovery_state["restore_sent_at"] = datetime.now(UTC).isoformat()
        recovery_state["last_event"] = "restoring_after_ha_failure"
        _save_recovery_state(recovery_state)
        marstek.set_output_power(MIN_TEST_COMMAND_POWER_W)
        return MIN_TEST_COMMAND_POWER_W, time.monotonic()
    return last_command_w, last_command_time

def _apply_marstek_control(
    config,
    marstek: MarstekMqttAdapter,
    status: MarstekStatus | None,
    target_power_w: float,
    last_command_w: float | None,
    last_command_time: float | None,
    recovery_state: dict,
    recovery_requested: bool,
) -> tuple[float | None, float | None, bool, str, float | None, bool]:
    battery_state = status.battery_state if status is not None else None
    control_inhibited = (
        battery_state is None
        or battery_state.soc_percent <= config.battery.min_soc_percent
        or battery_state.soc_percent
        >= config.battery.control_inhibit_soc_percent
    )
    command_sent = False
    command_reason = "battery_unavailable"
    seconds_since_last_command = None
    if status is None:
        # Keep the persisted recovery deadline. Restore when communication returns.
        return (last_command_w, last_command_time, command_sent,
                command_reason, seconds_since_last_command, control_inhibited)
    now = time.monotonic()
    wall_now = datetime.now(UTC)
    if last_command_w is None:
        last_command_w = status.target_power_w
        last_command_time = now
        if recovery_state["active_until"] is None:
            return (last_command_w, last_command_time, False, "initializing",
                    seconds_since_last_command, control_inhibited)
    assert last_command_time is not None
    seconds_since_last_command = now - last_command_time
    # An outstanding recovery always takes priority over normal control.
    # It survives process restarts, lost MQTT and short HA outages.
    active_until = recovery_state["active_until"]
    if active_until is not None:
        deadline = datetime.fromisoformat(active_until)
        interrupted_by_load = target_power_w > RECOVERY_TARGET_W
        restoring = recovery_state["restore_target_w"] is not None

        if control_inhibited:
            command_reason = "recovery_restore_pending_soc"
        elif not restoring and wall_now < deadline and not interrupted_by_load:
            command_reason = "recovery_hold"
        else:
            # Always restore toward the CURRENT controller target. Recovery
            # remains pending until the device confirms that target.
            command_target_w = max(
                MIN_TEST_COMMAND_POWER_W, float(round(target_power_w))
            )
            restore_target = recovery_state["restore_target_w"]
            previous_sent_at = recovery_state["restore_sent_at"]
            acknowledged = (
                restore_target is not None and
                abs(status.target_power_w - restore_target) <= RECOVERY_CONFIRM_TOLERANCE_W
            )
            # Avoid chasing the load by issuing a command every 10 s. Once a
            # restore has been requested, confirm THAT target and leave normal
            # regulation to resume afterward.
            if acknowledged:
                recovery_state["active_until"] = None
                recovery_state["restore_target_w"] = None
                recovery_state["restore_sent_at"] = None
                command_reason = "recovery_restore_confirmed"
                recovery_state["last_event"] = command_reason
                _save_recovery_state(recovery_state)
            elif (previous_sent_at is None or
                  (wall_now - datetime.fromisoformat(previous_sent_at)).total_seconds()
                    >= RECOVERY_RETRY_SECONDS):
                recovery_state["restore_target_w"] = command_target_w
                recovery_state["restore_sent_at"] = wall_now.isoformat()
                command_reason = (
                    "recovery_interrupted_by_load" if interrupted_by_load and not restoring
                    else "recovery_restore_sent"
                )
                recovery_state["last_event"] = command_reason
                _save_recovery_state(recovery_state)
                marstek.set_output_power(command_target_w)
                last_command_w = command_target_w
                last_command_time = now
                command_sent = True
            else:
                command_reason = "recovery_awaiting_confirmation"
        return (last_command_w, last_command_time, command_sent,
                command_reason, seconds_since_last_command, control_inhibited)
    if control_inhibited:
        command_reason = (
            "low_soc" if battery_state.soc_percent <= config.battery.min_soc_percent
            else "high_soc"
        )
    else:
        command_target_w = max(
            MIN_TEST_COMMAND_POWER_W, float(round(target_power_w))
        )
        last_attempt = recovery_state["last_attempt_at"]
        cooldown_expired = (
            last_attempt is None or
            (wall_now - datetime.fromisoformat(last_attempt)).total_seconds()
            >= RECOVERY_COOLDOWN_SECONDS
        )
        can_start_recovery = (
            RECOVERY_ENABLED
            and recovery_requested
            and cooldown_expired
            and seconds_since_last_command >= MIN_COMMAND_INTERVAL_SECONDS
            and battery_state.soc_percent >= (
                config.battery.min_soc_percent + RECOVERY_SOC_MARGIN_PERCENT
            )
            and target_power_w < RECOVERY_TARGET_W
        )

        if can_start_recovery:
            # Persist first: a crash immediately after the MQTT write must not
            # leave the 400 W target without a scheduled restoration.
            recovery_state["active_until"] = (
                wall_now + timedelta(seconds=RECOVERY_DURATION_SECONDS)
            ).isoformat()
            recovery_state["last_attempt_at"] = wall_now.isoformat()
            recovery_state["last_event"] = "recovery_started"
            recovery_state["restore_target_w"] = None
            recovery_state["restore_sent_at"] = None
            _save_recovery_state(recovery_state)
            marstek.set_output_power(RECOVERY_TARGET_W)
            last_command_w = RECOVERY_TARGET_W
            last_command_time = now
            command_sent = True
            command_reason = "recovery_started"
        else:
            target_difference_w = abs(command_target_w - last_command_w)
            if target_difference_w < COMMAND_DEADBAND_W:
                command_reason = "deadband"
            elif seconds_since_last_command < MIN_COMMAND_INTERVAL_SECONDS:
                command_reason = "minimum_hold_time"
            else:
                marstek.set_output_power(command_target_w)
                last_command_w = command_target_w
                last_command_time = now
                command_sent = True
                command_reason = "command_sent"
    return (last_command_w, last_command_time, command_sent,
            command_reason, seconds_since_last_command, control_inhibited)
