"""Solcast readiness checks and startup forecast bootstrap."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from homeassistant.core import HomeAssistant

from .const import CONF_SOLCAST_FORECAST_TODAY
from .coordinator_data import CoordinatorData

_LOGGER = logging.getLogger(__name__)


class ForecastBootstrapper:
    """Handle Solcast readiness checks and startup forecast computation."""

    def __init__(
        self,
        hass: HomeAssistant,
        data: CoordinatorData,
        get_entity_id: Callable[[str], str],
        read_state_func: Callable[[], None],
        compute_derived_values_func: Callable[[], None],
        notify_listeners_func: Callable[[], None],
        evaluate_state_machine_func: Callable[[], object],
        retry_delay,
        max_retries: int,
    ) -> None:
        self.hass = hass
        self.data = data
        self._get_entity_id = get_entity_id
        self._read_state = read_state_func
        self._compute_derived_values = compute_derived_values_func
        self._notify_listeners = notify_listeners_func
        self._evaluate_state_machine = evaluate_state_machine_func
        self._retry_delay = retry_delay
        self._max_retries = max_retries

        self._retry_count = 0
        self._solcast_ready = False
        self._forecast_computed_on_startup = False

    @property
    def retry_count(self) -> int:
        """Return the number of Solcast retries attempted."""
        return self._retry_count

    @property
    def solcast_ready(self) -> bool:
        """Return whether Solcast is ready on startup."""
        return self._solcast_ready

    @property
    def forecast_computed_on_startup(self) -> bool:
        """Return whether startup forecast computation completed."""
        return self._forecast_computed_on_startup

    def check_solcast_ready(self) -> bool:
        """Check if Solcast forecast data is available and valid.

        Returns True if Solcast data is ready, False otherwise.
        Also updates forecast_ready and forecast_status in CoordinatorData (Issue #319).
        """
        today_entity = self._get_entity_id(CONF_SOLCAST_FORECAST_TODAY)
        today_state = self.hass.states.get(today_entity)

        if today_state is None:
            _LOGGER.debug("Solcast today entity not found: %s", today_entity)
            self.data.forecast_ready = False
            self.data.forecast_status = "stale"
            return False

        if today_state.state in ("unknown", "unavailable", None, ""):
            _LOGGER.debug(
                "Solcast today entity state is %s, waiting for data",
                today_state.state,
            )
            self.data.forecast_ready = False
            self.data.forecast_status = "stale"
            return False

        forecast_data = today_state.attributes.get("detailedForecast")
        if not forecast_data or not isinstance(forecast_data, list):
            _LOGGER.debug("Solcast today forecast attribute is empty or invalid")
            self.data.forecast_ready = False
            self.data.forecast_status = "partial"
            return False

        if len(forecast_data) == 0:
            _LOGGER.debug("Solcast today forecast has no entries")
            self.data.forecast_ready = False
            self.data.forecast_status = "partial"
            return False

        if len(forecast_data) < 8:
            _LOGGER.debug(
                "Solcast today forecast has only %d entries (need 8+ for full forecast)",
                len(forecast_data),
            )
            self.data.forecast_ready = True
            self.data.forecast_status = "partial"
            return True

        _LOGGER.info(
            "Solcast forecast data is ready (%d entries for today)",
            len(forecast_data),
        )
        self.data.forecast_ready = True
        self.data.forecast_status = "ready"
        return True

    async def wait_for_solcast_and_compute(self) -> None:
        """Wait for Solcast data to be ready, then compute derived values."""
        if self.check_solcast_ready():
            self._solcast_ready = True
            _LOGGER.info("Solcast data available, proceeding with forecast computation")
            self._compute_derived_values()
            self._notify_listeners()

            await self._evaluate_state_machine()

            self._forecast_computed_on_startup = True
            return

        if self._retry_count >= self._max_retries:
            _LOGGER.warning(
                "Solcast data not available after %d retries. "
                "Forecast will use 0 kWh solar until Solcast provides data. "
                "Check Solcast integration status.",
                self._max_retries,
            )
            self._compute_derived_values()
            self._notify_listeners()

            await self._evaluate_state_machine()

            self._forecast_computed_on_startup = True
            return

        self._retry_count += 1
        _LOGGER.info(
            "Solcast data not ready yet (attempt %d/%d), retrying in %d seconds",
            self._retry_count,
            self._max_retries,
            self._retry_delay.total_seconds(),
        )

        self.hass.async_create_task(
            self._retry_solcast_check(),
            "localshift_solcast_retry",
        )

    async def _retry_solcast_check(self) -> None:
        """Retry checking Solcast data after a delay."""
        await asyncio.sleep(self._retry_delay.total_seconds())

        self._read_state()

        await self.wait_for_solcast_and_compute()
