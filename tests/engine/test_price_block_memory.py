"""Trough memory and entry dwell for the price block (docs/PRICE_BLOCK_TARGET.md; #1114).

The flap test (#1111) failed for two reasons, and this is the state that fixes
them. Both live on the coordinator data and are handled by
``apply_price_block_flags``; the detector stays a pure function.

1. *The reference price forgot the morning.* ``p_ref`` was the cheapest price
   among the slots still ahead, so it rose between plans as the cheap morning
   became the past and the block vanished mid-morning. The cheapest price
   actually observed since the last block is now remembered and passed in.
2. *Forecast revision on a knife edge.* A changed detection (entry moved by more
   than the one-slot hysteresis, or the block appearing or disappearing) is
   adopted only after it has persisted for ``ENTRY_DWELL_MINUTES``.

Plans are driven on synthetic 30-minute horizons that slide forward in time, the
way successive live plans do.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from custom_components.localshift.coordinator.data import CoordinatorData
from custom_components.localshift.engine import target_block
from custom_components.localshift.engine.optimizer_runner import (
    TARGET_BLOCK_MEMORY_KEYS,
    target_block_memory_telemetry,
)
from custom_components.localshift.engine.slots import (
    PRICE_BLOCK_STATE_FIELDS,
    apply_price_block_flags,
)
from custom_components.localshift.engine.target_block import (
    ENTRY_DWELL_MINUTES,
    TROUGH_MEMORY_HOURS,
    TargetBlock,
    find_target_block,
    hold_target_block,
    remember_trough,
)
from custom_components.localshift.engine.types import OptimizerConfig, SlotContext
from custom_components.localshift.sensors.optimizer import OptimizerSummarySensor
from tests.helpers.price_block_driver import CAPTURE_0907, drive, load

PriceAt = Callable[[datetime], float]

_ON = OptimizerConfig(price_block_target=True)


def _at(clock: str, day: int = 11) -> datetime:
    return datetime.fromisoformat(f"2026-09-{day:02d}T{clock}:00+10:00")


def _iso(clock: str, day: int = 11) -> str:
    return _at(clock, day).isoformat()


def _clock(stamp: str | None) -> str | None:
    return datetime.fromisoformat(stamp).strftime("%H:%M") if stamp else None


def _day(
    base: float,
    evening: float,
    *,
    dear_from: str = "17:00",
    dear_to: str = "21:00",
    now_price: dict[str, float] | None = None,
) -> PriceAt:
    """A price shape by clock time: ``base`` all day, ``evening`` in the dear
    window, and ``now_price`` overriding single half-hours."""
    overrides = now_price or {}

    def price_at(when: datetime) -> float:
        clock = when.strftime("%H:%M")
        if clock in overrides:
            return overrides[clock]
        return evening if dear_from <= clock < dear_to else base

    return price_at


def _horizon(start: datetime, price_at: PriceAt, hours: int = 15) -> list[SlotContext]:
    slots = []
    for index in range(hours * 2):
        when = start + timedelta(minutes=30 * index)
        slots.append(
            SlotContext(
                slot_index=index,
                timestamp_iso=when.isoformat(),
                slot_interval_minutes=30,
                buy_price=price_at(when),
                sell_price=0.0,
                solar_kwh=0.0,
                consumption_kwh=1.0,
            )
        )
    return slots


@dataclass
class _Plan:
    block: TargetBlock | None
    slots: list[SlotContext]

    @property
    def entry(self) -> str | None:
        """Clock time of the slot flagged as the entry, read off the slots."""
        flagged = [s for s in self.slots if s.is_demand_window_entry]
        assert len(flagged) <= 1
        return _clock(flagged[0].timestamp_iso) if flagged else None

    @property
    def last(self) -> str | None:
        flagged = [s for s in self.slots if s.is_demand_window_slot]
        return _clock(flagged[-1].timestamp_iso) if flagged else None


def _plan(
    data: Any,
    clock: str,
    price_at: PriceAt,
    *,
    day: int = 11,
    config: OptimizerConfig = _ON,
    persist: bool = True,
    hours: int = 15,
) -> _Plan:
    slots = _horizon(_at(clock, day), price_at, hours)
    block = apply_price_block_flags(slots, config, data, persist=persist)
    return _Plan(block, slots)


# 12c all day and a 17c evening: 5c of spread, which is not a block. What makes
# it one is the 8c the battery could have bought at first thing.
_FLAT_12 = _day(0.12, 0.17)
_CHEAP_AT_0900 = _day(0.12, 0.17, now_price={"09:00": 0.08})

# 7c all day and a 19c evening: a block on any plan, whatever is remembered.
_BASE = _day(0.07, 0.19)
_EARLY = _day(0.07, 0.19, dear_from="15:00")
_NO_EVENING = _day(0.07, 0.07)


# ---------------------------------------------------------------------------
# The detector takes the remembered trough as an argument
# ---------------------------------------------------------------------------


def test_detector_alone_loses_the_block_once_the_cheap_slot_has_passed() -> None:
    """The #1111 failure, reproduced on the pure function."""
    at_nine = _horizon(_at("09:00"), _CHEAP_AT_0900)
    at_half_past = _horizon(_at("09:30"), _CHEAP_AT_0900)

    assert find_target_block(at_nine, _ON) is not None
    assert find_target_block(at_half_past, _ON) is None


