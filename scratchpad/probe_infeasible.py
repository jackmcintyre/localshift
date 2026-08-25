"""Instrument the REAL solve: what is feasible at slots 0-4, with the config the DP builds."""
import sys
sys.path.insert(0, ".")
from unittest.mock import patch
import io, contextlib
with contextlib.redirect_stdout(io.StringIO()):
    from scratchpad.deferral_replay import build_slots, build_config, INITIAL_SOC, RAW
from custom_components.localshift.engine.core import DPPlanner
from custom_components.localshift.engine.types import OptimizerInputs
import custom_components.localshift.engine.core as core_mod

_real = core_mod._constraints_feasible_actions
seen = {}

def spy(soc_pct, slot, config, slot_idx=0, slots=None, terminal_penalty_idx=None, **kw):
    acts = _real(soc_pct, slot, config, slot_idx=slot_idx, slots=slots,
                 terminal_penalty_idx=terminal_penalty_idx, **kw)
    if slot_idx <= 4:
        key = (slot_idx, round(soc_pct, 1))
        seen.setdefault(key, sorted(a.value for a in acts))
    return acts

cfg = build_config()
with patch.object(core_mod, "_constraints_feasible_actions", side_effect=spy):
    DPPlanner().plan(OptimizerInputs(cycle_id="probe", initial_soc_pct=INITIAL_SOC,
                                     slots=build_slots(), config=cfg))

print("config the DP built:")
for f in ("hard_target_floor", "urgency_window_start_idx", "terminal_penalty_idx",
          "pre_dw_funding_water_level", "hard_floor_suppressed_by_solar",
          "precharge_runway_slack_min"):
    print(f"  {f} = {getattr(cfg, f, '<absent>')}")
thr = getattr(cfg, "pre_dw_charge_thresholds", None)
print(f"  pre_dw_charge_thresholds[0:6] = {thr[:6] if thr else None}")

print("\nfeasible actions at the head of the horizon (by slot, SOC bin):")
for (idx, soc) in sorted(seen):
    if soc < 30:
        print(f"  slot {idx} {RAW[idx][1]} soc={soc:5.1f}%  -> {seen[(idx, soc)]}")
