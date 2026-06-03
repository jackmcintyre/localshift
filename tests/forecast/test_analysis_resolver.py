"""Tests for cross-day confidence resolver (Issue #794)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from homeassistant.util import dt as dt_util
from custom_components.localshift.forecast.analysis_resolver import ConfidenceResolver
from custom_components.localshift.forecast.solcast_analysis import (
    SolcastAnalysis,
    ConfidenceInterval,
)


class TestConfidenceResolver:
    """Tests for ConfidenceResolver cross-day lookup."""

    def test_returns_today_confidence_for_today_slot(self):
        today_analysis = SolcastAnalysis(
            entity_id="today",
            last_updated=datetime.now(timezone.utc),
            day_confidence=0.8,
            day_spread_kwh=0,
            estimate10_kwh=0,
            estimate90_kwh=0,
            intervals=[
                ConfidenceInterval(
                    period_start=datetime(2026, 3, 19, 10, 0, tzinfo=timezone.utc),
                    spread_kwh=0,
                    confidence=0.9,
                ),
            ],
        )
        tomorrow_analysis = SolcastAnalysis(
            entity_id="tomorrow",
            last_updated=datetime.now(timezone.utc),
            day_confidence=0.3,
            day_spread_kwh=0,
            estimate10_kwh=0,
            estimate90_kwh=0,
            intervals=[
                ConfidenceInterval(
                    period_start=datetime(2026, 3, 20, 10, 0, tzinfo=timezone.utc),
                    spread_kwh=0,
                    confidence=0.4,
                ),
            ],
        )

        resolver = ConfidenceResolver(today_analysis, tomorrow_analysis)
        # Today slot should get today's interval confidence
        conf = resolver.get_confidence(
            datetime(2026, 3, 19, 10, 0, tzinfo=timezone.utc)
        )
        assert conf == pytest.approx(0.9)

    def test_returns_tomorrow_confidence_for_tomorrow_slot(self):
        today_analysis = SolcastAnalysis(
            entity_id="today",
            last_updated=datetime.now(timezone.utc),
            day_confidence=0.8,
            day_spread_kwh=0,
            estimate10_kwh=0,
            estimate90_kwh=0,
            intervals=[],
        )
        tomorrow_analysis = SolcastAnalysis(
            entity_id="tomorrow",
            last_updated=datetime.now(timezone.utc),
            day_confidence=0.3,
            day_spread_kwh=0,
            estimate10_kwh=0,
            estimate90_kwh=0,
            intervals=[
                ConfidenceInterval(
                    period_start=datetime(2026, 3, 20, 10, 0, tzinfo=timezone.utc),
                    spread_kwh=0,
                    confidence=0.4,
                ),
            ],
        )

        resolver = ConfidenceResolver(today_analysis, tomorrow_analysis)
        conf = resolver.get_confidence(
            datetime(2026, 3, 20, 10, 0, tzinfo=timezone.utc)
        )
        assert conf == pytest.approx(0.4)

    def test_fallback_to_day_confidence_when_no_match(self):
        analysis = SolcastAnalysis(
            entity_id="today",
            last_updated=datetime.now(timezone.utc),
            day_confidence=0.6,
            day_spread_kwh=0,
            estimate10_kwh=0,
            estimate90_kwh=0,
            intervals=[],
        )
        resolver = ConfidenceResolver(analysis, None)
        conf = resolver.get_confidence(
            datetime(2026, 3, 19, 15, 0, tzinfo=timezone.utc)
        )
        assert conf == pytest.approx(0.6)  # Falls back to day_confidence

    def test_no_analysis_returns_1(self):
        resolver = ConfidenceResolver(None, None)
        conf = resolver.get_confidence(
            datetime(2026, 3, 19, 10, 0, tzinfo=timezone.utc)
        )
        assert conf == 1.0

    def test_tomorrow_only_when_today_is_none(self):
        """Test fallback to tomorrow when today is None."""
        tomorrow_analysis = SolcastAnalysis(
            entity_id="tomorrow",
            last_updated=datetime.now(timezone.utc),
            day_confidence=0.5,
            day_spread_kwh=0,
            estimate10_kwh=0,
            estimate90_kwh=0,
            intervals=[],
        )
        resolver = ConfidenceResolver(None, tomorrow_analysis)
        conf = resolver.get_confidence(
            datetime(2026, 3, 20, 10, 0, tzinfo=timezone.utc)
        )
        assert conf == pytest.approx(0.5)

    def test_get_analysis_for_period_today(self):
        """Test get_analysis_for_period returns today's analysis."""
        today_analysis = SolcastAnalysis(
            entity_id="today",
            last_updated=datetime.now(timezone.utc),
            day_confidence=0.8,
            day_spread_kwh=0,
            estimate10_kwh=0,
            estimate90_kwh=0,
            intervals=[
                ConfidenceInterval(
                    period_start=datetime(2026, 3, 19, 10, 0, tzinfo=timezone.utc),
                    spread_kwh=0,
                    confidence=0.9,
                ),
            ],
        )
        resolver = ConfidenceResolver(today_analysis, None)
        analysis = resolver.get_analysis_for_period(
            datetime(2026, 3, 19, 10, 0, tzinfo=timezone.utc)
        )
        assert analysis == today_analysis

    def test_get_analysis_for_period_tomorrow(self):
        """Test get_analysis_for_period returns tomorrow's analysis."""
        tomorrow_analysis = SolcastAnalysis(
            entity_id="tomorrow",
            last_updated=datetime.now(timezone.utc),
            day_confidence=0.3,
            day_spread_kwh=0,
            estimate10_kwh=0,
            estimate90_kwh=0,
            intervals=[
                ConfidenceInterval(
                    period_start=datetime(2026, 3, 20, 10, 0, tzinfo=timezone.utc),
                    spread_kwh=0,
                    confidence=0.4,
                ),
            ],
        )
        resolver = ConfidenceResolver(None, tomorrow_analysis)
        analysis = resolver.get_analysis_for_period(
            datetime(2026, 3, 20, 10, 0, tzinfo=timezone.utc)
        )
        assert analysis == tomorrow_analysis

    def test_get_analysis_for_period_no_match(self):
        """Test get_analysis_for_period returns today when no match."""
        today_analysis = SolcastAnalysis(
            entity_id="today",
            last_updated=datetime.now(timezone.utc),
            day_confidence=0.6,
            day_spread_kwh=0,
            estimate10_kwh=0,
            estimate90_kwh=0,
            intervals=[],
        )
        resolver = ConfidenceResolver(today_analysis, None)
        analysis = resolver.get_analysis_for_period(
            datetime(2026, 3, 19, 15, 0, tzinfo=timezone.utc)
        )
        assert analysis == today_analysis


