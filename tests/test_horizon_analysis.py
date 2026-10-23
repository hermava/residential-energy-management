from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from energy_manager.forecast.horizon_analysis import evaluate_forecast_horizons
from energy_manager.forecast.offline_analysis import select_latest_forecasts
from energy_manager.forecast.power_forecast import local_day_bounds

ZONE = ZoneInfo("Europe/Berlin")


def archive_rows(start_day=date(2026, 10, 17), days=8):
    rows = []
    for index in range(days):
        day = start_day + timedelta(days=index)
        start, end = local_day_bounds(day, ZONE)
        stamp = start
        while stamp < end:
            versions = [(stamp - timedelta(hours=lead), 100 if lead < 1 else 200)
                        for lead in (0.5, 2.5, 6.5, 18.5, 30.5, 54.5)]
            for offset in (1, 2):
                issue_day = day - timedelta(days=offset)
                issued = datetime.combine(issue_day, time(17, 50), ZONE).astimezone(UTC)
                versions.append((issued, 200 + offset * 100))
            for issued, irradiance in versions:
                rows.append({
                    "target_start": stamp.isoformat(),
                    "target_end": (stamp + timedelta(hours=1)).isoformat(),
                    "forecast_at": issued.isoformat(),
                    "solar_irradiation_j_m2": irradiance * 3600,
                    "panel_incidence_cosine": 0.5,
                    "actual_battery_input_wh": 200,
                })
            stamp += timedelta(hours=1)
    return rows


def test_lead_groups_use_one_version_per_target_and_freeze_before_issue_time():
    rows = archive_rows()
    report = evaluate_forecast_horizons(rows)
    assert report["status"] == "available"
    for group in report["hourly_by_lead"]:
        upper = group["maximum_lead_hours"] or float("inf")
        selected = select_latest_forecasts(rows, group["minimum_lead_hours"], upper)
        selected = [row for row in selected if row["target_start"] >= report["test_start"]]
        assert group["test_rows"] == len(selected) == 48
        assert len({row["target_start"] for row in selected}) == 48
        cutoff = datetime.fromisoformat(group["trained_through"])
        assert all(cutoff <= datetime.fromisoformat(row["forecast_at"]) for row in selected)
        assert group["model_status"] == "available"
        assert group["parameters"]["irradiance_geometry"]["base_coefficient"] == 2
    short = report["hourly_by_lead"][0]
    assert short["scores"]["irradiance_geometry"]["mae_w"] == 0
    assert report["hourly_by_lead"][-1]["scores"]["irradiance_geometry"]["mae_w"] > 0


def test_lead_bounds_are_half_open_and_choose_latest_within_each_bin():
    rows = archive_rows(days=1)
    first = rows[0]["target_start"]
    target_rows = [row for row in rows if row["target_start"] == first]
    exact = {**target_rows[0], "forecast_at": (
        datetime.fromisoformat(first) - timedelta(hours=1)
    ).isoformat()}
    target_rows.append(exact)
    assert select_latest_forecasts(target_rows, 0, 1)[0] != exact
    assert select_latest_forecasts(target_rows, 1, 3) == [exact]


def test_fixed_planning_time_selects_one_snapshot_and_separate_calendar_days():
    report = evaluate_forecast_horizons(archive_rows())
    tomorrow, day_after = report["daily_at_decision_time"]
    for group, expected_prediction in ((tomorrow, 14.4), (day_after, 19.2)):
        assert group["complete_days"] == 2
        assert group["model_status"] == "available"
        for case in group["cases"]:
            assert case["expected_hours"] == case["covered_forecast_hours"] == 24
            assert case["actual_kwh"] == pytest.approx(4.8)
            assert case["predicted_kwh"]["irradiance_geometry"] == expected_prediction
            assert case["snapshot_age_hours"] == pytest.approx(1 / 6)
            assert datetime.fromisoformat(group["trained_through"]) <= datetime.fromisoformat(
                case["decision_at"]
            )
        score = group["scores"]["irradiance_geometry"]
        assert score["mae_kwh"] == pytest.approx(expected_prediction - 4.8)
        assert score["bias_kwh"] == pytest.approx(expected_prediction - 4.8)


def test_update_after_decision_cannot_improve_the_daily_backtest():
    rows = archive_rows()
    before = evaluate_forecast_horizons(rows)["daily_at_decision_time"][0]["cases"][0]
    selected = [row for row in rows if row["forecast_at"] == before["forecast_at"]]
    late = (datetime.fromisoformat(before["decision_at"]) + timedelta(minutes=5)).isoformat()
    rows.extend({**row, "forecast_at": late, "solar_irradiation_j_m2": 9999}
                for row in selected)
    after = evaluate_forecast_horizons(rows)["daily_at_decision_time"][0]["cases"][0]
    assert before == after


