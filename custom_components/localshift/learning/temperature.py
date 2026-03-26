from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from ..const import CONF_WEATHER_ENTITY

_LOGGER = logging.getLogger(__name__)

# Forecast cache settings
FORECAST_CACHE_TTL = timedelta(minutes=30)


@dataclass
class TemperatureForecast:
    """Temperature forecast for a time slot.

    Attributes:
        slot_time: The datetime this forecast applies to
        temperature: Forecasted temperature in °C
        condition: Weather condition (sunny, cloudy, etc.)

    """

    slot_time: datetime
    temperature: float | None = None
    condition: str = "unknown"


class TemperatureForecastProvider:
    """Provides temperature forecasts with caching and parsing helpers."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry[Any],
        weather_entity_id: str | None,
    ) -> None:
        self.hass = hass
        self.entry = entry
        self._weather_entity_id = weather_entity_id or ""
        self._cached_forecasts: list[TemperatureForecast] = []
        self._forecast_cache_time: datetime | None = None

    @property
    def weather_entity_id(self) -> str:
        return self._weather_entity_id

    def set_weather_entity_id(self, weather_entity_id: str | None) -> None:
        self._weather_entity_id = weather_entity_id or ""

    def get_temperature_forecast(self) -> list[TemperatureForecast]:
        """Fetch forecasted temperatures from weather entity.

        Returns:
            List of TemperatureForecast objects for upcoming hours.

        """
        weather_entity = self._weather_entity_id
        if not weather_entity:
            _LOGGER.debug("No weather entity configured")
            return []

        state = self.hass.states.get(weather_entity)
        if state is None:
            _LOGGER.warning("Weather entity %s not found", weather_entity)
            return []

        forecasts: list[TemperatureForecast] = []

        # Get forecast from weather entity attributes
        # Most weather integrations provide forecast in attributes
        forecast_data = state.attributes.get("forecast", [])
        now = dt_util.now()

        _LOGGER.debug(
            "Forecast data for %s: %d entries, now=%s",
            weather_entity,
            len(forecast_data) if forecast_data else 0,
            now.isoformat(),
        )

        for forecast_entry in forecast_data:
            # Parse forecast datetime
            forecast_time_str = forecast_entry.get("datetime")
            if not forecast_time_str:
                continue

            try:
                forecast_time = dt_util.parse_datetime(forecast_time_str)
                if forecast_time is None:
                    continue
            except (ValueError, TypeError):
                continue

            # Only include forecasts for the next 24 hours
            hours_ahead = (forecast_time - now).total_seconds() / 3600
            if hours_ahead < 0 or hours_ahead > 24:
                continue

            temperature = forecast_entry.get("temperature")
            condition = forecast_entry.get("condition", "unknown")

            forecasts.append(
                TemperatureForecast(
                    slot_time=forecast_time,
                    temperature=temperature,
                    condition=condition,
                )
            )

        _LOGGER.debug(
            "Got %d temperature forecasts from %s (legacy attribute)",
            len(forecasts),
            weather_entity,
        )

        return forecasts

    def _refresh_weather_entity_from_config(self) -> str:
        """Get the current weather entity from config entry.

        Always reads fresh from config to pick up user changes without
        requiring a restart. Checks options first (Configure UI), then data.

        Returns:
            Current weather entity ID from config, or empty string.

        """
        return self.entry.options.get(CONF_WEATHER_ENTITY, "") or self.entry.data.get(
            CONF_WEATHER_ENTITY, ""
        )

    async def async_get_temperature_forecast(
        self, force_refresh: bool = False
    ) -> list[TemperatureForecast]:
        """Fetch forecasted temperatures using weather.get_forecasts service.

        Uses Home Assistant's modern weather.get_forecasts service (HA 2024.3+)
        with caching to avoid excessive service calls.

        Args:
            force_refresh: If True, bypass cache and fetch fresh data

        Returns:
            List of TemperatureForecast objects for upcoming hours.

        """
        # Always get fresh entity ID from config to pick up user changes
        weather_entity = self._refresh_weather_entity_from_config()
        if not weather_entity:
            _LOGGER.debug("No weather entity configured")
            return []

        # Update cached entity ID if changed
        if weather_entity != self._weather_entity_id:
            _LOGGER.info(
                "Weather entity changed from %s to %s, clearing forecast cache",
                self._weather_entity_id,
                weather_entity,
            )
            self._weather_entity_id = weather_entity
            # Clear cache to force fresh fetch with new entity
            self._cached_forecasts = []
            self._forecast_cache_time = None

        now = dt_util.now()

        # Return cached forecasts if still valid
        if (
            not force_refresh
            and self._forecast_cache_time is not None
            and self._cached_forecasts
            and (now - self._forecast_cache_time) < FORECAST_CACHE_TTL
        ):
            _LOGGER.debug(
                "Returning %d cached temperature forecasts (age: %s)",
                len(self._cached_forecasts),
                now - self._forecast_cache_time,
            )
            return self._cached_forecasts

        forecasts: list[TemperatureForecast] = []

        try:
            response = await self.hass.services.async_call(
                "weather",
                "get_forecasts",
                {"entity_id": weather_entity, "type": "hourly"},
                blocking=True,
                return_response=True,
            )

            _LOGGER.info(
                "weather.get_forecasts response for %s: %s",
                weather_entity,
                "found" if response else "None",
            )

            if response and weather_entity in response:
                forecast_data = self._extract_forecast_list(response, weather_entity)
                if forecast_data:
                    forecasts = self._parse_forecast_entries(forecast_data, now)
            else:
                _LOGGER.debug(
                    "No response from weather.get_forecasts for entity %s, "
                    "trying legacy attribute",
                    weather_entity,
                )

        except Exception as e:
            _LOGGER.debug(
                "Failed to fetch forecasts via weather.get_forecasts service: %s, "
                "falling back to legacy attribute",
                e,
            )

        if not forecasts:
            forecasts = self.get_temperature_forecast()

        self._cached_forecasts = forecasts
        self._forecast_cache_time = now

        return forecasts

    def _extract_forecast_list(
        self, response: dict, weather_entity: str
    ) -> list | None:
        """Extract forecast list from API response.

        Args:
            response: Service call response dictionary
            weather_entity: Weather entity ID

        Returns:
            List of forecast entries or None

        """
        forecast_data = response.get(weather_entity)
        if forecast_data is None:
            return None

        _LOGGER.info(
            "forecast_data type=%s, len=%s, keys=%s",
            type(forecast_data).__name__,
            len(forecast_data) if isinstance(forecast_data, list) else "N/A",
            list(forecast_data.keys()) if isinstance(forecast_data, dict) else "N/A",
        )

        if isinstance(forecast_data, dict):
            _LOGGER.info("forecast_data is dict, checking for forecast/hourly keys")
            if "forecast" in forecast_data:
                forecast_data = forecast_data["forecast"]
                _LOGGER.info(
                    "Found 'forecast' key with %d entries",
                    len(forecast_data) if isinstance(forecast_data, list) else 0,
                )
            elif "hourly" in forecast_data:
                forecast_data = forecast_data["hourly"]
                _LOGGER.info(
                    "Found 'hourly' key with %d entries",
                    len(forecast_data) if isinstance(forecast_data, list) else 0,
                )

        if isinstance(forecast_data, list):
            return forecast_data

        _LOGGER.debug(
            "Unexpected forecast_data format: %s",
            type(forecast_data).__name__,
        )
        return None

    def _parse_forecast_entries(
        self, forecast_data: list, now: datetime
    ) -> list[TemperatureForecast]:
        """Parse forecast entries into TemperatureForecast objects.

        Args:
            forecast_data: List of forecast entry dictionaries
            now: Current datetime for filtering

        Returns:
            List of TemperatureForecast objects

        """
        forecasts: list[TemperatureForecast] = []
        parse_failed_count = 0
        skipped_no_datetime = 0
        filtered_count = 0

        _LOGGER.info(
            "Processing %d forecast entries, first entry keys: %s",
            len(forecast_data),
            list(forecast_data[0].keys())
            if forecast_data and isinstance(forecast_data[0], dict)
            else "empty",
        )

        for i, forecast_entry in enumerate(forecast_data):
            result = self._parse_single_forecast_entry(
                forecast_entry,
                now,
                i,
                parse_failed_count,
                skipped_no_datetime,
                filtered_count,
            )
            if result is None:
                continue
            forecast, parse_failed_count, skipped_no_datetime, filtered_count = result
            if forecast:
                forecasts.append(forecast)

        _LOGGER.info(
            "Fetched %d temperature forecasts via weather.get_forecasts service",
            len(forecasts),
        )
        return forecasts

    def _parse_single_forecast_entry(
        self,
        entry: dict,
        now: datetime,
        index: int,
        parse_failed_count: int,
        skipped_no_datetime: int,
        filtered_count: int,
    ) -> tuple[TemperatureForecast | None, int, int, int] | None:
        """Parse a single forecast entry.

        Args:
            entry: Forecast entry dictionary
            now: Current datetime for filtering
            index: Entry index for logging
            parse_failed_count: Running count of parse failures
            skipped_no_datetime: Running count of entries skipped for missing datetime
            filtered_count: Running count of filtered entries

        Returns:
            Tuple of (forecast or None, updated counts) or None if entry invalid

        """
        if not isinstance(entry, dict):
            return None

        forecast_time_str = entry.get("datetime")
        if not forecast_time_str:
            skipped_no_datetime += 1
            if skipped_no_datetime <= 2:
                _LOGGER.info(
                    "Entry missing 'datetime', keys: %s",
                    list(entry.keys()),
                )
            return (None, parse_failed_count, skipped_no_datetime, filtered_count)

        forecast_time = self._parse_forecast_datetime(
            forecast_time_str, index, parse_failed_count
        )
        if forecast_time is None:
            parse_failed_count += 1
            return (None, parse_failed_count, skipped_no_datetime, filtered_count)

        hours_ahead = (forecast_time - now).total_seconds() / 3600
        if hours_ahead < 0 or hours_ahead > 24:
            filtered_count += 1
            if filtered_count <= 3:
                _LOGGER.info(
                    "Filtering out forecast: time=%s, now=%s, hours_ahead=%.1f",
                    forecast_time.isoformat(),
                    now.isoformat(),
                    hours_ahead,
                )
            return (None, parse_failed_count, skipped_no_datetime, filtered_count)

        temperature = entry.get("temperature")
        condition = entry.get("condition", "unknown")

        forecast = TemperatureForecast(
            slot_time=forecast_time,
            temperature=temperature,
            condition=condition,
        )
        return (forecast, parse_failed_count, skipped_no_datetime, filtered_count)

    def _parse_forecast_datetime(
        self, time_str: str, index: int, parse_failed_count: int
    ) -> datetime | None:
        """Parse forecast datetime string.

        Args:
            time_str: Datetime string to parse
            index: Entry index for logging
            parse_failed_count: Current parse failure count

        Returns:
            Parsed datetime or None if parsing failed

        """
        try:
            from datetime import datetime as dt

            forecast_time = dt_util.parse_datetime(time_str)
            if forecast_time is None:
                try:
                    naive_dt = dt.fromisoformat(time_str)
                    forecast_time = dt_util.as_local(naive_dt)
                    if index == 0:
                        _LOGGER.info(
                            "First entry: datetime='%s' parsed as naive=%s, localized=%s",
                            time_str,
                            naive_dt,
                            forecast_time,
                        )
                except (ValueError, TypeError) as e:
                    if parse_failed_count < 3:
                        _LOGGER.info(
                            "Failed to parse datetime '%s': %s",
                            time_str,
                            e,
                        )
                    return None
            else:
                if forecast_time.tzinfo is None:
                    forecast_time = dt_util.as_local(forecast_time)
                if index == 0:
                    _LOGGER.info(
                        "First entry: datetime='%s' parsed as %s (tzinfo=%s)",
                        time_str,
                        forecast_time,
                        forecast_time.tzinfo,
                    )
            return forecast_time
        except (ValueError, TypeError) as e:
            if parse_failed_count < 3:
                _LOGGER.info(
                    "Exception parsing datetime '%s': %s",
                    time_str,
                    e,
                )
            return None

    def get_current_temperature(self) -> float | None:
        """Get current temperature from weather entity.

        Returns:
            Current temperature in °C, or None if unavailable.

        """
        weather_entity = self._weather_entity_id
        if not weather_entity:
            return None

        state = self.hass.states.get(weather_entity)
        if state is None:
            return None

        try:
            return float(state.attributes.get("temperature", 0))
        except (ValueError, TypeError):
            return None
