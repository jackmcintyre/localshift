import sys
sys.path.insert(0, ".")
from scratchpad.deferral_replay import build_slots, build_config, RAW
from custom_components.localshift.engine.constraints import feasible_actions

slots = build_slots()
cfg = build_config()
soc = 21.986
for idx in range(0, 8):
    acts = feasible_actions(
        soc_pct=soc, slot=slots[idx], config=cfg, slot_idx=idx,
        slots=slots, terminal_penalty_idx=15,
    )
    names = sorted(a.value for a in acts)
    print(f"slot {idx:2d} {RAW[idx][1]}  interval={RAW[idx][2]:2d}m buy={RAW[idx][3]:.2f}  feasible={names}")
