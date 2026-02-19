"""Computation engine library modules."""

from .forecast_computer import ForecastComputer
from .history_fetcher import HistoryFetcher
from .solar_utils import (
    get_price_for_slot,
    get_solar_for_5min_slot,
    get_solar_for_15min_slot,
    get_solar_for_slot,
    sum_solar_before_target,
)
from .utils import (
    analyze_spike_window,
    build_hourly_forecast_summary,
    calculate_spike_price_threshold,
    max_forecast_price,
    parse_forecast_dt,
    percentile,
    scan_forecast_for_spike,
)

__all__ = [
    "ForecastComputer",
    "HistoryFetcher",
    "analyze_spike_window",
    "build_hourly_forecast_summary",
    "calculate_spike_price_threshold",
    "get_price_for_slot",
    "get_solar_for_15min_slot",
    "get_solar_for_5min_slot",
    "get_solar_for_slot",
    "max_forecast_price",
    "parse_forecast_dt",
    "percentile",
    "scan_forecast_for_spike",
    "sum_solar_before_target",
]
