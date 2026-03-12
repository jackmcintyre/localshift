"""Dispatch evaluation triggers and guards."""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from datetime import datetime
from typing import Any

from homeassistant.core import Event, HomeAssistant, callback

from ..const import CONF_PRICING_GENERAL_PRICE
from ..forecast.load_deviation import LoadDeviationDetector

_LOGGER = logging.getLogger(__name__)


class EvaluationDispatcher:
    """Handle evaluation trigger logic and stale price guards."""

    def __init__(
        self,
        hass: HomeAssistant,
        get_entity_id: Callable[[str], str],
        read_state_func: Callable[[], None],
        notify_listeners_func: Callable[[], None],
        evaluate_state_machine_func: Callable[[], Coroutine[Any, Any, Any]],
        state_machine,
        stale_price_threshold,
    ) -> None:
        self.hass = hass
        self._get_entity_id = get_entity_id
        self._read_state = read_state_func
        self._notify_listeners = notify_listeners_func
        self._evaluate_state_machine = evaluate_state_machine_func
        self._state_machine = state_machine
        self._stale_price_threshold = stale_price_threshold
        self._load_deviation_detector = LoadDeviationDetector()

    @callback
    def on_state_change(self, _event: Event) -> None:
        """Handle a state change from a monitored entity."""
        if self._state_machine is None:
            return

        if self._state_machine.in_mode_transition:
            _LOGGER.debug("Skipping re-evaluation during mode transition")
            return

        self._read_state()
        self._notify_listeners()

        self.hass.async_create_task(
            self._evaluate_state_machine(),
            "localshift_evaluate_state_change",
        )

    @callback
    def on_fast_tick(self, _now: datetime) -> bool:
        """Handle evaluation dispatch on fast tick.

        Returns True if stale price was detected.
        """
        if self._trigger_load_deviation_reoptimization(_now):
            return False

        stale_price = self._check_stale_price()

        if stale_price:
            _LOGGER.info("Stale price detected, triggering state machine evaluation")
            self.hass.async_create_task(
                self._evaluate_state_machine(),
                "localshift_evaluate_stale_price",
            )
        else:
            self.hass.async_create_task(
                self._evaluate_state_machine(),
                "localshift_evaluate_periodic",
            )

        return stale_price

    def maybe_trigger_on_startup_ready(
        self, check_func: Callable[[], bool] | None
    ) -> None:
        """Trigger immediate evaluation when automation becomes ready during startup.

        Issue #478: This method checks if automation_ready has transitioned from
        False to True during startup, and triggers an immediate state machine
        evaluation if so. This prevents waiting up to 1 minute for the next
        periodic tick when all data becomes available.

        Args:
            check_func: Callable that returns True if automation is ready.
                       This is typically coordinator.data.automation_ready or
                       a lambda that checks the current state.

        """
        if self._state_machine is None:
            return

        if self._state_machine.in_mode_transition:
            _LOGGER.debug("Skipping startup-ready trigger during mode transition")
            return

        if check_func is None:
            return

        try:
            is_ready = check_func()
        except Exception:
            _LOGGER.exception("Error checking automation ready state")
            return

        # Track if we've seen automation NOT ready yet (startup tracking)
        if not hasattr(self, "_startup_saw_not_ready"):
            self._startup_saw_not_ready = False

        # Track if we've already triggered on startup ready
        if not hasattr(self, "_startup_trigger_fired"):
            self._startup_trigger_fired = False

        # If we haven't seen not-ready yet and it's ready now, this is initial state
        # Don't trigger yet - wait until we see not-ready first
        if not self._startup_saw_not_ready:
            if not is_ready:
                self._startup_saw_not_ready = True
                _LOGGER.debug("Startup: First time seeing automation not ready")
            return

        # We've seen not-ready, now check if it became ready
        if is_ready and not self._startup_trigger_fired:
            self._startup_trigger_fired = True
            _LOGGER.info(
                "Automation became ready during startup, triggering immediate evaluation"
            )

            self._read_state()
            self._notify_listeners()

            self.hass.async_create_task(
                self._evaluate_state_machine(),
                "localshift_evaluate_startup_ready",
            )

    def _trigger_load_deviation_reoptimization(self, now: datetime) -> bool:
        coordinator = getattr(self._read_state, "__self__", None)
        if coordinator is None:
            return False

        if not self._load_deviation_detector.evaluate(coordinator.data, now):
            return False

        self.hass.async_create_task(
            coordinator.async_recompute_and_evaluate(),
            "localshift_reoptimize_load_deviation",
        )
        return True

    def _check_stale_price(self) -> bool:
        """Check if price sensor hasn't updated in the stale threshold."""
        price_entity = self._get_entity_id(CONF_PRICING_GENERAL_PRICE)
        price_state = self.hass.states.get(price_entity)

        if price_state is None:
            _LOGGER.debug("Price entity not found: %s", price_entity)
            return False

        if price_state.state in ("unknown", "unavailable", None, ""):
            _LOGGER.debug("Price entity state is invalid: %s", price_state.state)
            return False

        if price_state.last_updated:
            from homeassistant.util import dt as dt_util

            now = dt_util.now()
            age = now - price_state.last_updated

            if age > self._stale_price_threshold:
                _LOGGER.warning(
                    "Price sensor %s is stale (last updated %s ago). "
                    "This may indicate an issue with the pricing integration.",
                    price_entity,
                    age,
                )
                return True

        return False
