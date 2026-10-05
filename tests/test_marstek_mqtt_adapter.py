from datetime import UTC, datetime
from unittest.mock import Mock, patch

import pytest

from energy_manager.adapters.marstek_mqtt import (
    MarstekMqttAdapter,
    _build_output_power_command,
    _parse_battery_state,
)
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
        match="Invalid MARSTEK battery state payload",
    ):
        ...
        _parse_battery_state(
            payload,
            received_at=timestamp,
        )

def test_parse_battery_state_rejects_invalid_numeric_value() -> None:
    timestamp = datetime.now(UTC)
    payload = "pe=82,w1=0,w2=0,g1=abc,g2=29"
    with pytest.raises(
        InvalidMeasurementError,
        match="Invalid MARSTEK battery state payload",
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
        payload = (
            "pe=50,w1=100,w2=200,g1=30,g2=40,"
            "lv=70,cs=0,cd=0,do=85,cj=0,md=0,"
            "tl=20,th=21"
        )
        message = Mock()
        message.payload = payload.encode("utf-8")
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
    assert state.soc_percent == 50.0
    assert state.input_power_w == 300.0
    assert state.output_power_w == 70.0
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
        match="MARSTEK MQTT subscription timed out",
    ):
        adapter.get_battery_state(
            timeout_seconds=0.01,
        )

    client.disconnect.assert_called_once()
    client.loop_stop.assert_called_once()

def test_build_output_power_command() -> None:
    command = _build_output_power_command(
        250
    )

    assert command == (
        "cd=20,md=0,"
        "a1=1,b1=0:0,e1=23:59,v1=250,"
        "a2=0,b2=0:0,e2=0:0,v2=0,"
        "a3=0,b3=0:0,e3=0:0,v3=0"
    )

@pytest.mark.parametrize(
    "power_w",
    [99, 801, float("nan")],
)
def test_build_output_power_command_rejects_invalid_power(
    power_w: float,
) -> None:
    with pytest.raises(ValueError):
        _build_output_power_command(power_w)

@patch("energy_manager.adapters.marstek_mqtt.mqtt.Client")
def test_get_status(mock_client_class: Mock) -> None:
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
        payload = (
            "pe=50,w1=100,w2=200,g1=30,g2=40,"
            "lv=70,cs=0,cd=0,do=85,cj=0,md=0,"
            "tl=20,th=21"
        )
        message = Mock()
        message.payload = payload.encode("utf-8")
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
    status = adapter.get_status(
        timeout_seconds=0.1,
    )
    assert status.target_power_w == 70.0
    assert status.output_1_power_w == 30.0
    assert status.output_2_power_w == 40.0
    assert status.charging_setting == 0
    assert status.discharge_depth_percent == 85
    assert status.temperature_low == 20.0
    assert status.temperature_high == 21.0
    assert status.discharge_setting == 0
    assert status.scene == 0
    assert status.discharge_setting_mode == 0
    assert status.battery_state.soc_percent == 50.0
    assert status.battery_state.input_power_w == 300.0
    assert status.battery_state.output_power_w == 70.0
    assert status.raw_values["lv"] == "70"
    client.subscribe.assert_called_once_with(
        "hame_energy/HMJ-2/device/123456789abc/ctrl"
    )
    client.publish.assert_called_once_with(
        "hame_energy/HMJ-2/App/123456789abc/ctrl",
        "cd=01",
    )
    client.disconnect.assert_called_once()
    client.loop_stop.assert_called_once()