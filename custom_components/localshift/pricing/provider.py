"""Pricing provider protocol and implementations."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .types import ForecastSlot

if TYPE_CHECKING:
    pass  # pragma: no cover

_LOGGER = logging.getLogger(__name__)


class PricingProvider(Protocol):
    """Protocol for pricing data providers.

    Implementations handle provider-specific differences:
    - Entity ID construction
    - Forecast data location and format
    - Spike detection logic
    """

    @property
    def name(self) -> str:  # pragma: no cover
        """Provider identifier for logging/debugging."""
        ...

    @property
    def entity_prefix(self) -> str:  # pragma: no cover
        """Return entity prefix like 'sensor.100h_' or 'sensor.amber_express_100h_'."""
        ...

    def read_forecasts(  # pragma: no cover
        self, hass: HomeAssistant, price_entity_id: str
    ) -> list[ForecastSlot]:
        """Read and normalize forecast data from price entity.

        Args:
            hass: Home Assistant instance
            price_entity_id: The price sensor entity ID

        Returns:
            List of normalized ForecastSlot objects
        """
        ...

    def is_spike(self, forecast_entry: dict[str, Any]) -> bool:  # pragma: no cover
        """Check if a raw forecast entry represents a price spike.

        Args:
            forecast_entry: Raw forecast entry from provider

        Returns:
            True if this entry represents a spike
        """
        ...


class AmberProvider:
    """Amber pricing provider (original 100H integration)."""

    @property
    def name(self) -> str:
        return "amber"

    @property
    def entity_prefix(self) -> str:
        return "sensor.100h_"

    def read_forecasts(
        self, hass: HomeAssistant, price_entity_id: str
    ) -> list[ForecastSlot]:
        raise NotImplementedError

    def is_spike(self, entry: dict[str, Any]) -> bool:
        raise NotImplementedError
