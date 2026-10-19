"""Issue #1159: no charge START in the price-boundary gap.

Backtest (16 days, 36 charge starts): every sub-minute boost started in the
gap around a 5-minute price boundary, either in the last ~30 s of an interval
or after the price sensor rolled to the next interval with ``estimate = true``
but before the confirmed price landed. The release that followed seconds later
was a correct reaction to the confirmed price.

So a charge mode is not committed FROM a non-charge mode while the price
sensor reports an estimate (up to a cap after the boundary) or in the last
seconds before a boundary. Releases, charge-to-charge switches, spike /
demand-window / SOC-floor grants and the backstops are not gated.

The deferral must not spend the decision token: Amber's confirmation can land
at the estimate's price to 4 dp, which moves nothing in the decision context,
so a spent token would leave the start waiting for some later price change.
The facade reports an unspent grant and the state machine hands it back, so
the first evaluation after the gap clears grants again.

These tests drive the real ``StateMachine.evaluate_state_machine`` and the
real ``OptimizerFacade._assign_active_mode`` commit path; only the plan's
slot-0 mode, the safety gate and the backstops are scripted.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.util import dt as dt_util

from custom_components.localshift import const
from custom_components.localshift.const import BatteryMode
from custom_components.localshift.coordinator import CoordinatorData
from custom_components.localshift.engine.optimizer_facade import OptimizerFacade
from custom_components.localshift.engine.types import OptimizerConfig
from custom_components.localshift.state.machine import StateMachine

SELF = BatteryMode.SELF_CONSUMPTION
BOOST = BatteryMode.BOOST_CHARGING
GRID = BatteryMode.GRID_CHARGING
_CHARGE = (GRID, BOOST)

# A 5-minute price boundary. Every event below is an offset from it in seconds.
BOUNDARY = datetime(2026, 10, 16, tzinfo=UTC)
_FACADE = "custom_components.localshift.engine.optimizer_facade"


def _at(offset_s: float) -> datetime:
    return BOUNDARY + timedelta(seconds=offset_s)


class FacadeEngine:
    """compute_derived_values → the real facade commit path, plan scripted."""

    def __init__(self) -> None:
        self.facade = OptimizerFacade()
        self.plan_mode = SELF
        self.backstop: BatteryMode | None = None

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
                OptimizerFacade, "_precharge_backstop_mode", return_value=self.backstop
            ),
            patch.object(OptimizerFacade, "_runway_backstop_mode", return_value=None),
        ):
            gate.return_value.check_admission.return_value = SimpleNamespace(
                allowed=True, block_reason=None
            )
            self.facade._assign_active_mode(
                data, SimpleNamespace(), OptimizerConfig(), {}
            )


@pytest.fixture
def state_machine() -> StateMachine:
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
        lambda key: {"automation_enabled": True, "dry_run": False}.get(key, False),
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
    price: float | None = None,
    estimate: bool | None = False,
) -> BatteryMode:
    """One in-lock evaluation at BOUNDARY + offset_s; returns the committed mode."""
    if price is not None:
        data.general_price = price
    data.price_estimate = estimate
    engine.plan_mode = plan
    with patch.object(dt_util, "now", return_value=_at(offset_s)):
        asyncio.run(sm.evaluate_state_machine(data, engine))
    return data.active_mode


def _settle(sm, data, engine, mode: BatteryMode = SELF) -> None:
    """Consume a token mid-interval so the next one needs a real change."""
    _tick(sm, data, engine, -200, mode, price=0.20)
    assert data.active_mode == mode


def test_thresholds_are_the_approved_ones() -> None:
    assert getattr(const, "CHARGE_START_GATE_PRE_BOUNDARY_S", None) == 30
    assert getattr(const, "CHARGE_START_GATE_MAX_WAIT_S", None) == 60


class TestStartIsDeferredNotSpent:
    def test_estimate_defers_then_commits_on_equal_confirmed_price(
        self, state_machine, data
    ) -> None:
        """Confirmed at the estimate's price: no context change, still commits."""
        engine = FacadeEngine()
        _settle(state_machine, data, engine)

        mode = _tick(state_machine, data, engine, 2, BOOST, price=0.15, estimate=True)
        assert mode == SELF
        assert data.debug_plan_mode_pending == BOOST.value

        mode = _tick(state_machine, data, engine, 24, BOOST, price=0.15, estimate=False)
        assert mode == BOOST
        bucket = data.boundary_lag_history.get("charge_start_gate", [])
        assert [entry["to_mode"] for entry in bucket] == [BOOST.value]

    def test_commits_on_the_next_tick_without_any_price_event(
        self, state_machine, data
    ) -> None:
        """The 1-minute tick retries: no price or estimate edge needed."""
        engine = FacadeEngine()
        _settle(state_machine, data, engine)

        assert _tick(state_machine, data, engine, 2, BOOST, 0.15, True) == SELF
        assert _tick(state_machine, data, engine, 62, BOOST, 0.15, False) == BOOST

    def test_last_seconds_before_boundary_defer(self, state_machine, data) -> None:
        engine = FacadeEngine()
        _settle(state_machine, data, engine)

        assert _tick(state_machine, data, engine, -20, BOOST, 0.15, False) == SELF
        assert _tick(state_machine, data, engine, -1, BOOST, 0.15, True) == SELF
        assert _tick(state_machine, data, engine, 22, BOOST, 0.15, False) == BOOST

    def test_cap_commits_on_a_late_estimate(self, state_machine, data) -> None:
        engine = FacadeEngine()
        _settle(state_machine, data, engine)

        assert _tick(state_machine, data, engine, 3, BOOST, 0.15, True) == SELF
        assert _tick(state_machine, data, engine, 45, BOOST, 0.15, True) == SELF
        assert _tick(state_machine, data, engine, 61, BOOST, 0.15, True) == BOOST

    def test_plan_charge_epoch_grant_is_deferred_and_retried(
        self, state_machine, data
    ) -> None:
        """A grant raised by the plan alone (no price move) is handed back too."""
        engine = FacadeEngine()
        _settle(state_machine, data, engine)
        # Frozen tick: the plan wants a charge, which arms the plan-charge epoch.
        assert _tick(state_machine, data, engine, -100, BOOST) == SELF
        assert data.debug_plan_mode_pending == BOOST.value

        assert _tick(state_machine, data, engine, -25, BOOST) == SELF
        assert _tick(state_machine, data, engine, 5, BOOST, estimate=True) == SELF
        assert _tick(state_machine, data, engine, 35, BOOST) == BOOST

    def test_mid_interval_start_is_not_delayed(self, state_machine, data) -> None:
        engine = FacadeEngine()
        _settle(state_machine, data, engine)

        assert _tick(state_machine, data, engine, 150, GRID, 0.15, False) == GRID


