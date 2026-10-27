"""Copy measurement CSV files into the Samba-accessible app configuration folder."""

import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path

LEGACY_PATTERNS = (
    "pv_input_history_*.csv", "brightsky_forecast_*.csv", "energy_forecast_*.csv",
    "marstek_*.csv", "*_marstek_*.csv",
)


class LogExportError(ValueError):
    """A local export error without file contents or credentials."""


def _measurement_files(source: Path) -> list[Path]:
    if source.is_symlink() or not source.is_dir():
        raise LogExportError("App data directory is missing or is a symlink")
    files = set()
    for pattern in LEGACY_PATTERNS:
        files.update(source.glob(pattern))
    logs = source / "logs"
    directories = (logs, logs / "forecast", logs / "marstek")
    for directory in directories:
        if directory.is_symlink():
            raise LogExportError("Measurement log directories must not be symlinks")
        if directory != logs:
            for pattern in LEGACY_PATTERNS:
                files.update(directory.glob(pattern))
    for path in files:
        if path.is_symlink() or not path.is_file():
            raise LogExportError(f"Measurement CSV must be a regular file: {path.name}")
    if not files:
        raise LogExportError("No measurement CSV files found; nothing exported")
    return sorted(files)


def export_app_logs(source: Path, destination: Path) -> tuple[Path, int]:
    """Stage a separate snapshot; preserve source files and all previous exports."""
    files = _measurement_files(source)
    if destination.is_symlink():
        raise LogExportError("Log export destination must not be a symlink")
    destination.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y-%m-%d_%H-%M-%SZ")
    with tempfile.TemporaryDirectory(prefix=".pending-", dir=destination) as temporary:
        staging = Path(temporary)
        for path in files:
            target = staging / path.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
        exported = destination / f"{timestamp}-{staging.name.removeprefix('.pending-')}"
        staging.rename(exported)
    return exported, len(files)
