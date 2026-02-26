"""Unit tests for ThermalManager.

Tests cover:
- Daily mode determination (HEAT > COOL > DRY priority)
- HVAC power learning from state changes
- Baseline load estimation
- Pre-conditioning evaluation
- Solar tapering evaluation
"""

from datetime import datetime, time
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.localshift.const import ThermalMode
from custom_components.localshift.coordinator_data import CoordinatorData
from custom_components.localshift.thermal_manager import (
    ClimateEntityState,
    LearnedHVACPower,
    ThermalManager,
)


@pytest.fixture
def mock_hass():
    """Create a mock Home Assistant instance."""
    hass = MagicMock()
    hass.services = MagicMock()
    hass.services.async_call = AsyncMock()
    # async_create_task receives coroutines - use a mock that consumes them
    # to avoid "coroutine was never awaited" warnings
    hass.async_create_task = MagicMock(side_effect=lambda coro, name=None: None)
    hass.loop = MagicMock()
    hass.data = {}
    return hass


@pytest.fixture
def mock_entry():
    """Create a mock config entry."""
    entry = MagicMock()
    entry.data = {}
    entry.options = {}
    return entry


@pytest.fixture
def mock_get_entity_id():
    """Create a mock get_entity_id function."""

    def get_entity_id(key: str) -> str:
        return f"sensor.{key}"

    return get_entity_id


@pytest.fixture
def mock_get_switch_state():
    """Create a mock get_switch_state function."""
    defaults = {
        "thermal_management_enabled": False,
        "solar_taper_enabled": True,
    }

    def get_switch_state(key: str) -> bool:
        return defaults.get(key, False)

    return get_switch_state


@pytest.fixture
def mock_get_option():
    """Create a mock get_option function."""
    defaults = {
        "cooling_trigger_temp": 28.0,
        "heating_trigger_temp": 15.0,
        "dehumidify_trigger_humidity": 70.0,
        "precondition_hours_before_dw": 1.0,
        "precondition_temp_offset": 2.0,
        "taper_max_setpoint_offset": 3.0,
    }

    def get_option(key: str, default=None):
        return defaults.get(key, default)

    return get_option


@pytest.fixture
def thermal_manager(
    mock_hass,
    mock_entry,
    mock_get_entity_id,
    mock_get_switch_state,
    mock_get_option,
):
    """Create a ThermalManager instance."""
    return ThermalManager(
        hass=mock_hass,
        entry=mock_entry,
        get_entity_id_func=mock_get_entity_id,
        get_switch_state_func=mock_get_switch_state,
        get_option_func=mock_get_option,
    )


@pytest.fixture
def coordinator_data():
    """Create CoordinatorData for testing."""
    data = CoordinatorData()
    data.daily_thermal_mode = ThermalMode.OFF
    data.climate_states = {}
    data.climate_control_entities = []
    data.current_excess_rate_kw = 0.0
    data.load_shift_signal = "HOLD"
    return data


# =============================================================================
# DAILY MODE DETERMINATION TESTS
# =============================================================================


