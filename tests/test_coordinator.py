"""Unit tests for coordinator."""

from custom_components.localshift.coordinator import LocalShiftCoordinator


def test_coordinator_initialization(mock_hass, mock_entry):
    """Test coordinator initialization."""
    coordinator = LocalShiftCoordinator(mock_hass, mock_entry)

    assert coordinator is not None
    assert coordinator.hass == mock_hass
    assert coordinator.entry == mock_entry


def test_coordinator_get_entity_id(mock_hass, mock_entry, mock_get_entity_id):
    """Test entity ID retrieval."""
    entity_id = mock_get_entity_id("teslemetry_soc")
    assert entity_id == "sensor.tesla_powerwall_soc"


def test_coordinator_get_switch_state(mock_hass, mock_entry):
    """Test switch state retrieval."""
    coordinator = LocalShiftCoordinator(mock_hass, mock_entry)

    # Test with mock data
    coordinator._switch_states = {
        "automation_enabled": True,
    }

    state = coordinator.get_switch_state("automation_enabled")
    assert state is True

    # Test default
    state = coordinator.get_switch_state("nonexistent")
    assert state is False
