"""Constants for the LocalShift integration."""

from __future__ import annotations

from enum import Enum

# -----------------------------------------------------------------------------
# Domain
# -----------------------------------------------------------------------------

DOMAIN = "localshift"

# -----------------------------------------------------------------------------
# Battery Modes (state machine states)
# -----------------------------------------------------------------------------


class BatteryMode(str, Enum):
    """Battery operating modes."""

    SELF_CONSUMPTION = "self_consumption"
    GRID_CHARGING = "grid_charging"
    BOOST_CHARGING = "boost_charging"
    SPIKE_DISCHARGE = "spike_discharge"
    PROACTIVE_EXPORT = "proactive_export"
    DEMAND_BLOCK = "demand_block"
    MANUAL = "manual"


# Teslemetry export modes (select.allow_export options)
TESLEMETRY_EXPORT_PV_ONLY = "pv_only"
TESLEMETRY_EXPORT_BATTERY_OK = "battery_ok"

# Teslemetry power charge thresholds
CHARGE_RATE_BACKUP_KW = 3.3  # Force charge rate (backup mode - grid charging)
CHARGE_RATE_BOOST_KW = 5.0  # Boost charge rate (5kW)
CHARGE_RATE_SOLAR_KW = 5.0  # Max solar-to-battery charge rate (inverter limit)

# Powerwall capacity
BATTERY_CAPACITY_KWH = 13.5

# Force discharge time window (dummy tariff limitation)
DISCHARGE_EARLIEST_HOUR = 6

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
    CONF_PRICING_GENERAL_PRICE: "sensor.100h_general_price",
    CONF_PRICING_FEED_IN_PRICE: "sensor.100h_feed_in_price",
    CONF_PRICING_GENERAL_FORECAST: "sensor.100h_general_forecast",
    CONF_PRICING_FEED_IN_FORECAST: "sensor.100h_feed_in_forecast",
    CONF_PRICING_PRICE_SPIKE: "binary_sensor.100h_price_spike",
    CONF_SOLCAST_FORECAST_TODAY: "sensor.solcast_pv_forecast_forecast_today",
    CONF_SOLCAST_FORECAST_TOMORROW: "sensor.solcast_pv_forecast_forecast_tomorrow",
    CONF_SUN_ENTITY: "sun.sun",
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

# Default values (matching YAML package)
DEFAULT_CHEAP_PRICE_PERCENTILE = 25  # percentile (e.g., 25th percentile)
DEFAULT_MAX_PRECHARGE_PRICE = 0.20  # $/kWh
DEFAULT_CHEAP_PRICE_DEADBAND = 0.03  # $/kWh
DEFAULT_FORECAST_LOOKAHEAD_HOURS = 2.0  # hours
DEFAULT_BATTERY_TARGET = 100  # %
DEFAULT_DEMAND_WINDOW_START = "15:00:00"
DEFAULT_DEMAND_WINDOW_END = "21:00:00"
DEFAULT_MANUAL_OVERRIDE_TIMEOUT = 4  # hours
DEFAULT_LOAD_WEIGHT_RECENT = 0.67  # 2/3 weighting to recent usage
DEFAULT_EXPORT_MIN_SPREAD = 0.10  # $/kWh minimum spread to export (buy - sell)
DEFAULT_MINIMUM_TARGET_SOC = 20  # % minimum SOC for discharge modes
DEFAULT_ALLOW_DW_ENTRY_UNDER_TARGET = (
    False  # Allow DW entry under target when solar can reach target
)
DEFAULT_SPIKE_PRICE_PERCENTILE = 75  # Only export at top 25% of spike prices

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
    CONF_LOAD_WEIGHT_RECENT: {
        "min": 0.0,
        "max": 1.0,
        "step": 0.05,
        "unit": "",
        "icon": "mdi:scale-balance",
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

SWITCH_DEFAULTS = {
    SWITCH_AUTOMATION_ENABLED: True,
    SWITCH_SPIKE_DISCHARGE_ENABLED: True,
    SWITCH_SPIKE_DISCHARGE_CONSERVATIVE: False,
    SWITCH_DRY_RUN: False,
    SWITCH_DEMAND_WINDOW_BLOCK: True,
    SWITCH_ALLOW_DW_ENTRY_UNDER_TARGET: False,
}

SWITCH_ICONS = {
    SWITCH_AUTOMATION_ENABLED: "mdi:battery-sync",
    SWITCH_SPIKE_DISCHARGE_ENABLED: "mdi:flash-alert",
    SWITCH_SPIKE_DISCHARGE_CONSERVATIVE: "mdi:shield-check",
    SWITCH_DRY_RUN: "mdi:test-tube",
    SWITCH_DEMAND_WINDOW_BLOCK: "mdi:clock-alert-outline",
    SWITCH_ALLOW_DW_ENTRY_UNDER_TARGET: "mdi:transfer-down",
}

SWITCH_NAMES = {
    SWITCH_AUTOMATION_ENABLED: "Automation Enabled",
    SWITCH_SPIKE_DISCHARGE_ENABLED: "Spike Discharge Enabled",
    SWITCH_SPIKE_DISCHARGE_CONSERVATIVE: "Spike Discharge Conservative",
    SWITCH_DRY_RUN: "Dry Run",
    SWITCH_DEMAND_WINDOW_BLOCK: "Demand Window Block",
    SWITCH_ALLOW_DW_ENTRY_UNDER_TARGET: "Allow DW Entry Under Target",
}

# -----------------------------------------------------------------------------
# Button Keys (manual mode controls)
# -----------------------------------------------------------------------------

BUTTON_FORCE_CHARGE = "force_charge"
BUTTON_FORCE_DISCHARGE = "force_discharge"
BUTTON_BOOST_CHARGE = "boost_charge"
BUTTON_SELF_CONSUMPTION = "self_consumption"
BUTTON_UPDATE_FORECAST = "update_forecast"

BUTTON_ICONS = {
    BUTTON_FORCE_CHARGE: "mdi:battery-charging",
    BUTTON_FORCE_DISCHARGE: "mdi:battery-arrow-down",
    BUTTON_BOOST_CHARGE: "mdi:battery-charging-high",
    BUTTON_SELF_CONSUMPTION: "mdi:battery-sync",
    BUTTON_UPDATE_FORECAST: "mdi:refresh",
}

BUTTON_NAMES = {
    BUTTON_FORCE_CHARGE: "Force Charge",
    BUTTON_FORCE_DISCHARGE: "Force Discharge",
    BUTTON_BOOST_CHARGE: "Boost Charge",
    BUTTON_SELF_CONSUMPTION: "Self Consumption",
    BUTTON_UPDATE_FORECAST: "Update Forecast",
}

# -----------------------------------------------------------------------------
# Platforms
# -----------------------------------------------------------------------------

PLATFORMS = ["sensor", "binary_sensor", "number", "switch", "button"]
