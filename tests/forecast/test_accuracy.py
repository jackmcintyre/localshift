"""Tests for forecast accuracy tracking modules.

Issue #270: Extended forecast accuracy with bias detection.
Issue #694: Fix past-due semantics and None defaults for extended metrics.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from custom_components.localshift.forecast.accuracy import (
    ExtendedAccuracyMetrics,
    ExtendedForecastAccuracyEngine,
    ForecastAccuracyEngine,
)

# =============================================================================
# EXTENDED ACCURACY METRICS TESTS
# =============================================================================


class TestExtendedAccuracyMetrics:
    """Tests for ExtendedAccuracyMetrics dataclass."""

    def test_default_values(self):
        """Test default values are set correctly."""
        metrics = ExtendedAccuracyMetrics()
        assert metrics.accuracy_24h is None
        assert metrics.accuracy_7d is None
        assert metrics.accuracy_30d is None
        assert metrics.bias == 0.0
        assert metrics.mape == 0.0
        assert metrics.sample_count == 0
        assert metrics.last_updated is None

    def test_custom_values(self):
        """Test custom values are set correctly."""
        now = datetime(2026, 2, 26, 8, 0, 0)
        metrics = ExtendedAccuracyMetrics(
            accuracy_24h=95.5,
            accuracy_7d=93.2,
            accuracy_30d=91.8,
            bias=-2.3,
            mape=4.5,
            sample_count=150,
            last_updated=now,
        )
        assert metrics.accuracy_24h == 95.5
        assert metrics.accuracy_7d == 93.2
        assert metrics.accuracy_30d == 91.8
        assert metrics.bias == -2.3
        assert metrics.mape == 4.5
        assert metrics.sample_count == 150
        assert metrics.last_updated == now

    def test_to_dict(self):
        """Test serialization to dictionary."""
        now = datetime(2026, 2, 26, 8, 0, 0)
        metrics = ExtendedAccuracyMetrics(
            accuracy_24h=94.5,
            accuracy_7d=92.0,
            accuracy_30d=90.0,
            bias=1.5,
            mape=3.2,
            sample_count=100,
            last_updated=now,
        )
        result = metrics.to_dict()

        assert result["accuracy_24h"] == 94.5
        assert result["accuracy_7d"] == 92.0
        assert result["accuracy_30d"] == 90.0
        assert result["bias"] == 1.5
        assert result["mape"] == 3.2
        assert result["sample_count"] == 100
        assert result["last_updated"] == "2026-02-26T08:00:00"

    def test_to_dict_no_last_updated(self):
        """Test serialization when last_updated is None."""
        metrics = ExtendedAccuracyMetrics(accuracy_24h=95.0)
        result = metrics.to_dict()

        assert result["last_updated"] is None

    def test_from_dict(self):
        """Test deserialization from dictionary."""
        data = {
            "accuracy_24h": 93.5,
            "accuracy_7d": 91.0,
            "accuracy_30d": 89.5,
            "bias": -1.8,
            "mape": 4.1,
            "sample_count": 75,
            "last_updated": "2026-02-26T08:00:00",
        }
        metrics = ExtendedAccuracyMetrics.from_dict(data)

        assert metrics.accuracy_24h == 93.5
        assert metrics.accuracy_7d == 91.0
        assert metrics.accuracy_30d == 89.5
        assert metrics.bias == -1.8
        assert metrics.mape == 4.1
        assert metrics.sample_count == 75
        assert metrics.last_updated == datetime(2026, 2, 26, 8, 0, 0)

    def test_from_dict_invalid_datetime(self):
        """Test deserialization handles invalid datetime strings."""
        data = {
            "accuracy_24h": 95.0,
            "last_updated": "invalid-datetime",
        }
        metrics = ExtendedAccuracyMetrics.from_dict(data)

        assert metrics.accuracy_24h == 95.0
        assert metrics.last_updated is None

    def test_from_dict_missing_fields(self):
        """Test deserialization with missing fields uses None defaults."""
        data = {}
        metrics = ExtendedAccuracyMetrics.from_dict(data)

        assert metrics.accuracy_24h is None
        assert metrics.accuracy_7d is None
        assert metrics.accuracy_30d is None
        assert metrics.bias == 0.0
        assert metrics.mape == 0.0
        assert metrics.sample_count == 0


# =============================================================================
# EXTENDED FORECAST ACCURACY ENGINE TESTS
# =============================================================================


class TestExtendedForecastAccuracyEngine:
    """Tests for ExtendedForecastAccuracyEngine class."""

    def test_init(self):
        """Test initialization."""
        engine = ExtendedForecastAccuracyEngine()
        assert engine.metrics is not None
        assert engine.metrics.accuracy_24h is None

    def test_init_with_storage_path(self):
        """Test initialization with storage path."""
        engine = ExtendedForecastAccuracyEngine(storage_path="/tmp/test.json")
        assert engine.storage_path == "/tmp/test.json"

    def test_metrics_property(self):
        """Test metrics property returns current metrics."""
        engine = ExtendedForecastAccuracyEngine()
        metrics = engine.metrics

        assert isinstance(metrics, ExtendedAccuracyMetrics)
        assert metrics.accuracy_24h is None

    def test_to_dict(self):
        """Test engine serialization."""
        engine = ExtendedForecastAccuracyEngine()
        engine._metrics = ExtendedAccuracyMetrics(
            accuracy_24h=95.0,
            sample_count=50,
        )

        result = engine.to_dict()

        assert "metrics" in result
        assert result["metrics"]["accuracy_24h"] == 95.0
        assert result["history_count"] == 0

    def test_from_dict(self):
        """Test engine deserialization."""
        data = {
            "metrics": {
                "accuracy_24h": 94.0,
                "accuracy_7d": 92.0,
                "accuracy_30d": 90.0,
                "bias": 1.5,
                "mape": 3.0,
                "sample_count": 100,
                "last_updated": None,
            },
            "history_count": 10,
        }

        engine = ExtendedForecastAccuracyEngine.from_dict(data)

        assert engine.metrics.accuracy_24h == 94.0
        assert engine.metrics.accuracy_7d == 92.0
        assert engine.metrics.sample_count == 100


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
# BUG #694: EXTENDED ACCURACY METRICS NONE DEFAULTS
# =============================================================================


class TestExtendedAccuracyMetricsNoneDefaults:
    """Bug #694: Extended accuracy defaults must be None until real data exists."""

    def test_accuracy_24h_default_is_none(self):
        assert ExtendedAccuracyMetrics().accuracy_24h is None

    def test_accuracy_7d_default_is_none(self):
        assert ExtendedAccuracyMetrics().accuracy_7d is None

    def test_accuracy_30d_default_is_none(self):
        assert ExtendedAccuracyMetrics().accuracy_30d is None

    def test_from_dict_missing_fields_returns_none_defaults(self):
        m = ExtendedAccuracyMetrics.from_dict({})
        assert m.accuracy_24h is None
        assert m.accuracy_7d is None
        assert m.accuracy_30d is None

    def test_from_dict_explicit_none_preserved(self):
        m = ExtendedAccuracyMetrics.from_dict({
            "accuracy_24h": None,
            "accuracy_7d": None,
            "accuracy_30d": None,
        })
        assert m.accuracy_24h is None
        assert m.accuracy_7d is None
        assert m.accuracy_30d is None

    def test_to_dict_none_values_preserved(self):
        result = ExtendedAccuracyMetrics().to_dict()
        assert result["accuracy_24h"] is None
        assert result["accuracy_7d"] is None
        assert result["accuracy_30d"] is None

    def test_explicit_values_still_work(self):
        m = ExtendedAccuracyMetrics(
            accuracy_24h=95.0, accuracy_7d=93.0, accuracy_30d=91.0
        )
        assert m.accuracy_24h == pytest.approx(95.0)
        assert m.accuracy_7d == pytest.approx(93.0)
        assert m.accuracy_30d == pytest.approx(91.0)

    def test_from_dict_with_real_values(self):
        m = ExtendedAccuracyMetrics.from_dict({
            "accuracy_24h": 94.5,
            "accuracy_7d": 92.0,
            "accuracy_30d": 90.0,
        })
        assert m.accuracy_24h == pytest.approx(94.5)
        assert m.accuracy_7d == pytest.approx(92.0)
        assert m.accuracy_30d == pytest.approx(90.0)


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
