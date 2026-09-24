"""Tests for the away-history helpers in utils/away.py.

Docs: docs/holiday-away/plan.md "What to build" item 3 — keep away hours out
of learning. These cover the pure interval math (`_iter_quarter_hours`,
`away_utc_hour_starts`, `away_local_hour_keys`) and the recorder-backed
fetch (`fetch_away_intervals_sync`, `async_get_away_intervals`).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from custom_components.localshift.const import CONF_AWAY_ENTITY
from custom_components.localshift.utils.away import (
    _iter_quarter_hours,
    async_get_away_intervals,
    away_full_utc_hour_starts,
    away_local_hour_keys,
    away_utc_hour_starts,
    fetch_away_intervals_sync,
    get_away_entity_id,
)

SYDNEY = ZoneInfo("Australia/Sydney")
ADELAIDE = ZoneInfo("Australia/Adelaide")


@dataclass
class FakeState:
    """Minimal stand-in for HA's ``State`` — just what the walk reads."""

    state: str
    last_changed: datetime


# =============================================================================
# A. away_utc_hour_starts / _iter_quarter_hours
# =============================================================================


class TestAwayUtcHourStarts:
    """Tests for away_utc_hour_starts (consumption-stats masking)."""

    def test_partial_hours_both_included(self):
        """10:50-11:05 touches the 10:00 and 11:00 hour starts."""
        start = datetime(2026, 3, 1, 10, 50, tzinfo=UTC)
        end = datetime(2026, 3, 1, 11, 5, tzinfo=UTC)

        result = away_utc_hour_starts([(start, end)])

        assert result == {
            datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
            datetime(2026, 3, 1, 11, 0, tzinfo=UTC),
        }

    def test_exact_hour_is_end_exclusive(self):
        """10:00-11:00 touches only the 10:00 hour; the end hour is exclusive."""
        start = datetime(2026, 3, 1, 10, 0, tzinfo=UTC)
        end = datetime(2026, 3, 1, 11, 0, tzinfo=UTC)

        result = away_utc_hour_starts([(start, end)])

        assert result == {datetime(2026, 3, 1, 10, 0, tzinfo=UTC)}

    def test_empty_intervals_is_empty(self):
        """No intervals means no masked hours."""
        assert away_utc_hour_starts([]) == frozenset()

    def test_inverted_interval_is_skipped(self):
        """An end before start yields nothing rather than looping forever."""
        start = datetime(2026, 3, 1, 11, 0, tzinfo=UTC)
        end = datetime(2026, 3, 1, 10, 0, tzinfo=UTC)

        assert away_utc_hour_starts([(start, end)]) == frozenset()

    def test_equal_start_and_end_is_skipped(self):
        """A zero-length interval yields nothing."""
        t = datetime(2026, 3, 1, 10, 0, tzinfo=UTC)

        assert away_utc_hour_starts([(t, t)]) == frozenset()

    def test_overlapping_intervals_merge(self):
        """Overlapping intervals union into one set of hour starts."""
        first = (
            datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
            datetime(2026, 3, 1, 11, 0, tzinfo=UTC),
        )
        second = (
            datetime(2026, 3, 1, 10, 30, tzinfo=UTC),
            datetime(2026, 3, 1, 12, 0, tzinfo=UTC),
        )

        result = away_utc_hour_starts([first, second])

        assert result == {
            datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
            datetime(2026, 3, 1, 11, 0, tzinfo=UTC),
        }

    def test_result_type_is_frozenset(self):
        """The return value is a frozenset, per the interface contract."""
        result = away_utc_hour_starts([])
        assert isinstance(result, frozenset)


class TestIterQuarterHours:
    """Direct tests for the shared quarter-hour walk."""

    def test_steps_every_15_minutes(self):
        """A 1-hour interval yields exactly 4 quarters."""
        start = datetime(2026, 3, 1, 10, 0, tzinfo=UTC)
        end = datetime(2026, 3, 1, 11, 0, tzinfo=UTC)

        quarters = list(_iter_quarter_hours([(start, end)]))

        assert quarters == [
            datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
            datetime(2026, 3, 1, 10, 15, tzinfo=UTC),
            datetime(2026, 3, 1, 10, 30, tzinfo=UTC),
            datetime(2026, 3, 1, 10, 45, tzinfo=UTC),
        ]


# =============================================================================
# A2. away_full_utc_hour_starts
# =============================================================================


