"""Find the marginal regime on a REAL price series, then test price resolution there (#958).

The blocker on #958 has been that every captured horizon is surplus-solar, so
the DP grid-charges zero times and every A/B arm is identical. Waiting for an
overcast day is one answer; this is the other.

Hold the real, full-precision 2026-08-31 price series fixed and scale only the
solar forecast. That walks the horizon from surplus through the marginal band
into deficit while keeping under test exactly the thing #958 is about — real
Amber prices at sub-cent resolution. At each scale, run the full-precision and
cent-rounded arms and report whether the plan differs.

Surplus makes the grid charge unnecessary and deep deficit makes it compulsory;
if price resolution ever matters, it matters in between.
"""

import contextlib
import io
import runpy
import sys

sys.path.insert(0, ".")

cap = runpy.run_path("scratchpad/raw_2026-08-31.py")
RAW = cap["RAW"]
INITIAL_SOC = cap["INITIAL_SOC"]
DW_ENTRY_IDX, DW_END_IDX, ROLL = cap["DW_ENTRY_IDX"], cap["DW_END_IDX"], cap["DAY_ROLLOVER_IDX"]

with contextlib.redirect_stdout(io.StringIO()):
    from scratchpad.deferral_replay import build_config
from custom_components.localshift.engine.core import DPPlanner
from custom_components.localshift.engine.types import OptimizerInputs, SlotContext


def build(round_to_cents: bool, solar_scale: float):
    out = []
    for i, (idx, hhmm, interval, buy, sell, solar, cons) in enumerate(RAW):
        day = cap["START_DAY"] if i < ROLL else cap["NEXT_DAY"]
        out.append(
            SlotContext(
                slot_index=idx,
                timestamp_iso=f"{day}T{hhmm}:01+10:00",
                slot_interval_minutes=interval,
                buy_price=round(buy, 2) if round_to_cents else buy,
                sell_price=round(sell, 2) if round_to_cents else sell,
                solar_kwh=solar * solar_scale,
                consumption_kwh=cons,
                is_demand_window_entry=(idx == DW_ENTRY_IDX),
                is_demand_window_slot=(DW_ENTRY_IDX <= idx < DW_END_IDX),
                price_source="5min" if interval == 5 else "30min",
            )
        )
    return out


def plan(slots, label):
    r = DPPlanner().plan(
        OptimizerInputs(cycle_id=label, initial_soc_pct=INITIAL_SOC, slots=slots, config=build_config())
    )
    acts = [str(getattr(d, "action", "")) for d in r.decisions]
    charges = sum(1 for a in acts if "charge_grid" in a)
    kwh = sum(
        float(getattr(d, "grid_import_kwh", 0.0) or 0.0)
        for d in r.decisions
        if "charge_grid" in str(getattr(d, "action", ""))
    )
    return r, acts, charges, kwh


load = sum(r[6] for r in RAW)
print(f"real horizon: solar {sum(r[5] for r in RAW):.2f} kWh vs load {load:.2f} kWh, "
      f"initial_soc={INITIAL_SOC}%, 43 distinct full-precision prices\n")
print(f"{'scale':>6} {'solar':>7} {'regime':>9} | {'full precision':>28} | {'cents':>28} | differs")
print("-" * 100)

for scale in (1.0, 0.8, 0.6, 0.5, 0.4, 0.35, 0.3, 0.25, 0.2, 0.15, 0.1, 0.05, 0.0):
    solar = sum(r[5] for r in RAW) * scale
    rf, af, cf, kf = plan(build(False, scale), f"full-{scale}")
    rc, ac, cc, kc = plan(build(True, scale), f"cent-{scale}")
    diff = [i for i, (x, y) in enumerate(zip(af, ac)) if x != y]
    regime = "surplus" if solar > load else ("marginal" if solar > load * 0.55 else "deficit")
    print(
        f"{scale:>6.2f} {solar:>6.1f}k {regime:>9} | "
        f"chg={cf:>2d} kWh={kf:>6.3f} dw={rf.dw_entry_soc_pct:>6.2f} ${rf.projected_net_cost:>7.4f} | "
        f"chg={cc:>2d} kWh={kc:>6.3f} dw={rc.dw_entry_soc_pct:>6.2f} ${rc.projected_net_cost:>7.4f} | "
        f"{len(diff):>2d} slots {diff[:6] if diff else ''}"
    )
