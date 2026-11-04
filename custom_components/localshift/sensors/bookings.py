"""Bookings sensor (issue #1237): the start the house reads for each booked load."""

from __future__ import annotations

from typing import Any

from homeassistant.util import dt as dt_util

from .base import LocalShiftSensorBase


class BookingsSensor(LocalShiftSensorBase):
    _attr_unique_id = "localshift_bookings"
    _attr_name = "Bookings"
    _attr_icon = "mdi:washing-machine"
    _unrecorded_attributes = frozenset({"bookings"})

    def _live(self) -> list[dict[str, Any]]:
        store = self.coordinator.bookings
        return store.as_list(dt_util.utcnow()) if store else []

    def _update_from_coordinator(self) -> None:
        self._attr_native_value = sum(
            b["status"] in ("booked", "started") for b in self._live()
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"bookings": self._live()}
