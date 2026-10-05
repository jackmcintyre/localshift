"""Tesla tariff -> periods where export cannot physically happen (Issue #1097)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

from custom_components.localshift.utils.export_availability import (
    export_available_at,
    export_blocked_periods,
)

AEDT = timezone(timedelta(hours=11))


def _at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=AEDT)


# The sell tariff calendar.get_events returned live on 2026-10-05.
LIVE_EVENTS = [
    {
        "start": "2026-10-05T04:00:00+11:00",
        "end": "2026-10-05T21:00:00+11:00",
        "summary": "On peak: 0.50/kWh",
        "description": "Season: Summer\nPeriod: On peak\nPrice: 0.50/kWh",
    },
    {
        "start": "2026-10-05T21:00:00+11:00",
        "end": "2026-10-06T04:00:00+11:00",
        "summary": "Off peak: 0.05/kWh",
        "description": "Season: Summer\nPeriod: Off peak\nPrice: 0.05/kWh",
    },
    {
        "start": "2026-10-06T04:00:00+11:00",
        "end": "2026-10-06T21:00:00+11:00",
        "summary": "On peak: 0.50/kWh",
        "description": "Season: Summer\nPeriod: On peak\nPrice: 0.50/kWh",
    },
    {
        "start": "2026-10-06T21:00:00+11:00",
        "end": "2026-10-07T04:00:00+11:00",
        "summary": "Off peak: 0.05/kWh",
        "description": "Season: Summer\nPeriod: Off peak\nPrice: 0.05/kWh",
    },
]


def test_off_peak_periods_are_blocked():
    assert export_blocked_periods(LIVE_EVENTS) == [
        (_at(5, 21), _at(6, 4)),
        (_at(6, 21), _at(7, 4)),
    ]


def test_three_tier_tariff_blocks_everything_below_the_top_rate():
    events = [
        {"start": "2026-10-05T00:00:00+11:00", "end": "2026-10-05T07:00:00+11:00",
         "summary": "Off peak: 0.05/kWh"},
        {"start": "2026-10-05T07:00:00+11:00", "end": "2026-10-05T16:00:00+11:00",
         "summary": "Partial peak: 0.20/kWh"},
        {"start": "2026-10-05T16:00:00+11:00", "end": "2026-10-05T21:00:00+11:00",
         "summary": "On peak: 0.50/kWh"},
    ]  # fmt: skip

    assert export_blocked_periods(events) == [
        (_at(5, 0), _at(5, 7)),
        (_at(5, 7), _at(5, 16)),
    ]


def test_single_rate_tariff_blocks_nothing():
    events = [
        {"start": "2026-10-05T00:00:00+11:00", "end": "2026-10-06T00:00:00+11:00",
         "summary": "All day: 0.30/kWh"},
        {"start": "2026-10-06T00:00:00+11:00", "end": "2026-10-07T00:00:00+11:00",
         "summary": "All day: 0.30/kWh"},
    ]  # fmt: skip

    assert export_blocked_periods(events) == []


def test_no_events_blocks_nothing():
    assert export_blocked_periods([]) == []


def test_unreadable_price_fails_open():
    """One event we cannot price means we cannot place the top rate: block nothing."""
    events = [*LIVE_EVENTS, {**LIVE_EVENTS[1], "summary": "Off peak"}]

    assert export_blocked_periods(events) == []


def test_unreadable_time_fails_open():
    events = [*LIVE_EVENTS, {**LIVE_EVENTS[1], "start": "not a time"}]

    assert export_blocked_periods(events) == []


def test_malformed_event_fails_open():
    assert export_blocked_periods([*LIVE_EVENTS, "not an event"]) == []


def test_naive_event_time_fails_open():
    events = [*LIVE_EVENTS, {**LIVE_EVENTS[1], "start": "2026-10-05T21:00:00"}]

    assert export_blocked_periods(events) == []


def test_available_outside_blocked_periods():
    periods = export_blocked_periods(LIVE_EVENTS)

    assert export_available_at(_at(5, 20, 59), periods, None) is True
    assert export_available_at(_at(6, 4), periods, None) is True


def test_unavailable_inside_blocked_period():
    """The period start is inclusive: 21:00:00 is already off-peak."""
    periods = export_blocked_periods(LIVE_EVENTS)

    assert export_available_at(_at(5, 21), periods, None) is False
    assert export_available_at(_at(6, 3, 55), periods, None) is False


def test_blocked_period_is_matched_across_timezones():
    periods = export_blocked_periods(LIVE_EVENTS)
    ten_pm_sydney_in_utc = datetime(2026, 10, 5, 11, 0, tzinfo=UTC)

    assert export_available_at(ten_pm_sydney_in_utc, periods, None) is False


def test_holdoff_suppresses_until_it_expires():
    suppressed_until = _at(5, 15)

    assert export_available_at(_at(5, 14, 59), [], suppressed_until) is False
    assert export_available_at(_at(5, 15), [], suppressed_until) is True


def test_no_restrictions_means_available():
    assert export_available_at(_at(5, 22), None, None) is True
    assert export_available_at(_at(5, 22), [], None) is True


def test_naive_instant_is_not_restricted():
    periods = export_blocked_periods(LIVE_EVENTS)

    assert export_available_at(datetime(2026, 10, 5, 22, 0), periods, _at(6, 0)) is True
