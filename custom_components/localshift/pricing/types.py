"""Normalized data types for pricing providers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any


@dataclass(frozen=True)
class ForecastSlot:
    """Canonical forecast data structure used throughout LocalShift.

    All pricing providers normalize their data to this format,
    ensuring consumers have a consistent interface.

    Provides dict-compatible .get() method for backward compatibility
    with existing code that uses dict-style access patterns.
    """

    start_time: datetime
    duration: int
    per_kwh: float
    is_spike: bool
    source_type: str

    def get(self, key: str, default: Any = None) -> Any:
        """Dict-compatible accessor for backward compatibility.

        Supports all ForecastSlot fields plus computed 'end_time'.

        Args:
            key: Field name to retrieve.
            default: Value to return if field doesn't exist.

        Returns:
            Field value or default if field doesn't exist.
        """
        if key == "end_time":
            return self.start_time + timedelta(minutes=self.duration)
        return getattr(self, key, default)
