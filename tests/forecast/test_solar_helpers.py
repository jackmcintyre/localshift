"""Tests for solar.py helper methods extracted during complexity refactoring."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
import logging

import pytest

from custom_components.localshift.forecast.solar import (
    _normalize_slot_time,
    _process_forecast_entry,
    _get_period_estimate,
    _log_debug_forecast_entries,
    _log_debug_matched_entries,
    _parse_forecast_dt,
    get_solar_for_5min_slot,
    get_solar_for_30min_slot,
    get_solar_for_slot_by_interval,
    get_solar_for_15min_slot,
    sum_solar_before_target,
)


class TestParseForecastDt:
    """Tests for _parse_forecast_dt helper."""

    def test_none_returns_none(self):
        """None input returns None."""
        result = _parse_forecast_dt(None)
        assert result is None

    def test_valid_iso_string(self):
        """Parse valid ISO format string."""
        result = _parse_forecast_dt("2026-02-27T15:30:00+11:00")
        assert result is not None

    def test_invalid_string_returns_none(self):
        """Invalid string returns None."""
        result = _parse_forecast_dt("not a datetime")
        assert result is None

    def test_type_error_returns_none(self):
        """TypeError during parsing returns None."""
        with patch(
            "homeassistant.util.dt.parse_datetime", side_effect=TypeError("error")
        ):
            result = _parse_forecast_dt("2026-02-27T15:30:00+11:00")
        assert result is None


class TestLogDebugForecastEntries:
    """Tests for _log_debug_forecast_entries helper."""

    def test_empty_list_logs_nothing(self, caplog):
        """Empty forecast list does nothing."""
        with caplog.at_level(logging.DEBUG):
            _log_debug_forecast_entries([])
        assert len(caplog.records) == 0

    def test_logs_first_entry_keys(self, caplog):
        """Log first entry keys."""
        forecasts = [{"period_start": "2026-02-27T15:00:00+11:00", "pv_estimate": 2.0}]

        with caplog.at_level(logging.DEBUG):
            _log_debug_forecast_entries(forecasts)

        assert any("First entry keys" in r.message for r in caplog.records)

    def test_logs_multiple_entries(self, caplog):
        """Log multiple entries."""
        forecasts = [
            {"period_start": f"2026-02-27T15:{i:02d}:00+11:00", "pv_estimate": float(i)}
            for i in range(5)
        ]

        with caplog.at_level(logging.DEBUG):
            _log_debug_forecast_entries(forecasts)

        assert any("Entry 0" in r.message for r in caplog.records)
        assert any("Entry 1" in r.message for r in caplog.records)
        assert any("Entry 2" in r.message for r in caplog.records)


class TestLogDebugMatchedEntries:
    """Tests for _log_debug_matched_entries helper."""

    def test_morning_slot_no_logging(self, caplog):
        """Morning slot (before 14:00) should not log."""
        slot_start = datetime(2026, 2, 27, 10, 0, tzinfo=timezone(timedelta(hours=11)))
        matched = [{"contribution": 0.5}]
        total = 0.5

        with caplog.at_level(logging.DEBUG):
            _log_debug_matched_entries(slot_start, matched, total)

        assert len(caplog.records) == 0

    def test_evening_slot_no_logging(self, caplog):
        """Evening slot (after 18:00) should not log."""
        slot_start = datetime(2026, 2, 27, 19, 0, tzinfo=timezone(timedelta(hours=11)))
        matched = [{"contribution": 0.5}]
        total = 0.5

        with caplog.at_level(logging.DEBUG):
            _log_debug_matched_entries(slot_start, matched, total)

        assert len(caplog.records) == 0

    def test_afternoon_slot_logs_details(self, caplog):
        """Afternoon slot (14:00-18:00) should log."""
        slot_start = datetime(2026, 2, 27, 15, 0, tzinfo=timezone(timedelta(hours=11)))
        matched = [
            {
                "period_start": "2026-02-27 15:00",
                "pv_estimate": 2.0,
                "pv_estimate10": 1.0,
                "selected_value": 2.0,
                "overlap_pct": 25.0,
                "contribution": 0.5,
            }
        ]
        total = 0.5

        with caplog.at_level(logging.DEBUG):
            _log_debug_matched_entries(slot_start, matched, total)

        assert any("SOLAR_DEBUG_SLOT" in r.message for r in caplog.records)
        assert any("SOLAR_DEBUG_MATCH" in r.message for r in caplog.records)


class TestGetSolarFor5minSlot:
    """Tests for get_solar_for_5min_slot function."""

    def test_empty_forecast(self):
        """Empty forecast returns 0.0."""
        result = get_solar_for_5min_slot([], datetime(2026, 2, 27, 15, 0))
        assert result == 0.0

    def test_single_period_full_overlap(self):
        """5-min slot fully inside period."""
        forecasts = [{"period_start": "2026-02-27T15:00:00+11:00", "pv_estimate": 2.0}]
        slot = datetime(2026, 2, 27, 15, 5, tzinfo=timezone(timedelta(hours=11)))

        result = get_solar_for_5min_slot(forecasts, slot)

        expected = 2.0 * (5 * 60 / 3600.0)
        assert abs(result - expected) < 0.001

    def test_non_dict_entry_skipped(self):
        """Non-dict entries are skipped."""
        forecasts = [
            "not a dict",
            {"period_start": "2026-02-27T15:00:00+11:00", "pv_estimate": 2.0},
        ]
        slot = datetime(2026, 2, 27, 15, 0, tzinfo=timezone(timedelta(hours=11)))

        result = get_solar_for_5min_slot(forecasts, slot)

        expected = 2.0 * (5 * 60 / 3600.0)
        assert abs(result - expected) < 0.001

    def test_missing_period_start_skipped(self):
        """Entries missing period_start are skipped."""
        forecasts = [
            {"pv_estimate": 2.0},
            {"period_start": "2026-02-27T15:00:00+11:00", "pv_estimate": 2.0},
        ]
        slot = datetime(2026, 2, 27, 15, 0, tzinfo=timezone(timedelta(hours=11)))

        result = get_solar_for_5min_slot(forecasts, slot)

        expected = 2.0 * (5 * 60 / 3600.0)
        assert abs(result - expected) < 0.001

    def test_invalid_datetime_skipped(self):
        """Entries with invalid datetime are skipped."""
        forecasts = [
            {"period_start": "invalid", "pv_estimate": 2.0},
            {"period_start": "2026-02-27T15:00:00+11:00", "pv_estimate": 2.0},
        ]
        slot = datetime(2026, 2, 27, 15, 0, tzinfo=timezone(timedelta(hours=11)))

        result = get_solar_for_5min_slot(forecasts, slot)

        expected = 2.0 * (5 * 60 / 3600.0)
        assert abs(result - expected) < 0.001


class TestGetSolarFor30minSlot:
    """Tests for get_solar_for_30min_slot function."""

    def test_empty_forecast(self):
        """Empty forecast returns 0.0."""
        result = get_solar_for_30min_slot([], datetime(2026, 2, 27, 15, 0))
        assert result == 0.0

    def test_naive_datetime(self):
        """Naive datetime is converted to local."""
        forecasts = [{"period_start": "2026-02-27T15:00:00+11:00", "pv_estimate": 2.0}]
        slot = datetime(2026, 2, 27, 15, 0)

        result = get_solar_for_30min_slot(forecasts, slot)

        assert result >= 0.0

    def test_single_period_full_overlap(self):
        """30-min slot fully inside period."""
        forecasts = [{"period_start": "2026-02-27T15:00:00+11:00", "pv_estimate": 2.0}]
        slot = datetime(2026, 2, 27, 15, 0, tzinfo=timezone(timedelta(hours=11)))

        result = get_solar_for_30min_slot(forecasts, slot)

        expected = 2.0 * (30 * 60 / 3600.0)
        assert abs(result - expected) < 0.001

    def test_non_dict_entry_skipped(self):
        """Non-dict entries are skipped."""
        forecasts = [
            "not a dict",
            {"period_start": "2026-02-27T15:00:00+11:00", "pv_estimate": 2.0},
        ]
        slot = datetime(2026, 2, 27, 15, 0, tzinfo=timezone(timedelta(hours=11)))

        result = get_solar_for_30min_slot(forecasts, slot)

        expected = 2.0 * (30 * 60 / 3600.0)
        assert abs(result - expected) < 0.001

    def test_missing_period_start_skipped(self):
        """Entries missing period_start are skipped."""
        forecasts = [
            {"pv_estimate": 2.0},
            {"period_start": "2026-02-27T15:00:00+11:00", "pv_estimate": 2.0},
        ]
        slot = datetime(2026, 2, 27, 15, 0, tzinfo=timezone(timedelta(hours=11)))

        result = get_solar_for_30min_slot(forecasts, slot)

        expected = 2.0 * (30 * 60 / 3600.0)
        assert abs(result - expected) < 0.001

    def test_invalid_datetime_skipped(self):
        """Entries with invalid datetime are skipped."""
        forecasts = [
            {"period_start": "invalid", "pv_estimate": 2.0},
            {"period_start": "2026-02-27T15:00:00+11:00", "pv_estimate": 2.0},
        ]
        slot = datetime(2026, 2, 27, 15, 0, tzinfo=timezone(timedelta(hours=11)))

        result = get_solar_for_30min_slot(forecasts, slot)

        expected = 2.0 * (30 * 60 / 3600.0)
        assert abs(result - expected) < 0.001


class TestGetSolarForSlotByInterval:
    """Tests for get_solar_for_slot_by_interval function."""

    def test_5min_interval(self):
        """Dispatches to 5-min function."""
        forecasts = [{"period_start": "2026-02-27T15:00:00+11:00", "pv_estimate": 2.0}]
        slot = datetime(2026, 2, 27, 15, 0, tzinfo=timezone(timedelta(hours=11)))

        result = get_solar_for_slot_by_interval(forecasts, slot, 5)

        expected = 2.0 * (5 * 60 / 3600.0)
        assert abs(result - expected) < 0.001

    def test_15min_interval(self):
        """Dispatches to 15-min function."""
        forecasts = [{"period_start": "2026-02-27T15:00:00+11:00", "pv_estimate": 2.0}]
        slot = datetime(2026, 2, 27, 15, 0, tzinfo=timezone(timedelta(hours=11)))

        result = get_solar_for_slot_by_interval(forecasts, slot, 15)

        expected = 2.0 * (15 * 60 / 3600.0)
        assert abs(result - expected) < 0.001

    def test_30min_interval(self):
        """Dispatches to 30-min function."""
        forecasts = [{"period_start": "2026-02-27T15:00:00+11:00", "pv_estimate": 2.0}]
        slot = datetime(2026, 2, 27, 15, 0, tzinfo=timezone(timedelta(hours=11)))

        result = get_solar_for_slot_by_interval(forecasts, slot, 30)

        expected = 2.0 * (30 * 60 / 3600.0)
        assert abs(result - expected) < 0.001

    def test_unsupported_interval_defaults_to_15min(self, caplog):
        """Unsupported interval logs warning and defaults to 15-min."""
        forecasts = [{"period_start": "2026-02-27T15:00:00+11:00", "pv_estimate": 2.0}]
        slot = datetime(2026, 2, 27, 15, 0, tzinfo=timezone(timedelta(hours=11)))

        with caplog.at_level(logging.WARNING):
            result = get_solar_for_slot_by_interval(forecasts, slot, 60)

        assert any("Unsupported slot interval" in r.message for r in caplog.records)
        expected = 2.0 * (15 * 60 / 3600.0)
        assert abs(result - expected) < 0.001


class TestSumSolarBeforeTarget:
    """Tests for sum_solar_before_target function."""

    def test_empty_forecast(self):
        """Empty forecast returns 0.0."""
        now_dt = datetime(2026, 2, 27, 10, 0, tzinfo=timezone(timedelta(hours=11)))
        result = sum_solar_before_target([], now_dt, 15)
        assert result == 0.0

    def test_sums_future_periods_before_target(self):
        """Sum all periods from now until target hour."""
        forecasts = [
            {"period_start": "2026-02-27T10:00:00+11:00", "pv_estimate": 2.0},
            {"period_start": "2026-02-27T10:30:00+11:00", "pv_estimate": 2.5},
        ]
        now_dt = datetime(2026, 2, 27, 10, 0, tzinfo=timezone(timedelta(hours=11)))

        result = sum_solar_before_target(forecasts, now_dt, 12)

        assert result > 0

    def test_skips_periods_at_or_after_target(self):
        """Skip periods that start at or after target hour."""
        forecasts = [
            {"period_start": "2026-02-27T10:00:00+11:00", "pv_estimate": 2.0},
            {"period_start": "2026-02-27T15:00:00+11:00", "pv_estimate": 3.0},
        ]
        now_dt = datetime(2026, 2, 27, 10, 0, tzinfo=timezone(timedelta(hours=11)))

        result = sum_solar_before_target(forecasts, now_dt, 12)

        assert abs(result - 2.0 * 0.5) < 0.001

    def test_prorates_in_progress_period(self):
        """Prorate period that's currently in progress."""
        forecasts = [
            {"period_start": "2026-02-27T10:00:00+11:00", "pv_estimate": 2.0},
        ]
        now_dt = datetime(2026, 2, 27, 10, 15, tzinfo=timezone(timedelta(hours=11)))

        result = sum_solar_before_target(forecasts, now_dt, 15)

        expected = 2.0 * (15 * 60 / 3600.0)
        assert abs(result - expected) < 0.01

    def test_invalid_period_start_skipped(self):
        """Skip periods with invalid period_start."""
        forecasts = [
            {"period_start": "invalid", "pv_estimate": 2.0},
            {"period_start": "2026-02-27T10:00:00+11:00", "pv_estimate": 2.5},
        ]
        now_dt = datetime(2026, 2, 27, 10, 0, tzinfo=timezone(timedelta(hours=11)))

        result = sum_solar_before_target(forecasts, now_dt, 12)

        assert abs(result - 2.5 * 0.5) < 0.001