class TestNotGated:
    @pytest.fixture
    def charging(self, state_machine, data):
        def _start(mode: BatteryMode) -> FacadeEngine:
            engine = FacadeEngine()
            _settle(state_machine, data, engine, mode)
            return engine

        return _start

    def test_release_during_estimate_is_immediate(
        self, state_machine, data, charging
    ) -> None:
        engine = charging(BOOST)
        assert _tick(state_machine, data, engine, 4, SELF, 0.30, True) == SELF

    def test_release_in_last_seconds_is_immediate(
        self, state_machine, data, charging
    ) -> None:
        engine = charging(GRID)
        assert _tick(state_machine, data, engine, -10, SELF, 0.30, False) == SELF

    def test_charge_to_charge_during_estimate_is_immediate(
        self, state_machine, data, charging
    ) -> None:
        engine = charging(GRID)
        assert _tick(state_machine, data, engine, 4, BOOST, 0.12, True) == BOOST

    def test_backstop_force_is_not_gated(self, state_machine, data) -> None:
        engine = FacadeEngine()
        _settle(state_machine, data, engine)
        engine.backstop = BOOST
        assert _tick(state_machine, data, engine, 4, SELF, estimate=True) == BOOST

    @pytest.mark.parametrize("trigger", ["spike", "demand_window", "soc_floor"])
    def test_non_price_grants_are_not_gated(self, state_machine, data, trigger) -> None:
        engine = FacadeEngine()
        _settle(state_machine, data, engine)
        if trigger == "spike":
            data.price_spike = True
        elif trigger == "demand_window":
            data.demand_window_active = True
        else:
            data.soc = 5.0
        assert _tick(state_machine, data, engine, 4, GRID, 0.15, True) == GRID

    @pytest.mark.parametrize("offset_s", [-10, 4, 40])
    def test_source_without_estimate_attribute_is_unaffected(
        self, state_machine, data, offset_s
    ) -> None:
        engine = FacadeEngine()
        _settle(state_machine, data, engine)
        assert _tick(state_machine, data, engine, offset_s, BOOST, 0.15, None) == BOOST


