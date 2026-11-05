"""Price-block target: the deadline and target for the expensive evening (#1104).

The demand window used to do two jobs. It banned grid import (the demand charge) and
it set the deadline, target and useful period the planner prepares the battery for
(the expensive evening). Only the first belongs to the demand charge, which is
seasonal and which Amber flags per interval.

With ``switch.localshift_demand_window_from_amber`` on, the import ban reads
``SlotContext.is_charge_window_slot`` (Amber's flag). The deadline reads
``is_demand_window_slot``, which this module sets: the flagged window when one is
ahead, otherwise the first expensive run of slots found in the price and load
forecast. The rest of the planner reads the same two slot flags it always did, so
the water level, hard floor, urgency window, boost unlocks and useful-period stop
follow without change. See docs/PRICE_BLOCK_TARGET.md.
"""

from __future__ import annotations

from custom_components.localshift.const import (
    PRICE_BLOCK_HEADROOM_PCT,
    PRICE_BLOCK_LOW_PERCENTILE,
    PRICE_BLOCK_MAX_GAP_MIN,
    PRICE_BLOCK_MIN_DURATION_MIN,
    PRICE_BLOCK_MIN_SPREAD,
)
from custom_components.localshift.engine.types import OptimizerConfig, SlotContext


def apply_price_block(
    slots: list[SlotContext],
    config: OptimizerConfig,
    solar_accuracy: float = 1.0,
) -> tuple[int, int] | None:
    """Set the deadline flags on ``slots`` and the target on ``config``.

    Does nothing with the switch off, so the plan is exactly what it was. With it
    on, the deadline flags are rebuilt from the charge-window flags on every call
    (the planner may be given the same slots again), then a price block is added
    when no charge window is in view. Returns the block's (first, last) slot
    indices, or None when the deadline is the flagged window or there is none.
    """
    if not config.demand_window_from_amber:
        return None

    ceiling = config.price_block_target_ceiling_pct
    if ceiling is None:
        ceiling = config.price_block_target_ceiling_pct = (
            config.demand_window_target_soc_pct
        )
    config.demand_window_target_soc_pct = ceiling

    _mirror_charge_window(slots)
    if _charge_window_in_view(slots):
        return None

    block = detect_price_block(slots)
    if block is None:
        return None
    first, last = block
    for slot in slots[first : last + 1]:
        slot.is_demand_window_slot = True
        slot.charge_window_override = False
    slots[first].is_demand_window_entry = True
    config.demand_window_target_soc_pct = block_target_soc_pct(
        slots[first : last + 1], config, solar_accuracy, ceiling
    )
    return block


def _mirror_charge_window(slots: list[SlotContext]) -> None:
    """Deadline flags = charge-window flags, with an entry on each window's first slot."""
    previous = False
    for slot in slots:
        in_window = slot.is_charge_window_slot
        slot.is_demand_window_slot = in_window
        slot.charge_window_override = None
        slot.is_demand_window_entry = in_window and not previous
        previous = in_window


def _charge_window_in_view(slots: list[SlotContext]) -> bool:
    """Any charge-window slot in the horizon, open now or ahead.

    A window open at slot 0 is not a deadline, but it is not a day without one either:
    the next-window tail (#1033) prepares for the window that follows it, and a price
    block set up beside that would give the same evening two targets.
    """
    return any(slot.is_charge_window_slot for slot in slots)


