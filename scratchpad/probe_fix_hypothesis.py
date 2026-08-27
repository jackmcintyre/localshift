"""If reconstruction follows the DP's stored successor bin, does the plan hit target?"""

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

_real_map = core_mod._map_soc_to_bin


def run(label, follow_policy):
    """follow_policy=True: snap the reconstruction back onto the DP's assumed successor."""
    state = {"dp": None, "grid": None}
    _real_back = DPPlanner._backward_induction

    def spy_back(self, dp, slots, soc_grid, config, tpi, inputs, nfac=None, tpb=None):
        n = _real_back(self, dp, slots, soc_grid, config, tpi, inputs, nfac, tpb)
        state["dp"], state["grid"] = dp, soc_grid
        return n

    # Re-bin by nearest rather than by truncation-prone lookup, i.e. stop the
    # systematic downward rounding that costs ~1 bin per boosted 5-minute slot.
    def nearest_bin(soc, soc_grid):
        return min(range(len(soc_grid)), key=lambda i: abs(soc_grid[i] - soc))

    cfg = build_config()
    patches = [patch.object(DPPlanner, "_backward_induction", spy_back)]
    if follow_policy:
        patches.append(patch.object(core_mod, "_map_soc_to_bin", nearest_bin))
    with contextlib.ExitStack() as st:
        for p in patches:
            st.enter_context(p)
        r = DPPlanner().plan(
            OptimizerInputs(
                cycle_id=label,
                initial_soc_pct=INITIAL_SOC,
                slots=build_slots(),
                config=cfg,
            )
        )
    print(
        f"{label:34s} dw_entry={r.dw_entry_soc_pct:6.2f}  shortfall={r.terminal_shortfall_pct:5.2f}  "
        f"net_cost={r.projected_net_cost:.4f}"
    )
    return r


print("how does _map_soc_to_bin round?")
import inspect

print(inspect.getsource(_real_map))

run("as-shipped", False)
run("nearest-bin re-binning", True)
