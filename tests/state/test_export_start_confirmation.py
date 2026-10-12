"""Issue #1119: an export start deferred on an unconfirmed price must be re-decided.

Slot 0 may not START an export while its sell price is still Amber's
``estimate`` (the previous interval's price, carried for the first seconds of
the new one). That gate lives in the planner. The COMMIT path is the state
machine's decision token, and the token is granted only when the decision
context changes: buy | sell | spike | dw_active | soc_floor.

Amber's confirmation is an attribute update. When the confirmed price equals
the carried one (seen live 2026-10-12: 19:20 and 19:25 both 0.2587426) the
price sensors' states do not move, so no token follows. A token that landed
inside the grace for any other reason (demand-window boundary, spike flip,
SOC floor, startup) was spent on a hold, and the export the plan wanted could
not start until some later interval happened to carry a different price.

These tests drive the real commit path: ``StateMachine.evaluate_state_machine``
over the real ``ComputationEngine`` (slot builder, DP, optimizer facade), on
the inputs captured from the live system at 15:20:10 on 2026-10-12.
"""

from __future__ import annotations

import asyncio
import copy
import dataclasses
import json
import sys
from contextlib import ExitStack
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.localshift.const import BatteryMode
from custom_components.localshift.state.machine import StateMachine

REPO_ROOT = Path(__file__).resolve().parents[2]
CAPTURE = REPO_ROOT / "simulations" / "replay-1119" / "2026-10-12T152010.json"


class _WallClockMeta(type(datetime)):
    def __instancecheck__(cls, obj: object) -> bool:
        return isinstance(obj, datetime)


def _wall_clock(at: datetime) -> type:
    """A ``datetime`` whose ``now()`` is ``at``.

    The facade picks the plan slot to apply by ``datetime.now(UTC)``, not by
    HA's clock, so the replayed instant has to be the wall clock too.
    """

    class _Frozen(datetime, metaclass=_WallClockMeta):
        @classmethod
        def now(cls, tz=None):
            return at.astimezone(tz) if tz is not None else at

    return _Frozen


