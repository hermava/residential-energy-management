from energy_manager.models import EntityState, PowerMeasurement


def is_measurement_fresh(
    measurement: PowerMeasurement,
    max_age_seconds: float,
) -> bool:
    age = measurement.received_at - measurement.reported_at

    return age.total_seconds() <= max_age_seconds

def is_entity_state_fresh(
    state: EntityState,
    max_age_seconds: float,
) -> bool:
    age = state.received_at - state.reported_at
    return age.total_seconds() <= max_age_seconds

def is_power_plausible(
    measurement: PowerMeasurement,
    min_power_w: float,
    max_power_w: float,
) -> bool:
    return min_power_w <= measurement.power_w <= max_power_w