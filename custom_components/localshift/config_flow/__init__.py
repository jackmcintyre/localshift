"""Config flow for the LocalShift integration.

This module provides the config flow and options flow classes for setting
up and configuring the LocalShift integration. Helper functions and schema
builders are imported from separate modules for maintainability.
"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from ..const import (
    CONF_ALLOW_DW_ENTRY_UNDER_TARGET,
    CONF_BATTERY_TARGET,
    CONF_CHEAP_PRICE_DEADBAND,
    CONF_CHEAP_PRICE_PERCENTILE,
    CONF_DEMAND_WINDOW_END,
    CONF_DEMAND_WINDOW_START,
    CONF_EXPORT_PRICE_MARGIN,
    CONF_FORECAST_LOOKAHEAD_HOURS,
    CONF_MANUAL_OVERRIDE_TIMEOUT,
    CONF_MAX_PRECHARGE_PRICE,
    CONF_MINIMUM_TARGET_SOC,
    CONF_NOTIFY_SERVICE,
    CONF_OPTIMIZATION_MODE,
    CONF_PRICING_FEED_IN_FORECAST,
    CONF_PRICING_FEED_IN_PRICE,
    CONF_PRICING_GENERAL_FORECAST,
    CONF_PRICING_GENERAL_PRICE,
    CONF_PRICING_PRICE_SPIKE,
    CONF_SOLCAST_FORECAST_TODAY,
    CONF_SOLCAST_FORECAST_TOMORROW,
    CONF_SUN_ENTITY,
    CONF_TESLEMETRY_BACKUP_RESERVE,
    CONF_TESLEMETRY_BATTERY_POWER,
    CONF_TESLEMETRY_GRID_POWER,
    CONF_TESLEMETRY_LOAD_POWER,
    CONF_TESLEMETRY_OPERATION_MODE,
    CONF_TESLEMETRY_SOC,
    CONF_TESLEMETRY_SOLAR_POWER,
    CONF_WEATHER_ENTITY,
    CONF_WEATHER_LEARNING_ENABLED,
    DEFAULT_ALLOW_DW_ENTRY_UNDER_TARGET,
    DEFAULT_BATTERY_TARGET,
    DEFAULT_CHEAP_PRICE_DEADBAND,
    DEFAULT_CHEAP_PRICE_PERCENTILE,
    DEFAULT_DEMAND_WINDOW_END,
    DEFAULT_DEMAND_WINDOW_START,
    DEFAULT_EXPORT_PRICE_MARGIN,
    DEFAULT_FORECAST_LOOKAHEAD_HOURS,
    DEFAULT_MANUAL_OVERRIDE_TIMEOUT,
    DEFAULT_MAX_PRECHARGE_PRICE,
    DEFAULT_MINIMUM_TARGET_SOC,
    DEFAULT_OPTIMIZATION_MODE,
    DEFAULT_WEATHER_ENTITY,
    DEFAULT_WEATHER_LEARNING_ENABLED,
    DOMAIN,
)
from .schemas import (
    build_options_schema,
    build_pricing_schema,
    build_solcast_schema,
    build_user_schema,
)
from .validators import (
    get_climate_entities,
    get_current_notify_service,
    get_notify_services,
    get_weather_entities,
    validate_all_entities,
    validate_notify_service,
)


class LocalShiftConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for LocalShift."""

    VERSION = 1

    async def _validate_entities(
        self, entities: dict[str, tuple[str, str]]
    ) -> dict[str, str] | None:
        """Validate entities exist, are available, and have correct domains."""
        return await validate_all_entities(self.hass, entities)

    async def _validate_notify_service(self, notify_service: str) -> str | None:
        """Validate that a notify service exists."""
        return await validate_notify_service(self.hass, notify_service)

    async def _get_notify_services(self) -> list[str]:
        """Get list of available notify services."""
        return await get_notify_services(self.hass)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the Teslemetry entity selection step."""
        if user_input is not None:
            # Validate entities
            entities_to_validate = {
                CONF_TESLEMETRY_OPERATION_MODE: (
                    user_input[CONF_TESLEMETRY_OPERATION_MODE],
                    "select",
                ),
                CONF_TESLEMETRY_BACKUP_RESERVE: (
                    user_input[CONF_TESLEMETRY_BACKUP_RESERVE],
                    "number",
                ),
                CONF_TESLEMETRY_SOC: (
                    user_input[CONF_TESLEMETRY_SOC],
                    "sensor",
                ),
                CONF_TESLEMETRY_GRID_POWER: (
                    user_input[CONF_TESLEMETRY_GRID_POWER],
                    "sensor",
                ),
                CONF_TESLEMETRY_BATTERY_POWER: (
                    user_input[CONF_TESLEMETRY_BATTERY_POWER],
                    "sensor",
                ),
                CONF_TESLEMETRY_SOLAR_POWER: (
                    user_input[CONF_TESLEMETRY_SOLAR_POWER],
                    "sensor",
                ),
                CONF_TESLEMETRY_LOAD_POWER: (
                    user_input[CONF_TESLEMETRY_LOAD_POWER],
                    "sensor",
                ),
            }

            errors = await validate_all_entities(self.hass, entities_to_validate)
            if errors:
                return self.async_show_form(
                    step_id="user",
                    data_schema=build_user_schema(user_input=user_input),
                    errors=errors,
                )

            # Store teslemetry config, move to pricing step
            self._teslemetry_data = user_input
            return await self.async_step_pricing()

        return self.async_show_form(
            step_id="user",
            data_schema=build_user_schema(),
        )

    async def async_step_pricing(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the pricing entity selection step."""
        if user_input is not None:
            # Validate entities
            entities_to_validate = {
                CONF_PRICING_GENERAL_PRICE: (
                    user_input[CONF_PRICING_GENERAL_PRICE],
                    "sensor",
                ),
                CONF_PRICING_FEED_IN_PRICE: (
                    user_input[CONF_PRICING_FEED_IN_PRICE],
                    "sensor",
                ),
                CONF_PRICING_GENERAL_FORECAST: (
                    user_input[CONF_PRICING_GENERAL_FORECAST],
                    "sensor",
                ),
                CONF_PRICING_FEED_IN_FORECAST: (
                    user_input[CONF_PRICING_FEED_IN_FORECAST],
                    "sensor",
                ),
                CONF_PRICING_PRICE_SPIKE: (
                    user_input[CONF_PRICING_PRICE_SPIKE],
                    "binary_sensor",
                ),
            }

            errors = await validate_all_entities(self.hass, entities_to_validate)
            if errors:
                return self.async_show_form(
                    step_id="pricing",
                    data_schema=build_pricing_schema(user_input=user_input),
                    errors=errors,
                )

            # Store pricing config, move to solcast step
            self._pricing_data = user_input
            return await self.async_step_solcast()

        return self.async_show_form(
            step_id="pricing",
            data_schema=build_pricing_schema(),
        )

    async def async_step_solcast(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the Solcast + notification entity selection step."""
        # Get available notify services dynamically
        notify_services = await get_notify_services(self.hass)

        if user_input is not None:
            # Validate entities and notify service
            entities_to_validate = {
                CONF_SOLCAST_FORECAST_TODAY: (
                    user_input[CONF_SOLCAST_FORECAST_TODAY],
                    "sensor",
                ),
                CONF_SOLCAST_FORECAST_TOMORROW: (
                    user_input[CONF_SOLCAST_FORECAST_TOMORROW],
                    "sensor",
                ),
                CONF_SUN_ENTITY: (
                    user_input[CONF_SUN_ENTITY],
                    "sun",
                ),
            }

            errors = await validate_all_entities(self.hass, entities_to_validate) or {}

            # Validate notify service
            notify_error = await validate_notify_service(
                self.hass, user_input[CONF_NOTIFY_SERVICE]
            )
            if notify_error:
                errors[CONF_NOTIFY_SERVICE] = notify_error

            if errors:
                weather_entities = await get_weather_entities(self.hass)
                return self.async_show_form(
                    step_id="solcast",
                    data_schema=build_solcast_schema(
                        notify_services=notify_services,
                        weather_entities=weather_entities,
                        user_input=user_input,
                    ),
                    errors=errors,
                )

            # Combine all data and create entry
            # Note: notify_service goes into options, not data, so it can be changed later
            all_data = {
                **self._teslemetry_data,
                **self._pricing_data,
                CONF_SOLCAST_FORECAST_TODAY: user_input[CONF_SOLCAST_FORECAST_TODAY],
                CONF_SOLCAST_FORECAST_TOMORROW: user_input[
                    CONF_SOLCAST_FORECAST_TOMORROW
                ],
                CONF_SUN_ENTITY: user_input[CONF_SUN_ENTITY],
            }
            # Set default options (includes notify_service for configurability)
            options = {
                CONF_NOTIFY_SERVICE: user_input[CONF_NOTIFY_SERVICE],
                CONF_CHEAP_PRICE_PERCENTILE: DEFAULT_CHEAP_PRICE_PERCENTILE,
                CONF_MAX_PRECHARGE_PRICE: DEFAULT_MAX_PRECHARGE_PRICE,
                CONF_CHEAP_PRICE_DEADBAND: DEFAULT_CHEAP_PRICE_DEADBAND,
                CONF_FORECAST_LOOKAHEAD_HOURS: DEFAULT_FORECAST_LOOKAHEAD_HOURS,
                CONF_BATTERY_TARGET: DEFAULT_BATTERY_TARGET,
                CONF_DEMAND_WINDOW_START: DEFAULT_DEMAND_WINDOW_START,
                CONF_DEMAND_WINDOW_END: DEFAULT_DEMAND_WINDOW_END,
                CONF_MANUAL_OVERRIDE_TIMEOUT: DEFAULT_MANUAL_OVERRIDE_TIMEOUT,
                CONF_MINIMUM_TARGET_SOC: DEFAULT_MINIMUM_TARGET_SOC,
                CONF_ALLOW_DW_ENTRY_UNDER_TARGET: DEFAULT_ALLOW_DW_ENTRY_UNDER_TARGET,
                # Weather correlation options
                CONF_WEATHER_ENTITY: user_input.get(
                    CONF_WEATHER_ENTITY, DEFAULT_WEATHER_ENTITY
                ),
                CONF_WEATHER_LEARNING_ENABLED: DEFAULT_WEATHER_LEARNING_ENABLED,
            }

            return self.async_create_entry(
                title="LocalShift",
                data=all_data,
                options=options,
            )

        # Get available weather entities
        weather_entities = await get_weather_entities(self.hass)

        return self.async_show_form(
            step_id="solcast",
            data_schema=build_solcast_schema(
                notify_services=notify_services,
                weather_entities=weather_entities,
            ),
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow for this handler."""
        return LocalShiftOptionsFlow()


class LocalShiftOptionsFlow(OptionsFlow):
    """Handle options flow for LocalShift."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the thresholds and timing options."""
        # Get available notify services, weather entities, and climate entities
        notify_services = await get_notify_services(self.hass)
        weather_entities = await get_weather_entities(self.hass)
        climate_entities = await get_climate_entities(self.hass)

        if user_input is not None:
            # Validate notify service
            errors = {}
            notify_error = await validate_notify_service(
                self.hass, user_input[CONF_NOTIFY_SERVICE]
            )
            if notify_error:
                errors[CONF_NOTIFY_SERVICE] = notify_error

            if errors:
                return self.async_show_form(
                    step_id="init",
                    data_schema=build_options_schema(
                        user_input, notify_services, weather_entities, climate_entities
                    ),
                    errors=errors,
                )

            # Merge with existing options to preserve other settings
            merged_options = dict(self.config_entry.options)
            merged_options.update(user_input)
            return self.async_create_entry(data=merged_options)

        current = self.config_entry.options
        current_notify = get_current_notify_service(self.config_entry)

        return self.async_show_form(
            step_id="init",
            data_schema=build_options_schema(
                {
                    CONF_NOTIFY_SERVICE: current_notify,
                    CONF_DEMAND_WINDOW_START: current.get(
                        CONF_DEMAND_WINDOW_START,
                        DEFAULT_DEMAND_WINDOW_START,
                    ),
                    CONF_DEMAND_WINDOW_END: current.get(
                        CONF_DEMAND_WINDOW_END,
                        DEFAULT_DEMAND_WINDOW_END,
                    ),
                    CONF_MANUAL_OVERRIDE_TIMEOUT: current.get(
                        CONF_MANUAL_OVERRIDE_TIMEOUT,
                        DEFAULT_MANUAL_OVERRIDE_TIMEOUT,
                    ),
                    CONF_CHEAP_PRICE_PERCENTILE: current.get(
                        CONF_CHEAP_PRICE_PERCENTILE,
                        DEFAULT_CHEAP_PRICE_PERCENTILE,
                    ),
                    CONF_MAX_PRECHARGE_PRICE: current.get(
                        CONF_MAX_PRECHARGE_PRICE,
                        DEFAULT_MAX_PRECHARGE_PRICE,
                    ),
                    CONF_CHEAP_PRICE_DEADBAND: current.get(
                        CONF_CHEAP_PRICE_DEADBAND,
                        DEFAULT_CHEAP_PRICE_DEADBAND,
                    ),
                    CONF_BATTERY_TARGET: current.get(
                        CONF_BATTERY_TARGET,
                        DEFAULT_BATTERY_TARGET,
                    ),
                    CONF_MINIMUM_TARGET_SOC: current.get(
                        CONF_MINIMUM_TARGET_SOC,
                        DEFAULT_MINIMUM_TARGET_SOC,
                    ),
                    # Weather correlation options
                    CONF_WEATHER_ENTITY: current.get(
                        CONF_WEATHER_ENTITY,
                        DEFAULT_WEATHER_ENTITY,
                    ),
                    CONF_WEATHER_LEARNING_ENABLED: current.get(
                        CONF_WEATHER_LEARNING_ENABLED,
                        DEFAULT_WEATHER_LEARNING_ENABLED,
                    ),
                    # Export price margin
                    CONF_EXPORT_PRICE_MARGIN: current.get(
                        CONF_EXPORT_PRICE_MARGIN,
                        DEFAULT_EXPORT_PRICE_MARGIN,
                    ),
                    # Optimization mode (Issue #406)
                    CONF_OPTIMIZATION_MODE: current.get(
                        CONF_OPTIMIZATION_MODE,
                        DEFAULT_OPTIMIZATION_MODE,
                    ),
                },
                notify_services,
                weather_entities,
                climate_entities,
            ),
        )