class TestDetermineDailyMode:
    """Tests for daily mode determination."""

    def test_empty_forecast_returns_off(self, thermal_manager):
        """Empty forecast should return OFF mode."""
        result = thermal_manager.determine_daily_mode({})
        assert result == ThermalMode.OFF

    def test_heat_priority_over_cool(self, thermal_manager):
        """HEAT mode takes priority over COOL even on hot day.

        If min temp < heating trigger, HEAT mode regardless of max temp.
        """
        # Cold morning, hot afternoon
        forecast = {
            6: 12.0,  # Below heating trigger (15°C)
            12: 32.0,  # Above cooling trigger (28°C)
            18: 25.0,
        }
        result = thermal_manager.determine_daily_mode(forecast)
        assert result == ThermalMode.HEAT

    def test_cool_mode_when_hot_no_heating_needed(self, thermal_manager):
        """COOL mode when max > trigger and min > heating trigger."""
        # Warm day, no heating needed
        forecast = {
            6: 20.0,  # Above heating trigger
            12: 32.0,  # Above cooling trigger
            18: 28.0,
        }
        result = thermal_manager.determine_daily_mode(forecast)
        assert result == ThermalMode.COOL

    def test_dry_mode_when_humid_no_temp_triggers(self, thermal_manager):
        """DRY mode when humidity > trigger and no temp triggers met."""
        # Mild humid day
        forecast = {
            6: 18.0,  # Above heating trigger
            12: 26.0,  # Below cooling trigger
            18: 22.0,
        }
        result = thermal_manager.determine_daily_mode(forecast, humidity=75.0)
        assert result == ThermalMode.DRY

    def test_off_mode_mild_day(self, thermal_manager):
        """OFF mode on mild days with no triggers met."""
        # Mild day
        forecast = {
            6: 18.0,  # Above heating trigger
            12: 24.0,  # Below cooling trigger
            18: 20.0,
        }
        result = thermal_manager.determine_daily_mode(forecast, humidity=50.0)
        assert result == ThermalMode.OFF

    def test_heat_mode_cold_day(self, thermal_manager):
        """HEAT mode on cold day."""
        forecast = {
            6: 8.0,  # Below heating trigger
            12: 12.0,  # Below cooling trigger
            18: 10.0,
        }
        result = thermal_manager.determine_daily_mode(forecast)
        assert result == ThermalMode.HEAT

    def test_mode_uses_custom_thresholds(self, thermal_manager, mock_get_option):
        """Mode determination respects custom thresholds."""

        # Set custom thresholds
        def get_custom_option(key: str, default=None):
            if key == "cooling_trigger_temp":
                return 25.0  # Lower threshold
            if key == "heating_trigger_temp":
                return 18.0  # Higher threshold
            return default

        thermal_manager._get_option = get_custom_option

        # 26°C would be OFF with default (28°C trigger), but COOL with 25°C trigger
        forecast = {12: 26.0}
        result = thermal_manager.determine_daily_mode(forecast)
        assert result == ThermalMode.COOL


# =============================================================================
# HVAC POWER LEARNING TESTS
# =============================================================================


