import math
from dataclasses import dataclass, field
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
    control_inhibit_soc_percent: float

    def __post_init__(self) -> None:
        if self.capacity_wh <= 0:
            raise ValueError("capacity_wh must be greater than zero")
        if not 0.0 <= self.min_soc_percent <= 100.0:
            raise ValueError("min_soc_percent must be between 0 and 100")
        if self.max_output_power_w <= 0:
            raise ValueError("max_output_power_w must be greater than zero")
        if not 0.0 <= self.control_inhibit_soc_percent <= 100.0:
            raise ValueError("control_inhibit_soc_percent must be between 0 and 100")

@dataclass(frozen=True)
class ForecastSensorConfig:
    entity_id: str
    unit: str | None = None

    def __post_init__(self) -> None:
        if not self.entity_id.strip():
            raise ValueError("entity_id must not be empty")


@dataclass(frozen=True)
class ForecastSourceConfig:
    provider: str
    values: dict[str, ForecastSensorConfig]

    def __post_init__(self) -> None:
        if not self.provider.strip():
            raise ValueError("provider must not be empty")

@dataclass(frozen=True)
class ForecastCorrectionConfig:
    method: str = "ratio_of_means_v1"
    min_forecast_power_w: float = 50.0
    min_factor: float = 0.25
    max_factor: float = 4.0
    max_age_seconds: float = 1800.0

    def __post_init__(self) -> None:
        values = (
            self.min_forecast_power_w, self.min_factor, self.max_factor, self.max_age_seconds,
        )
        if not all(math.isfinite(value) for value in values):
            raise ValueError("forecast correction parameters must be finite")
        if self.min_forecast_power_w <= 0 or self.max_age_seconds <= 0:
            raise ValueError("forecast correction power and age limits must be positive")
        if not 0 <= self.min_factor <= self.max_factor:
            raise ValueError("forecast correction requires 0 <= min_factor <= max_factor")


@dataclass(frozen=True)
class EnergyForecastConfig:
    consumption_w: float = 130.0
    surplus_min_soc_percent: float = 30.0
    surplus_target_soc_percent: float = 95.0
    input_efficiency: float = 1.0
    output_efficiency: float = 1.0
    max_age_seconds: float = 3600.0
    reserve_soc_percent: float = 30.0
    input_history_max_age_seconds: float = 120.0

    def __post_init__(self) -> None:
        if not math.isfinite(self.consumption_w) or self.consumption_w < 0:
            raise ValueError("forecast consumption_w must be finite and nonnegative")
        if not 0 <= self.surplus_min_soc_percent < self.surplus_target_soc_percent <= 100:
            raise ValueError("forecast SOC thresholds must satisfy 0 <= minimum < target <= 100")
        if not 0 < self.input_efficiency <= 1 or not 0 < self.output_efficiency <= 1:
            raise ValueError("forecast efficiencies must be between 0 (exclusive) and 1")
        if not math.isfinite(self.max_age_seconds) or self.max_age_seconds <= 0:
            raise ValueError("forecast max_age_seconds must be finite and positive")
        if not 0 <= self.reserve_soc_percent <= self.surplus_min_soc_percent:
            raise ValueError("reserve SOC must be between 0 and surplus minimum SOC")
        if not math.isfinite(self.input_history_max_age_seconds):
            raise ValueError("input history max age must be finite")
        if self.input_history_max_age_seconds <= 0:
            raise ValueError("input history max age must be positive")


@dataclass(frozen=True)
class LoadConfig:
    power_sensors: dict[str, PowerSensorConfig]
    estimated_consumers: dict[str, EstimatedConsumerConfig]
    constant_consumers: dict[str, ConstantConsumerConfig]
    outputs: dict[str, OutputConfig]
    battery: BatteryConfig
    forecast_sources: dict[str, ForecastSourceConfig] = field(default_factory=dict)
    forecast_correction: ForecastCorrectionConfig = field(default_factory=ForecastCorrectionConfig)
    energy_forecast: EnergyForecastConfig = field(default_factory=EnergyForecastConfig)

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



