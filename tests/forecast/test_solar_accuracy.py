"""Tests for forecast/solar_accuracy.py - Solar forecast accuracy tracking."""

from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.util import dt as dt_util

from custom_components.localshift.forecast.solar_accuracy import (
    BIAS_HALF_LIFE_DAYS,
    MAX_PERIOD_RECORDS,
    SolarAccuracyTracker,
    SolarBiasMetrics,
    SolarPeriodRecord,
)


class TestSolarPeriodRecord:
    """Tests for SolarPeriodRecord dataclass."""

    def test_initialization_with_forecast(self):
        """Test record initialization with forecast value."""
        record = SolarPeriodRecord(
            period_start=datetime(2026, 1, 15, 10, 0, tzinfo=UTC),
            forecast_kwh=2.5,
            actual_kwh=2.0,
            weather_condition="sunny",
            time_of_day="morning",
            season="summer",
        )
        assert record.forecast_kwh == 2.5
        assert record.actual_kwh == 2.0
        # Bias = (forecast - actual) / forecast = (2.5 - 2.0) / 2.5 = 0.2
        assert record.bias == pytest.approx(0.2)
        assert record.additive_bias == pytest.approx(0.5)

    def test_initialization_with_zero_forecast(self):
        """Test record initialization with zero forecast - bias should be 0."""
        record = SolarPeriodRecord(
            period_start=datetime(2026, 1, 15, 10, 0, tzinfo=UTC),
            forecast_kwh=0.0,
            actual_kwh=0.0,
            weather_condition="cloudy",
            time_of_day="morning",
            season="summer",
        )
        assert record.bias == 0.0
        assert record.additive_bias == 0.0

    def test_initialization_with_small_forecast(self):
        """Test record initialization with very small forecast - bias should be 0."""
        record = SolarPeriodRecord(
            period_start=datetime(2026, 1, 15, 10, 0, tzinfo=UTC),
            forecast_kwh=0.001,
            actual_kwh=0.0,
            weather_condition="cloudy",
            time_of_day="morning",
            season="summer",
        )
        assert record.bias == 0.0
        assert record.additive_bias == pytest.approx(0.001)

    def test_initialization_with_boost_flag(self):
        """Boost-tagged records preserve the boost marker."""
        record = SolarPeriodRecord(
            period_start=datetime(2026, 1, 15, 10, 0, tzinfo=UTC),
            forecast_kwh=2.0,
            actual_kwh=1.0,
            weather_condition="sunny",
            time_of_day="morning",
            season="summer",
            is_boost_period=True,
        )
        assert record.is_boost_period is True

    def test_to_dict(self):
        """Test serialization to dictionary."""
        record = SolarPeriodRecord(
            period_start=datetime(2026, 1, 15, 10, 0, tzinfo=UTC),
            forecast_kwh=2.5,
            actual_kwh=2.0,
            weather_condition="sunny",
            time_of_day="morning",
            season="summer",
        )
        data = record.to_dict()
        assert data["forecast_kwh"] == 2.5
        assert data["actual_kwh"] == 2.0
        assert data["weather_condition"] == "sunny"
        assert data["time_of_day"] == "morning"
        assert data["season"] == "summer"
        assert data["additive_bias"] == pytest.approx(0.5)
        assert "period_start" in data

    def test_from_dict(self):
        """Test deserialization from dictionary."""
        data = {
            "period_start": "2026-01-15T10:00:00+00:00",
            "forecast_kwh": 2.5,
            "actual_kwh": 2.0,
            "weather_condition": "sunny",
            "time_of_day": "morning",
            "season": "summer",
            "bias": 0.2,
            "additive_bias": 0.5,
        }
        record = SolarPeriodRecord.from_dict(data)
        assert record.forecast_kwh == 2.5
        assert record.actual_kwh == 2.0
        assert record.weather_condition == "sunny"
        assert record.time_of_day == "morning"
        assert record.season == "summer"
        assert record.additive_bias == pytest.approx(0.5)

    def test_from_dict_with_defaults(self):
        """Test deserialization with missing fields uses defaults."""
        data = {"period_start": "2026-01-15T10:00:00+00:00"}
        record = SolarPeriodRecord.from_dict(data)
        assert record.forecast_kwh == 0.0
        assert record.actual_kwh == 0.0
        assert record.weather_condition == "unknown"
        assert record.time_of_day == "unknown"
        assert record.season == "unknown"
        assert record.additive_bias == 0.0


class TestSolarBiasMetrics:
    """Tests for SolarBiasMetrics dataclass."""

    def test_defaults(self):
        """Test default values."""
        metrics = SolarBiasMetrics()
        assert metrics.overall_bias == 0.0
        assert metrics.overall_additive_bias == 0.0
        assert metrics.bias_by_time == {}
        assert metrics.bias_by_weather == {}
        assert metrics.bias_by_season == {}
        assert metrics.additive_bias_by_time == {}
        assert metrics.additive_bias_by_weather == {}
        assert metrics.additive_bias_by_season == {}
        assert metrics.sample_count == 0
        assert metrics.mape == 0.0
        assert metrics.accuracy == 100.0

    def test_to_dict(self):
        """Test serialization to dictionary."""
        metrics = SolarBiasMetrics(
            overall_bias=0.1,
            overall_additive_bias=0.2,
            bias_by_time={"morning": 0.05},
            bias_by_weather={"sunny": 0.1},
            additive_bias_by_time={"morning": 0.1},
            additive_bias_by_weather={"sunny": 0.2},
            sample_count=10,
            mape=15.0,
            accuracy=85.0,
        )
        data = metrics.to_dict()
        assert data["overall_bias"] == 0.1
        assert data["overall_additive_bias"] == 0.2
        assert data["bias_by_time"] == {"morning": 0.05}
        assert data["additive_bias_by_time"] == {"morning": 0.1}
        assert data["sample_count"] == 10

    def test_from_dict(self):
        """Test deserialization from dictionary."""
        data = {
            "overall_bias": 0.1,
            "overall_additive_bias": 0.2,
            "bias_by_time": {"morning": 0.05},
            "bias_by_weather": {"sunny": 0.1},
            "bias_by_season": {"summer": 0.15},
            "additive_bias_by_time": {"morning": 0.1},
            "additive_bias_by_weather": {"sunny": 0.2},
            "additive_bias_by_season": {"summer": 0.25},
            "sample_count": 10,
            "mape": 15.0,
            "accuracy": 85.0,
        }
        metrics = SolarBiasMetrics.from_dict(data)
        assert metrics.overall_bias == 0.1
        assert metrics.overall_additive_bias == 0.2
        assert metrics.bias_by_time == {"morning": 0.05}
        assert metrics.additive_bias_by_weather == {"sunny": 0.2}
        assert metrics.sample_count == 10

    def test_from_dict_with_defaults(self):
        """Test deserialization with missing fields uses defaults."""
        data = {}
        metrics = SolarBiasMetrics.from_dict(data)
        assert metrics.overall_bias == 0.0
        assert metrics.accuracy == 100.0


