from pathlib import Path

from energy_manager.adapters.home_assistant import HomeAssistantAdapter
from energy_manager.config import (
    get_home_assistant_token,
    get_home_assistant_url,
)
from energy_manager.config_loader import load_config
from energy_manager.estimation import estimate_load
from energy_manager.exceptions import (
    EntityUnavailableError,
    InvalidMeasurementError,
    MeasurementUnavailableError,
)
from energy_manager.models import PowerChannelType, PowerContributionStatus, PowerContributionType


def main() -> None:
    config = load_config(Path("config/sensors.yaml"))

    adapter = HomeAssistantAdapter(
        base_url=get_home_assistant_url(),
        token=get_home_assistant_token(),
    )

    measurements = {}
    entity_states = {}

    for name, sensor_config in config.power_sensors.items():
        if sensor_config.channel_type is not PowerChannelType.CONSUMER:
            continue

        try:
            measurements[name] = adapter.get_power_measurement(
                sensor_config.entity_id
            )
        except (MeasurementUnavailableError, InvalidMeasurementError, EntityUnavailableError):
            pass

    for name, consumer_config in config.estimated_consumers.items():
        try:
            entity_states[name] = adapter.get_entity_state(
                consumer_config.entity_id
            )
        except EntityUnavailableError:
            pass
    estimate = estimate_load(
        measurements=measurements,
        entity_states=entity_states,
        config=config,
    )
    measured_power_w = sum(
        contribution.power_w
        for contribution in estimate.contributions
        if contribution.contribution_type is PowerContributionType.MEASURED
    )

    state_estimated_power_w = sum(
        contribution.power_w
        for contribution in estimate.contributions
        if contribution.contribution_type
        is PowerContributionType.STATE_ESTIMATED
    )

    constant_estimated_power_w = sum(
        contribution.power_w
        for contribution in estimate.contributions
        if contribution.contribution_type
        is PowerContributionType.CONSTANT_ESTIMATED
    )
    valid_count = sum(
        contribution.status is PowerContributionStatus.VALID
        for contribution in estimate.contributions
    )

    stale_count = sum(
        contribution.status is PowerContributionStatus.STALE
        for contribution in estimate.contributions
    )

    implausible_count = sum(
        contribution.status is PowerContributionStatus.IMPLAUSIBLE
        for contribution in estimate.contributions
    )

    unavailable_count = sum(
        contribution.status is PowerContributionStatus.UNAVAILABLE
        for contribution in estimate.contributions
    )
    output_config = config.outputs["estimated_total_load"]
    adapter.publish_state(
        output_config.entity_id,
        f"{estimate.total_power_w:.1f}",
        {
            "friendly_name": "Estimated Total Load",
            "unit_of_measurement": "W",
            "device_class": "power",
            "state_class": "measurement",
            "measured_power_w": measured_power_w,
            "state_estimated_power_w": state_estimated_power_w,
            "constant_estimated_power_w": constant_estimated_power_w,
            "valid_count": valid_count,
            "stale_count": stale_count,
            "implausible_count": implausible_count,
            "unavailable_count": unavailable_count,
        },
    )
    print(f"Estimated total load: {estimate.total_power_w:.1f} W")
    for contribution in estimate.contributions:
        print(
            f"{contribution.name}: "
            f"{contribution.power_w:.1f} W "
            f"[{contribution.contribution_type.value}, "
            f"{contribution.status.value}]"
        )


if __name__ == "__main__":
    main()