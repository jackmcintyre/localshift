"""Spike analysis helpers for computation engine."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, time

from homeassistant.config_entries import ConfigEntry

from ..const import (
    BATTERY_CAPACITY_KWH,
    CONF_DEMAND_WINDOW_END,
    CONF_DEMAND_WINDOW_START,
    DEFAULT_DEMAND_WINDOW_END,
    DEFAULT_DEMAND_WINDOW_START,
    DEFAULT_FORECAST_LOOKAHEAD_HOURS,
    DEFAULT_SPIKE_PRICE_PERCENTILE,
    SWITCH_SPIKE_DISCHARGE_CONSERVATIVE,
)
from ..coordinator_data import CoordinatorData

_LOGGER = logging.getLogger(__name__)


class SpikeAnalyzer:
    """Compute conservative spike-discharge analysis fields."""

    def __init__(
        self,
        entry: ConfigEntry,
        get_switch_state: Callable[[str], bool],
        parse_time_option: Callable[[str, str], time],
        analyze_spike_window: Callable[[list[dict], datetime, float], tuple],
        calculate_spike_price_threshold: Callable[[list[float], float], float],
    ) -> None:
        """Initialize analyzer dependencies."""
        self.entry = entry
        self._get_switch_state = get_switch_state
        self._parse_time_option = parse_time_option
        self._analyze_spike_window = analyze_spike_window
        self._calculate_spike_price_threshold = calculate_spike_price_threshold

    def analyze_spike(
        self,
        data: CoordinatorData,
        now_dt: datetime,
    ) -> None:
        """Analyze feed-in forecast for spike-window details."""
        conservative_enabled = self._get_switch_state(
            SWITCH_SPIKE_DISCHARGE_CONSERVATIVE
        )
        # Hardcoded defaults (Issue #214)
        spike_percentile = DEFAULT_SPIKE_PRICE_PERCENTILE

        data.spike_end_time = None
        data.spike_max_price = 0.0
        data.spike_price_threshold = 0.0
        data.spike_reserve_soc = 0.0
        data.spike_hours_remaining = 0.0
        data.spike_in_conservative_mode = False

        if not conservative_enabled:
            return

        # Hardcoded default (Issue #214)
        lookahead = DEFAULT_FORECAST_LOOKAHEAD_HOURS

        spike_end, max_price, spike_prices = self._analyze_spike_window(
            data.feed_in_forecast, now_dt, lookahead
        )

        if spike_end is None or not spike_prices:
            return

        data.spike_end_time = spike_end
        data.spike_max_price = max_price
        data.spike_hours_remaining = (spike_end - now_dt).total_seconds() / 3600
        data.spike_price_threshold = self._calculate_spike_price_threshold(
            spike_prices, spike_percentile
        )
        data.spike_reserve_soc = self.calculate_spike_reserve_soc(
            data, now_dt, spike_end, spike_percentile
        )
        data.spike_in_conservative_mode = True

        _LOGGER.info(
            "Spike analysis: max_price=%.2f, threshold=%.2f, reserve=%.1f%%, hours_remaining=%.1f",
            data.spike_max_price,
            data.spike_price_threshold,
            data.spike_reserve_soc,
            data.spike_hours_remaining,
        )

    def calculate_spike_reserve_soc(
        self,
        data: CoordinatorData,
        now_dt: datetime,
        spike_end: datetime,
        _spike_percentile: float,
    ) -> float:
        """Calculate reserve SOC needed to survive spike period."""
        demand_window_start = self._parse_time_option(
            CONF_DEMAND_WINDOW_START, DEFAULT_DEMAND_WINDOW_START
        )
        demand_window_end = self._parse_time_option(
            CONF_DEMAND_WINDOW_END, DEFAULT_DEMAND_WINDOW_END
        )

        spike_duration_hours = max((spike_end - now_dt).total_seconds() / 3600, 0)

        demand_window_duration_hours = (
            datetime.combine(now_dt.date(), demand_window_end)
            - datetime.combine(now_dt.date(), demand_window_start)
        ).total_seconds() / 3600
        if demand_window_duration_hours < 0:
            demand_window_duration_hours += 24

        demand_window_start_dt = now_dt.replace(
            hour=demand_window_start.hour,
            minute=demand_window_start.minute,
            second=0,
            microsecond=0,
        )
        demand_window_overlaps = (
            demand_window_start_dt > now_dt and demand_window_start_dt < spike_end
        )

        if demand_window_overlaps:
            required_hours = max(spike_duration_hours, demand_window_duration_hours)
            _LOGGER.debug(
                "DW overlaps spike: spike=%.1fh, dw=%.1fh, using=%.1fh",
                spike_duration_hours,
                demand_window_duration_hours,
                required_hours,
            )
        else:
            required_hours = spike_duration_hours

        expected_load_kw = data.load_power_kw if data.load_power_kw > 0 else 0.5
        reserve_kwh = expected_load_kw * required_hours
        reserve_soc = (reserve_kwh / BATTERY_CAPACITY_KWH) * 100
        return min(reserve_soc, 100.0)
