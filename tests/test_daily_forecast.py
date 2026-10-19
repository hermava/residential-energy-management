from dataclasses import replace

import pytest

from energy_manager.forecast.forecast import forecast_daily_energy
from energy_manager.models import EnergyForecastConfig


def test_daily_balance_accounts_for_total_consumption_once():
    result = forecast_daily_energy(80, 1000, 2, 2240, EnergyForecastConfig())
    assert result.assessment == "surplus_expected"
    assert result.data_quality == "heuristic"
    assert result.consumption_wh == 260
    assert result.balance_energy_wh == 1792 + 1000 - 260
    assert result.balance_soc_percent == 100
    assert result.surplus_energy_wh == 292


def test_correction_can_change_assessment_and_original_is_preserved():
    config = EnergyForecastConfig()
    original = forecast_daily_energy(40, 1600, 2, 2240, config)
    corrected = forecast_daily_energy(40, 1600, 2, 2240, config, 0.5)
    assert original.assessment == "surplus_expected"
    assert corrected.assessment == "limited"
    assert corrected.effective_pv_wh == 800
    assert corrected.original_pv_wh == 1600
    assert corrected.original_balance_soc_percent == original.balance_soc_percent


def test_strict_configurable_soc_thresholds():
    config = EnergyForecastConfig(consumption_w=0)
    assert forecast_daily_energy(30, 2000, 1, 1000, config).assessment == "limited"
    assert forecast_daily_energy(40, 550, 1, 1000, config).assessment == "limited"
    assert forecast_daily_energy(40, 551, 1, 1000, config).assessment == "surplus_expected"
    config = replace(config, surplus_min_soc_percent=50, surplus_target_soc_percent=90)
    assert forecast_daily_energy(40, 1000, 1, 1000, config).assessment == "limited"
    assert forecast_daily_energy(60, 301, 1, 1000, config).assessment == "surplus_expected"


def test_efficiency_assumptions_are_explicit_and_configurable():
    config = EnergyForecastConfig(input_efficiency=0.9, output_efficiency=0.8)
    result = forecast_daily_energy(40, 1000, 2, 2240, config)
    assert result.consumption_wh == 325
    assert result.balance_energy_wh == 896 + 900 - 325


@pytest.mark.parametrize("soc,pv,hours", [
    (None, 1000, 2), (40, None, 2), (40, 1000, None),
    (float("nan"), 1000, 2), (40, float("inf"), 2), (40, 1000, float("nan")),
    (-1, 1000, 2), (101, 1000, 2), (40, -1, 2), (40, 1000, -1),
])
def test_missing_or_invalid_data_never_produces_an_assessment(soc, pv, hours):
    result = forecast_daily_energy(soc, pv, hours, 2240, EnergyForecastConfig())
    assert result.assessment == "unavailable"
    assert result.data_quality == "unavailable"
    assert result.balance_soc_percent is None


def test_ended_daylight_and_negative_balance():
    config = EnergyForecastConfig()
    assert forecast_daily_energy(80, 1000, 0, 2240, config).reason == "daylight_period_ended"
    result = forecast_daily_energy(20, 0, 8, 2240, config, input_mean_w=0)
    assert result.balance_soc_percent == 0
    assert result.balance_energy_wh < 0
    assert result.assessment == "reserve_at_risk"


@pytest.mark.parametrize("factor", [-1, float("nan"), float("inf")])
def test_invalid_correction_is_unavailable(factor):
    result = forecast_daily_energy(80, 1000, 2, 2240, EnergyForecastConfig(), factor)
    assert result.assessment == "unavailable"


@pytest.mark.parametrize("kwargs", [
    {"consumption_w": -1}, {"consumption_w": float("nan")},
    {"surplus_min_soc_percent": 95}, {"surplus_target_soc_percent": 101},
    {"input_efficiency": 0}, {"output_efficiency": 1.1},
    {"input_efficiency": float("nan")},
])
def test_invalid_forecast_configuration(kwargs):
    with pytest.raises(ValueError):
        EnergyForecastConfig(**kwargs)


@pytest.mark.parametrize("soc,pv,input_w,expected", [
    (20, 0, 0, "reserve_at_risk"),
    (20, 100, 50, "reserve_at_risk"),
    (20, 0, 800, "limited"),
    (20, 2000, 0, "limited"),
    (80, 0, 0, "limited"),
    (30, 0, 0, "limited"),
    (20, 0, 130, "limited"),
    (20, 260, 0, "limited"),
    (80, 2000, 800, "surplus_expected"),
])
def test_three_assessment_rules_and_strict_boundaries(soc, pv, input_w, expected):
    result = forecast_daily_energy(soc, pv, 2, 2240, EnergyForecastConfig(), input_mean_w=input_w)
    assert result.assessment == expected
    assert result.data_quality == "heuristic"


@pytest.mark.parametrize("input_w", [None, -1, float("nan"), float("inf")])
def test_missing_input_history_cannot_decide_between_risk_and_limited(input_w):
    result = forecast_daily_energy(20, 0, 2, 2240, EnergyForecastConfig(), input_mean_w=input_w)
    assert result.assessment == "unavailable"
    assert result.reason == "missing_input_history"
    assert result.balance_energy_wh == 188


def test_reserve_threshold_and_consumption_are_configurable():
    config = EnergyForecastConfig(reserve_soc_percent=25, consumption_w=200)
    result = forecast_daily_energy(24, 0, 2, 2240, config, input_mean_w=150)
    assert result.assessment == "reserve_at_risk"
    result = forecast_daily_energy(25, 0, 2, 2240, config, input_mean_w=150)
    assert result.assessment == "limited"


def test_declining_reserve_uses_corrected_energy_and_configured_losses():
    config = EnergyForecastConfig()
    original = forecast_daily_energy(20, 400, 2, 2240, config, input_mean_w=0)
    corrected = forecast_daily_energy(20, 400, 2, 2240, config, 0.5, input_mean_w=0)
    assert original.assessment == "limited"
    assert corrected.assessment == "reserve_at_risk"
    losses = replace(config, input_efficiency=0.9)
    result = forecast_daily_energy(20, 260, 2, 2240, losses, input_mean_w=0)
    assert result.assessment == "reserve_at_risk"


@pytest.mark.parametrize("kwargs", [
    {"reserve_soc_percent": -1}, {"reserve_soc_percent": 40},
    {"reserve_soc_percent": float("nan")}, {"input_history_max_age_seconds": 0},
    {"input_history_max_age_seconds": float("inf")},
])
def test_invalid_risk_configuration(kwargs):
    with pytest.raises(ValueError):
        EnergyForecastConfig(**kwargs)
