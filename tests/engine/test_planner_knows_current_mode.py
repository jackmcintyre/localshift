"""switch.localshift_planner_knows_current_mode, through the real path (#1158).

``OptimizerInputs.current_action`` is what arms the planner's slot-0 switching
penalty. Live it was never set: the facade built ``OptimizerInputs`` without
it, so every plan was made as if the battery had no current mode. The switch
wires it from the committed mode. Off (the default) must leave it None; on, it
must carry the committed mode, and a slot 0 that changes it must pay the
penalty. Both are checked on ``ComputationEngine.compute_derived_values``, the
call the coordinator makes, so a break anywhere between the switch and the
planner shows up here.
"""

from __future__ import annotations

from contextlib import ExitStack
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from custom_components.localshift.computation_engine import ComputationEngine
from custom_components.localshift.const import (
    SWITCH_DEFAULTS,
    SWITCH_PLANNER_KNOWS_CURRENT_MODE,
    BatteryMode,
)
from custom_components.localshift.engine.types import PlannerAction
from custom_components.localshift.switch import (
    SWITCH_STATE_PREFIX,
    LocalShiftSwitch,
)
from tests.test_scenarios import (
    create_mock_entry,
    create_mock_get_entity_id,
    create_mock_get_switch_state,
    setup_coordinator_data,
    setup_mock_hass,
)

_NOW = datetime.fromisoformat("2026-10-06T10:02:00+11:00")
_FEED_START = datetime.fromisoformat("2026-10-06T10:00:00+11:00")


def _feed(per_kwh: float) -> list[dict]:
    return [
        {
            "start_time": (_FEED_START + timedelta(minutes=30 * i)).isoformat(),
            "duration": 30,
            "per_kwh": per_kwh,
            "spike_status": "none",
            "estimate": i > 0,
        }
        for i in range(48)
    ]


# A full battery on a flat buy price: nothing to gain from charging, so the plan
# opens on HOLD whatever mode the battery was left in.
_PAYLOAD = {
    "test_time": _NOW.isoformat(),
    "soc": 100.0,
    "operation_mode": "backup",
    "backup_reserve": 100,
    "general_price": 0.25,
    "feed_in_price": 0.01,
    "general_forecast": _feed(0.25),
    "feed_in_forecast": _feed(0.01),
    "solcast_today": [],
    "solcast_tomorrow": [],
    "load_power_kw": 0.7,
    "solar_power_kw": 0.0,
}
_OVERRIDES = {
    "battery_target": 95,
    "minimum_target_soc": 5,
    "switching_penalty": 0.08,
    "switching_penalty_per_kwh": 0.4,
    "demand_window_start": "15:00:00",
    "demand_window_end": "21:00:00",
    "optimization_mode": "self_consumption",
    "pricing_source": "amber",
    "comparison_mode": "disabled",
    "ha_timezone": "Australia/Sydney",
}


def _plan(switch_on: bool | None):
    """One engine pass; returns the planner's inputs and its result."""
    switches = {}
    if switch_on is not None:
        switches[SWITCH_PLANNER_KNOWS_CURRENT_MODE] = switch_on
    data = setup_coordinator_data(_PAYLOAD)
    data.forecast_horizon_hours = 24.0
    data.mode_decision_allowed = True
    # The mode the previous cycle committed, as the replay harness seeds it.
    data.active_mode = BatteryMode.GRID_CHARGING
    engine = ComputationEngine(
        setup_mock_hass(_PAYLOAD),
        create_mock_entry(_OVERRIDES),
        create_mock_get_entity_id(),
        create_mock_get_switch_state(switches),
    )
    planner = engine._optimizer_facade._planner
    seen: list = []

    def spy(inputs):
        result = real_plan(inputs)
        seen.append((inputs, result))
        return result

    real_plan = planner.plan
    patches = [
        patch("homeassistant.util.dt.now", return_value=_NOW),
        patch.object(planner, "plan", side_effect=spy),
        patch.object(engine, "_get_historical_hourly_averages", return_value={}),
        patch.object(engine._history_fetcher, "_historical_load_cache", {}),
        patch.object(engine._history_fetcher, "_historical_load_sample_counts", {}),
        patch.object(engine._history_fetcher, "_historical_load_source", "none"),
        patch.object(engine._history_fetcher, "_recent_load_1hr_kw", 0.7),
    ]
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        engine.compute_derived_values(data)
    assert len(seen) == 1, "the live solve did not run exactly once"
    return seen[0]


@pytest.mark.parametrize(
    ("stored", "expected"),
    [(None, False), (True, True), (False, False)],
    ids=["no-stored-state", "stored-on", "stored-off"],
)
def test_the_switch_ships_off_and_restores_a_stored_state(stored, expected):
    """Off on a fresh install and on an install with nothing stored for it."""
    assert SWITCH_DEFAULTS[SWITCH_PLANNER_KNOWS_CURRENT_MODE] is False
    states: dict[str, bool] = {}
    coordinator = MagicMock()
    coordinator.set_switch_state = MagicMock(side_effect=states.__setitem__)
    coordinator.get_switch_state = MagicMock(side_effect=states.__getitem__)
    entry = MagicMock()
    option_key = f"{SWITCH_STATE_PREFIX}{SWITCH_PLANNER_KNOWS_CURRENT_MODE}"
    entry.options = {} if stored is None else {option_key: stored}
    switch = LocalShiftSwitch(coordinator, entry, SWITCH_PLANNER_KNOWS_CURRENT_MODE)
    assert switch.is_on is expected


@pytest.mark.parametrize("switch_on", [None, False], ids=["no-state", "off"])
def test_off_the_planner_is_told_nothing_and_slot_0_pays_no_penalty(switch_on):
    inputs, result = _plan(switch_on)
    assert inputs.current_action is None
    assert result.success
    assert result.decisions[0].objective_terms.switching_penalty == 0.0


def test_on_the_planner_is_told_the_committed_mode_and_slot_0_pays_to_leave_it():
    inputs, result = _plan(True)
    assert inputs.current_action == PlannerAction.CHARGE_GRID_NORMAL
    assert result.success
    slot0 = result.decisions[0]
    # Full battery, flat price: the plan still leaves grid charging, and now
    # pays the switching penalty for doing so.
    assert slot0.action == PlannerAction.HOLD
    assert slot0.objective_terms.switching_penalty > 0.0
