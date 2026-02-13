"""Constants for the Amber Powerwall integration."""

from __future__ import annotations

from enum import StrEnum

# -----------------------------------------------------------------------------
# Domain
# -----------------------------------------------------------------------------

DOMAIN = "amber_powerwall"

# -----------------------------------------------------------------------------
# Battery Modes (state machine states)
# -----------------------------------------------------------------------------


class BatteryMode(StrEnum):
    """Battery operating modes."""

    SELF_CONSUMPTION = "self_consumption"
    HOLD = "hold"
    SOLAR_EXPORT_HOLD = "solar_export_hold"
    GRID_CHARGING = "grid_charging"
    BOOST_CHARGING = "boost_charging"
    SPIKE_DISCHARGE = "spike_discharge"
    HOLDING_FOR_SPIKE = "holding_for_spike"
    DEMAND_BLOCK = "demand_block"
    MANUAL = "manual"


# Teslemetry operation_mode values
TESLEMETRY_MODE_SELF_CONSUMPTION = "self_consumption"
TESLEMETRY_MODE_BACKUP = "backup"
TESLEMETRY_MODE_AUTONOMOUS = "autonomous"

# Teslemetry reserve values
RESERVE_SELF_CONSUMPTION = 10
RESERVE_BOOST_CHARGE = 100
RESERVE_FORCE_DISCHARGE = 10

# Teslemetry export modes (select.allow_export options)
TESLEMETRY_EXPORT_PV_ONLY = "pv_only"
TESLEMETRY_EXPORT_BATTERY_OK = "battery_ok"

# Teslemetry power charge thresholds
CHARGE_RATE_BACKUP_KW = 3.3  # Force charge rate (backup mode)
CHARGE_RATE_BOOST_KW = 5.0   # Boost charge rate (autonomous + reserve=100)

# Force discharge time window (dummy tariff limitation)
DISCHARGE_EARLIEST_HOUR = 6
DISCHARGE_LATEST_HOUR = 0  # midnight

# -----------------------------------------------------------------------------
# Config Flow Keys — Entity Selection (Step 1)
# -----------------------------------------------------------------------------

# Teslemetry entities
CONF_TESLEMETRY_OPERATION_MODE = "teslemetry_operation_mode"
CONF_TESLEMETRY_BACKUP_RESERVE = "teslemetry_backup_reserve"
CONF_TESLEMETRY_SOC = "teslemetry_soc"
CONF_TESLEMETRY_GRID_POWER = "teslemetry_grid_power"
CONF_TESLEMETRY_BATTERY_POWER = "teslemetry_battery_power"
CONF_TESLEMETRY_SOLAR_POWER = "teslemetry_solar_power"
CONF_TESLEMETRY_LOAD_POWER = "teslemetry_load_power"
CONF_TESLEMETRY_ALLOW_EXPORT = "teslemetry_allow_export"

# Amber Electric entities
CONF_AMBER_GENERAL_PRICE = "amber_general_price"
CONF_AMBER_FEED_IN_PRICE = "amber_feed_in_price"
CONF_AMBER_GENERAL_FORECAST = "amber_general_forecast"
CONF_AMBER_FEED_IN_FORECAST = "amber_feed_in_forecast"
CONF_AMBER_PRICE_SPIKE = "amber_price_spike"

# Solcast entities
CONF_SOLCAST_FORECAST_TODAY = "solcast_forecast_today"
CONF_SOLCAST_FORECAST_TOMORROW = "solcast_forecast_tomorrow"

# Notification service
CONF_NOTIFY_SERVICE = "notify_service"

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
    CONF_AMBER_GENERAL_PRICE: "sensor.100h_general_price",
    CONF_AMBER_FEED_IN_PRICE: "sensor.100h_feed_in_price",
    CONF_AMBER_GENERAL_FORECAST: "sensor.100h_general_forecast",
    CONF_AMBER_FEED_IN_FORECAST: "sensor.100h_feed_in_forecast",
    CONF_AMBER_PRICE_SPIKE: "binary_sensor.100h_price_spike",
    CONF_SOLCAST_FORECAST_TODAY: "sensor.solcast_pv_forecast_forecast_today",
    CONF_SOLCAST_FORECAST_TOMORROW: "sensor.solcast_pv_forecast_forecast_tomorrow",
    CONF_NOTIFY_SERVICE: "notify.mobile_app_jacks_iphone",
}

