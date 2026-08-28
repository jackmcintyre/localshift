"""Tests for Issue #510 Slice 2: retain the forecast entry covering "now".

Before this fix, `_parse_single_entry` dropped any forecast entry whose
`slot_start < now_local`. Amber's `detailedForecast` entries carry a +1
second offset on their start times (the 12:30 interval starts at
12:30:01), so the entry covering the CURRENT interval was always one
second in the past and always dropped. `_ensure_current_slot_coverage`
then synthesised slot 0 and priced it from the first SURVIVING entry --
the NEXT interval -- so plan slot 0 was priced from the wrong interval at
every evaluation.

These tests pin the fix: an entry whose interval covers `now_local` (after
flooring its start to the interval boundary) is retained as slot 0 with
`price_source="forecast_current"`, carrying its own price and its Amber
`estimate` flag. The synthetic slot becomes a stale-sensor fallback that
fires only when no entry covers "now" at all.

All entries below build dicts with an explicit `duration` field. Without
it, `get_slot_duration_minutes` would fall back to computing duration from
raw start_time/end_time deltas -- and a :01-offset 5-minute entry spans
299 seconds, which floors to 4 minutes and gets rejected by the
`duration_minutes not in (5, 30, 60)` guard for an unrelated reason. In
production this never happens because providers normalize to ForecastSlot
objects with duration already resolved (see AmberExpressProvider); tests
must supply it explicitly to exercise the coverage predicate itself.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from custom_components.localshift.engine.slot_schedule import (
    compute_hybrid_slot_schedule,
)

AEDT = timezone(timedelta(hours=11))


def _entry(
    start_time: str, duration: int, price: float, *, estimate: bool | None = None
) -> dict:
    """Build a raw forecast entry dict with an explicit duration."""
    entry: dict = {"start_time": start_time, "duration": duration, "per_kwh": price}
    if estimate is not None:
        entry["estimate"] = estimate
    return entry


class TestCurrentIntervalRetainedAsSlot0:
    """An entry whose interval covers "now" becomes slot 0, priced from itself."""

    def test_5min_entry_covering_now_retained_as_slot0(self):
        """5-min entry covering now is slot 0, priced at its own (not the next) price."""
        entries = [
            _entry("2026-03-16T12:30:01+11:00", 5, 0.10),
            _entry("2026-03-16T12:35:01+11:00", 5, 0.11),
            _entry("2026-03-16T12:40:01+11:00", 5, 0.12),
        ]
        now = datetime(2026, 3, 16, 12, 33, 0, tzinfo=AEDT)

        slots, _metadata = compute_hybrid_slot_schedule(
            now, entries, "Australia/Sydney"
        )

        assert slots[0]["price_source"] == "forecast_current"
        assert slots[0]["price"] == 0.10  # its OWN price, not 0.11 borrowed
        assert slots[0]["start"].minute == 30
        assert slots[0]["interval_minutes"] == 5

    def test_30min_entry_covering_now_retained_as_slot0(self):
        """30-min entry covering now prices slot 0, re-anchored to a 5-min quantum.

        Review feedback on the first cut of this fix: retaining the covering
        entry at its own 30-minute width (start=12:30:01, interval=30) made
        slot 0 up to 30 minutes wide, no matter how far into the interval
        "now" actually was. Every DP energy term scales by slot width
        (slot_hours = interval_minutes / 60), so a mostly-elapsed slot 0 let
        the optimiser book charge/discharge time that no longer existed, and
        it dragged core.py's DW-runway anchor and published
        precharge_runway_quantum_min back by up to 30 minutes instead of 5.
        The fix re-anchors slot 0 to the same 5-minute "now" quantum the
        stale-sensor fallback already uses, priced from the covering entry.
        """
        entries = [
            _entry("2026-03-16T12:30:01+11:00", 30, 0.20),
            _entry("2026-03-16T13:00:01+11:00", 30, 0.25),
        ]
        now = datetime(2026, 3, 16, 12, 47, 0, tzinfo=AEDT)

        slots, _metadata = compute_hybrid_slot_schedule(
            now, entries, "Australia/Sydney"
        )

        assert slots[0]["price_source"] == "forecast_current"
        assert slots[0]["price"] == 0.20  # its OWN price, not 0.25 borrowed
        # Re-anchored to the 5-min "now" quantum (floor(12:47, 5) = 12:45),
        # NOT left at the covering entry's own 30-min-wide start (12:30).
        assert slots[0]["start"] == datetime(2026, 3, 16, 12, 45, 0, tzinfo=AEDT)
        assert slots[0]["interval_minutes"] == 5

    def test_30min_entry_near_interval_end_stays_bounded_to_5min_quantum(self):
        """Regression: seconds before a 30-min interval ends, slot 0 must not claim 30 minutes.

        This is the exact shape the review feedback caught: at now=12:59:50,
        ten seconds before the 12:30-13:00 interval ends, the covering entry
        must NOT be retained as a 30-min-wide slot 0 (which would tell the
        optimiser it still has ten seconds *plus thirty minutes* to work
        with). Slot 0 stays a bounded 5-minute quantum, correctly priced from
        the covering (12:30) entry rather than the upcoming (13:00) one.
        """
        entries = [
            _entry("2026-03-16T12:30:01+11:00", 30, 0.20),
            _entry("2026-03-16T13:00:01+11:00", 30, 0.25),
        ]
        now = datetime(2026, 3, 16, 12, 59, 50, tzinfo=AEDT)

        slots, _metadata = compute_hybrid_slot_schedule(
            now, entries, "Australia/Sydney"
        )

        assert slots[0]["price_source"] == "forecast_current"
        assert slots[0]["price"] == 0.20  # still the covering (12:30) entry
        assert slots[0]["interval_minutes"] == 5  # NOT 30 -- the regression
        assert slots[0]["start"] == datetime(2026, 3, 16, 12, 55, 0, tzinfo=AEDT)
        # The slot's claimed end (12:55 + 5min = 13:00) never runs past the
        # real interval boundary, so the DP is never handed elapsed time.
        slot_end = slots[0]["start"] + timedelta(minutes=slots[0]["interval_minutes"])
        assert slot_end == datetime(2026, 3, 16, 13, 0, 0, tzinfo=AEDT)

    def test_one_second_offset_does_not_drop_current_interval(self):
        """Regression: the +1s Amber offset must not push the current entry out.

        This is the exact bug: with a naive `slot_start < now_local` check,
        the 12:30:01 entry reads as "1 second in the past" relative to
        anything after 12:30:01, and gets dropped -- forcing a synthetic
        slot priced from the *next* interval. This test fails on main.
        """
        entries = [
            _entry("2026-03-16T12:30:01+11:00", 5, 0.10),
            _entry("2026-03-16T12:35:01+11:00", 5, 0.11),
        ]
        now = datetime(2026, 3, 16, 12, 30, 30, tzinfo=AEDT)

        slots, _metadata = compute_hybrid_slot_schedule(
            now, entries, "Australia/Sydney"
        )

        assert all(s["price_source"] != "synthetic" for s in slots)
        assert slots[0]["start"] == datetime(2026, 3, 16, 12, 30, 1, tzinfo=AEDT)
        assert slots[0]["price"] == 0.10


class TestSyntheticFallback:
    """The synthetic slot is a stale-sensor fallback, not the steady state."""

    def test_synthetic_fallback_fires_only_when_no_entry_covers_now(self):
        """No entry covers now -> synthetic slot borrows the first real entry's price."""
        entries = [_entry("2026-03-16T12:45:01+11:00", 5, 0.30)]
        now = datetime(2026, 3, 16, 12, 33, 0, tzinfo=AEDT)

        slots, _metadata = compute_hybrid_slot_schedule(
            now, entries, "Australia/Sydney"
        )

        assert slots[0]["price_source"] == "synthetic"
        assert slots[0]["price"] == 0.30  # borrowed from the first real entry
        assert slots[1]["start"] == datetime(2026, 3, 16, 12, 45, 1, tzinfo=AEDT)

    def test_covered_case_produces_no_synthetic_slot(self):
        """Paired with the above: when an entry DOES cover now, no synthetic slot exists."""
        entries = [
            _entry("2026-03-16T12:30:01+11:00", 5, 0.10),
            _entry("2026-03-16T12:35:01+11:00", 5, 0.11),
        ]
        now = datetime(2026, 3, 16, 12, 33, 0, tzinfo=AEDT)

        slots, _metadata = compute_hybrid_slot_schedule(
            now, entries, "Australia/Sydney"
        )

        assert not any(s["price_source"] == "synthetic" for s in slots)


