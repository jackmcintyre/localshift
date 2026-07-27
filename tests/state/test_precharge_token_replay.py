"""Replay of the 2026-07-27 pre-charge execution failure (FIX 1: token starvation).

Incident: demand window 15:00-21:00, battery target 95%, entered at 64.0% —
31.0 points short. Prices were 0.13-0.16 all morning and dipped to 0.115 at
14:35 against a max_pre_charge_price of 0.20, so price was never the blocker.
The DP plan said ``first_charge=NOW`` at 09:40, 10:00, 10:30, 10:40, 10:50,
11:00, 11:10, 11:20, 12:40 and 12:50 and the mode never went to Grid Charging;
the only DP-commanded charge of the day was 13:41-14:16. The pre-charge log
from 13:56 to 14:50 shows ``first_charge`` sliding one slot forward on every
replan while the projected shortfall climbed 4.9 → 31.5.

Root cause: ``_get_decision_fingerprint`` enumerated price/spike/DW/floor as
the only legitimate re-decision triggers, assuming the plan is stable between
them. It is not — the DP's slot-0 action oscillates hold↔charge on every
replan because ``first_charge`` chases the cheapest slot forward. The token
therefore sampled the plan on a ~5-min (Amber tick) stride, and nearly every
sample landed on a hold-phase plan. ``debug_plan_mode_pending`` recorded the
wanted charge dozens of times and never committed. A counterfactual replay
that commits the wanted mode reaches 94.9% instead of 64%.

FIX 1 folds the plan into the decision context: the plan wanting to START
charging while frozen advances a monotone epoch, which is the 6th fingerprint
component. The trigger is one-directional (a wanted STOP never grants) and the
epoch never decrements, which together bound the flap at one extra decision per
(price context × new wanted-charge mode).
"""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.localshift.const import BatteryMode
from custom_components.localshift.state.machine import StateMachine

_CHARGE_MODES = (BatteryMode.GRID_CHARGING, BatteryMode.BOOST_CHARGING)


def dt_aware(year, month, day, hour, minute=0, second=0):
    """Create a timezone-aware datetime in Australia/Sydney time."""
    return datetime(
        year, month, day, hour, minute, second, tzinfo=timezone(timedelta(hours=11))
    )


# =============================================================================
# FIXTURES (local copies — tests/state has no conftest of its own)
# =============================================================================


@pytest.fixture
def mock_battery_controller():
    """Mock BatteryController with all commands succeeding."""
    controller = MagicMock()
    controller.set_self_consumption = AsyncMock(return_value=True)
    controller.set_force_charge = AsyncMock(return_value=True)
    controller.set_boost_charge = AsyncMock(return_value=True)
    controller.set_force_discharge = AsyncMock(return_value=True)
    controller.set_proactive_export = AsyncMock(return_value=True)
    controller.verify_current_state = AsyncMock(return_value=True)
    return controller


@pytest.fixture
def mock_notification_service():
    """Mock NotificationService."""
    service = MagicMock()
    service.send_transition_notification = AsyncMock()
    service.send_transition_failed_notification = AsyncMock()
    service.send_health_correction_notification = AsyncMock()
    service.send_manual_override_timeout_notification = AsyncMock()
    service.send_automation_disabled_notification = AsyncMock()
    service.send_tesla_override_notification = AsyncMock()
    return service


@pytest.fixture
def mock_entity_validator():
    """Mock EntityValidator that always allows automation."""
    from custom_components.localshift.utils.validation import IntegrationStatus

    validator = MagicMock()
    validator.should_allow_automation = MagicMock(return_value=True)
    validator.status = IntegrationStatus.OK
    validator.errors = []
    validator.warnings = []
    return validator


