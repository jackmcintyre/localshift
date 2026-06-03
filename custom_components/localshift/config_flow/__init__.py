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
    CONF_COMPARISON_MODE,
    CONF_DEMAND_WINDOW_END,
    CONF_DEMAND_WINDOW_START,
    CONF_FORECAST_LOOKAHEAD_HOURS,
    CONF_MANUAL_OVERRIDE_TIMEOUT,
    CONF_MAX_PRECHARGE_PRICE,
    CONF_MINIMUM_TARGET_SOC,
    CONF_NOTIFY_SERVICE,
    CONF_OPTIMIZATION_MODE,
    CONF_PRICING_DATA_SOURCE,
    CONF_PRICING_FEED_IN_FORECAST,
    CONF_PRICING_FEED_IN_PRICE,
    CONF_PRICING_GENERAL_FORECAST,
    CONF_PRICING_GENERAL_PRICE,
    CONF_PRICING_PRICE_SPIKE,
    CONF_SOLCAST_FORECAST_TODAY,
    CONF_SOLCAST_FORECAST_TOMORROW,
    CONF_TARGET_PENALTY,
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
    DEFAULT_COMPARISON_MODE,
    DEFAULT_DEMAND_WINDOW_END,
    DEFAULT_DEMAND_WINDOW_START,
    DEFAULT_FORECAST_LOOKAHEAD_HOURS,
    DEFAULT_MANUAL_OVERRIDE_TIMEOUT,
    DEFAULT_MAX_PRECHARGE_PRICE,
    DEFAULT_MINIMUM_TARGET_SOC,
    DEFAULT_OPTIMIZATION_MODE,
    DEFAULT_PRICING_DATA_SOURCE,
    DEFAULT_TARGET_PENALTY,
    DEFAULT_WEATHER_ENTITY,
    DEFAULT_WEATHER_LEARNING_ENABLED,
    DOMAIN,
)
from ..pricing import PRICING_SOURCE_AMBER_EXPRESS
from .schemas import (
    build_pricing_schema,
    build_pricing_source_schema,
    build_solcast_schema,
    build_user_schema,
)
from .validators import (
    discover_pricing_entities,
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

    async def _discover_pricing_defaults(self, pricing_source: str) -> dict[str, str]:
        """Discover pricing entity defaults based on pricing source."""
        discovered = await discover_pricing_entities(self.hass, pricing_source)
        # Filter out None values and return only discovered entities
        return {k: v for k, v in discovered.items() if v is not None}

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
            return await self.async_step_pricing_source()

        return self.async_show_form(
            step_id="user",
            data_schema=build_user_schema(),
        )

    async def async_step_pricing_source(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the pricing source selection step."""
        if user_input is not None:
            # Store pricing source config
            self._pricing_source_data = user_input
            return await self.async_step_pricing()

        return self.async_show_form(
            step_id="pricing_source",
            data_schema=build_pricing_source_schema(),
        )

    async def async_step_pricing(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the pricing entity selection step."""
        # Get pricing source from previous step
        pricing_source = (
            self._pricing_source_data.get(
                CONF_PRICING_DATA_SOURCE, DEFAULT_PRICING_DATA_SOURCE
            )
            if hasattr(self, "_pricing_source_data")
            else DEFAULT_PRICING_DATA_SOURCE
        )

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
                CONF_PRICING_PRICE_SPIKE: (
                    user_input[CONF_PRICING_PRICE_SPIKE],
                    "binary_sensor",
                ),
            }

            if pricing_source != PRICING_SOURCE_AMBER_EXPRESS:
                entities_to_validate[CONF_PRICING_GENERAL_FORECAST] = (
                    user_input[CONF_PRICING_GENERAL_FORECAST],
                    "sensor",
                )
                entities_to_validate[CONF_PRICING_FEED_IN_FORECAST] = (
                    user_input[CONF_PRICING_FEED_IN_FORECAST],
                    "sensor",
                )

            errors = await validate_all_entities(self.hass, entities_to_validate)
            if errors:
                return self.async_show_form(
                    step_id="pricing",
                    data_schema=build_pricing_schema(
                        user_input=user_input, pricing_source=pricing_source
                    ),
                    errors=errors,
                )

            # Store pricing config, move to solcast step
            self._pricing_data = dict(user_input)
            if pricing_source == PRICING_SOURCE_AMBER_EXPRESS:
                self._pricing_data.setdefault(CONF_PRICING_GENERAL_FORECAST, "")
                self._pricing_data.setdefault(CONF_PRICING_FEED_IN_FORECAST, "")
            return await self.async_step_solcast()

        # Get discovered defaults or fall back to static defaults
        discovered_defaults = await self._discover_pricing_defaults(pricing_source)
        static_defaults = {
            CONF_PRICING_GENERAL_PRICE: "",
            CONF_PRICING_FEED_IN_PRICE: "",
            CONF_PRICING_PRICE_SPIKE: "",
            CONF_PRICING_GENERAL_FORECAST: "",
            CONF_PRICING_FEED_IN_FORECAST: "",
        }

        # Merge discovered defaults with static defaults (discovered takes precedence)
        merged_defaults = {**static_defaults, **discovered_defaults}

        return self.async_show_form(
            step_id="pricing",
            data_schema=build_pricing_schema(
                defaults=merged_defaults, pricing_source=pricing_source
            ),
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
                **self._pricing_source_data,
                **self._pricing_data,
                CONF_SOLCAST_FORECAST_TODAY: user_input[CONF_SOLCAST_FORECAST_TODAY],
                CONF_SOLCAST_FORECAST_TOMORROW: user_input[
                    CONF_SOLCAST_FORECAST_TOMORROW
                ],
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

    async def _validate_entity_mappings(
        self, entities: dict[str, Any]
    ) -> dict[str, str] | None:
        """Validate entity mappings exist and are available.

        Args:
            entities: Dictionary of {config_key: entity_id}

        Returns:
            None if all valid, or dict of {config_key: error_message}

        """
        errors = {}
        for config_key, entity_id in entities.items():
            # Skip non-entity fields
            if config_key in (
                CONF_NOTIFY_SERVICE,
                CONF_WEATHER_ENTITY,
                CONF_PRICING_DATA_SOURCE,
                CONF_COMPARISON_MODE,
            ):
                continue
            if not entity_id:
                errors[config_key] = "Entity is required"
                continue
            state = self.hass.states.get(entity_id)
            if state is None:
                errors[config_key] = f"Entity '{entity_id}' does not exist"
            elif state.state in ("unavailable", "unknown"):
                errors[config_key] = f"Entity '{entity_id}' is {state.state}"
        return errors if errors else None

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the first step: show menu."""
        return self.async_show_menu(
            step_id="init",
            menu_options={
                "options_pricing_source": "Pricing Source",
                "entity_mappings": "Entity Mappings",
                "settings": "Settings",
                "advanced": "Advanced",
            },
        )

    async def async_step_options_pricing_source(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle pricing source selection for options flow."""
        if user_input is not None:
            new_data = {**self.config_entry.data, **user_input}
            self.hass.config_entries.async_update_entry(
                self.config_entry, data=new_data
            )
            return await self.async_step_entity_mappings()

        current = self.config_entry.data
        defaults = {
            CONF_PRICING_DATA_SOURCE: current.get(
                CONF_PRICING_DATA_SOURCE, DEFAULT_PRICING_DATA_SOURCE
            ),
            CONF_COMPARISON_MODE: current.get(
                CONF_COMPARISON_MODE, DEFAULT_COMPARISON_MODE
            ),
        }

        return self.async_show_form(
            step_id="options_pricing_source",
            data_schema=build_pricing_source_schema(defaults),
            description_placeholders={
                "integration_name": "LocalShift",
            },
        )

    async def async_step_entity_mappings(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage entity mappings - can be reconfigured after setup."""
        errors = {}

        if user_input is not None:
            # Validate all entity mappings
            entity_errors = await self._validate_entity_mappings(user_input)
            if entity_errors:
                errors = entity_errors
            else:
                # Update entry data with new entity mappings
                new_data = {**self.config_entry.data, **user_input}
                self.hass.config_entries.async_update_entry(
                    self.config_entry, data=new_data
                )
                # Move to settings step
                return await self.async_step_settings()

        # Get current entity mappings from data
        current = self.config_entry.data

        # Fetch available services for schema building
        notify_services = await get_notify_services(self.hass)
        weather_entities = await get_weather_entities(self.hass)

        return self.async_show_form(
            step_id="entity_mappings",
            data_schema=self._build_entity_mappings_schema(
                current, notify_services, weather_entities
            ),
            errors=errors,
            description_placeholders={
                "integration_name": "LocalShift",
            },
        )

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage essential settings (notify service, demand window, etc.)."""
        notify_services = await get_notify_services(self.hass)
        weather_entities = await get_weather_entities(self.hass)

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
                    step_id="settings",
                    data_schema=self._build_settings_schema(
                        user_input, notify_services, weather_entities
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
            step_id="settings",
            data_schema=self._build_settings_schema(
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
                    # Weather correlation options
                    CONF_WEATHER_ENTITY: current.get(
                        CONF_WEATHER_ENTITY,
                        DEFAULT_WEATHER_ENTITY,
                    ),
                    CONF_OPTIMIZATION_MODE: current.get(
                        CONF_OPTIMIZATION_MODE,
                        DEFAULT_OPTIMIZATION_MODE,
                    ),
                },
                notify_services,
                weather_entities,
            ),
            description_placeholders={
                "integration_name": "LocalShift",
            },
        )

    def _build_entity_mappings_schema(
        self,
        values: dict[str, Any],
        notify_services: list[str],
        weather_entities: list[str],
    ):
        """Build schema for entity mappings step."""
        import voluptuous as vol

        user_schema = build_user_schema(values)
        pricing_source = values.get(
            CONF_PRICING_DATA_SOURCE,
            DEFAULT_PRICING_DATA_SOURCE,
        )
        pricing_values = {
            k: v
            for k, v in values.items()
            if k
            not in (
                CONF_PRICING_GENERAL_PRICE,
                CONF_PRICING_FEED_IN_PRICE,
                CONF_PRICING_PRICE_SPIKE,
                CONF_PRICING_GENERAL_FORECAST,
                CONF_PRICING_FEED_IN_FORECAST,
            )
        }
        pricing_schema = build_pricing_schema(
            pricing_values, pricing_source=pricing_source
        )
        solcast_schema = build_solcast_schema(
            notify_services, weather_entities, values, include_notify=False
        )

        # Merge schema dicts
        merged_schema = {}
        merged_schema.update(user_schema.schema)
        merged_schema.update(pricing_schema.schema)
        merged_schema.update(solcast_schema.schema)

        return vol.Schema(merged_schema)

    def _build_settings_schema(
        self,
        values: dict[str, Any],
        notify_services: list[str],
        weather_entities: list[str],
    ):
        """Build schema for settings step (essential settings only)."""
        import voluptuous as vol
        from homeassistant.helpers import selector

        return vol.Schema({
            # Notification settings
            vol.Required(
                CONF_NOTIFY_SERVICE,
                default=values.get(CONF_NOTIFY_SERVICE, ""),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=notify_services,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                    custom_value=True,
                )
            ),
            # Demand window timing
            vol.Required(
                CONF_DEMAND_WINDOW_START,
                default=values.get(
                    CONF_DEMAND_WINDOW_START,
                    DEFAULT_DEMAND_WINDOW_START,
                ),
                description="Start of evening peak period (grid imports blocked)",
            ): selector.TimeSelector(),
            vol.Required(
                CONF_DEMAND_WINDOW_END,
                default=values.get(
                    CONF_DEMAND_WINDOW_END,
                    DEFAULT_DEMAND_WINDOW_END,
                ),
                description="End of evening peak period",
            ): selector.TimeSelector(),
            vol.Required(
                CONF_MANUAL_OVERRIDE_TIMEOUT,
                default=values.get(
                    CONF_MANUAL_OVERRIDE_TIMEOUT,
                    DEFAULT_MANUAL_OVERRIDE_TIMEOUT,
                ),
                description="Auto-clear manual mode after this time",
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0,
                    max=24,
                    step=1,
                    unit_of_measurement="hours",
                    mode=selector.NumberSelectorMode.SLIDER,
                )
            ),
            # Weather entity (optional)
            vol.Optional(
                CONF_WEATHER_ENTITY,
                default=values.get(
                    CONF_WEATHER_ENTITY,
                    DEFAULT_WEATHER_ENTITY,
                ),
                description="For solar forecast correlation (optional)",
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=weather_entities
                    if weather_entities
                    else [DEFAULT_WEATHER_ENTITY],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            # Optimization mode
            vol.Optional(
                CONF_OPTIMIZATION_MODE,
                default=values.get(
                    CONF_OPTIMIZATION_MODE,
                    DEFAULT_OPTIMIZATION_MODE,
                ),
                description="Optimizer objective: minimize cost or maximize self-use",
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        {
                            "label": "Self Consumption (maximize solar use)",
                            "value": "self_consumption",
                        },
                        {
                            "label": "Arbitrage (minimize total cost)",
                            "value": "arbitrage",
                        },
                    ],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
        })

    async def async_step_advanced(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage advanced optimizer settings (all number entities)."""
        if user_input is not None:
            merged_options = dict(self.config_entry.options)
            merged_options.update(user_input)
            return self.async_create_entry(data=merged_options)

        current = self.config_entry.options
        return self.async_show_form(
            step_id="advanced",
            data_schema=self._build_advanced_schema({
                CONF_CHEAP_PRICE_PERCENTILE: current.get(
                    CONF_CHEAP_PRICE_PERCENTILE, DEFAULT_CHEAP_PRICE_PERCENTILE
                ),
                CONF_MAX_PRECHARGE_PRICE: current.get(
                    CONF_MAX_PRECHARGE_PRICE, DEFAULT_MAX_PRECHARGE_PRICE
                ),
                CONF_BATTERY_TARGET: current.get(
                    CONF_BATTERY_TARGET, DEFAULT_BATTERY_TARGET
                ),
                CONF_MINIMUM_TARGET_SOC: current.get(
                    CONF_MINIMUM_TARGET_SOC, DEFAULT_MINIMUM_TARGET_SOC
                ),
                CONF_TARGET_PENALTY: current.get(
                    CONF_TARGET_PENALTY, DEFAULT_TARGET_PENALTY
                ),
            }),
            description_placeholders={
                "integration_name": "LocalShift",
            },
        )

    def _build_advanced_schema(self, values: dict[str, Any]):
        """Build schema for all configurable number entities."""
        import voluptuous as vol
        from homeassistant.helpers import selector

        return vol.Schema({
            vol.Required(
                CONF_CHEAP_PRICE_PERCENTILE,
                default=values.get(
                    CONF_CHEAP_PRICE_PERCENTILE, DEFAULT_CHEAP_PRICE_PERCENTILE
                ),
                description="Percentile of near-term prices used as cheap threshold",
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=5,
                    max=50,
                    step=1,
                    unit_of_measurement="%",
                    mode=selector.NumberSelectorMode.SLIDER,
                )
            ),
            vol.Required(
                CONF_MAX_PRECHARGE_PRICE,
                default=values.get(
                    CONF_MAX_PRECHARGE_PRICE, DEFAULT_MAX_PRECHARGE_PRICE
                ),
                description="Maximum price willing to pay for urgent grid charging",
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0.00,
                    max=0.50,
                    step=0.01,
                    unit_of_measurement="$/kWh",
                    mode=selector.NumberSelectorMode.SLIDER,
                )
            ),
            vol.Required(
                CONF_BATTERY_TARGET,
                default=values.get(CONF_BATTERY_TARGET, DEFAULT_BATTERY_TARGET),
                description="Target SOC for demand window",
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=50,
                    max=100,
                    step=5,
                    unit_of_measurement="%",
                    mode=selector.NumberSelectorMode.SLIDER,
                )
            ),
            vol.Required(
                CONF_MINIMUM_TARGET_SOC,
                default=values.get(CONF_MINIMUM_TARGET_SOC, DEFAULT_MINIMUM_TARGET_SOC),
                description="Minimum SOC maintained during discharge modes",
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=5,
                    max=30,
                    step=1,
                    unit_of_measurement="%",
                    mode=selector.NumberSelectorMode.SLIDER,
                )
            ),
            vol.Required(
                CONF_TARGET_PENALTY,
                default=values.get(CONF_TARGET_PENALTY, DEFAULT_TARGET_PENALTY),
                description="Penalty per % below demand window target SOC",
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0.000,
                    max=0.100,
                    step=0.005,
                    unit_of_measurement="$/%-point",
                    mode=selector.NumberSelectorMode.SLIDER,
                )
            ),
        })
