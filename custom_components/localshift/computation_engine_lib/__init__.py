"""Computation engine library modules."""

from .change_tracker import ForecastChangeTracker
from .excess_solar import ExcessSolarEngine
from .excess_solar_signals import ExcessSolarSignalsEngine
from .fit_analyzer import FitAnalyzer
from .forecast_accuracy import ForecastAccuracyEngine
from .forecast_computer import ForecastComputer
from .grid_charge_decision import GridChargeDecisionEngine
from .history_fetcher import HistoryFetcher
from .mode_decision import ModeDecisionEngine
from .price_calculator import PriceCalculator
from .proactive_export import ProactiveExportEngine
from .soc_simulator import SocSimulator
from .solar_utils import (
    get_price_for_slot,
    get_solar_for_5min_slot,
    get_solar_for_15min_slot,
    get_solar_for_slot,
    sum_solar_before_target,
)
from .spike_analyzer import SpikeAnalyzer
from .utils import (
    analyze_spike_window,
    build_hourly_forecast_summary,
    calculate_spike_price_threshold,
    max_forecast_price,
    parse_forecast_dt,
    percentile,
    scan_forecast_for_spike,
)
from .weather_diagnostics import WeatherDiagnosticsEngine

__all__ = [
    "ForecastChangeTracker",
    "ExcessSolarEngine",
    "ExcessSolarSignalsEngine",
    "FitAnalyzer",
    "ForecastAccuracyEngine",
    "ForecastComputer",
    "GridChargeDecisionEngine",
    "HistoryFetcher",
    "ModeDecisionEngine",
    "PriceCalculator",
    "ProactiveExportEngine",
    "SocSimulator",
    "SpikeAnalyzer",
    "WeatherDiagnosticsEngine",
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
