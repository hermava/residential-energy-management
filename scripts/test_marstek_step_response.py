import argparse
import csv
import time
from datetime import UTC, datetime
from pathlib import Path

from energy_manager.adapters.marstek_mqtt import MarstekMqttAdapter
from energy_manager.config import (
    get_marstek_device_mac,
    get_marstek_device_type,
    get_mqtt_host,
    get_mqtt_password,
    get_mqtt_port,
    get_mqtt_username,
)

SAMPLE_INTERVAL_SECONDS = 10
STABILIZATION_SECONDS = 60
STEP_HOLD_SECONDS = 40

MIN_TEST_SOC_PERCENT = 22.0
MAX_TEST_SOC_PERCENT = 90.0

LOG_PATH = Path("data/marstek_step_response.csv")


def _create_adapter() -> MarstekMqttAdapter:
    return MarstekMqttAdapter(
        host=get_mqtt_host(),
        port=get_mqtt_port(),
        username=get_mqtt_username(),
        password=get_mqtt_password(),
        device_type=get_marstek_device_type(),
        device_mac=get_marstek_device_mac(),
    )


def _append_log(
    adapter: MarstekMqttAdapter,
    phase: str,
    requested_target_w: float,
) -> None:
    status = adapter.get_status()
    battery = status.battery_state
    raw = status.raw_values

    row = {
        "timestamp": datetime.now(UTC).isoformat(),
        "phase": phase,
        "requested_target_w": requested_target_w,
        "device_target_w": status.target_power_w,
        "soc_percent": battery.soc_percent,
        "input_power_w": battery.input_power_w,
        "output_power_w": battery.output_power_w,
        "g1_w": status.output_1_power_w,
        "g2_w": status.output_2_power_w,
        "cj": status.scene,
        "p1": raw.get("p1"),
        "p2": raw.get("p2"),
        "o1": raw.get("o1"),
        "o2": raw.get("o2"),
        "raw_payload": status.raw_payload,
    }

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    file_exists = LOG_PATH.exists()

    with LOG_PATH.open("a", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=row.keys())

        if not file_exists:
            writer.writeheader()

        writer.writerow(row)

    print(
        f"{phase}: "
        f"target={status.target_power_w:.0f} W, "
        f"output={battery.output_power_w:.0f} W, "
        f"input={battery.input_power_w:.0f} W, "
        f"SOC={battery.soc_percent:.0f} %"
    )


def _hold_target(
    adapter: MarstekMqttAdapter,
    target_w: float,
    duration_seconds: int,
    phase: str,
) -> None:
    print(f"\nSetting {target_w:.0f} W")
    adapter.set_output_power(target_w)

    end_time = time.monotonic() + duration_seconds

    while time.monotonic() < end_time:
        _append_log(
            adapter=adapter,
            phase=phase,
            requested_target_w=target_w,
        )

        time.sleep(SAMPLE_INTERVAL_SECONDS)


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "mode",
        choices=["direct", "stepped"],
    )

    args = parser.parse_args()

    adapter = _create_adapter()

    initial_status = adapter.get_status()
    soc = initial_status.battery_state.soc_percent

    if not MIN_TEST_SOC_PERCENT <= soc <= MAX_TEST_SOC_PERCENT:
        raise RuntimeError(
            f"SOC {soc:.1f} % outside test range "
            f"{MIN_TEST_SOC_PERCENT:.0f}–"
            f"{MAX_TEST_SOC_PERCENT:.0f} %"
        )

    print(f"Initial SOC: {soc:.1f} %")

    # Establish identical initial condition.
    _hold_target(
        adapter,
        target_w=160.0,
        duration_seconds=STABILIZATION_SECONDS,
        phase="baseline_160",
    )

    if args.mode == "direct":
        _hold_target(
            adapter,
            target_w=110.0,
            duration_seconds=600,
            phase="direct_110",
        )

    else:
        for target_w in (135.0, 130.0, 125.0, 150.0, 130.0, 160.0, 130.0, 170.0,130.0):
            _hold_target(
                adapter,
                target_w=target_w,
                duration_seconds=STEP_HOLD_SECONDS,
                phase=f"step_{int(target_w)}",
            )

        # Observe final 110 W state longer.
        _hold_target(
            adapter,
            target_w=110.0,
            duration_seconds=600,
            phase="final_110",
        )


if __name__ == "__main__":
    main()