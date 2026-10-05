import math
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Event

import paho.mqtt.client as mqtt

from energy_manager.exceptions import InvalidMeasurementError
from energy_manager.models import BatteryState


@dataclass(frozen=True)
class MarstekStatus:
    battery_state: BatteryState

    target_power_w: float
    output_1_power_w: float
    output_2_power_w: float

    charging_setting: int
    discharge_setting: int
    discharge_depth_percent: int
    scene: int
    discharge_setting_mode: int

    temperature_low: float
    temperature_high: float

    raw_values: dict[str, str]
    raw_payload: str

    received_at: datetime

def _parse_marstek_status(payload: str, received_at: datetime) -> MarstekStatus:
    values = _parse_payload(payload)
    battery_state = _parse_battery_state_from_values(
        values=values,
        received_at=received_at,
    )
    try:
        return MarstekStatus(
            battery_state=battery_state,
            target_power_w=float(values["lv"]),
            output_1_power_w=float(values["g1"]),
            output_2_power_w=float(values["g2"]),
            charging_setting=int(values["cs"]),
            discharge_setting=int(values["cd"]),
            discharge_depth_percent=int(values["do"]),
            scene=int(values["cj"]),
            discharge_setting_mode=int(values["md"]),
            temperature_low=float(values["tl"]),
            temperature_high=float(values["th"]),
            raw_values=values,
            raw_payload=payload,
            received_at=received_at,
        )
    except (KeyError, ValueError) as exc:
        raise InvalidMeasurementError(
            "Invalid MARSTEK status payload"
        ) from exc

def _parse_payload(payload: str) -> dict[str, str]:
    values = {}

    try:
        for item in payload.split(","):
            key, value = item.split("=", maxsplit=1)
            values[key] = value
    except ValueError as exc:
        raise InvalidMeasurementError(
            "Invalid MARSTEK MQTT payload format"
        ) from exc

    return values

def _parse_battery_state(payload: str, received_at: datetime) -> BatteryState:
    values = _parse_payload(payload)
    return _parse_battery_state_from_values(
        values=values,
        received_at=received_at,
    )

def _parse_battery_state_from_values(
    values: dict[str, str],
    received_at: datetime,
) -> BatteryState:
    try:
        return BatteryState(
            soc_percent=float(values["pe"]),
            input_power_w=float(values["w1"]) + float(values["w2"]),
            output_power_w=float(values["g1"]) + float(values["g2"]),
            reported_at=received_at,
            received_at=received_at,
        )
    except (KeyError, ValueError) as exc:
        raise InvalidMeasurementError(
            "Invalid MARSTEK battery state payload"
        ) from exc

def _build_output_power_command(
    power_w: float,
) -> str:
    if not math.isfinite(power_w):
        raise ValueError("MARSTEK output power must be finite")
    if not 100 <= power_w <= 800:
        raise ValueError(
            "MARSTEK output power must be between 100 and 800 W"
        )
    rounded_power_w = round(power_w)
    return (
        "cd=20,md=0,"
        f"a1=1,b1=0:0,e1=23:59,v1={rounded_power_w},"
        "a2=0,b2=0:0,e2=0:0,v2=0,"
        "a3=0,b3=0:0,e3=0:0,v3=0"
    )

class MarstekMqttAdapter:
    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        device_type: str,
        device_mac: str,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._device_type = device_type
        self._device_mac = device_mac
        self._request_topic = (
            f"hame_energy/{device_type}/App/{device_mac}/ctrl"
        )
        self._response_topic = (
            f"hame_energy/{device_type}/device/{device_mac}/ctrl"
        )

    def set_output_power(self,power_w: float) -> None:
        command = _build_output_power_command(
            power_w
        )
        self._publish_command(command)

    def get_status(self, timeout_seconds: float = 10.0) -> MarstekStatus:
        response_received = Event()
        subscription_ready = Event()
        result: MarstekStatus | None = None
        error: Exception | None = None
        client = self._create_client()
        def on_connect(
            client,
            userdata,
            flags,
            reason_code,
            properties,
        ) -> None:
            if reason_code != 0:
                return

            client.subscribe(self._response_topic)
        def on_subscribe(
            client,
            userdata,
            mid,
            reason_code_list,
            properties,
        ) -> None:
            subscription_ready.set()
        def on_message(
            client,
            userdata,
            message,
        ) -> None:
            nonlocal result, error
            try:
                payload = message.payload.decode("utf-8")
                received_at = datetime.now(UTC)
                result = _parse_marstek_status(
                    payload=payload,
                    received_at=received_at,
                )
            except Exception as exc:
                error = exc
            finally:
                response_received.set()
        client.on_connect = on_connect
        client.on_subscribe = on_subscribe
        client.on_message = on_message
        try:
            client.connect(
                self._host,
                self._port,
            )
            client.loop_start()
            if not subscription_ready.wait(timeout_seconds):
                raise TimeoutError(
                    "MARSTEK MQTT subscription timed out"
                )
            client.publish(self._request_topic, "cd=01")
            if not response_received.wait(timeout_seconds):
                raise TimeoutError(
                    "No MARSTEK battery state received"
                )
            if error is not None:
                raise error
            if result is None:
                raise TimeoutError(
                    "No MARSTEK battery state received"
                )
            return result
        finally:
            client.disconnect()
            client.loop_stop()

    def get_battery_state(self, timeout_seconds: float = 10.0) -> BatteryState:
        return self.get_status(
            timeout_seconds=timeout_seconds
        ).battery_state

    def _create_client(self) -> mqtt.Client:
        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2
        )
        client.username_pw_set(
            self._username,
            self._password,
        )
        return client

    def _publish_command(
        self,
        payload: str,
        timeout_seconds: float = 5.0,
    ) -> None:
        connected = Event()
        connect_reason_code = None

        def on_connect(
            client,
            userdata,
            flags,
            reason_code,
            properties,
        ) -> None:
            nonlocal connect_reason_code
            connect_reason_code = reason_code
            connected.set()
        client = self._create_client()
        client.on_connect = on_connect
        client.connect(
            self._host,
            self._port,
            keepalive=60,
        )
        client.loop_start()
        try:
            if not connected.wait(timeout_seconds):
                raise TimeoutError(
                    "MQTT connection timed out"
                )

            if connect_reason_code != 0:
                raise RuntimeError(
                    f"MQTT connection failed: "
                    f"{connect_reason_code}"
                )

            message_info = client.publish(
                self._request_topic,
                payload,
            )

            message_info.wait_for_publish(
                timeout=timeout_seconds
            )

            if not message_info.is_published():
                raise TimeoutError(
                    "MARSTEK command was not published"
                )
        finally:
            client.disconnect()
            client.loop_stop()