"""Tests for number platform entities.

Issue #660: Add missing platform entity tests (0% coverage)
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.localshift.const import (
    CONF_BATTERY_TARGET,
    CONF_CHEAP_PRICE_PERCENTILE,
    CONF_MAX_PRECHARGE_PRICE,
    CONF_MINIMUM_TARGET_SOC,
    DEFAULT_BATTERY_TARGET,
    DEFAULT_CHEAP_PRICE_PERCENTILE,
    DEFAULT_MAX_PRECHARGE_PRICE,
    DEFAULT_MINIMUM_TARGET_SOC,
    DOMAIN,
    THRESHOLD_RANGES,
)
from custom_components.localshift.number import (
    NUMBER_DEFINITIONS,
    LocalShiftNumber,
    async_setup_entry,
)


@pytest.fixture
def mock_coordinator():
    """Create a mock coordinator."""
    coordinator = MagicMock()
    coordinator.async_recompute_and_evaluate = AsyncMock()
    return coordinator


@pytest.fixture
def mock_entry():
    """Create a mock config entry."""
    entry = MagicMock()
    entry.entry_id = "test_entry_123"
    entry.data = {}
    entry.options = {}
    return entry


class TestLocalShiftNumber:
    """Tests for LocalShiftNumber entity."""

    def test_number_initialization_cheap_price_percentile(
        self, mock_coordinator, mock_entry
    ):
        """Test number entity initialization for cheap price percentile."""
        number = LocalShiftNumber(
            mock_coordinator,
            mock_entry,
            CONF_CHEAP_PRICE_PERCENTILE,
            "Cheap Price Percentile",
            DEFAULT_CHEAP_PRICE_PERCENTILE,
        )

        assert number._attr_unique_id == "localshift_cheap_price_percentile"
        assert number._attr_name == "Cheap Price Percentile"
        assert number._conf_key == CONF_CHEAP_PRICE_PERCENTILE
        assert number._default == DEFAULT_CHEAP_PRICE_PERCENTILE

        spec = THRESHOLD_RANGES[CONF_CHEAP_PRICE_PERCENTILE]
        assert number._attr_native_min_value == spec["min"]
        assert number._attr_native_max_value == spec["max"]
        assert number._attr_native_step == spec["step"]
        assert number._attr_native_unit_of_measurement == spec["unit"]
        assert number._attr_icon == spec["icon"]

    def test_number_initialization_max_precharge_price(
        self, mock_coordinator, mock_entry
    ):
        """Test number entity initialization for max pre-charge price."""
        number = LocalShiftNumber(
            mock_coordinator,
            mock_entry,
            CONF_MAX_PRECHARGE_PRICE,
            "Max Pre-charge Price",
            DEFAULT_MAX_PRECHARGE_PRICE,
        )

        assert number._attr_unique_id == "localshift_max_pre_charge_price"
        assert number._attr_name == "Max Pre-charge Price"

        spec = THRESHOLD_RANGES[CONF_MAX_PRECHARGE_PRICE]
        assert number._attr_native_min_value == spec["min"]
        assert number._attr_native_max_value == spec["max"]
        assert number._attr_icon == spec["icon"]

    def test_number_initialization_battery_target(self, mock_coordinator, mock_entry):
        """Test number entity initialization for battery target."""
        number = LocalShiftNumber(
            mock_coordinator,
            mock_entry,
            CONF_BATTERY_TARGET,
            "Battery Target",
            DEFAULT_BATTERY_TARGET,
        )

        assert number._attr_unique_id == "localshift_battery_target"
        assert number._attr_name == "Battery Target"

        spec = THRESHOLD_RANGES[CONF_BATTERY_TARGET]
        assert number._attr_native_min_value == spec["min"]
        assert number._attr_native_max_value == spec["max"]

    def test_number_initialization_minimum_target_soc(
        self, mock_coordinator, mock_entry
    ):
        """Test number entity initialization for minimum target SOC."""
        number = LocalShiftNumber(
            mock_coordinator,
            mock_entry,
            CONF_MINIMUM_TARGET_SOC,
            "Minimum Target SOC",
            DEFAULT_MINIMUM_TARGET_SOC,
        )

        assert number._attr_unique_id == "localshift_minimum_target_soc"
        assert number._attr_name == "Minimum Target SOC"

    def test_number_device_info(self, mock_coordinator, mock_entry):
        """Test device info is correctly generated."""
        number = LocalShiftNumber(
            mock_coordinator,
            mock_entry,
            CONF_BATTERY_TARGET,
            "Battery Target",
            DEFAULT_BATTERY_TARGET,
        )
        device_info = number.device_info

        assert device_info["identifiers"] == {(DOMAIN, "test_entry_123")}
        assert device_info["name"] == "LocalShift"

    def test_native_value_returns_from_options(self, mock_coordinator, mock_entry):
        """Test native_value returns value from entry options."""
        mock_entry.options = {CONF_BATTERY_TARGET: 85}
        number = LocalShiftNumber(
            mock_coordinator,
            mock_entry,
            CONF_BATTERY_TARGET,
            "Battery Target",
            DEFAULT_BATTERY_TARGET,
        )

        assert number.native_value == 85

    def test_native_value_returns_default_when_not_in_options(
        self, mock_coordinator, mock_entry
    ):
        """Test native_value returns default when not in options."""
        mock_entry.options = {}
        number = LocalShiftNumber(
            mock_coordinator,
            mock_entry,
            CONF_BATTERY_TARGET,
            "Battery Target",
            DEFAULT_BATTERY_TARGET,
        )

        assert number.native_value == DEFAULT_BATTERY_TARGET

    @pytest.mark.asyncio
    async def test_async_set_native_value_updates_options(
        self, mock_coordinator, mock_entry
    ):
        """Test setting native value updates entry options."""
        mock_entry.options = {}
        mock_hass = MagicMock()
        mock_hass.config_entries = MagicMock()
        mock_hass.config_entries.async_update_entry = MagicMock()

        number = LocalShiftNumber(
            mock_coordinator,
            mock_entry,
            CONF_BATTERY_TARGET,
            "Battery Target",
            DEFAULT_BATTERY_TARGET,
        )
        number.hass = mock_hass
        number._attr_entity_id = "number.localshift_battery_target"

        with patch.object(number, "async_write_ha_state"):
            await number.async_set_native_value(90)

        mock_hass.config_entries.async_update_entry.assert_called_once()
        call_args = mock_hass.config_entries.async_update_entry.call_args
        assert call_args[0][0] == mock_entry
        assert call_args[1]["options"][CONF_BATTERY_TARGET] == 90


class TestNumberDefinitions:
    """Tests for NUMBER_DEFINITIONS constant."""

    def test_number_definitions_count(self):
        """Test that there are 6 number definitions (4 basic + 2 penalty)."""
        assert len(NUMBER_DEFINITIONS) == 6

    def test_number_definitions_contains_cheap_price_percentile(self):
        """Test definitions contain cheap price percentile."""
        keys = [d[0] for d in NUMBER_DEFINITIONS]
        assert CONF_CHEAP_PRICE_PERCENTILE in keys

    def test_number_definitions_contains_max_precharge_price(self):
        """Test definitions contain max pre-charge price."""
        keys = [d[0] for d in NUMBER_DEFINITIONS]
        assert CONF_MAX_PRECHARGE_PRICE in keys

    def test_number_definitions_contains_battery_target(self):
        """Test definitions contain battery target."""
        keys = [d[0] for d in NUMBER_DEFINITIONS]
        assert CONF_BATTERY_TARGET in keys

    def test_number_definitions_contains_minimum_target_soc(self):
        """Test definitions contain minimum target SOC."""
        keys = [d[0] for d in NUMBER_DEFINITIONS]
        assert CONF_MINIMUM_TARGET_SOC in keys


class TestAsyncSetupEntry:
    """Tests for async_setup_entry."""

    @pytest.mark.asyncio
    async def test_async_setup_entry_creates_all_numbers(
        self, mock_coordinator, mock_entry
    ):
        """Test that async_setup_entry creates all 6 number entities."""
        mock_entry.runtime_data = mock_coordinator
        added_entities = []

        def mock_async_add_entities(entities):
            added_entities.extend(entities)

        await async_setup_entry(MagicMock(), mock_entry, mock_async_add_entities)

        assert len(added_entities) == 6

    @pytest.mark.asyncio
    async def test_async_setup_entry_creates_localshift_number_instances(
        self, mock_coordinator, mock_entry
    ):
        """Test that async_setup_entry creates LocalShiftNumber instances."""
        mock_entry.runtime_data = mock_coordinator
        added_entities = []

        def mock_async_add_entities(entities):
            added_entities.extend(entities)

        await async_setup_entry(MagicMock(), mock_entry, mock_async_add_entities)

        for entity in added_entities:
            assert isinstance(entity, LocalShiftNumber)

    @pytest.mark.asyncio
    async def test_numbers_receive_coordinator_and_entry(
        self, mock_coordinator, mock_entry
    ):
        """Test that numbers receive coordinator and entry."""
        mock_entry.runtime_data = mock_coordinator
        added_entities = []

        def mock_async_add_entities(entities):
            added_entities.extend(entities)

        await async_setup_entry(MagicMock(), mock_entry, mock_async_add_entities)

        for entity in added_entities:
            assert entity.coordinator == mock_coordinator
            assert entity._entry == mock_entry
