import math
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class PowerMeasurement:
    source: str
    power_w: float
    timestamp: datetime

    def __post_init__(self) -> None:
        if not self.source.strip():
            raise ValueError("source must not be empty")

        if not math.isfinite(self.power_w):
            raise ValueError("power_w must be a finite number")
