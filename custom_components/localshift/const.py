"""Constants for the LocalShift integration."""

from __future__ import annotations

from enum import StrEnum

# -----------------------------------------------------------------------------
# Domain
# -----------------------------------------------------------------------------

DOMAIN = "localshift"

# -----------------------------------------------------------------------------
# Battery Modes (state machine states)
# -----------------------------------------------------------------------------


class BatteryMode(StrEnum):
    """Battery operating modes."""

    SELF_CONSUMPTION = "self_consumption"
    GRID_CHARGING = "grid_charging"
    BOOST_CHARGING = "boost_charging"
    SPIKE_DISCHARGE = "spike_discharge"
    PROACTIVE_EXPORT = "proactive_export"
    DEMAND_BLOCK = "demand_block"
    MANUAL = "manual"

    @property
    def display_name(self) -> str:
        """Return a user-friendly display name for the mode."""
        names = {
            BatteryMode.SELF_CONSUMPTION: "Self Consumption",
            BatteryMode.GRID_CHARGING: "Grid Charging",
            BatteryMode.BOOST_CHARGING: "Boost Charging",
            BatteryMode.SPIKE_DISCHARGE: "Spike Discharge",
            BatteryMode.PROACTIVE_EXPORT: "Proactive Export",
            BatteryMode.DEMAND_BLOCK: "Demand Block",
            BatteryMode.MANUAL: "Manual",
        }
        return names[self]


# Teslemetry export modes (select.allow_export options)
TESLEMETRY_EXPORT_PV_ONLY = "pv_only"
TESLEMETRY_EXPORT_BATTERY_OK = "battery_ok"

# Teslemetry power charge thresholds
CHARGE_RATE_GRID_KW = 3.3  # Grid charging rate (backup mode)
CHARGE_RATE_BOOST_KW = 5.0  # Boost charge rate (autonomous mode)
CHARGE_RATE_SOLAR_KW = 5.0  # Max solar-to-battery charge rate (inverter limit)

# Tesla firmware (July 2025) silently resets backup reserve values 81-99% to 80%.
# Valid backup reserve values: 0-80% or 100%
BACKUP_RESERVE_MAX_VALID = (
    80  # Maximum reserve that Tesla firmware accepts in backup mode
)

# Issue #309: Maximum SOC for boost charging
# Powerwall throttles charge rate above 80%, making boost charging inefficient.
# When SOC >= 80%, use normal grid charging (3.3kW) instead of boost (5kW).
BOOST_CHARGE_MAX_SOC = 80.0

# Powerwall capacity
BATTERY_CAPACITY_KWH = 13.5

# Force discharge time window (dummy tariff limitation)
DISCHARGE_EARLIEST_HOUR = 6

# -----------------------------------------------------------------------------
# Consumption Prediction Settings
# -----------------------------------------------------------------------------

# History window for consumption profile calculation (4 weeks for better statistics)
HISTORY_WINDOW_DAYS = 28

# Minimum samples per hour before using profile-specific data
MIN_SAMPLES_PER_HOUR = 3

# -----------------------------------------------------------------------------
# Config Flow Keys — Entity Selection (Step 1)
# -----------------------------------------------------------------------------

# Teslemetry entities
CONF_TESLEMETRY_OPERATION_MODE = "teslemetry_operation_mode"
CONF_TESLEMETRY_BACKUP_RESERVE = "teslemetry_backup_reserve"
CONF_TESLEMETRY_SOC = "teslemetry_soc"
CONF_MINIMUM_TARGET_SOC = "minimum_target_soc"
CONF_TESLEMETRY_GRID_POWER = "teslemetry_grid_power"
CONF_TESLEMETRY_BATTERY_POWER = "teslemetry_battery_power"
CONF_TESLEMETRY_SOLAR_POWER = "teslemetry_solar_power"
CONF_TESLEMETRY_LOAD_POWER = "teslemetry_load_power"
CONF_TESLEMETRY_ALLOW_EXPORT = "teslemetry_allow_export"
CONF_TESLEMETRY_ALLOW_CHARGING_FROM_GRID = "teslemetry_allow_charging_from_grid"

