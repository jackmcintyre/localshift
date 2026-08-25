"""Where does head-of-horizon charge go? Bin-by-bin SOC through slots 0-15."""
import sys, io, contextlib
sys.path.insert(0, ".")
from unittest.mock import patch
with contextlib.redirect_stdout(io.StringIO()):
    from scratchpad.deferral_replay import build_slots, build_config, INITIAL_SOC, RAW
from custom_components.localshift.engine.core import DPPlanner
from custom_components.localshift.engine.types import OptimizerInputs
import custom_components.localshift.engine.core as core_mod

_real = core_mod._constraints_feasible_actions


def force(n):
    def spy(soc_pct, slot, config, slot_idx=0, slots=None, terminal_penalty_idx=None, **kw):
        acts = _real(soc_pct, slot, config, slot_idx=slot_idx, slots=slots,
                     terminal_penalty_idx=terminal_penalty_idx, **kw)
        if slot_idx < n:
            c = [a for a in acts if "CHARGE_GRID" in a.name]
            if c:
                return c
        return acts
    return spy


def traj(n):
    cfg = build_config()
    ctx = (patch.object(core_mod, "_constraints_feasible_actions", side_effect=force(n))
           if n else contextlib.nullcontext())
    with ctx:
        r = DPPlanner().plan(OptimizerInputs(cycle_id=f"f{n}", initial_soc_pct=INITIAL_SOC,
                                             slots=build_slots(), config=cfg))
    return {getattr(d, "slot_index", -1): d for d in r.decisions}, r


a, ra = traj(0)
b, rb = traj(3)
print(f"{'slot':>4} {'time':>6} {'int':>4} | {'DP-choice':>22} | {'forced-0..2':>22} | delta")
print(f"{'':>4} {'':>6} {'':>4} | {'action':>14}{'soc':>8} | {'action':>14}{'soc':>8} |")
for i in range(0, 16):
    da, db = a.get(i), b.get(i)
    if not da or not db:
        continue
    sa = getattr(da, "predicted_soc_pct", 0.0)
    sb = getattr(db, "predicted_soc_pct", 0.0)
    aa = str(getattr(da, "action", "")).split(".")[-1].replace("CHARGE_GRID_", "cg_").lower()
    ab = str(getattr(db, "action", "")).split(".")[-1].replace("CHARGE_GRID_", "cg_").lower()
    print(f"{i:>4} {RAW[i][1]:>6} {RAW[i][2]:>3}m | {aa:>14}{sa:>8.2f} | {ab:>14}{sb:>8.2f} | {sb-sa:+6.2f}")


print("\n--- was 95% reachable at all? force a CONTINUOUS pre-charge, slots 0..14 ---")
for n in (0, 3, 6, 12, 15):
    _, r = traj(n)
    print(f"  forced charge slots 0..{n-1:<2d}  dw_entry={r.dw_entry_soc_pct:6.2f}  "
          f"shortfall={r.terminal_shortfall_pct:5.2f}  net_cost={r.projected_net_cost:.4f}")

print("\n--- does the terminal penalty influence the plan AT ALL? ---")
from custom_components.localshift.engine.types import OptimizerInputs as _OI
for pen in (0.0, 0.03, 0.10, 1.00, 100.0):
    cfg = build_config()
    cfg.target_shortfall_penalty_per_pct = pen
    r = DPPlanner().plan(_OI(cycle_id=f"p{pen}", initial_soc_pct=INITIAL_SOC,
                             slots=build_slots(), config=cfg))
    print(f"  penalty_per_pct={pen:7.2f}  dw_entry={r.dw_entry_soc_pct:6.2f}  "
          f"net_cost={r.projected_net_cost:.4f}")

print("\n--- is the #886 hard floor actually pruning? ---")
for floor in (None, 95.0):
    cfg = build_config()
    r = DPPlanner().plan(_OI(cycle_id="f", initial_soc_pct=INITIAL_SOC,
                             slots=build_slots(), config=cfg))
    print(f"  config.hard_target_floor after solve = {cfg.hard_target_floor}  "
          f"-> dw_entry={r.dw_entry_soc_pct:6.2f} (floor is {'MET' if r.dw_entry_soc_pct >= 95 else 'VIOLATED'})")
    break
