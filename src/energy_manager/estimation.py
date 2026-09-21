
from energy_manager.exceptions import InvalidMeasurementError
from energy_manager.models import (
    ConstantConsumerConfig,
    EntityState,
    EstimatedConsumerConfig,
    LoadEstimate,
    LoadInputConfig,
    PowerChannelType,
    PowerContribution,
    PowerContributionStatus,
    PowerContributionType,
    PowerMeasurement,
    PowerSensorConfig,
)
from energy_manager.quality import MeasurementQuality, evaluate_measurement_quality
from energy_manager.validation import is_entity_state_fresh


def estimate_consumer_power(
    state: EntityState,
    config: EstimatedConsumerConfig,
) -> float:
    normalized_state = state.state.strip().lower()
    if normalized_state == "on" or normalized_state == "running":
        return config.estimated_power_w
    if normalized_state == "off":
        return 0.0
    raise InvalidMeasurementError(
        f"Unsupported state for {state.source}: {state.state}"
)

def build_load_estimate(
    contributions: list[PowerContribution],
) -> LoadEstimate:
    total_power_w = sum(
        contribution.power_w
        for contribution in contributions
    )

    return LoadEstimate(
        total_power_w=total_power_w,
        contributions=tuple(contributions),
    )


def measurement_to_contribution(
    measurement: PowerMeasurement,
    config: PowerSensorConfig,
    quality: MeasurementQuality,
) -> PowerContribution:
    if quality is MeasurementQuality.VALID:
        power_w = measurement.power_w
        status = PowerContributionStatus.VALID

    elif quality is MeasurementQuality.STALE:
        power_w = 0.0
        status = PowerContributionStatus.STALE

    elif quality is MeasurementQuality.IMPLAUSIBLE:
        power_w = 0.0
        status = PowerContributionStatus.IMPLAUSIBLE

    else:
        raise ValueError(f"Unsupported measurement quality: {quality}")

    return PowerContribution(
        name=config.name,
        source=measurement.source,
        power_w=power_w,
        contribution_type=PowerContributionType.MEASURED,
        status=status,
    )

def estimated_consumer_to_contribution(
    state: EntityState,
    config: EstimatedConsumerConfig,
    is_fresh: bool,
) -> PowerContribution:
    normalized_state = state.state.strip().lower()
    if normalized_state in {"unknown", "unavailable"}:
        return PowerContribution(
            name=config.name,
            source=state.source,
            power_w=0.0,
            contribution_type=PowerContributionType.STATE_ESTIMATED,
            status=PowerContributionStatus.UNAVAILABLE,
        )
    if not is_fresh:
        return PowerContribution(
            name=config.name,
            source=state.source,
            power_w=0.0,
            contribution_type=PowerContributionType.STATE_ESTIMATED,
            status=PowerContributionStatus.STALE,
        )

    power_w = estimate_consumer_power(state, config)

    return PowerContribution(
        name=config.name,
        source=state.source,
        power_w=power_w,
        contribution_type=PowerContributionType.STATE_ESTIMATED,
        status=PowerContributionStatus.VALID,
    )

def constant_consumer_to_contribution(
    config: ConstantConsumerConfig,
) -> PowerContribution:
    return PowerContribution(
        name=config.name,
        power_w=config.estimated_power_w,
        contribution_type=PowerContributionType.CONSTANT_ESTIMATED,
    )


def estimate_load(
    measurements: dict[str, PowerMeasurement],
    entity_states: dict[str, EntityState],
    config: LoadInputConfig,
) -> LoadEstimate:
    contributions = []

    for name, sensor_config in config.power_sensors.items():
        if sensor_config.channel_type is not PowerChannelType.CONSUMER:
            continue

        measurement = measurements.get(name)
        if measurement is None:
            contributions.append(
                PowerContribution(
                    name=sensor_config.name,
                    source=sensor_config.entity_id,
                    power_w=0.0,
                    contribution_type=PowerContributionType.MEASURED,
                    status=PowerContributionStatus.UNAVAILABLE,
                )
            )
            continue

        quality = evaluate_measurement_quality(
            measurement,
            sensor_config,
        )

        contributions.append(
            measurement_to_contribution(
                measurement,
                sensor_config,
                quality,
            )
        )

    for name, consumer_config in config.estimated_consumers.items():
        state = entity_states.get(name)

        if state is None:
            contributions.append(
                PowerContribution(
                    name=consumer_config.name,
                    source=consumer_config.entity_id,
                    power_w=0.0,
                    contribution_type=PowerContributionType.STATE_ESTIMATED,
                    status=PowerContributionStatus.UNAVAILABLE,
                )
            )
            continue

        is_fresh = is_entity_state_fresh(
            state,
            consumer_config.max_age_seconds,
        )

        contributions.append(
            estimated_consumer_to_contribution(
                state,
                consumer_config,
                is_fresh,
            )
        )

    for consumer_config in config.constant_consumers.values():
        contributions.append(
            constant_consumer_to_contribution(consumer_config)
        )

    return build_load_estimate(contributions)
