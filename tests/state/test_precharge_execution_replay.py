"""Replay of the 2026-07-27 pre-charge execution failure (#622 token starvation).

Live shape: demand window 15:00-21:00, target 95%. The battery entered at **64.0%**
— 31.0 points short — after a day on which the DP planned the charge correctly and the
executor never ran it.

Prices were never the blocker: 0.13-0.16 all morning, dipping to 0.115/0.113/0.116 at
14:35-14:50, against ``max_pre_charge_price`` 0.20. The pre-charge log shows the plan
saying ``first_charge=NOW`` at 09:40, 10:00, 10:30, 10:40, 10:50, 11:00, 11:10, 11:20,
12:40 and 12:50 — and the mode never went to Grid Charging on any of them. The only
DP-commanded charge all day was 13:41-14:16. From 13:56 the log shows ``first_charge``
sliding one slot forward on every replan while the projected shortfall climbed
4.9 → 9.9 → 16.6 → 22.4 → 25.5 → 31.5.

Root cause: ``_get_decision_fingerprint`` enumerates price/spike/DW/floor as the only
legitimate re-decision triggers, and silently assumes the plan is stable between them.
It is not — the DP's slot-0 action oscillates hold↔charge on every replan because
``first_charge`` chases the cheapest slot forward. The token therefore samples the plan
on a ~5-min stride, and nearly every sample landed on a hold-phase plan.
``debug_plan_mode_pending`` recorded the wanted charge dozens of times and never
committed it. **The plan itself is a decision input and was missing from the
fingerprint.** A counterfactual replay that commits the wanted mode reaches 94.9%.

Three fixes, one class each:

* ``TestPlanChangeTokenReplay`` — FIX 1: a monotone ``_plan_charge_epoch`` joins the
  fingerprint, so a plan that wants to *start* charging while frozen grants exactly one
  token. Deliberately one-directional: a wanted *stop* never grants.
* ``TestPrechargeBackstop`` — FIX 2: an in-lock execution backstop that force-commits
  BOOST_CHARGING when the projected DW-entry shortfall exceeds
  ``PRECHARGE_BACKSTOP_SHORTFALL_PCT`` and the plan already contains a pre-DW charge
  slot the token never sampled.
* ``TestDwEntryActualTelemetry`` — FIX 3: capture the *real* SOC at demand-window
  entry once per day. ``optimizer_summary.dw_entry_soc_pct`` is a projection that rolled
  over to tomorrow's window at 15:00 (98.5%, shortfall 0.0, first_charge 2026-07-28T13:00)
  and erased the miss from every sensor.

Nothing here exercises the DP. The 2026-07-27 plan was right; only its execution failed.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.localshift.computation_engine import ComputationEngine
from custom_components.localshift.const import BatteryMode
from custom_components.localshift.coordinator.data import CoordinatorData
from custom_components.localshift.engine.optimizer_facade import (
    PRECHARGE_BACKSTOP_SHORTFALL_PCT,
    OptimizerFacade,
)
from custom_components.localshift.engine.types import OptimizerConfig
from custom_components.localshift.sensors.optimizer import OptimizerSummarySensor
from custom_components.localshift.state.machine import StateMachine

SYDNEY = timezone(timedelta(hours=10))
CHARGE_MODES = (BatteryMode.GRID_CHARGING, BatteryMode.BOOST_CHARGING)


def dt_syd(year, month, day, hour, minute=0, second=0):
    """Create a timezone-aware datetime in the live system's timezone."""
    return datetime(year, month, day, hour, minute, second, tzinfo=SYDNEY)


def _interpolate(anchors: list[tuple[int, float]], n: int) -> list[float]:
    """Linear per-minute interpolation of a sparse live-trace SOC table."""
    out: list[float] = []
    for i in range(n):
        lo = max((a for a in anchors if a[0] <= i), key=lambda a: a[0])
        later = [a for a in anchors if a[0] > i]
        if not later:
            out.append(lo[1])
            continue
        hi = min(later, key=lambda a: a[0])
        span = hi[0] - lo[0]
        out.append(lo[1] + (hi[1] - lo[1]) * ((i - lo[0]) / span))
    return out


# =============================================================================
# FIXTURES
# =============================================================================
# tests/state/ has no conftest — test_state_machine.py declares its state-machine
# fixtures at module level — so they are declared here rather than imported across
# test modules.


