"""Tests for pricing types."""

from datetime import datetime, timedelta, timezone

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


class TestForecastSlotGetMethod:
    """Tests for dict-compatible .get() method."""

    def test_get_existing_fields(self):
        """Test .get() returns existing field values."""
        slot = ForecastSlot(
            start_time=datetime(2026, 3, 16, 10, 0, tzinfo=timezone.utc),
            duration=30,
            per_kwh=0.25,
            is_spike=False,
            source_type="amber",
        )

        assert slot.get("start_time") == datetime(
            2026, 3, 16, 10, 0, tzinfo=timezone.utc
        )
        assert slot.get("duration") == 30
        assert slot.get("per_kwh") == 0.25
        assert slot.get("is_spike") is False
        assert slot.get("source_type") == "amber"

    def test_get_missing_field_returns_none(self):
        """Test .get() returns None for missing fields by default."""
        slot = ForecastSlot(
            start_time=datetime(2026, 3, 16, 10, 0, tzinfo=timezone.utc),
            duration=30,
            per_kwh=0.25,
            is_spike=False,
            source_type="amber",
        )

        assert slot.get("nonexistent") is None

    def test_get_missing_field_returns_custom_default(self):
        """Test .get() returns custom default for missing fields."""
        slot = ForecastSlot(
            start_time=datetime(2026, 3, 16, 10, 0, tzinfo=timezone.utc),
            duration=30,
            per_kwh=0.25,
            is_spike=False,
            source_type="amber",
        )

        assert slot.get("nonexistent", "default") == "default"
        assert slot.get("nonexistent", 42) == 42

    def test_get_end_time_computed(self):
        """Test .get() computes end_time on demand from start_time + duration."""
        slot = ForecastSlot(
            start_time=datetime(2026, 3, 16, 10, 0, tzinfo=timezone.utc),
            duration=30,
            per_kwh=0.25,
            is_spike=False,
            source_type="amber",
        )

        end_time = slot.get("end_time")
        assert end_time == datetime(2026, 3, 16, 10, 30, tzinfo=timezone.utc)

    def test_get_end_time_various_durations(self):
        """Test .get() end_time works with different durations."""
        for duration_min in [5, 15, 30, 60]:
            slot = ForecastSlot(
                start_time=datetime(2026, 3, 16, 10, 0, tzinfo=timezone.utc),
                duration=duration_min,
                per_kwh=0.10,
                is_spike=False,
                source_type="amber",
            )
            expected = datetime(2026, 3, 16, 10, 0, tzinfo=timezone.utc) + timedelta(
                minutes=duration_min
            )
            assert slot.get("end_time") == expected
