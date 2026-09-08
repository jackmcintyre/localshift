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

import pytest

from custom_components.localshift.engine.slot_schedule import (
    compute_hybrid_slot_schedule,
)

AEDT = timezone(timedelta(hours=11))

# Issue #947/#948/#949: how far a slot's start may sit past the exact end of
# its predecessor before we call it a gap. Providers (Amber) offset interval
# starts by +1s, so an exact-equality assertion would fail on every real feed.
SKEW_TOLERANCE_S = 1.0


def _entry(
    start_time: str, duration: int, price: float, *, estimate: bool | None = None
) -> dict:
    """Build a raw forecast entry dict with an explicit duration."""
    entry: dict = {"start_time": start_time, "duration": duration, "per_kwh": price}
    if estimate is not None:
        entry["estimate"] = estimate
    return entry


def _assert_no_gaps(slots: list[dict]) -> None:
    """Assert the schedule's NO GAPS contract: each slot ends where the next starts.

    Issues #947/#948: a hole in the slot schedule is not a cosmetic defect —
    the DP plans over `slots[i].start .. slots[i+1].start`, so a gap silently
    removes real time from the horizon and the optimiser never sees it. The
    1-second tolerance absorbs Amber's documented +1s interval-start offset.
    """
    assert slots, "expected a non-empty slot schedule"
    for prev, nxt in zip(slots, slots[1:], strict=False):
        expected_end = prev["start"] + timedelta(minutes=prev["interval_minutes"])
        gap_s = (nxt["start"] - expected_end).total_seconds()
        assert abs(gap_s) <= SKEW_TOLERANCE_S, (
            f"gap/overlap between {prev['start'].isoformat()} "
            f"(+{prev['interval_minutes']}min) and {nxt['start'].isoformat()}: "
            f"{gap_s:+.1f}s"
        )


def _assert_no_overlap(slots: list[dict]) -> None:
    """Assert no slot claims time its predecessor already owns (#948).

    Double-counted time inflates the energy the DP can book (every energy term
    scales by slot width), so overlap is as bad as a gap — just quieter.
    """
    for prev, nxt in zip(slots, slots[1:], strict=False):
        expected_end = prev["start"] + timedelta(minutes=prev["interval_minutes"])
        assert nxt["start"] >= expected_end - timedelta(seconds=SKEW_TOLERANCE_S), (
            f"{nxt['start'].isoformat()} overlaps slot ending "
            f"{expected_end.isoformat()}"
        )


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

        with caplog.at_level(logging.DEBUG):
            slots, _metadata = compute_hybrid_slot_schedule(
                now, entries, "Australia/Sydney"
            )

        assert slots[0]["estimate"] is False
        assert any(
            "SLOT0_CURRENT" in record.message and "estimate=False" in record.message
            for record in caplog.records
        )


# ==============================================================================
# Issue #946: a malformed duration must skip the entry, never abort the build
# ==============================================================================