class TestNormalizeSlotTime:
    """Tests for _normalize_slot_time helper."""

    def test_naive_datetime_converted_to_local(self):
        """Convert naive datetime to timezone-aware local datetime."""
        slot_start = datetime(2026, 2, 27, 15, 30)

        result = _normalize_slot_time(slot_start)

        assert result.tzinfo is not None

    def test_timezone_aware_datetime_converted_to_local(self):
        """Convert timezone-aware datetime to local datetime."""
        slot_start = datetime(2026, 2, 27, 15, 30, tzinfo=timezone(timedelta(hours=11)))

        result = _normalize_slot_time(slot_start)

        assert result.tzinfo is not None

    def test_utc_datetime_converted_to_local(self):
        """Convert UTC datetime to local datetime."""
        slot_start = datetime(2026, 2, 27, 4, 30, tzinfo=timezone.utc)

        result = _normalize_slot_time(slot_start)

        assert result.tzinfo is not None


class TestGetPeriodEstimate:
    """Tests for _get_period_estimate helper."""

    def test_pv_estimate_primary(self):
        """Use pv_estimate as primary value."""
        entry = {"pv_estimate": 2.0, "pv_estimate10": 1.0}

        result = _get_period_estimate(entry)

        assert result == 2.0

    def test_estimate_fallback(self):
        """Fall back to estimate if pv_estimate missing."""
        entry = {"estimate": 1.8, "pv_estimate10": 1.0}

        result = _get_period_estimate(entry)

        assert result == 1.8

    def test_pv_estimate10_fallback(self):
        """Fall back to pv_estimate10 if others missing."""
        entry = {"pv_estimate10": 1.0}

        result = _get_period_estimate(entry)

        assert result == 1.0

    def test_estimate10_fallback(self):
        """Fall back to estimate10 if others missing."""
        entry = {"estimate10": 0.9}

        result = _get_period_estimate(entry)

        assert result == 0.9

    def test_zero_fallback(self):
        """Return 0.0 if no estimate fields present."""
        entry = {}

        result = _get_period_estimate(entry)

        assert result == 0.0

    def test_none_values_skipped(self):
        """Skip None values and use next available."""
        entry = {"pv_estimate": None, "estimate": None, "pv_estimate10": 1.0}

        result = _get_period_estimate(entry)

        assert result == 1.0


