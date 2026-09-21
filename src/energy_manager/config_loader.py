from pathlib import Path

import yaml

from energy_manager.models import (
    ConstantConsumerConfig,
    EstimatedConsumerConfig,
    LoadInputConfig,
    PowerChannelType,
    PowerSensorConfig,
)


def load_input_config(path: Path) -> LoadInputConfig:
    with path.open() as file:
        data = yaml.safe_load(file) or {}

    return LoadInputConfig(
        power_sensors=_parse_power_sensors(data),
        estimated_consumers=_parse_estimated_consumers(data),
        constant_consumers=_parse_constant_consumers(data),
    )

def _parse_power_sensors(
    data: dict,
) -> dict[str, PowerSensorConfig]:
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

def _parse_estimated_consumers(
    data: dict,
) -> dict[str, EstimatedConsumerConfig]:
    configs = {}

    for name, consumer_data in data.get("estimated_consumers", {}).items():
        configs[name] = EstimatedConsumerConfig(
            name=name,
            entity_id=consumer_data["entity_id"],
            estimated_power_w=consumer_data["estimated_power_w"],
            max_age_seconds=consumer_data["max_age_seconds"],
        )

    return configs

def _parse_constant_consumers(
    data: dict,
) -> dict[str, ConstantConsumerConfig]:
    configs = {}

    for name, consumer_data in data.get("constant_consumers", {}).items():
        configs[name] = ConstantConsumerConfig(
            name=name,
            estimated_power_w=consumer_data["estimated_power_w"],
        )

    return configs