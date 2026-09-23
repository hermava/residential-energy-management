from datetime import UTC, datetime, timedelta

import pytest

from energy_manager.controller import calculate_battery_output_target
from energy_manager.models import (
    BatteryConfig,
    BatteryState,
    LoadEstimate,
)


@pytest.fixture
def battery_config() -> BatteryConfig:
    return BatteryConfig(
        capacity_wh=2240.0,
        min_soc_percent=10.0,
        max_output_power_w=800.0,
        max_age_seconds=30.0,
    )

def _make_battery_state(
    soc_percent: float = 60.0,
    input_power_w: float = 0.0,
    output_power_w: float = 0.0,
) -> BatteryState:
    timestamp = datetime.now(UTC)

    return BatteryState(
        soc_percent=soc_percent,
        input_power_w=input_power_w,
        output_power_w=output_power_w,
        reported_at=timestamp,
        received_at=timestamp,
    )

def test_controller_follows_load_when_battery_can_supply_it(
    battery_config,
) -> None:
    estimate = LoadEstimate(
        total_power_w=300.0,
        contributions=(),
    )

    battery_state = _make_battery_state(
        soc_percent=60.0,
    )

    target = calculate_battery_output_target(
        estimate,
        battery_state,
        battery_config,
    )

    assert target == 300.0

def test_controller_returns_zero_at_minimum_soc(
    battery_config,
) -> None:
    estimate = LoadEstimate(
        total_power_w=500.0,
        contributions=(),
    )

    battery_state = _make_battery_state(
        soc_percent=battery_config.min_soc_percent,
    )

    target = calculate_battery_output_target(
        estimate,
        battery_state,
        battery_config,
    )

    assert target == 0.0


def test_controller_returns_zero_for_stale_battery_state(
    battery_config,
) -> None:
    received_at = datetime.now(UTC)

    battery_state = BatteryState(
        soc_percent=60.0,
        input_power_w=0.0,
        output_power_w=0.0,
        reported_at=received_at - timedelta(seconds=60),
        received_at=received_at,
    )

    estimate = LoadEstimate(
        total_power_w=500.0,
        contributions=(),
    )

    target = calculate_battery_output_target(
        estimate,
        battery_state,
        battery_config,
    )

    assert target == 0.0

def test_controller_limits_output_to_maximum_power(
    battery_config,
) -> None:
    estimate = LoadEstimate(
        total_power_w=1200.0,
        contributions=(),
    )

    battery_state = _make_battery_state(
        soc_percent=60.0,
    )

    target = calculate_battery_output_target(
        estimate,
        battery_state,
        battery_config,
    )

    assert target == battery_config.max_output_power_w