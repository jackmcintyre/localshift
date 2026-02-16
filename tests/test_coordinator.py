"""Unit tests for coordinator."""
import pytest
from datetime import datetime, time, timedelta
from unittest.mock import MagicMock, AsyncMock, patch

from custom_components.amber_powerwall.coordinator import AmberPowerwallCoordinator

from .conftest import mock_hass, mock_entry, mock_get_entity_id


def test_coordinator_initialization(mock_hass, mock_entry):
    """Test coordinator initialization."""
    coordinator = AmberPowerwallCoordinator(
        mock_hass,
        mock_entry,
        mock_get_entity_id,
    )
    
    assert coordinator is not None
    assert coordinator.hass == mock_hass
    assert coordinator.entry == mock_entry


def test_coordinator_get_entity_id(mock_hass, mock_entry):
    """Test entity ID retrieval."""
    coordinator = AmberPowerwallCoordinator(
        mock_hass,
        mock_entry,
        mock_get_entity_id,
    )
    
    entity_id = coordinator._get_entity_id("teslemetry_soc")
    assert entity_id == "sensor.tesla_powerwall_soc"


def test_coordinator_get_switch_state(mock_hass, mock_entry):
    """Test switch state retrieval."""
    coordinator = AmberPowerwallCoordinator(
        mock_hass,
        mock_entry,
        mock_get_entity_id,
    )
    
    # Test with mock data
    coordinator._switch_states = {
        "automation_enabled": True,
    }
    
    state = coordinator._get_switch_state("automation_enabled")
    assert state is True
    
    # Test default
    state = coordinator._get_switch_state("nonexistent")
    assert state is False