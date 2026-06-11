"""Unit tests for LoadForecaster with exponential decay algorithm (Issue #381, #441)."""

from unittest.mock import MagicMock

import pytest

from custom_components.localshift.const import (
    DEFAULT_LOAD_DECAY_FACTOR,
    DEFAULT_LOAD_INITIAL_WEIGHT,
)
from custom_components.localshift.forecast.corrections import ForecastCorrectionProvider
from custom_components.localshift.forecast.load import (
    LoadForecaster,
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

    def test_adaptive_bias_applied(self, mock_adaptive_params):
        """Test consumption_forecast_bias adaptive parameter is applied.

        Positive bias should increase forecast, negative should decrease.
        """
        mock_entry = _create_mock_entry()
        forecaster = LoadForecaster(mock_entry)
        forecaster.set_adaptive_params(mock_adaptive_params)
        hourly_avg = {10: 0.5, 11: 0.6, 12: 0.7}

        kw, _ = forecaster.estimate_hourly_consumption_kw(
            hourly_avg_kw=hourly_avg,
            slot_hour=10,
            current_hour=11,
            current_load_kw=0.45,
            recent_load_kw=0.5,
        )

        # With bias of +0.1, expected: decay_weighted_result + 0.1
        expected_live_weight = DEFAULT_LOAD_INITIAL_WEIGHT * (
            DEFAULT_LOAD_DECAY_FACTOR**1
        )
        expected_historical_weight = 1.0 - expected_live_weight
        base_kw = (expected_live_weight * 0.5) + (expected_historical_weight * 0.5)
        expected_kw = round(max(0.0, base_kw + 0.1), 3)

        assert abs(kw - expected_kw) < 0.001

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

    def test_context_correction_applies_when_context_provided(self):
        forecaster = _create_load_forecaster()
        corrections = ForecastCorrectionProvider(min_samples=1)
        corrections.record_error(2.0, 1.0, 1, 11, "summer")
        forecaster.set_forecast_corrections(corrections)

        kw, source = forecaster.estimate_hourly_consumption_kw(
            hourly_avg_kw={11: 0.6},
            slot_hour=11,
            current_hour=11,
            current_load_kw=1.0,
            recent_load_kw=1.0,
            day_of_week=1,
            season="summer",
        )

        assert source == "blended_live"
        assert kw == 1.5

    def test_context_correction_runs_after_global_bias(self):
        forecaster = _create_load_forecaster()
        adaptive = MagicMock()
        adaptive.get.return_value = 0.2
        forecaster.set_adaptive_params(adaptive)

        corrections = ForecastCorrectionProvider(min_samples=1)
        corrections.record_error(2.0, 1.0, 2, 11, "winter")
        forecaster.set_forecast_corrections(corrections)

        kw, _ = forecaster.estimate_hourly_consumption_kw(
            hourly_avg_kw={11: 0.6},
            slot_hour=11,
            current_hour=11,
            current_load_kw=1.0,
            recent_load_kw=1.0,
            day_of_week=2,
            season="winter",
        )

        assert kw == 1.8

    def test_context_correction_ignored_without_context_parameters(self):
        forecaster = _create_load_forecaster()
        corrections = ForecastCorrectionProvider(min_samples=1)
        corrections.record_error(2.0, 1.0, 4, 11, "spring")
        forecaster.set_forecast_corrections(corrections)

        kw, source = forecaster.estimate_hourly_consumption_kw(
            hourly_avg_kw={11: 0.6},
            slot_hour=11,
            current_hour=11,
            current_load_kw=1.0,
            recent_load_kw=1.0,
        )

        assert source == "blended_live"
        assert kw == 1.0

    def test_context_correction_neutral_factor_returns_original_load(self):
        forecaster = _create_load_forecaster()
        corrections = ForecastCorrectionProvider(min_samples=1)
        corrections.record_error(1.0, 1.0, 6, 11, "autumn")
        forecaster.set_forecast_corrections(corrections)

        kw, _ = forecaster.estimate_hourly_consumption_kw(
            hourly_avg_kw={11: 0.6},
            slot_hour=11,
            current_hour=11,
            current_load_kw=1.0,
            recent_load_kw=1.0,
            day_of_week=6,
            season="autumn",
        )

        assert kw == 1.0

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


class TestLoadForecasterSpikeGuardrail:
    """Issue #826: physical sanity bounds reject transient load spikes."""

    def test_instantaneous_spike_clamped_to_recent_multiple(self):
        """A 20.671 kW spike with 2.587 kW recent avg must not inflate the forecast."""
        forecaster = _create_load_forecaster()
        # current clamped to recent*4 = 10.348; blend = 0.3*10.348 + 0.7*2.587 = 4.915
        kw, _ = forecaster.estimate_hourly_consumption_kw(
            hourly_avg_kw={12: 2.399},
            slot_hour=12,
            current_hour=12,
            current_load_kw=20.671,
            recent_load_kw=2.587,
            hours_ahead=0.0,
        )
        assert kw < 6.0  # far below the 20.671 inflation
        assert kw == pytest.approx(4.915, abs=0.05)

    def test_normal_load_not_clamped(self):
        """Normal load within 4x of recent avg is unaffected by the guardrail."""
        forecaster = _create_load_forecaster()
        # current 3.0 < recent*4 = 10.0, so blend = 0.3*3.0 + 0.7*2.5 = 2.65
        kw, _ = forecaster.estimate_hourly_consumption_kw(
            hourly_avg_kw={12: 2.5},
            slot_hour=12,
            current_hour=12,
            current_load_kw=3.0,
            recent_load_kw=2.5,
            hours_ahead=0.0,
        )
        assert kw == pytest.approx(2.65, abs=0.01)

    def test_spike_with_no_recent_avg_clamped_to_absolute_ceiling(self):
        """With no recent rolling avg, a spike is clamped to the absolute ceiling."""
        forecaster = _create_load_forecaster()
        # recent=0 -> live_load path returns clamped current = MAX_PLAUSIBLE_LOAD_KW (15.0)
        kw, source = forecaster.estimate_hourly_consumption_kw(
            hourly_avg_kw={},
            slot_hour=12,
            current_hour=12,
            current_load_kw=100.0,
            recent_load_kw=0.0,
            hours_ahead=0.0,
        )
        assert kw <= 15.0

    def test_final_output_never_exceeds_ceiling(self):
        """No forecast path may emit a value above MAX_PLAUSIBLE_LOAD_KW."""
        forecaster = _create_load_forecaster()
        kw, _ = forecaster.estimate_hourly_consumption_kw(
            hourly_avg_kw={12: 50.0},  # absurd historical to probe the output ceiling
            slot_hour=12,
            current_hour=None,  # skip blending -> falls back to historical/profile
            current_load_kw=0.0,
            recent_load_kw=0.0,
            hours_ahead=None,
        )
        assert kw <= 15.0


@pytest.fixture
def mock_weather_correlation():
    """Mock WeatherCorrelation for testing."""
    mock = MagicMock()
    return mock


@pytest.fixture
def mock_adaptive_params():
    """Mock AdaptiveParameters with consumption_forecast_bias."""
    mock = MagicMock()
    mock.get.return_value = 0.1  # +0.1 bias
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
