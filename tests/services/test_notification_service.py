"""Unit tests for NotificationService."""

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.localshift.const import (
    SWITCH_NOTIFICATIONS_ENABLED,
    BatteryMode,
)
from custom_components.localshift.coordinator import CoordinatorData
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

    @pytest.mark.asyncio
    async def test_self_consumption_from_proactive_export(
        self, notification_service, coordinator_data, mock_hass
    ):
        """Test notification when returning to self consumption after FIT improves."""
        await notification_service.send_transition_notification(
            BatteryMode.PROACTIVE_EXPORT, BatteryMode.SELF_CONSUMPTION, coordinator_data
        )

        call_args = mock_hass.services.async_call.call_args
        data = call_args[0][2]

        assert "Proactive Export Ended" in data["title"]
        assert "FIT has improved" in data["message"]

    @pytest.mark.asyncio
    async def test_self_consumption_charging_ended_above_effective_only(
        self, notification_service, coordinator_data, mock_hass
    ):
        """Price above effective threshold but not stop threshold."""
        # 0.11 is between effective_cheap_price (0.10) and cheap_charge_stop_price (0.12).
        coordinator_data.general_price = 0.11

        await notification_service.send_transition_notification(
            BatteryMode.GRID_CHARGING, BatteryMode.SELF_CONSUMPTION, coordinator_data
        )

        call_args = mock_hass.services.async_call.call_args
        data = call_args[0][2]

        assert "Charging Stopped" in data["title"]
        assert "above effective threshold" in data["message"]

    @pytest.mark.asyncio
    async def test_self_consumption_charging_complete_still_cheap(
        self, notification_service, coordinator_data, mock_hass
    ):
        """Charging stopped for a reason other than price (still below threshold)."""
        coordinator_data.general_price = 0.05  # below effective threshold (0.10)

        await notification_service.send_transition_notification(
            BatteryMode.BOOST_CHARGING, BatteryMode.SELF_CONSUMPTION, coordinator_data
        )

        call_args = mock_hass.services.async_call.call_args
        data = call_args[0][2]

        assert "Charging Complete" in data["title"]
        assert "still below threshold" in data["message"]

    @pytest.mark.asyncio
    async def test_self_consumption_from_demand_block(
        self, notification_service, coordinator_data, mock_hass
    ):
        """Test notification when the demand window ends."""
        await notification_service.send_transition_notification(
            BatteryMode.DEMAND_BLOCK, BatteryMode.SELF_CONSUMPTION, coordinator_data
        )

        call_args = mock_hass.services.async_call.call_args
        data = call_args[0][2]

        assert "Demand Window Ended" in data["title"]
        assert "Returning to normal automation" in data["message"]

    @pytest.mark.asyncio
    async def test_self_consumption_from_unmapped_old_mode(
        self, notification_service, coordinator_data, mock_hass
    ):
        """Generic self-consumption message when old_mode has no special case."""
        await notification_service.send_transition_notification(
            BatteryMode.HOLD, BatteryMode.SELF_CONSUMPTION, coordinator_data
        )

        call_args = mock_hass.services.async_call.call_args
        data = call_args[0][2]

        assert "Self Consumption" in data["title"]
        assert "Returning to self consumption" in data["message"]

    @pytest.mark.asyncio
    async def test_transition_notification_unmapped_mode_fallback(
        self, notification_service, coordinator_data, mock_hass
    ):
        """A new_mode with no dedicated branch still gets a generic notification."""
        await notification_service.send_transition_notification(
            BatteryMode.SELF_CONSUMPTION, BatteryMode.HOLD, coordinator_data
        )

        call_args = mock_hass.services.async_call.call_args
        data = call_args[0][2]

        assert "Mode Change" in data["title"]
        assert "self_consumption" in data["message"]
        assert "hold" in data["message"]


# =============================================================================
# DAILY SUMMARY TESTS
# =============================================================================


