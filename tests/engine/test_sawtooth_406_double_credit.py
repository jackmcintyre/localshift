"""Regression: #406 self-consumption double-credit drove the overnight sawtooth.

Live incident 2026-06-29 (flat/elevated-price day, buy $0.15-0.20, no genuine cheap
window). The optimizer charged the battery in three wasteful overnight pulses
(21:20-22:00, ~02:30, 06:00) that each drained to the SOC floor through high overnight
load before the real demand-window pre-charge at midday — ~11 kWh of grid import that
funded 0% of the DW target, pure round-trip-loss churn.

Root cause (confirmed by deterministic replay of the live plan): ObjectiveTerms.net_cost
subtracted self_consumption_value (battery_for_load * buy_price) on top of an import_cost
that ALREADY netted out battery-served load (transitions._transition_hold_deficit). Stored
energy was valued at ~2x retail, so thin overnight charge-and-drain looked profitable
(~5c/kWh apparent profit on a ~2c/kWh round-trip loss). The solar_opportunity_penalty base
had been doubled to compensate for the same credit; both halves are removed together.

The slot table below is the EXACT live plan input (sensor.localshift_optimizer_plan_detailed,
55 slots, DW entry idx 42 = 15:00). Replaying it goes RED on the double-credit (overnight
charge-and-drain reappears, ~11 kWh) and GREEN with the fix (zero overnight charging), while
the legitimate midday demand-window pre-charge and the 95% target are preserved.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from custom_components.localshift.engine.core import DPPlanner
from custom_components.localshift.engine.types import (
    ObjectiveTerms,
    OptimizerConfig,
    OptimizerInputs,
    PlannerAction,
    SlotContext,
)

CHARGE = (PlannerAction.CHARGE_GRID_NORMAL, PlannerAction.CHARGE_GRID_BOOST)
DW_ENTRY_IDX = 42  # 15:00 — plan stops charging and starts discharging here
OVERNIGHT_END = 26  # idx < 26 are the overnight/early-morning slots (21:20-06:30)
INITIAL_SOC = 12.8

# (interval_min, buy, sell, solar_kwh, consumption_kwh) — verbatim live 2026-06-29 plan.
LIVE = [
    (5, 0.160, 0.1000, 0.0000, 0.1030), (5, 0.160, 0.1000, 0.0000, 0.1030),
    (5, 0.180, 0.1033, 0.0000, 0.1030), (5, 0.180, 0.0967, 0.0000, 0.1030),
    (5, 0.170, 0.0933, 0.0000, 0.1030), (5, 0.160, 0.0867, 0.0000, 0.1030),
    (5, 0.160, 0.0833, 0.0000, 0.1030), (5, 0.150, 0.0800, 0.0000, 0.1030),
    (30, 0.150, 0.0800, 0.0000, 0.6350), (30, 0.160, 0.0900, 0.0000, 0.6650),
    (30, 0.160, 0.0900, 0.0000, 0.5870), (30, 0.160, 0.0900, 0.0000, 0.5420),
    (30, 0.160, 0.0900, 0.0000, 0.5520), (30, 0.160, 0.0900, 0.0000, 0.5250),
    (30, 0.160, 0.0900, 0.0000, 0.4760), (30, 0.160, 0.0900, 0.0000, 0.3500),
    (30, 0.160, 0.0900, 0.0000, 0.3260), (30, 0.160, 0.0900, 0.0000, 0.3210),
    (30, 0.160, 0.0900, 0.0000, 0.4050), (30, 0.160, 0.0900, 0.0000, 0.4220),
    (30, 0.180, 0.1000, 0.0000, 0.3920), (30, 0.170, 0.1000, 0.0000, 0.3860),
    (30, 0.150, 0.0800, 0.0000, 0.3720), (30, 0.150, 0.0800, 0.0000, 0.3690),
    (30, 0.150, 0.0900, 0.0000, 0.2810), (30, 0.170, 0.1000, 0.0000, 0.2630),
    (30, 0.200, 0.1300, 0.0110, 0.3920), (30, 0.210, 0.1300, 0.0460, 0.4170),
    (30, 0.220, 0.1500, 0.1410, 0.4160), (30, 0.180, 0.1100, 0.3230, 0.4150),
    (30, 0.170, 0.1000, 0.5040, 0.5850), (30, 0.190, 0.1200, 0.6680, 0.6190),
    (30, 0.180, 0.1000, 0.7790, 0.8330), (30, 0.180, 0.1000, 0.8840, 0.8760),
    (30, 0.170, 0.0900, 0.9610, 0.9250), (30, 0.170, 0.0900, 0.9930, 0.9350),
    (30, 0.170, 0.0900, 0.9820, 0.9410), (30, 0.170, 0.0800, 0.9210, 0.9420),
    (30, 0.170, 0.0900, 0.8280, 0.6830), (30, 0.180, 0.1000, 0.6970, 0.6310),
    (30, 0.180, 0.1000, 0.5680, 0.5730), (30, 0.190, 0.1100, 0.4090, 0.5620),
    (30, 0.200, 0.1300, 0.2460, 0.4280), (30, 0.200, 0.1200, 0.0880, 0.4020),
    (30, 0.190, 0.1600, 0.0210, 0.4570), (30, 0.190, 0.1600, 0.0030, 0.4680),
    (30, 0.210, 0.1700, 0.0000, 0.6480), (30, 0.230, 0.2000, 0.0000, 0.6840),
    (30, 0.230, 0.1900, 0.0000, 0.6600), (30, 0.280, 0.2400, 0.0000, 0.6550),
    (30, 0.470, 0.4100, 0.0000, 0.5690), (30, 0.220, 0.1800, 0.0000, 0.5520),
    (30, 0.220, 0.1800, 0.0000, 0.5310), (30, 0.190, 0.1600, 0.0000, 0.5270),
    (30, 0.200, 0.2000, 0.0000, 0.4320),
]


def _slots() -> list[SlotContext]:
    base = datetime(2026, 6, 29, 21, 15)
    out: list[SlotContext] = []
    t = base
    for i, (interval, buy, sell, solar, cons) in enumerate(LIVE):
        out.append(
            SlotContext(
                slot_index=i,
                timestamp_iso=t.isoformat(),
                slot_interval_minutes=interval,
                buy_price=buy,
                sell_price=sell,
                solar_kwh=solar,
                consumption_kwh=cons,
                is_demand_window_entry=(i == DW_ENTRY_IDX),
                is_demand_window_slot=(i >= DW_ENTRY_IDX),
            )
        )
        t += timedelta(minutes=interval)
    return out


def _config() -> OptimizerConfig:
    return OptimizerConfig(
        optimization_mode="self_consumption",
        battery_capacity_kwh=13.5,
        charge_efficiency=0.92,
        discharge_efficiency=0.95,
        min_soc_pct=10.0,
        max_soc_pct=100.0,
        demand_window_target_soc_pct=95.0,
        allow_dw_entry_under_target=False,
        effective_cheap_price=0.18,
        base_cheap_price=0.16,
        max_precharge_price=0.20,
        target_shortfall_penalty_per_pct=0.10,
        switching_penalty=0.08,
        min_cycle_saving=0.25,
        soc_bins=100,
    )


def _plan(monkeypatch=None, *, double_credit=False):
    """Run the planner; with double_credit=True, restore the #406 bug to prove RED."""
    if double_credit:
        orig = ObjectiveTerms.net_cost.fget
        monkeypatch.setattr(
            ObjectiveTerms,
            "net_cost",
            property(lambda self: orig(self) - self.self_consumption_value),
        )
    inputs = OptimizerInputs(
        cycle_id="sawtooth-406", initial_soc_pct=INITIAL_SOC,
        slots=_slots(), config=_config(), all_solcast=[],
    )
    return DPPlanner(_config()).plan(inputs)


