"""Control: is the 2026-08-29 capture degenerate, or do the gates really not fire?

The captured horizon has 31.6 kWh solar against 28.6 kWh load, so the battery
reaches target on solar alone and the DP never grid-charges — every arm is
identical and the anti-cycling gates are never exercised. Scaling solar down
turns the same horizon into one that must grid-charge, which is where price
resolution can actually matter.
"""

import contextlib
import io
import runpy
import sys

sys.path.insert(0, ".")
cap = runpy.run_path("scratchpad/raw_2026-08-29.py")
RAW, INITIAL_SOC = cap["RAW"], cap["INITIAL_SOC"]
DW_ENTRY_IDX, DW_END_IDX, ROLL = cap["DW_ENTRY_IDX"], cap["DW_END_IDX"], cap["DAY_ROLLOVER_IDX"]

with contextlib.redirect_stdout(io.StringIO()):
    from scratchpad.deferral_replay import build_config
from custom_components.localshift.engine.core import DPPlanner
from custom_components.localshift.engine.types import OptimizerInputs, SlotContext

OVERNIGHT = list(range(28))


def build(cents: bool, solar_scale: float, soc: float):
    out = []
    for i, (idx, hhmm, interval, buy, sell, solar, cons) in enumerate(RAW):
        day = cap["START_DAY"] if i < ROLL else cap["NEXT_DAY"]
        out.append(
            SlotContext(
                slot_index=idx,
                timestamp_iso=f"{day}T{hhmm}:01+10:00",
                slot_interval_minutes=interval,
                buy_price=round(buy, 2) if cents else buy,
                sell_price=round(sell, 2) if cents else sell,
                solar_kwh=solar * solar_scale,
                consumption_kwh=cons,
                is_demand_window_entry=(idx == DW_ENTRY_IDX),
                is_demand_window_slot=(DW_ENTRY_IDX <= idx < DW_END_IDX),
                price_source="5min" if interval == 5 else "30min",
            )
        )
    return out


def run(label, slots):
    r = DPPlanner().plan(
        OptimizerInputs(cycle_id=label, initial_soc_pct=INITIAL_SOC, slots=slots, config=build_config())
    )
    acts = [str(getattr(d, "action", "")) for d in r.decisions]
    charges = [d for d in r.decisions if "charge_grid" in str(getattr(d, "action", ""))]
    on = sum(
        float(getattr(d, "grid_import_kwh", 0.0) or 0.0)
        for d in r.decisions
        if getattr(d, "slot_index", -1) in OVERNIGHT and "charge_grid" in str(getattr(d, "action", ""))
    )
    flips = sum(1 for a, b in zip(acts, acts[1:]) if a != b)
    print(
        f"  {label:22s} charge_slots={len(charges):>2d}  overnight_grid={on:6.3f}  "
        f"flips={flips:>2d}  dw_entry={r.dw_entry_soc_pct:6.2f}  net={r.projected_net_cost:.4f}"
    )
    return acts


for scale, soc in ((1.0, INITIAL_SOC), (0.35, INITIAL_SOC), (0.15, 25.0)):
    label = f"solar x{scale}, soc {soc:.0f}%"
    print(f"\n{label}  (solar {sum(r[5] for r in RAW) * scale:.1f} kWh vs load 28.6 kWh)")
    a_full = run("full precision", build(False, scale, soc))
    a_cent = run("rounded to cents", build(True, scale, soc))
    diff = [i for i, (x, y) in enumerate(zip(a_full, a_cent)) if x != y]
    print(f"  -> action differs on {len(diff)} of {len(a_full)} slots {diff[:12]}")