def test_remembered_trough_keeps_the_block() -> None:
    at_half_past = _horizon(_at("09:30"), _CHEAP_AT_0900)

    block = find_target_block(at_half_past, _ON, trough_price=0.08)

    assert block is not None
    assert _clock(at_half_past[block.entry_idx].timestamp_iso) == "17:00"
    assert "remembered trough $0.0800" in block.reason


def test_reference_is_the_cheaper_of_trough_and_horizon() -> None:
    """A trough dearer than the horizon's own cheap slots changes nothing."""
    slots = _horizon(_at("09:00"), _BASE)

    plain = find_target_block(slots, _ON)
    with_dear_trough = find_target_block(slots, _ON, trough_price=0.15)

    assert with_dear_trough == plain
    assert "remembered trough" not in plain.reason


def test_trough_one_spread_below_the_evening_is_enough() -> None:
    slots = _horizon(_at("09:30"), _FLAT_12)

    assert find_target_block(slots, _ON, trough_price=0.09) is not None
    assert find_target_block(slots, _ON, trough_price=0.0901) is None


def test_trough_never_makes_the_current_slot_dear() -> None:
    """Slot 0 is now. A block is something to prepare for, not to be inside."""
    slots = _horizon(_at("17:00"), _FLAT_12)

    block = find_target_block(slots, _ON, trough_price=0.08)

    assert block is not None
    assert block.entry_idx == 1


@pytest.mark.parametrize("trough", [None, float("nan"), float("inf"), "0.08"])
def test_unusable_trough_is_ignored(trough: Any) -> None:
    slots = _horizon(_at("09:30"), _CHEAP_AT_0900)

    assert find_target_block(slots, _ON, trough_price=trough) is None


# ---------------------------------------------------------------------------
# remember_trough: a rolling minimum over the last 24 hours
# ---------------------------------------------------------------------------


def test_first_observation_is_the_trough() -> None:
    assert remember_trough([], _iso("09:00"), 0.11) == [(_iso("09:00"), 0.11)]


def test_cheaper_observation_replaces_everything_before_it() -> None:
    history = remember_trough([], _iso("09:00"), 0.11)

    history = remember_trough(history, _iso("09:30"), 0.08)

    assert history == [(_iso("09:30"), 0.08)]


def test_dearer_observation_leaves_the_trough_and_queues_behind_it() -> None:
    history = remember_trough([], _iso("09:00"), 0.08)

    history = remember_trough(history, _iso("09:30"), 0.12)

    assert history[0] == (_iso("09:00"), 0.08)
    assert history[-1] == (_iso("09:30"), 0.12)


def test_same_price_seen_again_refreshes_when_it_was_seen() -> None:
    history = remember_trough([], _iso("09:00"), 0.08)

    history = remember_trough(history, _iso("09:30"), 0.08)

    assert history == [(_iso("09:30"), 0.08)]


