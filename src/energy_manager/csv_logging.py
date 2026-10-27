"""Write optional diagnostics and bounded forecast training CSV data."""
import csv
import io
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from energy_manager import settings
from energy_manager.adapters.marstek_mqtt import MarstekStatus
from energy_manager.log_storage import append_with_budget
from energy_manager.settings import (
    BRIGHTSKY_FORECAST_LOG_PATH,
    FORECAST_DAILY_LOG_PATH,
    FORECAST_LOG_PATH,
    LOG_PATH,
    LOG_RETENTION_DAYS,
    PV_INPUT_ARCHIVE_PATH,
)


def append_csv_row(
    row: dict, timestamp: datetime, base_path: Path, retention_days: int | None = None,
    *, category: str = "debug",
) -> None:
    if not settings.CSV_LOGGING_ENABLED:
        return
    if category not in ("debug", "forecast"):
        raise ValueError("CSV category must be debug or forecast")
    enabled = (settings.FORECAST_DATA_LOGGING_ENABLED if category == "forecast"
               else settings.DEBUG_LOGGING_ENABLED)
    if not enabled:
        return
    maximum_bytes = (settings.FORECAST_LOG_BUDGET_BYTES if category == "forecast"
                     else settings.DEBUG_LOG_BUDGET_BYTES)
    bases = ((PV_INPUT_ARCHIVE_PATH, BRIGHTSKY_FORECAST_LOG_PATH) if category == "forecast"
             else (LOG_PATH, FORECAST_LOG_PATH, FORECAST_DAILY_LOG_PATH))
    group = tuple(sorted({base_path, *(base for base in bases if base.parent == base_path.parent)}))
    log_path = base_path.with_name(f"{base_path.stem}_{timestamp:%Y-%m-%d}{base_path.suffix}")
    try:
        existing_size = log_path.stat().st_size
    except FileNotFoundError:
        existing_size = None
    file_exists = existing_size is not None
    if not file_exists and retention_days is not None:
        _remove_old_csv_files(base_path, timestamp.date(), retention_days)
    content = io.StringIO(newline="")
    writer = csv.DictWriter(content, fieldnames=row.keys())
    if not existing_size:
        writer.writeheader()
    writer.writerow(row)
    append_with_budget(log_path, content.getvalue().encode("utf-8"), group,
                       timestamp.date(), maximum_bytes)


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
    try:
        append_csv_row(
            {"timestamp": status.received_at.isoformat(), "input_power_w": battery.input_power_w},
            status.received_at, PV_INPUT_ARCHIVE_PATH, category="forecast",
        )
    except OSError as exc:
        print(f"PV input archive failed: {exc}")
    if not settings.DEBUG_LOGGING_ENABLED:
        return
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
    try:
        append_csv_row(row, datetime.now(UTC), LOG_PATH, retention_days=LOG_RETENTION_DAYS)
    except OSError as exc:
        print(f"MARSTEK diagnostic logging failed: {exc}")
