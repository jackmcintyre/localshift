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
    slots[first].is_demand_window_entry = True
    config.demand_window_target_soc_pct = block_target_soc_pct(
        slots[first : last + 1], config, solar_accuracy, ceiling
    )
    return block


def _mirror_charge_window(slots: list[SlotContext]) -> None:
    """Deadline flags = charge-window flags, with an entry on each window's first slot."""
    previous = False
    for slot in slots:
        in_window = bool(slot.is_charge_window_slot)
        slot.is_demand_window_slot = in_window
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
    """First run of dear slots lasting ``min_duration_min`` or more, as (first, last).

    A slot is dear when it costs at least ``min_spread`` more than the cheapest slot
    before it (the cheapest charge the battery could have taken) and carries a net
    load. That reference never rises as the horizon advances, so it cannot move a
    block boundary by itself. A single slot short of the test inside a run is
    tolerated. Slots in a charge window are never part of a block.
    """
    reference: float | None = None
    run_first: int | None = None
    run_last = 0
    gap = 0
    for idx, slot in enumerate(slots):
        dear = (
            reference is not None
            and not slot.is_charge_window_slot
            and slot.buy_price >= reference + min_spread
            and slot.consumption_kwh > slot.solar_kwh
        )
        if dear:
            if run_first is None:
                run_first = idx
            run_last = idx
            gap = 0
        elif run_first is not None:
            gap += 1
            if gap > 1:
                if _minutes(slots[run_first : run_last + 1]) >= min_duration_min:
                    return run_first, run_last
                run_first = None
                gap = 0
        reference = (
            slot.buy_price if reference is None else min(reference, slot.buy_price)
        )
    if run_first is not None and _minutes(slots[run_first : run_last + 1]) >= (
        min_duration_min
    ):
        return run_first, run_last
    return None


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
    Clamped between the minimum target SOC and the configured target.
    """
    accuracy = max(0.0, min(1.0, solar_accuracy))
    net_kwh = sum(max(0.0, s.consumption_kwh - s.solar_kwh * accuracy) for s in block)
    needed_pct = (
        net_kwh / config.discharge_efficiency / config.battery_capacity_kwh * 100.0
    )
    floor = min(config.min_soc_pct, ceiling_pct)
    return max(floor, min(ceiling_pct, floor + needed_pct + PRICE_BLOCK_HEADROOM_PCT))
