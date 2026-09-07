"""Tests for boundary-lag telemetry (Issue #510 slice 1).

Measurement only: this slice records how far into its 5-minute price interval
a mode transition lands, tagged by the fingerprint component that granted the
re-decision. Nothing here may change a decision, a transition, or the
optimiser — these tests exist to prove that invariant as much as to prove the
numbers are right.

Issues #940 / #941 / #943 extend the same invariant set: from_mode must be the
previously *commanded* mode (not the target masquerading as the origin),
debounce-completion and retry transitions must be distinguishable from a fresh
price grant, and backstop corrections must not spam the INFO log.
"""

import logging
from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.util import dt as dt_util

from custom_components.localshift.const import BatteryMode
from custom_components.localshift.coordinator.data import CoordinatorData
from custom_components.localshift.state.machine import StateMachine

SYDNEY = timezone(timedelta(hours=11))


def dt_aware(*args):
    return datetime(*args, tzinfo=SYDNEY)


@pytest.fixture
def data():
    return CoordinatorData()


@pytest.fixture
def state_machine():
    controller = MagicMock()
    controller.set_self_consumption = AsyncMock(return_value=True)
    controller.set_force_charge = AsyncMock(return_value=True)
    controller.set_boost_charge = AsyncMock(return_value=True)
    controller.set_force_discharge = AsyncMock(return_value=True)
    controller.set_proactive_export = AsyncMock(return_value=True)
    controller.verify_current_state = AsyncMock(return_value=True)
    controller.read_fresh_soc = MagicMock(return_value=None)

    notifications = MagicMock()
    notifications.send_transition_notification = AsyncMock()
    notifications.send_transition_failed_notification = AsyncMock()
    notifications.send_health_correction_notification = AsyncMock()
    notifications.send_manual_override_timeout_notification = AsyncMock()
    notifications.send_tesla_override_notification = AsyncMock()

    return StateMachine(
        controller,
        notifications,
        lambda key: False,
        lambda key, default=None: default,
        MagicMock(),
    )


