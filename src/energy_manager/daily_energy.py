"""Daily measured energy, integrated independently of battery control."""

import json
import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from energy_manager.models import BatteryState
from energy_manager.settings import DAILY_ENERGY_MAX_GAP_SECONDS, DAILY_ENERGY_STATE_PATH

LOCAL_TIMEZONE = ZoneInfo("Europe/Berlin")


@dataclass
class DailyEnergy:
    day: date
    input_wh: float = 0.0
    output_wh: float = 0.0
    complete: bool = False
    _previous: tuple[datetime, float, float] | None = None

    def update(
        self, now: datetime, input_w: float | None, output_w: float | None,
        max_gap_seconds: float,
    ) -> None:
        now = now.astimezone(UTC)
        day = now.astimezone(LOCAL_TIMEZONE).date()
        if day < self.day:
            self.complete = False
            self._previous = None
            return
        if self._previous is not None and now <= self._previous[0]:
            self.complete = False
            return
        if day > self.day:
            self.day = day
            self.input_wh = self.output_wh = 0.0
            self.complete = self._previous is not None
        if any(value is None or not math.isfinite(value) or value < 0
               for value in (input_w, output_w)):
            self.complete = False
            self._previous = None
            return
        if self._previous is not None:
            self._integrate(now, input_w, output_w, max_gap_seconds)
        self._previous = (now, input_w, output_w)

    def _integrate(
        self, now: datetime, input_w: float, output_w: float, max_gap_seconds: float,
    ) -> None:
        previous_at, previous_input, previous_output = self._previous
        seconds = (now - previous_at).total_seconds()
        if seconds <= 0 or seconds > max_gap_seconds:
            self.complete = False
            return
        midnight = datetime.combine(self.day, time.min, LOCAL_TIMEZONE).astimezone(UTC)
        start = max(previous_at, midnight)
        fraction = (start - previous_at).total_seconds() / seconds
        start_input = previous_input + fraction * (input_w - previous_input)
        start_output = previous_output + fraction * (output_w - previous_output)
        hours = (now - start).total_seconds() / 3600
        self.input_wh += (start_input + input_w) / 2 * hours
        self.output_wh += (start_output + output_w) / 2 * hours


def read_daily_energy(path: Path, now: datetime) -> DailyEnergy:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return DailyEnergy(now.astimezone(LOCAL_TIMEZONE).date())
    day = date.fromisoformat(data["day"])
    values = (data["input_wh"], data["output_wh"])
    if any(not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0
           for value in values):
        raise ValueError("Invalid persisted daily energy")
    # Keep totals, but never bridge unobserved time across a process restart.
    return DailyEnergy(day, *values)


def save_daily_energy(energy: DailyEnergy, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({
        "day": energy.day.isoformat(), "input_wh": energy.input_wh,
        "output_wh": energy.output_wh, "complete": energy.complete,
    }), encoding="utf-8")
    temporary.replace(path)


def update_daily_energy(
    battery_state: BatteryState | None, state: dict, max_age_seconds: float,
) -> DailyEnergy:
    now = datetime.now(UTC)
    energy = state.get("daily_energy")
    if energy is None:
        energy = read_daily_energy(DAILY_ENERGY_STATE_PATH, now)
        state["daily_energy"] = energy
    input_w = output_w = None
    if battery_state is not None:
        age = (now - battery_state.reported_at).total_seconds()
        if 0 <= age <= max_age_seconds:
            input_w, output_w = battery_state.input_power_w, battery_state.output_power_w
    energy.update(now, input_w, output_w, DAILY_ENERGY_MAX_GAP_SECONDS)
    save_daily_energy(energy, DAILY_ENERGY_STATE_PATH)
    return energy
