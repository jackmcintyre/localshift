"""State reading functionality for external entities."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_CLIMATE_CONTROL_ENTITIES,
    CONF_CLIMATE_ENTITIES,
    CONF_PRICING_FEED_IN_FORECAST,
    CONF_PRICING_FEED_IN_PRICE,
    CONF_PRICING_GENERAL_FORECAST,
    CONF_PRICING_GENERAL_PRICE,
    CONF_PRICING_PRICE_SPIKE,
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
    CONF_WEATHER_ENTITY,
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
                _LOGGER.debug(
                    "Using discovered Solcast entity %s for %s", discovered, entity_id
                )
            else:
                return []

        if state.state in ("unknown", "unavailable"):
            _LOGGER.debug("Solcast entity unavailable: %s (%s)", entity_id, state.state)
            return []

        # Different Solcast versions expose different attribute names
        for attr_name in ("detailedForecast", "detailedHourly", "forecast"):
            value = state.attributes.get(attr_name)
            if isinstance(value, list) and value:
                # Debug: log the data structure for first few entries
                _LOGGER.debug(
                    "SOLAR_DEBUG: %s using attribute '%s' with %d entries",
                    entity_id,
                    attr_name,
                    len(value),
                )
                if value:
                    _LOGGER.debug(
                        "SOLAR_DEBUG: %s first entry keys: %s",
                        entity_id,
                        list(value[0].keys())
                        if isinstance(value[0], dict)
                        else type(value[0]),
                    )
                    # Log sample values from first entry
                    if isinstance(value[0], dict):
                        _LOGGER.debug(
                            "SOLAR_DEBUG: %s first entry sample: period_start=%s, pv_estimate=%s, pv_estimate10=%s",
                            entity_id,
                            value[0].get("period_start") or value[0].get("start"),
                            value[0].get("pv_estimate"),
                            value[0].get("pv_estimate10"),
                        )
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

        # Pricing
        data.general_price = self._read_float(
            self._get_entity_id(CONF_PRICING_GENERAL_PRICE)
        )
        data.feed_in_price = self._read_float(
            self._get_entity_id(CONF_PRICING_FEED_IN_PRICE)
        )
        data.price_spike = self._read_bool(
            self._get_entity_id(CONF_PRICING_PRICE_SPIKE)
        )
        data.general_forecast = (
            self._read_attribute(
                self._get_entity_id(CONF_PRICING_GENERAL_FORECAST), "forecasts", []
            )
            or []
        )
        data.feed_in_forecast = (
            self._read_attribute(
                self._get_entity_id(CONF_PRICING_FEED_IN_FORECAST), "forecasts", []
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

        # Weather
        self._read_weather_state(data)

        # Climate entities (Thermal Manager - Issue #137, #63)
        self._read_climate_states(data)

    def _read_weather_state(self, data: CoordinatorData) -> None:
        """Read weather entity current state (temperature and condition).

        Populates weather-related fields in CoordinatorData for use by
        the weather correlation system.

        NOTE: Temperature forecast is fetched asynchronously via the coordinator's
        _refresh_weather_forecast() method, which uses the modern weather.get_forecasts
        service (HA 2024.3+). The forecast attribute on weather entities was deprecated
        and removed in HA 2024.3+.

        Args:
            data: CoordinatorData instance to populate with weather data.
        """
        # Get weather entity from options (preferred) or data (fallback)
        weather_entity = self.entry.options.get(
            CONF_WEATHER_ENTITY
        ) or self.entry.data.get(CONF_WEATHER_ENTITY)

        if not weather_entity:
            _LOGGER.debug("No weather entity configured")
            data.weather_entity_id = ""
            return

        data.weather_entity_id = weather_entity

        state = self.hass.states.get(weather_entity)
        if state is None:
            _LOGGER.warning("Weather entity %s not found", weather_entity)
            return

        if state.state in ("unknown", "unavailable"):
            _LOGGER.debug("Weather entity %s is %s", weather_entity, state.state)
            return

        # Read current temperature
        try:
            data.weather_temperature_current = float(
                state.attributes.get("temperature", 0)
            )
        except (ValueError, TypeError):
            data.weather_temperature_current = 0.0

        # Read current condition
        data.weather_condition = state.state

        # NOTE: Temperature forecast is no longer read from the deprecated 'forecast' attribute.
        # It's now fetched asynchronously via weather.get_forecasts service in the coordinator's
        # _refresh_weather_forecast() method, which populates data.weather_temperature_forecast.
        # The legacy 'forecast' attribute was removed in Home Assistant 2024.3+.

        _LOGGER.debug(
            "Weather state read: entity=%s, temp=%.1f°C, condition=%s",
            weather_entity,
            data.weather_temperature_current,
            data.weather_condition,
        )

    def _read_climate_states(self, data: CoordinatorData) -> None:
        """Read current state of all configured climate entities.

        Populates climate-related fields in CoordinatorData for use by
        the thermal manager system (Issue #137, #63).

        For each climate entity, reads:
        - state: The HVAC mode ("off", "cool", "heat", "dry", "auto")
        - hvac_action: Current action ("off", "cooling", "heating", "drying", "idle")
        - temperature: Target setpoint
        - current_temperature: Room temperature from entity

        Args:
            data: CoordinatorData instance to populate with climate data.
        """
        # Get configured climate entities from options (preferred) or data (fallback)
        climate_entities = self.entry.options.get(
            CONF_CLIMATE_ENTITIES, []
        ) or self.entry.data.get(CONF_CLIMATE_ENTITIES, [])

        control_entities = self.entry.options.get(
            CONF_CLIMATE_CONTROL_ENTITIES, []
        ) or self.entry.data.get(CONF_CLIMATE_CONTROL_ENTITIES, [])

        # Store the entity lists in coordinator data
        data.climate_entities = climate_entities if climate_entities else []
        data.climate_control_entities = control_entities if control_entities else []

        if not data.climate_entities:
            _LOGGER.debug("No climate entities configured for thermal manager")
            data.climate_states = {}
            return

        climate_states: dict[str, dict[str, Any]] = {}

        for entity_id in data.climate_entities:
            state = self.hass.states.get(entity_id)
            if state is None:
                _LOGGER.debug("Climate entity %s not found", entity_id)
                continue

            if state.state in ("unknown", "unavailable"):
                _LOGGER.debug("Climate entity %s is %s", entity_id, state.state)
                continue

            # Build climate state dict for this entity
            entity_state: dict[str, Any] = {
                "entity_id": entity_id,
                "state": state.state,  # HVAC mode: off, cool, heat, dry, auto
                "hvac_action": state.attributes.get("hvac_action", "unknown"),
                "setpoint": self._read_float_attr(state, "temperature"),
                "current_temperature": self._read_float_attr(
                    state, "current_temperature"
                ),
                "is_controlled": entity_id in data.climate_control_entities,
                "friendly_name": state.attributes.get("friendly_name", entity_id),
            }

            climate_states[entity_id] = entity_state

            _LOGGER.debug(
                "Climate state: %s mode=%s action=%s setpoint=%.1f°C current=%.1f°C controlled=%s",
                entity_id,
                entity_state["state"],
                entity_state["hvac_action"],
                entity_state["setpoint"],
                entity_state["current_temperature"] or 0.0,
                entity_state["is_controlled"],
            )

        data.climate_states = climate_states

        _LOGGER.debug(
            "Climate states read: %d entities (%d controlled)",
            len(climate_states),
            len(data.climate_control_entities),
        )

    def _read_float_attr(self, state: Any, attr: str, default: float = 0.0) -> float:
        """Read a float value from a state's attributes.

        Args:
            state: Home Assistant state object.
            attr: Attribute name to read.
            default: Default value if attribute missing or invalid.

        Returns:
            Float value from attribute, or default.
        """
        try:
            value = state.attributes.get(attr)
            if value is None:
                return default
            return float(value)
        except (ValueError, TypeError):
            return default
