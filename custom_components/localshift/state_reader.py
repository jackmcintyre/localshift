"""State reading functionality for external entities."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
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
from .entity_validator import EntityValidator

_LOGGER = logging.getLogger(__name__)


class StateReader:
    """Handles reading state from external Home Assistant entities."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, entity_validator: EntityValidator
    ) -> None:
        """Initialize the state reader."""
        self.hass = hass
        self.entry = entry
        self.entity_validator = entity_validator

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

    def _read_float_optional(self, entity_id: str) -> float | None:
        """Read a float value from an entity's state, returning None if unavailable.

        This is used for price entities where we need to distinguish between
        a genuine $0 price and an unavailable entity.

        Returns:
            Float value if entity is available, None if unavailable or invalid.
        """
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable"):
            return None
        try:
            return float(state.state)
        except (ValueError, TypeError):
            return None

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

        # Pricing - read prices with unavailable detection
        # Issue #330: Track price availability to prevent incorrect mode decisions
        general_price_entity = self._get_entity_id(CONF_PRICING_GENERAL_PRICE)
        feed_in_price_entity = self._get_entity_id(CONF_PRICING_FEED_IN_PRICE)

        general_price_raw = self._read_float_optional(general_price_entity)
        feed_in_price_raw = self._read_float_optional(feed_in_price_entity)

        # Track if prices are available (Issue #330)
        # If either price is unavailable, we should not make grid charging decisions
        data.prices_available = (
            general_price_raw is not None and feed_in_price_raw is not None
        )

        if general_price_raw is None:
            _LOGGER.warning(
                "General price entity '%s' is unavailable - prices_available=False. "
                "Grid charging decisions will be deferred. Check entity configuration.",
                general_price_entity,
            )
            data.general_price = 0.0
        else:
            data.general_price = general_price_raw

        if feed_in_price_raw is None:
            _LOGGER.warning(
                "Feed-in price entity '%s' is unavailable - prices_available=False. "
                "Grid charging decisions will be deferred. Check entity configuration.",
                feed_in_price_entity,
            )
            data.feed_in_price = 0.0
        else:
            data.feed_in_price = feed_in_price_raw

        if data.prices_available:
            _LOGGER.debug(
                "Price entities available: buy=$%.3f/kWh, sell=$%.3f/kWh",
                data.general_price,
                data.feed_in_price,
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

    def check_automation_ready(
        self, data: CoordinatorData
    ) -> tuple[bool, dict[str, bool], list[str]]:
        """Check if all required inputs are valid for automation decisions.

        This validates that the system has all necessary data before making
        mode transition decisions. At startup, entities may not be fully
        populated, leading to incorrect mode inference.

        Issue #349: Prevents boost_charging inference from stale hardware state.

        Args:
            data: CoordinatorData with current state values.

        Returns:
            Tuple of (is_ready, status_dict, missing_list):
            - is_ready: True if all required inputs are valid
            - status_dict: Dict of input_name -> is_valid
            - missing_list: List of missing/invalid input names
        """
        status: dict[str, bool] = {}
        missing: list[str] = []

        # 1. SOC must be valid (> 0 and not None)
        # SOC of 0 indicates the entity hasn't populated yet
        soc_valid = data.soc is not None and data.soc > 0
        status["soc"] = soc_valid
        if not soc_valid:
            missing.append(f"SOC (current: {data.soc})")

        # 2. Prices must be available (Issue #330 already tracks this)
        # This checks if price entities are not unavailable
        prices_valid = data.prices_available
        status["prices_available"] = prices_valid
        if not prices_valid:
            missing.append("Price entities unavailable")

        # 3. Operation mode must be populated (not empty string)
        # Empty string indicates Teslemetry hasn't provided data yet
        operation_mode_valid = bool(
            data.operation_mode
        ) and data.operation_mode not in ("unknown", "unavailable")
        status["operation_mode"] = operation_mode_valid
        if not operation_mode_valid:
            missing.append(f"Operation mode (current: '{data.operation_mode}')")

        # 4. Backup reserve must be valid (>= 0)
        # Negative or None indicates data not yet populated
        backup_reserve_valid = (
            data.backup_reserve is not None and data.backup_reserve >= 0
        )
        status["backup_reserve"] = backup_reserve_valid
        if not backup_reserve_valid:
            missing.append(f"Backup reserve (current: {data.backup_reserve})")

        # 5. Solcast forecast should be ready (Issue #319 already tracks this)
        # We allow partial forecasts but not stale/unavailable
        forecast_valid = data.forecast_ready or data.forecast_status == "partial"
        status["forecast"] = forecast_valid
        if not forecast_valid:
            missing.append(f"Solcast forecast (status: {data.forecast_status})")

        # Overall ready state: all required inputs must be valid
        is_ready = all(status.values())

        # Update CoordinatorData with results
        data.automation_ready = is_ready
        data.automation_ready_status = status
        data.automation_ready_missing = missing

        if not is_ready:
            _LOGGER.warning(
                "Automation not ready - missing inputs: %s",
                ", ".join(missing) if missing else "none",
            )
        else:
            _LOGGER.info("Automation ready - all required inputs valid")

        return is_ready, status, missing

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
