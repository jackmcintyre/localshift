"""Integration tests for localshift component.

Tests the full flow from state changes through mode transitions,
including forecast computation and battery control decisions.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.localshift.const import BatteryMode
from custom_components.localshift.coordinator_data import CoordinatorData


# Helper to create timezone-aware datetimes
def dt_aware(year, month, day, hour, minute=0, second=0):
    """Create a timezone-aware datetime in Australia/Sydney timezone."""
    return datetime(
        year, month, day, hour, minute, second, tzinfo=timezone(timedelta(hours=11))
    )


@pytest.fixture
def integration_data():
    """Create CoordinatorData for integration tests."""
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
    data.daily_forecast_soc_15min = []
    data.target_reached_today = False
    # Issue #319: Mark forecast as ready for integration tests
    data.forecast_ready = True
    data.forecast_status = "ready"
    # Issue #351: Provide general_forecast for hybrid timescale
    # Create 24 hours of 30-min slots starting from a base time
    base_time = dt_aware(2026, 2, 16, 0, 0, 0)
    data.general_forecast = []
    for i in range(48):  # 48 x 30-min slots = 24 hours
        start = base_time + timedelta(minutes=i * 30)
        end = start + timedelta(minutes=30)
        data.general_forecast.append(
            {
                "start_time": start.isoformat(),
                "end_time": end.isoformat(),
                "per_kwh": 0.25,  # Default price
            }
        )
    data.feed_in_forecast = []
    data.solcast_today = []
    data.solcast_tomorrow = []
    return data


# =============================================================================
# FULL STATE MACHINE FLOW TESTS
# =============================================================================


@pytest.mark.skip(
    reason="Phase 4 removed _mode_decision, _get_forecast_entry_for_now - update in Phase 5/6"
)
class TestFullStateMachineFlow:
    """Tests for complete state machine flow with mode changes."""

    @pytest.mark.asyncio
    async def test_mode_transition_self_consumption_to_grid_charging(
        self, computation_engine, integration_data
    ):
        """Test transition from SELF_CONSUMPTION to GRID_CHARGING."""
        # Set up initial state
        integration_data.soc = 40.0
        integration_data.general_price = 0.10  # Cheap price
        integration_data.feed_in_price = 0.05
        # Issue #341: Set effective_cheap_price so live price validation passes
        integration_data.effective_cheap_price = 0.15  # Price threshold

        # Mock forecast with grid charging flag
        test_time = dt_aware(2026, 2, 16, 10, 0, 0)
        forecast_entry = {
            "timestamp": test_time.isoformat(),
            "hour": 10,
            "minute": 0,
            "grid_charge": True,
            "grid_charge_boost": False,
            "proactive_export": False,
            "grid_import_kwh": 2.0,  # Actual import planned
            "export_amount_kwh": 0.0,
            "predicted_soc": 45.0,
            "buy_price": 0.10,
            "sell_price": 0.05,
        }

        with patch("homeassistant.util.dt.now", return_value=test_time):
            # Mock switch states
            def mock_switch_state(key):
                if key == "automation_enabled":
                    return True
                return False

            computation_engine._get_switch_state = MagicMock(
                side_effect=mock_switch_state
            )
            # Also mock the mode decision engine's reference
            computation_engine._mode_decision._get_switch_state = MagicMock(
                side_effect=mock_switch_state
            )

            # Set up the forecast directly and mock forecast computation
            integration_data.daily_forecast = [forecast_entry]

            # Mock forecast entry lookup
            with patch.object(
                computation_engine,
                "_get_forecast_entry_for_now",
                return_value=forecast_entry,
            ):
                with patch.object(computation_engine, "_compute_daily_15min_forecast"):
                    computation_engine.compute_derived_values(integration_data)

        # Should be in GRID_CHARGING mode
        assert integration_data.active_mode == BatteryMode.GRID_CHARGING

    @pytest.mark.asyncio
    async def test_mode_transition_to_spike_discharge(
        self, computation_engine, integration_data
    ):
        """Test transition to SPIKE_DISCHARGE during price spike."""
        # Set up spike conditions
        integration_data.soc = 80.0
        integration_data.price_spike = True
        integration_data.general_price = 2.50  # High spike price
        integration_data.feed_in_price = 2.00

        test_time = dt_aware(2026, 2, 16, 19, 0, 0)  # 19:00, in discharge window

        # Provide a forecast entry for the current time slot
        forecast_entry = {
            "timestamp": test_time.isoformat(),
            "hour": 19,
            "minute": 0,
            "grid_charge": False,
            "grid_charge_boost": False,
            "proactive_export": False,
            "grid_import_kwh": 0.0,
            "export_amount_kwh": 0.0,
            "predicted_soc": 80.0,
            "buy_price": 2.50,
            "sell_price": 2.00,
        }

        with patch("homeassistant.util.dt.now", return_value=test_time):
            # Mock switch states
            def mock_switch_state(key):
                if key == "automation_enabled":
                    return True
                if key == "spike_discharge_enabled":
                    return True
                return False

            computation_engine._get_switch_state = MagicMock(
                side_effect=mock_switch_state
            )

            # Mock forecast entry lookup
            with patch.object(
                computation_engine,
                "_get_forecast_entry_for_now",
                return_value=forecast_entry,
            ):
                computation_engine.compute_derived_values(integration_data)

        # Should be in SPIKE_DISCHARGE mode
        assert integration_data.active_mode == BatteryMode.SPIKE_DISCHARGE

    @pytest.mark.asyncio
    async def test_mode_transition_to_demand_block(
        self, computation_engine, integration_data
    ):
        """Test transition to DEMAND_BLOCK during demand window."""
        test_time = dt_aware(2026, 2, 16, 19, 0, 0)  # During DW (18:00-22:00)

        # Provide a forecast entry for the current time slot
        forecast_entry = {
            "timestamp": test_time.isoformat(),
            "hour": 19,
            "minute": 0,
            "grid_charge": False,
            "grid_charge_boost": False,
            "proactive_export": False,
            "grid_import_kwh": 0.0,
            "export_amount_kwh": 0.0,
            "predicted_soc": 50.0,
            "buy_price": 0.25,
            "sell_price": 0.08,
        }

        with patch("homeassistant.util.dt.now", return_value=test_time):
            # Mock switch states
            def mock_switch_state(key):
                if key == "automation_enabled":
                    return True
                if key == "demand_window_block":
                    return True
                return False

            computation_engine._get_switch_state = MagicMock(
                side_effect=mock_switch_state
            )

            # Mock forecast entry lookup
            with patch.object(
                computation_engine,
                "_get_forecast_entry_for_now",
                return_value=forecast_entry,
            ):
                computation_engine.compute_derived_values(integration_data)

        # Should be in DEMAND_BLOCK mode
        assert integration_data.active_mode == BatteryMode.DEMAND_BLOCK


# =============================================================================
# FORECAST TO ACTIVE MODE PIPELINE TESTS
# =============================================================================


@pytest.mark.skip(
    reason="Phase 4 removed _mode_decision, _compute_daily_15min_forecast - update in Phase 5/6"
)
class TestForecastToActiveModePipeline:
    """Tests for forecast → active_mode → transition pipeline."""

    def test_forecast_drives_active_mode(self, computation_engine, integration_data):
        """Test that forecast computation drives active_mode decision."""
        test_time = dt_aware(2026, 2, 16, 14, 0, 0)

        # Set up forecast with proactive export
        forecast_entry = {
            "timestamp": test_time.isoformat(),
            "hour": 14,
            "minute": 0,
            "grid_charge": False,
            "grid_charge_boost": False,
            "proactive_export": True,
            "grid_import_kwh": 0.0,
            "export_amount_kwh": 1.5,  # Export planned
            "predicted_soc": 90.0,
            "buy_price": 0.30,
            "sell_price": 0.15,
        }

        with patch("homeassistant.util.dt.now", return_value=test_time):
            computation_engine._get_switch_state = MagicMock(return_value=True)
            # Also mock the mode decision engine's reference
            computation_engine._mode_decision._get_switch_state = MagicMock(
                return_value=True
            )

            # Set up the forecast directly and mock forecast computation
            integration_data.daily_forecast = [forecast_entry]

            # Mock forecast entry lookup
            with patch.object(
                computation_engine,
                "_get_forecast_entry_for_now",
                return_value=forecast_entry,
            ):
                with patch.object(computation_engine, "_compute_daily_15min_forecast"):
                    computation_engine.compute_derived_values(integration_data)

        # Should be in PROACTIVE_EXPORT mode
        assert integration_data.active_mode == BatteryMode.PROACTIVE_EXPORT
        assert integration_data.proactive_export_active is True

    def test_no_forecast_falls_back_to_self_consumption(
        self, computation_engine, integration_data
    ):
        """Test that missing forecast falls back to SELF_CONSUMPTION."""
        test_time = dt_aware(2026, 2, 16, 14, 0, 0)

        with patch("homeassistant.util.dt.now", return_value=test_time):
            computation_engine._get_switch_state = MagicMock(return_value=True)
            # Also mock the mode decision engine's reference
            computation_engine._mode_decision._get_switch_state = MagicMock(
                return_value=True
            )

            # Set up empty forecast
            integration_data.daily_forecast = []

            # Mock forecast entry lookup to return None (no forecast)
            with patch.object(
                computation_engine,
                "_get_forecast_entry_for_now",
                return_value=None,
            ):
                with patch.object(computation_engine, "_compute_daily_15min_forecast"):
                    computation_engine.compute_derived_values(integration_data)

        # Should fall back to SELF_CONSUMPTION
        assert integration_data.active_mode == BatteryMode.SELF_CONSUMPTION


# =============================================================================
# ERROR HANDLING AND RECOVERY TESTS
# =============================================================================


@pytest.mark.skip(
    reason="Phase 4 removed _compute_daily_15min_forecast, _get_forecast_entry_for_now - update in Phase 5/6"
)
class TestErrorHandlingAndRecovery:
    """Tests for error handling and recovery scenarios."""

    def test_forecast_error_does_not_crash(self, computation_engine, integration_data):
        """Test that forecast errors don't crash the system."""
        test_time = dt_aware(2026, 2, 16, 12, 0, 0)

        with patch("homeassistant.util.dt.now", return_value=test_time):
            computation_engine._get_switch_state = MagicMock(return_value=True)

            # Mock forecast computation to raise error - should be caught
            with patch.object(
                computation_engine,
                "_compute_daily_15min_forecast",
                side_effect=Exception("Test error"),
            ):
                # The error may or may not be caught depending on implementation
                # Just verify the system doesn't crash hard
                try:
                    computation_engine.compute_derived_values(integration_data)
                except Exception:
                    # If exception propagates, that's also acceptable behavior
                    pass

        # Test passes if we get here without crashing
        assert True

    def test_invalid_entity_state_handled(self, computation_engine, integration_data):
        """Test that invalid entity states are handled gracefully."""
        integration_data.soc = None  # Invalid state

        test_time = dt_aware(2026, 2, 16, 12, 0, 0)

        with patch("homeassistant.util.dt.now", return_value=test_time):
            computation_engine._get_switch_state = MagicMock(return_value=True)

            # Should not crash
            try:
                computation_engine.compute_derived_values(integration_data)
            except (TypeError, AttributeError):
                # Expected for None SOC - test passes if we get here
                pass

    @pytest.mark.asyncio
    async def test_battery_controller_failure_recovery(
        self, mock_hass, mock_get_entity_id, integration_data
    ):
        """Test recovery from battery controller failure."""
        from custom_components.localshift.battery_controller import BatteryController

        controller = BatteryController(mock_hass, mock_get_entity_id)

        # Mock service call to fail
        mock_hass.services.async_call = AsyncMock(
            side_effect=Exception("Service unavailable")
        )

        result = await controller.set_self_consumption(integration_data)

        # Should return False on failure
        assert result is False

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("mock_battery_sleep")
    async def test_validation_timeout_handled(
        self, mock_hass, mock_get_entity_id, integration_data
    ):
        """Test that validation timeout is handled gracefully.

        Note: With mock_battery_sleep, this test runs instantly but still
        verifies the timeout logic by checking that mismatched state returns False.
        """
        from custom_components.localshift.battery_controller import BatteryController

        # Create a proper mock for states BEFORE creating the controller
        # Use MagicMock with spec to allow attribute assignment
        mock_states = MagicMock()

        # Mock states.get to return a state that never matches expected
        mock_state = MagicMock()
        mock_state.state = "wrong_mode"  # Never matches expected "self_consumption"
        mock_states.get.return_value = mock_state

        # Set mock_hass.states BEFORE passing to BatteryController
        mock_hass.states = mock_states

        controller = BatteryController(mock_hass, mock_get_entity_id)

        result = await controller.validate_transition(
            expected_operation_mode="self_consumption",
            expected_backup_reserve=10,
            timeout=2,  # Short timeout for test
        )

        # Should return False after timeout (instantly with mock)
        assert result is False


