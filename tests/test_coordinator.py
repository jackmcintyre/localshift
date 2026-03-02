"""Unit tests for coordinator."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.localshift.const import BatteryMode, PLATFORMS
from custom_components.localshift.coordinator import LocalShiftCoordinator


@pytest.fixture
def mock_hass_with_services():
    """Create a mock Home Assistant instance with services."""
    hass = MagicMock()
    hass.services = MagicMock()
    hass.services.async_call = AsyncMock()
    hass.states = MagicMock()
    # async_create_task receives coroutines - use a mock that consumes them
    # to avoid "coroutine was never awaited" warnings
    hass.async_create_task = MagicMock(side_effect=lambda coro, name=None: None)
    return hass


@pytest.fixture
def coordinator(mock_hass_with_services, mock_entry):
    """Create a LocalShiftCoordinator instance."""
    return LocalShiftCoordinator(mock_hass_with_services, mock_entry)


@pytest.fixture
def coordinator_data():
    """Create basic CoordinatorData for coordinator tests."""
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
    data.decision_log = []
    data.daily_forecast = []
    return data


# =============================================================================
# INITIALIZATION TESTS
# =============================================================================


class TestCoordinatorInitialization:
    """Tests for coordinator initialization."""

    def test_coordinator_initialization(self, mock_hass, mock_entry):
        """Test coordinator initialization."""
        coordinator = LocalShiftCoordinator(mock_hass, mock_entry)

        assert coordinator is not None
        assert coordinator.hass == mock_hass
        assert coordinator.entry == mock_entry
        assert coordinator.data is not None

    def test_coordinator_get_entity_id(self, mock_hass, mock_entry, mock_get_entity_id):
        """Test entity ID retrieval."""
        entity_id = mock_get_entity_id("teslemetry_soc")
        assert entity_id == "sensor.tesla_powerwall_soc"

    def test_coordinator_get_switch_state(self, mock_hass, mock_entry):
        """Test switch state retrieval."""
        coordinator = LocalShiftCoordinator(mock_hass, mock_entry)

        # Test with mock data
        coordinator._switch_states = {
            "automation_enabled": True,
        }

        state = coordinator.get_switch_state("automation_enabled")
        assert state is True

        # Test default
        state = coordinator.get_switch_state("nonexistent")
        assert state is False

    def test_coordinator_set_switch_state(self, mock_hass, mock_entry):
        """Test setting switch state."""
        coordinator = LocalShiftCoordinator(mock_hass, mock_entry)

        coordinator.set_switch_state("automation_enabled", True)
        assert coordinator.get_switch_state("automation_enabled") is True

        coordinator.set_switch_state("automation_enabled", False)
        assert coordinator.get_switch_state("automation_enabled") is False


# =============================================================================
# ASYNC_START TESTS
# =============================================================================


@pytest.mark.usefixtures("mock_storage")
class TestAsyncStart:
    """Tests for async_start method.

    Uses mock_storage fixture to mock HA's Store class for components
    that use persistent storage (DecisionOutcomeTracker, ParameterOptimizer,
    PatternAnalyzer, OptimizationController).
    """

    @pytest.mark.asyncio
    async def test_async_start_initializes_modules(self, coordinator, mock_recorder):
        """Test that async_start initializes all helper modules."""
        with (
            patch(
                "custom_components.localshift.coordinator.async_track_state_change_event"
            ) as mock_track_state,
            patch(
                "custom_components.localshift.coordinator.async_track_time_interval"
            ) as mock_track_time,
            patch(
                "custom_components.localshift.coordinator.async_track_time_change"
            ) as mock_track_time_change,
        ):
            mock_track_state.return_value = MagicMock()
            mock_track_time.return_value = MagicMock()
            mock_track_time_change.return_value = MagicMock()

            await coordinator.async_start()

            # Verify modules are initialized
            assert coordinator._state_reader is not None
            assert coordinator._battery_controller is not None
            assert coordinator._computation_engine is not None
            assert coordinator._state_machine is not None
            assert coordinator._notification_service is not None

    @pytest.mark.asyncio
    async def test_async_start_subscribes_to_events(self, coordinator, mock_recorder):
        """Test that async_start subscribes to state changes and timers."""
        with (
            patch(
                "custom_components.localshift.coordinator.async_track_state_change_event"
            ) as mock_track_state,
            patch(
                "custom_components.localshift.coordinator.async_track_time_interval"
            ) as mock_track_time,
            patch(
                "custom_components.localshift.coordinator.async_track_time_change"
            ) as mock_track_time_change,
        ):
            mock_track_state.return_value = MagicMock()
            mock_track_time.return_value = MagicMock()
            mock_track_time_change.return_value = MagicMock()

            await coordinator.async_start()

            # Verify state change subscription
            mock_track_state.assert_called_once()
            # Verify periodic timer subscriptions (periodic tick, learning save, solcast retry, etc.)
            # The exact count may vary based on coordinator implementation
            assert mock_track_time.call_count >= 2
            # Verify midnight and daily summary subscriptions
            assert mock_track_time_change.call_count >= 2

    @pytest.mark.asyncio
    async def test_async_start_sets_startup_grace(self, coordinator, mock_recorder):
        """Test that async_start sets startup grace period."""
        with (
            patch(
                "custom_components.localshift.coordinator.async_track_state_change_event"
            ) as mock_track_state,
            patch(
                "custom_components.localshift.coordinator.async_track_time_interval"
            ) as mock_track_time,
            patch(
                "custom_components.localshift.coordinator.async_track_time_change"
            ) as mock_track_time_change,
        ):
            mock_track_state.return_value = MagicMock()
            mock_track_time.return_value = MagicMock()
            mock_track_time_change.return_value = MagicMock()

            await coordinator.async_start()

            # State machine should have startup grace set
            assert coordinator._state_machine is not None


# =============================================================================
# STATE CHANGE HANDLER TESTS
# =============================================================================


class TestHandleStateChange:
    """Tests for _handle_state_change method."""

    def test_handle_state_change_reads_state(self, coordinator, coordinator_data):
        """Test that state change handler reads external state."""
        coordinator.data = coordinator_data

        # Mock state reader
        coordinator._state_reader = MagicMock()
        coordinator._state_reader.read_all_external_state = MagicMock()

        # Mock state machine
        coordinator._state_machine = MagicMock()
        coordinator._state_machine.in_mode_transition = False

        # Mock hass for async_create_task - consume coroutines to avoid warnings
        coordinator.hass.async_create_task = MagicMock(
            side_effect=lambda coro, name=None: None
        )

        # Create mock event
        event = MagicMock()

        coordinator._handle_state_change(event)

        # Verify state was read
        coordinator._state_reader.read_all_external_state.assert_called_once()

    def test_handle_state_change_skips_during_transition(
        self, coordinator, coordinator_data
    ):
        """Test that state change is skipped during mode transition."""
        coordinator.data = coordinator_data

        # Mock state machine to be in transition
        coordinator._state_machine = MagicMock()
        coordinator._state_machine.in_mode_transition = True

        # Mock state reader
        coordinator._state_reader = MagicMock()
        coordinator._state_reader.read_all_external_state = MagicMock()

        # Create mock event
        event = MagicMock()

        coordinator._handle_state_change(event)

        # State should NOT be read during transition
        coordinator._state_reader.read_all_external_state.assert_not_called()


# =============================================================================
# PERIODIC TICK TESTS
# =============================================================================


class TestHandlePeriodicTick:
    """Tests for _handle_periodic_tick method."""

    def test_handle_periodic_tick_reads_state(self, coordinator, coordinator_data):
        """Test that periodic tick reads external state."""
        coordinator.data = coordinator_data

        # Mock state reader
        coordinator._state_reader = MagicMock()
        coordinator._state_reader.read_all_external_state = MagicMock()

        # Mock computation engine
        coordinator._computation_engine = MagicMock()
        coordinator._computation_engine.async_get_recent_load_1hr = AsyncMock()
        coordinator._computation_engine.async_get_historical_hourly_averages = (
            AsyncMock()
        )

        # Mock cost tracker
        coordinator._cost_tracker = MagicMock()
        coordinator._cost_tracker.accumulate_costs = MagicMock()

        # Mock state machine
        coordinator._state_machine = MagicMock()

        # Mock price update time to avoid MagicMock comparison with timedelta
        coordinator._last_price_update = datetime(2026, 2, 16, 11, 0, 0)

        # Mock hass.states.get to return a proper state with last_updated (timezone-aware)
        mock_state = MagicMock()
        mock_state.last_updated = datetime(2026, 2, 16, 11, 59, 0, tzinfo=UTC)
        coordinator.hass.states.get = MagicMock(return_value=mock_state)

        # Mock hass for async_create_task - consume coroutines to avoid warnings
        coordinator.hass.async_create_task = MagicMock(
            side_effect=lambda coro, name=None: None
        )

        now = datetime(2026, 2, 16, 12, 0, 0, tzinfo=UTC)
        coordinator._handle_periodic_tick(now)

        # Verify state was read
        coordinator._state_reader.read_all_external_state.assert_called_once()

    def test_handle_periodic_tick_accumulates_costs(
        self, coordinator, coordinator_data
    ):
        """Test that periodic tick accumulates costs."""

        coordinator.data = coordinator_data

        # Mock state reader
        coordinator._state_reader = MagicMock()
        coordinator._state_reader.read_all_external_state = MagicMock()

        # Mock cost tracker
        coordinator._cost_tracker = MagicMock()
        coordinator._cost_tracker.accumulate_costs = MagicMock()

        # Mock computation engine
        coordinator._computation_engine = MagicMock()
        coordinator._computation_engine.async_get_recent_load_1hr = AsyncMock()
        coordinator._computation_engine.async_get_historical_hourly_averages = (
            AsyncMock()
        )

        # Mock state machine
        coordinator._state_machine = MagicMock()

        # Mock price update time to avoid MagicMock comparison with timedelta
        coordinator._last_price_update = datetime(2026, 2, 16, 11, 0, 0, tzinfo=UTC)

        # Mock hass.states.get to return a proper state with last_updated (timezone-aware)
        mock_state = MagicMock()
        mock_state.last_updated = datetime(2026, 2, 16, 11, 59, 0, tzinfo=UTC)
        coordinator.hass.states.get = MagicMock(return_value=mock_state)

        # Mock hass for async_create_task - consume coroutines to avoid warnings
        coordinator.hass.async_create_task = MagicMock(
            side_effect=lambda coro, name=None: None
        )

        now = datetime(2026, 2, 16, 12, 0, 0, tzinfo=UTC)
        coordinator._handle_periodic_tick(now)

        # Verify costs were accumulated
        coordinator._cost_tracker.accumulate_costs.assert_called_once()


# =============================================================================
# MIDNIGHT RESET TESTS
# =============================================================================


class TestHandleMidnightReset:
    """Tests for _handle_midnight_reset method."""

    def test_handle_midnight_reset_clears_accumulators(
        self, coordinator, coordinator_data
    ):
        """Test that midnight reset clears cost accumulators."""
        coordinator.data = coordinator_data
        coordinator.data.grid_import_cost = 10.0
        coordinator.data.grid_export_revenue = 5.0
        coordinator.data.battery_savings = 3.0
        coordinator.data.battery_charge_cost = 2.0
        coordinator.data.target_reached_today = True

        # Mock listeners
        coordinator._notify_listeners = MagicMock()

        now = datetime(2026, 2, 17, 0, 0, 0)
        coordinator._handle_midnight_reset(now)

        # Verify accumulators were reset
        assert coordinator.data.grid_import_cost == 0.0
        assert coordinator.data.grid_export_revenue == 0.0
        assert coordinator.data.battery_savings == 0.0
        assert coordinator.data.battery_charge_cost == 0.0
        assert coordinator.data.target_reached_today is False

    def test_handle_midnight_reset_notifies_listeners(
        self, coordinator, coordinator_data
    ):
        """Test that midnight reset notifies listeners."""
        coordinator.data = coordinator_data

        # Mock listeners
        coordinator._notify_listeners = MagicMock()

        now = datetime(2026, 2, 17, 0, 0, 0)
        coordinator._handle_midnight_reset(now)

        coordinator._notify_listeners.assert_called_once()


# =============================================================================
# STATE MACHINE EVALUATION TESTS
# =============================================================================


class TestEvaluateStateMachine:
    """Tests for state machine evaluation."""

    @pytest.mark.asyncio
    async def test_evaluate_state_machine_calls_evaluate(self, coordinator):
        """Test that _evaluate_state_machine calls state machine evaluate."""
        # Mock state machine
        coordinator._state_machine = MagicMock()
        coordinator._state_machine.evaluate_state_machine = AsyncMock()

        # Mock computation engine
        coordinator._computation_engine = MagicMock()

        # Mock state reader
        coordinator._state_reader = MagicMock()
        coordinator._state_reader.read_all_external_state = MagicMock()

        # Mock notify
        coordinator._notify_listeners = MagicMock()

        await coordinator._evaluate_state_machine()

        # Verify evaluate was called
        coordinator._state_machine.evaluate_state_machine.assert_called_once()
        call_kwargs = coordinator._state_machine.evaluate_state_machine.call_args.kwargs
        assert call_kwargs["post_compute_func"] == coordinator._run_shadow_optimizer

    @pytest.mark.asyncio
    async def test_async_evaluate_state_machine_public(self, coordinator):
        """Test public async_evaluate_state_machine method."""
        # Mock state machine
        coordinator._state_machine = MagicMock()
        coordinator._state_machine.evaluate_state_machine = AsyncMock()

        # Mock computation engine
        coordinator._computation_engine = MagicMock()

        # Mock state reader
        coordinator._state_reader = MagicMock()
        coordinator._state_reader.read_all_external_state = MagicMock()

        # Mock notify
        coordinator._notify_listeners = MagicMock()

        await coordinator.async_evaluate_state_machine()

        # Verify evaluate was called
        coordinator._state_machine.evaluate_state_machine.assert_called_once()
        call_kwargs = coordinator._state_machine.evaluate_state_machine.call_args.kwargs
        assert call_kwargs["post_compute_func"] == coordinator._run_shadow_optimizer


# =============================================================================
# BUTTON HANDLER TESTS
# =============================================================================


class TestModeHandlers:
    """Tests for battery mode handler methods."""

    @pytest.mark.asyncio
    async def test_async_set_self_consumption(self, coordinator, coordinator_data):
        """Test set_self_consumption button handler."""
        coordinator.data = coordinator_data

        # Mock battery controller
        coordinator._battery_controller = MagicMock()
        coordinator._battery_controller.set_self_consumption = AsyncMock()

        await coordinator.async_set_self_consumption()

        coordinator._battery_controller.set_self_consumption.assert_called_once_with(
            coordinator.data, False
        )

    @pytest.mark.asyncio
    async def test_async_set_battery_mode_grid_charging(
        self, coordinator, coordinator_data
    ):
        """Test async_set_battery_mode with GRID_CHARGING."""
        coordinator.data = coordinator_data

        # Mock battery controller
        coordinator._battery_controller = MagicMock()
        coordinator._battery_controller.set_force_charge = AsyncMock()

        await coordinator.async_set_battery_mode(BatteryMode.GRID_CHARGING)

        coordinator._battery_controller.set_force_charge.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_set_battery_mode_boost_charging(
        self, coordinator, coordinator_data
    ):
        """Test async_set_battery_mode with BOOST_CHARGING."""
        coordinator.data = coordinator_data

        # Mock battery controller
        coordinator._battery_controller = MagicMock()
        coordinator._battery_controller.set_boost_charge = AsyncMock()

        await coordinator.async_set_battery_mode(BatteryMode.BOOST_CHARGING)

        coordinator._battery_controller.set_boost_charge.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_set_battery_mode_spike_discharge(
        self, coordinator, coordinator_data
    ):
        """Test async_set_battery_mode with SPIKE_DISCHARGE."""
        coordinator.data = coordinator_data

        # Mock battery controller
        coordinator._battery_controller = MagicMock()
        coordinator._battery_controller.set_force_discharge = AsyncMock()

        await coordinator.async_set_battery_mode(BatteryMode.SPIKE_DISCHARGE)

        coordinator._battery_controller.set_force_discharge.assert_called_once()


# =============================================================================
# LISTENER TESTS
# =============================================================================


class TestListeners:
    """Tests for listener registration and notification."""

    def test_async_add_listener(self, coordinator):
        """Test adding a listener."""
        callback = MagicMock()
        unsubscribe = coordinator.async_add_listener(callback)

        assert callback in coordinator._update_callbacks
        assert callable(unsubscribe)

    def test_listener_unsubscribe(self, coordinator):
        """Test unsubscribing a listener."""
        callback = MagicMock()
        unsubscribe = coordinator.async_add_listener(callback)

        assert callback in coordinator._update_callbacks

        unsubscribe()

        assert callback not in coordinator._update_callbacks

    def test_notify_listeners(self, coordinator):
        """Test notifying all listeners."""
        callback1 = MagicMock()
        callback2 = MagicMock()

        coordinator.async_add_listener(callback1)
        coordinator.async_add_listener(callback2)

        coordinator._notify_listeners()

        callback1.assert_called_once()
        callback2.assert_called_once()


# =============================================================================
# OPTIONS TESTS
# =============================================================================


class TestOptions:
    """Tests for options handling."""

    def test_get_option_existing(self, coordinator, mock_entry):
        """Test getting an existing option."""
        mock_entry.options = {"battery_target": 85}
        coordinator.entry = mock_entry

        result = coordinator.get_option("battery_target", 90)

        assert result == 85

    def test_get_option_default(self, coordinator, mock_entry):
        """Test getting an option with default value."""
        mock_entry.options = {}
        coordinator.entry = mock_entry

        result = coordinator.get_option("nonexistent", 90)

        assert result == 90

    def test_parse_time_option(self, coordinator):
        """Test parsing time option."""
        coordinator.get_option = MagicMock(return_value="18:30:00")

        result = coordinator._parse_time_option("demand_window_start", "00:00:00")

        assert result.hour == 18
        assert result.minute == 30
        assert result.second == 0

    def test_parse_time_option_invalid(self, coordinator):
        """Test parsing invalid time option falls back to default."""
        coordinator.get_option = MagicMock(return_value="invalid")

        result = coordinator._parse_time_option("test", "18:00:00")

        assert result.hour == 18
        assert result.minute == 0


# =============================================================================
# ASYNC_STOP TESTS
# =============================================================================


class TestAsyncStop:
    """Tests for async_stop method."""

    @pytest.mark.asyncio
    async def test_async_stop_unsubscribes(self, coordinator):
        """Test that async_stop unsubscribes from all events."""
        # Set up mock unsubscribers
        mock_unsub1 = MagicMock()
        mock_unsub2 = MagicMock()
        mock_unsub3 = MagicMock()
        mock_unsub4 = MagicMock()

        coordinator._unsub_state = mock_unsub1
        coordinator._unsub_timer = mock_unsub2
        coordinator._unsub_midnight = mock_unsub3
        coordinator._unsub_daily_summary = mock_unsub4

        # Mock computation engine
        coordinator._computation_engine = MagicMock()
        coordinator._computation_engine.clear_historical_cache = MagicMock()

        await coordinator.async_stop()

        # Verify all unsubscribers were called
        mock_unsub1.assert_called_once()
        mock_unsub2.assert_called_once()
        mock_unsub3.assert_called_once()
        mock_unsub4.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_stop_clears_cache(self, coordinator):
        """Test that async_stop clears historical cache."""
        # Mock computation engine
        coordinator._computation_engine = MagicMock()
        coordinator._computation_engine.clear_historical_cache = MagicMock()

        await coordinator.async_stop()

        coordinator._computation_engine.clear_historical_cache.assert_called_once()


# =============================================================================
# SHADOW OPTIMIZER CONFIG PLUMBING TESTS
# =============================================================================


class TestShadowOptimizerConfigPlumbing:
    """Tests that _run_shadow_optimizer passes required config keys.

    Issue #409: The coordinator was not passing CONF_ALLOW_DW_ENTRY_UNDER_TARGET
    in config_options, so the DP optimizer always received allow_dw_entry_under_target=False
    regardless of the switch state.
    """

    def test_config_options_includes_allow_dw_entry_under_target(
        self, coordinator, coordinator_data
    ):
        """CONF_ALLOW_DW_ENTRY_UNDER_TARGET must be in config_options passed to runner.

        Captures the config_options dict by mocking run_shadow_optimizer and
        asserting the key is present with the value derived from get_option.
        """
        coordinator.data = coordinator_data

        captured = {}

        def fake_run_shadow_optimizer(data, config_options):
            captured.update(config_options)

        # run_shadow_optimizer is imported locally inside _run_shadow_optimizer(),
        # so we must patch it at its definition site in the shadow runner module.
        with patch(
            "custom_components.localshift.computation_engine_lib"
            ".optimizer_shadow_runner.run_shadow_optimizer",
            side_effect=fake_run_shadow_optimizer,
        ):
            coordinator._run_shadow_optimizer()

        from custom_components.localshift.const import CONF_ALLOW_DW_ENTRY_UNDER_TARGET

        assert CONF_ALLOW_DW_ENTRY_UNDER_TARGET in captured, (
            "config_options must include CONF_ALLOW_DW_ENTRY_UNDER_TARGET so the "
            "DP optimizer respects the allow_dw_entry_under_target switch (#409)"
        )

    def test_config_options_allow_dw_entry_under_target_reflects_option(
        self, mock_hass_with_services, mock_entry
    ):
        """Switch value in options must be forwarded to config_options.

        When the allow_dw_entry_under_target option is True, the coordinator
        must pass True in config_options (not always the default False).
        """
        from custom_components.localshift.const import (
            CONF_ALLOW_DW_ENTRY_UNDER_TARGET,
            CONF_BATTERY_TARGET,
            CONF_MINIMUM_TARGET_SOC,
            CONF_OPTIMIZER_CONTROL_MODE,
        )
        from custom_components.localshift.coordinator_data import CoordinatorData

        # Build an entry whose options include allow_dw_entry_under_target=True
        mock_entry.options = {
            **mock_entry.options,
            CONF_ALLOW_DW_ENTRY_UNDER_TARGET: True,
            CONF_OPTIMIZER_CONTROL_MODE: "shadow",
            CONF_BATTERY_TARGET: 80.0,
            CONF_MINIMUM_TARGET_SOC: 10.0,
        }
        coordinator = LocalShiftCoordinator(mock_hass_with_services, mock_entry)
        coordinator.data = CoordinatorData()

        captured = {}

        def fake_run_shadow_optimizer(data, config_options):
            captured.update(config_options)

        # run_shadow_optimizer is imported locally inside _run_shadow_optimizer(),
        # so we must patch it at its definition site in the shadow runner module.
        with patch(
            "custom_components.localshift.computation_engine_lib"
            ".optimizer_shadow_runner.run_shadow_optimizer",
            side_effect=fake_run_shadow_optimizer,
        ):
            coordinator._run_shadow_optimizer()

        assert captured.get(CONF_ALLOW_DW_ENTRY_UNDER_TARGET) is True, (
            "allow_dw_entry_under_target=True in options must propagate to config_options"
        )