def test_trough_survives_midnight() -> None:
    """Nothing here is keyed on the calendar day."""
    history = remember_trough([], _iso("23:30", day=11), 0.08)

    history = remember_trough(history, _iso("00:30", day=12), 0.14)

    assert history[0] == (_iso("23:30", day=11), 0.08)


def test_trough_older_than_the_window_is_forgotten() -> None:
    history = remember_trough([], _iso("09:00", day=11), 0.08)
    history = remember_trough(history, _iso("20:00", day=11), 0.10)

    still = remember_trough(history, _iso("09:00", day=12), 0.14)
    gone = remember_trough(history, _iso("09:30", day=12), 0.14)

    assert TROUGH_MEMORY_HOURS == 24
    assert still[0] == (_iso("09:00", day=11), 0.08)
    # The next-cheapest price still inside the window takes over, not "now".
    assert gone[0] == (_iso("20:00", day=11), 0.10)


def test_remember_trough_does_not_modify_its_argument() -> None:
    history = [(_iso("09:00"), 0.08)]

    remember_trough(history, _iso("09:30"), 0.05)

    assert history == [(_iso("09:00"), 0.08)]


@pytest.mark.parametrize("price", [None, float("nan"), float("inf")])
def test_no_usable_price_only_forgets(price: float | None) -> None:
    history = [(_iso("09:00", day=10), 0.08), (_iso("09:00", day=11), 0.10)]

    assert remember_trough(history, _iso("09:30", day=11), price) == [
        (_iso("09:00", day=11), 0.10)
    ]


def test_unparseable_time_leaves_the_memory_as_it_was() -> None:
    history = [(_iso("09:00"), 0.08)]

    assert remember_trough(history, "slot-0", 0.05) == history


# ---------------------------------------------------------------------------
# hold_target_block: the adopted boundaries, sized on today's forecast
# ---------------------------------------------------------------------------


def test_held_block_is_sized_on_the_current_slots() -> None:
    slots = _horizon(_at("09:00"), _NO_EVENING)
    config = OptimizerConfig(price_block_target=True)

    block = hold_target_block(slots, config, 16, 23, "waiting")

    assert block is not None
    assert (block.entry_idx, block.end_idx) == (16, 23)
    # Eight slots of 1 kWh net load.
    assert block.needed_kwh == pytest.approx(8.0 / config.discharge_efficiency)
    assert "held block" in block.reason
    assert "waiting" in block.reason


def test_held_block_end_is_clamped_into_the_horizon() -> None:
    slots = _horizon(_at("09:00"), _NO_EVENING, hours=5)

    assert hold_target_block(slots, _ON, 6, 99, "x").end_idx == 9
    assert hold_target_block(slots, _ON, 6, 2, "x").end_idx == 6


@pytest.mark.parametrize("entry_idx", [-1, 0, 30, 99])
def test_held_entry_must_be_a_slot_ahead_of_now(entry_idx: int) -> None:
    slots = _horizon(_at("09:00"), _NO_EVENING)

    assert hold_target_block(slots, _ON, entry_idx, 20, "x") is None


def test_held_block_needs_a_sizeable_battery() -> None:
    slots = _horizon(_at("09:00"), _NO_EVENING)
    config = OptimizerConfig(price_block_target=True, battery_capacity_kwh=0.0)

    assert hold_target_block(slots, config, 16, 23, "x") is None


# ---------------------------------------------------------------------------
# Trough memory across plans
# ---------------------------------------------------------------------------


def test_first_plan_adopts_at_once_and_notes_the_current_price() -> None:
    """Nothing to hold on the very first plan: the detection is the block."""
    data = CoordinatorData()

    plan = _plan(data, "09:00", _CHEAP_AT_0900)

    assert plan.entry == "17:00"
    assert plan.last == "20:30"
    assert data.target_block_settled is True
    assert data.target_block_entry_iso == _iso("17:00")
    assert data.target_block_end_iso == _iso("21:00")
    assert data.target_block_trough == [(_iso("09:00"), 0.08)]
    assert data.target_block_pending_since_iso is None


