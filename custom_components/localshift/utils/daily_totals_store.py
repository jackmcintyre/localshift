"""Persistence for the day-to-date cost and energy totals (Issue #1102).

The totals are accumulated in memory on CoordinatorData by CostTracker. Without
a snapshot a Home Assistant restart starts them again from zero and the day's
figures are short until midnight.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.helpers.storage import Store

if TYPE_CHECKING:  # pragma: no cover
    from homeassistant.core import HomeAssistant

    from ..coordinator.data import CoordinatorData
    from .costs import CostTracker

_LOGGER = logging.getLogger(__name__)


class DailyTotalsStore:
    """Save and restore the day's totals across Home Assistant restarts."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Initialize the daily totals store.

        Args:
            hass: Home Assistant instance.
            entry_id: Config entry ID for storage key.

        """
        self._store = Store(hass, version=1, key=f"localshift.daily_totals.{entry_id}")

    async def async_restore(
        self, data: CoordinatorData, cost_tracker: CostTracker
    ) -> bool:
        """Restore today's totals into ``data`` if a same-day snapshot exists.

        Never raises: a missing, stale or unreadable snapshot leaves the totals
        at zero, which is what a restart did before this store existed.

        Returns:
            True if the totals were restored.

        """
        try:
            snapshot = await self._store.async_load()
            if snapshot is None:
                _LOGGER.debug("No stored daily totals found")
                return False
            restored = cost_tracker.restore_daily_totals(data, snapshot)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning(
                "Could not restore daily totals, starting the day's totals at zero: %s",
                exc,
            )
            return False

        if restored:
            _LOGGER.info(
                "Restored daily totals for %s: import $%.2f, export $%.2f",
                cost_tracker.totals_date.isoformat(),
                data.grid_import_cost,
                data.grid_export_revenue,
            )
        else:
            _LOGGER.info(
                "Discarded daily totals snapshot dated %s (today is %s)",
                snapshot.get("date"),
                cost_tracker.totals_date.isoformat(),
            )
        return restored

    async def async_save(
        self, data: CoordinatorData, cost_tracker: CostTracker
    ) -> None:
        """Persist the day's totals. Never raises."""
        try:
            await self._store.async_save(cost_tracker.snapshot_daily_totals(data))
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("Failed to save daily totals: %s", exc)
