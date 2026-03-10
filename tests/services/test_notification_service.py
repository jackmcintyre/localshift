"""Unit tests for NotificationService."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.localshift.const import (
    SWITCH_NOTIFICATIONS_ENABLED,
    BatteryMode,
)
from custom_components.localshift.coordinator_data import CoordinatorData
from custom_components.localshift.services.notification_service import (
    NotificationService,
)


@pytest.fixture
def mock_hass():
    """Create a mock Home Assistant instance."""
    hass = MagicMock()
    hass.services = MagicMock()
    hass.services.async_call = AsyncMock()
    hass.states = MagicMock()
    return hass


@pytest.fixture
def mock_entry():
    """Create a mock config entry."""
    entry = MagicMock()
    entry.options = {
        "demand_window_start": "14:00:00",
        "demand_window_end": "20:00:00",
        "battery_target": 85,
    }
    return entry


@pytest.fixture
def mock_get_entity_id():
    """Mock function to get entity IDs."""
    return lambda key: "notify.mobile_app"


@pytest.fixture
def notification_service(mock_hass, mock_entry, mock_get_entity_id):
    """Create a NotificationService instance."""
    return NotificationService(
        mock_hass, mock_entry, mock_get_entity_id, get_switch_state_func=None
    )


@pytest.fixture
def notification_service_with_switches(mock_hass, mock_entry, mock_get_entity_id):
    """Create a NotificationService with switch state function."""
    switch_states = {
        SWITCH_NOTIFICATIONS_ENABLED: True,
        "dry_run": False,
    }

    def get_switch_state(key):
        return switch_states.get(key, False)

    return NotificationService(
        mock_hass,
        mock_entry,
        mock_get_entity_id,
        get_switch_state_func=get_switch_state,
    ), switch_states


@pytest.fixture
def coordinator_data():
    """Create CoordinatorData for notification tests."""
    data = CoordinatorData()
    data.soc = 50.0
    data.feed_in_price = 0.25
    data.general_price = 0.15
    data.effective_cheap_price = 0.10
    data.cheap_charge_stop_price = 0.12
    data.grid_import_cost = 5.0
    data.grid_export_revenue = 2.0
    data.battery_savings = 1.5
    data.battery_charge_cost = 0.5
    data.solar_battery_forecast = {"net_solar_kwh": 10.0}
    return data


# =============================================================================
# SEND_NOTIFICATION TESTS
# =============================================================================


class TestSendNotification:
    """Tests for send_notification method."""

    @pytest.mark.asyncio
    async def test_send_notification_via_notify_service(
        self, notification_service, mock_hass
    ):
        """Test sending notification via configured notify service."""
        await notification_service.send_notification("Test Title", "Test Message")

        # async_call is called with (domain, service, data_dict) as positional args
        mock_hass.services.async_call.assert_called_once()
        call_args = mock_hass.services.async_call.call_args
        assert call_args[0][0] == "notify"
        assert call_args[0][1] == "mobile_app"
        assert call_args[0][2]["title"] == "Test Title"
        assert call_args[0][2]["message"] == "Test Message"

    @pytest.mark.asyncio
    async def test_send_notification_fallback_to_persistent(
        self, notification_service, mock_hass
    ):
        """Test fallback to persistent notification when notify service fails."""
        mock_hass.services.async_call.side_effect = Exception("Service not found")

        await notification_service.send_notification("Test Title", "Test Message")

        # Should have called persistent_notification.create
        calls = mock_hass.services.async_call.call_args_list
        persistent_call = None
        for call in calls:
            if call[0][0] == "persistent_notification":
                persistent_call = call
                break

        assert persistent_call is not None
        assert persistent_call[0][1] == "create"

    @pytest.mark.asyncio
    async def test_send_notification_handles_persistent_failure(
        self, notification_service, mock_hass
    ):
        """Test handling when both notify and persistent notification fail."""
        mock_hass.services.async_call.side_effect = Exception("All services fail")

        # Should not raise exception
        await notification_service.send_notification("Test", "Message")


# =============================================================================
# NOTIFICATION PREFERENCE TESTS
# =============================================================================


class TestNotificationPreferences:
    """Tests for notification preferences (consolidated switch)."""

    @pytest.mark.asyncio
    async def test_all_notifications_disabled(
        self, mock_hass, mock_entry, mock_get_entity_id, coordinator_data
    ):
        """Test that all notifications are skipped when disabled."""
        switch_states = {SWITCH_NOTIFICATIONS_ENABLED: False}

        def get_switch_state(key):
            return switch_states.get(key, False)

        service = NotificationService(
            mock_hass,
            mock_entry,
            mock_get_entity_id,
            get_switch_state_func=get_switch_state,
        )

        # Test transition notification is skipped
        await service.send_transition_notification(
            BatteryMode.SELF_CONSUMPTION, BatteryMode.SPIKE_DISCHARGE, coordinator_data
        )
        mock_hass.services.async_call.assert_not_called()

        # Test daily summary is skipped
        await service.send_daily_summary(coordinator_data)
        mock_hass.services.async_call.assert_not_called()

        # Test health correction is skipped
        await service.send_health_correction_notification(
            BatteryMode.SELF_CONSUMPTION, coordinator_data
        )
        mock_hass.services.async_call.assert_not_called()

        # Test manual action is skipped
        await service.send_manual_action_notification("Force Charge", coordinator_data)
        mock_hass.services.async_call.assert_not_called()


# =============================================================================
# TRANSITION NOTIFICATION TESTS
# =============================================================================


class TestTransitionNotifications:
    """Tests for mode transition notifications."""

    @pytest.mark.asyncio
    async def test_spike_discharge_notification(
        self, notification_service, coordinator_data, mock_hass
    ):
        """Test notification for spike discharge mode."""
        await notification_service.send_transition_notification(
            BatteryMode.SELF_CONSUMPTION, BatteryMode.SPIKE_DISCHARGE, coordinator_data
        )

        call_args = mock_hass.services.async_call.call_args
        # async_call(domain, service, data_dict) - data_dict is third positional arg
        data = call_args[0][2]
        title = data["title"]
        message = data["message"]

        assert "Price Spike" in title
        assert "0.25" in message  # feed_in_price
        assert "50%" in message  # SOC

    @pytest.mark.asyncio
    async def test_proactive_export_notification(
        self, notification_service, coordinator_data, mock_hass
    ):
        """Test notification for proactive export mode."""
        coordinator_data.soc = 60.0

        await notification_service.send_transition_notification(
            BatteryMode.SELF_CONSUMPTION, BatteryMode.PROACTIVE_EXPORT, coordinator_data
        )

        call_args = mock_hass.services.async_call.call_args
        data = call_args[0][2]
        message = data["message"]

        assert "Proactive Export" in data["title"]
        assert "55%" in message  # Reserve = SOC - 5 = 55

    @pytest.mark.asyncio
    async def test_demand_block_notification(
        self, notification_service, coordinator_data, mock_hass
    ):
        """Test notification for demand block mode."""
        await notification_service.send_transition_notification(
            BatteryMode.SELF_CONSUMPTION, BatteryMode.DEMAND_BLOCK, coordinator_data
        )

        call_args = mock_hass.services.async_call.call_args
        data = call_args[0][2]
        message = data["message"]

        assert "Demand Window" in data["title"]
        assert "14:00:00" in message  # Window start
        assert "20:00:00" in message  # Window end

    @pytest.mark.asyncio
    async def test_grid_charging_notification(
        self, notification_service, coordinator_data, mock_hass
    ):
        """Test notification for grid charging mode."""
        await notification_service.send_transition_notification(
            BatteryMode.SELF_CONSUMPTION, BatteryMode.GRID_CHARGING, coordinator_data
        )

        call_args = mock_hass.services.async_call.call_args
        data = call_args[0][2]
        message = data["message"]

        assert "Cheap Grid Charging" in data["title"]
        assert "0.15" in message  # general_price
        assert "0.10" in message  # effective_cheap_price

    @pytest.mark.asyncio
    async def test_boost_charging_notification(
        self, notification_service, coordinator_data, mock_hass
    ):
        """Test notification for boost charging mode."""
        await notification_service.send_transition_notification(
            BatteryMode.SELF_CONSUMPTION, BatteryMode.BOOST_CHARGING, coordinator_data
        )

        call_args = mock_hass.services.async_call.call_args
        data = call_args[0][2]
        message = data["message"]

        assert "Boost Charging" in data["title"]
        assert "5kW" in data["title"]
        assert "85%" in message  # battery_target

    @pytest.mark.asyncio
    async def test_manual_override_notification(
        self, notification_service, coordinator_data, mock_hass
    ):
        """Test notification for manual override mode."""
        await notification_service.send_transition_notification(
            BatteryMode.SELF_CONSUMPTION, BatteryMode.MANUAL, coordinator_data
        )

        call_args = mock_hass.services.async_call.call_args
        data = call_args[0][2]
        message = data["message"]

        assert "Manual Override" in data["title"]
        assert "manual override" in message.lower()

    @pytest.mark.asyncio
    async def test_self_consumption_from_spike(
        self, notification_service, coordinator_data, mock_hass
    ):
        """Test notification when returning to self consumption from spike."""
        await notification_service.send_transition_notification(
            BatteryMode.SPIKE_DISCHARGE, BatteryMode.SELF_CONSUMPTION, coordinator_data
        )

        call_args = mock_hass.services.async_call.call_args
        data = call_args[0][2]
        title = data["title"]

        assert "Spike Ended" in title

    @pytest.mark.asyncio
    async def test_self_consumption_from_grid_charging(
        self, notification_service, coordinator_data, mock_hass
    ):
        """Test notification when returning to self consumption from grid charging."""
        await notification_service.send_transition_notification(
            BatteryMode.GRID_CHARGING, BatteryMode.SELF_CONSUMPTION, coordinator_data
        )

        call_args = mock_hass.services.async_call.call_args
        data = call_args[0][2]
        title = data["title"]
        message = data["message"]

        assert "Charging Stopped" in title
        assert "0.12" in message  # cheap_charge_stop_price


# =============================================================================
# DAILY SUMMARY TESTS
# =============================================================================


class TestDailySummary:
    """Tests for daily summary notifications."""

    @pytest.mark.asyncio
    async def test_daily_summary_content(
        self, notification_service, coordinator_data, mock_hass
    ):
        """Test daily summary includes all expected content."""

        # Mock state reads for energy values
        def mock_get_state(entity_id):
            state = MagicMock()
            if "grid_import_energy" in entity_id:
                state.state = "15.5"
            elif "grid_export_energy" in entity_id:
                state.state = "8.2"
            elif "solar_production_energy" in entity_id:
                state.state = "25.0"
            else:
                state.state = "0"
            return state

        mock_hass.states.get = mock_get_state

        await notification_service.send_daily_summary(coordinator_data)

        call_args = mock_hass.services.async_call.call_args
        data = call_args[0][2]
        message = data["message"]

        assert "Daily Summary" in data["title"]
        assert "Solar:" in message
        assert "25.0 kWh" in message
        assert "Grid import:" in message
        assert "Grid export:" in message
        assert "Net cost:" in message
        assert "Battery savings:" in message
        assert "SOC:" in message

    @pytest.mark.asyncio
    async def test_daily_summary_with_dry_run(
        self, mock_hass, mock_entry, mock_get_entity_id, coordinator_data
    ):
        """Test daily summary includes dry run prefix."""
        switch_states = {"dry_run": True, SWITCH_NOTIFICATIONS_ENABLED: True}

        def get_switch_state(key):
            return switch_states.get(key, False)

        service = NotificationService(
            mock_hass,
            mock_entry,
            mock_get_entity_id,
            get_switch_state_func=get_switch_state,
        )

        def mock_get_state(entity_id):
            state = MagicMock()
            state.state = "10.0"
            return state

        mock_hass.states.get = mock_get_state

        await service.send_daily_summary(coordinator_data)

        call_args = mock_hass.services.async_call.call_args
        data = call_args[0][2]
        title = data["title"]

        assert "[Dry Run]" in title


# =============================================================================
# ALERT NOTIFICATION TESTS
# =============================================================================


class TestAlertNotifications:
    """Tests for alert-type notifications."""

    @pytest.mark.asyncio
    async def test_health_correction_notification(
        self, notification_service, coordinator_data, mock_hass
    ):
        """Test health correction notification."""
        await notification_service.send_health_correction_notification(
            BatteryMode.SELF_CONSUMPTION, coordinator_data
        )

        call_args = mock_hass.services.async_call.call_args
        data = call_args[0][2]
        title = data["title"]
        message = data["message"]

        assert "Health Check Correction" in title
        assert "self_consumption" in message
        assert "50%" in message

    @pytest.mark.asyncio
    async def test_transition_failed_notification(
        self, notification_service, coordinator_data, mock_hass
    ):
        """Test transition failed notification."""
        await notification_service.send_transition_failed_notification(
            BatteryMode.GRID_CHARGING, coordinator_data
        )

        call_args = mock_hass.services.async_call.call_args
        data = call_args[0][2]
        title = data["title"]
        message = data["message"]

        assert "Transition Failed" in title
        assert "grid_charging" in message
        assert "Powerwall connectivity" in message

    @pytest.mark.asyncio
    async def test_automation_disabled_notification(
        self, notification_service, coordinator_data, mock_hass
    ):
        """Test automation disabled notification."""
        await notification_service.send_automation_disabled_notification(
            coordinator_data
        )

        call_args = mock_hass.services.async_call.call_args
        data = call_args[0][2]
        title = data["title"]
        message = data["message"]

        assert "Automation Disabled" in title
        assert "self consumption" in message.lower()

    @pytest.mark.asyncio
    async def test_manual_override_timeout_notification(
        self, notification_service, coordinator_data, mock_hass
    ):
        """Test manual override timeout notification."""
        await notification_service.send_manual_override_timeout_notification(
            coordinator_data, timeout_hours=4.0
        )

        call_args = mock_hass.services.async_call.call_args
        data = call_args[0][2]
        title = data["title"]
        message = data["message"]

        assert "Manual Override Timeout" in title
        assert "4.0 hours" in message
        assert "Automation resuming" in message


# =============================================================================
# MANUAL ACTION NOTIFICATION TESTS
# =============================================================================


class TestManualActionNotification:
    """Tests for manual action notifications."""

    @pytest.mark.asyncio
    async def test_manual_action_notification(
        self, notification_service, coordinator_data, mock_hass
    ):
        """Test manual action notification."""
        await notification_service.send_manual_action_notification(
            "Force Charge", coordinator_data
        )

        call_args = mock_hass.services.async_call.call_args
        data = call_args[0][2]
        title = data["title"]
        message = data["message"]

        assert "Force Charge" in title
        assert "Force Charge started" in message
        assert "50%" in message


# =============================================================================
# DECISION REASON TESTS
# =============================================================================


class TestGenerateDecisionReason:
    """Tests for generate_decision_reason method."""

    def test_spike_discharge_reason(self, notification_service, coordinator_data):
        """Test decision reason for spike discharge."""
        reason = notification_service.generate_decision_reason(
            BatteryMode.SELF_CONSUMPTION, BatteryMode.SPIKE_DISCHARGE, coordinator_data
        )

        assert "Price spike detected" in reason
        assert "0.25" in reason

    def test_proactive_export_reason(self, notification_service, coordinator_data):
        """Test decision reason for proactive export."""
        reason = notification_service.generate_decision_reason(
            BatteryMode.SELF_CONSUMPTION, BatteryMode.PROACTIVE_EXPORT, coordinator_data
        )

        assert "low/negative FIT" in reason

    def test_demand_block_reason(self, notification_service, coordinator_data):
        """Test decision reason for demand block."""
        reason = notification_service.generate_decision_reason(
            BatteryMode.SELF_CONSUMPTION, BatteryMode.DEMAND_BLOCK, coordinator_data
        )

        assert "Demand window active" in reason

    def test_grid_charging_reason(self, notification_service, coordinator_data):
        """Test decision reason for grid charging."""
        reason = notification_service.generate_decision_reason(
            BatteryMode.SELF_CONSUMPTION, BatteryMode.GRID_CHARGING, coordinator_data
        )

        assert "below threshold" in reason
        assert "0.15" in reason
        assert "0.10" in reason

    def test_boost_charging_reason(self, notification_service, coordinator_data):
        """Test decision reason for boost charging."""
        reason = notification_service.generate_decision_reason(
            BatteryMode.SELF_CONSUMPTION, BatteryMode.BOOST_CHARGING, coordinator_data
        )

        assert "Solar gap" in reason
        assert "boost charging" in reason

    def test_self_consumption_from_charging_reason(
        self, notification_service, coordinator_data
    ):
        """Test decision reason for returning to self consumption from charging."""
        reason = notification_service.generate_decision_reason(
            BatteryMode.GRID_CHARGING, BatteryMode.SELF_CONSUMPTION, coordinator_data
        )

        assert "Charging ended" in reason
        assert "0.12" in reason  # cheap_charge_stop_price

    def test_self_consumption_from_spike_reason(
        self, notification_service, coordinator_data
    ):
        """Test decision reason for returning to self consumption from spike."""
        reason = notification_service.generate_decision_reason(
            BatteryMode.SPIKE_DISCHARGE, BatteryMode.SELF_CONSUMPTION, coordinator_data
        )

        assert "spike cleared" in reason.lower()

    def test_self_consumption_default_reason(
        self, notification_service, coordinator_data
    ):
        """Test default decision reason for self consumption."""
        reason = notification_service.generate_decision_reason(
            BatteryMode.MANUAL, BatteryMode.SELF_CONSUMPTION, coordinator_data
        )

        assert "Normal operation" in reason


# =============================================================================
# HELPER METHOD TESTS
# =============================================================================


class TestHelperMethods:
    """Tests for helper methods."""

    def test_is_notification_enabled_no_switch_func(self, notification_service):
        """Test notification enabled when no switch function provided."""
        # When get_switch_state_func is None, should default to True
        result = notification_service._is_notification_enabled(
            SWITCH_NOTIFICATIONS_ENABLED
        )
        assert result is True

    def test_is_notification_enabled_with_switch_func(
        self, mock_hass, mock_entry, mock_get_entity_id
    ):
        """Test notification enabled with switch function."""
        switch_states = {SWITCH_NOTIFICATIONS_ENABLED: True}

        def get_switch_state(key):
            return switch_states.get(key, False)

        service = NotificationService(
            mock_hass,
            mock_entry,
            mock_get_entity_id,
            get_switch_state_func=get_switch_state,
        )

        assert service._is_notification_enabled(SWITCH_NOTIFICATIONS_ENABLED) is True

        # Test with notifications disabled
        switch_states[SWITCH_NOTIFICATIONS_ENABLED] = False
        assert service._is_notification_enabled(SWITCH_NOTIFICATIONS_ENABLED) is False

    def test_get_dry_run_prefix_disabled(self, notification_service):
        """Test dry run prefix when disabled."""
        result = notification_service._get_dry_run_prefix()
        assert result == ""

    def test_get_dry_run_prefix_enabled(
        self, mock_hass, mock_entry, mock_get_entity_id
    ):
        """Test dry run prefix when enabled."""
        switch_states = {"dry_run": True}

        def get_switch_state(key):
            return switch_states.get(key, False)

        service = NotificationService(
            mock_hass,
            mock_entry,
            mock_get_entity_id,
            get_switch_state_func=get_switch_state,
        )

        result = service._get_dry_run_prefix()
        assert result == "[Dry Run] "

    def test_read_float_valid(self, notification_service, mock_hass):
        """Test _read_float with valid state."""
        state = MagicMock()
        state.state = "42.5"
        mock_hass.states.get.return_value = state

        result = notification_service._read_float("sensor.test")

        assert result == 42.5

    def test_read_float_unavailable(self, notification_service, mock_hass):
        """Test _read_float with unavailable state."""
        state = MagicMock()
        state.state = "unavailable"
        mock_hass.states.get.return_value = state

        result = notification_service._read_float("sensor.test", default=10.0)

        assert result == 10.0

    def test_read_float_none(self, notification_service, mock_hass):
        """Test _read_float with None state."""
        mock_hass.states.get.return_value = None

        result = notification_service._read_float("sensor.test", default=5.0)

        assert result == 5.0
