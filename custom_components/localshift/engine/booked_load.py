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
from .types import OptimizerInputs, SlotContext

SLOT = timedelta(minutes=15)
# Until real usage is learned (S3), a booked load counts for a fifth more than
# its estimate: an under-count would plan the battery too thin.
LOAD_MARGIN = 1.2
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


def _import_through_window(result: Any, slots: list[SlotContext]) -> float | None:
    """Grid import (kWh) up to the end of the first demand window; None if none."""
    last = None
    for i, slot in enumerate(slots):
        if slot.is_demand_window_slot:
            last = i
        elif last is not None:
            break
    if last is None:
        return None
    return sum(d.grid_import_kwh for d in result.decisions[: last + 1])


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
    before or during it, against the plan without this load, is rejected. A run
    that would end past the planning horizon is not a candidate: its tail would
    be invisible and so look free.
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
        return planner.plan(dataclasses.replace(base, slots=slots)), slots

    plain, plain_slots = solve([])
    if not plain.success:
        return None, "no plan to evaluate against"
    ceiling = _import_through_window(plain, plain_slots)
    best: tuple[float, datetime] | None = None
    tried = 0
    start = earliest
    while start <= latest_finish - duration and start + duration <= horizon_end:
        result, slots = solve([(start, start + duration, kwh * LOAD_MARGIN)])
        tried += 1
        imported = _import_through_window(result, slots)
        within = ceiling is None or (imported or 0.0) <= ceiling + _IMPORT_EPS_KWH
        if result.success and within:
            if best is None or result.projected_net_cost < best[0]:
                best = (result.projected_net_cost, start)
        start += SLOT
    if best is not None:
        return best[1], "lowest plan cost"
    if tried:
        return None, "every start adds grid import before or during the demand window"
    return None, "no start fits inside the planning horizon"