class TestHVACPowerLearning:
    """Tests for HVAC power learning."""

    def test_no_learning_when_disabled(self, thermal_manager, coordinator_data):
        """No learning happens when thermal management is disabled."""
        # thermal_management_enabled is False by default
        thermal_manager.learn_hvac_power(
            data=coordinator_data,
            current_load_kw=3.5,
            timestamp=datetime.now(),
        )

        # No learned power should be recorded
        assert len(thermal_manager._learned_power) == 0

    def test_learning_cooling_power(self, thermal_manager, coordinator_data):
        """Learning cooling power from state change."""
        # Enable thermal management
        thermal_manager._get_switch_state = lambda k: k == "thermal_management_enabled"

        # Set up previous state (AC was off)
        thermal_manager._prev_climate_states = {
            "climate.living_room": ClimateEntityState(
                entity_id="climate.living_room",
                state="off",
                hvac_action="off",
                setpoint=22.0,
                current_temperature=26.0,
                is_controlled=True,
            )
        }
        thermal_manager._prev_load_kw = 0.8

        # Current state (AC now cooling)
        coordinator_data.climate_states = {
            "climate.living_room": {
                "state": "cool",
                "hvac_action": "cooling",
                "setpoint": 22.0,
                "current_temperature": 26.0,
                "is_controlled": True,
            }
        }

        # Learn from state change (load increased from 0.8 to 3.5 kW)
        thermal_manager.learn_hvac_power(
            data=coordinator_data,
            current_load_kw=3.5,
            timestamp=datetime.now(),
        )

        # Should have learned cooling power
        assert "climate.living_room" in thermal_manager._learned_power
        learned = thermal_manager._learned_power["climate.living_room"]
        assert learned.cooling_power_kw > 0
        assert learned.sample_count == 1
        assert learned.confidence == "low"

    def test_learning_ignores_small_load_delta(self, thermal_manager, coordinator_data):
        """Small load delta (< 0.1 kW) is ignored."""
        thermal_manager._get_switch_state = lambda k: k == "thermal_management_enabled"

        thermal_manager._prev_climate_states = {
            "climate.living_room": ClimateEntityState(
                entity_id="climate.living_room",
                state="off",
                hvac_action="off",
                setpoint=22.0,
                current_temperature=26.0,
                is_controlled=True,
            )
        }
        thermal_manager._prev_load_kw = 0.8

        coordinator_data.climate_states = {
            "climate.living_room": {
                "state": "cool",
                "hvac_action": "cooling",
                "setpoint": 22.0,
                "current_temperature": 26.0,
                "is_controlled": True,
            }
        }

        # Tiny load delta (0.05 kW)
        thermal_manager.learn_hvac_power(
            data=coordinator_data,
            current_load_kw=0.85,
            timestamp=datetime.now(),
        )

        # Should not have learned
        assert len(thermal_manager._learned_power) == 0

    def test_learning_ignores_large_load_delta(self, thermal_manager, coordinator_data):
        """Large load delta (> 10 kW) is ignored."""
        thermal_manager._get_switch_state = lambda k: k == "thermal_management_enabled"

        thermal_manager._prev_climate_states = {
            "climate.living_room": ClimateEntityState(
                entity_id="climate.living_room",
                state="off",
                hvac_action="off",
                setpoint=22.0,
                current_temperature=26.0,
                is_controlled=True,
            )
        }
        thermal_manager._prev_load_kw = 0.8

        coordinator_data.climate_states = {
            "climate.living_room": {
                "state": "cool",
                "hvac_action": "cooling",
                "setpoint": 22.0,
                "current_temperature": 26.0,
                "is_controlled": True,
            }
        }

        # Huge load delta (12 kW)
        thermal_manager.learn_hvac_power(
            data=coordinator_data,
            current_load_kw=12.8,
            timestamp=datetime.now(),
        )

        # Should not have learned
        assert len(thermal_manager._learned_power) == 0

    def test_confidence_increases_with_samples(self, thermal_manager, coordinator_data):
        """Confidence level increases with more samples."""
        thermal_manager._get_switch_state = lambda k: k == "thermal_management_enabled"
        thermal_manager._learned_power["climate.living_room"] = LearnedHVACPower(
            entity_id="climate.living_room",
            cooling_power_kw=2.5,
            sample_count=4,
            confidence="low",
        )

        # Set up state change
        thermal_manager._prev_climate_states = {
            "climate.living_room": ClimateEntityState(
                entity_id="climate.living_room",
                state="cool",
                hvac_action="idle",
                setpoint=22.0,
                current_temperature=24.0,
                is_controlled=True,
            )
        }
        thermal_manager._prev_load_kw = 0.8

        coordinator_data.climate_states = {
            "climate.living_room": {
                "state": "cool",
                "hvac_action": "cooling",
                "setpoint": 22.0,
                "current_temperature": 26.0,
                "is_controlled": True,
            }
        }

        # Learn (this will be sample 5)
        thermal_manager.learn_hvac_power(
            data=coordinator_data,
            current_load_kw=3.3,
            timestamp=datetime.now(),
        )

        # Should now have medium confidence
        assert (
            thermal_manager._learned_power["climate.living_room"].confidence == "medium"
        )


# =============================================================================
# BASELINE ESTIMATION TESTS
# =============================================================================


class TestBaselineEstimation:
    """Tests for baseline load estimation."""

    def test_empty_historical_returns_empty(self, thermal_manager):
        """Empty historical data returns empty baseline."""
        result = thermal_manager.estimate_baseline_from_historical({})
        assert result == {}

    def test_subtracts_learned_hvac_power(self, thermal_manager):
        """Baseline subtracts learned HVAC power from historical."""
        thermal_manager._learned_power["climate.living_room"] = LearnedHVACPower(
            entity_id="climate.living_room",
            cooling_power_kw=2.5,
            sample_count=10,
            confidence="medium",
        )

        historical = {
            0: 1.0,  # Should become ~0 (1.0 - 2.5*0.4 = 0)
            6: 1.5,  # Should become 0.5
            12: 2.0,  # Should become 1.0
            18: 1.2,  # Should become 0.2
        }

        result = thermal_manager.estimate_baseline_from_historical(
            historical_avg_kw=historical,
            daily_mode=ThermalMode.COOL,
        )

        # All values should be reduced
        for hour in historical:
            assert result[hour] < historical[hour]
            assert result[hour] >= 0.0

    def test_uses_default_estimate_when_no_learned_power(self, thermal_manager):
        """Uses default 2.5 kW estimate when no learned power available."""
        historical = {
            12: 2.0,  # Should become 0 (2.0 - 2.5*0.4 = 1.0)
        }

        result = thermal_manager.estimate_baseline_from_historical(
            historical_avg_kw=historical,
            daily_mode=ThermalMode.COOL,
        )

        # Should still produce a result
        assert 12 in result
        assert result[12] >= 0.0


