"""Normalized data types for pricing providers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class ForecastSlot:
    """Canonical forecast data structure used throughout LocalShift.

    All pricing providers normalize their data to this format,
    ensuring consumers have a consistent interface.
    """

    start_time: datetime
    duration: int
    per_kwh: float
    is_spike: bool
    source_type: str