class TestBoundaryLagMeasurement:
    """Boundary lag is recorded for every successful non-dry-run transition,
    independent of the decision_lag `decision_mode == target` guard."""

    def test_recorded_when_decision_guard_would_skip(self, state_machine, data):
        # decision_mode deliberately does NOT match target: the existing
        # decision-lag guard in _record_transition_metrics skips this
        # transition entirely. Boundary lag must not.
        #
        # The shape below is a REACHABLE one (#940): _evaluate_core takes
        # `desired = data.active_mode`, so on every path that reaches
        # _record_boundary_lag the target *is* active_mode. The previous mode
        # lives in _commanded_mode, which _finalize_successful_transition
        # reassigns only after _execute_mode_transition returns.
        data.decision_mode = BatteryMode.BOOST_CHARGING
        data.decision_timestamp = dt_aware(2026, 8, 27, 6, 0, 0)
        data.active_mode = BatteryMode.GRID_CHARGING
        state_machine._commanded_mode = BatteryMode.SELF_CONSUMPTION
        with patch(
            "custom_components.localshift.state.machine.dt_util.now"
        ) as mock_now:
            mock_now.return_value = dt_aware(2026, 8, 27, 6, 5, 23)
            state_machine._record_transition_metrics(
                data, BatteryMode.GRID_CHARGING, dry_run=False
            )

        # The old guard still skips (mismatched decision_mode) — unchanged.
        assert data.decision_lag_seconds is None
        assert data.decision_lag_history == []

        # But boundary lag is measured regardless — the core point of this slice.
        assert data.boundary_lag_seconds == pytest.approx(23.0)
        assert len(data.boundary_lag_history) == 1
        entry = data.boundary_lag_history[0]
        # #940: from_mode is the previously COMMANDED mode, not active_mode
        # (which is the target on every reachable path).
        assert entry["from_mode"] == "self_consumption"
        assert entry["to_mode"] == "grid_charging"
        assert entry["boundary_lag"] == pytest.approx(23.0)

    def test_lag_from_interval_start(self, state_machine, data):
        # 06:07:23 floors to the 06:05 interval start -> 2m23s = 143.0s lag.
        with patch(
            "custom_components.localshift.state.machine.dt_util.now"
        ) as mock_now:
            mock_now.return_value = dt_aware(2026, 8, 27, 6, 7, 23)
            state_machine._record_transition_metrics(
                data, BatteryMode.SELF_CONSUMPTION, dry_run=False
            )
        assert data.boundary_lag_seconds == pytest.approx(143.0)
        assert (
            data.boundary_lag_history[0]["interval_start_utc"]
            == dt_util.as_utc(dt_aware(2026, 8, 27, 6, 5, 0)).isoformat()
        )

    def test_lag_zero_exactly_on_boundary(self, state_machine, data):
        with patch(
            "custom_components.localshift.state.machine.dt_util.now"
        ) as mock_now:
            mock_now.return_value = dt_aware(2026, 8, 27, 6, 5, 0)
            state_machine._record_transition_metrics(
                data, BatteryMode.SELF_CONSUMPTION, dry_run=False
            )
        assert data.boundary_lag_seconds == pytest.approx(0.0)

    def test_lag_just_under_next_boundary(self, state_machine, data):
        with patch(
            "custom_components.localshift.state.machine.dt_util.now"
        ) as mock_now:
            mock_now.return_value = dt_aware(2026, 8, 27, 6, 9, 59, 500000)
            state_machine._record_transition_metrics(
                data, BatteryMode.SELF_CONSUMPTION, dry_run=False
            )
        assert data.boundary_lag_seconds == pytest.approx(299.5)
        # Interval start is still 06:05, NOT rolled forward to 06:10.
        assert (
            data.boundary_lag_history[0]["interval_start_utc"]
            == dt_util.as_utc(dt_aware(2026, 8, 27, 6, 5, 0)).isoformat()
        )

    def test_from_mode_is_previous_commanded_not_active_mode(self, state_machine, data):
        """#940: from_mode must report the previous COMMANDED mode.

        Before #940 the field read ``data.active_mode``, which is always
        identical to the target on every reachable path (``_evaluate_core``
        sets ``desired = data.active_mode``), making the field dead. The real
        previous mode is still in ``_commanded_mode`` at this point because
        ``_finalize_successful_transition`` reassigns it only after
        ``_execute_mode_transition`` returns.
        """
        state_machine._commanded_mode = BatteryMode.SELF_CONSUMPTION
        data.active_mode = BatteryMode.GRID_CHARGING  # the target itself
        with patch(
            "custom_components.localshift.state.machine.dt_util.now"
        ) as mock_now:
            mock_now.return_value = dt_aware(2026, 8, 27, 6, 5, 23)
            state_machine._record_transition_metrics(
                data, BatteryMode.GRID_CHARGING, dry_run=False
            )
        assert data.boundary_lag_history[-1]["from_mode"] == "self_consumption"
        assert data.boundary_lag_history[-1]["to_mode"] == "grid_charging"

    def test_history_entry_shape(self, state_machine, data):
        data.active_mode = BatteryMode.GRID_CHARGING
        state_machine._commanded_mode = BatteryMode.SELF_CONSUMPTION
        transition_time = dt_aware(2026, 8, 27, 6, 5, 23, 456789)
        with patch(
            "custom_components.localshift.state.machine.dt_util.now"
        ) as mock_now:
            mock_now.return_value = transition_time
            state_machine._record_transition_metrics(
                data, BatteryMode.GRID_CHARGING, dry_run=False
            )
        entry = data.boundary_lag_history[0]
        assert {
            "from_mode",
            "to_mode",
            "boundary_lag",
            "grant_source",
            "interval_start_utc",
            "transition_time",
        } <= entry.keys()
        # #940: from_mode is the previously commanded mode, never the target.
        assert entry["from_mode"] == "self_consumption"
        assert entry["to_mode"] == "grid_charging"
        assert entry["boundary_lag"] == pytest.approx(round(23.456789, 2))
        # Both timestamps round-trip to the same instants they were recorded at.
        assert datetime.fromisoformat(entry["transition_time"]) == transition_time
        expected_start = dt_util.as_utc(transition_time).replace(
            second=0, microsecond=0
        )
        assert datetime.fromisoformat(entry["interval_start_utc"]) == expected_start

    def test_backstop_correction_records_from_mode_equal_to_mode(
        self, state_machine, data
    ):
        """#940 corollary: a backstop correction re-issues the commanded mode, so
        from_mode == to_mode by construction — it is a correction, not a mode
        change. The re-probe / health-check sites call
        _execute_mode_transition(data, self._commanded_mode) directly.
        """
        state_machine._commanded_mode = BatteryMode.GRID_CHARGING
        data.active_mode = BatteryMode.GRID_CHARGING
        with patch(
            "custom_components.localshift.state.machine.dt_util.now"
        ) as mock_now:
            mock_now.return_value = dt_aware(2026, 8, 27, 6, 5, 23)
            state_machine._record_transition_metrics(
                data, BatteryMode.GRID_CHARGING, dry_run=False
            )
        entry = data.boundary_lag_history[-1]
        assert entry["from_mode"] == "grid_charging"
        assert entry["to_mode"] == "grid_charging"


