"""Config-level tests for the away reserve (docs/holiday-away/plan.md item 4).

Entity-behaviour tests (native_value, set_native_value, THRESHOLD_RANGES) live
in ``tests/test_number.py`` alongside every other number entity; the planner
floor lives in ``tests/engine/test_away_reserve_floor.py``; the hardware
reserve and mid-trip re-step live in ``tests/state/test_away_reserve.py``.
This file covers the const-level contract those all build on.
"""

from __future__ import annotations

from custom_components.localshift.const import (
    AWAY_RESERVE_MIN,
    BACKUP_RESERVE_MAX_VALID,
    CONF_AWAY_RESERVE,
    DEFAULT_AWAY_RESERVE,
    THRESHOLD_RANGES,
)


def test_away_reserve_default_within_range():
    assert AWAY_RESERVE_MIN <= DEFAULT_AWAY_RESERVE <= BACKUP_RESERVE_MAX_VALID


def test_away_reserve_min_is_ten():
    assert AWAY_RESERVE_MIN == 10


def test_away_reserve_default_is_thirty():
    assert DEFAULT_AWAY_RESERVE == 30


def test_away_reserve_threshold_range_respects_backup_reserve_max_valid():
    """The slider's own max must equal BACKUP_RESERVE_MAX_VALID (80), not a
    literal, so a firmware-constant change can't silently desync it."""
    spec = THRESHOLD_RANGES[CONF_AWAY_RESERVE]
    assert spec["min"] == AWAY_RESERVE_MIN
    assert spec["max"] == BACKUP_RESERVE_MAX_VALID
    assert spec["unit"] == "%"
