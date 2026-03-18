"""Tests for Solcast v4.5.1 analysis attribute extraction.

Issue #778: Tests extraction of confidence scores and estimate10/90 spreads
from Solcast forecast entity attributes.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import Mock

import pytest
from homeassistant.core import HomeAssistant, State
from homeassistant.util import dt as dt_util

from custom_components.localshift.forecast.solcast_analysis import (
    ConfidenceInterval,
    SolcastAnalysis,
    compute_weighted_confidence,
    extract_analysis_from_entity,
    get_confidence_for_period,
)


@pytest.fixture
def mock_hass() -> HomeAssistant:
    """Create mock Home Assistant instance."""
    hass = Mock(spec=HomeAssistant)
    hass.states = Mock()
    return hass


@pytest.fixture
def sample_analysis_attribute() -> dict:
    """Sample analysis attribute from Solcast v4.5.1."""
    now = dt_util.utcnow()
    return {
        "estimate10_kwh": 5.5,
        "estimate90_kwh": 8.2,
        "spread_kwh": 2.7,
        "confidence": 0.67,  # 1.0 - (2.7 / 8.2) ≈ 0.67
        "intervals": [
            {
                "period_start": (now + timedelta(hours=i * 0.5)).isoformat(),
                "spread_kwh": 0.5 + i * 0.1,
                "confidence": 0.8 - i * 0.05,
            }
            for i in range(6)  # 3 hours worth
        ],
    }


def test_confidence_interval_creation():
    """Test ConfidenceInterval dataclass creation."""
    now = dt_util.utcnow()
    interval = ConfidenceInterval(
        period_start=now,
        spread_kwh=0.5,
        confidence=0.85,
    )

    assert interval.period_start == now
    assert interval.spread_kwh == 0.5
    assert interval.confidence == 0.85

    # Test serialization
    data = interval.to_dict()
    assert data["period_start"] == now.isoformat()
    assert data["spread_kwh"] == 0.5
    assert data["confidence"] == 0.85


def test_solcast_analysis_creation():
    """Test SolcastAnalysis dataclass creation."""
    now = dt_util.utcnow()
    interval = ConfidenceInterval(
        period_start=now,
        spread_kwh=0.5,
        confidence=0.85,
    )

    analysis = SolcastAnalysis(
        entity_id="sensor.solcast_pv_forecast_forecast_today",
        last_updated=now,
        day_confidence=0.75,
        day_spread_kwh=2.5,
        estimate10_kwh=5.0,
        estimate90_kwh=7.5,
        intervals=[interval],
    )

    assert analysis.entity_id == "sensor.solcast_pv_forecast_forecast_today"
    assert analysis.day_confidence == 0.75
    assert len(analysis.intervals) == 1

    # Test serialization
    data = analysis.to_dict()
    assert data["entity_id"] == "sensor.solcast_pv_forecast_forecast_today"
    assert data["day_confidence"] == 0.75
    assert len(data["intervals"]) == 1


def test_extract_analysis_entity_not_found(mock_hass):
    """Test extraction when entity doesn't exist."""
    mock_hass.states.get.return_value = None

    result = extract_analysis_from_entity(mock_hass, "sensor.nonexistent")

    assert result is None


def test_extract_analysis_no_attribute(mock_hass):
    """Test extraction when analysis attribute is missing."""
    state = Mock(spec=State)
    state.attributes = {}
    mock_hass.states.get.return_value = state

    result = extract_analysis_from_entity(mock_hass, "sensor.test")

    assert result is None


def test_extract_analysis_invalid_attribute_type(mock_hass):
    """Test extraction when analysis attribute is not a dict."""
    state = Mock(spec=State)
    state.attributes = {"analysis": "not a dict"}
    mock_hass.states.get.return_value = state

    result = extract_analysis_from_entity(mock_hass, "sensor.test")

    assert result is None


def test_extract_analysis_malformed_data_exception(mock_hass):
    """Test extraction handles exceptions from malformed data."""
    state = Mock(spec=State)
    state.attributes = {
        "analysis": {
            "estimate10_kwh": "not a number",  # This will raise ValueError
            "estimate90_kwh": 7.0,
            "spread_kwh": 2.0,
            "confidence": 0.71,
        }
    }
    state.last_updated = dt_util.utcnow()
    mock_hass.states.get.return_value = state

    # Should handle exception gracefully and return None
    result = extract_analysis_from_entity(mock_hass, "sensor.test")

    assert result is None


