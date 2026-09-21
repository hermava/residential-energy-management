from pathlib import Path

import yaml

from energy_manager.models import (
    EstimatedConsumerConfig,
    PowerChannelType,
    PowerSensorConfig,
)


def load_power_sensors(path: Path) -> dict[str, PowerSensorConfig]:
    with path.open() as file:
        data = yaml.safe_load(file)

    configs = {}

    for name, sensor_data in data.get("power_sensors", {}).items():
        configs[name] = PowerSensorConfig(
            name=name,
            entity_id=sensor_data["entity_id"],
            channel_type=PowerChannelType(sensor_data["channel_type"]),
            min_power_w=sensor_data["min_power_w"],
            max_power_w=sensor_data["max_power_w"],
            max_age_seconds=sensor_data["max_age_seconds"],
        )

    return configs

def load_estimated_consumers(
    path: Path,
) -> dict[str, EstimatedConsumerConfig]:
    with path.open() as file:
        data = yaml.safe_load(file)

    configs = {}

    for name, consumer_data in data.get("estimated_consumers", {}).items():
        configs[name] = EstimatedConsumerConfig(
            name=name,
            entity_id=consumer_data["entity_id"],
            estimated_power_w=consumer_data["estimated_power_w"],
            max_age_seconds=consumer_data["max_age_seconds"],
        )

    return configs