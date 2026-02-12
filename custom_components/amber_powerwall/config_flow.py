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
    CONF_CHEAP_PRICE_THRESHOLD,
    CONF_DEMAND_WINDOW_END,
    CONF_DEMAND_WINDOW_START,
    CONF_FORECAST_LOOKAHEAD_HOURS,
    CONF_MAX_PRECHARGE_PRICE,
    CONF_NOTIFY_SERVICE,
    CONF_PRECHARGE_BATTERY_THRESHOLD,
    CONF_SOLCAST_FORECAST_TODAY,
    CONF_SOLCAST_FORECAST_TOMORROW,
    CONF_TESLEMETRY_BACKUP_RESERVE,
    CONF_TESLEMETRY_BATTERY_POWER,
    CONF_TESLEMETRY_GRID_POWER,
    CONF_TESLEMETRY_ALLOW_EXPORT,
    CONF_TESLEMETRY_LOAD_POWER,
    CONF_TESLEMETRY_OPERATION_MODE,
    CONF_TESLEMETRY_SOC,
    CONF_TESLEMETRY_SOLAR_POWER,
    DEFAULT_BATTERY_TARGET,
    DEFAULT_CHEAP_PRICE_DEADBAND,
    DEFAULT_CHEAP_PRICE_THRESHOLD,
    DEFAULT_DEMAND_WINDOW_END,
    DEFAULT_DEMAND_WINDOW_START,
    DEFAULT_ENTITY_IDS,
    DEFAULT_FORECAST_LOOKAHEAD_HOURS,
    DEFAULT_MAX_PRECHARGE_PRICE,
    DEFAULT_PRECHARGE_BATTERY_THRESHOLD,
    DOMAIN,
)


class AmberPowerwallConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Amber Powerwall."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the Teslemetry entity selection step."""
        if user_input is not None:
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
        if user_input is not None:
            # Combine all data and create entry
            all_data = {
                **self._teslemetry_data,
                **self._amber_data,
                **user_input,
            }
            # Set default options
            options = {
                CONF_CHEAP_PRICE_THRESHOLD: DEFAULT_CHEAP_PRICE_THRESHOLD,
                CONF_MAX_PRECHARGE_PRICE: DEFAULT_MAX_PRECHARGE_PRICE,
                CONF_CHEAP_PRICE_DEADBAND: DEFAULT_CHEAP_PRICE_DEADBAND,
                CONF_FORECAST_LOOKAHEAD_HOURS: DEFAULT_FORECAST_LOOKAHEAD_HOURS,
                CONF_PRECHARGE_BATTERY_THRESHOLD: DEFAULT_PRECHARGE_BATTERY_THRESHOLD,
                CONF_BATTERY_TARGET: DEFAULT_BATTERY_TARGET,
                CONF_DEMAND_WINDOW_START: DEFAULT_DEMAND_WINDOW_START,
                CONF_DEMAND_WINDOW_END: DEFAULT_DEMAND_WINDOW_END,
            }
            return self.async_create_entry(
                title="Amber Powerwall",
                data=all_data,
                options=options,
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
                        default=DEFAULT_ENTITY_IDS[CONF_NOTIFY_SERVICE],
                    ): selector.TextSelector(),
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
                        CONF_CHEAP_PRICE_THRESHOLD,
                        default=current.get(
                            CONF_CHEAP_PRICE_THRESHOLD,
                            DEFAULT_CHEAP_PRICE_THRESHOLD,
                        ),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0.00,
                            max=0.25,
                            step=0.01,
                            unit_of_measurement="$/kWh",
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
                }
            ),
        )