class _Rig:
    """Real state machine + real computation engine on one captured instant."""

    def __init__(self) -> None:
        sys.path.insert(0, str(REPO_ROOT / "scripts"))
        from replay_no_dw import LIVE_OVERRIDES, LIVE_SWITCHES, _seed_horizon

        from custom_components.localshift.computation_engine import ComputationEngine
        from custom_components.localshift.utils.validation import IntegrationStatus
        from tests.test_scenarios import (
            create_mock_entry,
            create_mock_get_entity_id,
            create_mock_get_switch_state,
            setup_coordinator_data,
            setup_mock_hass,
        )

        scenario = json.loads(CAPTURE.read_text())
        self.payload = copy.deepcopy(scenario["input"])
        self.payload["adaptive_params"] = {}
        self.now = datetime.fromisoformat(self.payload["test_time"])

        self.data = setup_coordinator_data(self.payload)
        # Not exporting, so the unconfirmed price is a START, which is gated.
        self.data.active_mode = BatteryMode.SELF_CONSUMPTION
        self.data.automation_ready = True
        self.data.manual_override = False
        self.data.decision_log = []
        _seed_horizon(self.data, self.payload)

        overrides = dict(scenario.get("config_overrides", {}))
        overrides.update(LIVE_OVERRIDES)
        switches = dict(scenario.get("switch_states", {}))
        switches.update(LIVE_SWITCHES)
        self.engine = ComputationEngine(
            setup_mock_hass(self.payload),
            create_mock_entry(overrides),
            create_mock_get_entity_id(),
            create_mock_get_switch_state(switches),
        )

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
        self.machine = StateMachine(
            self.controller,
            notifications,
            lambda key: {"automation_enabled": True, "dry_run": False}.get(key, False),
            lambda key, default=None: default,
            validator,
        )
        self.granted: list[bool] = []

    def set_current(
        self, estimate: bool, sell: float | None = None, buy: float | None = None
    ) -> None:
        """Rewrite the entries covering now, as an Amber update would."""
        for forecast, sensor, price in (
            (self.data.feed_in_forecast, "feed_in_price", sell),
            (self.data.general_forecast, "general_price", buy),
        ):
            changes: dict = {"estimate": estimate}
            if price is not None:
                changes["per_kwh"] = price
                setattr(self.data, sensor, price)
            forecast[0] = dataclasses.replace(forecast[0], **changes)

    def confirm(self, sell: float | None = None, buy: float | None = None) -> None:
        """Amber confirms the current interval, optionally at a new price."""
        self.set_current(False, sell, buy)

    def tick(self, seconds: int) -> None:
        """One state-machine evaluation ``seconds`` after the captured instant."""
        engine = self.engine
        history = engine._history_fetcher
        real_compute = engine.compute_derived_values

        def compute(data):
            self.granted.append(data.mode_decision_allowed)
            real_compute(data)

        at = self.now + timedelta(seconds=seconds)
        with ExitStack() as stack:
            for p in (
                patch("homeassistant.util.dt.now", return_value=at),
                patch(
                    "custom_components.localshift.engine.optimizer_runner.datetime",
                    _wall_clock(at),
                ),
                patch.object(engine, "compute_derived_values", compute),
                patch.object(
                    engine, "_get_historical_hourly_averages", return_value={}
                ),
                patch.object(history, "_historical_load_cache", {}),
                patch.object(history, "_historical_load_sample_counts", {}),
                patch.object(history, "_historical_load_source", "none"),
                patch.object(
                    history,
                    "_recent_load_1hr_kw",
                    self.payload.get("load_power_kw", 0.5),
                ),
            ):
                stack.enter_context(p)
            asyncio.run(self.machine.evaluate_state_machine(self.data, engine))

    def spend_token_in_grace(self) -> None:
        """Land a decision inside the grace, while the price is unconfirmed.

        The first evaluation always decides; the second decides again because
        ``demand_window_active`` is refreshed by the first (15:20 is inside the
        demand window) — the documented one-tick lag, and exactly the kind of
        non-price token this is about. From here the context is settled.
        """
        self.tick(0)
        self.tick(5)
        assert self.granted == [True, True]
        self.granted.clear()

    @property
    def slot0(self) -> dict:
        return self.data.optimizer_summary["slot0_price"]

    @property
    def plan_action(self) -> str:
        return self.data.optimizer_decisions[0]["action"]


@pytest.fixture
def rig() -> _Rig:
    return _Rig()


