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
from datetime import time
from typing import TYPE_CHECKING

from ..const import CONF_PRICING_GENERAL_FORECAST, CONF_WEATHER_ENTITY
from .synthetic_slot_health import SyntheticSlotHealth

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
        self._check_validator_health()
        # Issue #956: independent of the validator being present -- a
        # sustained synthetic slot 0 must still escalate integration_status
        # even on a config with no entity_validator wired up.
        self._apply_synthetic_slot_health()

    def _check_validator_health(self) -> None:
        """Run the entity validator and copy its results onto CoordinatorData."""
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

        # Detect orphaned registry entries owned by this config entry (Issue #880)
        config_entry_id = self._coordinator.entry.entry_id
        data.orphaned_localshift_entities = validator.check_orphaned_owned_entities(
            config_entry_id
        )
        if data.orphaned_localshift_entities:
            orphan_ids = ", ".join(sorted(data.orphaned_localshift_entities))
            count = len(data.orphaned_localshift_entities)
            orphan_warning = (
                f"{count} orphaned localshift "
                f"{'entity' if count == 1 else 'entities'}: {orphan_ids}"
            )
            data.entity_warnings = list(data.entity_warnings) + [orphan_warning]

        # Log any new errors
        if data.entity_errors:
            for error in data.entity_errors:
                _LOGGER.warning("Entity health error: %s", error)

        # Log warnings at debug level
        if data.entity_warnings:
            for warning in data.entity_warnings:
                _LOGGER.debug("Entity health warning: %s", warning)

    def _apply_synthetic_slot_health(self) -> None:
        """Escalate a sustained synthetic-slot-0 rate into integration_status.

        Issue #956: ``engine/optimizer_facade.py`` records one sample per
        evaluation onto ``data.synthetic_slot_health``. This method reads
        that tracker's state and (a) fires a one-shot startup WARNING if the
        configured forecast source has never produced an entry covering
        "now", and (b) escalates ``integration_status`` to "degraded" (never
        downgrading "error") while the rate stays sustained above threshold.
        Clearing is automatic: once the tracker's own hysteresis flips
        `degraded` back to False, this method simply stops touching status,
        and `_check_validator_health` has already recomputed it from scratch
        this cycle.
        """
        data = self._coordinator.data
        health = getattr(data, "synthetic_slot_health", None)
        # Guard against a bare MagicMock (unit tests that build coordinator
        # data from a generic mock, not a real CoordinatorData) leaking a
        # truthy Mock into integration_status.
        if not isinstance(health, SyntheticSlotHealth):
            return

        # Issue #956 review fix: the pricing entity mappings live in
        # entry.data, never entry.options (get_option reads entry.options
        # and would always return None here) -- get_entity_id is the
        # accessor every other read site in the codebase uses
        # (coordinator.py:338, state/reader.py:497), and it falls back to
        # DEFAULT_ENTITY_IDS when the key is unset.
        forecast_entity = self._coordinator.get_entity_id(CONF_PRICING_GENERAL_FORECAST)

        if health.needs_startup_warning:
            _LOGGER.warning(
                "SYNTHETIC_SLOT_STARTUP: no entry from the configured price "
                "forecast source %s has covered 'now' in %d evaluations "
                "since startup -- every slot 0 has been priced synthetically "
                "(Issue #956)",
                forecast_entity,
                health.evaluations_since_start,
            )
            health.startup_warning_logged = True

        if not health.degraded:
            return

        warning = (
            f"slot 0 priced synthetically in {round(health.rate * 100)}% of "
            f"the last hour's evaluations ({health.sample_count} samples) -- "
            f"check {forecast_entity}"
        )
        data.entity_warnings = list(data.entity_warnings) + [warning]
        if data.integration_status == "ok":
            data.integration_status = "degraded"
            data.integration_status_message = warning

    def reset_entity_tracking_on_options_change(self) -> None:
        """Reset entity tracking when options change.

        Called when user reconfigures integration via options flow.
        Resets tracking for entities that may have changed (e.g., weather_entity)
        to clear broken status and allow recovery without restart.
        """
        if self._coordinator._entity_validator is None:
            return

        # Reset tracking for weather entity (most commonly reconfigured optional entity)
        self._coordinator._entity_validator.reset_entity_tracking(CONF_WEATHER_ENTITY)

        _LOGGER.info("Reset entity tracking for options change")

    async def refresh_weather_forecast(self) -> None:
        """Refresh temperature forecast from weather entity.

        Uses the modern weather.get_forecasts service (HA 2024.3+) with caching.
        Updates CoordinatorData with the latest forecast for use by sensors.
        """
        if self._coordinator._computation_engine is None:
            _LOGGER.debug(
                "Computation engine not initialized, skipping weather forecast"
            )
            return

        forecasts = (
            await self._coordinator._computation_engine.async_refresh_weather_forecast()
        )

        if forecasts is not None:
            # Update CoordinatorData with the forecast data
            self._coordinator.data.weather_temperature_forecast = {}
            for forecast in forecasts:
                hour = forecast.slot_time.hour
                temperature = forecast.temperature
                if temperature is not None:
                    self._coordinator.data.weather_temperature_forecast[hour] = (
                        temperature
                    )

            _LOGGER.debug(
                "Updated weather forecast: %d hours of temperature data",
                len(self._coordinator.data.weather_temperature_forecast),
            )

    def parse_time_option(self, key: str, default: str) -> time:
        """Parse a time string option (HH:MM:SS) into a time object.

        Args:
            key: Config option key
            default: Default time string if option not set

        Returns:
            Parsed time object
        """
        time_str = str(self._coordinator.get_option(key, default))
        parts = time_str.split(":")
        try:
            return time(
                int(parts[0]),
                int(parts[1]) if len(parts) > 1 else 0,
                int(parts[2]) if len(parts) > 2 else 0,
            )
        except (ValueError, IndexError):
            _LOGGER.debug(
                "Invalid time format for %s: %s. Using default: %s",
                key,
                time_str,
                default,
            )
            d_parts = default.split(":")
            return time(int(d_parts[0]), int(d_parts[1]), int(d_parts[2]))
