"""Tests for issue #976: "Hybrid slot schedule:" logs INFO only on change.

Before this fix, `_compute_slot_metadata` logged "Hybrid slot schedule: ..."
at INFO on every optimizer cycle regardless of whether anything about the
computed horizon had changed -- several lines of unchanging diagnostic
noise per cycle. It now compares a horizon signature (slot count, first
slot start, last slot start) against the previous cycle's and logs at INFO
only when that signature differs, DEBUG otherwise.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pytest

from custom_components.localshift.engine import slot_schedule
from custom_components.localshift.engine.slot_schedule import (
    compute_hybrid_slot_schedule,
)

AEDT = timezone(timedelta(hours=11))


def _entry(start_time: str, duration: int, price: float) -> dict:
    return {"start_time": start_time, "duration": duration, "per_kwh": price}


@pytest.fixture(autouse=True)
def _reset_horizon_signature(monkeypatch):
    """Each test starts as if no cycle has run yet -- the first call is INFO."""
    monkeypatch.setattr(slot_schedule, "_LAST_HORIZON_SIGNATURE", None)


def test_first_cycle_after_reset_logs_at_info(caplog):
    """The first call after a fresh (None) signature always logs INFO."""
    entries = [
        _entry("2026-03-16T12:30:01+11:00", 5, 0.10),
        _entry("2026-03-16T12:35:01+11:00", 5, 0.11),
    ]
    now = datetime(2026, 3, 16, 12, 33, 0, tzinfo=AEDT)

    with caplog.at_level(logging.DEBUG):
        compute_hybrid_slot_schedule(now, entries, "Australia/Sydney")

    records = [
        r
        for r in caplog.records
        if "Hybrid slot schedule:" in r.getMessage()
    ]
    assert records, "expected a 'Hybrid slot schedule:' log record"
    assert records[-1].levelno == logging.INFO


def test_unchanged_horizon_on_second_call_logs_at_debug(caplog):
    """An immediate identical second call logs the same line at DEBUG only."""
    entries = [
        _entry("2026-03-16T12:30:01+11:00", 5, 0.10),
        _entry("2026-03-16T12:35:01+11:00", 5, 0.11),
    ]
    now = datetime(2026, 3, 16, 12, 33, 0, tzinfo=AEDT)

    with caplog.at_level(logging.DEBUG):
        compute_hybrid_slot_schedule(now, entries, "Australia/Sydney")
        caplog.clear()
        compute_hybrid_slot_schedule(now, entries, "Australia/Sydney")

    records = [
        r
        for r in caplog.records
        if "Hybrid slot schedule:" in r.getMessage()
    ]
    assert records, "expected a 'Hybrid slot schedule:' log record"
    assert records[-1].levelno == logging.DEBUG
    assert not any(r.levelno == logging.INFO for r in records)


def test_changed_horizon_on_third_call_returns_to_info(caplog):
    """A call whose slot count/first/last slot start differs logs INFO again."""
    entries = [
        _entry("2026-03-16T12:30:01+11:00", 5, 0.10),
        _entry("2026-03-16T12:35:01+11:00", 5, 0.11),
    ]
    now = datetime(2026, 3, 16, 12, 33, 0, tzinfo=AEDT)

    changed_entries = entries + [
        _entry("2026-03-16T12:40:01+11:00", 5, 0.12),
    ]

    with caplog.at_level(logging.DEBUG):
        compute_hybrid_slot_schedule(now, entries, "Australia/Sydney")
        compute_hybrid_slot_schedule(now, entries, "Australia/Sydney")
        caplog.clear()
        compute_hybrid_slot_schedule(now, changed_entries, "Australia/Sydney")

    records = [
        r
        for r in caplog.records
        if "Hybrid slot schedule:" in r.getMessage()
    ]
    assert records, "expected a 'Hybrid slot schedule:' log record"
    assert records[-1].levelno == logging.INFO