class TestMalformedDurationDoesNotAbort:
    """A bad `duration` skips ONE entry; it must not take the whole schedule down.

    `_parse_single_entry` resolves the entry's duration BEFORE the past-entry
    drop, so `int(duration)` seeing a non-integral value ("30.0") raises
    ValueError straight out of `compute_hybrid_slot_schedule`. SlotBuilder
    (engine/slots.py) has no handler for that, so the exception propagates and
    the optimiser is left with NO plan at all — far worse than a horizon that
    is one interval short. The entry is skipped (`_parse_single_entry` returns
    None) and the rest of the feed survives.
    """

    def test_non_integral_duration_string_skips_entry_not_schedule(self):
        """duration="30.0" is skipped; the good entries around it still build a plan."""
        entries = [
            _entry("2026-03-16T12:30:01+11:00", "30.0", 0.20),  # malformed
            _entry("2026-03-16T13:00:01+11:00", 30, 0.25),
            _entry("2026-03-16T13:30:01+11:00", 30, 0.30),
        ]
        now = datetime(2026, 3, 16, 12, 47, tzinfo=AEDT)

        # Must not raise ValueError("invalid literal for int() with base 10")
        slots, metadata = compute_hybrid_slot_schedule(now, entries, "Australia/Sydney")

        assert slots, "schedule must survive one malformed entry"
        assert slots[0]["price"] == 0.25  # the 12:30 entry is gone, not slot 0
        assert all(s["price"] != 0.20 for s in slots)
        assert metadata["total_slots"] == len(slots)

    def test_non_numeric_duration_type_skips_entry_not_schedule(self):
        """A duration of the wrong TYPE (e.g. []) is a TypeError, also contained."""
        entries = [
            _entry("2026-03-16T12:30:01+11:00", [], 0.20),  # malformed type
            _entry("2026-03-16T12:35:01+11:00", 5, 0.11),
        ]
        # now must fall inside the SECOND (well-formed) entry's interval --
        # the malformed first entry is dropped entirely, so it can no longer
        # cover "now" and there is nothing before 12:35:01 to be current.
        now = datetime(2026, 3, 16, 12, 36, tzinfo=AEDT)

        slots, _metadata = compute_hybrid_slot_schedule(
            now, entries, "Australia/Sydney"
        )

        assert slots, "schedule must survive one malformed entry"
        assert all(s["price"] != 0.20 for s in slots)
        assert slots[0]["price_source"] == "forecast_current"
        assert slots[0]["price"] == 0.11

    def test_every_entry_malformed_returns_empty_not_raise(self):
        """All-malformed feed returns ([], metadata) with a warning — same as all-stale."""
        entries = [
            _entry("2026-03-16T12:30:01+11:00", "30.0", 0.20),
            _entry("2026-03-16T13:00:01+11:00", "30.0", 0.25),
        ]
        now = datetime(2026, 3, 16, 12, 47, tzinfo=AEDT)

        slots, metadata = compute_hybrid_slot_schedule(now, entries, "Australia/Sydney")

        assert slots == []
        assert metadata["total_slots"] == 0

    def test_malformed_duration_logs_a_warning(self, caplog):
        """The skip is observable: one aggregated warning names the dropped count."""
        entries = [
            _entry("2026-03-16T12:30:01+11:00", "30.0", 0.20),
            _entry("2026-03-16T12:40:01+11:00", "30.0", 0.21),
            _entry("2026-03-16T13:00:01+11:00", 30, 0.25),
        ]
        now = datetime(2026, 3, 16, 12, 47, tzinfo=AEDT)

        with caplog.at_level(logging.WARNING):
            slots, _metadata = compute_hybrid_slot_schedule(
                now, entries, "Australia/Sydney"
            )

        assert slots
        assert any("malformed duration" in r.message.lower() for r in caplog.records)


# ==============================================================================
# Issue #947: now == entry start must keep the 30-min entry at full width
# ==============================================================================


