"""Booked deferrable loads inside the plan (issue #1238).

A booking (washer, dryer, dishwasher) is known extra load: it is added to the
per-slot consumption the planner sees, and the start of a new booking is the
candidate whose plan costs least. Pure functions; nothing here reads Home
Assistant or holds state, so the planner stays deterministic.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Any

from .core import DPPlanner
from .cost import terminal_salvage_value
from .types import OptimizerInputs, SlotContext

SLOT = timedelta(minutes=15)
# Until real usage is learned (S3), a booked load counts for a fifth more than
# its estimate: an under-count would plan the battery too thin.
LOAD_MARGIN = 1.2
# Each candidate is a full plan (~0.2 s); a long window is thinned to this many.
MAX_CANDIDATES = 48
# Grid import below this is solver noise, not extra import.
_IMPORT_EPS_KWH = 0.01
_ACTIVE = ("booked", "started")

BookedLoad = tuple[datetime, datetime, float]


def active_loads(bookings: Iterable[dict[str, Any]] | None) -> list[BookedLoad]:
    """(start, end, kWh with margin) for every booking still to run.

    A stored booking from before S2 has no ``duration_min`` and is skipped.
    """
    loads = []
    for b in bookings or ():
        if b.get("status") in _ACTIVE and b.get("duration_min"):
            start = datetime.fromisoformat(b["start_at"])
            end = start + timedelta(minutes=b["duration_min"])
            loads.append((start, end, b["est_kwh"] * LOAD_MARGIN))
    return loads


def with_booked_load(
    slots: list[SlotContext], loads: list[BookedLoad]
) -> list[SlotContext]:
    """Copies of the slots with each load's kWh spread evenly over its run.

    With no loads the very same list comes back, so a plan without bookings
    is untouched.
    """
    if not loads:
        return slots
    out = []
    for slot in slots:
        begin = datetime.fromisoformat(slot.timestamp_iso)
        end = begin + timedelta(minutes=slot.slot_interval_minutes)
        extra = 0.0
        for start, stop, kwh in loads:
            overlap = (min(end, stop) - max(begin, start)).total_seconds()
            if overlap > 0:
                extra += kwh * overlap / (stop - start).total_seconds()
        out.append(
            dataclasses.replace(slot, consumption_kwh=slot.consumption_kwh + extra)
            if extra
            else dataclasses.replace(slot)
        )
    return out


def _window_imports(
    result: Any, slots: list[SlotContext]
) -> tuple[float, float] | None:
    """Grid import (kWh) before the first demand window and during it; None if none."""
    flags = [slot.is_demand_window_slot for slot in slots]
    if True not in flags:
        return None
    first = flags.index(True)
    last = first + flags[first:].index(False) if False in flags[first:] else len(flags)
    imports = [d.grid_import_kwh for d in result.decisions]
    return sum(imports[:first]), sum(imports[first:last])


def _adds_window_import(
    got: tuple[float, float] | None, ceiling: tuple[float, float] | None
) -> bool:
    """True if either the before-window or in-window import exceeds the baseline."""
    if ceiling is None or got is None:
        return False
    return any(g > c + _IMPORT_EPS_KWH for g, c in zip(got, ceiling, strict=True))


def _objective(result: Any, base: OptimizerInputs) -> float:
    """Net cost less the DP's own salvage credit for battery left at the horizon end.

    Without the credit, energy a load drains late in the horizon looks free.
    """
    if not base.config.terminal_salvage_enabled or not result.decisions:
        return result.projected_net_cost
    cheapest = min(slot.buy_price for slot in base.slots)
    credit = terminal_salvage_value(
        result.decisions[-1].predicted_soc_pct, base.config, cheapest
    )
    return result.projected_net_cost - credit


def _candidates(
    earliest: datetime,
    latest_start: datetime,
    horizon_end: datetime,
    duration: timedelta,
) -> list[datetime]:
    """Starts on the clock's 15-minute boundaries, thinned evenly to MAX_CANDIDATES."""
    latest_start = min(latest_start, horizon_end - duration)
    midnight = earliest.replace(hour=0, minute=0, second=0, microsecond=0)
    steps = -(-(earliest - midnight) // SLOT)
    start = midnight + steps * SLOT
    starts = []
    while start <= latest_start:
        starts.append(start)
        start += SLOT
    if not starts and earliest <= latest_start:
        starts = [earliest]
    if len(starts) > MAX_CANDIDATES:
        pick = [
            round(i * (len(starts) - 1) / (MAX_CANDIDATES - 1))
            for i in range(MAX_CANDIDATES)
        ]
        starts = [starts[i] for i in pick]
    return starts


def _why_none(candidates: int, solved: int) -> str:
    if not candidates:
        return "no start fits inside the planning horizon"
    if not solved:
        return "every candidate plan failed"
    return "every start adds grid import before or during the demand window"


def choose_start(
    planner: DPPlanner,
    base: OptimizerInputs,
    bookings: Iterable[dict[str, Any]],
    earliest: datetime,
    latest_finish: datetime,
    duration: timedelta,
    kwh: float,
) -> tuple[datetime | None, str]:
    """The cheapest start on the 15-minute grid, else (None, why none was chosen).

    ``base`` carries the slots before any booking; earlier bookings are laid on
    as fixed load. One plan is solved per candidate, so call this off the event
    loop. When the demand window is in force, a candidate that adds grid import
    before it, or during it, against the plan without this load, is rejected
    (each checked on its own, so a pre-window saving cannot pay for in-window
    import). A run that would end past the planning horizon is not a candidate:
    its tail would be invisible and so look free. Candidates are ranked on net
    cost less the terminal salvage credit.
    """
    fixed = active_loads(bookings)
    horizon_end = max(
        (
            datetime.fromisoformat(s.timestamp_iso)
            + timedelta(minutes=s.slot_interval_minutes)
            for s in base.slots
        ),
        default=earliest,
    )

    def solve(extra: list[BookedLoad]) -> Any:
        slots = with_booked_load(base.slots, fixed + extra)
        if slots is base.slots:  # the planner writes to its slots
            slots = [dataclasses.replace(s) for s in slots]
        # ...and to its config, from this thread.
        inputs = dataclasses.replace(
            base, slots=slots, config=dataclasses.replace(base.config)
        )
        return planner.plan(inputs), slots

    plain, plain_slots = solve([])
    if not plain.success:
        return None, "no plan to evaluate against"
    ceiling = _window_imports(plain, plain_slots)
    best: tuple[float, datetime] | None = None
    solved = 0
    starts = _candidates(earliest, latest_finish - duration, horizon_end, duration)
    for start in starts:
        result, slots = solve([(start, start + duration, kwh * LOAD_MARGIN)])
        if not result.success:
            continue
        solved += 1
        imported = _window_imports(result, slots)
        if _adds_window_import(imported, ceiling):
            continue
        cost = _objective(result, base)
        if best is None or cost < best[0]:
            best = (cost, start)
    if best is not None:
        return best[1], "lowest plan cost"
    return None, _why_none(len(starts), solved)
