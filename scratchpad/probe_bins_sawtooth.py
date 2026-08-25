"""Does the finer grid re-open the #800 overnight sawtooth, or change a flat day?"""

import contextlib
import io
import sys

sys.path.insert(0, ".")
with contextlib.redirect_stdout(io.StringIO()):
    from scratchpad.deferral_replay import INITIAL_SOC, RAW, build_config, build_slots
from custom_components.localshift.engine.core import DPPlanner
from custom_components.localshift.engine.types import OptimizerInputs, SlotContext


def overnight(r):
    return sum(
        float(getattr(d, "grid_import_kwh", 0.0) or 0.0)
        for d in r.decisions
        if 27 <= getattr(d, "slot_index", -1) <= 45
        and "charge_grid" in str(getattr(d, "action", ""))
    )


def cycles(r):
    """Charge->discharge alternations after the demand window: the sawtooth signature."""
    acts = [
        str(getattr(d, "action", ""))
        for d in r.decisions
        if getattr(d, "slot_index", -1) >= 27
    ]
    return sum(
        1
        for a, b in zip(acts, acts[1:], strict=False)
        if ("charge_grid" in a) != ("charge_grid" in b)
    )


print("--- today's horizon ---")
for bins in (50, 100):
    cfg = build_config()
    cfg.soc_bins = bins
    r = DPPlanner().plan(
        OptimizerInputs(
            cycle_id=f"n{bins}",
            initial_soc_pct=INITIAL_SOC,
            slots=build_slots(),
            config=cfg,
        )
    )
    print(
        f"  soc_bins={bins:3d}  dw_entry={r.dw_entry_soc_pct:6.2f}  "
        f"overnight_grid_charge={overnight(r):.3f} kWh  post-DW action flips={cycles(r)}"
    )

print("\n--- flat-price control day (no spread, nothing to arbitrage) ---")
flat = []
for idx, hhmm, interval, _buy, _sell, solar, cons in RAW:
    flat.append(
        SlotContext(
            slot_index=idx,
            timestamp_iso=f"2026-08-25T{hhmm}:01+10:00",
            slot_interval_minutes=interval,
            buy_price=0.17,
            sell_price=0.08,
            solar_kwh=solar,
            consumption_kwh=cons,
            is_demand_window_entry=(idx == 15),
            is_demand_window_slot=(15 <= idx < 27),
            price_source="30min",
        )
    )
for bins in (50, 100):
    cfg = build_config()
    cfg.soc_bins = bins
    r = DPPlanner().plan(
        OptimizerInputs(
            cycle_id=f"flat{bins}", initial_soc_pct=INITIAL_SOC, slots=flat, config=cfg
        )
    )
    print(
        f"  soc_bins={bins:3d}  dw_entry={r.dw_entry_soc_pct:6.2f}  "
        f"overnight_grid_charge={overnight(r):.3f} kWh  post-DW action flips={cycles(r)}"
    )

print("\n--- high-SOC start (nothing to do: must not invent charging) ---")
for bins in (50, 100):
    cfg = build_config()
    cfg.soc_bins = bins
    r = DPPlanner().plan(
        OptimizerInputs(
            cycle_id=f"h{bins}", initial_soc_pct=97.0, slots=build_slots(), config=cfg
        )
    )
    n = sum(1 for d in r.decisions if "charge_grid" in str(getattr(d, "action", "")))
    print(
        f"  soc_bins={bins:3d}  dw_entry={r.dw_entry_soc_pct:6.2f}  "
        f"charge_slots={n}  overnight_grid_charge={overnight(r):.3f} kWh"
    )
