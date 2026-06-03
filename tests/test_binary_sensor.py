"""Tests for binary_sensor platform entities.

Issue #660: Add missing platform entity tests (0% coverage)
"""

from unittest.mock import MagicMock

import pytest

from custom_components.localshift.binary_sensor import (
    AmberExpressDemandWindowSensor,
    BoostChargeActiveSensor,
    BoostChargeNeededSensor,
    DemandWindowActiveSensor,
    ExcessSolarAvailableSensor,
    ForceChargeActiveSensor,
    ForceDischargeActiveSensor,
    ForecastExpensivePeriodSensor,
    ForecastSpikeWithinWindowSensor,
    LocalShiftBinarySensorBase,
    SolarCanReachTargetSensor,
    TeslaOverrideActiveSensor,
    async_setup_entry,
)
from custom_components.localshift.const import DOMAIN


@pytest.fixture
def mock_coordinator():
    """Create a mock coordinator with data attributes."""
    coordinator = MagicMock()
    coordinator.data = MagicMock()
    coordinator.data.forecast_spike_within_window = True
    coordinator.data.max_forecast_price = 1.50
    coordinator.data.max_buy_forecast_price = 1.45
    coordinator.data.force_discharge_active = True
    coordinator.data.force_charge_active = False
    coordinator.data.boost_charge_active = True
    coordinator.data.forecast_expensive_period_coming = True
    coordinator.data.solar_can_reach_target = True
    coordinator.data.boost_charge_needed = False
    coordinator.data.demand_window_active = True
    coordinator.data.excess_solar_available = True
    coordinator.data.current_excess_rate_kw = 2.5
    coordinator.data.soc = 75.0
    coordinator.data.battery_power_kw = -2.0
    coordinator.data.can_add_load_now = True
    coordinator.data.safe_additional_load_kw = 3.0
    coordinator.data.operation_mode = "autonomous"
    coordinator.data.backup_reserve = 20
    coordinator.data.recent_decision_log = []
    coordinator._state_machine = MagicMock()
    coordinator._state_machine.is_tesla_override_active.return_value = False
    return coordinator


@pytest.fixture
def mock_entry():
    """Create a mock config entry."""
    entry = MagicMock()
    entry.entry_id = "test_entry_123"
    entry.data = {}
    entry.options = {}
    return entry


class TestBinarySensorBase:
    """Tests for LocalShiftBinarySensorBase."""

    def test_base_sensor_initialization(self, mock_coordinator, mock_entry):
        """Test base sensor initializes correctly."""
        sensor = LocalShiftBinarySensorBase(mock_coordinator, mock_entry)

        assert sensor.coordinator == mock_coordinator
        assert sensor._entry == mock_entry
        assert sensor._attr_has_entity_name is True

    def test_base_sensor_device_info(self, mock_coordinator, mock_entry):
        """Test device info is correctly generated."""
        sensor = LocalShiftBinarySensorBase(mock_coordinator, mock_entry)
        device_info = sensor.device_info

        assert device_info["identifiers"] == {(DOMAIN, "test_entry_123")}
        assert device_info["name"] == "LocalShift"
        assert device_info["manufacturer"] == "Custom"
        assert device_info["model"] == "Solar Battery Automation"
        assert device_info["sw_version"] == "0.0.2"

    def test_base_sensor_update_from_coordinator_is_noop(
        self, mock_coordinator, mock_entry
    ):
        """Test base _update_from_coordinator is a no-op."""
        sensor = LocalShiftBinarySensorBase(mock_coordinator, mock_entry)
        # Should not raise and should not modify anything
        sensor._update_from_coordinator()


class TestForecastSpikeWithinWindowSensor:
    """Tests for ForecastSpikeWithinWindowSensor."""

    def test_sensor_initialization(self, mock_coordinator, mock_entry):
        """Test sensor initializes with correct attributes."""
        sensor = ForecastSpikeWithinWindowSensor(mock_coordinator, mock_entry)

        assert sensor._attr_unique_id == "localshift_price_spike_coming"
        assert sensor._attr_name == "Price Spike Coming"
        assert sensor._attr_icon == "mdi:flash-alert-outline"

    def test_sensor_state_update(self, mock_coordinator, mock_entry):
        """Test sensor updates state from coordinator."""
        sensor = ForecastSpikeWithinWindowSensor(mock_coordinator, mock_entry)

        sensor._update_from_coordinator()

        assert sensor._attr_is_on is True

    def test_sensor_state_update_false(self, mock_coordinator, mock_entry):
        """Test sensor updates state to False."""
        mock_coordinator.data.forecast_spike_within_window = False
        sensor = ForecastSpikeWithinWindowSensor(mock_coordinator, mock_entry)

        sensor._update_from_coordinator()

        assert sensor._attr_is_on is False

    def test_extra_state_attributes(self, mock_coordinator, mock_entry):
        """Test extra state attributes are returned."""
        sensor = ForecastSpikeWithinWindowSensor(mock_coordinator, mock_entry)

        attrs = sensor.extra_state_attributes

        assert attrs["max_forecast_price"] == 1.50
        assert attrs["max_buy_forecast_price"] == 1.45