# Pricing entities
CONF_PRICING_GENERAL_PRICE = "pricing_general_price"
CONF_PRICING_FEED_IN_PRICE = "pricing_feed_in_price"
CONF_PRICING_GENERAL_FORECAST = "pricing_general_forecast"
CONF_PRICING_FEED_IN_FORECAST = "pricing_feed_in_forecast"
CONF_PRICING_PRICE_SPIKE = "pricing_price_spike"

# Solcast entities
CONF_SOLCAST_FORECAST_TODAY = "solcast_forecast_today"
CONF_SOLCAST_FORECAST_TOMORROW = "solcast_forecast_tomorrow"

# Notification service
CONF_NOTIFY_SERVICE = "notify_service"

# Sun entity (for solar export hold logic)
CONF_SUN_ENTITY = "sun_entity"

# Weather entity (for temperature-based consumption prediction)
CONF_WEATHER_ENTITY = "weather_entity"
DEFAULT_WEATHER_ENTITY = ""  # No default - user must configure

# Temperature thresholds for degree-day model
CONF_COOLING_THRESHOLD = "cooling_threshold"
CONF_HEATING_THRESHOLD = "heating_threshold"
DEFAULT_COOLING_THRESHOLD = 24.0  # °C - above this, cooling load increases
DEFAULT_HEATING_THRESHOLD = 18.0  # °C - below this, heating load increases

# Weather learning configuration
CONF_WEATHER_LEARNING_ENABLED = "weather_learning_enabled"
DEFAULT_WEATHER_LEARNING_ENABLED = True

# Manual override auto-clear timeout
CONF_MANUAL_OVERRIDE_TIMEOUT = "manual_override_timeout"

# -----------------------------------------------------------------------------
# Config Flow Keys — Default Entity IDs
# -----------------------------------------------------------------------------

DEFAULT_ENTITY_IDS = {
    CONF_TESLEMETRY_OPERATION_MODE: "select.my_home_operation_mode",
    CONF_TESLEMETRY_BACKUP_RESERVE: "number.my_home_backup_reserve",
    CONF_TESLEMETRY_SOC: "sensor.my_home_percentage_charged",
    CONF_TESLEMETRY_GRID_POWER: "sensor.my_home_grid_power",
    CONF_TESLEMETRY_BATTERY_POWER: "sensor.my_home_battery_power",
    CONF_TESLEMETRY_SOLAR_POWER: "sensor.my_home_solar_power",
    CONF_TESLEMETRY_LOAD_POWER: "sensor.my_home_load_power",
    CONF_TESLEMETRY_ALLOW_EXPORT: "select.my_home_allow_export",
    CONF_TESLEMETRY_ALLOW_CHARGING_FROM_GRID: "switch.my_home_allow_charging_from_grid",
    CONF_PRICING_GENERAL_PRICE: "sensor.100h_general_price",
    CONF_PRICING_FEED_IN_PRICE: "sensor.100h_feed_in_price",
    CONF_PRICING_GENERAL_FORECAST: "sensor.100h_general_forecast",
    CONF_PRICING_FEED_IN_FORECAST: "sensor.100h_feed_in_forecast",
    CONF_PRICING_PRICE_SPIKE: "binary_sensor.100h_price_spike",
    CONF_SOLCAST_FORECAST_TODAY: "sensor.solcast_pv_forecast_forecast_today",
    CONF_SOLCAST_FORECAST_TOMORROW: "sensor.solcast_pv_forecast_forecast_tomorrow",
    CONF_SUN_ENTITY: "sun.sun",
    CONF_WEATHER_ENTITY: DEFAULT_WEATHER_ENTITY,
}

# -----------------------------------------------------------------------------
# Options Flow Keys — User-Configurable Thresholds
# -----------------------------------------------------------------------------

