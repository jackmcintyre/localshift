"""Capturing one day at several hours (scripts/export_replay_days.py; #1111).

The flap test needs the same day captured at 09:00, 11:00, 13:00 and 14:30.
These cover the pieces that decide *when* a capture is taken and *what the file
is called*; nothing here talks to Home Assistant.
"""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

import pytest

from scripts import export_replay_days as exporter

SYDNEY = ZoneInfo("Australia/Sydney")


def _now(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp).replace(tzinfo=SYDNEY)


# ---------------------------------------------------------------------------
# Clock parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("9", time(9, 0)),
        ("09", time(9, 0)),
        ("09:00", time(9, 0)),
        ("14:30", time(14, 30)),
        ("0:05", time(0, 5)),
    ],
)
def test_parse_clock(text: str, expected: time) -> None:
    assert exporter.parse_clock(text) == expected


@pytest.mark.parametrize("text", ["", "25", "9:60", "nine", "09:00:00", "-1"])
def test_parse_clock_rejects_what_is_not_a_time(text: str) -> None:
    with pytest.raises(ValueError):
        exporter.parse_clock(text)


# ---------------------------------------------------------------------------
# Which moments get captured
# ---------------------------------------------------------------------------


def test_default_is_the_last_n_days_newest_first() -> None:
    """The behaviour before --date existed: yesterday back, one clock."""
    times = exporter.decision_times(
        _now("2026-09-08T16:20:00"), days=3, dates=[], clocks=[time(9, 0)]
    )

    assert [t.isoformat() for t in times] == [
        "2026-09-07T09:00:00+10:00",
        "2026-09-06T09:00:00+10:00",
        "2026-09-05T09:00:00+10:00",
    ]


def test_named_dates_replace_the_day_count() -> None:
    times = exporter.decision_times(
        _now("2026-10-09T12:00:00"),
        days=10,
        dates=["2026-09-07"],
        clocks=[time(9, 0)],
    )

    assert [t.isoformat() for t in times] == ["2026-09-07T09:00:00+10:00"]


def test_one_day_at_several_clocks_in_time_order() -> None:
    times = exporter.decision_times(
        _now("2026-10-09T12:00:00"),
        days=10,
        dates=["2026-09-07"],
        clocks=[time(13, 0), time(9, 0), time(14, 30), time(11, 0)],
    )

    assert [t.strftime("%H:%M") for t in times] == ["09:00", "11:00", "13:00", "14:30"]
    assert {t.date().isoformat() for t in times} == {"2026-09-07"}


def test_clock_is_sydney_wall_time_across_daylight_saving() -> None:
    """09:00 means 09:00 on the kitchen clock: +10:00 in September, +11:00 after
    the first Sunday of October. A fixed +10:00 offset captures an hour late."""
    times = exporter.decision_times(
        _now("2026-10-09T12:00:00"),
        days=10,
        dates=["2026-09-07", "2026-10-06"],
        clocks=[time(9, 0)],
    )

    assert [t.isoformat() for t in times] == [
        "2026-09-07T09:00:00+10:00",
        "2026-10-06T09:00:00+11:00",
    ]


def test_bad_date_is_rejected() -> None:
    with pytest.raises(ValueError):
        exporter.decision_times(
            _now("2026-10-09T12:00:00"),
            days=1,
            dates=["07/09/2026"],
            clocks=[time(9, 0)],
        )


# ---------------------------------------------------------------------------
# File names
# ---------------------------------------------------------------------------


def test_file_is_named_by_day_by_default() -> None:
    at = datetime.fromisoformat("2026-09-07T09:00:00+10:00")

    assert exporter.capture_filename(at, suffix_time=False) == "2026-09-07.json"


@pytest.mark.parametrize(
    ("stamp", "name"),
    [
        ("2026-09-07T09:00:00+10:00", "2026-09-07T0900.json"),
        ("2026-09-07T14:30:00+10:00", "2026-09-07T1430.json"),
    ],
)
def test_time_suffix_keeps_same_day_captures_apart(stamp: str, name: str) -> None:
    assert (
        exporter.capture_filename(datetime.fromisoformat(stamp), suffix_time=True)
        == name
    )


def test_suffixed_names_sort_in_time_order() -> None:
    stamps = ["2026-09-07T14:30:00+10:00", "2026-09-07T09:00:00+10:00"]
    names = sorted(
        exporter.capture_filename(datetime.fromisoformat(s), suffix_time=True)
        for s in stamps
    )

    assert names == ["2026-09-07T0900.json", "2026-09-07T1430.json"]


# ---------------------------------------------------------------------------
# Argument handling
# ---------------------------------------------------------------------------


def test_args_default_to_one_capture_a_day_at_nine() -> None:
    args = exporter.parse_args([])

    assert exporter.clocks_from_args(args) == [time(9, 0)]
    assert exporter.wants_time_suffix(args) is False
    assert args.date == []


def test_hour_still_works() -> None:
    args = exporter.parse_args(["--hour", "13"])

    assert exporter.clocks_from_args(args) == [time(13, 0)]


def test_time_overrides_hour_and_takes_a_list() -> None:
    args = exporter.parse_args([
        "--date",
        "2026-09-07",
        "--time",
        "09:00,11:00,13:00,14:30",
    ])

    assert exporter.clocks_from_args(args) == [
        time(9, 0),
        time(11, 0),
        time(13, 0),
        time(14, 30),
    ]
    assert args.date == ["2026-09-07"]


def test_several_times_force_the_suffix_so_captures_cannot_overwrite() -> None:
    several = exporter.parse_args(["--time", "09:00,11:00"])
    one = exporter.parse_args(["--time", "11:00"])
    asked = exporter.parse_args(["--time", "11:00", "--suffix-time"])

    assert exporter.wants_time_suffix(several) is True
    assert exporter.wants_time_suffix(one) is False
    assert exporter.wants_time_suffix(asked) is True
