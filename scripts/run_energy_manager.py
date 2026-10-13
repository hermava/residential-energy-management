import signal
import time
from collections import deque
from pathlib import Path

import requests

from energy_manager.adapters.home_assistant import HomeAssistantAdapter
from energy_manager.adapters.marstek_mqtt import MarstekMqttAdapter
from energy_manager.battery_control import _abort_recovery_on_ha_failure
from energy_manager.config import (
    get_home_assistant_token,
    get_home_assistant_url,
    get_marstek_device_mac,
    get_marstek_device_type,
    get_mqtt_host,
    get_mqtt_password,
    get_mqtt_port,
    get_mqtt_username,
)
from energy_manager.config_loader import load_config
from energy_manager.recovery import _read_recovery_state
from energy_manager.runtime import _run_cycle
from energy_manager.settings import UPDATE_INTERVAL_SECONDS


def main() -> None:
    config = load_config(Path("config/sensors.yaml"))
    ha = HomeAssistantAdapter(
        base_url=get_home_assistant_url(),
        token=get_home_assistant_token(),
    )
    marstek = MarstekMqttAdapter(
        host=get_mqtt_host(),
        port=get_mqtt_port(),
        username=get_mqtt_username(),
        password=get_mqtt_password(),
        device_type=get_marstek_device_type(),
        device_mac=get_marstek_device_mac(),
    )
    last_command_w: float | None = None
    last_command_time: float | None = None
    oscillation_history = deque()
    oscillation_state = {"last_detected_at": None}
    recovery_state = _read_recovery_state()

    # systemctl stop sends SIGTERM; use the existing shutdown path to attempt
    # restoration before the service exits.
    def _handle_sigterm(_signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _handle_sigterm)

    try:
        while True:
            try:
                last_command_w, last_command_time = _run_cycle(
                    config=config,
                    ha=ha,
                    marstek=marstek,
                    last_command_w=last_command_w,
                    last_command_time=last_command_time,
                    oscillation_history=oscillation_history,
                    oscillation_state=oscillation_state,
                    recovery_state=recovery_state,
                )
            except requests.RequestException as exc:
                print(f"Home Assistant communication failed: {exc}")
                last_command_w, last_command_time = _abort_recovery_on_ha_failure(
                    config, marstek, recovery_state, last_command_w,
                    last_command_time,
                )
            time.sleep(UPDATE_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        if recovery_state["active_until"] is not None:
            print("Stopping: requesting safe recovery cancellation")
            _abort_recovery_on_ha_failure(
                config, marstek, recovery_state,
                last_command_w, last_command_time,
            )
        print("\nEnergy manager stopped.")


if __name__ == "__main__":
    main()
