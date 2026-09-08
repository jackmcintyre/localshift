"""Unit tests for LoadForecaster with exponential decay algorithm (Issue #381, #441)."""

from unittest.mock import MagicMock

import pytest

from custom_components.localshift.const import (
    DEFAULT_LOAD_DECAY_FACTOR,
    DEFAULT_LOAD_INITIAL_WEIGHT,
    MIN_SAMPLES_PER_AGGREGATE_HOUR,
    MIN_SAMPLES_PER_DAY_HOUR,
)
from custom_components.localshift.forecast.load import (
    LoadForecaster,
    LoadProfiles,
)


def _create_mock_entry():
    """Create a mock config entry for testing."""
    entry = MagicMock()
    entry.options = {}
    return entry


def _create_load_forecaster():
    """Create a LoadForecaster instance for testing."""
    mock_entry = _create_mock_entry()
    return LoadForecaster(mock_entry)


class TestLoadForecasterExponentialDecay:
    """Test LoadForecaster.estimate_hourly_consumption_kw() with exponential decay."""

    def test_current_hour_blends_live_and_recent(self):
        """Test hour_distance == 0 blends live load with recent average.

        When slot_hour == current_hour, should blend current_load_kw (30%)
        with recent_load_kw (70%) for more stable forecasting.
        This prevents underestimation when instantaneous load is temporarily low.
        """
        forecaster = _create_load_forecaster()
        hourly_avg = {10: 0.5, 11: 0.6, 12: 0.7}

        # Scenario: instantaneous is low (0.526) but recent avg is higher (1.272)
        # Expected: 0.3 * 0.526 + 0.7 * 1.272 = 0.158 + 0.890 = 1.048
        kw, source = forecaster.estimate_hourly_consumption_kw(
            hourly_avg_kw=hourly_avg,
            slot_hour=11,
            current_hour=11,
            current_load_kw=0.526,
            recent_load_kw=1.272,
        )

        assert source == "blended_live"
        expected = 0.3 * 0.526 + 0.7 * 1.272
        assert abs(kw - round(expected, 3)) < 0.001

    def test_current_hour_falls_back_to_live_when_recent_unavailable(self):
        """Test hour_distance == 0 falls back to live load when recent unavailable.

        When recent_load_kw is 0 or unavailable, should use current_load_kw directly.
        """
        forecaster = _create_load_forecaster()
        hourly_avg = {10: 0.5, 11: 0.6, 12: 0.7}

        kw, source = forecaster.estimate_hourly_consumption_kw(
            hourly_avg_kw=hourly_avg,
            slot_hour=11,
            current_hour=11,
            current_load_kw=0.45,
            recent_load_kw=0.0,
        )

        assert source == "live_load"
        assert kw == 0.45

    def test_current_hour_falls_back_to_recent_when_current_unavailable(self):
        """Test hour_distance == 0 falls back to recent load when current unavailable.

        When current_load_kw is 0 or unavailable, should use recent_load_kw directly.
        """
        forecaster = _create_load_forecaster()
        hourly_avg = {10: 0.5, 11: 0.6, 12: 0.7}

        kw, source = forecaster.estimate_hourly_consumption_kw(
            hourly_avg_kw=hourly_avg,
            slot_hour=11,
            current_hour=11,
            current_load_kw=0.0,
            recent_load_kw=0.8,
        )

        assert source == "recent_load"
        assert kw == 0.8

    def test_current_hour_falls_back_to_historical_when_both_unavailable(self):
        """Test hour_distance == 0 falls back to historical when both unavailable.

        When both current_load_kw and recent_load_kw are 0, should use historical.
        """
        forecaster = _create_load_forecaster()
        hourly_avg = {10: 0.5, 11: 0.6, 12: 0.7}

        kw, source = forecaster.estimate_hourly_consumption_kw(
            hourly_avg_kw=hourly_avg,
            slot_hour=11,
            current_hour=11,
            current_load_kw=0.0,
            recent_load_kw=0.0,
        )

        assert source == "profile_hour"
        assert kw == 0.6

    def test_near_term_exponential_decay_d1(self):
        """Test hour_distance == 1 applies correct exponential decay.

        decay formula: live_weight = 0.8 * (0.8 ^ 1) = 0.64
        result = 0.64 * recent_load + 0.36 * historical
        """
        forecaster = _create_load_forecaster()
        # Include hour 10 in historical data
        hourly_avg = {9: 0.4, 10: 0.5, 11: 0.6, 12: 0.7}
        recent_load = 0.5
        historical = 0.5  # hourly_avg[10]

        kw, source = forecaster.estimate_hourly_consumption_kw(
            hourly_avg_kw=hourly_avg,
            slot_hour=10,
            current_hour=11,
            current_load_kw=0.45,
            recent_load_kw=recent_load,
        )

        assert source == "decay_load_d1"
        expected_live_weight = DEFAULT_LOAD_INITIAL_WEIGHT * (
            DEFAULT_LOAD_DECAY_FACTOR**1
        )
        expected_historical_weight = 1.0 - expected_live_weight
        expected_kw = (expected_live_weight * recent_load) + (
            expected_historical_weight * historical
        )
        assert abs(kw - round(expected_kw, 3)) < 0.001

    def test_near_term_exponential_decay_d2(self):
        """Test hour_distance == 2 applies correct exponential decay.

        decay formula: live_weight = 0.8 * (0.8 ^ 2) = 0.512
        """
        forecaster = _create_load_forecaster()
        # Include hour 9 in historical data
        hourly_avg = {8: 0.3, 9: 0.5, 10: 0.6, 11: 0.7, 12: 0.8}
        recent_load = 0.5
        historical = 0.5  # hourly_avg[9]

        kw, source = forecaster.estimate_hourly_consumption_kw(
            hourly_avg_kw=hourly_avg,
            slot_hour=9,
            current_hour=11,
            current_load_kw=0.45,
            recent_load_kw=recent_load,
        )

        assert source == "decay_load_d2"
        expected_live_weight = DEFAULT_LOAD_INITIAL_WEIGHT * (
            DEFAULT_LOAD_DECAY_FACTOR**2
        )
        expected_historical_weight = 1.0 - expected_live_weight
        expected_kw = (expected_live_weight * recent_load) + (
            expected_historical_weight * historical
        )
        assert abs(kw - round(expected_kw, 3)) < 0.001

    def test_near_term_exponential_decay_d3(self):
        """Test hour_distance == 3 applies correct exponential decay.

        decay formula: live_weight = 0.8 * (0.8 ^ 3) = 0.4096
        """
        forecaster = _create_load_forecaster()
        # Include hour 8 in historical data
        hourly_avg = {7: 0.2, 8: 0.5, 9: 0.6, 10: 0.7, 11: 0.8, 12: 0.9}
        recent_load = 0.5
        historical = 0.5  # hourly_avg[8]

        kw, source = forecaster.estimate_hourly_consumption_kw(
            hourly_avg_kw=hourly_avg,
            slot_hour=8,
            current_hour=11,
            current_load_kw=0.45,
            recent_load_kw=recent_load,
        )

        assert source == "decay_load_d3"
        expected_live_weight = DEFAULT_LOAD_INITIAL_WEIGHT * (
            DEFAULT_LOAD_DECAY_FACTOR**3
        )
        expected_historical_weight = 1.0 - expected_live_weight
        expected_kw = (expected_live_weight * recent_load) + (
            expected_historical_weight * historical
        )
        assert abs(kw - round(expected_kw, 3)) < 0.001

    def test_distant_hour_uses_historical(self):
        """Test hour_distance == 4 uses historical only.

        Beyond 3 hours, should use historical profile only.
        """
        forecaster = _create_load_forecaster()
        # Include hour 7 in historical data
        hourly_avg = {7: 0.35, 8: 0.4, 9: 0.5, 10: 0.6, 11: 0.7, 12: 0.8}

        kw, source = forecaster.estimate_hourly_consumption_kw(
            hourly_avg_kw=hourly_avg,
            slot_hour=7,
            current_hour=11,
            current_load_kw=0.45,
            recent_load_kw=0.5,
        )

        assert source == "profile_hour"
        assert kw == 0.35  # Historical value for hour 7

    def test_no_historical_falls_back_to_live(self):
        """Test when no historical data, falls back to live load.

        When hourly_avg_kw is empty or hour not in dict,
        should use current_load_kw or 0.6 fallback.
        """
        forecaster = _create_load_forecaster()

        kw, source = forecaster.estimate_hourly_consumption_kw(
            hourly_avg_kw={},
            slot_hour=10,
            current_hour=11,
            current_load_kw=0.45,
            recent_load_kw=0.5,
        )

        assert source == "live_load_fallback"
        assert kw == 0.45

    def test_no_historical_no_live_fallback(self, caplog):
        """Test when no historical and no live load, returns 0.0 with warning."""
        import logging

        forecaster = _create_load_forecaster()

        with caplog.at_level(logging.WARNING):
            kw, source = forecaster.estimate_hourly_consumption_kw(
                hourly_avg_kw={},
                slot_hour=10,
                current_hour=11,
                current_load_kw=0.0,
                recent_load_kw=0.0,
            )

        assert source == "live_load_fallback"
        assert kw == 0.0
        assert "NO_LOAD_DATA" in caplog.text

    def test_weather_correlation_applied(self, mock_weather_correlation):
        """Test weather correlation adjustment when confidence is high."""
        mock_entry = _create_mock_entry()
        mock_weather_correlation.get_coefficients_for_hour.return_value = MagicMock(
            confidence="high"
        )
        mock_weather_correlation.predict_load.return_value = (0.75, "weather_heating")

        forecaster = LoadForecaster(
            mock_entry, weather_correlation=mock_weather_correlation
        )
        hourly_avg = {10: 0.5, 11: 0.6, 12: 0.7}

        kw, source = forecaster.estimate_hourly_consumption_kw(
            hourly_avg_kw=hourly_avg,
            slot_hour=11,
            current_hour=11,
            current_load_kw=0.45,
            recent_load_kw=0.5,
            temperature=25.0,
        )

        assert source == "weather_heating"
        assert kw == 0.75

    def test_weather_correlation_skipped_low_confidence(self, mock_weather_correlation):
        """predict_load is the authority: a "low_confidence" result keeps base.

        The pre-gate on the hour's max-of-zones label was removed (it was
        strictly redundant with predict_load's per-zone gate and would wrongly
        suppress a usable zone in a mixed hour). Here predict_load itself refuses
        the adjustment by returning "low_confidence" even though it returned a
        candidate value, so the blended-live base must be kept.
        """
        mock_entry = _create_mock_entry()
        mock_weather_correlation.get_coefficients_for_hour.return_value = MagicMock(
            confidence="low"
        )
        mock_weather_correlation.predict_load.return_value = (0.99, "low_confidence")

        forecaster = LoadForecaster(
            mock_entry, weather_correlation=mock_weather_correlation
        )
        hourly_avg = {10: 0.5, 11: 0.6, 12: 0.7}

        kw, source = forecaster.estimate_hourly_consumption_kw(
            hourly_avg_kw=hourly_avg,
            slot_hour=11,
            current_hour=11,
            current_load_kw=0.45,
            recent_load_kw=0.5,
            temperature=25.0,
        )

        assert source == "blended_live"
        expected = 0.3 * 0.45 + 0.7 * 0.5
        assert abs(kw - round(expected, 3)) < 0.001

    def test_midnight_wrap_far_future_uses_historical(self):
        """Test hours_ahead prevents spurious live-blending for distant slots.

        Without hours_ahead, slot_hour=21 with current_hour=22 would appear
        1 hour away due to midnight wrap (24 - 22 + 21 = wrong).
        With hours_ahead=23, correctly uses historical profile only.
        """
        forecaster = _create_load_forecaster()
        hourly_avg = {21: 0.5, 22: 0.6, 23: 0.7, 0: 0.4, 1: 0.3}

        kw, source = forecaster.estimate_hourly_consumption_kw(
            hourly_avg_kw=hourly_avg,
            slot_hour=21,
            current_hour=22,
            current_load_kw=1.0,
            recent_load_kw=1.0,
            hours_ahead=23.0,
        )

        assert source == "profile_hour"
        assert kw == 0.5

    def test_hours_ahead_near_slots_decay_correctly(self):
        """Test hours_ahead correctly controls decay for near-term slots."""
        forecaster = _create_load_forecaster()
        hourly_avg = {10: 0.5, 11: 0.6, 12: 0.7}

        for hours_ahead_val, expected_source in [
            (0.0, "blended_live"),
            (1.0, "decay_load_d1"),
            (2.0, "decay_load_d2"),
            (3.0, "decay_load_d3"),
            (4.0, "profile_hour"),
        ]:
            _, source = forecaster.estimate_hourly_consumption_kw(
                hourly_avg_kw=hourly_avg,
                slot_hour=10,
                current_hour=11,
                current_load_kw=1.0,
                recent_load_kw=1.0,
                hours_ahead=hours_ahead_val,
            )
            assert source == expected_source, f"Failed at hours_ahead={hours_ahead_val}"

    def test_fallback_uses_historical_mean_when_available(self):
        """Test fallback uses mean of available historical hours when slot_hour missing."""
        forecaster = _create_load_forecaster()
        hourly_avg = {8: 0.4, 9: 0.6}

        kw, source = forecaster.estimate_hourly_consumption_kw(
            hourly_avg_kw=hourly_avg,
            slot_hour=15,
            current_hour=15,
            current_load_kw=0.0,
            recent_load_kw=0.0,
        )

        assert source == "live_load_fallback"
        assert kw == 0.5

    def test_cold_start_returns_zero_with_warning(self, caplog):
        """Test cold start returns 0.0 and logs warning when no data available."""
        import logging

        forecaster = _create_load_forecaster()

        with caplog.at_level(logging.WARNING):
            kw, source = forecaster.estimate_hourly_consumption_kw(
                hourly_avg_kw={},
                slot_hour=10,
                current_hour=10,
                current_load_kw=0.0,
                recent_load_kw=0.0,
            )

        assert source == "live_load_fallback"
        assert kw == 0.0
        assert "NO_LOAD_DATA" in caplog.text

    def test_parse_time_option_valid_and_invalid(self):
        entry = _create_mock_entry()
        entry.options = {"valid": "06:30:15", "invalid": "oops"}
        forecaster = LoadForecaster(entry)

        valid = forecaster.parse_time_option("valid", "01:02:03")
        fallback = forecaster.parse_time_option("invalid", "01:02:03")

        assert (valid.hour, valid.minute, valid.second) == (6, 30, 15)
        assert (fallback.hour, fallback.minute, fallback.second) == (1, 2, 3)

    def test_weather_correlation_invalid_adjustment_source_keeps_base_load(self):
        mock_entry = _create_mock_entry()
        weather = MagicMock()
        weather.get_coefficients_for_hour.return_value = MagicMock(confidence="high")
        weather.predict_load.return_value = (0.99, "low_confidence")

        forecaster = LoadForecaster(mock_entry, weather_correlation=weather)
        kw, source = forecaster.estimate_hourly_consumption_kw(
            hourly_avg_kw={11: 0.5},
            slot_hour=11,
            current_hour=11,
            current_load_kw=0.45,
            recent_load_kw=0.5,
            temperature=22.0,
        )

        expected = 0.3 * 0.45 + 0.7 * 0.5
        assert abs(kw - round(expected, 3)) < 0.001
        assert source == "blended_live"

    def test_weather_correlation_weather_none_keeps_base_load(self):
        mock_entry = _create_mock_entry()
        weather = MagicMock()
        weather.get_coefficients_for_hour.return_value = MagicMock(confidence="high")
        weather.predict_load.return_value = (0.6, "weather_none")

        forecaster = LoadForecaster(mock_entry, weather_correlation=weather)
        kw, source = forecaster.estimate_hourly_consumption_kw(
            hourly_avg_kw={11: 0.5},
            slot_hour=11,
            current_hour=11,
            current_load_kw=0.45,
            recent_load_kw=0.5,
            temperature=22.0,
        )

        expected = 0.3 * 0.45 + 0.7 * 0.5
        assert abs(kw - round(expected, 3)) < 0.001
        assert source == "blended_live"


