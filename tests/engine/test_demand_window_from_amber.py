"""Issue #1104: the demand window from Amber Express's per-slot flag.

Ausgrid's demand charge is seasonal and working-weekday only, but the planner
applied 15:00-21:00 every day by clock. Express's detailed forecast carries
``demand_window`` on every interval (all false on 2026-10-15, out of season).
With ``switch.localshift_demand_window_from_amber`` on, that flag decides which
slots are demand-window slots; a slot without a boolean flag keeps the clock.
With the switch off nothing changes.
"""

from __future__ import annotations

import copy
import json
from datetime import datetime, time, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from custom_components.localshift.const import (
    CONF_DEMAND_WINDOW_FROM_AMBER,
    SWITCH_DEFAULTS,
    SWITCH_DEMAND_WINDOW_FROM_AMBER,
)
from custom_components.localshift.engine.optimizer_runner import _serialize_decision
from custom_components.localshift.engine.slots import SlotBuilder
from custom_components.localshift.engine.types import (
    ObjectiveTerms,
    PlannedSlotDecision,
    PlannerAction,
    PlannerReasonCode,
)
from custom_components.localshift.pricing.types import ForecastSlot
from custom_components.localshift.switch import SWITCH_CATEGORIES, SWITCH_KEYS

SYDNEY = ZoneInfo("Australia/Sydney")
NOW = datetime(2026, 10, 15, 12, 0, tzinfo=SYDNEY)
REPO_ROOT = Path(__file__).resolve().parents[2]
PRECHARGE_DAY = REPO_ROOT / "simulations/replay/2026-08-25.json"


def _clock(ts: datetime) -> bool:
    return time(15) <= ts.astimezone(SYDNEY).time() < time(21)


def _forecast(flag) -> list[ForecastSlot]:
    """24 h of 30-minute entries from NOW; ``flag(start)`` gives demand_window."""
    return [
        ForecastSlot(
            start_time=NOW + timedelta(minutes=30 * i),
            duration=30,
            per_kwh=0.20,
            is_spike=False,
            source_type="amber_express",
            demand_window=flag(NOW + timedelta(minutes=30 * i)),
        )
        for i in range(48)
    ]


def _data(forecast: list[ForecastSlot]) -> SimpleNamespace:
    return SimpleNamespace(
        general_forecast=forecast,
        feed_in_forecast=[
            ForecastSlot(
                start_time=e.start_time,
                duration=30,
                per_kwh=0.05,
                is_spike=False,
                source_type="amber_express",
            )
            for e in forecast
        ],
        solcast_today=[],
        solcast_tomorrow=[],
        load_forecast_slots=[0.5] * 300,
        solcast_analysis_today=None,
        solcast_analysis_tomorrow=None,
        solar_absent_confidence=1.0,
    )


def _window(switch_on: bool, flag) -> list[tuple[datetime, bool, bool]]:
    builder = SlotBuilder(
        config_options={
            "demand_window_start": "15:00:00",
            "demand_window_end": "21:00:00",
            CONF_DEMAND_WINDOW_FROM_AMBER: switch_on,
        },
        ha_timezone="Australia/Sydney",
    )
    slots, _ = builder.build_slots(_data(_forecast(flag)), now_dt=NOW)
    return [
        (
            datetime.fromisoformat(s.timestamp_iso),
            s.is_demand_window_slot,
            s.is_demand_window_entry,
        )
        for s in slots
    ]


def _clock_flags(rows) -> list[bool]:
    return [_clock(ts) for ts, _, _ in rows]


# --------------------------------------------------------------------------
# Slot builder
# --------------------------------------------------------------------------


def test_switch_off_keeps_the_clock_window_whatever_amber_says():
    rows = _window(False, lambda ts: False)
    assert [dw for _, dw, _ in rows] == _clock_flags(rows)
    assert sum(entry for _, _, entry in rows) == 1


def test_switch_on_and_amber_says_no_window_means_no_demand_window():
    """2026-10-15: every Express interval carried demand_window=false, yet the
    planner banned import 15:00-21:00 and drove a 95% target for 15:00."""
    rows = _window(True, lambda ts: False)
    assert not any(dw for _, dw, _ in rows)
    assert not any(entry for _, _, entry in rows)