def _overnight_charge_kwh(result) -> float:
    return sum(
        max(0.0, getattr(d, "grid_import_kwh", 0.0))
        for d in result.decisions
        if d.slot_index < OVERNIGHT_END and d.action in CHARGE
    )


def test_no_overnight_sawtooth_with_fix():
    """GREEN: the corrected objective does not charge-and-drain overnight."""
    result = _plan()
    assert result.success
    overnight = [d.slot_index for d in result.decisions[:OVERNIGHT_END] if d.action in CHARGE]
    assert overnight == [], f"overnight sawtooth charges reappeared at {overnight}"
    assert _overnight_charge_kwh(result) == 0.0


def test_double_credit_reproduces_sawtooth(monkeypatch):
    """RED guard: restoring the #406 double-credit brings the overnight churn back.

    Pins the root cause — if this stops failing under the bug, the credit is no longer
    driving the sawtooth and the regression guard above would be testing nothing.
    """
    result = _plan(monkeypatch, double_credit=True)
    assert result.success
    overnight = [d.slot_index for d in result.decisions[:OVERNIGHT_END] if d.action in CHARGE]
    assert overnight, "expected the double-credit to reintroduce overnight charging"
    assert _overnight_charge_kwh(result) > 5.0  # live double-credit drew ~11 kWh overnight


def test_demand_window_precharge_preserved():
    """The fix must not regress legitimate DW pre-charge: target still funded midday."""
    result = _plan()
    assert result.success
    midday_charges = [
        d.slot_index for d in result.decisions[OVERNIGHT_END:DW_ENTRY_IDX] if d.action in CHARGE
    ]
    assert midday_charges, "the plan must still pre-charge for the demand window"
    assert result.decisions[DW_ENTRY_IDX].predicted_soc_pct >= 90.0
