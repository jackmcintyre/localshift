"""Tests for pricing provider protocol."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from custom_components.localshift.pricing.provider import PricingProvider
    from custom_components.localshift.pricing.types import ForecastSlot


def test_protocol_has_required_methods():
    """Test PricingProvider protocol defines required interface."""
    from custom_components.localshift.pricing.provider import PricingProvider

    assert hasattr(PricingProvider, "name")
    assert hasattr(PricingProvider, "entity_prefix")
    assert hasattr(PricingProvider, "read_forecasts")
    assert hasattr(PricingProvider, "is_spike")


def test_amber_provider_implements_protocol():
    """Test AmberProvider correctly implements PricingProvider."""
    from custom_components.localshift.pricing.provider import AmberProvider

    provider = AmberProvider()
    assert provider.name == "amber"
    assert provider.entity_prefix == "sensor.100h_"


def test_amber_provider_read_forecasts():
    """Test AmberProvider reads forecasts from separate entity."""
    from datetime import datetime, timezone
    from unittest.mock import MagicMock

    from custom_components.localshift.pricing.provider import AmberProvider

    provider = AmberProvider()

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
            {
                "start_time": "2026-03-16T12:30:00+00:00",
                "duration": 30,
                "per_kwh": 0.85,
                "spike_status": "spike",
            },
        ]
    }
    hass.states.get.return_value = forecast_state

    slots = provider.read_forecasts(hass, "sensor.100h_general_price")

    assert len(slots) == 2
    assert slots[0].per_kwh == 0.15
    assert slots[0].is_spike is False
    assert slots[1].per_kwh == 0.85
    assert slots[1].is_spike is True


def test_amber_provider_read_forecasts_entity_not_found():
    """Test AmberProvider returns empty list when entity not found."""
    from unittest.mock import MagicMock

    from custom_components.localshift.pricing.provider import AmberProvider

    provider = AmberProvider()
    hass = MagicMock()
    hass.states.get.return_value = None

    slots = provider.read_forecasts(hass, "sensor.100h_general_price")
    assert slots == []


def test_amber_provider_read_forecasts_no_forecasts_attr():
    """Test AmberProvider returns empty list when forecasts attribute missing."""
    from unittest.mock import MagicMock

    from custom_components.localshift.pricing.provider import AmberProvider

    provider = AmberProvider()
    hass = MagicMock()
    state = MagicMock()
    state.attributes = {}
    hass.states.get.return_value = state

    slots = provider.read_forecasts(hass, "sensor.100h_general_price")
    assert slots == []


def test_amber_provider_read_forecasts_skips_malformed():
    """Test AmberProvider skips malformed forecast entries."""
    from unittest.mock import MagicMock

    from custom_components.localshift.pricing.provider import AmberProvider

    provider = AmberProvider()
    hass = MagicMock()
    state = MagicMock()
    state.attributes = {
        "forecasts": [
            {"start_time": "bad-ts", "per_kwh": 0.15},  # invalid timestamp
            {
                "start_time": "2026-03-16T12:00:00+00:00",
                "duration": 30,
                "per_kwh": 0.20,
                "spike_status": "none",
            },
        ]
    }
    hass.states.get.return_value = state

    slots = provider.read_forecasts(hass, "sensor.100h_general_price")
    assert len(slots) == 1
    assert slots[0].per_kwh == 0.20


def test_amber_provider_is_spike_false():
    """Test AmberProvider.is_spike returns False for non-spike entries."""
    from custom_components.localshift.pricing.provider import AmberProvider

    provider = AmberProvider()
    assert provider.is_spike({"spike_status": "none"}) is False
    assert provider.is_spike({}) is False


def test_amber_provider_is_spike_true():
    """Test AmberProvider.is_spike returns True for spike entries."""
    from custom_components.localshift.pricing.provider import AmberProvider

    provider = AmberProvider()
    assert provider.is_spike({"spike_status": "spike"}) is True
