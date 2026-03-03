"""Computation engine library modules."""

from .decision_outcome_tracker import (
    DecisionOutcomeTracker,
    DecisionRecord,
    PerformanceMetrics,
)
from .excess_solar import ExcessSolarEngine
from .excess_solar_signals import ExcessSolarSignalsEngine
from .fit_analyzer import FitAnalyzer
from .forecast_accuracy import ForecastAccuracyEngine
from .history_fetcher import HistoryFetcher
from .optimization_controller import (
    ContextualAdjustment,
    ObjectiveWeights,
    OptimizationController,
)
from .parameter_optimizer import ParameterOptimizer
from .pattern_analyzer import (
    BiasCorrection,
    PatternAnalyzer,
    PatternBucket,
    PatternReport,
)
from .price_calculator import (
    PriceCalculator,
    get_price_for_slot,
    get_price_for_slot_or_none,
)
from .slot_builder import SlotBuilder, SlotBuildMetadata
from .soc_simulator import SocSimulator
from .solar_utils import (
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
    "BiasCorrection",
    "ContextualAdjustment",
    "DecisionOutcomeTracker",
    "DecisionRecord",
    "ObjectiveWeights",
    "OptimizationController",
    "ParameterOptimizer",
    "PatternAnalyzer",
    "PatternBucket",
    "PatternReport",
    "ExcessSolarEngine",
    "ExcessSolarSignalsEngine",
    "FitAnalyzer",
    "ForecastAccuracyEngine",
    "HistoryFetcher",
    "PerformanceMetrics",
    "PriceCalculator",
    "SlotBuilder",
    "SlotBuildMetadata",
    "SocSimulator",
    "SpikeAnalyzer",
    "WeatherDiagnosticsEngine",
    "analyze_spike_window",
    "build_hourly_forecast_summary",
    "calculate_spike_price_threshold",
    "get_price_for_slot",
    "get_price_for_slot_or_none",
    "get_solar_for_15min_slot",
    "get_solar_for_5min_slot",
    "get_solar_for_slot",
    "max_forecast_price",
    "parse_forecast_dt",
    "percentile",
    "scan_forecast_for_spike",
    "sum_solar_before_target",
]