def test_block_survives_the_cheap_slot_becoming_the_past() -> None:
    """Cause 1 of the flap-test failure."""
    data = CoordinatorData()
    _plan(data, "09:00", _CHEAP_AT_0900)

    for clock in ("09:30", "11:00", "13:00", "14:30", "16:30"):
        plan = _plan(data, clock, _CHEAP_AT_0900)
        assert plan.entry == "17:00", clock
        assert "remembered trough" in plan.block.reason

    assert data.target_block_trough[0] == (_iso("09:00"), 0.08)
    assert data.target_block_pending_since_iso is None


def test_restart_mid_day_starts_cold() -> None:
    """The memory is not persisted across a restart, and nothing is invented.

    The safe behaviour: with nothing remembered the detector sees only the
    horizon, exactly as it did before #1114. The block the morning funded is
    gone, and no deadline is asserted against a price nobody recorded.
    """
    before = CoordinatorData()
    _plan(before, "09:00", _CHEAP_AT_0900)

    restarted = CoordinatorData()
    plan = _plan(restarted, "09:30", _CHEAP_AT_0900)

    assert plan.block is None
    assert plan.entry is None
    assert restarted.target_block_settled is True
    assert restarted.target_block_trough == [(_iso("09:30"), 0.12)]


def test_trough_carries_across_midnight() -> None:
    """Day rollover: a cheap price late last night still funds this evening."""
    data = CoordinatorData()
    late_cheap = _day(0.12, 0.17, now_price={"23:00": 0.08})
    assert _plan(data, "23:00", late_cheap, day=11, hours=24).entry == "17:00"

    plan = _plan(data, "08:00", _FLAT_12, day=12)

    assert plan.entry == "17:00"
    assert data.target_block_trough[0] == (_iso("23:00", day=11), 0.08)


def test_day_with_no_block_forgets_the_trough_after_24_hours() -> None:
    data = CoordinatorData()
    yesterday = _day(0.12, 0.12, now_price={"09:00": 0.08, "20:00": 0.10})
    today = _day(0.12, 0.12)
    assert _plan(data, "09:00", yesterday, day=11).block is None
    assert _plan(data, "20:00", yesterday, day=11).block is None

    assert _plan(data, "09:00", today, day=12).block is None
    assert data.target_block_trough[0] == (_iso("09:00", day=11), 0.08)

    assert _plan(data, "09:30", today, day=12).block is None
    # The cheapest price still inside the window takes over.
    assert data.target_block_trough[0] == (_iso("20:00", day=11), 0.10)


def test_block_arriving_spends_the_trough() -> None:
    """Reset when a block has passed.

    The moment the adopted entry is the current slot the trough has done its
    job. It is dropped there, not at the block's end: kept any longer it would
    make every slot from the next one on dear against a price the battery can
    no longer buy at.
    """
    data = CoordinatorData()
    _plan(data, "09:00", _CHEAP_AT_0900)
    assert _plan(data, "16:30", _CHEAP_AT_0900).entry == "17:00"

    plan = _plan(data, "17:00", _CHEAP_AT_0900)

    assert plan.block is None
    assert plan.entry is None
    assert data.target_block_trough == []
    assert data.target_block_trough_resume_iso == _iso("21:00")
    assert data.target_block_entry_iso is None


def test_prices_inside_the_block_are_not_remembered() -> None:
    """The trough is the cheapest price since the previous block *ended*."""
    data = CoordinatorData()
    dip_inside = _day(0.12, 0.17, now_price={"09:00": 0.08, "19:00": 0.05})
    _plan(data, "09:00", dip_inside)
    _plan(data, "17:00", dip_inside)

    _plan(data, "19:00", dip_inside)
    assert data.target_block_trough == []

    _plan(data, "21:00", dip_inside)
    assert data.target_block_trough == [(_iso("21:00"), 0.12)]
    assert data.target_block_trough_resume_iso is None