class TestBoundaryEntryKeepsFullWidth:
    """Nothing has elapsed when now IS the interval start — so nothing is trimmed.

    The `is_current and duration_minutes == 30` re-anchor branch collapsed the
    covering entry to a 5-minute slot at floor_5(now) unconditionally. At
    now == entry start that anchor is the entry's own start, so slot 0 claimed
    5 minutes and the remaining 25 minutes of the interval were deleted from
    the head of the horizon — a 25-minute hole and a direct breach of this
    module's NO GAPS contract. Nothing has elapsed, so there is nothing to
    protect the DP from: keep the entry at its own 30-minute width.
    """

    def test_aligned_feed_now_equals_entry_start_keeps_full_30min(self):
        """now == 12:30:00 exactly → slot 0 is the full 12:30 +30m interval, no hole."""
        entries = [
            _entry("2026-03-16T12:30:00+11:00", 30, 0.20),
            _entry("2026-03-16T13:00:00+11:00", 30, 0.25),
        ]
        now = datetime(2026, 3, 16, 12, 30, 0, tzinfo=AEDT)

        slots, _metadata = compute_hybrid_slot_schedule(
            now, entries, "Australia/Sydney"
        )

        assert slots[0]["price_source"] == "forecast_current"
        assert slots[0]["price"] == 0.20
        assert slots[0]["start"] == datetime(2026, 3, 16, 12, 30, 0, tzinfo=AEDT)
        assert slots[0]["interval_minutes"] == 30  # NOT 5 -- the 25-min deletion
        assert not any(s["price_source"] == "synthetic" for s in slots)
        _assert_no_gaps(slots)

    def test_plus_1s_feed_now_equals_entry_start_keeps_full_30min(self):
        """Sub-second/1s-pre-offset window: the entry still starts at/before now.

        Amber's detailedForecast starts the 12:30 interval at 12:30:01. At
        now == 12:30:00 the raw entry start (12:30:01) is one second AFTER
        now, so the entry's origin is not strictly before now — re-anchoring
        would again delete 25 minutes. The entry keeps its own start and width.
        """
        entries = [
            _entry("2026-03-16T12:30:01+11:00", 30, 0.20),
            _entry("2026-03-16T13:00:01+11:00", 30, 0.25),
        ]
        now = datetime(2026, 3, 16, 12, 30, 0, tzinfo=AEDT)

        slots, _metadata = compute_hybrid_slot_schedule(
            now, entries, "Australia/Sydney"
        )

        assert slots[0]["price_source"] == "forecast_current"
        assert slots[0]["price"] == 0.20
        assert slots[0]["start"] == datetime(2026, 3, 16, 12, 30, 1, tzinfo=AEDT)
        assert slots[0]["interval_minutes"] == 30
        assert not any(s["price_source"] == "synthetic" for s in slots)
        _assert_no_gaps(slots)


# ==============================================================================
# Issue #948: a misaligned feed must not lose a real interval
# ==============================================================================


class TestMisalignedFeedKeepsCoveredInterval:
    """The re-anchor hand-off must not delete the next real 30-minute interval.

    Re-anchoring injects a 5-minute slot 0 into an otherwise all-30-minute
    list, which switches `_build_hybrid_schedule` from `_add_all_30min_slots`
    to `_add_30min_after_transition` — and that path used to discard every
    30-minute entry starting before floor_5(now) + 5 minutes. On a feed whose
    intervals are not aligned to the 5-minute grid (:18/:48) the entry at 12:48
    was dropped outright, leaving a hole after slot 0 instead of a schedule.
    """

    ENTRIES = [
        _entry("2026-03-16T12:18:00+11:00", 30, 0.20),
        _entry("2026-03-16T12:48:00+11:00", 30, 0.21),
        _entry("2026-03-16T13:18:00+11:00", 30, 0.22),
    ]

    def test_misaligned_feed_keeps_the_1248_interval(self):
        """:18/:48 feed, now=12:46:30 → the real 12:48 interval survives."""
        now = datetime(2026, 3, 16, 12, 46, 30, tzinfo=AEDT)

        slots, _metadata = compute_hybrid_slot_schedule(
            now, self.ENTRIES, "Australia/Sydney"
        )

        assert any(
            s["start"] == datetime(2026, 3, 16, 12, 48, 0, tzinfo=AEDT) for s in slots
        ), "the real 12:48 interval was dropped from the schedule"
        assert slots[0]["interval_minutes"] == 5  # slot 0 stays a bounded quantum
        assert slots[0]["start"] <= now  # slot 0 covers now
        _assert_no_gaps(slots)
        _assert_no_overlap(slots)

    def test_misaligned_feed_slot0_ends_on_the_real_interval_boundary(self):
        """Slot 0's back-off lands exactly on the covering interval's end (12:48)."""
        now = datetime(2026, 3, 16, 12, 46, 30, tzinfo=AEDT)

        slots, _metadata = compute_hybrid_slot_schedule(
            now, self.ENTRIES, "Australia/Sydney"
        )

        slot0_end = slots[0]["start"] + timedelta(minutes=slots[0]["interval_minutes"])
        assert slot0_end == datetime(2026, 3, 16, 12, 48, 0, tzinfo=AEDT)
        assert slots[0]["price_source"] == "forecast_current"
        assert slots[0]["price"] == 0.20  # priced from the covering 12:18 entry

    def test_misaligned_feed_does_not_fall_back_to_synthetic(self):
        """A covering entry exists, so the stale-sensor fallback must stay silent."""
        now = datetime(2026, 3, 16, 12, 46, 30, tzinfo=AEDT)

        slots, _metadata = compute_hybrid_slot_schedule(
            now, self.ENTRIES, "Australia/Sydney"
        )

        assert not any(s["price_source"] == "synthetic" for s in slots)


