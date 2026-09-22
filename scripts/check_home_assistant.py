from pathlib import Path

from energy_manager.adapters.home_assistant import HomeAssistantAdapter
from energy_manager.config import (
    get_home_assistant_token,
    get_home_assistant_url,
)
from energy_manager.config_loader import load_config
from energy_manager.quality import evaluate_measurement_quality

adapter = HomeAssistantAdapter(
    base_url=get_home_assistant_url(),
    token=get_home_assistant_token(),
)

config = load_config(
    Path("config/sensors.yaml")
)

smart_plug_config = config.power_sensors["smart_plug_valentin"]

measurement = adapter.get_power_measurement(
    smart_plug_config.entity_id
)

quality = evaluate_measurement_quality(
    measurement,
    smart_plug_config,
)

print(measurement)
print(quality)