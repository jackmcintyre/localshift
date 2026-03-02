"""Unit tests for LoadForecaster with exponential decay algorithm (Issue #381, #441)."""

from unittest.mock import MagicMock

import pytest

from custom_components.localshift.computation_engine_lib.load_forecaster import (
    LoadForecaster,
)
from custom_components.localshift.const import (
    DEFAULT_LOAD_DECAY_FACTOR,
    DEFAULT_LOAD_INITIAL_WEIGHT,
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

    def test_current_hour_uses_live_load(self):
        """Test hour_distance == 0 uses live load directly.

        When slot_hour == current_hour and current_load_kw > 0,
        should return current_load_kw with source "live_load".
        """
        forecaster = _create_load_forecaster()
        hourly_avg = {10: 0.5, 11: 0.6, 12: 0.7}

        kw, source = forecaster.estimate_hourly_consumption_kw(
            hourly_avg_kw=hourly_avg,
            slot_hour=11,
            current_hour=11,
            current_load_kw=0.45,
            recent_load_kw=0.5,
        )

        assert source == "live_load"
        assert kw == 0.45

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

    def test_no_historical_no_live_fallback(self):
        """Test when no historical and no live load, uses 0.6 default."""
        forecaster = _create_load_forecaster()

        kw, source = forecaster.estimate_hourly_consumption_kw(
            hourly_avg_kw={},
            slot_hour=10,
            current_hour=11,
            current_load_kw=0.0,
            recent_load_kw=0.0,
        )

        assert source == "live_load_fallback"
        assert kw == 0.6

    def test_weather_correlation_applied(self, mock_weather_correlation):
        """Test weather correlation adjustment when confidence is high.

        When temperature is provided and weather correlation has high confidence,
        should apply temperature-based adjustment.
        """
        mock_entry = _create_mock_entry()
        mock_weather_correlation.get_coefficients_for_hour.return_value = MagicMock(
            confidence="high"
        )
        mock_weather_correlation.predict_load.return_value = (0.75, "weather_adjusted")

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

        assert source == "weather_adjusted"
        assert kw == 0.75

    def test_weather_correlation_skipped_low_confidence(self, mock_weather_correlation):
        """Test weather correlation skipped when confidence is low."""
        mock_entry = _create_mock_entry()
        mock_weather_correlation.get_coefficients_for_hour.return_value = MagicMock(
            confidence="low"
        )

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

        assert source == "live_load"
        assert kw == 0.45  # No weather adjustment

    def test_adaptive_bias_applied(self, mock_adaptive_params):
        """Test consumption_forecast_bias adaptive parameter is applied.

        Positive bias should increase forecast, negative should decrease.
        """
        mock_entry = _create_mock_entry()
        forecaster = LoadForecaster(mock_entry)
        forecaster.set_adaptive_params(mock_adaptive_params)
        hourly_avg = {10: 0.5, 11: 0.6, 12: 0.7}

        kw, source = forecaster.estimate_hourly_consumption_kw(
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

    def test_matches_forecast_computer_algorithm(self, mock_entry, mock_get_entity_id):
        """Test LoadForecaster and ForecastComputer._estimate_hourly_consumption_kw return identical values.

        Parametrized test for hour_distance 0-5 to ensure both implementations
        use the same exponential decay algorithm.
        """
        from custom_components.localshift.computation_engine_lib.forecast_computer import (
            ForecastComputer,
        )

        forecaster = _create_load_forecaster()
        computer = ForecastComputer(mock_entry, mock_get_entity_id, lambda x: {})

        hourly_avg = {8: 0.4, 9: 0.5, 10: 0.6, 11: 0.7, 12: 0.8}
        current_hour = 10
        current_load = 0.55
        recent_load = 0.6

        for slot_hour in range(5, 16):
            kw_forecaster, source_forecaster = (
                forecaster.estimate_hourly_consumption_kw(
                    hourly_avg_kw=hourly_avg,
                    slot_hour=slot_hour,
                    current_hour=current_hour,
                    current_load_kw=current_load,
                    recent_load_kw=recent_load,
                )
            )

            kw_computer, source_computer = computer._estimate_hourly_consumption_kw(
                hourly_avg,
                slot_hour,
                current_hour,
                current_load,
                recent_load,
            )

            assert abs(kw_forecaster - kw_computer) < 0.001, (
                f"Mismatch at hour {slot_hour}: LoadForecaster={kw_forecaster}, "
                f"ForecastComputer={kw_computer}"
            )


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
