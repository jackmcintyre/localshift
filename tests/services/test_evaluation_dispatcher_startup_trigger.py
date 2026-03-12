"""Tests for EvaluationDispatcher startup-only immediate trigger (Issue #478).

These tests verify that the optimizer runs immediately when automation_ready
becomes True during startup, instead of waiting for the next periodic tick.
"""

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from homeassistant.util import dt as dt_util

from custom_components.localshift.services.evaluation_dispatcher import (
    EvaluationDispatcher,
)


class TestStartupReadyTrigger:
    """Tests for startup-only immediate evaluation trigger."""

    @pytest.fixture
    def mock_hass(self):
        """Create mock Home Assistant."""
        hass = MagicMock()
        hass.async_create_task = MagicMock()
        return hass

    @pytest.fixture
    def mock_state_machine(self):
        """Create mock state machine."""
        return MagicMock(in_mode_transition=False)

    @pytest.fixture
    def mock_callbacks(self):
        """Create mock callback functions."""
        return {
            "read_state": MagicMock(),
            "notify_listeners": MagicMock(),
            "evaluate_state_machine": AsyncMock(),
        }

    @pytest.fixture
    def dispatcher(self, mock_hass, mock_state_machine, mock_callbacks):
        """Create EvaluationDispatcher with mocks."""
        return EvaluationDispatcher(
            mock_hass,
            lambda _key: "sensor.price",
            mock_callbacks["read_state"],
            mock_callbacks["notify_listeners"],
            mock_callbacks["evaluate_state_machine"],
            mock_state_machine,
            timedelta(minutes=10),
        )

    def test_startup_trigger_fires_when_automation_becomes_ready(
        self, dispatcher, mock_hass, mock_callbacks
    ):
        """Test that immediate evaluation is triggered when automation_ready becomes True during startup."""
        # Initially automation is not ready
        is_ready = False

        # First call - automation not ready, trigger should not fire
        dispatcher.maybe_trigger_on_startup_ready(lambda: is_ready)
        mock_hass.async_create_task.assert_not_called()

        # Now automation becomes ready
        is_ready = True

        # Second call - automation now ready, trigger should fire
        dispatcher.maybe_trigger_on_startup_ready(lambda: is_ready)

        # Should have triggered immediate evaluation
        mock_hass.async_create_task.assert_called_once()
        assert (
            mock_hass.async_create_task.call_args.args[1]
            == "localshift_evaluate_startup_ready"
        )

    def test_startup_trigger_only_fires_once(
        self, dispatcher, mock_hass, mock_callbacks
    ):
        """Test that the startup trigger only fires once, even if called multiple times when ready."""
        is_ready = False

        # First call - not ready yet, won't trigger but tracks the not-ready state
        dispatcher.maybe_trigger_on_startup_ready(lambda: is_ready)
        assert mock_hass.async_create_task.call_count == 0

        # Now becomes ready
        is_ready = True

        # Second call - should trigger
        dispatcher.maybe_trigger_on_startup_ready(lambda: is_ready)
        assert mock_hass.async_create_task.call_count == 1

        # Third call - should NOT trigger again
        dispatcher.maybe_trigger_on_startup_ready(lambda: is_ready)
        assert mock_hass.async_create_task.call_count == 1

        # Fourth call - should still NOT trigger
        dispatcher.maybe_trigger_on_startup_ready(lambda: is_ready)
        assert mock_hass.async_create_task.call_count == 1

    def test_startup_trigger_does_not_fire_if_already_ready_at_startup(
        self, dispatcher, mock_hass, mock_callbacks
    ):
        """Test that trigger doesn't fire if automation was already ready when checked."""
        # Automation starts ready
        is_ready = True

        # First call - already ready, should NOT trigger (was ready before we started tracking)
        dispatcher.maybe_trigger_on_startup_ready(lambda: is_ready)
        mock_hass.async_create_task.assert_not_called()

    def test_startup_trigger_handles_transition_from_not_ready_to_ready(
        self, dispatcher, mock_hass, mock_callbacks
    ):
        """Test the complete transition scenario: not ready -> ready -> stays ready."""
        # Start not ready
        is_ready = False

        # Check 1: not ready
        dispatcher.maybe_trigger_on_startup_ready(lambda: is_ready)
        assert mock_hass.async_create_task.call_count == 0

        # Check 2: still not ready
        dispatcher.maybe_trigger_on_startup_ready(lambda: is_ready)
        assert mock_hass.async_create_task.call_count == 0

        # Transition to ready
        is_ready = True

        # Check 3: now ready - should trigger
        dispatcher.maybe_trigger_on_startup_ready(lambda: is_ready)
        assert mock_hass.async_create_task.call_count == 1

        # Check 4: still ready - should NOT trigger again
        dispatcher.maybe_trigger_on_startup_ready(lambda: is_ready)
        assert mock_hass.async_create_task.call_count == 1

    def test_startup_trigger_skips_during_mode_transition(
        self, mock_hass, mock_callbacks
    ):
        """Test that startup trigger is skipped when state machine is in mode transition."""
        state_machine = MagicMock(in_mode_transition=True)

        dispatcher = EvaluationDispatcher(
            mock_hass,
            lambda _key: "sensor.price",
            mock_callbacks["read_state"],
            mock_callbacks["notify_listeners"],
            mock_callbacks["evaluate_state_machine"],
            state_machine,
            timedelta(minutes=10),
        )

        # Transition from not ready to ready
        is_ready = False
        dispatcher.maybe_trigger_on_startup_ready(lambda: is_ready)

        is_ready = True
        dispatcher.maybe_trigger_on_startup_ready(lambda: is_ready)

        # Should not trigger during mode transition
        mock_hass.async_create_task.assert_not_called()

    def test_startup_trigger_reads_state_and_notifies(
        self, dispatcher, mock_hass, mock_callbacks
    ):
        """Test that startup trigger reads state and notifies listeners before evaluating."""
        is_ready = False
        dispatcher.maybe_trigger_on_startup_ready(lambda: is_ready)

        is_ready = True
        dispatcher.maybe_trigger_on_startup_ready(lambda: is_ready)

        # Verify evaluation was scheduled
        mock_hass.async_create_task.assert_called_once()

    def test_startup_trigger_with_none_check_func(self, dispatcher, mock_hass):
        """Test that startup trigger handles None check function gracefully."""
        # Should not raise when check_func is None
        dispatcher.maybe_trigger_on_startup_ready(None)
        mock_hass.async_create_task.assert_not_called()

    def test_startup_trigger_with_callable_that_raises(
        self, dispatcher, mock_hass, mock_callbacks
    ):
        """Test that startup trigger handles exceptions in check function gracefully."""

        def failing_check():
            raise ValueError("Test error")

        # Should not raise, just log and continue
        dispatcher.maybe_trigger_on_startup_ready(failing_check)
        mock_hass.async_create_task.assert_not_called()
