# Structural extraction from run_energy_manager.py (V5).
# Keep behavior/settings unchanged; see REFACTORING_NOTES.md.
from datetime import UTC, datetime

from energy_manager.adapters.home_assistant import HomeAssistantAdapter
from energy_manager.adapters.marstek_mqtt import MarstekStatus
from energy_manager.exceptions import (
    EntityUnavailableError,
    InvalidMeasurementError,
    MeasurementUnavailableError,
)
from energy_manager.models import (
    PowerChannelType,
    PowerContributionStatus,
    PowerContributionType,
)
from energy_manager.settings import OSC_WINDOW_SECONDS, RECOVERY_TARGET_W


def _collect_ha_inputs(config, ha: HomeAssistantAdapter) -> tuple[dict, dict]:
    measurements = {}
    entity_states = {}
    for name, sensor_config in config.power_sensors.items():
        if sensor_config.channel_type is not PowerChannelType.CONSUMER:
            continue
        try:
            measurements[name] = ha.get_power_measurement(sensor_config.entity_id)
        except (
            MeasurementUnavailableError,
            InvalidMeasurementError,
            EntityUnavailableError,
        ):
            pass
    for name, consumer_config in config.estimated_consumers.items():
        try:
            entity_states[name] = ha.get_entity_state(consumer_config.entity_id)
        except EntityUnavailableError:
            pass

    return measurements, entity_states

def _get_quality_diagnostics(config, load_estimate, measurements) -> tuple[list, dict]:
    quality_issues = [
        {
            "source": contribution.source,
            "status": contribution.status.value,
        }
        for contribution in load_estimate.contributions
        if contribution.status is not PowerContributionStatus.VALID
    ]
    measurement_diagnostics = {}
    if quality_issues:
        for name, measurement in measurements.items():
            sensor_config = config.power_sensors[name]
            age_seconds = (
                measurement.received_at - measurement.reported_at
            ).total_seconds()
            measurement_diagnostics[name] = {
                "entity_id": sensor_config.entity_id,
                "power_w": measurement.power_w,
                "age_seconds": round(age_seconds, 1),
                "max_age_seconds": sensor_config.max_age_seconds,
                "reported_at": measurement.reported_at.isoformat(),
            }
    return quality_issues, measurement_diagnostics

def _publish_load_estimate(
    config,
    ha: HomeAssistantAdapter,
    load_estimate,
    quality_issues: list,
    measurement_diagnostics: dict,
) -> None:
    contributions = load_estimate.contributions
    attributes = {
        "friendly_name": "Estimated Total Load",
        "unit_of_measurement": "W",
        "device_class": "power",
        "state_class": "measurement",
        "measured_power_w": sum(
            item.power_w
            for item in contributions
            if item.contribution_type is PowerContributionType.MEASURED
        ),
        "state_estimated_power_w": sum(
            item.power_w
            for item in contributions
            if item.contribution_type is PowerContributionType.STATE_ESTIMATED
        ),
        "constant_estimated_power_w": sum(
            item.power_w
            for item in contributions
            if item.contribution_type is PowerContributionType.CONSTANT_ESTIMATED
        ),
        "valid_count": sum(
            item.status is PowerContributionStatus.VALID for item in contributions
        ),
        "stale_count": sum(
            item.status is PowerContributionStatus.STALE for item in contributions
        ),
        "implausible_count": sum(
            item.status is PowerContributionStatus.IMPLAUSIBLE
            for item in contributions
        ),
        "unavailable_count": sum(
            item.status is PowerContributionStatus.UNAVAILABLE
            for item in contributions
        ),
        "quality_issues": quality_issues,
        "measurement_diagnostics": measurement_diagnostics,
        "last_cycle_at": datetime.now(UTC).isoformat(),
    }
    ha.publish_state(
        config.outputs["estimated_total_load"].entity_id,
        f"{load_estimate.total_power_w:.1f}",
        attributes=attributes,
    )

def _publish_oscillation_monitor(
    ha: HomeAssistantAdapter,
    status: MarstekStatus | None,
    assessment: dict,
) -> None:
    """Publish an already calculated assessment to Home Assistant."""
    result = assessment["oscillation_state"]
    reason = assessment["oscillation_reason"]
    output_range = assessment["output_range_w"]
    reversals = assessment["direction_reversals"]
    input_range = assessment["input_range_w"]
    ha.publish_state(
        "binary_sensor.residential_battery_output_oscillation",
        result,
        attributes={
            "friendly_name": "Battery Output Oscillation",
            "device_class": "problem",
            "reason": reason,
            "window_seconds": OSC_WINDOW_SECONDS,
            "output_range_w": round(output_range, 1) if output_range is not None else None,
            "direction_reversals": reversals,
            "input_range_w": round(input_range, 1) if input_range is not None else None,
        },
    )
    ha.publish_state(
        "sensor.residential_battery_output_range",
        f"{output_range:.1f}" if output_range is not None else "unavailable",
        attributes={
            "friendly_name": "Battery Output Range (2 min)",
            "unit_of_measurement": "W",
            "device_class": "power",
            "state_class": "measurement",
        },
    )
    ha.publish_state(
        "sensor.residential_battery_device_target",
        status.target_power_w if status is not None else "unavailable",
        attributes={
            "friendly_name": "Battery Device Target",
            "unit_of_measurement": "W",
            "device_class": "power",
            "state_class": "measurement",
        },
    )

def _publish_recovery_status(ha: HomeAssistantAdapter, info: dict) -> None:
    ha.publish_state(
        "sensor.residential_battery_recovery",
        info["recovery_state"],
        attributes={
            "friendly_name": "Battery Oscillation Recovery",
            "target_w": RECOVERY_TARGET_W,
            "recovery_until": info["recovery_until"],
            "last_attempt_at": info["recovery_last_attempt"],
            "event": info["recovery_event"],
            "restore_target_w": info["recovery_restore_target_w"],
        },
    )

def _publish_battery_states(config, ha: HomeAssistantAdapter, battery_state) -> None:
    if battery_state is None:
        return

    ha.publish_state(
        config.outputs["battery_soc"].entity_id,
        battery_state.soc_percent,
        attributes={
            "friendly_name": "Residential Battery SOC",
            "unit_of_measurement": "%",
            "device_class": "battery",
            "state_class": "measurement",
        },
    )
    ha.publish_state(
        config.outputs["battery_input_power"].entity_id,
        battery_state.input_power_w,
        attributes={
            "friendly_name": "Residential Battery Input Power",
            "unit_of_measurement": "W",
            "device_class": "power",
            "state_class": "measurement",
        },
    )
    ha.publish_state(
        config.outputs["battery_output_power"].entity_id,
        battery_state.output_power_w,
        attributes={
            "friendly_name": "Residential Battery Output Power",
            "unit_of_measurement": "W",
            "device_class": "power",
            "state_class": "measurement",
        },
    )

def _publish_controller_target(
    config,
    ha: HomeAssistantAdapter,
    target_power_w: float,
    last_command_w: float | None,
    command_sent: bool,
    command_reason: str,
    control_inhibited: bool,
) -> None:
    ha.publish_state(
        config.outputs["battery_output_target"].entity_id,
        target_power_w,
        attributes={
            "friendly_name": "Residential Battery Output Target",
            "unit_of_measurement": "W",
            "device_class": "power",
            "control_mode": "active",
            "last_command_w": last_command_w,
            "command_sent": command_sent,
            "command_reason": command_reason,
            "control_inhibited": control_inhibited,
        },
    )
