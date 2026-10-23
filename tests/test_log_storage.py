import csv
import runpy
from datetime import UTC, datetime
from pathlib import Path

import pytest

from energy_manager.forecast.offline_analysis import _load_measurements, find_training_input_files

organize_logs = runpy.run_path(
    str(Path(__file__).resolve().parents[1] / "scripts" / "organize_energy_logs.py"),
)["organize_logs"]


def write_measurements(path, values):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(("timestamp", "input_power_w"))
        writer.writerows(values)


def test_discovery_supports_both_layouts_and_ignores_analysis_exports(tmp_path):
    legacy = tmp_path / "marstek_status_closed_loop_V5_2026-10-20.csv"
    archive = tmp_path / "logs" / "forecast" / "pv_input_history_2026-10-20.csv"
    write_measurements(legacy, [("2026-10-20T10:00:00+00:00", 100)])
    write_measurements(archive, [("2026-10-20T12:00:00+02:00", 100)])
    sky = archive.parent / "brightsky_forecast_2026-10-20.csv"
    sky.write_text("weather\n")
    ignored = tmp_path / "analysis" / "brightsky_forecast_2026-10-20.csv"
    ignored.parent.mkdir()
    ignored.write_text("analysis copy\n")
    forecasts, measurements = find_training_input_files(tmp_path)
    assert forecasts == [sky]
    assert set(measurements) == {legacy, archive}
    assert _load_measurements(measurements) == [(datetime(2026, 10, 20, 10, tzinfo=UTC), 100)]


def test_conflicting_duplicates_are_not_silently_selected(tmp_path):
    first, second = tmp_path / "first.csv", tmp_path / "second.csv"
    write_measurements(first, [("2026-10-20T10:00:00+00:00", 100)])
    write_measurements(second, [("2026-10-20T12:00:00+02:00", 200)])
    with pytest.raises(ValueError, match="Conflicting input measurements"):
        _load_measurements([first, second])


def test_organization_preserves_csv_content_and_archives_valid_zero_values(tmp_path):
    legacy = tmp_path / "marstek_status_closed_loop_V5_2026-10-20.csv"
    write_measurements(legacy, [
        ("2026-10-20T10:00:00+00:00", 0), ("2026-10-20T10:00:10+00:00", 100),
    ])
    original = legacy.read_bytes()
    weather = tmp_path / "brightsky_forecast_2026-10-20.csv"
    weather.write_text("original weather content\n")
    state = tmp_path / "marstek_recovery_state.json"
    state.write_text('{"active_until": null}')
    result = organize_logs(tmp_path)
    assert result == {"moved_files": 2, "archived_samples": 2, "archive_files_written": 1}
    moved = tmp_path / "logs" / "marstek" / legacy.name
    assert moved.read_bytes() == original
    assert not legacy.exists()
    assert (tmp_path / "logs" / "forecast" / weather.name).read_text() == (
        "original weather content\n"
    )
    archive = tmp_path / "logs" / "forecast" / "pv_input_history_2026-10-20.csv"
    assert _load_measurements([archive]) == _load_measurements([moved])
    assert state.read_text() == '{"active_until": null}'
    assert organize_logs(tmp_path) == {
        "moved_files": 0, "archived_samples": 2, "archive_files_written": 0,
    }
    moved.unlink()
    assert organize_logs(tmp_path)["archived_samples"] == 2
    assert archive.exists()


def test_existing_destination_is_rejected_before_any_data_changes(tmp_path):
    legacy = tmp_path / "marstek_status_closed_loop_V5_2026-10-20.csv"
    write_measurements(legacy, [("2026-10-20T10:00:00+00:00", 100)])
    target = tmp_path / "logs" / "marstek" / legacy.name
    target.parent.mkdir(parents=True)
    target.write_text("different data\n")
    before = legacy.read_bytes()
    with pytest.raises(FileExistsError):
        organize_logs(tmp_path)
    assert legacy.read_bytes() == before
    assert target.read_text() == "different data\n"
    assert not (tmp_path / "logs" / "forecast").exists()


def test_versioned_historical_csv_files_are_not_moved(monkeypatch, tmp_path):
    historical = tmp_path / "marstek_status.csv"
    historical.write_text("versioned history\n")
    monkeypatch.setitem(
        organize_logs.__globals__, "_tracked_files", lambda _: {historical.resolve()},
    )
    assert organize_logs(tmp_path) == {
        "moved_files": 0, "archived_samples": 0, "archive_files_written": 0,
    }
    assert historical.read_text() == "versioned history\n"
    assert not (tmp_path / "logs").exists()
