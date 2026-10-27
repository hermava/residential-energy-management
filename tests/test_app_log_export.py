from pathlib import Path

import pytest

from energy_manager import app_log_export as export


def measurement_files(source):
    files = {
        "logs/forecast/pv_input_history_2026-10-27.csv": b"timestamp,input_power_w\nnow,0\n",
        "logs/forecast/brightsky_forecast_2026-10-27.csv": b"forecast_at,cloud_cover\nnow,0\n",
        "logs/marstek/marstek_status_closed_loop_V5_2026-10-27.csv": b"soc_percent\n50\n",
        "energy_forecast_history_2026-10-26.csv": b"timestamp,input_power_w\nyesterday,100\n",
    }
    for name, payload in files.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    return files


def test_export_preserves_measurements_and_excludes_private_and_unrelated_files(tmp_path):
    source, destination = tmp_path / "data", tmp_path / "config" / "log-export"
    files = measurement_files(source)
    excluded = (
        "options.json", "battery_daily_energy.json", "marstek_recovery_state.json", ".env",
        "credentials.csv", "logs/forecast/options.json", "logs/forecast/unrelated.csv",
        "analysis/pv_input_history_2026-10-27.csv", "logs/marstek/runtime.log",
    )
    for name in excluded:
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"private content")
    original = {p.relative_to(source): p.read_bytes() for p in source.rglob("*") if p.is_file()}
    exported, count = export.export_app_logs(source, destination)
    copied = {p.relative_to(exported): p.read_bytes() for p in exported.rglob("*") if p.is_file()}
    assert count == len(files)
    assert copied == {Path(name): payload for name, payload in files.items()}
    assert original == {
        p.relative_to(source): p.read_bytes() for p in source.rglob("*") if p.is_file()
    }
    assert exported.parent == destination
    assert "Z-" in exported.name
    (exported / next(iter(files))).write_bytes(b"edited export")
    assert (source / next(iter(files))).read_bytes() == files[next(iter(files))]


def test_repeated_exports_preserve_old_snapshots_and_include_new_measurements(tmp_path):
    source, destination = tmp_path / "data", tmp_path / "config" / "log-export"
    files = measurement_files(source)
    name = next(iter(files))
    first, _ = export.export_app_logs(source, destination)
    (source / name).write_bytes(files[name] + b"later,200\n")
    second, _ = export.export_app_logs(source, destination)
    assert first != second
    assert (first / name).read_bytes() == files[name]
    assert (second / name).read_bytes() == files[name] + b"later,200\n"
    assert sorted(destination.iterdir()) == sorted((first, second))


@pytest.mark.parametrize("location", ["file", "data", "logs", "forecast", "destination"])
def test_symlinks_cannot_export_private_files_or_redirect_output(tmp_path, location):
    source, destination = tmp_path / "data", tmp_path / "config" / "log-export"
    files = measurement_files(source)
    private = tmp_path / "private"
    private.mkdir()
    secret = private / "options.json"
    secret.write_text("credentials")
    if location == "file":
        path = source / next(iter(files))
        path.unlink()
        path.symlink_to(secret)
    elif location == "destination":
        destination.parent.mkdir()
        destination.symlink_to(private, target_is_directory=True)
    else:
        relative = {"data": ".", "logs": "logs", "forecast": "logs/forecast"}[location]
        path = source / relative
        moved = tmp_path / "original-directory"
        path.rename(moved)
        path.symlink_to(moved, target_is_directory=True)
    with pytest.raises(export.LogExportError, match="symlink|regular file"):
        export.export_app_logs(source, destination)
    assert secret.read_text() == "credentials"
    assert list(private.iterdir()) == [secret]
    if location != "destination":
        assert not destination.exists()


@pytest.mark.parametrize("create_source", [False, True])
def test_no_measurements_does_not_publish_an_empty_export(tmp_path, create_source):
    source, destination = tmp_path / "data", tmp_path / "config" / "log-export"
    if create_source:
        source.mkdir()
        (source / "options.json").write_text("private")
    with pytest.raises(export.LogExportError):
        export.export_app_logs(source, destination)
    assert not destination.exists()


def test_failed_copy_removes_partial_export_and_preserves_sources_and_old_exports(
    tmp_path, monkeypatch,
):
    source, destination = tmp_path / "data", tmp_path / "config" / "log-export"
    files = measurement_files(source)
    previous, _ = export.export_app_logs(source, destination)
    copy = export.shutil.copyfile
    calls = []
    def fail_second(original, target):
        calls.append(original)
        if len(calls) == 2:
            target.write_bytes(b"partial")
            raise OSError("simulated disk full")
        return copy(original, target)
    monkeypatch.setattr(export.shutil, "copyfile", fail_second)
    with pytest.raises(OSError, match="disk full"):
        export.export_app_logs(source, destination)
    assert list(destination.iterdir()) == [previous]
    for name, payload in files.items():
        assert (source / name).read_bytes() == payload
        assert (previous / name).read_bytes() == payload