def test_block_coming_into_view_after_one_has_passed_dwells_like_any_other() -> None:
    """Tomorrow's block appears when the horizon reaches it, a day ahead.

    The plan on which the old block arrived settled on "no block", so this is
    an appearance and waits out the dwell. An hour, twenty hours ahead of the
    deadline, costs nothing.
    """
    data = CoordinatorData()
    _plan(data, "09:00", _BASE, day=11)
    # 15 hours from 17:00 does not reach tomorrow evening; 26 hours does.
    assert _plan(data, "17:00", _BASE, day=11).block is None
    assert data.target_block_settled is True

    waiting = _plan(data, "17:30", _BASE, day=11, hours=26)
    assert waiting.block is None
    assert data.target_block_pending_entry_iso == _iso("17:00", day=12)

    adopted = _plan(data, "18:30", _BASE, day=11, hours=26)
    assert adopted.block is not None
    assert data.target_block_entry_iso == _iso("17:00", day=12)


# ---------------------------------------------------------------------------
# Entry dwell
# ---------------------------------------------------------------------------


def test_dwell_is_sixty_minutes() -> None:
    assert ENTRY_DWELL_MINUTES == 60


def test_moved_entry_is_not_adopted_until_it_has_persisted() -> None:
    """Cause 2 of the flap-test failure: 17:00 -> 15:00 on a forecast revision."""
    data = CoordinatorData()
    assert _plan(data, "09:00", _BASE).entry == "17:00"

    first = _plan(data, "09:30", _EARLY)
    assert first.entry == "17:00"
    assert first.last == "20:30"
    assert "held block" in first.block.reason
    assert data.target_block_entry_iso == _iso("17:00")
    assert data.target_block_pending_entry_iso == _iso("15:00")
    assert data.target_block_pending_since_iso == _iso("09:30")

    assert _plan(data, "10:00", _EARLY).entry == "17:00"
    assert data.target_block_pending_since_iso == _iso("09:30")

    adopted = _plan(data, "10:30", _EARLY)
    assert adopted.entry == "15:00"
    assert data.target_block_entry_iso == _iso("15:00")
    assert data.target_block_pending_entry_iso is None
    assert data.target_block_pending_since_iso is None


def test_revision_that_reverts_inside_the_dwell_never_moves_the_entry() -> None:
    data = CoordinatorData()
    _plan(data, "09:00", _BASE)
    _plan(data, "09:30", _EARLY)

    back = _plan(data, "10:00", _BASE)

    assert back.entry == "17:00"
    assert "held block" not in back.block.reason
    assert data.target_block_pending_entry_iso is None
    assert data.target_block_pending_since_iso is None
    # And the clock starts again if the revision comes back.
    _plan(data, "10:30", _EARLY)
    assert data.target_block_pending_since_iso == _iso("10:30")


def test_disappearing_block_stands_until_the_dwell_is_up() -> None:
    data = CoordinatorData()
    _plan(data, "09:00", _BASE)

    held = _plan(data, "09:30", _NO_EVENING)
    assert held.entry == "17:00"
    assert held.last == "20:30"
    # Sized on the forecast as it is now: eight slots of 1 kWh.
    assert held.block.needed_kwh == pytest.approx(8.0 / _ON.discharge_efficiency)
    assert data.target_block_pending_entry_iso is None
    assert data.target_block_pending_since_iso == _iso("09:30")

    assert _plan(data, "10:00", _NO_EVENING).entry == "17:00"

    gone = _plan(data, "10:30", _NO_EVENING)
    assert gone.block is None
    assert gone.entry is None
    assert data.target_block_entry_iso is None
    assert data.target_block_entry_idx is None
    assert data.target_block_pending_since_iso is None


def test_appearing_block_waits_out_the_dwell() -> None:
    data = CoordinatorData()
    assert _plan(data, "09:00", _NO_EVENING).block is None
    assert data.target_block_settled is True

    waiting = _plan(data, "09:30", _BASE)
    assert waiting.block is None
    assert waiting.entry is None
    assert data.target_block_pending_entry_iso == _iso("17:00")

    assert _plan(data, "10:00", _BASE).block is None
    assert _plan(data, "10:30", _BASE).entry == "17:00"


def test_a_different_candidate_restarts_the_dwell() -> None:
    data = CoordinatorData()
    earlier_still = _day(0.07, 0.19, dear_from="13:00")
    _plan(data, "09:00", _BASE)
    _plan(data, "09:30", _EARLY)

    _plan(data, "10:00", earlier_still)
    assert data.target_block_pending_entry_iso == _iso("13:00")
    assert data.target_block_pending_since_iso == _iso("10:00")

    assert _plan(data, "10:30", earlier_still).entry == "17:00"
    assert _plan(data, "11:00", earlier_still).entry == "13:00"


