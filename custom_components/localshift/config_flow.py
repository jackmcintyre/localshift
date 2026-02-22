"""Config flow for the LocalShift integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from .const import (
    CONF_ALLOW_DW_ENTRY_UNDER_TARGET,
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
    CONF_FORECAST_LOOKAHEAD_HOURS,
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
    DEFAULT_ALLOW_DW_ENTRY_UNDER_TARGET,
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
    DEFAULT_FORECAST_LOOKAHEAD_HOURS,
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
    DOMAIN,
    THRESHOLD_RANGES,
)


class LocalShiftConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for LocalShift."""

    VERSION = 1

    async def _validate_entities(
        self, entities: dict[str, tuple[str, str]]
    ) -> dict[str, str] | None:
        """Validate entities exist, are available, and have correct domains.

        Args:
            entities: Dictionary of {config_key: (entity_id, expected_domain)}

        Returns:
            None if all valid, or dict of {config_key: error_message}
        """
        errors = {}
        for config_key, (entity_id, expected_domain) in entities.items():
            state = self.hass.states.get(entity_id)
            if state is None:
                errors[config_key] = f"Entity '{entity_id}' does not exist"
            elif state.state in ("unavailable", "unknown"):
                errors[config_key] = f"Entity '{entity_id}' is {state.state}"
            elif state.domain != expected_domain:
                errors[config_key] = (
                    f"Expected {expected_domain} entity, got {state.domain}"
                )

        return errors if errors else None

    async def _get_notify_services(self) -> list[str]:
        """Get list of available notify services.

        Returns:
            List of notify service strings like ["notify.mobile_app"]
        """
        services = self.hass.services.async_services()
        notify_services = []

        if "notify" in services:
            for service_name in services["notify"].keys():
                notify_services.append(f"notify.{service_name}")

        return sorted(notify_services)

    async def _get_weather_entities(self) -> list[str]:
        """Get list of available weather entities.

        Returns:
            List of weather entity IDs like ["weather.home", "weather.forecast"]
        """
        weather_entities = []
        for state in self.hass.states.async_all():
            if state.domain == "weather":
                weather_entities.append(state.entity_id)

        return sorted(weather_entities)

    async def _get_climate_entities(self) -> list[str]:
        """Get list of available climate entities.

        Returns:
            List of climate entity IDs like ["climate.living_room", "climate.bedroom"]
        """
        climate_entities = []
        for state in self.hass.states.async_all():
            if state.domain == "climate":
                climate_entities.append(state.entity_id)

        return sorted(climate_entities)

    async def _validate_notify_service(self, notify_service: str) -> str | None:
        """Validate that a notify service exists.

        Args:
            notify_service: Service string like "notify.mobile_app_xxx"

        Returns:
            None if valid, or error message string
        """
        if not notify_service:
            return "Notify service is required"

        if not notify_service.startswith("notify."):
            return "Notify service must start with 'notify.'"

        # Parse domain and service name
        # Format: "notify.mobile_app_xxx" -> domain="notify", service="mobile_app_xxx"
        parts = notify_service.split(".", 1)
        if len(parts) != 2:
            return "Invalid notify service format"

        domain, service_name = parts

        # Check if notify service exists
        services = self.hass.services.async_services()
        if domain not in services:
            return f"Notify domain '{domain}' not found"

        if service_name not in services[domain]:
            return f"Notify service '{service_name}' not found in {domain}"

        return None

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

            errors = await self._validate_entities(entities_to_validate)
            if errors:
                return self.async_show_form(
                    step_id="user",
                    data_schema=vol.Schema(
                        {
                            vol.Required(
                                CONF_TESLEMETRY_OPERATION_MODE,
                                default=user_input[CONF_TESLEMETRY_OPERATION_MODE],
                            ): selector.EntitySelector(
                                selector.EntitySelectorConfig(domain="select")
                            ),
                            vol.Required(
                                CONF_TESLEMETRY_BACKUP_RESERVE,
                                default=user_input[CONF_TESLEMETRY_BACKUP_RESERVE],
                            ): selector.EntitySelector(
                                selector.EntitySelectorConfig(domain="number")
                            ),
                            vol.Required(
                                CONF_TESLEMETRY_SOC,
                                default=user_input[CONF_TESLEMETRY_SOC],
                            ): selector.EntitySelector(
                                selector.EntitySelectorConfig(domain="sensor")
                            ),
                            vol.Required(
                                CONF_TESLEMETRY_GRID_POWER,
                                default=user_input[CONF_TESLEMETRY_GRID_POWER],
                            ): selector.EntitySelector(
                                selector.EntitySelectorConfig(domain="sensor")
                            ),
                            vol.Required(
                                CONF_TESLEMETRY_BATTERY_POWER,
                                default=user_input[CONF_TESLEMETRY_BATTERY_POWER],
                            ): selector.EntitySelector(
                                selector.EntitySelectorConfig(domain="sensor")
                            ),
                            vol.Required(
                                CONF_TESLEMETRY_SOLAR_POWER,
                                default=user_input[CONF_TESLEMETRY_SOLAR_POWER],
                            ): selector.EntitySelector(
                                selector.EntitySelectorConfig(domain="sensor")
                            ),
                            vol.Required(
                                CONF_TESLEMETRY_LOAD_POWER,
                                default=user_input[CONF_TESLEMETRY_LOAD_POWER],
                            ): selector.EntitySelector(
                                selector.EntitySelectorConfig(domain="sensor")
                            ),
                        }
                    ),
                    errors=errors,
                )

            # Store teslemetry config, move to pricing step
            self._teslemetry_data = user_input
            return await self.async_step_pricing()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_TESLEMETRY_OPERATION_MODE,
                        default=DEFAULT_ENTITY_IDS[CONF_TESLEMETRY_OPERATION_MODE],
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="select")
                    ),
                    vol.Required(
                        CONF_TESLEMETRY_BACKUP_RESERVE,
                        default=DEFAULT_ENTITY_IDS[CONF_TESLEMETRY_BACKUP_RESERVE],
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="number")
                    ),
                    vol.Required(
                        CONF_TESLEMETRY_SOC,
                        default=DEFAULT_ENTITY_IDS[CONF_TESLEMETRY_SOC],
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="sensor")
                    ),
                    vol.Required(
                        CONF_TESLEMETRY_GRID_POWER,
                        default=DEFAULT_ENTITY_IDS[CONF_TESLEMETRY_GRID_POWER],
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="sensor")
                    ),
                    vol.Required(
                        CONF_TESLEMETRY_BATTERY_POWER,
                        default=DEFAULT_ENTITY_IDS[CONF_TESLEMETRY_BATTERY_POWER],
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="sensor")
                    ),
                    vol.Required(
                        CONF_TESLEMETRY_SOLAR_POWER,
                        default=DEFAULT_ENTITY_IDS[CONF_TESLEMETRY_SOLAR_POWER],
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="sensor")
                    ),
                    vol.Required(
                        CONF_TESLEMETRY_LOAD_POWER,
                        default=DEFAULT_ENTITY_IDS[CONF_TESLEMETRY_LOAD_POWER],
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="sensor")
                    ),
                }
            ),
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

            errors = await self._validate_entities(entities_to_validate)
            if errors:
                return self.async_show_form(
                    step_id="pricing",
                    data_schema=vol.Schema(
                        {
                            vol.Required(
                                CONF_PRICING_GENERAL_PRICE,
                                default=user_input[CONF_PRICING_GENERAL_PRICE],
                            ): selector.EntitySelector(
                                selector.EntitySelectorConfig(domain="sensor")
                            ),
                            vol.Required(
                                CONF_PRICING_FEED_IN_PRICE,
                                default=user_input[CONF_PRICING_FEED_IN_PRICE],
                            ): selector.EntitySelector(
                                selector.EntitySelectorConfig(domain="sensor")
                            ),
                            vol.Required(
                                CONF_PRICING_GENERAL_FORECAST,
                                default=user_input[CONF_PRICING_GENERAL_FORECAST],
                            ): selector.EntitySelector(
                                selector.EntitySelectorConfig(domain="sensor")
                            ),
                            vol.Required(
                                CONF_PRICING_FEED_IN_FORECAST,
                                default=user_input[CONF_PRICING_FEED_IN_FORECAST],
                            ): selector.EntitySelector(
                                selector.EntitySelectorConfig(domain="sensor")
                            ),
                            vol.Required(
                                CONF_PRICING_PRICE_SPIKE,
                                default=user_input[CONF_PRICING_PRICE_SPIKE],
                            ): selector.EntitySelector(
                                selector.EntitySelectorConfig(domain="binary_sensor")
                            ),
                        }
                    ),
                    errors=errors,
                )

            # Store pricing config, move to solcast step
            self._pricing_data = user_input
            return await self.async_step_solcast()

        return self.async_show_form(
            step_id="pricing",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_PRICING_GENERAL_PRICE,
                        default=DEFAULT_ENTITY_IDS[CONF_PRICING_GENERAL_PRICE],
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="sensor")
                    ),
                    vol.Required(
                        CONF_PRICING_FEED_IN_PRICE,
                        default=DEFAULT_ENTITY_IDS[CONF_PRICING_FEED_IN_PRICE],
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="sensor")
                    ),
                    vol.Required(
                        CONF_PRICING_GENERAL_FORECAST,
                        default=DEFAULT_ENTITY_IDS[CONF_PRICING_GENERAL_FORECAST],
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="sensor")
                    ),
                    vol.Required(
                        CONF_PRICING_FEED_IN_FORECAST,
                        default=DEFAULT_ENTITY_IDS[CONF_PRICING_FEED_IN_FORECAST],
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="sensor")
                    ),
                    vol.Required(
                        CONF_PRICING_PRICE_SPIKE,
                        default=DEFAULT_ENTITY_IDS[CONF_PRICING_PRICE_SPIKE],
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="binary_sensor")
                    ),
                }
            ),
        )

    async def async_step_solcast(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the Solcast + notification entity selection step."""
        # Get available notify services dynamically
        notify_services = await self._get_notify_services()

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

            errors = await self._validate_entities(entities_to_validate) or {}

            # Validate notify service
            notify_error = await self._validate_notify_service(
                user_input[CONF_NOTIFY_SERVICE]
            )
            if notify_error:
                errors[CONF_NOTIFY_SERVICE] = notify_error

            if errors:
                return self.async_show_form(
                    step_id="solcast",
                    data_schema=vol.Schema(
                        {
                            vol.Required(
                                CONF_SOLCAST_FORECAST_TODAY,
                                default=user_input[CONF_SOLCAST_FORECAST_TODAY],
                            ): selector.EntitySelector(
                                selector.EntitySelectorConfig(domain="sensor")
                            ),
                            vol.Required(
                                CONF_SOLCAST_FORECAST_TOMORROW,
                                default=user_input[CONF_SOLCAST_FORECAST_TOMORROW],
                            ): selector.EntitySelector(
                                selector.EntitySelectorConfig(domain="sensor")
                            ),
                            vol.Required(
                                CONF_NOTIFY_SERVICE,
                                default=user_input[CONF_NOTIFY_SERVICE],
                            ): selector.SelectSelector(
                                selector.SelectSelectorConfig(
                                    options=notify_services,
                                    mode=selector.SelectSelectorMode.DROPDOWN,
                                )
                            ),
                            vol.Required(
                                CONF_SUN_ENTITY,
                                default=user_input[CONF_SUN_ENTITY],
                            ): selector.EntitySelector(),
                        }
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
                CONF_LOAD_WEIGHT_RECENT: DEFAULT_LOAD_WEIGHT_RECENT,
                CONF_MINIMUM_TARGET_SOC: DEFAULT_MINIMUM_TARGET_SOC,
                CONF_ALLOW_DW_ENTRY_UNDER_TARGET: DEFAULT_ALLOW_DW_ENTRY_UNDER_TARGET,
                # Weather correlation options
                CONF_WEATHER_ENTITY: user_input.get(
                    CONF_WEATHER_ENTITY, DEFAULT_WEATHER_ENTITY
                ),
                CONF_WEATHER_LEARNING_ENABLED: DEFAULT_WEATHER_LEARNING_ENABLED,
                CONF_COOLING_THRESHOLD: DEFAULT_COOLING_THRESHOLD,
                CONF_HEATING_THRESHOLD: DEFAULT_HEATING_THRESHOLD,
            }

            return self.async_create_entry(
                title="LocalShift",
                data=all_data,
                options=options,
            )

        # Determine default notify service (first available or empty)
        default_notify = notify_services[0] if notify_services else ""

        # Get available weather entities
        weather_entities = await self._get_weather_entities()
        default_weather = (
            weather_entities[0] if weather_entities else DEFAULT_WEATHER_ENTITY
        )

        return self.async_show_form(
            step_id="solcast",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SOLCAST_FORECAST_TODAY,
                        default=DEFAULT_ENTITY_IDS[CONF_SOLCAST_FORECAST_TODAY],
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="sensor")
                    ),
                    vol.Required(
                        CONF_SOLCAST_FORECAST_TOMORROW,
                        default=DEFAULT_ENTITY_IDS[CONF_SOLCAST_FORECAST_TOMORROW],
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="sensor")
                    ),
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
                        default=DEFAULT_ENTITY_IDS[CONF_SUN_ENTITY],
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
            ),
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow for this handler."""
        return LocalShiftOptionsFlow()


class LocalShiftOptionsFlow(OptionsFlow):
    """Handle options flow for LocalShift."""

    async def _get_notify_services(self) -> list[str]:
        """Get list of available notify services.

        Returns:
            List of notify service strings like ["notify.mobile_app"]
        """
        services = self.hass.services.async_services()
        notify_services = []

        if "notify" in services:
            for service_name in services["notify"].keys():
                notify_services.append(f"notify.{service_name}")

        return sorted(notify_services)

    async def _validate_notify_service(self, notify_service: str) -> str | None:
        """Validate that a notify service exists.

        Args:
            notify_service: Service string like "notify.mobile_app_xxx"

        Returns:
            None if valid, or error message string
        """
        if not notify_service:
            return "Notify service is required"

        if not notify_service.startswith("notify."):
            return "Notify service must start with 'notify.'"

        # Parse domain and service name
        # Format: "notify.mobile_app_xxx" -> domain="notify", service="mobile_app_xxx"
        parts = notify_service.split(".", 1)
        if len(parts) != 2:
            return "Invalid notify service format"

        domain, service_name = parts

        # Check if notify service exists
        services = self.hass.services.async_services()
        if domain not in services:
            return f"Notify domain '{domain}' not found"

        if service_name not in services[domain]:
            return f"Notify service '{service_name}' not found in {domain}"

        return None

    def _get_current_notify_service(self) -> str:
        """Get the current notify service from options or data (for backward compatibility).

        Returns:
            Current notify service string, or empty string if not set.
        """
        # Check options first (new location)
        if CONF_NOTIFY_SERVICE in self.config_entry.options:
            return self.config_entry.options[CONF_NOTIFY_SERVICE]
        # Fall back to data (old location for existing entries)
        if CONF_NOTIFY_SERVICE in self.config_entry.data:
            return self.config_entry.data[CONF_NOTIFY_SERVICE]
        return ""

    async def _get_weather_entities(self) -> list[str]:
        """Get list of available weather entities.

        Returns:
            List of weather entity IDs like ["weather.home", "weather.forecast"]
        """
        weather_entities = []
        for state in self.hass.states.async_all():
            if state.domain == "weather":
                weather_entities.append(state.entity_id)

        return sorted(weather_entities)

    async def _get_climate_entities(self) -> list[str]:
        """Get list of available climate entities.

        Returns:
            List of climate entity IDs like ["climate.living_room", "climate.bedroom"]
        """
        climate_entities = []
        for state in self.hass.states.async_all():
            if state.domain == "climate":
                climate_entities.append(state.entity_id)

        return sorted(climate_entities)

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the thresholds and timing options."""
        # Get available notify services, weather entities, and climate entities
        notify_services = await self._get_notify_services()
        weather_entities = await self._get_weather_entities()
        climate_entities = await self._get_climate_entities()

        if user_input is not None:
            # Validate notify service
            errors = {}
            notify_error = await self._validate_notify_service(
                user_input[CONF_NOTIFY_SERVICE]
            )
            if notify_error:
                errors[CONF_NOTIFY_SERVICE] = notify_error

            if errors:
                return self.async_show_form(
                    step_id="init",
                    data_schema=self._build_options_schema(
                        user_input, notify_services, weather_entities, climate_entities
                    ),
                    errors=errors,
                )

            # Merge with existing options to preserve other settings
            merged_options = dict(self.config_entry.options)
            merged_options.update(user_input)
            return self.async_create_entry(data=merged_options)

        current = self.config_entry.options
        current_notify = self._get_current_notify_service()

        return self.async_show_form(
            step_id="init",
            data_schema=self._build_options_schema(
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
                    CONF_LOAD_WEIGHT_RECENT: current.get(
                        CONF_LOAD_WEIGHT_RECENT,
                        DEFAULT_LOAD_WEIGHT_RECENT,
                    ),
                    CONF_SPIKE_PRICE_PERCENTILE: current.get(
                        CONF_SPIKE_PRICE_PERCENTILE,
                        DEFAULT_SPIKE_PRICE_PERCENTILE,
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
                    CONF_COOLING_THRESHOLD: current.get(
                        CONF_COOLING_THRESHOLD,
                        DEFAULT_COOLING_THRESHOLD,
                    ),
                    CONF_HEATING_THRESHOLD: current.get(
                        CONF_HEATING_THRESHOLD,
                        DEFAULT_HEATING_THRESHOLD,
                    ),
                    # Export price margin
                    CONF_EXPORT_PRICE_MARGIN: current.get(
                        CONF_EXPORT_PRICE_MARGIN,
                        DEFAULT_EXPORT_PRICE_MARGIN,
                    ),
                    # Thermal Manager options (Issue #137, #63)
                    CONF_THERMAL_MANAGEMENT_ENABLED: current.get(
                        CONF_THERMAL_MANAGEMENT_ENABLED,
                        DEFAULT_THERMAL_MANAGEMENT_ENABLED,
                    ),
                    CONF_CLIMATE_ENTITIES: current.get(CONF_CLIMATE_ENTITIES, []),
                    CONF_CLIMATE_CONTROL_ENTITIES: current.get(
                        CONF_CLIMATE_CONTROL_ENTITIES, []
                    ),
                    CONF_COOLING_TRIGGER_TEMP: current.get(
                        CONF_COOLING_TRIGGER_TEMP,
                        DEFAULT_COOLING_TRIGGER_TEMP,
                    ),
                    CONF_HEATING_TRIGGER_TEMP: current.get(
                        CONF_HEATING_TRIGGER_TEMP,
                        DEFAULT_HEATING_TRIGGER_TEMP,
                    ),
                    CONF_DEHUMIDIFY_TRIGGER_HUMIDITY: current.get(
                        CONF_DEHUMIDIFY_TRIGGER_HUMIDITY,
                        DEFAULT_DEHUMIDIFY_TRIGGER_HUMIDITY,
                    ),
                    CONF_SOLAR_TAPER_ENABLED: current.get(
                        CONF_SOLAR_TAPER_ENABLED,
                        DEFAULT_SOLAR_TAPER_ENABLED,
                    ),
                    CONF_PRECONDITION_HOURS_BEFORE_DW: current.get(
                        CONF_PRECONDITION_HOURS_BEFORE_DW,
                        DEFAULT_PRECONDITION_HOURS_BEFORE_DW,
                    ),
                    CONF_PRECONDITION_TEMP_OFFSET: current.get(
                        CONF_PRECONDITION_TEMP_OFFSET,
                        DEFAULT_PRECONDITION_TEMP_OFFSET,
                    ),
                    CONF_TAPER_MAX_SETPOINT_OFFSET: current.get(
                        CONF_TAPER_MAX_SETPOINT_OFFSET,
                        DEFAULT_TAPER_MAX_SETPOINT_OFFSET,
                    ),
                    CONF_THERMAL_MODE_DECISION_TIME: current.get(
                        CONF_THERMAL_MODE_DECISION_TIME,
                        DEFAULT_THERMAL_MODE_DECISION_TIME,
                    ),
                },
                notify_services,
                weather_entities,
                climate_entities,
            ),
        )

    def _build_options_schema(
        self,
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
                        unit_of_measurement=THRESHOLD_RANGES[
                            CONF_CHEAP_PRICE_PERCENTILE
                        ]["unit"],
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
                        unit_of_measurement=THRESHOLD_RANGES[
                            CONF_SPIKE_PRICE_PERCENTILE
                        ]["unit"],
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
                        unit_of_measurement=THRESHOLD_RANGES[CONF_BATTERY_TARGET][
                            "unit"
                        ],
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
                        step=THRESHOLD_RANGES[CONF_PRECONDITION_HOURS_BEFORE_DW][
                            "step"
                        ],
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
                        unit_of_measurement=THRESHOLD_RANGES[
                            CONF_PRECONDITION_TEMP_OFFSET
                        ]["unit"],
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