@pytest.fixture
def mock_battery_controller():
    """Create a mock BatteryController."""
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
    """Create a mock NotificationService."""
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
    """Create a mock EntityValidator."""
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
    """StateMachine wired to the live 2026-07-27 config (battery_target 95)."""

    def _get_option(key, default=None):
        return {"battery_target": 95, "manual_override_timeout": 24.0}.get(key, default)

    return StateMachine(
        mock_battery_controller,
        mock_notification_service,
        lambda key: {"automation_enabled": True}.get(key, False),
        _get_option,
        mock_entity_validator,
    )


@pytest.fixture
def coordinator_data():
    """CoordinatorData seeded from the 2026-07-27 13:40 live line."""
    data = CoordinatorData()
    data.soc = 49.9
    data.operation_mode = "self_consumption"
    data.backup_reserve = 10
    data.general_price = 0.131
    data.feed_in_price = 0.04
    data.price_spike = False
    data.demand_window_active = False
    data.forecast_ready = True
    data.automation_ready = True
    data.active_mode = BatteryMode.SELF_CONSUMPTION
    return data


class FakeFacadeEngine:
    """Computation-engine stub that mimics the real optimizer-facade contract.

    Real flow: compute_derived_values() → facade._assign_active_mode() pins
    data.active_mode when data.mode_decision_allowed is False (recording the
    would-be mode in debug_plan_mode_pending), and re-decides it only when True.
    This stub reproduces exactly that pin/commit behaviour so the state machine
    can be driven through the incident without the full optimizer.
    """

    def __init__(self, plan_mode: BatteryMode) -> None:
        # The mode the "plan" currently wants (the oscillating slot-0 action).
        self.plan_mode = plan_mode
        # data.mode_decision_allowed as observed on the LAST call. The flag is
        # transient (reset in the evaluate finally block), so a test cannot read
        # it after evaluate_state_machine returns — it is captured here instead.
        self.last_decision_allowed: bool | None = None

    def compute_derived_values(self, data) -> None:
        self.last_decision_allowed = data.mode_decision_allowed
        if data.mode_decision_allowed:
            if self.plan_mode != data.active_mode:
                data.decision_timestamp = dt_syd(2026, 7, 27, 14, 0, 0)
                data.decision_mode = self.plan_mode
            data.active_mode = self.plan_mode
            data.debug_plan_mode_pending = None
        else:
            data.debug_plan_mode_pending = (
                self.plan_mode.value if self.plan_mode != data.active_mode else None
            )


# =============================================================================
# FIX 1 — the plan is a decision input
# =============================================================================


