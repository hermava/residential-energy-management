from pathlib import Path

import yaml

from energy_manager.models import (
    BatteryConfig,
    ConstantConsumerConfig,
    EstimatedConsumerConfig,
    LoadConfig,
    OutputConfig,
    PowerChannelType,
    PowerSensorConfig,
)


def load_config(path: Path) -> LoadConfig:
    with path.open() as file:
        data = yaml.safe_load(file) or {}

    return LoadConfig(
        power_sensors=_parse_power_sensors(data),
        estimated_consumers=_parse_estimated_consumers(data),
        constant_consumers=_parse_constant_consumers(data),
        outputs=_parse_outputs(data),
        battery=_parse_battery(data),
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

def _parse_outputs(
    data: dict,
) -> dict[str, OutputConfig]:
    configs = {}

    for name, output_data in data.get("outputs", {}).items():
        configs[name] = OutputConfig(
            entity_id=output_data["entity_id"],
        )

    return configs

def _parse_battery(data: dict) -> BatteryConfig:
    battery_data = data["battery"]

    return BatteryConfig(
        capacity_wh=battery_data["capacity_wh"],
        min_soc_percent=battery_data["min_soc_percent"],
        max_output_power_w=battery_data["max_output_power_w"],
        max_age_seconds=battery_data["max_age_seconds"],
    )