# Replays of the backtest's sub-minute boosts, as (offset s, price, estimate,
# slot-0 plan). On main each commits a boost in the gap and releases it seconds
# later on the confirmed price.
SHAPE_13_OCT = [
    (-19, 0.338, False, BOOST),
    (14, 0.338, True, BOOST),
    (22, 0.409, False, SELF),
    (37, 0.409, False, SELF),
    (97, 0.409, False, SELF),
]
SHAPE_16_OCT = [
    (3, 0.149, True, BOOST),
    (30, 0.171, False, SELF),
    (90, 0.171, False, SELF),
]
SHAPE_12_OCT = [
    (5, 0.150, True, BOOST),
    (28, 0.181, False, SELF),
    (305, 0.152, True, BOOST),
    (331, 0.183, False, SELF),
    (391, 0.183, False, SELF),
]


@pytest.mark.parametrize(
    "shape",
    [SHAPE_12_OCT, SHAPE_13_OCT, SHAPE_16_OCT],
    ids=["12-oct", "13-oct", "16-oct"],
)
def test_backtest_shapes_do_not_flap(state_machine, data, shape) -> None:
    engine = FacadeEngine()
    _settle(state_machine, data, engine)

    committed = [
        _tick(state_machine, data, engine, offset, plan, price, estimate)
        for offset, price, estimate, plan in shape
    ]

    assert not [mode for mode in committed if mode in _CHARGE]


class TestDeferralBookkeeping:
    """Review follow-ups on #1226: logging, retry tag, telemetry tag, source."""

    def test_deferral_logs_info_once_per_gap(self, state_machine, data, caplog):
        engine = FacadeEngine()
        _settle(state_machine, data, engine)
        caplog.set_level("DEBUG", logger="custom_components.localshift")

        def deferrals(level: str) -> int:
            return sum(
                1
                for r in caplog.records
                if r.levelname == level and "Charge start deferred" in r.message
            )

        _tick(state_machine, data, engine, -20, BOOST, 0.15, False)
        _tick(state_machine, data, engine, 2, BOOST, 0.15, True)
        assert (deferrals("INFO"), deferrals("DEBUG")) == (1, 1)

        _tick(state_machine, data, engine, 30, SELF, 0.30, False)
        _tick(state_machine, data, engine, 302, BOOST, 0.15, True)
        assert (deferrals("INFO"), deferrals("DEBUG")) == (2, 1)

    def test_deferral_shows_on_the_mode_source(self, state_machine, data) -> None:
        engine = FacadeEngine()
        _settle(state_machine, data, engine)

        _tick(state_machine, data, engine, 2, BOOST, 0.15, True)
        assert data.debug_mode_source == "charge_start_gate"
        _tick(state_machine, data, engine, 24, BOOST, 0.15, False)
        assert data.debug_mode_source == "optimizer"

    def test_deferred_grant_keeps_the_retry_tag(self, state_machine, data) -> None:
        engine = FacadeEngine()
        _settle(state_machine, data, engine)
        state_machine._pending_retry_mode = GRID
        seen = []

        async def core(data, engine) -> None:
            # What the transition logic sees once the token is handed back.
            seen.append(state_machine._pending_retry_mode)

        with patch.object(state_machine, "_evaluate_core", core):
            _tick(state_machine, data, engine, 2, BOOST, 0.15, True)

        assert seen == [GRID]

    def test_tag_does_not_outlive_a_retry_that_never_came(
        self, state_machine, data
    ) -> None:
        """Price A, B (deferred), back to A: no grant, so the tag is dropped."""
        engine = FacadeEngine()
        _settle(state_machine, data, engine)

        assert _tick(state_machine, data, engine, 2, BOOST, 0.15, True) == SELF
        # At target, so the pending charge raises no plan-charge grant either.
        data.soc = 100.0
        assert _tick(state_machine, data, engine, 25, SELF, 0.20, False) == SELF
        data.soc = 50.0
        assert _tick(state_machine, data, engine, 150, GRID, 0.12, False) == GRID

        assert "charge_start_gate" not in data.boundary_lag_history
        assert [e["to_mode"] for e in data.boundary_lag_history["price"]] == [
            GRID.value
        ]

    def test_invalidation_drops_the_tag(self, state_machine, data) -> None:
        engine = FacadeEngine()
        _settle(state_machine, data, engine)

        assert _tick(state_machine, data, engine, 2, BOOST, 0.15, True) == SELF
        state_machine.invalidate_decision_fingerprint("test")
        assert _tick(state_machine, data, engine, 150, GRID, 0.15, False) == GRID

        assert "charge_start_gate" not in data.boundary_lag_history
