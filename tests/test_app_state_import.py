import json

import pytest

from energy_manager import app_state_import as migration


def state_contents():
    return {
        "marstek_recovery_state.json": {
            "active_until": None, "last_event": "idle", "last_attempt_at": None,
            "restore_target_w": None, "restore_sent_at": None,
        },
        "battery_daily_energy.json": {
            "day": "2026-10-26", "input_wh": 123.5, "output_wh": 234.5, "complete": False,
        },
        "brightsky_forecast_state.json": {
            "daily_predictions": {"2026-10-27": {
                "forecast_at": "2026-10-26T12:00:00+00:00", "estimated_input_wh": 1000,
            }},
            "daily_comparisons": {"2026-10-25": {
                "forecast_at": "2026-10-24T12:00:00+00:00", "forecast_input_wh": 1000,
                "actual_input_wh": 500, "error_wh": -500, "complete": False,
            }},
        },
    }


def write_source(tmp_path, contents=None):
    source = tmp_path / "state-import"
    source.mkdir()
    for name, value in (contents or state_contents()).items():
        (source / name).write_text(json.dumps(value, indent=2) + "\n")
    return source


def test_import_preserves_exact_values_bytes_and_originals_and_is_repeatable(tmp_path):
    source = write_source(tmp_path)
    destination = tmp_path / "private"
    destination.mkdir()
    options = destination / "options.json"
    options.write_text('{"private": "credentials"}')
    original = {path.name: path.read_bytes() for path in source.iterdir()}
    result = migration.import_app_state(source, destination)
    assert result == {
        "imported": sorted(migration.STATE_FILES), "unchanged": [], "recovery_pending": False,
    }
    for name, payload in original.items():
        assert (source / name).read_bytes() == (destination / name).read_bytes() == payload
    assert options.read_text() == '{"private": "credentials"}'
    again = migration.import_app_state(source, destination)
    assert again["imported"] == []
    assert again["unchanged"] == sorted(migration.STATE_FILES)


def test_pending_recovery_is_reported_without_modifying_its_deadline(tmp_path):
    contents = state_contents()
    contents["marstek_recovery_state.json"]["active_until"] = "2026-10-26T12:05:00+00:00"
    source = write_source(tmp_path, contents)
    destination = tmp_path / "private"
    assert migration.import_app_state(source, destination)["recovery_pending"] is True
    assert (source / "marstek_recovery_state.json").read_bytes() == (
        destination / "marstek_recovery_state.json"
    ).read_bytes()


@pytest.mark.parametrize("filename,content", [
    ("marstek_recovery_state.json", '{"active_until": "private-invalid-timestamp"}'),
    ("marstek_recovery_state.json", '{"active_until": "2026-10-26T12:00:00"}'),
    ("marstek_recovery_state.json", '{}'),
    ("battery_daily_energy.json", '{"day": "2026-10-26", "input_wh": NaN, "output_wh": 1}'),
    ("battery_daily_energy.json", '{"day": "2026-10-26", "input_wh": true, "output_wh": 1}'),
    ("battery_daily_energy.json", '{}'),
    ("brightsky_forecast_state.json", '{"daily_predictions": []}'),
    ("brightsky_forecast_state.json", '[]'),
    ("brightsky_forecast_state.json", 'not JSON: private-content'),
])
def test_all_files_are_validated_before_any_state_is_imported(tmp_path, filename, content):
    source = write_source(tmp_path)
    (source / filename).write_text(content)
    destination = tmp_path / "private"
    with pytest.raises(migration.StateImportError) as error:
        migration.import_app_state(source, destination)
    assert not destination.exists()
    assert filename in str(error.value)
    assert "private" not in str(error.value)


def test_conflicting_existing_file_prevents_the_entire_import(tmp_path):
    source = write_source(tmp_path)
    destination = tmp_path / "private"
    destination.mkdir()
    existing = destination / "brightsky_forecast_state.json"
    existing.write_text('{"existing": true}')
    with pytest.raises(migration.StateImportError, match="Existing app state differs"):
        migration.import_app_state(source, destination)
    assert list(destination.iterdir()) == [existing]
    assert existing.read_text() == '{"existing": true}'


@pytest.mark.parametrize("filename", ["options.json", ".env", "unrelated.json"])
def test_private_configuration_and_unrelated_files_cannot_be_imported(tmp_path, filename):
    source = write_source(tmp_path)
    (source / filename).write_text("private-content")
    destination = tmp_path / "private"
    with pytest.raises(migration.StateImportError, match="only supported"):
        migration.import_app_state(source, destination)
    assert not destination.exists()


def test_missing_or_empty_import_folder_does_not_create_new_state(tmp_path):
    source = tmp_path / "state-import"
    destination = tmp_path / "private"
    with pytest.raises(migration.StateImportError):
        migration.import_app_state(source, destination)
    source.mkdir()
    with pytest.raises(migration.StateImportError):
        migration.import_app_state(source, destination)
    assert not destination.exists()


def test_symlinked_source_file_is_rejected(tmp_path):
    source = write_source(tmp_path)
    original = source / "marstek_recovery_state.json"
    moved = tmp_path / "original.json"
    original.rename(moved)
    original.symlink_to(moved)
    with pytest.raises(migration.StateImportError):
        migration.import_app_state(source, tmp_path / "private")


def test_partial_filesystem_failure_rolls_back_only_newly_created_files(tmp_path, monkeypatch):
    source = write_source(tmp_path)
    destination = tmp_path / "private"
    destination.mkdir()
    untouched = destination / "options.json"
    untouched.write_text("keep")
    link = migration.os.link
    calls = []
    def fail_second(staged, target):
        calls.append(target)
        if len(calls) == 2:
            raise OSError("simulated disk error")
        link(staged, target)
    monkeypatch.setattr(migration.os, "link", fail_second)
    with pytest.raises(OSError):
        migration.import_app_state(source, destination)
    assert list(destination.iterdir()) == [untouched]
    assert untouched.read_text() == "keep"
    assert len(list(source.iterdir())) == 3


def test_source_files_are_not_hardlinked_to_user_editable_import_folder(tmp_path):
    source = write_source(tmp_path)
    destination = tmp_path / "private"
    migration.import_app_state(source, destination)
    copied = destination / "marstek_recovery_state.json"
    original = copied.read_bytes()
    (source / copied.name).write_text("changed by user later")
    assert copied.read_bytes() == original


def test_only_available_state_files_can_be_imported_without_inventing_missing_ones(tmp_path):
    source = write_source(tmp_path, {"brightsky_forecast_state.json": {}})
    destination = tmp_path / "private"
    result = migration.import_app_state(source, destination)
    assert result["imported"] == ["brightsky_forecast_state.json"]
    assert sorted(path.name for path in destination.iterdir()) == ["brightsky_forecast_state.json"]
