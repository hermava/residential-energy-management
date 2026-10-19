"""Provider-independent forecast calculations and supporting statistics."""

import math
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from statistics import fmean, pstdev

from energy_manager.models import EnergyForecastConfig, ForecastCorrectionConfig


@dataclass(frozen=True)
class DailyEnergyForecast:
    assessment: str
    data_quality: str
    reason: str
    horizon_hours: float | None = None
    original_pv_wh: float | None = None
    effective_pv_wh: float | None = None
    correction_factor: float | None = None
    consumption_wh: float | None = None
    original_balance_soc_percent: float | None = None
    balance_soc_percent: float | None = None
    balance_energy_wh: float | None = None
    surplus_energy_wh: float | None = None
    reserve_change_wh: float | None = None
    model: str = "daily_balance_v1"


def forecast_daily_energy(
    soc_percent: float | None, remaining_pv_wh: float | None, horizon_hours: float | None,
    capacity_wh: float, config: EnergyForecastConfig, correction_factor: float | None = None,
    input_mean_w: float | None = None,
) -> DailyEnergyForecast:
    """Estimate an aggregate balance through today's sunset, not an SOC trajectory."""
    if not math.isfinite(capacity_wh) or capacity_wh <= 0:
        raise ValueError("capacity_wh must be finite and positive")
    values = (soc_percent, remaining_pv_wh, horizon_hours)
    if any(value is None or not math.isfinite(value) for value in values):
        return DailyEnergyForecast("unavailable", "unavailable", "missing_or_invalid_data")
    if not 0 <= soc_percent <= 100 or remaining_pv_wh < 0 or horizon_hours < 0:
        return DailyEnergyForecast("unavailable", "unavailable", "invalid_data")
    if horizon_hours == 0:
        return DailyEnergyForecast("unavailable", "unavailable", "daylight_period_ended")
    if correction_factor is not None:
        if not math.isfinite(correction_factor) or correction_factor < 0:
            return DailyEnergyForecast("unavailable", "unavailable", "invalid_correction")
    effective_pv = remaining_pv_wh * (correction_factor if correction_factor is not None else 1)
    consumption = config.consumption_w * horizon_hours / config.output_efficiency
    stored = capacity_wh * soc_percent / 100
    original_balance = stored + remaining_pv_wh * config.input_efficiency - consumption
    balance = stored + effective_pv * config.input_efficiency - consumption
    assessment, quality, reason = _assess_daily_reserve(
        soc_percent, balance, stored, capacity_wh, input_mean_w, config,
    )
    return DailyEnergyForecast(
        assessment=assessment,
        data_quality=quality,
        reason=reason,
        horizon_hours=horizon_hours,
        original_pv_wh=remaining_pv_wh,
        effective_pv_wh=effective_pv,
        correction_factor=correction_factor,
        consumption_wh=consumption,
        original_balance_soc_percent=min(100.0, max(0.0, original_balance / capacity_wh * 100)),
        balance_soc_percent=min(100.0, max(0.0, balance / capacity_wh * 100)),
        balance_energy_wh=balance,
        surplus_energy_wh=max(0.0, balance - capacity_wh),
        reserve_change_wh=effective_pv * config.input_efficiency - consumption,
    )


def _assess_daily_reserve(
    soc: float, balance: float, stored: float, capacity: float, input_mean_w: float | None,
    config: EnergyForecastConfig,
) -> tuple[str, str, str]:
    if soc > config.surplus_min_soc_percent:
        if balance > capacity * config.surplus_target_soc_percent / 100:
            return "surplus_expected", "heuristic", "thresholds_met"
    if soc < config.reserve_soc_percent and balance < stored:
        if input_mean_w is None or not math.isfinite(input_mean_w) or input_mean_w < 0:
            return "unavailable", "unavailable", "missing_input_history"
        if input_mean_w < config.consumption_w:
            return "reserve_at_risk", "heuristic", "low_soc_and_declining_reserve"
    return "limited", "heuristic", "no_surplus_or_reserve_risk"


@dataclass(frozen=True)
class InputPowerStatistics:
    sample_count: int
    mean_w: float | None
    standard_deviation_w: float | None
    min_w: float | None
    max_w: float | None
    span_seconds: float
    latest_age_seconds: float | None
    has_minimum_samples: bool


class InputPowerHistory:
    def __init__(self, window_seconds: float, min_samples: int, max_samples: int):
        if not math.isfinite(window_seconds) or window_seconds <= 0:
            raise ValueError("window_seconds must be finite and positive")
        if not 1 <= min_samples <= max_samples:
            raise ValueError("sample limits must satisfy 1 <= min_samples <= max_samples")
        self.window_seconds = window_seconds
        self.min_samples = min_samples
        self._samples: deque[tuple[float, float]] = deque(maxlen=max_samples)
        self._last_update: float | None = None

    def update(self, now: float, power_w: float | None) -> InputPowerStatistics:
        """Expire old samples even when input is missing; include the window boundary."""
        if not math.isfinite(now):
            raise ValueError("now must be finite")
        if self._last_update is not None and now < self._last_update:
            raise ValueError("now must not move backwards")
        while self._samples and self._samples[0][0] < now - self.window_seconds:
            self._samples.popleft()
        if power_w is not None and math.isfinite(power_w) and power_w >= 0:
            if self._last_update != now:
                self._samples.append((now, power_w))
        self._last_update = now
        return self._statistics(now)

    def _statistics(self, now: float) -> InputPowerStatistics:
        """Population deviation of equally weighted samples, not a time integral."""
        values = [power for _, power in self._samples]
        return InputPowerStatistics(
            sample_count=len(values),
            mean_w=fmean(values) if values else None,
            standard_deviation_w=pstdev(values) if values else None,
            min_w=min(values) if values else None,
            max_w=max(values) if values else None,
            span_seconds=self._samples[-1][0] - self._samples[0][0] if values else 0.0,
            latest_age_seconds=now - self._samples[-1][0] if values else None,
            has_minimum_samples=len(values) >= self.min_samples,
        )


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