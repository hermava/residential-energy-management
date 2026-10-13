# Structural extraction from run_energy_manager.py (V5).
# Keep behavior/settings unchanged; see REFACTORING_NOTES.md.
import json
from datetime import UTC, datetime

from energy_manager.settings import RECOVERY_STATE_PATH


def _read_recovery_state() -> dict:
    """Persistent deadline prevents indefinite recovery after a Pi restart."""
    try:
        state = json.loads(RECOVERY_STATE_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        state = {}
    return {
        "active_until": state.get("active_until"),
        "last_attempt_at": state.get("last_attempt_at"),
        "last_event": state.get("last_event", "idle"),
        "restore_target_w": state.get("restore_target_w"),
        "restore_sent_at": state.get("restore_sent_at"),
    }

def _save_recovery_state(state: dict) -> None:
    RECOVERY_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = RECOVERY_STATE_PATH.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(state), encoding="utf-8")
    tmp_path.replace(RECOVERY_STATE_PATH)

def _recovery_info(state: dict, *, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    until = state["active_until"]
    active = until is not None
    pending = active and (
        datetime.fromisoformat(until) <= now or
        state["restore_target_w"] is not None
    )
    return {
        "recovery_state": "restore_pending" if pending else "active" if active else "idle",
        "recovery_until": until,
        "recovery_last_attempt": state["last_attempt_at"],
        "recovery_event": state["last_event"],
        "recovery_restore_target_w": state["restore_target_w"],
    }
