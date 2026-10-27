import csv
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from energy_manager import csv_logging, log_storage, runtime, settings

TODAY = date(2026, 10, 26)


@pytest.fixture(autouse=True)
def isolated_budget_cache():
    log_storage._budgets.clear()
    yield
    log_storage._budgets.clear()


def test_shared_budget_removes_oldest_owned_day_and_preserves_other_files(tmp_path):
    pv, weather = tmp_path / "pv.csv", tmp_path / "weather.csv"
    oldest = tmp_path / "pv_2026-10-24.csv"
    newer = tmp_path / "weather_2026-10-25.csv"
    oldest.write_bytes(b"x" * 60)
    newer.write_bytes(b"y" * 60)
    untouched = [tmp_path / "battery_daily_energy.json", tmp_path / "unknown_2026-10-01.csv",
                 tmp_path / "pv_2026-99-99.csv"]
    for path in untouched:
        path.write_text("keep")
    current = tmp_path / "pv_2026-10-26.csv"
    assert log_storage.append_with_budget(current, b"z" * 20, (pv, weather), TODAY, 100)
    assert not oldest.exists()
    assert newer.read_bytes() == b"y" * 60
    assert current.stat().st_size + newer.stat().st_size == 80
    assert all(path.read_text() == "keep" for path in untouched)


def test_full_current_day_pauses_without_deleting_either_stream_then_resumes_next_day(
    tmp_path, capsys,
):
    pv, weather = tmp_path / "pv.csv", tmp_path / "weather.csv"
    current = tmp_path / "pv_2026-10-26.csv"
    other = tmp_path / "weather_2026-10-26.csv"
    current.write_bytes(b"x" * 50)
    other.write_bytes(b"y" * 50)
    for _ in range(2):
        assert not log_storage.append_with_budget(current, b"z", (pv, weather), TODAY, 100)
    assert capsys.readouterr().out.count("recording paused") == 1
    assert current.read_bytes() == b"x" * 50
    assert other.read_bytes() == b"y" * 50
    tomorrow = tmp_path / "pv_2026-10-27.csv"
    assert log_storage.append_with_budget(
        tomorrow, b"z", (pv, weather), TODAY + timedelta(days=1), 100,
    )
    assert sum(path.stat().st_size for path in tmp_path.glob("*.csv")) <= 100
    assert tomorrow.read_bytes() == b"z"


def test_one_oversized_record_does_not_delete_history_for_an_impossible_write(tmp_path):
    base = tmp_path / "pv.csv"
    old = tmp_path / "pv_2026-10-25.csv"
    old.write_bytes(b"keep")
    current = tmp_path / "pv_2026-10-26.csv"
    assert not log_storage.append_with_budget(current, b"x" * 101, (base,), TODAY, 100)
    assert old.read_bytes() == b"keep"
    assert not current.exists()


def test_cached_accounting_bounds_repeated_writes_without_rescanning(tmp_path, monkeypatch):
    base = tmp_path / "pv.csv"
    current = tmp_path / "pv_2026-10-26.csv"
    scan = Mock(wraps=log_storage._scan)
    monkeypatch.setattr(log_storage, "_scan", scan)
    for _ in range(10):
        assert log_storage.append_with_budget(current, b"x" * 10, (base,), TODAY, 100)
    assert scan.call_count == 1
    assert current.stat().st_size == 100
    assert not log_storage.append_with_budget(current, b"x", (base,), TODAY, 100)
    assert current.stat().st_size == 100


def test_periodic_rescan_accounts_for_external_files(tmp_path, monkeypatch):
    clock = [0]
    monkeypatch.setattr(log_storage.time, "monotonic", lambda: clock[0])
    base = tmp_path / "pv.csv"
    current = tmp_path / "pv_2026-10-26.csv"
    assert log_storage.append_with_budget(current, b"x" * 50, (base,), TODAY, 100)
    old = tmp_path / "pv_2026-10-25.csv"
    old.write_bytes(b"y" * 60)
    clock[0] += log_storage.RESCAN_SECONDS
    assert log_storage.append_with_budget(current, b"z", (base,), TODAY, 100)
    assert not old.exists()
    assert current.stat().st_size == 51


def test_symlinked_history_is_not_deleted_or_followed(tmp_path):
    state = tmp_path / "state.json"
    state.write_bytes(b"keep")
    link = tmp_path / "pv_2026-10-25.csv"
    link.symlink_to(state)
    current = tmp_path / "pv_2026-10-26.csv"
    assert log_storage.append_with_budget(current, b"x", (tmp_path / "pv.csv",), TODAY, 1)
    assert link.is_symlink()
    assert state.read_bytes() == b"keep"


def test_current_csv_symlink_cannot_redirect_writes_into_state(tmp_path):
    state = tmp_path / "state.json"
    state.write_text("keep")
    current = tmp_path / "pv_2026-10-26.csv"
    current.symlink_to(state)
    with pytest.raises(OSError, match="symlink"):
        log_storage.append_with_budget(current, b"x", (tmp_path / "pv.csv",), TODAY, 100)
    assert state.read_text() == "keep"


