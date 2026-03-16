"""Tests for pricing types."""

from datetime import datetime, timezone

from custom_components.localshift.pricing.types import ForecastSlot


def test_forecast_slot_creation():
    """Test ForecastSlot can be created with required fields."""
    slot = ForecastSlot(
        start_time=datetime(2026, 3, 16, 12, 0, tzinfo=timezone.utc),
        duration=30,
        per_kwh=0.15,
        is_spike=False,
        source_type="amber",
    )
    assert slot.duration == 30
    assert slot.per_kwh == 0.15
    assert slot.is_spike is False


def test_forecast_slot_is_frozen():
    """Test ForecastSlot is immutable (frozen dataclass)."""
    slot = ForecastSlot(
        start_time=datetime(2026, 3, 16, 12, 0, tzinfo=timezone.utc),
        duration=30,
        per_kwh=0.15,
        is_spike=False,
        source_type="amber",
    )
    try:
        slot.per_kwh = 0.20
        assert False, "Should not be able to modify frozen dataclass"
    except AttributeError:
        pass
