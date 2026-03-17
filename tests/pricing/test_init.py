"""Tests for pricing module factory."""

from custom_components.localshift.pricing import create_provider
from custom_components.localshift.pricing.provider import (
    AmberExpressProvider,
    AmberProvider,
)


def test_create_provider_returns_amber_by_default():
    """Test factory returns AmberProvider for unknown source."""
    provider = create_provider("unknown")
    assert isinstance(provider, AmberProvider)


def test_create_provider_returns_amber():
    """Test factory returns AmberProvider for amber source."""
    provider = create_provider("amber")
    assert isinstance(provider, AmberProvider)


def test_create_provider_returns_amber_express():
    """Test factory returns AmberExpressProvider for amber_express."""
    provider = create_provider("amber_express")
    assert isinstance(provider, AmberExpressProvider)
