"""Is 100 bins ROBUST, or did it just land well on one horizon?"""
import sys, io, contextlib
sys.path.insert(0, ".")
with contextlib.redirect_stdout(io.StringIO()):
    from scratchpad.deferral_replay import build_slots, build_config, INITIAL_SOC
from custom_components.localshift.engine.core import DPPlanner
from custom_components.localshift.engine.types import OptimizerInputs

SOCS = [18.0, 20.0, 21.986, 24.0, 26.0, 28.0, 30.0, 33.0, 36.0, 40.0]
BINS = [50, 70, 100, 150, 200, 400]

print(f"{'soc':>7} " + " ".join(f"{b:>7}" for b in BINS))
fails = {b: 0 for b in BINS}
for soc in SOCS:
    row = f"{soc:>7.1f} "
    for b in BINS:
        cfg = build_config(); cfg.soc_bins = b
        r = DPPlanner().plan(OptimizerInputs(cycle_id="r", initial_soc_pct=soc,
                                            slots=build_slots(), config=cfg))
        sf = r.terminal_shortfall_pct
        if sf > 0.5:
            fails[b] += 1
        row += f" {sf:>6.2f}"
    print(row)
print("\nshortfall > 0.5pp count (lower is better), out of", len(SOCS))
for b in BINS:
    print(f"  soc_bins={b:>4}: {fails[b]} failures")
