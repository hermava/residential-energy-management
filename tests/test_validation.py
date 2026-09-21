from datetime import UTC, datetime, timedelta

from energy_manager.models import EntityState, PowerMeasurement
from energy_manager.validation import (
    is_entity_state_fresh,
    is_measurement_fresh,
    is_power_plausible,
)


def test_measurement_is_fresh():
    received_at = datetime.now(UTC)
    reported_at = received_at - timedelta(seconds=20)

    measurement = PowerMeasurement(
        source="sensor.test_power",
        power_w=42.0,
        reported_at=reported_at,
        received_at=received_at,
    )

    assert is_measurement_fresh(measurement, max_age_seconds=30) is True


def test_measurement_is_stale():
    received_at = datetime.now(UTC)
    reported_at = received_at - timedelta(seconds=60)

    measurement = PowerMeasurement(
        source="sensor.test_power",
        power_w=42.0,
        reported_at=reported_at,
        received_at=received_at,
    )

    assert is_measurement_fresh(measurement, max_age_seconds=30) is False

def test_power_is_plausible():
    received_at = datetime.now(UTC)

    measurement = PowerMeasurement(
        source="sensor.test_power",
        power_w=500.0,
        reported_at=received_at,
        received_at=received_at,
    )

    assert is_power_plausible(
        measurement,
        min_power_w=0,
        max_power_w=800,
    ) is True


def test_power_is_not_plausible():
    received_at = datetime.now(UTC)

    measurement = PowerMeasurement(
        source="sensor.test_power",
        power_w=1200.0,
        reported_at=received_at,
        received_at=received_at,
    )

    assert is_power_plausible(
        measurement,
        min_power_w=0,
        max_power_w=800,
    ) is False

def test_entity_state_is_fresh():
    received_at = datetime.now(UTC)

    state = EntityState(
        source="switch.tv",
        state="on",
        reported_at=received_at - timedelta(seconds=20),
        received_at=received_at,
    )

    assert is_entity_state_fresh(
        state,
        max_age_seconds=30,
    ) is True


def test_entity_state_is_stale():
    received_at = datetime.now(UTC)

    state = EntityState(
        source="switch.tv",
        state="on",
        reported_at=received_at - timedelta(seconds=60),
        received_at=received_at,
    )

    assert is_entity_state_fresh(
        state,
        max_age_seconds=30,
    ) is False