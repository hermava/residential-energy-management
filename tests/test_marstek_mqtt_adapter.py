from datetime import UTC, datetime
from unittest.mock import Mock, patch

import pytest

from energy_manager.adapters.marstek_mqtt import MarstekMqttAdapter, _parse_battery_state
from energy_manager.exceptions import InvalidMeasurementError


def test_parse_battery_state() -> None:
    timestamp = datetime.now(UTC)
    payload = (
        "p1=0,p2=0,w1=0,w2=0,pe=82,vv=116,"
        "g1=75,g2=29,fc=202512040635"
    )
    state = _parse_battery_state(
        payload,
        received_at=timestamp,
    )
    assert state.soc_percent == 82.0
    assert state.input_power_w == 0.0
    assert state.output_power_w == 104.0
    assert state.reported_at == timestamp
    assert state.received_at == timestamp

def test_parse_battery_state_rejects_missing_value() -> None:
    timestamp = datetime.now(UTC)
    payload = "w1=0,w2=0,g1=75,g2=29"
    with pytest.raises(
        InvalidMeasurementError,
        match="Invalid or incomplete MARSTEK battery state",
    ):
        _parse_battery_state(
            payload,
            received_at=timestamp,
        )

def test_parse_battery_state_rejects_invalid_numeric_value() -> None:
    timestamp = datetime.now(UTC)
    payload = "pe=82,w1=0,w2=0,g1=abc,g2=29"
    with pytest.raises(
        InvalidMeasurementError,
        match="Invalid or incomplete MARSTEK battery state",
    ):
        _parse_battery_state(
            payload,
            received_at=timestamp,
        )

@patch("energy_manager.adapters.marstek_mqtt.mqtt.Client")
def test_get_battery_state(mock_client_class: Mock) -> None:
    client = mock_client_class.return_value

    def simulate_mqtt_communication() -> None:
        client.on_connect(
            client,
            None,
            None,
            0,
            None,
        )

        client.on_subscribe(
            client,
            None,
            1,
            [0],
            None,
        )

        message = Mock()
        message.payload = (
            b"pe=82,w1=10,w2=20,g1=75,g2=29"
        )

        client.on_message(
            client,
            None,
            message,
        )

    client.loop_start.side_effect = simulate_mqtt_communication

    adapter = MarstekMqttAdapter(
        host="mqtt.example",
        port=1883,
        username="user",
        password="password",
        device_type="HMJ-2",
        device_mac="123456789abc",
    )

    state = adapter.get_battery_state(
        timeout_seconds=0.1,
    )

    assert state.soc_percent == 82.0
    assert state.input_power_w == 30.0
    assert state.output_power_w == 104.0

    client.subscribe.assert_called_once_with(
        "hame_energy/HMJ-2/device/123456789abc/ctrl"
    )

    client.publish.assert_called_once_with(
        "hame_energy/HMJ-2/App/123456789abc/ctrl",
        "cd=01",
    )

    client.disconnect.assert_called_once()
    client.loop_stop.assert_called_once()

@patch("energy_manager.adapters.marstek_mqtt.mqtt.Client")
def test_get_battery_state_times_out(
    mock_client_class: Mock,
) -> None:
    client = mock_client_class.return_value

    adapter = MarstekMqttAdapter(
        host="mqtt.example",
        port=1883,
        username="user",
        password="password",
        device_type="HMJ-2",
        device_mac="123456789abc",
    )

    with pytest.raises(
        TimeoutError,
        match="No MARSTEK battery state received",
    ):
        adapter.get_battery_state(
            timeout_seconds=0.01,
        )

    client.disconnect.assert_called_once()
    client.loop_stop.assert_called_once()