CONF_CHEAP_PRICE_PERCENTILE = "cheap_price_percentile"
CONF_MAX_PRECHARGE_PRICE = "max_pre_charge_price"
CONF_CHEAP_PRICE_DEADBAND = "cheap_price_deadband"
CONF_FORECAST_LOOKAHEAD_HOURS = "forecast_lookahead_hours"
CONF_BATTERY_TARGET = "battery_target"
CONF_DEMAND_WINDOW_START = "demand_window_start"
CONF_DEMAND_WINDOW_END = "demand_window_end"
CONF_LOAD_WEIGHT_RECENT = "load_weight_recent"
CONF_EXPORT_MIN_SPREAD = "export_min_spread"
CONF_ALLOW_DW_ENTRY_UNDER_TARGET = "allow_dw_entry_under_target"
CONF_SPIKE_PRICE_PERCENTILE = "spike_price_percentile"
CONF_EXPORT_PRICE_MARGIN = "export_price_margin"

# Default values (matching YAML package)
DEFAULT_CHEAP_PRICE_PERCENTILE = 25  # percentile (e.g., 25th percentile)
DEFAULT_MAX_PRECHARGE_PRICE = 0.20  # $/kWh
DEFAULT_CHEAP_PRICE_DEADBAND = 0.03  # $/kWh
DEFAULT_FORECAST_LOOKAHEAD_HOURS = 2.0  # hours
DEFAULT_BATTERY_TARGET = 100  # %
DEFAULT_DEMAND_WINDOW_START = "15:00:00"
DEFAULT_DEMAND_WINDOW_END = "21:00:00"
DEFAULT_MANUAL_OVERRIDE_TIMEOUT = 4  # hours
DEFAULT_LOAD_WEIGHT_RECENT = (
    0.67  # 2/3 weighting to recent usage (legacy, kept for compatibility)
)
DEFAULT_LOAD_DECAY_FACTOR = 0.8  # 20% reduction per hour for exponential decay
DEFAULT_LOAD_INITIAL_WEIGHT = (
    0.8  # Initial weight for current hour (80% recent, 20% historical)
)
DEFAULT_MINIMUM_TARGET_SOC = 20  # % minimum SOC for discharge modes
DEFAULT_ALLOW_DW_ENTRY_UNDER_TARGET = (
    False  # Allow DW entry under target when solar can reach target
)
DEFAULT_SPIKE_PRICE_PERCENTILE = 75  # Only export at top 25% of spike prices
DEFAULT_EXPORT_PRICE_MARGIN = (
    0.10  # $/kWh minimum profit margin for export/re-import arbitrage
)

# Threshold min/max/step (for NumberEntity and options validation)
THRESHOLD_RANGES = {
    CONF_CHEAP_PRICE_PERCENTILE: {
        "min": 5,
        "max": 50,
        "step": 1,
        "unit": "%",
        "icon": "mdi:chart-box-outline",
    },
    CONF_MAX_PRECHARGE_PRICE: {
        "min": 0.00,
        "max": 0.50,
        "step": 0.01,
        "unit": "$/kWh",
        "icon": "mdi:tag-arrow-up-outline",
    },
    CONF_CHEAP_PRICE_DEADBAND: {
        "min": 0.00,
        "max": 0.10,
        "step": 0.01,
        "unit": "$/kWh",
        "icon": "mdi:arrow-left-right",
    },
    CONF_FORECAST_LOOKAHEAD_HOURS: {
        "min": 1.0,
        "max": 8.0,
        "step": 0.5,
        "unit": "hours",
        "icon": "mdi:clock-fast",
    },
    CONF_BATTERY_TARGET: {
        "min": 50,
        "max": 100,
        "step": 5,
        "unit": "%",
        "icon": "mdi:battery-check",
    },
    CONF_EXPORT_MIN_SPREAD: {
        "min": 0.00,
        "max": 0.30,
        "step": 0.01,
        "unit": "$/kWh",
        "icon": "mdi:swap-horizontal",
    },
    CONF_SPIKE_PRICE_PERCENTILE: {
        "min": 50,
        "max": 95,
        "step": 5,
        "unit": "%",
        "icon": "mdi:chart-line",
    },
    CONF_MINIMUM_TARGET_SOC: {
        "min": 5,
        "max": 30,
        "step": 1,
        "unit": "%",
        "icon": "mdi:battery-lock",
    },
    CONF_COOLING_THRESHOLD: {
        "min": 18.0,
        "max": 30.0,
        "step": 0.5,
        "unit": "°C",
        "icon": "mdi:thermometer-high",
    },
    CONF_HEATING_THRESHOLD: {
        "min": 10.0,
        "max": 22.0,
        "step": 0.5,
        "unit": "°C",
        "icon": "mdi:thermometer-low",
    },
    CONF_EXPORT_PRICE_MARGIN: {
        "min": 0.00,
        "max": 0.30,
        "step": 0.01,
        "unit": "$/kWh",
        "icon": "mdi:currency-usd",
    },
}

