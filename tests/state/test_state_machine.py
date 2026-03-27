"""Unit tests for State Machine.

Tests for battery mode transitions, debounce timers, health checks,
and error handling as specified in backlog-crit-002.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.localshift.const import (
    BACKUP_RESERVE_MAX_VALID,
    CONF_BATTERY_TARGET,
    DEFAULT_BATTERY_TARGET,
    TESLEMETRY_EXPORT_BATTERY_OK,
    TESLEMETRY_EXPORT_PV_ONLY,
    BatteryMode,
)
from custom_components.localshift.state.machine import StateMachine


def dt_aware(year, month, day, hour, minute=0, second=0):
    """Create a timezone-aware datetime in Australia/Sydney timezone."""
    return datetime(
        year, month, day, hour, minute, second, tzinfo=timezone(timedelta(hours=11))
    )


class TestTeslaOverrideDetection:
    """Test Tesla override detection."""

    @pytest.fixture
    def state_machine(
        self, mock_battery_controller, mock_notification_service, mock_entity_validator
    ):
        return StateMachine(
            mock_battery_controller,
            mock_notification_service,
            lambda key: True,
            lambda key, default=None: default,
            mock_entity_validator,
        )

    @pytest.mark.asyncio
    async def test_detect_tesla_override_not_detected(
        self, state_machine, coordinator_data
    ):
        """Test Tesla override not detected when not in override state."""
        coordinator_data.operation_mode = "self_consumption"
        coordinator_data.backup_reserve = 10
        result = state_machine._detect_tesla_override(coordinator_data)
        assert result is False

    @pytest.mark.asyncio
    async def test_detect_tesla_override_detected(
        self, state_machine, coordinator_data
    ):
        """Test Tesla override detected with 80% reserve."""
        coordinator_data.operation_mode = "self_consumption"
        coordinator_data.backup_reserve = 80
        result = state_machine._detect_tesla_override(coordinator_data)
        assert result is True

    @pytest.mark.asyncio
    async def test_detect_tesla_override_tolerance(
        self, state_machine, coordinator_data
    ):
        """Test Tesla override detection within tolerance."""
        coordinator_data.operation_mode = "self_consumption"
        coordinator_data.backup_reserve = 80.5
        result = state_machine._detect_tesla_override(coordinator_data)
        assert result is True

    @pytest.mark.asyncio
    async def test_is_tesla_override_active(self, state_machine):
        """Test checking if Tesla override is active."""
        assert state_machine.is_tesla_override_active() is False


class TestDecisionFingerprint:
    """Test decision fingerprint for price change detection."""

    @pytest.fixture
    def state_machine(
        self, mock_battery_controller, mock_notification_service, mock_entity_validator
    ):
        return StateMachine(
            mock_battery_controller,
            mock_notification_service,
            lambda key: True,
            lambda key, default=None: default,
            mock_entity_validator,
        )

    @pytest.mark.asyncio
    async def test_fingerprint_none_general_price(
        self, state_machine, coordinator_data
    ):
        """Test fingerprint returns None when general price missing."""
        coordinator_data.general_price = None
        coordinator_data.feed_in_price = 0.15
        result = state_machine._get_decision_fingerprint(coordinator_data)
        assert result is None

    @pytest.mark.asyncio
    async def test_fingerprint_none_feed_in_price(
        self, state_machine, coordinator_data
    ):
        """Test fingerprint returns None when feed-in price missing."""
        coordinator_data.general_price = 0.25
        coordinator_data.feed_in_price = None
        result = state_machine._get_decision_fingerprint(coordinator_data)
        assert result is None


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
    from custom_components.localshift.utils.validation import IntegrationStatus

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
    from custom_components.localshift.coordinator import CoordinatorData

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
    # Issue #349: Mark automation as ready for tests
    data.automation_ready = True
    return data


class TestModeConfig:
    """Test ModeConfig generation for each mode."""

    def test_get_mode_config_self_consumption_with_preserve_soc(
        self, state_machine, coordinator_data
    ):
        """SELF_CONSUMPTION uses preserve_soc when set."""
        coordinator_data.preserve_soc = 25.0

        config = state_machine._get_mode_config(
            BatteryMode.SELF_CONSUMPTION, coordinator_data
        )

        assert config.operation_mode == "self_consumption"
        assert config.backup_reserve == 25.0
        assert config.export_mode == TESLEMETRY_EXPORT_PV_ONLY
        assert config.grid_charging_allowed is False
        assert config.self_consumption_reserve == 25.0
        assert config.grid_charging_reserve is None
        assert config.proactive_export_reserve is None

    def test_get_mode_config_self_consumption_without_preserve_soc(
        self, state_machine, coordinator_data
    ):
        """SELF_CONSUMPTION defaults to 10 when preserve_soc missing."""
        coordinator_data.preserve_soc = None

        config = state_machine._get_mode_config(
            BatteryMode.SELF_CONSUMPTION, coordinator_data
        )

        assert config.backup_reserve == 10.0
        assert config.self_consumption_reserve == 10.0

    def test_get_mode_config_demand_block(self, state_machine, coordinator_data):
        """DEMAND_BLOCK mirrors self consumption settings."""
        coordinator_data.preserve_soc = 20.0

        config = state_machine._get_mode_config(
            BatteryMode.DEMAND_BLOCK, coordinator_data
        )

        assert config.operation_mode == "self_consumption"
        assert config.backup_reserve == 20.0
        assert config.self_consumption_reserve == 20.0

    def test_get_mode_config_grid_charging_clamps_reserve(
        self, state_machine, coordinator_data
    ):
        """GRID_CHARGING clamps reserve for 81-99% targets."""

        def _get_option(key, default):
            if key == CONF_BATTERY_TARGET:
                return 85
            return default

        state_machine._get_option = _get_option

        config = state_machine._get_mode_config(
            BatteryMode.GRID_CHARGING, coordinator_data
        )

        assert config.operation_mode == "backup"
        assert config.backup_reserve == BACKUP_RESERVE_MAX_VALID
        assert config.export_mode == TESLEMETRY_EXPORT_PV_ONLY
        assert config.grid_charging_allowed is True
        assert config.grid_charging_reserve == BACKUP_RESERVE_MAX_VALID

    def test_get_mode_config_grid_charging_default_target(
        self, state_machine, coordinator_data
    ):
        """GRID_CHARGING uses default target when option missing."""
        config = state_machine._get_mode_config(
            BatteryMode.GRID_CHARGING, coordinator_data
        )

        assert config.operation_mode == "backup"
        assert config.backup_reserve in (
            DEFAULT_BATTERY_TARGET,
            BACKUP_RESERVE_MAX_VALID,
        )

    def test_get_mode_config_boost_charging(self, state_machine, coordinator_data):
        """BOOST_CHARGING sets autonomous + full reserve."""
        config = state_machine._get_mode_config(
            BatteryMode.BOOST_CHARGING, coordinator_data
        )

        assert config.operation_mode == "autonomous"
        assert config.backup_reserve == 100.0
        assert config.grid_charging_allowed is True

    def test_get_mode_config_spike_discharge_conservative(
        self, state_machine, coordinator_data
    ):
        """SPIKE_DISCHARGE uses spike_reserve_soc in conservative mode."""
        coordinator_data.spike_in_conservative_mode = True
        coordinator_data.spike_reserve_soc = 15.0

        config = state_machine._get_mode_config(
            BatteryMode.SPIKE_DISCHARGE, coordinator_data
        )

        assert config.operation_mode == "autonomous"
        assert config.backup_reserve == 15.0
        assert config.export_mode == TESLEMETRY_EXPORT_BATTERY_OK

    def test_get_mode_config_spike_discharge_default_minimum(
        self, state_machine, coordinator_data
    ):
        """SPIKE_DISCHARGE uses minimum_target_soc when not conservative."""

        def _get_option(key, default):
            if key == "minimum_target_soc":
                return 12.0
            return default

        state_machine._get_option = _get_option
        coordinator_data.spike_in_conservative_mode = False

        config = state_machine._get_mode_config(
            BatteryMode.SPIKE_DISCHARGE, coordinator_data
        )

        assert config.backup_reserve == 12.0

    def test_get_mode_config_proactive_export(self, state_machine, coordinator_data):
        """PROACTIVE_EXPORT uses dynamic reserve based on SOC."""
        coordinator_data.soc = 50.0

        config = state_machine._get_mode_config(
            BatteryMode.PROACTIVE_EXPORT, coordinator_data
        )

        assert config.operation_mode == "autonomous"
        assert config.backup_reserve == 45.0
        assert config.export_mode == TESLEMETRY_EXPORT_BATTERY_OK
        assert config.proactive_export_reserve == 45.0

    def test_get_mode_config_proactive_export_minimum(
        self, state_machine, coordinator_data
    ):
        """PROACTIVE_EXPORT reserve never below 4%."""
        coordinator_data.soc = 6.0

        config = state_machine._get_mode_config(
            BatteryMode.PROACTIVE_EXPORT, coordinator_data
        )

        assert config.backup_reserve == 4.0

    def test_get_mode_config_hold(self, state_machine, coordinator_data):
        """HOLD preserves current SOC via elevated reserve."""

        def _get_option(key, default):
            if key == "minimum_target_soc":
                return 10.0
            return default

        state_machine._get_option = _get_option
        coordinator_data.soc = 60.0
        # Issue #559: mock read_fresh_soc to return the same as cached SOC
        state_machine._battery_controller.read_fresh_soc = MagicMock(return_value=60.0)

        config = state_machine._get_mode_config(BatteryMode.HOLD, coordinator_data)

        assert config.operation_mode == "self_consumption"
        assert config.backup_reserve == 60.0
        assert config.self_consumption_reserve == 60.0

    def test_get_mode_config_hold_respects_minimum_soc(
        self, state_machine, coordinator_data
    ):
        """HOLD respects minimum_target_soc if higher than SOC."""

        def _get_option(key, default):
            if key == "minimum_target_soc":
                return 15.0
            return default

        state_machine._get_option = _get_option
        coordinator_data.soc = 8.0
        # Issue #559: mock read_fresh_soc to return the same as cached SOC
        state_machine._battery_controller.read_fresh_soc = MagicMock(return_value=8.0)

        config = state_machine._get_mode_config(BatteryMode.HOLD, coordinator_data)

        assert config.backup_reserve == 15.0
        assert config.self_consumption_reserve == 15.0

    def test_get_mode_config_manual_returns_none(self, state_machine, coordinator_data):
        """MANUAL mode returns None (no config)."""
        config = state_machine._get_mode_config(BatteryMode.MANUAL, coordinator_data)

        assert config is None


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
        op, reserve, export, grid_charging = state_machine._get_expected_state_for_mode(
            BatteryMode.SELF_CONSUMPTION
        )
        assert op == "self_consumption"
        assert reserve == 10
        assert export == TESLEMETRY_EXPORT_PV_ONLY
        assert grid_charging == False

    def test_demand_block_expected_state(self, state_machine):
        """DEMAND_BLOCK should expect pv_only export mode."""
        op, reserve, export, grid_charging = state_machine._get_expected_state_for_mode(
            BatteryMode.DEMAND_BLOCK
        )
        assert op == "self_consumption"
        assert reserve == 10
        assert export == TESLEMETRY_EXPORT_PV_ONLY
        assert grid_charging == False

    def test_grid_charging_expected_state(self, state_machine):
        """GRID_CHARGING should expect backup mode with dynamic reserve.

        Grid charging uses backup mode for 3.3 kW charging.
        Reserve is clamped for Tesla firmware compatibility (81-99% → 80).
        The actual reserve is tracked in _grid_charging_reserve.
        Grid charging must be enabled for this mode.
        """
        op, reserve, export, grid_charging = state_machine._get_expected_state_for_mode(
            BatteryMode.GRID_CHARGING
        )
        assert op == "backup"
        assert reserve == -1  # Dynamic, tracked in _grid_charging_reserve
        assert export == TESLEMETRY_EXPORT_PV_ONLY
        assert grid_charging == True

    def test_boost_charging_expected_state(self, state_machine):
        """BOOST_CHARGING should expect 100% reserve and grid charging enabled."""
        op, reserve, export, grid_charging = state_machine._get_expected_state_for_mode(
            BatteryMode.BOOST_CHARGING
        )
        assert op == "autonomous"
        assert reserve == 100
        assert export == TESLEMETRY_EXPORT_PV_ONLY
        assert grid_charging == True

    def test_spike_discharge_expected_state(self, state_machine):
        """SPIKE_DISCHARGE should expect battery_ok export mode."""
        op, reserve, export, grid_charging = state_machine._get_expected_state_for_mode(
            BatteryMode.SPIKE_DISCHARGE
        )
        assert op == "autonomous"
        assert reserve == 10
        assert export == TESLEMETRY_EXPORT_BATTERY_OK
        assert grid_charging == False

    def test_proactive_export_expected_state(self, state_machine):
        """PROACTIVE_EXPORT should expect battery_ok export mode (backlog-high-020)."""
        op, reserve, export, grid_charging = state_machine._get_expected_state_for_mode(
            BatteryMode.PROACTIVE_EXPORT
        )
        assert op == "autonomous"
        assert reserve == 10  # Default for health check, actual is dynamic
        assert export == TESLEMETRY_EXPORT_BATTERY_OK
        assert grid_charging == False

    def test_manual_expected_state_empty(self, state_machine):
        """MANUAL mode should return empty expected state (skip validation)."""
        op, reserve, export, grid_charging = state_machine._get_expected_state_for_mode(
            BatteryMode.MANUAL
        )
        assert op == ""
        assert reserve == -1
        assert export == ""
        assert grid_charging == False


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
# POST-COMPUTE CALLBACK TESTS
# =============================================================================


@pytest.mark.skip(
    reason="Phase 3 removed post_compute_func from evaluate_state_machine"
)
class TestPostComputeCallback:
    """Tests for the optional post-compute callback hook."""

    def test_post_compute_callback_is_invoked(self, state_machine, coordinator_data):
        """State machine should invoke post_compute_func after recompute."""
        state_machine._commanded_mode = BatteryMode.SELF_CONSUMPTION
        coordinator_data.active_mode = BatteryMode.SELF_CONSUMPTION

        mock_engine = MagicMock()
        mock_engine.compute_derived_values = MagicMock()
        post_compute = MagicMock()

        asyncio.run(
            state_machine.evaluate_state_machine(
                coordinator_data,
                mock_engine,
                post_compute_func=post_compute,
            )
        )

        post_compute.assert_called_once()

    def test_post_compute_callback_failure_is_non_blocking(
        self, state_machine, coordinator_data
    ):
        """Exceptions from post_compute_func should not abort evaluation."""
        state_machine._commanded_mode = BatteryMode.SELF_CONSUMPTION
        coordinator_data.active_mode = BatteryMode.SELF_CONSUMPTION

        mock_engine = MagicMock()
        mock_engine.compute_derived_values = MagicMock()
        notify_func = MagicMock()

        def failing_post_compute() -> None:
            raise RuntimeError("shadow callback failure")

        asyncio.run(
            state_machine.evaluate_state_machine(
                coordinator_data,
                mock_engine,
                notify_func=notify_func,
                post_compute_func=failing_post_compute,
            )
        )

        # Evaluation continued and still notified listeners.
        notify_func.assert_called_once()


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
            "custom_components.localshift.state.machine.dt_util.now"
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
            "custom_components.localshift.state.machine.dt_util.now"
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
            "custom_components.localshift.state.machine.dt_util.now"
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
            "custom_components.localshift.state.machine.dt_util.now"
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
            "custom_components.localshift.state.machine.dt_util.now"
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


class TestStateMachineInternalBranches:
    """Targeted branch tests for uncovered state machine helpers."""

    def test_tesla_override_sets_flags_on_first_detection(
        self, state_machine, coordinator_data
    ):
        """First Tesla override detection should set tracking flags."""
        coordinator_data.operation_mode = "self_consumption"
        coordinator_data.backup_reserve = 80.0
        now = dt_aware(2026, 3, 1, 10, 0, 0)

        should_skip = state_machine._handle_tesla_override_state(coordinator_data, now)

        assert should_skip is True
        assert state_machine._tesla_override_detected is True
        assert state_machine._tesla_override_detected_at == now

    def test_tesla_override_cooldown_blocks_then_expires(
        self, state_machine, coordinator_data
    ):
        """Tesla override cooldown should block checks, then clear when elapsed."""
        coordinator_data.operation_mode = "self_consumption"
        coordinator_data.backup_reserve = 80.0
        detected_at = dt_aware(2026, 3, 1, 10, 0, 0)

        state_machine._handle_tesla_override_state(coordinator_data, detected_at)

        coordinator_data.operation_mode = "autonomous"
        coordinator_data.backup_reserve = 20.0

        released_at = dt_aware(2026, 3, 1, 10, 5, 0)
        should_skip_during_cooldown = state_machine._handle_tesla_override_state(
            coordinator_data, released_at
        )

        assert should_skip_during_cooldown is True
        assert state_machine._tesla_override_detected is False
        assert state_machine._tesla_override_released_at == released_at

        after_cooldown = released_at + timedelta(minutes=31)
        should_skip_after_cooldown = state_machine._handle_tesla_override_state(
            coordinator_data, after_cooldown
        )

        assert should_skip_after_cooldown is False
        assert state_machine._tesla_override_released_at is None

    def test_handle_debounce_timing_clears_stale_and_starts_timer(self, state_machine):
        """Debounce helper should clear stale timers and start desired timer."""
        stale_mode = BatteryMode.BOOST_CHARGING
        desired_mode = BatteryMode.PROACTIVE_EXPORT
        state_machine._mode_desired_since[stale_mode] = dt_aware(2026, 3, 1, 9, 0, 0)
        now = dt_aware(2026, 3, 1, 10, 0, 0)

        should_wait = state_machine._handle_debounce_timing(
            desired_mode, now, timedelta(minutes=2)
        )

        assert should_wait is True
        assert stale_mode not in state_machine._mode_desired_since
        assert state_machine._mode_desired_since[desired_mode] == now

    def test_handle_debounce_timing_allows_transition_after_elapsed(
        self, state_machine
    ):
        """Debounce helper should return False when elapsed meets debounce."""
        desired_mode = BatteryMode.PROACTIVE_EXPORT
        desired_since = dt_aware(2026, 3, 1, 10, 0, 0)
        state_machine._mode_desired_since[desired_mode] = desired_since

        should_wait = state_machine._handle_debounce_timing(
            desired_mode,
            desired_since + timedelta(minutes=2, seconds=1),
            timedelta(minutes=2),
        )

        assert should_wait is False

    def test_record_transition_metrics_records_lag_and_trims_history(
        self, state_machine, coordinator_data
    ):
        """Transition metrics should record lag, cap history, and clear decision state."""
        base = dt_aware(2026, 3, 1, 10, 0, 0)
        coordinator_data.active_mode = BatteryMode.SELF_CONSUMPTION
        coordinator_data.decision_mode = BatteryMode.GRID_CHARGING
        coordinator_data.decision_timestamp = base - timedelta(seconds=12)
        coordinator_data.decision_lag_history = [
            {"lag_seconds": float(i)} for i in range(60)
        ]

        with patch(
            "custom_components.localshift.state.machine.dt_util.now"
        ) as mock_now:
            mock_now.return_value = base
            state_machine._record_transition_metrics(
                coordinator_data,
                BatteryMode.GRID_CHARGING,
                dry_run=False,
            )

        assert state_machine._last_successful_transition == base
        assert coordinator_data.implementation_timestamp == base
        assert coordinator_data.decision_lag_seconds == 12.0
        assert len(coordinator_data.decision_lag_history) == 50
        assert coordinator_data.decision_timestamp is None
        assert coordinator_data.decision_mode is None

    def test_set_commanded_mode_updates_mode_and_clears_timers(self, state_machine):
        """Direct commanded mode setter should clear pending desired timers."""
        state_machine._mode_desired_since[BatteryMode.PROACTIVE_EXPORT] = dt_aware(
            2026, 3, 1, 10, 0, 0
        )

        state_machine.set_commanded_mode(BatteryMode.BOOST_CHARGING)

        assert state_machine._commanded_mode == BatteryMode.BOOST_CHARGING
        assert state_machine._mode_desired_since == {}

    def test_should_skip_health_check_when_manual_override(
        self, state_machine, coordinator_data
    ):
        """Health check skip helper should skip during manual override."""
        coordinator_data.manual_override = True

        should_skip = state_machine._should_skip_health_check(
            coordinator_data, dt_aware(2026, 3, 1, 10, 0, 0)
        )

        assert should_skip is True

    def test_soc_monitoring_logs_when_target_reached_in_grid_charging(
        self, state_machine, coordinator_data
    ):
        """SOC monitoring should hit target-reached branch for clamped grid charge."""

        def get_option(key, default):
            if key == CONF_BATTERY_TARGET:
                return 90.0
            return default

        state_machine._get_option = get_option
        state_machine._commanded_mode = BatteryMode.GRID_CHARGING
        coordinator_data.soc = 90.0

        result = asyncio.run(state_machine._handle_soc_monitoring(coordinator_data))

        assert result is False


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
            "custom_components.localshift.state.machine.dt_util.now"
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
            "custom_components.localshift.state.machine.dt_util.now"
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


class TestDecisionFingerprint:
    """Test fingerprint generation for gating mode transitions.

    Issue #622: Mode transitions are gated on price changes to prevent
    oscillation when optimizer re-runs with stable prices.
    """

    def test_fingerprint_includes_general_price(self, state_machine, coordinator_data):
        """Fingerprint changes when general_price changes."""
        coordinator_data.general_price = 0.25
        coordinator_data.feed_in_price = 0.08
        coordinator_data.price_spike = False

        fp1 = state_machine._get_decision_fingerprint(coordinator_data)

        coordinator_data.general_price = 0.30
        fp2 = state_machine._get_decision_fingerprint(coordinator_data)

        assert fp1 != fp2
        assert "0.2500" in fp1
        assert "0.3000" in fp2

    def test_fingerprint_includes_feed_in_price(self, state_machine, coordinator_data):
        """Fingerprint changes when feed_in_price changes."""
        coordinator_data.general_price = 0.25
        coordinator_data.feed_in_price = 0.08
        coordinator_data.price_spike = False

        fp1 = state_machine._get_decision_fingerprint(coordinator_data)

        coordinator_data.feed_in_price = 0.10
        fp2 = state_machine._get_decision_fingerprint(coordinator_data)

        assert fp1 != fp2
        assert "0.0800" in fp1
        assert "0.1000" in fp2

    def test_fingerprint_includes_spike(self, state_machine, coordinator_data):
        """Fingerprint changes when price_spike changes."""
        coordinator_data.general_price = 0.25
        coordinator_data.feed_in_price = 0.08
        coordinator_data.price_spike = False

        fp1 = state_machine._get_decision_fingerprint(coordinator_data)

        coordinator_data.price_spike = True
        fp2 = state_machine._get_decision_fingerprint(coordinator_data)

        assert fp1 != fp2
        assert "|False" in fp1
        assert "|True" in fp2

    def test_fingerprint_returns_none_if_general_price_missing(
        self, state_machine, coordinator_data
    ):
        """Returns None when general_price is unavailable."""
        coordinator_data.general_price = None
        coordinator_data.feed_in_price = 0.08
        coordinator_data.price_spike = False

        assert state_machine._get_decision_fingerprint(coordinator_data) is None

    def test_fingerprint_returns_none_if_feed_in_price_missing(
        self, state_machine, coordinator_data
    ):
        """Returns None when feed_in_price is unavailable."""
        coordinator_data.general_price = 0.25
        coordinator_data.feed_in_price = None
        coordinator_data.price_spike = False

        assert state_machine._get_decision_fingerprint(coordinator_data) is None

    def test_fingerprint_stable_with_same_prices(self, state_machine, coordinator_data):
        """Same prices produce same fingerprint."""
        coordinator_data.general_price = 0.25
        coordinator_data.feed_in_price = 0.08
        coordinator_data.price_spike = False

        fp1 = state_machine._get_decision_fingerprint(coordinator_data)
        fp2 = state_machine._get_decision_fingerprint(coordinator_data)

        assert fp1 == fp2
        assert fp1 == "0.2500|0.0800|False"


class TestModeTransitionGating:
    """Test that mode transitions are gated on fingerprint.

    Issue #622: Mode transitions only occur when price fingerprint changes.
    Optimizer always runs to update plan data.
    """

    @pytest.mark.asyncio
    async def test_optimizer_runs_even_if_price_unchanged(
        self,
        state_machine,
        coordinator_data,
        computation_engine,
    ):
        """Optimizer always runs even if price unchanged."""
        # Set up data with prices and a different desired mode
        coordinator_data.general_price = 0.25
        coordinator_data.feed_in_price = 0.08
        coordinator_data.price_spike = False
        # Set desired mode different from commanded mode (which is SELF_CONSUMPTION)
        coordinator_data.active_mode = BatteryMode.GRID_CHARGING

        # First evaluation sets fingerprint and transitions
        await state_machine.evaluate_state_machine(
            coordinator_data,
            computation_engine,
        )

        # Verify fingerprint was set
        fingerprint = state_machine._get_decision_fingerprint(coordinator_data)
        assert fingerprint is not None
        assert state_machine._last_decision_fingerprint is not None

    @pytest.mark.asyncio
    async def test_mode_transition_skipped_if_price_unchanged(
        self,
        state_machine,
        coordinator_data,
        computation_engine,
        mock_battery_controller,
    ):
        """Mode transition is skipped when fingerprint unchanged."""
        # Set up data with prices and different desired mode
        coordinator_data.general_price = 0.25
        coordinator_data.feed_in_price = 0.08
        coordinator_data.price_spike = False
        coordinator_data.active_mode = BatteryMode.GRID_CHARGING

        # First evaluation to set fingerprint and transition
        await state_machine.evaluate_state_machine(
            coordinator_data,
            computation_engine,
        )

        # Verify fingerprint was set
        fingerprint = state_machine._get_decision_fingerprint(coordinator_data)
        assert fingerprint is not None
        assert state_machine._last_decision_fingerprint is not None

        # Reset mock to track second call
        mock_battery_controller.set_force_charge.reset_mock()

        # Change desired mode but keep same prices
        coordinator_data.active_mode = BatteryMode.BOOST_CHARGING

        # Second evaluation should skip transition due to same prices
        await state_machine.evaluate_state_machine(
            coordinator_data,
            computation_engine,
        )

        # Mode transition should be skipped (no new calls)
        mock_battery_controller.set_force_charge.assert_not_called()

    @pytest.mark.asyncio
    async def test_first_evaluation_allows_transition(
        self,
        state_machine,
        coordinator_data,
        computation_engine,
        mock_battery_controller,
    ):
        """First evaluation (fingerprint=None) always allows transition."""
        # Set up data with prices
        coordinator_data.general_price = 0.25
        coordinator_data.feed_in_price = 0.08
        coordinator_data.price_spike = False
        coordinator_data.active_mode = BatteryMode.GRID_CHARGING

        # Skip debounce for first transition after startup grace
        state_machine._skip_next_debounce = True

        # First evaluation should allow transition
        await state_machine.evaluate_state_machine(
            coordinator_data,
            computation_engine,
        )

        # Mode transition should proceed on first evaluation
        mock_battery_controller.set_force_charge.assert_called_once()

    @pytest.mark.asyncio
    async def test_health_check_runs_even_if_price_unchanged(
        self,
        state_machine,
        coordinator_data,
        computation_engine,
        mock_battery_controller,
    ):
        """Health checks run regardless of fingerprint."""
        # Set up data with prices and same mode (no transition needed)
        coordinator_data.general_price = 0.25
        coordinator_data.feed_in_price = 0.08
        coordinator_data.price_spike = False
        coordinator_data.active_mode = BatteryMode.SELF_CONSUMPTION

        # First evaluation
        await state_machine.evaluate_state_machine(
            coordinator_data,
            computation_engine,
        )

        # Reset mock
        mock_battery_controller.verify_current_state.reset_mock()

        # Second evaluation with same prices, same mode
        await state_machine.evaluate_state_machine(
            coordinator_data,
            computation_engine,
        )

        # Health check (verify_current_state) should still be called
        mock_battery_controller.verify_current_state.assert_called()

    def test_debounce_completes_with_stable_prices(
        self,
        state_machine,
        coordinator_data,
        mock_battery_controller,
    ):
        """Debounce timer completes even when prices unchanged.

        Issue #622: PROACTIVE_EXPORT has 2-minute debounce. If prices are
        stable during the debounce period, the transition should still complete
        once debounce is satisfied.
        """
        import asyncio
        from unittest.mock import patch, MagicMock

        # Set up data with prices
        coordinator_data.general_price = 0.25
        coordinator_data.feed_in_price = 0.08
        coordinator_data.price_spike = False

        # Start in SELF_CONSUMPTION, desire PROACTIVE_EXPORT
        state_machine._commanded_mode = BatteryMode.SELF_CONSUMPTION
        coordinator_data.active_mode = BatteryMode.PROACTIVE_EXPORT

        mock_engine = MagicMock()
        mock_engine.compute_derived_values = MagicMock()

        # First evaluation: starts debounce (PROACTIVE_EXPORT has 2-min debounce)
        with patch(
            "custom_components.localshift.state.machine.dt_util.now"
        ) as mock_now:
            mock_now.return_value = dt_aware(2026, 2, 16, 16, 0, 0)
            asyncio.run(
                state_machine.evaluate_state_machine(coordinator_data, mock_engine)
            )

        # Should NOT have transitioned yet (debounce started)
        mock_battery_controller.set_proactive_export.assert_not_called()

        # Verify debounce timer started
        assert BatteryMode.PROACTIVE_EXPORT in state_machine._mode_desired_since

        # Reset mock
        mock_battery_controller.set_proactive_export.reset_mock()

        # Second evaluation: 1 minute later, prices UNCHANGED
        # Debounce should continue (not restart) even though price unchanged
        with patch(
            "custom_components.localshift.state.machine.dt_util.now"
        ) as mock_now:
            mock_now.return_value = dt_aware(2026, 2, 16, 16, 1, 0)
            asyncio.run(
                state_machine.evaluate_state_machine(coordinator_data, mock_engine)
            )

        # Should NOT have transitioned yet (1 min < 2 min debounce)
        mock_battery_controller.set_proactive_export.assert_not_called()

        # Third evaluation: 2+ minutes later, prices STILL UNCHANGED
        # Debounce should now be satisfied and transition should happen
        with patch(
            "custom_components.localshift.state.machine.dt_util.now"
        ) as mock_now:
            mock_now.return_value = dt_aware(2026, 2, 16, 16, 2, 1)
            asyncio.run(
                state_machine.evaluate_state_machine(coordinator_data, mock_engine)
            )

        # NOW transition should have happened despite unchanged prices
        mock_battery_controller.set_proactive_export.assert_called_once()