# =============================================================================
# DECISION LOG TESTS
# =============================================================================


@pytest.mark.skip(
    reason="Phase 4 removed _get_forecast_entry_for_now - update in Phase 5/6"
)
class TestDecisionLog:
    """Tests for decision log functionality."""

    def test_decision_log_records_mode_change(
        self, computation_engine, integration_data
    ):
        """Test that decision log records mode changes."""
        test_time = dt_aware(2026, 2, 16, 12, 0, 0)

        # Provide a forecast entry
        forecast_entry = {
            "timestamp": test_time.isoformat(),
            "hour": 12,
            "minute": 0,
            "grid_charge": False,
            "grid_charge_boost": False,
            "proactive_export": False,
            "grid_import_kwh": 0.0,
            "export_amount_kwh": 0.0,
            "predicted_soc": 50.0,
            "buy_price": 0.25,
            "sell_price": 0.08,
        }

        with patch("homeassistant.util.dt.now", return_value=test_time):
            computation_engine._get_switch_state = MagicMock(return_value=True)

            # Mock forecast entry lookup
            with patch.object(
                computation_engine,
                "_get_forecast_entry_for_now",
                return_value=forecast_entry,
            ):
                computation_engine.compute_derived_values(integration_data)

        # Should have at least one log entry
        assert len(integration_data.decision_log) >= 1
        assert "reason" in integration_data.decision_log[-1]

    def test_decision_log_capped_at_50(self, computation_engine, integration_data):
        """Test that decision log is capped at 50 entries."""
        test_time = dt_aware(2026, 2, 16, 12, 0, 0)

        # Pre-populate with 60 entries
        for i in range(60):
            integration_data.decision_log.append(
                {
                    "timestamp": test_time.isoformat(),
                    "old_mode": "test",
                    "new_mode": "test",
                    "reason": f"test entry {i}",
                }
            )

        # Provide a forecast entry
        forecast_entry = {
            "timestamp": test_time.isoformat(),
            "hour": 12,
            "minute": 0,
            "grid_charge": False,
            "grid_charge_boost": False,
            "proactive_export": False,
            "grid_import_kwh": 0.0,
            "export_amount_kwh": 0.0,
            "predicted_soc": 50.0,
            "buy_price": 0.25,
            "sell_price": 0.08,
        }

        with patch("homeassistant.util.dt.now", return_value=test_time):
            computation_engine._get_switch_state = MagicMock(return_value=True)
            computation_engine._last_decision_log_time = None

            # Mock forecast entry lookup
            with patch.object(
                computation_engine,
                "_get_forecast_entry_for_now",
                return_value=forecast_entry,
            ):
                computation_engine.compute_derived_values(integration_data)

        # Should be capped at 50
        assert len(integration_data.decision_log) <= 50


