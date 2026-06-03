"""North-star gate test for the pre-charge-to-target work.

This pins a deterministic "adverse winter day" scenario that reproduces the live
failure mode: solar alone reaches only ~55% by demand-window entry, pre-DW prices
straddle the cheap-charge threshold (two slots just below, three just above), and
the optimizer is asked to reach a 95% target before the DW.

It is the EXIT GATE for the autonomous tuning loop: the loop iterates on
optimizer config/logic until `test_precharge_reaches_target_before_dw` passes.
`measure_dw_entry_soc()` prints the current predicted DW-entry SOC so the loop can
track progress even while the assertion is still red.

Conditions mirror the live system on 2026-06-03 (cycle_penalty=0.15,
target_shortfall=0.08, effective_cheap_price=0.08, target=95).
"""

from __future__ import annotations

from custom_components.localshift.engine.optimizer_dp import DPPlanner
from custom_components.localshift.engine.types import (
    OptimizerConfig,
    OptimizerInputs,
    SlotContext,
)

TARGET_SOC = 95.0
# soc_bins=50 over 0-100 => 2% discretization; 95 lands between the 94 and 96 bins.
# "Reached target" = within one bin of the target.
GATE_TOLERANCE = 2.0

# Live config values on 2026-06-03 (see handoff). The loop tunes these.
LIVE_CYCLE_PENALTY = 0.15
LIVE_TARGET_SHORTFALL = 0.08
LIVE_EFFECTIVE_CHEAP_PRICE = 0.08


def _config(
    *,
    cycle_penalty: float = LIVE_CYCLE_PENALTY,
    target_shortfall: float = LIVE_TARGET_SHORTFALL,
    effective_cheap_price: float = LIVE_EFFECTIVE_CHEAP_PRICE,
) -> OptimizerConfig:
    return OptimizerConfig(
        battery_capacity_kwh=13.5,
        charge_rate_kw=3.3,
        boost_charge_rate_kw=5.0,
        solar_charge_rate_kw=5.0,
        discharge_rate_kw=5.0,
        charge_efficiency=0.92,
        discharge_efficiency=0.95,
        min_soc_pct=10.0,
        max_soc_pct=100.0,
        demand_window_target_soc_pct=TARGET_SOC,
        optimization_mode="self_consumption",
        target_shortfall_penalty_per_pct=target_shortfall,
        cycle_penalty_per_kwh=cycle_penalty,
        effective_cheap_price=effective_cheap_price,
        forecast_horizon_hours=24.0,
        demand_window_import_penalty_per_kwh=0.0,
        demand_charge_active=True,
        allow_dw_entry_under_target=True,
    )


def build_adverse_inputs(**config_kwargs) -> OptimizerInputs:
    """Adverse winter day: solar-only tops out ~55% by DW entry, prices straddle
    the cheap threshold, target is 95%. Hourly slots, 10:00 -> next-day 09:00."""
    slots: list[SlotContext] = []
    idx = 0

    # --- Pre-DW window 10:00-14:00 (5 slots) ---
    # Modest winter solar (~0.8 kWh/hr net of load) => solar-only ~ +30 pts over 5h.
    # Prices: two slots <= 0.08 (feasible to charge today), three just above.
    pre_dw = [
        (10, 0.079, 1.2),
        (11, 0.079, 1.3),
        (12, 0.082, 1.4),
        (13, 0.082, 1.3),
        (14, 0.082, 1.0),
    ]
    for hour, buy, solar in pre_dw:
        slots.append(
            SlotContext(
                slot_index=idx,
                timestamp_iso=f"2026-06-03T{hour:02d}:00:00+10:00",
                slot_interval_minutes=60,
                buy_price=buy,
                sell_price=0.03,
                solar_kwh=solar,
                consumption_kwh=0.4,
                is_demand_window_slot=False,
            )
        )
        idx += 1

    # --- Demand window 15:00-20:00 (6 slots): solar gone, heavy load, peak price ---
    for offset in range(6):
        hour = 15 + offset
        slots.append(
            SlotContext(
                slot_index=idx,
                timestamp_iso=f"2026-06-03T{hour:02d}:00:00+10:00",
                slot_interval_minutes=60,
                buy_price=0.13,
                sell_price=0.10,
                solar_kwh=0.0,
                consumption_kwh=1.2,
                is_demand_window_slot=True,
                is_demand_window_entry=(offset == 0),
            )
        )
        idx += 1

    # --- Overnight 21:00 -> 09:00 (13 slots): no solar, light load, cheap ---
    for step in range(13):
        hour = (21 + step) % 24
        day = 3 if (21 + step) < 24 else 4
        slots.append(
            SlotContext(
                slot_index=idx,
                timestamp_iso=f"2026-06-0{day}T{hour:02d}:00:00+10:00",
                slot_interval_minutes=60,
                buy_price=0.09,
                sell_price=0.03,
                solar_kwh=0.0,
                consumption_kwh=0.4,
                is_demand_window_slot=False,
            )
        )
        idx += 1

    return OptimizerInputs(
        cycle_id="adverse-precharge-gate",
        initial_soc_pct=25.0,
        slots=slots,
        config=_config(**config_kwargs),
    )


def dw_entry_soc(result) -> float:
    """SOC at the instant of *entering* the demand window — the predicted SOC at the
    END of the last pre-DW slot (the boundary the optimizer must hit by 15:00).

    NOT the 15:00 slot's own predicted_soc_pct, which is measured AFTER the first DW
    hour's discharge and so understates true entry SOC by a full slot of DW load.
    """
    prev = None
    for d in result.decisions:
        if int(d.timestamp_iso[11:13]) >= 15:
            if prev is None:
                raise AssertionError("plan starts inside the DW; no pre-DW entry slot")
            return prev.predicted_soc_pct
        prev = d
    raise AssertionError("no demand-window slot found in plan")


def measure_dw_entry_soc(**config_kwargs) -> float:
    result = DPPlanner().plan(build_adverse_inputs(**config_kwargs))
    assert result.success, "planner failed on adverse scenario"
    soc = dw_entry_soc(result)
    print(f"DW_ENTRY_SOC={soc:.2f} (target={TARGET_SOC}, cfg={config_kwargs or 'live'})")
    return soc


def test_precharge_reaches_target_before_dw():
    """EXIT GATE: with live config, the plan should pre-charge to ~target by DW entry."""
    soc = measure_dw_entry_soc()
    assert soc >= TARGET_SOC - GATE_TOLERANCE, (
        f"DW-entry SOC {soc:.1f}% is below target {TARGET_SOC}% "
        f"(tolerance {GATE_TOLERANCE}). The optimizer is under-charging."
    )