class TestSolarAccuracyTracker:
    """Tests for SolarAccuracyTracker class."""

    @pytest.fixture
    def mock_hass(self):
        """Create mock HomeAssistant instance."""
        hass = MagicMock()
        hass.data = {}
        return hass

    @pytest.fixture
    def tracker(self, mock_hass):
        """Create SolarAccuracyTracker instance."""
        return SolarAccuracyTracker(mock_hass, "test_entry")

    def test_initialization(self, tracker):
        """Test tracker initialization."""
        assert tracker._hass is not None
        assert tracker._pending_forecasts == {}
        assert tracker._period_records.maxlen == MAX_PERIOD_RECORDS
        assert tracker.metrics is not None
        assert tracker._save_pending is False

    def test_record_forecast(self, tracker):
        """Test recording a forecast."""
        period_start = datetime(2026, 1, 15, 10, 0, tzinfo=UTC)
        tracker.record_forecast(period_start, 2.5, "sunny")

        key = period_start.isoformat()
        assert key in tracker._pending_forecasts
        record = tracker._pending_forecasts[key]
        assert record.forecast_kwh == 2.5
        assert record.weather_condition == "sunny"
        assert record.time_of_day == "morning"
        assert record.season == "summer"

    def test_record_forecast_sets_boost_flag(self, tracker):
        """Boost periods are tagged when forecasts are recorded."""
        period_start = datetime(2026, 1, 15, 10, 0, tzinfo=UTC)
        tracker.record_forecast(period_start, 2.5, "sunny", is_boost=True)

        record = tracker._pending_forecasts[period_start.isoformat()]
        assert record.is_boost_period is True

    def test_record_forecast_normalizes_weather(self, tracker):
        """Test weather normalization when recording forecast."""
        period_start = datetime(2026, 1, 15, 10, 0, tzinfo=UTC)
        tracker.record_forecast(period_start, 2.5, "partly cloudy")

        record = tracker._pending_forecasts[period_start.isoformat()]
        assert record.weather_condition == "cloudy"

    def test_record_forecast_weather_clear(self, tracker):
        """Test weather normalization for clear conditions."""
        period_start = datetime(2026, 1, 15, 10, 0, tzinfo=UTC)
        tracker.record_forecast(period_start, 2.5, "clear skies")

        record = tracker._pending_forecasts[period_start.isoformat()]
        assert record.weather_condition == "sunny"

    def test_record_forecast_weather_rain(self, tracker):
        """Test weather normalization for rainy conditions."""
        period_start = datetime(2026, 1, 15, 10, 0, tzinfo=UTC)
        tracker.record_forecast(period_start, 2.5, "light rain shower")

        record = tracker._pending_forecasts[period_start.isoformat()]
        assert record.weather_condition == "rainy"

    def test_record_forecast_weather_snow(self, tracker):
        """Test weather normalization for snowy conditions."""
        period_start = datetime(2026, 1, 15, 10, 0, tzinfo=UTC)
        tracker.record_forecast(period_start, 2.5, "snow flurries")

        record = tracker._pending_forecasts[period_start.isoformat()]
        assert record.weather_condition == "snow"

    def test_record_forecast_weather_fog(self, tracker):
        """Test weather normalization for foggy conditions."""
        period_start = datetime(2026, 1, 15, 10, 0, tzinfo=UTC)
        tracker.record_forecast(period_start, 2.5, "foggy morning")

        record = tracker._pending_forecasts[period_start.isoformat()]
        assert record.weather_condition == "foggy"

    def test_record_forecast_weather_unknown(self, tracker):
        """Test weather normalization for unknown conditions."""
        period_start = datetime(2026, 1, 15, 10, 0, tzinfo=UTC)
        tracker.record_forecast(period_start, 2.5, "")

        record = tracker._pending_forecasts[period_start.isoformat()]
        assert record.weather_condition == "unknown"

    def test_record_forecast_weather_none(self, tracker):
        """Test weather normalization for None conditions."""
        period_start = datetime(2026, 1, 15, 10, 0, tzinfo=UTC)
        tracker.record_forecast(period_start, 2.5, None)

        record = tracker._pending_forecasts[period_start.isoformat()]
        assert record.weather_condition == "unknown"

    def test_backfill_actual(self, tracker):
        """Test backfilling actual solar value."""
        period_start = datetime(2026, 1, 15, 10, 0, tzinfo=UTC)
        tracker.record_forecast(period_start, 2.5, "sunny")

        tracker.backfill_actual(period_start, 2.0)

        # Should no longer be pending
        assert period_start.isoformat() not in tracker._pending_forecasts

        # Should be in period records
        assert len(tracker._period_records) == 1

        # Metrics should be recomputed
        assert tracker._metrics.sample_count == 1
        assert tracker._save_pending is True

    def test_backfill_actual_missing_forecast(self, tracker):
        """Test backfill with no matching forecast - should be no-op."""
        period_start = datetime(2026, 1, 15, 10, 0, tzinfo=UTC)
        # Don't record forecast first
        tracker.backfill_actual(period_start, 2.0)

        # Should be no records
        assert len(tracker._period_records) == 0

    def test_backfill_matches_offset_second_timestamps(self, tracker):
        """Live Amber slot timestamps carry +1s (18:00:01); the actuals
        integration floors to the boundary (18:00:00). The period key must
        floor both sides or every backfill misses (2026-06-12 zero-samples
        incident: 50 pending, 0 samples after a full daytime)."""
        tz = timezone(timedelta(hours=10))
        recorded_at = datetime(2026, 6, 12, 10, 0, 1, tzinfo=tz)
        flushed_at = datetime(2026, 6, 12, 10, 0, 0, tzinfo=tz)

        tracker.record_forecast(recorded_at, 2.5, "sunny")
        tracker.backfill_actual(flushed_at, 2.0)

        assert tracker._pending_forecasts == {}
        assert len(tracker._period_records) == 1
        assert tracker._metrics.sample_count == 1

    def test_backfill_matches_thirty_minute_boundary_with_seconds(self, tracker):
        """The :30 boundary floors the same way as :00."""
        tz = timezone(timedelta(hours=10))
        tracker.record_forecast(
            datetime(2026, 6, 12, 10, 30, 1, tzinfo=tz), 1.5, "sunny"
        )
        tracker.backfill_actual(datetime(2026, 6, 12, 10, 30, 0, tzinfo=tz), 1.2)

        assert len(tracker._period_records) == 1

    def test_rerecord_with_different_seconds_overwrites_single_pending(self, tracker):
        """Re-recording the same period with a different second offset must
        replace the pending, not create a duplicate key."""
        tz = timezone(timedelta(hours=10))
        tracker.record_forecast(
            datetime(2026, 6, 12, 10, 0, 1, tzinfo=tz), 2.5, "sunny"
        )
        tracker.record_forecast(
            datetime(2026, 6, 12, 10, 0, 0, tzinfo=tz), 3.0, "sunny"
        )

        assert len(tracker._pending_forecasts) == 1
        (record,) = tracker._pending_forecasts.values()
        assert record.forecast_kwh == 3.0

    def test_backfill_multiple_records(self, tracker):
        """Test multiple backfills."""
        # Record multiple forecasts
        for hour in [8, 9, 10, 11]:
            period_start = datetime(2026, 1, 15, hour, 0, tzinfo=UTC)
            tracker.record_forecast(period_start, 2.5, "sunny")

        # Backfill some
        tracker.backfill_actual(datetime(2026, 1, 15, 8, 0, tzinfo=UTC), 2.0)
        tracker.backfill_actual(datetime(2026, 1, 15, 9, 0, tzinfo=UTC), 2.5)

        assert len(tracker._period_records) == 2
        assert tracker._metrics.sample_count == 2

    def test_metrics_computation(self, tracker):
        """Test that metrics are computed correctly."""
        # Add multiple records
        for hour in [8, 9, 10]:
            period_start = datetime(2026, 1, 15, hour, 0, tzinfo=UTC)
            tracker.record_forecast(period_start, 2.0, "sunny")

        # Backfill with different actuals
        tracker.backfill_actual(
            datetime(2026, 1, 15, 8, 0, tzinfo=UTC), 2.0
        )  # bias = 0
        tracker.backfill_actual(
            datetime(2026, 1, 15, 9, 0, tzinfo=UTC), 1.0
        )  # bias = 0.5
        tracker.backfill_actual(
            datetime(2026, 1, 15, 10, 0, tzinfo=UTC), 3.0
        )  # bias = -0.5

        # Overall bias should be (0 + 0.5 - 0.5) / 3 = 0
        assert tracker._metrics.sample_count == 3
        assert tracker._metrics.overall_bias == pytest.approx(0.0, abs=0.01)

    def test_metrics_accuracy_calculation(self, tracker):
        """Test accuracy calculation."""
        # Record and backfill
        period_start = datetime(2026, 1, 15, 10, 0, tzinfo=UTC)
        tracker.record_forecast(period_start, 2.0, "sunny")
        tracker.backfill_actual(period_start, 2.0)  # Perfect forecast

        # With perfect forecasts, accuracy should be high
        assert tracker._metrics.accuracy > 90

    def test_boost_periods_are_excluded_from_metrics(self, tracker):
        """Boost-tagged periods remain in history but not in learning metrics."""
        normal_period = datetime(2026, 1, 15, 10, 0, tzinfo=UTC)
        boost_period = datetime(2026, 1, 15, 10, 30, tzinfo=UTC)

        tracker.record_forecast(normal_period, 5.0, "sunny")
        tracker.backfill_actual(normal_period, 2.0)

        tracker.record_forecast(boost_period, 5.0, "sunny", is_boost=True)
        tracker.backfill_actual(boost_period, 1.0)

        assert len(tracker._period_records) == 2
        assert tracker._metrics.sample_count == 1
        assert tracker._period_records[-1].is_boost_period is True

    def test_backfill_is_boost_override_true(self, tracker):
        """is_boost=True at flush time overrides a non-boost pending."""
        period_start = datetime(2026, 1, 15, 10, 0, tzinfo=UTC)
        tracker.record_forecast(period_start, 2.0, "sunny", is_boost=False)
        tracker.backfill_actual(period_start, 1.0, is_boost=True)

        assert tracker._period_records[-1].is_boost_period is True
        # Boost records are excluded from metrics.
        assert tracker._metrics.sample_count == 0

    def test_backfill_is_boost_override_false(self, tracker):
        """is_boost=False at flush time overrides a boost-tagged pending."""
        period_start = datetime(2026, 1, 15, 10, 0, tzinfo=UTC)
        tracker.record_forecast(period_start, 2.0, "sunny", is_boost=True)
        tracker.backfill_actual(period_start, 1.0, is_boost=False)

        assert tracker._period_records[-1].is_boost_period is False
        assert tracker._metrics.sample_count == 1

    def test_backfill_is_boost_none_preserves_pending_flag(self, tracker):
        """is_boost=None (default) preserves the pending's recorded flag."""
        period_start = datetime(2026, 1, 15, 10, 0, tzinfo=UTC)
        tracker.record_forecast(period_start, 2.0, "sunny", is_boost=True)
        tracker.backfill_actual(period_start, 1.0)  # no is_boost arg

        assert tracker._period_records[-1].is_boost_period is True

    def test_backfill_zero_forecast_zero_actual_dropped(self, tracker):
        """Information-free overnight samples are dropped, not counted."""
        period_start = datetime(2026, 1, 15, 2, 0, tzinfo=UTC)
        tracker.record_forecast(period_start, 0.0, "clear")
        tracker.backfill_actual(period_start, 0.0)

        # Pending consumed, but no record appended and sample_count unchanged.
        assert period_start.isoformat() not in tracker._pending_forecasts
        assert len(tracker._period_records) == 0
        assert tracker._metrics.sample_count == 0

    def test_backfill_dusk_forecast_positive_tiny_actual_kept(self, tracker):
        """Dusk periods (forecast > 0, ~0 actual) ARE kept — that bias matters."""
        period_start = datetime(2026, 1, 15, 19, 0, tzinfo=UTC)
        tracker.record_forecast(period_start, 0.5, "clear")
        tracker.backfill_actual(period_start, 0.0)

        assert len(tracker._period_records) == 1
        assert tracker._metrics.sample_count == 1

    def test_evict_stale_pendings_drops_past_keeps_future(self, tracker):
        """Past-dated pendings are evicted; future horizon slots are retained."""
        now = dt_util.now()
        stale = (now - timedelta(hours=6)).replace(minute=0, second=0, microsecond=0)
        recent = (now - timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
        future = (now + timedelta(hours=2)).replace(minute=0, second=0, microsecond=0)

        tracker.record_forecast(stale, 2.0, "sunny")
        tracker.record_forecast(recent, 2.0, "sunny")
        tracker.record_forecast(future, 2.0, "sunny")

        tracker.evict_stale_pendings(max_age_hours=4.0)

        assert stale.isoformat() not in tracker._pending_forecasts
        assert recent.isoformat() in tracker._pending_forecasts
        assert future.isoformat() in tracker._pending_forecasts

    def test_get_bias_correction_no_data(self, tracker):
        """Test get_bias_correction with no historical data returns 1.0."""
        correction = tracker.get_bias_correction("morning", "sunny", "summer")
        assert correction == 1.0

    def test_get_bias_correction_returns_1_with_insufficient_samples(self, tracker):
        """Test get_bias_correction returns 1.0 with fewer than 20 samples."""
        # Add 19 samples (below threshold)
        for i in range(19):
            period_start = datetime(2026, 1, 1 + i, 10, 0, tzinfo=UTC)
            tracker.record_forecast(period_start, 2.0, "sunny")
            tracker.backfill_actual(period_start, 1.0)  # 50% bias

        # With <20 samples, should return 1.0 (no correction)
        correction = tracker.get_bias_correction("morning", "sunny", "summer")
        assert correction == 1.0

    def test_get_bias_correction_clamped_to_upper_bound(self, tracker):
        """Test get_bias_correction clamps to 1.5 when bias would exceed it."""
        # Add 20 samples with extreme underestimate bias
        for i in range(20):
            period_start = datetime(2026, 1, 1 + i, 10, 0, tzinfo=UTC)
            tracker.record_forecast(period_start, 1.0, "sunny")
            tracker.backfill_actual(
                period_start, 5.0
            )  # -400% bias (huge underestimate)

        # Bias = (1-5)/1 = 4.0, correction = 1.0 - 4.0 = -3.0, clamped to 1.5
        correction = tracker.get_bias_correction("morning", "sunny", "summer")
        assert correction == pytest.approx(1.5)

    def test_get_additive_correction_returns_0_with_insufficient_samples(self, tracker):
        """Test get_additive_correction returns 0.0 with fewer than 20 samples."""
        # Add 19 samples (below threshold)
        for i in range(19):
            period_start = datetime(2026, 1, 1 + i, 10, 0, tzinfo=UTC)
            tracker.record_forecast(period_start, 2.0, "sunny")
            tracker.backfill_actual(period_start, 1.5)

        # With <20 samples, should return 0.0 (no correction)
        correction = tracker.get_additive_correction("morning", "sunny", "summer")
        assert correction == 0.0

    def test_has_sufficient_samples_false_when_not_enough(self, tracker):
        """Test has_sufficient_samples returns False with <20 samples."""
        # Add 19 samples
        for i in range(19):
            period_start = datetime(2026, 1, 1 + i, 10, 0, tzinfo=UTC)
            tracker.record_forecast(period_start, 2.0, "sunny")
            tracker.backfill_actual(period_start, 1.0)

        assert tracker.has_sufficient_samples() is False

    def test_has_sufficient_samples_true_when_enough(self, tracker):
        """Test has_sufficient_samples returns True with >=20 samples."""
        # Add 20 samples
        for i in range(20):
            period_start = datetime(2026, 1, 1 + i, 10, 0, tzinfo=UTC)
            tracker.record_forecast(period_start, 2.0, "sunny")
            tracker.backfill_actual(period_start, 1.0)

        assert tracker.has_sufficient_samples() is True

    def test_get_status_dict_below_threshold(self, tracker):
        """get_status_dict reports progress toward activation while dormant."""
        from custom_components.localshift.forecast.solar_accuracy import (
            MIN_SOLAR_CORRECTION_SAMPLES,
        )

        for i in range(5):
            period_start = datetime(2026, 1, 1 + i, 10, 0, tzinfo=UTC)
            tracker.record_forecast(period_start, 2.0, "sunny")
            tracker.backfill_actual(period_start, 1.0)
        # A still-pending forecast for a future slot.
        tracker.record_forecast(datetime(2026, 2, 1, 10, 0, tzinfo=UTC), 2.0, "sunny")
        # A boost record (excluded from sample_count). The boost flag is set at
        # record time and preserved through backfill.
        boost = datetime(2026, 1, 20, 14, 0, tzinfo=UTC)
        tracker.record_forecast(boost, 5.0, "sunny", is_boost=True)
        tracker.backfill_actual(boost, 1.0)

        status = tracker.get_status_dict()

        assert status["sample_count"] == 5
        assert status["min_samples_required"] == MIN_SOLAR_CORRECTION_SAMPLES
        assert status["samples_until_active"] == MIN_SOLAR_CORRECTION_SAMPLES - 5
        assert status["correction_active"] is False
        assert status["pending_forecasts"] == 1
        assert status["boost_records_excluded"] == 1
        # Still carries the underlying metrics fields.
        assert "overall_bias" in status
        assert "accuracy" in status

    def test_get_status_dict_active_clamps_samples_until_active(self, tracker):
        """Once enough samples exist, correction is active and the gap is 0."""
        for i in range(20):
            period_start = datetime(2026, 1, 1 + i, 10, 0, tzinfo=UTC)
            tracker.record_forecast(period_start, 2.0, "sunny")
            tracker.backfill_actual(period_start, 1.0)

        status = tracker.get_status_dict()

        assert status["sample_count"] == 20
        assert status["samples_until_active"] == 0
        assert status["correction_active"] is True

    def test_get_bias_correction_with_data(self, tracker):
        """Test get_bias_correction with historical data."""
        # Add historical data with known bias (need 20+ samples for correction)
        # Use month 1 (January) = summer in Southern hemisphere
        for i in range(20):
            period_start = datetime(2026, 1, 1 + i, 10, 0, tzinfo=UTC)
            tracker.record_forecast(period_start, 2.0, "sunny")
            tracker.backfill_actual(period_start, 1.0)  # 50% overestimate

        correction = tracker.get_bias_correction("morning", "sunny", "summer")
        # Correction = 1.0 - bias = 1.0 - 0.5 = 0.5, clamped to [0.5, 1.5]
        assert correction == pytest.approx(0.5, rel=0.1)

    def test_get_bias_correction_under_estimate(self, tracker):
        """Test get_bias_correction when forecasts underestimate."""
        # Add historical data showing underestimate (need 20+ samples)
        # Use month 1 (January) = summer in Southern hemisphere
        for i in range(20):
            period_start = datetime(2026, 1, 1 + i, 10, 0, tzinfo=UTC)
            tracker.record_forecast(period_start, 1.0, "sunny")
            tracker.backfill_actual(period_start, 2.0)  # -100% bias (underestimate)

        correction = tracker.get_bias_correction("morning", "sunny", "summer")
        # Correction = 1.0 - (-1.0) = 2.0, clamped to [0.5, 1.5]
        assert correction == pytest.approx(1.5, rel=0.1)

    def test_get_additive_correction_no_data(self, tracker):
        """Test get_additive_correction returns 0.0 (deprecated, always returns 0)."""
        correction = tracker.get_additive_correction("morning", "sunny", "summer")
        assert correction == 0.0

    def test_get_additive_correction_with_data(self, tracker):
        """Test get_additive_correction still returns 0.0 (deprecated, always returns 0)."""
        # Add historical data (need 20+ samples)
        # Use month 1 (January) = summer in Southern hemisphere
        for i in range(20):
            period_start = datetime(2026, 1, 1 + i, 10, 0, tzinfo=UTC)
            tracker.record_forecast(period_start, 2.0, "sunny")
            tracker.backfill_actual(period_start, 1.5)

        # Deprecated - always returns 0.0 regardless of data
        correction = tracker.get_additive_correction("morning", "sunny", "summer")
        assert correction == 0.0

    def test_get_additive_correction_clamps_to_bounds(self, tracker):
        """Test get_additive_correction returns 0.0 (deprecated, always returns 0)."""
        # Add historical data with extreme bias (need 20+ samples)
        # Use month 1 (January) = summer in Southern hemisphere
        for i in range(20):
            period_start = datetime(2026, 1, 1 + i, 10, 0, tzinfo=UTC)
            tracker.record_forecast(period_start, 3.0, "sunny")
            tracker.backfill_actual(period_start, 0.0)

        # Deprecated - always returns 0.0 regardless of data
        correction = tracker.get_additive_correction("morning", "sunny", "summer")
        assert correction == 0.0

    def test_apply_bias_correction_uses_multiplicative_only(self, tracker):
        """Test apply_bias_correction uses multiplicative only (issue #760)."""
        # Add historical data (need 20+ samples)
        # Use month 1 (January) = summer in Southern hemisphere
        for i in range(20):
            period_start = datetime(2026, 1, 1 + i, 10, 0, tzinfo=UTC)
            tracker.record_forecast(period_start, 4.0, "sunny")
            tracker.backfill_actual(period_start, 3.0)

        # With bias=0.25 (25% overestimate), multiplier = 0.75
        # corrected = 2.0 * 0.75 = 1.5 (no additive subtraction)
        corrected = tracker.apply_bias_correction(2.0, "morning", "sunny", "summer")
        assert corrected == pytest.approx(1.5, rel=0.1)

    def test_apply_bias_correction_floors_at_zero(self, tracker):
        """Test apply_bias_correction floors at zero with multiplicative-only."""
        # Add historical data (need 20+ samples)
        # Use month 1 (January) = summer in Southern hemisphere
        for i in range(20):
            period_start = datetime(2026, 1, 1 + i, 10, 0, tzinfo=UTC)
            tracker.record_forecast(period_start, 1.0, "sunny")
            tracker.backfill_actual(period_start, 0.4)

        # Bias = 0.6 (60% overestimate), multiplier = 0.4, clamped to 0.5
        # corrected = 0.2 * 0.5 = 0.1 (floors at 0.0 is still valid edge case)
        corrected = tracker.apply_bias_correction(0.2, "morning", "sunny", "summer")
        assert corrected == pytest.approx(0.1, rel=0.1)

    def test_get_additive_correction_is_context_specific(self, tracker):
        """Test get_additive_correction returns 0.0 (deprecated, always returns 0)."""
        # Add historical data for different contexts (need 20+ samples for each)
        # Use month 1 (January) = summer in Southern hemisphere
        for i in range(20):
            morning = datetime(2026, 1, 1 + i, 10, 0, tzinfo=UTC)
            afternoon = datetime(2026, 1, 1 + i, 14, 0, tzinfo=UTC)

            tracker.record_forecast(morning, 2.0, "sunny")
            tracker.backfill_actual(morning, 1.5)
            tracker.record_forecast(afternoon, 2.0, "cloudy")
            tracker.backfill_actual(afternoon, 1.9)

        # Deprecated - always returns 0.0 regardless of context
        sunny_correction = tracker.get_additive_correction("morning", "sunny", "summer")
        cloudy_correction = tracker.get_additive_correction(
            "afternoon", "cloudy", "summer"
        )

        assert sunny_correction == 0.0
        assert cloudy_correction == 0.0

    @pytest.mark.asyncio
    async def test_async_load_no_data(self, tracker, mock_hass):
        """Test async_load with no stored data."""
        mock_store = MagicMock()
        mock_store.async_load = AsyncMock(return_value=None)
        tracker._store = mock_store

        await tracker.async_load()

        assert len(tracker._period_records) == 0

    @pytest.mark.asyncio
    async def test_async_load_with_data(self, tracker, mock_hass):
        """Test async_load with stored data."""
        mock_store = MagicMock()
        mock_store.async_load = AsyncMock(
            return_value={
                "period_records": [
                    {
                        "period_start": "2026-01-15T10:00:00+00:00",
                        "forecast_kwh": 2.0,
                        "actual_kwh": 1.5,
                        "weather_condition": "sunny",
                        "time_of_day": "morning",
                        "season": "summer",
                        "bias": 0.25,
                    }
                ],
                "metrics": {
                    "overall_bias": 0.25,
                    "bias_by_time": {"morning": 0.25},
                    "bias_by_weather": {"sunny": 0.25},
                    "bias_by_season": {"summer": 0.25},
                    "sample_count": 1,
                    "mape": 25.0,
                    "accuracy": 75.0,
                },
            }
        )
        tracker._store = mock_store

        await tracker.async_load()

        assert len(tracker._period_records) == 1
        assert tracker._metrics.sample_count == 1
        assert tracker._metrics.overall_bias == 0.25
        assert tracker._metrics.overall_additive_bias == pytest.approx(0.5)
        assert tracker._metrics.additive_bias_by_time == {"morning": pytest.approx(0.5)}

    @pytest.mark.asyncio
    async def test_async_load_with_bad_record(self, tracker, mock_hass, caplog):
        """Test async_load handles corrupted records gracefully."""
        import logging

        mock_store = MagicMock()
        mock_store.async_load = AsyncMock(
            return_value={
                "period_records": [
                    {"invalid": "data"},  # This will cause an error
                ],
                "metrics": {},
            }
        )
        tracker._store = mock_store

        with caplog.at_level(logging.WARNING):
            await tracker.async_load()

        # Should not crash, just skip bad record
        assert len(tracker._period_records) == 0

    @pytest.mark.asyncio
    async def test_async_save_no_pending(self, tracker, mock_hass):
        """Test async_save with no pending changes."""
        tracker._save_pending = False

        mock_store = MagicMock()
        mock_store.async_save = AsyncMock()
        tracker._store = mock_store

        await tracker.async_save()

        # Should not save if nothing pending
        mock_store.async_save.assert_not_called()

    @pytest.mark.asyncio
    async def test_async_save_with_pending(self, tracker, mock_hass):
        """Test async_save with pending changes."""
        tracker._save_pending = True
        # Add a record
        period_start = datetime(2026, 1, 15, 10, 0, tzinfo=UTC)
        tracker.record_forecast(period_start, 2.0, "sunny")
        tracker.backfill_actual(period_start, 1.5)

        mock_store = MagicMock()
        mock_store.async_save = AsyncMock()
        tracker._store = mock_store

        await tracker.async_save()

        mock_store.async_save.assert_called_once()
        # save_pending should be reset
        assert tracker._save_pending is False

    @pytest.mark.asyncio
    async def test_save_load_round_trip_survives_fresh_tracker(self, mock_hass):
        """record_forecast -> backfill_actual -> async_save -> fresh tracker load.

        Regression for root cause B: samples must survive a process restart.
        """
        # Use a single in-memory store shared between the two trackers to
        # simulate the .storage file persisting across a restart.
        saved_payload = {}

        store = MagicMock()

        async def _save(data):
            saved_payload.clear()
            saved_payload.update(data)

        async def _load():
            return saved_payload or None

        store.async_save = AsyncMock(side_effect=_save)
        store.async_load = AsyncMock(side_effect=_load)

        tracker = SolarAccuracyTracker(mock_hass, "test_entry")
        tracker._store = store

        period_start = datetime(2026, 1, 15, 10, 0, tzinfo=UTC)
        tracker.record_forecast(period_start, 2.0, "sunny")
        tracker.backfill_actual(period_start, 1.5)

        assert tracker._save_pending is True
        await tracker.async_save()
        store.async_save.assert_called_once()
        assert tracker._save_pending is False

        # Fresh tracker on the same store == a restart.
        reloaded = SolarAccuracyTracker(mock_hass, "test_entry")
        reloaded._store = store
        await reloaded.async_load()

        assert reloaded.metrics.sample_count == 1
        assert len(reloaded._period_records) == 1
        assert reloaded._period_records[0].forecast_kwh == 2.0
        assert reloaded._period_records[0].actual_kwh == 1.5
        assert reloaded.metrics.overall_bias == tracker.metrics.overall_bias


class TestGetTimeOfDay:
    """Tests for _get_time_of_day static method."""

    def test_morning_hours(self):
        """Test morning hours (6-12)."""
        for hour in [6, 7, 8, 9, 10, 11]:
            dt = datetime(2026, 1, 15, hour, 0, tzinfo=UTC)
            assert SolarAccuracyTracker._get_time_of_day(dt) == "morning"

    def test_afternoon_hours(self):
        """Test afternoon hours (12-18)."""
        for hour in [12, 13, 14, 15, 16, 17]:
            dt = datetime(2026, 1, 15, hour, 0, tzinfo=UTC)
            assert SolarAccuracyTracker._get_time_of_day(dt) == "afternoon"

    def test_evening_hours(self):
        """Test evening hours (18-21)."""
        for hour in [18, 19, 20]:
            dt = datetime(2026, 1, 15, hour, 0, tzinfo=UTC)
            assert SolarAccuracyTracker._get_time_of_day(dt) == "evening"

    def test_night_hours(self):
        """Test night hours (21-6)."""
        for hour in [21, 22, 23, 0, 1, 2, 3, 4, 5]:
            dt = datetime(2026, 1, 15, hour, 0, tzinfo=UTC)
            assert SolarAccuracyTracker._get_time_of_day(dt) == "night"


class TestGetSeason:
    """Tests for _get_season static method - Southern hemisphere."""

    def test_summer_months(self):
        """Test summer months (Dec, Jan, Feb)."""
        for month in [12, 1, 2]:
            dt = datetime(2026, month, 15, 12, 0, tzinfo=UTC)
            assert SolarAccuracyTracker._get_season(dt) == "summer"

    def test_autumn_months(self):
        """Test autumn months (Mar, Apr, May)."""
        for month in [3, 4, 5]:
            dt = datetime(2026, month, 15, 12, 0, tzinfo=UTC)
            assert SolarAccuracyTracker._get_season(dt) == "autumn"

    def test_winter_months(self):
        """Test winter months (Jun, Jul, Aug)."""
        for month in [6, 7, 8]:
            dt = datetime(2026, month, 15, 12, 0, tzinfo=UTC)
            assert SolarAccuracyTracker._get_season(dt) == "winter"

    def test_spring_months(self):
        """Test spring months (Sep, Oct, Nov)."""
        for month in [9, 10, 11]:
            dt = datetime(2026, month, 15, 12, 0, tzinfo=UTC)
            assert SolarAccuracyTracker._get_season(dt) == "spring"


class TestNormalizeWeather:
    """Tests for _normalize_weather static method."""

    def test_sunny_variations(self):
        """Test sunny variations."""
        assert SolarAccuracyTracker._normalize_weather("sunny") == "sunny"
        assert SolarAccuracyTracker._normalize_weather("Sunny") == "sunny"
        assert SolarAccuracyTracker._normalize_weather("clear") == "sunny"
        assert SolarAccuracyTracker._normalize_weather("Clear Skies") == "sunny"

    def test_cloudy_variations(self):
        """Test cloudy variations."""
        assert SolarAccuracyTracker._normalize_weather("cloudy") == "cloudy"
        assert SolarAccuracyTracker._normalize_weather("overcast") == "cloudy"
        assert SolarAccuracyTracker._normalize_weather("Partly Cloudy") == "cloudy"

    def test_rainy_variations(self):
        """Test rainy variations."""
        assert SolarAccuracyTracker._normalize_weather("rain") == "rainy"
        assert SolarAccuracyTracker._normalize_weather("rainy") == "rainy"
        assert SolarAccuracyTracker._normalize_weather("showers") == "rainy"
        assert SolarAccuracyTracker._normalize_weather("Light Rain") == "rainy"

    def test_snow_variations(self):
        """Test snow variations."""
        assert SolarAccuracyTracker._normalize_weather("snow") == "snow"
        assert SolarAccuracyTracker._normalize_weather("hail") == "snow"
        assert SolarAccuracyTracker._normalize_weather("Snow flurries") == "snow"

    def test_fog_variations(self):
        """Test fog variations."""
        assert SolarAccuracyTracker._normalize_weather("fog") == "foggy"
        assert SolarAccuracyTracker._normalize_weather("mist") == "foggy"
        assert SolarAccuracyTracker._normalize_weather("Foggy") == "foggy"

    def test_unknown_conditions(self):
        """Test unknown conditions."""
        assert SolarAccuracyTracker._normalize_weather("unknown") == "unknown"
        assert SolarAccuracyTracker._normalize_weather("") == "unknown"
        assert SolarAccuracyTracker._normalize_weather(None) == "unknown"
        assert SolarAccuracyTracker._normalize_weather("blustery") == "unknown"


def _fill_tracker_with_bias(tracker, forecast: float, actual: float, count: int):
    """Helper: add `count` period records with given forecast/actual values."""
    from datetime import datetime

    for i in range(count):
        period = datetime(2026, 1, 1, 6 + i // 2, (i % 2) * 30, tzinfo=UTC)
        tracker.record_forecast(period, forecast, "sunny")
        tracker.backfill_actual(period, actual)


class TestComputeContextBias:
    """Tests for _compute_context_bias method."""

    @pytest.fixture
    def tracker_with_data(self, mock_hass):
        """Create tracker with historical data."""
        tracker = SolarAccuracyTracker(mock_hass, "test_entry")

        # Add data for different contexts
        # Morning, sunny, summer
        for _ in range(3):
            dt = datetime(2026, 1, 15, 10, 0, tzinfo=UTC)
            tracker.record_forecast(dt, 2.0, "sunny")
            tracker.backfill_actual(dt, 1.5)  # 25% overestimate

        # Morning, cloudy, summer
        dt = datetime(2026, 1, 15, 9, 0, tzinfo=UTC)
        tracker.record_forecast(dt, 1.0, "cloudy")
        tracker.backfill_actual(dt, 1.0)  # Perfect

        return tracker

    def test_finds_matching_context(self, tracker_with_data):
        """Test finding bias for matching context."""
        result = tracker_with_data._compute_context_bias("morning", "sunny", "summer")
        assert result is not None
        weighted_bias, count = result
        assert count == 3
        assert weighted_bias == pytest.approx(0.25, rel=0.1)

    def test_no_matching_context(self, tracker_with_data):
        """Test when no matching context exists."""
        result = tracker_with_data._compute_context_bias("evening", "sunny", "summer")
        assert result is None

    def test_no_season_filter(self, tracker_with_data):
        """Test without season filter."""
        result = tracker_with_data._compute_context_bias("morning", "sunny", None)
        assert result is not None
        _, count = result
        assert count == 3

    def test_compute_context_additive_bias(self, tracker_with_data):
        result = tracker_with_data._compute_context_additive_bias(
            "morning", "sunny", "summer"
        )
        assert result is not None
        weighted_bias, count = result
        assert count == 3
        assert weighted_bias == pytest.approx(0.5, rel=0.1)

    def test_compute_context_bias_uses_true_half_life_weighting(
        self, tracker_with_data
    ):
        tracker_with_data._period_records.clear()
        now = datetime(2026, 1, 15, 10, 0, tzinfo=UTC)
        tracker_with_data._period_records.extend([
            SolarPeriodRecord(
                period_start=now,
                forecast_kwh=1.0,
                actual_kwh=1.0,
                weather_condition="sunny",
                time_of_day="morning",
                season="summer",
            ),
            SolarPeriodRecord(
                period_start=now.replace(day=8),
                forecast_kwh=1.0,
                actual_kwh=0.0,
                weather_condition="sunny",
                time_of_day="morning",
                season="summer",
            ),
        ])

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "custom_components.localshift.forecast.solar_accuracy.dt_util.now",
                lambda: now,
            )
            result = tracker_with_data._compute_context_bias(
                "morning", "sunny", "summer"
            )

        assert result is not None
        weighted_bias, count = result
        expected = 0.5 / (1.0 + 0.5)
        assert count == 2
        assert BIAS_HALF_LIFE_DAYS == 7.0
        assert weighted_bias == pytest.approx(expected, rel=0.01)


# ─── accuracy_confidence_ceiling tests ─────────────────────────────────────


class TestAccuracyConfidenceCeiling:
    """Tests for accuracy_confidence_ceiling method."""

    @pytest.fixture
    def mock_hass(self):
        """Create mock HomeAssistant instance."""
        hass = MagicMock()
        hass.data = {}
        return hass

    @pytest.fixture
    def tracker(self, mock_hass):
        """Create SolarAccuracyTracker instance."""
        return SolarAccuracyTracker(mock_hass, "test_entry")

    def test_accuracy_ceiling_dormant_below_sample_threshold(self, tracker):
        """Returns 1.0 (no cap) when fewer than MIN_SOLAR_CORRECTION_SAMPLES records exist."""
        # tracker fixture starts empty → 0 samples
        assert tracker.accuracy_confidence_ceiling() == pytest.approx(1.0)
        assert tracker.accuracy_confidence_ceiling(low=0.2, high=0.9) == pytest.approx(
            1.0
        )

    def test_accuracy_ceiling_low_when_poor_accuracy(self, tracker):
        """Returns 'low' param when accuracy is ≤ 50%."""
        # Build 20 records with extreme over-forecasting (accuracy ≈ 0%)
        _fill_tracker_with_bias(tracker, forecast=10.0, actual=0.1, count=20)
        assert tracker.has_sufficient_samples()
        ceiling = tracker.accuracy_confidence_ceiling(low=0.25, high=1.0)
        assert ceiling == pytest.approx(0.25)

    def test_accuracy_ceiling_high_when_good_accuracy(self, tracker):
        """Returns 'high' param when accuracy is ≥ 85%."""
        # Build 20 records with near-perfect forecasting
        _fill_tracker_with_bias(tracker, forecast=1.0, actual=1.0, count=20)
        assert tracker.has_sufficient_samples()
        ceiling = tracker.accuracy_confidence_ceiling(low=0.3, high=1.0)
        assert ceiling == pytest.approx(1.0)

    def test_accuracy_ceiling_interpolates_midrange(self, tracker):
        """Linear interpolation between low and high for 50% < accuracy < 85%."""
        # accuracy ~67.5% (MAPE ~32.5%, actual = forecast * 0.675 → bias ≈ 32.5%)
        _fill_tracker_with_bias(tracker, forecast=1.0, actual=0.675, count=20)
        assert tracker.has_sufficient_samples()
        ceiling = tracker.accuracy_confidence_ceiling(low=0.3, high=1.0)
        # accuracy = 100 - 32.5 = 67.5 → interpolation at (67.5-50)/(85-50) = 0.5
        assert 0.3 < ceiling < 1.0