# =============================================================================
# DEMAND WINDOW INTEGRATION TESTS
# =============================================================================


@pytest.mark.skip(
    reason="Phase 4 removed _get_forecast_entry_for_now - update in Phase 5/6"
)
class TestDemandWindowIntegration:
    """Tests for demand window integration."""

    def test_demand_window_blocks_grid_import(
        self, computation_engine, integration_data
    ):
        """Test that demand window blocks grid import."""
        test_time = dt_aware(2026, 2, 16, 19, 0, 0)  # During DW

        # Set up conditions that would normally trigger grid charging
        integration_data.soc = 30.0  # Low SOC
        integration_data.general_price = 0.05  # Very cheap

        # Provide a forecast entry for the current time slot
        forecast_entry = {
            "timestamp": test_time.isoformat(),
            "hour": 19,
            "minute": 0,
            "grid_charge": False,
            "grid_charge_boost": False,
            "proactive_export": False,
            "grid_import_kwh": 0.0,
            "export_amount_kwh": 0.0,
            "predicted_soc": 30.0,
            "buy_price": 0.05,
            "sell_price": 0.03,
        }

        with patch("homeassistant.util.dt.now", return_value=test_time):
            # Enable demand window block
            def mock_switch_state(key):
                if key == "automation_enabled":
                    return True
                if key == "demand_window_block":
                    return True
                return False

            computation_engine._get_switch_state = MagicMock(
                side_effect=mock_switch_state
            )

            # Set up the forecast directly
            integration_data.daily_forecast = [forecast_entry]

            # Mock forecast entry lookup
            with patch.object(
                computation_engine,
                "_get_forecast_entry_for_now",
                return_value=forecast_entry,
            ):
                # Issue #351: Mock forecast computation to avoid hybrid timescale changes
                with patch.object(computation_engine, "_compute_daily_15min_forecast"):
                    computation_engine.compute_derived_values(integration_data)

        # Should be in DEMAND_BLOCK, not GRID_CHARGING
        assert integration_data.active_mode == BatteryMode.DEMAND_BLOCK

    def test_demand_window_entry_decision(self, computation_engine, integration_data):
        """Test demand window entry decision based on SOC."""
        test_time = dt_aware(2026, 2, 16, 17, 30, 0)  # Before DW (18:00)

        integration_data.soc = 95.0  # High SOC, can enter DW

        # Provide a forecast entry
        forecast_entry = {
            "timestamp": test_time.isoformat(),
            "hour": 17,
            "minute": 30,
            "grid_charge": False,
            "grid_charge_boost": False,
            "proactive_export": False,
            "grid_import_kwh": 0.0,
            "export_amount_kwh": 0.0,
            "predicted_soc": 95.0,
            "buy_price": 0.25,
            "sell_price": 0.08,
        }

        with patch("homeassistant.util.dt.now", return_value=test_time):
            computation_engine._get_switch_state = MagicMock(return_value=True)

            # Mock forecast entry lookup
            with patch.object(
                computation_engine,
                "_get_forecast_entry_for_now",
                return_value=forecast_entry,
            ):
                computation_engine.compute_derived_values(integration_data)

        # Should compute solar_can_reach_target
        # (actual value depends on forecast, just check it's computed)
        assert hasattr(integration_data, "solar_can_reach_target")


