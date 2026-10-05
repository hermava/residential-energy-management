import time

from dotenv import load_dotenv

from energy_manager.adapters.marstek_mqtt import MarstekMqttAdapter, MarstekStatus
from energy_manager.config import (
    get_marstek_device_mac,
    get_marstek_device_type,
    get_mqtt_host,
    get_mqtt_password,
    get_mqtt_port,
    get_mqtt_username,
)

load_dotenv()

def print_status(label: str, status: MarstekStatus) -> None:
    print(
        f"{label}: "
        f"SOC={status.battery_state.soc_percent:.0f}% | "
        f"target={status.target_power_w:.0f} W | "
        f"output={status.battery_state.output_power_w:.0f} W | "
        f"g1={status.output_1_power_w:.0f} W | "
        f"g2={status.output_2_power_w:.0f} W | "
        f"input={status.battery_state.input_power_w:.0f} W | "
        f"scene={status.scene}"
    )

adapter = MarstekMqttAdapter(
    host=get_mqtt_host(),
    port=get_mqtt_port(),
    username=get_mqtt_username(),
    password=get_mqtt_password(),
    device_type=get_marstek_device_type(),
    device_mac=get_marstek_device_mac(),
)

print_status("Before:")
print_status(adapter.get_status())

adapter.set_output_power(120)

print_status("Immediately after:")
print_status(adapter.get_status())

time.sleep(10)

print_status("After 10 s:")
print_status(adapter.get_status())

time.sleep(10)

print_status("After 20 s:")
print_status(adapter.get_status())


