"""Estimated export from measured battery output and the existing household load estimate."""

import json
import math
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from pathlib import Path

from energy_manager import settings
from energy_manager.daily_energy import LOCAL_TIMEZONE
from energy_manager.models import BatteryState, LoadEstimate, PowerContributionStatus

EXPORT_OUTPUTS = (
    "estimated_export_power", "estimated_export_energy_today", "estimated_export_energy_total",
)


def estimate_export_power(output_w: float | None, load_w: float) -> float | None:
    if any(value is None or not math.isfinite(value) or value < 0
           for value in (output_w, load_w)):
        return None
    return max(0.0, output_w - load_w)


@dataclass
class ExportEnergy:
    day: date
    started_at: datetime
    today_wh: float = 0.0
    total_wh: float = 0.0
    today_complete: bool = False
    total_complete: bool = True
    today_load_valid: bool = True
    total_load_valid: bool = True
    power_w: float | None = field(default=None, init=False)
    _previous: tuple[datetime, float, bool] | None = field(default=None, init=False)
    _last_update_at: datetime | None = field(default=None, init=False)

    def update(
        self, now: datetime, power_w: float | None, load_valid: bool, max_gap_seconds: float,
    ) -> None:
        now = now.astimezone(UTC)
        day = now.astimezone(LOCAL_TIMEZONE).date()
        self.power_w = None
        if day < self.day or (self._last_update_at is not None and now <= self._last_update_at):
            self._mark_gap()
            return
        self._last_update_at = now
        if day > self.day:
            self.day, self.today_wh = day, 0.0
            self.today_complete = self._previous is not None
            self.today_load_valid = True
        self.today_load_valid &= load_valid
        self.total_load_valid &= load_valid
        if power_w is None or not math.isfinite(power_w) or power_w < 0:
            self._mark_gap()
            return
        self.power_w = power_w
        if self._previous is not None:
            self._integrate(now, power_w, load_valid, max_gap_seconds)
        self._previous = (now, power_w, load_valid)

    def _mark_gap(self) -> None:
        self.today_complete = self.total_complete = False
        self._previous = None

    def _integrate(
        self, now: datetime, power_w: float, load_valid: bool, max_gap_seconds: float,
    ) -> None:
        previous_at, previous_w, previous_load_valid = self._previous
        seconds = (now - previous_at).total_seconds()
        if seconds > max_gap_seconds:
            self._mark_gap()
            return
        self.total_wh += (previous_w + power_w) / 2 * seconds / 3600
        midnight = datetime.combine(self.day, time.min, LOCAL_TIMEZONE).astimezone(UTC)
        start = max(previous_at, midnight)
        fraction = (start - previous_at).total_seconds() / seconds
        start_w = previous_w + (power_w - previous_w) * fraction
        self.today_wh += (start_w + power_w) / 2 * (now - start).total_seconds() / 3600
        self.today_load_valid &= previous_load_valid and load_valid


def read_export_energy(path: Path, now: datetime) -> ExportEnergy:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        day = now.astimezone(LOCAL_TIMEZONE).date()
        midnight = datetime.combine(day, time.min, LOCAL_TIMEZONE)
        return ExportEnergy(day, now, today_complete=now == midnight)
    day = date.fromisoformat(data["day"])
    started_at = datetime.fromisoformat(data["started_at"])
    values = (data["today_wh"], data["total_wh"])
    flags = tuple(data[key] for key in (
        "today_complete", "total_complete", "today_load_valid", "total_load_valid",
    ))
    if any(type(value) not in (int, float) or not math.isfinite(value) or value < 0
           for value in values):
        raise ValueError("Invalid persisted export energy")
    if (any(type(flag) is not bool for flag in flags) or values[0] > values[1]
            or started_at.utcoffset() is None
            or day < started_at.astimezone(LOCAL_TIMEZONE).date()):
        raise ValueError("Invalid persisted export metadata")
    # Preserve counters and quality, but never bridge downtime after a restart.
    return ExportEnergy(day, started_at, *values, False, False, *flags[2:])


def save_export_energy(energy: ExportEnergy, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({
        "day": energy.day.isoformat(), "started_at": energy.started_at.isoformat(),
        "today_wh": energy.today_wh, "total_wh": energy.total_wh,
        "today_complete": energy.today_complete, "total_complete": energy.total_complete,
        "today_load_valid": energy.today_load_valid, "total_load_valid": energy.total_load_valid,
    }, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def update_export_energy(
    battery_state: BatteryState | None, load_estimate: LoadEstimate, state: dict,
    max_age_seconds: float,
) -> ExportEnergy:
    now = datetime.now(UTC)
    energy = state.get("export_energy")
    if energy is None:
        energy = read_export_energy(settings.EXPORT_ENERGY_STATE_PATH, now)
        state["export_energy"] = energy
    output_w = None
    if battery_state is not None:
        age = (now - battery_state.reported_at).total_seconds()
        if 0 <= age <= max_age_seconds:
            output_w = battery_state.output_power_w
    power_w = estimate_export_power(output_w, load_estimate.total_power_w)
    load_valid = all(item.status is PowerContributionStatus.VALID
                     for item in load_estimate.contributions)
    energy.update(now, power_w, load_valid, settings.DAILY_ENERGY_MAX_GAP_SECONDS)
    save_export_energy(energy, settings.EXPORT_ENERGY_STATE_PATH)
    return energy
