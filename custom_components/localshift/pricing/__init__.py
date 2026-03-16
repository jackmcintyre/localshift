"""Pricing provider module.

Provides a unified interface for different pricing data sources (Amber, Amber Express).
"""

from __future__ import annotations

from .provider import AmberExpressProvider, AmberProvider, PricingProvider
from .types import ForecastSlot

__all__ = [
    "create_provider",
    "PricingProvider",
    "ForecastSlot",
    "AmberProvider",
    "AmberExpressProvider",
]


def create_provider(source: str) -> PricingProvider:
    """Create the appropriate pricing provider based on config."""
    if source == "amber_express":
        return AmberExpressProvider()
    return AmberProvider()
