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
        self.calls = []

    def estimate_hourly_consumption_kw(self, **_kwargs):
        self.calls.append(_kwargs)
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


def test_compute_load_forecast_slots_passes_context():
    data = CoordinatorData()
    data.weather_temperature_forecast = {}
    data.load_power_kw = 0.5

    load_forecaster = _StubLoadForecaster(1.0)
    pipeline = ForecastPipeline(
        load_forecaster=load_forecaster,
        price_signals=_StubPriceSignals(),
        forecast_history_store=_StubForecastHistoryStore(),
        get_switch_state=lambda _key: False,
        excess_solar_signals=MagicMock(),
    )

    now_dt = datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC)
    pipeline.compute_load_forecast_slots(
        data=data,
        now_dt=now_dt,
        historical_avg_kw={10: 0.5},
        recent_load_kw=0.5,
        total_slots=4,
    )

    assert len(load_forecaster.calls) == 4
    assert all("day_of_week" in call for call in load_forecaster.calls)
    assert all("season" in call for call in load_forecaster.calls)
    assert all(call["day_of_week"] == 0 for call in load_forecaster.calls)
    assert all(call["season"] == "winter" for call in load_forecaster.calls)


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


def test_compute_solar_battery_forecast_after_dw_sets_target_reached_today():
    data = CoordinatorData()
    data.soc = 85.0
    data.solcast_today = []
    data.solcast_tomorrow = []
    data.load_forecast_slots = [1.0] * 8
    data.optimizer_decisions = []

    pipeline = ForecastPipeline(
        load_forecaster=_StubLoadForecaster(1.0),
        price_signals=_StubPriceSignals(),
        forecast_history_store=_StubForecastHistoryStore(),
        get_switch_state=lambda _key: False,
        excess_solar_signals=MagicMock(),
    )

    now_dt = datetime(2026, 2, 16, 22, 0, 0, tzinfo=UTC)
    pipeline.compute_solar_battery_forecast(
        data=data,
        now_dt=now_dt,
        target_hour=18,
        before_dw=False,
        after_dw=True,
        target_pct=80.0,
    )

    assert data.target_reached_today is True
    assert data.solar_battery_forecast["target_reached_today"] is True


def test_compute_solar_battery_forecast_uses_allow_dw_entry_under_target_branch():
    data = CoordinatorData()
    data.soc = 40.0
    data.solcast_today = []
    data.solcast_tomorrow = []
    data.load_forecast_slots = [1.0] * 8
    data.optimizer_decisions = [
        {
            "timestamp_iso": "2026-02-16T18:00:00+00:00",
            "predicted_soc_pct": 60.0,
            "slot_interval_minutes": 15,
        }
    ]
    data.solar_can_reach_target_in_dw = False

    pipeline = ForecastPipeline(
        load_forecaster=_StubLoadForecaster(1.0),
        price_signals=_StubPriceSignals(),
        forecast_history_store=_StubForecastHistoryStore(),
        get_switch_state=lambda _key: True,
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
            target_pct=80.0,
        )

    assert data.solar_battery_forecast["boost_needed"] is True


def test_get_dp_decision_skips_invalid_entries_and_returns_none():
    data = CoordinatorData()
    data.optimizer_decisions = [
        {"other": "missing_timestamp"},
        {"timestamp_iso": "not-a-datetime"},
        {"timestamp_iso": "2026-02-16T17:00:00"},
    ]

    pipeline = ForecastPipeline(
        load_forecaster=_StubLoadForecaster(1.0),
        price_signals=_StubPriceSignals(),
        forecast_history_store=_StubForecastHistoryStore(),
        get_switch_state=lambda _key: False,
        excess_solar_signals=MagicMock(),
    )

    now_dt = datetime(2026, 2, 16, 10, 0, 0, tzinfo=UTC)
    with patch("homeassistant.util.dt.now", return_value=now_dt):
        decision = pipeline._get_dp_decision_at_demand_window(data, target_hour=18)

    assert decision is None


def test_get_dp_decision_handles_missing_tzinfo():
    """Test _get_dp_decision_at_demand_window handles naive datetime decision timestamps."""
    data = CoordinatorData()
    data.optimizer_decisions = [
        {"timestamp_iso": "2026-02-16T19:00:00"},  # No tzinfo, should be treated as UTC
    ]

    pipeline = ForecastPipeline(
        load_forecaster=_StubLoadForecaster(1.0),
        price_signals=_StubPriceSignals(),
        forecast_history_store=_StubForecastHistoryStore(),
        get_switch_state=lambda _key: False,
        excess_solar_signals=MagicMock(),
    )

    now_dt = datetime(2026, 2, 16, 10, 0, 0, tzinfo=UTC)
    with patch("homeassistant.util.dt.now", return_value=now_dt):
        decision = pipeline._get_dp_decision_at_demand_window(data, target_hour=18)

    assert decision is not None


