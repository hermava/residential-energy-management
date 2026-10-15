from collections import deque

import requests

from energy_manager.adapters.home_assistant import HomeAssistantAdapter
from energy_manager.adapters.marstek_mqtt import MarstekMqttAdapter
from energy_manager.battery_control import _apply_marstek_control, _get_marstek_status
from energy_manager.controller import calculate_battery_output_target
from energy_manager.csv_logging import _append_marstek_log
from energy_manager.estimation import estimate_load
from energy_manager.forecast.forecast_data import update_forecast_logging
from energy_manager.ha_reporting import (
    _collect_ha_inputs,
    _get_quality_diagnostics,
    _publish_battery_states,
    _publish_controller_target,
    _publish_load_estimate,
    _publish_oscillation_monitor,
    _publish_recovery_status,
)
from energy_manager.oscillation import _update_oscillation_monitor
from energy_manager.recovery import _recovery_info
from energy_manager.settings import OSC_MAX_INPUT_RANGE_W


def _print_cycle_status(load_estimate, battery_state, target_power_w: float) -> None:
    print(f"Load: {load_estimate.total_power_w:.1f} W")
    if battery_state is None:
        print("Battery state: unavailable")
    else:
        print(f"Battery SOC: {battery_state.soc_percent:.1f} %")
        print(f"Battery input: {battery_state.input_power_w:.1f} W")
        print(f"Battery output: {battery_state.output_power_w:.1f} W")
    print(f"Controller target: {target_power_w:.1f} W")

def _run_cycle(
    config,
    ha: HomeAssistantAdapter,
    marstek: MarstekMqttAdapter,
    last_command_w: float | None,
    last_command_time: float | None,
    oscillation_history: deque,
    oscillation_state: dict,
    recovery_state: dict,
    forecast_state: dict,
) -> tuple[float | None, float | None]:
    measurements, entity_states = _collect_ha_inputs(config, ha)
    load_estimate = estimate_load(
        measurements=measurements,
        entity_states=entity_states,
        config=config,
    )
    quality_issues, measurement_diagnostics = _get_quality_diagnostics(
        config, load_estimate, measurements
    )
    _publish_load_estimate(
        config, ha, load_estimate, quality_issues, measurement_diagnostics
    )

    marstek_status = _get_marstek_status(marstek)
    battery_state = marstek_status.battery_state if marstek_status is not None else None
    target_power_w = calculate_battery_output_target(
        load_estimate=load_estimate,
        battery_state=battery_state,
        battery_config=config.battery,
    )
    (
        last_command_w,
        last_command_time,
        command_sent,
        command_reason,
        seconds_since_last_command,
        control_inhibited,
    ) = _apply_marstek_control(
        config, marstek, marstek_status, target_power_w,
        last_command_w, last_command_time,
        recovery_state, oscillation_state.get("recovery_trigger_ready", False),
    )
    oscillation_assessment = _update_oscillation_monitor(
        status=marstek_status,
        command_sent=command_sent,
        control_inhibited=control_inhibited,
        recovery_active=recovery_state["active_until"] is not None,
        history=oscillation_history,
        monitor_state=oscillation_state,
    )
    # Request recovery only on a fresh oscillation assessment, not the
    # 60-second hysteresis in the public binary sensor.
    # Keep the HA detection visible even with variable solar input, but do
    # not automatically intervene while solar input itself is unstable.
    oscillation_state["recovery_trigger_ready"] = (
        oscillation_assessment["oscillation_detected"] is True
        and oscillation_assessment["input_range_w"] is not None
        and oscillation_assessment["input_range_w"] <= OSC_MAX_INPUT_RANGE_W
    )
    if recovery_state["active_until"] is not None:
        oscillation_state["recovery_trigger_ready"] = False
    recovery_info = _recovery_info(recovery_state)

    if marstek_status is not None:
        _append_marstek_log(
            status=marstek_status,
            controller_target_w=target_power_w,
            control_inhibited=control_inhibited,
            last_command_w=last_command_w,
            command_sent=command_sent,
            command_reason=command_reason,
            seconds_since_last_command=seconds_since_last_command,
            quality_issues=quality_issues,
            measurement_diagnostics=measurement_diagnostics,
            oscillation_assessment=oscillation_assessment,
            recovery_info=recovery_info,
        )

    _publish_battery_states(config, ha, battery_state)
    _publish_controller_target(
        config, ha, target_power_w,
        last_command_w, command_sent, command_reason, control_inhibited,
    )
    _print_cycle_status(load_estimate, battery_state, target_power_w)
    try:
        _publish_oscillation_monitor(
            ha=ha,
            status=marstek_status,
            assessment=oscillation_assessment,
        )
    except requests.RequestException as exc:
        print(f"Oscillation monitor publish failed: {exc}")
    try:
        _publish_recovery_status(ha, recovery_info)
    except requests.RequestException as exc:
        print(f"Recovery monitor publish failed: {exc}")
    try:
        update_forecast_logging(ha, config, battery_state, forecast_state)
    except (OSError, ValueError, requests.RequestException) as exc:
        print(f"Forecast logging failed: {exc}")

    return last_command_w, last_command_time