# ==============================================================================
# Issue #949: the boundary tolerance must scale to the interval
# ==============================================================================


class TestBoundaryToleranceScalesToInterval:
    """A 60s tolerance is 20% of a 5-minute interval — far too generous there.

    `_BOUNDARY_OFFSET_TOLERANCE_S = 60` was applied to both granularities, so a
    5-minute entry starting up to 59 seconds late was treated as if it had
    started on the boundary and "covered" now: slot 0 then began AFTER now with
    nothing covering the interim. The tolerance is scaled to the interval so it
    stays a small fraction of it, while remaining ~10x the documented +1s
    Amber skew. A genuinely late 5-minute entry now falls through to the
    stale-sensor synthetic fallback, which is loud — the honest failure mode.
    """

    def test_5min_entry_30s_late_is_not_current(self):
        """A 5-min entry starting 12:30:30 does not cover now=12:30:15."""
        entries = [
            _entry("2026-03-16T12:30:30+11:00", 5, 0.10),
            _entry("2026-03-16T12:35:30+11:00", 5, 0.11),
        ]
        now = datetime(2026, 3, 16, 12, 30, 15, tzinfo=AEDT)

        slots, _metadata = compute_hybrid_slot_schedule(
            now, entries, "Australia/Sydney"
        )

        # No real entry covers now, so the stale-sensor fallback fires...
        assert slots[0]["price_source"] == "synthetic"
        assert slots[0]["price"] == 0.10
        # ...and the genuinely-late entry keeps its own unfloored start,
        # rather than being silently re-dated onto the boundary.
        assert any(
            s["start"] == datetime(2026, 3, 16, 12, 30, 30, tzinfo=AEDT) for s in slots
        )

    def test_5min_plus_1s_offset_is_still_absorbed(self):
        """Guard against over-tightening: the documented +1s skew still works.

        Companion to `test_one_second_offset_does_not_drop_current_interval`
        above, pinned here so the scaling cannot silently collapse to zero.
        """
        entries = [
            _entry("2026-03-16T12:30:01+11:00", 5, 0.10),
            _entry("2026-03-16T12:35:01+11:00", 5, 0.11),
        ]
        now = datetime(2026, 3, 16, 12, 30, 30, tzinfo=AEDT)

        slots, _metadata = compute_hybrid_slot_schedule(
            now, entries, "Australia/Sydney"
        )

        assert slots[0]["price_source"] == "forecast_current"
        assert slots[0]["price"] == 0.10
        assert not any(s["price_source"] == "synthetic" for s in slots)

    def test_30min_tolerance_is_unchanged_by_scaling(self):
        """On a 30-min interval the +1s skew is still absorbed (60s cap preserved)."""
        entries = [
            _entry("2026-03-16T12:30:01+11:00", 30, 0.20),
            _entry("2026-03-16T13:00:01+11:00", 30, 0.25),
        ]
        now = datetime(2026, 3, 16, 12, 30, 30, tzinfo=AEDT)

        slots, _metadata = compute_hybrid_slot_schedule(
            now, entries, "Australia/Sydney"
        )

        assert slots[0]["price_source"] == "forecast_current"
        assert slots[0]["price"] == 0.20
        assert not any(s["price_source"] == "synthetic" for s in slots)


# ==============================================================================
# Coverage top-ups: parsing branches reachable only through the public entrypoint
# ==============================================================================


