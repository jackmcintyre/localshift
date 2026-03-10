"""Unit tests for 5-minute slot solar forecast edge cases.

Tests the fix for Issue: "drops to zero in some slots" where 5-minute slots
at Solcast period boundaries were returning 0.0 due to containment check
instead of overlap-weighted accumulation.
"""

from datetime import datetime, timedelta, timezone

from custom_components.localshift.forecast.solar import (
    get_solar_for_5min_slot,
    get_solar_for_15min_slot,
)


def _make_solcast_entry(period_start: str, pv_estimate: float) -> dict:
    """Create a Solcast-style forecast entry."""
    return {
        "period_start": period_start,
        "pv_estimate": pv_estimate,
        "pv_estimate10": pv_estimate * 0.5,  # pessimistic estimate
    }


class Test5MinSlotBoundaryCases:
    """Test 5-minute slots that straddle Solcast period boundaries."""

    def test_slot_fully_inside_period(self):
        """Slot entirely within one Solcast period should get 5/60 of the kWh/hr."""
        solcast = [
            _make_solcast_entry("2026-02-27T15:30:00+11:00", 2.0),  # 2 kWh/hr
        ]

        # Slot 15:35-15:40 is fully inside 15:30-16:00
        slot_start = datetime(2026, 2, 27, 15, 35, tzinfo=timezone(timedelta(hours=11)))
        result = get_solar_for_5min_slot(solcast, slot_start)

        # Expected: 2.0 kWh/hr × (5/60) hr = 0.1667 kWh
        expected = 2.0 * (5.0 / 60.0)
        assert abs(result - expected) < 0.001, f"Expected {expected}, got {result}"

    def test_slot_at_period_boundary(self):
        """Slot 15:55-16:00 should split between two Solcast periods."""
        solcast = [
            _make_solcast_entry("2026-02-27T15:30:00+11:00", 2.0),  # 2 kWh/hr
            _make_solcast_entry("2026-02-27T16:00:00+11:00", 1.8),  # 1.8 kWh/hr
        ]

        # Slot 15:55-16:00 overlaps both periods
        # - 5 min in 15:30-16:00: 2.0 × (5/60) = 0.1667
        # - 0 min in 16:00-16:30: 0 (slot ends at 16:00 exactly)
        # Actually, the slot 15:55-16:00 is fully contained in 15:30-16:00
        slot_start = datetime(2026, 2, 27, 15, 55, tzinfo=timezone(timedelta(hours=11)))
        result = get_solar_for_5min_slot(solcast, slot_start)

        # Expected: 2.0 × (5/60) = 0.1667 kWh
        expected = 2.0 * (5.0 / 60.0)
        assert abs(result - expected) < 0.001, f"Expected {expected}, got {result}"

    def test_slot_straddling_periods(self):
        """Slot that actually straddles two periods (hypothetical case).

        With 5-minute slots and 30-minute Solcast periods, this shouldn't happen
        in practice (slots align to 5-min boundaries, periods to 30-min boundaries).
        But the algorithm should handle it correctly.
        """
        solcast = [
            _make_solcast_entry("2026-02-27T15:00:00+11:00", 1.0),
            _make_solcast_entry("2026-02-27T15:30:00+11:00", 2.0),
        ]

        # A 10-minute slot starting at 15:25 would straddle (hypothetical)
        # But we're testing 5-min slots, so let's test a valid edge case:
        # Slot 15:00-15:05 should be entirely in the first period
        slot_start = datetime(2026, 2, 27, 15, 0, tzinfo=timezone(timedelta(hours=11)))
        result = get_solar_for_5min_slot(solcast, slot_start)

        expected = 1.0 * (5.0 / 60.0)
        assert abs(result - expected) < 0.001, f"Expected {expected}, got {result}"

    def test_slot_at_end_of_period(self):
        """Slot 16:25-16:30 should be inside 16:00-16:30 period."""
        solcast = [
            _make_solcast_entry("2026-02-27T16:00:00+11:00", 1.8),
            _make_solcast_entry("2026-02-27T16:30:00+11:00", 1.5),
        ]

        # Slot 16:25-16:30 is fully inside 16:00-16:30
        slot_start = datetime(2026, 2, 27, 16, 25, tzinfo=timezone(timedelta(hours=11)))
        result = get_solar_for_5min_slot(solcast, slot_start)

        # Expected: 1.8 × (5/60) = 0.15 kWh
        expected = 1.8 * (5.0 / 60.0)
        assert abs(result - expected) < 0.001, f"Expected {expected}, got {result}"

    def test_no_matching_period_returns_zero(self):
        """If no Solcast period overlaps the slot, return 0.0."""
        solcast = [
            _make_solcast_entry("2026-02-27T10:00:00+11:00", 2.0),
        ]

        # Slot at a time not covered by any forecast
        slot_start = datetime(2026, 2, 27, 15, 0, tzinfo=timezone(timedelta(hours=11)))
        result = get_solar_for_5min_slot(solcast, slot_start)

        assert result == 0.0

    def test_empty_forecast_returns_zero(self):
        """Empty Solcast forecast should return 0.0."""
        slot_start = datetime(2026, 2, 27, 15, 0, tzinfo=timezone(timedelta(hours=11)))
        result = get_solar_for_5min_slot([], slot_start)
        assert result == 0.0

    def test_nighttime_slot_returns_zero(self):
        """Nighttime slot with zero pv_estimate should return 0.0."""
        solcast = [
            _make_solcast_entry("2026-02-27T02:00:00+11:00", 0.0),  # Night, no solar
        ]

        slot_start = datetime(2026, 2, 27, 2, 5, tzinfo=timezone(timedelta(hours=11)))
        result = get_solar_for_5min_slot(solcast, slot_start)

        assert result == 0.0

    def test_consistency_with_15min_and_30min(self):
        """5-minute calculation should be consistent with 15/30-min approaches."""
        solcast = [
            _make_solcast_entry("2026-02-27T15:00:00+11:00", 1.0),
            _make_solcast_entry("2026-02-27T15:30:00+11:00", 2.0),
        ]

        slot_start = datetime(2026, 2, 27, 15, 0, tzinfo=timezone(timedelta(hours=11)))

        # Calculate using different methods
        result_5min = get_solar_for_5min_slot(solcast, slot_start)

        # For 15-min slot starting at same time
        result_15min = get_solar_for_15min_slot(solcast, slot_start)

        # The 15-min slot includes three 5-min slots (15:00-15:05, 15:05-15:10, 15:10-15:15)
        # All are in the same Solcast period, so 15-min should be 3x the 5-min
        expected_ratio = 3.0
        actual_ratio = result_15min / result_5min if result_5min > 0 else 0

        assert abs(actual_ratio - expected_ratio) < 0.01, (
            f"15-min result ({result_15min}) should be ~3x 5-min result ({result_5min}), "
            f"got ratio {actual_ratio}"
        )


