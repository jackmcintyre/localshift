"""Sensor modules for LocalShift integration."""

from .base import LocalShiftSensorBase
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
    EffectiveCheapPriceSensor,
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
    # Pricing
    "EffectiveCheapPriceSensor",
    "CheapChargeStopPriceSensor",
    "SolarWeightedAvgFITSensor",
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
