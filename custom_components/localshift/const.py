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


class ThermalMode(StrEnum):
    """Daily thermal operating mode for HVAC automation."""

    OFF = "off"  # No thermal automation needed (mild day)
    COOL = "cool"  # Cooling mode (hot day)
    HEAT = "heat"  # Heating mode (cold day)
    DRY = "dry"  # Dehumidification mode (humid day)

    @property
    def display_name(self) -> str:
        """Return a user-friendly display name for the mode."""
        names = {
            ThermalMode.OFF: "Off",
            ThermalMode.COOL: "Cooling",
            ThermalMode.HEAT: "Heating",
            ThermalMode.DRY: "Dehumidify",
        }
        return names[self]


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
DEFAULT_WEATHER_ENTITY = "weather.home"

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
# Thermal Manager Configuration (Issue #137, #63)
# -----------------------------------------------------------------------------

# Climate entity configuration
CONF_CLIMATE_ENTITIES = "climate_entities"  # All climate entities (monitored)
CONF_CLIMATE_CONTROL_ENTITIES = "climate_control_entities"  # Subset to control

# Thermal management switches
CONF_THERMAL_MANAGEMENT_ENABLED = "thermal_management_enabled"
CONF_SOLAR_TAPER_ENABLED = "solar_taper_enabled"

# Mode determination thresholds
CONF_COOLING_TRIGGER_TEMP = "cooling_trigger_temp"
CONF_HEATING_TRIGGER_TEMP = "heating_trigger_temp"
CONF_DEHUMIDIFY_TRIGGER_HUMIDITY = "dehumidify_trigger_humidity"
CONF_THERMAL_MODE_DECISION_TIME = "thermal_mode_decision_time"

# Pre-conditioning settings
CONF_PRECONDITION_HOURS_BEFORE_DW = "precondition_hours_before_dw"
CONF_PRECONDITION_TEMP_OFFSET = "precondition_temp_offset"

# Solar tapering settings
CONF_TAPER_MAX_SETPOINT_OFFSET = "taper_max_setpoint_offset"

# Defaults for thermal manager
DEFAULT_THERMAL_MANAGEMENT_ENABLED = False
DEFAULT_SOLAR_TAPER_ENABLED = True
DEFAULT_COOLING_TRIGGER_TEMP = 28.0  # °C - above this, commit to COOL mode
DEFAULT_HEATING_TRIGGER_TEMP = 15.0  # °C - below this, commit to HEAT mode
DEFAULT_DEHUMIDIFY_TRIGGER_HUMIDITY = 70.0  # % - above this, commit to DRY mode
DEFAULT_THERMAL_MODE_DECISION_TIME = "06:00"
DEFAULT_PRECONDITION_HOURS_BEFORE_DW = 1.0
DEFAULT_PRECONDITION_TEMP_OFFSET = 2.0
DEFAULT_TAPER_MAX_SETPOINT_OFFSET = 3.0

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
DEFAULT_LOAD_WEIGHT_RECENT = 0.67  # 2/3 weighting to recent usage
DEFAULT_EXPORT_MIN_SPREAD = 0.10  # $/kWh minimum spread to export (buy - sell)
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
    # Thermal manager thresholds
    CONF_COOLING_TRIGGER_TEMP: {
        "min": 20.0,
        "max": 35.0,
        "step": 0.5,
        "unit": "°C",
        "icon": "mdi:thermometer-high",
    },
    CONF_HEATING_TRIGGER_TEMP: {
        "min": 5.0,
        "max": 20.0,
        "step": 0.5,
        "unit": "°C",
        "icon": "mdi:thermometer-low",
    },
    CONF_DEHUMIDIFY_TRIGGER_HUMIDITY: {
        "min": 50.0,
        "max": 90.0,
        "step": 5.0,
        "unit": "%",
        "icon": "mdi:water-percent",
    },
    CONF_PRECONDITION_HOURS_BEFORE_DW: {
        "min": 0.5,
        "max": 4.0,
        "step": 0.5,
        "unit": "hours",
        "icon": "mdi:clock-start",
    },
    CONF_PRECONDITION_TEMP_OFFSET: {
        "min": 0.5,
        "max": 5.0,
        "step": 0.5,
        "unit": "°C",
        "icon": "mdi:thermometer",
    },
    CONF_TAPER_MAX_SETPOINT_OFFSET: {
        "min": 1.0,
        "max": 6.0,
        "step": 0.5,
        "unit": "°C",
        "icon": "mdi:tune-vertical",
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

# Thermal management switches (Issue #137)
SWITCH_THERMAL_MANAGEMENT_ENABLED = "thermal_management_enabled"
SWITCH_SOLAR_TAPER_ENABLED = "solar_taper_enabled"

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
    SWITCH_THERMAL_MANAGEMENT_ENABLED: False,
    SWITCH_SOLAR_TAPER_ENABLED: True,
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
    SWITCH_THERMAL_MANAGEMENT_ENABLED: "mdi:air-conditioner",
    SWITCH_SOLAR_TAPER_ENABLED: "mdi:solar-power",
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
    SWITCH_THERMAL_MANAGEMENT_ENABLED: "Thermal Management",
    SWITCH_SOLAR_TAPER_ENABLED: "Solar Taper",
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
