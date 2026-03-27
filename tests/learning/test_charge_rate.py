"""Tests for learning/charge_rate.py - charge rate curve basics."""

from __future__ import annotations

from custom_components.localshift.learning.charge_rate import ChargeRateCurve


def test_curve_rate_at_soc_empty_bins_returns_zero():
    """Returns 0.0 when no bins are provided."""
    curve = ChargeRateCurve.from_bins({})
    assert curve.rate_at_soc(50.0) == 0.0


def test_confidence_defaults_to_full_when_min_samples_zero():
    """Defaults to full confidence when min_samples is zero."""
    curve = ChargeRateCurve.from_bins({0: 5.0}, min_samples=0)
    assert curve.confidence == 1.0