def test_candidate_wobbling_by_one_slot_is_the_same_candidate() -> None:
    data = CoordinatorData()
    half_later = _day(0.07, 0.19, dear_from="15:30")
    _plan(data, "09:00", _BASE)
    _plan(data, "09:30", _EARLY)

    _plan(data, "10:00", half_later)
    assert data.target_block_pending_entry_iso == _iso("15:00")
    assert data.target_block_pending_since_iso == _iso("09:30")

    # Adopted at the entry the candidate was first seen at.
    assert _plan(data, "10:30", half_later).entry == "15:00"


def test_one_slot_move_is_absorbed_by_the_hysteresis_not_the_dwell() -> None:
    data = CoordinatorData()
    half_later = _day(0.07, 0.19, dear_from="17:30")
    _plan(data, "09:00", _BASE)

    plan = _plan(data, "09:30", half_later)

    assert plan.entry == "17:00"
    assert "entry held from previous plan" in plan.block.reason
    assert data.target_block_pending_since_iso is None


def test_held_entry_arriving_ends_the_hold() -> None:
    """The held block's entry time has passed: it was honoured, and it is over.

    The entry was adopted at 15:00. A late revision to 17:00 is still waiting
    out its dwell when 15:00 arrives. The battery was prepared for 15:00; the
    hold is released, the trough is spent, and the fresh detection is adopted at
    once, as on a first plan.
    """
    data = CoordinatorData()
    _plan(data, "09:00", _EARLY)
    waiting = _plan(data, "14:30", _BASE)
    assert waiting.entry == "15:00"
    assert data.target_block_pending_entry_iso == _iso("17:00")

    plan = _plan(data, "15:00", _BASE)

    assert plan.entry == "17:00"
    assert "held block" not in plan.block.reason
    assert data.target_block_entry_iso == _iso("17:00")
    assert data.target_block_pending_since_iso is None
    assert data.target_block_trough == []
    assert data.target_block_trough_resume_iso == _iso("21:00")


def test_zero_dwell_adopts_every_change_at_once() -> None:
    data = CoordinatorData()
    _plan(data, "09:00", _BASE)

    with patch.object(target_block, "ENTRY_DWELL_MINUTES", 0):
        plan = _plan(data, "09:30", _EARLY)

    assert plan.entry == "15:00"
    assert data.target_block_pending_since_iso is None


def test_repeating_a_plan_at_the_same_instant_changes_nothing() -> None:
    """The harness and the tests plan twice per capture; time has not moved."""
    data = CoordinatorData()
    _plan(data, "09:00", _BASE)
    _plan(data, "09:30", _EARLY)
    state = {name: getattr(data, name) for name in PRICE_BLOCK_STATE_FIELDS}

    again = _plan(data, "09:30", _EARLY)

    assert again.entry == "17:00"
    assert {name: getattr(data, name) for name in PRICE_BLOCK_STATE_FIELDS} == state


# ---------------------------------------------------------------------------
# What must not touch the state
# ---------------------------------------------------------------------------


def test_shadow_plan_reads_nothing_new_and_writes_nothing() -> None:
    """persist=False is the shadow comparison: a different price series."""
    data = CoordinatorData()
    _plan(data, "09:00", _CHEAP_AT_0900)
    state = {name: getattr(data, name) for name in PRICE_BLOCK_STATE_FIELDS}

    shadow = _plan(data, "09:30", _EARLY, persist=False)

    # Its own detection on its own prices, with no trough and no dwell.
    assert shadow.entry == "15:00"
    assert {name: getattr(data, name) for name in PRICE_BLOCK_STATE_FIELDS} == state


