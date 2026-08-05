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
feasible set because stored energy bought there displaces materially dearer
energy later. Qualification mirrors the demand-window water level — sort the
future by price, accumulate the net load the battery could actually displace,
and require the **marginal** displaced price to beat this slot by at least the
operator's own ``min_cycle_saving`` (grossed up for round-trip losses).

Using ``min_cycle_saving`` as the bar is deliberate: it is the operator's
existing statement of "a cycle is only worth it if it saves this much", so a
qualifying slot has *already* been proven to clear that gate. Callers therefore
also exempt these slots from the min-cycle gate itself (see
``core._is_urgency_precharge``) — re-applying it is both redundant and harmful,
because that gate is non-monotone and can reject a strictly better plan when the
feasible set grows.

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
Qualification is intentionally permissive; correctness comes from the caller's
guard, which solves with and without the widening and keeps the cheaper plan
(``DPPlanner.plan``). A 200-scenario sweep showed qualification alone still
harms ~4% of firing scenarios (worst $0.49) because the DP is approximate; the
guard removes that by construction.
"""

from __future__ import annotations

import logging

from custom_components.localshift.engine.types import OptimizerConfig, SlotContext

_LOGGER = logging.getLogger(__name__)

# A funding slot must displace at least this fraction of what one slot of
# normal-rate charging actually stores. Below that the trade is noise.
_MIN_DISPLACED_FRACTION = 0.5


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
    needed_kwh: float,
) -> bool:
    """True when the future holds ``needed_kwh`` of load all priced a full spread above j.

    ``by_price_desc`` is ranked dearest-first, so the first slot that fails the spread
    test ends the search: nothing further can clear it. The last slot accepted sets the
    marginal displaced price, mirroring the demand-window funding water level.
    """
    accumulated = 0.0
    own_price = slots[j].buy_price
    for i in by_price_desc:
        if i <= j:
            continue
        if slots[i].buy_price - own_price < spread:
            return False
        accumulated += displaceable[i]
        if accumulated >= needed_kwh:
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

    # Net load the battery could displace in each slot. Demand-window slots are
    # excluded: that energy already has a funder (the DW target), and bidding for
    # it here would re-open the overnight sawtooth on ordinary days.
    displaceable = [
        0.0
        if slot.is_demand_window_slot
        else max(0.0, slot.consumption_kwh - slot.solar_kwh)
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
        needed = _storable_kwh(slot, config) * _MIN_DISPLACED_FRACTION
        if needed <= 0:
            continue
        if _displaces_enough(j, slots, by_price_desc, displaceable, spread, needed):
            funding.add(j)

    if funding:
        _LOGGER.debug(
            "Spike funding: %d slot(s) qualified at spread $%.3f/kWh",
            len(funding),
            spread,
        )
    return frozenset(funding)