class TestPlanChangeTokenReplay:
    """Minute-by-minute replay of the 13:40-15:00 tail, where the miss became fatal."""

    # --- Amber buy price, one entry per simulated minute, changing only on the
    # 5-minute Amber tick. The 14:35/14:40/14:45/14:50 values (0.115/0.113/0.116/
    # 0.116) are verbatim from the evidence pack — the day's cheapest pre-DW dip,
    # far under the 0.20 max_pre_charge_price ceiling. The rest track the pack's
    # 0.13-0.16 tail down into that dip. Note 14:45 and 14:50 are IDENTICAL: a
    # repeated price is not a fingerprint change, so that boundary grants nothing
    # from price alone.
    PRICE_TICKS = [
        0.131,  # 13:40
        0.128,  # 13:45
        0.130,  # 13:50
        0.129,  # 13:55
        0.127,  # 14:00
        0.126,  # 14:05
        0.124,  # 14:10
        0.125,  # 14:15
        0.123,  # 14:20
        0.121,  # 14:25
        0.119,  # 14:30
        0.115,  # 14:35
        0.113,  # 14:40
        0.116,  # 14:45
        0.116,  # 14:50 — duplicate of 14:45
        0.114,  # 14:55
    ]
    PRICES = [p for p in PRICE_TICKS for _ in range(5)]

    # --- The plan's slot-0 action, one per minute. THE PATHOLOGY: every 5-minute
    # boundary — the only instant a price change can grant a token — lands on
    # SELF_CONSUMPTION (hold), while all four intervening minutes want to charge.
    # The token samples the plan exactly where the plan says "not yet".
    PLAN_SLOT0 = [
        BatteryMode.SELF_CONSUMPTION if i % 5 == 0 else BatteryMode.GRID_CHARGING
        for i in range(len(PRICES))
    ]

    # --- Live SOC (sensor.my_home_percentage_charged), anchored on the trace:
    # 13:40 49.9 (dip) → 14:00 57.0 → 14:20 62.6 (peak) → 14:50 60.3 → 15:00 64.0.
    # Never within 30 points of the 95% target.
    SOC_ANCHORS = [(0, 49.9), (20, 57.0), (40, 62.6), (70, 60.3), (79, 63.6)]
    SOC = _interpolate(SOC_ANCHORS, len(PRICES))

    def _replay(self, state_machine, data, engine) -> list[BatteryMode]:
        """Drive one evaluation per simulated minute; return committed modes."""
        committed: list[BatteryMode] = []
        for i, price in enumerate(self.PRICES):
            data.general_price = price
            data.soc = self.SOC[i]
            engine.plan_mode = self.PLAN_SLOT0[i]
            asyncio.run(state_machine.evaluate_state_machine(data, engine))
            committed.append(data.active_mode)
        return committed

    def _wanted_charge_indices(self) -> list[int]:
        return [i for i, m in enumerate(self.PLAN_SLOT0) if m in CHARGE_MODES]

    def test_prefix_baseline_no_charge_committed_without_plan_trigger(
        self, state_machine, coordinator_data, monkeypatch
    ):
        """RED anchor: with the epoch frozen (pre-fix behaviour) the plan-wanted
        charge is never committed — exactly the live 2026-07-27 outcome."""
        engine = FakeFacadeEngine(BatteryMode.SELF_CONSUMPTION)
        # Simulate the pre-fix fingerprint: the epoch never moves, so only
        # price/spike/DW/floor can grant. Raises if the method is missing.
        monkeypatch.setattr(
            state_machine, "_update_plan_charge_epoch", lambda data, context=None: None
        )

        committed = self._replay(state_machine, coordinator_data, engine)

        assert [m for m in committed if m in CHARGE_MODES] == []
        # And the plan really did want to charge — guard against a vacuous pass.
        assert len(self._wanted_charge_indices()) >= 60

    def test_plan_wanted_charge_is_committed(
        self, state_machine, coordinator_data, mock_battery_controller
    ):
        """The fix: a plan that wants to START charging while frozen grants a
        token, and the freshly-planned charge is committed within one tick."""
        engine = FakeFacadeEngine(BatteryMode.SELF_CONSUMPTION)

        committed = self._replay(state_machine, coordinator_data, engine)

        charge_ticks = [i for i, m in enumerate(committed) if m in CHARGE_MODES]
        # (a) charging actually happens.
        assert charge_ticks, "plan-wanted charge was never committed"
        assert BatteryMode.GRID_CHARGING in committed
        mock_battery_controller.set_force_charge.assert_called()

        # (b) it lands promptly — the pending value is read on the tick AFTER the
        # facade writes it (the documented 1-tick lag), so ≤2 evaluations.
        first_wanted = self._wanted_charge_indices()[0]
        assert charge_ticks[0] - first_wanted <= 2

        # (c) and it STAYS committed, rather than being sampled once and lost.
        wanted = len(self._wanted_charge_indices())
        assert len(charge_ticks) >= 0.6 * wanted

    def test_no_flap_within_a_price_interval(
        self, state_machine, coordinator_data, mock_battery_controller
    ):
        """The anti-flap bound: at most one extra decision per (price context ×
        new wanted-charge mode), and it can only START charging."""
        engine = FakeFacadeEngine(BatteryMode.SELF_CONSUMPTION)

        transitions: list[BatteryMode] = []
        original = state_machine._execute_mode_transition

        async def _counting(data, target):
            result = await original(data, target)
            if result:
                transitions.append(target)
            return result

        state_machine._execute_mode_transition = _counting

        committed = self._replay(state_machine, coordinator_data, engine)

        # No charge → non-charge → charge cycle is reachable inside one price group.
        for group in range(len(self.PRICE_TICKS)):
            seq = [m in CHARGE_MODES for m in committed[group * 5 : group * 5 + 5]]
            # Collapse runs, then reject the flap pattern [True, False, True].
            runs = [seq[0]] + [b for a, b in zip(seq, seq[1:]) if a != b]
            assert runs[:3] != [True, False, True], f"flap in price group {group}"

        # The §1.8 bound, computed from the data rather than hardcoded.
        n_price_groups = len(self.PRICE_TICKS)
        rising_edges = sum(
            1
            for i, m in enumerate(self.PLAN_SLOT0)
            if m in CHARGE_MODES
            and (i == 0 or self.PLAN_SLOT0[i - 1] not in CHARGE_MODES)
        )
        assert len(transitions) <= n_price_groups + rising_edges

    def test_wanted_stop_never_grants_a_token(
        self, state_machine, coordinator_data, mock_battery_controller
    ):
        """One-directional asymmetry: a plan that wants to STOP charging is never
        a re-decision trigger — the stop waits for the next natural fingerprint
        change (≤1 Amber tick)."""
        coordinator_data.active_mode = BatteryMode.GRID_CHARGING
        state_machine._commanded_mode = BatteryMode.GRID_CHARGING
        # Start already frozen on this context, so tick 1 is not a free grant.
        state_machine._last_evaluated_fingerprint = (
            state_machine._get_decision_fingerprint(coordinator_data)
        )
        coordinator_data.debug_plan_mode_pending = BatteryMode.SELF_CONSUMPTION.value
        engine = FakeFacadeEngine(BatteryMode.SELF_CONSUMPTION)

        for _ in range(10):
            asyncio.run(
                state_machine.evaluate_state_machine(coordinator_data, engine)
            )
            assert engine.last_decision_allowed is False

        assert coordinator_data.active_mode == BatteryMode.GRID_CHARGING
        mock_battery_controller.set_self_consumption.assert_not_called()

    def test_epoch_does_not_increment_on_resolution(
        self, state_machine, coordinator_data
    ):
        """Monotonicity, asserted directly: the epoch rises on the disagreement's
        rising edge and does NOT move again when the disagreement resolves. That
        is what stops the commit → pending-clears → grant-again flap loop."""
        engine = FakeFacadeEngine(BatteryMode.SELF_CONSUMPTION)
        state_machine._last_evaluated_fingerprint = (
            state_machine._get_decision_fingerprint(coordinator_data)
        )
        assert state_machine._plan_charge_epoch == 0

        # Tick 1: the previous cycle left a wanted-charge pending → rising edge.
        coordinator_data.debug_plan_mode_pending = BatteryMode.GRID_CHARGING.value
        engine.plan_mode = BatteryMode.GRID_CHARGING
        asyncio.run(state_machine.evaluate_state_machine(coordinator_data, engine))
        assert engine.last_decision_allowed is True
        assert state_machine._plan_charge_epoch == 1
        assert coordinator_data.debug_plan_mode_pending is None

        # Tick 2: the disagreement is resolved (pending back to None) — no move.
        epoch_before = state_machine._plan_charge_epoch
        asyncio.run(state_machine.evaluate_state_machine(coordinator_data, engine))
        assert state_machine._plan_charge_epoch == epoch_before
        assert engine.last_decision_allowed is False

    def test_deferred_redemption_still_dead(
        self, state_machine, coordinator_data, mock_battery_controller
    ):
        """FIX 1 must not reopen #622: a NON-charge plan flip on a stale token is
        still un-redeemable, on the flip tick and on every tick after it."""
        state_machine._commanded_mode = BatteryMode.SELF_CONSUMPTION
        engine = FakeFacadeEngine(BatteryMode.SELF_CONSUMPTION)

        # Tick 1: price change, plan stable. Token spent by the evaluation.
        asyncio.run(state_machine.evaluate_state_machine(coordinator_data, engine))
        mock_battery_controller.set_force_discharge.reset_mock()

        # Tick 2: SAME price, plan flips to a non-charge mode. Frozen.
        engine.plan_mode = BatteryMode.SPIKE_DISCHARGE
        asyncio.run(state_machine.evaluate_state_machine(coordinator_data, engine))
        assert engine.last_decision_allowed is False
        assert (
            coordinator_data.debug_plan_mode_pending
            == BatteryMode.SPIKE_DISCHARGE.value
        )

        # Tick 3: the pending non-charge mode must not grant on the next tick
        # either — that is precisely the deferred redemption #622 killed.
        asyncio.run(state_machine.evaluate_state_machine(coordinator_data, engine))
        assert engine.last_decision_allowed is False
        assert coordinator_data.active_mode == BatteryMode.SELF_CONSUMPTION
        mock_battery_controller.set_force_discharge.assert_not_called()


