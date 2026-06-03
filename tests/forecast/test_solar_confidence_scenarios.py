"""Scenario tests for solar confidence blending (Issue #794)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from custom_components.localshift.forecast.analysis_resolver import ConfidenceResolver
from custom_components.localshift.forecast.solcast_analysis import (
    ConfidenceInterval,
    SolcastAnalysis,
)
from custom_components.localshift.forecast.solar import (
    _blend_solar_estimate,
    sum_solar_before_target,
)


def test_reported_issue_794_blends_to_conservative_value():
    blended = _blend_solar_estimate(29.56, 7.99, 0.17)

    assert blended == pytest.approx(11.66, abs=0.02)
    assert blended < 29.56 * 0.5


def test_low_confidence_sum_uses_cross_day_resolver():
    now = datetime(2026, 3, 20, 0, 0, tzinfo=timezone.utc)
    forecasts = [
        {
            "period_start": "2026-03-20T00:00:00+00:00",
            "pv_estimate": 6.0,
            "pv_estimate10": 1.0,
        },
    ]
    tomorrow_analysis = SolcastAnalysis(
        entity_id="sensor.tomorrow",
        last_updated=now,
        day_confidence=0.2,
        day_spread_kwh=0.0,
        estimate10_kwh=0.0,
        estimate90_kwh=0.0,
        intervals=[
            ConfidenceInterval(
                period_start=datetime(2026, 3, 20, 0, 0, tzinfo=timezone.utc),
                spread_kwh=0.0,
                confidence=0.2,
            )
        ],
    )

    optimistic = sum_solar_before_target(forecasts, now, 1)
    conservative = sum_solar_before_target(
        forecasts,
        now,
        1,
        resolver=ConfidenceResolver(None, tomorrow_analysis),
    )

    assert optimistic == pytest.approx(3.0)
    assert conservative == pytest.approx(1.0, abs=0.01)
    assert conservative < optimistic