class TestBoundaryLagDryRun:
    def test_dry_run_records_nothing(self, state_machine, data):
        data.decision_mode = BatteryMode.GRID_CHARGING
        data.decision_timestamp = dt_aware(2026, 8, 27, 6, 0, 0)
        with patch(
            "custom_components.localshift.state.machine.dt_util.now"
        ) as mock_now:
            mock_now.return_value = dt_aware(2026, 8, 27, 6, 5, 7)
            state_machine._record_transition_metrics(
                data, BatteryMode.GRID_CHARGING, dry_run=True
            )
        assert data.boundary_lag_seconds is None
        assert data.boundary_lag_history == []
        # Dry runs skip the whole `if not dry_run` block, not just our part.
        assert state_machine._last_successful_transition is None


class TestGrantSourceTagging:
    """Grant-source attribution is captured in _apply_decision_token, not in
    _record_transition_metrics — see machine.py _classify_grant_source for why
    (by transition time, _last_evaluated_base has already been overwritten
    with the context that authorised the very transition being measured)."""

    BASE_KWARGS = {
        "general_price": 0.20,
        "feed_in_price": 0.05,
        "price_spike": False,
        "demand_window_active": False,
        "soc": 50.0,  # comfortably above the 20% default minimum target
    }

    def _seed(self, data, state_machine):
        """Grant #1: establishes _last_evaluated_base as the baseline context."""
        for key, value in self.BASE_KWARGS.items():
            setattr(data, key, value)
        state_machine._apply_decision_token(data)
        assert state_machine._last_grant_source == "unknown"  # no prior base yet

    def test_price_buy_change(self, state_machine, data):
        self._seed(data, state_machine)
        data.general_price = 0.30
        state_machine._apply_decision_token(data)
        assert state_machine._last_grant_source == "price"

    def test_price_sell_change(self, state_machine, data):
        self._seed(data, state_machine)
        data.feed_in_price = 0.08
        state_machine._apply_decision_token(data)
        assert state_machine._last_grant_source == "price"

    def test_spike(self, state_machine, data):
        self._seed(data, state_machine)
        data.price_spike = True
        state_machine._apply_decision_token(data)
        assert state_machine._last_grant_source == "spike"

    def test_demand_window(self, state_machine, data):
        self._seed(data, state_machine)
        data.demand_window_active = True
        state_machine._apply_decision_token(data)
        assert state_machine._last_grant_source == "demand_window"

    def test_soc_floor(self, state_machine, data):
        self._seed(data, state_machine)
        data.soc = 10.0  # below the 20% default minimum target
        state_machine._apply_decision_token(data)
        assert state_machine._last_grant_source == "soc_floor"

    def test_plan_charge(self, state_machine, data):
        self._seed(data, state_machine)
        # Base context unchanged; only the plan-disagreement epoch moves.
        data.active_mode = BatteryMode.SELF_CONSUMPTION
        data.debug_plan_mode_pending = BatteryMode.GRID_CHARGING.value
        state_machine._apply_decision_token(data)
        assert state_machine._last_grant_source == "plan_charge"

    def test_unknown_first_transition_after_restart(self, state_machine, data):
        # A single grant with no previous base (fresh machine) is "unknown".
        for key, value in self.BASE_KWARGS.items():
            setattr(data, key, value)
        state_machine._apply_decision_token(data)
        assert state_machine._last_grant_source == "unknown"

    def test_precedence_non_price_wins(self, state_machine, data):
        # Both price AND demand_window move in the same grant: the tag must be
        # demand_window, not price — the tag exists to exclude legitimately
        # mid-interval transitions (a DW crossing) from the price baseline.
        self._seed(data, state_machine)
        data.general_price = 0.35
        data.demand_window_active = True
        state_machine._apply_decision_token(data)
        assert state_machine._last_grant_source == "demand_window"

    def test_backstop_override(self, state_machine, data):
        self._seed(data, state_machine)
        data.general_price = 0.30
        state_machine._apply_decision_token(data)
        assert state_machine._last_grant_source == "price"

        state_machine._transition_source_override = "backstop"
        with patch(
            "custom_components.localshift.state.machine.dt_util.now"
        ) as mock_now:
            mock_now.return_value = dt_aware(2026, 8, 27, 6, 5, 0)
            state_machine._record_transition_metrics(
                data, BatteryMode.GRID_CHARGING, dry_run=False
            )
        state_machine._transition_source_override = None
        # Tagged backstop even though _last_grant_source says "price".
        assert data.boundary_lag_history[-1]["grant_source"] == "backstop"

    def test_invalidate_clears_grant_source(self, state_machine, data):
        self._seed(data, state_machine)
        data.general_price = 0.30
        state_machine._apply_decision_token(data)
        assert state_machine._last_grant_source == "price"

        state_machine.invalidate_decision_fingerprint("test")
        assert state_machine._last_grant_source is None

        with patch(
            "custom_components.localshift.state.machine.dt_util.now"
        ) as mock_now:
            mock_now.return_value = dt_aware(2026, 8, 27, 6, 5, 10)
            state_machine._record_transition_metrics(
                data, BatteryMode.SELF_CONSUMPTION, dry_run=False
            )
        assert data.boundary_lag_history[-1]["grant_source"] == "unknown"


