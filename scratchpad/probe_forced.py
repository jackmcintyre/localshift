"""Force a charge at the head and see what the terminal SOC actually becomes."""

import contextlib
import io
import sys

sys.path.insert(0, ".")
from unittest.mock import patch

with contextlib.redirect_stdout(io.StringIO()):
    from scratchpad.deferral_replay import INITIAL_SOC, build_config, build_slots
import custom_components.localshift.engine.core as core_mod
from custom_components.localshift.engine.core import DPPlanner
from custom_components.localshift.engine.types import OptimizerInputs

_real = core_mod._constraints_feasible_actions


def force_charge_until(n):
    def spy(
        soc_pct, slot, config, slot_idx=0, slots=None, terminal_penalty_idx=None, **kw
    ):
        acts = _real(
            soc_pct,
            slot,
            config,
            slot_idx=slot_idx,
            slots=slots,
            terminal_penalty_idx=terminal_penalty_idx,
            **kw,
        )
        if slot_idx < n:
            charge = [a for a in acts if "CHARGE_GRID" in a.name]
            if charge:
                return charge  # HOLD removed: the DP must charge here
        return acts

    return spy


def go(label, n):
    cfg = build_config()
    ctx = (
        patch.object(
            core_mod, "_constraints_feasible_actions", side_effect=force_charge_until(n)
        )
        if n
        else contextlib.nullcontext()
    )
    with ctx:
        r = DPPlanner().plan(
            OptimizerInputs(
                cycle_id=label,
                initial_soc_pct=INITIAL_SOC,
                slots=build_slots(),
                config=cfg,
            )
        )
    traj = {
        getattr(d, "slot_index", -1): getattr(d, "predicted_soc_pct", None)
        for d in r.decisions
    }
    print(
        f"{label:26s} dw_entry={r.dw_entry_soc_pct:6.2f}  shortfall={r.terminal_shortfall_pct:5.2f}  "
        f"soc@12:45={traj.get(3)}  soc@13:30={traj.get(12)}  soc@14:30={traj.get(14)}  "
        f"net_cost={r.projected_net_cost:.4f}"
    )


go("DP's own choice", 0)
for n in (1, 2, 3):
    go(f"forced charge slots 0..{n - 1}", n)