def test_switch_on_and_an_in_season_day_matches_the_clock_window():
    rows = _window(True, _clock)
    assert [dw for _, dw, _ in rows] == _clock_flags(rows)
    assert rows == _window(False, _clock)


def test_switch_on_follows_amber_where_it_differs_from_the_clock():
    """The flag decides in both directions, not only to remove window."""
    rows = _window(True, lambda ts: time(16) <= ts.astimezone(SYDNEY).time() < time(18))
    flagged = [ts.astimezone(SYDNEY).strftime("%H:%M") for ts, dw, _ in rows if dw]
    assert flagged == ["16:00", "16:30", "17:00", "17:30"]


def _padded_window(state_reader, now: datetime, flag) -> list[tuple[str, bool, bool]]:
    """Switch on, a 24 h Express feed from ``now`` floored to the half hour,
    run through the reader's real 24 h padding as live does."""
    start = now.replace(minute=now.minute - now.minute % 30, second=0)
    feed = [
        ForecastSlot(
            start_time=start + timedelta(minutes=30 * i),
            duration=30,
            per_kwh=0.20,
            is_spike=False,
            source_type="amber_express",
            demand_window=flag(start + timedelta(minutes=30 * i)),
        )
        for i in range(48)
    ]
    padded = state_reader._extend_forecast_with_assumed_prices(feed, now, 0.20)
    assert padded[-1].source_type == "extended"
    builder = SlotBuilder(
        config_options={
            "demand_window_start": "15:00:00",
            "demand_window_end": "21:00:00",
            CONF_DEMAND_WINDOW_FROM_AMBER: True,
        },
        ha_timezone="Australia/Sydney",
    )
    slots, _ = builder.build_slots(_data(padded), now_dt=now)
    return [
        (
            datetime
            .fromisoformat(s.timestamp_iso)
            .astimezone(SYDNEY)
            .strftime("%d %H:%M"),
            s.is_demand_window_slot,
            s.is_demand_window_entry,
        )
        for s in slots
    ]


def test_reader_padding_does_not_open_a_window_amber_never_flagged(state_reader):
    """PR #1201 review: live on 15 Oct Express's feed ended ~22 s short of
    now + 24 h, the reader padded one 30-minute slot at 16 Oct 15:00, and that
    unflagged slot fell back to the clock: a DW entry and a 95% target at the
    end of the horizon every afternoon, with every Express flag false."""
    rows = _padded_window(
        state_reader, datetime(2026, 10, 15, 15, 0, 22, tzinfo=SYDNEY), lambda ts: False
    )
    assert rows[-1][0] == "16 15:00"
    assert [r for r in rows if r[1] or r[2]] == []


def test_reader_padding_continues_a_window_the_feed_opened(state_reader):
    """In season the padding past a feed that ends mid-window stays in it."""
    rows = _padded_window(
        state_reader, datetime(2026, 10, 15, 18, 0, 22, tzinfo=SYDNEY), _clock
    )
    assert rows[-1] == ("16 18:00", True, False)
    assert [r[0] for r in rows if r[2]] == ["15 18:00", "16 15:00"]


def test_a_slot_without_a_boolean_flag_keeps_the_clock():
    """Field absent, base Amber, a synthetic slot: the feed can only add
    window, never remove it."""
    cutoff = NOW + timedelta(hours=5)  # 17:00: flags stop mid-window
    rows = _window(True, lambda ts: False if ts < cutoff else None)
    for ts, dw, _ in rows:
        assert dw is (_clock(ts) if ts >= cutoff else False), ts