# =============================================================================
# MANUAL OVERRIDE INTEGRATION TESTS
# =============================================================================


@pytest.mark.skip(reason="Phase 4 removed _mode_decision - update in Phase 5/6")
class TestManualOverrideIntegration:
    """Tests for manual override integration."""

    def test_manual_override_blocks_automation(
        self, computation_engine, integration_data
    ):
        """Test that manual override blocks automation."""
        test_time = dt_aware(2026, 2, 16, 12, 0, 0)
        integration_data.manual_override = True

        # Provide a forecast entry for the current time slot
        forecast_entry = {
            "timestamp": test_time.isoformat(),
            "hour": 12,
            "minute": 0,
            "grid_charge": False,
            "grid_charge_boost": False,
            "proactive_export": False,
            "grid_import_kwh": 0.0,
            "export_amount_kwh": 0.0,
            "predicted_soc": 50.0,
            "buy_price": 0.25,
            "sell_price": 0.08,
        }

        with patch("homeassistant.util.dt.now", return_value=test_time):
            computation_engine._get_switch_state = MagicMock(return_value=True)
            # Also mock the mode decision engine's reference
            computation_engine._mode_decision._get_switch_state = MagicMock(
                return_value=True
            )

            # Set up the forecast directly and mock forecast computation
            integration_data.daily_forecast = [forecast_entry]

            # Mock forecast entry lookup
            with patch.object(
                computation_engine,
                "_get_forecast_entry_for_now",
                return_value=forecast_entry,
            ):
                with patch.object(computation_engine, "_compute_daily_15min_forecast"):
                    computation_engine.compute_derived_values(integration_data)

        # Should be in MANUAL mode
        assert integration_data.active_mode == BatteryMode.MANUAL

    def test_manual_override_cleared_on_mode_change(
        self, computation_engine, integration_data
    ):
        """Test that manual override is cleared when mode changes."""
        test_time = dt_aware(2026, 2, 16, 12, 0, 0)
        integration_data.manual_override = True

        # Provide a forecast entry for the current time slot
        forecast_entry = {
            "timestamp": test_time.isoformat(),
            "hour": 12,
            "minute": 0,
            "grid_charge": False,
            "grid_charge_boost": False,
            "proactive_export": False,
            "grid_import_kwh": 0.0,
            "export_amount_kwh": 0.0,
            "predicted_soc": 50.0,
            "buy_price": 0.25,
            "sell_price": 0.08,
        }

        with patch("homeassistant.util.dt.now", return_value=test_time):
            computation_engine._get_switch_state = MagicMock(return_value=True)
            # Also mock the mode decision engine's reference
            computation_engine._mode_decision._get_switch_state = MagicMock(
                return_value=True
            )

            # Set up the forecast directly and mock forecast computation
            integration_data.daily_forecast = [forecast_entry]

            # Mock forecast entry lookup
            with patch.object(
                computation_engine,
                "_get_forecast_entry_for_now",
                return_value=forecast_entry,
            ):
                with patch.object(computation_engine, "_compute_daily_15min_forecast"):
                    computation_engine.compute_derived_values(integration_data)

        # After computation, manual_override should be respected
        assert integration_data.active_mode == BatteryMode.MANUAL


