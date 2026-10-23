import csv
from datetime import UTC, datetime

import pytest

from energy_manager import settings
from energy_manager.csv_logging import append_csv_row


def test_daily_csv_appends_rows_with_one_header(tmp_path):
    timestamp = datetime(2026, 10, 22, 10, tzinfo=UTC)
    base_path = tmp_path / "logs" / "forecast.csv"
    first = {"timestamp": timestamp.isoformat(), "value": 'cloudy, "rain"\nlater'}
    second = {"timestamp": timestamp.isoformat(), "value": "sunny"}
    append_csv_row(first, timestamp, base_path)
    append_csv_row(second, timestamp, base_path)
    with (base_path.parent / "forecast_2026-10-22.csv").open(newline="") as file:
        assert list(csv.DictReader(file)) == [first, second]


@pytest.mark.parametrize("retention_days", [None, 10])
def test_csv_retention_is_optional_and_limited_to_matching_files(tmp_path, retention_days):
    timestamp = datetime(2026, 10, 22, tzinfo=UTC)
    old_path = tmp_path / "status_2026-10-11.csv"
    retained = [
        tmp_path / "status_2026-10-12.csv",
        tmp_path / "status_2026-10-13.csv",
        tmp_path / "forecast_2026-10-01.csv",
        tmp_path / "status_2026-99-99.csv",
    ]
    for path in [old_path, *retained]:
        path.write_text("existing data\n")
    append_csv_row({"value": 100}, timestamp, tmp_path / "status.csv", retention_days)
    assert old_path.exists() is (retention_days is None)
    assert all(path.read_text() == "existing data\n" for path in retained)
    assert (tmp_path / "status_2026-10-22.csv").exists()


def test_disabled_csv_writer_preserves_files_and_skips_directory_creation(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "CSV_LOGGING_ENABLED", False)
    timestamp = datetime(2026, 10, 22, tzinfo=UTC)
    old_path = tmp_path / "status_2026-10-01.csv"
    old_path.write_text("existing data\n")
    append_csv_row({"value": 100}, timestamp, tmp_path / "status.csv", retention_days=10)
    append_csv_row({"value": 100}, timestamp, tmp_path / "logs" / "forecast.csv")
    assert list(tmp_path.iterdir()) == [old_path]
    assert old_path.read_text() == "existing data\n"
