"""Solcast confidence and accuracy sensors.

Issue #778: Diagnostic sensors for Solcast v4.5.1 analysis attribute,
providing visibility into forecast confidence and accuracy metrics.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import SensorStateClass
from homeassistant.helpers.entity import EntityCategory

from .base import LocalShiftSensorBase

if TYPE_CHECKING:
    pass


class SolcastConfidenceTodaySensor(LocalShiftSensorBase):
    """Sensor showing today's forecast confidence from Solcast analysis."""

    _attr_unique_id = "localshift_solcast_confidence_today"
    _attr_name = "Solcast Confidence Today"
    _attr_icon = "mdi:gauge"
    _attr_native_unit_of_measurement = "%"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_suggested_display_precision = 0

    def _update_from_coordinator(self) -> None:
        """Update sensor state from coordinator data."""
        analysis = self.coordinator.data.solcast_analysis_today
        if analysis:
            # Convert 0-1 confidence to 0-100%
            self._attr_native_value = round(analysis.day_confidence * 100, 0)
        else:
            self._attr_native_value = None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional attributes."""
        analysis = self.coordinator.data.solcast_analysis_today
        if not analysis:
            return {}

        attrs = {
            "spread_kwh": round(analysis.day_spread_kwh, 2),
            "estimate10_kwh": round(analysis.estimate10_kwh, 2),
            "estimate90_kwh": round(analysis.estimate90_kwh, 2),
            "last_updated": analysis.last_updated.isoformat(),
        }

        # Add hourly confidence breakdown (first 24 hours)
        if analysis.intervals:
            hourly_confidence = []
            for i, interval in enumerate(
                analysis.intervals[:48]
            ):  # Up to 24 hours (30-min intervals)
                if i % 2 == 0:  # Every 2nd interval = hourly
                    hourly_confidence.append({
                        "hour": i // 2,
                        "confidence": round(interval.confidence * 100, 0),
                        "spread_kwh": round(interval.spread_kwh, 2),
                    })
            attrs["hourly_confidence"] = hourly_confidence

        return attrs


class SolcastConfidenceTomorrowSensor(LocalShiftSensorBase):
    """Sensor showing tomorrow's forecast confidence from Solcast analysis."""

    _attr_unique_id = "localshift_solcast_confidence_tomorrow"
    _attr_name = "Solcast Confidence Tomorrow"
    _attr_icon = "mdi:gauge"
    _attr_native_unit_of_measurement = "%"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_suggested_display_precision = 0

    def _update_from_coordinator(self) -> None:
        """Update sensor state from coordinator data."""
        analysis = self.coordinator.data.solcast_analysis_tomorrow
        if analysis:
            # Convert 0-1 confidence to 0-100%
            self._attr_native_value = round(analysis.day_confidence * 100, 0)
        else:
            self._attr_native_value = None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional attributes."""
        analysis = self.coordinator.data.solcast_analysis_tomorrow
        if not analysis:
            return {}

        attrs = {
            "spread_kwh": round(analysis.day_spread_kwh, 2),
            "estimate10_kwh": round(analysis.estimate10_kwh, 2),
            "estimate90_kwh": round(analysis.estimate90_kwh, 2),
            "last_updated": analysis.last_updated.isoformat(),
        }

        # Add hourly confidence breakdown (first 24 hours)
        if analysis.intervals:
            hourly_confidence = []
            for i, interval in enumerate(
                analysis.intervals[:48]
            ):  # Up to 24 hours (30-min intervals)
                if i % 2 == 0:  # Every 2nd interval = hourly
                    hourly_confidence.append({
                        "hour": i // 2,
                        "confidence": round(interval.confidence * 100, 0),
                        "spread_kwh": round(interval.spread_kwh, 2),
                    })
            attrs["hourly_confidence"] = hourly_confidence

        return attrs


class ForecastAccuracyComparisonSensor(LocalShiftSensorBase):
    """Sensor comparing LocalShift and Solcast accuracy metrics.

    Shows combined accuracy score and provides detailed comparison in attributes.
    """

    _attr_unique_id = "localshift_forecast_accuracy_comparison"
    _attr_name = "Forecast Accuracy Comparison"
    _attr_icon = "mdi:compare"
    _attr_native_unit_of_measurement = "%"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_suggested_display_precision = 1

    def _update_from_coordinator(self) -> None:
        """Update sensor state from coordinator data."""
        # Get both accuracy metrics
        localshift_accuracy = self.coordinator.data.solar_forecast_accuracy
        solcast_mape = self.coordinator.data.solcast_mape

        # Calculate combined score (weighted average)
        # When both available, weight equally
        # When only one available, use that one
        if localshift_accuracy and solcast_mape is not None:
            # MAPE is error %, so convert to accuracy %
            solcast_accuracy = max(0, 100 - solcast_mape)
            combined = (localshift_accuracy + solcast_accuracy) / 2
            self._attr_native_value = round(combined, 1)
        elif localshift_accuracy:
            self._attr_native_value = round(localshift_accuracy, 1)
        elif solcast_mape is not None:
            # Convert MAPE to accuracy
            self._attr_native_value = round(max(0, 100 - solcast_mape), 1)
        else:
            self._attr_native_value = None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return detailed comparison attributes."""
        localshift_accuracy = self.coordinator.data.solar_forecast_accuracy
        solcast_mape = self.coordinator.data.solcast_mape

        attrs = {
            "localshift_accuracy_pct": round(localshift_accuracy, 1)
            if localshift_accuracy
            else None,
            "solcast_mape_pct": round(solcast_mape, 1)
            if solcast_mape is not None
            else None,
        }

        # Calculate divergence if both available
        if localshift_accuracy and solcast_mape is not None:
            # Both are percentages, but MAPE is error while localshift is accuracy
            # Convert MAPE to accuracy for comparison
            solcast_accuracy = max(0, 100 - solcast_mape)
            divergence = abs(localshift_accuracy - solcast_accuracy)
            attrs["divergence_pct"] = round(divergence, 1)

            # Determine which to trust more (lower divergence from 100% = more accurate)
            # LocalShift tracks site-specific patterns, so prefer it when divergence > 10%
            attrs["trust_localshift"] = divergence > 10.0
            attrs["divergence_status"] = (
                "significant"
                if divergence > 15
                else "moderate"
                if divergence > 10
                else "low"
            )
        else:
            attrs["divergence_pct"] = None
            attrs["trust_localshift"] = None
            attrs["divergence_status"] = "insufficient_data"

        # Add low confidence period count
        attrs["low_confidence_periods"] = len(
            self.coordinator.data.low_confidence_periods
        )

        return attrs
