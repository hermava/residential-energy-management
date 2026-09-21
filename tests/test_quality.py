from datetime import UTC, datetime, timedelta

import pytest

from energy_manager.models import PowerChannelType, PowerMeasurement, PowerSensorConfig
from energy_manager.quality import (
    MeasurementQuality,
    evaluate_measurement_quality,
)


@pytest.fixture
def sensor_config() -> PowerSensorConfig:
    return PowerSensorConfig(
        name="test_sensor",
        entity_id="sensor.test_power",
        channel_type=PowerChannelType.CONSUMER,
        min_power_w=0,
        max_power_w=800,
        max_age_seconds=30,
    )

def test_quality_is_valid(sensor_config):
    received_at = datetime.now(UTC)
    measurement = PowerMeasurement(
        source="sensor.test_power",
        power_w=500.0,
        reported_at=received_at - timedelta(seconds=10),
        received_at=received_at,
    )
    quality = evaluate_measurement_quality(
        measurement,
        sensor_config,
    )

    assert quality is MeasurementQuality.VALID


def test_quality_is_stale(sensor_config):
    received_at = datetime.now(UTC)

    measurement = PowerMeasurement(
        source="sensor.test_power",
        power_w=500.0,
        reported_at=received_at - timedelta(seconds=60),
        received_at=received_at,
    )

    quality = evaluate_measurement_quality(
        measurement,
        sensor_config
    )

    assert quality is MeasurementQuality.STALE


def test_quality_is_implausible(sensor_config):
    received_at = datetime.now(UTC)
    measurement = PowerMeasurement(
        source="sensor.test_power",
        power_w=1200.0,
        reported_at=received_at - timedelta(seconds=10),
        received_at=received_at,
    )
    quality = evaluate_measurement_quality(
        measurement,
        sensor_config
    )

    assert quality is MeasurementQuality.IMPLAUSIBLE