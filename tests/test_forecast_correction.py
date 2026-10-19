import csv
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock, patch

import pytest
import requests

from energy_manager.forecast.forecast import (
    CORRECTION_METHODS,
    CorrectionResult,
    InputPowerHistory,
    calculate_correction,
)
from energy_manager.forecast.forecast_data_collection import (
    update_forecast_correction,
    update_forecast_logging,
)
from energy_manager.models import (
    BatteryConfig,
    BatteryState,
    EntityState,
    ForecastCorrectionConfig,
    ForecastSensorConfig,
    ForecastSourceConfig,
    LoadConfig,
)


def statistics(value, count=10):
    history = InputPowerHistory(600, 10, 61)
    result = history.update(0, value)
    for i in range(1, count):
        result = history.update(i * 10, value)
    return result


@pytest.mark.parametrize(
    "actual,predicted,raw,factor,status",
    [(200, 400, 0.5, 0.5, "available"), (0, 400, 0, 0.25, "clamped"),
     (1000, 100, 10, 4, "clamped"), (400, 400, 1, 1, "available")],
)
def test_ratio_and_configured_bounds(actual, predicted, raw, factor, status):
    result = calculate_correction(
        statistics(actual), statistics(predicted), ForecastCorrectionConfig(),
    )
    assert result == CorrectionResult("ratio_of_means_v1", status, raw, factor)


@pytest.mark.parametrize("power", [0, 10, 49])
def test_low_denominator_is_unavailable(power):
    result = calculate_correction(statistics(100), statistics(power), ForecastCorrectionConfig())
    assert result.status == "low_forecast_power"
    assert result.factor is None


def test_missing_and_stale_history_are_unavailable():
    config = ForecastCorrectionConfig(max_age_seconds=120)
    result = calculate_correction(statistics(100, 9), statistics(200, 9), config)
    assert result.status == "insufficient_samples"
    stale = replace(statistics(100), latest_age_seconds=121)
    assert calculate_correction(stale, statistics(200), config).status == "stale_history"


def test_strategy_can_be_replaced_without_adapter_changes():
    strategy = Mock(return_value=CorrectionResult("test_v2", "available", 0.8, 0.8))
    config = ForecastCorrectionConfig(method="test_v2")
    actual, predicted = statistics(100), statistics(200)
    with patch.dict(CORRECTION_METHODS, {"test_v2": strategy}):
        result = calculate_correction(actual, predicted, config)
    strategy.assert_called_once_with(actual, predicted, config)
    assert result.factor == 0.8


@pytest.mark.parametrize("kwargs", [
    {"min_forecast_power_w": 0}, {"min_factor": -1}, {"max_factor": 0.1},
    {"max_age_seconds": 0}, {"max_factor": float("nan")},
])
def test_invalid_limits_are_rejected(kwargs):
    with pytest.raises(ValueError):
        ForecastCorrectionConfig(**kwargs)


def config(unit="W"):
    return LoadConfig(
        {}, {}, {}, {}, BatteryConfig(2240, 20, 800, 120, 95),
        {"solar": ForecastSourceConfig("test", {
            "power_now": ForecastSensorConfig("sensor.power_production_now", unit),
            "today_remaining": ForecastSensorConfig("sensor.remaining", "kWh"),
        })},
    )


@pytest.mark.parametrize("samples", [1, 10])
def test_paired_history_full_battery_and_single_snapshot_csv(tmp_path, samples):
    now = datetime.now(UTC)
    battery = BatteryState(100, 200, 130, now, now)
    ha = Mock()
    ha.get_entity_state.return_value = EntityState("test", "400", now, now)
    state = {}
    with patch("energy_manager.forecast.forecast_data_collection.time.monotonic") as clock:
        for i in range(samples):
            clock.return_value = i * 10
            update_forecast_correction(ha, config(), battery, state)
        assert state["correction"].factor == (0.5 if samples == 10 else None)
        ha.get_entity_state.return_value = EntityState("test", "2", now, now)
        with patch(
            "energy_manager.forecast.forecast_data_collection.FORECAST_LOG_PATH",
            tmp_path / "original.csv",
        ):
            update_forecast_logging(ha, config(), battery, state)
    path = next(tmp_path.glob("original_*.csv"))
    with path.open() as file:
        row = next(csv.DictReader(file))
    assert float(row["solar_today_remaining_wh"]) == 2000
    assert len(list(tmp_path.glob("*.csv"))) == 1


def test_optional_power_source_does_not_require_ha_access():
    settings = replace(config(), forecast_sources={})
    ha = Mock()
    state = {}
    update_forecast_correction(ha, settings, None, state)
    ha.get_entity_state.assert_not_called()
    assert state["correction"].factor is None


@pytest.mark.parametrize("value", ["unknown", "unavailable", "nan", "inf", "-1"])
def test_invalid_forecast_does_not_collect_unpaired_actual_values(value):
    now = datetime.now(UTC)
    battery = BatteryState(40, 200, 130, now, now)
    ha = Mock()
    ha.get_entity_state.return_value = EntityState("test", value, now, now)
    state = {}
    update_forecast_correction(ha, config(), battery, state)
    assert state["correction_actual_statistics"].sample_count == 0
    assert state["correction_predicted_statistics"].sample_count == 0
    assert state["correction"].factor is None


def test_freshness_units_missing_battery_and_ha_failure():
    now = datetime.now(UTC)
    battery = BatteryState(40, 200, 130, now, now)
    ha = Mock()
    state = {}
    old = now - timedelta(hours=1)
    ha.get_entity_state.return_value = EntityState("test", "0.4", old, now)
    update_forecast_correction(ha, config("kW"), battery, state)
    assert state["correction_actual_statistics"].sample_count == 0
    ha.get_entity_state.return_value = EntityState("test", "0.4", now, now)
    update_forecast_correction(ha, config("kW"), battery, state)
    assert state["correction_predicted_statistics"].mean_w == 400
    update_forecast_correction(ha, config("kW"), None, state)
    assert state["correction_actual_statistics"].sample_count == 1
    ha.get_entity_state.side_effect = requests.ConnectionError()
    update_forecast_correction(ha, config(), battery, state)
    assert state["correction_actual_statistics"].sample_count == 1