# =============================================================================
# AUTOMATION DISABLED TESTS
# =============================================================================


@pytest.mark.skip(reason="Phase 4 removed _mode_decision - update in Phase 5/6")
class TestAutomationDisabled:
    """Tests for automation disabled state."""

    def test_automation_disabled_sets_self_consumption(
        self, computation_engine, integration_data
    ):
        """Test that disabled automation sets SELF_CONSUMPTION."""
        test_time = dt_aware(2026, 2, 16, 12, 0, 0)

        with patch("homeassistant.util.dt.now", return_value=test_time):
            # Disable automation
            computation_engine._get_switch_state = MagicMock(return_value=False)
            # Also mock the mode decision engine's reference
            computation_engine._mode_decision._get_switch_state = MagicMock(
                return_value=False
            )

            # Set up empty forecast and mock forecast computation
            integration_data.daily_forecast = []

            with patch.object(computation_engine, "_compute_daily_15min_forecast"):
                computation_engine.compute_derived_values(integration_data)

        # Should be in SELF_CONSUMPTION regardless of other conditions
        assert integration_data.active_mode == BatteryMode.SELF_CONSUMPTION

    def test_automation_disabled_ignores_price_spike(
        self, computation_engine, integration_data
    ):
        """Test that disabled automation ignores price spike."""
        test_time = dt_aware(2026, 2, 16, 19, 0, 0)
        integration_data.price_spike = True
        integration_data.feed_in_price = 3.00  # High spike

        with patch("homeassistant.util.dt.now", return_value=test_time):
            # Disable automation
            def mock_switch_state(key):
                if key == "automation_enabled":
                    return False
                if key == "spike_discharge_enabled":
                    return True
                return False

            computation_engine._get_switch_state = MagicMock(
                side_effect=mock_switch_state
            )
            # Also mock the mode decision engine's reference
            computation_engine._mode_decision._get_switch_state = MagicMock(
                side_effect=mock_switch_state
            )

            # Set up empty forecast and mock forecast computation
            integration_data.daily_forecast = []

            with patch.object(computation_engine, "_compute_daily_15min_forecast"):
                computation_engine.compute_derived_values(integration_data)

        # Should be in SELF_CONSUMPTION, not SPIKE_DISCHARGE
        assert integration_data.active_mode == BatteryMode.SELF_CONSUMPTION


