import csv
import json
import time
from collections import deque
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import requests

from energy_manager.adapters.home_assistant import HomeAssistantAdapter
from energy_manager.adapters.marstek_mqtt import MarstekMqttAdapter, MarstekStatus
from energy_manager.config import (
    get_home_assistant_token,
    get_home_assistant_url,
    get_marstek_device_mac,
    get_marstek_device_type,
    get_mqtt_host,
    get_mqtt_password,
    get_mqtt_port,
    get_mqtt_username,
)
from energy_manager.config_loader import load_config
from energy_manager.controller import calculate_battery_output_target
from energy_manager.estimation import estimate_load
from energy_manager.exceptions import (
    EntityUnavailableError,
    InvalidMeasurementError,
    MeasurementUnavailableError,
)
from energy_manager.models import (
    PowerChannelType,
    PowerContributionStatus,
    PowerContributionType,
)

UPDATE_INTERVAL_SECONDS = 10
LOG_PATH = Path("data/marstek_status_closed_loop_V3.csv")
LOG_RETENTION_DAYS = 10
COMMAND_DEADBAND_W = 10.0
MIN_COMMAND_INTERVAL_SECONDS = 60.0
MIN_TEST_COMMAND_POWER_W = 130.0

# Monitoring only: these parameters do not affect battery commands.
OSC_WINDOW_SECONDS = 120.0
OSC_MAX_GAP_SECONDS = 25.0
OSC_MAX_TARGET_RANGE_W = 5.0
OSC_MAX_INPUT_RANGE_W = 75.0
OSC_MIN_OUTPUT_RANGE_W = 30.0
OSC_MIN_DIRECTION_CHANGE_W = 15.0
OSC_MIN_REVERSALS = 3
OSC_CLEAR_DELAY_SECONDS = 60.0


def _collect_ha_inputs(config, ha: HomeAssistantAdapter) -> tuple[dict, dict]:
    measurements = {}
    entity_states = {}
    for name, sensor_config in config.power_sensors.items():
        if sensor_config.channel_type is not PowerChannelType.CONSUMER:
            continue
        try:
            measurements[name] = ha.get_power_measurement(sensor_config.entity_id)
        except (
            MeasurementUnavailableError,
            InvalidMeasurementError,
            EntityUnavailableError,
        ):
            pass
    for name, consumer_config in config.estimated_consumers.items():
        try:
            entity_states[name] = ha.get_entity_state(consumer_config.entity_id)
        except EntityUnavailableError:
            pass
    return measurements, entity_states


def _get_quality_diagnostics(config, load_estimate, measurements) -> tuple[list, dict]:
    quality_issues = [
        {
            "source": contribution.source,
            "status": contribution.status.value,
        }
        for contribution in load_estimate.contributions
        if contribution.status is not PowerContributionStatus.VALID
    ]
    measurement_diagnostics = {}
    if quality_issues:
        for name, measurement in measurements.items():
            sensor_config = config.power_sensors[name]
            age_seconds = (
                measurement.received_at - measurement.reported_at
            ).total_seconds()
            measurement_diagnostics[name] = {
                "entity_id": sensor_config.entity_id,
                "power_w": measurement.power_w,
                "age_seconds": round(age_seconds, 1),
                "max_age_seconds": sensor_config.max_age_seconds,
                "reported_at": measurement.reported_at.isoformat(),
            }
    return quality_issues, measurement_diagnostics


