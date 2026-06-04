"""Entity configuration dictionaries for LocalShift validation.

Contains configuration for external entities (Tesla, pricing, etc.) and
internal LocalShift entities, including validation rules and staleness thresholds.
"""

from __future__ import annotations

from datetime import timedelta
from enum import Enum
from typing import Any

from ..const import (
    CONF_PRICING_FEED_IN_FORECAST,
    CONF_PRICING_FEED_IN_PRICE,
    CONF_PRICING_GENERAL_FORECAST,
    CONF_PRICING_GENERAL_PRICE,
    CONF_PRICING_PRICE_SPIKE,
    CONF_SOLCAST_FORECAST_TODAY,
    CONF_SOLCAST_FORECAST_TOMORROW,
    CONF_TESLEMETRY_ALLOW_EXPORT,
    CONF_TESLEMETRY_BACKUP_RESERVE,
    CONF_TESLEMETRY_BATTERY_POWER,
    CONF_TESLEMETRY_GRID_POWER,
    CONF_TESLEMETRY_LOAD_POWER,
    CONF_TESLEMETRY_OPERATION_MODE,
    CONF_TESLEMETRY_SOC,
    CONF_TESLEMETRY_SOLAR_POWER,
    CONF_WEATHER_ENTITY,
)


class EntityCategory(Enum):
    """Category of entity for error handling priority."""

    REQUIRED = "required"
    RECOMMENDED = "recommended"
    OPTIONAL = "optional"


ENTITY_CONFIG: dict[str, dict[str, Any]] = {
    CONF_TESLEMETRY_OPERATION_MODE: {
        "category": EntityCategory.REQUIRED,
        "expected_type": str,
        "valid_values": ["self_consumption", "backup", "autonomous"],
        "description": "Battery operation mode",
    },
    CONF_TESLEMETRY_BACKUP_RESERVE: {
        "category": EntityCategory.REQUIRED,
        "expected_type": (int, float),
        "min_value": 0,
        "max_value": 100,
        "description": "Backup reserve percentage",
    },
    CONF_TESLEMETRY_SOC: {
        "category": EntityCategory.REQUIRED,
        "expected_type": (int, float),
        "min_value": 0,
        "max_value": 100,
        "description": "Battery state of charge",
    },
    CONF_TESLEMETRY_GRID_POWER: {
        "category": EntityCategory.RECOMMENDED,
        "expected_type": (int, float),
        "description": "Grid power flow (kW)",
    },
    CONF_TESLEMETRY_BATTERY_POWER: {
        "category": EntityCategory.RECOMMENDED,
        "expected_type": (int, float),
        "description": "Battery power flow (kW)",
    },
    CONF_TESLEMETRY_SOLAR_POWER: {
        "category": EntityCategory.RECOMMENDED,
        "expected_type": (int, float),
        "min_value": 0,
        "description": "Solar power generation (kW)",
    },
    CONF_TESLEMETRY_LOAD_POWER: {
        "category": EntityCategory.RECOMMENDED,
        "expected_type": (int, float),
        "min_value": 0,
        "description": "Home load power (kW)",
    },
    CONF_TESLEMETRY_ALLOW_EXPORT: {
        "category": EntityCategory.REQUIRED,
        "expected_type": str,
        "valid_values": ["pv_only", "battery_ok"],
        "description": "Export mode",
    },
    CONF_PRICING_GENERAL_PRICE: {
        "category": EntityCategory.REQUIRED,
        "expected_type": (int, float),
        "description": "Current buy price ($/kWh)",
    },
    CONF_PRICING_FEED_IN_PRICE: {
        "category": EntityCategory.REQUIRED,
        "expected_type": (int, float),
        "description": "Current sell price ($/kWh)",
    },
    CONF_PRICING_GENERAL_FORECAST: {
        "category": EntityCategory.RECOMMENDED,
        "expected_type": list,
        "description": "Buy price forecast",
    },
    CONF_PRICING_FEED_IN_FORECAST: {
        "category": EntityCategory.RECOMMENDED,
        "expected_type": list,
        "description": "Sell price forecast",
    },
    CONF_PRICING_PRICE_SPIKE: {
        "category": EntityCategory.OPTIONAL,
        "expected_type": bool,
        "description": "Price spike indicator",
    },
    CONF_SOLCAST_FORECAST_TODAY: {
        "category": EntityCategory.RECOMMENDED,
        "expected_type": list,
        "description": "Today's solar forecast",
    },
    CONF_SOLCAST_FORECAST_TOMORROW: {
        "category": EntityCategory.OPTIONAL,
        "expected_type": list,
        "description": "Tomorrow's solar forecast",
    },
    CONF_WEATHER_ENTITY: {
        "category": EntityCategory.OPTIONAL,
        "description": "Weather entity for load prediction",
    },
}

