"""Tests for the load-shift signal and its safe-additional-load input (#1100).

Live incident 2026-10-06: ``sensor.localshift_load_shift_signal`` read ``REDUCE_LOAD``
for 94% of ten days, including mornings with a large forecast solar surplus. Two
defects in ``ExcessSolarEngine`` produced it, and neither was covered by a test:

- A static SOC gate (``current_soc < target_pct - 5``) returned ``(0.0, True)`` before
  the forecast simulation ran, and a second one (``data.soc < target_pct - 10``) emitted
  ``REDUCE_LOAD`` on its own. Below ~90% SOC the forecast was never consulted.
- When the simulation found any limit under the 5 kW test ceiling it reported
  ``grid_charge_risk=True``, so "4 kW is safe, 5 kW is not" became ``REDUCE_LOAD`` and
  the signal flapped as the cap moved between adjacent test loads.

``grid_charge_risk`` now means "the current load, with nothing added, is forecast to
need grid charging". Finite headroom is not risk.

These methods reach production only by constructor injection, and this repo has had
tests stay green for months on an orphaned code path (#959), so the wiring tests drive
``ExcessSolarSignalsEngine.compute_signals`` on a real ``ComputationEngine`` with only
the simulator and the data sources stubbed.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from unittest.mock import MagicMock

import pytest
from homeassistant.util import dt as dt_util

from custom_components.localshift.coordinator.data import CoordinatorData
from custom_components.localshift.engine.excess_solar import ExcessSolarEngine

SHORTFALL_REASON = "Current load may trigger grid charging"
TEST_LOADS = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0]


class StubSimulator:
    """Stand-in for SocSimulator._simulate_with_additional_load.

    Reports grid charging for any additional load at or above ``fails_from_kw``;
    ``None`` means every tested load passes. Records the loads it was asked about.
    """

    def __init__(self, fails_from_kw: float | None) -> None:
        self.fails_from_kw = fails_from_kw
        self.loads: list[float] = []

    def __call__(self, **kwargs) -> bool:
        load = kwargs["additional_load_kw"]
        self.loads.append(load)
        return self.fails_from_kw is not None and load >= self.fails_from_kw


def _engine(simulator: StubSimulator, target: float = 95) -> ExcessSolarEngine:
    entry = MagicMock()
    entry.options = {"battery_target": target}
    return ExcessSolarEngine(
        entry=entry,
        estimate_hourly_consumption_kw=lambda *a, **k: (0.5, "stub"),
        simulate_with_additional_load=simulator,
    )


def _safe_load(
    simulator: StubSimulator, soc: float, target: float = 95
) -> tuple[float, bool]:
    return _engine(simulator, target).calculate_safe_additional_load(
        base_slot=datetime(2026, 10, 6, 10, 10),
        all_solcast=[],
        historical_avg_kw={},
        current_load_kw=1.0,
        recent_load_kw=1.0,
        current_soc=soc,
        target_pct=target,
        dw_start_time=time(15, 0),
        effective_cheap_price=0.10,
        general_forecast=[],
    )


def _data(soc: float = 58.0, **overrides) -> CoordinatorData:
    data = CoordinatorData()
    data.soc = soc
    data.solcast_today = [{"period_start": "2026-10-06T10:00:00", "pv_estimate": 5.0}]
    for key, value in overrides.items():
        setattr(data, key, value)
    return data


def _signal(
    data: CoordinatorData,
    safe_additional_load: float,
    grid_charge_risk: bool,
    excess_until_full: float = 5.0,
    excess_next_2h: float = 6.0,
    target: float = 95,
) -> tuple[str, float, int, str, str]:
    return _engine(StubSimulator(None), target).compute_load_shift_signal(
        data=data,
        excess_by_windows={
            "excess_until_battery_full_kwh": excess_until_full,
            "excess_next_2h_kwh": excess_next_2h,
        },
        negative_fit_start=None,
        safe_additional_load=safe_additional_load,
        grid_charge_risk=grid_charge_risk,
        fill_point_minutes=90,
    )


class TestCalculateSafeAdditionalLoad:
    def test_low_soc_with_surplus_runs_the_simulation(self):
        """Incident repro: SOC 58, target 95, the forecast passes every load."""
        simulator = StubSimulator(None)

        assert _safe_load(simulator, soc=58) == (5.0, False)
        assert simulator.loads, "the SOC gate must not pre-empt the simulation"

    def test_baseline_load_is_simulated_first(self):
        simulator = StubSimulator(None)

        _safe_load(simulator, soc=58)

        assert simulator.loads == [0.0, *TEST_LOADS]

    def test_finite_headroom_is_not_risk(self):
        """4 kW passes, 5 kW fails: 4 kW of headroom, no risk."""
        simulator = StubSimulator(fails_from_kw=5.0)

        assert _safe_load(simulator, soc=100) == (4.0, False)

    @pytest.mark.parametrize(
        ("fails_from_kw", "expected_kw"),
        [(0.5, 0.0), (1.0, 0.5), (2.5, 2.0), (4.0, 3.5)],
    )
    def test_returns_largest_passing_load_without_risk(
        self, fails_from_kw, expected_kw
    ):
        simulator = StubSimulator(fails_from_kw)

        assert _safe_load(simulator, soc=80) == (expected_kw, False)

    def test_real_shortfall_is_risk(self):
        """The current load alone needs grid charging."""
        simulator = StubSimulator(fails_from_kw=0.0)

        assert _safe_load(simulator, soc=58) == (0.0, True)
        assert simulator.loads == [0.0]

    @pytest.mark.parametrize("soc", [10, 58, 84, 89, 90, 100])
    def test_result_does_not_depend_on_soc_alone(self, soc):
        assert _safe_load(StubSimulator(None), soc=soc) == (5.0, False)


class TestComputeLoadShiftSignal:
    def test_low_soc_with_surplus_is_not_reduce_load(self):
        signal, recommended_kw, *_ = _signal(_data(soc=58), 5.0, False)

        assert signal == "INCREASE_LOAD"
        assert recommended_kw == 5.0

    def test_finite_headroom_increases_load_by_the_headroom(self):
        signal, recommended_kw, *_ = _signal(_data(soc=100), 4.0, False)

        assert signal == "INCREASE_LOAD"
        assert recommended_kw == 4.0

    @pytest.mark.parametrize("safe_kw", [4.0, 5.0])
    def test_adjacent_caps_do_not_flap(self, safe_kw):
        """The live signal flipped every five minutes on the 4/5 kW boundary."""
        signal, recommended_kw, *_ = _signal(_data(soc=100), safe_kw, False)

        assert signal == "INCREASE_LOAD"
        assert recommended_kw == safe_kw

    def test_real_shortfall_reduces_load_with_existing_reason(self):
        assert _signal(_data(soc=58), 0.0, True) == (
            "REDUCE_LOAD",
            -1.0,
            60,
            SHORTFALL_REASON,
            "high",
        )

    @pytest.mark.parametrize("soc", [0, 10, 58, 80, 84])
    def test_soc_under_target_without_risk_or_excess_maintains(self, soc):
        signal, recommended_kw, *_ = _signal(
            _data(soc=soc), 0.0, False, excess_until_full=0.0, excess_next_2h=0.0
        )

        assert signal == "MAINTAIN_LOAD"
        assert recommended_kw == 0.0

    @pytest.mark.parametrize("soc", [0, 10, 58, 80, 84, 100])
    @pytest.mark.parametrize("safe_kw", [0.0, 0.5, 2.0, 5.0])
    @pytest.mark.parametrize("excess", [0.0, 5.0])
    def test_reduce_load_never_comes_from_soc_alone(self, soc, safe_kw, excess):
        signal, *_ = _signal(
            _data(soc=soc),
            safe_kw,
            False,
            excess_until_full=excess,
            excess_next_2h=excess,
        )

        assert signal != "REDUCE_LOAD"

    @pytest.mark.parametrize(
        ("overrides", "reason", "confidence"),
        [
            (
                {"demand_window_active": True},
                "Demand window active - maintain current loads",
                "high",
            ),
            ({"manual_override": True}, "Manual override active", "high"),
            ({"solcast_today": []}, "No solar forecast available", "low"),
        ],
    )
    @pytest.mark.parametrize("grid_charge_risk", [True, False])
    def test_hold_takes_precedence(
        self, overrides, reason, confidence, grid_charge_risk
    ):
        safe_kw = 0.0 if grid_charge_risk else 5.0

        assert _signal(_data(**overrides), safe_kw, grid_charge_risk) == (
            "HOLD",
            0.0,
            0,
            reason,
            confidence,
        )


NOW = datetime(2026, 10, 6, 10, 10, tzinfo=dt_util.DEFAULT_TIME_ZONE)


def _solcast(now: datetime, kw: float) -> list[dict]:
    """Flat ``kw`` of solar in 30-minute periods from two hours before ``now``."""
    start = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=2)
    return [
        {
            "period_start": (start + timedelta(minutes=30 * i)).isoformat(),
            "pv_estimate": kw,
        }
        for i in range(16)
    ]


def _feed_in(
    now: datetime, window_start: datetime, window_minutes: int, hours: int = 6
) -> list[dict]:
    """Five-minute feed-in prices, negative only inside the given window."""
    start = now.replace(minute=0, second=0, microsecond=0)
    window_end = window_start + timedelta(minutes=window_minutes)
    slots = []
    for i in range(hours * 12):
        slot = start + timedelta(minutes=5 * i)
        negative = window_start <= slot < window_end
        slots.append({
            "start_time": slot.isoformat(),
            "duration": 5,
            "per_kwh": -0.03 if negative else 0.05,
        })
    return slots


class TestNegativeFeedInWindow:
    def test_window_running_to_the_end_of_the_forecast(self):
        window_start = NOW.replace(hour=12, minute=0)
        feed_in = _feed_in(NOW, window_start, window_minutes=60, hours=3)

        found = _engine(StubSimulator(None)).find_nearest_negative_fit_window(
            feed_in, NOW, max_hours=3
        )

        # The price lookup averages a 15-minute span on a 5-minute step, so the
        # detected edge can lead the first negative interval by up to 10 minutes.
        start, duration = found
        assert timedelta(0) <= window_start - start <= timedelta(minutes=10)
        assert duration >= 60

    def test_no_window(self):
        feed_in = _feed_in(NOW, NOW - timedelta(days=1), window_minutes=5)

        found = _engine(StubSimulator(None)).find_nearest_negative_fit_window(
            feed_in, NOW
        )

        assert found == (None, 0)

    def test_two_hour_excess_alone_caps_the_recommendation(self):
        """No excess past full, but 3 kWh in two hours: recommend half of it."""
        signal, recommended_kw, duration, reason, _ = _signal(
            _data(soc=58), 5.0, False, excess_until_full=0.5, excess_next_2h=3.0
        )

        assert (signal, recommended_kw, duration) == ("INCREASE_LOAD", 1.5, 60)
        assert reason == "Excess solar: 3.0kWh available in next 2 hours"


@pytest.fixture
def wired(computation_engine):
    """The production signals engine, with only the simulator and data stubbed.

    Returns a function that installs a stub simulator on the real
    ``ExcessSolarEngine`` and runs ``compute_signals``.
    """
    excess_engine = computation_engine._excess_solar_engine
    signals = computation_engine._excess_solar_signals

    # Guard the wiring itself: the signals engine must hold the real engine's
    # methods, and the engine must hold the real simulator, before any stubbing.
    assert (
        signals._calculate_safe_additional_load
        == excess_engine.calculate_safe_additional_load
    )
    assert signals._compute_load_shift_signal == excess_engine.compute_load_shift_signal
    assert (
        excess_engine._simulate_with_additional_load
        == computation_engine._soc_simulator._simulate_with_additional_load
    )

    signals._get_historical_hourly_averages = lambda _entity_id: dict.fromkeys(
        range(24), 1.0
    )
    signals._recent_load_1hr_getter = lambda: 1.0

    def run(simulator: StubSimulator, **overrides) -> CoordinatorData:
        excess_engine._simulate_with_additional_load = simulator
        data = CoordinatorData()
        data.soc = 58.0
        data.solar_power_kw = 5.0
        data.load_power_kw = 1.0
        data.battery_power_kw = -3.0
        data.solcast_today = _solcast(NOW, 5.0)
        for key, value in overrides.items():
            setattr(data, key, value)
        signals.compute_signals(data, NOW)
        return data

    return run


class TestWiredSignalPath:
    """compute_signals on the real ComputationEngine wiring (mock_entry target 90)."""

    def test_low_soc_with_surplus(self, wired):
        simulator = StubSimulator(None)

        data = wired(simulator, soc=58.0)

        assert simulator.loads == [0.0, *TEST_LOADS]
        assert data.grid_charge_risk is False
        assert data.safe_additional_load_kw == 5.0
        assert data.load_shift_signal == "INCREASE_LOAD"
        assert data.load_shift_recommended_kw == 5.0
        assert data.can_add_load_now is True
        assert data.excess_solar_available is True

    def test_finite_headroom_at_full_battery(self, wired):
        data = wired(StubSimulator(fails_from_kw=5.0), soc=100.0)

        assert data.grid_charge_risk is False
        assert data.safe_additional_load_kw == 4.0
        assert data.load_shift_signal == "INCREASE_LOAD"
        assert data.load_shift_recommended_kw == 4.0
        assert data.can_add_load_now is True

    def test_real_shortfall(self, wired):
        data = wired(StubSimulator(fails_from_kw=0.0), soc=58.0)

        assert data.grid_charge_risk is True
        assert data.safe_additional_load_kw == 0.0
        assert data.load_shift_signal == "REDUCE_LOAD"
        assert data.load_shift_recommended_kw == -1.0
        assert data.load_shift_reason == SHORTFALL_REASON
        assert data.can_add_load_now is False
        assert data.excess_solar_available is False

    def test_low_soc_without_excess_maintains(self, wired):
        data = wired(
            StubSimulator(None), soc=30.0, solar_power_kw=0.0, solcast_today=[]
        )
        assert data.load_shift_signal == "HOLD"

        data = wired(
            StubSimulator(None),
            soc=30.0,
            solar_power_kw=0.0,
            solcast_today=_solcast(NOW, 0.0),
        )

        assert data.grid_charge_risk is False
        assert data.load_shift_signal == "MAINTAIN_LOAD"
        assert data.can_add_load_now is False

    def test_surplus_before_negative_feed_in_window(self, wired):
        """The 10-06 shape: SOC 58, surplus forecast, negative feed-in at 13:20."""
        window_start = NOW.replace(hour=13, minute=20)

        data = wired(
            StubSimulator(None),
            feed_in_forecast=_feed_in(NOW, window_start, window_minutes=60),
        )

        lead = window_start - data.negative_fit_window_start
        assert timedelta(0) <= lead <= timedelta(minutes=10)
        assert data.negative_fit_window_duration_minutes >= 60
        assert data.excess_until_negative_fit_kwh > 2.0
        assert data.grid_charge_risk is False
        assert data.load_shift_signal == "INCREASE_LOAD"
        assert data.load_shift_recommended_kw == 5.0
        assert "before negative FIT at 13:" in data.load_shift_reason
        assert 30 <= data.load_shift_recommended_duration_minutes <= 120
        assert data.load_shift_confidence == "high"

    def test_demand_window_holds(self, wired):
        data = wired(StubSimulator(fails_from_kw=0.0), demand_window_active=True)

        assert data.load_shift_signal == "HOLD"
        assert data.can_add_load_now is False
