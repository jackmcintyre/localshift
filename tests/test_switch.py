"""Tests for switch platform entities.

Issue #660: Add missing platform entity tests (0% coverage)
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.localshift.const import (
    DOMAIN,
    SWITCH_AUTOMATION_ENABLED,
    SWITCH_DEFAULTS,
    SWITCH_DEMAND_WINDOW_BLOCK,
    SWITCH_DRY_RUN,
    SWITCH_ICONS,
    SWITCH_NAMES,
    SWITCH_SPIKE_DISCHARGE_CONSERVATIVE,
    SWITCH_SPIKE_DISCHARGE_ENABLED,
)
from custom_components.localshift.switch import (
    SWITCH_KEYS,
    SWITCH_STATE_PREFIX,
    LocalShiftSwitch,
    async_setup_entry,
)


@pytest.fixture
def mock_coordinator():
    """Create a mock coordinator."""
    coordinator = MagicMock()
    coordinator.async_set_self_consumption = AsyncMock()
    coordinator.async_recompute_and_evaluate = AsyncMock()
    coordinator.optimization_controller = MagicMock()
    coordinator.optimization_controller.set_learning_enabled = MagicMock()
    coordinator._notification_service = None
    coordinator.set_switch_state = MagicMock()
    return coordinator


@pytest.fixture
def mock_entry():
    """Create a mock config entry."""
    entry = MagicMock()
    entry.entry_id = "test_entry_123"
    entry.data = {}
    entry.options = {}
    return entry


class TestLocalShiftSwitch:
    """Tests for LocalShiftSwitch entity."""

    def test_switch_initialization(self, mock_coordinator, mock_entry):
        """Test switch entity initializes with correct attributes."""
        switch = LocalShiftSwitch(mock_coordinator, mock_entry, SWITCH_AUTOMATION_ENABLED)

        assert switch._key == SWITCH_AUTOMATION_ENABLED
        assert switch._entry == mock_entry
        assert switch.coordinator == mock_coordinator
        assert switch._attr_has_entity_name is True

    def test_switch_loads_default_state(self, mock_coordinator, mock_entry):
        """Test switch loads default state when not in options."""
        switch = LocalShiftSwitch(mock_coordinator, mock_entry, SWITCH_AUTOMATION_ENABLED)

        expected_default = SWITCH_DEFAULTS[SWITCH_AUTOMATION_ENABLED]
        assert switch._is_on == expected_default
        assert switch.is_on == expected_default

    def test_switch_loads_persisted_state(self, mock_coordinator, mock_entry):
        """Test switch loads persisted state from options."""
        option_key = f"{SWITCH_STATE_PREFIX}{SWITCH_AUTOMATION_ENABLED}"
        mock_entry.options = {option_key: False}

        switch = LocalShiftSwitch(mock_coordinator, mock_entry, SWITCH_AUTOMATION_ENABLED)

        assert switch._is_on is False

    def test_switch_attributes(self, mock_coordinator, mock_entry):
        """Test switch has correct unique_id, name, and icon."""
        switch = LocalShiftSwitch(mock_coordinator, mock_entry, SWITCH_AUTOMATION_ENABLED)

        assert switch._attr_unique_id == "localshift_automation_enabled"
        assert switch._attr_name == SWITCH_NAMES[SWITCH_AUTOMATION_ENABLED]
        assert switch._attr_icon == SWITCH_ICONS[SWITCH_AUTOMATION_ENABLED]

    def test_switch_device_info(self, mock_coordinator, mock_entry):
        """Test device info is correctly generated."""
        switch = LocalShiftSwitch(mock_coordinator, mock_entry, SWITCH_AUTOMATION_ENABLED)
        device_info = switch.device_info

        assert device_info["identifiers"] == {(DOMAIN, "test_entry_123")}
        assert device_info["name"] == "LocalShift"

    def test_switch_syncs_initial_state_to_coordinator(self, mock_coordinator, mock_entry):
        """Test switch syncs initial state to coordinator."""
        switch = LocalShiftSwitch(mock_coordinator, mock_entry, SWITCH_AUTOMATION_ENABLED)

        expected_default = SWITCH_DEFAULTS[SWITCH_AUTOMATION_ENABLED]
        mock_coordinator.set_switch_state.assert_called_once_with(
            SWITCH_AUTOMATION_ENABLED, expected_default
        )

    @pytest.mark.asyncio
    async def test_async_turn_on_updates_state(self, mock_coordinator, mock_entry):
        """Test turn_on updates switch state."""
        mock_hass = MagicMock()
        mock_hass.config_entries = MagicMock()
        mock_hass.config_entries.async_update_entry = MagicMock()

        switch = LocalShiftSwitch(mock_coordinator, mock_entry, SWITCH_AUTOMATION_ENABLED)
        switch.hass = mock_hass
        switch._attr_entity_id = "switch.localshift_automation_enabled"
        switch._is_on = False

        with patch.object(switch, "async_write_ha_state"):
            await switch.async_turn_on()

        assert switch._is_on is True
        mock_coordinator.set_switch_state.assert_called_with(SWITCH_AUTOMATION_ENABLED, True)

    @pytest.mark.asyncio
    async def test_async_turn_on_persists_state(self, mock_coordinator, mock_entry):
        """Test turn_on persists state to options."""
        mock_hass = MagicMock()
        mock_hass.config_entries = MagicMock()
        mock_hass.config_entries.async_update_entry = MagicMock()

        switch = LocalShiftSwitch(mock_coordinator, mock_entry, SWITCH_AUTOMATION_ENABLED)
        switch.hass = mock_hass
        switch._attr_entity_id = "switch.localshift_automation_enabled"

        with patch.object(switch, "async_write_ha_state"):
            await switch.async_turn_on()

        mock_hass.config_entries.async_update_entry.assert_called_once()
        call_args = mock_hass.config_entries.async_update_entry.call_args
        option_key = f"{SWITCH_STATE_PREFIX}{SWITCH_AUTOMATION_ENABLED}"
        assert call_args[1]["options"][option_key] is True

    @pytest.mark.asyncio
    async def test_async_turn_off_updates_state(self, mock_coordinator, mock_entry):
        """Test turn_off updates switch state."""
        mock_hass = MagicMock()
        mock_hass.config_entries = MagicMock()
        mock_hass.config_entries.async_update_entry = MagicMock()

        switch = LocalShiftSwitch(mock_coordinator, mock_entry, SWITCH_AUTOMATION_ENABLED)
        switch.hass = mock_hass
        switch._attr_entity_id = "switch.localshift_automation_enabled"
        switch._is_on = True

        with patch.object(switch, "async_write_ha_state"):
            await switch.async_turn_off()

        assert switch._is_on is False

    @pytest.mark.asyncio
    async def test_turn_off_automation_disabled_sets_self_consumption(
        self, mock_coordinator, mock_entry
    ):
        """Test turning off automation switch sets self consumption mode."""
        mock_hass = MagicMock()
        mock_hass.config_entries = MagicMock()
        mock_hass.config_entries.async_update_entry = MagicMock()

        switch = LocalShiftSwitch(mock_coordinator, mock_entry, SWITCH_AUTOMATION_ENABLED)
        switch.hass = mock_hass
        switch._attr_entity_id = "switch.localshift_automation_enabled"

        with patch.object(switch, "async_write_ha_state"):
            await switch.async_turn_off()

        mock_coordinator.async_set_self_consumption.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_turn_on_learning_enabled(self, mock_coordinator, mock_entry):
        """Test turning on learning switch enables learning."""
        mock_hass = MagicMock()
        mock_hass.config_entries = MagicMock()
        mock_hass.config_entries.async_update_entry = MagicMock()

        switch = LocalShiftSwitch(mock_coordinator, mock_entry, "enable_learning")
        switch.hass = mock_hass
        switch._attr_entity_id = "switch.localshift_enable_learning"

        with patch.object(switch, "async_write_ha_state"):
            await switch.async_turn_on()

        mock_coordinator.optimization_controller.set_learning_enabled.assert_called_once_with(True)


class TestSwitchKeys:
    """Tests for SWITCH_KEYS constant."""

    def test_switch_keys_contains_automation_enabled(self):
        """Test SWITCH_KEYS contains automation_enabled."""
        assert SWITCH_AUTOMATION_ENABLED in SWITCH_KEYS

    def test_switch_keys_contains_spike_discharge_enabled(self):
        """Test SWITCH_KEYS contains spike_discharge_enabled."""
        assert SWITCH_SPIKE_DISCHARGE_ENABLED in SWITCH_KEYS

    def test_switch_keys_contains_dry_run(self):
        """Test SWITCH_KEYS contains dry_run."""
        assert SWITCH_DRY_RUN in SWITCH_KEYS

    def test_switch_keys_contains_demand_window_block(self):
        """Test SWITCH_KEYS contains demand_window_block."""
        assert SWITCH_DEMAND_WINDOW_BLOCK in SWITCH_KEYS

    def test_switch_keys_count(self):
        """Test there are 8 switch keys."""
        assert len(SWITCH_KEYS) == 8


class TestAsyncSetupEntry:
    """Tests for async_setup_entry."""

    @pytest.mark.asyncio
    async def test_async_setup_entry_creates_all_switches(self, mock_coordinator, mock_entry):
        """Test that async_setup_entry creates all switch entities."""
        mock_entry.runtime_data = mock_coordinator
        added_entities = []

        def mock_async_add_entities(entities):
            added_entities.extend(entities)

        await async_setup_entry(MagicMock(), mock_entry, mock_async_add_entities)

        assert len(added_entities) == len(SWITCH_KEYS)

    @pytest.mark.asyncio
    async def test_async_setup_entry_creates_localshift_switch_instances(
        self, mock_coordinator, mock_entry
    ):
        """Test that async_setup_entry creates LocalShiftSwitch instances."""
        mock_entry.runtime_data = mock_coordinator
        added_entities = []

        def mock_async_add_entities(entities):
            added_entities.extend(entities)

        await async_setup_entry(MagicMock(), mock_entry, mock_async_add_entities)

        for entity in added_entities:
            assert isinstance(entity, LocalShiftSwitch)

    @pytest.mark.asyncio
    async def test_switches_receive_coordinator_and_entry(self, mock_coordinator, mock_entry):
        """Test that switches receive coordinator and entry."""
        mock_entry.runtime_data = mock_coordinator
        added_entities = []

        def mock_async_add_entities(entities):
            added_entities.extend(entities)

        await async_setup_entry(MagicMock(), mock_entry, mock_async_add_entities)

        for entity in added_entities:
            assert entity.coordinator == mock_coordinator
            assert entity._entry == mock_entry

    @pytest.mark.asyncio
    async def test_automation_enabled_switch_created(self, mock_coordinator, mock_entry):
        """Test that automation enabled switch is created."""
        mock_entry.runtime_data = mock_coordinator
        added_entities = []

        def mock_async_add_entities(entities):
            added_entities.extend(entities)

        await async_setup_entry(MagicMock(), mock_entry, mock_async_add_entities)

        switch_keys = [s._key for s in added_entities]
        assert SWITCH_AUTOMATION_ENABLED in switch_keys

    @pytest.mark.asyncio
    async def test_spike_discharge_switch_created(self, mock_coordinator, mock_entry):
        """Test that spike discharge switch is created."""
        mock_entry.runtime_data = mock_coordinator
        added_entities = []

        def mock_async_add_entities(entities):
            added_entities.extend(entities)

        await async_setup_entry(MagicMock(), mock_entry, mock_async_add_entities)

        switch_keys = [s._key for s in added_entities]
        assert SWITCH_SPIKE_DISCHARGE_ENABLED in switch_keys