STALENESS_THRESHOLDS: dict[str, timedelta] = {
    CONF_TESLEMETRY_SOC: timedelta(minutes=30),
    CONF_TESLEMETRY_OPERATION_MODE: timedelta(minutes=5),
    CONF_PRICING_GENERAL_PRICE: timedelta(minutes=10),
    CONF_PRICING_FEED_IN_PRICE: timedelta(minutes=10),
    CONF_SOLCAST_FORECAST_TODAY: timedelta(hours=2),
    CONF_SOLCAST_FORECAST_TOMORROW: timedelta(hours=6),
}

FAILURE_THRESHOLD_WARNING = 3
FAILURE_THRESHOLD_ERROR = 10

LOCALSHIFT_ENTITY_CONFIG: dict[str, dict[str, Any]] = {
    "sensor.localshift_price_cheap_effective": {
        "category": EntityCategory.REQUIRED,
        "expected_type": float,
        "staleness_minutes": 15,
    },
    "sensor.localshift_price_cheap_charge_stop": {
        "category": EntityCategory.REQUIRED,
        "expected_type": float,
        "staleness_minutes": 15,
    },
    "sensor.localshift_solar_weighted_avg_fit": {
        "category": EntityCategory.REQUIRED,
        "expected_type": float,
        "staleness_minutes": 15,
    },
    "sensor.localshift_forecast_battery": {
        "category": EntityCategory.REQUIRED,
        "expected_type": float,
        "staleness_minutes": 15,
    },
    "sensor.localshift_cost_electricity_net": {
        "category": EntityCategory.REQUIRED,
        "expected_type": float,
        "staleness_minutes": 15,
    },
    "sensor.localshift_optimizer_plan": {
        "category": EntityCategory.REQUIRED,
        "expected_type": int,
        "staleness_minutes": 15,
    },
    "sensor.localshift_forecast_prices": {
        "category": EntityCategory.REQUIRED,
        "expected_type": float,
        "staleness_minutes": 15,
    },
    "sensor.localshift_optimizer_plan_grid": {
        "category": EntityCategory.REQUIRED,
        "expected_type": float,
        "staleness_minutes": 15,
    },
    "sensor.localshift_load_deviation": {
        "category": EntityCategory.REQUIRED,
        "expected_type": float,
        "staleness_minutes": 15,
    },
    "sensor.localshift_forecast_diagnostics": {
        "category": EntityCategory.REQUIRED,
        "expected_type": str,
        "staleness_minutes": 15,
    },
    "sensor.localshift_target_soc_minimum": {
        "category": EntityCategory.REQUIRED,
        "expected_type": float,
        "staleness_minutes": None,
    },
    "sensor.localshift_excess_solar": {
        "category": EntityCategory.REQUIRED,
        "expected_type": float,
        "staleness_minutes": 15,
    },
    "sensor.localshift_load_shift_signal": {
        "category": EntityCategory.REQUIRED,
        "expected_type": str,
        "staleness_minutes": 15,
    },
    "sensor.localshift_forecast_status": {
        "category": EntityCategory.REQUIRED,
        "expected_type": str,
        "staleness_minutes": 15,
    },
    "sensor.localshift_automation_ready": {
        "category": EntityCategory.REQUIRED,
        "expected_type": str,
        "staleness_minutes": 15,
    },
    "sensor.localshift_optimizer_plan_detailed": {
        "category": EntityCategory.REQUIRED,
        "expected_type": str,
        "staleness_minutes": 15,
    },
    "sensor.localshift_optimizer_summary": {
        "category": EntityCategory.REQUIRED,
        "expected_type": str,
        "staleness_minutes": 15,
    },
    "binary_sensor.localshift_price_spike_coming": {
        "category": EntityCategory.REQUIRED,
        "expected_type": bool,
        "staleness_minutes": 15,
    },
    "binary_sensor.localshift_discharge_forced": {
        "category": EntityCategory.REQUIRED,
        "expected_type": bool,
        "staleness_minutes": 15,
    },
    "binary_sensor.localshift_charge_forced": {
        "category": EntityCategory.REQUIRED,
        "expected_type": bool,
        "staleness_minutes": 15,
    },
    "binary_sensor.localshift_charge_boost": {
        "category": EntityCategory.REQUIRED,
        "expected_type": bool,
        "staleness_minutes": 15,
    },
    "binary_sensor.localshift_price_expensive_coming": {
        "category": EntityCategory.REQUIRED,
        "expected_type": bool,
        "staleness_minutes": 15,
    },
    "binary_sensor.localshift_solar_can_reach_target": {
        "category": EntityCategory.REQUIRED,
        "expected_type": bool,
        "staleness_minutes": 15,
    },
    "binary_sensor.localshift_charge_boost_needed": {
        "category": EntityCategory.REQUIRED,
        "expected_type": bool,
        "staleness_minutes": 15,
    },
    "binary_sensor.localshift_demand_window": {
        "category": EntityCategory.REQUIRED,
        "expected_type": bool,
        "staleness_minutes": 15,
    },
    "binary_sensor.localshift_excess_solar_available": {
        "category": EntityCategory.REQUIRED,
        "expected_type": bool,
        "staleness_minutes": 15,
    },
    "binary_sensor.localshift_tesla_override_active": {
        "category": EntityCategory.REQUIRED,
        "expected_type": bool,
        "staleness_minutes": 15,
    },
    "switch.localshift_automation_enabled": {
        "category": EntityCategory.REQUIRED,
        "expected_type": bool,
        "staleness_minutes": None,
    },
    "switch.localshift_spike_discharge_enabled": {
        "category": EntityCategory.REQUIRED,
        "expected_type": bool,
        "staleness_minutes": None,
    },
    "switch.localshift_spike_discharge_conservative": {
        "category": EntityCategory.REQUIRED,
        "expected_type": bool,
        "staleness_minutes": None,
    },
    "switch.localshift_dry_run": {
        "category": EntityCategory.REQUIRED,
        "expected_type": bool,
        "staleness_minutes": None,
    },
    "switch.localshift_demand_window_block": {
        "category": EntityCategory.REQUIRED,
        "expected_type": bool,
        "staleness_minutes": None,
    },
    "switch.localshift_allow_dw_entry_under_target": {
        "category": EntityCategory.REQUIRED,
        "expected_type": bool,
        "staleness_minutes": None,
    },
    "switch.localshift_stale_solar_conservative": {
        "category": EntityCategory.REQUIRED,
        "expected_type": bool,
        "staleness_minutes": None,
    },
    "switch.localshift_notifications_enabled": {
        "category": EntityCategory.REQUIRED,
        "expected_type": bool,
        "staleness_minutes": None,
    },
    "switch.localshift_enable_learning": {
        "category": EntityCategory.REQUIRED,
        "expected_type": bool,
        "staleness_minutes": None,
    },
    "select.localshift_battery_mode": {
        "category": EntityCategory.REQUIRED,
        "expected_type": str,
        "staleness_minutes": None,
    },
    "select.localshift_optimization_mode": {
        "category": EntityCategory.REQUIRED,
        "expected_type": str,
        "staleness_minutes": None,
    },
    "number.localshift_cheap_price_percentile": {
        "category": EntityCategory.REQUIRED,
        "expected_type": float,
        "staleness_minutes": None,
    },
    "number.localshift_max_pre_charge_price": {
        "category": EntityCategory.REQUIRED,
        "expected_type": float,
        "staleness_minutes": None,
    },
    "number.localshift_battery_target": {
        "category": EntityCategory.REQUIRED,
        "expected_type": float,
        "staleness_minutes": None,
    },
    "number.localshift_minimum_target_soc": {
        "category": EntityCategory.REQUIRED,
        "expected_type": float,
        "staleness_minutes": None,
    },
    "number.localshift_stale_solar_confidence_ceiling": {
        "category": EntityCategory.REQUIRED,
        "expected_type": float,
        "staleness_minutes": None,
    },
    "sensor.localshift_forecast_accuracy": {
        "category": EntityCategory.OPTIONAL,
        "expected_type": float,
        "staleness_minutes": 60,
    },
    "sensor.localshift_extended_forecast_accuracy": {
        "category": EntityCategory.OPTIONAL,
        "expected_type": float,
        "staleness_minutes": 1440,
    },
    "sensor.localshift_solar_forecast_accuracy": {
        "category": EntityCategory.OPTIONAL,
        "expected_type": float,
        "staleness_minutes": 1440,
    },
    "sensor.localshift_decision_lag": {
        "category": EntityCategory.OPTIONAL,
        "expected_type": float,
        "staleness_minutes": 15,
    },
    "sensor.localshift_cost_reconciliation": {
        "category": EntityCategory.OPTIONAL,
        "expected_type": str,
        "staleness_minutes": 1440,
    },
    "sensor.localshift_learning_status": {
        "category": EntityCategory.OPTIONAL,
        "expected_type": str,
        "staleness_minutes": 60,
    },
    "sensor.localshift_decision_quality": {
        "category": EntityCategory.OPTIONAL,
        "expected_type": float,
        "staleness_minutes": 60,
    },
    "sensor.localshift_learning_decision_history": {
        "category": EntityCategory.OPTIONAL,
        "expected_type": int,
        "staleness_minutes": 60,
    },
    "sensor.localshift_decision_log": {
        "category": EntityCategory.OPTIONAL,
        "expected_type": str,
        "staleness_minutes": 60,
    },
    "sensor.localshift_forecast_history": {
        "category": EntityCategory.OPTIONAL,
        "expected_type": int,
        "staleness_minutes": 1440,
    },
    "sensor.localshift_integration_status": {
        "category": EntityCategory.OPTIONAL,
        "expected_type": str,
        "staleness_minutes": 15,
    },
    "sensor.localshift_entity_health": {
        "category": EntityCategory.OPTIONAL,
        "expected_type": str,
        "staleness_minutes": 15,
    },
}