def test_extract_analysis_success(mock_hass, sample_analysis_attribute):
    """Test successful extraction of analysis attribute."""
    now = dt_util.utcnow()
    state = Mock(spec=State)
    state.attributes = {"analysis": sample_analysis_attribute}
    state.last_updated = now
    mock_hass.states.get.return_value = state

    result = extract_analysis_from_entity(mock_hass, "sensor.test_forecast")

    assert result is not None
    assert result.entity_id == "sensor.test_forecast"
    assert result.day_confidence == 0.67
    assert result.day_spread_kwh == 2.7
    assert result.estimate10_kwh == 5.5
    assert result.estimate90_kwh == 8.2
    assert len(result.intervals) == 6

    # Verify first interval
    first_interval = result.intervals[0]
    assert first_interval.spread_kwh == 0.5
    assert first_interval.confidence == 0.8


def test_extract_analysis_handles_missing_intervals(mock_hass):
    """Test extraction gracefully handles missing intervals."""
    state = Mock(spec=State)
    state.attributes = {
        "analysis": {
            "estimate10_kwh": 5.0,
            "estimate90_kwh": 7.0,
            "spread_kwh": 2.0,
            "confidence": 0.71,
            # No intervals key
        }
    }
    state.last_updated = dt_util.utcnow()
    mock_hass.states.get.return_value = state

    result = extract_analysis_from_entity(mock_hass, "sensor.test")

    assert result is not None
    assert result.day_confidence == 0.71
    assert len(result.intervals) == 0


def test_extract_analysis_handles_invalid_interval_data(mock_hass):
    """Test extraction skips invalid interval entries."""
    now = dt_util.utcnow()
    state = Mock(spec=State)
    state.attributes = {
        "analysis": {
            "estimate10_kwh": 5.0,
            "estimate90_kwh": 7.0,
            "spread_kwh": 2.0,
            "confidence": 0.71,
            "intervals": [
                {  # Valid
                    "period_start": now.isoformat(),
                    "spread_kwh": 0.5,
                    "confidence": 0.8,
                },
                "not a dict",  # Invalid - not a dict
                {  # Invalid - missing period_start
                    "spread_kwh": 0.6,
                    "confidence": 0.75,
                },
                {  # Valid
                    "period_start": (now + timedelta(hours=1)).isoformat(),
                    "spread_kwh": 0.7,
                    "confidence": 0.7,
                },
            ],
        }
    }
    state.last_updated = now
    mock_hass.states.get.return_value = state

    result = extract_analysis_from_entity(mock_hass, "sensor.test")

    assert result is not None
    assert len(result.intervals) == 2  # Only valid intervals


def test_get_confidence_for_period_no_analysis():
    """Test confidence retrieval with no analysis data."""
    now = dt_util.utcnow()
    confidence = get_confidence_for_period(None, now)

    assert confidence == 1.0  # Default fallback


def test_get_confidence_for_period_no_intervals():
    """Test confidence retrieval with no intervals."""
    now = dt_util.utcnow()
    analysis = SolcastAnalysis(
        entity_id="sensor.test",
        last_updated=now,
        day_confidence=0.75,
        day_spread_kwh=2.0,
        estimate10_kwh=5.0,
        estimate90_kwh=7.0,
        intervals=[],
    )

    confidence = get_confidence_for_period(analysis, now)

    assert confidence == 1.0  # Default when no intervals


def test_get_confidence_for_period_exact_match():
    """Test confidence retrieval with exact time match."""
    now = dt_util.utcnow()
    interval = ConfidenceInterval(
        period_start=now,
        spread_kwh=0.5,
        confidence=0.85,
    )
    analysis = SolcastAnalysis(
        entity_id="sensor.test",
        last_updated=now,
        day_confidence=0.75,
        day_spread_kwh=2.0,
        estimate10_kwh=5.0,
        estimate90_kwh=7.0,
        intervals=[interval],
    )

    confidence = get_confidence_for_period(analysis, now)

    assert confidence == 0.85


def test_get_confidence_for_period_with_tolerance():
    """Test confidence retrieval with time tolerance (5 minutes)."""
    now = dt_util.utcnow()
    interval = ConfidenceInterval(
        period_start=now,
        spread_kwh=0.5,
        confidence=0.85,
    )
    analysis = SolcastAnalysis(
        entity_id="sensor.test",
        last_updated=now,
        day_confidence=0.75,
        day_spread_kwh=2.0,
        estimate10_kwh=5.0,
        estimate90_kwh=7.0,
        intervals=[interval],
    )

    # Query 3 minutes later (within 5-minute tolerance)
    query_time = now + timedelta(minutes=3)
    confidence = get_confidence_for_period(analysis, query_time)

    assert confidence == 0.85