class TestElapsedEntriesStillDropped:
    """Entries whose interval has genuinely ended are still dropped, as today."""

    def test_fully_elapsed_entry_is_dropped(self):
        """An entry whose 5-min interval ended before now is dropped, not retained."""
        entries = [
            _entry("2026-03-16T12:25:01+11:00", 5, 0.05),  # ended 12:30, elapsed
            _entry("2026-03-16T12:30:01+11:00", 5, 0.10),  # covers now
        ]
        now = datetime(2026, 3, 16, 12, 33, 0, tzinfo=AEDT)

        slots, _metadata = compute_hybrid_slot_schedule(
            now, entries, "Australia/Sydney"
        )

        assert all(s["start"].minute != 25 for s in slots)
        assert slots[0]["price_source"] == "forecast_current"
        assert slots[0]["price"] == 0.10

    def test_60min_entries_unchanged_by_covering_predicate(self):
        """Guard against re-enabling coverage-flooring for 60-min entries.

        A 60-min entry starting at 12:00 nominally still "contains" now
        (12:33) if you floor to the hour -- exactly the shape `_covers_now`
        must reject via its `duration_minutes not in (5, 30)` guard. If that
        guard were ever removed, this entry would get retained and handed to
        `_split_60min_slot`, which would produce a slot 0 already half an
        hour in the past -- a new bug. Today's (unchanged) rule for 60-min
        entries is a plain `slot_start < now_local` compare, so it is
        dropped exactly like it is on main. A second, future entry is
        included so parsing doesn't return empty outright and the
        stale-sensor synthetic fallback actually runs.
        """
        entries = [
            _entry("2026-03-16T12:00:00+11:00", 60, 0.40),
            _entry("2026-03-16T13:00:01+11:00", 30, 0.22),
        ]
        now = datetime(2026, 3, 16, 12, 33, 0, tzinfo=AEDT)

        slots, _metadata = compute_hybrid_slot_schedule(
            now, entries, "Australia/Sydney"
        )

        assert all(s["start"].hour != 12 or s["start"].minute != 0 for s in slots)
        assert slots[0]["price_source"] == "synthetic"


class TestEstimateFlag:
    """The covering entry's Amber `estimate` flag is carried through to slot 0."""

    def test_estimate_flag_carried_to_slot0(self, caplog):
        """estimate=False on the covering entry lands on slots[0] and in the log line."""
        entries = [
            _entry("2026-03-16T12:30:01+11:00", 5, 0.10, estimate=False),
            _entry("2026-03-16T12:35:01+11:00", 5, 0.11, estimate=True),
        ]
        now = datetime(2026, 3, 16, 12, 33, 0, tzinfo=AEDT)

        with caplog.at_level(logging.INFO):
            slots, _metadata = compute_hybrid_slot_schedule(
                now, entries, "Australia/Sydney"
            )

        assert slots[0]["estimate"] is False
        assert any(
            "SLOT0_CURRENT" in record.message and "estimate=False" in record.message
            for record in caplog.records
        )