# -----------------------------------------------------------------------------
# Switch Keys (user-facing toggles)
# -----------------------------------------------------------------------------

SWITCH_AUTOMATION_ENABLED = "automation_enabled"
SWITCH_SPIKE_DISCHARGE_ENABLED = "spike_discharge_enabled"
SWITCH_SPIKE_DISCHARGE_CONSERVATIVE = "spike_discharge_conservative"
SWITCH_DRY_RUN = "dry_run"
SWITCH_DEMAND_WINDOW_BLOCK = "demand_window_block"
SWITCH_ALLOW_DW_ENTRY_UNDER_TARGET = "allow_dw_entry_under_target"

# Notification preference switches
SWITCH_NOTIFY_TRANSITIONS = "notify_transitions"
SWITCH_NOTIFY_DAILY_SUMMARY = "notify_daily_summary"
SWITCH_NOTIFY_MANUAL_ACTIONS = "notify_manual_actions"
SWITCH_NOTIFY_ALERTS = "notify_alerts"

# Learning system switch (Issue #170 Phase 4)
SWITCH_ENABLE_LEARNING = "enable_learning"

SWITCH_DEFAULTS = {
    SWITCH_AUTOMATION_ENABLED: True,
    SWITCH_SPIKE_DISCHARGE_ENABLED: True,
    SWITCH_SPIKE_DISCHARGE_CONSERVATIVE: False,
    SWITCH_DRY_RUN: False,
    SWITCH_DEMAND_WINDOW_BLOCK: True,
    SWITCH_ALLOW_DW_ENTRY_UNDER_TARGET: False,
    SWITCH_NOTIFY_TRANSITIONS: True,
    SWITCH_NOTIFY_DAILY_SUMMARY: True,
    SWITCH_NOTIFY_MANUAL_ACTIONS: True,
    SWITCH_NOTIFY_ALERTS: True,
    SWITCH_ENABLE_LEARNING: False,  # Users must opt-in to active optimization
}

SWITCH_ICONS = {
    SWITCH_AUTOMATION_ENABLED: "mdi:battery-sync",
    SWITCH_SPIKE_DISCHARGE_ENABLED: "mdi:flash-alert",
    SWITCH_SPIKE_DISCHARGE_CONSERVATIVE: "mdi:shield-check",
    SWITCH_DRY_RUN: "mdi:test-tube",
    SWITCH_DEMAND_WINDOW_BLOCK: "mdi:clock-alert-outline",
    SWITCH_ALLOW_DW_ENTRY_UNDER_TARGET: "mdi:transfer-down",
    SWITCH_NOTIFY_TRANSITIONS: "mdi:bell-sleep",
    SWITCH_NOTIFY_DAILY_SUMMARY: "mdi:calendar-today",
    SWITCH_NOTIFY_MANUAL_ACTIONS: "mdi:gesture-tap",
    SWITCH_NOTIFY_ALERTS: "mdi:alert-circle",
    SWITCH_ENABLE_LEARNING: "mdi:brain",
}

