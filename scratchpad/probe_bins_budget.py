"""Smallest bin count that both fixes the defect and stays inside the runtime budget."""

import contextlib
import io
import sys
import time

sys.path.insert(0, ".")

# Mirrors tests/test_optimizer_scaffold.py::RUNTIME_BUDGET_S. Imported by value
# rather than by import: the tests package pulls in conftest fixtures this probe
# has no use for.
RUNTIME_BUDGET_S = 0.500
with contextlib.redirect_stdout(io.StringIO()):
    from scratchpad.deferral_replay import INITIAL_SOC, build_config, build_slots
from custom_components.localshift.engine.core import DPPlanner
from custom_components.localshift.engine.types import OptimizerInputs

print(
    f"{'bins':>5} {'width':>7} {'dw_entry':>9} {'shortfall':>10} {'first_charge':>13} {'p95 solve':>10}"
)
for bins in (50, 60, 70, 75, 80, 90, 100):
    times = []
    r = None
    for _ in range(9):
        cfg = build_config()
        cfg.soc_bins = bins
        t0 = time.perf_counter()
        r = DPPlanner().plan(
            OptimizerInputs(
                cycle_id=f"b{bins}",
                initial_soc_pct=INITIAL_SOC,
                slots=build_slots(),
                config=cfg,
            )
        )
        times.append(time.perf_counter() - t0)
    # nearest-rank p95: ceil(0.95 * n) - 1. The naive int() form truncates to the
    # eighth of nine samples and underreports the high tail.
    import math

    p95 = sorted(times)[math.ceil(0.95 * len(times)) - 1]
    fc = next(
        (
            getattr(d, "slot_index", None)
            for d in r.decisions
            if "charge_grid" in str(getattr(d, "action", ""))
        ),
        None,
    )
    # The docstring promises "fixes the defect AND stays inside the runtime budget", so
    # score both. NOTE the timing here is LOCAL: GitHub's shared runners measured ~5x
    # slower on this workload (74ms local vs 350ms CI at 100 bins), so a row that clears
    # the budget locally can still fail CI. Treat this column as a relative ranking
    # between bin counts, not as a pass/fail against CI.
    within_budget = p95 <= RUNTIME_BUDGET_S
    ok = (
        "OK "
        if r.terminal_shortfall_pct == 0.0 and fc == 0 and within_budget
        else "BAD"
    )
    print(
        f"{bins:>5} {90.0 / (bins - 1):>6.2f}pp {r.dw_entry_soc_pct:>9.2f} "
        f"{r.terminal_shortfall_pct:>10.2f} {'slot ' + str(fc):>13} {p95 * 1000:>8.1f}ms  {ok}"
    )