class TestProcessForecastEntry:
    """Tests for _process_forecast_entry helper."""

    def test_returns_none_for_non_dict(self):
        """Return None for non-dict entries."""
        slot_start = datetime(2026, 2, 27, 15, 0, tzinfo=timezone(timedelta(hours=11)))
        slot_end = slot_start + timedelta(minutes=15)
        period_duration = timedelta(minutes=30)

        result = _process_forecast_entry(
            "not a dict", slot_start, slot_end, period_duration
        )

        assert result is None

    def test_returns_none_for_missing_period_start(self):
        """Return None if period_start/start is missing."""
        slot_start = datetime(2026, 2, 27, 15, 0, tzinfo=timezone(timedelta(hours=11)))
        slot_end = slot_start + timedelta(minutes=15)
        period_duration = timedelta(minutes=30)
        entry = {"pv_estimate": 2.0}

        result = _process_forecast_entry(entry, slot_start, slot_end, period_duration)

        assert result is None

    def test_returns_none_for_invalid_datetime(self):
        """Return None if period_start is not parseable."""
        slot_start = datetime(2026, 2, 27, 15, 0, tzinfo=timezone(timedelta(hours=11)))
        slot_end = slot_start + timedelta(minutes=15)
        period_duration = timedelta(minutes=30)
        entry = {"period_start": "invalid", "pv_estimate": 2.0}

        result = _process_forecast_entry(entry, slot_start, slot_end, period_duration)

        assert result is None

    def test_returns_none_for_no_overlap(self):
        """Return None if period doesn't overlap slot."""
        slot_start = datetime(2026, 2, 27, 15, 0, tzinfo=timezone(timedelta(hours=11)))
        slot_end = slot_start + timedelta(minutes=15)
        period_duration = timedelta(minutes=30)
        entry = {
            "period_start": "2026-02-27T10:00:00+11:00",
            "pv_estimate": 2.0,
        }

        result = _process_forecast_entry(entry, slot_start, slot_end, period_duration)

        assert result is None

    def test_returns_contribution_for_overlap(self):
        """Return contribution dict for overlapping period."""
        slot_start = datetime(2026, 2, 27, 15, 0, tzinfo=timezone(timedelta(hours=11)))
        slot_end = slot_start + timedelta(minutes=15)
        period_duration = timedelta(minutes=30)
        entry = {
            "period_start": "2026-02-27T15:00:00+11:00",
            "pv_estimate": 2.0,
        }

        result = _process_forecast_entry(entry, slot_start, slot_end, period_duration)

        assert result is not None
        assert "contribution" in result
        assert "period_start" in result
        assert result["contribution"] > 0

    def test_uses_start_as_period_start_fallback(self):
        """Use 'start' field as fallback for 'period_start'."""
        slot_start = datetime(2026, 2, 27, 15, 0, tzinfo=timezone(timedelta(hours=11)))
        slot_end = slot_start + timedelta(minutes=15)
        period_duration = timedelta(minutes=30)
        entry = {
            "start": "2026-02-27T15:00:00+11:00",
            "pv_estimate": 2.0,
        }

        result = _process_forecast_entry(entry, slot_start, slot_end, period_duration)

        assert result is not None
        assert result["contribution"] > 0

    def test_calculates_correct_overlap_fraction(self):
        """Calculate correct overlap fraction for partial overlap."""
        slot_start = datetime(2026, 2, 27, 15, 20, tzinfo=timezone(timedelta(hours=11)))
        slot_end = slot_start + timedelta(minutes=15)
        period_duration = timedelta(minutes=30)
        entry = {
            "period_start": "2026-02-27T15:00:00+11:00",
            "pv_estimate": 2.0,
        }

        result = _process_forecast_entry(entry, slot_start, slot_end, period_duration)

        assert result is not None
        assert result["contribution"] == pytest.approx(
            2.0 * (10 * 60 / 3600.0), rel=0.01
        )

    def test_full_overlap_returns_full_contribution(self):
        """Full slot overlap returns full 15-min contribution."""
        slot_start = datetime(2026, 2, 27, 15, 0, tzinfo=timezone(timedelta(hours=11)))
        slot_end = slot_start + timedelta(minutes=15)
        period_duration = timedelta(minutes=30)
        entry = {
            "period_start": "2026-02-27T15:00:00+11:00",
            "pv_estimate": 2.0,
        }

        result = _process_forecast_entry(entry, slot_start, slot_end, period_duration)

        assert result is not None
        expected = 2.0 * (15 * 60 / 3600.0)
        assert result["contribution"] == pytest.approx(expected, rel=0.01)


