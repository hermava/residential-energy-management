from datetime import UTC, datetime
from threading import Event

import paho.mqtt.client as mqtt

from energy_manager.exceptions import InvalidMeasurementError
from energy_manager.models import BatteryState


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


def _parse_battery_state(
    payload: str,
    received_at: datetime,
) -> BatteryState:
    values = _parse_payload(payload)

    try:
        soc_percent = float(values["pe"])
        input_power_w = float(values["w1"]) + float(values["w2"])
        output_power_w = float(values["g1"]) + float(values["g2"])
    except (KeyError, ValueError) as exc:
        raise InvalidMeasurementError(
            "Invalid or incomplete MARSTEK battery state"
        ) from exc

    return BatteryState(
        soc_percent=soc_percent,
        input_power_w=input_power_w,
        output_power_w=output_power_w,
        reported_at=received_at,
        received_at=received_at,
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

    def get_battery_state(
        self,
        timeout_seconds: float = 10.0,
    ) -> BatteryState:
        response_received = Event()
        battery_state: BatteryState | None = None

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
            client.publish(
                self._request_topic,
                "cd=01",
            )

        def on_message(
            client,
            userdata,
            message,
        ) -> None:
            nonlocal battery_state
            payload = message.payload.decode("utf-8")
            battery_state = _parse_battery_state(
                payload,
                received_at=datetime.now(UTC),
            )

            response_received.set()
        client = self._create_client()
        client.on_connect = on_connect
        client.on_subscribe = on_subscribe
        client.on_message = on_message
        client.connect(
            self._host,
            self._port,
            keepalive=60,
        )
        client.loop_start()
        try:
            if not response_received.wait(timeout_seconds):
                raise TimeoutError(
                    "No MARSTEK battery state received"
                )
            if battery_state is None:
                raise RuntimeError(
                    "MARSTEK battery state was not parsed"
                )
            return battery_state
        finally:
            client.disconnect()
            client.loop_stop()

    def _create_client(self) -> mqtt.Client:
        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2
        )

        client.username_pw_set(
            self._username,
            self._password,
        )
        return client