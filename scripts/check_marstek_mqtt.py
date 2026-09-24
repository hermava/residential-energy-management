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
    adapter = MarstekMqttAdapter(
        host=get_mqtt_host(),
        port=get_mqtt_port(),
        username=get_mqtt_username(),
        password=get_mqtt_password(),
        device_type=get_marstek_device_type(),
        device_mac=get_marstek_device_mac(),
    )

    state = adapter.get_battery_state()

    print(state)


if __name__ == "__main__":
    main()