# =============================================================================
# PRE-CONDITIONING TESTS
# =============================================================================


class TestPreconditioning:
    """Tests for pre-conditioning evaluation."""

    def test_disabled_returns_false(self, thermal_manager, coordinator_data):
        """Pre-conditioning returns False when thermal management disabled."""
        is_active, offset = thermal_manager.evaluate_preconditioning(
            data=coordinator_data,
            now=datetime(2026, 2, 16, 14, 0, 0),
            demand_window_start=time(15, 0),
            demand_window_end=time(21, 0),
        )
        assert is_active is False
        assert offset == 0.0

    def test_not_active_outside_window(self, thermal_manager, coordinator_data):
        """Pre-conditioning not active outside the pre-conditioning window."""
        thermal_manager._get_switch_state = lambda k: k == "thermal_management_enabled"
        coordinator_data.daily_thermal_mode = ThermalMode.COOL

        # 10am, DW starts at 3pm, 1 hour before = 2pm start
        # 10am is outside the window
        is_active, offset = thermal_manager.evaluate_preconditioning(
            data=coordinator_data,
            now=datetime(2026, 2, 16, 10, 0, 0),
            demand_window_start=time(15, 0),
            demand_window_end=time(21, 0),
        )
        assert is_active is False

    def test_active_in_window_cool_mode(self, thermal_manager, coordinator_data):
        """Pre-conditioning active in window with COOL mode."""
        thermal_manager._get_switch_state = lambda k: k == "thermal_management_enabled"
        coordinator_data.daily_thermal_mode = ThermalMode.COOL

        # 2:30pm, DW starts at 3pm, 1 hour before = 2pm start
        # 2:30pm is in the window
        is_active, offset = thermal_manager.evaluate_preconditioning(
            data=coordinator_data,
            now=datetime(2026, 2, 16, 14, 30, 0),
            demand_window_start=time(15, 0),
            demand_window_end=time(21, 0),
        )
        assert is_active is True
        # COOL mode should lower setpoint (negative offset)
        assert offset < 0

    def test_active_in_window_heat_mode(self, thermal_manager, coordinator_data):
        """Pre-conditioning active in window with HEAT mode."""
        thermal_manager._get_switch_state = lambda k: k == "thermal_management_enabled"
        coordinator_data.daily_thermal_mode = ThermalMode.HEAT

        # 2:30pm, DW starts at 3pm
        is_active, offset = thermal_manager.evaluate_preconditioning(
            data=coordinator_data,
            now=datetime(2026, 2, 16, 14, 30, 0),
            demand_window_start=time(15, 0),
            demand_window_end=time(21, 0),
        )
        assert is_active is True
        # HEAT mode should raise setpoint (positive offset)
        assert offset > 0

    def test_not_active_for_off_mode(self, thermal_manager, coordinator_data):
        """Pre-conditioning not active when mode is OFF."""
        thermal_manager._get_switch_state = lambda k: k == "thermal_management_enabled"
        coordinator_data.daily_thermal_mode = ThermalMode.OFF

        is_active, offset = thermal_manager.evaluate_preconditioning(
            data=coordinator_data,
            now=datetime(2026, 2, 16, 14, 30, 0),
            demand_window_start=time(15, 0),
            demand_window_end=time(21, 0),
        )
        assert is_active is False


# =============================================================================
# SOLAR TAPERING TESTS
# =============================================================================


