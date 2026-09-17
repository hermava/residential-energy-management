import math
from datetime import datetime

import pytest

from energy_manager.models import PowerMeasurement


def test_power_measurement_stores_values() -> None:
    timestamp = datetime(2026, 9, 17, 12, 0)

    measurement = PowerMeasurement(
        source="office_plug",
        power_w=137.5,
        timestamp=timestamp,
    )

    assert measurement.source == "office_plug"
    assert measurement.power_w == 137.5
    assert measurement.timestamp == timestamp


def test_power_measurement_rejects_empty_source() -> None:
    with pytest.raises(ValueError, match="source must not be empty"):
        PowerMeasurement(
            source="   ",
            power_w=100.0,
            timestamp=datetime(2026, 9, 17, 12, 0),
        )


def test_power_measurement_rejects_nan_power() -> None:
    with pytest.raises(ValueError, match="power_w must be a finite number"):
        PowerMeasurement(
            source="office_plug",
            power_w=math.nan,
            timestamp=datetime(2026, 9, 17, 12, 0),
        )


def test_power_measurement_rejects_infinite_power() -> None:
    with pytest.raises(ValueError, match="power_w must be a finite number"):
        PowerMeasurement(
            source="office_plug",
            power_w=math.inf,
            timestamp=datetime(2026, 9, 17, 12, 0),
        )
