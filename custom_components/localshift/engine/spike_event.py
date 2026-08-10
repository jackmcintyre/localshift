"""Spike-event pre-charge funding (issue: forecast spike hit at the SOC floor).

WHY THIS EXISTS
---------------
``feasible_actions`` omits every CHARGE_* action when a slot's buy price exceeds
its cheap threshold, so the DP never *prices* a charge in those slots — it cannot
choose one at any cost. The only thing that ever raises that threshold is
``compute_pre_dw_charge_thresholds``, which is scoped to pre-demand-window slots
and sized from the demand-window target deficit.

A forecast price spike outside a demand window therefore has **no funder**: on
2026-08-05 the horizon carried a $1.65 print at 07:30 while the overnight trough
sat at $0.17–0.21 and ``base_cheap_price`` was $0.16. Two cents of threshold
blocked a $1.45/kWh trade, the plan held at the 10% floor through the whole
morning, and 71% of the projected day cost landed in that one block.

WHAT THIS MODULE DOES
---------------------
Identifies *funding slots*: slots where charging should be admitted to the
feasible set because stored energy bought there serves a genuine price SPIKE
later. Qualification has two parts, and both are required:

1. The dear slot must be a spike at all — at least ``_SPIKE_OUTLIER_FACTOR``
   times the horizon's own median non-demand-window price. This is what makes the
   module inert on ordinary days.
2. The trade must be worth doing — the marginal displaced price must beat this
   slot by the operator's ``min_cycle_saving`` (grossed up for round-trip
   losses), and the avoided cost must clear what the operator demands of one
   slot's worth of cycling. This mirrors the demand-window water level: sort the
   future by price and accumulate until the trade pays for itself.

Test 1 is not optional and cannot be folded into test 2. A price SPREAD alone
does not distinguish a spike from the ordinary daily shape — on any ordinary day
the overnight trough sits a full cycle bar below the morning peak — so a
spread-only rule fires on roughly half of all ordinary days. That is what #908
shipped, and it is how the #800 sawtooth came back (see SAFETY).

Qualifying slots are NOT exempt from the min-cycle-saving gate. #908 exempted
them on the grounds that qualification uses ``min_cycle_saving`` as its bar, but
the two compare different things: this module compares slot PRICES, the gate
compares the DP's REAL COSTS. See ``core._is_urgency_precharge`` for the full
argument and the measurement.

WHAT THIS MODULE DELIBERATELY DOES NOT DO
-----------------------------------------
It does not hold a reserve. Measured 2026-08-05: given only a feasible charge
action the DP's own economics buy immediately before the spike and hold through
it unaided (spike-slot import $0.635 -> $0.00). No second terminal index, no
time-varying floor, no reserve-hold machinery is required — and none should be
added without first re-measuring that result.

Demand-window slots are excluded from the *event* side: energy inside the DW is
already funded by the DW target machinery, and letting this module bid for it
would re-litigate a solved problem (and re-open the #800 sawtooth on ordinary
days, where the evening DW peak is the dearest thing in the horizon).

SAFETY
------
Safety lives HERE, in qualification. The caller's guard (``DPPlanner.plan``
solves with and without the widening and keeps the cheaper plan) is a backstop
against the DP's approximation error, not a sawtooth defence: it selects on the
DP objective, which is exactly the metric the min-cycle-saving gate exists to
correct, so a plan that cycles too eagerly looks *better* to it.

Nor can the guard be tightened into one. It chooses between two structurally
different plans, and this planner re-solves every few minutes against a revised
forecast: whenever that choice is close, ordinary jitter flips it, and the
committed action flips with it — charge, hold, charge, hold across consecutive
re-plans, which is the sawtooth as the battery experiences it. Any threshold
placed on a noisy quantity has this failure mode; a plan-level margin was tried
and simply moved the knife edge (measured: it left $0.06 of headroom on the live
2026-08-05 spike while still flapping elsewhere).

The only stable design is to fire ONLY when the case is unambiguous, so that
neither ordinary days nor spike days sit near a boundary. Measured over 400
randomised ordinary-day horizons: #908 qualified on 219 and changed the plan on
109; this version qualifies on 0, and the committed action no longer flaps in any
of them, while the live 2026-08-05 spike is still served entirely from the
battery.

That is why ``_SPIKE_OUTLIER_FACTOR`` must not be lowered to catch "nearly
spikes". The margin IS the safety property.
"""

