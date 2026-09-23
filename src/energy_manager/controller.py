from energy_manager.models import (
    BatteryConfig,
    BatteryState,
    LoadEstimate,
)


def calculate_battery_output_target(
    load_estimate: LoadEstimate,
    battery_state: BatteryState | None,
    battery_config: BatteryConfig,
) -> float:
    if battery_state is None:
        return 0.0
    age = battery_state.received_at - battery_state.reported_at
    if age.total_seconds() > battery_config.max_age_seconds:
        return 0.0
    if battery_state.soc_percent <= battery_config.min_soc_percent:
        return 0.0
    return min(
        load_estimate.total_power_w,
        battery_config.max_output_power_w,
    )