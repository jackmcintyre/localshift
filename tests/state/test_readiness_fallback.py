"""Issue #1228: automation readiness gates mode decisions on every tick.

``check_automation_ready`` used to be consulted only on the tick where the
startup grace ended. An outage of prices, SOC or the operation mode later on
reached the optimizer as placeholder data. These tests drive the state machine
through such outages: hold the commanded mode briefly, fall back to
SELF_CONSUMPTION with one write if the outage persists, resume normal decisions
when the data returns.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.localshift.const import (
    READINESS_LOSS_CONFIRM_SECONDS,
    BatteryMode,
)
from custom_components.localshift.coordinator.data import CoordinatorData
from custom_components.localshift.state.machine import StateMachine
from custom_components.localshift.utils.validation import IntegrationStatus

T0 = datetime(2026, 11, 5, 12, 0, 0, tzinfo=timezone(timedelta(hours=11)))
NOW_PATH = "custom_components.localshift.state.machine.dt_util.now"
CONFIRM = timedelta(seconds=READINESS_LOSS_CONFIRM_SECONDS)


class _Engine:
    """Computation-engine stub that commits ``plan_mode`` when a token is granted."""

    def __init__(self, plan_mode: BatteryMode) -> None:
        self.plan_mode = plan_mode

    def compute_derived_values(self, data) -> None:
        if data.mode_decision_allowed:
            data.active_mode = self.plan_mode


class _Rig:
    def __init__(self, switches: dict[str, bool] | None = None) -> None:
        self.controller = MagicMock()
        for name in (
            "set_self_consumption",
            "set_force_charge",
            "set_boost_charge",
            "set_force_discharge",
            "set_proactive_export",
            "verify_current_state",
        ):
            setattr(self.controller, name, AsyncMock(return_value=True))
        self.controller.read_fresh_soc = MagicMock(return_value=None)
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
        validator.status = IntegrationStatus.OK
        validator.errors = []
        validator.warnings = []
        states = {"automation_enabled": True, "dry_run": False}
        states.update(switches or {})
        self.machine = StateMachine(
            self.controller,
            notifications,
            lambda key: states.get(key, False),
            lambda key, default=None: default,
            validator,
        )
        self.machine._startup_grace_until = None
        self.data = CoordinatorData()
        self.data.soc = 50.0
        self.data.operation_mode = "autonomous"
        self.data.backup_reserve = 50
        self.data.decision_log = []
        self.data.forecast_consumption_source_counts = {}
        self.data.forecast_ready = True
        self.data.forecast_status = "ready"
        self.data.prices_available = True
        self.data.automation_ready = True
        self.data.manual_override = False
        self.engine = _Engine(BatteryMode.SELF_CONSUMPTION)
        self.now = T0

    def lose_prices(self) -> None:
        self.data.prices_available = False
        self.data.automation_ready = False
        self.data.automation_ready_missing = ["Price entities unavailable"]

    def restore_prices(self) -> None:
        self.data.prices_available = True
        self.data.automation_ready = True
        self.data.automation_ready_missing = []

    def tick(self, advance: timedelta = timedelta(0)) -> None:
        self.now += advance
        with patch(NOW_PATH, return_value=self.now):
            asyncio.run(
                self.machine.evaluate_state_machine(self.data, self.engine)
            )

    @property
    def writes(self) -> int:
        return sum(
            getattr(self.controller, n).await_count
            for n in (
                "set_self_consumption",
                "set_force_charge",
                "set_boost_charge",
                "set_force_discharge",
                "set_proactive_export",
            )
        )


def _warnings(caplog, needle: str) -> list[logging.LogRecord]:
    return [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and needle in r.getMessage()
    ]


@pytest.fixture
def rig() -> _Rig:
    return _Rig()


class TestPriceOutageAfterStartup:
    def test_outage_inside_confirm_window_commits_nothing(self, rig):
        """The plan wants a new mode, but prices are gone: no commit, no write."""
        rig.machine._commanded_mode = BatteryMode.SELF_CONSUMPTION
        rig.engine.plan_mode = BatteryMode.BOOST_CHARGING
        rig.lose_prices()

        rig.tick()
        rig.tick(timedelta(seconds=30))

        assert rig.writes == 0
        assert rig.machine._commanded_mode == BatteryMode.SELF_CONSUMPTION

    @pytest.mark.parametrize(
        "held",
        [
            BatteryMode.GRID_CHARGING,
            BatteryMode.BOOST_CHARGING,
            BatteryMode.PROACTIVE_EXPORT,
            BatteryMode.SPIKE_DISCHARGE,
        ],
    )
    def test_persistent_outage_in_active_mode_falls_back_with_one_write(
        self, rig, held, caplog
    ):
        rig.machine._commanded_mode = held
        rig.lose_prices()

        with caplog.at_level(logging.WARNING):
            rig.tick()
            rig.tick(CONFIRM)
            rig.tick(timedelta(seconds=60))
            rig.tick(timedelta(seconds=60))

        rig.controller.set_self_consumption.assert_awaited_once()
        assert rig.writes == 1
        assert rig.machine._commanded_mode == BatteryMode.SELF_CONSUMPTION
        assert len(_warnings(caplog, "readiness lost")) == 1

    def test_outage_in_self_consumption_writes_nothing(self, rig, caplog):
        rig.lose_prices()
        with caplog.at_level(logging.WARNING):
            rig.tick()
            rig.tick(CONFIRM)
            rig.tick(timedelta(seconds=60))

        assert rig.writes == 0
        assert len(_warnings(caplog, "readiness lost")) == 1

    def test_plan_is_not_followed_while_not_ready(self, rig):
        """Even after the fallback, a plan that wants charging is not executed."""
        rig.machine._commanded_mode = BatteryMode.GRID_CHARGING
        rig.engine.plan_mode = BatteryMode.GRID_CHARGING
        rig.lose_prices()
        rig.tick()
        rig.tick(CONFIRM)
        rig.tick(timedelta(seconds=60))

        rig.controller.set_force_charge.assert_not_awaited()
        assert rig.machine._commanded_mode == BatteryMode.SELF_CONSUMPTION

    def test_failed_fallback_write_is_retried_until_it_lands(self, rig):
        rig.machine._commanded_mode = BatteryMode.GRID_CHARGING
        rig.controller.set_self_consumption.return_value = False
        rig.lose_prices()
        rig.tick()
        rig.tick(CONFIRM)
        assert rig.machine._commanded_mode == BatteryMode.GRID_CHARGING

        rig.controller.set_self_consumption.return_value = True
        rig.tick(timedelta(seconds=60))
        rig.tick(timedelta(seconds=60))

        assert rig.machine._commanded_mode == BatteryMode.SELF_CONSUMPTION
        assert rig.controller.set_self_consumption.await_count == 2


class TestRecovery:
    def test_recovery_after_fallback_resumes_normal_decisions(self, rig, caplog):
        rig.machine._commanded_mode = BatteryMode.GRID_CHARGING
        rig.lose_prices()
        rig.tick()
        rig.tick(CONFIRM)
        assert rig.machine._commanded_mode == BatteryMode.SELF_CONSUMPTION

        rig.restore_prices()
        rig.engine.plan_mode = BatteryMode.GRID_CHARGING
        with caplog.at_level(logging.INFO):
            rig.tick(timedelta(seconds=60))

        assert rig.machine._commanded_mode == BatteryMode.GRID_CHARGING
        rig.controller.set_force_charge.assert_awaited_once()
        assert any("resum" in r.getMessage().lower() for r in caplog.records)

    def test_recovery_re_decides_even_when_context_is_unchanged(self, rig):
        """The decision fingerprint is invalidated, so the plan takes effect now."""
        rig.machine._commanded_mode = BatteryMode.SELF_CONSUMPTION
        rig.engine.plan_mode = BatteryMode.SELF_CONSUMPTION
        rig.tick()  # settles a fingerprint
        rig.lose_prices()
        rig.tick(timedelta(seconds=10))
        rig.tick(CONFIRM)

        rig.restore_prices()
        rig.engine.plan_mode = BatteryMode.BOOST_CHARGING
        rig.tick(timedelta(seconds=60))

        rig.controller.set_boost_charge.assert_awaited_once()


class TestFlappingData:
    def test_outage_that_clears_inside_the_window_causes_no_churn(
        self, rig, caplog
    ):
        rig.machine._commanded_mode = BatteryMode.GRID_CHARGING
        rig.engine.plan_mode = BatteryMode.GRID_CHARGING
        with caplog.at_level(logging.WARNING):
            for _ in range(3):
                rig.lose_prices()
                rig.tick(timedelta(seconds=5))
                rig.tick(timedelta(seconds=20))
                rig.restore_prices()
                rig.tick(timedelta(seconds=5))

        assert rig.writes == 0
        assert rig.machine._commanded_mode == BatteryMode.GRID_CHARGING
        assert not _warnings(caplog, "readiness lost")

    def test_window_restarts_after_a_brief_recovery(self, rig):
        """Two short outages do not add up to one long one."""
        rig.machine._commanded_mode = BatteryMode.GRID_CHARGING
        rig.engine.plan_mode = BatteryMode.GRID_CHARGING
        rig.lose_prices()
        rig.tick()
        rig.tick(CONFIRM - timedelta(seconds=10))
        rig.restore_prices()
        rig.tick(timedelta(seconds=5))
        rig.lose_prices()
        rig.tick(timedelta(seconds=5))
        rig.tick(CONFIRM - timedelta(seconds=10))

        assert rig.writes == 0

    def test_new_outage_after_fallback_warns_again(self, rig, caplog):
        rig.machine._commanded_mode = BatteryMode.GRID_CHARGING
        with caplog.at_level(logging.WARNING):
            rig.lose_prices()
            rig.tick()
            rig.tick(CONFIRM)
            rig.restore_prices()
            rig.tick(timedelta(seconds=60))
            rig.lose_prices()
            rig.tick(timedelta(seconds=60))
            rig.tick(CONFIRM)

        assert len(_warnings(caplog, "readiness lost")) == 2


class TestNeverWritesWhenNotInControl:
    @pytest.mark.parametrize("override", [False, True])
    def test_automation_disabled_or_manual_never_writes(self, override, caplog):
        rig = _Rig(switches={"automation_enabled": False})
        rig.data.manual_override = override
        rig.machine._commanded_mode = BatteryMode.GRID_CHARGING
        rig.lose_prices()

        with caplog.at_level(logging.WARNING):
            rig.tick()
            rig.tick(CONFIRM)
            rig.tick(timedelta(seconds=60))

        assert rig.writes == 0
        assert not _warnings(caplog, "readiness lost")

    def test_outage_clock_does_not_survive_a_disabled_stretch(self):
        """Disable mid-outage, re-enable later: a fresh window, not an old one."""
        states = {"automation_enabled": True, "dry_run": False}
        rig = _Rig()
        rig.machine._get_switch_state = lambda key: states.get(key, False)
        rig.machine._commanded_mode = BatteryMode.GRID_CHARGING
        rig.lose_prices()
        rig.tick()
        rig.tick(CONFIRM - timedelta(seconds=10))

        states["automation_enabled"] = False
        rig.tick(timedelta(seconds=5))
        states["automation_enabled"] = True
        rig.machine._commanded_mode = BatteryMode.GRID_CHARGING
        rig.tick(timedelta(seconds=60))

        assert rig.writes == 0

    def test_dry_run_issues_no_battery_writes(self):
        rig = _Rig(switches={"dry_run": True})
        rig.machine._commanded_mode = BatteryMode.GRID_CHARGING
        rig.lose_prices()
        rig.tick()
        rig.tick(CONFIRM)

        # The transition path hands dry_run to the controller, which no-ops.
        for call in rig.controller.set_self_consumption.await_args_list:
            assert call.args[1] is True
        assert rig.controller.set_force_charge.await_count == 0
        assert rig.controller.set_proactive_export.await_count == 0


class TestStartupHandover:
    def test_not_ready_at_grace_end_is_held_until_ready(self, rig, caplog):
        """The startup fallback and the per-tick gate are one path, not two."""
        rig.machine._startup_grace_until = T0 - timedelta(seconds=1)
        rig.lose_prices()
        rig.engine.plan_mode = BatteryMode.GRID_CHARGING

        with caplog.at_level(logging.WARNING):
            rig.tick()
            rig.tick(timedelta(seconds=60))
            rig.tick(timedelta(seconds=60))

        assert rig.writes == 0
        assert rig.machine._commanded_mode == BatteryMode.SELF_CONSUMPTION
        assert len(_warnings(caplog, "not ready")) == 1
        assert not _warnings(caplog, "readiness lost")

        rig.restore_prices()
        rig.tick(timedelta(seconds=60))
        rig.controller.set_force_charge.assert_awaited_once()


class TestReadinessIsRefreshedEveryEvaluation:
    def test_check_runs_even_when_ready_and_never_warns_itself(self, rig):
        check = MagicMock()
        asyncio.run(
            rig.machine.evaluate_state_machine(
                rig.data, rig.engine, check_automation_ready_func=check
            )
        )
        check.assert_called_once_with(rig.data, suppress_warning=True)