def test_switch_off_forgets_everything() -> None:
    data = CoordinatorData()
    _plan(data, "09:00", _BASE)
    _plan(data, "09:30", _EARLY)

    _plan(data, "10:00", _EARLY, config=OptimizerConfig())

    assert data.target_block_entry_idx is None
    assert data.target_block_entry_iso is None
    assert data.target_block_end_iso is None
    assert data.target_block_settled is False
    assert data.target_block_pending_entry_iso is None
    assert data.target_block_pending_since_iso is None
    assert data.target_block_trough == []
    assert data.target_block_trough_resume_iso is None


def test_fresh_coordinator_data_starts_with_nothing_remembered() -> None:
    data = CoordinatorData()

    assert data.target_block_settled is False
    assert data.target_block_trough == []
    assert CoordinatorData().target_block_trough is not data.target_block_trough
    for name in PRICE_BLOCK_STATE_FIELDS:
        assert hasattr(data, name), name


def test_data_object_without_the_new_fields_still_plans() -> None:
    """The batch runner's callers hand in whatever data object they have."""

    @dataclass
    class _Bare:
        target_block_entry_idx: int | None = None
        target_block_entry_iso: str | None = None

    data = _Bare()

    assert _plan(data, "09:00", _BASE).entry == "17:00"
    assert _plan(data, "09:30", _BASE).entry == "17:00"
    assert data.target_block_entry_iso == _iso("17:00")


def test_unparseable_slot_times_fall_back_to_the_plain_detection() -> None:
    """No clock, so nothing can dwell or age; the plan still gets its block."""
    slots = _horizon(_at("09:00"), _BASE)
    for slot in slots:
        slot.timestamp_iso = f"slot-{slot.slot_index}"
    data = CoordinatorData()

    block = apply_price_block_flags(slots, _ON, data)
    again = apply_price_block_flags(slots, _ON, data)

    assert block is not None
    assert again is not None
    assert block.entry_idx == again.entry_idx == 16
    assert data.target_block_trough == []


@pytest.mark.parametrize("junk", ["not a time", "2026-09-11T17:00:00"])
def test_unusable_stored_entry_is_dropped_not_trusted(junk: str) -> None:
    """Unparseable, or timezone-naive against aware slots: retired, like an
    entry that has passed, and the fresh detection adopted at once."""
    data = CoordinatorData()
    data.target_block_settled = True
    data.target_block_entry_iso = junk
    data.target_block_end_iso = junk
    data.target_block_trough_resume_iso = junk

    plan = _plan(data, "09:00", _BASE)

    assert plan.entry == "17:00"
    assert data.target_block_entry_iso == _iso("17:00")
    assert data.target_block_trough == [(_iso("09:00"), 0.07)]
    assert data.target_block_trough_resume_iso is None


def test_stored_entry_that_is_not_a_string_reads_as_no_block() -> None:
    data = CoordinatorData()
    data.target_block_settled = True
    data.target_block_entry_iso = 17

    plan = _plan(data, "09:00", _BASE)

    # Settled on "no block", so the detection is an appearance and dwells.
    assert plan.block is None
    assert data.target_block_pending_entry_iso == _iso("17:00")


def test_pending_entry_that_arrives_unadopted_is_not_adopted_late() -> None:
    """A candidate whose own entry time has come is no longer a candidate."""
    data = CoordinatorData()
    _plan(data, "09:00", _BASE)
    _plan(data, "14:30", _EARLY)
    assert data.target_block_pending_entry_iso == _iso("15:00")

    plan = _plan(data, "15:00", _EARLY)

    assert plan.entry == "17:00"
    assert data.target_block_pending_entry_iso == _iso("15:30")
    assert data.target_block_pending_since_iso == _iso("15:00")


@pytest.mark.parametrize("junk", ["not a time", "2026-09-11T09:00:00"])
def test_unusable_pending_clock_never_counts_as_elapsed(junk: str) -> None:
    """A pending change whose start cannot be read has not persisted at all."""
    data = CoordinatorData()
    _plan(data, "09:00", _BASE)
    _plan(data, "09:30", _EARLY)
    data.target_block_pending_since_iso = junk

    plan = _plan(data, "12:00", _EARLY)

    assert plan.entry == "17:00"
    assert data.target_block_pending_entry_iso == _iso("15:00")