class TestParseEdgeCases:
    """Malformed/neighbouring-edge entries are dropped without disturbing the rest."""

    def test_non_dict_entry_is_skipped(self):
        """A bare string in the forecast list is skipped (no .get attribute)."""
        entries = [
            "not-an-entry",
            _entry("2026-03-16T12:30:01+11:00", 5, 0.10),
        ]
        now = datetime(2026, 3, 16, 12, 33, tzinfo=AEDT)

        slots, _metadata = compute_hybrid_slot_schedule(
            now, entries, "Australia/Sydney"
        )

        assert slots
        assert slots[0]["price"] == 0.10

    def test_entry_without_start_time_is_skipped(self):
        """An entry with no start_time contributes nothing."""
        entries = [
            {"duration": 5, "per_kwh": 0.10},
            _entry("2026-03-16T12:30:01+11:00", 5, 0.12),
        ]
        now = datetime(2026, 3, 16, 12, 33, tzinfo=AEDT)

        slots, _metadata = compute_hybrid_slot_schedule(
            now, entries, "Australia/Sydney"
        )

        assert all(s["price"] != 0.10 for s in slots)
        assert slots[0]["price"] == 0.12

    def test_unparseable_start_time_is_skipped(self):
        """A start_time that will not parse as ISO is skipped."""
        entries = [
            {"start_time": "not-a-timestamp", "duration": 5, "per_kwh": 0.10},
            _entry("2026-03-16T12:30:01+11:00", 5, 0.12),
        ]
        now = datetime(2026, 3, 16, 12, 33, tzinfo=AEDT)

        slots, _metadata = compute_hybrid_slot_schedule(
            now, entries, "Australia/Sydney"
        )

        assert all(s["price"] != 0.10 for s in slots)
        assert slots[0]["price"] == 0.12

    def test_duration_from_end_time_delta(self):
        """No `duration` field: duration comes from the end_time - start_time delta."""
        entries = [
            {
                "start_time": "2026-03-16T12:30:01+11:00",
                "end_time": "2026-03-16T13:00:01+11:00",
                "per_kwh": 0.20,
            },
            {
                "start_time": "2026-03-16T13:00:01+11:00",
                "end_time": "2026-03-16T13:30:01+11:00",
                "per_kwh": 0.25,
            },
        ]
        # now sits exactly at the entry's floored origin (not mid-interval) so
        # this exercises the #947 "nothing elapsed" full-width path rather
        # than the #948 re-anchor-to-5min path -- this test's job is only to
        # prove the end_time-delta duration computation (lines resolving
        # duration from end_time - start_time), not the re-anchor branch.
        now = datetime(2026, 3, 16, 12, 30, 0, tzinfo=AEDT)

        slots, _metadata = compute_hybrid_slot_schedule(
            now, entries, "Australia/Sydney"
        )

        assert slots[0]["interval_minutes"] == 30
        assert slots[0]["price_source"] == "forecast_current"
        assert slots[1]["start"] == datetime(2026, 3, 16, 13, 0, 1, tzinfo=AEDT)

    def test_duration_from_unparseable_end_time_is_skipped(self):
        """An end_time that will not parse yields no duration, so the entry is dropped."""
        entries = [
            {
                "start_time": "2026-03-16T12:30:01+11:00",
                "end_time": "not-a-timestamp",
                "per_kwh": 0.20,
            },
            _entry("2026-03-16T12:35:01+11:00", 5, 0.11),
        ]
        now = datetime(2026, 3, 16, 12, 33, tzinfo=AEDT)

        slots, _metadata = compute_hybrid_slot_schedule(
            now, entries, "Australia/Sydney"
        )

        assert all(s["price"] != 0.20 for s in slots)
        assert slots[0]["price"] == 0.11

    def test_entry_with_end_time_but_no_duration_and_no_delta(self):
        """An end_time-only entry with an unparseable pair is dropped, not fatal."""
        entries = [
            {"start_time": "", "end_time": "2026-03-16T13:00:00+11:00", "per_kwh": 0.2},
            _entry("2026-03-16T12:30:01+11:00", 5, 0.10),
        ]
        now = datetime(2026, 3, 16, 12, 33, tzinfo=AEDT)

        slots, _metadata = compute_hybrid_slot_schedule(
            now, entries, "Australia/Sydney"
        )

        assert slots[0]["price"] == 0.10


