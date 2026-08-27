"""Replay the live 2026-08-25 12:32 horizon and ablate the futile-cycling penalty.

Inputs are the exact per-slot series published by sensor.localshift_optimizer_plan_detailed
at 12:32:43 (initial_soc 21.986%, target 95%), so a faithful replay must reproduce the live
plan: hold/hold/hold then charge from slot 3, dw_entry 90.84%, terminal shortfall 4.16%.
"""

import sys

sys.path.insert(0, ".")

from custom_components.localshift.engine.core import DPPlanner
from custom_components.localshift.engine.types import (
    OptimizerConfig,
    OptimizerInputs,
    SlotContext,
)

# slot_idx, hh:mm, interval_min, buy, sell, solar_kwh, consumption_kwh
RAW = [
    (0, "12:30", 5, 0.16, 0.07, 0.035, 0.127),
    (1, "12:35", 5, 0.16, 0.07, 0.036, 0.127),
    (2, "12:40", 5, 0.16, 0.07, 0.036, 0.127),
    (3, "12:45", 5, 0.16, 0.07, 0.036, 0.127),
    (4, "12:50", 5, 0.16, 0.07, 0.036, 0.127),
    (5, "12:55", 5, 0.16, 0.07, 0.037, 0.127),
    (6, "13:00", 5, 0.15, 0.07, 0.043, 0.127),
    (7, "13:05", 5, 0.15, 0.07, 0.114, 0.127),
    (8, "13:10", 5, 0.15, 0.07, 0.114, 0.127),
    (9, "13:15", 5, 0.15, 0.07, 0.114, 0.127),
    (10, "13:20", 5, 0.15, 0.0667, 0.114, 0.127),
    (11, "13:25", 5, 0.15, 0.065, 0.116, 0.127),
    (12, "13:30", 30, 0.15, 0.06, 0.638, 0.766),
    (13, "14:00", 30, 0.16, 0.07, 0.552, 0.740),
    (14, "14:30", 30, 0.16, 0.07, 0.501, 0.721),
    (15, "15:00", 30, 0.15, 0.08, 0.428, 0.609),
    (16, "15:30", 30, 0.16, 0.09, 0.295, 0.570),
    (17, "16:00", 30, 0.16, 0.12, 0.132, 0.590),
    (18, "16:30", 30, 0.16, 0.12, 0.041, 0.449),
    (19, "17:00", 30, 0.17, 0.14, 0.008, 0.749),
    (20, "17:30", 30, 0.18, 0.15, 0.0, 0.749),
    (21, "18:00", 30, 0.18, 0.15, 0.0, 0.751),
    (22, "18:30", 30, 0.19, 0.16, 0.0, 0.751),
    (23, "19:00", 30, 0.19, 0.15, 0.0, 0.650),
    (24, "19:30", 30, 0.18, 0.14, 0.0, 0.650),
    (25, "20:00", 30, 0.19, 0.16, 0.0, 0.649),
    (26, "20:30", 30, 0.20, 0.16, 0.0, 0.649),
    (27, "21:00", 30, 0.19, 0.12, 0.0, 0.732),
    (28, "21:30", 30, 0.19, 0.11, 0.0, 0.732),
    (29, "22:00", 30, 0.18, 0.10, 0.0, 0.497),
    (30, "22:30", 30, 0.17, 0.09, 0.0, 0.497),
    (31, "23:00", 30, 0.16, 0.09, 0.0, 0.343),
    (32, "23:30", 30, 0.15, 0.08, 0.0, 0.343),
    (33, "00:00", 30, 0.15, 0.08, 0.0, 0.253),
    (34, "00:30", 30, 0.15, 0.08, 0.0, 0.253),
    (35, "01:00", 30, 0.16, 0.09, 0.0, 0.242),
    (36, "01:30", 30, 0.16, 0.09, 0.0, 0.242),
    (37, "02:00", 30, 0.16, 0.09, 0.0, 0.241),
    (38, "02:30", 30, 0.17, 0.09, 0.0, 0.242),
    (39, "03:00", 30, 0.17, 0.09, 0.0, 0.439),
    (40, "03:30", 30, 0.17, 0.09, 0.0, 0.439),
    (41, "04:00", 30, 0.20, 0.20, 0.0, 0.381),
    (42, "04:30", 30, 0.20, 0.20, 0.0, 0.381),
    (43, "05:00", 30, 0.20, 0.20, 0.0, 0.376),
    (44, "05:30", 30, 0.20, 0.20, 0.0, 0.375),
    (45, "06:00", 30, 0.20, 0.20, 0.0, 0.339),
    (46, "06:30", 30, 0.20, 0.20, 0.022, 0.339),
    (47, "07:00", 30, 0.20, 0.20, 0.086, 0.733),
    (48, "07:30", 30, 0.20, 0.20, 0.233, 0.732),
    (49, "08:00", 30, 0.20, 0.20, 0.417, 0.574),
    (50, "08:30", 30, 0.20, 0.20, 0.561, 0.574),
    (51, "09:00", 30, 0.20, 0.20, 0.619, 0.654),
    (52, "09:30", 30, 0.20, 0.20, 0.673, 0.655),
    (53, "10:00", 30, 0.20, 0.20, 0.668, 0.884),
    (54, "10:30", 30, 0.20, 0.20, 0.544, 0.885),
    (55, "11:00", 30, 0.20, 0.20, 0.489, 1.048),
    (56, "11:30", 30, 0.20, 0.20, 0.515, 1.047),
    (57, "12:00", 30, 0.20, 0.20, 0.525, 1.015),
    (58, "12:30", 30, 0.20, 0.20, 0.523, 0.0),
]

