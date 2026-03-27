"""Tests for learning/charge_rate.py - charge rate curve confidence."""

from __future__ import annotations

from custom_components.localshift.learning.charge_rate import ChargeRateCurve


class TestChargeRateCurveConfidence:
    """Tests for ChargeRateCurve confidence clamping."""

    def test_rate_at_soc_returns_zero_without_bins(self):
        """Returns zero when no bins are defined."""
        curve = ChargeRateCurve.from_bins({})
        assert curve.rate_at_soc(50.0) == 0.0

    def test_confidence_min_samples_zero_defaults_to_one(self):
        """Defaults confidence base to one when min_samples is zero."""
        curve = ChargeRateCurve.from_bins(
            {0: 5.0}, sample_count=0, normalized_mad=0.0, min_samples=0
        )
        assert curve.confidence == 1.0

    def test_confidence_clamped_to_one(self):
        """Ensures confidence never rises above one."""
        curve = ChargeRateCurve.from_bins(
            {0: 5.0}, sample_count=100, normalized_mad=-0.5
        )
        assert curve.confidence == 1.0
