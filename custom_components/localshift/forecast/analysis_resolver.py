"""Cross-day confidence resolver for solar forecast analysis.

Provides a single interface to look up per-period confidence from
today and tomorrow SolcastAnalysis objects.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from custom_components.localshift.forecast.solcast_analysis import (
    get_confidence_for_period,
)


class ConfidenceResolver:
    """Resolves per-period confidence across today and tomorrow analyses.

    Uses date-based matching: if the slot date matches the analysis date,
    use that analysis for confidence lookup. Falls back to day_confidence.
    """

    def __init__(
        self,
        analysis_today: Any | None,
        analysis_tomorrow: Any | None,
        absent_confidence: float = 1.0,
    ) -> None:
        self._today = analysis_today
        self._tomorrow = analysis_tomorrow
        self._absent_confidence = absent_confidence

    def get_confidence(self, period_start: datetime) -> float:
        """Get confidence for a specific period, selecting the right analysis by date."""
        slot_date = period_start.date()

        # Check today's analysis
        if self._today and self._today.intervals:
            for interval in self._today.intervals:
                if interval.period_start.date() == slot_date:
                    return get_confidence_for_period(self._today, period_start, absent_confidence=self._absent_confidence)

        # Check tomorrow's analysis
        if self._tomorrow and self._tomorrow.intervals:
            for interval in self._tomorrow.intervals:
                if interval.period_start.date() == slot_date:
                    return get_confidence_for_period(self._tomorrow, period_start, absent_confidence=self._absent_confidence)

        # Fallback: try today's day_confidence, then tomorrow's, then absent_confidence
        if self._today:
            return get_confidence_for_period(self._today, period_start, absent_confidence=self._absent_confidence)
        if self._tomorrow:
            return get_confidence_for_period(self._tomorrow, period_start, absent_confidence=self._absent_confidence)
        return self._absent_confidence

    def get_analysis_for_period(self, period_start: datetime) -> Any | None:
        """Return the SolcastAnalysis object that covers the given period."""
        slot_date = period_start.date()

        if self._today and self._today.intervals:
            for interval in self._today.intervals:
                if interval.period_start.date() == slot_date:
                    return self._today

        if self._tomorrow and self._tomorrow.intervals:
            for interval in self._tomorrow.intervals:
                if interval.period_start.date() == slot_date:
                    return self._tomorrow

        return self._today