DW_ENTRY_IDX = 15  # 15:00 — the demand-window entry boundary
DW_END_IDX = 27  # 21:00 — first slot past the window
INITIAL_SOC = 21.986


def build_slots():
    out = []
    for idx, hhmm, interval, buy, sell, solar, cons in RAW:
        day = "2026-08-25" if idx <= 32 else "2026-08-26"
        out.append(
            SlotContext(
                slot_index=idx,
                timestamp_iso=f"{day}T{hhmm}:01+10:00",
                slot_interval_minutes=interval,
                buy_price=buy,
                sell_price=sell,
                solar_kwh=solar,
                consumption_kwh=cons,
                is_demand_window_entry=(idx == DW_ENTRY_IDX),
                is_demand_window_slot=(DW_ENTRY_IDX <= idx < DW_END_IDX),
                price_source="5min" if interval == 5 else "30min",
            )
        )
    return out


def build_config():
    # The live config_options block from sensor.localshift_optimizer_summary.
    return OptimizerConfig(
        demand_window_target_soc_pct=95.0,
        min_soc_pct=10.0,
        switching_penalty=0.08,
        target_shortfall_penalty_per_pct=0.10,
        min_cycle_saving=0.25,
        max_precharge_price=0.20,
        optimization_mode="self_consumption",
        forecast_horizon_hours=24.5,
    )


def run(label, config):
    planner = DPPlanner()
    result = planner.plan(
        OptimizerInputs(
            cycle_id=label,
            initial_soc_pct=INITIAL_SOC,
            slots=build_slots(),
            config=config,
        )
    )
    decisions = result.decisions
    first_charge = next(
        (d for d in decisions if "charge_grid" in str(getattr(d, "action", ""))), None
    )
    fc_idx = getattr(first_charge, "slot_index", None) if first_charge else None
    fc_time = RAW[fc_idx][1] if fc_idx is not None else "never"
    dw = getattr(result, "dw_entry_soc_pct", None)
    sf = getattr(result, "terminal_shortfall_pct", None)
    n_charge = sum(
        1 for d in decisions if "charge_grid" in str(getattr(d, "action", ""))
    )
    print(
        f"{label:28s} first_charge={fc_time:>6s} (slot {str(fc_idx):>4s})  "
        f"dw_entry={dw if dw is None else round(dw, 2):>6}  "
        f"shortfall={sf if sf is None else round(sf, 2):>5}  "
        f"charge_slots={n_charge:>2d}  net_cost={round(result.projected_net_cost, 4)}"
    )
    return result