class TestSolarTaper:
    """Tests for solar tapering evaluation."""

    def test_disabled_returns_false(self, thermal_manager, coordinator_data):
        """Solar taper returns False when thermal management disabled."""
        is_active, offset = thermal_manager.evaluate_solar_taper(
            data=coordinator_data,
            excess_solar_kw=2.0,
            load_shift_signal="INCREASE_LOAD",
        )
        assert is_active is False
        assert offset == 0.0

    def test_no_taper_without_increase_signal(self, thermal_manager, coordinator_data):
        """Solar taper not active without INCREASE_LOAD signal."""
        thermal_manager._get_switch_state = lambda k: k == "thermal_management_enabled"
        coordinator_data.daily_thermal_mode = ThermalMode.COOL

        is_active, offset = thermal_manager.evaluate_solar_taper(
            data=coordinator_data,
            excess_solar_kw=2.0,
            load_shift_signal="HOLD",
        )
        assert is_active is False

    def test_no_taper_with_low_excess(self, thermal_manager, coordinator_data):
        """Solar taper not active with low excess solar (< 0.5 kW)."""
        thermal_manager._get_switch_state = lambda k: k == "thermal_management_enabled"
        coordinator_data.daily_thermal_mode = ThermalMode.COOL

        is_active, offset = thermal_manager.evaluate_solar_taper(
            data=coordinator_data,
            excess_solar_kw=0.3,
            load_shift_signal="INCREASE_LOAD",
        )
        assert is_active is False

    def test_taper_active_with_excess_and_signal(
        self, thermal_manager, coordinator_data
    ):
        """Solar taper active with sufficient excess and INCREASE_LOAD signal."""
        thermal_manager._get_switch_state = lambda k: k in (
            "thermal_management_enabled",
            "solar_taper_enabled",
        )
        coordinator_data.daily_thermal_mode = ThermalMode.COOL

        is_active, offset = thermal_manager.evaluate_solar_taper(
            data=coordinator_data,
            excess_solar_kw=2.0,
            load_shift_signal="INCREASE_LOAD",
        )
        assert is_active is True
        # COOL mode with excess should lower setpoint (more cooling)
        assert offset < 0

    def test_taper_scales_with_excess(self, thermal_manager, coordinator_data):
        """Solar taper offset scales with excess solar amount."""
        thermal_manager._get_switch_state = lambda k: k in (
            "thermal_management_enabled",
            "solar_taper_enabled",
        )
        coordinator_data.daily_thermal_mode = ThermalMode.COOL

        # 1 kW excess = half max offset
        _, offset_1kw = thermal_manager.evaluate_solar_taper(
            data=coordinator_data,
            excess_solar_kw=1.0,
            load_shift_signal="INCREASE_LOAD",
        )

        # 2 kW excess = full max offset
        _, offset_2kw = thermal_manager.evaluate_solar_taper(
            data=coordinator_data,
            excess_solar_kw=2.0,
            load_shift_signal="INCREASE_LOAD",
        )

        # 4 kW excess should be clamped to same as 2 kW
        _, offset_4kw = thermal_manager.evaluate_solar_taper(
            data=coordinator_data,
            excess_solar_kw=4.0,
            load_shift_signal="INCREASE_LOAD",
        )

        # 2kW offset should be roughly double 1kW offset
        assert abs(offset_2kw) > abs(offset_1kw)
        # 4kW should be same as 2kW (clamped)
        assert offset_2kw == offset_4kw

    def test_taper_heat_mode_positive_offset(self, thermal_manager, coordinator_data):
        """Solar taper in HEAT mode uses positive offset (raise setpoint)."""
        thermal_manager._get_switch_state = lambda k: k in (
            "thermal_management_enabled",
            "solar_taper_enabled",
        )
        coordinator_data.daily_thermal_mode = ThermalMode.HEAT

        is_active, offset = thermal_manager.evaluate_solar_taper(
            data=coordinator_data,
            excess_solar_kw=2.0,
            load_shift_signal="INCREASE_LOAD",
        )
        assert is_active is True
        # HEAT mode with excess should raise setpoint (more heating)
        assert offset > 0


# =============================================================================
# CLIMATE CONTROL TESTS
# =============================================================================