SWITCH_NAMES = {
    SWITCH_AUTOMATION_ENABLED: "Automation Enabled",
    SWITCH_SPIKE_DISCHARGE_ENABLED: "Spike Discharge Enabled",
    SWITCH_SPIKE_DISCHARGE_CONSERVATIVE: "Spike Discharge Conservative",
    SWITCH_DRY_RUN: "Dry Run",
    SWITCH_DEMAND_WINDOW_BLOCK: "Demand Window Block",
    SWITCH_ALLOW_DW_ENTRY_UNDER_TARGET: "Allow DW Entry Under Target",
    SWITCH_NOTIFY_TRANSITIONS: "Notify Mode Transitions",
    SWITCH_NOTIFY_DAILY_SUMMARY: "Notify Daily Summary",
    SWITCH_NOTIFY_MANUAL_ACTIONS: "Notify Manual Actions",
    SWITCH_NOTIFY_ALERTS: "Notify Alerts",
    SWITCH_ENABLE_LEARNING: "Enable Learning",
}

# -----------------------------------------------------------------------------
# Select Keys (battery mode selection)
# -----------------------------------------------------------------------------

SELECT_BATTERY_MODE = "battery_mode"

SELECT_OPTIONS = {
    SELECT_BATTERY_MODE: [
        "self_consumption",
        "grid_charging",
        "boost_charging",
        "spike_discharge",
        "proactive_export",
    ],
}

SELECT_ICONS = {
    SELECT_BATTERY_MODE: "mdi:battery-sync",
}

SELECT_NAMES = {
    SELECT_BATTERY_MODE: "Battery Mode",
}

# -----------------------------------------------------------------------------
# Button Keys (utility controls - mode control moved to select entity)
# -----------------------------------------------------------------------------

BUTTON_UPDATE_FORECAST = "update_forecast"
BUTTON_RESET_LEARNING = "reset_learning"

BUTTON_ICONS = {
    BUTTON_UPDATE_FORECAST: "mdi:refresh",
    BUTTON_RESET_LEARNING: "mdi:brain-off",
}

BUTTON_NAMES = {
    BUTTON_UPDATE_FORECAST: "Update Forecast",
    BUTTON_RESET_LEARNING: "Reset Learning Data",
}

# -----------------------------------------------------------------------------
# Learning System: Optimizable Parameters (Issue #170 Phase 2)
# -----------------------------------------------------------------------------

from dataclasses import dataclass


@dataclass(frozen=True)
class OptimizableParam:
    """Definition of a parameter that the learning system can adjust."""

    name: str
    default: float
    min_val: float
    max_val: float
    step: float
    description: str


OPTIMIZABLE_PARAMS: dict[str, OptimizableParam] = {
    "cheap_price_bias": OptimizableParam(
        name="cheap_price_bias",
        default=0.0,
        min_val=-5.0,
        max_val=5.0,
        step=0.5,
        description="Additive bias (c/kWh) to effective cheap price threshold. "
        "Positive = more willing to grid charge, Negative = more conservative.",
    ),
    "solar_confidence_factor": OptimizableParam(
        name="solar_confidence_factor",
        default=1.0,
        min_val=0.5,
        max_val=1.5,
        step=0.05,
        description="Multiplier on Solcast solar forecast. <1.0 = pessimistic (charge more), "
        ">1.0 = optimistic (trust solar, charge less).",
    ),
    "overnight_drain_safety_margin": OptimizableParam(
        name="overnight_drain_safety_margin",
        default=0.0,
        min_val=-5.0,
        max_val=10.0,
        step=1.0,
        description="Extra SOC% safety margin added to overnight drain simulations. "
        "Positive = keep more buffer.",
    ),
    "grid_charge_soc_headroom": OptimizableParam(
        name="grid_charge_soc_headroom",
        default=0.0,
        min_val=-5.0,
        max_val=10.0,
        step=1.0,
        description="SOC% headroom above target. Positive = charge slightly above target "
        "to account for forecast errors.",
    ),
    "export_threshold_adjustment": OptimizableParam(
        name="export_threshold_adjustment",
        default=0.0,
        min_val=-3.0,
        max_val=3.0,
        step=0.5,
        description="Adjustment to proactive export profitability threshold (c/kWh). "
        "Positive = more conservative about exporting.",
    ),
    "consumption_forecast_bias": OptimizableParam(
        name="consumption_forecast_bias",
        default=0.0,
        min_val=-0.5,
        max_val=0.5,
        step=0.05,
        description="Additive bias (kW) to consumption forecast. "
        "Positive = assume higher consumption.",
    ),
}