# --------------------------------------------------------------------------
# demand_window_active (Demand Block execution, backstop guards)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("switch_on", "flag", "expected"),
    [
        (False, False, True),  # switch off: clock, as on main
        (True, False, False),  # Amber says no window: not active
        (True, True, True),
        (True, None, True),  # no flag on the current entry: clock
    ],
)
def test_demand_window_active_follows_the_current_interval(
    computation_engine, coordinator_data, switch_on, flag, expected
):
    now = datetime(2026, 10, 15, 16, 10, tzinfo=SYDNEY)  # inside 15:00-21:00
    computation_engine.entry.options["demand_window_start"] = "15:00:00"
    computation_engine.entry.options["demand_window_end"] = "21:00:00"
    computation_engine.entry.options["ha_timezone"] = "Australia/Sydney"
    switches = {"demand_window_block": True, SWITCH_DEMAND_WINDOW_FROM_AMBER: switch_on}
    computation_engine._get_switch_state = lambda key: switches.get(key, False)
    coordinator_data.general_forecast = [
        ForecastSlot(
            start_time=datetime(2026, 10, 15, 16, 0, tzinfo=SYDNEY),
            duration=30,
            per_kwh=0.29,
            is_spike=False,
            source_type="amber_express",
            demand_window=flag,
        )
    ]
    with patch("homeassistant.util.dt.now", return_value=now):
        ctx = computation_engine._build_step_context()
        computation_engine._compute_demand_window_active(coordinator_data, ctx)
    assert coordinator_data.demand_window_active is expected


# --------------------------------------------------------------------------
# The planner, end to end through the replay harness
# --------------------------------------------------------------------------


def _flagged(scenario: dict, flag) -> dict:
    out = copy.deepcopy(scenario)
    for entry in out["input"]["general_forecast"]:
        entry["demand_window"] = flag(datetime.fromisoformat(entry["start_time"]))
    return out


@pytest.fixture
def rpb(monkeypatch):
    from scripts import replay_plan_baseline as module

    opts, switches = module.ARMS["captured"]
    monkeypatch.setitem(
        module.ARMS,
        "amber",
        (opts, {**switches, SWITCH_DEMAND_WINDOW_FROM_AMBER: True}),
    )
    monkeypatch.setattr(
        module, "DECISION_COLUMNS", (*module.DECISION_COLUMNS, "is_demand_window")
    )
    return module


def test_plan_without_flagged_slots_has_no_window_and_no_target(rpb):
    """2026-08-25 09:00 at 10% SOC: on main the plan grid-charges before 15:00
    to enter the window at target. With Amber saying there is no window there
    is no entry, no target and no import ban: the planner just minimises cost."""
    scenario = json.loads(PRECHARGE_DAY.read_text())
    clock = rpb.replay(scenario, "captured")
    amber = rpb.replay(_flagged(scenario, lambda ts: False), "amber")

    assert clock["summary"]["dw_entry_soc_pct"] is not None
    assert any(row[-1] for row in clock["decisions"])
    assert amber["summary"]["dw_entry_soc_pct"] is None
    assert not any(row[-1] for row in amber["decisions"])
    assert amber["decisions"] != clock["decisions"]


def test_plan_on_an_in_season_day_is_the_clock_plan(rpb):
    scenario = json.loads(PRECHARGE_DAY.read_text())
    clock = rpb.replay(scenario, "captured")
    amber = rpb.replay(_flagged(scenario, _clock), "amber")
    assert amber == clock


# --------------------------------------------------------------------------
# Declared like the other switches; published on the plan
# --------------------------------------------------------------------------


def test_switch_is_declared_and_ships_off():
    assert SWITCH_DEMAND_WINDOW_FROM_AMBER in SWITCH_KEYS
    assert SWITCH_DEFAULTS[SWITCH_DEMAND_WINDOW_FROM_AMBER] is False
    assert SWITCH_DEMAND_WINDOW_FROM_AMBER in SWITCH_CATEGORIES


def test_plan_decisions_publish_the_demand_window_flag():
    decision = PlannedSlotDecision(
        slot_index=0,
        timestamp_iso=NOW.isoformat(),
        slot_interval_minutes=30,
        action=PlannerAction.HOLD,
        reason_code=next(iter(PlannerReasonCode)),
        objective_terms=ObjectiveTerms(),
        predicted_soc_pct=50.0,
        grid_import_kwh=0.0,
        grid_export_kwh=0.0,
        is_demand_window=True,
    )
    assert _serialize_decision(decision)["is_demand_window"] is True