def test_get_dp_decision_returns_none_when_no_matching_decision():
    """Test _get_dp_decision_at_demand_window returns None when all decisions are before DW."""
    data = CoordinatorData()
    data.optimizer_decisions = [
        {"timestamp_iso": "2026-02-16T17:00:00+00:00"},  # Before DW at 18:00
    ]

    pipeline = ForecastPipeline(
        load_forecaster=_StubLoadForecaster(1.0),
        price_signals=_StubPriceSignals(),
        forecast_history_store=_StubForecastHistoryStore(),
        get_switch_state=lambda _key: False,
        excess_solar_signals=MagicMock(),
    )

    now_dt = datetime(2026, 2, 16, 10, 0, 0, tzinfo=UTC)
    with patch("homeassistant.util.dt.now", return_value=now_dt):
        decision = pipeline._get_dp_decision_at_demand_window(data, target_hour=18)

    assert decision is None


def test_compute_solar_battery_forecast_after_dw_no_decisions():
    """Test solar battery forecast when after DW and no decisions available."""
    data = CoordinatorData()
    data.soc = 85.0
    data.solcast_today = []
    data.solcast_tomorrow = []
    data.load_forecast_slots = [1.0] * 8
    data.optimizer_decisions = []

    pipeline = ForecastPipeline(
        load_forecaster=_StubLoadForecaster(1.0),
        price_signals=_StubPriceSignals(),
        forecast_history_store=_StubForecastHistoryStore(),
        get_switch_state=lambda _key: False,
        excess_solar_signals=MagicMock(),
    )

    now_dt = datetime(2026, 2, 16, 22, 0, 0, tzinfo=UTC)
    pipeline.compute_solar_battery_forecast(
        data=data,
        now_dt=now_dt,
        target_hour=18,
        before_dw=False,
        after_dw=True,
        target_pct=80.0,
    )

    assert data.solar_battery_forecast["predicted_soc"] == 85.0
    assert data.solar_battery_forecast["can_reach_target"] is True
    assert data.solar_battery_forecast["boost_needed"] is False


def test_compute_solar_battery_forecast_before_dw_no_decisions():
    """Test solar battery forecast before DW with no decisions - calculates from solar/load."""
    from datetime import timedelta

    data = CoordinatorData()
    data.soc = 40.0
    # Solcast data should be list of dicts with period_end and pv_estimate
    now = datetime(2026, 2, 16, 10, 0, 0, tzinfo=UTC)
    data.solcast_today = [
        {
            "period_end": (now + timedelta(minutes=30 * i)).isoformat(),
            "pv_estimate": 5.0,
        }
        for i in range(48)
    ]
    data.solcast_tomorrow = [
        {
            "period_end": (now + timedelta(days=1, minutes=30 * i)).isoformat(),
            "pv_estimate": 5.0,
        }
        for i in range(48)
    ]
    data.load_forecast_slots = [1.0] * 8
    data.optimizer_decisions = []

    pipeline = ForecastPipeline(
        load_forecaster=_StubLoadForecaster(1.0),
        price_signals=_StubPriceSignals(),
        forecast_history_store=_StubForecastHistoryStore(),
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
            target_pct=80.0,
        )

    assert "predicted_soc" in data.solar_battery_forecast
    assert "boost_needed" in data.solar_battery_forecast


def test_compute_excess_solar_signals_delegates_to_signals():
    """Test compute_excess_solar_signals delegates to excess_solar_signals."""
    data = CoordinatorData()
    excess_signals = MagicMock()

    pipeline = ForecastPipeline(
        load_forecaster=_StubLoadForecaster(1.0),
        price_signals=_StubPriceSignals(),
        forecast_history_store=_StubForecastHistoryStore(),
        get_switch_state=lambda _key: False,
        excess_solar_signals=excess_signals,
    )

    now_dt = datetime(2026, 2, 16, 10, 0, 0, tzinfo=UTC)
    pipeline.compute_excess_solar_signals(data, now_dt)

    excess_signals.compute_signals.assert_called_once_with(data, now_dt)


def test_compute_solar_weighted_avg_fit_delegates_to_price_signals():
    """Test compute_solar_weighted_avg_fit delegates to price_signals."""
    data = CoordinatorData()
    price_signals = MagicMock()
    price_signals.compute_solar_weighted_avg_fit = MagicMock()

    pipeline = ForecastPipeline(
        load_forecaster=_StubLoadForecaster(1.0),
        price_signals=price_signals,
        forecast_history_store=_StubForecastHistoryStore(),
        get_switch_state=lambda _key: False,
        excess_solar_signals=MagicMock(),
    )

    now_dt = datetime(2026, 2, 16, 10, 0, 0, tzinfo=UTC)
    pipeline.compute_solar_weighted_avg_fit(
        data, now_dt, target_hour=18, after_dw=False
    )


def test_get_dp_decision_when_target_hour_already_passed():
    """Test _get_dp_decision when target hour is earlier than current time."""
    data = CoordinatorData()
    data.optimizer_decisions = [
        {"timestamp_iso": "2026-02-17T19:30:00+00:00"},
    ]

    pipeline = ForecastPipeline(
        load_forecaster=_StubLoadForecaster(1.0),
        price_signals=_StubPriceSignals(),
        forecast_history_store=_StubForecastHistoryStore(),
        get_switch_state=lambda _key: False,
        excess_solar_signals=MagicMock(),
    )

    now_dt = datetime(2026, 2, 16, 19, 0, 0, tzinfo=UTC)
    with patch("homeassistant.util.dt.now", return_value=now_dt):
        decision = pipeline._get_dp_decision_at_demand_window(data, target_hour=18)

    assert decision is not None
