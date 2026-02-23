"""Test fixtures and configuration for localshift tests.

This module provides both simple mocks for basic unit tests and realistic
HA state simulations for tests that need to verify behavior with real-world
entity states.

For tests that need realistic HA behavior, use:
- mock_hass_with_states: A hass instance with MockStates
- realistic_entity_states: Default entity states for LocalShift
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Import the real homeassistant modules
from custom_components.localshift.computation_engine import (
    ComputationEngine,
)
from custom_components.localshift.coordinator_data import CoordinatorData

# Import realistic HA state simulation
from tests.fixtures.ha_entities import (
    MockState,
    MockStates,
    create_amber_price_forecast_state,
    create_default_entity_states,
    create_sample_amber_price_forecast,
    create_sample_solcast_forecast,
    create_solcast_forecast_state,
    create_unavailable_entity_states,
    create_unknown_entity_states,
)

# =============================================================================
# SIMPLE MOCKS (for basic unit tests)
# =============================================================================


@pytest.fixture
def mock_hass():
    """Create a mock Home Assistant instance.

    This is a simple mock suitable for tests that don't need realistic
    entity state behavior. For realistic HA state simulation, use
    mock_hass_with_states instead.
    """
    hass = MagicMock()
    hass.states = {}
    hass.data = {}
    return hass


@pytest.fixture
def mock_entry():
    """Create a mock ConfigEntry."""
    entry = MagicMock()
    entry.data = {}
    entry.options = {
        "battery_target": 90,
        "cheap_price_percentile": 40,
        "cheap_price_deadband": 0.02,
        "max_pre_charge_price": 0.3,
        "forecast_lookahead_hours": 8,
        "demand_window_start": "18:00:00",
        "demand_window_end": "22:00:00",
        "load_weight_recent": 0.7,
    }
    return entry


@pytest.fixture
def mock_get_entity_id():
    """Mock function to get entity IDs."""

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
            "general_forecast": "sensor.general_forecast",
            "feed_in_forecast": "sensor.feed_in_forecast",
            "sun": "sun.sun",
        }
        return entity_map.get(key)

    return _get_entity_id


@pytest.fixture
def mock_get_switch_state():
    """Mock function to get switch states."""

    def _get_switch_state(key):
        switch_states = {
            "automation_enabled": True,
            "spike_discharge_enabled": True,
            "demand_window_block": False,
            "manual_override": False,
        }
        return switch_states.get(key, False)

    return _get_switch_state


@pytest.fixture
def coordinator_data():
    """Create a CoordinatorData instance with default values."""
    data = CoordinatorData()
    data.soc = 50.0
    data.operation_mode = "autonomous"
    data.backup_reserve = 50
    data.grid_power_kw = 0.0
    data.load_power_kw = 0.5
    data.solar_power_kw = 0.0
    data.general_price = 0.25
    data.feed_in_price = 0.08
    data.price_spike = False
    data.manual_override = False
    data.decision_log = []
    data.daily_forecast = []
    data.daily_forecast_soc_15min = []
    data.forecast_consumption_source_counts = {}
    return data


@pytest.fixture
def computation_engine(
    mock_hass, mock_entry, mock_get_entity_id, mock_get_switch_state
):
    """Create a ComputationEngine instance."""
    engine = ComputationEngine(
        mock_hass,
        mock_entry,
        mock_get_entity_id,
        mock_get_switch_state,
    )
    return engine


@pytest.fixture
def now():
    """Return a fixed datetime for testing."""
    return datetime(2026, 2, 16, 16, 0, 0)


# =============================================================================
# REALISTIC HA STATE SIMULATION FIXTURES
# =============================================================================


@pytest.fixture
def realistic_entity_states():
    """Create realistic entity states for LocalShift testing.

    This fixture provides a complete set of entity states that simulate
    real Home Assistant entity behavior. Use with mock_hass_with_states.

    Returns:
        Dictionary mapping entity IDs to MockState objects
    """
    return create_default_entity_states(
        soc=50.0,
        operation_mode="autonomous",
        backup_reserve=20.0,
        grid_power_kw=0.0,
        load_power_kw=0.5,
        solar_power_kw=3.0,
        battery_power_kw=-2.5,
        general_price=0.25,
        feed_in_price=0.08,
        price_spike=False,
    )


@pytest.fixture
def realistic_entity_states_with_forecasts(realistic_entity_states):
    """Create realistic entity states including forecast data.

    Extends realistic_entity_states with Solcast and Amber forecast data.

    Returns:
        Dictionary mapping entity IDs to MockState objects with forecasts
    """
    states = realistic_entity_states.copy()

    # Add Solcast forecasts (using DEFAULT_ENTITY_IDS naming)
    solcast_today = create_sample_solcast_forecast(num_periods=24, peak_kw=5.0)
    solcast_tomorrow = create_sample_solcast_forecast(num_periods=24, peak_kw=4.5)

    states["sensor.solcast_pv_forecast_forecast_today"] = create_solcast_forecast_state(
        solcast_today,
        entity_id="sensor.solcast_pv_forecast_forecast_today",
    )
    states["sensor.solcast_pv_forecast_forecast_tomorrow"] = (
        create_solcast_forecast_state(
            solcast_tomorrow,
            entity_id="sensor.solcast_pv_forecast_forecast_tomorrow",
        )
    )

    # Add Amber price forecasts (using DEFAULT_ENTITY_IDS naming)
    general_forecast = create_sample_amber_price_forecast(
        num_periods=48,
        base_price=0.25,
    )
    feed_in_forecast = create_sample_amber_price_forecast(
        num_periods=48,
        base_price=0.08,
    )

    states["sensor.100h_general_forecast"] = create_amber_price_forecast_state(
        general_forecast,
        entity_id="sensor.100h_general_forecast",
        friendly_name="General Price Forecast",
    )
    states["sensor.100h_feed_in_forecast"] = create_amber_price_forecast_state(
        feed_in_forecast,
        entity_id="sensor.100h_feed_in_forecast",
        friendly_name="Feed-in Price Forecast",
    )

    return states


@pytest.fixture
def mock_hass_with_states(realistic_entity_states):
    """Create a mock Home Assistant instance with realistic states.

    This fixture provides a hass instance that simulates real HA behavior:
    - hass.states.get() returns MockState objects or None
    - States have .state and .attributes properties
    - Entity availability is properly simulated

    Use this for tests that need to verify behavior with realistic entity states.

    Args:
        realistic_entity_states: Default entity states from fixture

    Returns:
        MagicMock with realistic states.get() behavior
    """
    hass = MagicMock()
    mock_states = MockStates(realistic_entity_states)

    # Make hass.states.get() use MockStates.get()
    hass.states.get = mock_states.get
    hass.states.async_all = mock_states.async_all

    # Allow tests to modify states
    hass._mock_states = mock_states

    hass.data = {}
    return hass


@pytest.fixture
def mock_hass_with_forecasts(realistic_entity_states_with_forecasts):
    """Create a mock Home Assistant instance with forecast data.

    Same as mock_hass_with_states but includes Solcast and Amber forecasts.

    Args:
        realistic_entity_states_with_forecasts: Entity states with forecast data

    Returns:
        MagicMock with realistic states including forecasts
    """
    hass = MagicMock()
    mock_states = MockStates(realistic_entity_states_with_forecasts)

    hass.states.get = mock_states.get
    hass.states.async_all = mock_states.async_all
    hass._mock_states = mock_states

    hass.data = {}
    return hass


# =============================================================================
# EDGE CASE FIXTURES
# =============================================================================


@pytest.fixture
def mock_hass_unavailable_entities(realistic_entity_states):
    """Create a mock HA instance where all entities are unavailable.

    Use this to test that the integration handles unavailable entities gracefully.

    Returns:
        MagicMock where all entities return state="unavailable"
    """
    entity_ids = list(realistic_entity_states.keys())
    unavailable_states = create_unavailable_entity_states(entity_ids)

    hass = MagicMock()
    mock_states = MockStates(unavailable_states)

    hass.states.get = mock_states.get
    hass.states.async_all = mock_states.async_all
    hass._mock_states = mock_states

    hass.data = {}
    return hass


@pytest.fixture
def mock_hass_unknown_entities(realistic_entity_states):
    """Create a mock HA instance where all entities are unknown.

    Use this to test that the integration handles unknown entity states.

    Returns:
        MagicMock where all entities return state="unknown"
    """
    entity_ids = list(realistic_entity_states.keys())
    unknown_states = create_unknown_entity_states(entity_ids)

    hass = MagicMock()
    mock_states = MockStates(unknown_states)

    hass.states.get = mock_states.get
    hass.states.async_all = mock_states.async_all
    hass._mock_states = mock_states

    hass.data = {}
    return hass


@pytest.fixture
def mock_hass_missing_entities():
    """Create a mock HA instance where entities don't exist.

    Use this to test that the integration handles missing entities gracefully.
    hass.states.get() will return None for all entity IDs.

    Returns:
        MagicMock where states.get() returns None
    """
    hass = MagicMock()
    mock_states = MockStates({})  # Empty states

    hass.states.get = mock_states.get
    hass.states.async_all = mock_states.async_all
    hass._mock_states = mock_states

    hass.data = {}
    return hass


@pytest.fixture
def mock_hass_partial_availability(realistic_entity_states):
    """Create a mock HA instance with mixed entity availability.

    Some entities are available, some are unavailable, some are missing.
    Use this to test partial failure scenarios.

    Returns:
        MagicMock with mixed entity availability
    """
    states = realistic_entity_states.copy()

    # Make some entities unavailable (using DEFAULT_ENTITY_IDS naming)
    states["sensor.my_home_percentage_charged"] = MockState(
        "sensor.my_home_percentage_charged", "unavailable", {}
    )
    states["sensor.100h_general_price"] = MockState(
        "sensor.100h_general_price", "unavailable", {}
    )

    # Remove some entities entirely (simulating missing entities)
    del states["sensor.my_home_solar_power"]
    del states["binary_sensor.100h_price_spike"]

    hass = MagicMock()
    mock_states = MockStates(states)

    hass.states.get = mock_states.get
    hass.states.async_all = mock_states.async_all
    hass._mock_states = mock_states

    hass.data = {}
    return hass


@pytest.fixture
def mock_hass_price_spike(realistic_entity_states):
    """Create a mock HA instance with price spike active.

    Returns:
        MagicMock with price_spike binary sensor set to 'on'
    """
    states = realistic_entity_states.copy()
    states["binary_sensor.100h_price_spike"] = MockState(
        "binary_sensor.100h_price_spike", "on", {}
    )
    states["sensor.100h_general_price"] = MockState(
        "sensor.100h_general_price",
        "2.50",
        {"unit_of_measurement": "$/kWh", "friendly_name": "General Price"},
    )

    hass = MagicMock()
    mock_states = MockStates(states)

    hass.states.get = mock_states.get
    hass.states.async_all = mock_states.async_all
    hass._mock_states = mock_states

    hass.data = {}
    return hass


@pytest.fixture
def mock_hass_low_battery(realistic_entity_states):
    """Create a mock HA instance with low battery SOC.

    Returns:
        MagicMock with SOC at 10%
    """
    states = realistic_entity_states.copy()
    states["sensor.my_home_percentage_charged"] = MockState(
        "sensor.my_home_percentage_charged",
        "10.0",
        {"unit_of_measurement": "%", "device_class": "battery"},
    )

    hass = MagicMock()
    mock_states = MockStates(states)

    hass.states.get = mock_states.get
    hass.states.async_all = mock_states.async_all
    hass._mock_states = mock_states

    hass.data = {}
    return hass


@pytest.fixture
def mock_hass_full_battery(realistic_entity_states):
    """Create a mock HA instance with full battery SOC.

    Returns:
        MagicMock with SOC at 100%
    """
    states = realistic_entity_states.copy()
    states["sensor.my_home_percentage_charged"] = MockState(
        "sensor.my_home_percentage_charged",
        "100.0",
        {"unit_of_measurement": "%", "device_class": "battery"},
    )

    hass = MagicMock()
    mock_states = MockStates(states)

    hass.states.get = mock_states.get
    hass.states.async_all = mock_states.async_all
    hass._mock_states = mock_states

    hass.data = {}
    return hass


@pytest.fixture
def mock_hass_negative_prices(realistic_entity_states):
    """Create a mock HA instance with negative electricity prices.

    Returns:
        MagicMock with negative general price
    """
    states = realistic_entity_states.copy()
    states["sensor.100h_general_price"] = MockState(
        "sensor.100h_general_price",
        "-0.05",
        {"unit_of_measurement": "$/kWh", "friendly_name": "General Price"},
    )

    hass = MagicMock()
    mock_states = MockStates(states)

    hass.states.get = mock_states.get
    hass.states.async_all = mock_states.async_all
    hass._mock_states = mock_states

    hass.data = {}
    return hass


# =============================================================================
# ENTITY VALIDATOR FIXTURE
# =============================================================================


@pytest.fixture
def mock_entity_validator():
    """Create a mock EntityValidator for testing.

    By default, reports all entities as healthy and allows automation.
    Tests can override the return values as needed.
    """
    from custom_components.localshift.entity_validator import IntegrationStatus

    validator = MagicMock()
    validator.should_allow_automation = MagicMock(return_value=True)
    validator.status = IntegrationStatus.OK
    validator.errors = []
    validator.warnings = []
    return validator


# =============================================================================
# STATE READER FIXTURES
# =============================================================================


@pytest.fixture
def state_reader(mock_hass_with_states, mock_entry, mock_entity_validator):
    """Create a StateReader instance with realistic HA states.

    Use this for testing state reading functionality with realistic entity states.
    """
    from custom_components.localshift.state_reader import StateReader

    return StateReader(mock_hass_with_states, mock_entry, mock_entity_validator)


@pytest.fixture
def state_reader_unavailable(
    mock_hass_unavailable_entities, mock_entry, mock_entity_validator
):
    """Create a StateReader with all entities unavailable."""
    from custom_components.localshift.state_reader import StateReader

    return StateReader(
        mock_hass_unavailable_entities, mock_entry, mock_entity_validator
    )


@pytest.fixture
def state_reader_missing(mock_hass_missing_entities, mock_entry, mock_entity_validator):
    """Create a StateReader with missing entities."""
    from custom_components.localshift.state_reader import StateReader

    return StateReader(mock_hass_missing_entities, mock_entry, mock_entity_validator)


# =============================================================================
# PARAMETRIZED FIXTURES FOR COMPREHENSIVE TESTING
# =============================================================================


@pytest.fixture(
    params=[
        "available",
        "unavailable",
        "unknown",
        "missing",
    ]
)
def mock_hass_various_states(request, realistic_entity_states):
    """Parametrized fixture for testing various entity states.

    Use this to run the same test against multiple availability scenarios:
    - available: All entities have valid states
    - unavailable: All entities have state="unavailable"
    - unknown: All entities have state="unknown"
    - missing: All entities return None from states.get()

    Example:
        def test_something(mock_hass_various_states):
            # Test runs 4 times with different states
            pass
    """
    availability = request.param

    if availability == "available":
        states = realistic_entity_states
    elif availability == "unavailable":
        entity_ids = list(realistic_entity_states.keys())
        states = create_unavailable_entity_states(entity_ids)
    elif availability == "unknown":
        entity_ids = list(realistic_entity_states.keys())
        states = create_unknown_entity_states(entity_ids)
    else:  # missing
        states = {}

    hass = MagicMock()
    mock_states = MockStates(states)

    hass.states.get = mock_states.get
    hass.states.async_all = mock_states.async_all
    hass._mock_states = mock_states

    hass.data = {}
    return hass


# =============================================================================
# RECORDER MOCK FIXTURE
# =============================================================================


@pytest.fixture
def mock_recorder():
    """Mock the recorder instance for async executor job.

    This fixture patches recorder.get_instance to return a mock that
    properly supports async_add_executor_job as an AsyncMock.

    Returns a dict with the expected structure for historical load data:
    - combined_avg: Combined hourly averages
    - combined_counts: Combined sample counts
    - weekday_avg: Weekday hourly averages
    - weekend_avg: Weekend hourly averages
    - weekday_counts: Weekday sample counts
    - weekend_counts: Weekend sample counts
    - profile_source: "weekday_weekend" or "combined_fallback"
    """
    # Create sample hourly data for testing
    combined_avg = {h: 0.5 for h in range(24)}  # 0.5 kW average for each hour
    combined_counts = {h: 7 for h in range(24)}  # 7 samples per hour
    weekday_avg = {h: 0.6 for h in range(24)}  # Higher weekday usage
    weekend_avg = {h: 0.4 for h in range(24)}  # Lower weekend usage
    weekday_counts = {h: 5 for h in range(24)}
    weekend_counts = {h: 2 for h in range(24)}

    mock_result = {
        "combined_avg": combined_avg,
        "combined_counts": combined_counts,
        "weekday_avg": weekday_avg,
        "weekend_avg": weekend_avg,
        "weekday_counts": weekday_counts,
        "weekend_counts": weekend_counts,
        "profile_source": "weekday_weekend",
    }

    mock_instance = MagicMock()
    mock_instance.async_add_executor_job = AsyncMock(return_value=mock_result)

    with patch(
        "homeassistant.components.recorder.get_instance",
        return_value=mock_instance,
    ):
        yield mock_instance
