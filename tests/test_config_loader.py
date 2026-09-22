from energy_manager.config_loader import load_config
from energy_manager.models import PowerChannelType


def test_load_power_sensors(tmp_path) -> None:
    config_file = tmp_path / "sensors.yaml"

    config_file.write_text(
        """
power_sensors:
  washing_machine:
    entity_id: sensor.washing_machine_power
    channel_type: consumer
    min_power_w: 0
    max_power_w: 2500
    max_age_seconds: 30
"""
    )

    config = load_config(config_file)

    sensor = config.power_sensors["washing_machine"]    

    assert sensor.name == "washing_machine"
    assert sensor.entity_id == "sensor.washing_machine_power"
    assert sensor.channel_type is PowerChannelType.CONSUMER
    assert sensor.min_power_w == 0
    assert sensor.max_power_w == 2500
    assert sensor.max_age_seconds == 30

def test_load_estimated_consumers(tmp_path) -> None:
    config_file = tmp_path / "sensors.yaml"

    config_file.write_text(
        """
estimated_consumers:
  tv:
    entity_id: switch.tv
    estimated_power_w: 85
    max_age_seconds: 60
"""
    )

    config = load_config(config_file)

    consumer = config.estimated_consumers["tv"]

    assert consumer.name == "tv"
    assert consumer.entity_id == "switch.tv"
    assert consumer.estimated_power_w == 85
    assert consumer.max_age_seconds == 60

def test_load_constant_consumers(tmp_path) -> None:
    config_file = tmp_path / "sensors.yaml"

    config_file.write_text(
        """
constant_consumers:
    fridge:
        estimated_power_w: 40
"""
    )

    config = load_config(config_file)

    consumer = config.constant_consumers["fridge"]

    assert consumer.name == "fridge"
    assert consumer.estimated_power_w == 40

def test_load_outputs(tmp_path) -> None:
    config_file = tmp_path / "sensors.yaml"

    config_file.write_text(
        """
outputs:
  estimated_total_load:
    entity_id: sensor.residential_estimated_total_load
"""
    )
    config = load_config(config_file)
    output = config.outputs["estimated_total_load"]

    assert output.entity_id == "sensor.residential_estimated_total_load"