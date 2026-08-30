"""Does cent-rounding of the price series change the plan?

Controlled A/B on one horizon: solar, consumption, initial SOC, demand window and
config are identical in both arms. Only the buy-price series differs — full
precision (what Amber Express publishes) versus rounded to 2 decimals (what the
native 100h forecast sensor published, and therefore what the optimizer consumed
on every plan built before 2026-08-29 09:54).

Prices are a real Amber Express detailedForecast captured live 2026-08-29.
Solar/consumption backdrop is the 2026-08-25 replay horizon, unchanged between
arms, so any difference is attributable to price resolution alone.
"""

import contextlib
import io
import sys

sys.path.insert(0, ".")
with contextlib.redirect_stdout(io.StringIO()):
    from scratchpad.deferral_replay import INITIAL_SOC, RAW, build_config
from custom_components.localshift.engine.core import DPPlanner
from custom_components.localshift.engine.types import OptimizerInputs, SlotContext

DW_ENTRY_IDX, DW_END_IDX = 15, 27

# Live Amber Express detailedForecast, 2026-08-29 ~09:55 AEST, full precision.
EXPRESS = [
    0.0913144, 0.0906105, 0.0915804, 0.0912456, 0.0892739, 0.0872389, 0.0873749,
    0.0876772, 0.0856287, 0.0877512, 0.0870796, 0.0864081, 0.0828585, 0.0808933,
    0.0806866, 0.0811261, 0.0805761, 0.0800695, 0.0797522, 0.0791168, 0.0817267,
    0.0892504, 0.1087197, 0.1359467, 0.1591649, 0.1686712, 0.1672449, 0.1641219,
    0.161449, 0.1599232, 0.159881, 0.1598755, 0.1596532, 0.1587094, 0.1578042,
    0.157354, 0.1560713, 0.1561063, 0.1564724, 0.1567503, 0.156156, 0.1547723,
    0.154604, 0.154592, 0.1535558, 0.1535053,
]


def build(prices):
    out = []
    for i, (idx, hhmm, interval, _buy, sell, solar, cons) in enumerate(RAW):
        day = "2026-08-25" if idx <= 32 else "2026-08-26"
        out.append(
            SlotContext(
                slot_index=idx,
                timestamp_iso=f"{day}T{hhmm}:01+10:00",
                slot_interval_minutes=interval,
                buy_price=prices[min(i, len(prices) - 1)],
                sell_price=sell,
                solar_kwh=solar,
                consumption_kwh=cons,
                is_demand_window_entry=(idx == DW_ENTRY_IDX),
                is_demand_window_slot=(DW_ENTRY_IDX <= idx < DW_END_IDX),
                price_source="5min" if interval == 5 else "30min",
            )
        )
    return out


def summarise(label, slots):
    r = DPPlanner().plan(
        OptimizerInputs(
            cycle_id=label, initial_soc_pct=INITIAL_SOC, slots=slots,
            config=build_config(),
        )
    )
    charges = [
        d for d in r.decisions if "charge_grid" in str(getattr(d, "action", ""))
    ]
    first = charges[0].slot_index if charges else None
    flips = sum(
        1
        for a, b in zip(r.decisions, r.decisions[1:])
        if str(getattr(a, "action", "")) != str(getattr(b, "action", ""))
    )
    print(
        f"{label:22s} first_charge_slot={str(first):>5s}  charge_slots={len(charges):>2d}  "
        f"dw_entry={r.dw_entry_soc_pct:6.2f}  action_flips={flips:>2d}  "
        f"net_cost={r.projected_net_cost:.4f}"
    )
    return r, [str(getattr(d, "action", "")) for d in r.decisions]


true_prices = EXPRESS
quantised = [round(p, 2) for p in EXPRESS]

print(f"distinct price levels: full={len(set(true_prices))}  quantised={len(set(quantised))}\n")

r_true, plan_true = summarise("full precision", build(true_prices))
r_quant, plan_quant = summarise("rounded to cents", build(quantised))

def overnight_charge(r):
    return sum(
        float(getattr(d, "grid_import_kwh", 0.0) or 0.0)
        for d in r.decisions
        if 27 <= getattr(d, "slot_index", -1) <= 45
        and "charge_grid" in str(getattr(d, "action", ""))
    )


print(
    f"\novernight (slots 27-45, the flat expensive stretch) grid charge:"
    f"  full={overnight_charge(r_true):.3f} kWh   quantised={overnight_charge(r_quant):.3f} kWh"
)

differing = [i for i, (a, b) in enumerate(zip(plan_true, plan_quant)) if a != b]
print(f"\nslots where the chosen action differs: {len(differing)} of {len(plan_true)}")
if differing:
    print(f"  slot indices: {differing[:20]}{' ...' if len(differing) > 20 else ''}")
    for i in differing[:6]:
        print(
            f"  slot {i:>2d} {RAW[i][1]}  true={true_prices[min(i, len(true_prices) - 1)]:.4f}"
            f" -> {quantised[min(i, len(quantised) - 1)]:.2f}   "
            f"{plan_true[i]}  vs  {plan_quant[i]}"
        )
print(f"\nnet cost delta (quantised - true): {r_quant.projected_net_cost - r_true.projected_net_cost:+.4f}")
