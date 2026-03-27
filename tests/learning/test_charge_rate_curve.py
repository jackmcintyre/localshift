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

    def test_curve_rate_at_soc_returns_zero_without_bins(self):
        """Returns zero when no bins are defined."""
        curve = ChargeRateCurve.from_bins({})
        assert curve.rate_at_soc(50.0) == 0.0

    def test_curve_rate_at_soc_uses_cached_bins(self):
        """Avoids re-sorting bins on repeated calls."""

        class TrackingBins(dict[int, float]):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.items_calls = 0

            def items(self):  # type: ignore[override]
                self.items_calls += 1
                return super().items()

        bins = TrackingBins({0: 5.0, 100: 1.0})
        curve = ChargeRateCurve.from_bins(bins)

        curve.rate_at_soc(10.0)
        curve.rate_at_soc(20.0)

        assert bins.items_calls == 1

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

    def test_confidence_scales_with_samples_below_minimum(self):
        """Scales confidence with sample count below min_samples."""
        curve = ChargeRateCurve.from_bins(
            {0: 5.0}, sample_count=10, normalized_mad=0.0, min_samples=50
        )
        assert curve.confidence == 0.2

    def test_confidence_scales_with_samples_below_minimum_at_low_count(self):
        """Scales confidence for very low sample counts."""
        curve = ChargeRateCurve.from_bins(
            {0: 5.0}, sample_count=1, normalized_mad=0.0, min_samples=10
        )
        assert curve.confidence == 0.1

    def test_confidence_clamped_to_zero(self):
        """Ensures confidence never drops below zero."""
        curve = ChargeRateCurve.from_bins({0: 5.0}, sample_count=10, normalized_mad=2.0)
        assert curve.confidence == 0.0
