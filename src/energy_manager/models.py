import math
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class PowerMeasurement:
    source: str
    power_w: float
    reported_at: datetime     # = time when e.g. Home assistant got the measurement from the device
    received_at: datetime     # = time when the measurement was received by the energy manager

    def __post_init__(self) -> None:
        if not self.source.strip():
            raise ValueError("source must not be empty")

        if not math.isfinite(self.power_w):
            raise ValueError("power_w must be a finite number")