class TestEmptyHorizonGuard:
    """The empty-slots guard in the coverage fallback runs when nothing survives."""

    def test_all_slots_past_cutoff_leaves_schedule_empty(self):
        """max_forecast_hours=0 puts every future entry past the cutoff.

        Nothing is left after the cutoff filter, so `_ensure_current_slot_-
        coverage` takes its empty-list early return instead of indexing
        `slots[0]`, and the caller gets an empty plan rather than an
        IndexError.
        """
        entries = [
            _entry("2026-03-16T13:00:01+11:00", 30, 0.25),
            _entry("2026-03-16T13:30:01+11:00", 30, 0.30),
        ]
        now = datetime(2026, 3, 16, 12, 47, tzinfo=AEDT)

        slots, metadata = compute_hybrid_slot_schedule(
            now, entries, "Australia/Sydney", max_forecast_hours=0
        )

        assert slots == []
        assert metadata["total_slots"] == 0


class TestNoGapsContract:
    """The module's own contract, checked across every feed shape above."""

    @pytest.mark.parametrize(
        ("entries", "now"),
        [
            # aligned 30-min feed, now exactly on a boundary (#947)
            (
                [
                    _entry("2026-03-16T12:30:00+11:00", 30, 0.20),
                    _entry("2026-03-16T13:00:00+11:00", 30, 0.25),
                    _entry("2026-03-16T13:30:00+11:00", 30, 0.30),
                ],
                datetime(2026, 3, 16, 12, 30, 0, tzinfo=AEDT),
            ),
            # +1s 30-min feed, now exactly on a boundary (#947)
            (
                [
                    _entry("2026-03-16T12:30:01+11:00", 30, 0.20),
                    _entry("2026-03-16T13:00:01+11:00", 30, 0.25),
                    _entry("2026-03-16T13:30:01+11:00", 30, 0.30),
                ],
                datetime(2026, 3, 16, 12, 30, 0, tzinfo=AEDT),
            ),
            # misaligned :18/:48 feed mid-interval (#948)
            (
                [
                    _entry("2026-03-16T12:18:00+11:00", 30, 0.20),
                    _entry("2026-03-16T12:48:00+11:00", 30, 0.21),
                    _entry("2026-03-16T13:18:00+11:00", 30, 0.22),
                ],
                datetime(2026, 3, 16, 12, 46, 30, tzinfo=AEDT),
            ),
            # misaligned feed, now at the tail of the covering interval
            (
                [
                    _entry("2026-03-16T12:18:00+11:00", 30, 0.20),
                    _entry("2026-03-16T12:48:00+11:00", 30, 0.21),
                    _entry("2026-03-16T13:18:00+11:00", 30, 0.22),
                ],
                datetime(2026, 3, 16, 12, 47, 30, tzinfo=AEDT),
            ),
        ],
        ids=[
            "aligned-boundary",
            "plus1s-boundary",
            "misaligned-mid",
            "misaligned-tail",
        ],
    )
    def test_schedule_is_contiguous(self, entries, now):
        """Every accepted feed shape yields a gap-free, overlap-free horizon."""
        slots, _metadata = compute_hybrid_slot_schedule(
            now, entries, "Australia/Sydney"
        )

        assert slots
        assert not any(s["price_source"] == "synthetic" for s in slots)
        _assert_no_gaps(slots)
        _assert_no_overlap(slots)


# ==============================================================================
# Coverage top-ups: lines not reached by any issue-specific test above
# ==============================================================================