@pytest.fixture
def state_machine(
    mock_battery_controller, mock_notification_service, mock_entity_validator
):
    """StateMachine with live-like options (battery_target defaults to 100)."""
    return StateMachine(
        mock_battery_controller,
        mock_notification_service,
        lambda key: {"automation_enabled": True, "dry_run": False}.get(key, False),
        lambda key, default=None: default,
        mock_entity_validator,
    )


@pytest.fixture
def coordinator_data():
    """CoordinatorData seeded with the incident's pre-DW state."""
    from custom_components.localshift.coordinator import CoordinatorData

    data = CoordinatorData()
    data.soc = 49.9
    data.operation_mode = "self_consumption"
    data.backup_reserve = 10
    data.general_price = 0.131
    data.feed_in_price = 0.08
    data.price_spike = False
    data.demand_window_active = False
    data.manual_override = False
    data.automation_ready = True
    data.active_mode = BatteryMode.SELF_CONSUMPTION
    data.decision_log = []
    return data


class FakeFacadeEngine:
    """Computation-engine stub mimicking the optimizer-facade contract.

    Real flow: compute_derived_values() → facade._assign_active_mode() pins
    data.active_mode when data.mode_decision_allowed is False (surfacing the
    would-be plan mode on debug_plan_mode_pending) and re-decides it only when
    True. ``plan_mode`` is re-assigned by the replay loop each tick, which is
    how the per-minute plan script is driven.
    """

    def __init__(self, plan_mode: BatteryMode) -> None:
        self.plan_mode = plan_mode
        # data.mode_decision_allowed as observed in-lock on the LAST call (the
        # flag is transient and already False once evaluate returns).
        self.last_decision_allowed: bool | None = None

    def compute_derived_values(self, data) -> None:
        self.last_decision_allowed = data.mode_decision_allowed
        if data.mode_decision_allowed:
            data.active_mode = self.plan_mode
            data.debug_plan_mode_pending = None
        else:
            data.debug_plan_mode_pending = (
                self.plan_mode.value if self.plan_mode != data.active_mode else None
            )