def _publish_load_estimate(
    config,
    ha: HomeAssistantAdapter,
    load_estimate,
    quality_issues: list,
    measurement_diagnostics: dict,
) -> None:
    contributions = load_estimate.contributions
    attributes = {
        "friendly_name": "Estimated Total Load",
        "unit_of_measurement": "W",
        "device_class": "power",
        "state_class": "measurement",
        "measured_power_w": sum(
            item.power_w
            for item in contributions
            if item.contribution_type is PowerContributionType.MEASURED
        ),
        "state_estimated_power_w": sum(
            item.power_w
            for item in contributions
            if item.contribution_type is PowerContributionType.STATE_ESTIMATED
        ),
        "constant_estimated_power_w": sum(
            item.power_w
            for item in contributions
            if item.contribution_type is PowerContributionType.CONSTANT_ESTIMATED
        ),
        "valid_count": sum(
            item.status is PowerContributionStatus.VALID for item in contributions
        ),
        "stale_count": sum(
            item.status is PowerContributionStatus.STALE for item in contributions
        ),
        "implausible_count": sum(
            item.status is PowerContributionStatus.IMPLAUSIBLE
            for item in contributions
        ),
        "unavailable_count": sum(
            item.status is PowerContributionStatus.UNAVAILABLE
            for item in contributions
        ),
        "quality_issues": quality_issues,
        "measurement_diagnostics": measurement_diagnostics,
        "last_cycle_at": datetime.now(UTC).isoformat(),
    }
    ha.publish_state(
        config.outputs["estimated_total_load"].entity_id,
        f"{load_estimate.total_power_w:.1f}",
        attributes=attributes,
    )

def _get_marstek_status(marstek: MarstekMqttAdapter) -> MarstekStatus | None:
    try:
        return marstek.get_status()
    except TimeoutError:
        return None

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


def _assess_oscillation(history: deque) \
-> tuple[bool | None, str, float | None, int | None, float | None]:
    """Return detection, reason, output range and reversal count.
    None means data are insufficient or not comparable; it does NOT mean stable.
    """
    if len(history) < 10 or history[-1][0] - history[0][0] < 110: # min 10 samples over 110 seconds
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


def _publish_oscillation_monitor(
    ha: HomeAssistantAdapter,
    status: MarstekStatus | None,
    command_sent: bool,
    control_inhibited: bool,
    history: deque,
    monitor_state: dict,
) -> None:
    """Publish diagnostic state only; never changes MARSTEK commands."""
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
    ha.publish_state(
        "binary_sensor.residential_battery_output_oscillation",
        result,
        attributes={
            "friendly_name": "Battery Output Oscillation",
            "device_class": "problem",
            "reason": reason,
            "window_seconds": OSC_WINDOW_SECONDS,
            "output_range_w": round(output_range, 1) if output_range is not None else None,
            "direction_reversals": reversals,
            "input_range_w": round(input_range, 1) if input_range is not None else None,
        },
    )
    ha.publish_state(
        "sensor.residential_battery_output_range",
        f"{output_range:.1f}" if output_range is not None else "unavailable",
        attributes={
            "friendly_name": "Battery Output Range (2 min)",
            "unit_of_measurement": "W",
            "device_class": "power",
            "state_class": "measurement",
        },
    )
    ha.publish_state(
        "sensor.residential_battery_device_target",
        status.target_power_w if status is not None else "unavailable",
        attributes={
            "friendly_name": "Battery Device Target",
            "unit_of_measurement": "W",
            "device_class": "power",
            "state_class": "measurement",
        },
    )


