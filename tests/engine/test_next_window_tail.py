"""Plan tail for the next demand window when it starts past the horizon (issue #1033).

Live 2026-09-07 14:47: the price feed ended at 15:00 the next day, which is the
moment the next demand window opens. The planner handles one window per plan
(the first one it finds), and that was today's 15:00 window, already satisfied
at 100%. Nothing asked for charge before tomorrow's, so the plan held every
slot and published a tail that sat on the SOC floor: 9.3% against a 95% target.

The tail is now planned a second time, from the end of the window the main
solve handled, against the next window. These tests pin:

1. The live shape, before a window (14:47) and inside one (15:07).
2. Every decision up to the end of the handled window is unchanged.
3. No grid charge overnight in the re-planned tail.
4. The cases where nothing changes: no gap supplied, a window already ending the
   horizon, ``allow_dw_entry_under_target``, a next window too far off to matter,
   and the feature switched off.
5. The tail cannot change an executed command: it never supplies slot 0, its
   first slot starts at least ``NEXT_WINDOW_TAIL_MIN_LEAD_MIN`` after slot 0
   (the executor picks its slot by wall clock), and over a varied set of inputs
   every decision inside that lead, and every decision before the tail start,
   is the same with the tail on or off.
6. Why the tail was or was not applied is always published.
7. On flat and cheap-overnight prices the tail's overnight charge matches the
   ordinary evening re-plan of the same slots, to within one SOC bin.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, time, timedelta
from unittest.mock import patch

import pytest

from custom_components.localshift.engine.core import (
    DPPlanner,
    _soc_required_before_gap,
)
from custom_components.localshift.engine.slots import SlotBuilder
from custom_components.localshift.engine.types import (
    NEXT_WINDOW_TAIL_MIN_LEAD_MIN,
    OptimizerConfig,
    OptimizerInputs,
    PlannerAction,
    SlotContext,
)

_CHARGE = (PlannerAction.CHARGE_GRID_NORMAL, PlannerAction.CHARGE_GRID_BOOST)
_DW_START = time(15, 0)
_DW_END = time(21, 0)


def _config(**overrides) -> OptimizerConfig:
    defaults = dict(
        min_soc_pct=5.0,
        max_soc_pct=100.0,
        demand_window_target_soc_pct=95.0,
        allow_dw_entry_under_target=False,
        optimization_mode="self_consumption",
        battery_capacity_kwh=13.5,
        charge_rate_kw=3.3,
        boost_charge_rate_kw=5.0,
        charge_efficiency=0.92,
        discharge_efficiency=0.95,
        effective_cheap_price=0.11,
        base_cheap_price=0.11,
        max_precharge_price=0.20,
        target_shortfall_penalty_per_pct=0.1,
        soc_bins=100,
    )
    defaults.update(overrides)
    return OptimizerConfig(**defaults)


def _buy_price(t: datetime) -> float:
    """The 2026-09-07 price shape: dear evening, cheap morning."""
    if _DW_START <= t.time() < _DW_END:
        return 0.30
    if time(7, 30) <= t.time() < time(9, 0):
        return 0.10
    if time(9, 0) <= t.time() < time(15, 0):
        return 0.12
    return 0.15


def _solar_kwh(t: datetime, daily_kwh: float) -> float:
    """Flat solar from 08:00 to 16:00, ``daily_kwh`` in total, per 30 minutes."""
    return daily_kwh / 16.0 if time(8, 0) <= t.time() < time(16, 0) else 0.0


def _slots(
    start: datetime,
    end: datetime,
    solar_daily_kwh: float = 6.0,
    load_kw: float = 0.7,
) -> list[SlotContext]:
    """30-minute slots from ``start`` up to (not including) ``end``."""
    slots: list[SlotContext] = []
    t = start
    prev_in_dw = False
    while t < end:
        in_dw = _DW_START <= t.time() < _DW_END
        slots.append(
            SlotContext(
                slot_index=len(slots),
                timestamp_iso=t.isoformat(),
                slot_interval_minutes=30,
                buy_price=_buy_price(t),
                sell_price=0.03,
                solar_kwh=_solar_kwh(t, solar_daily_kwh),
                consumption_kwh=load_kw * 0.5,
                is_demand_window_entry=in_dw and not prev_in_dw,
                is_demand_window_slot=in_dw,
            )
        )
        prev_in_dw = in_dw
        t += timedelta(minutes=30)
    return slots


def _plan(slots, gap_min, soc=100.0, **config_overrides):
    return DPPlanner().plan(
        OptimizerInputs(
            cycle_id="next-window-tail",
            initial_soc_pct=soc,
            slots=slots,
            config=_config(**config_overrides),
            next_demand_window_gap_min=gap_min,
        )
    )


# Live 2026-09-07 14:47: today's window opens in the next slot and the feed ends
# at 15:00 tomorrow. Tomorrow is a solar-deficit day (6 kWh against 16.8 kWh load).
_PRE_DW_START = datetime(2026, 9, 7, 14, 30)
_HORIZON_END = datetime(2026, 9, 8, 15, 0)
# Twenty minutes later: the plan starts inside today's window.
_MID_DW_START = datetime(2026, 9, 7, 15, 0)


def _hour(decision) -> time:
    return datetime.fromisoformat(decision.timestamp_iso).time()


def _overnight_charge_kwh(decisions) -> float:
    """Grid charge between the end of the demand window and 06:00."""
    return sum(
        d.grid_import_kwh
        for d in decisions
        if d.action in _CHARGE and (_hour(d) >= _DW_END or _hour(d) < time(6, 0))
    )


class TestLiveShape:
    """The 2026-09-07 plan, with and without the next window's gap."""

    def test_without_the_gap_the_tail_sits_on_the_floor(self):
        """What #1033 reported: no charge anywhere, and a tail a few points off
        the 5% floor (live: 9.3%). Without a gap the planner cannot know a window
        follows the horizon, so this is also what a caller that supplies none gets."""
        result = _plan(_slots(_PRE_DW_START, _HORIZON_END), gap_min=None)
        assert result.success
        assert all(d.action not in _CHARGE for d in result.decisions)
        assert result.decisions[-1].predicted_soc_pct < 20.0
        assert result.next_window_tail_start_idx is None

    def test_tail_reaches_the_next_window_target(self):
        slots = _slots(_PRE_DW_START, _HORIZON_END)
        result = _plan(slots, gap_min=0.0)
        assert result.success
        assert len(result.decisions) == len(slots)
        # One SOC bin is ~0.96 points; the tail must arrive at the 95% target.
        assert result.decisions[-1].predicted_soc_pct >= 94.0
        assert result.next_window_required_soc_pct == pytest.approx(95.0)
        assert result.next_window_entry_soc_pct >= 94.0
        # Today's window ends at 21:00, 13 slots in.
        assert result.next_window_tail_start_idx == 13

    def test_decisions_up_to_the_end_of_todays_window_are_unchanged(self):
        """The executed part of the plan must not move: slot 0 is in it."""
        slots = _slots(_PRE_DW_START, _HORIZON_END)
        before = _plan(slots, gap_min=None)
        after = _plan(slots, gap_min=0.0)
        split = after.next_window_tail_start_idx
        assert after.decisions[:split] == before.decisions[:split]
        # Today's window is still the one the summary reports on.
        assert after.terminal_shortfall_pct == before.terminal_shortfall_pct
        assert after.dw_entry_soc_pct == before.dw_entry_soc_pct

    def test_no_overnight_grid_charge(self):
        """The #800 sawtooth: the tail must fund the target from tomorrow's
        daytime slots, never by charging overnight and draining by morning."""
        result = _plan(_slots(_PRE_DW_START, _HORIZON_END), gap_min=0.0)
        assert _overnight_charge_kwh(result.decisions) == 0.0
        charges = [d for d in result.decisions if d.action in _CHARGE]
        assert charges
        assert all(time(6, 0) <= _hour(d) < _DW_START for d in charges)

    def test_plan_started_inside_the_window(self):
        """15:07: slot 0 is in today's window, so the main solve has no window
        ahead of it at all and the whole plan after 21:00 is the tail."""
        slots = _slots(_MID_DW_START, _HORIZON_END)
        before = _plan(slots, gap_min=None)
        after = _plan(slots, gap_min=0.0)
        assert before.decisions[-1].predicted_soc_pct < 20.0
        assert after.decisions[-1].predicted_soc_pct >= 94.0
        split = after.next_window_tail_start_idx
        assert split == 12
        assert after.decisions[:split] == before.decisions[:split]
        assert _overnight_charge_kwh(after.decisions) == 0.0

    def test_totals_cover_the_published_decisions(self):
        result = _plan(_slots(_PRE_DW_START, _HORIZON_END), gap_min=0.0)
        decisions = result.decisions
        assert [d.slot_index for d in decisions] == list(range(len(decisions)))
        assert result.total_slots == len(decisions)
        assert result.projected_import_kwh == pytest.approx(
            sum(d.grid_import_kwh for d in decisions)
        )
        assert result.projected_net_cost == pytest.approx(
            sum(d.objective_terms.net_cost for d in decisions)
        )
        assert sum(result.reason_code_histogram.values()) == len(decisions)

    def test_solar_sufficient_day_needs_no_grid_charge(self):
        """Tomorrow's solar fills the battery: the tail rises on solar alone."""
        slots = _slots(_PRE_DW_START, _HORIZON_END, solar_daily_kwh=40.0)
        result = _plan(slots, gap_min=0.0)
        assert all(d.action not in _CHARGE for d in result.decisions)
        assert result.decisions[-1].predicted_soc_pct >= 94.0


