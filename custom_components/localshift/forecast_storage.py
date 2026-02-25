"""Forecast storage using HA Storage API to avoid recorder 16KB limit.

Issue #231: Entity attributes are limited to 16KB by the recorder.
This module stores forecast data in HA's persistent storage which has
no size limit, while keeping minimal summary attributes on entities.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
STORAGE_KEY = f"{DOMAIN}.forecast_data"


class ForecastStorage:
    """Manages persistent storage of forecast data.

    Stores the full forecast data (daily_forecast, grid_interaction, etc.)
    in HA's Storage API, which has no size limit unlike entity attributes.

    The sensors expose only minimal summary attributes, while the full
    data is retrieved via service calls.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize forecast storage.

        Args:
            hass: Home Assistant instance
            entry: Config entry for this integration
        """
        self._hass = hass
        self._entry = entry
        self._store = Store[dict[str, Any]](hass, STORAGE_VERSION, STORAGE_KEY)
        self._data: dict[str, Any] = {}
        self._loaded = False

    async def async_load(self) -> None:
        """Load persisted forecast data from storage.

        Called during coordinator startup.
        """
        if self._loaded:
            return

        try:
            stored = await self._store.async_load()
            if stored is not None:
                self._data = stored
                _LOGGER.info(
                    "Loaded forecast storage: %d keys",
                    len(self._data)
                )
            else:
                self._data = {}
                _LOGGER.info("No existing forecast storage found, starting fresh")

            self._loaded = True
        except Exception as e:
            _LOGGER.warning("Failed to load forecast storage: %s", e)
            self._data = {}
            self._loaded = True

    async def async_save(self, data: dict[str, Any]) -> None:
        """Save forecast data to storage.

        Args:
            data: Dictionary containing forecast data to persist
        """
        try:
            self._data = data
            await self._store.async_save(data)
            _LOGGER.debug("Saved forecast data to storage")
        except Exception as e:
            _LOGGER.warning("Failed to save forecast storage: %s", e)

    def get_data(self) -> dict[str, Any]:
        """Get the current forecast data.

        Returns:
            Dictionary with forecast data
        """
        return self._data

    def get_forecast_slots(self) -> list[dict[str, Any]]:
        """Get forecast slots for dashboard.

        Returns:
            List of forecast slot dictionaries
        """
        return self._data.get("forecast_slots", [])

    def get_soc_series(self) -> list[dict[str, Any]]:
        """Get SOC time series for graphing.

        Returns:
            List of {time, soc} dictionaries
        """
        return self._data.get("soc_series", [])

    def get_grid_interaction(self) -> list[dict[str, Any]]:
        """Get grid interaction data.

        Returns:
            List of grid interaction slot dictionaries
        """
        return self._data.get("grid_interaction", [])

    def get_buy_prices(self) -> list[dict[str, Any]]:
        """Get buy price time series.

        Returns:
            List of {time, price} dictionaries
        """
        return self._data.get("buy_prices", [])

    def get_sell_prices(self) -> list[dict[str, Any]]:
        """Get sell price time series.

        Returns:
            List of {time, price} dictionaries
        """
        return self._data.get("sell_prices", [])

    def get_forecast_hourly(self) -> list[dict[str, Any]]:
        """Get hourly forecast summary.

        Returns:
            List of hourly summary dictionaries
        """
        return self._data.get("forecast_hourly", [])

    async def async_clear(self) -> None:
        """Clear all stored forecast data."""
        self._data = {}
        await self._store.async_save({})
        _LOGGER.info("Cleared forecast storage")