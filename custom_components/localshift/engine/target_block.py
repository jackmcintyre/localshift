"""Price-driven target block detection (docs/PRICE_BLOCK_TARGET.md, slice 1).

WHY THIS EXISTS
---------------
The planner's demand window is a clock window applied every day of the year, and
it is the only thing that funds a pre-charge ahead of the expensive evening. The
tariff's demand charge is seasonal; the expensive evening is not. This module
finds the expensive evening from the forecast itself, so the deadline machinery
has something to key on when the clock window has no basis.

WHAT THIS MODULE DOES
---------------------
``find_target_block`` returns the first run of slots that is materially dearer
than the cheapest charge the battery could have taken beforehand and that
carries positive net load, together with the energy needed to carry that run.

1. ``p_ref(i)`` is the cheapest buy price in any slot strictly before ``i``. It
   is monotone non-increasing in ``i``, so it cannot move a boundary on its own.
2. A slot is *dear* when ``buy_price >= p_ref + block_min_spread`` and
   ``consumption_kwh > solar_kwh``.
3. The block is the first maximal run of dear slots, tolerating single-slot
   gaps, that spans at least ``block_min_duration_hours``.
4. ``needed_kwh`` is the run's net load with solar discounted by forecast
   accuracy (the same clamp ``check_global_solar_sufficiency`` applies), divided
   by discharge efficiency. ``needed_pct`` is that as a share of capacity.

WHAT THIS MODULE DELIBERATELY DOES NOT DO
-----------------------------------------
It does not touch a slot flag, a threshold or the planner. It is a pure function
of its arguments: the caller decides what a block means. It does not clamp the
sizing to any target either; the caller owns ``minimum_target_soc`` and
``battery_target``, and a long evening routinely needs more than one battery.

``block_min_spread`` is not ``min_cycle_saving``. The cycle hurdle governs
speculative arbitrage; the block spread answers a different question, namely
whether this evening is expensive enough to prepare for.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from custom_components.localshift.engine.types import OptimizerConfig, SlotContext

DEFAULT_BLOCK_MIN_SPREAD = 0.08
"""$/kWh a slot must sit above the cheapest earlier price to count as dear."""

DEFAULT_BLOCK_MIN_DURATION_HOURS = 2.0
"""Shortest dear run that counts as a block worth preparing for."""

# Prices arrive as binary floats, so a slot priced exactly one spread above the
# reference (0.07 + 0.08 against 0.15) would otherwise fail ">=" on
# representation error alone.
_EPSILON = 1e-9

# The longest non-dear stretch a run may contain without being split.
_MAX_GAP_SLOTS = 1

# How far a fresh detection may sit from the previous plan's entry and still be
# treated as the same boundary.
_HYSTERESIS_SLOTS = 1


@dataclass(frozen=True)
class TargetBlock:
    """The expensive block the planner should arrive prepared for."""

    entry_idx: int
    """Index of the first slot of the block."""

    end_idx: int
    """Index of the last slot of the block (inclusive)."""

    needed_kwh: float
    """Battery energy needed to carry the block's net load, before any clamp."""

    needed_pct: float
    """``needed_kwh`` as a percentage of battery capacity, before any clamp."""

    reason: str
    """Human-readable account of why this block was chosen."""


def _solar_accuracy(config: OptimizerConfig) -> float:
    """Solar forecast accuracy clamped to [0, 1]; missing or unusable reads as 0."""
    accuracy = getattr(config, "solar_forecast_accuracy", None)
    if accuracy is None or math.isnan(accuracy):
        return 0.0
    return max(0.0, min(1.0, accuracy))


def _dear_slots(slots: list[SlotContext], min_spread: float) -> list[bool]:
    """Flag each slot that is dear against the cheapest price before it.

    Slot 0 is never dear: nothing precedes it, so there is no earlier charge it
    could be dearer than.
    """
    dear = [False] * len(slots)
    p_ref = slots[0].buy_price
    for i in range(1, len(slots)):
        slot = slots[i]
        dear[i] = (
            slot.buy_price >= p_ref + min_spread - _EPSILON
            and slot.consumption_kwh > slot.solar_kwh
        )
        p_ref = min(p_ref, slot.buy_price)
    return dear