def detect_price_block(
    slots: list[SlotContext],
    min_spread: float = PRICE_BLOCK_MIN_SPREAD,
    min_duration_min: float = PRICE_BLOCK_MIN_DURATION_MIN,
) -> tuple[int, int] | None:
    """The expensive run to prepare for, as (first, last) slot indices, or None.

    A slot is dear when it costs at least ``min_spread`` more than the horizon's low
    price (see ``_low_price``) and carries a net load. A run is dear slots with
    not-dear gaps of at most ``PRICE_BLOCK_MAX_GAP_MIN``; it counts when it lasts
    ``min_duration_min``. The reference is the whole horizon's low, not the cheapest
    slot before the run: that one would move as ``now`` advances and drop the block
    on a ramp once the cheap slots are behind it. What is cheap enough to fund it is
    the planner's to decide (water level, ``max_precharge_price``). The run already
    under way at slot 0 counts whatever is left of it, so a plan made inside the peak
    finds tonight's block and not tomorrow's.
    Of the others the one carrying the most net load wins, because it is the one
    that needs the preparing; a morning peak does not outrank a larger evening by
    coming first. Slots in a charge window are never part of a block.
    """
    low = _low_price(slots)
    if low is None:
        return None
    best: tuple[float, tuple[int, int]] | None = None
    for first, last in _dear_runs(slots, low + min_spread):
        run = slots[first : last + 1]
        if first == 0:
            return first, last
        if _minutes(run) < min_duration_min:
            continue
        load = _net_kwh(run, 1.0)
        if best is None or load > best[0]:
            best = (load, (first, last))
    return best[1] if best else None


def _is_priced(slot: SlotContext) -> bool:
    return not slot.price_defaulted


def _low_price(slots: list[SlotContext]) -> float | None:
    """Duration-weighted ``PRICE_BLOCK_LOW_PERCENTILE`` of buy prices, floored at 0.

    Slots that fell back to the 0.0 default (issue #987) are not prices and are left
    out; a negative price counts as 0, the cheapest a charge can be.
    """
    priced = sorted(
        (max(0.0, s.buy_price), s.slot_interval_minutes) for s in slots if _is_priced(s)
    )
    if not priced:
        return None
    threshold = sum(minutes for _, minutes in priced) * PRICE_BLOCK_LOW_PERCENTILE
    elapsed = 0.0
    for price, minutes in priced:
        elapsed += minutes
        if elapsed >= threshold:
            return price
    return priced[-1][0]


def _dear_runs(slots: list[SlotContext], dear_from: float) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    first: int | None = None
    last = 0
    gap = 0.0
    for idx, slot in enumerate(slots):
        dear = (
            _is_priced(slot)
            and not slot.is_charge_window_slot
            and slot.buy_price >= dear_from
            and slot.consumption_kwh > slot.solar_kwh
        )
        if dear:
            if first is None:
                first = idx
            last = idx
            gap = 0.0
        elif first is not None:
            gap += slot.slot_interval_minutes
            if gap > PRICE_BLOCK_MAX_GAP_MIN:
                runs.append((first, last))
                first = None
    if first is not None:
        runs.append((first, last))
    return runs


def _net_kwh(block: list[SlotContext], solar_accuracy: float) -> float:
    return sum(
        max(0.0, s.consumption_kwh - s.solar_kwh * solar_accuracy) for s in block
    )


def _minutes(run: list[SlotContext]) -> float:
    return float(sum(slot.slot_interval_minutes for slot in run))


def block_target_soc_pct(
    block: list[SlotContext],
    config: OptimizerConfig,
    solar_accuracy: float,
    ceiling_pct: float,
) -> float:
    """SOC to hold entering the block: its net load, plus headroom, above the floor.

    Solar in the block is discounted by forecast accuracy as the pre-charge gates
    discount it, and the load is taken out of the battery at discharge efficiency.
    Clamped between the discharge floor (which honours the away reserve) and the
    configured target.
    """
    accuracy = max(0.0, min(1.0, solar_accuracy))
    net_kwh = _net_kwh(block, accuracy)
    needed_pct = (
        net_kwh / config.discharge_efficiency / config.battery_capacity_kwh * 100.0
    )
    floor = min(config.discharge_floor_pct, ceiling_pct)
    return max(floor, min(ceiling_pct, floor + needed_pct + PRICE_BLOCK_HEADROOM_PCT))
