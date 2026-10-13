"""Persistence for the day-to-date cost and energy totals (Issue #1102).

The totals are accumulated in memory on CoordinatorData by CostTracker. Without
a snapshot a Home Assistant restart starts them again from zero and the day's
figures are short until midnight.

The same dated snapshot also carries the demand-window entry record
(Issue #1142). That record is captured once, within a few minutes of the window
opening, so a restart later in the window used to leave it blank until
midnight.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from homeassistant.helpers.storage import Store

if TYPE_CHECKING:  # pragma: no cover
    from homeassistant.core import HomeAssistant

    from ..coordinator.data import CoordinatorData
    from .costs import CostTracker

_LOGGER = logging.getLogger(__name__)

# Top-level snapshot key holding the demand-window entry record, beside "date"
# and "totals". It is a new top-level key rather than more entries in "totals"
# so both directions of a version change keep working: a version without this
# code reads only "date" and "totals" and ignores the rest, and this version
# treats a snapshot without the key as "no record".
DW_ENTRY_SNAPSHOT_KEY = "dw_entry"

# The five CoordinatorData fields that make up the record. Telemetry only:
# they are written by ComputationEngine._capture_dw_entry_actual and read by
# the optimizer summary sensor and the plan log line, never by anything that
# chooses a battery mode.
DW_ENTRY_RECORD_FIELDS: tuple[str, ...] = (
    "dw_entry_actual_soc_pct",
    "dw_entry_actual_at",
    "dw_entry_actual_target_pct",
    "dw_entry_actual_shortfall_pct",
    "dw_entry_actual_date",
)
_DW_ENTRY_PERCENT_FIELDS: tuple[str, ...] = (
    "dw_entry_actual_soc_pct",
    "dw_entry_actual_target_pct",
    "dw_entry_actual_shortfall_pct",
)


def snapshot_dw_entry_record(data: CoordinatorData) -> dict[str, Any] | None:
    """Return today's demand-window entry record for persistence, or None.

    None when nothing has been recorded: before the window opens, or on a day
    whose entry was not observed. A blank record is saved as blank so that it
    restores as blank.
    """
    record_date = data.dw_entry_actual_date
    captured_at = data.dw_entry_actual_at
    if record_date is None or captured_at is None:
        return None
    record: dict[str, Any] = {
        "dw_entry_actual_date": record_date.isoformat(),
        "dw_entry_actual_at": captured_at.isoformat(),
    }
    for key in _DW_ENTRY_PERCENT_FIELDS:
        value = getattr(data, key)
        if value is None:
            return None
        record[key] = float(value)
    return record


def restore_dw_entry_record(data: CoordinatorData, record: Any, today: date) -> bool:
    """Restore the demand-window entry record captured before a restart.

    Applied only when the record is dated ``today``. The record is validated
    whole before any field is written. Setting ``dw_entry_actual_date`` to
    today is what stops a later evaluation capturing over the restored record
    or warning about it a second time: the capture returns early on that date.

    Returns:
        True if a record was restored, False if there was none or it belongs
        to another day.

    Raises:
        ValueError: the record is malformed.

    """
    if record is None:
        return False
    if not isinstance(record, dict):
        raise ValueError(f"expected a dict, got {type(record).__name__}")

    saved_date = record.get("dw_entry_actual_date")
    if not isinstance(saved_date, str):
        raise ValueError(f"missing or non-string date: {saved_date!r}")
    record_date = date.fromisoformat(saved_date)

    saved_at = record.get("dw_entry_actual_at")
    if not isinstance(saved_at, str):
        raise ValueError(f"missing or non-string timestamp: {saved_at!r}")
    captured_at = datetime.fromisoformat(saved_at)
    if captured_at.tzinfo is None:
        raise ValueError(f"timestamp has no timezone: {saved_at!r}")

    percents: dict[str, float] = {}
    for key in _DW_ENTRY_PERCENT_FIELDS:
        value = record.get(key)
        # The range test also rejects NaN and both infinities.
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not 0.0 <= value <= 100.0
        ):
            raise ValueError(f"invalid {key}: {value!r}")
        percents[key] = float(value)

    if record_date != today:
        return False

    for key, value in percents.items():
        setattr(data, key, value)
    data.dw_entry_actual_at = captured_at
    data.dw_entry_actual_date = record_date
    return True


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
            self._restore_dw_entry_record(data, snapshot, cost_tracker.totals_date)
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
        """Persist the day's totals and entry record. Never raises."""
        try:
            snapshot = cost_tracker.snapshot_daily_totals(data)
            snapshot[DW_ENTRY_SNAPSHOT_KEY] = snapshot_dw_entry_record(data)
            await self._store.async_save(snapshot)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("Failed to save daily totals: %s", exc)

    @staticmethod
    def _restore_dw_entry_record(
        data: CoordinatorData, snapshot: dict[str, Any], today: date
    ) -> None:
        """Restore the entry record from a same-day snapshot. Never raises.

        Called only once the totals have been restored, so the snapshot is
        already known to be dated today. A missing key (a snapshot written
        before Issue #1142) or a bad record leaves the record blank and costs
        nothing else: the totals are already in place.
        """
        try:
            restored = restore_dw_entry_record(
                data, snapshot.get(DW_ENTRY_SNAPSHOT_KEY), today
            )
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning(
                "Could not restore the demand-window entry record, leaving it "
                "blank: %s",
                exc,
            )
            return
        if restored:
            _LOGGER.info(
                "Restored demand-window entry record for %s: entry SOC %.1f%%, "
                "target %.0f%%, recorded at %s",
                today.isoformat(),
                data.dw_entry_actual_soc_pct,
                data.dw_entry_actual_target_pct,
                data.dw_entry_actual_at,
            )
