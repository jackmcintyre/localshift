"""Anti-cycling probes on the 2026-08-31 full-precision horizon (#958).

Generalised from rerun_probes_full_precision.py, which hardcoded the 8/29
capture and an overnight window tied to its 21:05 start. Here the capture path
is an argument and the overnight window is derived from where solar actually
falls to zero, so the same runner works on any capture.
"""

import contextlib
import io
import runpy
import sys

sys.path.insert(0, ".")

CAP_PATH = sys.argv[1] if len(sys.argv) > 1 else "scratchpad/raw_2026-08-31.py"
cap = runpy.run_path(CAP_PATH)
RAW = cap["RAW"]
INITIAL_SOC = cap["INITIAL_SOC"]
DW_ENTRY_IDX = cap["DW_ENTRY_IDX"]
DW_END_IDX = cap["DW_END_IDX"]
ROLL = cap["DAY_ROLLOVER_IDX"]

with contextlib.redirect_stdout(io.StringIO()):
    from scratchpad.deferral_replay import build_config
from custom_components.localshift.engine.core import DPPlanner
from custom_components.localshift.engine.types import OptimizerInputs, SlotContext

# Overnight = the contiguous zero-solar run, derived from the capture.
_solar = [r[5] for r in RAW]
_last_sun = max(i for i, s in enumerate(_solar) if s > 0 and i < ROLL)
_dawn = min((i for i, s in enumerate(_solar) if s > 0 and i > _last_sun), default=len(RAW))
OVERNIGHT = list(range(_last_sun + 1, _dawn))


def build(round_to_cents: bool):
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
                solar_kwh=solar,
                consumption_kwh=cons,
                is_demand_window_entry=(idx == DW_ENTRY_IDX),
                is_demand_window_slot=(DW_ENTRY_IDX <= idx < DW_END_IDX),
                price_source="5min" if interval == 5 else "30min",
            )
        )
    return out


def run(label, slots, bins=None):
    cfg = build_config()
    if bins is not None:
        cfg.soc_bins = bins
    r = DPPlanner().plan(
        OptimizerInputs(cycle_id=label, initial_soc_pct=INITIAL_SOC, slots=slots, config=cfg)
    )
    acts = [str(getattr(d, "action", "")) for d in r.decisions]
    charges = [d for d in r.decisions if "charge_grid" in str(getattr(d, "action", ""))]
    overnight_kwh = sum(
        float(getattr(d, "grid_import_kwh", 0.0) or 0.0)
        for d in r.decisions
        if getattr(d, "slot_index", -1) in OVERNIGHT
        and "charge_grid" in str(getattr(d, "action", ""))
    )
    flips = sum(1 for a, b in zip(acts, acts[1:]) if a != b)
    print(
        f"{label:26s} charge_slots={len(charges):>2d}  overnight_grid={overnight_kwh:6.3f} kWh  "
        f"flips={flips:>2d}  dw_entry={r.dw_entry_soc_pct:6.2f}  net_cost={r.projected_net_cost:.4f}"
    )
    return r, acts


print(f"capture: {CAP_PATH}")
print(f"horizon: {len(RAW)} slots, {RAW[0][1]} -> {RAW[-1][1]} next day; initial_soc={INITIAL_SOC}%")
print(f"overnight window = slots {OVERNIGHT[0]}-{OVERNIGHT[-1]} "
      f"({RAW[OVERNIGHT[0]][1]}-{RAW[OVERNIGHT[-1]][1]}, zero solar)")
print(f"solar {sum(_solar):.2f} kWh vs load {sum(r[6] for r in RAW):.2f} kWh "
      f"-> {'SURPLUS' if sum(_solar) > sum(r[6] for r in RAW) else 'DEFICIT'}")
print(f"distinct buy prices: full={len({r[3] for r in RAW})}  cents={len({round(r[3], 2) for r in RAW})}\n")

print("--- A/B: price resolution, everything else identical ---")
r_full, a_full = run("full precision", build(False))
r_cent, a_cent = run("rounded to cents", build(True))
diff = [i for i, (x, y) in enumerate(zip(a_full, a_cent)) if x != y]
print(f"\naction differs on {len(diff)} of {len(a_full)} slots: {diff}")
for i in diff[:8]:
    print(
        f"  slot {i:>2d} {RAW[i][1]}  {RAW[i][3]:.4f} -> {round(RAW[i][3], 2):.2f}   "
        f"{a_full[i]}  vs  {a_cent[i]}"
    )

print("\n--- #800 sawtooth control: soc_bins sweep on full-precision prices ---")
for bins in (50, 100, 200):
    run(f"soc_bins={bins}", build(False), bins=bins)

print("\n--- same sweep on the cent-rounded series ---")
for bins in (50, 100, 200):
    run(f"soc_bins={bins}", build(True), bins=bins)
