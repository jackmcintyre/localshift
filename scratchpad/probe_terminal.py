"""What penalty does the DP actually attach to each DW-entry SOC bin?"""

import contextlib
import io
import sys

sys.path.insert(0, ".")
from unittest.mock import patch

with contextlib.redirect_stdout(io.StringIO()):
    from scratchpad.deferral_replay import INITIAL_SOC, build_config, build_slots
from custom_components.localshift.engine.core import DPPlanner
from custom_components.localshift.engine.types import OptimizerInputs

captured = {}
_real_init = DPPlanner._initialize_dp_tables


def spy_init(self, *a, **kw):
    dp, tpb = _real_init(self, *a, **kw)
    captured["tpb"] = dict(tpb) if tpb else {}
    captured["args"] = (a, kw)
    return dp, tpb


_real_gain = None
for name in dir(DPPlanner):
    if "solar_gain" in name:
        print("gain-related method:", name)

cfg = build_config()
with patch.object(DPPlanner, "_initialize_dp_tables", spy_init):
    r = DPPlanner().plan(
        OptimizerInputs(
            cycle_id="t", initial_soc_pct=INITIAL_SOC, slots=build_slots(), config=cfg
        )
    )

tpb = captured.get("tpb", {})
print(f"\nterminal_penalty_by_bin: {len(tpb)} entries")
grid = (
    [10.0 + i * (100.0 - 10.0) / (len(tpb) - 1) for i in range(len(tpb))]
    if len(tpb) > 1
    else []
)
for bin_idx in sorted(tpb):
    soc = grid[bin_idx] if bin_idx < len(grid) else None
    if soc is None or soc < 85:
        continue
    print(f"  bin {bin_idx:3d}  soc~{soc:6.2f}%  terminal_penalty = {tpb[bin_idx]:.4f}")
print(
    f"\nplan chose dw_entry={r.dw_entry_soc_pct:.2f}  net_cost={r.projected_net_cost:.4f}"
)
