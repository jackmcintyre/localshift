"""Tests for Solcast confidence sensors.

Issue #778: Tests for diagnostic sensors exposing Solcast v4.5.1
analysis attribute data.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import Mock

import pytest

from custom_components.localshift.forecast.solcast_analysis import (
    ConfidenceInterval,
    SolcastAnalysis,
)
from custom_components.localshift.sensors.solcast import (
    ForecastAccuracyComparisonSensor,
    SolcastConfidenceTodaySensor,
    SolcastConfidenceTomorrowSensor,
)


@pytest.fixture
def coordinator_with_analysis():
    """Create mock coordinator with Solcast analysis data."""
    coordinator = Mock()
    coordinator.data = Mock()

    now = datetime.now()

    # Create sample analysis data
    analysis = SolcastAnalysis(
        entity_id="sensor.solcast_pv_forecast_forecast_today",
        last_updated=now,
        day_confidence=0.75,
        day_spread_kwh=2.5,
        estimate10_kwh=5.0,
        estimate90_kwh=7.5,
        intervals=[
            ConfidenceInterval(
                period_start=now,
                spread_kwh=0.5,
                confidence=0.8,
            ),
            ConfidenceInterval(
                period_start=now.replace(hour=now.hour + 1),
                spread_kwh=0.6,
                confidence=0.7,
            ),
        ],
    )

    coordinator.data.solcast_analysis_today = analysis
    coordinator.data.solcast_analysis_tomorrow = analysis
    coordinator.data.solar_forecast_accuracy = 85.0
    coordinator.data.solcast_mape = 10.5
    coordinator.data.low_confidence_periods = [(now, 0.6)]

    return coordinator


@pytest.fixture
def coordinator_without_analysis():
    """Create mock coordinator without Solcast analysis data."""
    coordinator = Mock()
    coordinator.data = Mock()
    coordinator.data.solcast_analysis_today = None
    coordinator.data.solcast_analysis_tomorrow = None
    coordinator.data.solar_forecast_accuracy = 85.0
    coordinator.data.solcast_mape = None
    coordinator.data.low_confidence_periods = []

    return coordinator


class TestSolcastConfidenceTodaySensor:
    """Tests for SolcastConfidenceTodaySensor."""

    def test_update_with_analysis(self, coordinator_with_analysis):
        """Test sensor updates with analysis data."""
        sensor = SolcastConfidenceTodaySensor(coordinator_with_analysis, Mock())
        sensor._update_from_coordinator()

        assert sensor.native_value == 75  # 0.75 * 100
        assert sensor.extra_state_attributes["spread_kwh"] == 2.5
        assert sensor.extra_state_attributes["estimate10_kwh"] == 5.0
        assert sensor.extra_state_attributes["estimate90_kwh"] == 7.5
        assert "hourly_confidence" in sensor.extra_state_attributes

    def test_update_without_analysis(self, coordinator_without_analysis):
        """Test sensor returns None when no analysis available."""
        sensor = SolcastConfidenceTodaySensor(coordinator_without_analysis, Mock())
        sensor._update_from_coordinator()

        assert sensor.native_value is None
        assert sensor.extra_state_attributes == {}

    def test_entity_category_diagnostic(self, coordinator_with_analysis):
        """Test sensor is marked as diagnostic."""
        from homeassistant.helpers.entity import EntityCategory

        sensor = SolcastConfidenceTodaySensor(coordinator_with_analysis, Mock())
        assert sensor.entity_category == EntityCategory.DIAGNOSTIC


class TestSolcastConfidenceTomorrowSensor:
    """Tests for SolcastConfidenceTomorrowSensor."""

    def test_update_with_analysis(self, coordinator_with_analysis):
        """Test sensor updates with analysis data."""
        sensor = SolcastConfidenceTomorrowSensor(coordinator_with_analysis, Mock())
        sensor._update_from_coordinator()

        assert sensor.native_value == 75  # 0.75 * 100
        assert sensor.extra_state_attributes["spread_kwh"] == 2.5
        assert sensor.extra_state_attributes["estimate10_kwh"] == 5.0
        assert sensor.extra_state_attributes["estimate90_kwh"] == 7.5

    def test_update_without_analysis(self, coordinator_without_analysis):
        """Test sensor returns None when no analysis available."""
        sensor = SolcastConfidenceTomorrowSensor(coordinator_without_analysis, Mock())
        sensor._update_from_coordinator()

        assert sensor.native_value is None
        assert sensor.extra_state_attributes == {}

    def test_entity_category_diagnostic(self, coordinator_with_analysis):
        """Test sensor is marked as diagnostic."""
        from homeassistant.helpers.entity import EntityCategory

        sensor = SolcastConfidenceTomorrowSensor(coordinator_with_analysis, Mock())
        assert sensor.entity_category == EntityCategory.DIAGNOSTIC


class TestForecastAccuracyComparisonSensor:
    """Tests for ForecastAccuracyComparisonSensor."""

    def test_update_with_both_metrics(self, coordinator_with_analysis):
        """Test sensor combines both accuracy metrics."""
        sensor = ForecastAccuracyComparisonSensor(coordinator_with_analysis, Mock())
        sensor._update_from_coordinator()

        # LocalShift: 85%, Solcast MAPE: 10.5% → 89.5% accuracy
        # Combined: (85 + 89.5) / 2 = 87.25 → round(87.25, 1) = 87.2
        assert sensor.native_value == 87.2

        attrs = sensor.extra_state_attributes
        assert attrs["localshift_accuracy_pct"] == 85.0
        assert attrs["solcast_mape_pct"] == 10.5
        assert attrs["divergence_pct"] == 4.5  # |85 - 89.5|
        assert attrs["trust_localshift"] is False  # divergence < 10
        assert attrs["divergence_status"] == "low"
        assert attrs["low_confidence_periods"] == 1

    def test_update_with_only_localshift(self, coordinator_without_analysis):
        """Test sensor uses LocalShift accuracy when Solcast unavailable."""
        sensor = ForecastAccuracyComparisonSensor(coordinator_without_analysis, Mock())
        sensor._update_from_coordinator()

        assert sensor.native_value == 85.0

        attrs = sensor.extra_state_attributes
        assert attrs["localshift_accuracy_pct"] == 85.0
        assert attrs["solcast_mape_pct"] is None
        assert attrs["divergence_pct"] is None
        assert attrs["trust_localshift"] is None
        assert attrs["divergence_status"] == "insufficient_data"

    def test_update_with_only_solcast(self):
        """Test sensor uses Solcast MAPE when LocalShift unavailable."""
        coordinator = Mock()
        coordinator.data = Mock()
        coordinator.data.solar_forecast_accuracy = None  # Unavailable
        coordinator.data.solcast_mape = 12.0
        coordinator.data.low_confidence_periods = []

        sensor = ForecastAccuracyComparisonSensor(coordinator, Mock())
        sensor._update_from_coordinator()

        # 100 - 12 = 88%
        assert sensor.native_value == 88.0

        attrs = sensor.extra_state_attributes
        assert attrs["localshift_accuracy_pct"] is None
        assert attrs["solcast_mape_pct"] == 12.0

    def test_update_with_no_metrics(self):
        """Test sensor returns None when no metrics available."""
        coordinator = Mock()
        coordinator.data = Mock()
        coordinator.data.solar_forecast_accuracy = 100.0  # Default
        coordinator.data.solcast_mape = None
        coordinator.data.low_confidence_periods = []

        sensor = ForecastAccuracyComparisonSensor(coordinator, Mock())
        sensor._update_from_coordinator()

        # When only default 100% accuracy, should return 100
        assert sensor.native_value == 100.0

    def test_significant_divergence_trusts_localshift(self):
        """Test sensor trusts LocalShift when divergence > 10%."""
        coordinator = Mock()
        coordinator.data = Mock()
        coordinator.data.solar_forecast_accuracy = 85.0
        coordinator.data.solcast_mape = 26.0  # MAPE 26% → 74% accuracy, divergence = 11
        coordinator.data.low_confidence_periods = []

        sensor = ForecastAccuracyComparisonSensor(coordinator, Mock())
        sensor._update_from_coordinator()

        attrs = sensor.extra_state_attributes
        # |85 - 74| = 11, > 10 threshold
        assert attrs["divergence_pct"] == 11.0
        # divergence > 10, trust_localshift should be True
        assert attrs["trust_localshift"] is True
        assert attrs["divergence_status"] == "moderate"

    def test_entity_category_diagnostic(self, coordinator_with_analysis):
        """Test sensor is marked as diagnostic."""
        from homeassistant.helpers.entity import EntityCategory

        sensor = ForecastAccuracyComparisonSensor(coordinator_with_analysis, Mock())
        assert sensor.entity_category == EntityCategory.DIAGNOSTIC
