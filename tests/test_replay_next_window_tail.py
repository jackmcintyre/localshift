"""The replay harness checks its own headline claims (issue #1033).

``scripts/replay_next_window_tail.py`` exits non-zero when an instant breaks one
of them. These tests feed ``violations`` rows that break each claim in turn, so
a harness that prints a clean table over a broken result is itself a failure.
"""

from __future__ import annotations

import pytest

from scripts.replay_next_window_tail import _local_label, violations

_CLEAN = {
    "instant": "2026-10-06 14:35 spliced",
    "status": "applied",
    "tail_start": 13,
    "tail_start_before": None,
    "identical": False,
    "head_identical": True,
    "slot0_before": "hold",
    "slot0_after": "hold",
    "mode_before": "self_consumption",
    "mode_after": "self_consumption",
    "cost_before": -0.5,
    "cost_after": 0.1,
}
_UNTOUCHED = {
    **_CLEAN,
    "status": "gap_too_long",
    "tail_start": None,
    "identical": True,
    "cost_after": -0.5,
}


def test_clean_rows_pass():
    assert violations([_CLEAN, _UNTOUCHED]) == []


@pytest.mark.parametrize(
    ("base", "change", "expected"),
    [
        (_CLEAN, {"slot0_after": "charge_grid_boost"}, "slot-0 action"),
        (_CLEAN, {"mode_after": "boost_charging"}, "commanded mode"),
        (_CLEAN, {"head_identical": False}, "before the tail start"),
        (_CLEAN, {"tail_start": 0}, "tail starts at index 0"),
        (_CLEAN, {"tail_start_before": 13}, "'before' arm"),
        (_CLEAN, {"tail_start": None}, "status applied with tail start None"),
        (_UNTOUCHED, {"identical": False}, "but the plan changed"),
        (_UNTOUCHED, {"cost_after": -0.4}, "but the projected cost changed"),
        (_UNTOUCHED, {"tail_start": 13}, "status gap_too_long with tail start 13"),
    ],
)
def test_each_broken_claim_is_reported(base, change, expected):
    found = violations([_UNTOUCHED, {**base, **change}])
    assert any(expected in line for line in found), found


def test_instants_are_labelled_in_local_time():
    """File stamps are UTC+11 all month; daylight saving started on 10-04."""
    before_dst = {"input": {"test_time": "2026-10-02T14:35:40+11:00"}}
    after_dst = {"input": {"test_time": "2026-10-06T14:35:40+11:00"}}
    assert _local_label(before_dst, spliced=True) == "2026-10-02 13:35 spliced"
    assert _local_label(after_dst) == "2026-10-06 14:35"
