from unittest.mock import Mock, patch

from energy_manager.adapters.home_assistant import HomeAssistantAdapter


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