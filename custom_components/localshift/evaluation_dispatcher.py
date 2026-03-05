"""Dispatch evaluation triggers and guards."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime

from homeassistant.core import Event, HomeAssistant, callback

from .const import CONF_PRICING_GENERAL_PRICE

_LOGGER = logging.getLogger(__name__)


class EvaluationDispatcher:
    """Handle evaluation trigger logic and stale price guards."""

    def __init__(
        self,
        hass: HomeAssistant,
        get_entity_id: Callable[[str], str],
        read_state_func: Callable[[], None],
        notify_listeners_func: Callable[[], None],
        evaluate_state_machine_func: Callable[[], object],
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