class TestCoverageTopUps:
    """Remaining branches the four issue fixes pass through but don't target."""

    def test_empty_general_forecast_returns_empty_not_raise(self):
        """No forecast at all -> empty schedule, not an IndexError or exception."""
        now = datetime(2026, 3, 16, 12, 33, tzinfo=AEDT)

        slots, metadata = compute_hybrid_slot_schedule(now, [], "Australia/Sydney")

        assert slots == []
        assert metadata["total_slots"] == 0

    def test_future_60min_entry_is_split_into_two_30min_slots(self):
        """A 60-min entry that has NOT elapsed is kept and split (`_split_60min_slot`).

        `TestParseEdgeCases.test_60min_entries_unchanged_by_covering_predicate`
        only exercises the PAST 60-min case (dropped before ever reaching
        `_split_60min_slot`). This entry starts in the future, so it survives
        the past-entry drop and must be split into two 30-min slots.
        """
        entries = [
            _entry("2026-03-16T14:00:00+11:00", 60, 0.33),
            _entry("2026-03-16T13:00:01+11:00", 30, 0.22),
        ]
        now = datetime(2026, 3, 16, 12, 33, 0, tzinfo=AEDT)

        slots, _metadata = compute_hybrid_slot_schedule(
            now, entries, "Australia/Sydney"
        )

        split = [s for s in slots if s["start"].hour == 14]
        assert len(split) == 2
        assert split[0]["start"] == datetime(2026, 3, 16, 14, 0, 0, tzinfo=AEDT)
        assert split[1]["start"] == datetime(2026, 3, 16, 14, 30, 0, tzinfo=AEDT)
        assert all(s["interval_minutes"] == 30 for s in split)
        assert all(s["price"] == 0.33 for s in split)

    def test_entry_with_no_duration_and_no_end_time_is_skipped(self):
        """No `duration` and no `end_time` -> duration unresolvable -> entry dropped."""
        entries = [
            {"start_time": "2026-03-16T12:30:01+11:00", "per_kwh": 0.20},
            _entry("2026-03-16T12:35:01+11:00", 5, 0.11),
        ]
        now = datetime(2026, 3, 16, 12, 36, tzinfo=AEDT)

        slots, _metadata = compute_hybrid_slot_schedule(
            now, entries, "Australia/Sydney"
        )

        assert slots, "the durationless entry is skipped, not fatal"
        assert all(s["price"] != 0.20 for s in slots)
        assert slots[0]["price_source"] == "forecast_current"
        assert slots[0]["price"] == 0.11

    def test_30min_entry_fully_inside_5min_block_is_not_duplicated(self):
        """A 30-min entry entirely covered by the 5-min block is dropped (line 627).

        With a full ~1h run of 5-min entries (last_5min_end well past any one
        30-min interval's end), a 30-min entry that starts and ends inside
        that span is already represented at finer granularity -- keeping it
        too would double-book that time in the DP.
        """
        five_min_entries = [
            _entry(
                f"2026-03-16T{12 + (30 + 5 * i) // 60:02d}:{(30 + 5 * i) % 60:02d}:01+11:00",
                5,
                0.10 + i * 0.01,
            )
            for i in range(12)  # 12:30:01 .. 13:25:01, i.e. covers to 13:30:01
        ]
        fully_contained = _entry("2026-03-16T12:45:01+11:00", 30, 0.50)
        after_block = _entry("2026-03-16T13:30:01+11:00", 30, 0.60)
        entries = [*five_min_entries, fully_contained, after_block]
        now = datetime(2026, 3, 16, 12, 33, 0, tzinfo=AEDT)

        slots, _metadata = compute_hybrid_slot_schedule(
            now, entries, "Australia/Sydney"
        )

        assert all(s["price"] != 0.50 for s in slots), (
            "the fully-contained 30-min entry must not be duplicated"
        )
        assert any(s["price"] == 0.60 for s in slots)
        _assert_no_gaps(slots)
        _assert_no_overlap(slots)

    def test_30min_entry_past_cutoff_stops_the_transition_loop(self):
        """A 30-min entry starting at/after cutoff_time breaks the loop (line 629)."""
        entries = [
            _entry("2026-03-16T12:30:01+11:00", 5, 0.10),
            _entry("2026-03-16T12:35:01+11:00", 30, 0.15),
            _entry("2026-03-16T14:00:01+11:00", 30, 0.99),  # past a 1h cutoff
        ]
        now = datetime(2026, 3, 16, 12, 33, 0, tzinfo=AEDT)

        slots, _metadata = compute_hybrid_slot_schedule(
            now, entries, "Australia/Sydney", max_forecast_hours=1
        )

        assert all(s["price"] != 0.99 for s in slots), (
            "the past-cutoff entry must not appear in the schedule"
        )
        assert any(s["price"] == 0.15 for s in slots)
