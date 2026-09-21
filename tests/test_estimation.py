from datetime import UTC, datetime

import pytest

from energy_manager.estimation import estimate_consumer_power
from energy_manager.exceptions import InvalidMeasurementError
from energy_manager.models import EntityState, EstimatedConsumerConfig


@pytest.fixture
def consumer_config() -> EstimatedConsumerConfig:
    return EstimatedConsumerConfig(
        name="tv",
        entity_id="switch.tv",
        estimated_power_w=85.0,
        max_age_seconds=60,
    )


def test_estimate_consumer_power_when_on(consumer_config):
    timestamp = datetime.now(UTC)

    state = EntityState(
        source="switch.tv",
        state="on",
        reported_at=timestamp,
        received_at=timestamp,
    )

    assert estimate_consumer_power(state, consumer_config) == 85.0


def test_estimate_consumer_power_when_off(consumer_config):
    timestamp = datetime.now(UTC)

    state = EntityState(
        source="switch.tv",
        state="off",
        reported_at=timestamp,
        received_at=timestamp,
    )

    assert estimate_consumer_power(state, consumer_config) == 0.0


def test_estimate_consumer_power_rejects_invalid_state(consumer_config):
    timestamp = datetime.now(UTC)

    state = EntityState(
        source="switch.tv",
        state="unavailable",
        reported_at=timestamp,
        received_at=timestamp,
    )

    with pytest.raises(InvalidMeasurementError):
        estimate_consumer_power(state, consumer_config)