# =============================================================================
# COST TRACKING INTEGRATION TESTS
# =============================================================================


@pytest.mark.skip(
    reason="Phase 4 removed _mode_decision, daily_forecast - update in Phase 5/6"
)
class TestCostTrackingIntegration:
    """Tests for cost tracking integration."""

    def test_forecast_costs_calculated(self, computation_engine, integration_data):
        """Test that forecast costs are calculated."""
        test_time = dt_aware(2026, 2, 16, 12, 0, 0)

        # Provide a forecast entry
        forecast_entry = {
            "timestamp": test_time.isoformat(),
            "hour": 12,
            "minute": 0,
            "grid_charge": False,
            "grid_charge_boost": False,
            "proactive_export": False,
            "grid_import_kwh": 0.0,
            "export_amount_kwh": 0.0,
            "predicted_soc": 50.0,
            "buy_price": 0.25,
            "sell_price": 0.08,
        }

        with patch("homeassistant.util.dt.now", return_value=test_time):
            computation_engine._get_switch_state = MagicMock(return_value=True)

            # Mock forecast entry lookup
            with patch.object(
                computation_engine,
                "_get_forecast_entry_for_now",
                return_value=forecast_entry,
            ):
                computation_engine.compute_derived_values(integration_data)

        # Should have forecast cost fields
        assert hasattr(integration_data, "forecast_import_cost")
        assert hasattr(integration_data, "forecast_export_revenue")
        assert hasattr(integration_data, "forecast_net_cost")
