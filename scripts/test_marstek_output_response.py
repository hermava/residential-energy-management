import time
from datetime import datetime
from pathlib import Path

from energy_manager.adapters.home_assistant import (
    HomeAssistantAdapter,
)
from energy_manager.adapters.marstek_mqtt import (
    MarstekMqttAdapter,
)
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

STEP_DURATION_SECONDS = 300
POLL_INTERVAL_SECONDS = 5

TEST_STEPS_W = (
    100,
    250,
    120,
    150,
)


def main() -> None:
    config = load_config(
        Path("config/sensors.yaml")
    )

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

    for target_power_w in TEST_STEPS_W:
        print(
            f"\n{datetime.now():%H:%M:%S} - "
            f"Setting output to {target_power_w} W"
        )

        marstek.set_output_power(
            target_power_w
        )

        ha.publish_state(
            config.outputs[
                "battery_output_target"
            ].entity_id,
            target_power_w,
            attributes={
                "friendly_name":
                    "Residential Battery Output Target",
                "unit_of_measurement": "W",
                "device_class": "power",
                "control_mode": "test_sequence",
            },
        )

        step_end = (
            time.monotonic()
            + STEP_DURATION_SECONDS
        )

        while time.monotonic() < step_end:
            try:
                state = marstek.get_battery_state(
                    timeout_seconds=5
                )
            except TimeoutError:
                print("Battery state unavailable")
            else:
                ha.publish_state(
                    config.outputs[
                        "battery_output_power"
                    ].entity_id,
                    state.output_power_w,
                    attributes={
                        "friendly_name":
                            "Residential Battery Output Power",
                        "unit_of_measurement": "W",
                        "device_class": "power",
                        "state_class": "measurement",
                    },
                )

                print(
                    f"{datetime.now():%H:%M:%S} "
                    f"target={target_power_w:.0f} W, "
                    f"battery={state.output_power_w:.0f} W"
                )

            time.sleep(
                POLL_INTERVAL_SECONDS
            )


if __name__ == "__main__":
    main()