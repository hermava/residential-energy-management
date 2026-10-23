# Structural extraction from run_energy_manager.py (V5).
# Keep behavior/settings unchanged; see REFACTORING_NOTES.md.
import csv
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from energy_manager import settings
from energy_manager.adapters.marstek_mqtt import MarstekStatus
from energy_manager.settings import LOG_PATH, LOG_RETENTION_DAYS, PV_INPUT_ARCHIVE_PATH


def append_csv_row(
    row: dict, timestamp: datetime, base_path: Path, retention_days: int | None = None,
) -> None:
    if not settings.CSV_LOGGING_ENABLED:
        return
    log_path = base_path.with_name(f"{base_path.stem}_{timestamp:%Y-%m-%d}{base_path.suffix}")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    file_exists = log_path.exists()
    if not file_exists and retention_days is not None:
        _remove_old_csv_files(base_path, timestamp.date(), retention_days)
    with log_path.open("a", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=row.keys())
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)


def _remove_old_csv_files(base_path: Path, today: date, retention_days: int) -> None:
    cutoff = today - timedelta(days=retention_days)
    for old_path in base_path.parent.glob(f"{base_path.stem}_????-??-??.csv"):
        try:
            log_date = date.fromisoformat(old_path.stem.removeprefix(f"{base_path.stem}_"))
        except ValueError:
            continue
        if log_date < cutoff:
            old_path.unlink()

def _append_marstek_log(
    status: MarstekStatus,
    controller_target_w: float,
    control_inhibited: bool,
    last_command_w: float | None,
    command_sent: bool,
    command_reason: str,
    seconds_since_last_command: float | None,
    quality_issues: list[dict],
    measurement_diagnostics: dict,
    oscillation_assessment: dict,
    recovery_info: dict,
) -> None:
    if not settings.CSV_LOGGING_ENABLED:
        return
    battery = status.battery_state
    raw = status.raw_values
    row = {
        "timestamp": status.received_at.isoformat(),
        "soc_percent": battery.soc_percent,
        "input_power_w": battery.input_power_w,
        "output_power_w": battery.output_power_w,
        "device_target_w": status.target_power_w,
        "controller_target_w": controller_target_w,
        "control_inhibited": control_inhibited,
        "g1_w": status.output_1_power_w,
        "g2_w": status.output_2_power_w,
        "cs": status.charging_setting,
        "cd": status.discharge_setting,
        "do": status.discharge_depth_percent,
        "cj": status.scene,
        "md": status.discharge_setting_mode,
        "temperature_low": status.temperature_low,
        "temperature_high": status.temperature_high,
        "p1": raw.get("p1"),
        "p2": raw.get("p2"),
        "o1": raw.get("o1"),
        "o2": raw.get("o2"),
        "st": raw.get("st"),
        "bs": raw.get("bs"),
        "tc": raw.get("tc"),
        "tf": raw.get("tf"),
        "raw_payload": status.raw_payload,
        "last_command_w": last_command_w,
        "command_sent": command_sent,
        "command_reason": command_reason,
        "seconds_since_last_command": seconds_since_last_command,
        "quality_issues": json.dumps(quality_issues),
        "measurement_diagnostics": json.dumps(measurement_diagnostics),
        "oscillation_state": oscillation_assessment["oscillation_state"],
        "oscillation_detected": oscillation_assessment["oscillation_detected"],
        "oscillation_reason": oscillation_assessment["oscillation_reason"],
        "output_range_w": oscillation_assessment["output_range_w"],
        "direction_reversals": oscillation_assessment["direction_reversals"],
        "input_range_w": oscillation_assessment["input_range_w"],
        "recovery_state": recovery_info["recovery_state"],
        "recovery_until": recovery_info["recovery_until"],
        "recovery_last_attempt": recovery_info["recovery_last_attempt"],
        "recovery_event": recovery_info["recovery_event"],
        "recovery_restore_target_w": recovery_info["recovery_restore_target_w"],
    }
    append_csv_row(row, datetime.now(UTC), LOG_PATH, retention_days=LOG_RETENTION_DAYS)
    try:
        append_csv_row(
            {"timestamp": status.received_at.isoformat(), "input_power_w": battery.input_power_w},
            status.received_at, PV_INPUT_ARCHIVE_PATH,
        )
    except OSError as exc:
        print(f"PV input archive failed: {exc}")
