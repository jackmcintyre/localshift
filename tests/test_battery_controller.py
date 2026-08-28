"""Unit tests for BatteryController."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.localshift.integration.controller import BatteryController
from custom_components.localshift.const import (
    CONF_MINIMUM_TARGET_SOC,
    DEFAULT_MINIMUM_TARGET_SOC,
    TESLEMETRY_EXPORT_BATTERY_OK,
    TESLEMETRY_EXPORT_PV_ONLY,
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
def mock_get_entity_id():
    """Mock function to get entity IDs."""

    def _get_entity_id(key):
        entity_map = {
            "teslemetry_soc": "sensor.tesla_powerwall_soc",
            "teslemetry_operation_mode": "select.tesla_powerwall_operation_mode",
            "teslemetry_backup_reserve": "number.tesla_powerwall_backup_reserve",
            "teslemetry_allow_export": "select.tesla_powerwall_allow_export",
            "teslemetry_grid_power": "sensor.tesla_powerwall_grid_power",
            "teslemetry_allow_charging_from_grid": "switch.tesla_powerwall_allow_charging_from_grid",
            "minimum_target_soc": "number.minimum_target_soc",
        }
        return entity_map.get(key)

    return _get_entity_id


@pytest.fixture
def battery_controller(mock_hass, mock_get_entity_id):
    """Create a BatteryController instance.

    Wired with a get_option_func so minimum_target_soc reads work (Issue #894).
    Individual tests can override the option value by constructing their own
    controller with a custom get_option_func.
    """
    options = {CONF_MINIMUM_TARGET_SOC: DEFAULT_MINIMUM_TARGET_SOC}

    def get_option(key, default=None):
        return options.get(key, default)

    return BatteryController(
        mock_hass, mock_get_entity_id, get_option_func=get_option
    )


@pytest.fixture
def coordinator_data():
    """Create basic CoordinatorData for battery controller tests."""
    from custom_components.localshift.coordinator import CoordinatorData

    data = CoordinatorData()
    data.soc = 50.0
    data.operation_mode = "autonomous"
    data.backup_reserve = 50
    data.manual_override = False
    return data


# =============================================================================
# SET_SELF_CONSUMPTION TESTS
# =============================================================================


class TestSetSelfConsumption:
    """Tests for set_self_consumption method."""

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("mock_battery_sleep")
    async def test_set_self_consumption_success(
        self, battery_controller, coordinator_data, mock_hass
    ):
        """Test successful transition to self consumption mode."""
        # Mock service calls to succeed
        mock_hass.services.async_call.return_value = None

        # Mock state reads for validation
        def mock_get_state(entity_id):
            if entity_id is None:
                return None
            state = MagicMock()
            if "operation_mode" in entity_id:
                state.state = "self_consumption"
            elif "backup_reserve" in entity_id:
                state.state = "10"
            elif "allow_export" in entity_id:
                state.state = TESLEMETRY_EXPORT_PV_ONLY
            elif "allow_charging_from_grid" in entity_id:
                state.state = "off"
            return state

        mock_hass.states.get = mock_get_state

        result = await battery_controller.set_self_consumption(coordinator_data)

        assert result is True
        assert coordinator_data.manual_override is False
        # Verify service calls were made in correct order
        assert mock_hass.services.async_call.call_count >= 3

    @pytest.mark.asyncio
    async def test_set_self_consumption_dry_run(
        self, battery_controller, coordinator_data, mock_hass
    ):
        """Test dry run mode does not execute commands."""
        result = await battery_controller.set_self_consumption(
            coordinator_data, dry_run=True
        )

        assert result is True
        assert coordinator_data.manual_override is False
        # No service calls should be made in dry run
        mock_hass.services.async_call.assert_not_called()

    @pytest.mark.asyncio
    async def test_set_self_consumption_export_mode_fails(
        self, battery_controller, coordinator_data, mock_hass
    ):
        """Test failure when export mode cannot be set."""
        mock_hass.services.async_call.side_effect = Exception("Service call failed")

        result = await battery_controller.set_self_consumption(coordinator_data)

        assert result is False

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("mock_battery_sleep")
    async def test_set_self_consumption_preserves_manual_override(
        self, battery_controller, coordinator_data, mock_hass
    ):
        """Test that manual_override is NOT cleared by battery controller.

        The manual_override flag is managed by button handlers (who set it)
        and the state machine (who auto-clears it after timeout).
        Battery controller should NOT modify this flag.
        """
        coordinator_data.manual_override = True
        mock_hass.services.async_call.return_value = None

        def mock_get_state(entity_id):
            if entity_id is None:
                return None
            state = MagicMock()
            if "operation_mode" in entity_id:
                state.state = "self_consumption"
            elif "backup_reserve" in entity_id:
                state.state = "10"
            elif "allow_export" in entity_id:
                state.state = TESLEMETRY_EXPORT_PV_ONLY
            elif "allow_charging_from_grid" in entity_id:
                state.state = "on"
            return state

        mock_hass.states.get = mock_get_state

        await battery_controller.set_self_consumption(coordinator_data)

        # manual_override should still be True - battery controller doesn't clear it
        assert coordinator_data.manual_override is True


# =============================================================================
# SET_FORCE_CHARGE TESTS
# =============================================================================


class TestSetForceCharge:
    """Tests for set_force_charge method."""

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("mock_battery_sleep")
    async def test_set_force_charge_success(
        self, battery_controller, coordinator_data, mock_hass
    ):
        """Test successful transition to force charge mode.

        Force charge now uses backup mode for 3.3 kW grid charging.
        The reserve is clamped to 80 for targets 81-99% (Tesla firmware restriction).
        """
        mock_hass.services.async_call.return_value = None

        def mock_get_state(entity_id):
            if entity_id is None:
                return None
            state = MagicMock()
            if "operation_mode" in entity_id:
                state.state = "backup"
            elif "backup_reserve" in entity_id:
                state.state = "100"  # Default target=100 is not clamped
            elif "allow_export" in entity_id:
                state.state = TESLEMETRY_EXPORT_PV_ONLY
            elif "allow_charging_from_grid" in entity_id:
                state.state = "on"
            return state

        mock_hass.states.get = mock_get_state

        result = await battery_controller.set_force_charge(coordinator_data)

        assert result is True
        assert coordinator_data.manual_override is False

    @pytest.mark.asyncio
    async def test_set_force_charge_dry_run(
        self, battery_controller, coordinator_data, mock_hass
    ):
        """Test dry run mode for force charge."""
        result = await battery_controller.set_force_charge(
            coordinator_data, dry_run=True
        )

        assert result is True
        mock_hass.services.async_call.assert_not_called()

    @pytest.mark.asyncio
    async def test_set_force_charge_operation_mode_fails(
        self, battery_controller, coordinator_data, mock_hass
    ):
        """Test failure when operation mode cannot be set."""
        # First call (export mode) succeeds, second (operation mode) fails
        call_count = [0]

        async def mock_async_call(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] >= 2:
                raise Exception("Service call failed")

        mock_hass.services.async_call = mock_async_call

        result = await battery_controller.set_force_charge(coordinator_data)

        assert result is False


# =============================================================================
# SET_BOOST_CHARGE TESTS
# =============================================================================


class TestSetBoostCharge:
    """Tests for set_boost_charge method."""

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("mock_battery_sleep")
    async def test_set_boost_charge_success(
        self, battery_controller, coordinator_data, mock_hass
    ):
        """Test successful transition to boost charge mode."""
        mock_hass.services.async_call.return_value = None

        def mock_get_state(entity_id):
            if entity_id is None:
                return None
            state = MagicMock()
            if "operation_mode" in entity_id:
                state.state = "autonomous"
            elif "backup_reserve" in entity_id:
                state.state = "100"
            elif "allow_export" in entity_id:
                state.state = TESLEMETRY_EXPORT_PV_ONLY
            elif "allow_charging_from_grid" in entity_id:
                state.state = "on"
            return state

        mock_hass.states.get = mock_get_state

        result = await battery_controller.set_boost_charge(coordinator_data)

        assert result is True
        assert coordinator_data.manual_override is False

    @pytest.mark.asyncio
    async def test_set_boost_charge_dry_run(
        self, battery_controller, coordinator_data, mock_hass
    ):
        """Test dry run mode for boost charge."""
        result = await battery_controller.set_boost_charge(
            coordinator_data, dry_run=True
        )

        assert result is True
        mock_hass.services.async_call.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("mock_battery_sleep")
    async def test_set_boost_charge_sets_100_reserve(
        self, battery_controller, coordinator_data, mock_hass
    ):
        """Test that boost charge sets reserve to 100%."""
        mock_hass.services.async_call.return_value = None

        def mock_get_state(entity_id):
            if entity_id is None:
                return None
            state = MagicMock()
            if "operation_mode" in entity_id:
                state.state = "autonomous"
            elif "backup_reserve" in entity_id:
                state.state = "100"
            elif "allow_export" in entity_id:
                state.state = TESLEMETRY_EXPORT_PV_ONLY
            elif "allow_charging_from_grid" in entity_id:
                state.state = "on"
            return state

        mock_hass.states.get = mock_get_state

        await battery_controller.set_boost_charge(coordinator_data)

        # Check that set_value was called with 100
        # call_args_list: each call is (positional_args, kwargs)
        # async_call("number", "set_value", {"entity_id": ..., "value": ...}, blocking=True)
        # So call[0][2] is the data dict
        calls = mock_hass.services.async_call.call_args_list
        reserve_call = None
        for call in calls:
            if call[0][0] == "number" and call[0][1] == "set_value":
                reserve_call = call
                break
        assert reserve_call is not None
        assert reserve_call[0][2]["value"] == 100


# =============================================================================
# SET_FORCE_DISCHARGE TESTS
# =============================================================================


class TestSetForceDischarge:
    """Tests for set_force_discharge method."""

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("mock_battery_sleep")
    async def test_set_force_discharge_success(
        self, battery_controller, coordinator_data, mock_hass
    ):
        """Test successful transition to force discharge mode."""
        mock_hass.services.async_call.return_value = None

        def mock_get_state(entity_id):
            if entity_id is None:
                return None
            state = MagicMock()
            if "operation_mode" in entity_id:
                state.state = "autonomous"
            elif "backup_reserve" in entity_id:
                state.state = str(DEFAULT_MINIMUM_TARGET_SOC)
            elif "allow_export" in entity_id:
                state.state = TESLEMETRY_EXPORT_BATTERY_OK
            return state

        mock_hass.states.get = mock_get_state

        result = await battery_controller.set_force_discharge(coordinator_data)

        assert result is True
        assert coordinator_data.manual_override is False

    @pytest.mark.asyncio
    async def test_set_force_discharge_dry_run(
        self, battery_controller, coordinator_data, mock_hass
    ):
        """Test dry run mode for force discharge."""
        result = await battery_controller.set_force_discharge(
            coordinator_data, dry_run=True
        )

        assert result is True
        mock_hass.services.async_call.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("mock_battery_sleep")
    async def test_set_force_discharge_with_reserve_soc_override(
        self, battery_controller, coordinator_data, mock_hass
    ):
        """Test force discharge with custom reserve SOC."""
        mock_hass.services.async_call.return_value = None

        def mock_get_state(entity_id):
            if entity_id is None:
                return None
            state = MagicMock()
            if "operation_mode" in entity_id:
                state.state = "autonomous"
            elif "backup_reserve" in entity_id:
                state.state = "20"
            elif "allow_export" in entity_id:
                state.state = TESLEMETRY_EXPORT_BATTERY_OK
            elif "allow_charging_from_grid" in entity_id:
                state.state = "on"
            return state

        mock_hass.states.get = mock_get_state

        result = await battery_controller.set_force_discharge(
            coordinator_data, reserve_soc=20.0
        )

        assert result is True
        # Check that set_value was called with 20
        # call_args_list: each call is (positional_args, kwargs)
        calls = mock_hass.services.async_call.call_args_list
        reserve_call = None
        for call in calls:
            if call[0][0] == "number" and call[0][1] == "set_value":
                reserve_call = call
                break
        assert reserve_call is not None
        assert reserve_call[0][2]["value"] == 20

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("mock_battery_sleep")
    async def test_set_force_discharge_uses_minimum_target_soc(
        self, coordinator_data, mock_hass, mock_get_entity_id
    ):
        """Issue #894: force discharge reads minimum_target_soc from config option."""
        controller = BatteryController(
            mock_hass,
            mock_get_entity_id,
            get_option_func=lambda key, default=None: 15.0,
        )
        mock_hass.services.async_call.return_value = None

        def mock_get_state(entity_id):
            if entity_id is None:
                return None
            state = MagicMock()
            if "operation_mode" in entity_id:
                state.state = "autonomous"
            elif "backup_reserve" in entity_id:
                state.state = "15"
            elif "allow_export" in entity_id:
                state.state = TESLEMETRY_EXPORT_BATTERY_OK
            return state

        mock_hass.states.get = mock_get_state

        result = await controller.set_force_discharge(coordinator_data)

        assert result is True
        # Should have used minimum_target_soc (15) for reserve
        calls = mock_hass.services.async_call.call_args_list
        reserve_call = None
        for call in calls:
            if call[0][0] == "number" and call[0][1] == "set_value":
                reserve_call = call
                break
        assert reserve_call is not None
        assert reserve_call[0][2]["value"] == 15

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("mock_battery_sleep")
    async def test_set_force_discharge_sets_battery_ok_export(
        self, battery_controller, coordinator_data, mock_hass
    ):
        """Test that force discharge sets export mode to battery_ok."""
        mock_hass.services.async_call.return_value = None

        def mock_get_state(entity_id):
            if entity_id is None:
                return None
            state = MagicMock()
            if "operation_mode" in entity_id:
                state.state = "autonomous"
            elif "backup_reserve" in entity_id:
                state.state = "10"
            elif "allow_export" in entity_id:
                state.state = TESLEMETRY_EXPORT_BATTERY_OK
            elif "minimum_target_soc" in entity_id:
                state.state = "10"
            return state

        mock_hass.states.get = mock_get_state

        await battery_controller.set_force_discharge(coordinator_data)

        # Check that select_option was called with battery_ok
        # call_args_list: each call is (positional_args, kwargs)
        # async_call("select", "select_option", {"entity_id": ..., "option": ...}, blocking=True)
        calls = mock_hass.services.async_call.call_args_list
        export_call = None
        for call in calls:
            if call[0][0] == "select" and call[0][1] == "select_option":
                if "allow_export" in call[0][2]["entity_id"]:
                    export_call = call
                    break
        assert export_call is not None
        assert export_call[0][2]["option"] == TESLEMETRY_EXPORT_BATTERY_OK


# =============================================================================
# SET_PROACTIVE_EXPORT TESTS
# =============================================================================


class TestSetProactiveExport:
    """Tests for set_proactive_export method."""

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("mock_battery_sleep")
    async def test_set_proactive_export_success(
        self, battery_controller, coordinator_data, mock_hass
    ):
        """Test successful transition to proactive export mode."""
        mock_hass.services.async_call.return_value = None
        coordinator_data.soc = 50.0

        def mock_get_state(entity_id):
            if entity_id is None:
                return None
            state = MagicMock()
            if "operation_mode" in entity_id:
                state.state = "autonomous"
            elif "backup_reserve" in entity_id:
                state.state = "45"  # SOC (50) - 5% buffer
            elif "allow_export" in entity_id:
                state.state = TESLEMETRY_EXPORT_BATTERY_OK
            elif "allow_charging_from_grid" in entity_id:
                state.state = "on"
            return state

        mock_hass.states.get = mock_get_state

        result = await battery_controller.set_proactive_export(coordinator_data)

        assert result is True
        assert coordinator_data.manual_override is False

    @pytest.mark.asyncio
    async def test_set_proactive_export_dry_run(
        self, battery_controller, coordinator_data, mock_hass
    ):
        """Test dry run mode for proactive export."""
        result = await battery_controller.set_proactive_export(
            coordinator_data, dry_run=True
        )

        assert result is True
        mock_hass.services.async_call.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("mock_battery_sleep")
    async def test_set_proactive_export_dynamic_reserve(
        self, battery_controller, coordinator_data, mock_hass
    ):
        """Test that proactive export calculates dynamic reserve (SOC - 5)."""
        mock_hass.services.async_call.return_value = None
        coordinator_data.soc = 60.0

        def mock_get_state(entity_id):
            if entity_id is None:
                return None
            state = MagicMock()
            if "operation_mode" in entity_id:
                state.state = "autonomous"
            elif "backup_reserve" in entity_id:
                state.state = "55"  # SOC - 5 = 55
            elif "allow_export" in entity_id:
                state.state = TESLEMETRY_EXPORT_BATTERY_OK
            return state

        mock_hass.states.get = mock_get_state

        await battery_controller.set_proactive_export(coordinator_data)

        # Check that set_value was called with SOC - 5
        calls = mock_hass.services.async_call.call_args_list
        reserve_call = None
        for call in calls:
            if call[0][0] == "number" and call[0][1] == "set_value":
                reserve_call = call
                break
        assert reserve_call is not None
        assert reserve_call[0][2]["value"] == 55  # 60 - 5

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("mock_battery_sleep")
    async def test_set_proactive_export_minimum_reserve(
        self, battery_controller, coordinator_data, mock_hass
    ):
        """Test that proactive export respects minimum reserve of 4%."""
        mock_hass.services.async_call.return_value = None
        coordinator_data.soc = 5.0  # Low SOC

        def mock_get_state(entity_id):
            if entity_id is None:
                return None
            state = MagicMock()
            if "operation_mode" in entity_id:
                state.state = "autonomous"
            elif "backup_reserve" in entity_id:
                state.state = "4"  # Minimum 4
            elif "allow_export" in entity_id:
                state.state = TESLEMETRY_EXPORT_BATTERY_OK
            return state

        mock_hass.states.get = mock_get_state

        await battery_controller.set_proactive_export(coordinator_data)

        # Check that set_value was called with minimum 4
        calls = mock_hass.services.async_call.call_args_list
        reserve_call = None
        for call in calls:
            if call[0][0] == "number" and call[0][1] == "set_value":
                reserve_call = call
                break
        assert reserve_call is not None
        assert reserve_call[0][2]["value"] == 4  # max(4, 5-5) = max(4, 0) = 4

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("mock_battery_sleep")
    async def test_set_proactive_export_floors_reserve_at_minimum_target_when_soc_zero(
        self, battery_controller, coordinator_data, mock_hass
    ):
        """Issue #895: SOC=0 floors reserve at minimum_target_soc, not 4%."""
        mock_hass.services.async_call.return_value = None
        coordinator_data.soc = 0.0  # Unavailable/empty SOC

        def mock_get_state(entity_id):
            if entity_id is None:
                return None
            state = MagicMock()
            if "operation_mode" in entity_id:
                state.state = "autonomous"
            elif "backup_reserve" in entity_id:
                state.state = str(DEFAULT_MINIMUM_TARGET_SOC)
            elif "allow_export" in entity_id:
                state.state = TESLEMETRY_EXPORT_BATTERY_OK
            return state

        mock_hass.states.get = mock_get_state

        await battery_controller.set_proactive_export(coordinator_data)

        calls = mock_hass.services.async_call.call_args_list
        reserve_call = None
        for call in calls:
            if call[0][0] == "number" and call[0][1] == "set_value":
                reserve_call = call
                break
        assert reserve_call is not None
        # SOC=0 must floor at minimum_target_soc (20), not the dynamic 4.0.
        assert reserve_call[0][2]["value"] == DEFAULT_MINIMUM_TARGET_SOC

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("mock_battery_sleep")
    async def test_set_proactive_export_sets_battery_ok_export(
        self, battery_controller, coordinator_data, mock_hass
    ):
        """Test that proactive export sets export mode to battery_ok."""
        mock_hass.services.async_call.return_value = None
        coordinator_data.soc = 50.0

        def mock_get_state(entity_id):
            if entity_id is None:
                return None
            state = MagicMock()
            if "operation_mode" in entity_id:
                state.state = "autonomous"
            elif "backup_reserve" in entity_id:
                state.state = "45"
            elif "allow_export" in entity_id:
                state.state = TESLEMETRY_EXPORT_BATTERY_OK
            return state

        mock_hass.states.get = mock_get_state

        await battery_controller.set_proactive_export(coordinator_data)

        # Check that select_option was called with battery_ok
        calls = mock_hass.services.async_call.call_args_list
        export_call = None
        for call in calls:
            if call[0][0] == "select" and call[0][1] == "select_option":
                if "allow_export" in call[0][2]["entity_id"]:
                    export_call = call
                    break
        assert export_call is not None
        assert export_call[0][2]["option"] == TESLEMETRY_EXPORT_BATTERY_OK


# =============================================================================
# VALIDATE_TRANSITION TESTS
# =============================================================================


class TestValidateTransition:
    """Tests for validate_transition method."""

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("mock_battery_sleep")
    async def test_validate_transition_success(self, battery_controller, mock_hass):
        """Test successful validation when state matches."""

        def mock_get_state(entity_id):
            state = MagicMock()
            if "operation_mode" in entity_id:
                state.state = "autonomous"
            elif "backup_reserve" in entity_id:
                state.state = "50"
            elif "allow_export" in entity_id:
                state.state = TESLEMETRY_EXPORT_BATTERY_OK
            return state

        mock_hass.states.get = mock_get_state

        result = await battery_controller.validate_transition(
            expected_operation_mode="autonomous",
            expected_backup_reserve=50,
            expected_export_mode=TESLEMETRY_EXPORT_BATTERY_OK,
            timeout=2,
        )

        assert result is True

    @pytest.mark.asyncio
    async def test_validate_transition_timeout(self, battery_controller, mock_hass):
        """Test validation fails after timeout.

        Note: With mock_battery_sleep, this test runs instantly but still
        verifies the timeout logic by checking that mismatched state returns False.
        The mock removes the real-time delay while preserving the loop behavior.
        """

        def mock_get_state(entity_id):
            state = MagicMock()
            if "operation_mode" in entity_id:
                state.state = "self_consumption"  # Wrong mode
            elif "backup_reserve" in entity_id:
                state.state = "10"
            return state

        mock_hass.states.get = mock_get_state

        # Use mock_battery_sleep to make this instant
        with patch(
            "custom_components.localshift.state.validator.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            result = await battery_controller.validate_transition(
                expected_operation_mode="autonomous",
                expected_backup_reserve=50,
                timeout=2,
            )

        assert result is False

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("mock_battery_sleep")
    async def test_validate_transition_reserve_tolerance(
        self, battery_controller, mock_hass
    ):
        """Test validation allows 1% tolerance for reserve."""

        def mock_get_state(entity_id):
            state = MagicMock()
            if "operation_mode" in entity_id:
                state.state = "autonomous"
            elif "backup_reserve" in entity_id:
                state.state = "51"  # Within 1% of 50
            return state

        mock_hass.states.get = mock_get_state

        result = await battery_controller.validate_transition(
            expected_operation_mode="autonomous",
            expected_backup_reserve=50,
            timeout=2,
        )

        assert result is True

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("mock_battery_sleep")
    async def test_validate_transition_succeeds_on_operation_mode_match(
        self, battery_controller, mock_hass
    ):
        """Test validation succeeds if operation mode matches even if reserve lags."""
        call_count = [0]

        def mock_get_state(entity_id):
            call_count[0] += 1
            state = MagicMock()
            if "operation_mode" in entity_id:
                state.state = "autonomous"  # Correct
            elif "backup_reserve" in entity_id:
                # Reserve lags behind
                state.state = "20"
            elif "allow_export" in entity_id:
                state.state = TESLEMETRY_EXPORT_PV_ONLY
            elif "allow_charging_from_grid" in entity_id:
                state.state = "on"
            return state

        mock_hass.states.get = mock_get_state

        result = await battery_controller.validate_transition(
            expected_operation_mode="autonomous",
            expected_backup_reserve=50,
            expected_export_mode=TESLEMETRY_EXPORT_BATTERY_OK,
            timeout=2,
        )

        # Should succeed because operation mode matches. With no
        # retry_reserve_write callback the settle-verify stays lenient and
        # accepts the lagging reserve (case a).
        assert result is True

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("mock_battery_sleep")
    async def test_validate_transition_without_export_mode(
        self, battery_controller, mock_hass
    ):
        """Test validation without checking export mode."""

        def mock_get_state(entity_id):
            state = MagicMock()
            if "operation_mode" in entity_id:
                state.state = "backup"
            elif "backup_reserve" in entity_id:
                state.state = "10"
            return state

        mock_hass.states.get = mock_get_state

        result = await battery_controller.validate_transition(
            expected_operation_mode="backup",
            expected_backup_reserve=10,
            expected_export_mode=None,
            timeout=2,
        )

        assert result is True


# =============================================================================
# RESERVE SETTLE-VERIFY TESTS (Tesla-override freeze fix, D1)
# =============================================================================


class TestReserveSettleVerify:
    """Tests for the post-acceptance reserve settle-verify + retry."""

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("mock_battery_sleep")
    async def test_stable_reserve_no_retry(self, battery_controller, mock_hass):
        """Reserve stable through the settle window -> True, retry not called."""

        def mock_get_state(entity_id):
            state = MagicMock()
            if "operation_mode" in entity_id:
                state.state = "self_consumption"
            elif "backup_reserve" in entity_id:
                state.state = "10"
            elif "allow_export" in entity_id:
                state.state = TESLEMETRY_EXPORT_PV_ONLY
            return state

        mock_hass.states.get = mock_get_state
        retry = AsyncMock(return_value=True)

        result = await battery_controller.validate_transition(
            expected_operation_mode="self_consumption",
            expected_backup_reserve=10,
            expected_export_mode=TESLEMETRY_EXPORT_PV_ONLY,
            timeout=2,
            retry_reserve_write=retry,
        )

        assert result is True
        retry.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("mock_battery_sleep")
    async def test_full_match_revert_retry_converges(
        self, battery_controller, mock_hass
    ):
        """In-loop match, reserve reverts at settle, retry converges -> True."""
        reserve_reads = {"n": 0}

        def mock_get_state(entity_id):
            state = MagicMock()
            if "operation_mode" in entity_id:
                state.state = "self_consumption"
            elif "backup_reserve" in entity_id:
                reserve_reads["n"] += 1
                # read 1: loop match (10); read 2: settle sees Tesla revert (80);
                # read 3+: retry converged (10)
                state.state = "80" if reserve_reads["n"] == 2 else "10"
            elif "allow_export" in entity_id:
                state.state = TESLEMETRY_EXPORT_PV_ONLY
            return state

        mock_hass.states.get = mock_get_state
        retry = AsyncMock(return_value=True)

        result = await battery_controller.validate_transition(
            expected_operation_mode="self_consumption",
            expected_backup_reserve=10,
            expected_export_mode=TESLEMETRY_EXPORT_PV_ONLY,
            timeout=2,
            retry_reserve_write=retry,
        )

        assert result is True
        retry.assert_awaited_once_with(10)

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("mock_battery_sleep")
    async def test_fallback_accept_revert_retry_converges(
        self, battery_controller, mock_hass
    ):
        """Fallback (op + grid_charging) acceptance also settle-verifies reserve."""
        reserve = {"val": "80"}

        async def _retry(_value):
            reserve["val"] = "10"
            return True

        def mock_get_state(entity_id):
            state = MagicMock()
            if "operation_mode" in entity_id:
                state.state = "self_consumption"
            elif "backup_reserve" in entity_id:
                state.state = reserve["val"]
            elif "allow_export" in entity_id:
                state.state = TESLEMETRY_EXPORT_PV_ONLY
            elif "allow_charging_from_grid" in entity_id:
                state.state = "off"
            return state

        mock_hass.states.get = mock_get_state
        retry = AsyncMock(side_effect=_retry)

        # Reserve never matches in the loop (80 vs 10) -> op+grid fallback accept.
        result = await battery_controller.validate_transition(
            expected_operation_mode="self_consumption",
            expected_backup_reserve=10,
            expected_export_mode=TESLEMETRY_EXPORT_PV_ONLY,
            expected_grid_charging_allowed=False,
            timeout=2,
            retry_reserve_write=retry,
        )

        assert result is True
        retry.assert_awaited_once_with(10)

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("mock_battery_sleep")
    async def test_retry_write_fails_returns_false(self, battery_controller, mock_hass):
        """A failed retry write fails the transition."""
        reserve_reads = {"n": 0}

        def mock_get_state(entity_id):
            state = MagicMock()
            if "operation_mode" in entity_id:
                state.state = "self_consumption"
            elif "backup_reserve" in entity_id:
                reserve_reads["n"] += 1
                state.state = "80" if reserve_reads["n"] == 2 else "10"
            elif "allow_export" in entity_id:
                state.state = TESLEMETRY_EXPORT_PV_ONLY
            return state

        mock_hass.states.get = mock_get_state
        retry = AsyncMock(return_value=False)

        result = await battery_controller.validate_transition(
            expected_operation_mode="self_consumption",
            expected_backup_reserve=10,
            expected_export_mode=TESLEMETRY_EXPORT_PV_ONLY,
            timeout=2,
            retry_reserve_write=retry,
        )

        assert result is False
        retry.assert_awaited_once()

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("mock_battery_sleep")
    async def test_revert_after_validation_race_end_to_end(
        self, battery_controller, mock_hass
    ):
        """The incident: set_self_consumption succeeds despite a reserve revert,
        re-issuing the reserve write (number.set_value called twice)."""
        mock_hass.services.async_call.return_value = None
        reserve_reads = {"n": 0}

        def mock_get_state(entity_id):
            if entity_id is None:
                return None
            state = MagicMock()
            if "operation_mode" in entity_id:
                state.state = "self_consumption"
            elif "backup_reserve" in entity_id:
                reserve_reads["n"] += 1
                # loop match (10) -> settle sees revert (80) -> retry converges (10)
                state.state = "80" if reserve_reads["n"] == 2 else "10"
            elif "allow_export" in entity_id:
                state.state = TESLEMETRY_EXPORT_PV_ONLY
            elif "allow_charging_from_grid" in entity_id:
                state.state = "off"
            return state

        mock_hass.states.get = mock_get_state

        from custom_components.localshift.coordinator import CoordinatorData

        data = CoordinatorData()
        data.soc = 50.0
        data.preserve_soc = None  # -> reserve 10

        result = await battery_controller.set_self_consumption(data)

        assert result is True
        reserve_writes = [
            call
            for call in mock_hass.services.async_call.call_args_list
            if call.args[:2] == ("number", "set_value")
        ]
        # Once in the transition recipe, once in the settle-verify retry.
        assert len(reserve_writes) == 2


# =============================================================================
# VERIFY_CURRENT_STATE TESTS
# =============================================================================


class TestVerifyCurrentState:
    """Tests for verify_current_state method."""

    @pytest.mark.asyncio
    async def test_verify_current_state_success(self, battery_controller, mock_hass):
        """Test successful state verification."""

        def mock_get_state(entity_id):
            state = MagicMock()
            if "operation_mode" in entity_id:
                state.state = "autonomous"
            elif "backup_reserve" in entity_id:
                state.state = "50"
            elif "allow_export" in entity_id:
                state.state = TESLEMETRY_EXPORT_BATTERY_OK
            return state

        mock_hass.states.get = mock_get_state

        result = await battery_controller.verify_current_state(
            expected_operation_mode="autonomous",
            expected_backup_reserve=50,
            expected_export_mode=TESLEMETRY_EXPORT_BATTERY_OK,
        )

        assert result is True

    @pytest.mark.asyncio
    async def test_verify_current_state_mismatch(self, battery_controller, mock_hass):
        """Test state verification detects mismatch."""

        def mock_get_state(entity_id):
            state = MagicMock()
            if "operation_mode" in entity_id:
                state.state = "self_consumption"  # Wrong
            elif "backup_reserve" in entity_id:
                state.state = "10"
            return state

        mock_hass.states.get = mock_get_state

        result = await battery_controller.verify_current_state(
            expected_operation_mode="autonomous",
            expected_backup_reserve=50,
        )

        assert result is False

    @pytest.mark.asyncio
    async def test_verify_current_state_unavailable_entity(
        self, battery_controller, mock_hass
    ):
        """Test state verification handles unavailable entities."""

        def mock_get_state(entity_id):
            state = MagicMock()
            state.state = "unavailable"
            return state

        mock_hass.states.get = mock_get_state

        result = await battery_controller.verify_current_state(
            expected_operation_mode="autonomous",
            expected_backup_reserve=50,
        )

        assert result is False


# =============================================================================
# HELPER METHOD TESTS
# =============================================================================


class TestHelperMethods:
    """Tests for helper methods."""

    def test_read_float_success(self, battery_controller, mock_hass):
        """Test _read_float with valid state."""
        state = MagicMock()
        state.state = "42.5"
        mock_hass.states.get = MagicMock(return_value=state)

        result = battery_controller._read_float("sensor.test")

        assert result == 42.5

    def test_read_float_unavailable(self, battery_controller, mock_hass):
        """Test _read_float with unavailable state."""
        mock_hass.states.get = MagicMock(return_value=None)

        result = battery_controller._read_float("sensor.test", default=10.0)

        assert result == 10.0

    def test_read_float_invalid(self, battery_controller, mock_hass):
        """Test _read_float with invalid value."""
        state = MagicMock()
        state.state = "invalid"
        mock_hass.states.get = MagicMock(return_value=state)

        result = battery_controller._read_float("sensor.test", default=5.0)

        assert result == 5.0

    def test_read_str_success(self, battery_controller, mock_hass):
        """Test _read_str with valid state."""
        state = MagicMock()
        state.state = "autonomous"
        mock_hass.states.get = MagicMock(return_value=state)

        result = battery_controller._read_str("sensor.test")

        assert result == "autonomous"

    def test_read_str_unavailable(self, battery_controller, mock_hass):
        """Test _read_str with unavailable state."""
        mock_hass.states.get = MagicMock(return_value=None)

        result = battery_controller._read_str("sensor.test", default="default")

        assert result == "default"

    def test_get_minimum_target_soc(self, battery_controller, mock_hass):
        """Issue #894: _get_minimum_target_soc reads the config option value."""
        result = battery_controller._get_minimum_target_soc()

        assert result == float(DEFAULT_MINIMUM_TARGET_SOC)

    def test_get_minimum_target_soc_uses_configured_option(
        self, mock_hass, mock_get_entity_id
    ):
        """Issue #894: _get_minimum_target_soc returns the configured option."""
        controller = BatteryController(
            mock_hass,
            mock_get_entity_id,
            get_option_func=lambda key, default=None: 25.0,
        )

        assert controller._get_minimum_target_soc() == 25.0

    def test_get_minimum_target_soc_default(self, mock_hass, mock_get_entity_id):
        """Issue #894: default is DEFAULT_MINIMUM_TARGET_SOC (20) without option func."""
        controller = BatteryController(mock_hass, mock_get_entity_id)

        result = controller._get_minimum_target_soc()

        assert result == float(DEFAULT_MINIMUM_TARGET_SOC)

    def test_get_minimum_target_soc_option_func_raises_falls_back(
        self, mock_hass, mock_get_entity_id
    ):
        """Issue #894: a raising option func falls back to DEFAULT_MINIMUM_TARGET_SOC."""

        def raising_option(key, default=None):
            raise ValueError("uncoercible option")

        controller = BatteryController(
            mock_hass,
            mock_get_entity_id,
            get_option_func=raising_option,
        )

        assert controller._get_minimum_target_soc() == float(DEFAULT_MINIMUM_TARGET_SOC)

    def test_read_fresh_soc_success(self, battery_controller, mock_hass):
        """Issue #559 Phase 4: test read_fresh_soc() returns float on valid state."""
        state = MagicMock()
        state.state = "67.5"
        mock_hass.states.get = MagicMock(return_value=state)

        result = battery_controller.read_fresh_soc()

        assert result == 67.5
        # Verify it used the correct entity ID
        mock_hass.states.get.assert_called_once_with("sensor.tesla_powerwall_soc")

    def test_read_fresh_soc_unavailable(self, battery_controller, mock_hass):
        """Issue #559 Phase 4: test read_fresh_soc() returns None when unavailable."""
        state = MagicMock()
        state.state = "unavailable"
        mock_hass.states.get = MagicMock(return_value=state)

        result = battery_controller.read_fresh_soc()

        assert result is None

    def test_read_fresh_soc_unknown(self, battery_controller, mock_hass):
        """Issue #559 Phase 4: test read_fresh_soc() returns None when unknown."""
        state = MagicMock()
        state.state = "unknown"
        mock_hass.states.get = MagicMock(return_value=state)

        result = battery_controller.read_fresh_soc()

        assert result is None

    def test_read_fresh_soc_no_state(self, battery_controller, mock_hass):
        """Issue #559 Phase 4: test read_fresh_soc() returns None when state object is None."""
        mock_hass.states.get = MagicMock(return_value=None)

        result = battery_controller.read_fresh_soc()

        assert result is None

    def test_read_fresh_soc_invalid_float(self, battery_controller, mock_hass):
        """Issue #559 Phase 4: test read_fresh_soc() returns None on invalid float."""
        state = MagicMock()
        state.state = "not_a_number"
        mock_hass.states.get = MagicMock(return_value=state)

        result = battery_controller.read_fresh_soc()

        assert result is None