# =============================================================================
# FIX 2 — the pre-charge execution backstop
# =============================================================================


def _backstop_config(**overrides) -> OptimizerConfig:
    """OptimizerConfig from the 14:30 live line (shortfall 22.4, still pre-DW)."""
    defaults = dict(
        optimization_mode="self_consumption",
        demand_window_target_soc_pct=95.0,
        battery_capacity_kwh=13.5,
        charge_rate_kw=3.3,
        boost_charge_rate_kw=5.0,
        max_precharge_price=0.20,
        max_soc_pct=100.0,
        min_soc_pct=10.0,
        hard_target_floor=95.0,
        urgency_window_start_idx=0,
        terminal_penalty_idx=10,
    )
    defaults.update(overrides)
    return OptimizerConfig(**defaults)


def _backstop_data(**overrides) -> CoordinatorData:
    """CoordinatorData at 14:30: token frozen, plan says hold, DW 30 min away."""
    data = CoordinatorData()
    data.active_mode = BatteryMode.SELF_CONSUMPTION
    data.soc = 62.2
    data.general_price = 0.116
    data.demand_window_active = False
    # The token is frozen — the whole point of the backstop.
    data.mode_decision_allowed = False
    data.mode_backstop_allowed = True
    # A slot bracketing "now" so the current-slot lookup reports idx 0, plus the
    # pre-DW grid charge the DP planned and the token never sampled.
    data.optimizer_decisions = [
        {
            "action": "hold",
            "timestamp_iso": "2020-01-01T00:00:00+00:00",
            "slot_interval_minutes": 9_999_999,
            "slot_index": 0,
            "grid_charge": False,
        },
        {
            "action": "charge_grid_normal",
            "timestamp_iso": "2020-01-01T00:00:00+00:00",
            "slot_interval_minutes": 9_999_999,
            "slot_index": 4,
            "grid_charge": True,
        },
    ]
    for key, value in overrides.items():
        setattr(data, key, value)
    return data


