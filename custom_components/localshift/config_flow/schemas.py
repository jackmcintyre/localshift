"""Schema builders for config flow forms.

This module contains functions to build voluptuous schemas for the
various config flow steps.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.helpers import selector

from ..const import (
    CONF_BATTERY_TARGET,
    CONF_CHEAP_PRICE_DEADBAND,
    CONF_CHEAP_PRICE_PERCENTILE,
    CONF_CLIMATE_CONTROL_ENTITIES,
    CONF_CLIMATE_ENTITIES,
    CONF_COOLING_THRESHOLD,
    CONF_COOLING_TRIGGER_TEMP,
    CONF_DEHUMIDIFY_TRIGGER_HUMIDITY,
    CONF_DEMAND_WINDOW_END,
    CONF_DEMAND_WINDOW_START,
    CONF_EXPORT_PRICE_MARGIN,
    CONF_HEATING_THRESHOLD,
    CONF_HEATING_TRIGGER_TEMP,
    CONF_LOAD_WEIGHT_RECENT,
    CONF_MANUAL_OVERRIDE_TIMEOUT,
    CONF_MAX_PRECHARGE_PRICE,
    CONF_MINIMUM_TARGET_SOC,
    CONF_NOTIFY_SERVICE,
    CONF_PRECONDITION_HOURS_BEFORE_DW,
    CONF_PRECONDITION_TEMP_OFFSET,
    CONF_PRICING_FEED_IN_FORECAST,
    CONF_PRICING_FEED_IN_PRICE,
    CONF_PRICING_GENERAL_FORECAST,
    CONF_PRICING_GENERAL_PRICE,
    CONF_PRICING_PRICE_SPIKE,
    CONF_SOLAR_TAPER_ENABLED,
    CONF_SOLCAST_FORECAST_TODAY,
    CONF_SOLCAST_FORECAST_TOMORROW,
    CONF_SPIKE_PRICE_PERCENTILE,
    CONF_SUN_ENTITY,
    CONF_TAPER_MAX_SETPOINT_OFFSET,
    CONF_TESLEMETRY_BACKUP_RESERVE,
    CONF_TESLEMETRY_BATTERY_POWER,
    CONF_TESLEMETRY_GRID_POWER,
    CONF_TESLEMETRY_LOAD_POWER,
    CONF_TESLEMETRY_OPERATION_MODE,
    CONF_TESLEMETRY_SOC,
    CONF_TESLEMETRY_SOLAR_POWER,
    CONF_THERMAL_MANAGEMENT_ENABLED,
    CONF_THERMAL_MODE_DECISION_TIME,
    CONF_WEATHER_ENTITY,
    CONF_WEATHER_LEARNING_ENABLED,
    DEFAULT_BATTERY_TARGET,
    DEFAULT_CHEAP_PRICE_DEADBAND,
    DEFAULT_CHEAP_PRICE_PERCENTILE,
    DEFAULT_COOLING_THRESHOLD,
    DEFAULT_COOLING_TRIGGER_TEMP,
    DEFAULT_DEHUMIDIFY_TRIGGER_HUMIDITY,
    DEFAULT_DEMAND_WINDOW_END,
    DEFAULT_DEMAND_WINDOW_START,
    DEFAULT_ENTITY_IDS,
    DEFAULT_EXPORT_PRICE_MARGIN,
    DEFAULT_HEATING_THRESHOLD,
    DEFAULT_HEATING_TRIGGER_TEMP,
    DEFAULT_LOAD_WEIGHT_RECENT,
    DEFAULT_MANUAL_OVERRIDE_TIMEOUT,
    DEFAULT_MAX_PRECHARGE_PRICE,
    DEFAULT_MINIMUM_TARGET_SOC,
    DEFAULT_PRECONDITION_HOURS_BEFORE_DW,
    DEFAULT_PRECONDITION_TEMP_OFFSET,
    DEFAULT_SOLAR_TAPER_ENABLED,
    DEFAULT_SPIKE_PRICE_PERCENTILE,
    DEFAULT_TAPER_MAX_SETPOINT_OFFSET,
    DEFAULT_THERMAL_MANAGEMENT_ENABLED,
    DEFAULT_THERMAL_MODE_DECISION_TIME,
    DEFAULT_WEATHER_ENTITY,
    DEFAULT_WEATHER_LEARNING_ENABLED,
    THRESHOLD_RANGES,
)


def build_user_schema(
    defaults: dict[str, str] | None = None,
    errors: dict[str, str] | None = None,
    user_input: dict[str, Any] | None = None,
) -> vol.Schema:
    """Build schema for the user (Teslemetry entity selection) step.

    Args:
        defaults: Default entity IDs to use
        errors: Validation errors to display
        user_input: Previously submitted input to use as defaults

    Returns:
        Voluptuous schema for the user step form
    """
    if defaults is None:
        defaults = DEFAULT_ENTITY_IDS
    if user_input is not None:
        defaults = user_input

    return vol.Schema(
        {
            vol.Required(
                CONF_TESLEMETRY_OPERATION_MODE,
                default=defaults.get(CONF_TESLEMETRY_OPERATION_MODE, ""),
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="select")),
            vol.Required(
                CONF_TESLEMETRY_BACKUP_RESERVE,
                default=defaults.get(CONF_TESLEMETRY_BACKUP_RESERVE, ""),
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="number")),
            vol.Required(
                CONF_TESLEMETRY_SOC,
                default=defaults.get(CONF_TESLEMETRY_SOC, ""),
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
            vol.Required(
                CONF_TESLEMETRY_GRID_POWER,
                default=defaults.get(CONF_TESLEMETRY_GRID_POWER, ""),
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
            vol.Required(
                CONF_TESLEMETRY_BATTERY_POWER,
                default=defaults.get(CONF_TESLEMETRY_BATTERY_POWER, ""),
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
            vol.Required(
                CONF_TESLEMETRY_SOLAR_POWER,
                default=defaults.get(CONF_TESLEMETRY_SOLAR_POWER, ""),
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
            vol.Required(
                CONF_TESLEMETRY_LOAD_POWER,
                default=defaults.get(CONF_TESLEMETRY_LOAD_POWER, ""),
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
        }
    )


def build_pricing_schema(
    defaults: dict[str, str] | None = None,
    errors: dict[str, str] | None = None,
    user_input: dict[str, Any] | None = None,
) -> vol.Schema:
    """Build schema for the pricing entity selection step.

    Args:
        defaults: Default entity IDs to use
        errors: Validation errors to display
        user_input: Previously submitted input to use as defaults

    Returns:
        Voluptuous schema for the pricing step form
    """
    if defaults is None:
        defaults = DEFAULT_ENTITY_IDS
    if user_input is not None:
        defaults = user_input

    return vol.Schema(
        {
            vol.Required(
                CONF_PRICING_GENERAL_PRICE,
                default=defaults.get(CONF_PRICING_GENERAL_PRICE, ""),
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
            vol.Required(
                CONF_PRICING_FEED_IN_PRICE,
                default=defaults.get(CONF_PRICING_FEED_IN_PRICE, ""),
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
            vol.Required(
                CONF_PRICING_GENERAL_FORECAST,
                default=defaults.get(CONF_PRICING_GENERAL_FORECAST, ""),
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
            vol.Required(
                CONF_PRICING_FEED_IN_FORECAST,
                default=defaults.get(CONF_PRICING_FEED_IN_FORECAST, ""),
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
            vol.Required(
                CONF_PRICING_PRICE_SPIKE,
                default=defaults.get(CONF_PRICING_PRICE_SPIKE, ""),
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="binary_sensor")
            ),
        }
    )


def build_solcast_schema(
    notify_services: list[str],
    weather_entities: list[str],
    defaults: dict[str, str] | None = None,
    errors: dict[str, str] | None = None,
    user_input: dict[str, Any] | None = None,
) -> vol.Schema:
    """Build schema for the solcast + notification step.

    Args:
        notify_services: List of available notify services
        weather_entities: List of available weather entities
        defaults: Default entity IDs to use
        errors: Validation errors to display
        user_input: Previously submitted input to use as defaults

    Returns:
        Voluptuous schema for the solcast step form
    """
    if defaults is None:
        defaults = DEFAULT_ENTITY_IDS

    # Determine default values
    if user_input is not None:
        default_notify = user_input.get(CONF_NOTIFY_SERVICE, "")
        default_weather = user_input.get(CONF_WEATHER_ENTITY, DEFAULT_WEATHER_ENTITY)
        default_today = user_input.get(
            CONF_SOLCAST_FORECAST_TODAY, defaults.get(CONF_SOLCAST_FORECAST_TODAY, "")
        )
        default_tomorrow = user_input.get(
            CONF_SOLCAST_FORECAST_TOMORROW,
            defaults.get(CONF_SOLCAST_FORECAST_TOMORROW, ""),
        )
        default_sun = user_input.get(CONF_SUN_ENTITY, defaults.get(CONF_SUN_ENTITY, ""))
    else:
        default_notify = notify_services[0] if notify_services else ""
        default_weather = (
            weather_entities[0] if weather_entities else DEFAULT_WEATHER_ENTITY
        )
        default_today = defaults.get(CONF_SOLCAST_FORECAST_TODAY, "")
        default_tomorrow = defaults.get(CONF_SOLCAST_FORECAST_TOMORROW, "")
        default_sun = defaults.get(CONF_SUN_ENTITY, "")

    return vol.Schema(
        {
            vol.Required(
                CONF_SOLCAST_FORECAST_TODAY,
                default=default_today,
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
            vol.Required(
                CONF_SOLCAST_FORECAST_TOMORROW,
                default=default_tomorrow,
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
            vol.Required(
                CONF_NOTIFY_SERVICE,
                default=default_notify,
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=notify_services,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Required(
                CONF_SUN_ENTITY,
                default=default_sun,
            ): selector.EntitySelector(),
            vol.Optional(
                CONF_WEATHER_ENTITY,
                default=default_weather,
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=weather_entities
                    if weather_entities
                    else [DEFAULT_WEATHER_ENTITY],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
        }
    )


def build_options_schema(
    values: dict[str, Any],
    notify_services: list[str],
    weather_entities: list[str],
    climate_entities: list[str] | None = None,
) -> vol.Schema:
    """Build the options form schema.

    Args:
        values: Current/default values for the form fields
        notify_services: List of available notify services
        weather_entities: List of available weather entities
        climate_entities: List of available climate entities

    Returns:
        Voluptuous schema for the options form
    """
    if climate_entities is None:
        climate_entities = []

    return vol.Schema(
        {
            # Notification settings
            vol.Required(
                CONF_NOTIFY_SERVICE,
                default=values.get(CONF_NOTIFY_SERVICE, ""),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=notify_services,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            # Demand window timing
            vol.Required(
                CONF_DEMAND_WINDOW_START,
                default=values.get(
                    CONF_DEMAND_WINDOW_START,
                    DEFAULT_DEMAND_WINDOW_START,
                ),
            ): selector.TimeSelector(),
            vol.Required(
                CONF_DEMAND_WINDOW_END,
                default=values.get(
                    CONF_DEMAND_WINDOW_END,
                    DEFAULT_DEMAND_WINDOW_END,
                ),
            ): selector.TimeSelector(),
            vol.Required(
                CONF_MANUAL_OVERRIDE_TIMEOUT,
                default=values.get(
                    CONF_MANUAL_OVERRIDE_TIMEOUT,
                    DEFAULT_MANUAL_OVERRIDE_TIMEOUT,
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0,
                    max=24,
                    step=1,
                    unit_of_measurement="hours",
                    mode=selector.NumberSelectorMode.SLIDER,
                )
            ),
            # Price thresholds
            vol.Required(
                CONF_CHEAP_PRICE_PERCENTILE,
                default=values.get(
                    CONF_CHEAP_PRICE_PERCENTILE,
                    DEFAULT_CHEAP_PRICE_PERCENTILE,
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=THRESHOLD_RANGES[CONF_CHEAP_PRICE_PERCENTILE]["min"],
                    max=THRESHOLD_RANGES[CONF_CHEAP_PRICE_PERCENTILE]["max"],
                    step=THRESHOLD_RANGES[CONF_CHEAP_PRICE_PERCENTILE]["step"],
                    unit_of_measurement=THRESHOLD_RANGES[CONF_CHEAP_PRICE_PERCENTILE][
                        "unit"
                    ],
                    mode=selector.NumberSelectorMode.SLIDER,
                )
            ),
            vol.Required(
                CONF_MAX_PRECHARGE_PRICE,
                default=values.get(
                    CONF_MAX_PRECHARGE_PRICE,
                    DEFAULT_MAX_PRECHARGE_PRICE,
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=THRESHOLD_RANGES[CONF_MAX_PRECHARGE_PRICE]["min"],
                    max=THRESHOLD_RANGES[CONF_MAX_PRECHARGE_PRICE]["max"],
                    step=THRESHOLD_RANGES[CONF_MAX_PRECHARGE_PRICE]["step"],
                    unit_of_measurement=THRESHOLD_RANGES[CONF_MAX_PRECHARGE_PRICE][
                        "unit"
                    ],
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_CHEAP_PRICE_DEADBAND,
                default=values.get(
                    CONF_CHEAP_PRICE_DEADBAND,
                    DEFAULT_CHEAP_PRICE_DEADBAND,
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=THRESHOLD_RANGES[CONF_CHEAP_PRICE_DEADBAND]["min"],
                    max=THRESHOLD_RANGES[CONF_CHEAP_PRICE_DEADBAND]["max"],
                    step=THRESHOLD_RANGES[CONF_CHEAP_PRICE_DEADBAND]["step"],
                    unit_of_measurement=THRESHOLD_RANGES[CONF_CHEAP_PRICE_DEADBAND][
                        "unit"
                    ],
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_SPIKE_PRICE_PERCENTILE,
                default=values.get(
                    CONF_SPIKE_PRICE_PERCENTILE,
                    DEFAULT_SPIKE_PRICE_PERCENTILE,
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=THRESHOLD_RANGES[CONF_SPIKE_PRICE_PERCENTILE]["min"],
                    max=THRESHOLD_RANGES[CONF_SPIKE_PRICE_PERCENTILE]["max"],
                    step=THRESHOLD_RANGES[CONF_SPIKE_PRICE_PERCENTILE]["step"],
                    unit_of_measurement=THRESHOLD_RANGES[CONF_SPIKE_PRICE_PERCENTILE][
                        "unit"
                    ],
                    mode=selector.NumberSelectorMode.SLIDER,
                )
            ),
            # Battery settings
            vol.Required(
                CONF_BATTERY_TARGET,
                default=values.get(
                    CONF_BATTERY_TARGET,
                    DEFAULT_BATTERY_TARGET,
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=THRESHOLD_RANGES[CONF_BATTERY_TARGET]["min"],
                    max=THRESHOLD_RANGES[CONF_BATTERY_TARGET]["max"],
                    step=THRESHOLD_RANGES[CONF_BATTERY_TARGET]["step"],
                    unit_of_measurement=THRESHOLD_RANGES[CONF_BATTERY_TARGET]["unit"],
                    mode=selector.NumberSelectorMode.SLIDER,
                )
            ),
            vol.Required(
                CONF_MINIMUM_TARGET_SOC,
                default=values.get(
                    CONF_MINIMUM_TARGET_SOC,
                    DEFAULT_MINIMUM_TARGET_SOC,
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=THRESHOLD_RANGES[CONF_MINIMUM_TARGET_SOC]["min"],
                    max=THRESHOLD_RANGES[CONF_MINIMUM_TARGET_SOC]["max"],
                    step=THRESHOLD_RANGES[CONF_MINIMUM_TARGET_SOC]["step"],
                    unit_of_measurement=THRESHOLD_RANGES[CONF_MINIMUM_TARGET_SOC][
                        "unit"
                    ],
                    mode=selector.NumberSelectorMode.SLIDER,
                )
            ),
            # Advanced settings
            vol.Required(
                CONF_LOAD_WEIGHT_RECENT,
                default=values.get(
                    CONF_LOAD_WEIGHT_RECENT,
                    DEFAULT_LOAD_WEIGHT_RECENT,
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=THRESHOLD_RANGES[CONF_LOAD_WEIGHT_RECENT]["min"],
                    max=THRESHOLD_RANGES[CONF_LOAD_WEIGHT_RECENT]["max"],
                    step=THRESHOLD_RANGES[CONF_LOAD_WEIGHT_RECENT]["step"],
                    unit_of_measurement=THRESHOLD_RANGES[CONF_LOAD_WEIGHT_RECENT][
                        "unit"
                    ],
                    mode=selector.NumberSelectorMode.SLIDER,
                )
            ),
            # Weather correlation settings
            vol.Optional(
                CONF_WEATHER_ENTITY,
                default=values.get(
                    CONF_WEATHER_ENTITY,
                    DEFAULT_WEATHER_ENTITY,
                ),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=weather_entities
                    if weather_entities
                    else [DEFAULT_WEATHER_ENTITY],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Optional(
                CONF_WEATHER_LEARNING_ENABLED,
                default=values.get(
                    CONF_WEATHER_LEARNING_ENABLED,
                    DEFAULT_WEATHER_LEARNING_ENABLED,
                ),
            ): selector.BooleanSelector(),
            vol.Optional(
                CONF_COOLING_THRESHOLD,
                default=values.get(
                    CONF_COOLING_THRESHOLD,
                    DEFAULT_COOLING_THRESHOLD,
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=THRESHOLD_RANGES[CONF_COOLING_THRESHOLD]["min"],
                    max=THRESHOLD_RANGES[CONF_COOLING_THRESHOLD]["max"],
                    step=THRESHOLD_RANGES[CONF_COOLING_THRESHOLD]["step"],
                    unit_of_measurement=THRESHOLD_RANGES[CONF_COOLING_THRESHOLD][
                        "unit"
                    ],
                    mode=selector.NumberSelectorMode.SLIDER,
                )
            ),
            vol.Optional(
                CONF_HEATING_THRESHOLD,
                default=values.get(
                    CONF_HEATING_THRESHOLD,
                    DEFAULT_HEATING_THRESHOLD,
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=THRESHOLD_RANGES[CONF_HEATING_THRESHOLD]["min"],
                    max=THRESHOLD_RANGES[CONF_HEATING_THRESHOLD]["max"],
                    step=THRESHOLD_RANGES[CONF_HEATING_THRESHOLD]["step"],
                    unit_of_measurement=THRESHOLD_RANGES[CONF_HEATING_THRESHOLD][
                        "unit"
                    ],
                    mode=selector.NumberSelectorMode.SLIDER,
                )
            ),
            # Export price margin for arbitrage
            vol.Optional(
                CONF_EXPORT_PRICE_MARGIN,
                default=values.get(
                    CONF_EXPORT_PRICE_MARGIN,
                    DEFAULT_EXPORT_PRICE_MARGIN,
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=THRESHOLD_RANGES[CONF_EXPORT_PRICE_MARGIN]["min"],
                    max=THRESHOLD_RANGES[CONF_EXPORT_PRICE_MARGIN]["max"],
                    step=THRESHOLD_RANGES[CONF_EXPORT_PRICE_MARGIN]["step"],
                    unit_of_measurement=THRESHOLD_RANGES[CONF_EXPORT_PRICE_MARGIN][
                        "unit"
                    ],
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            # Thermal Manager settings (Issue #137, #63)
            vol.Optional(
                CONF_THERMAL_MANAGEMENT_ENABLED,
                default=values.get(
                    CONF_THERMAL_MANAGEMENT_ENABLED,
                    DEFAULT_THERMAL_MANAGEMENT_ENABLED,
                ),
            ): selector.BooleanSelector(),
            vol.Optional(
                CONF_CLIMATE_ENTITIES,
                default=values.get(CONF_CLIMATE_ENTITIES, []),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=climate_entities,
                    multiple=True,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Optional(
                CONF_CLIMATE_CONTROL_ENTITIES,
                default=values.get(CONF_CLIMATE_CONTROL_ENTITIES, []),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=climate_entities,
                    multiple=True,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Optional(
                CONF_COOLING_TRIGGER_TEMP,
                default=values.get(
                    CONF_COOLING_TRIGGER_TEMP,
                    DEFAULT_COOLING_TRIGGER_TEMP,
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=THRESHOLD_RANGES[CONF_COOLING_TRIGGER_TEMP]["min"],
                    max=THRESHOLD_RANGES[CONF_COOLING_TRIGGER_TEMP]["max"],
                    step=THRESHOLD_RANGES[CONF_COOLING_TRIGGER_TEMP]["step"],
                    unit_of_measurement=THRESHOLD_RANGES[CONF_COOLING_TRIGGER_TEMP][
                        "unit"
                    ],
                    mode=selector.NumberSelectorMode.SLIDER,
                )
            ),
            vol.Optional(
                CONF_HEATING_TRIGGER_TEMP,
                default=values.get(
                    CONF_HEATING_TRIGGER_TEMP,
                    DEFAULT_HEATING_TRIGGER_TEMP,
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=THRESHOLD_RANGES[CONF_HEATING_TRIGGER_TEMP]["min"],
                    max=THRESHOLD_RANGES[CONF_HEATING_TRIGGER_TEMP]["max"],
                    step=THRESHOLD_RANGES[CONF_HEATING_TRIGGER_TEMP]["step"],
                    unit_of_measurement=THRESHOLD_RANGES[CONF_HEATING_TRIGGER_TEMP][
                        "unit"
                    ],
                    mode=selector.NumberSelectorMode.SLIDER,
                )
            ),
            vol.Optional(
                CONF_DEHUMIDIFY_TRIGGER_HUMIDITY,
                default=values.get(
                    CONF_DEHUMIDIFY_TRIGGER_HUMIDITY,
                    DEFAULT_DEHUMIDIFY_TRIGGER_HUMIDITY,
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=THRESHOLD_RANGES[CONF_DEHUMIDIFY_TRIGGER_HUMIDITY]["min"],
                    max=THRESHOLD_RANGES[CONF_DEHUMIDIFY_TRIGGER_HUMIDITY]["max"],
                    step=THRESHOLD_RANGES[CONF_DEHUMIDIFY_TRIGGER_HUMIDITY]["step"],
                    unit_of_measurement=THRESHOLD_RANGES[
                        CONF_DEHUMIDIFY_TRIGGER_HUMIDITY
                    ]["unit"],
                    mode=selector.NumberSelectorMode.SLIDER,
                )
            ),
            vol.Optional(
                CONF_SOLAR_TAPER_ENABLED,
                default=values.get(
                    CONF_SOLAR_TAPER_ENABLED,
                    DEFAULT_SOLAR_TAPER_ENABLED,
                ),
            ): selector.BooleanSelector(),
            vol.Optional(
                CONF_PRECONDITION_HOURS_BEFORE_DW,
                default=values.get(
                    CONF_PRECONDITION_HOURS_BEFORE_DW,
                    DEFAULT_PRECONDITION_HOURS_BEFORE_DW,
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=THRESHOLD_RANGES[CONF_PRECONDITION_HOURS_BEFORE_DW]["min"],
                    max=THRESHOLD_RANGES[CONF_PRECONDITION_HOURS_BEFORE_DW]["max"],
                    step=THRESHOLD_RANGES[CONF_PRECONDITION_HOURS_BEFORE_DW]["step"],
                    unit_of_measurement=THRESHOLD_RANGES[
                        CONF_PRECONDITION_HOURS_BEFORE_DW
                    ]["unit"],
                    mode=selector.NumberSelectorMode.SLIDER,
                )
            ),
            vol.Optional(
                CONF_PRECONDITION_TEMP_OFFSET,
                default=values.get(
                    CONF_PRECONDITION_TEMP_OFFSET,
                    DEFAULT_PRECONDITION_TEMP_OFFSET,
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=THRESHOLD_RANGES[CONF_PRECONDITION_TEMP_OFFSET]["min"],
                    max=THRESHOLD_RANGES[CONF_PRECONDITION_TEMP_OFFSET]["max"],
                    step=THRESHOLD_RANGES[CONF_PRECONDITION_TEMP_OFFSET]["step"],
                    unit_of_measurement=THRESHOLD_RANGES[CONF_PRECONDITION_TEMP_OFFSET][
                        "unit"
                    ],
                    mode=selector.NumberSelectorMode.SLIDER,
                )
            ),
            vol.Optional(
                CONF_TAPER_MAX_SETPOINT_OFFSET,
                default=values.get(
                    CONF_TAPER_MAX_SETPOINT_OFFSET,
                    DEFAULT_TAPER_MAX_SETPOINT_OFFSET,
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=THRESHOLD_RANGES[CONF_TAPER_MAX_SETPOINT_OFFSET]["min"],
                    max=THRESHOLD_RANGES[CONF_TAPER_MAX_SETPOINT_OFFSET]["max"],
                    step=THRESHOLD_RANGES[CONF_TAPER_MAX_SETPOINT_OFFSET]["step"],
                    unit_of_measurement=THRESHOLD_RANGES[
                        CONF_TAPER_MAX_SETPOINT_OFFSET
                    ]["unit"],
                    mode=selector.NumberSelectorMode.SLIDER,
                )
            ),
            vol.Optional(
                CONF_THERMAL_MODE_DECISION_TIME,
                default=values.get(
                    CONF_THERMAL_MODE_DECISION_TIME,
                    DEFAULT_THERMAL_MODE_DECISION_TIME,
                ),
            ): selector.TimeSelector(),
        }
    )