class TestForceDischargeActiveSensor:
    """Tests for ForceDischargeActiveSensor."""

    def test_sensor_initialization(self, mock_coordinator, mock_entry):
        """Test sensor initializes with correct attributes."""
        sensor = ForceDischargeActiveSensor(mock_coordinator, mock_entry)

        assert sensor._attr_unique_id == "localshift_discharge_forced"
        assert sensor._attr_name == "Discharge Forced"
        assert sensor._attr_icon == "mdi:battery-arrow-down"

    def test_sensor_state_update(self, mock_coordinator, mock_entry):
        """Test sensor updates state from coordinator."""
        sensor = ForceDischargeActiveSensor(mock_coordinator, mock_entry)

        sensor._update_from_coordinator()

        assert sensor._attr_is_on is True


class TestForceChargeActiveSensor:
    """Tests for ForceChargeActiveSensor."""

    def test_sensor_initialization(self, mock_coordinator, mock_entry):
        """Test sensor initializes with correct attributes."""
        sensor = ForceChargeActiveSensor(mock_coordinator, mock_entry)

        assert sensor._attr_unique_id == "localshift_charge_forced"
        assert sensor._attr_name == "Charge Forced"
        assert sensor._attr_icon == "mdi:battery-charging"

    def test_sensor_state_update(self, mock_coordinator, mock_entry):
        """Test sensor updates state from coordinator."""
        sensor = ForceChargeActiveSensor(mock_coordinator, mock_entry)

        sensor._update_from_coordinator()

        assert sensor._attr_is_on is False


class TestBoostChargeActiveSensor:
    """Tests for BoostChargeActiveSensor."""

    def test_sensor_initialization(self, mock_coordinator, mock_entry):
        """Test sensor initializes with correct attributes."""
        sensor = BoostChargeActiveSensor(mock_coordinator, mock_entry)

        assert sensor._attr_unique_id == "localshift_charge_boost"
        assert sensor._attr_name == "Charge Boost"
        assert sensor._attr_icon == "mdi:battery-charging-high"

    def test_sensor_state_update(self, mock_coordinator, mock_entry):
        """Test sensor updates state from coordinator."""
        sensor = BoostChargeActiveSensor(mock_coordinator, mock_entry)

        sensor._update_from_coordinator()

        assert sensor._attr_is_on is True


class TestForecastExpensivePeriodSensor:
    """Tests for ForecastExpensivePeriodSensor."""

    def test_sensor_initialization(self, mock_coordinator, mock_entry):
        """Test sensor initializes with correct attributes."""
        sensor = ForecastExpensivePeriodSensor(mock_coordinator, mock_entry)

        assert sensor._attr_unique_id == "localshift_price_expensive_coming"
        assert sensor._attr_name == "Price Expensive Coming"
        assert sensor._attr_icon == "mdi:currency-usd"

    def test_sensor_state_update(self, mock_coordinator, mock_entry):
        """Test sensor updates state from coordinator."""
        sensor = ForecastExpensivePeriodSensor(mock_coordinator, mock_entry)

        sensor._update_from_coordinator()

        assert sensor._attr_is_on is True


class TestSolarCanReachTargetSensor:
    """Tests for SolarCanReachTargetSensor."""

    def test_sensor_initialization(self, mock_coordinator, mock_entry):
        """Test sensor initializes with correct attributes."""
        sensor = SolarCanReachTargetSensor(mock_coordinator, mock_entry)

        assert sensor._attr_unique_id == "localshift_solar_can_reach_target"
        assert sensor._attr_name == "Solar Can Reach Target"
        assert sensor._attr_icon == "mdi:white-balance-sunny"

    def test_sensor_state_update(self, mock_coordinator, mock_entry):
        """Test sensor updates state from coordinator."""
        sensor = SolarCanReachTargetSensor(mock_coordinator, mock_entry)

        sensor._update_from_coordinator()

        assert sensor._attr_is_on is True


