"""Adapt Home Assistant app options and mounts to the existing standalone manager."""

import json
import math
import os
import shutil
import sys
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

from energy_manager.app_state_import import StateImportError, import_app_state
from energy_manager.config_loader import load_config

APP_DIRECTORY = Path("/app")
OPTIONS_PATH = Path("/data/options.json")
SENSORS_PATH = Path("/config/sensors.yaml")
TEMPLATE_PATH = APP_DIRECTORY / "defaults/sensors.example.yaml"
PRIVATE_DATA_PATH = Path("/data")


class AppConfigurationError(ValueError):
    """A configuration error whose message identifies a setting without revealing its value."""


def _text(options: dict, key: str, default: str = "") -> str:
    value = options.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise AppConfigurationError(f"App option {key} must be nonempty text")
    return value


def app_environment(options: dict, supervisor_token: str) -> dict[str, str]:
    """Validate local options without contacting HA, MQTT, weather services or the battery."""
    if not supervisor_token:
        raise AppConfigurationError("SUPERVISOR_TOKEN is missing; enable Home Assistant API access")
    environment = {
        "HOME_ASSISTANT_URL": "http://supervisor/core",
        "HOME_ASSISTANT_TOKEN": supervisor_token,
    }
    for key in ("mqtt_host", "mqtt_username", "mqtt_password",
                "marstek_device_type", "marstek_device_mac"):
        environment[key.upper()] = _text(options, key)
    for key, default, minimum, maximum in (
        ("mqtt_port", 1883, 1, 65535),
        ("brightsky_max_distance_m", 50000, 0, math.inf),
    ):
        value = options.get(key, default)
        if type(value) is not int or not minimum <= value <= maximum:
            raise AppConfigurationError(f"App option {key} must be an integer in its allowed range")
        environment[key.upper()] = str(value)
    coordinates = [options.get(key) for key in ("brightsky_latitude", "brightsky_longitude")]
    if (coordinates[0] is None) != (coordinates[1] is None):
        raise AppConfigurationError("Configure both Bright Sky coordinates, or leave both unset")
    for key, value, limit in zip(
        ("BRIGHTSKY_LATITUDE", "BRIGHTSKY_LONGITUDE"), coordinates, (90, 180),
    ):
        if value is not None and (
            type(value) not in (int, float) or not math.isfinite(value) or abs(value) > limit
        ):
            raise AppConfigurationError(f"App option {key.lower()} is outside its allowed range")
        environment[key] = "" if value is None else str(value)
    timezone = _text(options, "brightsky_timezone", "Europe/Berlin")
    try:
        ZoneInfo(timezone)
    except (ValueError, ZoneInfoNotFoundError) as error:
        raise AppConfigurationError(
            "App option brightsky_timezone must name a valid timezone"
        ) from error
    environment["BRIGHTSKY_TIMEZONE"] = timezone
    environment["TZ"] = timezone
    return environment


def main() -> int:
    try:
        options = json.loads(OPTIONS_PATH.read_text(encoding="utf-8"))
        if not isinstance(options, dict):
            raise AppConfigurationError("App options must be a JSON object")
        run_manager = options.get("run_manager", False)
        import_state = options.get("import_state", False)
        if type(run_manager) is not bool or type(import_state) is not bool:
            raise AppConfigurationError("run_manager and import_state must be true or false")
        if run_manager and import_state:
            raise AppConfigurationError("State import requires run_manager: false")
        if not SENSORS_PATH.exists():
            SENSORS_PATH.parent.mkdir(parents=True, exist_ok=True)
            with TEMPLATE_PATH.open("rb") as source, SENSORS_PATH.open("xb") as target:
                shutil.copyfileobj(source, target)
            print("Created /config/sensors.yaml from the example. Adapt it before starting again.")
            return 1
        load_config(SENSORS_PATH)
        environment = app_environment(options, os.getenv("SUPERVISOR_TOKEN", ""))
        if import_state:
            result = import_app_state(SENSORS_PATH.parent / "state-import", PRIVATE_DATA_PATH)
            print(f"State import complete. Imported: {', '.join(result['imported']) or 'none'}.")
            print(f"Identical existing files: {', '.join(result['unchanged']) or 'none'}.")
            if result["recovery_pending"]:
                print("Imported recovery is pending. Review before enabling battery control.")
            print("Disable import_state before enabling run_manager. No network requests made.")
            return 0
    except (AppConfigurationError, StateImportError) as error:
        print(f"App configuration failed: {error}", file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError) as error:
        print(f"App configuration failed ({type(error).__name__}). Check options and sensors.yaml.",
              file=sys.stderr)
        return 1
    if not run_manager:
        print("Local configuration validated. No network requests or battery commands were made.")
        print("run_manager is false; the manager remains stopped. This does not test connectivity.")
        return 0
    os.environ.update(environment)
    os.chdir(APP_DIRECTORY)
    print("Starting energy manager; battery control is active.", flush=True)
    command = [sys.executable, "-u", str(APP_DIRECTORY / "scripts/run_energy_manager.py")]
    os.execv(sys.executable, command)
    return 0


if __name__ == "__main__":
    sys.exit(main())