class TestAwayFullUtcHourStarts:
    """Tests for away_full_utc_hour_starts (away-profile masking, plan item 2).

    Strict, unlike away_utc_hour_starts: an hour counts only when the away
    interval covers it entirely, so departure/return hours never contaminate
    the away-mode profile mean.
    """

    def test_exact_boundaries_include_every_full_hour(self):
        """U1: 08:00-11:00 gives {08, 09, 10} — the interval covers each
        wholly, and the end hour (11:00-12:00) isn't started."""
        start = datetime(2026, 3, 1, 8, 0, tzinfo=UTC)
        end = datetime(2026, 3, 1, 11, 0, tzinfo=UTC)

        result = away_full_utc_hour_starts([(start, end)])

        assert result == {
            datetime(2026, 3, 1, 8, 0, tzinfo=UTC),
            datetime(2026, 3, 1, 9, 0, tzinfo=UTC),
            datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
        }

    def test_partial_departure_and_return_hours_excluded(self):
        """U2: 08:10-10:50 gives only {09} — the 08:00 and 10:00 hours are
        each only partly covered."""
        start = datetime(2026, 3, 1, 8, 10, tzinfo=UTC)
        end = datetime(2026, 3, 1, 10, 50, tzinfo=UTC)

        result = away_full_utc_hour_starts([(start, end)])

        assert result == {datetime(2026, 3, 1, 9, 0, tzinfo=UTC)}

    def test_sub_hour_interval_is_empty(self):
        """U3: an interval shorter than one hour never fully covers any hour."""
        start = datetime(2026, 3, 1, 8, 10, tzinfo=UTC)
        end = datetime(2026, 3, 1, 8, 40, tzinfo=UTC)

        assert away_full_utc_hour_starts([(start, end)]) == frozenset()

    def test_exact_boundary_end_is_exclusive(self):
        """U4: 08:00-09:00 gives {08} only — the hour *starting* at the end
        boundary (09:00-10:00) is not included."""
        start = datetime(2026, 3, 1, 8, 0, tzinfo=UTC)
        end = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)

        result = away_full_utc_hour_starts([(start, end)])

        assert result == {datetime(2026, 3, 1, 8, 0, tzinfo=UTC)}
        assert datetime(2026, 3, 1, 9, 0, tzinfo=UTC) not in result

    def test_non_utc_aware_input_is_converted(self):
        """U5a: a non-UTC aware interval is converted to UTC before walking."""
        start = datetime(2026, 3, 1, 18, 0, tzinfo=SYDNEY)  # 07:00 UTC (AEDT, +11)
        end = datetime(2026, 3, 1, 21, 0, tzinfo=SYDNEY)  # 10:00 UTC

        result = away_full_utc_hour_starts([(start, end)])

        assert result == {
            datetime(2026, 3, 1, 7, 0, tzinfo=UTC),
            datetime(2026, 3, 1, 8, 0, tzinfo=UTC),
            datetime(2026, 3, 1, 9, 0, tzinfo=UTC),
        }

    def test_empty_intervals_is_empty(self):
        """U5b: no intervals means no fully-covered hours."""
        assert away_full_utc_hour_starts([]) == frozenset()

    def test_reversed_interval_is_empty(self):
        """U5c: an end before start yields nothing."""
        start = datetime(2026, 3, 1, 11, 0, tzinfo=UTC)
        end = datetime(2026, 3, 1, 8, 0, tzinfo=UTC)

        assert away_full_utc_hour_starts([(start, end)]) == frozenset()

    def test_result_type_is_frozenset(self):
        """The return value is a frozenset, per the interface contract."""
        assert isinstance(away_full_utc_hour_starts([]), frozenset)


# =============================================================================
# B. away_local_hour_keys
# =============================================================================


