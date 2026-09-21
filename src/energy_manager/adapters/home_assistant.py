from datetime import UTC, datetime

import requests

from energy_manager.exceptions import (
    InvalidMeasurementError,
    MeasurementUnavailableError,
)
from energy_manager.models import EntityState, PowerMeasurement


class HomeAssistantAdapter:
    def __init__(self, base_url: str, token: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    def check_connection(self) -> bool:
        response = requests.get(
            f"{self._base_url}/api/",
            headers=self._headers,
            timeout=5,
        )

        response.raise_for_status()

        return response.json().get("message") == "API running."

    def _get_raw_state(self, entity_id: str) -> dict:
        response = requests.get(
            f"{self._base_url}/api/states/{entity_id}",
            headers=self._headers,
            timeout=5,
        )
        response.raise_for_status()
        return response.json()

    def get_power_measurement(self, entity_id: str) -> PowerMeasurement:
        state = self._get_raw_state(entity_id)

        raw_power = state["state"]

        if raw_power in {"unknown", "unavailable"}:
            raise MeasurementUnavailableError(
                f"Power measurement for {entity_id} is {raw_power}"
            )

        try:
            power_w = float(raw_power)
        except ValueError as exc:
            raise InvalidMeasurementError(
                f"Invalid power value for {entity_id}: {raw_power}"
            ) from exc

        reported_at = datetime.fromisoformat(state["last_reported"])
        received_at = datetime.now(UTC)

        return PowerMeasurement(
            source=entity_id,
            power_w=power_w,
            reported_at=reported_at,
            received_at=received_at,
        )

    def get_entity_state(self, entity_id: str) -> EntityState:
        state = self.get_state(entity_id)

        reported_at = datetime.fromisoformat(state["last_reported"])
        received_at = datetime.now(UTC)

        return EntityState(
            source=entity_id,
            state=state["state"],
            reported_at=reported_at,
            received_at=received_at,
        )