class TestGapBeforeTheNextWindow:
    """The feed often ends short of the window; the unseen gap can still charge."""

    def test_required_soc_falls_as_the_gap_grows(self):
        config = _config()
        required = [
            _soc_required_before_gap(config, gap)
            for gap in (0.0, 30.0, 60.0, 120.0, 240.0, 600.0)
        ]
        assert required[0] == pytest.approx(95.0)
        assert required == sorted(required, reverse=True)
        assert required[1] < 95.0
        # Ten hours at the normal rate refills the battery from the floor.
        assert required[-1] == pytest.approx(config.min_soc_pct)

    def test_required_soc_is_what_normal_rate_charging_needs(self):
        """Charging at the normal rate for the gap from the required SOC lands on
        target, taper included."""
        from custom_components.localshift.engine.transitions import (
            _tapered_stored_kwh,
        )

        config = _config()
        soc = _soc_required_before_gap(config, 60.0)
        for _ in range(60):
            stored = _tapered_stored_kwh(soc, config.charge_rate_kw, 1 / 60, config)
            soc += stored / config.battery_capacity_kwh * 100.0
        assert soc == pytest.approx(95.0, abs=0.5)

    def test_tail_holds_what_the_gap_cannot_add(self):
        """Feed ends at 14:00, an hour short of the window."""
        slots = _slots(_PRE_DW_START, _HORIZON_END - timedelta(hours=1))
        result = _plan(slots, gap_min=60.0)
        required = _soc_required_before_gap(_config(), 60.0)
        assert result.next_window_required_soc_pct == pytest.approx(required)
        assert 30.0 < required < 95.0
        assert result.decisions[-1].predicted_soc_pct >= required - 1.0

    def test_far_window_changes_nothing(self):
        """A feed ending at 04:00 is eleven hours short: the tail may rest."""
        slots = _slots(_PRE_DW_START, datetime(2026, 9, 8, 4, 0))
        before = _plan(slots, gap_min=None)
        after = _plan(slots, gap_min=660.0)
        assert after.decisions == before.decisions
        assert after.projected_net_cost == before.projected_net_cost
        assert after.next_window_tail_start_idx is None


