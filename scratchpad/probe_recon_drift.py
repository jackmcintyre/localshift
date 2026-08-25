"""Does forward reconstruction follow the trajectory backward induction actually valued?

The DP stores, per (slot, bin), the best action AND the successor bin it assumed
(best[2]). _forward_reconstruct ignores that successor and re-derives the next bin from
the CONTINUOUS soc via _transition + _map_soc_to_bin. If the two disagree, the policy read
at step n+1 is the policy for a state whose value never justified the action taken at n.
"""
import sys, io, contextlib
sys.path.insert(0, ".")
with contextlib.redirect_stdout(io.StringIO()):
    from scratchpad.deferral_replay import build_slots, build_config, INITIAL_SOC, RAW
from custom_components.localshift.engine.core import (
    DPPlanner, _map_soc_to_bin, _transition,
)
from custom_components.localshift.engine.types import OptimizerInputs, PlannerAction

captured = {}
_real_back = DPPlanner._backward_induction


def spy(self, dp, slots, soc_grid, config, tpi, inputs, nfac=None, tpb=None):
    n = _real_back(self, dp, slots, soc_grid, config, tpi, inputs, nfac, tpb)
    captured["dp"] = dp
    captured["grid"] = soc_grid
    captured["config"] = config
    captured["slots"] = slots
    return n


DPPlanner._backward_induction = spy
cfg = build_config()
r = DPPlanner().plan(OptimizerInputs(cycle_id="d", initial_soc_pct=INITIAL_SOC,
                                     slots=build_slots(), config=cfg))
dp, grid, config, slots = (captured[k] for k in ("dp", "grid", "config", "slots"))

soc = INITIAL_SOC
b = _map_soc_to_bin(soc, grid)
print(f"{'slot':>4} {'time':>6} | {'bin':>4} {'action':>18} | {'DP assumed':>10} {'recon got':>9} | drift")
drift_total = 0
for i in range(0, 16):
    if b not in dp[i]:
        break
    _, action, dp_next_bin, *_ = dp[i][b]
    nsoc, _, _ = _transition(soc, action, slots[i], config)
    nsoc = max(config.min_soc_pct, min(config.max_soc_pct, nsoc))
    recon_bin = _map_soc_to_bin(nsoc, grid)
    d = recon_bin - dp_next_bin
    drift_total += abs(d)
    flag = "" if d == 0 else f"  <-- {d:+d} bin"
    print(f"{i:>4} {RAW[i][1]:>6} | {b:>4} {str(action).split('.')[-1].lower():>18} | "
          f"{dp_next_bin:>10} {recon_bin:>9} | {d:+d}{flag}")
    soc, b = nsoc, recon_bin

print(f"\ntotal absolute bin drift over slots 0-15: {drift_total}")
print(f"one bin = {(grid[-1]-grid[0])/(len(grid)-1):.2f} SOC points")
