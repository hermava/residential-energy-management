from unittest.mock import Mock, patch

import pytest

from energy_manager.adapters.home_assistant import HomeAssistantAdapter
from energy_manager.exceptions import InvalidMeasurementError, MeasurementUnavailableError


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