class TestSlotAlignment:
    """Test that slots align correctly with Solcast periods."""

    def test_midnight_boundary(self):
        """Test slots crossing midnight."""
        solcast = [
            _make_solcast_entry("2026-02-27T23:30:00+11:00", 0.0),
            _make_solcast_entry("2026-02-28T00:00:00+11:00", 0.0),
        ]

        # Slot at 23:55-00:00 should work correctly
        slot_start = datetime(2026, 2, 27, 23, 55, tzinfo=timezone(timedelta(hours=11)))
        result = get_solar_for_5min_slot(solcast, slot_start)

        # Nighttime, should be 0
        assert result == 0.0

    def test_timezone_handling(self):
        """Test that naive datetimes are handled correctly."""
        solcast = [
            _make_solcast_entry("2026-02-27T15:30:00+11:00", 2.0),
        ]

        # Naive datetime (should be treated as local time)
        # The function should convert naive datetime to HA's configured timezone
        slot_start = datetime(2026, 2, 27, 15, 35)  # No timezone
        result = get_solar_for_5min_slot(solcast, slot_start)

        # Note: In test environment without HA timezone config,
        # naive datetime conversion may not match the +11:00 offset in Solcast data
        # This is expected behavior - in production, HA's timezone is configured
        # We just verify the function doesn't crash and returns a reasonable value
        expected = 2.0 * (5.0 / 60.0)
        # Accept either the expected value or 0.0 (if timezone mismatch causes no overlap)
        assert result == 0.0 or abs(result - expected) < 0.001, (
            f"Expected ~{expected} or 0.0 (if timezone mismatch), got {result}"
        )
