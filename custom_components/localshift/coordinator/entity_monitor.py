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

    def read_all_external_state(self) -> None:
        """Read current state of all monitored external entities."""
        if self._coordinator._state_reader is None:
            return
        self._coordinator._state_reader.read_all_external_state(self._coordinator.data)

    def check_entity_health(self) -> None:
        """Check health of all tracked entities and update data.

        Populates integration status, errors, and warnings in CoordinatorData
        for sensors to expose to users.
        """
        if self._coordinator._entity_validator is None:
            return

        # Check all entities (external dependencies)
        self._coordinator._entity_validator.check_all_entities()

        # Update coordinator data with health status
        data = self._coordinator.data
        validator = self._coordinator._entity_validator

        data.integration_status = validator.status.value
        data.integration_status_message = validator.get_user_friendly_message()
        data.entity_errors = validator.errors
        data.entity_warnings = validator.warnings
        data.required_entities_healthy = all(
            validator.get_required_entities_status().values()
        )

        # Get detailed health summary
        health_summary = validator.get_health_summary()
        data.entity_health = health_summary.get("entities", {})
        data.last_entity_check = health_summary.get("last_check", "")

        # Check LocalShift internal entities
        data.localshift_entity_health = validator.check_all_localshift_entities()

        # Log any new errors
        if data.entity_errors:
            for error in data.entity_errors:
                _LOGGER.warning("Entity health error: %s", error)

        # Log warnings at debug level
        if data.entity_warnings:
            for warning in data.entity_warnings:
                _LOGGER.debug("Entity health warning: %s", warning)