def _run_end(dear: list[bool], start: int) -> int:
    """Last dear slot of the run starting at ``start``, bridging short gaps."""
    end = start
    gap = 0
    for i in range(start + 1, len(dear)):
        if dear[i]:
            end = i
            gap = 0
            continue
        gap += 1
        if gap > _MAX_GAP_SLOTS:
            break
    return end


def _hours(slots: list[SlotContext], first: int, last: int) -> float:
    return sum(s.slot_interval_minutes for s in slots[first : last + 1]) / 60.0


def _first_qualifying_run(
    slots: list[SlotContext], dear: list[bool], min_duration_hours: float
) -> tuple[int, int] | None:
    """Return (entry, end) of the first long-enough run of dear slots.

    A run starts and ends on a dear slot. A tolerated gap inside it counts
    toward its duration, because the block spans it.
    """
    i = 0
    while i < len(slots):
        if not dear[i]:
            i += 1
            continue
        end = _run_end(dear, i)
        if _hours(slots, i, end) >= min_duration_hours - _EPSILON:
            return i, end
        i = end + 1
    return None


def find_target_block(
    slots: list[SlotContext],
    config: OptimizerConfig,
    previous_entry_idx: int | None = None,
) -> TargetBlock | None:
    """Find the first expensive block in the horizon and size what it needs.

    Args:
        slots: Planning horizon, slot 0 being now.
        config: Optimizer configuration (spread, duration, efficiency, capacity,
            solar forecast accuracy).
        previous_entry_idx: Entry index from the previous plan, expressed in this
            horizon's indices. When the fresh detection lands within one slot of
            it the previous entry is kept, so forecast jitter at the boundary
            cannot move the deadline back and forth between re-plans. It never
            creates a block: with nothing detected the result is None.

    Returns:
        The block, or None when no run qualifies, the horizon is too short to
        have a reference price, or the battery cannot be sized against.

    """
    if len(slots) < 2:
        return None

    capacity_kwh = config.battery_capacity_kwh
    discharge_efficiency = config.discharge_efficiency
    if capacity_kwh <= 0 or discharge_efficiency <= 0:
        return None

    min_spread = getattr(config, "block_min_spread", DEFAULT_BLOCK_MIN_SPREAD)
    min_duration_hours = getattr(
        config, "block_min_duration_hours", DEFAULT_BLOCK_MIN_DURATION_HOURS
    )

    run = _first_qualifying_run(
        slots, _dear_slots(slots, min_spread), min_duration_hours
    )
    if run is None:
        return None
    detected_entry, end_idx = run

    entry_idx = detected_entry
    held = (
        previous_entry_idx is not None
        and previous_entry_idx != detected_entry
        and abs(previous_entry_idx - detected_entry) <= _HYSTERESIS_SLOTS
    )
    if held:
        entry_idx = previous_entry_idx

    accuracy = _solar_accuracy(config)
    net_load_kwh = sum(
        max(0.0, slot.consumption_kwh - slot.solar_kwh * accuracy)
        for slot in slots[entry_idx : end_idx + 1]
    )
    needed_kwh = net_load_kwh / discharge_efficiency
    needed_pct = needed_kwh / capacity_kwh * 100.0

    reason = (
        f"dear run of {_hours(slots, entry_idx, end_idx):.1f} h from slot "
        f"{entry_idx} to {end_idx} (>= ${min_spread:.2f}/kWh above the cheapest "
        f"earlier price), net load {net_load_kwh:.2f} kWh at solar accuracy "
        f"{accuracy:.2f}"
    )
    if held:
        reason += f"; entry held from previous plan (detected {detected_entry})"

    return TargetBlock(
        entry_idx=entry_idx,
        end_idx=end_idx,
        needed_kwh=needed_kwh,
        needed_pct=needed_pct,
        reason=reason,
    )