class TestLoadForecasterOutputCeiling:
    """Issue #826: data-driven output ceiling and no input clamp."""

    def test_no_input_clamp_passes_current_through(self):
        """Without the clamp, current_load_kw is passed directly; blend = 0.3*3.0 + 0.7*2.5 = 2.65."""
        forecaster = _create_load_forecaster()
        kw, _ = forecaster.estimate_hourly_consumption_kw(
            hourly_avg_kw={12: 2.5},
            slot_hour=12,
            current_hour=12,
            current_load_kw=3.0,
            recent_load_kw=2.5,
            hours_ahead=0.0,
        )
        # No clamp; data-driven ceiling = 2.5*3=7.5 > 2.65 so no ceiling hit either
        assert kw == pytest.approx(2.65, abs=0.01)

    def test_output_ceiling_is_data_driven(self):
        """Per-slot ceiling = max(hourly_avg_kw)*3; low-peak home clamps, high-peak home does not."""
        forecaster = _create_load_forecaster()

        # Case 1: peak avg = 2.0 kW, ceiling = 6.0 kW; live-load path returns 50.0 → clamped
        kw, _ = forecaster.estimate_hourly_consumption_kw(
            hourly_avg_kw={12: 2.0},
            slot_hour=12,
            current_hour=12,
            current_load_kw=50.0,
            recent_load_kw=0.0,
            hours_ahead=0.0,
        )
        assert kw == pytest.approx(6.0)

        # Case 2: peak avg = 10.0 kW, ceiling = 30.0 kW; 20.0 kW is below ceiling, not clamped
        kw2, _ = forecaster.estimate_hourly_consumption_kw(
            hourly_avg_kw={12: 10.0},
            slot_hour=12,
            current_hour=12,
            current_load_kw=20.0,
            recent_load_kw=0.0,
            hours_ahead=0.0,
        )
        assert kw2 == pytest.approx(20.0)