@pytest.mark.parametrize(
    "debug,forecast", [(False, False), (False, True), (True, False), (True, True)],
)
def test_categories_are_independent(monkeypatch, tmp_path, debug, forecast):
    monkeypatch.setattr(settings, "DEBUG_LOGGING_ENABLED", debug)
    monkeypatch.setattr(settings, "FORECAST_DATA_LOGGING_ENABLED", forecast)
    stamp = datetime(2026, 10, 26, tzinfo=UTC)
    csv_logging.append_csv_row({"value": 1}, stamp, tmp_path / "debug" / "rows.csv")
    csv_logging.append_csv_row(
        {"value": 1}, stamp, tmp_path / "forecast" / "rows.csv", category="forecast",
    )
    assert (tmp_path / "debug" / "rows_2026-10-26.csv").exists() is debug
    assert (tmp_path / "forecast" / "rows_2026-10-26.csv").exists() is forecast


def test_pv_measurement_archive_still_records_zero_without_full_diagnostics(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "DEBUG_LOGGING_ENABLED", False)
    monkeypatch.setattr(settings, "FORECAST_DATA_LOGGING_ENABLED", True)
    monkeypatch.setattr(csv_logging, "PV_INPUT_ARCHIVE_PATH", tmp_path / "pv.csv")
    monkeypatch.setattr(csv_logging, "LOG_PATH", tmp_path / "debug.csv")
    stamp = datetime(2026, 10, 26, tzinfo=UTC)
    status = SimpleNamespace(received_at=stamp, battery_state=SimpleNamespace(input_power_w=0))
    csv_logging._append_marstek_log(status, 130, False, 130, False, "deadband", 60, [], {}, {}, {})
    with (tmp_path / "pv_2026-10-26.csv").open(newline="") as file:
        assert list(csv.DictReader(file)) == [
            {"timestamp": stamp.isoformat(), "input_power_w": "0"},
        ]
    assert not (tmp_path / "debug_2026-10-26.csv").exists()


def test_csv_budget_counts_utf8_bytes_and_preserves_one_header(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "FORECAST_LOG_BUDGET_BYTES", 100)
    stamp = datetime(2026, 10, 26, tzinfo=UTC)
    base = tmp_path / "pv.csv"
    row = {"value": "ä" * 10}
    for _ in range(10):
        csv_logging.append_csv_row(row, stamp, base, category="forecast")
    path = tmp_path / "pv_2026-10-26.csv"
    assert path.stat().st_size <= 100
    with path.open(newline="") as file:
        assert list(csv.DictReader(file)) == [row] * 4


def test_forecast_budget_is_shared_but_cannot_delete_debug_csv(monkeypatch, tmp_path):
    monkeypatch.setattr(csv_logging, "PV_INPUT_ARCHIVE_PATH", tmp_path / "pv.csv")
    monkeypatch.setattr(csv_logging, "BRIGHTSKY_FORECAST_LOG_PATH", tmp_path / "weather.csv")
    monkeypatch.setattr(settings, "FORECAST_LOG_BUDGET_BYTES", 100)
    pv = tmp_path / "pv_2026-10-24.csv"
    weather = tmp_path / "weather_2026-10-25.csv"
    debug = tmp_path / "energy_forecast_history_2026-10-01.csv"
    for path in (pv, weather, debug):
        path.write_bytes(b"x" * 60)
    stamp = datetime(2026, 10, 26, tzinfo=UTC)
    csv_logging.append_csv_row({"value": 1}, stamp, tmp_path / "pv.csv", category="forecast")
    assert not pv.exists()
    assert weather.exists()
    assert debug.read_bytes() == b"x" * 60
    csv_logging.append_csv_row({"value": 2}, stamp, tmp_path / "weather.csv", category="forecast")
    assert sum(path.stat().st_size for path in tmp_path.glob("pv_*.csv")) + sum(
        path.stat().st_size for path in tmp_path.glob("weather_*.csv")
    ) <= 100


def test_debug_cycle_output_is_optional_and_does_not_touch_inputs(monkeypatch, capsys):
    monkeypatch.setattr(settings, "DEBUG_LOGGING_ENABLED", False)
    runtime._print_cycle_status(None, None, 130)
    assert capsys.readouterr().out == ""
    monkeypatch.setattr(settings, "DEBUG_LOGGING_ENABLED", True)
    runtime._print_cycle_status(SimpleNamespace(total_power_w=130), None, 130)
    assert "Controller target: 130.0 W" in capsys.readouterr().out


def test_empty_csv_left_by_a_failed_write_gets_a_header(monkeypatch, tmp_path):
    stamp = datetime(2026, 10, 26, tzinfo=UTC)
    path = tmp_path / "pv_2026-10-26.csv"
    path.touch()
    csv_logging.append_csv_row({"value": "0"}, stamp, tmp_path / "pv.csv", category="forecast")
    with path.open(newline="") as file:
        assert list(csv.DictReader(file)) == [{"value": "0"}]
