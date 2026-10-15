"""Versioned correction strategies: paired history in, correction result out."""

from collections.abc import Callable
from dataclasses import dataclass

from energy_manager.forecast.energy_forecast import InputPowerStatistics
from energy_manager.models import ForecastCorrectionConfig


@dataclass(frozen=True)
class CorrectionResult:
    method: str
    status: str
    raw_factor: float | None = None
    factor: float | None = None


def ratio_of_means_v1(
    actual: InputPowerStatistics,
    predicted: InputPowerStatistics,
    config: ForecastCorrectionConfig,
) -> CorrectionResult:
    if not actual.has_minimum_samples or not predicted.has_minimum_samples:
        return CorrectionResult(config.method, "insufficient_samples")
    ages = (actual.latest_age_seconds, predicted.latest_age_seconds)
    if any(age is None or age > config.max_age_seconds for age in ages):
        return CorrectionResult(config.method, "stale_history")
    if actual.mean_w is None or predicted.mean_w is None:
        return CorrectionResult(config.method, "missing_data")
    if predicted.mean_w < config.min_forecast_power_w:
        return CorrectionResult(config.method, "low_forecast_power")
    raw_factor = actual.mean_w / predicted.mean_w
    factor = min(config.max_factor, max(config.min_factor, raw_factor))
    status = "clamped" if factor != raw_factor else "available"
    return CorrectionResult(config.method, status, raw_factor, factor)


CorrectionMethod = Callable[
    [InputPowerStatistics, InputPowerStatistics, ForecastCorrectionConfig], CorrectionResult
]
CORRECTION_METHODS: dict[str, CorrectionMethod] = {"ratio_of_means_v1": ratio_of_means_v1}


def calculate_correction(
    actual: InputPowerStatistics,
    predicted: InputPowerStatistics,
    config: ForecastCorrectionConfig,
) -> CorrectionResult:
    return CORRECTION_METHODS[config.method](actual, predicted, config)