class TestUnchangedPlans:
    """Where the next window is already handled, or the tail is not planned."""

    def test_window_at_the_end_of_the_horizon(self):
        """15:37: tomorrow's entry slot is the last slot. The main solve targets
        it, so the gap must not start a second tail."""
        slots = _slots(datetime(2026, 9, 7, 15, 30), datetime(2026, 9, 8, 15, 30))
        before = _plan(slots, gap_min=None)
        after = _plan(slots, gap_min=1410.0)
        assert before.decisions[-2].predicted_soc_pct >= 94.0
        assert after.decisions == before.decisions
        assert after.next_window_tail_start_idx is None

    def test_allow_dw_entry_under_target(self):
        """That mode keeps its target on the horizon boundary already."""
        slots = _slots(_PRE_DW_START, _HORIZON_END)
        before = _plan(slots, gap_min=None, allow_dw_entry_under_target=True)
        after = _plan(slots, gap_min=0.0, allow_dw_entry_under_target=True)
        assert after.decisions == before.decisions
        assert after.next_window_tail_start_idx is None

    def test_switched_off(self):
        slots = _slots(_PRE_DW_START, _HORIZON_END)
        before = _plan(slots, gap_min=None)
        after = _plan(slots, gap_min=0.0, next_window_tail_enabled=False)
        assert after.decisions == before.decisions
        assert after.next_window_tail_start_idx is None

    def test_tail_failure_keeps_the_main_plan(self):
        slots = _slots(_PRE_DW_START, _HORIZON_END)
        before = _plan(slots, gap_min=None)
        with patch.object(
            DPPlanner, "_solve_next_window_tail", side_effect=RuntimeError("boom")
        ):
            after = _plan(slots, gap_min=0.0)
        assert after.success
        assert after.decisions == before.decisions

    def test_callers_config_is_not_touched_by_the_tail_solve(self):
        """The runway backstop reads these fields off the config after the plan
        returns; they must still describe today's window."""
        slots = _slots(_PRE_DW_START, _HORIZON_END)
        config = _config()
        expected = replace(config)
        DPPlanner().plan(
            OptimizerInputs(
                cycle_id="main-only",
                initial_soc_pct=100.0,
                slots=slots,
                config=expected,
            )
        )
        with_tail = DPPlanner().plan(
            OptimizerInputs(
                cycle_id="with-tail",
                initial_soc_pct=100.0,
                slots=slots,
                config=config,
                next_demand_window_gap_min=0.0,
            )
        )
        # Without this the comparison below proves nothing: a tail that never
        # ran leaves the config alone too.
        assert with_tail.next_window_tail_status == "applied"
        assert with_tail.next_window_tail_start_idx == 13
        assert config == expected
        assert config.terminal_penalty_idx == 1
        assert config.demand_window_target_soc_pct == 95.0


class TestGapFromSlotBuilder:
    """``SlotBuilder`` measures the gap; the planner has no clock of its own."""

    @staticmethod
    def _gap(last_start: datetime, in_dw: bool = False, interval: int = 30):
        builder = SlotBuilder(
            {"demand_window_start": "15:00:00", "demand_window_end": "21:00:00"},
            "Australia/Sydney",
        )
        last = SlotContext(
            slot_index=0,
            timestamp_iso=last_start.isoformat(),
            slot_interval_minutes=interval,
            buy_price=0.1,
            sell_price=0.03,
            solar_kwh=0.0,
            consumption_kwh=0.0,
            is_demand_window_slot=in_dw,
        )
        return builder._next_demand_window_gap_min(
            [last], builder._get_local_timezone(), _DW_START
        )

    def test_feed_ends_as_the_window_opens(self):
        assert self._gap(datetime.fromisoformat("2026-09-08T14:30:00+10:00")) == 0.0

    def test_feed_ends_short_of_the_window(self):
        assert self._gap(datetime.fromisoformat("2026-09-08T13:30:00+10:00")) == 60.0

    def test_feed_ends_after_todays_window_started(self):
        """03:30 + 30 min = 04:00; the next 15:00 is eleven hours on."""
        assert self._gap(datetime.fromisoformat("2026-09-08T03:30:00+10:00")) == 660.0

    def test_feed_ends_inside_a_window(self):
        last = datetime.fromisoformat("2026-09-08T15:00:00+10:00")
        assert self._gap(last, in_dw=True) is None

    def test_gap_is_measured_in_local_time(self):
        """The feed is stamped UTC; 04:30Z is 14:30 in Sydney."""
        assert self._gap(datetime.fromisoformat("2026-09-08T04:30:00+00:00")) == 0.0

    def test_no_slots(self):
        builder = SlotBuilder({}, "Australia/Sydney")
        assert builder._next_demand_window_gap_min([], None, _DW_START) is None


# ---------------------------------------------------------------------------
# Review follow-up: the tail is a reporting fix and must not be able to change
# an executed command.
# ---------------------------------------------------------------------------


def _flat(price: float):
    return lambda t: price


def _cheap_overnight(t: datetime) -> float:
    """Overnight below the morning: the shape that invites a sawtooth."""
    if _DW_START <= t.time() < _DW_END:
        return 0.30
    if t.time() >= _DW_END or t.time() < time(6, 0):
        return 0.08
    return 0.12


def _evening_dip(t: datetime) -> float:
    """10c for the hour after the window, 19c for the rest of the day."""
    if _DW_START <= t.time() < _DW_END:
        return 0.30
    return 0.10 if time(21, 0) <= t.time() < time(22, 0) else 0.19


def _deep_cheap_overnight(t: datetime) -> float:
    """Overnight at 5c, 10c under the 15c day: the review's wider probe."""
    if _DW_START <= t.time() < _DW_END:
        return 0.30
    if t.time() >= _DW_END or t.time() < time(6, 0):
        return 0.05
    return 0.15


def _cheap_overnight_dear_afternoon(t: datetime) -> float:
    """Overnight at 8c, with the last two hours before the window at 19c."""
    if _DW_START <= t.time() < _DW_END:
        return 0.30
    if t.time() >= _DW_END or t.time() < time(6, 0):
        return 0.08
    return 0.19 if t.time() >= time(13, 0) else 0.12


def _one_soc_bin_of_grid_charge_kwh(config: OptimizerConfig) -> float:
    """Grid energy it takes to raise the battery one step of the solver's grid."""
    step_pct = (config.max_soc_pct - config.min_soc_pct) / (config.soc_bins - 1)
    return step_pct / 100.0 * config.battery_capacity_kwh / config.charge_efficiency


def _level_overnight(t: datetime) -> float:
    """Overnight exactly at the morning price."""
    return 0.30 if _DW_START <= t.time() < _DW_END else 0.10


def _priced_slots(
    start: datetime,
    end: datetime,
    price=_buy_price,
    solar_daily_kwh: float = 6.0,
    load_kw: float = 0.7,
    lead_5min: int = 0,
) -> list[SlotContext]:
    """Slots from ``start`` to ``end``: ``lead_5min`` five-minute slots, then
    30-minute slots, which is how the live feed arrives."""
    slots: list[SlotContext] = []
    t = start
    prev_in_dw = False
    while t < end:
        minutes = 5 if len(slots) < lead_5min or t.minute % 30 else 30
        in_dw = _DW_START <= t.time() < _DW_END
        slots.append(
            SlotContext(
                slot_index=len(slots),
                timestamp_iso=t.isoformat(),
                slot_interval_minutes=minutes,
                buy_price=price(t),
                sell_price=0.03,
                solar_kwh=_solar_kwh(t, solar_daily_kwh) * minutes / 30.0,
                consumption_kwh=load_kw * minutes / 60.0,
                is_demand_window_entry=in_dw and not prev_in_dw,
                is_demand_window_slot=in_dw,
            )
        )
        prev_in_dw = in_dw
        t += timedelta(minutes=minutes)
    return slots