class TestPlanChangeTokenReplay:
    """FIX 1 — the plan is a decision input; a wanted charge must not starve."""

    # --- Live trace: 13:40-15:00, one entry per simulated minute -------------
    # Amber publishes on a 5-minute stride, so the price is constant within each
    # group of five. Anchors from the evidence pack: 14:35 0.115, 14:40 0.113,
    # 14:45/14:50 0.116. max_pre_charge_price was 0.20 — price never blocked.
    PRICE_GROUPS = [
        0.131,  # 13:40
        0.128,  # 13:45
        0.134,  # 13:50
        0.130,  # 13:55
        0.127,  # 14:00
        0.133,  # 14:05
        0.129,  # 14:10
        0.126,  # 14:15
        0.124,  # 14:20
        0.122,  # 14:25
        0.119,  # 14:30
        0.115,  # 14:35
        0.113,  # 14:40
        0.116,  # 14:45
        0.116,  # 14:50 (unchanged → no price-driven context change)
        0.116,  # 14:55 (unchanged)
    ]
    PRICES = [p for p in PRICE_GROUPS for _ in range(5)]

    # The incident's exact pathology: the DP's slot-0 action is "hold" at every
    # 5-minute boundary (the instant the price tick grants a token) and "charge"
    # on the four intervening minutes. The pre-fix token samples the plan only on
    # the boundaries, so it never sees the wanted charge.
    PLAN_SLOT0 = [
        BatteryMode.SELF_CONSUMPTION if i % 5 == 0 else BatteryMode.GRID_CHARGING
        for i in range(len(PRICES))
    ]

    # Live SOC (sensor.my_home_percentage_charged), linearly interpolated between
    # the recorded anchors. Never near the 95% target, so the sanity gate is open
    # for the whole replay.
    SOC_ANCHORS = {0: 49.9, 20: 57.0, 40: 62.6, 70: 60.3, 79: 60.3}

    @classmethod
    def _soc(cls, minute: int) -> float:
        keys = sorted(cls.SOC_ANCHORS)
        lo = max(k for k in keys if k <= minute)
        hi = min((k for k in keys if k >= minute), default=lo)
        if hi == lo:
            return cls.SOC_ANCHORS[lo]
        span = (minute - lo) / (hi - lo)
        return cls.SOC_ANCHORS[lo] + span * (cls.SOC_ANCHORS[hi] - cls.SOC_ANCHORS[lo])

    def _replay(self, state_machine, data, engine, minutes=None):
        """Drive the per-minute replay; return the per-tick committed modes."""
        committed = []
        count = len(self.PRICES) if minutes is None else minutes
        for i in range(count):
            data.general_price = self.PRICES[i]
            data.soc = self._soc(i)
            engine.plan_mode = self.PLAN_SLOT0[i]
            asyncio.run(state_machine.evaluate_state_machine(data, engine))
            committed.append(data.active_mode)
        return committed

    # --- RED anchor ---------------------------------------------------------

    def test_prefix_baseline_no_charge_committed_without_plan_trigger(
        self, state_machine, coordinator_data
    ):
        """Pre-fix behaviour (epoch frozen): the wanted charge never commits.

        This is the incident. Without it the fix test below would be vacuous.
        """
        state_machine._update_plan_charge_epoch = lambda data, context=None: None
        engine = FakeFacadeEngine(BatteryMode.SELF_CONSUMPTION)

        committed = self._replay(state_machine, coordinator_data, engine)

        assert not [m for m in committed if m in _CHARGE_MODES]

    # --- The fix ------------------------------------------------------------

    def test_plan_wanted_charge_is_committed(self, state_machine, coordinator_data):
        """The plan-change trigger commits the charge the plan kept asking for."""
        engine = FakeFacadeEngine(BatteryMode.SELF_CONSUMPTION)

        committed = self._replay(state_machine, coordinator_data, engine)

        charge_ticks = [i for i, m in enumerate(committed) if m in _CHARGE_MODES]
        wanted_ticks = [i for i, m in enumerate(self.PLAN_SLOT0) if m in _CHARGE_MODES]

        # (a) the charge commits at all
        assert charge_ticks
        # (b) it commits within 2 evaluations of the first tick that wanted it
        #     (the pending value carries a 1-tick lag by design)
        assert charge_ticks[0] - wanted_ticks[0] <= 2
        # (c) and it stays committed for the bulk of the wanted window rather
        #     than winning a single tick
        assert len(charge_ticks) >= 0.6 * len(wanted_ticks)

    def test_no_flap_within_a_price_interval(self, state_machine, coordinator_data):
        """The §1.8 bound: no charge→hold→charge inside one price context."""
        engine = FakeFacadeEngine(BatteryMode.SELF_CONSUMPTION)

        transitions = []
        original = state_machine._execute_mode_transition

        async def _counting(data, target):
            result = await original(data, target)
            if result:
                transitions.append(target)
            return result

        state_machine._execute_mode_transition = _counting

        committed = self._replay(state_machine, coordinator_data, engine)

        # No charge → non-charge → charge cycle inside a single 5-minute group.
        for group in range(len(self.PRICE_GROUPS)):
            window = committed[group * 5 : group * 5 + 5]
            seen_charge = seen_release = False
            for mode in window:
                if mode in _CHARGE_MODES:
                    assert not seen_release, (
                        f"charge→hold→charge flap inside price group {group}: {window}"
                    )
                    seen_charge = True
                elif seen_charge:
                    seen_release = True

        distinct_price_contexts = len([
            i
            for i, p in enumerate(self.PRICE_GROUPS)
            if i == 0 or p != self.PRICE_GROUPS[i - 1]
        ])
        rising_edges = state_machine._plan_charge_epoch
        assert len(transitions) <= distinct_price_contexts + rising_edges

    # --- Guards on the trigger's shape --------------------------------------

    def test_wanted_stop_never_grants_a_token(self, state_machine, coordinator_data):
        """One-directional: a plan that wants to STOP charging never grants."""
        engine = FakeFacadeEngine(BatteryMode.GRID_CHARGING)

        # Prime: one granted decision commits GRID_CHARGING.
        asyncio.run(state_machine.evaluate_state_machine(coordinator_data, engine))
        assert coordinator_data.active_mode == BatteryMode.GRID_CHARGING

        # The plan now wants to hold, on an otherwise unchanged context.
        engine.plan_mode = BatteryMode.SELF_CONSUMPTION
        for _ in range(10):
            asyncio.run(state_machine.evaluate_state_machine(coordinator_data, engine))
            assert engine.last_decision_allowed is False
            assert coordinator_data.mode_decision_allowed is False
            assert (
                coordinator_data.debug_plan_mode_pending
                == BatteryMode.SELF_CONSUMPTION.value
            )

        assert coordinator_data.active_mode == BatteryMode.GRID_CHARGING
        assert state_machine._commanded_mode == BatteryMode.GRID_CHARGING

    def test_epoch_does_not_increment_on_resolution(
        self, state_machine, coordinator_data
    ):
        """Monotonicity: the disagreement clearing must not grant a second token."""
        engine = FakeFacadeEngine(BatteryMode.SELF_CONSUMPTION)

        # Tick 1: establish the context (plan == current, nothing pending).
        asyncio.run(state_machine.evaluate_state_machine(coordinator_data, engine))
        assert state_machine._plan_charge_epoch == 0

        # Tick 2: plan wants to charge on an unchanged context → frozen, pending.
        engine.plan_mode = BatteryMode.GRID_CHARGING
        asyncio.run(state_machine.evaluate_state_machine(coordinator_data, engine))
        assert engine.last_decision_allowed is False
        assert (
            coordinator_data.debug_plan_mode_pending == BatteryMode.GRID_CHARGING.value
        )
        assert state_machine._plan_charge_epoch == 0

        # Tick 3: the rising edge grants exactly one decision; the plan commits.
        asyncio.run(state_machine.evaluate_state_machine(coordinator_data, engine))
        assert engine.last_decision_allowed is True
        assert coordinator_data.active_mode == BatteryMode.GRID_CHARGING
        assert state_machine._plan_charge_epoch == 1
        assert coordinator_data.debug_plan_mode_pending is None

        # Tick 4: the disagreement is resolved — the epoch must NOT move again.
        asyncio.run(state_machine.evaluate_state_machine(coordinator_data, engine))
        assert state_machine._plan_charge_epoch == 1
        assert engine.last_decision_allowed is False

    def test_no_grant_at_or_above_battery_target(self, state_machine, coordinator_data):
        """Sanity gate: nothing to grant when the battery is already at target."""
        # get_option returns the default → DEFAULT_BATTERY_TARGET (100).
        coordinator_data.soc = 100.0
        engine = FakeFacadeEngine(BatteryMode.SELF_CONSUMPTION)
        asyncio.run(state_machine.evaluate_state_machine(coordinator_data, engine))

        engine.plan_mode = BatteryMode.GRID_CHARGING
        for _ in range(3):
            asyncio.run(state_machine.evaluate_state_machine(coordinator_data, engine))
            assert engine.last_decision_allowed is False

        assert state_machine._plan_charge_epoch == 0
        assert coordinator_data.active_mode == BatteryMode.SELF_CONSUMPTION

    def test_deferred_redemption_still_dead(
        self, state_machine, coordinator_data, mock_battery_controller
    ):
        """#622 stays fixed: a non-charge plan flip on a stale token never runs."""
        engine = FakeFacadeEngine(BatteryMode.SELF_CONSUMPTION)

        # Tick 1: price change, plan stable → token spent, no transition.
        asyncio.run(state_machine.evaluate_state_machine(coordinator_data, engine))
        mock_battery_controller.set_force_discharge.reset_mock()

        # Ticks 2-3: SAME price, plan flips to a NON-charge mode. Under the old
        # gate the stale token was redeemed here; the plan-change trigger is
        # one-directional so it stays frozen.
        engine.plan_mode = BatteryMode.SPIKE_DISCHARGE
        for _ in range(2):
            asyncio.run(state_machine.evaluate_state_machine(coordinator_data, engine))
            assert engine.last_decision_allowed is False

        assert coordinator_data.active_mode == BatteryMode.SELF_CONSUMPTION
        assert state_machine._plan_charge_epoch == 0
        mock_battery_controller.set_force_discharge.assert_not_called()

    def test_backstop_permission_is_confined_to_the_lock(
        self, state_machine, coordinator_data
    ):
        """mode_backstop_allowed is opened in-lock and always closed on exit."""
        observed = []

        class _Observer(FakeFacadeEngine):
            def compute_derived_values(self, data):
                observed.append(data.mode_backstop_allowed)
                super().compute_derived_values(data)

        engine = _Observer(BatteryMode.SELF_CONSUMPTION)
        asyncio.run(state_machine.evaluate_state_machine(coordinator_data, engine))

        assert observed == [True]
        assert coordinator_data.mode_backstop_allowed is False


