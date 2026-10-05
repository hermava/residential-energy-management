import csv
import json
import time
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
COMMAND_DEADBAND_W = 10.0
MIN_COMMAND_INTERVAL_SECONDS = 60.0
MIN_TEST_COMMAND_POWER_W = 130.0


def _run_cycle(
    config,
    ha: HomeAssistantAdapter,
    marstek: MarstekMqttAdapter,
    last_command_w: float | None,
    last_command_time: float | None,
) -> tuple[float | None, float | None]:
    measurements = {}
    entity_states = {}

    for name, sensor_config in config.power_sensors.items():
        if sensor_config.channel_type is not PowerChannelType.CONSUMER:
            continue
        try:
            measurements[name] = ha.get_power_measurement(
                sensor_config.entity_id
            )
        except (
            MeasurementUnavailableError,
            InvalidMeasurementError,
            EntityUnavailableError,
        ):
            pass

    for name, consumer_config in config.estimated_consumers.items():
        try:
            entity_states[name] = ha.get_entity_state(
                consumer_config.entity_id
            )
        except EntityUnavailableError:
            pass

    load_estimate = estimate_load(
        measurements=measurements,
        entity_states=entity_states,
        config=config,
    )

    measured_power_w = sum(
        contribution.power_w
        for contribution in load_estimate.contributions
        if contribution.contribution_type
        is PowerContributionType.MEASURED
    )

    state_estimated_power_w = sum(
        contribution.power_w
        for contribution in load_estimate.contributions
        if contribution.contribution_type
        is PowerContributionType.STATE_ESTIMATED
    )

    constant_estimated_power_w = sum(
        contribution.power_w
        for contribution in load_estimate.contributions
        if contribution.contribution_type
        is PowerContributionType.CONSTANT_ESTIMATED
    )

    valid_count = sum(
        contribution.status is PowerContributionStatus.VALID
        for contribution in load_estimate.contributions
    )

    stale_count = sum(
        contribution.status is PowerContributionStatus.STALE
        for contribution in load_estimate.contributions
    )

    implausible_count = sum(
        contribution.status is PowerContributionStatus.IMPLAUSIBLE
        for contribution in load_estimate.contributions
    )

    unavailable_count = sum(
        contribution.status is PowerContributionStatus.UNAVAILABLE
        for contribution in load_estimate.contributions
    )

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

    ha.publish_state(
        config.outputs["estimated_total_load"].entity_id,
        f"{load_estimate.total_power_w:.1f}",
        attributes={
            "friendly_name": "Estimated Total Load",
            "unit_of_measurement": "W",
            "device_class": "power",
            "state_class": "measurement",
            "measured_power_w": measured_power_w,
            "state_estimated_power_w": state_estimated_power_w,
            "constant_estimated_power_w": constant_estimated_power_w,
            "valid_count": valid_count,
            "stale_count": stale_count,
            "implausible_count": implausible_count,
            "unavailable_count": unavailable_count,
            "quality_issues": quality_issues,
            "measurement_diagnostics": measurement_diagnostics,
        },
    )
    marstek_status = None
    battery_state = None
    try:
        marstek_status = marstek.get_status()
        battery_state = marstek_status.battery_state
    except TimeoutError:
        battery_state = None
    target_power_w = calculate_battery_output_target(
        load_estimate=load_estimate,
        battery_state=battery_state,
        battery_config=config.battery,
    )
    command_sent = False
    command_reason = "battery_unavailable"
    seconds_since_last_command = None
    control_inhibited = (
        battery_state is None
        or battery_state.soc_percent <= config.battery.min_soc_percent
        or battery_state.soc_percent
        >= config.battery.control_inhibit_soc_percent
    )
    if marstek_status is not None:
        now = time.monotonic()
        if last_command_w is None:
            last_command_w = marstek_status.target_power_w
            last_command_time = now
            command_reason = "initializing"
        elif control_inhibited:
            if battery_state.soc_percent <= config.battery.min_soc_percent:
                command_reason = "low_soc"
            else:
                command_reason = "high_soc"
        else:
            assert last_command_time is not None
            seconds_since_last_command = (
                now - last_command_time
            )
            command_target_w = max(MIN_TEST_COMMAND_POWER_W,float(round(target_power_w)))
            target_difference_w = abs(
                command_target_w - last_command_w
            )
            if target_difference_w < COMMAND_DEADBAND_W:
                command_reason = "deadband"
            elif (
                seconds_since_last_command
                < MIN_COMMAND_INTERVAL_SECONDS
            ):
                command_reason = "minimum_hold_time"
            else:
                marstek.set_output_power(
                    command_target_w
                )
                last_command_w = command_target_w
                last_command_time = now
                command_sent = True
                command_reason = "command_sent"
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
    if battery_state is not None:
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

    print(f"Load: {load_estimate.total_power_w:.1f} W")

    if battery_state is None:
        print("Battery state: unavailable")
    else:
        print(f"Battery SOC: {battery_state.soc_percent:.1f} %")
        print(f"Battery input: {battery_state.input_power_w:.1f} W")
        print(f"Battery output: {battery_state.output_power_w:.1f} W")

    print(f"Controller target: {target_power_w:.1f} W")
    return last_command_w, last_command_time

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
    LOG_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    file_exists = LOG_PATH.exists()
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
    with LOG_PATH.open(
        "a",
        newline="",
        encoding="utf-8",
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=row.keys(),
        )
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)


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

    try:
        while True:
            try:
                last_command_w, last_command_time = _run_cycle(
                    config=config,
                    ha=ha,
                    marstek=marstek,
                    last_command_w=last_command_w,
                    last_command_time=last_command_time,
                )
            except requests.RequestException as exc:
                print(
                    f"Home Assistant communication failed: {exc}"
                )
            time.sleep(UPDATE_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        print("\nEnergy manager stopped.")


if __name__ == "__main__":
    main()