def _apply_marstek_control(
    config,
    marstek: MarstekMqttAdapter,
    status: MarstekStatus | None,
    target_power_w: float,
    last_command_w: float | None,
    last_command_time: float | None,
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
        return (
            last_command_w, last_command_time, command_sent,
            command_reason, seconds_since_last_command, control_inhibited,
        )
    now = time.monotonic()
    if last_command_w is None:
        last_command_w = status.target_power_w
        last_command_time = now
        command_reason = "initializing"
    elif control_inhibited:
        if battery_state.soc_percent <= config.battery.min_soc_percent:
            command_reason = "low_soc"
        else:
            command_reason = "high_soc"
    else:
        assert last_command_time is not None
        seconds_since_last_command = now - last_command_time
        command_target_w = max(
            MIN_TEST_COMMAND_POWER_W, float(round(target_power_w))
        )
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
    return (
        last_command_w, last_command_time, command_sent,
        command_reason, seconds_since_last_command, control_inhibited,
    )


def _publish_battery_states(config, ha: HomeAssistantAdapter, battery_state) -> None:
    if battery_state is None:
        return

    ha.publish_state(
        config.outputs["battery_soc"].entity_id,
        battery_state.soc_percent,
        attributes={
            "friendly_name": "Residential Battery SOC",
            "unit_of_measurement": "%",
            "device_class": "battery",
            "state_class": "measurement",
        },
    )
    ha.publish_state(
        config.outputs["battery_input_power"].entity_id,
        battery_state.input_power_w,
        attributes={
            "friendly_name": "Residential Battery Input Power",
            "unit_of_measurement": "W",
            "device_class": "power",
            "state_class": "measurement",
        },
    )
    ha.publish_state(
        config.outputs["battery_output_power"].entity_id,
        battery_state.output_power_w,
        attributes={
            "friendly_name": "Residential Battery Output Power",
            "unit_of_measurement": "W",
            "device_class": "power",
            "state_class": "measurement",
        },
    )


def _publish_controller_target(
    config,
    ha: HomeAssistantAdapter,
    target_power_w: float,
    last_command_w: float | None,
    command_sent: bool,
    command_reason: str,
    control_inhibited: bool,
) -> None:
    ha.publish_state(
        config.outputs["battery_output_target"].entity_id,
        target_power_w,
        attributes={
            "friendly_name": "Residential Battery Output Target",
            "unit_of_measurement": "W",
            "device_class": "power",
            "control_mode": "active",
            "last_command_w": last_command_w,
            "command_sent": command_sent,
            "command_reason": command_reason,
            "control_inhibited": control_inhibited,
        },
    )


def _get_daily_log_path() -> tuple[Path, bool]:
    now = datetime.now(UTC)
    log_path = LOG_PATH.with_name(
        f"{LOG_PATH.stem}_{now:%Y-%m-%d}{LOG_PATH.suffix}"
    )
    log_path.parent.mkdir(parents=True, exist_ok=True)
    file_exists = log_path.exists()
    if not file_exists:
        cutoff = now.date() - timedelta(days=LOG_RETENTION_DAYS)
        for old_path in LOG_PATH.parent.glob(f"{LOG_PATH.stem}_????-??-??.csv"):
            try:
                log_date = date.fromisoformat(
                    old_path.stem.removeprefix(f"{LOG_PATH.stem}_")
                )
            except ValueError:
                continue
            if log_date < cutoff:
                old_path.unlink()
    return log_path, file_exists


def _append_marstek_log(
    status: MarstekStatus,
    controller_target_w: float,
    control_inhibited: bool,
    last_command_w: float | None,
    command_sent: bool,
    command_reason: str,
    seconds_since_last_command: float | None,
    quality_issues: list[dict],
    measurement_diagnostics: dict,
) -> None:
    log_path, file_exists = _get_daily_log_path()
    battery = status.battery_state
    raw = status.raw_values
    row = {
        "timestamp": status.received_at.isoformat(),
        "soc_percent": battery.soc_percent,
        "input_power_w": battery.input_power_w,
        "output_power_w": battery.output_power_w,
        "device_target_w": status.target_power_w,
        "controller_target_w": controller_target_w,
        "control_inhibited": control_inhibited,
        "g1_w": status.output_1_power_w,
        "g2_w": status.output_2_power_w,
        "cs": status.charging_setting,
        "cd": status.discharge_setting,
        "do": status.discharge_depth_percent,
        "cj": status.scene,
        "md": status.discharge_setting_mode,
        "temperature_low": status.temperature_low,
        "temperature_high": status.temperature_high,
        "p1": raw.get("p1"),
        "p2": raw.get("p2"),
        "o1": raw.get("o1"),
        "o2": raw.get("o2"),
        "st": raw.get("st"),
        "bs": raw.get("bs"),
        "tc": raw.get("tc"),
        "tf": raw.get("tf"),
        "raw_payload": status.raw_payload,
        "last_command_w": last_command_w,
        "command_sent": command_sent,
        "command_reason": command_reason,
        "seconds_since_last_command": seconds_since_last_command,
        "quality_issues": json.dumps(quality_issues),
        "measurement_diagnostics": json.dumps(measurement_diagnostics),
    }
    with log_path.open("a", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=row.keys())
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)


