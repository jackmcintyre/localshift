"""Hard DW-target feasibility gate (issue #885).

The battery used to enter the evening demand window (DW) under target with no hard
backstop: target attainment was only a *soft* terminal penalty, structurally capped
below grid-charge prices, so the DP paid through it and held. The 3pm force-charge
guardrail that used to backstop this was removed 2026-06-12.

These tests encode:

1. The 2026-06-14 live repro: SOC 66%, target 95%, flat-ish price ~0.13 (below the
   max_pre_charge_price of 0.20), DW ~1h ahead, solar insufficient. With
   ``allow_dw_entry_under_target=False`` the plan must now reach target.
2. A sawtooth guard (#800): the hard gate must NOT introduce overnight or post-DW
   force-charging.
3. Graceful degradation: when the target is physically unreachable (not enough
   time/rate), the plan charges to the max feasible SOC without error.
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
_INTERVAL = 30


def _config(**overrides) -> OptimizerConfig:
    defaults = dict(
        min_soc_pct=10.0,
        max_soc_pct=100.0,
        demand_window_target_soc_pct=95.0,
        allow_dw_entry_under_target=False,
        optimization_mode="self_consumption",
        battery_capacity_kwh=13.5,
        charge_rate_kw=3.3,
        boost_charge_rate_kw=5.0,
        charge_efficiency=0.92,
        discharge_efficiency=0.95,
        effective_cheap_price=0.115,
        base_cheap_price=0.115,
        max_precharge_price=0.20,
        target_shortfall_penalty_per_pct=0.08,
        soc_bins=100,
    )
    defaults.update(overrides)
    return OptimizerConfig(**defaults)


# ---------------------------------------------------------------------------
# 2026-06-14 live repro: SOC 66%, target 95%, flat ~0.13 price, DW ~1h ahead.
# ---------------------------------------------------------------------------

_REPRO_START = datetime(2026, 6, 14, 13, 0)
# DW ~2h ahead -> entry at slot 4 (13:00..14:30 pre-DW, then 15:00 DW). Four boost-rate
# pre-DW slots make 95% physically reachable (max-feasible peak ~100%), so this is a
# genuine soft-penalty undercharge, NOT a physical-infeasibility case. Long horizon so
# there is ample post-DW time the gate must NOT charge in.
_REPRO_DW_ENTRY_IDX = 4
_REPRO_N_SLOTS = 16


def _repro_slots() -> list[SlotContext]:
    slots: list[SlotContext] = []
    for i in range(_REPRO_N_SLOTS):
        t = _REPRO_START + timedelta(minutes=_INTERVAL * i)
        is_dw = _REPRO_DW_ENTRY_IDX <= i < _REPRO_DW_ENTRY_IDX + 8  # 4h DW
        # Flat-ish ~0.13 buy price everywhere (below max_pre_charge_price 0.20),
        # evening peak inside the DW.
        buy = 0.30 if is_dw else 0.13
        slots.append(
            SlotContext(
                slot_index=i,
                timestamp_iso=t.isoformat(),
                slot_interval_minutes=_INTERVAL,
                buy_price=buy,
                sell_price=0.05,
                # Solar insufficient (net negative): tiny solar, real consumption.
                solar_kwh=0.05,
                consumption_kwh=0.3,
                is_demand_window_entry=(i == _REPRO_DW_ENTRY_IDX),
                is_demand_window_slot=is_dw,
            )
        )
    return slots


def _plan_repro(**config_overrides):
    slots = _repro_slots()
    config = _config(**config_overrides)
    return DPPlanner(config).plan(
        OptimizerInputs(
            cycle_id="repro-885",
            initial_soc_pct=66.0,
            slots=slots,
            config=config,
            all_solcast=[],
        )
    )


def test_repro_2026_06_14_reaches_target_under_hard_gate():
    """The 2026-06-14 live repro: plan must reach target with the hard gate.

    SOC 66 -> 95 on a 13.5 kWh battery needs ~3.9 kWh stored; the four boost-rate
    pre-DW slots can reach ~100% peak, so target is physically reachable. Price 0.13 <
    max_pre_charge_price 0.20, so the slots ARE eligible. The hard gate must route a
    charge through them so the DW-entry SOC lands at target.

    MUST FAIL on origin/main (soft penalty caps below price -> DW entry ~93.6%, shortfall
    ~1.4%) and PASS with the fix (shortfall -> ~0).
    """
    result = _plan_repro()
    assert result.success
    assert result.terminal_shortfall_pct < 0.5, (
        "hard gate must drive DW entry to target; got shortfall "
        f"{result.terminal_shortfall_pct}%"
    )
    # The SOC entering the demand window (start of the entry slot) should be at/near
    # target. This is the value the DP controls and reports as dw_entry_soc_pct.
    assert result.dw_entry_soc_pct is not None
    assert result.dw_entry_soc_pct >= 94.5, (
        f"DW entry SOC {result.dw_entry_soc_pct}% under target"
    )


def test_repro_charges_only_in_eligible_pre_dw_slots():
    """Charging happens, only pre-DW, and only at eligible (<= max_pre_charge_price) prices."""
    result = _plan_repro()
    charges = [d for d in result.decisions if d.action in _CHARGE]
    assert charges, "expected grid charging to reach target (was all-HOLD)"
    for c in charges:
        idx = next(
            d.slot_index for d in result.decisions if d.timestamp_iso == c.timestamp_iso
        )
        assert idx < _REPRO_DW_ENTRY_IDX, (
            f"charge at slot {idx} is not strictly pre-DW (entry={_REPRO_DW_ENTRY_IDX})"
        )
        assert c.buy_price <= 0.20, f"charge at ineligible price {c.buy_price}"


def test_does_not_charge_above_target():
    """The gate must not over-charge: peak SOC stays at/near target, not 100%."""
    result = _plan_repro()
    assert result.success
    # Target is 95; allow a small overshoot from bin granularity but never near 100.
    assert result.peak_soc_pct is not None
    assert result.peak_soc_pct <= 97.0, (
        f"plan over-charged above target; peak {result.peak_soc_pct}%"
    )


# ---------------------------------------------------------------------------
# Sawtooth guard (#800): no overnight / post-DW force-charging.
# ---------------------------------------------------------------------------


def test_hard_gate_no_post_dw_or_overnight_charge():
    """The hard gate is strictly bounded to pre-DW slots (guards #800)."""
    result = _plan_repro()
    assert result.success
    for d in result.decisions:
        if d.action not in _CHARGE:
            continue
        idx = d.slot_index
        assert idx < _REPRO_DW_ENTRY_IDX, (
            "no charging inside or after the DW (sawtooth guard); "
            f"got charge at slot {idx}"
        )


# ---------------------------------------------------------------------------
# Solar sufficiency: don't fight #816/#849.
# ---------------------------------------------------------------------------


def test_no_grid_charge_when_solar_reaches_target():
    """When solar alone projects to target, the hard gate must not force grid charging."""
    # Move the DW far out and give strong pre-DW solar so solar reaches target alone.
    start = datetime(2026, 6, 14, 8, 0)
    n = 24
    dw_entry = 16  # 8h ahead -> plenty of daytime solar before it
    slots: list[SlotContext] = []
    for i in range(n):
        t = start + timedelta(minutes=_INTERVAL * i)
        is_dw = dw_entry <= i < dw_entry + 6
        slots.append(
            SlotContext(
                slot_index=i,
                timestamp_iso=t.isoformat(),
                slot_interval_minutes=_INTERVAL,
                buy_price=0.30 if is_dw else 0.13,
                sell_price=0.05,
                # Strong solar before the DW, easily reaching target from 66%.
                solar_kwh=3.0 if (not is_dw and i < dw_entry) else 0.0,
                consumption_kwh=0.3,
                is_demand_window_entry=(i == dw_entry),
                is_demand_window_slot=is_dw,
            )
        )
    config = _config()
    result = DPPlanner(config).plan(
        OptimizerInputs(
            cycle_id="repro-885-solar",
            initial_soc_pct=66.0,
            slots=slots,
            config=config,
            all_solcast=[],
        )
    )
    assert result.success
    charges = [d for d in result.decisions if d.action in _CHARGE]
    assert charges == [], (
        "solar reaches target on its own — the hard gate must not grid-charge; "
        f"got charges at {[d.timestamp_iso for d in charges]}"
    )


# ---------------------------------------------------------------------------
# Graceful degradation: unreachable target -> max feasible, no error/empty plan.
# ---------------------------------------------------------------------------


def test_unreachable_target_degrades_to_max_feasible():
    """Insufficient time/rate before the DW: charge to max feasible, no error."""
    start = datetime(2026, 6, 14, 14, 30)
    # DW entry at slot 1 (only ONE 30-min pre-DW slot). From 20% to 95% needs ~10 kWh;
    # one boost slot stores ~2.3 kWh -> physically unreachable.
    n = 12
    dw_entry = 1
    slots: list[SlotContext] = []
    for i in range(n):
        t = start + timedelta(minutes=_INTERVAL * i)
        is_dw = dw_entry <= i < dw_entry + 6
        slots.append(
            SlotContext(
                slot_index=i,
                timestamp_iso=t.isoformat(),
                slot_interval_minutes=_INTERVAL,
                buy_price=0.30 if is_dw else 0.13,
                sell_price=0.05,
                solar_kwh=0.0,
                consumption_kwh=0.3,
                is_demand_window_entry=(i == dw_entry),
                is_demand_window_slot=is_dw,
            )
        )
    config = _config()
    result = DPPlanner(config).plan(
        OptimizerInputs(
            cycle_id="repro-885-unreachable",
            initial_soc_pct=20.0,
            slots=slots,
            config=config,
            all_solcast=[],
        )
    )
    assert result.success
    assert result.decisions, "plan must not be empty when target is unreachable"
    # Still a meaningful shortfall (target physically unreachable), but the single
    # eligible pre-DW slot should be used to charge as much as possible.
    assert result.terminal_shortfall_pct > 0.0
    pre_dw_charge = [
        d for d in result.decisions if d.action in _CHARGE and d.slot_index < dw_entry
    ]
    assert pre_dw_charge, "the one eligible pre-DW slot must be used to charge"