# -----------------------------------------------------------------------------
# Options Flow Keys — User-Configurable Thresholds
# -----------------------------------------------------------------------------

CONF_CHEAP_PRICE_PERCENTILE = "cheap_price_percentile"
CONF_MAX_PRECHARGE_PRICE = "max_precharge_price"
CONF_CHEAP_PRICE_DEADBAND = "cheap_price_deadband"
CONF_FORECAST_LOOKAHEAD_HOURS = "forecast_lookahead_hours"
CONF_PRECHARGE_BATTERY_THRESHOLD = "precharge_battery_threshold"
CONF_BATTERY_TARGET = "battery_target"
CONF_DEMAND_WINDOW_START = "demand_window_start"
CONF_DEMAND_WINDOW_END = "demand_window_end"

# Default values (matching YAML package)
DEFAULT_CHEAP_PRICE_PERCENTILE = 25  # percentile (e.g., 25th percentile)
# Legacy constant for backward compatibility during migration
DEFAULT_CHEAP_PRICE_THRESHOLD = 0.15  # $/kWh (deprecated)
DEFAULT_MAX_PRECHARGE_PRICE = 0.20  # $/kWh
DEFAULT_CHEAP_PRICE_DEADBAND = 0.03  # $/kWh
DEFAULT_FORECAST_LOOKAHEAD_HOURS = 2.0  # hours
DEFAULT_PRECHARGE_BATTERY_THRESHOLD = 50  # %
DEFAULT_BATTERY_TARGET = 100  # %
DEFAULT_DEMAND_WINDOW_START = "15:00:00"
DEFAULT_DEMAND_WINDOW_END = "21:00:00"

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
    CONF_PRECHARGE_BATTERY_THRESHOLD: {
        "min": 0,
        "max": 100,
        "step": 5,
        "unit": "%",
        "icon": "mdi:battery-charging-40",
    },
    CONF_BATTERY_TARGET: {
        "min": 50,
        "max": 100,
        "step": 5,
        "unit": "%",
        "icon": "mdi:battery-check",
    },
}

# -----------------------------------------------------------------------------
# Switch Keys (user-facing toggles)
# -----------------------------------------------------------------------------

SWITCH_AUTOMATION_ENABLED = "automation_enabled"
SWITCH_SPIKE_DISCHARGE_ENABLED = "spike_discharge_enabled"
SWITCH_DRY_RUN = "dry_run"
SWITCH_DEMAND_WINDOW_BLOCK = "demand_window_block"

SWITCH_DEFAULTS = {
    SWITCH_AUTOMATION_ENABLED: True,
    SWITCH_SPIKE_DISCHARGE_ENABLED: True,
    SWITCH_DRY_RUN: False,
    SWITCH_DEMAND_WINDOW_BLOCK: True,
}

SWITCH_ICONS = {
    SWITCH_AUTOMATION_ENABLED: "mdi:battery-sync",
    SWITCH_SPIKE_DISCHARGE_ENABLED: "mdi:flash-alert",
    SWITCH_DRY_RUN: "mdi:test-tube",
    SWITCH_DEMAND_WINDOW_BLOCK: "mdi:clock-alert-outline",
}

SWITCH_NAMES = {
    SWITCH_AUTOMATION_ENABLED: "Automation Enabled",
    SWITCH_SPIKE_DISCHARGE_ENABLED: "Spike Discharge Enabled",
    SWITCH_DRY_RUN: "Dry Run",
    SWITCH_DEMAND_WINDOW_BLOCK: "Demand Window Block",
}

# -----------------------------------------------------------------------------
# Button Keys (manual mode controls)
# -----------------------------------------------------------------------------

BUTTON_FORCE_CHARGE = "force_charge"
BUTTON_FORCE_DISCHARGE = "force_discharge"
BUTTON_HOLD = "hold"
BUTTON_BOOST_CHARGE = "boost_charge"
BUTTON_SELF_CONSUMPTION = "self_consumption"

BUTTON_ICONS = {
    BUTTON_FORCE_CHARGE: "mdi:battery-charging",
    BUTTON_FORCE_DISCHARGE: "mdi:battery-arrow-down",
    BUTTON_HOLD: "mdi:battery-lock",
    BUTTON_BOOST_CHARGE: "mdi:battery-charging-high",
    BUTTON_SELF_CONSUMPTION: "mdi:battery-sync",
}

