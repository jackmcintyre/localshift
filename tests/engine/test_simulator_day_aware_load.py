"""Issue #1005: the SOC simulator and the excess-solar engine see the planner's load.

The planner's ``data.load_forecast_slots`` is built per slot with
``day_of_week=slot_start.weekday()``, so on a weekend it reads the weekend
profile. ``SocSimulator`` and ``ExcessSolarEngine`` call the same estimator;
before #1005 they left ``day_of_week`` out and silently kept the combined
profile, so on a Sunday the DP planned against one load while the fill-point,
safe-additional-load and excess-solar signals planned against another.

Each test runs one simulator path against a real ``LoadForecaster`` with a
weekend profile (3.0 kW), a weekday profile (1.5 kW) and a combined profile
(1.0 kW) that all differ, starting Sunday 14:00 so most paths cross into
Monday, and asserts every slot the path estimates equals the planner's slot.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from unittest.mock import MagicMock

import pytest

from custom_components.localshift.const import (
    MIN_SAMPLES_PER_AGGREGATE_HOUR,
)
from custom_components.localshift.coordinator import CoordinatorData
from custom_components.localshift.engine.excess_solar import ExcessSolarEngine
from custom_components.localshift.engine.soc_simulator import SocSimulator
from custom_components.localshift.forecast.load import LoadForecaster, LoadProfiles
from custom_components.localshift.forecast.pipeline import ForecastPipeline

COMBINED_KW = 1.0
WEEKDAY_KW = 1.5
WEEKEND_KW = 3.0
LIVE_KW = 2.0
TOTAL_SLOTS = 96

# Sunday 11 Oct 2026, 14:00.
SUNDAY_14 = datetime(2026, 10, 11, 14, 0, tzinfo=UTC)


def _flat(value: float) -> dict[int, float]:
    return {h: value for h in range(24)}


def _forecaster() -> LoadForecaster:
    entry = MagicMock()
    entry.options = {}
    forecaster = LoadForecaster(entry)
    forecaster.set_daily_profiles(
        LoadProfiles(
            daily_avg={},
            daily_counts={},
            weekday_avg=_flat(WEEKDAY_KW),
            weekday_counts={h: MIN_SAMPLES_PER_AGGREGATE_HOUR for h in range(24)},
            weekend_avg=_flat(WEEKEND_KW),
            weekend_counts={h: MIN_SAMPLES_PER_AGGREGATE_HOUR for h in range(24)},
        )
    )
    return forecaster


def _planner_slots(forecaster: LoadForecaster) -> list[float]:
    """The planner's per-slot load, built the way the coordinator builds it."""
    data = CoordinatorData()
    data.weather_temperature_forecast = {}
    data.load_power_kw = LIVE_KW
    data.recent_load_short_kw = 0.0
    pipeline = ForecastPipeline(
        load_forecaster=forecaster,
        price_signals=MagicMock(),
        forecast_history_store=MagicMock(),
        get_switch_state=lambda _key: False,
        excess_solar_signals=MagicMock(),
    )
    pipeline.compute_load_forecast_slots(
        data,
        SUNDAY_14,
        historical_avg_kw=_flat(COMBINED_KW),
        recent_load_kw=LIVE_KW,
        total_slots=TOTAL_SLOTS,
    )
    return data.load_forecast_slots


class _Recorder:
    """Wrap the real estimator; keep (planner slot index, kW) per call."""

    def __init__(self, forecaster: LoadForecaster) -> None:
        self._forecaster = forecaster
        self.seen: list[tuple[int, float]] = []

    def __call__(self, *args, **kwargs) -> tuple[float, str]:
        kw, source = self._forecaster.estimate_hourly_consumption_kw(*args, **kwargs)
        self.seen.append((round(kwargs["hours_ahead"] * 4), kw))
        return kw, source


def _assert_matches_planner(recorder: _Recorder, planner: list[float]) -> None:
    assert recorder.seen, "the path estimated no slots"
    mismatches = [
        (index, kw, planner[index])
        for index, kw in recorder.seen
        if kw != pytest.approx(planner[index])
    ]
    assert not mismatches, (
        f"(slot, path kW, planner kW) differ for {len(mismatches)} of "
        f"{len(recorder.seen)} slots: {mismatches[:5]}"
    )


