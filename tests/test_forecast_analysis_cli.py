import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "analyze_brightsky_input.py"


def test_cli_writes_horizon_settings_and_retains_existing_report_fields(tmp_path):
    report_path = tmp_path / "report.json"
    output = tmp_path / "pairs.csv"
    result = subprocess.run([
        sys.executable, str(SCRIPT), "--data-dir", str(tmp_path),
        "--config", str(ROOT / "config" / "sensors.example.yaml"),
        "--decision-time", "17:30", "--max-snapshot-age-hours", "1.5",
        "--model-report", str(report_path), "--output", str(output),
    ], cwd=ROOT, capture_output=True, text=True, check=True)
    report = json.loads(report_path.read_text())
    assert report["status"] == "insufficient_training_data"
    assert report["train_rows"] == report["test_rows"] == 0
    assert report["horizon_evaluation"]["decision_time_local"] == "17:30"
    assert report["horizon_evaluation"]["maximum_snapshot_age_hours"] == 1.5
    assert report["horizon_evaluation"]["status"] == "insufficient_target_days"
    assert output.is_file()
    assert "Wrote 0 complete hourly pairs" in result.stdout


@pytest.mark.parametrize("arguments", [
    ["--decision-time", "25:00"], ["--decision-time", "18:00:30"],
    ["--decision-time", "18:00+02:00"], ["--max-snapshot-age-hours", "0"],
    ["--max-snapshot-age-hours", "nan"],
])
def test_cli_rejects_invalid_planning_options(arguments):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), *arguments], cwd=ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 2
    assert "error:" in result.stderr
