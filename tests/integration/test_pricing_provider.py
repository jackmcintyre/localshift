"""Integration tests for pricing provider with StateReader."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from custom_components.localshift.pricing import (
    PRICING_SOURCE_AMBER,
    PRICING_SOURCE_AMBER_EXPRESS,
    create_provider,
)
from custom_components.localshift.pricing.types import ForecastSlot


class TestPricingProviderIntegration:
    """Test that providers work correctly."""

    def test_amber_provider_reads_forecasts(self):
        """Amber provider reads from separate forecast entity."""
        provider = create_provider(PRICING_SOURCE_AMBER)

        hass = MagicMock()
        forecast_state = MagicMock()
        forecast_state.attributes = {
            "forecasts": [
                {
                    "start_time": "2026-03-16T12:00:00+00:00",
                    "duration": 30,
                    "per_kwh": 0.15,
                    "spike_status": "none",
                },
            ]
        }
        hass.states.get.return_value = forecast_state

        slots = provider.read_forecasts(hass, "sensor.100h_general_price")

        assert len(slots) == 1
        assert slots[0].per_kwh == 0.15
        assert slots[0].is_spike is False
        assert slots[0].source_type == "amber"

    def test_amber_express_provider_reads_forecasts(self):
        """Amber Express provider reads from _detailed entity."""
        provider = create_provider(PRICING_SOURCE_AMBER_EXPRESS)

        hass = MagicMock()
        detailed_state = MagicMock()
        detailed_state.attributes = {
            "forecasts": [
                {
                    "start_time": "2026-03-16T12:00:00+11:00",
                    "duration": 30,
                    "per_kwh": 0.20,
                    "demand_window": False,
                },
                {
                    "start_time": "2026-03-16T12:30:00+11:00",
                    "duration": 30,
                    "per_kwh": 2.50,
                    "demand_window": True,
                },
            ]
        }
        hass.states.get.return_value = detailed_state

        slots = provider.read_forecasts(hass, "sensor.amber_express_100h_general_price")

        assert len(slots) == 2
        assert slots[0].per_kwh == 0.20
        assert slots[0].is_spike is False
        assert slots[1].per_kwh == 2.50
        assert slots[1].is_spike is True
        assert slots[0].source_type == "amber_express"

    def test_both_providers_normalize_to_same_format(self):
        """Both providers produce identical ForecastSlot structure."""
        from homeassistant.util import dt as dt_util

        amber_provider = create_provider(PRICING_SOURCE_AMBER)
        express_provider = create_provider(PRICING_SOURCE_AMBER_EXPRESS)

        now = dt_util.now()
        test_time = now.replace(hour=12, minute=0, second=0, microsecond=0)

        hass_amber = MagicMock()
        state_amber = MagicMock()
        state_amber.attributes = {
            "forecasts": [
                {
                    "start_time": test_time.isoformat(),
                    "duration": 30,
                    "per_kwh": 0.25,
                    "spike_status": "none",
                },
            ]
        }
        hass_amber.states.get.return_value = state_amber

        hass_express = MagicMock()
        state_express = MagicMock()
        state_express.attributes = {
            "forecasts": [
                {
                    "start_time": test_time.isoformat(),
                    "duration": 30,
                    "per_kwh": 0.25,
                    "demand_window": False,
                },
            ]
        }
        hass_express.states.get.return_value = state_express

        amber_slots = amber_provider.read_forecasts(
            hass_amber, "sensor.100h_general_price"
        )
        express_slots = express_provider.read_forecasts(
            hass_express, "sensor.amber_express_100h_general_price"
        )

        assert len(amber_slots) == len(express_slots) == 1

        amber_slot = amber_slots[0]
        express_slot = express_slots[0]

        assert type(amber_slot) == type(express_slot) == ForecastSlot
        assert amber_slot.per_kwh == express_slot.per_kwh == 0.25
        assert amber_slot.duration == express_slot.duration == 30

    def test_amber_provider_detects_spike(self):
        """Amber provider correctly identifies spike status."""
        provider = create_provider(PRICING_SOURCE_AMBER)

        hass = MagicMock()
        forecast_state = MagicMock()
        forecast_state.attributes = {
            "forecasts": [
                {
                    "start_time": "2026-03-16T14:00:00+00:00",
                    "duration": 30,
                    "per_kwh": 3.00,
                    "spike_status": "spike",
                },
                {
                    "start_time": "2026-03-16T14:30:00+00:00",
                    "duration": 30,
                    "per_kwh": 0.10,
                    "spike_status": "none",
                },
            ]
        }
        hass.states.get.return_value = forecast_state

        slots = provider.read_forecasts(hass, "sensor.100h_general_price")

        assert len(slots) == 2
        assert slots[0].is_spike is True
        assert slots[1].is_spike is False

    def test_amber_express_provider_detects_demand_window_spike(self):
        """Amber Express provider uses demand_window as spike indicator."""
        provider = create_provider(PRICING_SOURCE_AMBER_EXPRESS)

        hass = MagicMock()
        detailed_state = MagicMock()
        detailed_state.attributes = {
            "forecasts": [
                {
                    "start_time": "2026-03-16T14:00:00+10:30",
                    "duration": 30,
                    "per_kwh": 3.50,
                    "demand_window": True,
                },
                {
                    "start_time": "2026-03-16T15:00:00+10:30",
                    "duration": 30,
                    "per_kwh": 0.15,
                    "demand_window": False,
                },
            ]
        }
        hass.states.get.return_value = detailed_state

        slots = provider.read_forecasts(hass, "sensor.amber_express_100h_general_price")

        assert len(slots) == 2
        assert slots[0].is_spike is True
        assert slots[1].is_spike is False

    def test_create_provider_returns_correct_type(self):
        """create_provider returns appropriate provider instance."""
        amber = create_provider(PRICING_SOURCE_AMBER)
        express = create_provider(PRICING_SOURCE_AMBER_EXPRESS)

        from custom_components.localshift.pricing import AmberProvider
        from custom_components.localshift.pricing.provider import AmberExpressProvider

        assert isinstance(amber, AmberProvider)
        assert isinstance(express, AmberExpressProvider)

    def test_create_provider_default_returns_amber(self):
        """create_provider with unknown source returns AmberProvider."""
        from custom_components.localshift.pricing.provider import AmberProvider

        provider = create_provider("unknown_source")
        assert isinstance(provider, AmberProvider)

    def test_provider_entity_prefix(self):
        """Providers have correct entity_prefix for forecast reading."""
        from custom_components.localshift.pricing.provider import (
            AmberExpressProvider,
            AmberProvider,
        )

        amber = AmberProvider()
        express = AmberExpressProvider()

        assert amber.entity_prefix == "sensor.100h_"
        assert express.entity_prefix == "sensor.amber_express_100h_"