class TestOverrideLifecycle:
    """The try/finally around both backstop call sites must clear the override
    on every path out, including a failed transition — machine.py's own
    comment calls this "not optional" because _record_transition_metrics only
    runs on success, so a failure would otherwise leave it armed forever."""

    async def test_override_cleared_after_failed_transition(self, state_machine, data):
        state_machine._last_grant_source = "price"
        state_machine._battery_controller.verify_current_state = AsyncMock(
            return_value=False
        )
        state_machine._battery_controller.set_self_consumption = AsyncMock(
            return_value=False
        )
        state_machine._notification_service.send_health_correction_notification = (
            AsyncMock()
        )

        with patch(
            "custom_components.localshift.state.machine.dt_util.now"
        ) as mock_now:
            mock_now.return_value = dt_aware(2026, 8, 27, 6, 0, 0)
            await state_machine._perform_health_check(data)

        assert state_machine._transition_source_override is None
        # The failed correction never reaches _record_transition_metrics.
        assert data.boundary_lag_history == []

        # A subsequent SUCCESSFUL transition must not inherit "backstop".
        state_machine._battery_controller.set_self_consumption = AsyncMock(
            return_value=True
        )
        with patch(
            "custom_components.localshift.state.machine.dt_util.now"
        ) as mock_now:
            mock_now.return_value = dt_aware(2026, 8, 27, 6, 0, 30)
            success = await state_machine._execute_mode_transition(
                data, BatteryMode.SELF_CONSUMPTION
            )
        assert success is True
        assert data.boundary_lag_history[-1]["grant_source"] == "price"


