from energy_manager.exceptions import InvalidMeasurementError
from energy_manager.models import EntityState, EstimatedConsumerConfig


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