def _true_gap_min(slots: list[SlotContext]) -> float | None:
    """What ``SlotBuilder`` would measure for these slots."""
    last = slots[-1]
    if last.is_demand_window_slot:
        return None
    end = datetime.fromisoformat(last.timestamp_iso) + timedelta(
        minutes=last.slot_interval_minutes
    )
    nxt = end.replace(hour=15, minute=0, second=0, microsecond=0)
    if nxt < end:
        nxt += timedelta(days=1)
    return (nxt - end).total_seconds() / 60.0


def _mode(result, config, idx: int = 0) -> str:
    """The battery mode the slot at ``idx`` (slot 0 by default) would command."""
    from custom_components.localshift.engine.optimizer_runner import (
        _derive_runtime_apply_plan,
        _serialize_decision,
    )

    serialized = [_serialize_decision(d) for d in result.decisions]
    return _derive_runtime_apply_plan(serialized, idx, config)["battery_mode"]


def _lead_min(decisions, idx: int) -> float:
    """Minutes from the start of slot 0 to the start of the slot at ``idx``."""
    first = datetime.fromisoformat(decisions[0].timestamp_iso)
    return (
        datetime.fromisoformat(decisions[idx].timestamp_iso) - first
    ).total_seconds() / 60.0


class TestTailNeverSuppliesSlotZero:
    """A feed that ends short of the next window with none in view used to make
    the tail the whole plan, slot 0 included. The tail credits no solar in the
    unseen gap, so that could command a grid charge the main solve would not."""

    # 22:00, outside a window, feed truncated at 14:00: no entry slot in view.
    _START = datetime(2026, 9, 7, 22, 0)
    _END = datetime(2026, 9, 8, 14, 0)

    def test_truncated_feed_with_no_window_in_view_is_the_main_plan(self):
        slots = _slots(self._START, self._END)
        off = _plan(slots, gap_min=60.0, soc=30.0, next_window_tail_enabled=False)
        on = _plan(slots, gap_min=60.0, soc=30.0)
        assert on.decisions == off.decisions
        assert on.projected_net_cost == off.projected_net_cost
        assert on.next_window_tail_start_idx is None
        assert on.next_window_tail_status == "whole_plan_skipped"

    def test_slot_zero_command_is_the_main_solves(self):
        """The case that changed a command. 21:00 at 10% SOC with a dip to 10c
        for the first hour and 19c after: the main solve charges slot 0 at the
        normal rate. As the whole plan, the tail boosted it, because it had a
        target to reach by a horizon that ends short of the window."""
        start = datetime(2026, 9, 7, 21, 0)
        end = datetime(2026, 9, 8, 13, 0)
        slots = _priced_slots(start, end, _evening_dip, 0.0)
        config = _config()
        off = _plan(
            _priced_slots(start, end, _evening_dip, 0.0),
            gap_min=120.0,
            soc=10.0,
            next_window_tail_enabled=False,
        )
        on = _plan(slots, gap_min=120.0, soc=10.0)
        assert off.decisions[0].action == PlannerAction.CHARGE_GRID_NORMAL
        assert on.decisions[0].action == PlannerAction.CHARGE_GRID_NORMAL
        assert _mode(on, config) == _mode(off, config) == "grid_charging"
        assert on.decisions == off.decisions

    def test_five_minute_slot_zero(self):
        slots = _priced_slots(
            datetime(2026, 9, 7, 22, 5), self._END, lead_5min=6, solar_daily_kwh=2.0
        )
        off = _plan(slots, gap_min=60.0, soc=10.0, next_window_tail_enabled=False)
        on = _plan(slots, gap_min=60.0, soc=10.0)
        assert on.decisions == off.decisions
        assert on.next_window_tail_status == "whole_plan_skipped"


class TestTailKeepsItsDistanceFromSlotZero:
    """The executor picks the slot to act on with a fresh wall-clock read when it
    applies the plan, not the time the slots were built from. A plan built in
    the last five-minute slot of the demand window (20:55) on a truncated feed
    had its tail start at index 1 (21:00): had the clock crossed 21:00 inside
    the cycle, the executed command would have been a tail decision. So the
    tail is refused unless its first slot starts at least
    ``NEXT_WINDOW_TAIL_MIN_LEAD_MIN`` after slot 0 starts."""

    _START = datetime(2026, 9, 7, 20, 55)

    @pytest.mark.parametrize("feed_h", [14.0, 16.0, 17.0, 18.0])
    @pytest.mark.parametrize("soc", [10.0, 55.0])
    @pytest.mark.parametrize("solar", [0.0, 6.0])
    def test_last_window_slot_on_a_truncated_feed_is_the_main_plan(
        self, feed_h, soc, solar
    ):
        """Review probe: evening-dip prices, one five-minute lead slot. With the
        tail applied from index 1, the index-1 decision differed from the
        tail-off plan (``hold`` or ``charge_grid_normal`` became
        ``charge_grid_boost``)."""
        end = self._START + timedelta(hours=feed_h)

        def slots():
            return _priced_slots(self._START, end, _evening_dip, solar, lead_5min=1)

        gap = _true_gap_min(slots())
        assert gap is not None
        assert slots()[0].is_demand_window_slot
        assert not slots()[1].is_demand_window_slot
        config_off = _config(next_window_tail_enabled=False)
        config_on = _config()
        off, on = (
            DPPlanner().plan(
                OptimizerInputs(
                    cycle_id="tail-distance",
                    initial_soc_pct=soc,
                    slots=slots(),
                    config=config,
                    next_demand_window_gap_min=gap,
                )
            )
            for config in (config_off, config_on)
        )
        assert off.success and on.success
        assert on.next_window_tail_status == "tail_too_close"
        assert on.next_window_tail_start_idx is None
        assert on.next_window_required_soc_pct is None
        # Decision for decision the tail-disabled plan.
        assert len(on.decisions) == len(off.decisions)
        for mine, theirs in zip(on.decisions, off.decisions, strict=True):
            assert mine == theirs
        assert on.projected_net_cost == off.projected_net_cost
        # The slot the executor would read once the clock crosses 21:00.
        assert _mode(on, config_on, 1) == _mode(off, config_off, 1)

    def test_tail_exactly_the_minimum_lead_away_is_applied(self):
        """20:30 on 30-minute slots: the tail starts at 21:00, the minimum lead
        to the minute. That is allowed; the bound is inclusive."""
        slots = _slots(datetime(2026, 9, 7, 20, 30), _HORIZON_END)
        on = _plan(slots, gap_min=0.0, soc=30.0)
        assert on.next_window_tail_status == "applied"
        split = on.next_window_tail_start_idx
        assert split == 1
        assert _lead_min(on.decisions, split) == NEXT_WINDOW_TAIL_MIN_LEAD_MIN

    def test_the_lead_is_measured_in_time_not_slots(self):
        """20:35 on five-minute slots: the tail starts five slots on, at index 5,
        but only 25 minutes after slot 0. Refused."""
        start = datetime(2026, 9, 7, 20, 35)

        def slots():
            return _priced_slots(start, _HORIZON_END, lead_5min=5)

        assert [s.slot_interval_minutes for s in slots()[:6]] == [5, 5, 5, 5, 5, 30]
        off = _plan(slots(), gap_min=0.0, soc=30.0, next_window_tail_enabled=False)
        on = _plan(slots(), gap_min=0.0, soc=30.0)
        assert on.next_window_tail_status == "tail_too_close"
        assert on.next_window_tail_start_idx is None
        assert on.decisions == off.decisions


