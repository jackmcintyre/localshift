"""Forecast history persistence and shaping for computation engine."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from ..coordinator.data import CoordinatorData

_LOGGER = logging.getLogger(__name__)


class ForecastHistoryStore:
    """Persist and shape forecast history entries."""

    def __init__(
        self,
        hass: HomeAssistant,
        store_key: str = "localshift_forecast_history",
    ) -> None:
        self._hass = hass
        self._store_key = store_key
        self._store: Any = None
        self._loaded: bool = False
        self._last_forecast_hour: int | None = None

    async def async_initialize(self) -> None:
        """Initialize forecast history storage."""
        try:
            from homeassistant.helpers.storage import Store

            self._store = Store(self._hass, 1, self._store_key)
            _LOGGER.info("Forecast history storage initialized")
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("Failed to initialize forecast history storage: %s", exc)
            self._store = None

    async def async_load(self, data: CoordinatorData) -> None:
        """Load persisted forecast history from storage."""
        if self._store is None:
            _LOGGER.debug("No forecast history store available")
            return

        try:
            stored_data = await self._store.async_load()
            if not stored_data or not isinstance(stored_data, dict):
                return

            history = stored_data.get("forecast_history", [])
            first_prediction = stored_data.get("first_prediction_time", "")

            if history:
                now_dt = dt_util.now()
                cutoff = now_dt - timedelta(hours=24)
                valid_entries = self._filter_valid_history_entries(history, cutoff)

                data.forecast_history = valid_entries
                data.forecast_first_prediction_time = first_prediction
                data.forecast_history_count = len(valid_entries)

                _LOGGER.info(
                    "Loaded %d forecast history entries from storage (filtered from %d)",
                    len(valid_entries),
                    len(history),
                )

                if not first_prediction and valid_entries:
                    self._find_first_prediction_time(data, valid_entries)

            self._loaded = True
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("Failed to load forecast history: %s", exc)

    async def async_save(self, data: CoordinatorData) -> None:
        """Persist forecast history to storage."""
        if self._store is None:
            return

        try:
            entries_to_save = [
                entry
                for entry in data.forecast_history
                if "target_time" in entry and "offset_minutes" in entry
            ]

            if len(entries_to_save) > 100:
                entries_to_save = entries_to_save[-100:]

            stored_data = {
                "forecast_history": entries_to_save,
                "first_prediction_time": data.forecast_first_prediction_time,
            }

            await self._store.async_save(stored_data)
            _LOGGER.debug("Saved %d forecast history entries", len(entries_to_save))
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("Failed to save forecast history: %s", exc)

    def store_forecast_history(self, data: CoordinatorData, now_dt: datetime) -> None:
        """Store DP optimizer predictions to in-memory history."""
        current_hour = now_dt.hour
        if (
            self._last_forecast_hour is not None
            and current_hour == self._last_forecast_hour
        ):
            return

        slots = data.optimizer_decisions or []
        if not slots:
            return

        for offset_minutes in [15, 60, 240]:
            target_dt = self._localize_datetime(
                now_dt + timedelta(minutes=offset_minutes)
            )

            for slot in slots:
                entry = self._match_slot_to_target(
                    slot, target_dt, now_dt, offset_minutes
                )
                if entry:
                    data.forecast_history.append(entry)
                    break

        if len(data.forecast_history) > 200:
            data.forecast_history = data.forecast_history[-200:]

        data.forecast_history_count = len(data.forecast_history)
        self._last_forecast_hour = current_hour

    def _filter_valid_history_entries(
        self, history: list[dict], cutoff: datetime
    ) -> list[dict]:
        """Filter history entries to only valid ones within cutoff time."""
        valid_entries = []
        for entry in history:
            if "target_time" not in entry:
                continue
            if self._is_history_entry_valid(entry, cutoff):
                valid_entries.append(entry)
        return valid_entries

    def _is_history_entry_valid(self, entry: dict, cutoff: datetime) -> bool:
        """Check if a history entry is valid and within cutoff time."""
        try:
            target_dt = datetime.fromisoformat(entry["target_time"])
            target_dt = self._localize_datetime(target_dt)
            return target_dt >= cutoff
        except (ValueError, TypeError):
            return False

    def _localize_datetime(self, dt: datetime) -> datetime:
        """Convert datetime to local timezone."""
        if dt.tzinfo is None:
            return dt_util.as_local(dt_util.as_utc(dt))
        return dt_util.as_local(dt)

    def _match_slot_to_target(
        self, slot: dict, target_dt: datetime, now_dt: datetime, offset_minutes: int
    ) -> dict | None:
        """Match a slot to target time and create history entry."""
        ts = slot.get("timestamp_iso", "")
        if not ts:
            return None

        try:
            slot_dt = datetime.fromisoformat(ts)
        except ValueError:
            return None

        slot_dt = self._localize_datetime(slot_dt)
        slot_interval = slot.get("slot_interval_minutes", 15)
        slot_end = slot_dt + timedelta(minutes=slot_interval)

        if slot_dt <= target_dt < slot_end:
            return {
                "prediction_time": now_dt.isoformat(),
                "target_time": target_dt.isoformat(),
                "offset_minutes": offset_minutes,
                "predicted_soc": slot.get("predicted_soc_pct", 0),
                "predicted_buy_price": slot.get("buy_price", 0),
                "predicted_sell_price": slot.get("sell_price", 0),
            }

        return None

    def _find_first_prediction_time(
        self, data: CoordinatorData, entries: list[dict]
    ) -> None:
        """Find first prediction time from loaded entries."""
        for entry in entries:
            if entry.get("prediction_time"):
                data.forecast_first_prediction_time = entry["prediction_time"]
                break
