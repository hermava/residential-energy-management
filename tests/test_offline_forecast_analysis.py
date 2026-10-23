import csv
from datetime import datetime, timedelta

import pytest

from energy_manager.forecast.offline_analysis import (
    build_hourly_training_rows,
    evaluate_hourly_feature_model,
    integrate_power_interval,
)


def test_integrate_power_interval_trapezoidal():
    start = datetime.fromisoformat("2026-10-21T10:00:00+02:00")
    samples = [(start, 100.0), (start + timedelta(minutes=30), 200.0),
               (start + timedelta(hours=1), 100.0)]
    assert integrate_power_interval(
        samples, start, start + timedelta(hours=1), max_gap_seconds=1800,
    ) == pytest.approx(150)


def test_integrate_power_interval_rejects_incomplete_or_gapped_data():
    start = datetime.fromisoformat("2026-10-21T10:00:00+02:00")
    samples = [(start, 100.0), (start + timedelta(minutes=2), 100.0)]
    assert integrate_power_interval(samples, start, start + timedelta(hours=1)) is None
    assert integrate_power_interval(
        [(start, 100.0), (start + timedelta(seconds=61), 100.0)],
        start, start + timedelta(seconds=61),
    ) is None


@pytest.mark.parametrize("gap_seconds", [31, 60, 61])
@pytest.mark.parametrize("power_w", [0, 100])
def test_offline_gap_limit_includes_sixty_seconds(gap_seconds, power_w):
    start = datetime.fromisoformat("2026-10-21T10:00:00+02:00")
    end = start + timedelta(seconds=gap_seconds)
    samples = [(start, power_w), (end, power_w)]
    result = integrate_power_interval(samples, start, end)
    if gap_seconds <= 60:
        assert result == pytest.approx(power_w * gap_seconds / 3600)
    else:
        assert result is None
    assert integrate_power_interval(samples, start, end, max_gap_seconds=30) is None


@pytest.mark.parametrize(
    "sample_gap_seconds,max_gap_seconds", [(10, 30), (10, 60), (60, 30), (60, 60)],
)
def test_build_hourly_pairs_uses_latest_forecast_available_before_interval(
    tmp_path, sample_gap_seconds, max_gap_seconds,
):
    start = datetime.fromisoformat("2026-10-21T10:00:00+02:00")
    forecast_path = tmp_path / "bright.csv"
    with forecast_path.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=[
            "retrieved_at", "forecast_at", "solar_irradiation_j_m2",
            "sunshine_seconds", "cloud_cover_percent", "solar_altitude_deg",
            "solar_azimuth_deg", "panel_incidence_cosine", "day_of_year_sin",
            "day_of_year_cos",
        ])
        writer.writeheader()
        for retrieved, irradiation in ((start - timedelta(hours=2), 1000),
                                       (start - timedelta(minutes=5), 2000),
                                       (start + timedelta(minutes=1), 9000)):
            writer.writerow({
                "retrieved_at": retrieved.isoformat(),
                "forecast_at": (start + timedelta(hours=1)).isoformat(),
                "solar_irradiation_j_m2": irradiation,
                "sunshine_seconds": 1800,
                "cloud_cover_percent": 50,
                "solar_altitude_deg": 30,
                "solar_azimuth_deg": 140,
                "panel_incidence_cosine": 0.8,
                "day_of_year_sin": 0.5,
                "day_of_year_cos": 0.5,
            })
    measurement_path = tmp_path / "actual.csv"
    with measurement_path.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=["timestamp", "input_power_w"])
        writer.writeheader()
        for seconds in range(0, 3601, sample_gap_seconds):
            writer.writerow({
                "timestamp": (start + timedelta(seconds=seconds)).isoformat(),
                "input_power_w": 100,
            })
    rows = build_hourly_training_rows(
        [forecast_path], [measurement_path], max_gap_seconds=max_gap_seconds,
    )
    if sample_gap_seconds > max_gap_seconds:
        assert rows == []
        return
    assert len(rows) == 1
    assert rows[0]["solar_irradiation_j_m2"] == 2000
    assert rows[0]["actual_battery_input_wh"] == pytest.approx(100)
    assert rows[0]["panel_incidence_cosine"] == 0.8


def test_build_hourly_pairs_reconstructs_geometry_for_legacy_forecast_logs(tmp_path):
    start = datetime.fromisoformat("2026-06-21T10:00:00+02:00")
    forecast_path = tmp_path / "legacy_bright.csv"
    with forecast_path.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=[
            "retrieved_at", "forecast_at", "solar_irradiation_j_m2",
        ])
        writer.writeheader()
        writer.writerow({
            "retrieved_at": (start - timedelta(minutes=5)).isoformat(),
            "forecast_at": (start + timedelta(hours=1)).isoformat(),
            "solar_irradiation_j_m2": 800_000,
        })
    measurement_path = tmp_path / "actual.csv"
    with measurement_path.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=["timestamp", "input_power_w"])
        writer.writeheader()
        for index in range(361):
            writer.writerow({
                "timestamp": (start + timedelta(seconds=index * 10)).isoformat(),
                "input_power_w": 100,
            })
    rows = build_hourly_training_rows(
        [forecast_path], [measurement_path], latitude_deg=48.7, longitude_deg=9.1,
        panel_azimuth_deg=143, panel_tilt_deg=60,
    )
    assert len(rows) == 1
    assert isinstance(rows[0]["solar_altitude_deg"], float)
    assert isinstance(rows[0]["panel_incidence_cosine"], float)


def test_feature_model_reports_insufficient_data_without_fitting():
    result = evaluate_hourly_feature_model([])
    assert result.status == "insufficient_training_data"
    assert result.train_rows == 0
    assert result.test_rows == 0


def test_feature_model_evaluation_uses_chronological_holdout():
    rows = []
    start = datetime.fromisoformat("2026-01-01T00:00:00+00:00")
    for index in range(40):
        irradiation = float(100_000 + (index * 7919) % 900_000)
        rows.append({
            "target_start": (start + timedelta(hours=index)).isoformat(),
            "solar_irradiation_j_m2": irradiation,
            "sunshine_seconds": float(index * 73 % 3600),
            "cloud_cover_percent": float(index * 17 % 100),
            "solar_altitude_deg": float(index * 11 % 80),
            "panel_incidence_cosine": float(index * 13 % 100) / 100,
            "day_of_year_sin": float(index * 0.11 % 1),
            "day_of_year_cos": float(index * 0.17 % 1),
            "forecast_lead_hours": float(index % 24),
            "actual_battery_input_wh": irradiation / 10_000 + index % 5,
        })
    result = evaluate_hourly_feature_model(rows, minimum_training_rows=20)
    assert result.status == "available"
    assert result.train_rows == 30
    assert result.test_rows == 10
    assert result.baseline_mae_wh is not None
    assert result.feature_model_mae_wh is not None
