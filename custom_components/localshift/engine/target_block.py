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

1. ``p_ref(i)`` is the cheapest buy price in any slot strictly before ``i``, or
   the remembered trough when the caller passes one and it is cheaper. Within
   one plan it is monotone non-increasing in ``i``, so it cannot move a
   boundary on its own. Between plans the horizon's own part of it rises as
   the cheap morning becomes the past, which is why the caller remembers the
   trough (#1114).
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

It keeps no state. What one plan must remember for the next (the cheapest price
the day has already offered, the block last adopted, a change waiting out its
dwell) lives on the coordinator data and is handled by
``slots.apply_price_block_flags``; ``remember_trough`` and ``hold_target_block``
are the pure pieces of that.

``block_min_spread`` is not ``min_cycle_saving``. The cycle hurdle governs
speculative arbitrage; the block spread answers a different question, namely
whether this evening is expensive enough to prepare for.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

from custom_components.localshift.engine.types import OptimizerConfig, SlotContext

# Prices arrive as binary floats, so a slot priced exactly one spread above the
# reference (0.07 + 0.08 against 0.15) would otherwise fail ">=" on
# representation error alone.
_EPSILON = 1e-9

# The longest non-dear stretch a run may contain without being split.
_MAX_GAP_SLOTS = 1

# How far a fresh detection may sit from the previous plan's entry and still be
# treated as the same boundary.
_HYSTERESIS_SLOTS = 1

# How long a changed detection (an entry moved by more than the hysteresis, or a
# block appearing or disappearing) must persist before it replaces the block the
# planner is working to. Internal, not an operator entity (#1114).
ENTRY_DWELL_MINUTES = 60

# How far back the remembered trough may reach. A price older than this is no
# longer "the cheapest charge the battery could have taken" for today's evening.
TROUGH_MEMORY_HOURS = 24

TroughHistory = list[tuple[str, float]]
"""Observed (timestamp, buy price) pairs, oldest first and strictly rising in
price: the first is the cheapest price in the memory window and each later one
is the cheapest seen since the one before it. That is all a rolling minimum
needs, so the list stays a handful of entries long."""


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


def _usable_price(price: float | None) -> bool:
    return isinstance(price, (int, float)) and math.isfinite(price)


def _dear_slots(
    slots: list[SlotContext], min_spread: float, trough_price: float | None = None
) -> list[bool]:
    """Flag each slot that is dear against the cheapest price before it.

    Slot 0 is never dear: it is now, and the block is something to prepare for.
    A remembered trough lowers the reference for every later slot; it never
    raises it.
    """
    dear = [False] * len(slots)
    p_ref = slots[0].buy_price
    if trough_price is not None and _usable_price(trough_price):
        p_ref = min(p_ref, trough_price)
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
    trough_price: float | None = None,
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
        trough_price: The cheapest buy price already observed since the last
            block, remembered by the caller. The reference for slot ``i``
            becomes the cheaper of this and the cheapest slot before ``i``, so
            a block does not vanish merely because its cheap morning is now the
            past. None, or an unusable number, leaves the horizon alone.

    Returns:
        The block, or None when no run qualifies, the horizon is too short to
        have a reference price, or the battery cannot be sized against.

    """
    if len(slots) < 2 or not _sizeable(config):
        return None

    min_spread = config.block_min_spread
    run = _first_qualifying_run(
        slots,
        _dear_slots(slots, min_spread, trough_price),
        config.block_min_duration_hours,
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

    reason = (
        f"dear run of {_hours(slots, entry_idx, end_idx):.1f} h from slot "
        f"{entry_idx} to {end_idx} (>= ${min_spread:.2f}/kWh above the cheapest "
        f"earlier price)"
    )
    note = ""
    if _trough_sets_reference(slots, detected_entry, trough_price):
        note += f"; reference is the remembered trough ${trough_price:.4f}/kWh"
    if held:
        note += f"; entry held from previous plan (detected {detected_entry})"
    return _sized(slots, config, entry_idx, end_idx, reason, note)


def _sizeable(config: OptimizerConfig) -> bool:
    return config.battery_capacity_kwh > 0 and config.discharge_efficiency > 0


def _trough_sets_reference(
    slots: list[SlotContext], entry_idx: int, trough_price: float | None
) -> bool:
    """True when the remembered trough, not the horizon, is the entry's reference."""
    if trough_price is None or not _usable_price(trough_price):
        return False
    return trough_price < min(slot.buy_price for slot in slots[:entry_idx])


def _sized(
    slots: list[SlotContext],
    config: OptimizerConfig,
    entry_idx: int,
    end_idx: int,
    reason: str,
    note: str = "",
) -> TargetBlock:
    """The block over ``entry_idx..end_idx`` with its need sized on these slots."""
    accuracy = _solar_accuracy(config)
    net_load_kwh = sum(
        max(0.0, slot.consumption_kwh - slot.solar_kwh * accuracy)
        for slot in slots[entry_idx : end_idx + 1]
    )
    needed_kwh = net_load_kwh / config.discharge_efficiency
    return TargetBlock(
        entry_idx=entry_idx,
        end_idx=end_idx,
        needed_kwh=needed_kwh,
        needed_pct=needed_kwh / config.battery_capacity_kwh * 100.0,
        reason=(
            f"{reason}, net load {net_load_kwh:.2f} kWh at solar accuracy "
            f"{accuracy:.2f}{note}"
        ),
    )


def hold_target_block(
    slots: list[SlotContext],
    config: OptimizerConfig,
    entry_idx: int,
    end_idx: int,
    why: str,
) -> TargetBlock | None:
    """A block the caller is holding, re-sized on the current horizon.

    While a changed detection waits out its dwell the previously adopted block
    stands. Its boundaries are the ones adopted; its need is computed afresh,
    because the load and solar forecast inside it keep moving.

    Args:
        slots: Planning horizon, slot 0 being now.
        config: Optimizer configuration.
        entry_idx: Index of the held entry in this horizon.
        end_idx: Index of the held block's last slot. Clamped into the horizon
            and never before the entry.
        why: Why it is held, for the reason text.

    Returns:
        The block, or None when the entry is not a slot ahead of now or the
        battery cannot be sized against.

    """
    if not _sizeable(config) or not 0 < entry_idx < len(slots):
        return None
    end_idx = max(entry_idx, min(end_idx, len(slots) - 1))
    reason = (
        f"held block of {_hours(slots, entry_idx, end_idx):.1f} h from slot "
        f"{entry_idx} to {end_idx} ({why})"
    )
    return _sized(slots, config, entry_idx, end_idx, reason)


def remember_trough(
    history: TroughHistory,
    at_iso: str,
    price: float | None,
    window_hours: float = TROUGH_MEMORY_HOURS,
) -> TroughHistory:
    """Forget what has aged out of the window, then note the price seen now.

    Args:
        history: The memory so far (``TroughHistory``). Not modified.
        at_iso: When ``price`` was observed, ISO format.
        price: The buy price observed then. None only forgets.
        window_hours: How far back the memory reaches.

    Returns:
        The new memory. ``history[0]`` is the trough: the cheapest price
        observed within the window, and when it was last seen. With ``at_iso``
        unparseable the memory is returned as it was.

    """
    try:
        now = datetime.fromisoformat(at_iso)
        oldest = now - timedelta(hours=window_hours)
        kept = [
            (stamp, seen)
            for stamp, seen in history
            if oldest <= datetime.fromisoformat(stamp) <= now
        ]
    except (TypeError, ValueError):
        # Unparseable, or aware against naive: nothing can be aged.
        return list(history)
    if price is None or not _usable_price(price):
        return kept
    # Anything no cheaper than this is superseded: it is older and cannot be
    # the minimum of any window that still contains this observation.
    kept = [(stamp, seen) for stamp, seen in kept if seen < price]
    kept.append((at_iso, float(price)))
    return kept
