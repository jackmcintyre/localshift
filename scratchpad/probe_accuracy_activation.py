"""What does the newly-active solar-accuracy correction actually change? (#849/#856)

On 2026-08-31 the sampler crossed 20 samples and `correction_active` flipped true
for the first time, taking `config.solar_forecast_accuracy` from its pinned 1.0
to the measured 0.7116. Both the feasibility gate
(constraints.check_global_solar_sufficiency) and the shortfall classifier now
discount projected solar gain by that factor:

    discounted_gain = max(0, simulated_terminal_soc - soc) * accuracy

Lower accuracy therefore makes the gate LESS willing to believe solar reaches
target, which keeps grid pre-charge in the feasible set — the conservative
direction, and the #816 fix.

Today's live horizon is surplus-solar, so the gate clears either way and nothing
changes. This sweeps solar scale to find where the correction STARTS to bite.
"""

import contextlib, io, runpy, sys
sys.path.insert(0, ".")

cap = runpy.run_path("scratchpad/raw_2026-08-31.py")
RAW = cap["RAW"]; INITIAL_SOC = cap["INITIAL_SOC"]
DW_ENTRY_IDX, DW_END_IDX, ROLL = cap["DW_ENTRY_IDX"], cap["DW_END_IDX"], cap["DAY_ROLLOVER_IDX"]

with contextlib.redirect_stdout(io.StringIO()):
    from scratchpad.deferral_replay import build_config
from custom_components.localshift.engine.core import DPPlanner
from custom_components.localshift.engine.types import OptimizerInputs, SlotContext

OLD, NEW = 1.0, 0.7116  # pinned-at-100% vs the measured live accuracy


def build(solar_scale):
    return [
        SlotContext(
            slot_index=idx,
            timestamp_iso=f"{cap['START_DAY'] if i < ROLL else cap['NEXT_DAY']}T{hhmm}:01+10:00",
            slot_interval_minutes=iv, buy_price=b, sell_price=s,
            solar_kwh=so * solar_scale, consumption_kwh=co,
            is_demand_window_entry=(idx == DW_ENTRY_IDX),
            is_demand_window_slot=(DW_ENTRY_IDX <= idx < DW_END_IDX),
            price_source="5min" if iv == 5 else "30min",
        )
        for i, (idx, hhmm, iv, b, s, so, co) in enumerate(RAW)
    ]


class _FakeMetrics:
    def __init__(self, pct): self.accuracy = pct


class _FakeTracker:
    """Minimal stand-in for SolarAccuracyTracker.

    core.py:582 OVERWRITES config.solar_forecast_accuracy from
    inputs.solar_accuracy_tracker on every plan, so setting it on the config
    alone is silently clobbered (get_forecast_accuracy returns 1.0 for a None
    tracker). The accuracy has to be injected through the tracker.
    """

    def __init__(self, pct): self.metrics = _FakeMetrics(pct)


def plan(slots, accuracy, label):
    cfg = build_config()
    cfg.solar_forecast_accuracy = accuracy
    r = DPPlanner().plan(
        OptimizerInputs(
            cycle_id=label, initial_soc_pct=INITIAL_SOC, slots=slots, config=cfg,
            solar_accuracy_tracker=_FakeTracker(accuracy * 100.0),
        )
    )
    acts = [str(getattr(d, "action", "")) for d in r.decisions]
    chg = sum(1 for a in acts if "charge_grid" in a)
    kwh = sum(float(getattr(d, "grid_import_kwh", 0.0) or 0.0)
              for d in r.decisions if "charge_grid" in str(getattr(d, "action", "")))
    return r, acts, chg, kwh


load = sum(r[6] for r in RAW)
print(f"horizon 2026-08-31, initial_soc={INITIAL_SOC}%, load {load:.1f} kWh")
print(f"accuracy: was pinned {OLD:.4f} (no samples) -> now measured {NEW:.4f} (21 samples)\n")
print(f"{'scale':>6} {'solar':>7} | {'accuracy=1.00 (before)':>34} | {'accuracy=0.71 (now live)':>34} | changed")
print("-" * 118)

for sc in (1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2):
    slots = build(sc)
    ro, ao, co_, ko = plan(slots, OLD, f"old-{sc}")
    rn, an, cn, kn = plan(slots, NEW, f"new-{sc}")
    diff = [i for i, (x, y) in enumerate(zip(ao, an)) if x != y]
    mark = "YES" if diff or abs(ko - kn) > 1e-9 else "-"
    print(f"{sc:>6.2f} {sum(r[5] for r in RAW)*sc:>6.1f}k | "
          f"chg={co_:>2d} kWh={ko:>6.3f} dw={ro.dw_entry_soc_pct:>6.2f} ${ro.projected_net_cost:>7.4f} | "
          f"chg={cn:>2d} kWh={kn:>6.3f} dw={rn.dw_entry_soc_pct:>6.2f} ${rn.projected_net_cost:>7.4f} | "
          f"{mark} {diff[:6] if diff else ''}")
