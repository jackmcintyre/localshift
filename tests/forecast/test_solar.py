"""Tests for solar confidence blending (Issue #794)."""

from __future__ import annotations

from datetime import datetime

import pytest
from custom_components.localshift.forecast.solar import (
    _blend_solar_estimate,
    get_solar_for_5min_slot,
)


class TestBlendSolarEstimate:
    """Tests for _blend_solar_estimate()."""

    def test_full_confidence_returns_median(self):
        assert _blend_solar_estimate(29.56, 7.99, 1.0) == 29.56

    def test_zero_confidence_returns_p10(self):
        assert _blend_solar_estimate(29.56, 7.99, 0.0) == 7.99

    def test_mid_confidence_returns_average(self):
        result = _blend_solar_estimate(20.0, 10.0, 0.5)
        assert result == pytest.approx(15.0)

    def test_low_confidence_realistic(self):
        # Issue #794 reported case: confidence 17%, median 29.56, P10 7.99
        result = _blend_solar_estimate(29.56, 7.99, 0.17)
        expected = 0.17 * 29.56 + 0.83 * 7.99  # ~11.65
        assert result == pytest.approx(expected, abs=0.01)

    def test_confidence_above_one_clamps_to_median(self):
        assert _blend_solar_estimate(29.56, 7.99, 1.5) == 29.56

    def test_confidence_below_zero_clamps_to_p10(self):
        assert _blend_solar_estimate(29.56, 7.99, -0.5) == 7.99

    def test_zero_values(self):
        assert _blend_solar_estimate(0.0, 0.0, 0.5) == 0.0

    def test_p10_larger_than_median(self):
        # Edge case: inverted spread
        result = _blend_solar_estimate(5.0, 10.0, 0.5)
        assert result == pytest.approx(7.5)


class TestGetSolarFor5minSlotCoverage:
    """Additional tests to ensure coverage for get_solar_for_5min_slot()."""

    def test_naive_datetime_with_forecast(self):
        """Test that naive datetime is converted to local (line 241 coverage)."""
        forecasts = [
            {
                "period_start": "2026-03-19T10:00:00+00:00",
                "pv_estimate": 4.0,
                "pv_estimate10": 1.0,
            }
        ]
        slot_start = datetime(2026, 3, 19, 10, 0)  # Naive datetime
        result = get_solar_for_5min_slot(forecasts, slot_start)
        assert result > 0.0