@pytest.fixture
def mock_weather_correlation():
    """Mock WeatherCorrelation for testing."""
    mock = MagicMock()
    return mock


class TestWeatherAdjustmentTracking:
    """Tests for weather_adjustment_applied tracking (Issue #739)."""

    def test_get_weather_adjustment_applied_defaults_to_false(self):
        forecaster = _create_load_forecaster()
        assert forecaster.get_weather_adjustment_applied() is False

    def test_reset_weather_adjustment_applied_sets_flag_false(self):
        forecaster = _create_load_forecaster()
        forecaster._weather_adjustment_applied = True
        forecaster.reset_weather_adjustment_applied()
        assert forecaster.get_weather_adjustment_applied() is False

    def test_weather_adjustment_flag_set_when_applied(self):
        mock_entry = _create_mock_entry()
        weather = MagicMock()
        weather.get_coefficients_for_hour.return_value = MagicMock(confidence="medium")
        weather.predict_load.return_value = (1.5, "weather_heating")

        forecaster = LoadForecaster(mock_entry, weather_correlation=weather)
        forecaster.reset_weather_adjustment_applied()

        forecaster.estimate_hourly_consumption_kw(
            hourly_avg_kw={11: 1.0},
            slot_hour=11,
            current_hour=10,
            current_load_kw=0.5,
            recent_load_kw=0.6,
            temperature=30.0,
        )

        assert forecaster.get_weather_adjustment_applied() is True

    def test_weather_adjustment_flag_not_set_for_low_confidence(self):
        # Authority moved to predict_load: it refuses by returning a
        # "low_confidence" source, so the flag stays unset even though a coef
        # exists for the hour. (Previously a "low" hour label pre-gated this.)
        mock_entry = _create_mock_entry()
        weather = MagicMock()
        weather.get_coefficients_for_hour.return_value = MagicMock(confidence="low")
        weather.predict_load.return_value = (1.5, "low_confidence")

        forecaster = LoadForecaster(mock_entry, weather_correlation=weather)
        forecaster.reset_weather_adjustment_applied()

        forecaster.estimate_hourly_consumption_kw(
            hourly_avg_kw={11: 1.0},
            slot_hour=11,
            current_hour=10,
            current_load_kw=0.5,
            recent_load_kw=0.6,
            temperature=30.0,
        )

        assert forecaster.get_weather_adjustment_applied() is False

    def test_weather_adjustment_flag_not_set_when_no_temperature(self):
        mock_entry = _create_mock_entry()
        weather = MagicMock()
        weather.get_coefficients_for_hour.return_value = MagicMock(confidence="medium")

        forecaster = LoadForecaster(mock_entry, weather_correlation=weather)
        forecaster.reset_weather_adjustment_applied()

        forecaster.estimate_hourly_consumption_kw(
            hourly_avg_kw={11: 1.0},
            slot_hour=11,
            current_hour=10,
            current_load_kw=0.5,
            recent_load_kw=0.6,
            temperature=None,
        )

        assert forecaster.get_weather_adjustment_applied() is False

    def test_weather_adjustment_flag_not_set_for_invalid_source(self):
        mock_entry = _create_mock_entry()
        weather = MagicMock()
        weather.get_coefficients_for_hour.return_value = MagicMock(confidence="medium")
        weather.predict_load.return_value = (0.99, "no_coefficients")

        forecaster = LoadForecaster(mock_entry, weather_correlation=weather)
        forecaster.reset_weather_adjustment_applied()

        forecaster.estimate_hourly_consumption_kw(
            hourly_avg_kw={11: 1.0},
            slot_hour=11,
            current_hour=10,
            current_load_kw=0.5,
            recent_load_kw=0.6,
            temperature=30.0,
        )

        assert forecaster.get_weather_adjustment_applied() is False

    def test_weather_adjustment_flag_not_set_for_weather_none(self):
        mock_entry = _create_mock_entry()
        weather = MagicMock()
        weather.get_coefficients_for_hour.return_value = MagicMock(confidence="medium")
        weather.predict_load.return_value = (0.99, "weather_none")

        forecaster = LoadForecaster(mock_entry, weather_correlation=weather)
        forecaster.reset_weather_adjustment_applied()

        forecaster.estimate_hourly_consumption_kw(
            hourly_avg_kw={11: 1.0},
            slot_hour=11,
            current_hour=10,
            current_load_kw=0.5,
            recent_load_kw=0.6,
            temperature=21.0,
        )

        assert forecaster.get_weather_adjustment_applied() is False