class TestDebounceAndRetryTagging:
    """#941: debounce-completion and post-failure retry transitions must not
    inherit a stale ``_last_grant_source``.

    ``_last_grant_source`` is written only when a decision token is granted and
    cleared only by ``invalidate_decision_fingerprint``, so it is sticky. A
    PROACTIVE_EXPORT debounce completing 2 minutes after a price grant, and a
    transition retried after a failed command or a validator block, would both
    otherwise land in the ``price``/``spike``/``demand_window`` buckets and
    pollute the Amber-latency baseline those buckets exist to measure.

    Every test here drives ``_handle_desired_mode_transition`` so the wiring is
    pinned, not just the tag.
    """

    def _tick(self, state_machine, data, desired, now):
        return state_machine._handle_desired_mode_transition(data, desired, now)

    async def test_debounce_completion_tagged_debounce(self, state_machine, data):
        state_machine._last_grant_source = "price"
        state_machine._commanded_mode = BatteryMode.SELF_CONSUMPTION
        data.active_mode = BatteryMode.PROACTIVE_EXPORT

        # Tick 1: the only 2-minute debounce starts; nothing is recorded.
        await self._tick(
            state_machine,
            data,
            BatteryMode.PROACTIVE_EXPORT,
            dt_aware(2026, 8, 27, 6, 0, 0),
        )
        assert data.boundary_lag_history == []

        # Tick 2 (+3min): debounce satisfied, transition lands.
        with patch(
            "custom_components.localshift.state.machine.dt_util.now"
        ) as mock_now:
            mock_now.return_value = dt_aware(2026, 8, 27, 6, 3, 12)
            await self._tick(
                state_machine,
                data,
                BatteryMode.PROACTIVE_EXPORT,
                dt_aware(2026, 8, 27, 6, 3, 12),
            )

        assert len(data.boundary_lag_history) == 1
        # Tagged debounce — NOT the stale "price" grant that started the timer.
        assert data.boundary_lag_history[-1]["grant_source"] == "debounce"

    async def test_zero_debounce_transition_keeps_grant_source(
        self, state_machine, data
    ):
        """Guard against over-tagging: a transition that lands on the very tick
        the grant was made (debounce 0) is a clean sample and keeps the grant."""
        state_machine._last_grant_source = "price"
        state_machine._commanded_mode = BatteryMode.SELF_CONSUMPTION
        data.active_mode = BatteryMode.GRID_CHARGING

        await self._tick(
            state_machine,
            data,
            BatteryMode.GRID_CHARGING,
            dt_aware(2026, 8, 27, 6, 0, 0),
        )

        assert len(data.boundary_lag_history) == 1
        assert data.boundary_lag_history[-1]["grant_source"] == "price"

    async def test_retry_after_failed_transition_tagged_retry(
        self, state_machine, data
    ):
        state_machine._last_grant_source = "price"
        state_machine._commanded_mode = BatteryMode.SELF_CONSUMPTION
        data.active_mode = BatteryMode.GRID_CHARGING

        # Tick 1: the controller rejects the command; nothing is recorded.
        state_machine._battery_controller.set_force_charge = AsyncMock(
            return_value=False
        )
        await self._tick(
            state_machine,
            data,
            BatteryMode.GRID_CHARGING,
            dt_aware(2026, 8, 27, 6, 0, 0),
        )
        assert data.boundary_lag_history == []
        assert state_machine._commanded_mode == BatteryMode.SELF_CONSUMPTION

        # Tick 2: the command succeeds — a retry, not a fresh price grant.
        state_machine._battery_controller.set_force_charge = AsyncMock(
            return_value=True
        )
        await self._tick(
            state_machine,
            data,
            BatteryMode.GRID_CHARGING,
            dt_aware(2026, 8, 27, 6, 1, 0),
        )

        assert len(data.boundary_lag_history) == 1
        assert data.boundary_lag_history[-1]["grant_source"] == "retry"

    async def test_retry_after_validator_block_tagged_retry(self, state_machine, data):
        """The case a naive ``debounce_in_progress`` heuristic gets wrong.

        A validator-blocked transition does NOT pop the debounce timer, so on
        the next tick ``debounce_in_progress`` is True exactly as it would be
        for a genuine debounce completion — only the retry marker
        distinguishes them.
        """
        state_machine._last_grant_source = "price"
        state_machine._commanded_mode = BatteryMode.SELF_CONSUMPTION
        data.active_mode = BatteryMode.GRID_CHARGING
        state_machine.entity_validator.should_allow_automation = MagicMock(
            return_value=False
        )

        await self._tick(
            state_machine,
            data,
            BatteryMode.GRID_CHARGING,
            dt_aware(2026, 8, 27, 6, 0, 0),
        )
        assert data.boundary_lag_history == []
        # Blocked ≠ failed: the desired-mode timer survives for the retry.
        assert BatteryMode.GRID_CHARGING in state_machine._mode_desired_since

        state_machine.entity_validator.should_allow_automation = MagicMock(
            return_value=True
        )
        await self._tick(
            state_machine,
            data,
            BatteryMode.GRID_CHARGING,
            dt_aware(2026, 8, 27, 6, 1, 0),
        )

        assert len(data.boundary_lag_history) == 1
        assert data.boundary_lag_history[-1]["grant_source"] == "retry"

    async def test_retry_marker_does_not_leak_to_a_different_mode(
        self, state_machine, data
    ):
        """The marker is mode-scoped: a failed GRID_CHARGING attempt must not
        relabel a later BOOST_CHARGING transition (which has its own grant)."""
        state_machine._last_grant_source = "price"
        state_machine._commanded_mode = BatteryMode.SELF_CONSUMPTION
        data.active_mode = BatteryMode.GRID_CHARGING

        state_machine._battery_controller.set_force_charge = AsyncMock(
            return_value=False
        )
        await self._tick(
            state_machine,
            data,
            BatteryMode.GRID_CHARGING,
            dt_aware(2026, 8, 27, 6, 0, 0),
        )
        assert data.boundary_lag_history == []

        data.active_mode = BatteryMode.BOOST_CHARGING
        state_machine._last_grant_source = "price"
        await self._tick(
            state_machine,
            data,
            BatteryMode.BOOST_CHARGING,
            dt_aware(2026, 8, 27, 6, 5, 8),
        )

        assert data.boundary_lag_history[-1]["grant_source"] == "price"
        assert data.boundary_lag_history[-1]["to_mode"] == "boost_charging"

    async def test_retry_marker_cleared_when_a_different_mode_is_desired(
        self, state_machine, data
    ):
        """Stale-timer clearing also drops a stale retry marker, so the marker
        cannot outlive the mode it belongs to."""
        state_machine._last_grant_source = "price"
        state_machine._commanded_mode = BatteryMode.SELF_CONSUMPTION
        state_machine._pending_retry_mode = BatteryMode.GRID_CHARGING

        # Desiring a different mode clears the GRID_CHARGING marker...
        await self._tick(
            state_machine,
            data,
            BatteryMode.BOOST_CHARGING,
            dt_aware(2026, 8, 27, 6, 0, 0),
        )
        assert state_machine._pending_retry_mode is None

        # ...so a later GRID_CHARGING transition is a fresh decision, not a retry.
        data.active_mode = BatteryMode.GRID_CHARGING
        await self._tick(
            state_machine,
            data,
            BatteryMode.GRID_CHARGING,
            dt_aware(2026, 8, 27, 6, 5, 8),
        )
        assert data.boundary_lag_history[-1]["grant_source"] == "price"

    async def test_retry_marker_cleared_in_stable_mode(self, state_machine, data):
        """A stable (desired == commanded) evaluation drops the marker, else it
        would be as sticky as the bug it fixes."""
        state_machine._pending_retry_mode = BatteryMode.GRID_CHARGING
        await state_machine._handle_stable_mode(data)
        assert state_machine._pending_retry_mode is None

    async def test_override_cleared_after_debounce_transition(
        self, state_machine, data
    ):
        """The new override call site honours the same try/finally contract as
        the two backstop sites: cleared on every path out."""
        state_machine._last_grant_source = "price"
        state_machine._commanded_mode = BatteryMode.SELF_CONSUMPTION

        await self._tick(
            state_machine,
            data,
            BatteryMode.PROACTIVE_EXPORT,
            dt_aware(2026, 8, 27, 6, 0, 0),
        )
        await self._tick(
            state_machine,
            data,
            BatteryMode.PROACTIVE_EXPORT,
            dt_aware(2026, 8, 27, 6, 3, 12),
        )

        assert state_machine._transition_source_override is None

    async def test_override_cleared_after_failed_transition(self, state_machine, data):
        """Same contract on the failure path — _record_transition_metrics only
        runs on success, so a failure would otherwise leave the tag armed."""
        state_machine._last_grant_source = "price"
        state_machine._commanded_mode = BatteryMode.SELF_CONSUMPTION
        state_machine._battery_controller.set_force_charge = AsyncMock(
            return_value=False
        )

        await self._tick(
            state_machine,
            data,
            BatteryMode.GRID_CHARGING,
            dt_aware(2026, 8, 27, 6, 0, 0),
        )

        assert state_machine._transition_source_override is None

    async def test_backstop_still_wins_over_retry_and_price(self, state_machine, data):
        """A health-check correction is tagged backstop even when a retry marker
        is armed and a stale price grant is pending — the override outranks both."""
        state_machine._last_grant_source = "price"
        state_machine._pending_retry_mode = BatteryMode.SELF_CONSUMPTION
        state_machine._commanded_mode = BatteryMode.SELF_CONSUMPTION
        data.active_mode = BatteryMode.SELF_CONSUMPTION
        state_machine._battery_controller.verify_current_state = AsyncMock(
            return_value=False
        )
        state_machine._notification_service.send_health_correction_notification = (
            AsyncMock()
        )

        with patch(
            "custom_components.localshift.state.machine.dt_util.now"
        ) as mock_now:
            mock_now.return_value = dt_aware(2026, 8, 27, 6, 5, 0)
            await state_machine._perform_health_check(data)

        assert len(data.boundary_lag_history) == 1
        assert data.boundary_lag_history[-1]["grant_source"] == "backstop"


