"""Tests for button platform entities.

Issue #660: Add missing platform entity tests (0% coverage)
"""

from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from custom_components.localshift.button import (
    BUTTON_ICONS,
    BUTTON_NAMES,
    BUTTON_RESET_LEARNING,
    BUTTON_UPDATE_FORECAST,
    LocalShiftButtonBase,
    ResetLearningDataButton,
    UpdateForecastButton,
    async_setup_entry,
)
from custom_components.localshift.const import DOMAIN


@pytest.fixture
def mock_coordinator():
    """Create a mock coordinator with required methods."""
    coordinator = MagicMock()
    coordinator.async_clear_historical_cache = AsyncMock()
    coordinator.async_evaluate_state_machine = AsyncMock()
    coordinator._notification_service = None
    coordinator.data = MagicMock()
    coordinator.data.recent_decision_log = []
    return coordinator


@pytest.fixture
def mock_entry():
    """Create a mock config entry."""
    entry = MagicMock()
    entry.entry_id = "test_entry_123"
    entry.data = {}
    entry.options = {}
    return entry


class TestLocalShiftButtonBase:
    """Tests for LocalShiftButtonBase."""

    def test_button_initialization(self, mock_coordinator, mock_entry):
        """Test base button initializes correctly."""
        button = LocalShiftButtonBase(
            mock_coordinator, mock_entry, BUTTON_UPDATE_FORECAST
        )

        assert button.coordinator == mock_coordinator
        assert button._entry == mock_entry
        assert button._attr_has_entity_name is True

    def test_button_device_info(self, mock_coordinator, mock_entry):
        """Test device info is correctly generated."""
        button = LocalShiftButtonBase(
            mock_coordinator, mock_entry, BUTTON_UPDATE_FORECAST
        )
        device_info = button.device_info

        assert device_info["identifiers"] == {(DOMAIN, "test_entry_123")}
        assert device_info["name"] == "LocalShift"
        assert device_info["manufacturer"] == "Custom"
        assert device_info["model"] == "Solar Battery Automation"
        assert device_info["sw_version"] == "0.0.2"

    def test_button_attributes_update_forecast(self, mock_coordinator, mock_entry):
        """Test Update Forecast button has correct attributes."""
        button = LocalShiftButtonBase(
            mock_coordinator, mock_entry, BUTTON_UPDATE_FORECAST
        )

        assert button._attr_unique_id == "localshift_update_forecast"
        assert button._attr_name == BUTTON_NAMES[BUTTON_UPDATE_FORECAST]
        assert button._attr_icon == BUTTON_ICONS[BUTTON_UPDATE_FORECAST]

    def test_button_attributes_reset_learning(self, mock_coordinator, mock_entry):
        """Test Reset Learning button has correct attributes."""
        button = LocalShiftButtonBase(
            mock_coordinator, mock_entry, BUTTON_RESET_LEARNING
        )

        assert button._attr_unique_id == "localshift_reset_learning"
        assert button._attr_name == BUTTON_NAMES[BUTTON_RESET_LEARNING]
        assert button._attr_icon == BUTTON_ICONS[BUTTON_RESET_LEARNING]


class TestUpdateForecastButton:
    """Tests for UpdateForecastButton."""

    def test_button_initialization(self, mock_coordinator, mock_entry):
        """Test button initializes with correct attributes."""
        button = UpdateForecastButton(mock_coordinator, mock_entry)

        assert button._attr_unique_id == "localshift_update_forecast"
        assert button._attr_name == BUTTON_NAMES[BUTTON_UPDATE_FORECAST]
        assert button._attr_icon == BUTTON_ICONS[BUTTON_UPDATE_FORECAST]

    @pytest.mark.asyncio
    async def test_async_press_clears_cache_and_evaluates(
        self, mock_coordinator, mock_entry
    ):
        """Test button press clears cache and triggers evaluation."""
        button = UpdateForecastButton(mock_coordinator, mock_entry)

        await button.async_press()

        mock_coordinator.async_clear_historical_cache.assert_awaited_once()
        mock_coordinator.async_evaluate_state_machine.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_async_press_sends_notification_when_service_available(
        self, mock_coordinator, mock_entry
    ):
        """Test button press sends notification when service is available."""
        notification_service = MagicMock()
        notification_service.send_manual_action_notification = AsyncMock()
        mock_coordinator._notification_service = notification_service

        button = UpdateForecastButton(mock_coordinator, mock_entry)
        await button.async_press()

        notification_service.send_manual_action_notification.assert_awaited_once()