class TestAwayLocalHourKeys:
    """Tests for away_local_hour_keys (weather-correlation masking)."""

    def test_normal_day_sydney(self):
        """A plain winter (non-DST-boundary) Sydney interval, no surprises."""
        start = datetime(2026, 6, 10, 10, 30, tzinfo=SYDNEY)
        end = datetime(2026, 6, 10, 11, 15, tzinfo=SYDNEY)

        result = away_local_hour_keys([(start, end)], SYDNEY)

        assert result == {("2026-06-10", 10), ("2026-06-10", 11)}

    def test_dst_end_repeated_hour_is_one_key(self):
        """Sydney DST end (5 Apr 2026): the doubled 02:00 hour is one key.

        01:30-03:30 local spans the AEDT->AEST fallback at 03:00 AEDT / 02:00
        AEST, so wall-clock hour 2 occurs twice (once each offset) but must
        still collapse to a single (date, hour) key.
        """
        start = datetime(2026, 4, 5, 1, 30, tzinfo=SYDNEY)
        end = datetime(2026, 4, 5, 3, 30, tzinfo=SYDNEY)

        result = away_local_hour_keys([(start, end)], SYDNEY)

        assert ("2026-04-05", 2) in result
        assert result == {
            ("2026-04-05", 1),
            ("2026-04-05", 2),
            ("2026-04-05", 3),
        }

    def test_dst_start_skipped_hour_absent(self):
        """Sydney DST start (4 Oct 2026): wall-clock hour 2 never happens.

        01:00-04:00 local spans the AEST->AEDT spring-forward at 02:00 AEST
        (jumping straight to 03:00 AEDT), so no quarter ever falls in local
        hour 2.
        """
        start = datetime(2026, 10, 4, 1, 0, tzinfo=SYDNEY)
        end = datetime(2026, 10, 4, 4, 0, tzinfo=SYDNEY)

        result = away_local_hour_keys([(start, end)], SYDNEY)

        assert ("2026-10-04", 2) not in result
        assert result == {("2026-10-04", 1), ("2026-10-04", 3)}

    def test_adelaide_half_hour_offset_splits_one_utc_hour(self):
        """A half-hour-offset zone (Adelaide) can put one UTC hour in two local hours."""
        start = datetime(2026, 7, 15, 14, 0, tzinfo=UTC)
        end = datetime(2026, 7, 15, 15, 0, tzinfo=UTC)

        result = away_local_hour_keys([(start, end)], ADELAIDE)

        assert result == {("2026-07-15", 23), ("2026-07-16", 0)}

    def test_empty_intervals_is_empty(self):
        """No intervals means no masked local keys."""
        assert away_local_hour_keys([], SYDNEY) == frozenset()

    def test_result_type_is_frozenset(self):
        """The return value is a frozenset, per the interface contract."""
        assert isinstance(away_local_hour_keys([], SYDNEY), frozenset)


# =============================================================================
# C. fetch_away_intervals_sync
# =============================================================================


def _patch_history(states_by_entity):
    return patch(
        "homeassistant.components.recorder.history.state_changes_during_period",
        return_value=states_by_entity,
    )