class TestBoundaryLagLogLevel:
    """#943: the ``Boundary lag:`` line is INFO for decision-granted transitions
    and DEBUG for backstop corrections.

    Health-check corrections and Tesla re-probes fire up to once per 5 minutes
    for the length of a drift episode and carry no analytic signal for the
    Amber-latency baseline, so they must not spam the INFO log.
    """

    LOGGER = "custom_components.localshift.state.machine"

    def _boundary_records(self, caplog):
        return [
            record
            for record in caplog.records
            if record.getMessage().startswith("Boundary lag:")
        ]

    def _record(self, state_machine, data, caplog):
        with (
            caplog.at_level(logging.DEBUG, logger=self.LOGGER),
            patch("custom_components.localshift.state.machine.dt_util.now") as mock_now,
        ):
            mock_now.return_value = dt_aware(2026, 8, 27, 6, 5, 23)
            state_machine._record_transition_metrics(
                data, BatteryMode.SELF_CONSUMPTION, dry_run=False
            )
        records = self._boundary_records(caplog)
        assert len(records) == 1
        return records[0]

    def test_backstop_logs_at_debug(self, state_machine, data, caplog):
        state_machine._transition_source_override = "backstop"
        record = self._record(state_machine, data, caplog)
        assert record.levelno == logging.DEBUG
        state_machine._transition_source_override = None

    def test_price_logs_at_info(self, state_machine, data, caplog):
        state_machine._last_grant_source = "price"
        record = self._record(state_machine, data, caplog)
        assert record.levelno == logging.INFO

    @pytest.mark.parametrize("source", ["debounce", "retry"])
    def test_debounce_and_retry_log_at_info(self, state_machine, data, caplog, source):
        state_machine._transition_source_override = source
        record = self._record(state_machine, data, caplog)
        assert record.levelno == logging.INFO
        state_machine._transition_source_override = None

    def test_unknown_logs_at_info(self, state_machine, data, caplog):
        # No grant, no override -> "unknown"; only backstop is demoted.
        record = self._record(state_machine, data, caplog)
        assert record.levelno == logging.INFO
        assert data.boundary_lag_history[-1]["grant_source"] == "unknown"


