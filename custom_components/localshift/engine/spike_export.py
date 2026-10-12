"""Spike export admission for the default (self_consumption) mode (issue #1120).

WHY THIS EXISTS
---------------
In ``self_consumption`` mode ``feasible_actions`` admits EXPORT only when
``sell >= buy + export_price_margin`` in the SAME slot. Amber's feed-in price
is always below its general price for the same interval, so on honest prices
that test never passes: on 2026-10-12 feed-in hit $24.23 and $10.52 and the
default mode held through both. Export was not discouraged, it was absent from
the feasible set, so nothing in the DP could choose it.

Comparing against the same interval's buy price asks the wrong question.
Energy sold now is not bought back now; it is bought back later. So a slot is
also admitted when it is a genuine spike, and both tests below are required:

1. Outlier. The sell price is at least ``SPIKE_EXPORT_OUTLIER_FACTOR`` times
   the horizon's median buy price. This is what keeps the rule inert on
   ordinary days: an ordinary evening peak is under 2.5x the median, the
   2026-10-12 intervals were 50x and 120x.
2. Rebuy. The sell price beats the *rebuy price* grossed up for round-trip
   losses, plus the operator's ``export_price_margin``. The rebuy price is the
   DEAREST buy price in the following ``SPIKE_EXPORT_REBUY_LOOKAHEAD_HOURS``,
   once the spike is over: the worst the energy could cost to replace. It is
   deliberately the dearest and not the cheapest. In this mode grid charging
   is itself gated (cheap threshold, no import in the demand window), so the
   planner cannot promise to refill at the cheapest price.
3. Not a dearer interval of the same spike. The slots that follow slot 0
   without a break and are themselves spike-priced (sell price at or above the
   outlier floor) are the rest of the same spike. They are somewhere else to
   SELL, not what the energy costs to replace, so they are left out of the
   rebuy price and compared like for like: one of them blocks the export only
   when it pays more than the current interval does.

Test 3 exists because the first cut put those slots in the rebuy price. Feed-in
is about 0.9x general, so a following interval forecast above roughly 87% of
the current sell price failed test 2, and a spike was held through until its
last interval: a flat four-interval $10 spike sold once, a falling
$15/$12/$10/$8 spike sold only at $8. With test 3 both sell in every interval,
and a rising spike still waits for its dearest interval. A dear interval after
a gap is not part of the run and still counts as a rebuy price.

Only the CURRENT interval (slot 0) can be admitted. A spike that is merely
forecast is never planned as an export, for two reasons. Later slots are not
priced honestly enough to act on (their sell price is still a 15-minute mean,
issue #1119). And a planned future export is a reason to charge beforehand:
measured on the 2026-08-05 fixture, admitting the forecast $1.65 slot made the
planner add an overnight grid-charge slot to sell into it, which is exactly the
charge/discharge cycling this mode exists to avoid. With slot 0 only, nothing
is ever charged in order to be exported later.

Test 1 cannot be folded into test 2, for the reason ``spike_event`` gives for
its own outlier factor: a price spread alone does not tell a spike from the
ordinary daily shape, and a rule that sits near a boundary on ordinary days
flips with forecast jitter.

WHAT THIS MODULE DOES NOT DO
----------------------------
It decides nothing. It only widens the feasible set; the DP still prices the
export against holding, the load-sized export floor (``export_floor``) still
bounds how far it may drain, and the state machine's PROACTIVE_EXPORT path
(reserve leash, re-stepped reserve) is unchanged. Consult
docs/PLANNING_MODEL.md before changing it.

It does not unlock grid charging: which slots may charge is decided elsewhere
and is untouched. It CAN make the plan charge more. Before the demand window,
selling leaves a gap to the entry target and the planner refills it from the
grid in slots that were already allowed to charge. Measured on the 2026-10-09
12:30 capture with a confirmed $1.00 spike in the current interval: one charge
slot becomes four (about 9.6c against the $1.00 sale) and the projected
demand-window entry drops from 100% to 95.3%, still on target. That is an
export followed by a recharge within the hour. It is the planner's own priced
choice, not a guarantee of this module, and the entry target then rests on the
pre-charge path landing it.

It does nothing in ``arbitrage`` mode, where every positive sell price is
already admitted. The verdict is still computed there, because the state
machine uses it to tell a genuine spike from an ordinary export.
"""

