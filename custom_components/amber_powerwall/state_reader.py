"""State reading functionality for external entities."""

from __future__ import annotations

import logging
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

_LOGGER = logging.getLogger(__name__)


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

    def _read_solcast_forecast_list(self, entity_id: str) -> list[dict[str, Any]]:
        """Read Solcast forecast list using resilient attribute fallbacks."""
        state = self.hass.states.get(entity_id)
        if state is None:
            _LOGGER.debug("Solcast entity not found: %s", entity_id)
            # Fallback: auto-discover a likely Solcast sensor
            discovered = self._discover_solcast_entity(entity_id)
            if discovered:
                state = self.hass.states.get(discovered)
                _LOGGER.debug("Using discovered Solcast entity %s for %s", discovered, entity_id)
            else:
                return []

        if state.state in ("unknown", "unavailable"):
            _LOGGER.debug("Solcast entity unavailable: %s (%s)", entity_id, state.state)
            return []

        # Different Solcast versions expose different attribute names
        for attr_name in ("detailedForecast", "detailedHourly", "forecast"):
            value = state.attributes.get(attr_name)
            if isinstance(value, list) and value:
                return value

        _LOGGER.debug(
            "No usable Solcast forecast list on %s. Attribute keys: %s",
            entity_id,
            sorted(state.attributes.keys()),
        )
        return []

    def _discover_solcast_entity(self, requested_entity_id: str) -> str | None:
        """Try to discover a Solcast forecast sensor when configured ID is missing."""
        hint = "tomorrow" if "tomorrow" in requested_entity_id else "today"
        candidates = []

        for st in self.hass.states.async_all("sensor"):
            attrs = st.attributes
            has_forecast_list = any(
                isinstance(attrs.get(name), list)
                for name in ("detailedForecast", "detailedHourly", "forecast")
            )
            if not has_forecast_list:
                continue
            entity_id = st.entity_id.lower()
            if "solcast" in entity_id or "forecast" in entity_id:
                candidates.append(st.entity_id)

        if not candidates:
            return None

        # Prefer matching today/tomorrow hint
        for entity_id in candidates:
            if hint in entity_id.lower():
                return entity_id

        return candidates[0]

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
        today_entity = self._get_entity_id(CONF_SOLCAST_FORECAST_TODAY)
        tomorrow_entity = self._get_entity_id(CONF_SOLCAST_FORECAST_TOMORROW)

        data.solcast_today = self._read_solcast_forecast_list(today_entity)
        data.solcast_tomorrow = self._read_solcast_forecast_list(tomorrow_entity)

        _LOGGER.debug(
            "Solcast ingest: today_entity=%s (%s entries), tomorrow_entity=%s (%s entries)",
            today_entity,
            len(data.solcast_today),
            tomorrow_entity,
            len(data.solcast_tomorrow),
        )
