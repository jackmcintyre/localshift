"""Entity monitoring and health checks for LocalShift coordinator.

Responsibilities:
- Read external entity states
- Entity health checks (availability, staleness)
- Track broken/recovered entities
- Reset tracking on config changes
- Weather forecast refresh
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant

if TYPE_CHECKING:  # pragma: no cover
    from .coordinator import LocalShiftCoordinator

_LOGGER = logging.getLogger(__name__)


class EntityMonitor:
    """Monitors external entities and performs health checks."""

    def __init__(
        self,
        coordinator: LocalShiftCoordinator,
    ) -> None:
        """Initialize entity monitor.

        Args:
            coordinator: Parent coordinator instance
        """
        self._coordinator = coordinator
        self._hass: HomeAssistant = coordinator.hass
