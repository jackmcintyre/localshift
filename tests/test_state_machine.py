"""Unit tests for State Machine.

Tests for battery mode transitions, debounce timers, health checks,
and error handling as specified in backlog-crit-002.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.localshift.const import (
    TESLEMETRY_EXPORT_BATTERY_OK,
    TESLEMETRY_EXPORT_PV_ONLY,
    BatteryMode,
)
from custom_components.localshift.state_machine import StateMachine


def dt_aware(year, month, day, hour, minute=0, second=0):
    """Create a timezone-aware datetime in Australia/Sydney timezone."""
    return datetime(
        year, month, day, hour, minute, second, tzinfo=timezone(timedelta(hours=11))
    )


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
    return service


@pytest.fixture
def mock_get_switch_state():
    """Mock function to get switch states."""

    def _get_switch_state(key):
        switch_states = {
            "automation_enabled": True,
            "dry_run": False,
            "spike_discharge_enabled": True,
            "demand_window_block": False,
            "manual_override": False,
        }
        return switch_states.get(key, False)

    return _get_switch_state


@pytest.fixture
def mock_get_option():
    """Mock function to get configuration options."""

    def _get_option(key, default):
        options = {
            "manual_override_timeout": 24.0,
        }
        return options.get(key, default)

    return _get_option


@pytest.fixture
def mock_entity_validator():
    """Create a mock EntityValidator."""
    from custom_components.localshift.entity_validator import IntegrationStatus

    validator = MagicMock()
    validator.should_allow_automation = MagicMock(return_value=True)
    validator.status = IntegrationStatus.OK
    validator.errors = []
    validator.warnings = []
    return validator


@pytest.fixture
def state_machine(
    mock_battery_controller,
    mock_notification_service,
    mock_get_switch_state,
    mock_get_option,
    mock_entity_validator,
):
    """Create a StateMachine instance."""
    return StateMachine(
        mock_battery_controller,
        mock_notification_service,
        mock_get_switch_state,
        mock_get_option,
        mock_entity_validator,
    )


@pytest.fixture
def coordinator_data():
    """Create basic CoordinatorData for state machine tests."""
    from custom_components.localshift.coordinator_data import CoordinatorData

    data = CoordinatorData()
    data.soc = 50.0
    data.operation_mode = "autonomous"
    data.backup_reserve = 50
    data.grid_power_kw = 0.0
    data.load_power_kw = 0.5
    data.solar_power_kw = 0.0
    data.general_price = 0.25
    data.feed_in_price = 0.08
    data.price_spike = False
    data.manual_override = False
    data.force_charge_active = False
    data.boost_charge_active = False
    data.force_discharge_active = False
    data.decision_log = []
    data.daily_forecast = []
    data.daily_forecast_soc_15min = []
    return data


# =============================================================================
# DEBOUNCE TESTS
# =============================================================================


class TestDebounceTimers:
    """Tests for debounce timer logic per backlog-crit-002."""

    def test_immediate_debounce_for_spike_discharge(self, state_machine):
        """Spike discharge should have immediate (0) debounce."""
        debounce = state_machine.get_debounce_for_transition(
            BatteryMode.SELF_CONSUMPTION, BatteryMode.SPIKE_DISCHARGE
        )
        assert debounce == timedelta(0)

    def test_immediate_debounce_for_demand_block(self, state_machine):
        """Demand block should have immediate (0) debounce."""
        debounce = state_machine.get_debounce_for_transition(
            BatteryMode.SELF_CONSUMPTION, BatteryMode.DEMAND_BLOCK
        )
        assert debounce == timedelta(0)

    def test_immediate_debounce_for_manual(self, state_machine):
        """Manual mode should have immediate (0) debounce."""
        debounce = state_machine.get_debounce_for_transition(
            BatteryMode.SELF_CONSUMPTION, BatteryMode.MANUAL
        )
        assert debounce == timedelta(0)

    def test_immediate_debounce_leaving_spike_discharge(self, state_machine):
        """Leaving spike discharge should be immediate."""
        debounce = state_machine.get_debounce_for_transition(
            BatteryMode.SPIKE_DISCHARGE, BatteryMode.SELF_CONSUMPTION
        )
        assert debounce == timedelta(0)

    def test_immediate_debounce_leaving_demand_block(self, state_machine):
        """Leaving demand block should be immediate."""
        debounce = state_machine.get_debounce_for_transition(
            BatteryMode.DEMAND_BLOCK, BatteryMode.SELF_CONSUMPTION
        )
        assert debounce == timedelta(0)

    def test_proactive_export_has_debounce(self, state_machine):
        """PROACTIVE_EXPORT should have 2-minute debounce (backlog-high-021)."""
        debounce = state_machine.get_debounce_for_transition(
            BatteryMode.SELF_CONSUMPTION, BatteryMode.PROACTIVE_EXPORT
        )
        assert debounce == timedelta(minutes=2)

    def test_leaving_proactive_export_is_immediate(self, state_machine):
        """Leaving PROACTIVE_EXPORT should be immediate."""
        debounce = state_machine.get_debounce_for_transition(
            BatteryMode.PROACTIVE_EXPORT, BatteryMode.SELF_CONSUMPTION
        )
        assert debounce == timedelta(0)

    def test_grid_charging_has_immediate_debounce(self, state_machine):
        """Grid charging should have immediate debounce (hysteresis prevents oscillation)."""
        debounce = state_machine.get_debounce_for_transition(
            BatteryMode.SELF_CONSUMPTION, BatteryMode.GRID_CHARGING
        )
        assert debounce == timedelta(0)

    def test_boost_charging_has_immediate_debounce(self, state_machine):
        """Boost charging should have immediate debounce (hysteresis prevents oscillation)."""
        debounce = state_machine.get_debounce_for_transition(
            BatteryMode.SELF_CONSUMPTION, BatteryMode.BOOST_CHARGING
        )
        assert debounce == timedelta(0)


# =============================================================================
# HARDWARE MODE INFERENCE TESTS
# =============================================================================


class TestHardwareModeInference:
    """Tests for inferring current hardware mode from state."""

    def test_infer_self_consumption(self, state_machine, coordinator_data):
        """Should infer SELF_CONSUMPTION when no special flags active."""
        coordinator_data.force_charge_active = False
        coordinator_data.boost_charge_active = False
        coordinator_data.force_discharge_active = False

        mode = state_machine.infer_current_hardware_mode(coordinator_data)
        assert mode == BatteryMode.SELF_CONSUMPTION

    def test_infer_grid_charging(self, state_machine, coordinator_data):
        """Should infer GRID_CHARGING when force_charge_active."""
        coordinator_data.force_charge_active = True
        coordinator_data.boost_charge_active = False
        coordinator_data.force_discharge_active = False

        mode = state_machine.infer_current_hardware_mode(coordinator_data)
        assert mode == BatteryMode.GRID_CHARGING

    def test_infer_boost_charging(self, state_machine, coordinator_data):
        """Should infer BOOST_CHARGING when boost_charge_active."""
        coordinator_data.force_charge_active = False
        coordinator_data.boost_charge_active = True
        coordinator_data.force_discharge_active = False

        mode = state_machine.infer_current_hardware_mode(coordinator_data)
        assert mode == BatteryMode.BOOST_CHARGING

    def test_infer_spike_discharge(self, state_machine, coordinator_data):
        """Should infer SPIKE_DISCHARGE when force_discharge_active."""
        coordinator_data.force_charge_active = False
        coordinator_data.boost_charge_active = False
        coordinator_data.force_discharge_active = True

        mode = state_machine.infer_current_hardware_mode(coordinator_data)
        assert mode == BatteryMode.SPIKE_DISCHARGE

    def test_infer_spike_discharge_takes_priority(
        self, state_machine, coordinator_data
    ):
        """Spike discharge should take priority over other modes."""
        coordinator_data.force_charge_active = True
        coordinator_data.boost_charge_active = True
        coordinator_data.force_discharge_active = True

        mode = state_machine.infer_current_hardware_mode(coordinator_data)
        assert mode == BatteryMode.SPIKE_DISCHARGE


# =============================================================================
# EXPECTED STATE FOR MODE TESTS
# =============================================================================


class TestExpectedStateForMode:
    """Tests for _get_expected_state_for_mode health check logic."""

    def test_self_consumption_expected_state(self, state_machine):
        """SELF_CONSUMPTION should expect pv_only export mode."""
        op, reserve, export = state_machine._get_expected_state_for_mode(
            BatteryMode.SELF_CONSUMPTION
        )
        assert op == "self_consumption"
        assert reserve == 10
        assert export == TESLEMETRY_EXPORT_PV_ONLY

    def test_demand_block_expected_state(self, state_machine):
        """DEMAND_BLOCK should expect pv_only export mode."""
        op, reserve, export = state_machine._get_expected_state_for_mode(
            BatteryMode.DEMAND_BLOCK
        )
        assert op == "self_consumption"
        assert reserve == 10
        assert export == TESLEMETRY_EXPORT_PV_ONLY

    def test_grid_charging_expected_state(self, state_machine):
        """GRID_CHARGING should expect backup mode with dynamic reserve.

        Grid charging uses backup mode for 3.3 kW charging.
        Reserve is clamped for Tesla firmware compatibility (81-99% → 80).
        The actual reserve is tracked in _grid_charging_reserve.
        """
        op, reserve, export = state_machine._get_expected_state_for_mode(
            BatteryMode.GRID_CHARGING
        )
        assert op == "backup"
        assert reserve == -1  # Dynamic, tracked in _grid_charging_reserve
        assert export == TESLEMETRY_EXPORT_PV_ONLY

    def test_boost_charging_expected_state(self, state_machine):
        """BOOST_CHARGING should expect 100% reserve."""
        op, reserve, export = state_machine._get_expected_state_for_mode(
            BatteryMode.BOOST_CHARGING
        )
        assert op == "autonomous"
        assert reserve == 100
        assert export == TESLEMETRY_EXPORT_PV_ONLY

    def test_spike_discharge_expected_state(self, state_machine):
        """SPIKE_DISCHARGE should expect battery_ok export mode."""
        op, reserve, export = state_machine._get_expected_state_for_mode(
            BatteryMode.SPIKE_DISCHARGE
        )
        assert op == "autonomous"
        assert reserve == 10
        assert export == TESLEMETRY_EXPORT_BATTERY_OK

    def test_proactive_export_expected_state(self, state_machine):
        """PROACTIVE_EXPORT should expect battery_ok export mode (backlog-high-020)."""
        op, reserve, export = state_machine._get_expected_state_for_mode(
            BatteryMode.PROACTIVE_EXPORT
        )
        assert op == "autonomous"
        assert reserve == 10  # Default for health check, actual is dynamic
        assert export == TESLEMETRY_EXPORT_BATTERY_OK

    def test_manual_expected_state_empty(self, state_machine):
        """MANUAL mode should return empty expected state (skip validation)."""
        op, reserve, export = state_machine._get_expected_state_for_mode(
            BatteryMode.MANUAL
        )
        assert op == ""
        assert reserve == -1
        assert export == ""


# =============================================================================
# STARTUP GRACE PERIOD TESTS
# =============================================================================


class TestStartupGracePeriod:
    """Tests for startup grace period logic."""

    def test_startup_grace_prevents_evaluation(
        self, state_machine, coordinator_data, mock_battery_controller
    ):
        """State machine should skip evaluation during grace period."""
        state_machine.set_startup_grace(grace_seconds=30)

        # Create a mock computation engine
        mock_engine = MagicMock()
        mock_engine.compute_derived_values = MagicMock()

        # Try to evaluate
        import asyncio

        asyncio.run(state_machine.evaluate_state_machine(coordinator_data, mock_engine))

        # Should not have issued any commands
        mock_battery_controller.set_self_consumption.assert_not_called()

    def test_startup_grace_infers_mode_after_expiry(
        self, state_machine, coordinator_data
    ):
        """After grace period, should infer hardware mode."""
        # Set a very short grace period that's already expired (timezone-aware)
        state_machine._startup_grace_until = dt_aware(2020, 1, 1, 0, 0, 0)

        # Create a mock computation engine
        mock_engine = MagicMock()
        mock_engine.compute_derived_values = MagicMock()

        # Set up for a mode match
        coordinator_data.force_charge_active = False
        coordinator_data.boost_charge_active = False
        coordinator_data.force_discharge_active = False

        asyncio.run(state_machine.evaluate_state_machine(coordinator_data, mock_engine))

        # Should have inferred SELF_CONSUMPTION
        assert state_machine._commanded_mode == BatteryMode.SELF_CONSUMPTION


# =============================================================================
# MODE TRANSITION TESTS
# =============================================================================


class TestModeTransitions:
    """Tests for mode transition execution."""

    def test_transition_to_self_consumption(
        self, state_machine, coordinator_data, mock_battery_controller
    ):
        """Test transition to SELF_CONSUMPTION mode from SPIKE_DISCHARGE (immediate)."""
        # Start from SPIKE_DISCHARGE (has immediate transition to SELF_CONSUMPTION)
        state_machine._commanded_mode = BatteryMode.SPIKE_DISCHARGE
        coordinator_data.active_mode = BatteryMode.SELF_CONSUMPTION
        # Set hardware state to match SPIKE_DISCHARGE so inference works
        coordinator_data.force_discharge_active = True

        mock_engine = MagicMock()
        mock_engine.compute_derived_values = MagicMock()

        asyncio.run(state_machine.evaluate_state_machine(coordinator_data, mock_engine))

        mock_battery_controller.set_self_consumption.assert_called_once()

    def test_transition_to_grid_charging(
        self, state_machine, coordinator_data, mock_battery_controller
    ):
        """Test transition to GRID_CHARGING mode (immediate - 0 debounce)."""
        state_machine._commanded_mode = BatteryMode.SELF_CONSUMPTION
        coordinator_data.active_mode = BatteryMode.GRID_CHARGING

        mock_engine = MagicMock()
        mock_engine.compute_derived_values = MagicMock()

        # Mock dt_util.now to return a time far enough in the past to satisfy debounce
        with patch(
            "custom_components.localshift.state_machine.dt_util.now"
        ) as mock_now:
            # First call starts debounce, second call returns time after debounce
            mock_now.return_value = dt_aware(2026, 2, 16, 16, 5, 0)  # 5 min later
            asyncio.run(
                state_machine.evaluate_state_machine(coordinator_data, mock_engine)
            )

    def test_transition_to_boost_charging(
        self, state_machine, coordinator_data, mock_battery_controller
    ):
        """Test transition to BOOST_CHARGING mode (immediate - 0 debounce)."""
        state_machine._commanded_mode = BatteryMode.SELF_CONSUMPTION
        coordinator_data.active_mode = BatteryMode.BOOST_CHARGING

        mock_engine = MagicMock()
        mock_engine.compute_derived_values = MagicMock()

        with patch(
            "custom_components.localshift.state_machine.dt_util.now"
        ) as mock_now:
            mock_now.return_value = dt_aware(2026, 2, 16, 16, 5, 0)
            asyncio.run(
                state_machine.evaluate_state_machine(coordinator_data, mock_engine)
            )

    def test_transition_to_spike_discharge(
        self, state_machine, coordinator_data, mock_battery_controller
    ):
        """Test transition to SPIKE_DISCHARGE mode (immediate - 0 debounce)."""
        state_machine._commanded_mode = BatteryMode.SELF_CONSUMPTION
        coordinator_data.active_mode = BatteryMode.SPIKE_DISCHARGE

        mock_engine = MagicMock()
        mock_engine.compute_derived_values = MagicMock()

        asyncio.run(state_machine.evaluate_state_machine(coordinator_data, mock_engine))

        mock_battery_controller.set_force_discharge.assert_called_once()

    def test_transition_to_proactive_export(
        self, state_machine, coordinator_data, mock_battery_controller
    ):
        """Test transition to PROACTIVE_EXPORT mode (2 min debounce)."""
        state_machine._commanded_mode = BatteryMode.SELF_CONSUMPTION
        coordinator_data.active_mode = BatteryMode.PROACTIVE_EXPORT

        mock_engine = MagicMock()
        mock_engine.compute_derived_values = MagicMock()

        with patch(
            "custom_components.localshift.state_machine.dt_util.now"
        ) as mock_now:
            mock_now.return_value = dt_aware(
                2026, 2, 16, 16, 3, 0
            )  # 3 min later > 2 min debounce
            asyncio.run(
                state_machine.evaluate_state_machine(coordinator_data, mock_engine)
            )

    def test_failed_transition_keeps_previous_mode(
        self, state_machine, coordinator_data, mock_battery_controller
    ):
        """Failed transition should not update commanded_mode."""
        state_machine._commanded_mode = BatteryMode.SELF_CONSUMPTION
        coordinator_data.active_mode = BatteryMode.SPIKE_DISCHARGE  # Use immediate mode

        # Make the transition fail
        mock_battery_controller.set_force_discharge = AsyncMock(return_value=False)

        mock_engine = MagicMock()
        mock_engine.compute_derived_values = MagicMock()

        asyncio.run(state_machine.evaluate_state_machine(coordinator_data, mock_engine))

        # Should still be SELF_CONSUMPTION
        assert state_machine._commanded_mode == BatteryMode.SELF_CONSUMPTION


# =============================================================================
# DEBOUNCE BEHAVIOR TESTS
# =============================================================================


class TestDebounceBehavior:
    """Tests for debounce timer behavior during evaluation."""

    def test_debounce_waits_full_period_for_proactive_export(
        self, state_machine, coordinator_data, mock_battery_controller
    ):
        """Should wait full debounce period before transitioning to PROACTIVE_EXPORT."""
        state_machine._commanded_mode = BatteryMode.SELF_CONSUMPTION
        coordinator_data.active_mode = BatteryMode.PROACTIVE_EXPORT

        mock_engine = MagicMock()
        mock_engine.compute_derived_values = MagicMock()

        # First evaluation should start debounce timer (but not transition yet)
        with patch(
            "custom_components.localshift.state_machine.dt_util.now"
        ) as mock_now:
            mock_now.return_value = dt_aware(2026, 2, 16, 16, 0, 0)
            asyncio.run(
                state_machine.evaluate_state_machine(coordinator_data, mock_engine)
            )

        # Should NOT have transitioned yet (2 minute debounce, only 0 minutes elapsed)
        mock_battery_controller.set_proactive_export.assert_not_called()

        # Should have recorded when PROACTIVE_EXPORT was first desired
        assert BatteryMode.PROACTIVE_EXPORT in state_machine._mode_desired_since

    def test_debounce_clears_stale_timers(
        self, state_machine, coordinator_data, mock_battery_controller
    ):
        """Debounce timers for non-desired modes should be cleared."""
        state_machine._commanded_mode = BatteryMode.SELF_CONSUMPTION

        # Set up stale timer for BOOST_CHARGING
        state_machine._mode_desired_since[BatteryMode.BOOST_CHARGING] = dt_aware(
            2020, 1, 1, 0, 0, 0
        )

        # Use PROACTIVE_EXPORT which has debounce, so timer tracking happens
        coordinator_data.active_mode = BatteryMode.PROACTIVE_EXPORT

        mock_engine = MagicMock()
        mock_engine.compute_derived_values = MagicMock()

        with patch(
            "custom_components.localshift.state_machine.dt_util.now"
        ) as mock_now:
            mock_now.return_value = dt_aware(2026, 2, 16, 16, 0, 0)
            asyncio.run(
                state_machine.evaluate_state_machine(coordinator_data, mock_engine)
            )

        # Stale timer should be cleared
        assert BatteryMode.BOOST_CHARGING not in state_machine._mode_desired_since
        # New timer for desired mode should be started
        assert BatteryMode.PROACTIVE_EXPORT in state_machine._mode_desired_since


# =============================================================================
# AUTOMATION DISABLED TEST
# =============================================================================


class TestAutomationDisabled:
    """Tests for behavior when automation is disabled."""

    def test_automation_disabled_sets_manual_mode(
        self, state_machine, coordinator_data, mock_battery_controller
    ):
        """When automation disabled, should set MANUAL mode."""

        # Override the switch state to disable automation
        def disabled_switch_state(key):
            if key == "automation_enabled":
                return False
            return False

        state_machine._get_switch_state = disabled_switch_state
        state_machine._commanded_mode = BatteryMode.SELF_CONSUMPTION
        coordinator_data.active_mode = BatteryMode.GRID_CHARGING

        mock_engine = MagicMock()
        mock_engine.compute_derived_values = MagicMock()

        asyncio.run(state_machine.evaluate_state_machine(coordinator_data, mock_engine))

        # Should be in MANUAL mode
        assert state_machine._commanded_mode == BatteryMode.MANUAL
        # Should not issue any commands
        mock_battery_controller.set_force_charge.assert_not_called()


# =============================================================================
# HEALTH CHECK TESTS
# =============================================================================


class TestHealthCheck:
    """Tests for health check validation."""

    def test_health_check_verifies_state(
        self, state_machine, coordinator_data, mock_battery_controller
    ):
        """Health check should verify hardware state matches commanded mode."""
        state_machine._commanded_mode = BatteryMode.SELF_CONSUMPTION
        coordinator_data.active_mode = BatteryMode.SELF_CONSUMPTION

        mock_engine = MagicMock()
        mock_engine.compute_derived_values = MagicMock()

        asyncio.run(state_machine.evaluate_state_machine(coordinator_data, mock_engine))

        # Health check should have been called
        mock_battery_controller.verify_current_state.assert_called()

    def test_health_check_correction_cooldown(
        self, state_machine, coordinator_data, mock_battery_controller
    ):
        """Health check should respect correction cooldown."""
        state_machine._commanded_mode = BatteryMode.SELF_CONSUMPTION
        coordinator_data.active_mode = BatteryMode.SELF_CONSUMPTION

        mock_engine = MagicMock()
        mock_engine.compute_derived_values = MagicMock()

        # First, fail the health check
        mock_battery_controller.verify_current_state = AsyncMock(return_value=False)

        # First evaluation - should attempt correction
        asyncio.run(state_machine.evaluate_state_machine(coordinator_data, mock_engine))

        first_call_count = mock_battery_controller.set_self_consumption.call_count

        # Second evaluation immediately - should be blocked by cooldown
        asyncio.run(state_machine.evaluate_state_machine(coordinator_data, mock_engine))

        # Should not have made another correction attempt
        assert (
            mock_battery_controller.set_self_consumption.call_count == first_call_count
        )


# =============================================================================
# MANUAL OVERRIDE TESTS
# =============================================================================


class TestManualOverride:
    """Tests for manual override handling."""

    def test_manual_override_timeout_clears(
        self, state_machine, coordinator_data, mock_battery_controller
    ):
        """Manual override should clear after timeout."""
        state_machine._commanded_mode = BatteryMode.MANUAL
        coordinator_data.active_mode = BatteryMode.MANUAL
        coordinator_data.manual_override = True

        # Set manual override time to 25 hours ago (beyond default 24h timeout)
        state_machine._manual_override_set_at = dt_aware(2020, 1, 1, 0, 0, 0)

        mock_engine = MagicMock()
        mock_engine.compute_derived_values = MagicMock()
        mock_engine._forecast_change_tracker = MagicMock()
        mock_engine._forecast_change_tracker._last_forecast_time = None

        asyncio.run(state_machine.evaluate_state_machine(coordinator_data, mock_engine))

        # Manual override should be cleared
        assert coordinator_data.manual_override == False


# =============================================================================
# ENTITY AVAILABILITY BLOCKING TESTS (Issue #161)
# =============================================================================


class TestEntityAvailabilityBlocking:
    """Tests for blocking mode transitions when required entities unavailable.

    Issue #161: When REQUIRED entities (prices, SOC, operation_mode) are
    unavailable, mode transitions should be blocked to prevent incorrect
    automation decisions.
    """

    def test_mode_transition_blocked_when_required_entity_unavailable(
        self, state_machine, coordinator_data, mock_battery_controller
    ):
        """Mode transition should be blocked when a required entity is unavailable."""
        state_machine._commanded_mode = BatteryMode.SELF_CONSUMPTION
        coordinator_data.active_mode = BatteryMode.GRID_CHARGING

        # Mock entity validator to report unavailable required entity
        mock_validator = MagicMock()
        mock_validator.should_allow_automation = MagicMock(return_value=False)
        state_machine.entity_validator = mock_validator

        mock_engine = MagicMock()
        mock_engine.compute_derived_values = MagicMock()

        asyncio.run(state_machine.evaluate_state_machine(coordinator_data, mock_engine))

        # Should NOT have transitioned - should stay in SELF_CONSUMPTION
        assert state_machine._commanded_mode == BatteryMode.SELF_CONSUMPTION
        # Should not have issued any commands
        mock_battery_controller.set_force_charge.assert_not_called()

    def test_mode_transition_allowed_when_entities_healthy(
        self, state_machine, coordinator_data, mock_battery_controller
    ):
        """Mode transition should proceed when all required entities are healthy."""
        state_machine._commanded_mode = BatteryMode.SELF_CONSUMPTION
        coordinator_data.active_mode = (
            BatteryMode.SPIKE_DISCHARGE
        )  # Immediate transition

        # Mock entity validator to report all entities healthy
        mock_validator = MagicMock()
        mock_validator.should_allow_automation = MagicMock(return_value=True)
        state_machine.entity_validator = mock_validator

        mock_engine = MagicMock()
        mock_engine.compute_derived_values = MagicMock()

        asyncio.run(state_machine.evaluate_state_machine(coordinator_data, mock_engine))

        # Should have transitioned to SPIKE_DISCHARGE
        assert state_machine._commanded_mode == BatteryMode.SPIKE_DISCHARGE
        # Should have issued the command
        mock_battery_controller.set_force_discharge.assert_called_once()

    def test_current_mode_maintained_when_entities_unavailable(
        self, state_machine, coordinator_data, mock_battery_controller
    ):
        """Current mode should be maintained when entities become unavailable.

        This tests the key requirement from #161: don't reset mode, maintain it.
        """
        # Start in GRID_CHARGING mode
        state_machine._commanded_mode = BatteryMode.GRID_CHARGING

        # Desired mode is different (e.g., SPIKE_DISCHARGE)
        coordinator_data.active_mode = BatteryMode.SPIKE_DISCHARGE

        # But entities are unavailable
        mock_validator = MagicMock()
        mock_validator.should_allow_automation = MagicMock(return_value=False)
        state_machine.entity_validator = mock_validator

        mock_engine = MagicMock()
        mock_engine.compute_derived_values = MagicMock()

        asyncio.run(state_machine.evaluate_state_machine(coordinator_data, mock_engine))

        # Should STILL be in GRID_CHARGING (maintained, not reset)
        assert state_machine._commanded_mode == BatteryMode.GRID_CHARGING
        # Should not have issued any transition commands
        mock_battery_controller.set_force_discharge.assert_not_called()

    def test_blocked_transition_preserves_desired_timer(
        self, state_machine, coordinator_data, mock_battery_controller
    ):
        """When transition is blocked, the desired mode timer should be preserved.

        This ensures proper debounce when entities become available again.
        """
        state_machine._commanded_mode = BatteryMode.SELF_CONSUMPTION
        coordinator_data.active_mode = BatteryMode.GRID_CHARGING

        # Mock entity validator to block automation
        mock_validator = MagicMock()
        mock_validator.should_allow_automation = MagicMock(return_value=False)
        state_machine.entity_validator = mock_validator

        mock_engine = MagicMock()
        mock_engine.compute_derived_values = MagicMock()

        # Set a timer for the desired mode
        state_machine._mode_desired_since[BatteryMode.GRID_CHARGING] = dt_aware(
            2020, 1, 1, 0, 0, 0
        )

        asyncio.run(state_machine.evaluate_state_machine(coordinator_data, mock_engine))

        # Timer should still be present (preserved for when entities become available)
        assert BatteryMode.GRID_CHARGING in state_machine._mode_desired_since


# =============================================================================
# RE-ENTRANT CALL PREVENTION TESTS
# =============================================================================


class TestReentrantCallPrevention:
    """Tests for re-entrant call prevention."""

    def test_in_mode_transition_flag(self, state_machine):
        """in_mode_transition property should reflect _in_mode_transition flag."""
        state_machine._in_mode_transition = True
        assert state_machine.in_mode_transition == True

        state_machine._in_mode_transition = False
        assert state_machine.in_mode_transition == False


# =============================================================================
# SKIP DEBOUNCE FLAG RESET TESTS (Issue #340)
# =============================================================================


class TestSkipDebounceFlagReset:
    """Tests for _skip_next_debounce flag reset behavior.

    Issue #340: The _skip_next_debounce flag was not being reset when
    desired == commanded, causing it to persist incorrectly across
    evaluation cycles and skip debounce on later transitions.
    """

    def test_skip_debounce_reset_when_no_change_needed(
        self, state_machine, coordinator_data, mock_battery_controller
    ):
        """_skip_next_debounce should be reset when no transition is needed.

        This prevents the flag from persisting and incorrectly skipping
        debounce on a later transition.
        """
        # Simulate startup grace ending with skip_next_debounce set
        state_machine._startup_grace_until = dt_aware(2020, 1, 1, 0, 0, 0)  # Expired
        state_machine._skip_next_debounce = True  # Would be set by grace period ending

        # Mode matches - no transition needed
        state_machine._commanded_mode = BatteryMode.SELF_CONSUMPTION
        coordinator_data.active_mode = BatteryMode.SELF_CONSUMPTION

        mock_engine = MagicMock()
        mock_engine.compute_derived_values = MagicMock()

        asyncio.run(state_machine.evaluate_state_machine(coordinator_data, mock_engine))

        # Flag should be reset to False
        assert state_machine._skip_next_debounce == False

    def test_skip_debounce_not_persisting_across_evaluations(
        self, state_machine, coordinator_data, mock_battery_controller
    ):
        """Debounce should NOT be skipped on transitions after a 'no change' evaluation.

        This is the core regression test for Issue #340:
        1. Startup grace ends, skip_next_debounce = True
        2. First evaluation: desired == commanded, no transition
        3. Second evaluation: desired != commanded, should NOT skip debounce
        """
        # Step 1: Simulate startup grace ending
        state_machine._startup_grace_until = dt_aware(2020, 1, 1, 0, 0, 0)  # Expired

        # Step 2: First evaluation - no change needed
        state_machine._commanded_mode = BatteryMode.SELF_CONSUMPTION
        coordinator_data.active_mode = BatteryMode.SELF_CONSUMPTION

        mock_engine = MagicMock()
        mock_engine.compute_derived_values = MagicMock()

        with patch(
            "custom_components.localshift.state_machine.dt_util.now"
        ) as mock_now:
            mock_now.return_value = dt_aware(2026, 2, 27, 9, 26, 0)
            asyncio.run(
                state_machine.evaluate_state_machine(coordinator_data, mock_engine)
            )

        # Flag should be reset
        assert state_machine._skip_next_debounce == False

        # Step 3: Second evaluation - transition needed (PROACTIVE_EXPORT has 2-min debounce)
        coordinator_data.active_mode = BatteryMode.PROACTIVE_EXPORT

        with patch(
            "custom_components.localshift.state_machine.dt_util.now"
        ) as mock_now:
            # First call starts debounce
            mock_now.return_value = dt_aware(2026, 2, 27, 9, 30, 0)
            asyncio.run(
                state_machine.evaluate_state_machine(coordinator_data, mock_engine)
            )

        # Should NOT have transitioned yet - debounce should be active
        mock_battery_controller.set_proactive_export.assert_not_called()

        # Should have started debounce timer
        assert BatteryMode.PROACTIVE_EXPORT in state_machine._mode_desired_since

    def test_skip_debounce_works_for_actual_first_transition(
        self, state_machine, coordinator_data, mock_battery_controller
    ):
        """Skip debounce should work correctly for the actual first transition.

        When startup grace ends and there IS a mismatch, debounce should be skipped.
        """
        # Simulate startup grace ending
        state_machine._startup_grace_until = dt_aware(2020, 1, 1, 0, 0, 0)  # Expired

        # Hardware is in SELF_CONSUMPTION but desired is different
        state_machine._commanded_mode = BatteryMode.SELF_CONSUMPTION
        coordinator_data.active_mode = BatteryMode.SPIKE_DISCHARGE  # Immediate mode

        mock_engine = MagicMock()
        mock_engine.compute_derived_values = MagicMock()

        asyncio.run(state_machine.evaluate_state_machine(coordinator_data, mock_engine))

        # Should have transitioned immediately (skip_next_debounce was True)
        mock_battery_controller.set_force_discharge.assert_called_once()

        # Flag should now be False
        assert state_machine._skip_next_debounce == False
