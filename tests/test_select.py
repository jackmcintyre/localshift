"""Tests for select platform entities.

Issue #660: Add missing platform entity tests (0% coverage)
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.localshift.const import (
    DOMAIN,
    SELECT_BATTERY_MODE,
    SELECT_ICONS,
    SELECT_NAMES,
    SELECT_OPTIONS,
    SWITCH_AUTOMATION_ENABLED,
    BatteryMode,
)
from custom_components.localshift.select import (
    BatteryModeSelect,
    OptimizationModeSelect,
    async_setup_entry,
)


@pytest.fixture
def mock_coordinator():
    """Create a mock coordinator."""
    coordinator = MagicMock()
    coordinator.data = MagicMock()
    coordinator.data.active_mode = BatteryMode.SELF_CONSUMPTION
    coordinator._notification_service = None
    coordinator.get_switch_state = MagicMock(return_value=False)
    coordinator.async_set_battery_mode = AsyncMock(return_value=True)
    return coordinator


@pytest.fixture
def mock_entry():
    """Create a mock config entry."""
    entry = MagicMock()
    entry.entry_id = "test_entry_123"
    entry.data = {}
    entry.options = {}
    return entry


class TestBatteryModeSelect:
    """Tests for BatteryModeSelect."""

    def test_select_initialization(self, mock_coordinator, mock_entry):
        """Test select entity initializes with correct attributes."""
        select = BatteryModeSelect(mock_coordinator, mock_entry)

        assert select._attr_unique_id == "localshift_battery_mode"
        assert select._attr_name == SELECT_NAMES[SELECT_BATTERY_MODE]
        assert select._attr_icon == SELECT_ICONS[SELECT_BATTERY_MODE]
        assert select._attr_options == SELECT_OPTIONS[SELECT_BATTERY_MODE]
        assert select._previous_mode == "self_consumption"

    def test_device_info(self, mock_coordinator, mock_entry):
        """Test device info is correctly generated."""
        select = BatteryModeSelect(mock_coordinator, mock_entry)
        device_info = select.device_info

        assert device_info["identifiers"] == {(DOMAIN, "test_entry_123")}
        assert device_info["name"] == "LocalShift"

    def test_current_option_self_consumption(self, mock_coordinator, mock_entry):
        """Test current option returns manual_mode when automation off."""
        mock_coordinator.get_switch_state.return_value = False
        mock_entry.options = {"manual_battery_mode": "self_consumption"}
        select = BatteryModeSelect(mock_coordinator, mock_entry)

        assert select.current_option == "self_consumption"

    def test_current_option_grid_charging(self, mock_coordinator, mock_entry):
        """Test current option returns automatic when automation is on."""
        mock_coordinator.get_switch_state.return_value = True
        select = BatteryModeSelect(mock_coordinator, mock_entry)

        assert select.current_option == "automatic"

    def test_current_option_returns_manual_mode_when_automation_off(
        self, mock_coordinator, mock_entry
    ):
        """Test current option returns _manual_mode when automation is disabled."""
        mock_coordinator.get_switch_state.return_value = False
        mock_entry.options = {"manual_battery_mode": "grid_charging"}
        select = BatteryModeSelect(mock_coordinator, mock_entry)

        assert select.current_option == "grid_charging"

    def test_current_option_returns_automatic_when_automation_on(
        self, mock_coordinator, mock_entry
    ):
        """Test current option returns automatic when automation is enabled."""
        mock_coordinator.get_switch_state.return_value = True
        select = BatteryModeSelect(mock_coordinator, mock_entry)

        assert select.current_option == "automatic"

    @pytest.mark.asyncio
    async def test_select_automatic_enables_automation_and_clears_manual_override(
        self, mock_coordinator, mock_entry
    ):
        """Test selecting automatic enables automation and clears manual_override."""
        mock_entry.options = {"manual_battery_mode": "grid_charging"}
        mock_coordinator.async_recompute_and_evaluate = AsyncMock()
        mock_hass = MagicMock()
        mock_hass.config_entries = MagicMock()
        mock_hass.config_entries.async_update_entry = MagicMock()
        select = BatteryModeSelect(mock_coordinator, mock_entry)
        select.hass = mock_hass
        select._attr_entity_id = "select.localshift_battery_mode"

        with patch.object(select, "async_write_ha_state"):
            await select.async_select_option("automatic")

        mock_coordinator.set_switch_state.assert_called_once_with(
            SWITCH_AUTOMATION_ENABLED, True
        )
        assert mock_coordinator.data.manual_override is False
        mock_hass.config_entries.async_update_entry.assert_called_once()
        update_call_kwargs = mock_hass.config_entries.async_update_entry.call_args[1]
        assert update_call_kwargs["options"]["switch_state_automation_enabled"] is True
        mock_coordinator.async_recompute_and_evaluate.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_select_manual_mode_sets_manual_override_and_persists(
        self, mock_coordinator, mock_entry
    ):
        """Test selecting manual mode disables automation, sets manual_override, and persists selection."""
        mock_coordinator.get_switch_state.return_value = False
        mock_entry.options = {}
        mock_hass = MagicMock()
        mock_hass.config_entries = MagicMock()
        mock_hass.config_entries.async_update_entry = MagicMock()
        select = BatteryModeSelect(mock_coordinator, mock_entry)
        select.hass = mock_hass
        select._attr_entity_id = "select.localshift_battery_mode"

        with patch.object(select, "async_write_ha_state"):
            await select.async_select_option("grid_charging")

        mock_coordinator.set_switch_state.assert_called_once_with(
            SWITCH_AUTOMATION_ENABLED, False
        )
        assert mock_coordinator.data.manual_override is True
        mock_hass.config_entries.async_update_entry.assert_called_once()
        update_call_kwargs = mock_hass.config_entries.async_update_entry.call_args[1]
        assert update_call_kwargs["options"]["switch_state_automation_enabled"] is False
        assert update_call_kwargs["options"]["manual_battery_mode"] == "grid_charging"
        assert select._manual_mode == "grid_charging"
        assert select._previous_mode == "grid_charging"

    @pytest.mark.asyncio
    async def test_startup_with_automation_off_syncs_manual_override(
        self, mock_coordinator, mock_entry
    ):
        """Test that async_added_to_hass syncs manual_override when automation is off."""
        mock_coordinator.get_switch_state.return_value = False
        select = BatteryModeSelect(mock_coordinator, mock_entry)
        select.hass = MagicMock()

        with patch.object(select, "async_write_ha_state"):
            await select.async_added_to_hass()

        assert mock_coordinator.data.manual_override is True

    @pytest.mark.asyncio
    async def test_async_select_option_invalid_logs_error(
        self, mock_coordinator, mock_entry
    ):
        """Test selecting invalid option logs error and returns early."""
        mock_entry.options = {}
        select = BatteryModeSelect(mock_coordinator, mock_entry)
        select.hass = MagicMock()
        select._attr_entity_id = "select.localshift_battery_mode"
        with patch.object(select, "async_write_ha_state"):
            with patch("custom_components.localshift.select._LOGGER") as mock_logger:
                await select.async_select_option("invalid_option_xyz")
        mock_logger.error.assert_called()

    @pytest.mark.asyncio
    async def test_async_select_option_battery_mode_value_error(
        self, mock_coordinator, mock_entry
    ):
        """Test ValueError when BatteryMode conversion fails."""
        mock_coordinator.get_switch_state.return_value = False
        mock_entry.options = {}
        mock_hass = MagicMock()
        mock_hass.config_entries = MagicMock()
        mock_hass.config_entries.async_update_entry = MagicMock()
        select = BatteryModeSelect(mock_coordinator, mock_entry)
        select.hass = mock_hass
        select._attr_entity_id = "select.localshift_battery_mode"
        with patch.object(select, "async_write_ha_state"):
            with patch("custom_components.localshift.select._LOGGER") as mock_logger:
                with patch(
                    "custom_components.localshift.select.BatteryMode",
                    side_effect=ValueError("test error"),
                ):
                    await select.async_select_option("grid_charging")
        mock_logger.error.assert_called()

    def test_handle_coordinator_update_early_exit(self, mock_coordinator, mock_entry):
        """Test _handle_coordinator_update returns early when _internal_update is True."""
        select = BatteryModeSelect(mock_coordinator, mock_entry)
        select._internal_update = True
        select._update_count = 0
        with patch.object(select, "async_write_ha_state") as mock_write:
            select._handle_coordinator_update()
        mock_write.assert_not_called()
        assert select._update_count == 0

    def test_handle_coordinator_update_debug_log_every_60(
        self, mock_coordinator, mock_entry
    ):
        """Test debug log is emitted when update_count % 60 == 0."""
        mock_coordinator.get_switch_state.return_value = False
        mock_entry.options = {"manual_battery_mode": "self_consumption"}
        select = BatteryModeSelect(mock_coordinator, mock_entry)
        select._update_count = 59
        select._last_committed_mode = "self_consumption"
        select._previous_mode = "self_consumption"
        with patch("custom_components.localshift.select._LOGGER") as mock_logger:
            select._handle_coordinator_update()
        assert select._update_count == 60
        mock_logger.debug.assert_called()

    def test_handle_coordinator_update_mode_change(self, mock_coordinator, mock_entry):
        """Test _handle_coordinator_update detects mode change."""
        mock_coordinator.get_switch_state.return_value = False
        mock_entry.options = {"manual_battery_mode": "grid_charging"}
        select = BatteryModeSelect(mock_coordinator, mock_entry)
        select._last_committed_mode = "self_consumption"
        select._update_count = 0
        select._change_count = 0
        with patch.object(select, "async_write_ha_state") as mock_write:
            with patch("custom_components.localshift.select._LOGGER") as mock_logger:
                select._handle_coordinator_update()
        mock_write.assert_called_once()
        assert select._change_count == 1
        mock_logger.debug.assert_called()

    @pytest.mark.asyncio
    async def test_async_select_option_sends_notification(
        self, mock_coordinator, mock_entry
    ):
        """Test that notification is sent when notification_service exists."""
        mock_coordinator.get_switch_state.return_value = False
        mock_entry.options = {}
        mock_notification_service = MagicMock()
        mock_notification_service.send_manual_action_notification = AsyncMock()
        mock_coordinator._notification_service = mock_notification_service
        mock_hass = MagicMock()
        mock_hass.config_entries = MagicMock()
        mock_hass.config_entries.async_update_entry = MagicMock()
        select = BatteryModeSelect(mock_coordinator, mock_entry)
        select.hass = mock_hass
        select._attr_entity_id = "select.localshift_battery_mode"
        with patch.object(select, "async_write_ha_state"):
            await select.async_select_option("grid_charging")
        mock_notification_service.send_manual_action_notification.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_async_select_option_reverts_on_failure(
        self, mock_coordinator, mock_entry
    ):
        """Test selection reverts when mode set fails."""
        mock_coordinator.async_set_battery_mode = AsyncMock(return_value=False)
        mock_entry.options = {}
        mock_hass = MagicMock()
        mock_hass.config_entries = MagicMock()
        mock_hass.config_entries.async_update_entry = MagicMock()
        select = BatteryModeSelect(mock_coordinator, mock_entry)
        select.hass = mock_hass
        select._previous_mode = "self_consumption"
        select._attr_entity_id = "select.localshift_battery_mode"
        with patch.object(select, "async_write_ha_state"):
            await select.async_select_option("grid_charging")
        assert select._previous_mode == "self_consumption"

    @pytest.mark.asyncio
    async def test_startup_with_automation_on_syncs_manual_override(
        self, mock_coordinator, mock_entry
    ):
        """Test that async_added_to_hass syncs manual_override when automation is on."""
        mock_coordinator.get_switch_state.return_value = True
        select = BatteryModeSelect(mock_coordinator, mock_entry)
        select.hass = MagicMock()
        with patch.object(select, "async_write_ha_state"):
            await select.async_added_to_hass()
        assert mock_coordinator.data.manual_override is False


class TestOptimizationModeSelect:
    """Tests for OptimizationModeSelect."""

    def test_select_initialization(self, mock_coordinator, mock_entry):
        """Test select entity initializes with correct attributes."""
        select = OptimizationModeSelect(mock_coordinator, mock_entry)

        assert select._attr_unique_id == "localshift_optimization_mode"
        assert select._attr_name == SELECT_NAMES["optimization_mode"]
        assert select._attr_icon == SELECT_ICONS["optimization_mode"]

    def test_current_option_returns_entry_option(self, mock_coordinator, mock_entry):
        """Test current_option returns value from entry options."""
        mock_entry.options = {"optimization_mode": "arbitrage"}
        select = OptimizationModeSelect(mock_coordinator, mock_entry)
        assert select.current_option == "arbitrage"

    def test_current_option_returns_default_when_invalid(
        self, mock_coordinator, mock_entry
    ):
        """Test current_option returns default for invalid option."""
        mock_entry.options = {"optimization_mode": "invalid_mode"}
        select = OptimizationModeSelect(mock_coordinator, mock_entry)
        assert select.current_option == "self_consumption"

    def test_current_option_returns_default_when_missing(
        self, mock_coordinator, mock_entry
    ):
        """Test current_option returns default when not in options."""
        mock_entry.options = {}
        select = OptimizationModeSelect(mock_coordinator, mock_entry)
        assert select.current_option == "self_consumption"

    @pytest.mark.asyncio
    async def test_async_select_option_valid(self, mock_coordinator, mock_entry):
        """Test selecting a valid option updates config entry."""
        mock_entry.options = {}
        mock_hass = MagicMock()
        mock_hass.config_entries = MagicMock()
        mock_hass.config_entries.async_update_entry = MagicMock()
        mock_coordinator.async_recompute_and_evaluate = AsyncMock()
        select = OptimizationModeSelect(mock_coordinator, mock_entry)
        select.hass = mock_hass
        select._attr_entity_id = "select.localshift_optimization_mode"
        with patch.object(select, "async_write_ha_state"):
            await select.async_select_option("arbitrage")
        mock_hass.config_entries.async_update_entry.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_select_option_invalid_logs_error(
        self, mock_coordinator, mock_entry
    ):
        """Test selecting invalid option logs error and returns."""
        mock_entry.options = {}
        select = OptimizationModeSelect(mock_coordinator, mock_entry)
        select.hass = MagicMock()
        select._attr_entity_id = "select.localshift_optimization_mode"
        with patch.object(select, "async_write_ha_state"):
            with patch("custom_components.localshift.select._LOGGER") as mock_logger:
                await select.async_select_option("invalid_mode")
        mock_logger.error.assert_called()

    @pytest.mark.asyncio
    async def test_async_added_to_hass(self, mock_coordinator, mock_entry):
        """Test async_added_to_hass registers listener."""
        mock_entry.options = {}
        select = OptimizationModeSelect(mock_coordinator, mock_entry)
        select.hass = MagicMock()
        mock_coordinator.async_add_listener = MagicMock(return_value=MagicMock())
        with patch.object(select, "async_on_remove") as mock_on_remove:
            await select.async_added_to_hass()
        mock_on_remove.assert_called_once()
        mock_coordinator.async_add_listener.assert_called_once()

    def test_handle_coordinator_update(self, mock_coordinator, mock_entry):
        """Test _handle_coordinator_update writes state."""
        mock_entry.options = {"optimization_mode": "arbitrage"}
        select = OptimizationModeSelect(mock_coordinator, mock_entry)
        with patch.object(select, "async_write_ha_state") as mock_write:
            select._handle_coordinator_update()
        mock_write.assert_called_once()

    def test_device_info_property(self, mock_coordinator, mock_entry):
        """Test device_info property returns correct device info."""
        select = OptimizationModeSelect(mock_coordinator, mock_entry)
        info = select.device_info
        assert info is not None


class TestAsyncSetupEntry:
    """Tests for async_setup_entry."""

    @pytest.mark.asyncio
    async def test_async_setup_entry_creates_both_selects(
        self, mock_coordinator, mock_entry
    ):
        """Test that async_setup_entry creates both select entities."""
        mock_entry.runtime_data = mock_coordinator
        added_entities = []

        def mock_async_add_entities(entities):
            added_entities.extend(entities)

        await async_setup_entry(MagicMock(), mock_entry, mock_async_add_entities)

        assert len(added_entities) == 2

    @pytest.mark.asyncio
    async def test_async_setup_entry_creates_battery_mode_select(
        self, mock_coordinator, mock_entry
    ):
        """Test that BatteryModeSelect is created."""
        mock_entry.runtime_data = mock_coordinator
        added_entities = []

        def mock_async_add_entities(entities):
            added_entities.extend(entities)

        await async_setup_entry(MagicMock(), mock_entry, mock_async_add_entities)

        select_classes = [type(s) for s in added_entities]
        assert BatteryModeSelect in select_classes

    @pytest.mark.asyncio
    async def test_async_setup_entry_creates_optimization_mode_select(
        self, mock_coordinator, mock_entry
    ):
        """Test that OptimizationModeSelect is created."""
        mock_entry.runtime_data = mock_coordinator
        added_entities = []

        def mock_async_add_entities(entities):
            added_entities.extend(entities)

        await async_setup_entry(MagicMock(), mock_entry, mock_async_add_entities)

        select_classes = [type(s) for s in added_entities]
        assert OptimizationModeSelect in select_classes
