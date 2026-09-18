from energy_manager.adapters.home_assistant import HomeAssistantAdapter
from energy_manager.config import (
    get_home_assistant_token,
    get_home_assistant_url,
)

adapter = HomeAssistantAdapter(
    base_url=get_home_assistant_url(),
    token=get_home_assistant_token(),
)

print(adapter.check_connection())
state = adapter.get_state("sensor.smart_plug_1_hello_world_power")
print(state)
print("***********************************************************")

measurement = adapter.get_power_measurement(
    "sensor.smart_plug_1_hello_world_power"
)

print(measurement)