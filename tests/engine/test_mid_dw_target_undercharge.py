"""Mid-demand-window target under-charge (in-progress DW false-positive).

When the optimizer re-plans *during* the daily demand window (e.g. 19:00, inside a
15:00-21:00 DW), the DW-entry flag false-positives on slot 0: ``slots.py`` computes
``is_demand_window_entry = in_demand_window and not prev_in_demand_window`` with
``prev_in_demand_window`` seeded ``False``, so the very first slot — already inside the DW —
is wrongly flagged as a demand-window *entry*.

``DPPlanner._find_demand_window_bounds`` takes the FIRST entry, so ``terminal_penalty_idx``
becomes 0. The hard target penalty then lands on the present instant (nothing earlier to
charge in) and TOMORROW's real DW entry gets no penalty at all — so the plan exerts zero
pre-charge pressure, holds all the way, and accepts a large terminal shortfall, relying on an
external guardrail to reach target.

These tests reproduce that under-charge deterministically and assert the fix: an in-progress
DW at slot 0 is ignored, the terminal penalty targets tomorrow's real DW entry, and the plan
reaches target by grid-charging in the cheapest (midday) hours.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta

from custom_components.localshift.engine.core import DPPlanner
from custom_components.localshift.engine.types import (
    OptimizerConfig,
    OptimizerInputs,
    PlannerAction,
    SlotContext,
)

_CHARGE = (PlannerAction.CHARGE_GRID_NORMAL, PlannerAction.CHARGE_GRID_BOOST)
_INTERVAL = 30
# Plan computed at 19:00, INSIDE today's 15:00-21:00 demand window.
_START = datetime(2026, 6, 8, 19, 0)
_DW_START = time(15, 0)
_DW_END = time(21, 0)

# Price bands ($/kWh)
_EVENING_PEAK = 0.30  # today's + tomorrow's DW peak
_OVERNIGHT = 0.13  # above the base percentile -> not "cheap" outside the urgency window
_MIDDAY_CHEAP = 0.11  # the cheapest hours, when the sun is up
_BASE_CHEAP = 0.117
_EFFECTIVE_CHEAP = 0.15


def _price_for(t: datetime) -> float:
    h = t.hour + t.minute / 60.0
    in_dw = _DW_START <= t.time() < _DW_END
    if in_dw:
        return _EVENING_PEAK
    if t.day == 9 and 9.0 <= h < 15.0:
        # Tomorrow daytime: cheapest around midday.
        return _MIDDAY_CHEAP if 10.5 <= h < 13.5 else 0.125
    return _OVERNIGHT


def _solar_for(t: datetime) -> float:
    """Modest tomorrow solar that does NOT reach target on its own."""
    if t.day != 9:
        return 0.0
    h = t.hour + t.minute / 60.0
    if 9.0 <= h < 15.0:
        return 0.45  # ~0.45 kWh/30min across 6h ~= 5.4 kWh -> well under target
    return 0.0


def _build_slots(n: int, solar_fn=_solar_for) -> list[SlotContext]:
    """Build slots computing the DW flags exactly as slots.py does (prev seeded False)."""
    slots: list[SlotContext] = []
    prev_in_dw = False
    for i in range(n):
        t = _START + timedelta(minutes=_INTERVAL * i)
        in_dw = _DW_START <= t.time() < _DW_END
        slots.append(
            SlotContext(
                slot_index=i,
                timestamp_iso=t.isoformat(),
                slot_interval_minutes=_INTERVAL,
                buy_price=_price_for(t),
                sell_price=0.05,
                solar_kwh=solar_fn(t),
                consumption_kwh=0.3,
                is_demand_window_entry=(in_dw and not prev_in_dw),
                is_demand_window_slot=in_dw,
            )
        )
        prev_in_dw = in_dw
    return slots


# 19:00 day-8 -> 16:30 day-9 (43 slots). Tomorrow's real DW entry (15:00) is slot 40.
_N_SLOTS = 43
_TOMORROW_DW_ENTRY_IDX = 40


def _config() -> OptimizerConfig:
    return OptimizerConfig(
        min_soc_pct=10.0,
        max_soc_pct=100.0,
        demand_window_target_soc_pct=95.0,
        allow_dw_entry_under_target=False,
        optimization_mode="self_consumption",
        effective_cheap_price=_EFFECTIVE_CHEAP,
        base_cheap_price=_BASE_CHEAP,
        target_shortfall_penalty_per_pct=0.08,
        soc_bins=100,
    )


def _plan():
    slots = _build_slots(_N_SLOTS)
    config = _config()
    return DPPlanner(config).plan(
        OptimizerInputs(
            cycle_id="mid-dw-undercharge",
            initial_soc_pct=70.0,
            slots=slots,
            config=config,
            all_solcast=[],
        )
    )


class TestMidDwEntryDetection:
    def test_slot0_in_progress_dw_is_not_the_target_entry(self):
        """An in-progress DW at slot 0 must not be treated as the terminal-penalty entry."""
        slots = _build_slots(_N_SLOTS)
        # Sanity: the scenario really does false-positive slot 0 and have a real entry later.
        assert slots[0].is_demand_window_slot and slots[0].is_demand_window_entry
        assert slots[_TOMORROW_DW_ENTRY_IDX].is_demand_window_entry

        bounds = DPPlanner(_config())._find_demand_window_bounds(slots)
        assert bounds["entry_idx"] == _TOMORROW_DW_ENTRY_IDX, (
            "demand-window entry must resolve to tomorrow's real 15:00 entry, not the "
            f"in-progress slot 0; got {bounds['entry_idx']}"
        )


class TestMidDwPlanReachesTarget:
    def test_plan_reaches_target(self):
        """With the entry fixed, the optimizer must (nearly) reach the 95% target."""
        result = _plan()
        assert result.success
        assert result.terminal_shortfall_pct < 2.0, (
            "plan computed inside the demand window must still pre-charge for tomorrow's "
            f"DW and reach target; got shortfall {result.terminal_shortfall_pct}%"
        )

    def test_reaches_target_via_cheap_midday_grid_charge(self):
        """Charging must happen, concentrated in the cheapest (midday) hours."""
        result = _plan()
        charges = [d for d in result.decisions if d.action in _CHARGE]
        assert charges, "expected grid charging to reach target (was all-HOLD)"
        # Every grid charge should be in a genuinely-cheap slot, never the evening peak.
        assert all(c.buy_price <= _EFFECTIVE_CHEAP for c in charges), (
            f"charges must be in cheap slots; got prices {[c.buy_price for c in charges]}"
        )
        # The bulk of charging should land in the cheapest midday band (10:30-13:30 tomorrow).
        midday = [c for c in charges if c.buy_price <= _MIDDAY_CHEAP + 1e-9]
        assert midday, "expected charging in the cheapest midday band"


def _strong_solar(t: datetime) -> float:
    """Tomorrow solar strong enough to reach target on its own."""
    if t.day != 9:
        return 0.0
    h = t.hour + t.minute / 60.0
    return 2.0 if 8.0 <= h < 15.0 else 0.0


class TestSolarSufficientDayNoOverCharge:
    """Regression: the fix must not cause grid charging when solar reaches target alone."""

    def _plan_strong_solar(self):
        slots = _build_slots(_N_SLOTS, solar_fn=_strong_solar)
        config = _config()
        return DPPlanner(config).plan(
            OptimizerInputs(
                cycle_id="mid-dw-solar-sufficient",
                initial_soc_pct=70.0,
                slots=slots,
                config=config,
                all_solcast=[],
            )
        )

    def test_no_grid_charge_when_solar_reaches_target(self):
        result = self._plan_strong_solar()
        assert result.success
        charges = [d for d in result.decisions if d.action in _CHARGE]
        assert charges == [], (
            "solar reaches target on its own — the optimizer must not grid-charge; "
            f"got charges at {[d.timestamp_iso for d in charges]}"
        )

    def test_no_overnight_grid_charge(self):
        """Sawtooth guard intact: no overnight grid charge even with the DW target active."""
        result = self._plan_strong_solar()
        overnight = [
            d
            for d in result.decisions
            if d.action in _CHARGE and datetime.fromisoformat(d.timestamp_iso).hour < 8
        ]
        assert overnight == [], (
            f"no overnight grid charging expected; got {[d.timestamp_iso for d in overnight]}"
        )
