"""Tests for learning/correlation.py - weather correlation regression."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.localshift.const import (
    CONF_COOLING_THRESHOLD,
    CONF_HEATING_THRESHOLD,
    CONF_WEATHER_ENTITY,
)
import custom_components.localshift.learning.correlation as correlation_module
from custom_components.localshift.learning.correlation import (
    DailySnapshot,
    HourlyRegressionData,
    HourlyRegressionResult,
    WeatherCorrelation,
    WeatherCorrelationData,
    ZoneStats,
)


def _linear_stats(n: int, slope: float) -> ZoneStats:
    sum_x = n * (n + 1) / 2
    sum_xx = n * (n + 1) * (2 * n + 1) / 6
    sum_y = slope * sum_x
    sum_xy = slope * sum_xx
    sum_yy = slope * slope * sum_xx
    return ZoneStats(
        n=n,
        sum_x=float(sum_x),
        sum_y=float(sum_y),
        sum_xx=float(sum_xx),
        sum_xy=float(sum_xy),
        sum_yy=float(sum_yy),
    )


class TestZoneStats:
    def test_roundtrip(self):
        stats = ZoneStats(n=2, sum_x=3.0, sum_y=4.0, sum_xx=5.0, sum_xy=6.0, sum_yy=7.0)
        restored = ZoneStats.from_dict(stats.to_dict())
        assert restored == stats


class TestHourlyRegressionData:
    def test_roundtrip(self):
        data = HourlyRegressionData(
            mild=ZoneStats(n=1, sum_y=2.0),
            heating=ZoneStats(n=2, sum_x=3.0, sum_y=4.0),
            cooling=ZoneStats(n=3, sum_x=4.0, sum_y=5.0),
        )
        restored = HourlyRegressionData.from_dict(data.to_dict())
        assert restored.mild.n == 1
        assert restored.heating.sum_x == 3.0
        assert restored.cooling.sum_y == 5.0


class TestDailySnapshot:
    def test_roundtrip(self):
        snapshot = DailySnapshot(
            date_key="2026-03-26",
            data=HourlyRegressionData(mild=ZoneStats(n=1, sum_y=2.0)),
        )
        restored = DailySnapshot.from_dict(snapshot.to_dict())
        assert restored.date_key == "2026-03-26"
        assert restored.data.mild.sum_y == 2.0


class TestHourlyRegressionResult:
    def test_to_dict(self):
        result = HourlyRegressionResult(
            base_load_kw=1.2,
            heating_slope=0.4,
            cooling_slope=0.3,
            r_squared=0.5,
            sample_count=40,
            confidence="medium",
        )
        d = result.to_dict()
        assert d["base_load_kw"] == 1.2
        assert d["r_squared"] == 0.5
        assert d["confidence"] == "medium"


class TestRegressionMath:
    def test_fit_zone_regression_returns_expected_slope_and_r_squared(self):
        stats = ZoneStats(
            n=3,
            sum_x=6.0,
            sum_y=12.0,
            sum_xx=14.0,
            sum_xy=28.0,
            sum_yy=56.0,
        )
        fit_fn = getattr(correlation_module, "_fit_zone_regression", None)
        assert callable(fit_fn)
        slope, r_squared = fit_fn(stats)
        assert slope == pytest.approx(2.0)
        assert r_squared == pytest.approx(1.0)

    def test_fit_zone_regression_handles_insufficient_stats(self):
        fit_fn = getattr(correlation_module, "_fit_zone_regression", None)
        assert callable(fit_fn)
        slope, r_squared = fit_fn(ZoneStats(n=1, sum_xx=0.0))
        assert slope == 0.0
        assert r_squared == 0.0

    def test_fit_zone_regression_handles_zero_variance(self):
        fit_fn = getattr(correlation_module, "_fit_zone_regression", None)
        assert callable(fit_fn)
        stats = ZoneStats(n=2, sum_x=2.0, sum_y=2.0, sum_xx=2.0, sum_xy=2.0, sum_yy=0.0)
        slope, r_squared = fit_fn(stats)
        assert slope == pytest.approx(1.0)
        assert r_squared == 0.0


class TestWeatherCorrelationData:
    def test_roundtrip(self):
        snapshot = DailySnapshot(
            date_key="2026-03-26",
            data=HourlyRegressionData(mild=ZoneStats(n=2, sum_y=5.0)),
        )
        original = WeatherCorrelationData(
            weather_entity_id="weather.home",
            daily_regression_stats={12: [snapshot]},
        )
        restored = WeatherCorrelationData.from_dict(original.to_dict())
        assert restored.weather_entity_id == "weather.home"
        assert 12 in restored.daily_regression_stats
        assert restored.daily_regression_stats[12][0].data.mild.sum_y == 5.0

    def test_temperature_history_roundtrip(self):
        data = WeatherCorrelationData()
        data.temperature_history["2024-01-01"] = 22.0
        restored = WeatherCorrelationData.from_dict(data.to_dict())
        assert restored.temperature_history == {"2024-01-01": 22.0}

    def test_no_hourly_coefficients_field(self):
        data = WeatherCorrelationData()
        assert not hasattr(data, "hourly_coefficients")


class TestAsyncInitialize:
    @pytest.mark.asyncio
    async def test_initialize_from_storage(self, correlation):
        stored = WeatherCorrelationData(
            weather_entity_id="weather.stored",
            cooling_threshold=25.0,
            heating_threshold=17.0,
        )
        stored.daily_regression_stats[10] = [
            DailySnapshot(
                date_key="2026-03-26",
                data=HourlyRegressionData(mild=ZoneStats(n=1, sum_y=2.0)),
            )
        ]
        correlation._store.async_load.return_value = stored.to_dict()

        await correlation.async_initialize()

        assert correlation._initialized is True
        assert correlation._data.weather_entity_id == "weather.stored"
        assert 10 in correlation._data.daily_regression_stats

    @pytest.mark.asyncio
    async def test_initialize_fresh(self, correlation, mock_entry):
        correlation._store.async_load.return_value = None
        mock_entry.options = {
            CONF_WEATHER_ENTITY: "weather.new",
            CONF_COOLING_THRESHOLD: 26.0,
            CONF_HEATING_THRESHOLD: 16.0,
        }
        mock_entry.data = {}

        await correlation.async_initialize()

        assert correlation._initialized is True
        assert correlation._data.weather_entity_id == "weather.new"
        assert correlation._data.cooling_threshold == 26.0
        assert correlation._data.heating_threshold == 16.0

    @pytest.mark.asyncio
    async def test_initialize_idempotent(self, correlation):
        correlation._store.async_load.return_value = None
        await correlation.async_initialize()
        await correlation.async_initialize()
        assert correlation._store.async_load.call_count == 1

    @pytest.mark.asyncio
    async def test_initialize_falls_back_to_data(self, correlation, mock_entry):
        correlation._store.async_load.return_value = None
        mock_entry.options = {}
        mock_entry.data = {CONF_WEATHER_ENTITY: "weather.fallback"}

        await correlation.async_initialize()

        assert correlation._data.weather_entity_id == "weather.fallback"


class TestAsyncSave:
    @pytest.mark.asyncio
    async def test_save_calls_store(self, correlation):
        correlation._data.weather_entity_id = "weather.test"
        await correlation.async_save()

        correlation._store.async_save.assert_called_once()
        saved = correlation._store.async_save.call_args[0][0]
        assert saved["weather_entity_id"] == "weather.test"


class TestLearningAndPrediction:
    def test_learn_ignores_invalid_hour(self, correlation):
        correlation.learn_from_sample(-1, 25.0, 2.0)
        assert correlation._data.daily_regression_stats == {}

    def test_learn_records_mild_zone_stats(self, correlation):
        now = datetime.now(UTC)
        with patch(
            "custom_components.localshift.learning.correlation.dt_util.now",
            return_value=now,
        ):
            correlation.learn_from_sample(12, 21.0, 3.0)

        snapshot = correlation._data.daily_regression_stats[12][0]
        assert snapshot.data.mild.n == 1
        assert snapshot.data.mild.sum_y == 3.0

    def test_learn_skips_small_temp_delta(self, correlation):
        now = datetime.now(UTC)
        with patch(
            "custom_components.localshift.learning.correlation.dt_util.now",
            return_value=now,
        ):
            correlation.learn_from_sample(8, 24.5, 4.0)

        snapshot = correlation._data.daily_regression_stats[8][0]
        assert snapshot.data.cooling.n == 0

    def test_learn_records_cooling_zone_stats(self, correlation):
        now = datetime.now(UTC)
        with patch(
            "custom_components.localshift.learning.correlation.dt_util.now",
            return_value=now,
        ):
            correlation.learn_from_sample(8, 26.0, 4.0)

        snapshot = correlation._data.daily_regression_stats[8][0]
        assert snapshot.data.cooling.n == 1

    def test_predict_load_returns_weather_none_for_mild_zone(self, correlation):
        now = datetime.now(UTC)
        with patch(
            "custom_components.localshift.learning.correlation.dt_util.now",
            return_value=now,
        ):
            min_samples = getattr(correlation_module, "MIN_SAMPLES_PER_ZONE", None)
            assert min_samples is not None
            for _ in range(min_samples):
                correlation.learn_from_sample(10, 21.0, 0.72)

        predicted, reason = correlation.predict_load(10, 21.0, 0.72)
        assert predicted == pytest.approx(0.72)
        assert reason == "weather_none"

    def test_predict_load_caps_output(self, correlation):
        now = datetime.now(UTC)
        with patch(
            "custom_components.localshift.learning.correlation.dt_util.now",
            return_value=now,
        ):
            min_samples = getattr(correlation_module, "MIN_SAMPLES_PER_ZONE", None)
            assert min_samples is not None
            for _ in range(min_samples):
                correlation.learn_from_sample(8, 10.0, 5.0)
                correlation.learn_from_sample(8, 0.0, 10.0)

        predicted, reason = correlation.predict_load(8, 0.0, 0.5)
        assert reason == "weather_heating"
        max_multiplier = getattr(correlation_module, "MAX_LOAD_MULTIPLIER", None)
        assert max_multiplier is not None
        cap = max(0.5, 0.1) * max_multiplier
        assert predicted <= cap + 1e-6

    def test_regression_replay_caps_original_bug(self, correlation):
        now = datetime.now(UTC)
        with patch(
            "custom_components.localshift.learning.correlation.dt_util.now",
            return_value=now,
        ):
            min_samples = getattr(correlation_module, "MIN_SAMPLES_PER_ZONE", None)
            assert min_samples is not None
            for _ in range(min_samples):
                correlation.learn_from_sample(8, 15.0, 2.0)
                correlation.learn_from_sample(8, 5.0, 6.0)

        predicted, reason = correlation.predict_load(8, 5.0, 0.72)
        assert reason == "weather_heating"
        max_multiplier = getattr(correlation_module, "MAX_LOAD_MULTIPLIER", None)
        assert max_multiplier is not None
        cap = max(0.72, 0.1) * max_multiplier
        assert predicted <= cap + 1e-6

    def test_predict_load_returns_invalid_hour(self, correlation):
        predicted, reason = correlation.predict_load(-1, 20.0, 1.0)
        assert predicted == 1.0
        assert reason == "invalid_hour"

    def test_predict_load_returns_no_coefficients(self, correlation):
        predicted, reason = correlation.predict_load(8, 20.0, 1.0)
        assert predicted == 1.0
        assert reason == "no_coefficients"

    def test_predict_load_applies_cooling_adjustment(self, correlation):
        correlation._data.daily_regression_stats = {
            8: [
                DailySnapshot(
                    date_key="2026-03-26",
                    data=HourlyRegressionData(
                        mild=ZoneStats(),
                        heating=ZoneStats(),
                        cooling=_linear_stats(20, 1.0),
                    ),
                )
            ]
        }

        predicted, reason = correlation.predict_load(8, 26.0, 1.0)

        assert reason == "weather_cooling"
        assert predicted > 1.0

    def test_predict_load_returns_weather_none_for_small_delta(self, correlation):
        correlation._data.daily_regression_stats = {
            8: [
                DailySnapshot(
                    date_key="2026-03-26",
                    data=HourlyRegressionData(
                        mild=ZoneStats(),
                        heating=_linear_stats(20, 1.0),
                        cooling=_linear_stats(20, 1.0),
                    ),
                )
            ]
        }

        predicted, reason = correlation.predict_load(8, 24.4, 1.0)

        assert predicted == 1.0
        assert reason == "weather_none"

    def test_predict_load_returns_low_confidence_for_poor_fit(self, correlation):
        low_fit_stats = ZoneStats(
            n=20,
            sum_x=210.0,
            sum_y=1.0,
            sum_xx=2870.0,
            sum_xy=1.0,
            sum_yy=1000.0,
        )
        correlation._data.daily_regression_stats = {
            8: [
                DailySnapshot(
                    date_key="2026-03-26",
                    data=HourlyRegressionData(
                        mild=ZoneStats(),
                        heating=ZoneStats(),
                        cooling=low_fit_stats,
                    ),
                )
            ]
        }

        predicted, reason = correlation.predict_load(8, 26.0, 1.0)

        assert predicted == 1.0
        assert reason == "low_confidence"


class TestMigrationAndReset:
    @pytest.mark.asyncio
    async def test_async_initialize_migrates_v1_storage_and_discards_hourly_coefficients(
        self, correlation, mock_weather_store
    ):
        mock_weather_store.async_load.return_value = {
            "version": 1,
            "weather_entity_id": "weather.home",
            "hourly_coefficients": {"8": {"cooling_coefficient": 12.761}},
            "learning_stats": {"note": "keep"},
            "temperature_history": {"2026-03-25": 22.5},
        }

        await correlation.async_initialize()

        assert correlation._data.daily_regression_stats == {}
        assert correlation._data.temperature_history == {"2026-03-25": 22.5}
        assert correlation._data.learning_stats == {"note": "keep"}

    @pytest.mark.asyncio
    async def test_async_reset_clears_regression_stats_but_keeps_temperature_history(
        self, correlation
    ):
        correlation._data.daily_regression_stats = {
            8: [DailySnapshot(date_key="2026-03-26", data=HourlyRegressionData())]
        }
        correlation._data.temperature_history = {"2026-03-25": 22.5}

        await correlation.async_reset()

        assert correlation._data.daily_regression_stats == {}
        assert correlation._data.temperature_history == {"2026-03-25": 22.5}

    def test_prune_daily_snapshots_removes_entries_older_than_window(self, correlation):
        correlation._data.daily_regression_stats = {
            8: [
                DailySnapshot(date_key="2026-02-23", data=HourlyRegressionData()),
                DailySnapshot(date_key="2026-02-24", data=HourlyRegressionData()),
                DailySnapshot(date_key="2026-03-25", data=HourlyRegressionData()),
            ]
        }

        correlation._prune_daily_snapshots(datetime(2026, 3, 26, tzinfo=UTC))

        remaining = [
            snap.date_key for snap in correlation._data.daily_regression_stats[8]
        ]
        assert "2026-02-23" not in remaining
        assert "2026-02-24" not in remaining
        assert "2026-03-25" in remaining

    def test_prune_daily_snapshots_removes_empty_hours(self, correlation):
        correlation._data.daily_regression_stats = {
            8: [DailySnapshot(date_key="2026-02-01", data=HourlyRegressionData())]
        }

        correlation._prune_daily_snapshots(datetime(2026, 3, 26, tzinfo=UTC))

        assert 8 not in correlation._data.daily_regression_stats


class TestDelegationsAndDiagnostics:
    def test_get_temperature_forecast_delegates(self, correlation):
        forecast = correlation_module.TemperatureForecast(
            slot_time=datetime(2026, 3, 26, 8, tzinfo=UTC),
            temperature=18.0,
            condition="cloudy",
        )
        provider = MagicMock()
        provider.get_temperature_forecast.return_value = [forecast]
        correlation._temperature_provider = provider

        assert correlation.get_temperature_forecast() == [forecast]
        provider.get_temperature_forecast.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_get_temperature_forecast_updates_weather_entity_id(
        self, correlation
    ):
        forecast = correlation_module.TemperatureForecast(
            slot_time=datetime(2026, 3, 26, 9, tzinfo=UTC),
            temperature=19.0,
            condition="sunny",
        )
        provider = MagicMock()
        provider.weather_entity_id = "weather.updated"
        provider.async_get_temperature_forecast = AsyncMock(return_value=[forecast])
        correlation._temperature_provider = provider

        result = await correlation.async_get_temperature_forecast(force_refresh=True)

        assert result == [forecast]
        assert correlation._data.weather_entity_id == "weather.updated"
        provider.async_get_temperature_forecast.assert_awaited_once_with(
            force_refresh=True
        )

    def test_get_current_temperature_delegates(self, correlation):
        provider = MagicMock()
        provider.get_current_temperature.return_value = 21.5
        correlation._temperature_provider = provider

        assert correlation.get_current_temperature() == 21.5
        provider.get_current_temperature.assert_called_once()

    def test_record_daily_temperature_delegates(self, correlation):
        detector = MagicMock()
        correlation._anomaly_detector = detector

        correlation.record_daily_temperature(18.0, "2026-03-26")

        detector.record_daily_temperature.assert_called_once_with(18.0, "2026-03-26")

    def test_detect_weather_anomaly_delegates(self, correlation):
        detector = MagicMock()
        result = correlation_module.WeatherAnomalyResult(
            is_anomalous=False,
            weight=1.0,
            temperature=20.0,
            deviation_sigma=0.0,
            mean_temperature=20.0,
            std_temperature=1.0,
        )
        detector.detect_weather_anomaly.return_value = result
        correlation._anomaly_detector = detector

        assert correlation.detect_weather_anomaly(20.0) == result
        detector.detect_weather_anomaly.assert_called_once_with(20.0)

    def test_get_diagnostics_reports_averages(self, correlation):
        heating_stats = _linear_stats(20, 2.0)
        cooling_stats = _linear_stats(20, 1.0)
        correlation._data.daily_regression_stats = {
            8: [
                DailySnapshot(
                    date_key="2026-03-26",
                    data=HourlyRegressionData(
                        mild=ZoneStats(),
                        heating=heating_stats,
                        cooling=cooling_stats,
                    ),
                )
            ]
        }

        diagnostics = correlation.get_diagnostics()

        assert diagnostics["average_heating_slope"] == pytest.approx(2.0, rel=1e-3)
        assert diagnostics["average_cooling_slope"] == pytest.approx(1.0, rel=1e-3)
        assert diagnostics["average_r_squared"] == pytest.approx(1.0, rel=1e-3)
        assert diagnostics["total_samples"] == 40
        assert diagnostics["hourly_regression"][8]["confidence"] == "medium"

    def test_get_diagnostics_handles_empty_hours(self, correlation):
        correlation._data.daily_regression_stats = {8: []}
        diagnostics = correlation.get_diagnostics()
        assert diagnostics["hours_with_data"] == 0

    def test_get_diagnostics_sets_high_confidence(self, correlation):
        correlation._data.daily_regression_stats = {
            8: [
                DailySnapshot(
                    date_key="2026-03-26",
                    data=HourlyRegressionData(
                        mild=ZoneStats(),
                        heating=_linear_stats(30, 2.0),
                        cooling=_linear_stats(30, 1.0),
                    ),
                )
            ]
        }
        diagnostics = correlation.get_diagnostics()
        assert diagnostics["hourly_regression"][8]["confidence"] == "high"

    def test_get_coefficients_for_hour_invalid_hour_returns_none(self, correlation):
        assert correlation.get_coefficients_for_hour(-1) is None
        assert correlation.get_coefficients_for_hour(24) is None

    def test_get_coefficients_for_hour_with_no_data_returns_none(self, correlation):
        correlation._data.daily_regression_stats = {8: []}
        assert correlation.get_coefficients_for_hour(8) is None


class TestConfidence:
    def test_calculate_confidence_thresholds(self, correlation):
        assert correlation._calculate_confidence(1) == "low"
        assert correlation._calculate_confidence(25, 0.05) == "low"
        assert correlation._calculate_confidence(40, 0.5) == "high"


class TestConstants:
    def test_regression_constants(self):
        assert getattr(correlation_module, "MIN_TEMP_DELTA", None) == 1.0
        assert getattr(correlation_module, "MIN_SAMPLES_PER_ZONE", None) == 20
        assert getattr(correlation_module, "MIN_R_SQUARED", None) == 0.10
        assert getattr(correlation_module, "MAX_SLOPE_KW_PER_DEGREE", None) == 2.0
        assert getattr(correlation_module, "MAX_LOAD_MULTIPLIER", None) == 3.0
        assert getattr(correlation_module, "SLIDING_WINDOW_DAYS", None) == 30