def _backstop_result(shortfall: float = 22.4, dw_entry: float = 72.6):
    return SimpleNamespace(
        terminal_shortfall_pct=shortfall,
        dw_entry_soc_pct=dw_entry,
        decisions=[],
    )


def _run_assign(data, result, config):
    """Run _assign_active_mode with the safety gate admitting and the plan
    (correctly, per the incident) asking to HOLD in slot 0."""
    facade = OptimizerFacade()
    with (
        patch(
            "custom_components.localshift.engine.optimizer_facade.OptimizerSafetyGate"
        ) as mock_gate,
        patch(
            "custom_components.localshift.engine.optimizer_facade._derive_runtime_apply_plan",
            return_value={
                "battery_mode": BatteryMode.SELF_CONSUMPTION.value,
                "action": "hold",
            },
        ),
    ):
        mock_gate.return_value.check_admission.return_value = SimpleNamespace(
            allowed=True, block_reason=None
        )
        facade._assign_active_mode(data, result, config, {})
    return facade


class TestPrechargeBackstop:
    """The 14:16-14:52 hole: shortfall climbing 16→30% with the battery idle."""

    def test_backstop_forces_boost_when_token_frozen(self, caplog):
        data = _backstop_data()
        with caplog.at_level("WARNING"):
            _run_assign(data, _backstop_result(), _backstop_config())

        assert data.active_mode == BatteryMode.BOOST_CHARGING
        assert data.debug_mode_source == "precharge_backstop"
        assert data.optimizer_precharge_backstop_active is True
        assert data.debug_plan_mode_pending is None
        assert "PRE-CHARGE BACKSTOP" in caplog.text

    @pytest.mark.parametrize(
        ("case", "data_overrides", "config_overrides", "result_kwargs"),
        [
            # Pointless once the window is live, and it would fight the DW block.
            ("dw_active", {"demand_window_active": True}, {}, {}),
            # #886 gate dormant (allow_dw_entry_under_target, or solar reaches
            # target on its own) ⇒ the backstop is dormant with it. Reuse, not
            # re-derivation.
            ("floor_dormant", {}, {"hard_target_floor": None}, {}),
            # Nothing to force at or above the floor.
            ("soc_at_floor", {"soc": 96.0}, {}, {}),
            # Below the failure-detector threshold — a normal plan, not a failure.
            (
                "shortfall_small",
                {},
                {},
                {"shortfall": 4.0, "dw_entry": 91.0},
            ),
            # Not yet in the urgency window: there is still runway.
            ("before_urgency_window", {}, {"urgency_window_start_idx": 5}, {}),
            # Above the operator's own pre-charge price ceiling.
            ("price_over_ceiling", {"general_price": 0.35}, {}, {}),
            # Out-of-lock recompute: the backstop must never command hardware.
            ("out_of_lock", {"mode_backstop_allowed": False}, {}, {}),
        ],
    )
    def test_backstop_inert_when(
        self, case, data_overrides, config_overrides, result_kwargs
    ):
        data = _backstop_data(**data_overrides)
        _run_assign(
            data,
            _backstop_result(**result_kwargs),
            _backstop_config(**config_overrides),
        )

        assert data.active_mode == BatteryMode.SELF_CONSUMPTION, case
        assert data.optimizer_precharge_backstop_active is False, case

    def test_backstop_inert_without_a_pre_dw_charge_in_the_plan(self):
        """It never invents charge intent — only executes intent the DP expressed."""
        data = _backstop_data()
        for decision in data.optimizer_decisions:
            decision["grid_charge"] = False

        _run_assign(data, _backstop_result(), _backstop_config())

        assert data.active_mode == BatteryMode.SELF_CONSUMPTION
        assert data.optimizer_precharge_backstop_active is False

    def test_backstop_inert_when_charge_is_only_inside_the_demand_window(self):
        """The negative twin: a charge at/after terminal_penalty_idx is not
        pre-charge intent and must not count."""
        data = _backstop_data()
        data.optimizer_decisions[1]["slot_index"] = 12  # terminal_penalty_idx = 10

        _run_assign(data, _backstop_result(), _backstop_config())

        assert data.active_mode == BatteryMode.SELF_CONSUMPTION
        assert data.optimizer_precharge_backstop_active is False

    def test_backstop_never_runs_when_safety_gate_blocks(self):
        """Hard constraint: the backstop sits AFTER check_admission and never
        runs on the blocked path."""
        data = _backstop_data()
        facade = OptimizerFacade()
        with patch(
            "custom_components.localshift.engine.optimizer_facade.OptimizerSafetyGate"
        ) as mock_gate:
            mock_gate.return_value.check_admission.return_value = SimpleNamespace(
                allowed=False, block_reason="stale forecast"
            )
            facade._assign_active_mode(
                data, _backstop_result(), _backstop_config(), {}
            )

        assert data.active_mode == BatteryMode.SELF_CONSUMPTION
        assert data.optimizer_precharge_backstop_active is False
        assert data.optimizer_last_apply_status == "blocked"

    def test_backstop_is_idempotent(self):
        """It re-asserts every tick while its conditions hold, without churn."""
        data = _backstop_data()
        config = _backstop_config()
        modes = []
        for _ in range(5):
            _run_assign(data, _backstop_result(), config)
            modes.append(data.active_mode)

        assert set(modes) == {BatteryMode.BOOST_CHARGING}
        assert data.optimizer_precharge_backstop_active is True


