"""Unit tests for ForecastPipeline."""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from custom_components.localshift.forecast.pipeline import (
    ForecastPipeline,
)
from custom_components.localshift.coordinator import CoordinatorData


class _StubLoadForecaster:
    def __init__(self, value: float = 1.0) -> None:
        self._value = value

    def estimate_hourly_consumption_kw(self, **_kwargs):
        return self._value, "stub"


class _StubPriceSignals:
    @staticmethod
    def get_expected_load_kw_from_slots(_data, _hours_to_target):
        return 1.0

    @staticmethod
    def compute_solar_weighted_avg_fit(**_kwargs):
        return None


class _StubForecastHistoryStore:
    def __init__(self) -> None:
        self.calls = []

    def store_forecast_history(self, data, now_dt):
        self.calls.append((data, now_dt))


def test_compute_load_forecast_slots_populates_slots():
    """Load forecast slots should be populated with stub values."""
    data = CoordinatorData()
    data.weather_temperature_forecast = {}
    data.load_power_kw = 0.5

    pipeline = ForecastPipeline(
        load_forecaster=_StubLoadForecaster(1.2),
        price_signals=_StubPriceSignals(),
        forecast_history_store=_StubForecastHistoryStore(),
        get_switch_state=lambda _key: False,
        excess_solar_signals=MagicMock(),
    )

    now_dt = datetime(2026, 2, 16, 10, 0, 0, tzinfo=UTC)
    pipeline.compute_load_forecast_slots(
        data=data,
        now_dt=now_dt,
        historical_avg_kw={10: 0.5},
        recent_load_kw=0.5,
        total_slots=8,
    )

    assert len(data.load_forecast_slots) == 8
    assert all(slot == 1.2 for slot in data.load_forecast_slots)


def test_compute_solar_battery_forecast_uses_dp_decision():
    """Solar battery forecast should use DP decisions when available."""
    data = CoordinatorData()
    data.soc = 50.0
    data.solcast_today = []
    data.solcast_tomorrow = []
    data.load_forecast_slots = [1.0] * 8
    data.optimizer_decisions = [
        {
            "timestamp_iso": "2026-02-16T18:00:00+00:00",
            "predicted_soc_pct": 95.5,
            "slot_interval_minutes": 15,
        }
    ]

    history_store = _StubForecastHistoryStore()

    pipeline = ForecastPipeline(
        load_forecaster=_StubLoadForecaster(1.0),
        price_signals=_StubPriceSignals(),
        forecast_history_store=history_store,
        get_switch_state=lambda _key: False,
        excess_solar_signals=MagicMock(),
    )

    now_dt = datetime(2026, 2, 16, 10, 0, 0, tzinfo=UTC)

    with patch("homeassistant.util.dt.now", return_value=now_dt):
        pipeline.compute_solar_battery_forecast(
            data=data,
            now_dt=now_dt,
            target_hour=18,
            before_dw=True,
            after_dw=False,
            target_pct=90.0,
        )

    assert data.solar_battery_forecast["predicted_soc"] == 95.5
    assert data.solar_battery_forecast["can_reach_target"] is True
    assert history_store.calls
