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
        DPPlanner().plan(
            OptimizerInputs(
                cycle_id="with-tail",
                initial_soc_pct=100.0,
                slots=slots,
                config=config,
                next_demand_window_gap_min=0.0,
            )
        )
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