class _DriftingFacade:
    """The incident's defining case, which no test in the original diff covered.

    Frozen ticks see a plan that wants to charge; the evaluation that is actually
    GRANTED re-plans from fresh data and its slot-0 says hold — ``first_charge``
    drifting forward, exactly what ``_update_plan_charge_epoch``'s docstring
    describes. The committed mode therefore never enters ``_CHARGE_MODES``, so the
    "committed mode is not already charging" gate never closes the loop.
    """

    def __init__(self, granted_mode=BatteryMode.SELF_CONSUMPTION):
        self.granted_mode = granted_mode
        self.grants = 0

    def compute_derived_values(self, data):
        if data.mode_decision_allowed:
            self.grants += 1
            data.active_mode = self.granted_mode
            data.debug_plan_mode_pending = None
        else:
            wanted = BatteryMode.GRID_CHARGING
            data.debug_plan_mode_pending = (
                wanted.value if wanted != data.active_mode else None
            )


class TestPlanChargeGrantIsBounded:
    """The §1.8 bound must hold as a BUDGET, not as an edge detector."""

    def test_grant_does_not_re_arm_inside_one_price_context(
        self, state_machine, coordinator_data
    ):
        """Regression: commit → pending-clears → re-grant, forever.

        Edge memory cannot bound this. A granted evaluation clears
        ``debug_plan_mode_pending``, so the memory of "the last qualifying pending
        value" is reset by the very grant it gates; the next frozen tick re-sets the
        pending value and the tick after that reads as a fresh rising edge. Measured
        before the fix, with the price held constant for all 21 ticks: 11 grants and
        epoch 10 in a SINGLE price context — the #622 flap budget, spent, with the
        docstring claiming a bound of one.
        """
        engine = _DriftingFacade()

        for _ in range(21):  # price/spike/DW/floor constant throughout
            asyncio.run(state_machine.evaluate_state_machine(coordinator_data, engine))

        # One grant establishes the context, one is the plan-charge grant. The plan
        # keeps wanting to charge for all 21 ticks and must not keep buying grants.
        assert engine.grants == 2
        assert state_machine._plan_charge_epoch == 1

    def test_budget_refills_when_the_price_context_changes(
        self, state_machine, coordinator_data
    ):
        """The bound is per-context, not global — a real price move re-arms it."""
        engine = _DriftingFacade()

        for _ in range(6):
            asyncio.run(state_machine.evaluate_state_machine(coordinator_data, engine))
        epoch_after_first_context = state_machine._plan_charge_epoch

        coordinator_data.general_price = 0.199  # genuine Amber tick
        for _ in range(6):
            asyncio.run(state_machine.evaluate_state_machine(coordinator_data, engine))

        assert state_machine._plan_charge_epoch == epoch_after_first_context + 1

    def test_plan_charge_grant_is_flagged_for_the_facade(
        self, state_machine, coordinator_data
    ):
        """A grant on an unchanged price context is attributable to the plan alone,
        so the facade can refuse to spend it on a non-charge mode."""
        seen = []

        class _Recorder(_DriftingFacade):
            def compute_derived_values(self, data):
                if data.mode_decision_allowed:
                    seen.append(data.mode_decision_plan_charge_only)
                super().compute_derived_values(data)

        engine = _Recorder()
        for _ in range(6):
            asyncio.run(state_machine.evaluate_state_machine(coordinator_data, engine))

        # First grant establishes the context (not plan-attributable); the second is
        # the plan-charge grant.
        assert seen == [False, True]
        # And the flag is transient — closed with the rest on exit.
        assert coordinator_data.mode_decision_plan_charge_only is False


