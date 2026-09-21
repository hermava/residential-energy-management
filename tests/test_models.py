from datetime import UTC, datetime

import pytest

from energy_manager.models import (
    EntityState,
    EstimatedConsumerConfig,
    PowerChannelType,
    PowerMeasurement,
    PowerSensorConfig,
)


def test_power_measurement_creation():
    timestamp = datetime.now(UTC)

    measurement = PowerMeasurement(
        source="sensor.test_power",
        power_w=42.5,
        reported_at=timestamp,
        received_at=timestamp,
    )

    assert measurement.source == "sensor.test_power"
    assert measurement.power_w == 42.5
    assert measurement.reported_at == timestamp
    assert measurement.received_at == timestamp

def test_source_must_not_be_empty():
    timestamp = datetime.now(UTC)

    with pytest.raises(ValueError):
        PowerMeasurement(
            source="",
            power_w=42.5,
            reported_at=timestamp,
            received_at=timestamp,
        )

def test_power_must_be_finite():
    timestamp = datetime.now(UTC)

    with pytest.raises(ValueError):
        PowerMeasurement(
            source="sensor.test_power",
            power_w=float("nan"),
            reported_at=timestamp,
            received_at=timestamp,
        )

def test_power_sensor_config_creation():
    config = PowerSensorConfig(
        name="test_sensor",
        entity_id="sensor.test_power",
        channel_type=PowerChannelType.CONSUMER,
        min_power_w=0,
        max_power_w=800,
        max_age_seconds=30,
    )

    assert config.entity_id == "sensor.test_power"
    assert config.min_power_w == 0
    assert config.max_power_w == 800
    assert config.max_age_seconds == 30

def test_power_sensor_config_rejects_empty_entity_id():
    with pytest.raises(ValueError):
        PowerSensorConfig(
            name="test_sensor",
            entity_id="",
            channel_type=PowerChannelType.CONSUMER,
            min_power_w=0,
            max_power_w=800,
            max_age_seconds=30,
        )

def test_power_sensor_config_rejects_invalid_range():
    with pytest.raises(ValueError):
        PowerSensorConfig(
            name="test_sensor",
            entity_id="sensor.test_power",
            channel_type=PowerChannelType.CONSUMER,
            min_power_w=900,
            max_power_w=800,
            max_age_seconds=30,
        )

def test_power_sensor_config_rejects_non_positive_max_age():
    with pytest.raises(ValueError):
        PowerSensorConfig(
            name="test_sensor",
            entity_id="sensor.test_power",
            channel_type=PowerChannelType.CONSUMER,
            min_power_w=0,
            max_power_w=800,
            max_age_seconds=0,
        )

def test_estimated_consumer_config_creation():
    config = EstimatedConsumerConfig(
        name="tv",
        entity_id="switch.tv",
        estimated_power_w=85.0,
        max_age_seconds=60,
    )

    assert config.name == "tv"
    assert config.entity_id == "switch.tv"
    assert config.estimated_power_w == 85.0
    assert config.max_age_seconds == 60

def test_estimated_consumer_config_rejects_empty_name():
    with pytest.raises(ValueError):
        EstimatedConsumerConfig(
            name="",
            entity_id="switch.tv",
            estimated_power_w=85.0,
            max_age_seconds=60,
        )


def test_estimated_consumer_config_rejects_empty_entity_id():
    with pytest.raises(ValueError):
        EstimatedConsumerConfig(
            name="tv",
            entity_id="",
            estimated_power_w=85.0,
            max_age_seconds=60,
        )


def test_estimated_consumer_config_rejects_negative_power():
    with pytest.raises(ValueError):
        EstimatedConsumerConfig(
            name="tv",
            entity_id="switch.tv",
            estimated_power_w=-1.0,
            max_age_seconds=60,
        )


def test_estimated_consumer_config_rejects_non_positive_max_age():
    with pytest.raises(ValueError):
        EstimatedConsumerConfig(
            name="tv",
            entity_id="switch.tv",
            estimated_power_w=85.0,
            max_age_seconds=0,
        )

def test_entity_state_creation():
    timestamp = datetime.now(UTC)

    state = EntityState(
        source="switch.tv",
        state="on",
        reported_at=timestamp,
        received_at=timestamp,
    )

    assert state.source == "switch.tv"
    assert state.state == "on"


def test_entity_state_rejects_empty_source():
    timestamp = datetime.now(UTC)

    with pytest.raises(ValueError):
        EntityState(
            source="",
            state="on",
            reported_at=timestamp,
            received_at=timestamp,
        )


def test_entity_state_rejects_empty_state():
    timestamp = datetime.now(UTC)

    with pytest.raises(ValueError):
        EntityState(
            source="switch.tv",
            state="",
            reported_at=timestamp,
            received_at=timestamp,
        )