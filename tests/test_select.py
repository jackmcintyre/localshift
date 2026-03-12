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
    _device_info,
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
        """Test current option returns self_consumption."""
        mock_coordinator.data.active_mode = BatteryMode.SELF_CONSUMPTION
        select = BatteryModeSelect(mock_coordinator, mock_entry)

        assert select.current_option == "self_consumption"

    def test_current_option_grid_charging(self, mock_coordinator, mock_entry):
        """Test current option returns grid_charging."""
        mock_coordinator.data.active_mode = BatteryMode.GRID_CHARGING
        select = BatteryModeSelect(mock_coordinator, mock_entry)

        assert select.current_option == "grid_charging"

    def test_current_option_manual_returns_previous(self, mock_coordinator, mock_entry):
        """Test current option returns previous mode when in MANUAL mode."""
        mock_coordinator.data.active_mode = BatteryMode.MANUAL
        select = BatteryModeSelect(mock_coordinator, mock_entry)
        select._previous_mode = "grid_charging"

        assert select.current_option == "grid_charging"

    def test_current_option_demand_block_returns_self_consumption(
        self, mock_coordinator, mock_entry
    ):
        """Test current option returns self_consumption when in DEMAND_BLOCK mode."""
        mock_coordinator.data.active_mode = BatteryMode.DEMAND_BLOCK
        select = BatteryModeSelect(mock_coordinator, mock_entry)

        assert select.current_option == "self_consumption"

    @pytest.mark.asyncio
    async def test_async_select_option_sets_battery_mode(self, mock_coordinator, mock_entry):
        """Test selecting an option sets the battery mode."""
        select = BatteryModeSelect(mock_coordinator, mock_entry)
        select._attr_entity_id = "select.localshift_battery_mode"

        with patch.object(select, "async_write_ha_state"):
            await select.async_select_option("grid_charging")

        mock_coordinator.async_set_battery_mode.assert_awaited_once()
        call_args = mock_coordinator.async_set_battery_mode.call_args[0][0]
        assert call_args == BatteryMode.GRID_CHARGING

    @pytest.mark.asyncio
    async def test_async_select_option_disables_automation(self, mock_coordinator, mock_entry):
        """Test selecting an option disables automation."""
        mock_coordinator.get_switch_state.return_value = True
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

    @pytest.mark.asyncio
    async def test_async_select_option_reverts_on_failure(self, mock_coordinator, mock_entry):
        """Test selection reverts when mode set fails."""
        mock_coordinator.async_set_battery_mode = AsyncMock(return_value=False)
        select = BatteryModeSelect(mock_coordinator, mock_entry)
        select._previous_mode = "self_consumption"
        select._attr_entity_id = "select.localshift_battery_mode"
        mock_hass = MagicMock()
        select.hass = mock_hass

        with patch.object(select, "async_write_ha_state"):
            await select.async_select_option("grid_charging")

        assert select._previous_mode == "self_consumption"


class TestOptimizationModeSelect:
    """Tests for OptimizationModeSelect."""

    def test_select_initialization(self, mock_coordinator, mock_entry):
        """Test select entity initializes with correct attributes."""
        select = OptimizationModeSelect(mock_coordinator, mock_entry)

        assert select._attr_unique_id == "localshift_optimization_mode"
        assert select._attr_name == SELECT_NAMES["optimization_mode"]
        assert select._attr_icon == SELECT_ICONS["optimization_mode"]


class TestAsyncSetupEntry:
    """Tests for async_setup_entry."""

    @pytest.mark.asyncio
    async def test_async_setup_entry_creates_both_selects(self, mock_coordinator, mock_entry):
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
