"""Solcast analysis attribute extraction and confidence management.

This module extracts confidence data from Solcast v4.5.1's new 'analysis'
attribute on forecast entities. The analysis attribute provides:
- Overall confidence scores (0-1 scale)
- Estimate10/90 spreads (uncertainty quantification)
- Per-half-hour confidence intervals

Reference: https://github.com/BJReplay/ha-solcast-solar/releases/tag/v4.5.1
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from homeassistant.util import dt as dt_util

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


@dataclass
class ConfidenceInterval:
    """Per-period confidence data from Solcast analysis.

    Attributes:
        period_start: Start time of the 30-min period
        spread_kwh: Uncertainty (estimate90 - estimate10) for this period
        confidence: Confidence score (0-1), where 1.0 = high certainty

    """

    period_start: datetime
    spread_kwh: float
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary for storage/debugging."""
        return {
            "period_start": self.period_start.isoformat(),
            "spread_kwh": self.spread_kwh,
            "confidence": self.confidence,
        }


@dataclass
class SolcastAnalysis:
    """Solcast forecast analysis data extracted from entity attributes.

    The analysis attribute structure from Solcast v4.5.1:
    {
        "estimate10_kwh": float,      # 10th percentile total
        "estimate90_kwh": float,      # 90th percentile total
        "spread_kwh": float,          # Uncertainty (estimate90 - estimate10)
        "confidence": float,          # Overall score (0-1)
        "intervals": [...]            # Per-half-hour breakdown
    }

    Confidence calculation: 1.0 - spread_kwh / estimate90_kwh
    Interpretation:
    - 1.0 = high certainty (narrow spread)
    - 0.7 = moderate certainty
    - <0.5 = high uncertainty (wide spread)

    """

    entity_id: str
    last_updated: datetime
    day_confidence: float
    day_spread_kwh: float
    estimate10_kwh: float
    estimate90_kwh: float
    intervals: list[ConfidenceInterval] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary for storage/debugging."""
        return {
            "entity_id": self.entity_id,
            "last_updated": self.last_updated.isoformat(),
            "day_confidence": self.day_confidence,
            "day_spread_kwh": self.day_spread_kwh,
            "estimate10_kwh": self.estimate10_kwh,
            "estimate90_kwh": self.estimate90_kwh,
            "intervals": [iv.to_dict() for iv in self.intervals],
        }


def extract_analysis_from_entity(
    hass: HomeAssistant, entity_id: str
) -> SolcastAnalysis | None:
    """Extract Solcast analysis attribute from a forecast entity.

    Args:
        hass: Home Assistant instance
        entity_id: Entity ID of Solcast forecast (e.g., sensor.solcast_pv_forecast_forecast_today)

    Returns:
        SolcastAnalysis object if analysis attribute exists, None otherwise

    """
    state = hass.states.get(entity_id)
    if state is None:
        _LOGGER.debug("Entity %s not found", entity_id)
        return None

    analysis_attr = state.attributes.get("analysis")
    if analysis_attr is None or not isinstance(analysis_attr, dict):
        _LOGGER.debug(
            "Entity %s has no analysis attribute or not a dict (Solcast v4.5.1+ required)",
            entity_id,
        )
        return None

    try:
        # Extract day-level metrics
        day_confidence = float(analysis_attr.get("confidence", 1.0))
        day_spread_kwh = float(analysis_attr.get("spread_kwh", 0.0))
        estimate10_kwh = float(analysis_attr.get("estimate10_kwh", 0.0))
        estimate90_kwh = float(analysis_attr.get("estimate90_kwh", 0.0))

        # Extract per-interval confidence data
        intervals_raw = analysis_attr.get("intervals", [])
        intervals: list[ConfidenceInterval] = []

        if isinstance(intervals_raw, list):
            for interval_data in intervals_raw:
                if not isinstance(interval_data, dict):
                    continue

                period_start_str = interval_data.get("period_start")
                if period_start_str is None:
                    continue

                period_start = dt_util.parse_datetime(str(period_start_str))
                if period_start is None:
                    continue

                intervals.append(
                    ConfidenceInterval(
                        period_start=period_start,
                        spread_kwh=float(interval_data.get("spread_kwh", 0.0)),
                        confidence=float(interval_data.get("confidence", 1.0)),
                    )
                )

        last_updated = state.last_updated or dt_util.utcnow()

        return SolcastAnalysis(
            entity_id=entity_id,
            last_updated=last_updated,
            day_confidence=day_confidence,
            day_spread_kwh=day_spread_kwh,
            estimate10_kwh=estimate10_kwh,
            estimate90_kwh=estimate90_kwh,
            intervals=intervals,
        )

    except (ValueError, TypeError, AttributeError) as err:
        _LOGGER.warning(
            "Failed to parse analysis attribute from %s: %s",
            entity_id,
            err,
        )
        return None


def get_confidence_for_period(
    analysis: SolcastAnalysis | None, period_start: datetime
) -> float:
    """Get confidence score for a specific period.

    Args:
        analysis: SolcastAnalysis object (or None)
        period_start: Start time of the period to query

    Returns:
        Confidence score (0-1), defaults to 1.0 if no data available

    """
    if analysis is None:
        return 1.0

    if not analysis.intervals:
        # No intervals available, return day-level confidence
        return analysis.day_confidence

    # Find matching interval (allow 5-minute tolerance for rounding)
    period_start_local = dt_util.as_local(period_start)

    for interval in analysis.intervals:
        interval_start_local = dt_util.as_local(interval.period_start)
        time_diff = abs((period_start_local - interval_start_local).total_seconds())

        if time_diff <= 300:  # 5-minute tolerance
            return interval.confidence

    # No exact match found, return day-level confidence as fallback
    return analysis.day_confidence


def compute_weighted_confidence(
    analysis: SolcastAnalysis | None, start_time: datetime, hours_ahead: float
) -> float:
    """Compute weighted average confidence for a time window.

    Args:
        analysis: SolcastAnalysis object (or None)
        start_time: Start of the window
        hours_ahead: Duration of the window in hours

    Returns:
        Weighted average confidence (0-1), defaults to 1.0 if no data

    """
    if analysis is None or not analysis.intervals:
        return 1.0

    start_local = dt_util.as_local(start_time)
    end_local = start_local + dt_util.dt.timedelta(hours=hours_ahead)

    total_weight = 0.0
    weighted_sum = 0.0

    for interval in analysis.intervals:
        interval_start = dt_util.as_local(interval.period_start)
        interval_end = interval_start + dt_util.dt.timedelta(minutes=30)

        # Calculate overlap with query window
        overlap_start = max(start_local, interval_start)
        overlap_end = min(end_local, interval_end)
        overlap_seconds = (overlap_end - overlap_start).total_seconds()

        if overlap_seconds > 0:
            weight = overlap_seconds / 3600.0  # Convert to hours
            weighted_sum += interval.confidence * weight
            total_weight += weight

    if total_weight > 0:
        return weighted_sum / total_weight

    # Fallback to day-level confidence
    return analysis.day_confidence
