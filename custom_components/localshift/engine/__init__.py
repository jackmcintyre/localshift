"""Computation engine library modules."""

from ..forecast.accuracy import ForecastAccuracyEngine
from ..forecast.history import HistoryFetcher
from ..forecast.history_store import ForecastHistoryStore
from ..forecast.pipeline import ForecastPipeline
from ..forecast.solar import (
    get_solar_for_5min_slot,
    get_solar_for_15min_slot,
    sum_solar_before_target,
)
from .excess_solar import ExcessSolarEngine
from .excess_solar_signals import ExcessSolarSignalsEngine
from .optimization_controller import (
    ContextualAdjustment,
    ObjectiveWeights,
    OptimizationController,
)
from .optimizer_facade import OptimizerFacade
from .outcomes import (
    DecisionOutcomeTracker,
    DecisionRecord,
    PerformanceMetrics,
)
from .parameters import ParameterOptimizer
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
from .price_signal_engine import PriceSignalEngine
from .slots import SlotBuilder, SlotBuildMetadata
from .soc_simulator import SocSimulator
from .spike_analyzer import SpikeAnalyzer
from .utils import (
    analyze_spike_window,
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
    "ForecastAccuracyEngine",
    "ForecastHistoryStore",
    "ForecastPipeline",
    "HistoryFetcher",
    "PerformanceMetrics",
    "PriceCalculator",
    "PriceSignalEngine",
    "OptimizerFacade",
    "SlotBuilder",
    "SlotBuildMetadata",
    "SocSimulator",
    "SpikeAnalyzer",
    "WeatherDiagnosticsEngine",
    "analyze_spike_window",
    "calculate_spike_price_threshold",
    "get_price_for_slot",
    "get_price_for_slot_or_none",
    "get_solar_for_15min_slot",
    "get_solar_for_5min_slot",
    "max_forecast_price",
    "parse_forecast_dt",
    "percentile",
    "scan_forecast_for_spike",
    "sum_solar_before_target",
]