@pytest.fixture
def forecaster() -> LoadForecaster:
    return _forecaster()


@pytest.fixture
def planner(forecaster: LoadForecaster) -> list[float]:
    slots = _planner_slots(forecaster)
    # Guard against a vacuous pass: beyond the live-load blend the planner
    # must read the weekend profile on Sunday evening and the weekday profile
    # on Monday, both unlike the combined profile.
    sunday_20 = 6 * 4
    monday_10 = 20 * 4
    assert slots[sunday_20] == pytest.approx(WEEKEND_KW)
    assert slots[monday_10] == pytest.approx(WEEKDAY_KW)
    return slots


def test_simulate_with_additional_load_uses_planner_load(forecaster, planner):
    recorder = _Recorder(forecaster)
    SocSimulator(recorder)._simulate_with_additional_load(
        base_slot=SUNDAY_14,
        all_solcast=[],
        historical_avg_kw=_flat(COMBINED_KW),
        current_load_kw=LIVE_KW,
        recent_load_kw=LIVE_KW,
        current_soc=50.0,
        target_pct=95.0,
        dw_start_time=time(13, 0),
        effective_cheap_price=0.10,
        general_forecast=[],
        additional_load_kw=0.0,
        current_hour=SUNDAY_14.hour,
    )
    # Sunday 14:00 to Monday 13:00.
    assert len(recorder.seen) == 92
    _assert_matches_planner(recorder, planner)


def test_find_battery_fill_point_uses_planner_load(forecaster, planner):
    recorder = _Recorder(forecaster)
    SocSimulator(recorder).find_battery_fill_point(
        start_soc=20.0,
        start_slot=SUNDAY_14,
        all_solcast=[],
        historical_avg_kw=_flat(COMBINED_KW),
        current_load_kw=LIVE_KW,
        recent_load_kw=LIVE_KW,
        current_hour=SUNDAY_14.hour,
    )
    assert len(recorder.seen) == TOTAL_SLOTS
    _assert_matches_planner(recorder, planner)


def test_find_battery_fill_point_hybrid_uses_planner_load(forecaster, planner):
    hybrid_slots = [
        {"start": SUNDAY_14 + timedelta(minutes=15 * i), "interval_minutes": 15}
        for i in range(8)
    ] + [
        {"start": SUNDAY_14 + timedelta(minutes=120 + 30 * i), "interval_minutes": 30}
        for i in range(40)
    ]
    recorder = _Recorder(forecaster)
    SocSimulator(recorder).find_battery_fill_point(
        start_soc=20.0,
        start_slot=SUNDAY_14,
        all_solcast=[],
        historical_avg_kw=_flat(COMBINED_KW),
        current_load_kw=LIVE_KW,
        recent_load_kw=LIVE_KW,
        current_hour=SUNDAY_14.hour,
        hybrid_slots=hybrid_slots,
    )
    assert len(recorder.seen) == len(hybrid_slots)
    _assert_matches_planner(recorder, planner)


def test_excess_by_windows_uses_planner_load(forecaster, planner):
    recorder = _Recorder(forecaster)
    ExcessSolarEngine(MagicMock(), recorder, MagicMock()).calculate_excess_by_windows(
        base_slot=SUNDAY_14,
        all_solcast=[],
        historical_avg_kw=_flat(COMBINED_KW),
        current_load_kw=LIVE_KW,
        recent_load_kw=LIVE_KW,
        current_soc=50.0,
        target_pct=95.0,
        current_hour=SUNDAY_14.hour,
    )
    assert len(recorder.seen) == 16
    _assert_matches_planner(recorder, planner)


def test_excess_until_negative_fit_uses_planner_load(forecaster, planner):
    recorder = _Recorder(forecaster)
    ExcessSolarEngine(
        MagicMock(), recorder, MagicMock()
    ).calculate_excess_until_negative_fit(
        base_slot=SUNDAY_14,
        negative_fit_start=SUNDAY_14 + timedelta(hours=16),
        all_solcast=[],
        historical_avg_kw=_flat(COMBINED_KW),
        current_load_kw=LIVE_KW,
        recent_load_kw=LIVE_KW,
        current_soc=50.0,
        target_pct=95.0,
        current_hour=SUNDAY_14.hour,
    )
    assert len(recorder.seen) == 64
    _assert_matches_planner(recorder, planner)
