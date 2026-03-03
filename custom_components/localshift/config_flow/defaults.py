"""Default values and constants for config flow.

This module contains default entity IDs, default option values, and
threshold ranges used throughout the config flow.
"""

from __future__ import annotations

from typing import Any

# Default entity IDs for common configurations
DEFAULT_ENTITY_IDS: dict[str, str] = {
    "teslemetry_operation_mode": "select.tesla_powerwall_operation_mode",
    "teslemetry_backup_reserve": "number.tesla_powerwall_backup_reserve",
    "teslemetry_soc": "sensor.tesla_powerwall_soc",
    "teslemetry_grid_power": "sensor.tesla_powerwall_grid_power",
    "teslemetry_battery_power": "sensor.tesla_powerwall_battery_power",
    "teslemetry_solar_power": "sensor.tesla_powerwall_solar_power",
    "teslemetry_load_power": "sensor.tesla_powerwall_load_power",
    "pricing_general_price": "sensor.amber_general_price",
    "pricing_feed_in_price": "sensor.amber_feed_in_price",
    "pricing_general_forecast": "sensor.amber_general_forecast",
    "pricing_feed_in_forecast": "sensor.amber_feed_in_forecast",
    "pricing_price_spike": "binary_sensor.amber_price_spike",
    "solcast_forecast_today": "sensor.solcast_forecast_today",
    "solcast_forecast_tomorrow": "sensor.solcast_forecast_tomorrow",
    "weather_entity": "",  # No default - user must configure
}

# Default option values
DEFAULT_CHEAP_PRICE_PERCENTILE = 15.0
DEFAULT_MAX_PRECHARGE_PRICE = 0.20
DEFAULT_CHEAP_PRICE_DEADBAND = 0.02
DEFAULT_FORECAST_LOOKAHEAD_HOURS = 24
DEFAULT_BATTERY_TARGET = 90.0
DEFAULT_DEMAND_WINDOW_START = "14:00:00"
DEFAULT_DEMAND_WINDOW_END = "20:00:00"
DEFAULT_MANUAL_OVERRIDE_TIMEOUT = 4
DEFAULT_LOAD_WEIGHT_RECENT = 0.7
DEFAULT_MINIMUM_TARGET_SOC = 20.0
DEFAULT_ALLOW_DW_ENTRY_UNDER_TARGET = True
DEFAULT_WEATHER_ENTITY = ""  # No default - user must configure
DEFAULT_WEATHER_LEARNING_ENABLED = False
DEFAULT_COOLING_THRESHOLD = 28.0
DEFAULT_HEATING_THRESHOLD = 18.0
DEFAULT_EXPORT_PRICE_MARGIN = 0.05
DEFAULT_THERMAL_MANAGEMENT_ENABLED = False
DEFAULT_COOLING_TRIGGER_TEMP = 24.0
DEFAULT_HEATING_TRIGGER_TEMP = 18.0
DEFAULT_DEHUMIDIFY_TRIGGER_HUMIDITY = 70.0
DEFAULT_SOLAR_TAPER_ENABLED = False
DEFAULT_PRECONDITION_HOURS_BEFORE_DW = 2
DEFAULT_PRECONDITION_TEMP_OFFSET = 2.0
DEFAULT_TAPER_MAX_SETPOINT_OFFSET = 3.0
DEFAULT_THERMAL_MODE_DECISION_TIME = "12:00:00"

# Threshold ranges for numeric options
THRESHOLD_RANGES: dict[str, dict[str, Any]] = {
    "cheap_price_percentile": {
        "min": 0.0,
        "max": 50.0,
        "step": 1.0,
        "unit": "%",
    },
    "max_precharge_price": {
        "min": 0.0,
        "max": 2.0,
        "step": 0.01,
        "unit": "$/kWh",
    },
    "cheap_price_deadband": {
        "min": 0.0,
        "max": 0.50,
        "step": 0.01,
        "unit": "$/kWh",
    },
    "spike_price_percentile": {
        "min": 50.0,
        "max": 100.0,
        "step": 1.0,
        "unit": "%",
    },
    "battery_target": {
        "min": 0.0,
        "max": 100.0,
        "step": 5.0,
        "unit": "%",
    },
    "minimum_target_soc": {
        "min": 0.0,
        "max": 100.0,
        "step": 5.0,
        "unit": "%",
    },
    "load_weight_recent": {
        "min": 0.0,
        "max": 1.0,
        "step": 0.1,
        "unit": "weight",
    },
    "cooling_threshold": {
        "min": 15.0,
        "max": 40.0,
        "step": 0.5,
        "unit": "°C",
    },
    "heating_threshold": {
        "min": 5.0,
        "max": 30.0,
        "step": 0.5,
        "unit": "°C",
    },
    "export_price_margin": {
        "min": 0.0,
        "max": 1.0,
        "step": 0.01,
        "unit": "$/kWh",
    },
    "cooling_trigger_temp": {
        "min": 18.0,
        "max": 32.0,
        "step": 0.5,
        "unit": "°C",
    },
    "heating_trigger_temp": {
        "min": 10.0,
        "max": 26.0,
        "step": 0.5,
        "unit": "°C",
    },
    "dehumidify_trigger_humidity": {
        "min": 40.0,
        "max": 90.0,
        "step": 5.0,
        "unit": "%",
    },
    "precondition_hours_before_dw": {
        "min": 0,
        "max": 6,
        "step": 1,
        "unit": "hours",
    },
    "precondition_temp_offset": {
        "min": 0.0,
        "max": 5.0,
        "step": 0.5,
        "unit": "°C",
    },
    "taper_max_setpoint_offset": {
        "min": 0.0,
        "max": 10.0,
        "step": 0.5,
        "unit": "°C",
    },
}