print(f"initial_soc={INITIAL_SOC:.3f}%  target=95%  DW entry=15:00 (slot 15)\n")
print("--- baseline: reproduce the live 12:32 plan ---")
run("live-as-shipped", build_config())


# --------------------------------------------------------------------------
# Ablations
# --------------------------------------------------------------------------
from unittest.mock import patch

import custom_components.localshift.engine.penalties as pen_mod

FUTILE = "custom_components.localshift.engine.core.get_futile_cycling_penalty_factor"


def overnight_charge_kwh(result):
    """Grid charge scheduled in the post-DW overnight block (slots 27..45)."""
    total = 0.0
    for d in result.decisions:
        idx = getattr(d, "slot_index", -1)
        if 27 <= idx <= 45 and "charge_grid" in str(getattr(d, "action", "")):
            total += float(getattr(d, "grid_import_kwh", 0.0) or 0.0)
    return total


print("\n--- ablation A: futile-cycling penalty OFF everywhere (blunt) ---")
with patch(FUTILE, return_value=0.0):
    a = run("futile-off-everywhere", build_config())

print("\n--- ablation B: shortfall penalty raised, futile untouched ---")
for pen in (0.20, 0.30, 0.50):
    cfg = build_config()
    cfg.target_shortfall_penalty_per_pct = pen
    run(f"shortfall-penalty={pen:.2f}", cfg)

print("\n--- proposed fix: futile OFF only for urgency-window pre-charge ---")
_real_futile = pen_mod.get_futile_cycling_penalty_factor


def scoped_futile(
    action,
    slot_idx,
    slots,
    config,
    soc_after_charge_pct,
    charge_kwh,
    terminal_penalty_idx=None,
):
    """Exempt a charge that is funding the DW target, exactly as #860 did for min-cycle."""
    if (
        terminal_penalty_idx is not None
        and slot_idx < terminal_penalty_idx
        and soc_after_charge_pct <= config.demand_window_target_soc_pct
    ):
        return 0.0
    return _real_futile(
        action,
        slot_idx,
        slots,
        config,
        soc_after_charge_pct,
        charge_kwh,
        terminal_penalty_idx,
    )


with patch(FUTILE, side_effect=scoped_futile):
    c = run("futile-off-in-urgency-window", build_config())

print("\n--- #800 sawtooth control: overnight grid charge (slots 21:00-06:00) ---")
base = run("baseline", build_config())
print(
    f"  baseline                    overnight grid charge = {overnight_charge_kwh(base):.3f} kWh"
)
print(
    f"  futile-off-everywhere       overnight grid charge = {overnight_charge_kwh(a):.3f} kWh"
)
print(
    f"  futile-off-in-urgency-window overnight grid charge = {overnight_charge_kwh(c):.3f} kWh"
)


print(
    "\n--- probe: is 91.4% the PHYSICAL ceiling, or is the DP leaving charge on the table? ---"
)
from custom_components.localshift.engine.core import DPPlanner as _DP
from custom_components.localshift.engine.types import OptimizerConfig as _OC

for soc in (21.986, 24.6, 33.56):
    mins = _DP._boost_minutes_to_close_gap(_OC(demand_window_target_soc_pct=95.0), soc)
    print(f"  engine charge model: {soc:6.2f}% -> 95% needs {mins:6.1f} min at boost")
print("  minutes from 12:30 to the 15:00 demand window = 150.0")

print("\n  every economic brake released at once:")
cfg = build_config()
cfg.min_cycle_saving = 0.0
cfg.switching_penalty = 0.0
cfg.target_shortfall_penalty_per_pct = 5.0
with patch(FUTILE, return_value=0.0):
    run("all-brakes-off", cfg)