class TestClimateControl:
    """Tests for climate control application."""

    @pytest.mark.asyncio
    async def test_no_control_when_disabled(self, thermal_manager, coordinator_data):
        """No climate control when thermal management disabled."""
        await thermal_manager.async_apply_climate_control(
            data=coordinator_data,
            setpoint_offset=-2.0,
        )
        # Should not raise, and should not call any service

    @pytest.mark.asyncio
    async def test_no_control_with_zero_offset(self, thermal_manager, coordinator_data):
        """No climate control when offset is zero."""
        thermal_manager._get_switch_state = lambda k: k == "thermal_management_enabled"
        coordinator_data.climate_control_entities = ["climate.living_room"]
        coordinator_data.climate_states = {
            "climate.living_room": {
                "setpoint": 22.0,
            }
        }

        await thermal_manager.async_apply_climate_control(
            data=coordinator_data,
            setpoint_offset=0.0,
        )
        # Should not call service

    @pytest.mark.asyncio
    async def test_tracks_original_setpoint(self, thermal_manager, coordinator_data):
        """Climate control tracks original setpoint to prevent ratchet."""
        thermal_manager._get_switch_state = lambda k: k == "thermal_management_enabled"
        coordinator_data.daily_thermal_mode = ThermalMode.COOL
        coordinator_data.climate_control_entities = ["climate.living_room"]
        coordinator_data.climate_states = {
            "climate.living_room": {
                "setpoint": 24.0,
            }
        }

        # Apply first offset
        await thermal_manager.async_apply_climate_control(
            data=coordinator_data,
            setpoint_offset=-2.0,
        )

        # Original setpoint should be captured
        assert "climate.living_room" in thermal_manager._original_setpoints
        assert thermal_manager._original_setpoints["climate.living_room"] == 24.0

        # Apply another offset - should use ORIGINAL, not current
        # (simulating the case where the thermostat might have updated)
        await thermal_manager.async_apply_climate_control(
            data=coordinator_data,
            setpoint_offset=-1.0,
        )

        # Should still be tracking original
        assert thermal_manager._original_setpoints["climate.living_room"] == 24.0

    @pytest.mark.asyncio
    async def test_clears_tracking_when_offset_zero(
        self, thermal_manager, coordinator_data
    ):
        """Tracking cleared when offset returns to zero."""
        thermal_manager._get_switch_state = lambda k: k == "thermal_management_enabled"
        thermal_manager._original_setpoints = {"climate.living_room": 24.0}
        thermal_manager._current_active_offset = -2.0
        coordinator_data.daily_thermal_mode = ThermalMode.COOL
        coordinator_data.climate_control_entities = ["climate.living_room"]

        await thermal_manager.async_apply_climate_control(
            data=coordinator_data,
            setpoint_offset=0.0,
        )

        # Tracking should be cleared
        assert len(thermal_manager._original_setpoints) == 0
        assert thermal_manager._current_active_offset == 0.0


# =============================================================================
# CONFIGURATION ACCESSOR TESTS
# =============================================================================


class TestConfigurationAccessors:
    """Tests for configuration accessor methods."""

    def test_is_enabled_from_switch(self, thermal_manager):
        """is_enabled returns True when switch is True."""
        thermal_manager._get_switch_state = lambda k: k == "thermal_management_enabled"
        assert thermal_manager.is_enabled() is True

    def test_is_enabled_switch_is_authoritative(self, thermal_manager):
        """is_enabled only checks switch state, not config options.
        
        This verifies the fix for the bug where config options could
        override the switch state, causing thermal management to run
        even when disabled via the UI switch.
        """
        # Switch is OFF
        thermal_manager._get_switch_state = lambda k: False
        # Config option is ON (should be ignored)
        thermal_manager._get_option = lambda k, d: (
            True if k == "thermal_management_enabled" else d
        )
        # Should return False because switch is the authoritative source
        assert thermal_manager.is_enabled() is False

    def test_is_solar_taper_enabled(self, thermal_manager):
        """is_solar_taper_enabled returns correct value."""
        thermal_manager._get_switch_state = lambda k: k == "solar_taper_enabled"
        assert thermal_manager.is_solar_taper_enabled() is True

    def test_get_thresholds(self, thermal_manager):
        """Threshold getters return configured values."""
        assert thermal_manager.get_cooling_trigger_temp() == 28.0
        assert thermal_manager.get_heating_trigger_temp() == 15.0
        assert thermal_manager.get_dehumidify_trigger_humidity() == 70.0
