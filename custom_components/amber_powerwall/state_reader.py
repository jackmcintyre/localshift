"""State reading functionality for external entities."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_AMBER_FEED_IN_FORECAST,
    CONF_AMBER_FEED_IN_PRICE,
    CONF_AMBER_GENERAL_FORECAST,
    CONF_AMBER_GENERAL_PRICE,
    CONF_AMBER_PRICE_SPIKE,
    CONF_SOLCAST_FORECAST_TODAY,
    CONF_SOLCAST_FORECAST_TOMORROW,
    CONF_TESLEMETRY_ALLOW_EXPORT,
    CONF_TESLEMETRY_BACKUP_RESERVE,
    CONF_TESLEMETRY_BATTERY_POWER,
    CONF_TESLEMETRY_GRID_POWER,
    CONF_TESLEMETRY_LOAD_POWER,
    CONF_TESLEMETRY_OPERATION_MODE,
    CONF_TESLEMETRY_SOC,
    CONF_TESLEMETRY_SOLAR_POWER,
    DEFAULT_ENTITY_IDS,
)
from .coordinator_data import CoordinatorData


class StateReader:
    """Handles reading state from external Home Assistant entities."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the state reader."""
        self.hass = hass
        self.entry = entry

    def _get_entity_id(self, key: str) -> str:
        """Get a configured external entity ID by config key.

        Returns default from DEFAULT_ENTITY_IDS if key not in entry data
        (handles missing keys in existing config entries).
        """
        if key in self.entry.data:
            return self.entry.data[key]

        # Fallback to default if key not in entry data
        return DEFAULT_ENTITY_IDS.get(key, "")

    def _read_float(self, entity_id: str, default: float = 0.0) -> float:
        """Read a float value from an entity's state."""
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable"):
            return default
        try:
            return float(state.state)
        except (ValueError, TypeError):
            return default

    def _read_state(self, entity_id: str, default: str = "") -> str:
        """Read a string value from an entity's state."""
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable"):
            return default
        return state.state

    def _read_bool(self, entity_id: str) -> bool:
        """Read a boolean value from an entity's state (on/off)."""
        return self._read_state(entity_id) == "on"

    def _read_attribute(self, entity_id: str, attr: str, default: Any = None) -> Any:
        """Read an attribute from an entity."""
        state = self.hass.states.get(entity_id)
        if state is None:
            return default
        return state.attributes.get(attr, default)

    def read_all_external_state(self, data: CoordinatorData) -> None:
        """Read current state of all monitored external entities.

        Populates the provided CoordinatorData instance with all raw values.
        """

        # Teslemetry
        data.grid_power_kw = self._read_float(
            self._get_entity_id(CONF_TESLEMETRY_GRID_POWER)
        )
        data.battery_power_kw = self._read_float(
            self._get_entity_id(CONF_TESLEMETRY_BATTERY_POWER)
        )
        data.solar_power_kw = self._read_float(
            self._get_entity_id(CONF_TESLEMETRY_SOLAR_POWER)
        )
        data.load_power_kw = self._read_float(
            self._get_entity_id(CONF_TESLEMETRY_LOAD_POWER)
        )
        data.soc = self._read_float(self._get_entity_id(CONF_TESLEMETRY_SOC))
        data.operation_mode = self._read_state(
            self._get_entity_id(CONF_TESLEMETRY_OPERATION_MODE)
        )
        data.backup_reserve = self._read_float(
            self._get_entity_id(CONF_TESLEMETRY_BACKUP_RESERVE)
        )
        data.allow_export = self._read_state(
            self._get_entity_id(CONF_TESLEMETRY_ALLOW_EXPORT)
        )

        # Amber
        data.general_price = self._read_float(
            self._get_entity_id(CONF_AMBER_GENERAL_PRICE)
        )
        data.feed_in_price = self._read_float(
            self._get_entity_id(CONF_AMBER_FEED_IN_PRICE)
        )
        data.price_spike = self._read_bool(self._get_entity_id(CONF_AMBER_PRICE_SPIKE))
        data.general_forecast = (
            self._read_attribute(
                self._get_entity_id(CONF_AMBER_GENERAL_FORECAST), "forecasts", []
            )
            or []
        )
        data.feed_in_forecast = (
            self._read_attribute(
                self._get_entity_id(CONF_AMBER_FEED_IN_FORECAST), "forecasts", []
            )
            or []
        )

        # Solcast
        data.solcast_today = (
            self._read_attribute(
                self._get_entity_id(CONF_SOLCAST_FORECAST_TODAY),
                "detailedForecast",
                [],
            )
            or []
        )
        data.solcast_tomorrow = (
            self._read_attribute(
                self._get_entity_id(CONF_SOLCAST_FORECAST_TOMORROW),
                "detailedForecast",
                [],
            )
            or []
        )
