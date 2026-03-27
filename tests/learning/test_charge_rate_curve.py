"""Tests for learning/charge_rate.py - charge rate curve model."""

from __future__ import annotations

from custom_components.localshift.learning.charge_rate import ChargeRateCurve


class TestChargeRateCurve:
    """Tests for ChargeRateCurve interpolation and confidence."""

    def test_curve_rate_at_soc_interpolates_between_bins(self):
        """Interpolates linearly between bins."""
        curve = ChargeRateCurve.from_bins({0: 5.0, 50: 3.0, 100: 0.5})
        assert curve.rate_at_soc(25.0) == 4.0
        assert curve.rate_at_soc(75.0) == 1.75

    def test_curve_clamps_to_physical_cap(self):
        """Clamps rates to the physical maximum."""
        curve = ChargeRateCurve.from_bins({0: 20.0})
        assert curve.rate_at_soc(0.0) == 10.0

    def test_curve_extrapolates_at_edges(self):
        """Extrapolates to nearest edge bin."""
        curve = ChargeRateCurve.from_bins({20: 5.0, 80: 2.0})
        assert curve.rate_at_soc(10.0) == 5.0
        assert curve.rate_at_soc(90.0) == 2.0

    def test_confidence_clamped_to_zero(self):
        """Ensures confidence never drops below zero."""
        curve = ChargeRateCurve.from_bins({0: 5.0}, sample_count=10, normalized_mad=2.0)
        assert curve.confidence == 0.0