from __future__ import annotations

from ..const import SPIKE_EXPORT_OUTLIER_FACTOR, SPIKE_EXPORT_REBUY_LOOKAHEAD_HOURS
from .types import OptimizerConfig, SlotContext


def _median_buy_price(slots: list[SlotContext]) -> float | None:
    """Median buy price of the horizon, or None when it is not positive."""
    prices = sorted(s.buy_price for s in slots)
    if not prices:
        return None
    median = prices[len(prices) // 2]
    return median if median > 0 else None


def _end_of_spike(slots: list[SlotContext], spike_floor: float | None) -> int:
    """Index of the first slot after slot 0 that is not part of the same spike.

    The same spike is the unbroken run of slots after slot 0 whose sell price
    is at or above the outlier floor. 1 when the next slot is already ordinary.
    """
    if spike_floor is None:
        return 1
    end = 1
    while end < len(slots) and slots[end].sell_price >= spike_floor:
        end += 1
    return end


def _rebuy(slots: list[SlotContext], first: int) -> tuple[float, int] | None:
    """Dearest buy price from slot ``first`` to the end of the lookahead.

    The lookahead is measured from the end of slot 0 whatever ``first`` is, so
    a long spike does not push the window later. None when no slot from
    ``first`` on falls inside it: with nothing to judge the export against, it
    is not admitted.
    """
    budget_minutes = SPIKE_EXPORT_REBUY_LOOKAHEAD_HOURS * 60.0
    elapsed = 0.0
    best: tuple[float, int] | None = None
    for j in range(1, len(slots)):
        if elapsed >= budget_minutes:
            break
        if j >= first:
            price = max(0.0, slots[j].buy_price)
            if best is None or price > best[0]:
                best = (price, j)
        elapsed += slots[j].slot_interval_minutes
    return best


def apply_spike_export_admission(
    slots: list[SlotContext], config: OptimizerConfig
) -> None:
    """Decide whether slot 0 is a genuine spike and record why.

    Sets ``spike_export_admitted`` on slot 0 (every later slot stays False) and
    publishes the verdict on ``config.spike_export_now`` for the summary sensor
    and the state machine.
    """
    for slot in slots:
        slot.spike_export_admitted = False
    config.spike_export_now = {}
    if not slots:
        return

    slot = slots[0]
    median = _median_buy_price(slots)
    round_trip = config.charge_efficiency * config.discharge_efficiency
    spike_floor = median * SPIKE_EXPORT_OUTLIER_FACTOR if median is not None else None
    margin = max(0.0, config.export_price_margin)
    spike_end = _end_of_spike(slots, spike_floor)
    rebuy = _rebuy(slots, spike_end)
    # The dearest sell price in the rest of the same spike: selling now is not
    # admitted while a later interval of it pays more.
    later_spike_sell = max((s.sell_price for s in slots[1:spike_end]), default=None)

    min_sell: float | None = None
    if rebuy is not None and spike_floor is not None and round_trip > 0:
        min_sell = max(
            spike_floor, rebuy[0] / round_trip + margin, later_spike_sell or 0.0
        )
    admitted = min_sell is not None and slot.sell_price >= min_sell
    slot.spike_export_admitted = admitted

    def _rounded(value: float | None) -> float | None:
        return round(value, 4) if value is not None else None

    config.spike_export_now = {
        "admitted": admitted,
        "sell": round(slot.sell_price, 4),
        "rebuy_price": _rounded(rebuy[0] if rebuy is not None else None),
        "rebuy_at": slots[rebuy[1]].timestamp_iso if rebuy is not None else None,
        "later_spike_sell": _rounded(later_spike_sell),
        "min_sell": _rounded(min_sell),
        "spike_floor": _rounded(spike_floor),
        "median_buy": _rounded(median),
    }
