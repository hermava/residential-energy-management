from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from energy_manager.forecast.offline_analysis import evaluate_power_models, fit_power_model


def training_rows(count=96, duration_hours=1):
    start = datetime(2026, 10, 18, tzinfo=ZoneInfo("Europe/Berlin")).astimezone(UTC)
    rows = []
    for index in range(count):
        stamp = start + timedelta(hours=index)
        irradiance = 0 if index % 12 == 0 else 50 + (index * 37) % 400
        incidence = (index % 7) / 6
        power = irradiance * (1.5 + 0.8 * incidence)
        rows.append({
            "target_start": stamp.isoformat(),
            "target_end": (stamp + timedelta(hours=duration_hours)).isoformat(),
            "forecast_at": (stamp - timedelta(hours=1)).isoformat(),
            "solar_irradiation_j_m2": irradiance * duration_hours * 3600,
            "panel_incidence_cosine": incidence,
            "actual_battery_input_wh": power * duration_hours,
        })
    return rows


@pytest.mark.parametrize("duration_hours", [0.5, 1, 2])
def test_fitting_recovers_known_coefficients_with_correct_units(duration_hours):
    model = fit_power_model(training_rows(duration_hours=duration_hours))
    assert model is not None
    assert model.base_coefficient == pytest.approx(1.5)
    assert model.incidence_coefficient == pytest.approx(0.8)
    assert model.predict(0, 0.5) == 0


def test_fitting_does_not_use_negative_coefficients():
    rows = training_rows()
    for index, row in enumerate(rows):
        row["solar_irradiation_j_m2"] = 100 * 3600
        row["panel_incidence_cosine"] = (index % 7) / 6
        row["actual_battery_input_wh"] = 100 * (1 - row["panel_incidence_cosine"])
    model = fit_power_model(rows)
    assert model.base_coefficient >= 0
    assert model.incidence_coefficient == 0


def test_constant_incidence_falls_back_without_a_singular_fit():
    rows = training_rows()
    for row in rows:
        row["panel_incidence_cosine"] = 0.5
        row["actual_battery_input_wh"] = row["solar_irradiation_j_m2"] / 3600 * 1.9
    model = fit_power_model(rows)
    assert model.predict(100, 0.5) == pytest.approx(190)


def test_comparison_uses_later_whole_local_days_and_one_shared_test_set():
    result = evaluate_power_models(training_rows())
    assert result.status == "available"
    assert (result.train_rows, result.test_rows) == (72, 24)
    assert (result.train_days, result.test_days) == (3, 1)
    assert result.irradiance_geometry.mae_w == pytest.approx(0, abs=1e-9)
    assert result.irradiance_only.mae_w > 0
    assert result.legacy_hourly.mae_w > 0


def test_held_out_targets_cannot_change_the_fitted_coefficients():
    rows = training_rows()
    for row in rows[-24:]:
        row["actual_battery_input_wh"] *= 2
    result = evaluate_power_models(rows)
    model = result.irradiance_geometry.model
    assert model.base_coefficient == pytest.approx(1.5)
    assert model.incidence_coefficient == pytest.approx(0.8)
    assert result.irradiance_geometry.bias_w < 0


def test_legacy_comparison_uses_hourly_power_formula_not_the_daily_intercept():
    rows = training_rows(count=48)
    for row in rows[-24:]:
        row["solar_irradiation_j_m2"] = 0
        row["actual_battery_input_wh"] = 0
    result = evaluate_power_models(rows, minimum_training_rows=20)
    assert result.legacy_hourly.mae_w == pytest.approx(3.5967)
    assert result.irradiance_geometry.mae_w == 0
    assert result.irradiance_geometry.active_mae_w is None


def test_night_only_and_one_day_data_do_not_create_a_validated_model():
    rows = training_rows(count=48)
    for row in rows:
        row["solar_irradiation_j_m2"] = 0
        row["actual_battery_input_wh"] = 0
    assert fit_power_model(rows) is None
    result = evaluate_power_models(rows, minimum_training_rows=20)
    assert result.status == "insufficient_irradiation"
    assert evaluate_power_models(training_rows(24)).status == "insufficient_training_data"
    assert evaluate_power_models([]).status == "insufficient_training_data"


@pytest.mark.parametrize("invalid", ["missing_geometry", "future_forecast", "invalid_energy"])
def test_invalid_pairs_are_excluded_without_discarding_valid_zero_values(invalid):
    rows = training_rows()
    if invalid == "missing_geometry":
        rows[1]["panel_incidence_cosine"] = None
    elif invalid == "future_forecast":
        rows[1]["forecast_at"] = rows[1]["target_end"]
    else:
        rows[1]["actual_battery_input_wh"] = float("nan")
    result = evaluate_power_models(rows)
    assert (result.train_rows, result.test_rows) == (71, 24)
    assert result.irradiance_geometry.model.base_coefficient == pytest.approx(1.5)


def test_training_interval_cannot_extend_into_the_test_day():
    rows = training_rows()
    rows[71]["target_end"] = rows[72]["target_end"]
    result = evaluate_power_models(rows)
    assert result.train_rows == 71
    assert result.test_rows == 24