class TestFetchAwayIntervalsSync:
    """Tests for fetch_away_intervals_sync."""

    def test_off_on_off_on_yields_two_intervals(self):
        """off -> on -> off -> on (still open) yields [(t1,t2), (t3,end)]."""
        hass = MagicMock()
        start = datetime(2026, 5, 1, 0, 0, tzinfo=UTC)
        end = datetime(2026, 5, 2, 0, 0, tzinfo=UTC)
        t1 = start + timedelta(hours=1)
        t2 = start + timedelta(hours=2)
        t3 = start + timedelta(hours=3)
        states = [
            FakeState("off", start),
            FakeState("on", t1),
            FakeState("off", t2),
            FakeState("on", t3),
        ]

        with _patch_history({"input_boolean.away": states}):
            result = fetch_away_intervals_sync(
                hass, "input_boolean.away", start, end
            )

        assert result == [(t1, t2), (t3, end)]

    def test_on_before_window_is_clamped_to_start(self):
        """A start state already "on" before the window clamps to start."""
        hass = MagicMock()
        start = datetime(2026, 5, 1, 0, 0, tzinfo=UTC)
        end = datetime(2026, 5, 2, 0, 0, tzinfo=UTC)
        states = [FakeState("on", start - timedelta(hours=5))]

        with _patch_history({"input_boolean.away": states}):
            result = fetch_away_intervals_sync(
                hass, "input_boolean.away", start, end
            )

        assert result == [(start, end)]

    def test_unavailable_between_on_states_splits_interval(self):
        """unavailable between two "on" states closes and reopens the interval."""
        hass = MagicMock()
        start = datetime(2026, 5, 1, 0, 0, tzinfo=UTC)
        end = datetime(2026, 5, 2, 0, 0, tzinfo=UTC)
        t1 = start + timedelta(hours=1)
        t2 = start + timedelta(hours=2)
        t3 = start + timedelta(hours=3)
        states = [
            FakeState("on", t1),
            FakeState("unavailable", t2),
            FakeState("on", t3),
        ]

        with _patch_history({"input_boolean.away": states}):
            result = fetch_away_intervals_sync(
                hass, "input_boolean.away", start, end
            )

        assert result == [(t1, t2), (t3, end)]

    def test_unknown_also_closes_the_interval(self):
        """"unknown" is not "on" either, so it closes an open interval."""
        hass = MagicMock()
        start = datetime(2026, 5, 1, 0, 0, tzinfo=UTC)
        end = datetime(2026, 5, 2, 0, 0, tzinfo=UTC)
        t1 = start + timedelta(hours=1)
        t2 = start + timedelta(hours=2)
        states = [FakeState("on", t1), FakeState("unknown", t2)]

        with _patch_history({"input_boolean.away": states}):
            result = fetch_away_intervals_sync(
                hass, "input_boolean.away", start, end
            )

        assert result == [(t1, t2)]

    def test_no_states_returns_empty(self):
        """An empty state list returns no intervals."""
        hass = MagicMock()
        start = datetime(2026, 5, 1, 0, 0, tzinfo=UTC)
        end = datetime(2026, 5, 2, 0, 0, tzinfo=UTC)

        with _patch_history({"input_boolean.away": []}):
            result = fetch_away_intervals_sync(
                hass, "input_boolean.away", start, end
            )

        assert result == []

    def test_missing_entity_key_returns_empty(self):
        """The entity missing entirely from the result returns no intervals."""
        hass = MagicMock()
        start = datetime(2026, 5, 1, 0, 0, tzinfo=UTC)
        end = datetime(2026, 5, 2, 0, 0, tzinfo=UTC)

        with _patch_history({}):
            result = fetch_away_intervals_sync(
                hass, "input_boolean.away", start, end
            )

        assert result == []

    def test_recorder_exception_returns_empty_and_warns(self, caplog):
        """A recorder failure returns [] and logs a warning, never raises."""
        hass = MagicMock()
        start = datetime(2026, 5, 1, 0, 0, tzinfo=UTC)
        end = datetime(2026, 5, 2, 0, 0, tzinfo=UTC)

        with (
            patch(
                "homeassistant.components.recorder.history.state_changes_during_period",
                side_effect=Exception("boom"),
            ),
            caplog.at_level(logging.WARNING),
        ):
            result = fetch_away_intervals_sync(
                hass, "input_boolean.away", start, end
            )

        assert result == []
        assert any(
            record.levelno == logging.WARNING for record in caplog.records
        )


# =============================================================================
# D. async_get_away_intervals
# =============================================================================


class TestAsyncGetAwayIntervals:
    """Tests for the async recorder-executor wrapper."""

    @pytest.mark.asyncio
    async def test_goes_through_recorder_executor(self):
        """The async wrapper delegates to the recorder's executor job."""
        hass = MagicMock()
        start = datetime(2026, 5, 1, 0, 0, tzinfo=UTC)
        end = datetime(2026, 5, 2, 0, 0, tzinfo=UTC)
        expected = [(start, end)]

        with patch(
            "homeassistant.components.recorder.get_instance"
        ) as mock_get_instance:
            mock_recorder = MagicMock()
            mock_recorder.async_add_executor_job = AsyncMock(return_value=expected)
            mock_get_instance.return_value = mock_recorder

            result = await async_get_away_intervals(
                hass, "input_boolean.away", start, end
            )

        assert result == expected
        mock_get_instance.assert_called_once_with(hass)
        mock_recorder.async_add_executor_job.assert_called_once_with(
            fetch_away_intervals_sync, hass, "input_boolean.away", start, end
        )


# =============================================================================
# E. get_away_entity_id hardening
# =============================================================================


class TestGetAwayEntityIdHardening:
    """A non-str option (e.g. an unset MagicMock) reports unset, not truthy."""

    def test_bare_magicmock_entry_reports_none(self):
        """An entry.options built as a bare MagicMock() is not a real option."""
        entry = MagicMock()  # entry.options is itself an unconfigured MagicMock

        assert get_away_entity_id(entry) is None

    def test_non_string_option_value_reports_none(self):
        """A non-str value under the option key is treated as unset."""
        entry = MagicMock()
        entry.options = {CONF_AWAY_ENTITY: MagicMock()}

        assert get_away_entity_id(entry) is None
