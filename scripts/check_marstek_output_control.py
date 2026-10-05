import time

from energy_manager.adapters.marstek_mqtt import MarstekMqttAdapter
from energy_manager.config import (
    get_marstek_device_mac,
    get_marstek_device_type,
    get_mqtt_host,
    get_mqtt_password,
    get_mqtt_port,
    get_mqtt_username,
)


def main() -> None:
    marstek = MarstekMqttAdapter(
        host=get_mqtt_host(),
        port=get_mqtt_port(),
        username=get_mqtt_username(),
        password=get_mqtt_password(),
        device_type=get_marstek_device_type(),
        device_mac=get_marstek_device_mac(),
    )

    print("Setting MARSTEK output target to 100 W")
    marstek.set_output_power(100)

    time.sleep(10)

    state = marstek.get_battery_state()

    print(f"SOC: {state.soc_percent:.1f} %")
    print(f"Input power: {state.input_power_w:.1f} W")
    print(f"Output power: {state.output_power_w:.1f} W")


if __name__ == "__main__":
    main()