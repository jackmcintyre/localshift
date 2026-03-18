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


class _ProviderMixin:
    """Shared helper methods for pricing providers."""

    _VALID_DURATIONS = (5, 15, 30)

    def _normalize_slot(self, raw: dict[str, Any]) -> ForecastSlot:
        """Convert raw forecast dict to ForecastSlot."""
        duration = raw.get("duration")
        if duration is None:
            duration = self._infer_duration_minutes(raw)
        return ForecastSlot(
            start_time=self._parse_timestamp(raw["start_time"]),
            duration=int(duration),
            per_kwh=raw["per_kwh"],
            is_spike=self.is_spike(raw),  # type: ignore[attr-defined]
            source_type=self.name,  # type: ignore[attr-defined]
        )

    def _infer_duration_minutes(self, raw: dict[str, Any]) -> int:
        """Infer slot duration from start_time/end_time timestamps.

        Returns the closest valid duration (5, 15, or 30 minutes),
        or 30 as fallback when end_time is missing or unparseable.
        """
        end_time_str = raw.get("end_time")
        if end_time_str is None:
            return 30
        try:
            start_dt = self._parse_timestamp(raw["start_time"])
            end_dt = self._parse_timestamp(end_time_str)
            delta_minutes = (end_dt - start_dt).total_seconds() / 60
            if delta_minutes <= 0:
                return 5
            return min(self._VALID_DURATIONS, key=lambda d: abs(d - delta_minutes))
        except (ValueError, TypeError, KeyError):
            return 30

    def _read_attribute(
        self, hass: HomeAssistant, entity_id: str, attr: str, default: Any
    ) -> Any:
        """Read an attribute from a Home Assistant entity."""
        state = hass.states.get(entity_id)
        if state is None:
            _LOGGER.debug("Entity not found: %s", entity_id)
            return default
        return state.attributes.get(attr, default)

    def _parse_timestamp(self, ts: str) -> datetime:
        """Parse ISO timestamp to timezone-aware datetime."""
        parsed = dt_util.parse_datetime(ts)
        if parsed is None:
            raise ValueError(f"Invalid timestamp: {ts}")
        return parsed


class AmberProvider(_ProviderMixin):
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
        """Read forecasts from separate forecast entity."""
        forecast_entity = price_entity_id.replace("_price", "_forecast")
        raw_forecasts = self._read_attribute(hass, forecast_entity, "forecasts", [])

        if not raw_forecasts:
            _LOGGER.warning("No forecasts found on %s", forecast_entity)
            return []

        slots = []
        for raw in raw_forecasts:
            try:
                slots.append(self._normalize_slot(raw))
            except (KeyError, ValueError, TypeError) as e:
                _LOGGER.warning("Skipping malformed forecast slot: %s", e)
                continue
        return slots

    def is_spike(self, forecast_entry: dict[str, Any]) -> bool:
        """Check if entry represents a spike (Amber uses spike_status)."""
        return forecast_entry.get("spike_status") == "spike"


class AmberExpressProvider(_ProviderMixin):
    """Amber Express pricing provider."""

    @property
    def name(self) -> str:
        return "amber_express"

    @property
    def entity_prefix(self) -> str:
        return "sensor.amber_express_100h_"

    def read_forecasts(
        self, hass: HomeAssistant, price_entity_id: str
    ) -> list[ForecastSlot]:
        """Read forecasts from _price_detailed entity with fallback.

        Auto-corrects non-Express entity IDs by adding the amber_express_ prefix.
        This handles the case where _get_entity_id falls back to non-Express defaults.
        """
        # Auto-correct entity ID if missing Express prefix
        # e.g., sensor.100h_general_price → sensor.amber_express_100h_general_price
        if price_entity_id.startswith("sensor.100h_"):
            corrected = price_entity_id.replace(
                "sensor.100h_", "sensor.amber_express_100h_", 1
            )
            _LOGGER.warning(
                "Auto-correcting Express entity ID: %s → %s", price_entity_id, corrected
            )
            price_entity_id = corrected

        detailed_entity = price_entity_id.replace("_price", "_price_detailed")
        raw_forecasts = self._read_attribute(hass, detailed_entity, "forecasts", [])

        if not raw_forecasts:
            _LOGGER.debug("%s has no forecasts, trying simple entity", detailed_entity)
            raw_forecasts = self._read_attribute(hass, price_entity_id, "forecast", [])

        if not raw_forecasts:
            _LOGGER.warning("No forecasts found for %s", price_entity_id)
            return []

        slots = []
        for raw in raw_forecasts:
            try:
                slots.append(self._normalize_slot(raw))
            except (KeyError, ValueError, TypeError) as e:
                _LOGGER.warning("Skipping malformed forecast slot: %s", e)
                continue
        return slots

    def is_spike(self, forecast_entry: dict[str, Any]) -> bool:
        """Check if entry is a spike (Express uses demand_window)."""
        return forecast_entry.get("demand_window") is True
