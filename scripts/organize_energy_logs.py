"""Organize CSV logs and preserve compact PV measurements from existing V5 logs.

Run with the manager stopped or on an offline copy. JSON state files are not moved.
"""

import argparse
import csv
import io
import subprocess
from collections import defaultdict
from pathlib import Path

from energy_manager.forecast.offline_analysis import _load_measurements, find_training_input_files
from energy_manager.settings import PV_INPUT_ARCHIVE_PATH


def _tracked_files(data_dir: Path) -> set[Path]:
    try:
        result = subprocess.run(
            ["git", "-C", str(data_dir), "ls-files", "-z"],
            capture_output=True, text=True, check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return set()
    return {(data_dir / name).resolve() for name in result.stdout.split("\0") if name}


def _migration_plan(data_dir: Path) -> list[tuple[Path, Path]]:
    plan = {}
    for pattern in ("marstek_*.csv", "*_marstek_*.csv"):
        for path in data_dir.glob(pattern):
            plan[path] = data_dir / "logs" / "marstek" / path.name
    for pattern in ("energy_forecast_*.csv", "brightsky_forecast_*.csv"):
        for path in data_dir.glob(pattern):
            plan[path] = data_dir / "logs" / "forecast" / path.name
    legacy_pairs = data_dir / "bright_sky_input_training_pairs.csv"
    if legacy_pairs.exists():
        plan[legacy_pairs] = data_dir / "analysis" / "forecast" / legacy_pairs.name
    tracked = _tracked_files(data_dir)
    plan = {source: target for source, target in plan.items() if source.resolve() not in tracked}
    for destination in plan.values():
        if destination.exists():
            raise FileExistsError(f"Destination already exists: {destination}")
    return sorted(plan.items())


def _preserve_pv_measurements(paths: list[Path], base_path: Path) -> tuple[int, int]:
    samples = _load_measurements(paths)
    by_day = defaultdict(list)
    for timestamp, power in samples:
        by_day[timestamp.date()].append((timestamp.isoformat(), power))
    written = 0
    for day, values in sorted(by_day.items()):
        destination = base_path.with_name(f"{base_path.stem}_{day}{base_path.suffix}")
        buffer = io.StringIO(newline="")
        writer = csv.writer(buffer)
        writer.writerow(("timestamp", "input_power_w"))
        writer.writerows(values)
        content = buffer.getvalue().encode("utf-8")
        if destination.exists() and destination.read_bytes() == content:
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(".tmp")
        temporary.write_bytes(content)
        temporary.replace(destination)
        written += 1
    return len(samples), written


def organize_logs(data_dir: Path) -> dict[str, int]:
    plan = _migration_plan(data_dir)
    _, measurements = find_training_input_files(data_dir)
    archive = data_dir / "logs" / "forecast" / PV_INPUT_ARCHIVE_PATH.name
    samples, written = _preserve_pv_measurements(measurements, archive)
    for source, destination in plan:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise FileExistsError(f"Destination already exists: {destination}")
        source.rename(destination)
    return {"moved_files": len(plan), "archived_samples": samples, "archive_files_written": written}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    args = parser.parse_args()
    print(organize_logs(args.data_dir))


if __name__ == "__main__":
    main()
