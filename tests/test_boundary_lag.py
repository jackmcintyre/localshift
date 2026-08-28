"""Tests for boundary-lag telemetry (Issue #510 slice 1).

Measurement only: this slice records how far into its 5-minute price interval
a mode transition lands, tagged by the fingerprint component that granted the
re-decision. Nothing here may change a decision, a transition, or the
optimiser — these tests exist to prove that invariant as much as to prove the
numbers are right.
"""

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
    return StateMachine(
        controller,
        MagicMock(),
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
        data.decision_mode = BatteryMode.BOOST_CHARGING
        data.decision_timestamp = dt_aware(2026, 8, 27, 6, 0, 0)
        data.active_mode = BatteryMode.SELF_CONSUMPTION
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

    def test_history_entry_shape(self, state_machine, data):
        data.active_mode = BatteryMode.SELF_CONSUMPTION
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
        assert entry["from_mode"] == "self_consumption"
        assert entry["to_mode"] == "grid_charging"
        assert entry["boundary_lag"] == pytest.approx(round(23.456789, 2))
        # Both timestamps round-trip to the same instants they were recorded at.
        assert datetime.fromisoformat(entry["transition_time"]) == transition_time
        expected_start = dt_util.as_utc(transition_time).replace(
            second=0, microsecond=0
        )
        assert datetime.fromisoformat(entry["interval_start_utc"]) == expected_start


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
