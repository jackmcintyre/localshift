"""Test fixtures and configuration for amber_powerwall tests."""

from datetime import datetime
from unittest.mock import MagicMock

import pytest

# Import the real homeassistant modules
from custom_components.amber_powerwall.computation_engine import (
    ComputationEngine,
)
from custom_components.amber_powerwall.coordinator_data import CoordinatorData


@pytest.fixture
def mock_hass():
    """Create a mock Home Assistant instance."""
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
        "max_precharge_price": 0.3,
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
