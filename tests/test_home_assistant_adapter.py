from unittest.mock import Mock, patch

import pytest
import requests

from energy_manager.adapters.home_assistant import HomeAssistantAdapter
from energy_manager.exceptions import (
    EntityUnavailableError,
    InvalidMeasurementError,
    MeasurementUnavailableError,
)


def test_check_connection_returns_true():
    response = Mock()
    response.json.return_value = {"message": "API running."}
    response.raise_for_status.return_value = None

    with patch(
        "energy_manager.adapters.home_assistant.requests.get",
        return_value=response,
    ) as mock_get:
        adapter = HomeAssistantAdapter(
            base_url="http://example.local:8123",
            token="test-token",
        )

        result = adapter.check_connection()

    assert result is True
    mock_get.assert_called_once_with(
        "http://example.local:8123/api/",
        headers={
            "Authorization": "Bearer test-token",
            "Content-Type": "application/json",
        },
        timeout=5,
    )

def test_get_power_measurement_raises_when_unavailable():
    adapter = HomeAssistantAdapter(
        base_url="http://example.local:8123",
        token="test-token",
    )

    state = {
        "state": "unavailable",
        "last_reported": "2026-09-18T10:38:59+00:00",
    }

    with patch.object(adapter, "_get_raw_state", return_value=state):
        with pytest.raises(MeasurementUnavailableError):
            adapter.get_power_measurement("sensor.test_power")

def test_get_power_measurement_raises_for_invalid_value():
    adapter = HomeAssistantAdapter(
        base_url="http://example.local:8123",
        token="test-token",
    )

    state = {
        "state": "abc",
        "last_reported": "2026-09-18T10:38:59+00:00",
    }

    with patch.object(adapter, "_get_raw_state", return_value=state):
        with pytest.raises(InvalidMeasurementError):
            adapter.get_power_measurement("sensor.test_power")

def test_get_entity_state_raises_entity_unavailable_for_404() -> None:
    adapter = HomeAssistantAdapter(
        base_url="http://homeassistant.local:8123",
        token="test-token",
    )

    response = Mock()
    response.status_code = 404

    with patch(
        "energy_manager.adapters.home_assistant.requests.get",
        return_value=response,
    ):
        with pytest.raises(
            EntityUnavailableError,
            match="Home Assistant entity not found: switch.tv",
        ):
            adapter.get_entity_state("switch.tv")

def test_get_entity_state_does_not_convert_500_to_entity_unavailable() -> None:
    adapter = HomeAssistantAdapter(
        base_url="http://homeassistant.local:8123",
        token="test-token",
    )

    response = Mock()
    response.status_code = 500
    response.raise_for_status.side_effect = requests.HTTPError(
        "500 Internal Server Error"
    )

    with patch(
        "energy_manager.adapters.home_assistant.requests.get",
        return_value=response,
    ):
        with pytest.raises(requests.HTTPError):
            adapter.get_entity_state("switch.tv")

def test_publish_state() -> None:
    adapter = HomeAssistantAdapter(
        base_url="http://homeassistant.local:8123",
        token="test-token",
    )

    response = Mock()
    response.raise_for_status.return_value = None

    with patch(
        "energy_manager.adapters.home_assistant.requests.post",
        return_value=response,
    ) as mock_post:
        adapter.publish_state(
            "sensor.estimated_total_load",
            "210.7",
            {
                "unit_of_measurement": "W",
                "device_class": "power",
            },
        )

    mock_post.assert_called_once_with(
        "http://homeassistant.local:8123/api/states/"
        "sensor.estimated_total_load",
        headers=adapter._headers,
        json={
            "state": "210.7",
            "attributes": {
                "unit_of_measurement": "W",
                "device_class": "power",
            },
        },
        timeout=5,
    )