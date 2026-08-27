"""Forecast accuracy metrics persistence for computation engine."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant

from ..coordinator.data import CoordinatorData

_LOGGER = logging.getLogger(__name__)


class AccuracyMetricsStore:
    """Persist forecast accuracy metrics across Home Assistant restarts."""

    STORAGE_KEY = "localshift_forecast_accuracy_metrics"
    STORAGE_VERSION = 1

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._store_key = self.STORAGE_KEY
        self._store: Any = None

    async def async_initialize(self) -> None:
        """Initialize accuracy metrics storage."""
        try:
            from homeassistant.helpers.storage import Store

            self._store = Store(self._hass, self.STORAGE_VERSION, self._store_key)
            _LOGGER.info("Forecast accuracy metrics storage initialized")
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("Failed to initialize accuracy metrics storage: %s", exc)
            self._store = None

    async def async_load(self, data: CoordinatorData) -> None:
        """Load persisted accuracy metrics from storage."""
        if self._store is None:
            _LOGGER.debug("No accuracy metrics store available")
            return
        try:
            stored = await self._store.async_load()
            if not stored or not isinstance(stored, dict):
                return
            # Restore 9 scalar fields
            data.forecast_error_soc_15min = stored.get("forecast_error_soc_15min", 0.0)
            data.forecast_error_soc_1h = stored.get("forecast_error_soc_1h", 0.0)
            data.forecast_error_soc_4h = stored.get("forecast_error_soc_4h", 0.0)
            data.forecast_accuracy_soc_15min = stored.get(
                "forecast_accuracy_soc_15min", None
            )
            data.forecast_accuracy_soc_1h = stored.get("forecast_accuracy_soc_1h", None)
            data.forecast_accuracy_soc_4h = stored.get("forecast_accuracy_soc_4h", None)
            data.forecast_error_buy_price_1h = stored.get(
                "forecast_error_buy_price_1h", 0.0
            )
            data.forecast_error_sell_price_1h = stored.get(
                "forecast_error_sell_price_1h", 0.0
            )
            data.forecast_comparisons_made = stored.get("forecast_comparisons_made", 0)
            _LOGGER.info(
                "Loaded forecast accuracy metrics from storage (comparisons=%d)",
                data.forecast_comparisons_made,
            )
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("Failed to load forecast accuracy metrics: %s", exc)

    async def async_save(self, data: CoordinatorData) -> None:
        """Persist accuracy metrics to storage."""
        if self._store is None:
            return
        try:
            stored = {
                "forecast_error_soc_15min": data.forecast_error_soc_15min,
                "forecast_error_soc_1h": data.forecast_error_soc_1h,
                "forecast_error_soc_4h": data.forecast_error_soc_4h,
                "forecast_accuracy_soc_15min": data.forecast_accuracy_soc_15min,
                "forecast_accuracy_soc_1h": data.forecast_accuracy_soc_1h,
                "forecast_accuracy_soc_4h": data.forecast_accuracy_soc_4h,
                "forecast_error_buy_price_1h": data.forecast_error_buy_price_1h,
                "forecast_error_sell_price_1h": data.forecast_error_sell_price_1h,
                "forecast_comparisons_made": data.forecast_comparisons_made,
            }
            await self._store.async_save(stored)
            _LOGGER.debug(
                "Saved forecast accuracy metrics (comparisons=%d)",
                data.forecast_comparisons_made,
            )
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("Failed to save forecast accuracy metrics: %s", exc)
