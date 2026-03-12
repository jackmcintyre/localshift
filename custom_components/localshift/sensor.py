"""Sensor platform for LocalShift integration."""

from .sensors import (
    AutomationReadySensor,
    CheapChargeStopPriceSensor,
    DecisionLagSensor,
    DecisionLogSensor,
    DecisionQualitySensor,
    EffectiveCheapPriceSensor,
    EntityHealthSensor,
    ExcessSolarSensor,
    ExtendedForecastAccuracySensor,
    ForecastAccuracySensor,
    ForecastDiagnosticsSensor,
    ForecastHistorySensor,
    ForecastPricesSensor,
    ForecastStatusSensor,
    IntegrationStatusSensor,
    LearningDecisionHistorySensor,
    LearningStatusSensor,
    LoadDeviationSensor,
    LoadShiftSignalSensor,
    MinimumTargetSOCSensor,
    NetElectricityCostSensor,
    OptimizerPlanDetailedSensor,
    OptimizerPlanGridSensor,
    OptimizerPlanSensor,
    OptimizerSummarySensor,
    SolarBatteryForecastSensor,
    SolarForecastAccuracySensor,
    SolarWeightedAvgFITSensor,
)
from .sensors.base import LocalShiftSensorBase

# For backward compatibility - these were previously defined inline
__all__ = [
    "LocalShiftSensorBase",
    "EffectiveCheapPriceSensor",
    "CheapChargeStopPriceSensor",
    "SolarWeightedAvgFITSensor",
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
    "ExcessSolarSensor",
    "LoadShiftSignalSensor",
    "ForecastAccuracySensor",
    "IntegrationStatusSensor",
    "EntityHealthSensor",
    "LearningStatusSensor",
    "DecisionQualitySensor",
    "LearningDecisionHistorySensor",
    "DecisionLagSensor",
    "ExtendedForecastAccuracySensor",
    "ForecastStatusSensor",
    "AutomationReadySensor",
    "OptimizerPlanDetailedSensor",
    "OptimizerSummarySensor",
    "SolarForecastAccuracySensor",
]


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up LocalShift sensor entities."""
    # Import here to avoid circular imports
    from .sensors import (
        AutomationReadySensor,
        CheapChargeStopPriceSensor,
        DecisionLagSensor,
        DecisionLogSensor,
        DecisionQualitySensor,
        EffectiveCheapPriceSensor,
        EntityHealthSensor,
        ExcessSolarSensor,
        ExtendedForecastAccuracySensor,
        ForecastAccuracySensor,
        ForecastDiagnosticsSensor,
        ForecastHistorySensor,
        ForecastPricesSensor,
        ForecastStatusSensor,
        IntegrationStatusSensor,
        LearningDecisionHistorySensor,
        LearningStatusSensor,
        LoadDeviationSensor,
        LoadShiftSignalSensor,
        MinimumTargetSOCSensor,
        NetElectricityCostSensor,
        OptimizerPlanDetailedSensor,
        OptimizerPlanGridSensor,
        OptimizerPlanSensor,
        OptimizerSummarySensor,
        SolarBatteryForecastSensor,
        SolarForecastAccuracySensor,
        SolarWeightedAvgFITSensor,
    )

    coordinator = entry.runtime_data

    entities = [
        EffectiveCheapPriceSensor(coordinator, entry),
        CheapChargeStopPriceSensor(coordinator, entry),
        SolarWeightedAvgFITSensor(coordinator, entry),
        # ActiveModeSensor removed - replaced by select entity (Issue #382)
        SolarBatteryForecastSensor(coordinator, entry),
        NetElectricityCostSensor(coordinator, entry),
        DecisionLogSensor(coordinator, entry),
        ForecastHistorySensor(coordinator, entry),
        # Phase 5 (#447): Renamed/migrated sensors
        OptimizerPlanSensor(coordinator, entry),  # Was DailyForecastSensor
        # Price and grid sensors
        ForecastPricesSensor(coordinator, entry),
        OptimizerPlanGridSensor(coordinator, entry),  # Was ForecastGridSensor
        LoadDeviationSensor(coordinator, entry),
        ForecastDiagnosticsSensor(coordinator, entry),
        MinimumTargetSOCSensor(coordinator, entry),
        # Excess solar load shifting sensors (backlog-high-017)
        ExcessSolarSensor(coordinator, entry),
        LoadShiftSignalSensor(coordinator, entry),
        # Forecast accuracy sensor (Issue #37 Phase 2)
        ForecastAccuracySensor(coordinator, entry),
        # Entity health and error tracking sensors (Issue #94)
        IntegrationStatusSensor(coordinator, entry),
        EntityHealthSensor(coordinator, entry),
        # Learning system sensors (Issue #170 Phase 5)
        LearningStatusSensor(coordinator, entry),
        DecisionQualitySensor(coordinator, entry),
        LearningDecisionHistorySensor(coordinator, entry),
        # Decision-to-implementation lag sensor (Issue #501)
        DecisionLagSensor(coordinator, entry),
        # Extended forecast accuracy sensor (Issue #270)
        ExtendedForecastAccuracySensor(coordinator, entry),
        # Forecast status sensor (Issue #319)
        ForecastStatusSensor(coordinator, entry),
        # Automation ready sensor (Issue #349)
        AutomationReadySensor(coordinator, entry),
        # Optimizer sensors (Phase 5 #447: renamed, comparison deleted)
        OptimizerPlanDetailedSensor(
            coordinator, entry
        ),  # Was OptimizerShadowPlanSensor
        OptimizerSummarySensor(coordinator, entry),  # Was OptimizerShadowSummarySensor
        SolarForecastAccuracySensor(coordinator, entry),
    ]

    async_add_entities(entities)
