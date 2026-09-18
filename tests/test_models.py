from datetime import UTC, datetime

import pytest

from energy_manager.models import PowerMeasurement


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