def _full_profile(value: float) -> dict[int, float]:
    """Build a 24-hour profile with the same value at every hour."""
    return {h: value for h in range(24)}


class TestPerDayOfWeekProfiles:
    """Issue #679: per-day-of-week load profile resolution (7 buckets).

    Profiles are injected via ``set_daily_profiles`` (from HistoryFetcher) and
    resolved per ``(day_of_week, slot_hour)`` with a three-rung fallback chain:
    day-specific (>=4 samples) -> weekday/weekend aggregate (>=8 samples) ->
    global average (the caller-supplied ``hourly_avg_kw``).
    """

    def _profiles(
        self,
        daily_avg: dict[int, dict[int, float]] | None = None,
        daily_counts: dict[int, dict[int, int]] | None = None,
        weekday_avg: dict[int, float] | None = None,
        weekday_counts: dict[int, int] | None = None,
        weekend_avg: dict[int, float] | None = None,
        weekend_counts: dict[int, int] | None = None,
    ) -> LoadProfiles:
        daily_avg = daily_avg if daily_avg is not None else {}
        daily_counts = daily_counts if daily_counts is not None else {}
        return LoadProfiles(
            daily_avg=daily_avg,
            daily_counts=daily_counts,
            weekday_avg=weekday_avg or {},
            weekday_counts=weekday_counts or {},
            weekend_avg=weekend_avg or {},
            weekend_counts=weekend_counts or {},
        )

    def test_day_specific_profile_used_when_enough_samples(self):
        """4 samples at the hour -> the day profile wins over global average."""
        forecaster = _create_load_forecaster()
        forecaster.set_daily_profiles(
            self._profiles(
                daily_avg={2: {11: 2.0}},
                daily_counts={2: {11: MIN_SAMPLES_PER_DAY_HOUR}},
                weekday_avg=_full_profile(1.0),
                weekday_counts={h: 20 for h in range(24)},
            )
        )

        kw, source = forecaster.estimate_hourly_consumption_kw(
            hourly_avg_kw=_full_profile(0.5),
            slot_hour=11,
            current_hour=11,
            current_load_kw=0.0,
            recent_load_kw=0.0,
            day_of_week=2,
        )

        assert source == "profile_hour:day_2"
        assert kw == 2.0

    def test_day_specific_profile_below_threshold_falls_back_to_aggregate(self):
        """3 samples (<4) at the hour with a rich aggregate -> aggregate wins."""
        forecaster = _create_load_forecaster()
        forecaster.set_daily_profiles(
            self._profiles(
                daily_avg={2: {11: 2.0}},
                daily_counts={2: {11: MIN_SAMPLES_PER_DAY_HOUR - 1}},
                weekday_avg={11: 1.2},
                weekday_counts={11: MIN_SAMPLES_PER_AGGREGATE_HOUR},
            )
        )

        kw, source = forecaster.estimate_hourly_consumption_kw(
            hourly_avg_kw=_full_profile(0.5),
            slot_hour=11,
            current_hour=11,
            current_load_kw=0.0,
            recent_load_kw=0.0,
            day_of_week=2,
        )

        assert source == "profile_hour:aggregate_weekday"
        assert kw == 1.2

    def test_weekend_day_uses_weekend_aggregate(self):
        """day_of_week >= 5 resolves the weekend aggregate, not weekday."""
        forecaster = _create_load_forecaster()
        forecaster.set_daily_profiles(
            self._profiles(
                weekday_avg={11: 1.0},
                weekday_counts={11: 20},
                weekend_avg={11: 3.0},
                weekend_counts={11: MIN_SAMPLES_PER_AGGREGATE_HOUR},
            )
        )

        kw, source = forecaster.estimate_hourly_consumption_kw(
            hourly_avg_kw=_full_profile(0.5),
            slot_hour=11,
            current_hour=11,
            current_load_kw=0.0,
            recent_load_kw=0.0,
            day_of_week=6,
        )

        assert source == "profile_hour:aggregate_weekend"
        assert kw == 3.0

    def test_aggregate_below_threshold_falls_back_to_global_average(self):
        """3 day samples and 7 aggregate samples -> the global average rung."""
        forecaster = _create_load_forecaster()
        forecaster.set_daily_profiles(
            self._profiles(
                daily_avg={2: {11: 2.0}},
                daily_counts={2: {11: 3}},
                weekday_avg={11: 1.2},
                weekday_counts={11: MIN_SAMPLES_PER_AGGREGATE_HOUR - 1},
            )
        )

        kw, source = forecaster.estimate_hourly_consumption_kw(
            hourly_avg_kw=_full_profile(0.5),
            slot_hour=11,
            current_hour=11,
            current_load_kw=0.0,
            recent_load_kw=0.0,
            day_of_week=2,
        )

        # Global rung keeps the bare tag: no bucket was selected.
        assert source == "profile_hour"
        assert kw == 0.5

    def test_empty_day_bucket_falls_back_without_error(self):
        """A day absent from the injected profiles must not raise."""
        forecaster = _create_load_forecaster()
        forecaster.set_daily_profiles(
            self._profiles(
                weekday_avg={11: 1.0},
                weekday_counts={11: MIN_SAMPLES_PER_AGGREGATE_HOUR},
            )
        )

        kw, source = forecaster.estimate_hourly_consumption_kw(
            hourly_avg_kw=_full_profile(0.5),
            slot_hour=11,
            current_hour=11,
            current_load_kw=0.0,
            recent_load_kw=0.0,
            day_of_week=4,
        )

        assert source == "profile_hour:aggregate_weekday"
        assert kw == 1.0

    def test_all_buckets_empty_uses_global_average(self):
        """Fully empty profiles behave exactly like no injection at all."""
        forecaster = _create_load_forecaster()
        forecaster.set_daily_profiles(self._profiles())

        kw, source = forecaster.estimate_hourly_consumption_kw(
            hourly_avg_kw=_full_profile(0.5),
            slot_hour=11,
            current_hour=11,
            current_load_kw=0.0,
            recent_load_kw=0.0,
            day_of_week=1,
        )

        assert source == "profile_hour"
        assert kw == 0.5

    def test_uninjected_forecaster_is_unchanged(self):
        """Without set_daily_profiles, the forecaster must be byte-identical to today."""
        combined = _full_profile(0.6)
        kwargs = dict(
            hourly_avg_kw=combined,
            slot_hour=11,
            current_hour=11,
            current_load_kw=0.0,
            recent_load_kw=0.0,
            day_of_week=2,
        )

        plain = _create_load_forecaster().estimate_hourly_consumption_kw(**kwargs)
        unaware = _create_load_forecaster()
        unaware.set_daily_profiles(None)

        assert unaware.estimate_hourly_consumption_kw(**kwargs) == plain
        assert plain == (0.6, "profile_hour")

    def test_resolution_is_per_hour_not_per_profile(self):
        """A day bucket can qualify at 18:00 and be thin at 03:00; each hour resolves alone."""
        forecaster = _create_load_forecaster()
        forecaster.set_daily_profiles(
            self._profiles(
                daily_avg={2: {18: 2.0, 3: 9.0}},
                daily_counts={2: {18: MIN_SAMPLES_PER_DAY_HOUR, 3: 1}},
                weekday_avg={3: 1.1, 18: 1.5},
                weekday_counts={3: MIN_SAMPLES_PER_AGGREGATE_HOUR, 18: 20},
            )
        )

        base = dict(
            hourly_avg_kw=_full_profile(0.5),
            current_hour=11,
            current_load_kw=0.0,
            recent_load_kw=0.0,
            day_of_week=2,
        )

        evening_kw, evening_source = forecaster.estimate_hourly_consumption_kw(
            slot_hour=18, **base
        )
        night_kw, night_source = forecaster.estimate_hourly_consumption_kw(
            slot_hour=3, **base
        )

        assert evening_source == "profile_hour:day_2"
        assert evening_kw == 2.0
        assert night_source == "profile_hour:aggregate_weekday"
        assert night_kw == 1.1

    def test_same_hour_different_days_resolve_independently(self):
        """No cross-day contamination: Monday and Sunday at 11:00 differ."""
        forecaster = _create_load_forecaster()
        forecaster.set_daily_profiles(
            self._profiles(
                daily_avg={0: {11: 0.7}, 6: {11: 2.1}},
                daily_counts={0: {11: MIN_SAMPLES_PER_DAY_HOUR}, 6: {11: 10}},
            )
        )

        base = dict(
            hourly_avg_kw=_full_profile(0.5),
            slot_hour=11,
            current_hour=11,
            current_load_kw=0.0,
            recent_load_kw=0.0,
        )

        monday_kw, monday_source = forecaster.estimate_hourly_consumption_kw(
            day_of_week=0, **base
        )
        sunday_kw, sunday_source = forecaster.estimate_hourly_consumption_kw(
            day_of_week=6, **base
        )

        assert (monday_kw, monday_source) == (0.7, "profile_hour:day_0")
        assert (sunday_kw, sunday_source) == (2.1, "profile_hour:day_6")

    def test_day_of_week_none_uses_global_average(self):
        """A caller that passes no day_of_week must get the plain profile."""
        forecaster = _create_load_forecaster()
        forecaster.set_daily_profiles(
            self._profiles(
                daily_avg={2: {11: 2.0}},
                daily_counts={2: {11: MIN_SAMPLES_PER_DAY_HOUR}},
            )
        )

        kw, source = forecaster.estimate_hourly_consumption_kw(
            hourly_avg_kw=_full_profile(0.5),
            slot_hour=11,
            current_hour=11,
            current_load_kw=0.0,
            recent_load_kw=0.0,
        )

        assert source == "profile_hour"
        assert kw == 0.5

    def test_ceiling_derived_from_resolved_profile(self):
        """Issue #826 ceiling must follow the *resolved* bucket, not the global average.

        A day profile whose peak is higher than the global peak would otherwise be
        clamped by a ceiling derived from the wrong bucket.
        """
        forecaster = _create_load_forecaster()
        forecaster.set_daily_profiles(
            self._profiles(
                # Monday peaks at 5.0 kW -> ceiling 15.0 kW
                daily_avg={0: {12: 5.0, 13: 4.0}},
                daily_counts={0: {12: MIN_SAMPLES_PER_DAY_HOUR, 13: 10}},
            )
        )

        # 12.0 kW forecast is above the global ceiling (0.5*3 = 1.5) but below
        # the Monday ceiling (5.0*3 = 15.0), so it must NOT be clamped.
        kw, _ = forecaster.estimate_hourly_consumption_kw(
            hourly_avg_kw=_full_profile(0.5),
            slot_hour=13,
            current_hour=13,
            current_load_kw=12.0,
            recent_load_kw=0.0,
            hours_ahead=0.0,
            day_of_week=0,
        )

        assert kw == pytest.approx(12.0)

    def test_ceiling_still_applies_from_resolved_profile(self):
        """The ceiling guardrail still fires when the resolved profile is exceeded."""
        forecaster = _create_load_forecaster()
        forecaster.set_daily_profiles(
            self._profiles(
                daily_avg={0: {12: 2.0}},
                daily_counts={0: {12: MIN_SAMPLES_PER_DAY_HOUR}},
            )
        )

        kw, _ = forecaster.estimate_hourly_consumption_kw(
            hourly_avg_kw=_full_profile(0.5),
            slot_hour=12,
            current_hour=12,
            current_load_kw=50.0,
            recent_load_kw=0.0,
            hours_ahead=0.0,
            day_of_week=0,
        )

        # Monday ceiling = 2.0 * 3.0 = 6.0
        assert kw == pytest.approx(6.0)

    def test_ceiling_survives_a_thin_peak_hour(self):
        """The #826 ceiling must not collapse when the *peak* hour is the thin one.

        Regression for the second review finding: a resolved profile that omits
        a thin hour instead of backfilling it from the global average leaves
        max(resolved.values()) blind to that hour's true peak whenever it is
        also the home's highest-consumption hour. In the live repro, one
        missing recorder sample dropped Saturday 18:00 below both the
        day-specific and aggregate thresholds; the ceiling then collapsed from
        12.0 kW (correct, 4.0 kW peak * 3) to 1.5 kW (wrong, some other thin
        hour's value * 3) and clamped every Saturday slot -- not just the
        dropped hour -- with a spurious #826 warning. Backfilling the thin
        peak hour from the combined profile keeps the resolved dict's max
        equal to the combined profile's true peak, so the ceiling stays 12.0
        and a genuine 20.0 kW live overshoot is clamped to that correct value,
        not to a wrong, much lower one.
        """
        forecaster = _create_load_forecaster()
        forecaster.set_daily_profiles(
            self._profiles(
                # Both rungs thin at the peak hour -> falls through to global.
                daily_avg={5: {18: 4.0}},
                daily_counts={5: {18: 3}},  # < MIN_SAMPLES_PER_DAY_HOUR (4)
                weekend_avg={18: 4.0},
                weekend_counts={18: 7},  # < MIN_SAMPLES_PER_AGGREGATE_HOUR (8)
            )
        )
        combined = _full_profile(0.5)
        combined[18] = 4.0  # the home's true peak, held by the combined profile

        kw, source = forecaster.estimate_hourly_consumption_kw(
            hourly_avg_kw=combined,
            slot_hour=18,
            current_hour=18,
            current_load_kw=20.0,
            recent_load_kw=0.0,
            hours_ahead=0.0,
            day_of_week=5,  # Saturday -> weekend aggregate
        )

        # Ceiling = combined peak (4.0) * LOAD_FORECAST_CEILING_FACTOR (3.0).
        assert kw == pytest.approx(12.0)
        assert source == "live_load"

    def test_ceiling_never_tightens_below_the_combined_peak(self):
        """A qualifying-but-lower day bucket must not shrink the #826 ceiling.

        Review finding on the first cut: the ceiling was derived from the
        *resolved* profile alone, so a quiet day bucket that qualified at
        exactly ``MIN_SAMPLES_PER_DAY_HOUR`` samples (a 4-sample mean) could
        lower the household peak the ceiling is scaled from and clamp real,
        present-tense *measured* load. Repro: the combined 28-day profile
        peaks at 4.0 kW (ceiling 12.0 kW) while Sunday's day profile is a flat
        1.0 kW (which alone would imply a 3.0 kW ceiling). A Sunday afternoon
        drawing a genuine 8.0 kW live+recent load must forecast 8.0 kW, not
        3.0 kW — that 62% under-forecast of measured load is exactly the
        DW-undercharge failure family (#870/#886/#903). The ceiling therefore
        takes the max over the resolved *and* combined profiles, so a day
        bucket can only ever loosen the guardrail, never tighten it.
        """
        forecaster = _create_load_forecaster()
        forecaster.set_daily_profiles(
            self._profiles(
                # Sunday qualifies at exactly the day-specific threshold...
                daily_avg={6: _full_profile(1.0)},
                daily_counts={6: {h: MIN_SAMPLES_PER_DAY_HOUR for h in range(24)}},
            )
        )
        combined = _full_profile(0.5)
        combined[15] = 4.0  # ...but the home's true peak lives in the combined profile

        base = dict(
            hourly_avg_kw=combined,
            slot_hour=15,
            current_hour=15,
            recent_load_kw=8.0,
            hours_ahead=0.0,
            day_of_week=6,
        )

        kw, source = forecaster.estimate_hourly_consumption_kw(
            current_load_kw=8.0, **base
        )
        # blended_live = 0.3*8.0 + 0.7*8.0 = 8.0; must NOT be clamped to the
        # day-bucket ceiling (1.0*3 = 3.0) — the combined peak keeps the
        # ceiling at 12.0 kW, above the load.
        assert source == "blended_live:day_6"
        assert kw == pytest.approx(8.0)

        # The guardrail itself still fires, at the *combined*-derived ceiling
        # (4.0 * 3.0 = 12.0), not the day-bucket one.
        kw_clamped, _ = forecaster.estimate_hourly_consumption_kw(
            current_load_kw=50.0, **base
        )
        assert kw_clamped == pytest.approx(12.0)

    def test_thin_hour_falls_back_to_global_average_not_flat_mean(self):
        """A day/aggregate-thin hour must use the global average, not the flat-mean fallback.

        Regression for a review finding on the first cut of _resolve_daily_profile:
        an hour that cleared neither the day-specific nor the aggregate threshold
        was omitted from the resolved dict entirely (not backfilled from the
        combined profile), so ``_get_historical`` read it as 0.0 / "no historical
        data" and the estimate fell all the way through to
        ``_fallback_to_available_data``'s flat mean of whatever hours *did*
        qualify -- silently replacing a genuine peak with a much lower number
        (reproduced live: a single missing recorder sample thinned a Saturday
        18:00 peak of 4.0 kW down to a 0.5 kW flat-mean fallback). Hour 15 here
        has no day-specific or aggregate data at all, so it must resolve to the
        combined profile's value for that hour, tagged with the bare
        "profile_hour" source (the global rung keeps no bucket suffix).
        """
        forecaster = _create_load_forecaster()
        forecaster.set_daily_profiles(
            self._profiles(
                daily_avg={2: {8: 0.4, 9: 0.6}},
                daily_counts={2: {8: MIN_SAMPLES_PER_DAY_HOUR, 9: 10}},
            )
        )

        kw, source = forecaster.estimate_hourly_consumption_kw(
            hourly_avg_kw=_full_profile(5.0),
            slot_hour=15,
            current_hour=15,
            current_load_kw=0.0,
            recent_load_kw=0.0,
            day_of_week=2,
        )

        assert source == "profile_hour"
        assert kw == 5.0

    def test_decay_branch_keeps_bucket_suffix(self):
        """The bucket suffix composes with the blend axis (decay_load_d1 etc.)."""
        forecaster = _create_load_forecaster()
        forecaster.set_daily_profiles(
            self._profiles(
                daily_avg={2: {10: 0.9}},
                daily_counts={2: {10: MIN_SAMPLES_PER_DAY_HOUR}},
            )
        )

        kw, source = forecaster.estimate_hourly_consumption_kw(
            hourly_avg_kw=_full_profile(0.5),
            slot_hour=10,
            current_hour=11,
            current_load_kw=0.45,
            recent_load_kw=0.5,
            day_of_week=2,
        )

        assert source == "decay_load_d1:day_2"
        # hour_distance=1 -> live_weight decays by DEFAULT_LOAD_DECAY_FACTOR**1,
        # per the existing (unchanged) _apply_exponential_decay formula.
        live_weight = DEFAULT_LOAD_INITIAL_WEIGHT * (DEFAULT_LOAD_DECAY_FACTOR**1)
        assert kw == pytest.approx(
            (live_weight * 0.5) + ((1.0 - live_weight) * 0.9),
            abs=0.001,
        )

    def test_profile_bucket_counts_exposed_for_diagnostics(self):
        """The forecaster tallies which rung won, per bucket tag."""
        forecaster = _create_load_forecaster()
        forecaster.set_daily_profiles(
            self._profiles(
                daily_avg={2: {11: 2.0, 12: 2.0}},
                daily_counts={2: {11: MIN_SAMPLES_PER_DAY_HOUR, 12: 1}},
                weekday_avg={12: 1.2},
                weekday_counts={12: MIN_SAMPLES_PER_AGGREGATE_HOUR},
            )
        )

        base = dict(
            hourly_avg_kw=_full_profile(0.5),
            current_hour=11,
            current_load_kw=0.0,
            recent_load_kw=0.0,
            day_of_week=2,
        )
        forecaster.estimate_hourly_consumption_kw(slot_hour=11, **base)
        forecaster.estimate_hourly_consumption_kw(slot_hour=12, **base)

        counts = forecaster.get_profile_bucket_counts()
        assert counts["day_2"] == 1
        assert counts["aggregate_weekday"] == 1
        assert "global" not in counts

    def test_profile_bucket_counts_reset_each_call(self):
        """Counts describe the most recent estimate call, not a cumulative total."""
        forecaster = _create_load_forecaster()
        forecaster.set_daily_profiles(
            self._profiles(
                daily_avg={2: {11: 2.0}},
                daily_counts={2: {11: MIN_SAMPLES_PER_DAY_HOUR}},
            )
        )

        base = dict(
            hourly_avg_kw=_full_profile(0.5),
            slot_hour=11,
            current_hour=11,
            current_load_kw=0.0,
            recent_load_kw=0.0,
            day_of_week=2,
        )
        forecaster.estimate_hourly_consumption_kw(**base)
        forecaster.estimate_hourly_consumption_kw(**base)

        # Hour 11 resolves via the day bucket; the other 23 hours of the day
        # have no day-specific or aggregate data at all, so they backfill
        # from the global average (_full_profile covers all 24 hours).
        assert forecaster.get_profile_bucket_counts() == {
            "day_2": 1,
            "global_avg": 23,
        }

    def test_profile_resolution_is_the_final_stage(self):
        """Ordering: profile -> weather. #970 removed the context-correction
        stage that used to run after this; profile resolution's own output
        is now the final result, unmodified."""
        forecaster = _create_load_forecaster()
        forecaster.set_daily_profiles(
            self._profiles(
                daily_avg={1: {11: 1.0}},
                daily_counts={1: {11: MIN_SAMPLES_PER_DAY_HOUR}},
            )
        )

        kw, source = forecaster.estimate_hourly_consumption_kw(
            hourly_avg_kw=_full_profile(0.5),
            slot_hour=11,
            current_hour=11,
            current_load_kw=0.0,
            recent_load_kw=0.0,
            day_of_week=1,
        )

        assert source == "profile_hour:day_1"
        assert kw == 1.0

    def test_profiles_with_non_numeric_entry_are_ignored(self):
        """A malformed injected profile must not raise; the rung is skipped."""
        forecaster = _create_load_forecaster()
        profiles = self._profiles(
            daily_avg={2: {11: 2.0}},
            daily_counts={2: {11: MIN_SAMPLES_PER_DAY_HOUR}},
        )
        # Corrupt the day profile with a non-numeric entry for the target hour.
        profiles.daily_avg[2][11] = "oops"  # type: ignore[assignment]
        forecaster.set_daily_profiles(profiles)

        kw, source = forecaster.estimate_hourly_consumption_kw(
            hourly_avg_kw=_full_profile(0.5),
            slot_hour=11,
            current_hour=11,
            current_load_kw=0.0,
            recent_load_kw=0.0,
            day_of_week=2,
        )

        assert source == "profile_hour"
        assert kw == 0.5
