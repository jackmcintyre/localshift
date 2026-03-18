"""Sensor modules for LocalShift integration."""

from .base import LocalShiftSensorBase
from .cloud_event import CloudEventSensor
from .forecast import (
    DecisionLogSensor,
    ForecastDiagnosticsSensor,
    ForecastHistorySensor,
    ForecastPricesSensor,
    MinimumTargetSOCSensor,
    NetElectricityCostSensor,
    OptimizerPlanGridSensor,
    OptimizerPlanSensor,
    SolarBatteryForecastSensor,
)
from .learning import (
    DecisionQualitySensor,
    LearningDecisionHistorySensor,
    LearningStatusSensor,
    OptimizerAdvantageSensor,
)
from .load_deviation import LoadDeviationSensor
from .misc import (
    ExcessSolarSensor,
    LoadShiftSignalSensor,
)
from .optimizer import (
    OptimizerPlanDetailedSensor,
    OptimizerSummarySensor,
    SolarForecastAccuracySensor,
)
from .pricing import (
    CheapChargeStopPriceSensor,
    ComparisonResultSensor,
    EffectiveCheapPriceSensor,
    PriceDeltaSensor,
    SolarWeightedAvgFITSensor,
)
from .status import (
    AutomationReadySensor,
    DecisionLagSensor,
    EntityHealthSensor,
    ExtendedForecastAccuracySensor,
    ForecastAccuracySensor,
    ForecastStatusSensor,
    IntegrationStatusSensor,
)

__all__ = [
    # Base
    "LocalShiftSensorBase",
    "CloudEventSensor",
    # Pricing
    "EffectiveCheapPriceSensor",
    "CheapChargeStopPriceSensor",
    "SolarWeightedAvgFITSensor",
    "ComparisonResultSensor",
    "PriceDeltaSensor",
    # Forecast
    "SolarBatteryForecastSensor",
    "NetElectricityCostSensor",
    "DecisionLogSensor",
    "ForecastHistorySensor",
    "OptimizerPlanSensor",
    "ForecastPricesSensor",
    "OptimizerPlanGridSensor",
    "LoadDeviationSensor",
    "ForecastDiagnosticsSensor",
    "MinimumTargetSOCSensor",
    # Optimizer
    "OptimizerPlanDetailedSensor",
    "OptimizerSummarySensor",
    "SolarForecastAccuracySensor",
    # Learning
    "LearningStatusSensor",
    "DecisionQualitySensor",
    "LearningDecisionHistorySensor",
    "OptimizerAdvantageSensor",
    # Status
    "IntegrationStatusSensor",
    "EntityHealthSensor",
    "ForecastAccuracySensor",
    "ForecastStatusSensor",
    "AutomationReadySensor",
    "ExtendedForecastAccuracySensor",
    "DecisionLagSensor",
    # Misc
    "ExcessSolarSensor",
    "LoadShiftSignalSensor",
]