BUTTON_NAMES = {
    BUTTON_FORCE_CHARGE: "Force Charge",
    BUTTON_FORCE_DISCHARGE: "Force Discharge",
    BUTTON_HOLD: "Hold Battery",
    BUTTON_BOOST_CHARGE: "Boost Charge (5kW)",
    BUTTON_SELF_CONSUMPTION: "Return to Self Consumption",
}

# -----------------------------------------------------------------------------
# Binary Sensor Keys
# -----------------------------------------------------------------------------

BINARY_SENSOR_KEYS = {
    "forecast_spike_within_window": "Forecast Spike Within Window",
    "battery_force_discharge_active": "Force Discharge Active",
    "battery_force_charge_active": "Force Charge Active",
    "battery_boost_charge_active": "Boost Charge Active",
    "battery_hold_active": "Hold Active",
    "forecast_expensive_period_coming": "Expensive Period Coming",
    "solar_can_reach_target": "Solar Can Reach Target",
    "boost_charge_needed": "Boost Charge Needed",
    "hold_justified": "Hold Justified",
    "solar_export_hold_justified": "Solar Export Hold Justified",
    "demand_window_active": "Demand Window Active",
}

# -----------------------------------------------------------------------------
# Sensor Keys
# -----------------------------------------------------------------------------

SENSOR_KEYS = {
    "effective_cheap_price": "Effective Cheap Price",
    "cheap_charge_stop_price": "Cheap Charge Stop Price",
    "solar_weighted_avg_fit": "Solar Weighted Average FIT",
    "active_mode": "Active Mode",
    "solar_battery_forecast": "Solar Battery Forecast",
    "grid_import_power": "Grid Import Power",
    "grid_export_power": "Grid Export Power",
    "net_electricity_cost_today": "Net Electricity Cost Today",
    "decision_log": "Decision Log",
}

# -----------------------------------------------------------------------------
# Internal State Keys (not user-facing, managed by state machine)
# -----------------------------------------------------------------------------

INTERNAL_MANUAL_OVERRIDE = "manual_override_active"
INTERNAL_HOLD_MODE = "hold_mode"
INTERNAL_SOLAR_EXPORT_HOLD = "solar_export_hold_active"
INTERNAL_TARGET_REACHED = "target_reached_today"

# Cost accumulator keys
COST_GRID_IMPORT = "grid_import_cost"
COST_GRID_EXPORT = "grid_export_revenue"
COST_BATTERY_SAVINGS = "battery_savings"
COST_BATTERY_CHARGE = "battery_charge_cost"

# -----------------------------------------------------------------------------
# Mode display configuration
# -----------------------------------------------------------------------------

MODE_DISPLAY = {
    BatteryMode.SELF_CONSUMPTION: {
        "name": "Self Consumption",
        "icon": "mdi:battery-sync",
    },
    BatteryMode.HOLD: {
        "name": "Hold (Grid Only)",
        "icon": "mdi:battery-lock",
    },
    BatteryMode.SOLAR_EXPORT_HOLD: {
        "name": "Solar Export Hold",
        "icon": "mdi:solar-power-variant",
    },
    BatteryMode.GRID_CHARGING: {
        "name": "Grid Charging",
        "icon": "mdi:battery-charging",
    },
    BatteryMode.BOOST_CHARGING: {
        "name": "Boost Charging (5kW)",
        "icon": "mdi:battery-charging-high",
    },
    BatteryMode.SPIKE_DISCHARGE: {
        "name": "Spike Discharge",
        "icon": "mdi:flash-alert",
    },
    BatteryMode.HOLDING_FOR_SPIKE: {
        "name": "Holding for Spike",
        "icon": "mdi:flash-alert-outline",
    },
    BatteryMode.DEMAND_BLOCK: {
        "name": "Demand Window Block",
        "icon": "mdi:clock-alert",
    },
    BatteryMode.MANUAL: {
        "name": "Manual Override",
        "icon": "mdi:hand-back-right",
    },
}

# -----------------------------------------------------------------------------
# Solar Export Hold Thresholds
# -----------------------------------------------------------------------------

SOLAR_EXPORT_SURPLUS_ENTRY = 1.5  # Surplus ratio to enter solar export hold
SOLAR_EXPORT_SURPLUS_STAY = 1.0   # Surplus ratio to stay in solar export hold

# -----------------------------------------------------------------------------
# Platforms
# -----------------------------------------------------------------------------

PLATFORMS = ["sensor", "binary_sensor", "number", "switch", "button"]
