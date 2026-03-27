"""Tests for learning/charge_rate.py."""

from __future__ import annotations

from custom_components.localshift.learning.charge_rate import ChargeRateCurve


def test_charge_rate_curve_confidence_scales_with_samples() -> None:
    """Confidence scales by sample count when below minimum."""
    curve = ChargeRateCurve.from_bins(
        {0: 5.0}, sample_count=1, normalized_mad=0.0, min_samples=10
    )
    assert curve.confidence == 0.1