class TestResetLearningDataButton:
    """Tests for ResetLearningDataButton."""

    def test_button_initialization(self, mock_coordinator, mock_entry):
        """Test button initializes with correct attributes."""
        button = ResetLearningDataButton(mock_coordinator, mock_entry)

        assert button._attr_unique_id == "localshift_reset_learning"
        assert button._attr_name == BUTTON_NAMES[BUTTON_RESET_LEARNING]
        assert button._attr_icon == BUTTON_ICONS[BUTTON_RESET_LEARNING]

    @pytest.mark.asyncio
    async def test_async_press_clears_decision_tracker(
        self, mock_coordinator, mock_entry
    ):
        """Test button press clears decision tracker data."""
        decision_tracker = MagicMock()
        decision_tracker._pending_decisions = {"decision1": "data"}
        decision_tracker._completed_decisions = {"decision2": "data"}
        decision_tracker.async_save = AsyncMock()
        mock_coordinator.decision_tracker = decision_tracker

        button = ResetLearningDataButton(mock_coordinator, mock_entry)
        await button.async_press()

        assert len(decision_tracker._pending_decisions) == 0
        assert len(decision_tracker._completed_decisions) == 0
        decision_tracker.async_save.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_async_press_resets_learning_status(
        self, mock_coordinator, mock_entry
    ):
        """Test button press resets learning status."""
        decision_tracker = MagicMock()
        decision_tracker._pending_decisions = {}
        decision_tracker._completed_decisions = {}
        decision_tracker.async_save = AsyncMock()
        mock_coordinator.decision_tracker = decision_tracker
        mock_coordinator.data.learning_status = "optimizing"

        button = ResetLearningDataButton(mock_coordinator, mock_entry)
        await button.async_press()

        assert mock_coordinator.data.learning_status == "observing"

    @pytest.mark.asyncio
    async def test_async_press_clears_recent_decision_log(
        self, mock_coordinator, mock_entry
    ):
        """Test button press clears recent decision log."""
        decision_tracker = MagicMock()
        decision_tracker._pending_decisions = {}
        decision_tracker._completed_decisions = {}
        decision_tracker.async_save = AsyncMock()
        mock_coordinator.decision_tracker = decision_tracker
        mock_coordinator.data.recent_decision_log = ["decision1", "decision2"]

        button = ResetLearningDataButton(mock_coordinator, mock_entry)
        await button.async_press()

        assert len(mock_coordinator.data.recent_decision_log) == 0

    @pytest.mark.asyncio
    async def test_async_press_handles_missing_decision_tracker(
        self, mock_coordinator, mock_entry
    ):
        """Test button press handles missing decision tracker gracefully."""
        mock_coordinator.decision_tracker = None

        button = ResetLearningDataButton(mock_coordinator, mock_entry)

        await button.async_press()

        assert mock_coordinator.data.learning_status == "observing"

    @pytest.mark.asyncio
    async def test_async_press_calls_weather_correlation_reset(
        self, mock_coordinator, mock_entry
    ):
        """Test button press triggers weather correlation reset when available."""
        mock_coordinator.decision_tracker = None
        weather_correlation = MagicMock()
        weather_correlation.async_reset = AsyncMock()
        mock_coordinator._computation_engine = MagicMock()
        mock_coordinator._computation_engine._weather_correlation = weather_correlation

        button = ResetLearningDataButton(mock_coordinator, mock_entry)
        await button.async_press()

        weather_correlation.async_reset.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_async_press_calls_weather_correlation_reset_via_property(
        self, mock_coordinator, mock_entry
    ):
        """Test reset uses computation engine property access when available."""

        class _EngineWithProperty:
            def __init__(self, corr):
                self._corr = corr

            @property
            def weather_correlation(self):
                return self._corr

        mock_coordinator.decision_tracker = None
        weather_correlation = MagicMock()
        weather_correlation.async_reset = AsyncMock()
        mock_coordinator._computation_engine = _EngineWithProperty(weather_correlation)

        button = ResetLearningDataButton(mock_coordinator, mock_entry)
        await button.async_press()

        weather_correlation.async_reset.assert_awaited_once()


class TestAsyncSetupEntry:
    """Tests for async_setup_entry."""

    @pytest.mark.asyncio
    async def test_async_setup_entry_creates_both_buttons(
        self, mock_coordinator, mock_entry
    ):
        """Test that async_setup_entry creates both button entities."""
        mock_entry.runtime_data = mock_coordinator
        added_entities = []

        def mock_async_add_entities(entities):
            added_entities.extend(entities)

        with patch("custom_components.localshift.button._LOGGER") as mock_logger:
            await async_setup_entry(MagicMock(), mock_entry, mock_async_add_entities)

            assert len(added_entities) == 2
            assert mock_logger.info.called

    @pytest.mark.asyncio
    async def test_buttons_receive_coordinator(self, mock_coordinator, mock_entry):
        """Test that buttons receive the coordinator and entry."""
        mock_entry.runtime_data = mock_coordinator
        added_entities = []

        def mock_async_add_entities(entities):
            added_entities.extend(entities)

        await async_setup_entry(MagicMock(), mock_entry, mock_async_add_entities)

        for button in added_entities:
            assert button.coordinator == mock_coordinator
            assert button._entry == mock_entry

    @pytest.mark.asyncio
    async def test_update_forecast_button_created(self, mock_coordinator, mock_entry):
        """Test that UpdateForecastButton is created."""
        mock_entry.runtime_data = mock_coordinator
        added_entities = []

        def mock_async_add_entities(entities):
            added_entities.extend(entities)

        await async_setup_entry(MagicMock(), mock_entry, mock_async_add_entities)

        button_classes = [type(b) for b in added_entities]
        assert UpdateForecastButton in button_classes

    @pytest.mark.asyncio
    async def test_reset_learning_button_created(self, mock_coordinator, mock_entry):
        """Test that ResetLearningDataButton is created."""
        mock_entry.runtime_data = mock_coordinator
        added_entities = []

        def mock_async_add_entities(entities):
            added_entities.extend(entities)

        await async_setup_entry(MagicMock(), mock_entry, mock_async_add_entities)

        button_classes = [type(b) for b in added_entities]
        assert ResetLearningDataButton in button_classes