def test_get_confidence_for_period_fallback_to_day():
    """Test confidence falls back to day-level when no match."""
    now = dt_util.utcnow()
    interval = ConfidenceInterval(
        period_start=now,
        spread_kwh=0.5,
        confidence=0.85,
    )
    analysis = SolcastAnalysis(
        entity_id="sensor.test",
        last_updated=now,
        day_confidence=0.75,
        day_spread_kwh=2.0,
        estimate10_kwh=5.0,
        estimate90_kwh=7.0,
        intervals=[interval],
    )

    # Query 2 hours later (outside tolerance)
    query_time = now + timedelta(hours=2)
    confidence = get_confidence_for_period(analysis, query_time)

    assert confidence == 0.75  # Fallback to day confidence


def test_compute_weighted_confidence_no_analysis():
    """Test weighted confidence with no analysis data."""
    now = dt_util.utcnow()
    confidence = compute_weighted_confidence(None, now, 2.0)

    assert confidence == 1.0


def test_compute_weighted_confidence_no_intervals():
    """Test weighted confidence with no intervals."""
    now = dt_util.utcnow()
    analysis = SolcastAnalysis(
        entity_id="sensor.test",
        last_updated=now,
        day_confidence=0.75,
        day_spread_kwh=2.0,
        estimate10_kwh=5.0,
        estimate90_kwh=7.0,
        intervals=[],
    )

    confidence = compute_weighted_confidence(analysis, now, 2.0)

    assert confidence == 1.0


def test_compute_weighted_confidence_single_interval():
    """Test weighted confidence with single overlapping interval."""
    now = dt_util.utcnow()
    interval = ConfidenceInterval(
        period_start=now,
        spread_kwh=0.5,
        confidence=0.85,
    )
    analysis = SolcastAnalysis(
        entity_id="sensor.test",
        last_updated=now,
        day_confidence=0.75,
        day_spread_kwh=2.0,
        estimate10_kwh=5.0,
        estimate90_kwh=7.0,
        intervals=[interval],
    )

    # Query 30-minute window (exactly one interval)
    confidence = compute_weighted_confidence(analysis, now, 0.5)

    assert confidence == 0.85


def test_compute_weighted_confidence_multiple_intervals():
    """Test weighted confidence across multiple intervals."""
    now = dt_util.utcnow()
    intervals = [
        ConfidenceInterval(
            period_start=now + timedelta(hours=i * 0.5),
            spread_kwh=0.5,
            confidence=0.9 - i * 0.1,  # Decreasing confidence
        )
        for i in range(4)  # 2 hours worth
    ]
    analysis = SolcastAnalysis(
        entity_id="sensor.test",
        last_updated=now,
        day_confidence=0.75,
        day_spread_kwh=2.0,
        estimate10_kwh=5.0,
        estimate90_kwh=7.0,
        intervals=intervals,
    )

    # Query 2-hour window covering all intervals
    confidence = compute_weighted_confidence(analysis, now, 2.0)

    # Expected: (0.9*0.5 + 0.8*0.5 + 0.7*0.5 + 0.6*0.5) / 2.0 = 0.75
    assert abs(confidence - 0.75) < 0.01


def test_compute_weighted_confidence_partial_overlap():
    """Test weighted confidence with partial interval overlap."""
    now = dt_util.utcnow()
    interval = ConfidenceInterval(
        period_start=now,
        spread_kwh=0.5,
        confidence=0.8,
    )
    analysis = SolcastAnalysis(
        entity_id="sensor.test",
        last_updated=now,
        day_confidence=0.75,
        day_spread_kwh=2.0,
        estimate10_kwh=5.0,
        estimate90_kwh=7.0,
        intervals=[interval],
    )

    # Query 1-hour window, but interval is only 30 minutes
    # Should weight by 0.5 hours and fallback to day confidence for remainder
    confidence = compute_weighted_confidence(analysis, now, 1.0)

    # Only the first 0.5 hours has data, so result equals that interval's confidence
    # (0.8 * 0.5) / 0.5 = 0.8
    assert abs(confidence - 0.8) < 0.01


def test_compute_weighted_confidence_no_overlap():
    """Test weighted confidence with no overlap falls back to day confidence."""
    now = dt_util.utcnow()
    interval = ConfidenceInterval(
        period_start=now + timedelta(hours=5),
        spread_kwh=0.5,
        confidence=0.9,
    )
    analysis = SolcastAnalysis(
        entity_id="sensor.test",
        last_updated=now,
        day_confidence=0.75,
        day_spread_kwh=2.0,
        estimate10_kwh=5.0,
        estimate90_kwh=7.0,
        intervals=[interval],
    )

    # Query window doesn't overlap with interval
    confidence = compute_weighted_confidence(analysis, now, 1.0)

    assert confidence == 0.75  # Falls back to day confidence
