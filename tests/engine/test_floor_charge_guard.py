"""SOC-floor anti-sawtooth guard.

Even with the per-slot cheap-threshold gate (Issue #800), the live system still showed a
slow charge/drain "sawtooth" right at the SOC floor overnight: a marginal grid charge
nudges SOC a point or two off the floor, house load drains it straight back, and the next
re-plan repeats it — pure round-trip loss for no arbitrage.

The guard (``DPPlanner._compute_best_action``) refuses a grid charge while SOC is within
``min_soc_floor_buffer_pct`` of the floor unless the charge is *substantial*
(>= ``min_floor_charge_gain_pct`` SOC) or we are inside the urgency window before a demand
window. That kills the initiating nibble without touching genuine pre-charge (which either
clears the buffer in one step, e.g. boost, or happens under urgency).

These tests A/B the guard via ``min_floor_charge_gain_pct`` (0.0 disables it) on a
deterministic overnight scenario, and pin the two properties that keep it from
over-blocking: it is dormant above the floor buffer, and exempt under urgency.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from custom_components.localshift.engine.core import DPPlanner
from custom_components.localshift.engine.types import (
    OptimizerConfig,
    OptimizerInputs,
    PlannerAction,
    SlotContext,
)

_CHARGE = (PlannerAction.CHARGE_GRID_NORMAL, PlannerAction.CHARGE_GRID_BOOST)
_INTERVAL = 5  # 5-min slots: a normal grid charge gains <2% SOC per slot at the floor
_START = datetime(2026, 6, 8, 1, 0)  # 1am — deep overnight, far from any demand window

# A flat "cheap but not very cheap" overnight price: <= the cheap threshold (so a normal
# grid charge is feasible) but above threshold*0.8 (so boost, which would clear the buffer
# in one step, is NOT feasible). This isolates the sub-threshold floor nibble.
_OVERNIGHT = 0.09
_MORNING_PEAK = 0.30  # a later peak gives the nibble a (spurious) purpose
_CHEAP = 0.10


def _build_slots(n: int, *, with_dw: bool = False) -> list[SlotContext]:
    slots: list[SlotContext] = []
    for i in range(n):
        t = _START + timedelta(minutes=_INTERVAL * i)
        h = t.hour + t.minute / 60.0
        peak = 7.0 <= h < 8.0
        # Optional demand window at 01:30 so slot 0 falls inside the urgency window.
        is_dw_entry = with_dw and abs(h - 1.5) < 1e-9
        slots.append(
            SlotContext(
                slot_index=i,
                timestamp_iso=t.isoformat(),
                slot_interval_minutes=_INTERVAL,
                buy_price=_MORNING_PEAK if peak else _OVERNIGHT,
                sell_price=0.05,
                solar_kwh=0.0,
                consumption_kwh=0.10,
                is_demand_window_entry=is_dw_entry,
                is_demand_window_slot=(with_dw and 1.5 <= h < 3.0),
            )
        )
    return slots


def _plan(min_floor_gain: float, *, soc0: float = 10.0, with_dw: bool = False):
    slots = _build_slots(96, with_dw=with_dw)  # 8h horizon (01:00 -> 09:00)
    config = OptimizerConfig(
        min_soc_pct=10.0,
        max_soc_pct=100.0,
        demand_window_target_soc_pct=95.0,
        optimization_mode="self_consumption",
        effective_cheap_price=_CHEAP,
        base_cheap_price=_CHEAP,
        target_shortfall_penalty_per_pct=0.03,
        soc_bins=100,
        min_floor_charge_gain_pct=min_floor_gain,
    )
    return DPPlanner(config).plan(
        OptimizerInputs(
            cycle_id="floor-guard",
            initial_soc_pct=soc0,
            slots=slots,
            config=config,
            all_solcast=[],
        )
    )


def _overnight_floor_charges(result):
    """Grid charges taken while still at the floor, before the morning peak."""
    return [
        d
        for d in result.decisions
        if d.action in _CHARGE
        and d.predicted_soc_pct <= 12.5
        and datetime.fromisoformat(d.timestamp_iso).hour < 7
    ]


class TestFloorChargeGuard:
    def test_unguarded_nibbles_off_the_floor(self):
        """Documents the sawtooth: without the guard a floor charge fires overnight."""
        result = _plan(min_floor_gain=0.0)
        assert _overnight_floor_charges(result), (
            "expected the unguarded planner to take a marginal grid charge at the SOC "
            "floor (the initiating sawtooth nibble)"
        )

    def test_guard_removes_floor_nibble(self):
        """With the guard, no sub-threshold grid charge is taken at the floor."""
        result = _plan(min_floor_gain=2.0)
        charges = _overnight_floor_charges(result)
        assert charges == [], (
            "a sub-threshold grid charge at the SOC floor must be suppressed; got "
            f"charges at {[c.timestamp_iso for c in charges]}"
        )

    def test_guard_dormant_above_floor_buffer(self):
        """Starting above the buffer, charging is unaffected — the guard only acts at the floor."""
        guarded = [d for d in _plan(2.0, soc0=15.0).decisions if d.action in _CHARGE]
        unguarded = [d for d in _plan(0.0, soc0=15.0).decisions if d.action in _CHARGE]
        assert guarded and unguarded, (
            "above the floor buffer the guard must not block legitimate charging"
        )

    def test_guard_exempt_under_urgency(self):
        """A floor charge inside the urgency window (DW imminent) is still allowed."""
        result = _plan(2.0, with_dw=True)
        urgency_floor_charges = [
            d
            for d in result.decisions
            if d.action in _CHARGE and d.predicted_soc_pct <= 13.0
        ]
        assert urgency_floor_charges, (
            "the guard must exempt floor charging when a demand window is imminent "
            "(urgency window), otherwise it would block genuine pre-charge"
        )