_PROPERTY_STARTS = [
    # (start, five-minute lead slots)
    (datetime(2026, 9, 7, 0, 0), 0),
    (datetime(2026, 9, 7, 7, 0), 0),
    (datetime(2026, 9, 7, 12, 30), 0),
    (datetime(2026, 9, 7, 14, 5), 5),
    (datetime(2026, 9, 7, 14, 30), 0),
    (datetime(2026, 9, 7, 14, 45), 3),
    (datetime(2026, 9, 7, 15, 0), 0),
    (datetime(2026, 9, 7, 15, 10), 4),
    (datetime(2026, 9, 7, 18, 30), 0),
    (datetime(2026, 9, 7, 20, 55), 1),
    (datetime(2026, 9, 7, 21, 0), 0),
    (datetime(2026, 9, 7, 22, 5), 5),
]
# Feed length in hours: the usual 24, one that stops half an hour short of it
# (the October captures), a truncated one, and one long enough to hold a second
# window.
_PROPERTY_FEEDS = (24.0, 23.5, 16.0, 31.0)
_PROPERTY_MIX = [
    # (initial SOC, price name, price shape, tomorrow's solar kWh)
    (10.0, "peaky", _buy_price, 2.0),
    (55.0, "flat", _flat(0.12), 40.0),
    (100.0, "cheapnight", _cheap_overnight, 2.0),
    (10.0, "flat", _flat(0.12), 2.0),
    (100.0, "peaky", _buy_price, 40.0),
    (55.0, "cheapnight", _cheap_overnight, 6.0),
    (10.0, "dip", _evening_dip, 0.0),
    (55.0, "dip", _evening_dip, 2.0),
    (100.0, "flat", _flat(0.12), 0.0),
]
# One mix per (start, feed) pair, rotating with the indices instead of being
# multiplied out: every start time meets every feed length, every mix turns up
# five or six times, and the whole set is 48 pairs of solves.
_PROPERTY_CASES = [
    pytest.param(
        start,
        lead,
        feed_h,
        soc,
        price,
        solar,
        id=f"{start:%H%M}-{feed_h:g}h-soc{soc:g}-{name}-solar{solar:g}",
    )
    for i, (start, lead) in enumerate(_PROPERTY_STARTS)
    for j, feed_h in enumerate(_PROPERTY_FEEDS)
    for k, (soc, name, price, solar) in enumerate(_PROPERTY_MIX)
    if (i + j + k) % len(_PROPERTY_MIX) == 0
]
_PROPERTY_STATUSES: dict[str | None, int] = {}


class TestTailCannotChangeAnExecutedCommand:
    """The headline claim, over start times across the day in 5 and 30 minute
    slots, low/mid/high SOC, flat and peaky prices, low and high solar, a gap
    present and absent, and a truncated feed: with the tail on or off, slot 0 is
    the same action and the same commanded mode, and every decision before the
    tail start is the same decision.

    The executor picks its slot by wall clock, so "slot 0" is not enough: every
    slot that starts inside ``NEXT_WINDOW_TAIL_MIN_LEAD_MIN`` of slot 0 must be
    the same decision and command the same mode, and an applied tail must start
    no sooner than that."""

    @pytest.mark.parametrize(
        ("start", "lead", "feed_h", "soc", "price", "solar"), _PROPERTY_CASES
    )
    def test_slot_zero_and_head_are_identical(
        self, start, lead, feed_h, soc, price, solar
    ):
        end = start + timedelta(hours=feed_h)
        gap = _true_gap_min(_priced_slots(start, end, price, solar, lead_5min=lead))
        config_off = _config(soc_bins=50, next_window_tail_enabled=False)
        config_on = _config(soc_bins=50)
        off, on = (
            DPPlanner().plan(
                OptimizerInputs(
                    cycle_id="tail-property",
                    initial_soc_pct=soc,
                    # Fresh slots per arm: plan() writes the export floor on them.
                    slots=_priced_slots(start, end, price, solar, lead_5min=lead),
                    config=config,
                    next_demand_window_gap_min=gap,
                )
            )
            for config in (config_off, config_on)
        )
        assert off.success and on.success
        assert off.next_window_tail_start_idx is None

        # (a) slot 0: same action, same commanded mode.
        assert on.decisions[0].action == off.decisions[0].action
        assert _mode(on, config_on) == _mode(off, config_off)

        # (b) every slot the clock could reach inside one compute cycle: those
        # starting within the minimum lead of slot 0. Same decision, same mode.
        assert len(on.decisions) == len(off.decisions)
        within_lead = [
            idx
            for idx in range(len(on.decisions))
            if _lead_min(on.decisions, idx) < NEXT_WINDOW_TAIL_MIN_LEAD_MIN
        ]
        assert within_lead and within_lead[0] == 0
        for idx in within_lead:
            assert on.decisions[idx] == off.decisions[idx], idx
            assert _mode(on, config_on, idx) == _mode(off, config_off, idx), idx

        # (c) an applied tail starts no sooner than the minimum lead, and every
        # decision before it is the main solve's.
        tail_start = on.next_window_tail_start_idx
        head = len(on.decisions) if tail_start is None else tail_start
        if tail_start is not None:
            assert tail_start >= 1
            assert tail_start > within_lead[-1]
            assert _lead_min(on.decisions, tail_start) >= NEXT_WINDOW_TAIL_MIN_LEAD_MIN
        assert on.decisions[:head] == off.decisions[:head]

        # What the backstops read off the config after the plan returns, and the
        # window the summary reports on.
        assert replace(config_on, next_window_tail_enabled=False) == config_off
        assert on.terminal_shortfall_pct == off.terminal_shortfall_pct
        assert on.dw_entry_soc_pct == off.dw_entry_soc_pct

        status = getattr(on, "next_window_tail_status", None)
        _PROPERTY_STATUSES[status] = _PROPERTY_STATUSES.get(status, 0) + 1
        if status != "applied":
            assert tail_start is None
            assert on.decisions == off.decisions
            assert on.projected_net_cost == off.projected_net_cost

    def test_the_cases_above_exercise_every_branch(self):
        """Guards the property test itself: it proves nothing if the tail never
        ran, or if the skipped-whole-plan or too-close case never came up."""
        if sum(_PROPERTY_STATUSES.values()) != len(_PROPERTY_CASES):
            pytest.skip("needs the full parametrised set in the same session")
        assert _PROPERTY_STATUSES.get("applied", 0) >= 8, _PROPERTY_STATUSES
        for status in (
            "whole_plan_skipped",
            "tail_too_close",
            "gap_too_long",
            "no_gap",
            "no_tail_slots",
        ):
            assert _PROPERTY_STATUSES.get(status, 0) >= 1, _PROPERTY_STATUSES