# Minimum observations before first optimization
LEARNING_MIN_OBSERVATIONS = 50

# Hours between optimization updates
LEARNING_UPDATE_INTERVAL_HOURS = 24

# -----------------------------------------------------------------------------
# Statistics Backfiller Configuration (Issue #267)
# -----------------------------------------------------------------------------

CONF_GRID_IMPORT_ENTITY = "grid_import_entity"
CONF_GRID_EXPORT_ENTITY = "grid_export_entity"
CONF_BATTERY_CHARGE_ENTITY = "battery_charge_entity"
CONF_BATTERY_DISCHARGE_ENTITY = "battery_discharge_entity"
CONF_BACKFILL_SCHEDULE = "backfill_schedule"

DEFAULT_BACKFILL_SCHEDULE = "02:00"  # Run backfill at 2 AM

# -----------------------------------------------------------------------------
# Extended History Configuration (Issue #268)
# -----------------------------------------------------------------------------

CONF_EXTENDED_HISTORY_DAYS = "extended_history_days"
CONF_SEASONAL_PROFILE_ENABLED = "seasonal_profile_enabled"

DEFAULT_EXTENDED_HISTORY_DAYS = 90  # 90 days for seasonal pattern detection
DEFAULT_SEASONAL_PROFILE_ENABLED = True

# Extended history lookback limits
EXTENDED_HISTORY_MIN_DAYS = 30  # Minimum days for meaningful patterns
EXTENDED_HISTORY_MAX_DAYS = 365  # Maximum days (1 year)

# -----------------------------------------------------------------------------
# Platforms
# -----------------------------------------------------------------------------

PLATFORMS = ["sensor", "binary_sensor", "number", "switch", "button", "select"]

# -----------------------------------------------------------------------------
# DP Optimizer rollout configuration (#403)
# -----------------------------------------------------------------------------

CONF_OPTIMIZER_ENABLED = "optimizer_enabled"
"""Feature flag — set True to run the DP optimizer in shadow mode each cycle."""

CONF_OPTIMIZER_CONTROL_MODE = "optimizer_control_mode"
"""
Controls how the optimizer result is used:
  'shadow'  — optimizer runs but does NOT affect runtime control (default)
  'assist'  — optimizer result is logged and compared; legacy planner controls
  'active'  — (future) optimizer result drives runtime control
"""

DEFAULT_OPTIMIZER_ENABLED = False
"""Optimizer is disabled by default; enabled via config flow / options."""

DEFAULT_OPTIMIZER_CONTROL_MODE = "shadow"
"""Always start in shadow mode; never silently assume active control."""

OPTIMIZER_CONTROL_MODES = ["shadow", "assist", "active"]
"""Valid optimizer control mode values."""

CONF_OPTIMIZER_ACTIVE_MIN_SHADOW_SUCCESS_RATE = (
    "optimizer_active_min_shadow_success_rate"
)
"""Config key for minimum shadow success rate before enabling active mode."""
DEFAULT_OPTIMIZER_ACTIVE_MIN_SHADOW_SUCCESS_RATE = 0.0
"""Default: require no minimum success rate (0.0 = disabled check)."""

OPTIMIZER_COOLDOWN_CYCLES = 3
"""Number of cycles to stay in fallback cooldown after repeated failures."""

OPTIMIZER_FORECAST_FRESHNESS_MINUTES = 30
"""Maximum forecast age in minutes for active mode admission."""
