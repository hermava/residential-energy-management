import time
from pathlib import Path

from energy_manager.adapters.home_assistant import HomeAssistantAdapter
from energy_manager.adapters.marstek_mqtt import MarstekMqttAdapter
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


def _run_cycle(
    config,
    ha: HomeAssistantAdapter,
    marstek: MarstekMqttAdapter,
) -> None:
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
        },
    )

    try:
        battery_state = marstek.get_battery_state()
    except TimeoutError:
        battery_state = None

    target_power_w = calculate_battery_output_target(
        load_estimate=load_estimate,
        battery_state=battery_state,
        battery_config=config.battery,
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
            "control_mode": "dry_run",
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


import time

import requests


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

    try:
        while True:
            try:
                _run_cycle(
                    config=config,
                    ha=ha,
                    marstek=marstek,
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