class TestDailySummary:
    """Tests for daily summary notifications."""

    @pytest.mark.asyncio
    async def test_daily_summary_content(
        self, notification_service, coordinator_data, mock_hass
    ):
        """Test daily summary includes all expected content.

        Issue #971: energy values come from the integration's own daily
        accumulators, not entity state lookups (those utility meter entities
        were removed with the legacy YAML stack in #880).
        """
        coordinator_data.solar_kwh_today = 25.0
        coordinator_data.grid_import_kwh_today = 15.5
        coordinator_data.grid_export_kwh_today = 8.2

        await notification_service.send_daily_summary(coordinator_data)

        call_args = mock_hass.services.async_call.call_args
        data = call_args[0][2]
        message = data["message"]

        assert "Daily Summary" in data["title"]
        assert "Solar: 25.0 kWh" in message
        assert "Grid import: 15.5 kWh" in message
        assert "Grid export: 8.2 kWh" in message
        assert "Net cost:" in message
        assert "Battery savings:" in message
        assert "SOC:" in message

    @pytest.mark.asyncio
    async def test_daily_summary_does_not_read_hass_states(
        self, notification_service, coordinator_data, mock_hass
    ):
        """Issue #971: the daily summary must not read any entity state.

        The three utility meter entities it used to read were removed with
        the YAML stack (#880) and would return ENTITY_NOT_FOUND, which is
        exactly the bug this pins shut.
        """
        coordinator_data.solar_kwh_today = 25.0
        coordinator_data.grid_import_kwh_today = 15.5
        coordinator_data.grid_export_kwh_today = 8.2
        mock_hass.states.get = MagicMock(
            side_effect=AssertionError("must not read entity state")
        )

        await notification_service.send_daily_summary(coordinator_data)

        call_args = mock_hass.services.async_call.call_args
        data = call_args[0][2]
        assert "25.0 kWh" in data["message"]

    @pytest.mark.asyncio
    async def test_daily_summary_includes_decision_telemetry_when_present(
        self, notification_service, coordinator_data, mock_hass
    ):
        """When decisions were made today, the summary appends the quality line."""
        coordinator_data.performance_metrics.total_decisions_today = 12
        coordinator_data.performance_metrics.avg_decision_score_today = 0.875
        coordinator_data.performance_metrics.cost_trend = "improving"
        coordinator_data.learning_status = "active"

        await notification_service.send_daily_summary(coordinator_data)

        message = mock_hass.services.async_call.call_args[0][2]["message"]
        assert "Decisions: active" in message
        assert "Quality: 88%" in message
        assert "Trend: improving" in message

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

        mock_hass.states.get = MagicMock(
            side_effect=AssertionError("must not read entity state")
        )

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
    async def test_health_correction_suppressed_for_tesla_grid_charging_sync(
        self, notification_service, coordinator_data, mock_hass
    ):
        """Issue #394: suppress when only grid_charging_allowed mismatched."""
        await notification_service.send_health_correction_notification(
            BatteryMode.SELF_CONSUMPTION,
            coordinator_data,
            mismatch_details={
                "grid_charging_allowed": True,
                "operation_mode": False,
                "backup_reserve": False,
            },
        )

        mock_hass.services.async_call.assert_not_called()

    @pytest.mark.asyncio
    async def test_health_correction_not_suppressed_when_other_field_mismatched(
        self, notification_service, coordinator_data, mock_hass
    ):
        """Not suppressed when operation_mode also mismatched, not just grid_charging."""
        await notification_service.send_health_correction_notification(
            BatteryMode.SELF_CONSUMPTION,
            coordinator_data,
            mismatch_details={
                "grid_charging_allowed": True,
                "operation_mode": True,
                "backup_reserve": False,
            },
        )

        mock_hass.services.async_call.assert_called_once()

    @pytest.mark.asyncio
    async def test_health_correction_not_suppressed_for_unrelated_mode(
        self, notification_service, coordinator_data, mock_hass
    ):
        """Tesla grid_charging sync only applies to SELF_CONSUMPTION/DEMAND_BLOCK."""
        await notification_service.send_health_correction_notification(
            BatteryMode.GRID_CHARGING,
            coordinator_data,
            mismatch_details={
                "grid_charging_allowed": True,
                "operation_mode": False,
                "backup_reserve": False,
            },
        )

        mock_hass.services.async_call.assert_called_once()

    @pytest.mark.asyncio
    async def test_health_correction_notification_disabled(
        self, notification_service_with_switches, coordinator_data, mock_hass
    ):
        """No notification is sent when notifications are disabled."""
        service, switch_states = notification_service_with_switches
        switch_states[SWITCH_NOTIFICATIONS_ENABLED] = False

        await service.send_health_correction_notification(
            BatteryMode.SELF_CONSUMPTION, coordinator_data
        )

        mock_hass.services.async_call.assert_not_called()

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
    async def test_transition_failed_notification_disabled(
        self, notification_service_with_switches, coordinator_data, mock_hass
    ):
        """No notification is sent when notifications are disabled."""
        service, switch_states = notification_service_with_switches
        switch_states[SWITCH_NOTIFICATIONS_ENABLED] = False

        await service.send_transition_failed_notification(
            BatteryMode.GRID_CHARGING, coordinator_data
        )

        mock_hass.services.async_call.assert_not_called()

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
    async def test_automation_disabled_notification_disabled(
        self, notification_service_with_switches, coordinator_data, mock_hass
    ):
        """No notification is sent when notifications are disabled."""
        service, switch_states = notification_service_with_switches
        switch_states[SWITCH_NOTIFICATIONS_ENABLED] = False

        await service.send_automation_disabled_notification(coordinator_data)

        mock_hass.services.async_call.assert_not_called()

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

    @pytest.mark.asyncio
    async def test_manual_override_timeout_notification_disabled(
        self, notification_service_with_switches, coordinator_data, mock_hass
    ):
        """No notification is sent when notifications are disabled."""
        service, switch_states = notification_service_with_switches
        switch_states[SWITCH_NOTIFICATIONS_ENABLED] = False

        await service.send_manual_override_timeout_notification(
            coordinator_data, timeout_hours=4.0
        )

        mock_hass.services.async_call.assert_not_called()

    @pytest.mark.asyncio
    async def test_tesla_override_detected_corroborated(
        self, notification_service, coordinator_data, mock_hass
    ):
        """Detected notification names the confirming signal when corroborated."""
        await notification_service.send_tesla_override_notification(
            coordinator_data, detected=True, corroborated=True
        )

        data = mock_hass.services.async_call.call_args[0][2]
        assert "Tesla Override Detected" in data["title"]
        assert "Confirmed by Tesla" in data["message"]

    @pytest.mark.asyncio
    async def test_tesla_override_detected_heuristic(
        self, notification_service, coordinator_data, mock_hass
    ):
        """Uncorroborated detection flags heuristic + re-probe in the message."""
        await notification_service.send_tesla_override_notification(
            coordinator_data, detected=True, corroborated=False
        )

        data = mock_hass.services.async_call.call_args[0][2]
        assert "Tesla Override Detected" in data["title"]
        assert "heuristically" in data["message"]
        assert "re-probe" in data["message"]

    @pytest.mark.asyncio
    async def test_tesla_override_released(
        self, notification_service, coordinator_data, mock_hass
    ):
        """Release notification reports the duration and reason."""
        await notification_service.send_tesla_override_notification(
            coordinator_data,
            detected=False,
            corroborated=False,
            duration=timedelta(hours=17, minutes=48),
            release_reason="re-probe succeeded",
        )

        data = mock_hass.services.async_call.call_args[0][2]
        assert "Tesla Override Released" in data["title"]
        assert "17h 48m" in data["message"]
        assert "re-probe succeeded" in data["message"]

    @pytest.mark.asyncio
    async def test_tesla_override_released_unknown_duration(
        self, notification_service, coordinator_data, mock_hass
    ):
        """A None duration renders as an unknown period, not a crash."""
        await notification_service.send_tesla_override_notification(
            coordinator_data, detected=False, corroborated=False, duration=None
        )

        data = mock_hass.services.async_call.call_args[0][2]
        assert "an unknown period" in data["message"]

    @pytest.mark.asyncio
    async def test_tesla_override_released_sub_hour_duration(
        self, notification_service, coordinator_data, mock_hass
    ):
        """A sub-hour duration renders as minutes only, no leading '0h'."""
        await notification_service.send_tesla_override_notification(
            coordinator_data,
            detected=False,
            corroborated=False,
            duration=timedelta(minutes=25),
        )

        data = mock_hass.services.async_call.call_args[0][2]
        assert "25m" in data["message"]
        assert "0h" not in data["message"]

    @pytest.mark.asyncio
    async def test_tesla_override_notification_suppressed_when_disabled(
        self, notification_service_with_switches, coordinator_data, mock_hass
    ):
        """No notification is sent when notifications are disabled."""
        service, switch_states = notification_service_with_switches
        switch_states[SWITCH_NOTIFICATIONS_ENABLED] = False

        await service.send_tesla_override_notification(
            coordinator_data, detected=True, corroborated=True
        )

        mock_hass.services.async_call.assert_not_called()


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

    def test_self_consumption_from_proactive_export_reason(
        self, notification_service, coordinator_data
    ):
        """Test decision reason for returning to self consumption from export."""
        reason = notification_service.generate_decision_reason(
            BatteryMode.PROACTIVE_EXPORT, BatteryMode.SELF_CONSUMPTION, coordinator_data
        )

        assert "Proactive export ended" in reason

    def test_self_consumption_from_demand_block_reason(
        self, notification_service, coordinator_data
    ):
        """Test decision reason for returning to self consumption after demand window."""
        reason = notification_service.generate_decision_reason(
            BatteryMode.DEMAND_BLOCK, BatteryMode.SELF_CONSUMPTION, coordinator_data
        )

        assert "Demand window ended" in reason

    def test_charging_ended_reason_above_effective_only(
        self, notification_service, coordinator_data
    ):
        """Price above effective threshold but not stop threshold."""
        coordinator_data.general_price = 0.11  # between 0.10 and 0.12

        reason = notification_service.generate_decision_reason(
            BatteryMode.GRID_CHARGING, BatteryMode.SELF_CONSUMPTION, coordinator_data
        )

        assert "above effective threshold" in reason

    def test_charging_ended_reason_still_cheap(
        self, notification_service, coordinator_data
    ):
        """Charging stopped for another reason while price is still cheap."""
        coordinator_data.general_price = 0.05  # below effective threshold

        reason = notification_service.generate_decision_reason(
            BatteryMode.BOOST_CHARGING, BatteryMode.SELF_CONSUMPTION, coordinator_data
        )

        assert "Charging complete" in reason
        assert "still below threshold" in reason

    def test_manual_override_reason(self, notification_service, coordinator_data):
        """Test decision reason for manual override."""
        reason = notification_service.generate_decision_reason(
            BatteryMode.SELF_CONSUMPTION, BatteryMode.MANUAL, coordinator_data
        )

        assert reason == "Automation disabled or manual override"

    def test_unmapped_mode_decision_reason_fallback(
        self, notification_service, coordinator_data
    ):
        """A new_mode with no dedicated reason handler still gets a generic reason."""
        reason = notification_service.generate_decision_reason(
            BatteryMode.SELF_CONSUMPTION, BatteryMode.HOLD, coordinator_data
        )

        assert reason == "Mode changed: self_consumption -> hold"


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
