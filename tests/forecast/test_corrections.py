from custom_components.localshift.forecast.corrections import (
    CORRECTION_CLAMP_MAX,
    CORRECTION_CLAMP_MIN,
    ContextErrorStats,
    ForecastCorrectionProvider,
)


class TestContextErrorStats:
    def test_record_ignores_non_positive_forecast(self):
        stats = ContextErrorStats()

        stats.record(actual_kw=1.0, forecast_kw=0.0)
        stats.record(actual_kw=1.0, forecast_kw=-0.1)

        assert stats.sample_count == 0
        assert stats.mean_ratio == 1.0
        assert stats.last_updated == ""

    def test_record_updates_running_mean(self):
        stats = ContextErrorStats()

        stats.record(actual_kw=2.0, forecast_kw=1.0)
        stats.record(actual_kw=1.0, forecast_kw=1.0)
        stats.record(actual_kw=3.0, forecast_kw=2.0)

        assert stats.sample_count == 3
        assert stats.mean_ratio == 1.5
        assert stats.last_updated

    def test_to_dict_and_from_dict_round_trip(self):
        stats = ContextErrorStats(mean_ratio=1.25, sample_count=12, last_updated="ts")

        payload = stats.to_dict()
        restored = ContextErrorStats.from_dict(payload)

        assert restored.mean_ratio == 1.25
        assert restored.sample_count == 12
        assert restored.last_updated == "ts"

    def test_from_dict_uses_defaults(self):
        restored = ContextErrorStats.from_dict({})

        assert restored.mean_ratio == 1.0
        assert restored.sample_count == 0
        assert restored.last_updated == ""


class TestForecastCorrectionProvider:
    def test_make_key(self):
        assert ForecastCorrectionProvider._make_key(2, 13, "winter") == "2:13:winter"

    def test_returns_neutral_for_unknown_context(self):
        provider = ForecastCorrectionProvider(min_samples=1)

        factor = provider.get_correction_factor(1, 9, "spring")

        assert factor == 1.0

    def test_returns_neutral_until_min_samples(self):
        provider = ForecastCorrectionProvider(min_samples=3)

        provider.record_error(2.0, 1.0, 1, 10, "summer")
        provider.record_error(2.0, 1.0, 1, 10, "summer")

        assert provider.get_correction_factor(1, 10, "summer") == 1.0

    def test_activates_correction_after_min_samples(self):
        provider = ForecastCorrectionProvider(min_samples=2)

        provider.record_error(2.0, 1.0, 3, 8, "autumn")
        provider.record_error(2.0, 1.0, 3, 8, "autumn")

        assert provider.get_correction_factor(3, 8, "autumn") == 1.5

    def test_clamps_upper_and_lower_factors(self):
        provider = ForecastCorrectionProvider(min_samples=1)

        provider.record_error(10.0, 1.0, 0, 7, "winter")
        assert provider.get_correction_factor(0, 7, "winter") == CORRECTION_CLAMP_MAX

        provider.record_error(0.01, 1.0, 0, 6, "winter")
        assert provider.get_correction_factor(0, 6, "winter") == CORRECTION_CLAMP_MIN

    def test_summary_reports_active_state(self):
        provider = ForecastCorrectionProvider(min_samples=2)

        provider.record_error(2.0, 1.0, 4, 14, "spring")
        provider.record_error(2.0, 1.0, 4, 14, "spring")
        provider.record_error(2.0, 1.0, 5, 15, "spring")

        summary = provider.get_stats_summary()

        assert summary["4:14:spring"]["active"] is True
        assert summary["5:15:spring"]["active"] is False
        assert summary["4:14:spring"]["sample_count"] == 2

    def test_to_dict_and_from_dict_round_trip(self):
        provider = ForecastCorrectionProvider(min_samples=4)
        provider.record_error(2.0, 1.0, 1, 10, "summer")
        provider.record_error(1.0, 1.0, 1, 10, "summer")

        payload = provider.to_dict()
        restored = ForecastCorrectionProvider.from_dict(payload)

        assert restored.to_dict() == payload
        assert restored.get_correction_factor(1, 10, "summer") == 1.0
