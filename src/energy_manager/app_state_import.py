"""One-time, offline import of existing state files into the app's private storage."""

import json
import math
import os
import tempfile
from datetime import UTC, date, datetime
from pathlib import Path

from energy_manager.daily_energy import read_daily_energy

STATE_FILES = (
    "marstek_recovery_state.json", "battery_daily_energy.json", "brightsky_forecast_state.json",
)
MAX_STATE_BYTES = 1024 * 1024


class StateImportError(ValueError):
    """A safe import error containing filenames or field names, never state values."""


def _timestamp(value) -> None:
    if not isinstance(value, str) or datetime.fromisoformat(value).utcoffset() is None:
        raise ValueError("State timestamps must include a timezone")


def _number(value, nonnegative: bool = True) -> None:
    if type(value) not in (float, int) or not math.isfinite(value):
        raise ValueError("Invalid numeric state value")
    if nonnegative and value < 0:
        raise ValueError("Invalid negative state value")


def _validate_recovery(data: dict) -> None:
    if "active_until" not in data:
        raise ValueError("Recovery state must contain active_until")
    for key in ("active_until", "last_attempt_at", "restore_sent_at"):
        if data.get(key) is not None:
            _timestamp(data[key])
    if data.get("restore_target_w") is not None:
        _number(data["restore_target_w"])
    if "last_event" in data and not isinstance(data["last_event"], str):
        raise ValueError("Invalid recovery event")


def _validate_forecast(data: dict) -> None:
    for field in ("daily_predictions", "daily_comparisons"):
        records = data.get(field, {})
        if not isinstance(records, dict):
            raise ValueError("Invalid forecast history")
        for day, record in records.items():
            date.fromisoformat(day)
            if not isinstance(record, dict):
                raise ValueError("Invalid forecast record")
            _timestamp(record["forecast_at"])
            keys = ("estimated_input_wh",) if field == "daily_predictions" else (
                "forecast_input_wh", "actual_input_wh", "error_wh",
            )
            for key in keys:
                _number(record[key], nonnegative=key != "error_wh")
            if field == "daily_comparisons" and type(record["complete"]) is not bool:
                raise ValueError("Invalid forecast completeness flag")


def _state_payload(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_STATE_BYTES:
        raise StateImportError(f"Invalid import file: {path.name}")
    payload = path.read_bytes()
    try:
        data = json.loads(payload)
        if not isinstance(data, dict):
            raise ValueError("State must be a JSON object")
        if path.name == "marstek_recovery_state.json":
            _validate_recovery(data)
        elif path.name == "battery_daily_energy.json":
            read_daily_energy(path, datetime.now(UTC))
            _number(data["input_wh"])
            _number(data["output_wh"])
            if "complete" in data and type(data["complete"]) is not bool:
                raise ValueError("Invalid daily completeness flag")
        else:
            _validate_forecast(data)
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        raise StateImportError(f"Invalid state contents: {path.name}") from error
    return payload


def import_app_state(source: Path, destination: Path) -> dict:
    """Validate all files first; create atomically, never overwrite, never change values."""
    if source.is_symlink() or not source.is_dir():
        raise StateImportError("Create state-import in the app configuration folder first")
    entries = list(source.iterdir())
    if not entries or any(path.name not in STATE_FILES for path in entries):
        raise StateImportError("state-import must contain only supported state JSON files")
    payloads = {path.name: _state_payload(path) for path in entries}
    unchanged, pending = [], []
    for name, payload in payloads.items():
        target = destination / name
        if target.is_symlink():
            raise StateImportError(f"Import destination must not be a symlink: {name}")
        if target.exists():
            if not target.is_file() or target.read_bytes() != payload:
                raise StateImportError(f"Existing app state differs; nothing imported: {name}")
            unchanged.append(name)
        else:
            pending.append(name)
    destination.mkdir(parents=True, exist_ok=True)
    created = []
    try:
        with tempfile.TemporaryDirectory(prefix="state-import-", dir=destination) as staging:
            for name in pending:
                staged = Path(staging) / name
                staged.write_bytes(payloads[name])
                os.link(staged, destination / name)
                created.append(destination / name)
    except OSError:
        for path in created:
            path.unlink()
        raise
    recovery = json.loads(payloads.get("marstek_recovery_state.json", b"{}"))
    return {
        "imported": sorted(pending), "unchanged": sorted(unchanged),
        "recovery_pending": recovery.get("active_until") is not None,
    }
