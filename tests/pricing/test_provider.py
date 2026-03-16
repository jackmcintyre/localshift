"""Tests for pricing provider protocol."""

from typing import TYPE_CHECKING

import pytest

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


def test_amber_provider_read_forecasts_not_implemented():
    """Test AmberProvider.read_forecasts raises NotImplementedError."""
    from unittest.mock import MagicMock

    from custom_components.localshift.pricing.provider import AmberProvider

    provider = AmberProvider()
    hass = MagicMock()

    with pytest.raises(NotImplementedError):
        provider.read_forecasts(hass, "sensor.100h_general_price")


def test_amber_provider_is_spike_not_implemented():
    """Test AmberProvider.is_spike raises NotImplementedError."""
    from custom_components.localshift.pricing.provider import AmberProvider

    provider = AmberProvider()

    with pytest.raises(NotImplementedError):
        provider.is_spike({})