class TestBoundaryLagHistoryCap:
    def test_capped_at_200(self, state_machine, data):
        data.boundary_lag_history = [{"boundary_lag": float(i)} for i in range(210)]
        with patch(
            "custom_components.localshift.state.machine.dt_util.now"
        ) as mock_now:
            mock_now.return_value = dt_aware(2026, 8, 27, 6, 5, 0)
            state_machine._record_transition_metrics(
                data, BatteryMode.SELF_CONSUMPTION, dry_run=False
            )
        assert len(data.boundary_lag_history) == 200
        assert data.boundary_lag_history[-1]["to_mode"] == "self_consumption"


class TestUtcDerivation:
    """The interval start is floored in UTC (NEM time is a fixed UTC+10
    offset), not local wall clock, so a DST transition is a non-event by
    construction. With Australia's whole-hour offsets a naive local floor
    would usually agree anyway (10h and 11h are both multiples of 5 minutes)
    — this is a correctness-by-construction choice plus a regression guard,
    not a fix for an observed live bug."""

    def test_dst_boundary_does_not_shift_interval(self, state_machine, data):
        aest = timezone(timedelta(hours=10))  # non-DST
        aedt = timezone(timedelta(hours=11))  # DST
        utc_instant = datetime(2026, 4, 5, 3, 7, 23, tzinfo=UTC)
        as_aest = utc_instant.astimezone(aest)
        as_aedt = utc_instant.astimezone(aedt)
        # Sanity: genuinely different local representations of one instant.
        assert as_aest.utcoffset() != as_aedt.utcoffset()
        assert as_aest.hour != as_aedt.hour

        data_a = CoordinatorData()
        data_b = CoordinatorData()
        with patch(
            "custom_components.localshift.state.machine.dt_util.now"
        ) as mock_now:
            mock_now.return_value = as_aest
            state_machine._record_transition_metrics(
                data_a, BatteryMode.SELF_CONSUMPTION, dry_run=False
            )
        with patch(
            "custom_components.localshift.state.machine.dt_util.now"
        ) as mock_now:
            mock_now.return_value = as_aedt
            state_machine._record_transition_metrics(
                data_b, BatteryMode.SELF_CONSUMPTION, dry_run=False
            )

        assert data_a.boundary_lag_seconds == pytest.approx(data_b.boundary_lag_seconds)
        assert (
            data_a.boundary_lag_history[0]["interval_start_utc"]
            == data_b.boundary_lag_history[0]["interval_start_utc"]
        )

    def test_spring_forward_boundary_does_not_shift_interval(self, state_machine, data):
        aest = timezone(timedelta(hours=10))
        aedt = timezone(timedelta(hours=11))
        utc_instant = datetime(2026, 10, 4, 16, 22, 41, tzinfo=UTC)
        as_aest = utc_instant.astimezone(aest)
        as_aedt = utc_instant.astimezone(aedt)
        assert as_aest.utcoffset() != as_aedt.utcoffset()

        data_a = CoordinatorData()
        data_b = CoordinatorData()
        with patch(
            "custom_components.localshift.state.machine.dt_util.now"
        ) as mock_now:
            mock_now.return_value = as_aest
            state_machine._record_transition_metrics(
                data_a, BatteryMode.SELF_CONSUMPTION, dry_run=False
            )
        with patch(
            "custom_components.localshift.state.machine.dt_util.now"
        ) as mock_now:
            mock_now.return_value = as_aedt
            state_machine._record_transition_metrics(
                data_b, BatteryMode.SELF_CONSUMPTION, dry_run=False
            )

        assert data_a.boundary_lag_seconds == pytest.approx(data_b.boundary_lag_seconds)
        assert (
            data_a.boundary_lag_history[0]["interval_start_utc"]
            == data_b.boundary_lag_history[0]["interval_start_utc"]
        )
