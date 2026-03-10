"""State reading functionality for external entities."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

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
from .utils.validation import EntityValidator

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

    def _calculate_current_day_average_price(
        self, forecast: list[dict[str, Any]], now: datetime
    ) -> float:
        """Calculate average price from today's forecast slots.

        Issue #632: Used to compute assumed price for forecast extension.

        Args:
            forecast: List of forecast entries with per_kwh field
            now: Current datetime

        Returns:
            Average price in $/kWh, or default of $0.20 if no valid entries

        """
        if not forecast:
            return 0.20

        prices = []
        for entry in forecast:
            if not isinstance(entry, dict):
                continue

            price = entry.get("per_kwh")
            if price is None:
                continue

            start_time_str = entry.get("start_time")
            if not start_time_str:
                continue

            try:
                start_time = datetime.fromisoformat(start_time_str)
                if start_time <= now:
                    prices.append(float(price))
            except (ValueError, TypeError):
                continue

        if not prices:
            return 0.20

        return sum(prices) / len(prices)

    def _extend_forecast_with_assumed_prices(
        self, forecast: list[dict[str, Any]], now: datetime, assumed_price: float
    ) -> list[dict[str, Any]]:
        """Extend forecast to guarantee 24-hour planning horizon.

        Issue #632: Ensures optimizer can see tomorrow's solar opportunities
        even when price forecast (e.g., Amber free tier) provides only ~16 hours.

        Args:
            forecast: List of forecast entries
            now: Current datetime
            assumed_price: Price to use for extended entries ($/kWh)

        Returns:
            Extended forecast list with synthetic entries added if needed

        """
        if not forecast:
            return []

        last_entry = forecast[-1]
        if not isinstance(last_entry, dict):
            return forecast

        last_time_str = last_entry.get("start_time")
        if not last_time_str:
            return forecast

        try:
            last_time = datetime.fromisoformat(last_time_str)
        except (ValueError, TypeError):
            return forecast

        # Normalize timezone for comparison (Issue #632)
        # datetime.fromisoformat() may return timezone-aware datetime
        # while the 'now' parameter may be timezone-aware from dt_util.now()
        if last_time.tzinfo is None and now.tzinfo is not None:
            last_time = last_time.replace(tzinfo=now.tzinfo)

        target_time = now + timedelta(hours=24)
        last_duration = last_entry.get("duration", 30)
        last_entry_end = last_time + timedelta(minutes=last_duration)
        if last_entry_end >= target_time:
            return forecast

        extended = list(forecast)
        current_time = last_time + timedelta(minutes=30)

        while current_time < target_time:
            extended.append({
                "start_time": current_time.isoformat(),
                "duration": 30,
                "per_kwh": assumed_price,
            })
            current_time += timedelta(minutes=30)

        return extended

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

        # Issue #632: Extend forecasts to 24 hours if needed
        # This ensures the optimizer can see tomorrow's solar opportunities
        # even when price forecast (e.g., Amber free tier) provides only ~16 hours
        now_dt = dt_util.now()
        if data.general_forecast:
            avg_buy_price = self._calculate_current_day_average_price(
                data.general_forecast, now_dt
            )
            data.general_forecast = self._extend_forecast_with_assumed_prices(
                data.general_forecast, now_dt, avg_buy_price
            )
            _LOGGER.debug(
                "Extended general_forecast to %d entries using avg price $%.3f/kWh",
                len(data.general_forecast),
                avg_buy_price,
            )

        if data.feed_in_forecast:
            avg_sell_price = self._calculate_current_day_average_price(
                data.feed_in_forecast, now_dt
            )
            data.feed_in_forecast = self._extend_forecast_with_assumed_prices(
                data.feed_in_forecast, now_dt, avg_sell_price
            )
            _LOGGER.debug(
                "Extended feed_in_forecast to %d entries using avg price $%.3f/kWh",
                len(data.feed_in_forecast),
                avg_sell_price,
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
        self, data: CoordinatorData, suppress_warning: bool = False
    ) -> tuple[bool, dict[str, bool], list[str]]:
        """Check if all required inputs are valid for automation decisions.

        This validates that the system has all necessary data before making
        mode transition decisions. At startup, entities may not be fully
        populated, leading to incorrect mode inference.

        Issue #349: Prevents boost_charging inference from stale hardware state.
        Issue #551: Added suppress_warning to reduce log noise during startup.

        Args:
            data: CoordinatorData with current state values.
            suppress_warning: If True, log at DEBUG instead of WARNING level.

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
            log_level = logging.DEBUG if suppress_warning else logging.WARNING
            _LOGGER.log(
                log_level,
                "Automation not ready - missing inputs: %s",
                ", ".join(missing) if missing else "none",
            )
        else:
            _LOGGER.info("Automation ready - all required inputs valid")

        return is_ready, status, missing
