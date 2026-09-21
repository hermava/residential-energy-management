from enum import Enum

from energy_manager.models import PowerMeasurement, PowerSensorConfig
from energy_manager.validation import (
    is_measurement_fresh,
    is_power_plausible,
)


class MeasurementQuality(Enum):
    VALID = "valid"
    STALE = "stale"
    IMPLAUSIBLE = "implausible"


def evaluate_measurement_quality(
    measurement: PowerMeasurement,
    config: PowerSensorConfig,
) -> MeasurementQuality:
    if not is_measurement_fresh(
        measurement,
        config.max_age_seconds,
    ):
        return MeasurementQuality.STALE

    if not is_power_plausible(
        measurement,
        config.min_power_w,
        config.max_power_w,
    ):
        return MeasurementQuality.IMPLAUSIBLE

    return MeasurementQuality.VALID