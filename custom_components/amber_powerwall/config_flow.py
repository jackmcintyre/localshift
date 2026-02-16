"""Config flow for the Amber Powerwall integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from .const import (
    CONF_AMBER_FEED_IN_FORECAST,
    CONF_AMBER_FEED_IN_PRICE,
    CONF_AMBER_GENERAL_FORECAST,
    CONF_AMBER_GENERAL_PRICE,
    CONF_AMBER_PRICE_SPIKE,
    CONF_BATTERY_TARGET,
    CONF_CHEAP_PRICE_DEADBAND,
    CONF_CHEAP_PRICE_PERCENTILE,
    CONF_DEMAND_WINDOW_END,
    CONF_DEMAND_WINDOW_START,
    CONF_FORECAST_LOOKAHEAD_HOURS,
    CONF_LOAD_WEIGHT_RECENT,
    CONF_MANUAL_OVERRIDE_TIMEOUT,
    CONF_MAX_PRECHARGE_PRICE,
    CONF_MINIMUM_TARGET_SOC,
    CONF_NOTIFY_SERVICE,
    CONF_PRECHARGE_BATTERY_THRESHOLD,
    CONF_SOLCAST_FORECAST_TODAY,
    CONF_SOLCAST_FORECAST_TOMORROW,
    CONF_SUN_ENTITY,
    CONF_TESLEMETRY_ALLOW_EXPORT,
    CONF_TESLEMETRY_BACKUP_RESERVE,
    CONF_TESLEMETRY_BATTERY_POWER,
    CONF_TESLEMETRY_GRID_POWER,
    CONF_TESLEMETRY_LOAD_POWER,
    CONF_TESLEMETRY_OPERATION_MODE,
    CONF_TESLEMETRY_SOC,
    CONF_TESLEMETRY_SOLAR_POWER,
    DEFAULT_BATTERY_TARGET,
    DEFAULT_CHEAP_PRICE_DEADBAND,
    DEFAULT_CHEAP_PRICE_PERCENTILE,
    DEFAULT_DEMAND_WINDOW_END,
    DEFAULT_DEMAND_WINDOW_START,
    DEFAULT_ENTITY_IDS,
    DEFAULT_FORECAST_LOOKAHEAD_HOURS,
    DEFAULT_LOAD_WEIGHT_RECENT,
    DEFAULT_MANUAL_OVERRIDE_TIMEOUT,
    DEFAULT_MAX_PRECHARGE_PRICE,
    DEFAULT_MINIMUM_TARGET_SOC,
    DEFAULT_PRECHARGE_BATTERY_THRESHOLD,
    DOMAIN,
)


class AmberPowerwallConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Amber Powerwall."""

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

        # Check if the notify service exists
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
                CONF_MINIMUM_TARGET_SOC: (
                    user_input[CONF_MINIMUM_TARGET_SOC],
                    "number",
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
                CONF_TESLEMETRY_ALLOW_EXPORT: (
                    user_input[CONF_TESLEMETRY_ALLOW_EXPORT],
                    "select",
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
                                CONF_MINIMUM_TARGET_SOC,
                                default=user_input[CONF_MINIMUM_TARGET_SOC],
                            ): selector.EntitySelector(
                                selector.EntitySelectorConfig(domain="number")
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
                            vol.Required(
                                CONF_TESLEMETRY_ALLOW_EXPORT,
                                default=user_input[CONF_TESLEMETRY_ALLOW_EXPORT],
                            ): selector.EntitySelector(
                                selector.EntitySelectorConfig(domain="select")
                            ),
                        }
                    ),
                    errors=errors,
                )

            # Store teslemetry config, move to amber step
            self._teslemetry_data = user_input
            return await self.async_step_amber()

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
                        CONF_MINIMUM_TARGET_SOC,
                        default=DEFAULT_ENTITY_IDS[CONF_MINIMUM_TARGET_SOC],
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="number")
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
                    vol.Required(
                        CONF_TESLEMETRY_ALLOW_EXPORT,
                        default=DEFAULT_ENTITY_IDS[CONF_TESLEMETRY_ALLOW_EXPORT],
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="select")
                    ),
                }
            ),
        )

    async def async_step_amber(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the Amber Electric entity selection step."""
        if user_input is not None:
            # Validate entities
            entities_to_validate = {
                CONF_AMBER_GENERAL_PRICE: (
                    user_input[CONF_AMBER_GENERAL_PRICE],
                    "sensor",
                ),
                CONF_AMBER_FEED_IN_PRICE: (
                    user_input[CONF_AMBER_FEED_IN_PRICE],
                    "sensor",
                ),
                CONF_AMBER_GENERAL_FORECAST: (
                    user_input[CONF_AMBER_GENERAL_FORECAST],
                    "sensor",
                ),
                CONF_AMBER_FEED_IN_FORECAST: (
                    user_input[CONF_AMBER_FEED_IN_FORECAST],
                    "sensor",
                ),
                CONF_AMBER_PRICE_SPIKE: (
                    user_input[CONF_AMBER_PRICE_SPIKE],
                    "binary_sensor",
                ),
            }

            errors = await self._validate_entities(entities_to_validate)
            if errors:
                return self.async_show_form(
                    step_id="amber",
                    data_schema=vol.Schema(
                        {
                            vol.Required(
                                CONF_AMBER_GENERAL_PRICE,
                                default=user_input[CONF_AMBER_GENERAL_PRICE],
                            ): selector.EntitySelector(
                                selector.EntitySelectorConfig(domain="sensor")
                            ),
                            vol.Required(
                                CONF_AMBER_FEED_IN_PRICE,
                                default=user_input[CONF_AMBER_FEED_IN_PRICE],
                            ): selector.EntitySelector(
                                selector.EntitySelectorConfig(domain="sensor")
                            ),
                            vol.Required(
                                CONF_AMBER_GENERAL_FORECAST,
                                default=user_input[CONF_AMBER_GENERAL_FORECAST],
                            ): selector.EntitySelector(
                                selector.EntitySelectorConfig(domain="sensor")
                            ),
                            vol.Required(
                                CONF_AMBER_FEED_IN_FORECAST,
                                default=user_input[CONF_AMBER_FEED_IN_FORECAST],
                            ): selector.EntitySelector(
                                selector.EntitySelectorConfig(domain="sensor")
                            ),
                            vol.Required(
                                CONF_AMBER_PRICE_SPIKE,
                                default=user_input[CONF_AMBER_PRICE_SPIKE],
                            ): selector.EntitySelector(
                                selector.EntitySelectorConfig(domain="binary_sensor")
                            ),
                        }
                    ),
                    errors=errors,
                )

            # Store amber config, move to solcast step
            self._amber_data = user_input
            return await self.async_step_solcast()

        return self.async_show_form(
            step_id="amber",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_AMBER_GENERAL_PRICE,
                        default=DEFAULT_ENTITY_IDS[CONF_AMBER_GENERAL_PRICE],
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="sensor")
                    ),
                    vol.Required(
                        CONF_AMBER_FEED_IN_PRICE,
                        default=DEFAULT_ENTITY_IDS[CONF_AMBER_FEED_IN_PRICE],
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="sensor")
                    ),
                    vol.Required(
                        CONF_AMBER_GENERAL_FORECAST,
                        default=DEFAULT_ENTITY_IDS[CONF_AMBER_GENERAL_FORECAST],
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="sensor")
                    ),
                    vol.Required(
                        CONF_AMBER_FEED_IN_FORECAST,
                        default=DEFAULT_ENTITY_IDS[CONF_AMBER_FEED_IN_FORECAST],
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="sensor")
                    ),
                    vol.Required(
                        CONF_AMBER_PRICE_SPIKE,
                        default=DEFAULT_ENTITY_IDS[CONF_AMBER_PRICE_SPIKE],
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
            all_data = {
                **self._teslemetry_data,
                **self._amber_data,
                **user_input,
            }
            # Set default options
            options = {
                CONF_CHEAP_PRICE_PERCENTILE: DEFAULT_CHEAP_PRICE_PERCENTILE,
                CONF_MAX_PRECHARGE_PRICE: DEFAULT_MAX_PRECHARGE_PRICE,
                CONF_CHEAP_PRICE_DEADBAND: DEFAULT_CHEAP_PRICE_DEADBAND,
                CONF_FORECAST_LOOKAHEAD_HOURS: DEFAULT_FORECAST_LOOKAHEAD_HOURS,
                CONF_PRECHARGE_BATTERY_THRESHOLD: DEFAULT_PRECHARGE_BATTERY_THRESHOLD,
                CONF_BATTERY_TARGET: DEFAULT_BATTERY_TARGET,
                CONF_DEMAND_WINDOW_START: DEFAULT_DEMAND_WINDOW_START,
                CONF_DEMAND_WINDOW_END: DEFAULT_DEMAND_WINDOW_END,
                # Hold mode options removed
                CONF_MANUAL_OVERRIDE_TIMEOUT: DEFAULT_MANUAL_OVERRIDE_TIMEOUT,
                CONF_LOAD_WEIGHT_RECENT: DEFAULT_LOAD_WEIGHT_RECENT,
            }

            return self.async_create_entry(
                title="Amber Powerwall",
                data=all_data,
                options=options,
            )

        # Determine default notify service (first available or empty)
        default_notify = notify_services[0] if notify_services else ""

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
                }
            ),
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow for this handler."""
        return AmberPowerwallOptionsFlow()


class AmberPowerwallOptionsFlow(OptionsFlow):
    """Handle options flow for Amber Powerwall."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the thresholds and timing options."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        current = self.config_entry.options

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_CHEAP_PRICE_PERCENTILE,
                        default=current.get(
                            CONF_CHEAP_PRICE_PERCENTILE,
                            DEFAULT_CHEAP_PRICE_PERCENTILE,
                        ),
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
                        default=current.get(
                            CONF_MAX_PRECHARGE_PRICE,
                            DEFAULT_MAX_PRECHARGE_PRICE,
                        ),
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
                        CONF_CHEAP_PRICE_DEADBAND,
                        default=current.get(
                            CONF_CHEAP_PRICE_DEADBAND,
                            DEFAULT_CHEAP_PRICE_DEADBAND,
                        ),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0.00,
                            max=0.10,
                            step=0.01,
                            unit_of_measurement="$/kWh",
                            mode=selector.NumberSelectorMode.SLIDER,
                        )
                    ),
                    vol.Required(
                        CONF_FORECAST_LOOKAHEAD_HOURS,
                        default=current.get(
                            CONF_FORECAST_LOOKAHEAD_HOURS,
                            DEFAULT_FORECAST_LOOKAHEAD_HOURS,
                        ),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=1.0,
                            max=8.0,
                            step=0.5,
                            unit_of_measurement="hours",
                            mode=selector.NumberSelectorMode.SLIDER,
                        )
                    ),
                    vol.Required(
                        CONF_PRECHARGE_BATTERY_THRESHOLD,
                        default=current.get(
                            CONF_PRECHARGE_BATTERY_THRESHOLD,
                            DEFAULT_PRECHARGE_BATTERY_THRESHOLD,
                        ),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0,
                            max=100,
                            step=5,
                            unit_of_measurement="%",
                            mode=selector.NumberSelectorMode.SLIDER,
                        )
                    ),
                    vol.Required(
                        CONF_BATTERY_TARGET,
                        default=current.get(
                            CONF_BATTERY_TARGET,
                            DEFAULT_BATTERY_TARGET,
                        ),
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
                        CONF_DEMAND_WINDOW_START,
                        default=current.get(
                            CONF_DEMAND_WINDOW_START,
                            DEFAULT_DEMAND_WINDOW_START,
                        ),
                    ): selector.TimeSelector(),
                    vol.Required(
                        CONF_DEMAND_WINDOW_END,
                        default=current.get(
                            CONF_DEMAND_WINDOW_END,
                            DEFAULT_DEMAND_WINDOW_END,
                        ),
                    ): selector.TimeSelector(),
                    # Hold mode options removed
                    vol.Required(
                        CONF_MANUAL_OVERRIDE_TIMEOUT,
                        default=current.get(
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
                    vol.Required(
                        CONF_LOAD_WEIGHT_RECENT,
                        default=current.get(
                            CONF_LOAD_WEIGHT_RECENT,
                            DEFAULT_LOAD_WEIGHT_RECENT,
                        ),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0.0,
                            max=1.0,
                            step=0.05,
                            mode=selector.NumberSelectorMode.SLIDER,
                        )
                    ),
                    vol.Required(
                        CONF_MINIMUM_TARGET_SOC,
                        default=current.get(
                            CONF_MINIMUM_TARGET_SOC,
                            DEFAULT_MINIMUM_TARGET_SOC,
                        ),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=5,
                            max=30,
                            step=1,
                            unit_of_measurement="%",
                            mode=selector.NumberSelectorMode.SLIDER,
                        )
                    ),
                }
            ),
        )
