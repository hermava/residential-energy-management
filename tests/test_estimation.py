from datetime import UTC, datetime

import pytest

from energy_manager.estimation import (
    build_load_estimate,
    constant_consumer_to_contribution,
    estimate_consumer_power,
    estimate_load,
    estimated_consumer_to_contribution,
    measurement_to_contribution,
)
from energy_manager.exceptions import InvalidMeasurementError
from energy_manager.models import (
    ConstantConsumerConfig,
    EntityState,
    EstimatedConsumerConfig,
    LoadInputConfig,
    PowerChannelType,
    PowerContribution,
    PowerContributionStatus,
    PowerContributionType,
    PowerMeasurement,
    PowerSensorConfig,
)
from energy_manager.quality import MeasurementQuality


@pytest.fixture
def consumer_config() -> EstimatedConsumerConfig:
    return EstimatedConsumerConfig(
        name="tv",
        entity_id="switch.tv",
        estimated_power_w=85.0,
        max_age_seconds=60,
    )

@pytest.fixture
def power_sensor_config() -> PowerSensorConfig:
    return PowerSensorConfig(
        name="washing_machine",
        entity_id="sensor.washing_machine_power",
        channel_type=PowerChannelType.CONSUMER,
        min_power_w=0.0,
        max_power_w=2500.0,
        max_age_seconds=30.0,
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

def test_build_load_estimate_sums_contributions() -> None:
    contributions = [
        PowerContribution(
            source="sensor.washing_machine",
            name="washing_machine",
            power_w=180.0,
            contribution_type=PowerContributionType.MEASURED,
        ),
        PowerContribution(
            source="sensor.tv",
            name="tv",
            power_w=85.0,
            contribution_type=PowerContributionType.STATE_ESTIMATED,
        ),
    ]

    estimate = build_load_estimate(contributions)

    assert estimate.total_power_w == 265.0
    assert estimate.contributions == tuple(contributions)

def test_measurement_to_contribution() -> None:
    timestamp = datetime.now(UTC)

    measurement = PowerMeasurement(
        source="sensor.washing_machine_power",
        power_w=180.0,
        reported_at=timestamp,
        received_at=timestamp,
    )
    sensor_config = PowerSensorConfig(
        name="washing_machine",
        entity_id="sensor.washing_machine_power",
        max_age_seconds=60,
        channel_type=PowerChannelType.CONSUMER,
        min_power_w=0.0,
        max_power_w=3000.0,
    )

    contribution = measurement_to_contribution(
        measurement, 
        sensor_config,
        quality=MeasurementQuality.VALID,
    )

    assert contribution.source == "sensor.washing_machine_power"
    assert contribution.power_w == 180.0
    assert contribution.contribution_type is PowerContributionType.MEASURED
    assert contribution.status is PowerContributionStatus.VALID


def test_measurement_to_contribution_sets_stale_measurement_to_zero(power_sensor_config) -> None:
    timestamp = datetime.now(UTC)

    measurement = PowerMeasurement(
        source="sensor.washing_machine_power",
        power_w=180.0,
        reported_at=timestamp,
        received_at=timestamp,
    )

    config = power_sensor_config

    contribution = measurement_to_contribution(
        measurement,
        config,
        MeasurementQuality.STALE,
    )

    assert contribution.power_w == 0.0
    assert contribution.status is PowerContributionStatus.STALE

def test_measurement_to_contribution_sets_implausible_measurement_to_zero(
    power_sensor_config,
) -> None:
    timestamp = datetime.now(UTC)

    measurement = PowerMeasurement(
        source="sensor.washing_machine_power",
        power_w=5000.0,
        reported_at=timestamp,
        received_at=timestamp,
    )

    contribution = measurement_to_contribution(
        measurement,
        power_sensor_config,
        MeasurementQuality.IMPLAUSIBLE,
    )

    assert contribution.power_w == 0.0
    assert contribution.status is PowerContributionStatus.IMPLAUSIBLE

def test_estimated_consumer_to_contribution(
    consumer_config: EstimatedConsumerConfig,
) -> None:
    timestamp = datetime.now(UTC)

    state = EntityState(
        source="switch.tv",
        state="on",
        reported_at=timestamp,
        received_at=timestamp,
    )

    contribution = estimated_consumer_to_contribution(
        state,
        consumer_config,
        is_fresh=True
    )

    assert contribution.source == "switch.tv"
    assert contribution.power_w == consumer_config.estimated_power_w
    assert (
        contribution.contribution_type
        is PowerContributionType.STATE_ESTIMATED
    )

def test_estimated_consumer_to_contribution_sets_stale_state_to_zero(
    consumer_config,
) -> None:
    timestamp = datetime.now(UTC)

    state = EntityState(
        source="switch.tv",
        state="on",
        reported_at=timestamp,
        received_at=timestamp,
    )

    contribution = estimated_consumer_to_contribution(
        state,
        consumer_config,
        is_fresh=False,
    )

    assert contribution.power_w == 0.0
    assert contribution.status is PowerContributionStatus.STALE

def test_estimated_consumer_to_contribution_sets_unavailable_state_to_zero(
    consumer_config,
) -> None:
    timestamp = datetime.now(UTC)
    state = EntityState(
        source="switch.tv",
        state="unavailable",
        reported_at=timestamp,
        received_at=timestamp,
    )

    contribution = estimated_consumer_to_contribution(
        state,
        consumer_config,
        is_fresh=True,
    )

    assert contribution.power_w == 0.0
    assert contribution.status is PowerContributionStatus.UNAVAILABLE


def test_constant_consumer_to_contribution() -> None:
    config = ConstantConsumerConfig(
        name="fridge",
        estimated_power_w=40.0,
    )

    contribution = constant_consumer_to_contribution(config)

    assert contribution.name == "fridge"
    assert contribution.power_w == 40.0
    assert (
        contribution.contribution_type
        is PowerContributionType.CONSTANT_ESTIMATED
    )
    assert contribution.source is None


def test_estimate_load_combines_all_consumer_types() -> None:
    timestamp = datetime.now(UTC)

    config = LoadInputConfig(
        power_sensors={
            "washing_machine": PowerSensorConfig(
                name="washing_machine",
                entity_id="sensor.washing_machine_power",
                channel_type=PowerChannelType.CONSUMER,
                min_power_w=0.0,
                max_power_w=2500.0,
                max_age_seconds=30.0,
            ),
        },
        estimated_consumers={
            "tv": EstimatedConsumerConfig(
                name="tv",
                entity_id="switch.tv",
                estimated_power_w=85.0,
                max_age_seconds=60.0,
            ),
        },
        constant_consumers={
            "fridge": ConstantConsumerConfig(
                name="fridge",
                estimated_power_w=40.0,
            ),
        },
    )

    measurements = {
        "washing_machine": PowerMeasurement(
            source="sensor.washing_machine_power",
            power_w=180.0,
            reported_at=timestamp,
            received_at=timestamp,
        ),
    }

    entity_states = {
        "tv": EntityState(
            source="switch.tv",
            state="on",
            reported_at=timestamp,
            received_at=timestamp,
        ),
    }

    estimate = estimate_load(
        measurements,
        entity_states,
        config,
    )

    assert estimate.total_power_w == 305.0
    assert len(estimate.contributions) == 3

def test_estimate_load_sets_missing_measurement_to_unavailable() -> None:
    config = LoadInputConfig(
        power_sensors={
            "washing_machine": PowerSensorConfig(
                name="washing_machine",
                entity_id="sensor.washing_machine_power",
                channel_type=PowerChannelType.CONSUMER,
                min_power_w=0.0,
                max_power_w=2500.0,
                max_age_seconds=30.0,
            ),
        },
        estimated_consumers={},
        constant_consumers={},
    )

    estimate = estimate_load(
        measurements={},
        entity_states={},
        config=config,
    )

    contribution = estimate.contributions[0]

    assert estimate.total_power_w == 0.0
    assert contribution.name == "washing_machine"
    assert contribution.power_w == 0.0
    assert contribution.status is PowerContributionStatus.UNAVAILABLE

def test_estimate_load_sets_missing_entity_state_to_unavailable() -> None:
    config = LoadInputConfig(
        power_sensors={},
        estimated_consumers={
            "tv": EstimatedConsumerConfig(
                name="tv",
                entity_id="switch.tv",
                estimated_power_w=85.0,
                max_age_seconds=60.0,
            ),
        },
        constant_consumers={},
    )

    estimate = estimate_load(
        measurements={},
        entity_states={},
        config=config,
    )

    contribution = estimate.contributions[0]

    assert estimate.total_power_w == 0.0
    assert contribution.name == "tv"
    assert contribution.power_w == 0.0
    assert contribution.status is PowerContributionStatus.UNAVAILABLE


def test_estimate_load_ignores_non_consumer_power_channels() -> None:
    timestamp = datetime.now(UTC)

    config = LoadInputConfig(
        power_sensors={
            "washing_machine": PowerSensorConfig(
                name="washing_machine",
                entity_id="sensor.washing_machine_power",
                channel_type=PowerChannelType.CONSUMER,
                min_power_w=0.0,
                max_power_w=2500.0,
                max_age_seconds=30.0,
            ),
            "battery_output": PowerSensorConfig(
                name="battery_output",
                entity_id="sensor.battery_output_power",
                channel_type=PowerChannelType.BATTERY_OUTPUT,
                min_power_w=0.0,
                max_power_w=1000.0,
                max_age_seconds=30.0,
            ),
        },
        estimated_consumers={},
        constant_consumers={},
    )

    measurements = {
        "washing_machine": PowerMeasurement(
            source="sensor.washing_machine_power",
            power_w=180.0,
            reported_at=timestamp,
            received_at=timestamp,
        ),
        "battery_output": PowerMeasurement(
            source="sensor.battery_output_power",
            power_w=300.0,
            reported_at=timestamp,
            received_at=timestamp,
        ),
    }

    estimate = estimate_load(
        measurements=measurements,
        entity_states={},
        config=config,
    )

    assert estimate.total_power_w == 180.0
    assert len(estimate.contributions) == 1
    assert estimate.contributions[0].name == "washing_machine"