class TestBoostChargeNeededSensor:
    """Tests for BoostChargeNeededSensor."""

    def test_sensor_initialization(self, mock_coordinator, mock_entry):
        """Test sensor initializes with correct attributes."""
        sensor = BoostChargeNeededSensor(mock_coordinator, mock_entry)

        assert sensor._attr_unique_id == "localshift_charge_boost_needed"
        assert sensor._attr_name == "Charge Boost Needed"
        assert sensor._attr_icon == "mdi:speedometer"

    def test_sensor_state_update(self, mock_coordinator, mock_entry):
        """Test sensor updates state from coordinator."""
        sensor = BoostChargeNeededSensor(mock_coordinator, mock_entry)

        sensor._update_from_coordinator()

        assert sensor._attr_is_on is False


class TestDemandWindowActiveSensor:
    """Tests for DemandWindowActiveSensor."""

    def test_sensor_initialization(self, mock_coordinator, mock_entry):
        """Test sensor initializes with correct attributes."""
        sensor = DemandWindowActiveSensor(mock_coordinator, mock_entry)

        assert sensor._attr_unique_id == "localshift_demand_window"
        assert sensor._attr_name == "Demand Window"

    def test_sensor_state_update(self, mock_coordinator, mock_entry):
        """Test sensor updates state from coordinator."""
        sensor = DemandWindowActiveSensor(mock_coordinator, mock_entry)

        sensor._update_from_coordinator()

        assert sensor._attr_is_on is True

    def test_icon_when_on(self, mock_coordinator, mock_entry):
        """Test icon changes when sensor is on."""
        sensor = DemandWindowActiveSensor(mock_coordinator, mock_entry)
        sensor._attr_is_on = True

        assert sensor.icon == "mdi:clock-alert"

    def test_icon_when_off(self, mock_coordinator, mock_entry):
        """Test icon changes when sensor is off."""
        sensor = DemandWindowActiveSensor(mock_coordinator, mock_entry)
        sensor._attr_is_on = False

        assert sensor.icon == "mdi:clock-outline"


class TestExcessSolarAvailableSensor:
    """Tests for ExcessSolarAvailableSensor."""

    def test_sensor_initialization(self, mock_coordinator, mock_entry):
        """Test sensor initializes with correct attributes."""
        sensor = ExcessSolarAvailableSensor(mock_coordinator, mock_entry)

        assert sensor._attr_unique_id == "localshift_excess_solar_available"
        assert sensor._attr_name == "Excess Solar Available"

    def test_sensor_state_update(self, mock_coordinator, mock_entry):
        """Test sensor updates state from coordinator."""
        sensor = ExcessSolarAvailableSensor(mock_coordinator, mock_entry)

        sensor._update_from_coordinator()

        assert sensor._attr_is_on is True

    def test_icon_when_on(self, mock_coordinator, mock_entry):
        """Test icon changes when sensor is on."""
        sensor = ExcessSolarAvailableSensor(mock_coordinator, mock_entry)
        sensor._attr_is_on = True

        assert sensor.icon == "mdi:solar-power-variant"

    def test_icon_when_off(self, mock_coordinator, mock_entry):
        """Test icon changes when sensor is off."""
        sensor = ExcessSolarAvailableSensor(mock_coordinator, mock_entry)
        sensor._attr_is_on = False

        assert sensor.icon == "mdi:solar-power-variant-outline"

    def test_extra_state_attributes(self, mock_coordinator, mock_entry):
        """Test extra state attributes are returned."""
        sensor = ExcessSolarAvailableSensor(mock_coordinator, mock_entry)

        attrs = sensor.extra_state_attributes

        assert attrs["current_excess_kw"] == 2.5
        assert attrs["battery_soc"] == 75.0
        assert attrs["battery_charging"] is True
        assert attrs["can_add_load_now"] is True
        assert attrs["safe_additional_load_kw"] == 3.0


