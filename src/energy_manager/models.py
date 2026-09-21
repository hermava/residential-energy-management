import math
from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class PowerChannelType(Enum):
    CONSUMER = "consumer"
    BATTERY_INPUT = "battery_input"
    BATTERY_OUTPUT = "battery_output"
    INVERTER_OUTPUT = "inverter_output"

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

@dataclass(frozen=True)
class PowerSensorConfig:
    name: str
    entity_id: str
    channel_type: PowerChannelType
    min_power_w: float
    max_power_w: float
    max_age_seconds: float

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("name must not be empty")

        if not self.entity_id.strip():
            raise ValueError("entity_id must not be empty")

        if self.min_power_w > self.max_power_w:
            raise ValueError("min_power_w must not be greater than max_power_w")

        if self.max_age_seconds <= 0:
            raise ValueError("max_age_seconds must be greater than zero")

@dataclass(frozen=True)
class EstimatedConsumerConfig:
    name: str
    entity_id: str
    estimated_power_w: float
    max_age_seconds: float

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("name must not be empty")

        if not self.entity_id.strip():
            raise ValueError("entity_id must not be empty")

        if self.estimated_power_w < 0:
            raise ValueError("estimated_power_w must not be negative")

        if self.max_age_seconds <= 0:
            raise ValueError("max_age_seconds must be greater than zero")

@dataclass(frozen=True)
class EntityState:
    source: str
    state: str
    reported_at: datetime
    received_at: datetime

    def __post_init__(self) -> None:
        if not self.source.strip():
            raise ValueError("source must not be empty")

        if not self.state.strip():
            raise ValueError("state must not be empty")

