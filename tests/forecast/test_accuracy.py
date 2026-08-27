"""Tests for forecast accuracy tracking modules.

Issue #694: Fix past-due semantics for forecast accuracy comparisons.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from custom_components.localshift.forecast.accuracy import (
    ForecastAccuracyEngine,
)

# =============================================================================
# FORECAST ACCURACY ENGINE TESTS
# =============================================================================


class TestForecastAccuracyEngine:
    """Tests for ForecastAccuracyEngine class."""

    def test_init(self):
        """Test initialization."""
        engine = ForecastAccuracyEngine()
        assert engine is not None

    @pytest.mark.asyncio
    async def test_compute_forecast_accuracy_no_history(self):
        """Test computing accuracy with no forecast history."""
        from custom_components.localshift.coordinator import CoordinatorData

        engine = ForecastAccuracyEngine()
        data = CoordinatorData()
        data.forecast_history = []
        data.soc = 80.0
        data.general_price = 0.25
        data.feed_in_price = 0.08

        await engine.compute_forecast_accuracy(data)

        # Should set default values
        assert data.forecast_error_soc_15min == 0.0
        assert data.forecast_accuracy_soc_1h is None  # No data available

    @pytest.mark.asyncio
    async def test_compute_forecast_accuracy_sets_defaults(self):
        """Test that compute sets default values on CoordinatorData."""
        engine = ForecastAccuracyEngine()
        data = MagicMock()
        data.forecast_history = []
        data.soc = 75.0
        data.general_price = 0.30
        data.feed_in_price = 0.10

        await engine.compute_forecast_accuracy(data)

        # Verify defaults are set
        assert hasattr(data, "forecast_error_soc_15min")
        assert hasattr(data, "forecast_error_soc_1h")
        assert hasattr(data, "forecast_error_soc_4h")
        assert hasattr(data, "forecast_accuracy_soc_15min")
        assert hasattr(data, "forecast_accuracy_soc_1h")
        assert hasattr(data, "forecast_accuracy_soc_4h")
        assert hasattr(data, "forecast_error_buy_price_1h")
        assert hasattr(data, "forecast_error_sell_price_1h")


# =============================================================================
# BUG #694: PAST-DUE SEMANTICS TESTS
# =============================================================================


class TestForecastAccuracyEnginePastDue:
    """Bug #694: Comparisons must use past-due semantics, not a ±5-min window."""

    def _entry(
        self, target_dt: datetime, offset: int = 60, consumed: bool = False
    ) -> dict:
        entry: dict = {
            "offset_minutes": offset,
            "target_time": target_dt.isoformat(),
            "predicted_soc": 80.0,
            "predicted_buy_price": 0.25,
            "predicted_sell_price": 0.08,
        }
        if consumed:
            entry["consumed"] = True
        return entry

    def test_entry_12_min_past_due_matches(self):
        engine = ForecastAccuracyEngine()
        now_dt = datetime(2026, 3, 12, 20, 12, 0, tzinfo=timezone.utc)
        target_dt = datetime(2026, 3, 12, 20, 0, 0, tzinfo=timezone.utc)
        result = engine._process_single_entry(
            self._entry(target_dt), now_dt, 75.0, 0.25, 0.08
        )
        assert result is not None
        assert result["offset"] == 60

    def test_future_target_does_not_match(self):
        engine = ForecastAccuracyEngine()
        now_dt = datetime(2026, 3, 12, 12, 0, 0, tzinfo=timezone.utc)
        target_dt = now_dt + timedelta(minutes=5)
        result = engine._process_single_entry(
            self._entry(target_dt), now_dt, 75.0, 0.25, 0.08
        )
        assert result is None

    def test_past_due_beyond_30_min_does_not_match(self):
        engine = ForecastAccuracyEngine()
        now_dt = datetime(2026, 3, 12, 12, 0, 0, tzinfo=timezone.utc)
        target_dt = now_dt - timedelta(minutes=35)
        result = engine._process_single_entry(
            self._entry(target_dt), now_dt, 75.0, 0.25, 0.08
        )
        assert result is None

    def test_consumed_entry_is_skipped(self):
        engine = ForecastAccuracyEngine()
        now_dt = datetime(2026, 3, 12, 12, 0, 0, tzinfo=timezone.utc)
        target_dt = now_dt - timedelta(minutes=5)
        result = engine._process_single_entry(
            self._entry(target_dt, consumed=True), now_dt, 75.0, 0.25, 0.08
        )
        assert result is None

    def test_entry_marked_consumed_after_match(self):
        from custom_components.localshift.coordinator import CoordinatorData

        engine = ForecastAccuracyEngine()
        now_dt = datetime(2026, 3, 12, 12, 0, 0, tzinfo=timezone.utc)
        target_dt = now_dt - timedelta(minutes=5)
        entry = self._entry(target_dt, offset=60)
        data = CoordinatorData()
        data.forecast_history = [entry]
        data.soc = 75.0
        data.general_price = 0.25
        data.feed_in_price = 0.08
        engine._process_history_for_comparisons(data, now_dt)
        assert entry.get("consumed") is True

    def test_comparisons_made_increments_and_accuracy_set(self):
        from custom_components.localshift.coordinator import CoordinatorData

        engine = ForecastAccuracyEngine()
        now_dt = datetime(2026, 3, 12, 12, 0, 0, tzinfo=timezone.utc)
        target_dt = now_dt - timedelta(minutes=5)
        data = CoordinatorData()
        data.forecast_history = [self._entry(target_dt, offset=60)]
        data.soc = 75.0
        data.general_price = 0.25
        data.feed_in_price = 0.08
        comparisons = engine._process_history_for_comparisons(data, now_dt)
        engine._apply_comparison_results(data, comparisons)
        assert data.forecast_comparisons_made == 1
        assert data.forecast_accuracy_soc_1h == pytest.approx(95.0)

    def test_soc_error_calculated_correctly(self):
        engine = ForecastAccuracyEngine()
        now_dt = datetime(2026, 3, 12, 12, 0, 0, tzinfo=timezone.utc)
        target_dt = now_dt - timedelta(minutes=5)
        result = engine._process_single_entry(
            self._entry(target_dt, offset=60), now_dt, 75.0, 0.25, 0.08
        )
        assert result is not None
        assert result["soc_error"] == pytest.approx(5.0)

    def test_entry_at_exactly_30_min_past_due_matches(self):
        engine = ForecastAccuracyEngine()
        now_dt = datetime(2026, 3, 12, 12, 30, 0, tzinfo=timezone.utc)
        target_dt = datetime(2026, 3, 12, 12, 0, 0, tzinfo=timezone.utc)
        result = engine._process_single_entry(
            self._entry(target_dt), now_dt, 75.0, 0.25, 0.08
        )
        assert result is not None

    def test_entry_at_31_min_past_due_does_not_match(self):
        engine = ForecastAccuracyEngine()
        now_dt = datetime(2026, 3, 12, 12, 31, 0, tzinfo=timezone.utc)
        target_dt = datetime(2026, 3, 12, 12, 0, 0, tzinfo=timezone.utc)
        result = engine._process_single_entry(
            self._entry(target_dt), now_dt, 75.0, 0.25, 0.08
        )
        assert result is None


