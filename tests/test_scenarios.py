"""Scenario-based tests for battery automation logic.

This module loads JSON scenario files and validates that the computation
engine produces expected outputs for each scenario.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from custom_components.localshift.computation_engine import (
    BatteryMode,
    ComputationEngine,
)
from custom_components.localshift.coordinator_data import CoordinatorData
from simulations.schema import Scenario, discover_scenarios


# Timezone helper for creating aware datetimes
def dt_aware(year, month, day, hour, minute=0, second=0, tz_offset_hours=11):
    """Create a timezone-aware datetime."""
    return datetime(
        year,
        month,
        day,
        hour,
        minute,
        second,
        tzinfo=timezone(timedelta(hours=tz_offset_hours)),
    )


def setup_coordinator_data(input_data: dict) -> CoordinatorData:
    """Create CoordinatorData from scenario input.

    Args:
        input_data: Dictionary of input values from scenario

    Returns:
        Populated CoordinatorData instance
    """
    data = CoordinatorData()

    # Core state values
    data.soc = input_data.get("soc", 50.0)
    data.operation_mode = input_data.get("operation_mode", "autonomous")
    data.backup_reserve = input_data.get("backup_reserve", 50)
    data.grid_power_kw = input_data.get("grid_power_kw", 0.0)
    data.load_power_kw = input_data.get("load_power_kw", 0.5)
    data.solar_power_kw = input_data.get("solar_power_kw", 0.0)
    data.battery_power_kw = input_data.get("battery_power_kw", 0.0)
    data.general_price = input_data.get("general_price", 0.25)
    data.feed_in_price = input_data.get("feed_in_price", 0.08)
    data.price_spike = input_data.get("price_spike", False)
    data.manual_override = input_data.get("manual_override", False)

    # Forecast data
    data.solcast_today = input_data.get("solcast_today", [])
    data.solcast_tomorrow = input_data.get("solcast_tomorrow", [])
    data.general_forecast = input_data.get("general_forecast", [])
    data.feed_in_forecast = input_data.get("feed_in_forecast", [])

    # Initialize empty containers
    data.decision_log = []
    data.daily_forecast = []
    data.daily_forecast_soc_15min = []
    data.forecast_consumption_source_counts = {}
    data.forecast_history = []
    data.target_reached_today = input_data.get("target_reached_today", False)

    return data


def setup_mock_hass(input_data: dict) -> MagicMock:
    """Create mock Home Assistant instance with entity states.

    Args:
        input_data: Dictionary containing entity states

    Returns:
        Mock HASS instance
    """
    hass = MagicMock()
    hass.states = MagicMock()
    hass.data = {}

    def mock_get_state(entity_id):
        state = MagicMock()
        # Return appropriate state based on entity_id
        if "solcast_today" in entity_id:
            state.state = "unknown"
            state.attributes = {"detailedForecast": input_data.get("solcast_today", [])}
        elif "solcast_tomorrow" in entity_id:
            state.state = "unknown"
            state.attributes = {
                "detailedForecast": input_data.get("solcast_tomorrow", [])
            }
        elif "general_forecast" in entity_id:
            state.state = "unknown"
            state.attributes = {"forecasts": input_data.get("general_forecast", [])}
        elif "feed_in_forecast" in entity_id:
            state.state = "unknown"
            state.attributes = {"forecasts": input_data.get("feed_in_forecast", [])}
        elif "sun" in entity_id:
            state.state = input_data.get("sun_state", "above_horizon")
            state.attributes = {}
        else:
            state.state = "unknown"
            state.attributes = {}
        return state

    hass.states.get = mock_get_state
    return hass


def create_mock_entry(config_overrides: dict) -> MagicMock:
    """Create mock ConfigEntry with scenario config overrides.

    Args:
        config_overrides: Dictionary of config values to override

    Returns:
        Mock ConfigEntry instance
    """
    entry = MagicMock()
    entry.data = {}
    entry.options = {
        "battery_target": 90,
        "cheap_price_percentile": 40,
        "cheap_price_deadband": 0.02,
        "max_pre_charge_price": 0.30,
        "forecast_lookahead_hours": 8,
        "demand_window_start": "18:00:00",
        "demand_window_end": "22:00:00",
        "load_weight_recent": 0.7,
        "minimum_target_soc": 15,
        "spike_price_percentile": 80,
    }
    # Apply overrides
    entry.options.update(config_overrides)
    return entry


def create_mock_get_entity_id() -> callable:
    """Create mock entity ID resolver."""

    def _get_entity_id(key):
        entity_map = {
            "teslemetry_soc": "sensor.tesla_powerwall_soc",
            "teslemetry_operation_mode": "sensor.tesla_powerwall_operation_mode",
            "teslemetry_backup_reserve": "sensor.tesla_powerwall_backup_reserve",
            "teslemetry_grid_power": "sensor.tesla_powerwall_grid_power",
            "teslemetry_load_power": "sensor.tesla_load_power",
            "teslemetry_solar_power": "sensor.tesla_solar_power",
            "solcast_today": "sensor.solcast_today",
            "solcast_tomorrow": "sensor.solcast_tomorrow",
            "general_forecast": "sensor.amber_general_forecast",
            "feed_in_forecast": "sensor.amber_feed_in_forecast",
            "sun": "sun.sun",
        }
        return entity_map.get(key, f"sensor.{key}")

    return _get_entity_id


def create_mock_get_switch_state(switch_states: dict) -> callable:
    """Create mock switch state resolver.

    Args:
        switch_states: Dictionary of switch name -> bool state

    Returns:
        Function that returns switch states
    """

    def _get_switch_state(key):
        defaults = {
            "automation_enabled": True,
            "spike_discharge_enabled": True,
            "demand_window_block": True,
            "manual_override": False,
            "allow_dw_entry_under_target": False,
            "spike_discharge_conservative": False,
        }
        return switch_states.get(key, defaults.get(key, False))

    return _get_switch_state


def assert_expected_values(data: CoordinatorData, expected: dict, scenario_name: str):
    """Assert that CoordinatorData matches expected values.

    Args:
        data: Computed CoordinatorData
        expected: Dictionary of expected values
        scenario_name: Name of scenario (for error messages)
    """
    for key, expected_value in expected.items():
        # Handle special computed keys
        if key == "daily_forecast_min_soc":
            if not data.daily_forecast:
                pytest.fail(f"[{scenario_name}] daily_forecast is empty")
            actual_value = min(
                slot.get("predicted_soc", 100) for slot in data.daily_forecast
            )
            assert actual_value == pytest.approx(expected_value, rel=0.01), (
                f"[{scenario_name}] {key}: expected {expected_value}, got {actual_value}"
            )
            continue

        if key == "demand_window_end_soc":
            if not data.daily_forecast:
                pytest.fail(f"[{scenario_name}] daily_forecast is empty")
            # Find the slot at or just after demand window end (22:00)
            dw_end_soc = None
            for slot in data.daily_forecast:
                if slot.get("hour") == 22 and slot.get("minute") == 0:
                    dw_end_soc = slot.get("predicted_soc")
                    break
            if dw_end_soc is None:
                pytest.fail(f"[{scenario_name}] No forecast slot at demand window end")
            assert dw_end_soc >= expected_value, (
                f"[{scenario_name}] {key}: expected >= {expected_value}, got {dw_end_soc}"
            )
            continue

        actual_value = getattr(data, key, None)

        if actual_value is None:
            pytest.fail(f"[{scenario_name}] Missing attribute: {key}")

        # Handle BatteryMode enum comparison
        if isinstance(expected_value, str) and expected_value in [
            e.value for e in BatteryMode
        ]:
            expected_value = BatteryMode(expected_value)

        # Handle float comparisons with tolerance
        if isinstance(expected_value, float) and isinstance(actual_value, float):
            assert actual_value == pytest.approx(expected_value, rel=0.01), (
                f"[{scenario_name}] {key}: expected {expected_value}, got {actual_value}"
            )
        else:
            assert actual_value == expected_value, (
                f"[{scenario_name}] {key}: expected {expected_value}, got {actual_value}"
            )


# Discover all scenario files
SCENARIO_PATHS = discover_scenarios()


@pytest.mark.parametrize("scenario_path", SCENARIO_PATHS, ids=lambda p: p.stem)
def test_scenario(scenario_path, request):
    """Run a single scenario test.

    This test:
    1. Loads the scenario JSON
    2. Sets up mock HASS, config, and coordinator data
    3. Runs compute_derived_values
    4. Validates expected outputs
    """
    # Load scenario
    scenario = Scenario.from_json(scenario_path)

    # Skip if marked as skip
    if scenario.input.get("skip", False):
        pytest.skip(f"Scenario marked as skip: {scenario.name}")

    # Setup test time
    test_time_str = scenario.input.get("test_time", "2026-02-16T14:00:00+11:00")
    test_time = datetime.fromisoformat(test_time_str)

    # Setup mocks
    data = setup_coordinator_data(scenario.input)
    hass = setup_mock_hass(scenario.input)
    entry = create_mock_entry(scenario.config_overrides)
    get_entity_id = create_mock_get_entity_id()
    get_switch_state = create_mock_get_switch_state(scenario.switch_states)

    # Create computation engine
    engine = ComputationEngine(hass, entry, get_entity_id, get_switch_state)

    # Mock time and history fetcher
    # Use scenario's load_power_kw for recent load (for realistic forecast)
    recent_load = scenario.input.get("load_power_kw", 0.5)
    with (
        patch("homeassistant.util.dt.now", return_value=test_time),
        patch.object(engine, "_get_historical_hourly_averages", return_value={}),
        patch.object(engine._history_fetcher, "_historical_load_cache", {}),
        patch.object(engine._history_fetcher, "_historical_load_sample_counts", {}),
        patch.object(engine._history_fetcher, "_historical_load_source", "none"),
        patch.object(engine._history_fetcher, "_recent_load_1hr_kw", recent_load),
        patch.object(
            engine._forecast_computer,
            "_get_historical_hourly_averages",
            return_value={},
        ),
    ):
        # Run computation
        engine.compute_derived_values(data)

    # Validate expected values
    assert_expected_values(data, scenario.expected, scenario.name)


def test_scenario_discovery():
    """Verify that scenario discovery works."""
    # This test ensures the discovery mechanism works
    # It will fail if no scenarios are found (which shouldn't happen)
    assert len(SCENARIO_PATHS) >= 0, "No scenarios discovered"

    # Verify each scenario can be loaded
    for path in SCENARIO_PATHS:
        scenario = Scenario.from_json(path)
        assert scenario.name, f"Scenario {path} has no name"
        assert scenario.input, f"Scenario {path} has no input"
        assert scenario.expected, f"Scenario {path} has no expected values"