class TestTokenWindowIntegrity:
    """The in-lock window must close on every path."""

    def test_flags_close_even_when_compute_derived_values_raises(
        self, state_machine, coordinator_data
    ):
        """``compute_derived_values`` sat OUTSIDE the try whose finally closes the
        window, so an exception from any post-facade step escaped with the decision
        token AND the backstop permission still open — leaving the next out-of-lock
        recompute armed to force a charge."""

        class _Exploding:
            def compute_derived_values(self, data):
                raise RuntimeError("post-facade step blew up")

        with pytest.raises(RuntimeError):
            asyncio.run(
                state_machine.evaluate_state_machine(coordinator_data, _Exploding())
            )

        assert coordinator_data.mode_decision_allowed is False
        assert coordinator_data.mode_backstop_allowed is False
        assert coordinator_data.mode_decision_plan_charge_only is False

    def test_suppression_blocks_the_reoptimize_path_from_granting(
        self, state_machine, coordinator_data
    ):
        """``async_recompute_and_evaluate(invalidate_decision=False)`` is documented
        as "may update the plan but must NOT grant a mode change". It runs
        compute_derived_values OUT OF LOCK first, so the frozen facade writes
        debug_plan_mode_pending and the evaluation that follows reads it back with
        ZERO lag and grants — a mode change caused by a load-deviation reoptimize."""
        engine = _DriftingFacade()

        # Establish the context.
        asyncio.run(state_machine.evaluate_state_machine(coordinator_data, engine))
        # A frozen tick leaves a wanted charge pending.
        asyncio.run(state_machine.evaluate_state_machine(coordinator_data, engine))
        assert (
            coordinator_data.debug_plan_mode_pending == BatteryMode.GRID_CHARGING.value
        )
        grants_before = engine.grants

        state_machine.suppress_next_plan_charge_grant("reoptimize")
        asyncio.run(state_machine.evaluate_state_machine(coordinator_data, engine))

        assert engine.grants == grants_before
        assert state_machine._plan_charge_epoch == 0

    def test_suppression_is_one_shot(self, state_machine, coordinator_data):
        """It must not leave the trigger disabled for the rest of the day."""
        engine = _DriftingFacade()
        asyncio.run(state_machine.evaluate_state_machine(coordinator_data, engine))
        asyncio.run(state_machine.evaluate_state_machine(coordinator_data, engine))

        state_machine.suppress_next_plan_charge_grant("reoptimize")
        asyncio.run(state_machine.evaluate_state_machine(coordinator_data, engine))
        # Next regular evaluation: the trigger works again.
        asyncio.run(state_machine.evaluate_state_machine(coordinator_data, engine))
        asyncio.run(state_machine.evaluate_state_machine(coordinator_data, engine))

        assert state_machine._plan_charge_epoch == 1
