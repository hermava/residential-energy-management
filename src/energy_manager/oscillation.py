# Structural extraction from run_energy_manager.py (V5).
# Keep behavior/settings unchanged; see REFACTORING_NOTES.md.
import time
from collections import deque

from energy_manager.adapters.marstek_mqtt import MarstekStatus
from energy_manager.settings import (
    OSC_CLEAR_DELAY_SECONDS,
    OSC_MAX_GAP_SECONDS,
    OSC_MAX_INPUT_RANGE_W,
    OSC_MAX_TARGET_RANGE_W,
    OSC_MIN_DIRECTION_CHANGE_W,
    OSC_MIN_OUTPUT_RANGE_W,
    OSC_MIN_REVERSALS,
    OSC_WINDOW_SECONDS,
)


def _count_significant_reversals(values: list[float]) -> int:
    """Count substantial changes of direction, ignoring small fluctuations."""
    if not values:
        return 0

    direction = 0
    extreme = values[0]
    reversals = 0
    threshold = OSC_MIN_DIRECTION_CHANGE_W
    for value in values[1:]:
        if direction == 0:
            if value >= extreme + threshold:
                direction, extreme = 1, value
            elif value <= extreme - threshold:
                direction, extreme = -1, value
        elif direction == 1:
            if value > extreme:
                extreme = value
            elif value <= extreme - threshold:
                direction, extreme = -1, value
                reversals += 1
        else:
            if value < extreme:
                extreme = value
            elif value >= extreme + threshold:
                direction, extreme = 1, value
                reversals += 1

    return reversals

def _assess_oscillation(history: deque) -> \
    tuple[bool | None, str, float | None, int | None, float | None]:
    """Return detection, reason, output range and reversal count.
    None means data are insufficient or not comparable; it does NOT mean stable.
    """
    if len(history) < 10 or history[-1][0] - history[0][0] < 110:
        return None, "collecting", None, None, None

    if any(right[0] - left[0] > OSC_MAX_GAP_SECONDS
           for left, right in zip(history, list(history)[1:])):
        return None, "measurement_gap", None, None, None
    targets = [item[2] for item in history]
    inputs = [item[3] for item in history]
    outputs = [item[1] for item in history]
    output_range = max(outputs) - min(outputs)
    input_range = max(inputs) - min(inputs)
    if max(targets) - min(targets) > OSC_MAX_TARGET_RANGE_W:
        return None, "target_changing", output_range, None, input_range

    reversals = _count_significant_reversals(outputs)
    detected = (output_range >= OSC_MIN_OUTPUT_RANGE_W
                and reversals >= OSC_MIN_REVERSALS)
    reason = "oscillation" if detected else "stable"
    if input_range > OSC_MAX_INPUT_RANGE_W:
        reason += "_variable_input"
    return detected, reason, output_range, reversals, input_range

def _update_oscillation_monitor(
    status: MarstekStatus | None,
    command_sent: bool,
    control_inhibited: bool,
    recovery_active: bool,
    history: deque,
    monitor_state: dict,
) -> dict:
    """Assess oscillation once per cycle without changing battery commands."""
    now = time.monotonic()
    reason = "collecting"
    output_range = None
    reversals = None
    input_range = None
    detected = None
    if status is None:
        history.clear()
        monitor_state["last_detected_at"] = None
        reason = "battery_unavailable"
    elif recovery_active:
        history.clear()
        monitor_state["last_detected_at"] = None
        reason = "recovery_active"
    elif control_inhibited:
        history.clear()
        monitor_state["last_detected_at"] = None
        reason = "control_inhibited"
    elif command_sent:
        history.clear()
        monitor_state["last_detected_at"] = None
        reason = "command_sent"
    else:
        history.append((
            now,
            status.battery_state.output_power_w,
            status.target_power_w,
            status.battery_state.input_power_w,
        ))
        while history and now - history[0][0] > OSC_WINDOW_SECONDS:
            history.popleft()
        detected, reason, output_range, reversals, input_range = _assess_oscillation(history)

    if detected is True:
        monitor_state["last_detected_at"] = now
        result = "on"
    elif detected is None:
        monitor_state["last_detected_at"] = None
        result = "unavailable"
    else:
        last_detected_at = monitor_state["last_detected_at"]
        result = (
            "on" if last_detected_at is not None
            and now - last_detected_at < OSC_CLEAR_DELAY_SECONDS
            else "off"
        )
        if result == "off":
            monitor_state["last_detected_at"] = None
        else:
            reason = "waiting_for_stability"
    return {
        "oscillation_state": result,
        "oscillation_detected": detected,
        "oscillation_reason": reason,
        "output_range_w": round(output_range, 1) if output_range is not None else None,
        "direction_reversals": reversals,
        "input_range_w": round(input_range, 1) if input_range is not None else None,
    }