class TestTeslaOverrideActiveSensor:
    """Tests for TeslaOverrideActiveSensor."""

    def test_sensor_initialization(self, mock_coordinator, mock_entry):
        """Test sensor initializes with correct attributes."""
        sensor = TeslaOverrideActiveSensor(mock_coordinator, mock_entry)

        assert sensor._attr_unique_id == "localshift_tesla_override_active"
        assert sensor._attr_name == "Tesla Override Active"

    def test_sensor_state_update_not_overridden(self, mock_coordinator, mock_entry):
        """Test sensor updates state when Tesla is not overriding."""
        sensor = TeslaOverrideActiveSensor(mock_coordinator, mock_entry)

        sensor._update_from_coordinator()

        assert sensor._attr_is_on is False

    def test_sensor_state_update_overridden(self, mock_coordinator, mock_entry):
        """Test sensor updates state when Tesla is overriding."""
        mock_coordinator._state_machine.is_tesla_override_active.return_value = True
        sensor = TeslaOverrideActiveSensor(mock_coordinator, mock_entry)

        sensor._update_from_coordinator()

        assert sensor._attr_is_on is True

    def test_sensor_state_update_no_state_machine(self, mock_coordinator, mock_entry):
        """Test sensor handles missing state machine."""
        mock_coordinator._state_machine = None
        sensor = TeslaOverrideActiveSensor(mock_coordinator, mock_entry)

        sensor._update_from_coordinator()

        assert sensor._attr_is_on is False

    def test_icon_when_on(self, mock_coordinator, mock_entry):
        """Test icon changes when sensor is on."""
        sensor = TeslaOverrideActiveSensor(mock_coordinator, mock_entry)
        sensor._attr_is_on = True

        assert sensor.icon == "mdi:shield-alert"

    def test_icon_when_off(self, mock_coordinator, mock_entry):
        """Test icon changes when sensor is off."""
        sensor = TeslaOverrideActiveSensor(mock_coordinator, mock_entry)
        sensor._attr_is_on = False

        assert sensor.icon == "mdi:shield-check"

    def test_extra_state_attributes_when_active(self, mock_coordinator, mock_entry):
        """Test extra state attributes when Tesla override is active."""
        mock_coordinator._state_machine.is_tesla_override_active.return_value = True
        sensor = TeslaOverrideActiveSensor(mock_coordinator, mock_entry)
        sensor._attr_is_on = True

        attrs = sensor.extra_state_attributes

        assert attrs["operation_mode"] == "autonomous"
        assert attrs["backup_reserve"] == 20
        assert "Tesla has taken control" in attrs["description"]

    def test_extra_state_attributes_when_inactive(self, mock_coordinator, mock_entry):
        """Test extra state attributes when Tesla override is inactive."""
        sensor = TeslaOverrideActiveSensor(mock_coordinator, mock_entry)
        sensor._attr_is_on = False

        attrs = sensor.extra_state_attributes

        assert attrs["operation_mode"] == "autonomous"
        assert attrs["backup_reserve"] == 20
        assert "Tesla is not overriding control" in attrs["description"]


class TestAsyncSetupEntry:
    """Tests for async_setup_entry."""

    @pytest.mark.asyncio
    async def test_async_setup_entry_creates_all_sensors(
        self, mock_coordinator, mock_entry
    ):
        """Test that async_setup_entry creates all 11 binary sensors."""
        mock_entry.runtime_data = mock_coordinator
        added_entities = []

        def mock_async_add_entities(entities):
            added_entities.extend(entities)

        await async_setup_entry(MagicMock(), mock_entry, mock_async_add_entities)

        assert len(added_entities) == 11

        sensor_classes = [type(s) for s in added_entities]
        assert ForecastSpikeWithinWindowSensor in sensor_classes
        assert ForceDischargeActiveSensor in sensor_classes
        assert ForceChargeActiveSensor in sensor_classes
        assert BoostChargeActiveSensor in sensor_classes
        assert ForecastExpensivePeriodSensor in sensor_classes
        assert SolarCanReachTargetSensor in sensor_classes
        assert BoostChargeNeededSensor in sensor_classes
        assert DemandWindowActiveSensor in sensor_classes
        assert ExcessSolarAvailableSensor in sensor_classes
        assert TeslaOverrideActiveSensor in sensor_classes
        assert AmberExpressDemandWindowSensor in sensor_classes

    @pytest.mark.asyncio
    async def test_async_setup_entry_passes_coordinator(
        self, mock_coordinator, mock_entry
    ):
        """Test that sensors receive the coordinator."""
        mock_entry.runtime_data = mock_coordinator
        added_entities = []

        def mock_async_add_entities(entities):
            added_entities.extend(entities)

        await async_setup_entry(MagicMock(), mock_entry, mock_async_add_entities)

        for sensor in added_entities:
            assert sensor.coordinator == mock_coordinator
            assert sensor._entry == mock_entry