class TestDeferredExportStartIsReDecidedOnConfirmation:
    def test_token_inside_the_grace_commits_a_hold_not_an_export(self, rig):
        rig.spend_token_in_grace()

        assert rig.slot0["sell_estimate"] is True
        assert rig.slot0["export_start_deferred"] is True
        assert rig.data.active_mode != BatteryMode.PROACTIVE_EXPORT

    def test_confirmation_at_the_same_price_starts_the_export(self, rig):
        """The defect: no price moved, so no token, so the export never started."""
        rig.spend_token_in_grace()
        rig.confirm()  # same price, now confirmed; the sensor states do not move
        for seconds in (15, 30, 45):
            rig.tick(seconds)

        assert rig.slot0["sell_estimate"] is False
        assert rig.plan_action == "export_proactive"
        assert rig.data.active_mode == BatteryMode.PROACTIVE_EXPORT
        rig.controller.set_proactive_export.assert_not_awaited()  # debouncing

        rig.tick(170)  # past the 2-minute export debounce

        rig.controller.set_proactive_export.assert_awaited_once()

    def test_estimate_that_never_confirms_starts_the_export_after_the_grace(self, rig):
        """Past the grace the estimate is acted on; that too needs a decision."""
        rig.spend_token_in_grace()
        for seconds in (30, 60, 95, 110, 125):
            rig.tick(seconds)

        assert rig.slot0["sell_estimate"] is True
        assert rig.slot0["export_start_deferred"] is False
        assert rig.data.active_mode == BatteryMode.PROACTIVE_EXPORT

    def test_exactly_one_extra_decision_is_granted(self, rig):
        """One-shot: the confirmation grants once, not on every later tick."""
        rig.spend_token_in_grace()
        rig.confirm()
        for seconds in (15, 30, 45, 60, 75, 90):
            rig.tick(seconds)

        assert rig.granted.count(True) == 1

    def test_confirmation_at_a_new_price_needs_no_extra_decision(self, rig):
        """The ordinary case: the price moves, the price token re-decides."""
        rig.spend_token_in_grace()
        # The real confirmed 15:20 prices, 13 s later.
        rig.confirm(sell=0.71, buy=0.80)
        for seconds in (15, 30, 45, 60):
            rig.tick(seconds)

        assert rig.granted == [True, False, False, False]
        assert rig.data.active_mode != BatteryMode.PROACTIVE_EXPORT

    def test_no_extra_decision_when_no_token_was_spent_in_the_grace(self, rig):
        """A deferral nobody acted on owes nothing: frozen ticks stay frozen."""
        rig.spend_token_in_grace()
        rig.confirm(sell=0.71, buy=0.80)
        for seconds in (15, 30):
            rig.tick(seconds)
        assert rig.data.active_mode != BatteryMode.PROACTIVE_EXPORT

        # 15:25 rolls over unconfirmed, carrying 15:20's price, then confirms at
        # that same price. The plan is deferred again, but no token lands inside
        # this grace, so the confirmation must not mint one.
        rig.granted.clear()
        for forecast in (rig.data.feed_in_forecast, rig.data.general_forecast):
            del forecast[0]
        rig.set_current(True, sell=0.71, buy=0.80)
        for seconds in (305, 320):
            rig.tick(seconds)
        assert rig.slot0["sell_estimate"] is True
        rig.confirm()
        for seconds in (335, 350, 365):
            rig.tick(seconds)

        assert rig.granted == [False] * 5


class TestExportStartGrantIsOneDirectional:
    """The extra token exists to start the deferred export and nothing else."""

    def test_grant_is_flagged_export_start_only(self, rig):
        seen: list[tuple[bool, bool, bool]] = []
        real = rig.engine.compute_derived_values

        def spy(data):
            seen.append((
                data.mode_decision_allowed,
                data.mode_decision_export_start_only,
                data.mode_decision_plan_charge_only,
            ))
            real(data)

        rig.spend_token_in_grace()
        rig.engine.compute_derived_values = spy
        rig.confirm()
        for seconds in (15, 30, 45):
            rig.tick(seconds)

        assert [flags for flags in seen if flags[0]] == [(True, True, False)]
        assert rig.data.mode_decision_export_start_only is False  # transient
        assert rig.machine._last_grant_source == "price_confirmed"

    def test_grant_does_not_commit_any_other_mode(self, rig):
        """If the fresh plan no longer wants the export, the grant is not spent."""
        from custom_components.localshift.engine import optimizer_facade

        rig.spend_token_in_grace()
        committed = rig.data.active_mode
        rig.confirm()
        other = next(
            m for m in (BatteryMode.SPIKE_DISCHARGE, BatteryMode.HOLD) if m != committed
        )
        real = optimizer_facade._derive_runtime_apply_plan

        def drifted(*args, **kwargs):
            plan = dict(real(*args, **kwargs))
            plan["battery_mode"] = other.value
            return plan

        with patch.object(optimizer_facade, "_derive_runtime_apply_plan", drifted):
            for seconds in (15, 30, 45):
                rig.tick(seconds)

        assert rig.granted.count(True) == 1
        assert rig.data.active_mode == committed
