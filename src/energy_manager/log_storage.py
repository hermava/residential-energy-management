"""Bound managed CSV collections without touching state files or open UTC days."""

import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

RESCAN_SECONDS = 300


@dataclass
class _Budget:
    day: date
    scanned_at: float
    sizes: dict[Path, int] = field(default_factory=dict)
    dates: dict[Path, date] = field(default_factory=dict)
    paused: bool = False


_budgets: dict[tuple, _Budget] = {}


def _scan(bases: tuple[Path, ...], today: date, now: float) -> _Budget:
    budget = _Budget(today, now)
    for base in bases:
        for path in base.parent.glob(f"{base.stem}_????-??-??.csv"):
            if path.is_symlink() or not path.is_file():
                continue
            try:
                day = date.fromisoformat(path.stem.removeprefix(f"{base.stem}_"))
            except ValueError:
                continue
            budget.sizes[path] = path.stat().st_size
            budget.dates[path] = day
    return budget


def append_with_budget(
    path: Path, content: bytes, bases: tuple[Path, ...], today: date,
    maximum_bytes: int,
) -> bool:
    """Append if it fits; discard oldest closed files first, pause when today's files fill up."""
    if maximum_bytes < 1:
        raise ValueError("CSV storage budget must be positive")
    if path.is_symlink():
        raise OSError("CSV destination must not be a symlink")
    key = (tuple(bases), maximum_bytes)
    now = time.monotonic()
    budget = _budgets.get(key)
    if budget is None or budget.day != today or now - budget.scanned_at >= RESCAN_SECONDS:
        budget = _budgets[key] = _scan(bases, today, now)
    elif path in budget.sizes and not path.exists():
        budget = _budgets[key] = _scan(bases, today, now)
    used = sum(budget.sizes.values())
    old = (sorted((item for item in budget.sizes if budget.dates[item] < today),
                  key=lambda item: (budget.dates[item], str(item)))
           if used + len(content) > maximum_bytes and len(content) <= maximum_bytes else [])
    for candidate in old:
        if used + len(content) <= maximum_bytes:
            break
        candidate.unlink(missing_ok=True)
        used -= budget.sizes.pop(candidate)
        budget.dates.pop(candidate)
    if used + len(content) > maximum_bytes:
        if not budget.paused:
            print(f"CSV budget reached in {path.parent}; recording paused until space is free.")
        budget.paused = True
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("ab") as output:
            output.write(content)
    except OSError:
        _budgets.pop(key, None)
        raise
    budget.sizes[path] = budget.sizes.get(path, 0) + len(content)
    budget.dates[path] = today
    budget.paused = False
    return True
