"""If discretisation error is the cause, a finer SOC grid should recover the target."""

import contextlib
import io
import sys

sys.path.insert(0, ".")
with contextlib.redirect_stdout(io.StringIO()):
    from scratchpad.deferral_replay import INITIAL_SOC, build_config, build_slots
import time

from custom_components.localshift.engine.core import DPPlanner
from custom_components.localshift.engine.types import OptimizerInputs

print(
    f"{'soc_bins':>9} {'bin width':>10} {'dw_entry':>9} {'shortfall':>10} {'net_cost':>9} {'first_charge':>13} {'solve_s':>8}"
)
for bins in (50, 100, 200, 400, 900):
    cfg = build_config()
    cfg.soc_bins = bins
    t0 = time.time()
    r = DPPlanner().plan(
        OptimizerInputs(
            cycle_id=f"b{bins}",
            initial_soc_pct=INITIAL_SOC,
            slots=build_slots(),
            config=cfg,
        )
    )
    dt = time.time() - t0
    fc = next(
        (
            getattr(d, "slot_index", None)
            for d in r.decisions
            if "charge_grid" in str(getattr(d, "action", ""))
        ),
        None,
    )
    print(
        f"{bins:>9} {90.0 / (bins - 1):>9.2f}pp {r.dw_entry_soc_pct:>9.2f} "
        f"{r.terminal_shortfall_pct:>10.2f} {r.projected_net_cost:>9.4f} "
        f"{'slot ' + str(fc):>13} {dt:>8.3f}"
    )