class TestGetSolarFor15minSlotDebug:
    """Tests for get_solar_for_15min_slot with debug_log=True."""

    def test_debug_log_empty_forecast(self, caplog):
        """Empty forecast with debug_log returns 0.0."""
        with caplog.at_level(logging.DEBUG):
            result = get_solar_for_15min_slot(
                [], datetime(2026, 2, 27, 15, 0), debug_log=True
            )

        assert result == 0.0

    def test_debug_log_with_forecast(self, caplog):
        """Debug log logs forecast entries."""
        forecasts = [
            {"period_start": "2026-02-27T15:00:00+11:00", "pv_estimate": 2.0},
            {"period_start": "2026-02-27T15:30:00+11:00", "pv_estimate": 2.5},
        ]
        slot = datetime(2026, 2, 27, 15, 0, tzinfo=timezone(timedelta(hours=11)))

        with caplog.at_level(logging.DEBUG):
            result = get_solar_for_15min_slot(forecasts, slot, debug_log=True)

        assert result > 0
        assert any("SOLAR_DEBUG" in r.message for r in caplog.records)

    def test_debug_log_afternoon_slot(self, caplog):
        """Debug log for afternoon slot (14:00-18:00) logs matches."""
        forecasts = [
            {"period_start": "2026-02-27T15:00:00+11:00", "pv_estimate": 2.0},
        ]
        slot = datetime(2026, 2, 27, 15, 0, tzinfo=timezone(timedelta(hours=11)))

        with caplog.at_level(logging.DEBUG):
            result = get_solar_for_15min_slot(forecasts, slot, debug_log=True)

        assert result > 0
        assert any("SOLAR_DEBUG" in r.message for r in caplog.records)
