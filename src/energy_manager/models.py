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
class ConstantConsumerConfig:
    name: str
    estimated_power_w: float

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("name must not be empty")

        if self.estimated_power_w < 0:
            raise ValueError("estimated_power_w must not be negative")

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


class PowerContributionType(Enum):
    MEASURED = "measured"
    STATE_ESTIMATED = "state_estimated"
    CONSTANT_ESTIMATED = "constant_estimated"

class PowerContributionStatus(Enum):
    VALID = "valid"
    STALE = "stale"
    IMPLAUSIBLE = "implausible"
    UNAVAILABLE = "unavailable"

@dataclass(frozen=True)
class PowerContribution:
    name: str
    power_w: float
    contribution_type: PowerContributionType
    source: str | None = None
    status: PowerContributionStatus = PowerContributionStatus.VALID

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("name must not be empty")

        if self.power_w < 0:
            raise ValueError("power_w must not be negative")

@dataclass(frozen=True)
class OutputConfig:
    entity_id: str

    def __post_init__(self) -> None:
        if not self.entity_id:
            raise ValueError("entity_id must not be empty")

@dataclass(frozen=True)
class BatteryConfig:
    capacity_wh: float
    min_soc_percent: float
    max_output_power_w: float
    max_age_seconds: float

    def __post_init__(self) -> None:
        if self.capacity_wh <= 0:
            raise ValueError("capacity_wh must be greater than zero")

        if not 0.0 <= self.min_soc_percent <= 100.0:
            raise ValueError("min_soc_percent must be between 0 and 100")

        if self.max_output_power_w <= 0:
            raise ValueError("max_output_power_w must be greater than zero")

@dataclass(frozen=True)
class LoadConfig:
    power_sensors: dict[str, PowerSensorConfig]
    estimated_consumers: dict[str, EstimatedConsumerConfig]
    constant_consumers: dict[str, ConstantConsumerConfig]
    outputs: dict[str, OutputConfig]
    battery: BatteryConfig

@dataclass(frozen=True)
class LoadEstimate:
    total_power_w: float
    contributions: tuple[PowerContribution, ...]

@dataclass(frozen=True)
class BatteryState:
    soc_percent: float
    input_power_w: float
    output_power_w: float
    reported_at: datetime
    received_at: datetime

    def __post_init__(self) -> None:
        if not 0.0 <= self.soc_percent <= 100.0:
            raise ValueError("soc_percent must be between 0 and 100")

        if self.input_power_w < 0:
            raise ValueError("input_power_w must not be negative")

        if self.output_power_w < 0:
            raise ValueError("output_power_w must not be negative")

@dataclass(frozen=True)
class EnergyForecast:
    horizon_hours: float
    expected_input_energy_wh: float

    def __post_init__(self) -> None:
        if self.horizon_hours <= 0:
            raise ValueError("horizon_hours must be greater than zero")

        if self.expected_input_energy_wh < 0:
            raise ValueError(
                "expected_input_energy_wh must not be negative"
            )