# ─── absent_confidence threading tests ─────────────────────────────────────


class TestAbsentConfidence:
    """Tests for absent_confidence parameter threading."""

    def test_resolver_absent_confidence_when_no_analysis(self):
        """absent_confidence is returned when both analyses are None."""
        resolver = ConfidenceResolver(None, None, absent_confidence=0.3)
        result = resolver.get_confidence(dt_util.utcnow())
        assert result == pytest.approx(0.3)

    def test_resolver_absent_confidence_default_backward_compat(self):
        """Default absent_confidence=1.0 preserves legacy behaviour."""
        resolver = ConfidenceResolver(None, None)
        result = resolver.get_confidence(dt_util.utcnow())
        assert result == pytest.approx(1.0)

    def test_resolver_stale_analysis_capped_via_ceiling(self):
        """confidence_ceiling on SolcastAnalysis flows through to resolver.get_confidence."""
        now = dt_util.utcnow()
        analysis = SolcastAnalysis(
            entity_id="test",
            last_updated=now,
            day_confidence=0.9,
            day_spread_kwh=0.5,
            estimate10_kwh=3.0,
            estimate90_kwh=9.0,
            is_stale=True,
            confidence_ceiling=0.3,
        )
        resolver = ConfidenceResolver(analysis, None, absent_confidence=0.3)
        result = resolver.get_confidence(now + timedelta(days=365))  # no interval match
        assert result == pytest.approx(0.3)  # capped by confidence_ceiling