def _print_cycle_status(load_estimate, battery_state, target_power_w: float) -> None:
    print(f"Load: {load_estimate.total_power_w:.1f} W")
    if battery_state is None:
        print("Battery state: unavailable")
    else:
        print(f"Battery SOC: {battery_state.soc_percent:.1f} %")
        print(f"Battery input: {battery_state.input_power_w:.1f} W")
        print(f"Battery output: {battery_state.output_power_w:.1f} W")
    print(f"Controller target: {target_power_w:.1f} W")


def _run_cycle(
    config,
    ha: HomeAssistantAdapter,
    marstek: MarstekMqttAdapter,
    last_command_w: float | None,
    last_command_time: float | None,
    oscillation_history: deque,
    oscillation_state: dict,
) -> tuple[float | None, float | None]:
    measurements, entity_states = _collect_ha_inputs(config, ha)
    load_estimate = estimate_load(
        measurements=measurements,
        entity_states=entity_states,
        config=config,
    )
    quality_issues, measurement_diagnostics = _get_quality_diagnostics(
        config, load_estimate, measurements
    )
    _publish_load_estimate(
        config, ha, load_estimate, quality_issues, measurement_diagnostics
    )
    marstek_status = _get_marstek_status(marstek)
    battery_state = marstek_status.battery_state if marstek_status is not None else None
    target_power_w = calculate_battery_output_target(
        load_estimate=load_estimate,
        battery_state=battery_state,
        battery_config=config.battery,
    )
    (
        last_command_w,
        last_command_time,
        command_sent,
        command_reason,
        seconds_since_last_command,
        control_inhibited,
    ) = _apply_marstek_control(
        config, marstek, marstek_status, target_power_w,
        last_command_w, last_command_time,
    )
    if marstek_status is not None:
        _append_marstek_log(
            status=marstek_status,
            controller_target_w=target_power_w,
            control_inhibited=control_inhibited,
            last_command_w=last_command_w,
            command_sent=command_sent,
            command_reason=command_reason,
            seconds_since_last_command=seconds_since_last_command,
            quality_issues=quality_issues,
            measurement_diagnostics=measurement_diagnostics,
        )
    _publish_battery_states(config, ha, battery_state)
    _publish_controller_target(
        config, ha, target_power_w,
        last_command_w, command_sent, command_reason, control_inhibited,
    )
    _print_cycle_status(load_estimate, battery_state, target_power_w)
    # Diagnostic HTTP failures must never abort a successful control cycle.
    try:
        _publish_oscillation_monitor(
            ha=ha,
            status=marstek_status,
            command_sent=command_sent,
            control_inhibited=control_inhibited,
            history=oscillation_history,
            monitor_state=oscillation_state,
        )
    except requests.RequestException as exc:
        print(f"Oscillation monitor publish failed: {exc}")
    return last_command_w, last_command_time


def main() -> None:
    config = load_config(Path("config/sensors.yaml"))
    ha = HomeAssistantAdapter(
        base_url=get_home_assistant_url(),
        token=get_home_assistant_token(),
    )
    marstek = MarstekMqttAdapter(
        host=get_mqtt_host(),
        port=get_mqtt_port(),
        username=get_mqtt_username(),
        password=get_mqtt_password(),
        device_type=get_marstek_device_type(),
        device_mac=get_marstek_device_mac(),
    )
    last_command_w: float | None = None
    last_command_time: float | None = None
    oscillation_history = deque()
    oscillation_state = {"last_detected_at": None}
    try:
        while True:
            try:
                last_command_w, last_command_time = _run_cycle(
                    config=config,
                    ha=ha,
                    marstek=marstek,
                    last_command_w=last_command_w,
                    last_command_time=last_command_time,
                    oscillation_history=oscillation_history,
                    oscillation_state=oscillation_state,
                )
            except requests.RequestException as exc:
                print(f"Home Assistant communication failed: {exc}")
            time.sleep(UPDATE_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        print("\nEnergy manager stopped.")


if __name__ == "__main__":
    main()
