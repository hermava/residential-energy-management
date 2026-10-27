"""Runtime parameters; the HA app supplies logging choices through process environment."""
import os
from pathlib import Path


def _logging_flag(name: str, default: bool) -> bool:
    value = os.getenv(name, str(default)).lower()
    if value not in ("true", "false"):
        raise ValueError(f"{name} must be true or false")
    return value == "true"


def _logging_budget(name: str, default_mib: int) -> int:
    value = int(os.getenv(name, str(default_mib)))
    if not 1 <= value <= 10240:
        raise ValueError(f"{name} must be between 1 and 10240 MiB")
    return value * 1024 * 1024

UPDATE_INTERVAL_SECONDS = 10
CSV_LOGGING_ENABLED = True  # Optional CSV files only; operational JSON persistence stays active.
# Standalone defaults remain compatible; the HA app explicitly disables debug logging.
DEBUG_LOGGING_ENABLED = _logging_flag("ENERGY_MANAGER_DEBUG_LOGGING", True)
FORECAST_DATA_LOGGING_ENABLED = _logging_flag("ENERGY_MANAGER_FORECAST_DATA_LOGGING", True)
FORECAST_LOG_BUDGET_BYTES = _logging_budget("ENERGY_MANAGER_FORECAST_LOG_MAX_MIB", 200)
DEBUG_LOG_BUDGET_BYTES = _logging_budget("ENERGY_MANAGER_DEBUG_LOG_MAX_MIB", 10)
LOG_PATH = Path("data/logs/marstek/marstek_status_closed_loop_V5.csv")
LOG_RETENTION_DAYS = 10
PV_INPUT_ARCHIVE_PATH = Path("data/logs/forecast/pv_input_history.csv")
COMMAND_DEADBAND_W = 10.0
MIN_COMMAND_INTERVAL_SECONDS = 60.0
MIN_TEST_COMMAND_POWER_W = 120.0

# Experimental, automatic oscillation recovery.
# Disable automatic recovery if that is unacceptable in your installation.
RECOVERY_ENABLED = True
RECOVERY_TARGET_W = 800.0
RECOVERY_DURATION_SECONDS = 190
RECOVERY_COOLDOWN_SECONDS = 6 * 60
RECOVERY_SOC_MARGIN_PERCENT = 1.0
RECOVERY_CONFIRM_TOLERANCE_W = 5.0
RECOVERY_RETRY_SECONDS = 30
RECOVERY_STATE_PATH = Path("data/marstek_recovery_state.json")

# Monitoring thresholds.
OSC_WINDOW_SECONDS = 120.0
OSC_MAX_GAP_SECONDS = 25.0
OSC_MAX_TARGET_RANGE_W = 30.0
OSC_MAX_INPUT_RANGE_W = 75.0
OSC_MIN_OUTPUT_RANGE_W = 15.0
OSC_MIN_DIRECTION_CHANGE_W = 10.0
OSC_MIN_REVERSALS = 3
OSC_CLEAR_DELAY_SECONDS = 60.0

FORECAST_LOG_PATH = Path("data/logs/forecast/energy_forecast_history.csv")
FORECAST_LOG_INTERVAL_SECONDS = 60
FORECAST_INPUT_HISTORY_SECONDS = 10 * 60
FORECAST_MIN_HISTORY_SAMPLES = 10
FORECAST_DAILY_LOG_PATH = Path("data/logs/forecast/energy_forecast_daily_v4.csv")
BRIGHTSKY_FORECAST_LOG_PATH = Path("data/logs/forecast/brightsky_forecast.csv")
BRIGHTSKY_FORECAST_INTERVAL_SECONDS = 3600
BRIGHTSKY_FORECAST_RETRY_SECONDS = 300
BRIGHTSKY_FORECAST_STATE_PATH = Path("data/brightsky_forecast_state.json")

DAILY_ENERGY_STATE_PATH = Path("data/battery_daily_energy.json")
DAILY_ENERGY_MAX_GAP_SECONDS = 30.0