from __future__ import annotations

import logging

from custom_components.localshift.engine.types import OptimizerConfig, SlotContext

_LOGGER = logging.getLogger(__name__)

# NOTE: there is deliberately no "minimum displaced kWh" fraction here any more.
#
# #908 required a funding slot to displace at least half of what one charge slot stores.
# That bar was calibrated when EVERY future slot counted as displaceable; now that only
# genuine spike slots do (see _SPIKE_OUTLIER_FACTOR), it rejects the canonical case — a
# spike one slot wide, whose net load is naturally less than half a charge slot. The
# sufficiency test is instead stated in value (see _displaces_enough), which is what the
# quantity bar was a proxy for.

# How far above the horizon's ordinary price level a slot must sit before it counts as a
# spike EVENT rather than the day's normal shape.
#
# This is the load-bearing constant for #800 safety, so it is set from measured
# separation rather than taste. Against the horizon's own median non-demand-window price:
#
#   live 2026-08-05 spike ($1.65 vs a $0.20 median)          8.2x   must fire
#   ordinary winter morning peak ($0.55 vs a $0.25 median)   2.2x   must NOT fire
#   ordinary evening/shoulder shape                         <2x     must NOT fire
#
# 4.0 sits in the empty middle of that gap, so neither class is near the boundary —
# which is the whole point. A price-SPREAD test alone (what #908 shipped) cannot make
# this call: on an ordinary day an overnight trough is a full cycle-bar below the morning
# peak, so the spread test fires on 55% of ordinary days, and once qualification sits on
# a knife edge, ordinary forecast jitter flips it from cycle to cycle — the battery then
# charges, holds, charges, holds across consecutive re-plans, which is the sawtooth.
#
# Deliberately relative to the horizon's own median rather than to min_cycle_saving:
# what counts as a spike is a property of the day's prices, not of the operator's
# separate opinion about when cycling the battery is worthwhile.
_SPIKE_OUTLIER_FACTOR = 4.0


def required_spread(config: OptimizerConfig) -> float:
    """Price gap a funding slot must beat, grossed up for round-trip losses.

    ``min_cycle_saving`` is stated per kWh *cycled*; a kWh bought from the grid
    only returns ``charge_efficiency * discharge_efficiency`` of itself, so the
    raw threshold understates the gap the arbitrage must clear.
    """
    round_trip = config.charge_efficiency * config.discharge_efficiency
    if round_trip <= 0:
        return float("inf")
    return config.min_cycle_saving / round_trip


