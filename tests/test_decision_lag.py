"""Tests for two-phase decision lag tracking (Issues #501, #508, #917).

Phase 1 (command lag): decision -> control entity command completion.
Phase 2 (physical lag): decision -> battery power crossing a mode-appropriate
threshold, observed via a temporary state-change listener with a 10-minute
timeout.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.util import dt as dt_util

from custom_components.localshift.const import BatteryMode
from custom_components.localshift.coordinator.coordinator import LocalShiftCoordinator
from custom_components.localshift.coordinator.data import (
    CoordinatorData,
    PhysicalResponseWatch,
)
from custom_components.localshift.sensors.status import DecisionLagSensor
from custom_components.localshift.state.machine import (
    PHYSICAL_RESPONSE_DIRECTIONS,
    StateMachine,
)

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


def make_coordinator():
    hass = MagicMock()
    entry = MagicMock()
    entry.data = {}
    entry.options = {}
    return LocalShiftCoordinator(hass, entry)


def make_event(power):
    return MagicMock(data={"new_state": SimpleNamespace(state=str(power))})


class TestPhysicalResponseWatch:
    def test_defaults_and_fields(self):
        decision = dt_aware(2026, 8, 27, 6, 0, 0)
        watch = PhysicalResponseWatch(
            decision_timestamp=decision,
            expected_direction="charging",
            baseline_power_kw=0.1,
            target_mode=BatteryMode.GRID_CHARGING,
            timeout_at=decision + timedelta(minutes=10),
        )
        assert watch.decision_timestamp == decision
        assert watch.expected_direction == "charging"
        assert watch.baseline_power_kw == 0.1
        assert watch.target_mode == BatteryMode.GRID_CHARGING
        assert watch.timeout_at == decision + timedelta(minutes=10)

    def test_coordinator_data_defaults(self):
        data = CoordinatorData()
        assert data.command_completion_timestamp is None
        assert data.physical_response_watch is None
        assert data.physical_response_timestamp is None
        assert data.physical_response_lag_seconds is None
        assert data.physical_response_timed_out is False
        assert data.decision_timestamp is None
        assert data.decision_mode is None
        assert data.decision_lag_seconds is None
        assert len(data.decision_lag_history) == 0


class TestPhysicalResponseDirections:
    def test_charging_modes(self):
        assert PHYSICAL_RESPONSE_DIRECTIONS[BatteryMode.GRID_CHARGING] == "charging"
        assert PHYSICAL_RESPONSE_DIRECTIONS[BatteryMode.BOOST_CHARGING] == "charging"

    def test_discharging_modes(self):
        assert (
            PHYSICAL_RESPONSE_DIRECTIONS[BatteryMode.SPIKE_DISCHARGE] == "discharging"
        )
        assert (
            PHYSICAL_RESPONSE_DIRECTIONS[BatteryMode.PROACTIVE_EXPORT] == "discharging"
        )

    def test_other_modes_unobservable(self):
        for mode in (
            BatteryMode.SELF_CONSUMPTION,
            BatteryMode.HOLD,
            BatteryMode.MANUAL,
            BatteryMode.DEMAND_BLOCK,
        ):
            assert mode not in PHYSICAL_RESPONSE_DIRECTIONS


class TestRecordTransitionMetrics:
    """Phase 1 + watch start via StateMachine._record_transition_metrics."""

    def _prime(self, data):
        data.active_mode = BatteryMode.SELF_CONSUMPTION
        data.decision_mode = BatteryMode.GRID_CHARGING
        data.decision_timestamp = dt_aware(2026, 8, 27, 6, 0, 0)
        data.battery_power_kw = 0.0
        data.soc = 50.0
        data.battery_target_soc = 80.0

    def test_command_lag_uses_command_completion(self, state_machine, data):
        self._prime(data)
        data.command_completion_timestamp = dt_aware(2026, 8, 27, 6, 0, 7)
        with patch(
            "custom_components.localshift.state.machine.dt_util.now"
        ) as mock_now:
            mock_now.return_value = dt_aware(2026, 8, 27, 6, 0, 30)
            state_machine._record_transition_metrics(
                data, BatteryMode.GRID_CHARGING, dry_run=False
            )
        # Command lag is decision -> command completion (7s), not -> validation (30s)
        assert data.decision_lag_seconds == 7.0
        assert data.command_completion_timestamp == dt_aware(2026, 8, 27, 6, 0, 7)

    def test_watch_started_for_observable_mode(self, state_machine, data):
        self._prime(data)
        completion = dt_aware(2026, 8, 27, 6, 0, 7)
        data.command_completion_timestamp = completion
        with patch(
            "custom_components.localshift.state.machine.dt_util.now"
        ) as mock_now:
            mock_now.return_value = completion
            state_machine._record_transition_metrics(
                data, BatteryMode.GRID_CHARGING, dry_run=False
            )
        watch = data.physical_response_watch
        assert watch is not None
        assert watch.decision_timestamp == dt_aware(2026, 8, 27, 6, 0, 0)
        assert watch.expected_direction == "charging"
        assert watch.target_mode == BatteryMode.GRID_CHARGING
        assert watch.baseline_power_kw == 0.0
        assert watch.timeout_at == dt_aware(2026, 8, 27, 6, 10, 0)

    def test_watch_not_started_dry_run(self, state_machine, data):
        self._prime(data)
        with patch(
            "custom_components.localshift.state.machine.dt_util.now"
        ) as mock_now:
            mock_now.return_value = dt_aware(2026, 8, 27, 6, 0, 7)
            state_machine._record_transition_metrics(
                data, BatteryMode.GRID_CHARGING, dry_run=True
            )
        assert data.physical_response_watch is None
        assert data.decision_lag_seconds is None
        # Dry runs record neither phase: no history entry at all.
        assert data.decision_lag_history == []

    def test_watch_not_started_unobservable_mode(self, state_machine, data):
        self._prime(data)
        data.decision_mode = BatteryMode.SELF_CONSUMPTION
        with patch(
            "custom_components.localshift.state.machine.dt_util.now"
        ) as mock_now:
            mock_now.return_value = dt_aware(2026, 8, 27, 6, 0, 7)
            state_machine._record_transition_metrics(
                data, BatteryMode.SELF_CONSUMPTION, dry_run=False
            )
        assert data.physical_response_watch is None
        assert data.decision_lag_history[-1]["observable"] is False

    def test_watch_skipped_when_soc_at_target(self, state_machine, data):
        self._prime(data)
        data.soc = 80.0
        assert data.soc >= data.battery_target_soc
        with patch(
            "custom_components.localshift.state.machine.dt_util.now"
        ) as mock_now:
            mock_now.return_value = dt_aware(2026, 8, 27, 6, 0, 7)
            state_machine._record_transition_metrics(
                data, BatteryMode.GRID_CHARGING, dry_run=False
            )
        assert data.physical_response_watch is None
        assert data.decision_lag_history[-1]["observable"] is False

    def test_no_metrics_when_decision_mode_mismatch(self, state_machine, data):
        self._prime(data)
        data.decision_mode = BatteryMode.BOOST_CHARGING
        with patch(
            "custom_components.localshift.state.machine.dt_util.now"
        ) as mock_now:
            mock_now.return_value = dt_aware(2026, 8, 27, 6, 0, 7)
            state_machine._record_transition_metrics(
                data, BatteryMode.GRID_CHARGING, dry_run=False
            )
        assert data.decision_lag_seconds is None
        assert data.physical_response_watch is None
        assert len(data.decision_lag_history) == 0
        # Decision state preserved for the matching transition
        assert data.decision_timestamp is not None

    def test_history_entry_shape_and_cap(self, state_machine, data):
        self._prime(data)
        completion = dt_aware(2026, 8, 27, 6, 0, 7)
        data.command_completion_timestamp = completion
        data.decision_lag_history = [{"command_lag": float(i)} for i in range(60)]
        with patch(
            "custom_components.localshift.state.machine.dt_util.now"
        ) as mock_now:
            mock_now.return_value = completion
            state_machine._record_transition_metrics(
                data, BatteryMode.GRID_CHARGING, dry_run=False
            )
        assert len(data.decision_lag_history) == 50
        entry = data.decision_lag_history[-1]
        assert entry["from_mode"] == "self_consumption"
        assert entry["to_mode"] == "grid_charging"
        assert entry["command_lag"] == 7.0
        assert entry["physical_lag"] is None
        assert (
            entry["decision_time"]
            == data.physical_response_watch.decision_timestamp.isoformat()
        )
        assert entry["observable"] is True
        assert entry["timed_out"] is False
        assert data.decision_timestamp is None
        assert data.decision_mode is None


class TestFailedTransitionCleanup:
    def test_failed_transition_clears_tracking(self, data):
        state_machine = StateMachine(
            MagicMock(),
            MagicMock(),
            lambda key: False,
            lambda key, default=None: default,
            MagicMock(),
        )
        decision = dt_aware(2026, 8, 27, 6, 0, 0)
        data.decision_mode = BatteryMode.GRID_CHARGING
        data.decision_timestamp = decision
        data.physical_response_watch = PhysicalResponseWatch(
            decision_timestamp=decision,
            expected_direction="charging",
            baseline_power_kw=0.0,
            target_mode=BatteryMode.GRID_CHARGING,
            timeout_at=decision + timedelta(minutes=10),
        )
        state_machine._get_mode_config = lambda target, d: MagicMock(backup_reserve=10)
        state_machine._dispatch_mode_transition = AsyncMock(return_value=False)

        ok = asyncio.run(
            state_machine._execute_mode_transition(data, BatteryMode.GRID_CHARGING)
        )

        assert ok is False
        assert data.decision_timestamp is None
        assert data.decision_mode is None
        assert data.physical_response_watch is None


class TestControllerCommandCompletion:
    def test_run_transition_records_command_completion_before_validation(
        self, mock_hass, mock_get_entity_id
    ):
        from custom_components.localshift.integration.controller import (
            BatteryController,
            TransitionExpectation,
            TransitionRecipe,
            TransitionStep,
        )

        controller = BatteryController(mock_hass, mock_get_entity_id)
        data = CoordinatorData()
        recorded = {}

        async def step():
            return True

        async def validate(**kwargs):
            recorded["completion_at_validation"] = data.command_completion_timestamp
            return True

        controller._validator = MagicMock()
        controller._validator.validate_transition = validate
        completion_time = dt_aware(2026, 8, 27, 6, 0, 7)
        recipe = TransitionRecipe(
            name="test",
            steps=[TransitionStep(action=step, failure_message="nope")],
            expectation=TransitionExpectation(
                operation_mode="self_consumption", backup_reserve=10
            ),
        )
        with patch(
            "custom_components.localshift.integration.controller.dt_util.now"
        ) as mock_now:
            mock_now.return_value = completion_time
            ok = asyncio.run(controller._run_transition(recipe, data))
        assert ok is True
        # Recorded after the last command, BEFORE validation observed None? No:
        # validation must already see the completion timestamp.
        assert recorded["completion_at_validation"] == completion_time
        assert data.command_completion_timestamp == completion_time

    def test_dry_run_skips_command_completion(self, mock_hass, mock_get_entity_id):
        from custom_components.localshift.integration.controller import (
            BatteryController,
        )

        controller = BatteryController(mock_hass, mock_get_entity_id)
        data = CoordinatorData()
        ok = asyncio.run(controller.set_self_consumption(data, dry_run=True))
        assert ok is True
        assert data.command_completion_timestamp is None


class TestCoordinatorPhysicalWatch:
    def _watch(self, minutes_old=0.0, direction="charging"):
        # Build relative to the real clock so timeout logic is deterministic:
        # minutes_old >= 10 means the watch is already expired.
        decision = dt_util.now() - timedelta(minutes=minutes_old)
        return PhysicalResponseWatch(
            decision_timestamp=decision,
            expected_direction=direction,
            baseline_power_kw=0.0,
            target_mode=BatteryMode.GRID_CHARGING
            if direction == "charging"
            else BatteryMode.SPIKE_DISCHARGE,
            timeout_at=decision + timedelta(minutes=10),
        )

    def test_check_starts_listener_for_active_watch(self):
        coordinator = make_coordinator()
        coordinator.data.physical_response_watch = self._watch()
        with patch(
            "custom_components.localshift.coordinator.coordinator."
            "async_track_state_change_event"
        ) as track:
            unsub = MagicMock()
            track.return_value = unsub
            coordinator._check_physical_response_watch()
        track.assert_called_once()
        assert coordinator._unsub_battery_power_listener == unsub

    def test_check_removes_listener_when_watch_cleared(self):
        coordinator = make_coordinator()
        unsub = MagicMock()
        coordinator._unsub_battery_power_listener = unsub
        coordinator._check_physical_response_watch()
        unsub.assert_called_once_with()
        assert coordinator._unsub_battery_power_listener is None

    def test_check_timeout_records_and_cleans_up(self):
        coordinator = make_coordinator()
        watch = self._watch(minutes_old=11)
        coordinator.data.physical_response_watch = watch
        coordinator.data.decision_lag_history = [
            {"decision_time": watch.decision_timestamp.isoformat()}
        ]
        unsub = MagicMock()
        coordinator._unsub_battery_power_listener = unsub
        coordinator._check_physical_response_watch()
        assert coordinator.data.physical_response_timed_out is True
        assert coordinator.data.physical_response_watch is None
        unsub.assert_called_once_with()
        assert coordinator.data.decision_lag_history[-1]["timed_out"] is True
        assert coordinator.data.decision_lag_history[-1]["physical_lag"] is None

    def test_listener_detects_charging_threshold(self):
        coordinator = make_coordinator()
        watch = self._watch()
        coordinator.data.physical_response_watch = watch
        coordinator.data.decision_lag_history = [
            {"decision_time": watch.decision_timestamp.isoformat()}
        ]
        now = watch.decision_timestamp + timedelta(seconds=8)
        with patch(
            "custom_components.localshift.coordinator.coordinator.dt_util.now"
        ) as mock_now:
            mock_now.return_value = now
            coordinator._on_battery_power_change(make_event(-0.35))
        assert coordinator.data.physical_response_lag_seconds == 8.0
        assert coordinator.data.physical_response_timestamp == now
        assert coordinator.data.physical_response_watch is None
        assert coordinator.data.physical_response_timed_out is False
        assert coordinator.data.decision_lag_history[-1]["physical_lag"] == 8.0

    def test_listener_detects_discharging_threshold(self):
        coordinator = make_coordinator()
        watch = self._watch(direction="discharging")
        coordinator.data.physical_response_watch = watch
        coordinator.data.decision_lag_history = [
            {"decision_time": watch.decision_timestamp.isoformat()}
        ]
        now = watch.decision_timestamp + timedelta(seconds=12)
        with patch(
            "custom_components.localshift.coordinator.coordinator.dt_util.now"
        ) as mock_now:
            mock_now.return_value = now
            coordinator._on_battery_power_change(make_event(1.2))
        assert coordinator.data.physical_response_lag_seconds == 12.0

    def test_listener_ignores_wrong_direction(self):
        coordinator = make_coordinator()
        coordinator.data.physical_response_watch = self._watch()
        coordinator._on_battery_power_change(make_event(2.0))
        assert coordinator.data.physical_response_lag_seconds is None
        assert coordinator.data.physical_response_watch is not None

    def test_listener_ignores_below_threshold(self):
        coordinator = make_coordinator()
        coordinator.data.physical_response_watch = self._watch()
        coordinator._on_battery_power_change(make_event(-0.2))
        assert coordinator.data.physical_response_lag_seconds is None

    def test_listener_ignores_unavailable_state(self):
        coordinator = make_coordinator()
        coordinator.data.physical_response_watch = self._watch()
        coordinator._on_battery_power_change(make_event("unavailable"))
        assert coordinator.data.physical_response_lag_seconds is None

    def test_listener_timeout_marks_timed_out(self):
        coordinator = make_coordinator()
        watch = self._watch()
        coordinator.data.physical_response_watch = watch
        coordinator.data.decision_lag_history = [
            {"decision_time": watch.decision_timestamp.isoformat()}
        ]
        coordinator._unsub_battery_power_listener = MagicMock()
        with patch(
            "custom_components.localshift.coordinator.coordinator.dt_util.now"
        ) as mock_now:
            mock_now.return_value = watch.timeout_at + timedelta(seconds=1)
            coordinator._on_battery_power_change(make_event(-1.5))
        assert coordinator.data.physical_response_timed_out is True
        assert coordinator.data.physical_response_watch is None
        assert coordinator.data.decision_lag_history[-1]["timed_out"] is True

    def test_listener_noop_without_watch(self):
        coordinator = make_coordinator()
        coordinator._on_battery_power_change(make_event(-1.5))
        assert coordinator.data.physical_response_lag_seconds is None

    def test_record_physical_response_noop_without_watch(self):
        coordinator = make_coordinator()
        coordinator._record_physical_response(timed_out=True)
        assert coordinator.data.physical_response_timed_out is False
        assert coordinator.data.physical_response_lag_seconds is None

    def test_evaluate_state_machine_starts_listener(self):
        coordinator = make_coordinator()
        coordinator.data.physical_response_watch = self._watch()
        coordinator._state_machine = MagicMock()
        coordinator._state_machine.evaluate_state_machine = AsyncMock()
        coordinator._computation_engine = MagicMock()
        coordinator._state_reader = MagicMock()
        with patch(
            "custom_components.localshift.coordinator.coordinator."
            "async_track_state_change_event"
        ) as track:
            track.return_value = MagicMock()
            asyncio.run(coordinator._evaluate_state_machine())
        track.assert_called_once()
        coordinator._state_machine.evaluate_state_machine.assert_awaited_once()

    def test_fast_tick_enforces_timeout(self):
        coordinator = make_coordinator()
        coordinator.data.physical_response_watch = self._watch(minutes_old=15)
        coordinator._tick_scheduler = MagicMock()
        now = dt_util.now()
        coordinator._handle_fast_tick(now)
        coordinator._tick_scheduler.handle_fast_tick.assert_called_once()
        assert coordinator.data.physical_response_timed_out is True


class TestDecisionLagSensor:
    def _sensor(self, **data_attrs):
        coordinator = MagicMock()
        entry = MagicMock()
        for key, val in data_attrs.items():
            setattr(coordinator.data, key, val)
        return DecisionLagSensor(coordinator, entry)

    def test_native_value_zero_when_never_measured(self):
        sensor = self._sensor(
            decision_lag_seconds=None,
            physical_response_lag_seconds=None,
        )
        sensor._update_from_coordinator()
        assert sensor._attr_native_value == 0.0

    def test_native_value_prefers_physical_lag(self):
        sensor = self._sensor(
            decision_lag_seconds=7.0,
            physical_response_lag_seconds=42.0,
        )
        sensor._update_from_coordinator()
        assert sensor._attr_native_value == 42.0

    def test_native_value_falls_back_to_command_lag(self):
        sensor = self._sensor(
            decision_lag_seconds=7.0,
            physical_response_lag_seconds=None,
        )
        sensor._update_from_coordinator()
        assert sensor._attr_native_value == 7.0

    def test_attributes_expose_both_phases(self):
        now = dt_aware(2026, 8, 27, 6, 0, 0)
        sensor = self._sensor(
            decision_lag_seconds=7.0,
            physical_response_lag_seconds=42.0,
            physical_response_timed_out=False,
            physical_response_watch=None,
            decision_lag_history=[
                {
                    "command_lag": 7.0,
                    "physical_lag": 42.0,
                    "observable": True,
                    "timed_out": False,
                }
            ],
            decision_timestamp=now,
            command_completion_timestamp=now,
        )
        attrs = sensor.extra_state_attributes
        assert attrs["command_lag_seconds"] == 7.0
        assert attrs["physical_lag_seconds"] == 42.0
        assert attrs["physical_lag_observable"] is True
        assert attrs["physical_response_timed_out"] is False
        assert attrs["avg_lag_24h"] == 7.0
        assert attrs["avg_physical_lag"] == 42.0
        assert attrs["command_completion_timestamp"] == now.isoformat()

    def test_attributes_when_no_history(self):
        sensor = self._sensor(
            decision_lag_seconds=None,
            physical_response_lag_seconds=None,
            physical_response_timed_out=False,
            physical_response_watch=None,
            decision_lag_history=[],
            decision_timestamp=None,
            command_completion_timestamp=None,
        )
        attrs = sensor.extra_state_attributes
        assert attrs["avg_lag_24h"] is None
        assert attrs["avg_physical_lag"] is None
        assert attrs["physical_lag_observable"] is False
        assert attrs["command_completion_timestamp"] is None
