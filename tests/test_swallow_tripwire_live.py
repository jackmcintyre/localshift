"""Live trips for the tripwires no other test reaches.

``spike_funding_detection``, ``spike_funded_solve`` and ``mode_transition``
are guarded in ``tests/swallow_tripwire.py``, but nothing in the suite ever
drove the production code into those ``except`` blocks, so the only evidence
that the wires were connected was the static format-string check. Each test
here breaks the real code path, asserts the documented fallback, and carries
the opt-in. The opt-in doubles as the proof: an opt-in that is never reached
fails the test, so these pass only while the wire actually trips.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.localshift.const import BatteryMode
from custom_components.localshift.coordinator import CoordinatorData
from custom_components.localshift.engine import core
from custom_components.localshift.engine.core import DPPlanner
from custom_components.localshift.engine.types import OptimizerInputs
from custom_components.localshift.engine.spike_event import find_funding_slots
from custom_components.localshift.state.machine import StateMachine
from custom_components.localshift.state.mode_configs import MODE_EXECUTORS

# The 2026-08-05 spike horizon: a morning on which funding slots do qualify.
from tests.engine.test_spike_event_precharge import _config, _slots


def _inputs(**config_overrides) -> OptimizerInputs:
    return OptimizerInputs(
        cycle_id="tripwire-live",
        initial_soc_pct=57.225,
        slots=_slots(),
        config=_config(**config_overrides),
    )


def _shape(result) -> tuple:
    return (
        result.success,
        [d.action for d in result.decisions],
        [round(d.predicted_soc_pct, 6) for d in result.decisions],
        round(result.projected_net_cost, 6),
    )


@pytest.fixture
def baseline_plan():
    """The plan with spike pre-charge switched off: what every fallback returns."""
    result = DPPlanner().plan(_inputs(spike_precharge_enabled=False))
    assert result.success
    return result


def test_the_spike_horizon_does_fund_when_nothing_is_broken(baseline_plan):
    """Control: without a fault the funded plan is not the baseline plan."""
    assert find_funding_slots(_slots(), _config())
    funded = DPPlanner().plan(_inputs())
    assert _shape(funded) != _shape(baseline_plan)


@pytest.mark.allow_swallowed_failure("spike_funding_detection")
def test_spike_funding_detection_failure_falls_back_to_baseline(
    monkeypatch, baseline_plan
):
    def gone(slots, config):
        raise AttributeError("find_funding_slots lost something it needed")

    monkeypatch.setattr(core, "find_funding_slots", gone)

    result = DPPlanner().plan(_inputs())

    assert _shape(result) == _shape(baseline_plan)


@pytest.mark.allow_swallowed_failure("spike_funded_solve")
def test_spike_funded_solve_failure_falls_back_to_baseline(monkeypatch, baseline_plan):
    inputs = _inputs()
    funding = find_funding_slots(inputs.slots, inputs.config)
    assert funding
    monkeypatch.setattr(core, "find_funding_slots", lambda slots, config: funding)

    planner = DPPlanner()
    real_solve = planner._solve
    calls = {"n": 0}

    def second_solve_fails(solve_inputs):
        calls["n"] += 1
        if calls["n"] == 2:
            # The funded solve: the one that runs with the funding slots set.
            assert solve_inputs.config.spike_funding_slots == funding
            raise TypeError("the funded solve lost an argument")
        return real_solve(solve_inputs)

    monkeypatch.setattr(planner, "_solve", second_solve_fails)

    result = planner.plan(inputs)

    assert calls["n"] >= 2
    assert _shape(result) == _shape(baseline_plan)
    assert inputs.config.spike_funding_slots is None


@pytest.mark.allow_swallowed_failure("mode_transition")
def test_a_raising_mode_executor_is_reported_as_a_failed_transition():
    """What a deleted or broken executor behind ``MODE_EXECUTORS`` looks like.

    The executor is found by name with ``getattr``; when it raises, the
    transition is reported as merely failed and the commanded mode stays put.
    """
    state_machine = StateMachine(
        MagicMock(),
        MagicMock(),
        lambda key: False,
        lambda key, default=None: default,
        MagicMock(),
    )
    data = CoordinatorData()
    commanded_before = state_machine._commanded_mode
    state_machine._get_mode_config = lambda target, d: MagicMock(backup_reserve=10)
    executor_name = MODE_EXECUTORS[BatteryMode.GRID_CHARGING]
    setattr(
        state_machine,
        executor_name,
        AsyncMock(side_effect=AttributeError("executor lost a collaborator")),
    )

    ok = asyncio.run(
        state_machine._execute_mode_transition(data, BatteryMode.GRID_CHARGING)
    )

    assert ok is False
    assert state_machine._commanded_mode == commanded_before
    assert state_machine._in_mode_transition is False
