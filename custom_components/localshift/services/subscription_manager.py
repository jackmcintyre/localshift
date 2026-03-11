"""Schedule timers and HA state subscriptions."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import time

from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_change,
    async_track_time_interval,
)

_LOGGER = logging.getLogger(__name__)


class SubscriptionManager:
    """Manage coordinator timer and state subscriptions."""

    def __init__(
        self,
        hass: HomeAssistant,
        handle_state_change,
        handle_fast_tick,
        handle_medium_tick,
        handle_slow_tick,
        handle_midnight_reset,
        handle_daily_summary,
        handle_learning_save,
        periodic_interval_fast,
        periodic_interval_medium,
        periodic_interval_slow,
        learning_save_interval,
    ) -> None:
        self.hass = hass
        self._handle_state_change = handle_state_change
        self._handle_fast_tick = handle_fast_tick
        self._handle_medium_tick = handle_medium_tick
        self._handle_slow_tick = handle_slow_tick
        self._handle_midnight_reset = handle_midnight_reset
        self._handle_daily_summary = handle_daily_summary
        self._handle_learning_save = handle_learning_save

        self._periodic_interval_fast = periodic_interval_fast
        self._periodic_interval_medium = periodic_interval_medium
        self._periodic_interval_slow = periodic_interval_slow
        self._learning_save_interval = learning_save_interval

        self._unsub_state: CALLBACK_TYPE | None = None
        self._unsub_midnight: CALLBACK_TYPE | None = None
        self._unsub_daily_summary: CALLBACK_TYPE | None = None
        self._unsub_learning_save: CALLBACK_TYPE | None = None
        self._unsub_timer_fast: CALLBACK_TYPE | None = None
        self._unsub_timer_medium: CALLBACK_TYPE | None = None
        self._unsub_timer_slow: CALLBACK_TYPE | None = None

    def start(self, monitored_entities: Iterable[str], demand_window_end: time) -> None:
        """Start all subscriptions and timers."""
        self._unsub_state = async_track_state_change_event(
            self.hass,
            list(monitored_entities),
            self._handle_state_change,
        )

        self._unsub_timer_fast = async_track_time_interval(
            self.hass,
            self._handle_fast_tick,
            self._periodic_interval_fast,
        )
        self._unsub_timer_medium = async_track_time_interval(
            self.hass,
            self._handle_medium_tick,
            self._periodic_interval_medium,
        )
        self._unsub_timer_slow = async_track_time_interval(
            self.hass,
            self._handle_slow_tick,
            self._periodic_interval_slow,
        )

        self._unsub_midnight = async_track_time_change(
            self.hass,
            self._handle_midnight_reset,
            hour=0,
            minute=0,
            second=0,
        )

        self._unsub_daily_summary = async_track_time_change(
            self.hass,
            self._handle_daily_summary,
            hour=demand_window_end.hour,
            minute=demand_window_end.minute,
            second=0,
        )

        self._unsub_learning_save = async_track_time_interval(
            self.hass,
            self._handle_learning_save,
            self._learning_save_interval,
        )

    def stop(self) -> None:
        """Stop all subscriptions and timers."""
        for unsub in (
            self._unsub_state,
            self._unsub_midnight,
            self._unsub_daily_summary,
            self._unsub_learning_save,
            self._unsub_timer_fast,
            self._unsub_timer_medium,
            self._unsub_timer_slow,
        ):
            if unsub:
                unsub()

        self._unsub_state = None
        self._unsub_midnight = None
        self._unsub_daily_summary = None
        self._unsub_learning_save = None
        self._unsub_timer_fast = None
        self._unsub_timer_medium = None
        self._unsub_timer_slow = None

    def reschedule_daily_summary(self, demand_window_end: time) -> None:
        """Reschedule daily summary timer using the provided time."""
        if self._unsub_daily_summary is not None:
            self._unsub_daily_summary()
            self._unsub_daily_summary = None

        self._unsub_daily_summary = async_track_time_change(
            self.hass,
            self._handle_daily_summary,
            hour=demand_window_end.hour,
            minute=demand_window_end.minute,
            second=0,
        )

        _LOGGER.info(
            "Daily summary timer rescheduled for %02d:%02d",
            demand_window_end.hour,
            demand_window_end.minute,
        )
