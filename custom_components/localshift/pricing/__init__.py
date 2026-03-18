"""Pricing provider module.

Provides a unified interface for different pricing data sources (Amber, Amber Express).
"""

from __future__ import annotations

from .provider import AmberExpressProvider, AmberProvider, PricingProvider
from .types import ForecastSlot

PRICING_SOURCE_AMBER = "amber"
PRICING_SOURCE_AMBER_EXPRESS = "amber_express"

__all__ = [
    "create_provider",
    "PricingProvider",
    "ForecastSlot",
    "AmberProvider",
    "AmberExpressProvider",
    "PRICING_SOURCE_AMBER",
    "PRICING_SOURCE_AMBER_EXPRESS",
]


def create_provider(source: str) -> PricingProvider:
    """Create the appropriate pricing provider based on config."""
    if source == PRICING_SOURCE_AMBER_EXPRESS:
        return AmberExpressProvider()
    return AmberProvider()