@pytest.mark.parametrize("missing", ["forecast", "measurement"])
def test_incomplete_days_are_excluded_without_backfilling_older_snapshot_hours(missing):
    rows = archive_rows()
    first = evaluate_forecast_horizons(rows)["daily_at_decision_time"][0]["cases"][0]
    target = first["target_start"]
    if missing == "forecast":
        rows = [row for row in rows if not (
            row["target_start"] == target and row["forecast_at"] == first["forecast_at"]
        )]
    else:
        for row in rows:
            if row["target_start"] == target:
                row["actual_battery_input_wh"] = ""
    group = evaluate_forecast_horizons(rows)["daily_at_decision_time"][0]
    case = group["cases"][0]
    assert case["status"] == f"incomplete_{'forecast' if missing == 'forecast' else 'measurements'}"
    assert case["predicted_kwh"] is None
    assert group["complete_days"] == 1
    assert case[f"covered_{'forecast' if missing == 'forecast' else 'measurement'}_hours"] == 23


def test_old_snapshots_are_explicitly_excluded():
    group = evaluate_forecast_horizons(
        archive_rows(), maximum_snapshot_age_hours=0.1,
    )["daily_at_decision_time"][0]
    assert group["complete_days"] == 0
    assert group["coverage_counts"] == {"stale_forecast": 2}
    assert group["scores"]["legacy_hourly"] is None


@pytest.mark.parametrize("start_day,expected_hours", [
    (date(2026, 10, 19), 25), (date(2026, 3, 23), 23),
])
def test_daily_backtest_uses_actual_dst_day_length(start_day, expected_hours):
    report = evaluate_forecast_horizons(archive_rows(start_day))
    case = report["daily_at_decision_time"][0]["cases"][0]
    assert case["expected_hours"] == expected_hours
    assert case["status"] == "available"
    assert case["actual_kwh"] == pytest.approx(expected_hours * 0.2)
    assert case["predicted_kwh"]["irradiance_geometry"] == pytest.approx(expected_hours * 0.6)


@pytest.mark.parametrize("start_day", [date(2026, 10, 20), date(2026, 3, 24)])
def test_ambiguous_or_nonexistent_local_decision_time_is_not_guessed(start_day):
    report = evaluate_forecast_horizons(archive_rows(start_day), decision_time=time(2, 30))
    case = report["daily_at_decision_time"][0]["cases"][0]
    assert case["status"] == "invalid_local_decision_time"
    assert case["decision_at"] is None


def test_held_out_measurements_cannot_change_any_fitted_model():
    rows = archive_rows()
    before = evaluate_forecast_horizons(rows)
    for row in rows:
        if row["target_start"] >= before["test_start"]:
            row["actual_battery_input_wh"] *= 3
    after = evaluate_forecast_horizons(rows)
    for name in ("hourly_by_lead", "daily_at_decision_time"):
        for old, new in zip(before[name], after[name]):
            assert old["parameters"] == new["parameters"]
            assert old["train_rows"] == new["train_rows"]


def test_zero_measurements_remain_valid_days():
    rows = archive_rows()
    for row in rows:
        row["actual_battery_input_wh"] = 0
    group = evaluate_forecast_horizons(rows)["daily_at_decision_time"][0]
    assert group["complete_days"] == 2
    assert group["cases"][0]["actual_kwh"] == 0
    assert group["scores"]["irradiance_geometry"]["mae_kwh"] == 0


def test_baseline_is_reported_even_when_there_is_not_enough_training_data():
    report = evaluate_forecast_horizons(archive_rows(), minimum_training_rows=9999)
    group = report["hourly_by_lead"][0]
    assert group["model_status"] == "insufficient_training_data"
    assert group["scores"]["legacy_hourly"] is not None
    assert group["scores"]["irradiance_geometry"] is None
    assert report["daily_at_decision_time"][0]["scores"]["legacy_hourly"] is not None
    assert evaluate_forecast_horizons([])["status"] == "insufficient_target_days"


@pytest.mark.parametrize("settings", [
    {"maximum_snapshot_age_hours": 0}, {"maximum_snapshot_age_hours": float("nan")},
    {"minimum_training_rows": 0}, {"holdout_fraction": 1}, {"max_power_w": -1},
    {"decision_time": time(18, tzinfo=UTC)},
])
def test_invalid_evaluation_settings_are_rejected(settings):
    with pytest.raises(ValueError):
        evaluate_forecast_horizons([], **settings)
