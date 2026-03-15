"""Price signal orchestration for cheap price and spike logic."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.config_entries import ConfigEntry

from ..const import PRICING_SOURCE_AMBER
from ..coordinator.data import CoordinatorData
from ..forecast.solar import sum_solar_before_target
from .price_calculator import PriceCalculator
from .spike_analyzer import SpikeAnalyzer
from .utils import (
    analyze_spike_window,
    calculate_spike_price_threshold,
    max_forecast_price,
    parse_forecast_dt,
    percentile,
    scan_forecast_for_spike,
)


class PriceSignalEngine:
    """Compute cheap price thresholds and spike signals."""

    def __init__(
        self,
        entry: ConfigEntry,
        get_switch_state: Any,
        parse_time_option: Any,
    ) -> None:
        self._price_calculator = PriceCalculator(
            entry=entry,
            parse_forecast_dt=parse_forecast_dt,
            percentile_func=percentile,
            sum_solar_before_target=sum_solar_before_target,
            get_expected_load_kw=self.get_expected_load_kw_from_slots,
        )
        self._spike_analyzer = SpikeAnalyzer(
            entry=entry,
            get_switch_state=get_switch_state,
            parse_time_option=parse_time_option,
            analyze_spike_window=analyze_spike_window,
            calculate_spike_price_threshold=calculate_spike_price_threshold,
        )

    def compute_effective_cheap_price_preliminary(
        self,
        data: CoordinatorData,
        now_dt: datetime,
        before_dw: bool,
        target_hour: int,
        target_pct: float,
    ) -> None:
        """Compute preliminary effective cheap price threshold."""
        self._price_calculator.compute_effective_cheap_price_preliminary(
            data=data,
            now_dt=now_dt,
            before_dw=before_dw,
            target_hour=target_hour,
            target_pct=target_pct,
        )

    def compute_effective_cheap_price(
        self, data: CoordinatorData, now_dt: datetime, before_dw: bool, target_hour: int
    ) -> None:
        """Compute final effective cheap price threshold."""
        self._price_calculator.compute_effective_cheap_price(
            data=data,
            now_dt=now_dt,
            before_dw=before_dw,
            target_hour=target_hour,
        )

    def compute_solar_weighted_avg_fit(
        self, data: CoordinatorData, now_dt: datetime, target_hour: int, after_dw: bool
    ) -> None:
        """Compute solar-weighted average feed-in tariff."""
        self._price_calculator.compute_solar_weighted_avg_fit(
            data=data,
            now_dt=now_dt,
            target_hour=target_hour,
            after_dw=after_dw,
        )

    @staticmethod
    def scan_forecast_for_spike(
        forecasts: list[dict[str, Any]],
        now_dt: datetime,
        cutoff: datetime,
        pricing_source: str = PRICING_SOURCE_AMBER,
    ) -> bool:
        """Return True if any forecast indicates spike in window."""
        return scan_forecast_for_spike(forecasts, now_dt, cutoff, pricing_source)

    @staticmethod
    def max_forecast_price(
        forecasts: list[dict[str, Any]],
        now_dt: datetime,
        cutoff: datetime,
    ) -> float:
        """Return maximum per_kwh price from forecasts within window."""
        return max_forecast_price(forecasts, now_dt, cutoff)

    def analyze_spike(self, data: CoordinatorData, now_dt: datetime) -> None:
        """Analyze feed-in forecast for spike window details."""
        self._spike_analyzer.analyze_spike(data, now_dt)

    @staticmethod
    def get_expected_load_kw_from_slots(
        data: CoordinatorData, hours_to_target: float
    ) -> float:
        """Estimate average load kW until DW using data.load_forecast_slots."""
        if not data.load_forecast_slots:
            return data.load_power_kw if data.load_power_kw > 0 else 0.5

        slots_until_dw = max(1, int(hours_to_target * 4))
        relevant = data.load_forecast_slots[:slots_until_dw]
        return sum(relevant) / len(relevant) if relevant else 0.5
