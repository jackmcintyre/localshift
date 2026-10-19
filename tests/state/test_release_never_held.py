"""Issue #1227: a charge release commits on the price change that wants it.

The old charge-release dwell held a release for a band after the charge was
decided, keyed on ``decision_timestamp``. In normal operation it never held
anything: the state machine clears that field in the same evaluation that
executes the charge. When the charge never reached the battery (dry run, or the
entity validator blocking the transition) the field was never cleared, so the
dwell held the release and spent the decision token doing it. The release then
waited for some later price change, and a validator that unblocked in between
executed the stale charge even though the plan wanted self consumption.

These tests drive the real ``StateMachine.evaluate_state_machine`` and the real
``OptimizerFacade._assign_active_mode`` commit path; only the plan's slot-0
mode, the safety gate and the backstops are scripted.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.util import dt as dt_util

from custom_components.localshift.const import BatteryMode
from custom_components.localshift.coordinator import CoordinatorData
from custom_components.localshift.engine.optimizer_facade import OptimizerFacade
from custom_components.localshift.engine.types import OptimizerConfig
from custom_components.localshift.state.machine import StateMachine

SELF = BatteryMode.SELF_CONSUMPTION
GRID = BatteryMode.GRID_CHARGING

# Every event is an offset in seconds from a 5-minute price boundary. All of
# them sit mid-interval, well clear of the #1159 charge-start gate's window.
BOUNDARY = datetime(2026, 10, 16, tzinfo=UTC)
_FACADE = "custom_components.localshift.engine.optimizer_facade"


class FacadeEngine:
    """compute_derived_values → the real facade commit path, plan scripted."""

    def __init__(self) -> None:
        self.facade = OptimizerFacade()
        self.plan_mode = SELF

    def compute_derived_values(self, data: CoordinatorData) -> None:
        with (
            patch(f"{_FACADE}.OptimizerSafetyGate") as gate,
            patch(
                f"{_FACADE}._current_slot_debug_info",
                return_value=(0, True, "", "", 0.0),
            ),
            patch(
                f"{_FACADE}._derive_runtime_apply_plan",
                return_value={"battery_mode": self.plan_mode.value, "action": "x"},
            ),
            patch.object(
                OptimizerFacade, "_precharge_backstop_mode", return_value=None
            ),
            patch.object(OptimizerFacade, "_runway_backstop_mode", return_value=None),
        ):
            gate.return_value.check_admission.return_value = SimpleNamespace(
                allowed=True, block_reason=None
            )
            self.facade._assign_active_mode(
                data, SimpleNamespace(), OptimizerConfig(), {}
            )


def _state_machine(dry_run: bool) -> StateMachine:
    controller = MagicMock()
    for name in (
        "set_self_consumption",
        "set_force_charge",
        "set_boost_charge",
        "set_force_discharge",
        "set_proactive_export",
        "verify_current_state",
    ):
        setattr(controller, name, AsyncMock(return_value=True))
    notifications = MagicMock()
    for name in (
        "send_transition_notification",
        "send_transition_failed_notification",
        "send_health_correction_notification",
        "send_manual_override_timeout_notification",
        "send_automation_disabled_notification",
        "send_tesla_override_notification",
    ):
        setattr(notifications, name, AsyncMock())
    validator = MagicMock()
    validator.should_allow_automation = MagicMock(return_value=True)
    validator.errors = []
    validator.warnings = []
    return StateMachine(
        controller,
        notifications,
        lambda key: {"automation_enabled": True, "dry_run": dry_run}.get(key, False),
        lambda key, default=None: default,
        validator,
    )


@pytest.fixture
def data() -> CoordinatorData:
    data = CoordinatorData()
    data.soc = 50.0
    data.operation_mode = "self_consumption"
    data.backup_reserve = 10
    data.general_price = 0.20
    data.feed_in_price = 0.05
    data.price_spike = False
    data.demand_window_active = False
    data.manual_override = False
    data.automation_ready = True
    data.active_mode = SELF
    data.decision_log = []
    data.price_estimate = False
    return data


def _tick(
    sm: StateMachine,
    data: CoordinatorData,
    engine: FacadeEngine,
    offset_s: float,
    plan: BatteryMode,
    price: float,
) -> BatteryMode:
    """One in-lock evaluation at BOUNDARY + offset_s; returns the decided mode."""
    data.general_price = price
    data.price_estimate = False
    engine.plan_mode = plan
    with patch.object(
        dt_util, "now", return_value=BOUNDARY + timedelta(seconds=offset_s)
    ):
        asyncio.run(sm.evaluate_state_machine(data, engine))
    return data.active_mode


def _charge_commands(sm: StateMachine) -> int:
    controller = sm._battery_controller
    return (
        controller.set_force_charge.await_count
        + controller.set_boost_charge.await_count
    )


def test_validator_blocked_charge_then_release_commits_immediately(data) -> None:
    """The stale charge is released at once and never executed afterwards."""
    sm = _state_machine(dry_run=False)
    engine = FacadeEngine()
    assert _tick(sm, data, engine, -280, SELF, 0.20) == SELF

    sm.entity_validator.should_allow_automation.return_value = False
    assert _tick(sm, data, engine, -250, GRID, 0.10) == GRID
    assert _charge_commands(sm) == 0, "the validator blocked the charge"

    # Price moves; the fresh plan wants self consumption: release now.
    assert _tick(sm, data, engine, -220, SELF, 0.30) == SELF
    assert data.debug_plan_mode_pending is None

    # The validator unblocks with no further price change: nothing to converge
    # to except self consumption, so the stale charge never runs.
    sm.entity_validator.should_allow_automation.return_value = True
    assert _tick(sm, data, engine, -190, SELF, 0.30) == SELF
    assert _tick(sm, data, engine, -130, SELF, 0.30) == SELF
    assert _charge_commands(sm) == 0


def test_dry_run_charge_then_release_commits_immediately(data) -> None:
    sm = _state_machine(dry_run=True)
    engine = FacadeEngine()
    assert _tick(sm, data, engine, -280, SELF, 0.20) == SELF

    assert _tick(sm, data, engine, -250, GRID, 0.10) == GRID

    assert _tick(sm, data, engine, -220, SELF, 0.30) == SELF
    assert data.debug_plan_mode_pending is None