# =============================================================================
# FIX 3 — the DW-entry actual that the 15:00 rollover erased
# =============================================================================


@pytest.fixture
def dw_entry_engine(mock_hass, mock_get_entity_id):
    """ComputationEngine with the live 2026-07-27 window (15:00-21:00, target 95)."""
    entry = MagicMock()
    entry.data = {}
    entry.options = {
        "battery_target": 95,
        "demand_window_start": "15:00:00",
        "demand_window_end": "21:00:00",
    }
    return ComputationEngine(
        mock_hass,
        entry,
        mock_get_entity_id,
        lambda key: {"demand_window_block": True}.get(key, False),
    )


def _tick(engine, data, now_dt):
    """One capture tick: refresh demand_window_active, then capture the actual."""
    with patch(
        "custom_components.localshift.computation_engine.dt_util.now",
        return_value=now_dt,
    ):
        ctx = engine._build_step_context()
    engine._compute_demand_window_active(data, ctx)
    engine._capture_dw_entry_actual(data, ctx)
    return ctx


class TestDwEntryActualTelemetry:
    """The real number: 64.0% at 15:00 against a 95% target."""

    def test_captured_at_window_start(self, dw_entry_engine):
        data = CoordinatorData()
        data.soc = 64.0

        _tick(dw_entry_engine, data, dt_syd(2026, 7, 27, 14, 59))
        assert data.demand_window_active is False
        assert data.dw_entry_actual_soc_pct is None

        _tick(dw_entry_engine, data, dt_syd(2026, 7, 27, 15, 0))
        assert data.demand_window_active is True
        assert data.dw_entry_actual_soc_pct == 64.0
        assert data.dw_entry_actual_target_pct == 95.0
        assert data.dw_entry_actual_shortfall_pct == pytest.approx(31.0)
        assert data.dw_entry_actual_at == dt_syd(2026, 7, 27, 15, 0)
        assert data.dw_entry_actual_date == dt_syd(2026, 7, 27, 15, 0).date()

    def test_survives_the_rollover_that_erased_the_miss(self, dw_entry_engine):
        """The 15:00 log line reported dw_entry 98.5 / shortfall 0.0 /
        first_charge 2026-07-28T13:00 — tomorrow's window. The projection is
        allowed to roll over; the actual is not."""
        data = CoordinatorData()
        data.soc = 64.0
        _tick(dw_entry_engine, data, dt_syd(2026, 7, 27, 15, 0))

        data.optimizer_summary = {
            "enabled": True,
            "success": True,
            "dw_entry_soc_pct": 98.5,
            "terminal_shortfall_pct": 0.0,
            "first_charge": "2026-07-28T13:00",
        }

        assert data.dw_entry_actual_soc_pct == 64.0
        assert data.dw_entry_actual_shortfall_pct == pytest.approx(31.0)

        coordinator = MagicMock()
        coordinator.data = data
        sensor = OptimizerSummarySensor(coordinator, MagicMock())
        with patch(
            "custom_components.localshift.sensors.optimizer.dt_util.now",
            return_value=datetime(2026, 7, 27, 5, 0, tzinfo=UTC),
        ):
            attrs = sensor.extra_state_attributes

        # Projected and actual sit side by side and disagree — which is the point.
        assert attrs["dw_entry_soc_pct"] == 98.5
        assert attrs["dw_entry_actual_soc_pct"] == 64.0
        assert attrs["dw_entry_actual_shortfall_pct"] == pytest.approx(31.0)
        assert attrs["dw_entry_actual_target_pct"] == 95.0
        assert attrs["dw_entry_actual_at"] == dt_syd(2026, 7, 27, 15, 0).isoformat()
        assert attrs["precharge_backstop_active"] is False

    def test_none_soc_defers_capture(self, dw_entry_engine):
        """A None SOC at the exact boundary must defer, not record a null — the
        next tick (≤1 min) retries."""
        data = CoordinatorData()
        data.soc = None

        _tick(dw_entry_engine, data, dt_syd(2026, 7, 27, 15, 0))
        assert data.dw_entry_actual_soc_pct is None
        assert data.dw_entry_actual_date is None

        data.soc = 64.0
        _tick(dw_entry_engine, data, dt_syd(2026, 7, 27, 15, 1))
        assert data.dw_entry_actual_soc_pct == 64.0
        assert data.dw_entry_actual_at == dt_syd(2026, 7, 27, 15, 1)

    def test_capture_is_once_per_day_and_resets_on_date_change(self, dw_entry_engine):
        data = CoordinatorData()
        data.soc = 64.0
        _tick(dw_entry_engine, data, dt_syd(2026, 7, 27, 15, 0))

        # Later ticks in the same window must not overwrite the entry value.
        data.soc = 88.0
        _tick(dw_entry_engine, data, dt_syd(2026, 7, 27, 17, 30))
        assert data.dw_entry_actual_soc_pct == 64.0
        assert data.dw_entry_actual_at == dt_syd(2026, 7, 27, 15, 0)

        # The daily latch reset clears all five fields on the date change.
        dw_entry_engine._reset_daily_precharge_latch(data, dt_syd(2026, 7, 28, 0, 5).date())
        assert data.dw_entry_actual_soc_pct is None
        assert data.dw_entry_actual_at is None
        assert data.dw_entry_actual_date is None
        assert data.dw_entry_actual_target_pct is None
        assert data.dw_entry_actual_shortfall_pct is None

    def test_shortfall_threshold_is_the_backstop_constant(self):
        """The capture logs at WARNING above the same threshold the backstop
        fires on — one number, not two."""
        assert PRECHARGE_BACKSTOP_SHORTFALL_PCT == 5.0
