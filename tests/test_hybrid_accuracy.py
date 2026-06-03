"""Tests for hybrid solar accuracy calculation.

Issue #778 Phase 2: Tests for combining LocalShift tracker accuracy
with Solcast MAPE for hybrid forecasting.
"""

from unittest.mock import Mock

import pytest


class TestHybridAccuracy:
    """Tests for _update_hybrid_accuracy method."""

    def test_no_solcast_mape_uses_localshift(self):
        """Test hybrid accuracy uses LocalShift when Solcast MAPE unavailable."""
        from custom_components.localshift.coordinator.coordinator import (
            LocalShiftCoordinator,
        )

        coordinator = Mock(spec=LocalShiftCoordinator)
        coordinator.data = Mock()
        coordinator.data.solar_forecast_accuracy = 85.0
        coordinator.data.solcast_mape = None
        coordinator.data.hybrid_solar_accuracy = None

        LocalShiftCoordinator._update_hybrid_accuracy(coordinator)

        assert coordinator.data.hybrid_solar_accuracy == 85.0

    def test_low_samples_favors_solcast(self):
        """Test hybrid favors Solcast when LocalShift has few samples."""
        from custom_components.localshift.coordinator.coordinator import (
            LocalShiftCoordinator,
        )

        coordinator = Mock(spec=LocalShiftCoordinator)
        coordinator.data = Mock()
        coordinator.data.solar_forecast_accuracy = 85.0
        coordinator.data.solcast_mape = 10.0
        coordinator.data.hybrid_solar_accuracy = None

        tracker = Mock()
        tracker.metrics.sample_count = 5
        coordinator.solar_accuracy_tracker = tracker

        LocalShiftCoordinator._update_hybrid_accuracy(coordinator)

        assert coordinator.data.hybrid_solar_accuracy == 88.5

    def test_medium_samples_equal_weight(self):
        """Test hybrid uses equal weights with medium samples."""
        from custom_components.localshift.coordinator.coordinator import (
            LocalShiftCoordinator,
        )

        coordinator = Mock(spec=LocalShiftCoordinator)
        coordinator.data = Mock()
        coordinator.data.solar_forecast_accuracy = 80.0
        coordinator.data.solcast_mape = 20.0
        coordinator.data.hybrid_solar_accuracy = None

        tracker = Mock()
        tracker.metrics.sample_count = 20
        coordinator.solar_accuracy_tracker = tracker

        LocalShiftCoordinator._update_hybrid_accuracy(coordinator)

        assert coordinator.data.hybrid_solar_accuracy == 80.0

    def test_high_samples_trusts_localshift(self):
        """Test hybrid trusts LocalShift more with high samples and low divergence."""
        from custom_components.localshift.coordinator.coordinator import (
            LocalShiftCoordinator,
        )

        coordinator = Mock(spec=LocalShiftCoordinator)
        coordinator.data = Mock()
        coordinator.data.solar_forecast_accuracy = 85.0
        coordinator.data.solcast_mape = 10.0
        coordinator.data.hybrid_solar_accuracy = None

        tracker = Mock()
        tracker.metrics.sample_count = 50
        coordinator.solar_accuracy_tracker = tracker

        LocalShiftCoordinator._update_hybrid_accuracy(coordinator)

        assert coordinator.data.hybrid_solar_accuracy == 87.0

    def test_significant_divergence_trusts_solcast(self):
        """Test hybrid trusts Solcast more when divergence is significant."""
        from custom_components.localshift.coordinator.coordinator import (
            LocalShiftCoordinator,
        )

        coordinator = Mock(spec=LocalShiftCoordinator)
        coordinator.data = Mock()
        coordinator.data.solar_forecast_accuracy = 70.0
        coordinator.data.solcast_mape = 10.0
        coordinator.data.hybrid_solar_accuracy = None

        tracker = Mock()
        tracker.metrics.sample_count = 50
        coordinator.solar_accuracy_tracker = tracker

        LocalShiftCoordinator._update_hybrid_accuracy(coordinator)

        assert coordinator.data.hybrid_solar_accuracy == 82.0