@pytest.mark.parametrize("junk", ["not a time", "2026-09-11T21:00:00", None])
def test_held_block_with_an_unusable_end_is_held_as_its_entry_slot(junk: Any) -> None:
    data = CoordinatorData()
    _plan(data, "09:00", _BASE)
    data.target_block_end_iso = junk

    plan = _plan(data, "09:30", _EARLY)

    assert plan.entry == "17:00"
    assert plan.last == "17:00"


def test_unparseable_slot_inside_the_horizon_does_not_stop_a_hold() -> None:
    data = CoordinatorData()
    _plan(data, "09:00", _BASE)
    slots = _horizon(_at("09:30"), _NO_EVENING)
    slots[-1].timestamp_iso = "slot-last"

    block = apply_price_block_flags(slots, _ON, data)

    assert block is not None
    assert slots[block.entry_idx].timestamp_iso == _iso("17:00")


# ---------------------------------------------------------------------------
# Telemetry: the memory is visible on the summary sensor
# ---------------------------------------------------------------------------

_QUIET = dict.fromkeys(TARGET_BLOCK_MEMORY_KEYS)


def test_memory_telemetry_keys() -> None:
    assert TARGET_BLOCK_MEMORY_KEYS == (
        "target_block_trough_price",
        "target_block_trough_at",
        "target_block_pending_change",
        "target_block_pending_entry",
        "target_block_pending_since",
    )


def test_memory_telemetry_reports_the_trough() -> None:
    data = CoordinatorData()
    _plan(data, "09:00", _CHEAP_AT_0900)
    _plan(data, "09:30", _CHEAP_AT_0900)

    assert target_block_memory_telemetry(data, _ON) == {
        "target_block_trough_price": 0.08,
        "target_block_trough_at": _iso("09:00"),
        "target_block_pending_change": None,
        "target_block_pending_entry": None,
        "target_block_pending_since": None,
    }


@pytest.mark.parametrize(
    ("before", "after", "change", "entry"),
    [
        (_BASE, _EARLY, "move", "15:00"),
        (_BASE, _NO_EVENING, "disappear", None),
        (_NO_EVENING, _BASE, "appear", "17:00"),
    ],
    ids=["move", "disappear", "appear"],
)
def test_memory_telemetry_reports_a_pending_change(
    before: PriceAt, after: PriceAt, change: str, entry: str | None
) -> None:
    data = CoordinatorData()
    _plan(data, "09:00", before)
    _plan(data, "09:30", after)

    record = target_block_memory_telemetry(data, _ON)

    assert record["target_block_pending_change"] == change
    assert _clock(record["target_block_pending_entry"]) == entry
    assert record["target_block_pending_since"] == _iso("09:30")


def test_memory_telemetry_is_quiet_with_the_switch_off() -> None:
    data = CoordinatorData()
    _plan(data, "09:00", _BASE)

    assert target_block_memory_telemetry(data, OptimizerConfig()) == _QUIET


def test_memory_telemetry_on_a_data_object_without_the_fields() -> None:
    assert target_block_memory_telemetry(MagicMock(spec=[]), _ON) == _QUIET


def test_summary_and_sensor_carry_the_memory_through_the_engine() -> None:
    run = drive(load(CAPTURE_0907), price_block_target=True, live=True)
    summary = run.data.optimizer_summary

    assert summary["target_block_trough_price"] == pytest.approx(run.slots[0].buy_price)
    assert summary["target_block_trough_at"] == run.slots[0].timestamp_iso
    assert summary["target_block_pending_change"] is None

    coordinator = MagicMock()
    coordinator.data = run.data
    attrs = OptimizerSummarySensor(coordinator, MagicMock()).extra_state_attributes
    for key in TARGET_BLOCK_MEMORY_KEYS:
        assert attrs[key] == summary[key], key


@pytest.mark.parametrize("switch", [False, None], ids=["off", "absent"])
def test_summary_memory_is_quiet_with_the_switch_off(switch: bool | None) -> None:
    run = drive(load(CAPTURE_0907), price_block_target=switch, live=True)

    for key in TARGET_BLOCK_MEMORY_KEYS:
        assert run.data.optimizer_summary[key] is None, key
    assert run.data.target_block_trough == []
