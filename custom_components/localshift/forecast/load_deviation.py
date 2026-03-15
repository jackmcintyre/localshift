from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from ..engine.optimizer_runner import _find_current_slot_index


class LoadDeviationDetector:
    def __init__(
        self,
        *,
        sustained_threshold_kw: float = 1.0,
        sustained_duration: timedelta = timedelta(minutes=10),
        spike_threshold_kw: float = 3.0,
        spike_duration: timedelta = timedelta(minutes=5),
        cooldown: timedelta = timedelta(minutes=15),
    ) -> None:
        self._sustained_threshold_kw = sustained_threshold_kw
        self._sustained_duration = sustained_duration
        self._spike_threshold_kw = spike_threshold_kw
        self._spike_duration = spike_duration
        self._cooldown = cooldown
        self._sustained_started_at: datetime | None = None
        self._spike_started_at: datetime | None = None
        self._last_triggered_at: datetime | None = None

    def evaluate(self, data: Any, now: datetime) -> bool:
        if not self._runtime_is_active(data):
            self._reset_windows()
            self._write_diagnostics(
                data,
                status="inactive",
                triggered=False,
                breach_type=None,
                actual_kw=float(getattr(data, "load_power_kw", 0.0) or 0.0),
                forecast_kw=None,
                deviation_kw=0.0,
                current_slot_index=None,
                cooldown_remaining_seconds=0,
            )
            return False

        current_slot_index = _find_current_slot_index(data)
        forecast_kw = self._current_slot_forecast(data, current_slot_index)
        actual_kw = float(getattr(data, "load_power_kw", 0.0) or 0.0)

        if forecast_kw is None:
            self._reset_windows()
            self._write_diagnostics(
                data,
                status="no_current_slot",
                triggered=False,
                breach_type=None,
                actual_kw=actual_kw,
                forecast_kw=None,
                deviation_kw=0.0,
                current_slot_index=None,
                cooldown_remaining_seconds=0,
            )
            return False

        deviation_kw = abs(actual_kw - forecast_kw)
        sustained_active = deviation_kw > self._sustained_threshold_kw
        spike_active = deviation_kw > self._spike_threshold_kw
        self._update_window("sustained", sustained_active, now)
        self._update_window("spike", spike_active, now)

        cooldown_remaining = self._cooldown_remaining_seconds(now)
        if cooldown_remaining > 0:
            self._reset_windows()
            self._write_diagnostics(
                data,
                status="cooldown",
                triggered=False,
                breach_type=self._breach_type(sustained_active, spike_active),
                actual_kw=actual_kw,
                forecast_kw=forecast_kw,
                deviation_kw=deviation_kw,
                current_slot_index=current_slot_index,
                cooldown_remaining_seconds=cooldown_remaining,
            )
            return False

        triggered_breach = self._triggered_breach(now)
        if triggered_breach is not None:
            self._last_triggered_at = now
            self._reset_windows()
            self._write_diagnostics(
                data,
                status="triggered",
                triggered=True,
                breach_type=triggered_breach,
                actual_kw=actual_kw,
                forecast_kw=forecast_kw,
                deviation_kw=deviation_kw,
                current_slot_index=current_slot_index,
                cooldown_remaining_seconds=int(self._cooldown.total_seconds()),
            )
            return True

        self._write_diagnostics(
            data,
            status=self._status(sustained_active, spike_active),
            triggered=False,
            breach_type=self._breach_type(sustained_active, spike_active),
            actual_kw=actual_kw,
            forecast_kw=forecast_kw,
            deviation_kw=deviation_kw,
            current_slot_index=current_slot_index,
            cooldown_remaining_seconds=0,
        )
        return False

    def _runtime_is_active(self, data: Any) -> bool:
        return (
            getattr(data, "optimizer_last_apply_status", "") == "ready_to_apply"
            and getattr(data, "optimizer_apply_plan", None) is not None
        )

    def _current_slot_forecast(
        self, data: Any, current_slot_index: int
    ) -> float | None:
        load_forecast_slots = getattr(data, "load_forecast_slots", []) or []
        if not load_forecast_slots:
            return None

        decisions = getattr(data, "optimizer_decisions", []) or []
        if not decisions or current_slot_index < 0:
            return None

        try:
            base_slot = datetime.fromisoformat(decisions[0]["timestamp_iso"])
            active_slot = datetime.fromisoformat(
                decisions[current_slot_index]["timestamp_iso"]
            )
        except (KeyError, ValueError, TypeError):
            return None

        elapsed_min = (active_slot - base_slot).total_seconds() / 60.0
        max_forecast_index = len(load_forecast_slots) - 1
        mapped_idx = max(0, min(int(elapsed_min // 15), max_forecast_index))

        if mapped_idx < 0 or mapped_idx >= len(load_forecast_slots):
            return None
        return float(load_forecast_slots[mapped_idx])

    def _update_window(
        self, breach_name: str, breach_active: bool, now: datetime
    ) -> None:
        started_at_attr = f"_{breach_name}_started_at"
        if breach_active:
            if getattr(self, started_at_attr) is None:
                setattr(self, started_at_attr, now)
            return
        setattr(self, started_at_attr, None)

    def _triggered_breach(self, now: datetime) -> str | None:
        if (
            self._spike_started_at is not None
            and now - self._spike_started_at > self._spike_duration
        ):
            return "spike"
        if (
            self._sustained_started_at is not None
            and now - self._sustained_started_at > self._sustained_duration
        ):
            return "sustained"
        return None

    def _cooldown_remaining_seconds(self, now: datetime) -> int:
        if self._last_triggered_at is None:
            return 0
        remaining = self._cooldown - (now - self._last_triggered_at)
        return max(0, int(remaining.total_seconds()))

    def _status(self, sustained_active: bool, spike_active: bool) -> str:
        if spike_active:
            return "spike_pending"
        if sustained_active:
            return "sustained_pending"
        return "normal"

    def _breach_type(self, sustained_active: bool, spike_active: bool) -> str | None:
        if spike_active:
            return "spike"
        if sustained_active:
            return "sustained"
        return None

    def _reset_windows(self) -> None:
        self._sustained_started_at = None
        self._spike_started_at = None

    def _write_diagnostics(
        self,
        data: Any,
        *,
        status: str,
        triggered: bool,
        breach_type: str | None,
        actual_kw: float,
        forecast_kw: float | None,
        deviation_kw: float,
        current_slot_index: int | None,
        cooldown_remaining_seconds: int,
    ) -> None:
        cooldown_until = None
        if self._last_triggered_at is not None:
            cooldown_until = self._serialize_timestamp(
                self._last_triggered_at + self._cooldown
            )

        data.load_deviation_diagnostics = {
            "status": status,
            "triggered": triggered,
            "breach_type": breach_type,
            "deviation_kw": round(deviation_kw, 3),
            "actual_kw": round(actual_kw, 3),
            "forecast_kw": round(forecast_kw, 3) if forecast_kw is not None else None,
            "current_slot_index": current_slot_index,
            "sustained_started_at": self._serialize_timestamp(
                self._sustained_started_at
            ),
            "spike_started_at": self._serialize_timestamp(self._spike_started_at),
            "last_triggered_at": self._serialize_timestamp(self._last_triggered_at),
            "cooldown_until": cooldown_until,
            "cooldown_remaining_seconds": cooldown_remaining_seconds,
        }

    def _serialize_timestamp(self, value: datetime | None) -> str | None:
        return value.isoformat() if value is not None else None
