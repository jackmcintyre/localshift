"""Schema builders for config flow forms.

This module contains functions to build voluptuous schemas for the
various config flow steps.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.helpers import selector

from ..const import (
    CONF_NOTIFY_SERVICE,
    CONF_PRICING_FEED_IN_FORECAST,
    CONF_PRICING_FEED_IN_PRICE,
    CONF_PRICING_GENERAL_FORECAST,
    CONF_PRICING_GENERAL_PRICE,
    CONF_PRICING_PRICE_SPIKE,
    CONF_SOLCAST_FORECAST_TODAY,
    CONF_SOLCAST_FORECAST_TOMORROW,
    CONF_TESLEMETRY_BACKUP_RESERVE,
    CONF_TESLEMETRY_BATTERY_POWER,
    CONF_TESLEMETRY_GRID_POWER,
    CONF_TESLEMETRY_LOAD_POWER,
    CONF_TESLEMETRY_OPERATION_MODE,
    CONF_TESLEMETRY_SOC,
    CONF_TESLEMETRY_SOLAR_POWER,
    CONF_WEATHER_ENTITY,
    DEFAULT_ENTITY_IDS,
    DEFAULT_WEATHER_ENTITY,
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

    return vol.Schema({
        vol.Required(
            CONF_TESLEMETRY_OPERATION_MODE,
            default=defaults.get(CONF_TESLEMETRY_OPERATION_MODE, ""),
            description="Current battery mode (e.g., self_consumption, grid_charging)",
        ): selector.EntitySelector(selector.EntitySelectorConfig(domain="select")),
        vol.Required(
            CONF_TESLEMETRY_BACKUP_RESERVE,
            default=defaults.get(CONF_TESLEMETRY_BACKUP_RESERVE, ""),
            description="Backup reserve setting (percentage)",
        ): selector.EntitySelector(selector.EntitySelectorConfig(domain="number")),
        vol.Required(
            CONF_TESLEMETRY_SOC,
            default=defaults.get(CONF_TESLEMETRY_SOC, ""),
            description="Battery state of charge",
        ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
        vol.Required(
            CONF_TESLEMETRY_GRID_POWER,
            default=defaults.get(CONF_TESLEMETRY_GRID_POWER, ""),
            description="Grid import/export power",
        ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
        vol.Required(
            CONF_TESLEMETRY_BATTERY_POWER,
            default=defaults.get(CONF_TESLEMETRY_BATTERY_POWER, ""),
            description="Battery charge/discharge power",
        ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
        vol.Required(
            CONF_TESLEMETRY_SOLAR_POWER,
            default=defaults.get(CONF_TESLEMETRY_SOLAR_POWER, ""),
            description="Solar production power",
        ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
        vol.Required(
            CONF_TESLEMETRY_LOAD_POWER,
            default=defaults.get(CONF_TESLEMETRY_LOAD_POWER, ""),
            description="Home load power",
        ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
    })


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

    return vol.Schema({
        vol.Required(
            CONF_PRICING_GENERAL_PRICE,
            default=defaults.get(CONF_PRICING_GENERAL_PRICE, ""),
            description="Grid import price ($/kWh)",
        ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
        vol.Required(
            CONF_PRICING_FEED_IN_PRICE,
            default=defaults.get(CONF_PRICING_FEED_IN_PRICE, ""),
            description="Solar export/feed-in price ($/kWh)",
        ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
        vol.Required(
            CONF_PRICING_GENERAL_FORECAST,
            default=defaults.get(CONF_PRICING_GENERAL_FORECAST, ""),
            description="Grid import price forecast",
        ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
        vol.Required(
            CONF_PRICING_FEED_IN_FORECAST,
            default=defaults.get(CONF_PRICING_FEED_IN_FORECAST, ""),
            description="Feed-in price forecast",
        ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
        vol.Required(
            CONF_PRICING_PRICE_SPIKE,
            default=defaults.get(CONF_PRICING_PRICE_SPIKE, ""),
            description="Price spike alert (binary sensor)",
        ): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="binary_sensor")
        ),
    })


def build_solcast_schema(
    notify_services: list[str],
    weather_entities: list[str],
    defaults: dict[str, str] | None = None,
    errors: dict[str, str] | None = None,
    user_input: dict[str, Any] | None = None,
    include_notify: bool = True,
) -> vol.Schema:
    """Build schema for the solcast + notification step.

    Args:
        notify_services: List of available notify services
        weather_entities: List of available weather entities
        defaults: Default entity IDs to use
        errors: Validation errors to display
        user_input: Previously submitted input to use as defaults
        include_notify: Whether to include notify_service field (default True)

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
    else:
        default_notify = notify_services[0] if notify_services else ""
        default_weather = (
            weather_entities[0] if weather_entities else DEFAULT_WEATHER_ENTITY
        )
        default_today = defaults.get(CONF_SOLCAST_FORECAST_TODAY, "")
        default_tomorrow = defaults.get(CONF_SOLCAST_FORECAST_TOMORROW, "")

    # Build schema fields
    schema_fields = {
        vol.Required(
            CONF_SOLCAST_FORECAST_TODAY,
            default=default_today,
            description="Today's solar forecast (kWh)",
        ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
        vol.Required(
            CONF_SOLCAST_FORECAST_TOMORROW,
            default=default_tomorrow,
            description="Tomorrow's solar forecast (kWh)",
        ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
        vol.Optional(
            CONF_WEATHER_ENTITY,
            default=default_weather,
            description="Weather entity for cloud cover correlation (optional)",
        ): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=weather_entities
                if weather_entities
                else [DEFAULT_WEATHER_ENTITY],
                mode=selector.SelectSelectorMode.DROPDOWN,
            )
        ),
    }

    # Optionally add notify_service field
    if include_notify:
        schema_fields[
            vol.Required(
                CONF_NOTIFY_SERVICE,
                default=default_notify,
            )
        ] = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=notify_services,
                mode=selector.SelectSelectorMode.DROPDOWN,
            )
        )

    return vol.Schema(schema_fields)