def spike_price_floor(slots: list[SlotContext]) -> float:
    """Price at or above which a slot is a spike EVENT, not the day's ordinary shape.

    Measured against the median non-demand-window buy price in the horizon, so the test
    travels with the day: a $0.55 morning peak is ordinary on a $0.25 day and would be
    extraordinary on a $0.05 one. Demand-window slots are excluded from the median for
    the same reason they are excluded everywhere else here — that energy has its own
    funder, and letting the DW peak drag the median up would make genuine spikes harder
    to see on exactly the days they matter.

    Returns ``inf`` (nothing can qualify) when the horizon has no usable positive price
    level to measure against — a negative or zero median means the ordinary-price notion
    has broken down, and this module must fail closed rather than admit everything.
    """
    prices = sorted(slot.buy_price for slot in slots if not slot.is_demand_window_slot)
    if not prices:
        return float("inf")

    median = prices[len(prices) // 2]
    if median <= 0:
        return float("inf")
    return median * _SPIKE_OUTLIER_FACTOR


def _slot_hours(slot: SlotContext) -> float:
    return slot.slot_interval_minutes / 60.0


def _storable_kwh(slot: SlotContext, config: OptimizerConfig) -> float:
    """Energy one slot of normal-rate grid charging actually puts in the battery."""
    return config.charge_rate_kw * _slot_hours(slot) * config.charge_efficiency


def _displaces_enough(
    j: int,
    slots: list[SlotContext],
    by_price_desc: list[int],
    displaceable: list[float],
    spread: float,
    needed_value: float,
) -> bool:
    """True when charging at ``j`` buys at least ``needed_value`` of avoided cost.

    Every slot counted must clear ``j`` by a full ``spread``; ``by_price_desc`` is ranked
    dearest-first, so the first slot that fails that test ends the search — nothing
    further can clear it. The last slot accepted sets the marginal displaced price,
    mirroring the demand-window funding water level.

    Sufficiency is measured in DOLLARS (displaced kWh x price gap), not kWh. A spike is
    frequently one slot wide, so any kWh bar wide enough to reject noise also rejects the
    canonical case; the value of serving a genuine spike is large precisely because the
    gap is, and that is the thing worth requiring.
    """
    accumulated = 0.0
    own_price = slots[j].buy_price
    for i in by_price_desc:
        if i <= j:
            continue
        gap = slots[i].buy_price - own_price
        if gap < spread:
            return False
        accumulated += displaceable[i] * gap
        if accumulated >= needed_value:
            return True
    return False


def find_funding_slots(
    slots: list[SlotContext],
    config: OptimizerConfig,
) -> frozenset[int]:
    """Slots whose grid charge should be admitted to fund a later expensive interval.

    A slot ``j`` qualifies when the future contains enough displaceable net load,
    all of it priced at least ``required_spread`` above ``slots[j].buy_price``, to
    absorb a meaningful share of one slot's worth of charging.

    Returns an empty set (feature fully inert) when the mode is not
    self-consumption, ``min_cycle_saving`` is unset, or nothing qualifies.
    """
    if config.optimization_mode != "self_consumption":
        return frozenset()
    if not slots or config.min_cycle_saving <= 0:
        return frozenset()

    spread = required_spread(config)
    if spread == float("inf"):
        return frozenset()

    # Net load the battery could displace, counted ONLY in spike slots.
    #
    # Demand-window slots are excluded because that energy already has a funder (the DW
    # target). Ordinary-priced slots are excluded because funding them is not this
    # module's job: routine daily arbitrage is what the cheap-price gate and
    # min_cycle_saving already govern, and bidding for it here is precisely how the #800
    # overnight sawtooth comes back. A price spread alone does not distinguish the two —
    # an ordinary morning peak sits a full cycle bar above the overnight trough — so the
    # displaced price must also be an outlier for the day (see _SPIKE_OUTLIER_FACTOR).
    floor = spike_price_floor(slots)
    displaceable = [
        max(0.0, slot.consumption_kwh - slot.solar_kwh)
        if (not slot.is_demand_window_slot and slot.buy_price >= floor)
        else 0.0
        for slot in slots
    ]

    # Future slots ranked dearest-first: the battery discharges into whichever
    # slots it likes, so the value of a stored kWh is set by the dearest load it
    # can displace, and the marginal price is the one that has to clear the gap.
    by_price_desc = sorted(range(len(slots)), key=lambda i: (-slots[i].buy_price, i))

    funding: set[int] = set()
    for j, slot in enumerate(slots):
        if slot.is_demand_window_slot:
            continue  # grid import is forbidden inside the DW anyway
        # What the trade must be worth: the saving the operator already demands of one
        # slot's worth of cycling. Below that, charging here is not worth the cycle —
        # which is the same judgement min_cycle_saving makes, in the same units.
        needed_value = config.min_cycle_saving * _storable_kwh(slot, config)
        if needed_value <= 0:
            continue
        if _displaces_enough(
            j, slots, by_price_desc, displaceable, spread, needed_value
        ):
            funding.add(j)

    if funding:
        _LOGGER.debug(
            "Spike funding: %d slot(s) qualified at spread $%.3f/kWh",
            len(funding),
            spread,
        )
    return frozenset(funding)
