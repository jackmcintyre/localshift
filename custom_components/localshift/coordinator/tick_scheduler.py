"""Periodic task scheduling for LocalShift coordinator.

Responsibilities:
- FAST tick (1 min): state machine evaluation, automation readiness
- MEDIUM tick (5 min): entity health, learning tasks, load refresh
- SLOW tick (30 min): weather forecast, forecast accuracy
- Daily events: midnight reset, daily summary
- Solar backfill tracking
- Cost accumulation
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from .coordinator import LocalShiftCoordinator

_LOGGER = logging.getLogger(__name__)


class TickScheduler:
    """Manages periodic task execution for coordinator."""

    def __init__(
        self,
        coordinator: LocalShiftCoordinator,
    ) -> None:
        """Initialize tick scheduler.

        Args:
            coordinator: Parent coordinator instance
        """
        self._coordinator = coordinator