# =============================================================================
# COVERAGE: EDGE CASES IN ACCURACY ENGINE (Bug #694)
# =============================================================================


class TestForecastAccuracyEngineEdgeCases:
    """Coverage tests for edge cases in ForecastAccuracyEngine."""

    @pytest.mark.asyncio
    async def test_compute_exception_in_process_history(self):
        """Exception in _process_history_for_comparisons is caught and logged."""
        from unittest.mock import patch

        from custom_components.localshift.coordinator import CoordinatorData

        engine = ForecastAccuracyEngine()
        data = CoordinatorData()
        data.forecast_history = []
        data.soc = 80.0
        data.general_price = 0.25
        data.feed_in_price = 0.08

        with patch.object(
            engine,
            "_process_history_for_comparisons",
            side_effect=Exception("test error"),
        ):
            await engine.compute_forecast_accuracy(data)

    def test_ensure_accuracy_fields_creates_missing(self):
        """_ensure_accuracy_fields uses setattr when field is missing (line 102)."""
        from types import SimpleNamespace

        engine = ForecastAccuracyEngine()
        data = SimpleNamespace(
            forecast_history=[],
            forecast_last_comparison_time=None,
            forecast_comparisons_made=0,
        )
        engine._ensure_accuracy_fields(data)  # type: ignore[arg-type]

        assert data.forecast_error_soc_15min == 0.0
        assert data.forecast_accuracy_soc_1h is None

    def test_process_single_entry_no_offset_minutes(self):
        """Return None when entry lacks offset_minutes key (line 156)."""
        engine = ForecastAccuracyEngine()
        now_dt = datetime(2026, 3, 12, 12, 0, 0, tzinfo=timezone.utc)
        entry = {"target_time": "2026-03-12T11:55:00+00:00", "predicted_soc": 80.0}
        result = engine._process_single_entry(entry, now_dt, 80.0, 0.25, 0.08)
        assert result is None

    def test_process_single_entry_no_target_time(self):
        """Return None when entry has no target_time (line 160)."""
        engine = ForecastAccuracyEngine()
        now_dt = datetime(2026, 3, 12, 12, 0, 0, tzinfo=timezone.utc)
        entry = {"offset_minutes": 60, "predicted_soc": 80.0}
        result = engine._process_single_entry(entry, now_dt, 80.0, 0.25, 0.08)
        assert result is None

    def test_process_single_entry_invalid_datetime_string(self):
        """Return None when target_time cannot be parsed (lines 164-165)."""
        engine = ForecastAccuracyEngine()
        now_dt = datetime(2026, 3, 12, 12, 0, 0, tzinfo=timezone.utc)
        entry = {
            "offset_minutes": 60,
            "target_time": "NOT-A-DATE",
            "predicted_soc": 80.0,
        }
        result = engine._process_single_entry(entry, now_dt, 80.0, 0.25, 0.08)
        assert result is None

    def test_process_single_entry_naive_datetime_string(self):
        """Naive datetime string (no tz) hits tzinfo-is-None branch (line 168)."""
        from homeassistant.util import dt as dt_util

        engine = ForecastAccuracyEngine()
        now_dt = dt_util.now()
        naive_target = (now_dt - timedelta(minutes=5)).replace(tzinfo=None)
        entry = {
            "offset_minutes": 60,
            "target_time": naive_target.isoformat(),
            "predicted_soc": 80.0,
        }
        engine._process_single_entry(entry, now_dt, 80.0, 0.25, 0.08)

    def test_process_single_entry_predicted_soc_none(self):
        """Return None when predicted_soc is None (line 184)."""
        engine = ForecastAccuracyEngine()
        now_dt = datetime(2026, 3, 12, 12, 0, 0, tzinfo=timezone.utc)
        target_dt = now_dt - timedelta(minutes=5)
        entry = {
            "offset_minutes": 60,
            "target_time": target_dt.isoformat(),
            "predicted_soc": None,
        }
        result = engine._process_single_entry(entry, now_dt, 80.0, 0.25, 0.08)
        assert result is None

    def test_apply_offset_result_offset_15(self):
        """Apply result for 15-min offset sets 15min accuracy/error fields (lines 250-254)."""
        from custom_components.localshift.coordinator import CoordinatorData

        engine = ForecastAccuracyEngine()
        data = CoordinatorData()
        result = {
            "soc_error": 5.0,
            "predicted_buy": 0.25,
            "predicted_sell": 0.08,
            "actual_buy": 0.25,
            "actual_sell": 0.08,
        }
        applied = engine._apply_offset_result(data, result, 15)
        assert applied is True
        assert data.forecast_error_soc_15min == pytest.approx(5.0)
        assert data.forecast_accuracy_soc_15min == pytest.approx(95.0)

    def test_apply_offset_result_offset_240(self):
        """Apply result for 240-min offset sets 4h accuracy/error fields (lines 265-269)."""
        from custom_components.localshift.coordinator import CoordinatorData

        engine = ForecastAccuracyEngine()
        data = CoordinatorData()
        result = {
            "soc_error": 3.0,
            "predicted_buy": 0.25,
            "predicted_sell": 0.08,
            "actual_buy": 0.25,
            "actual_sell": 0.08,
        }
        applied = engine._apply_offset_result(data, result, 240)
        assert applied is True
        assert data.forecast_error_soc_4h == pytest.approx(3.0)
        assert data.forecast_accuracy_soc_4h == pytest.approx(97.0)
