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

# The CoordinatorData fields that make up the record, each with the kind of
# value it holds. This mapping is the one list save and restore both work
# from: a field added here is written, validated and restored with no other
# change. Telemetry only: the fields are written by
# ComputationEngine._capture_dw_entry_actual and read by the optimizer summary
# sensor and the plan log line, never by anything that chooses a battery mode.
_KIND_PERCENT = "percent"
_KIND_TIMESTAMP = "timestamp"
_KIND_DATE = "date"
DW_ENTRY_RECORD_FIELDS: dict[str, str] = {
    "dw_entry_actual_soc_pct": _KIND_PERCENT,
    "dw_entry_actual_at": _KIND_TIMESTAMP,
    "dw_entry_actual_target_pct": _KIND_PERCENT,
    "dw_entry_actual_shortfall_pct": _KIND_PERCENT,
    "dw_entry_actual_date": _KIND_DATE,
}
# The field that dates the record: the once-per-day latch the capture checks.
_DW_ENTRY_DATE_FIELD = "dw_entry_actual_date"


def _encode_record_value(kind: str, value: Any) -> Any:
    """Return ``value`` as JSON for the snapshot."""
    if kind == _KIND_PERCENT:
        return float(value)
    if kind in (_KIND_TIMESTAMP, _KIND_DATE):
        return value.isoformat()
    raise ValueError(f"unknown record field kind: {kind!r}")


def _decode_record_value(key: str, kind: str, value: Any) -> Any:
    """Return the snapshot ``value`` as its CoordinatorData type.

    Raises:
        ValueError: the value is missing or not valid for its kind.

    """
    if kind == _KIND_PERCENT:
        # The range test also rejects NaN and both infinities.
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not 0.0 <= value <= 100.0
        ):
            raise ValueError(f"invalid {key}: {value!r}")
        return float(value)
    if not isinstance(value, str):
        raise ValueError(f"missing or non-string {key}: {value!r}")
    if kind == _KIND_DATE:
        return date.fromisoformat(value)
    if kind == _KIND_TIMESTAMP:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            raise ValueError(f"{key} has no timezone: {value!r}")
        return parsed
    raise ValueError(f"unknown record field kind: {kind!r}")


def snapshot_dw_entry_record(data: CoordinatorData) -> dict[str, Any] | None:
    """Return today's demand-window entry record for persistence, or None.

    None when nothing has been recorded: before the window opens, or on a day
    whose entry was not observed. A blank record is saved as blank so that it
    restores as blank. The fields are set together, so one that is None means
    there is no whole record to save.
    """
    record: dict[str, Any] = {}
    for key, kind in DW_ENTRY_RECORD_FIELDS.items():
        value = getattr(data, key)
        if value is None:
            return None
        record[key] = _encode_record_value(kind, value)
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

    values = {
        key: _decode_record_value(key, kind, record.get(key))
        for key, kind in DW_ENTRY_RECORD_FIELDS.items()
    }
    record_date = values[_DW_ENTRY_DATE_FIELD]
    # A timestamp must fall on the day the record is dated. The capture takes
    # both from one clock reading, so anything else is a corrupt or
    # hand-edited record. Compared in the timestamp's own offset, which is the
    # local offset it was captured in.
    for key, kind in DW_ENTRY_RECORD_FIELDS.items():
        if kind == _KIND_TIMESTAMP and values[key].date() != record_date:
            raise ValueError(
                f"{key} {values[key].isoformat()} is not on the record's date "
                f"{record_date.isoformat()}"
            )

    if record_date != today:
        return False

    for key, value in values.items():
        setattr(data, key, value)
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
        """Persist the day's totals and entry record. Never raises.

        The entry record is built on its own: if that ever fails, the totals
        are still saved, with the record blank.
        """
        try:
            snapshot = cost_tracker.snapshot_daily_totals(data)
            try:
                record = snapshot_dw_entry_record(data)
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning(
                    "Could not save the demand-window entry record, saving the "
                    "daily totals without it: %s",
                    exc,
                )
                record = None
            snapshot[DW_ENTRY_SNAPSHOT_KEY] = record
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
