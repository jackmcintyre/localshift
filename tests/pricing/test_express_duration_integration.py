"""Integration test: Amber Express duration inference through slot schedule pipeline.

Verifies the full pipeline from raw Express API data (no duration field)
through provider normalization to compute_hybrid_slot_schedule(), ensuring
5-minute near-term slots are correctly identified and separated from
30-minute extended forecast slots.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

from custom_components.localshift.engine.slot_schedule import (
    compute_hybrid_slot_schedule,
)
from custom_components.localshift.pricing.provider import AmberExpressProvider


def _make_express_entry(
    start: str, end: str, price: float, *, demand_window: bool = False
) -> dict:
    """Create a realistic Amber Express forecast entry (NO duration field)."""
    return {
        "start_time": start,
        "end_time": end,
        "per_kwh": price,
        "demand_window": demand_window,
    }


def _build_realistic_express_forecast() -> list[dict]:
    """Build a realistic forecast with 7 five-min slots + 30-min slots.

    Mimics actual Amber Express data with :01 second offset timestamps.
    """
    entries = []

    # 7 five-minute slots (09:25:01 -> 10:00:00)
    five_min_starts = [
        ("2026-03-16T09:25:01+11:00", "2026-03-16T09:30:00+11:00"),
        ("2026-03-16T09:30:01+11:00", "2026-03-16T09:35:00+11:00"),
        ("2026-03-16T09:35:01+11:00", "2026-03-16T09:40:00+11:00"),
        ("2026-03-16T09:40:01+11:00", "2026-03-16T09:45:00+11:00"),
        ("2026-03-16T09:45:01+11:00", "2026-03-16T09:50:00+11:00"),
        ("2026-03-16T09:50:01+11:00", "2026-03-16T09:55:00+11:00"),
        ("2026-03-16T09:55:01+11:00", "2026-03-16T10:00:00+11:00"),
    ]
    for i, (start, end) in enumerate(five_min_starts):
        entries.append(_make_express_entry(start, end, 0.10 + i * 0.01))

    # 4 thirty-minute slots (10:00:01 -> 12:00:00)
    thirty_min_starts = [
        ("2026-03-16T10:00:01+11:00", "2026-03-16T10:30:00+11:00"),
        ("2026-03-16T10:30:01+11:00", "2026-03-16T11:00:00+11:00"),
        ("2026-03-16T11:00:01+11:00", "2026-03-16T11:30:00+11:00"),
        ("2026-03-16T11:30:01+11:00", "2026-03-16T12:00:00+11:00"),
    ]
    for i, (start, end) in enumerate(thirty_min_starts):
        entries.append(_make_express_entry(start, end, 0.20 + i * 0.05))

    return entries


class TestExpressDurationIntegration:
    """End-to-end tests for Express duration inference pipeline."""

    def test_provider_infers_correct_durations(self):
        """Provider assigns duration=5 to 5-min slots, duration=30 to 30-min slots."""
        provider = AmberExpressProvider()

        hass = MagicMock()
        state = MagicMock()
        state.attributes = {"forecasts": _build_realistic_express_forecast()}
        hass.states.get.return_value = state

        slots = provider.read_forecasts(hass, "sensor.amber_express_general_price")

        assert len(slots) == 11  # 7 five-min + 4 thirty-min

        # First 7 should be 5-minute duration
        for i in range(7):
            assert slots[i].duration == 5, (
                f"Slot {i} should have duration=5, got {slots[i].duration}"
            )

        # Last 4 should be 30-minute duration
        for i in range(7, 11):
            assert slots[i].duration == 30, (
                f"Slot {i} should have duration=30, got {slots[i].duration}"
            )

    def test_hybrid_schedule_produces_5min_and_30min_slots(self):
        """compute_hybrid_slot_schedule correctly separates 5-min from 30-min slots."""
        provider = AmberExpressProvider()

        hass = MagicMock()
        state = MagicMock()
        state.attributes = {"forecasts": _build_realistic_express_forecast()}
        hass.states.get.return_value = state

        forecast_slots = provider.read_forecasts(
            hass, "sensor.amber_express_100h_general_price"
        )

        # Feed ForecastSlot objects to compute_hybrid_slot_schedule
        # now_local must be BEFORE the forecast entries (which are 09:25-12:00 AEDT)
        # Use AEDT timezone (UTC+11) to match forecast timestamps
        from datetime import timedelta

        aedt = timezone(timedelta(hours=11))
        now = datetime(2026, 3, 16, 9, 25, 0, tzinfo=aedt)
        slots, metadata = compute_hybrid_slot_schedule(
            now, [vars(slot) for slot in forecast_slots], "Australia/Sydney"
        )

        # Should have produced output slots
        assert len(slots) > 0, "compute_hybrid_slot_schedule returned no slots"

        # Check that we have both 5-min and 30-min interval_minutes
        intervals = {s.get("interval_minutes") or s["interval_minutes"] for s in slots}
        assert 5 in intervals, (
            f"Expected 5-minute slots in output, got intervals: {intervals}"
        )
        assert 30 in intervals, (
            f"Expected 30-minute slots in output, got intervals: {intervals}"
        )

    def test_no_duration_field_does_not_collapse_to_all_30min(self):
        """Regression: without duration inference, all slots become 30-min.

        This was the original bug: raw.get("duration", 30) defaulted ALL
        Express entries to 30 minutes, eliminating real 5-min slots.
        """
        provider = AmberExpressProvider()

        hass = MagicMock()
        state = MagicMock()
        # Use entries with NO duration field (real Express format)
        state.attributes = {"forecasts": _build_realistic_express_forecast()}
        hass.states.get.return_value = state

        slots = provider.read_forecasts(hass, "sensor.amber_express_100h_general_price")

        five_min_count = sum(1 for s in slots if s.duration == 5)
        thirty_min_count = sum(1 for s in slots if s.duration == 30)

        assert five_min_count == 7, (
            f"Expected 7 five-min slots, got {five_min_count} "
            f"(regression: all slots collapsed to 30-min)"
        )
        assert thirty_min_count == 4, (
            f"Expected 4 thirty-min slots, got {thirty_min_count}"
        )