class TestStatusTelemetry:
    """A tail that never runs must not look like a tail with nothing to do."""

    def test_applied(self):
        result = _plan(_slots(_PRE_DW_START, _HORIZON_END), gap_min=0.0)
        assert result.next_window_tail_status == "applied"
        assert result.next_window_gap_min == 0.0

    def test_gap_too_long(self):
        slots = _slots(_PRE_DW_START, datetime(2026, 9, 8, 4, 0))
        result = _plan(slots, gap_min=660.0)
        assert result.next_window_tail_status == "gap_too_long"
        assert result.next_window_gap_min == 660.0

    def test_no_gap(self):
        result = _plan(_slots(_PRE_DW_START, _HORIZON_END), gap_min=None)
        assert result.next_window_tail_status == "no_gap"
        assert result.next_window_gap_min is None

    def test_no_tail_slots(self):
        slots = _slots(datetime(2026, 9, 7, 15, 30), datetime(2026, 9, 8, 15, 30))
        result = _plan(slots, gap_min=1410.0)
        assert result.next_window_tail_status == "no_tail_slots"

    def test_disabled(self):
        result = _plan(
            _slots(_PRE_DW_START, _HORIZON_END),
            gap_min=0.0,
            next_window_tail_enabled=False,
        )
        assert result.next_window_tail_status == "disabled"
        assert result.next_window_gap_min == 0.0

    def test_under_target_mode(self):
        result = _plan(
            _slots(_PRE_DW_START, _HORIZON_END),
            gap_min=0.0,
            allow_dw_entry_under_target=True,
        )
        assert result.next_window_tail_status == "under_target_mode"

    def test_solve_unsuccessful(self):
        from custom_components.localshift.engine.types import OptimizerResult

        slots = _slots(_PRE_DW_START, _HORIZON_END)
        before = _plan(slots, gap_min=None)
        real = DPPlanner._solve_guarded
        calls = []

        def _second_solve_fails(self, inputs, *args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                return real(self, inputs, *args, **kwargs)
            return OptimizerResult(success=False, planner_version="dp_v1")

        with patch.object(DPPlanner, "_solve_guarded", _second_solve_fails):
            result = _plan(slots, gap_min=0.0)
        assert len(calls) == 2
        assert result.next_window_tail_status == "solve_unsuccessful"
        assert result.decisions == before.decisions

    def test_failed(self):
        slots = _slots(_PRE_DW_START, _HORIZON_END)
        with patch.object(
            DPPlanner, "_solve_next_window_tail", side_effect=RuntimeError("boom")
        ):
            result = _plan(slots, gap_min=0.0)
        assert result.success
        assert result.next_window_tail_status == "failed"
        assert result.next_window_tail_start_idx is None

    def test_away_reserve_above_the_requirement(self):
        """The away reserve already holds the battery above what the gap needs:
        there is nothing for a tail to add."""
        slots = _slots(_PRE_DW_START, _HORIZON_END - timedelta(hours=2))
        required = _soc_required_before_gap(_config(), 120.0)
        assert 20.0 < required < 60.0
        reserve = required + 5.0
        off = _plan(
            slots,
            gap_min=120.0,
            away_reserve_floor_pct=reserve,
            next_window_tail_enabled=False,
        )
        on = _plan(slots, gap_min=120.0, away_reserve_floor_pct=reserve)
        assert on.next_window_tail_status == "away_reserve_covers"
        assert on.next_window_tail_start_idx is None
        assert on.decisions == off.decisions
        # A reserve under the requirement leaves the tail in play.
        low = _plan(slots, gap_min=120.0, away_reserve_floor_pct=required - 10.0)
        assert low.next_window_tail_status == "applied"

    def test_away_reserve_is_not_reported_when_the_battery_is_below_it(self):
        """The reserve stops discharge; it does not put charge in. A battery
        that reaches the tail start under the reserve is not covered by it, and
        the status must not say it is."""
        slots = _slots(_MID_DW_START, _HORIZON_END - timedelta(hours=2))
        required = _soc_required_before_gap(_config(), 120.0)
        reserve = required + 5.0
        on = _plan(slots, gap_min=120.0, soc=10.0, away_reserve_floor_pct=reserve)
        tail_start = next(i for i, s in enumerate(slots) if not s.is_demand_window_slot)
        assert on.decisions[tail_start - 1].predicted_soc_pct < reserve
        assert on.next_window_tail_status != "away_reserve_covers"
        assert on.next_window_tail_status == "applied"
        assert on.next_window_tail_start_idx == tail_start
        # What is published is what the plan does. Whether the tail solve
        # charges toward a target that sits under the reserve is the away-mode
        # planner's business and is not pinned here (today it holds, and the
        # entry SOC below the requirement says so).
        assert on.next_window_required_soc_pct == pytest.approx(required)
        assert on.next_window_entry_soc_pct == on.decisions[-1].predicted_soc_pct

    def test_summary_carries_status_and_gap(self):
        from custom_components.localshift.engine.optimizer_runner import (
            _build_summary,
        )

        result = _plan(_slots(_PRE_DW_START, _HORIZON_END), gap_min=0.0)
        summary = _build_summary(result, "c", "2026-09-07T04:47:00+00:00", {}, {}, {})
        assert summary["next_window_tail_status"] == "applied"
        assert summary["next_window_gap_min"] == 0.0
        skipped = _plan(_slots(_PRE_DW_START, _HORIZON_END), gap_min=None)
        summary = _build_summary(skipped, "c", "2026-09-07T04:47:00+00:00", {}, {}, {})
        assert summary["next_window_tail_status"] == "no_gap"
        assert summary["next_window_gap_min"] is None


class TestTailFailureLogging:
    """A broken tail keeps the main plan; it must be visible once, not every
    replan for three hours."""

    @staticmethod
    def _failing_plan(start: datetime, exc: Exception | None = None):
        slots = _slots(start, start + timedelta(hours=24))
        with patch.object(
            DPPlanner,
            "_solve_next_window_tail",
            side_effect=exc if exc is not None else RuntimeError("boom"),
        ):
            return _plan(slots, gap_min=30.0)

    def test_a_different_failure_the_same_day_is_a_warning_again(self, caplog):
        """Once a day is per failure, not per day: a second bug must not hide
        behind the first one's DEBUG repeats."""
        import logging

        from custom_components.localshift.engine import core

        core._reset_tail_failure_log()
        day = datetime(2026, 9, 7, 14, 30)
        with caplog.at_level(logging.DEBUG, logger=core.__name__):
            self._failing_plan(day)
            self._failing_plan(day + timedelta(minutes=5))
            # Same type, different message.
            self._failing_plan(day + timedelta(minutes=10), RuntimeError("bang"))
            # Same message, different type.
            self._failing_plan(day + timedelta(minutes=15), ValueError("bang"))
            # Each already seen today: back to DEBUG.
            self._failing_plan(day + timedelta(minutes=20), RuntimeError("boom"))
            self._failing_plan(day + timedelta(minutes=25), ValueError("bang"))
        records = [r for r in caplog.records if "Next-window tail failed" in r.message]
        assert [r.levelno for r in records] == [
            logging.WARNING,
            logging.DEBUG,
            logging.WARNING,
            logging.WARNING,
            logging.DEBUG,
            logging.DEBUG,
        ]
        assert all(r.exc_info for r in records)

    def test_a_failure_whose_message_keeps_changing_is_capped(self, caplog):
        """A message carrying a number that moves every replan would otherwise
        be a WARNING every five minutes, which is what once-a-day prevents."""
        import logging

        from custom_components.localshift.engine import core

        core._reset_tail_failure_log()
        day = datetime(2026, 9, 7, 14, 30)
        with caplog.at_level(logging.DEBUG, logger=core.__name__):
            for i in range(core._TAIL_FAILURE_MAX_WARNINGS_PER_DAY + 3):
                self._failing_plan(day, RuntimeError(f"soc {i}"))
        records = [r for r in caplog.records if "Next-window tail failed" in r.message]
        warnings = [r for r in records if r.levelno == logging.WARNING]
        assert len(warnings) == core._TAIL_FAILURE_MAX_WARNINGS_PER_DAY
        assert all(r.levelno == logging.DEBUG for r in records[len(warnings) :])

    def test_warning_once_a_day_with_traceback_then_debug(self, caplog):
        import logging

        from custom_components.localshift.engine import core

        core._reset_tail_failure_log()
        with caplog.at_level(logging.DEBUG, logger=core.__name__):
            self._failing_plan(datetime(2026, 9, 7, 14, 30))
            self._failing_plan(datetime(2026, 9, 7, 14, 35))
            self._failing_plan(datetime(2026, 9, 7, 15, 0))
            self._failing_plan(datetime(2026, 9, 8, 14, 30))
        records = [r for r in caplog.records if "Next-window tail failed" in r.message]
        assert [r.levelno for r in records] == [
            logging.WARNING,
            logging.DEBUG,
            logging.DEBUG,
            logging.WARNING,
        ]
        assert all(r.exc_info for r in records)
        assert records[0].exc_info[0] is RuntimeError


class TestWinterSawtooth:
    """Flat prices, or an overnight at or below the morning price, with little
    solar: where an overnight charge-and-drain would show.

    The tail is the same ``_solve`` with the same gates as the ordinary evening
    re-plan, so the assertion is equivalence. The same tail slots are solved the
    ordinary way, from the same SOC, once tomorrow's real entry slot is in view;
    the tail's overnight charge must match that to within the solver's
    resolution."""

    @staticmethod
    def _ordinary_evening_replan(slots, split, start_soc, price):
        """The single-window solve of the tail slots plus tomorrow's entry slot."""
        last = datetime.fromisoformat(slots[-1].timestamp_iso)
        entry_t = last + timedelta(minutes=slots[-1].slot_interval_minutes)
        assert entry_t.time() == _DW_START
        evening = [replace(s, slot_index=i) for i, s in enumerate(slots[split:])]
        evening.append(
            SlotContext(
                slot_index=len(evening),
                timestamp_iso=entry_t.isoformat(),
                slot_interval_minutes=30,
                buy_price=price(entry_t),
                sell_price=0.03,
                solar_kwh=0.0,
                consumption_kwh=0.35,
                is_demand_window_entry=True,
                is_demand_window_slot=True,
            )
        )
        return _plan(evening, gap_min=None, soc=start_soc)

    @pytest.mark.parametrize("start", [_PRE_DW_START, _MID_DW_START], ids=["pre", "in"])
    @pytest.mark.parametrize("soc", [100.0, 30.0], ids=["full", "low"])
    @pytest.mark.parametrize("solar", [0.0, 2.0], ids=["nosolar", "lowsolar"])
    @pytest.mark.parametrize(
        ("price", "overnight_is_cheapest"),
        [(_flat(0.15), False), (_level_overnight, False), (_cheap_overnight, True)],
        ids=["flat", "level", "cheapnight"],
    )
    def test_overnight_charge_matches_the_ordinary_replan(
        self, start, soc, solar, price, overnight_is_cheapest
    ):
        tail, ordinary = self._tail_and_ordinary(start, soc, solar, price)
        tail_overnight = _overnight_charge_kwh(tail)
        ordinary_overnight = _overnight_charge_kwh(ordinary)
        if not overnight_is_cheapest:
            # Flat, or overnight level with the morning: nothing to gain from
            # charging early, and neither path does. Slot for slot the same plan.
            assert tail_overnight == 0.0
            assert ordinary_overnight == 0.0
            assert [d.action for d in tail] == [d.action for d in ordinary]
            return
        # Overnight 4c under the morning: the ordinary re-plan charges overnight
        # (10.8 to 16.0 kWh here) and so does the tail. Six of these eight cases
        # are the same plan slot for slot. In the other two the paths pick
        # different slots among the equally priced 8c ones, because the real
        # entry slot and the tail's zero-energy stand-in break the tie
        # differently, and the totals land 0.3 Wh over and 43 Wh under.
        assert ordinary_overnight > 5.0
        self._assert_within_one_bin(tail_overnight, ordinary_overnight)

    @pytest.mark.parametrize("start", [_PRE_DW_START, _MID_DW_START], ids=["pre", "in"])
    @pytest.mark.parametrize(
        ("price", "solar"),
        [(_deep_cheap_overnight, 6.0), (_cheap_overnight_dear_afternoon, 2.0)],
        ids=["overnight5c-solar6", "dearafternoon-solar2"],
    )
    def test_wider_cheap_overnight_inputs_stay_within_one_bin(
        self, start, price, solar
    ):
        """The review's wider probe (270 cases: three cheap-overnight shapes, two
        start times, SOC 10 to 100, solar 0/2/6 kWh, load 0.4/0.7/1.2 kW) found
        the two paths apart by more than a few Wh in five cases, in both
        directions. These are the extremes of it, full battery and a 0.4 kW
        load: overnight at 5c with 6 kWh of solar, where the tail charges
        140.7 Wh more than the ordinary re-plan, and a dear afternoon with 2 kWh,
        where it charges 83 Wh less. Nothing in the 270 exceeded one bin."""
        tail, ordinary = self._tail_and_ordinary(
            start, 100.0, solar, price, load_kw=0.4
        )
        ordinary_overnight = _overnight_charge_kwh(ordinary)
        assert ordinary_overnight > 5.0
        self._assert_within_one_bin(_overnight_charge_kwh(tail), ordinary_overnight)

    def _tail_and_ordinary(self, start, soc, solar, price, load_kw: float = 0.7):
        """The applied tail's decisions, and the ordinary re-plan of the same
        slots from the same SOC (without its entry slot)."""
        slots = _priced_slots(start, _HORIZON_END, price, solar, load_kw)
        result = _plan(slots, gap_min=0.0, soc=soc)
        assert result.next_window_tail_status == "applied"
        split = result.next_window_tail_start_idx
        ordinary = self._ordinary_evening_replan(
            _priced_slots(start, _HORIZON_END, price, solar, load_kw),
            split,
            result.decisions[split - 1].predicted_soc_pct,
            price,
        )
        assert ordinary.success
        return result.decisions[split:], ordinary.decisions[:-1]

    @staticmethod
    def _assert_within_one_bin(tail_overnight: float, ordinary_overnight: float):
        """The two paths are the same solve on the same slots, apart from the
        last one: a real entry slot with load against a zero-energy stand-in.
        The solver works on a grid of SOC bins (0.96% here, 130 Wh stored, 141 Wh
        of grid charge), so that difference can move the end state by one bin
        and no finer bound means anything: one bin of grid charge is the
        solver's resolution. Either direction, because one bin under is as much
        a tie-break as one bin over. A sawtooth is a charge-and-drain of whole
        slots (1.65 kWh a slot at the normal rate), more than ten bins."""
        tolerance = _one_soc_bin_of_grid_charge_kwh(_config())
        assert 0.10 < tolerance < 0.20
        assert abs(tail_overnight - ordinary_overnight) <= tolerance + 1e-9, (
            f"tail charges {tail_overnight:.4f} kWh overnight, the ordinary "
            f"re-plan {ordinary_overnight:.4f} kWh; one bin is {tolerance:.4f} kWh"
        )


class TestStitchedPlanReporting:
    def test_peak_soc_is_the_stitched_plans(self):
        """Low battery before today's window: the main plan peaks well under the
        SOC the tail climbs to for tomorrow's."""
        slots = _slots(_PRE_DW_START, _HORIZON_END, solar_daily_kwh=2.0)
        off = _plan(slots, gap_min=0.0, soc=40.0, next_window_tail_enabled=False)
        on = _plan(slots, gap_min=0.0, soc=40.0)
        assert on.next_window_tail_start_idx is not None
        stitched_peak = max(d.predicted_soc_pct for d in on.decisions)
        assert stitched_peak > off.peak_soc_pct + 5.0
        assert on.peak_soc_pct == pytest.approx(round(stitched_peak, 2))

    def test_tail_spike_funding_log_is_labelled(self, caplog):
        """A spike-funding line from inside the tail solve must not read as the
        main solve's."""
        import logging

        from custom_components.localshift.engine import core

        slots = _slots(_PRE_DW_START, _HORIZON_END)
        real_solve = DPPlanner._solve
        # Baseline then candidate, for the main solve and again for the tail:
        # the candidate is cheaper both times, so both take the accepted branch.
        costs = iter([10.0, 1.0, 10.0, 1.0])

        def _solve(self, inputs):
            result = real_solve(self, inputs)
            result.projected_net_cost = next(costs)
            return result

        with (
            patch.object(DPPlanner, "_solve", _solve),
            patch.object(core, "find_funding_slots", lambda s, c: frozenset({0})),
            caplog.at_level(logging.DEBUG, logger=core.__name__),
        ):
            result = _plan(slots, gap_min=0.0)
        assert result.next_window_tail_start_idx is not None
        lines = [r.message for r in caplog.records if "SPIKE PRECHARGE" in r.message]
        assert len(lines) == 2
        assert "next-window tail" not in lines[0]
        assert "next-window tail" in lines[1]


class TestPrechargeLogLine:
    """The line incidents are diagnosed from: ``dw_entry_soc`` and ``shortfall``
    describe today's window, so ``charge_slots`` and ``first_charge`` must too."""

    def test_charge_counts_stop_at_the_tail_start(self, caplog):
        import logging
        from types import SimpleNamespace

        from custom_components.localshift.engine.optimizer_facade import (
            OptimizerFacade,
        )

        slots = _slots(_PRE_DW_START, _HORIZON_END)
        config = _config()
        result = DPPlanner().plan(
            OptimizerInputs(
                cycle_id="log",
                initial_soc_pct=100.0,
                slots=slots,
                config=config,
                next_demand_window_gap_min=0.0,
            )
        )
        split = result.next_window_tail_start_idx
        tail_charges = [d for d in result.decisions[split:] if d.grid_charge]
        assert tail_charges
        assert not any(d.grid_charge for d in result.decisions[:split])
        data = SimpleNamespace(soc=100.0, demand_window_active=False)
        with caplog.at_level(logging.INFO):
            OptimizerFacade()._log_precharge_decision(data, result, 100.0, config)
        line = next(
            r.message for r in caplog.records if "Pre-charge decision" in r.message
        )
        assert " charge_slots=0 " in line
        assert "first_charge=None" in line
        assert f"next_window_tail=applied tail_charge_slots={len(tail_charges)}" in line
