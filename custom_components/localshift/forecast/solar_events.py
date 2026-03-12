from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

SOLCAST_PERIOD_MINUTES = 30
MIN_FORECAST_KW = 0.3
ONSET_RATIO = 0.50
SEVERE_RATIO = 0.25
CLEARING_RATIO = 1.20
ONSET_DURATION = timedelta(minutes=10)
CLEARING_DURATION = timedelta(minutes=10)
DEFAULT_COOLDOWN = timedelta(minutes=15)
MAX_CLOUD_SAMPLES = 120


class SolarEventDetector:
    def __init__(self, cooldown: timedelta = DEFAULT_COOLDOWN) -> None:
        self._cooldown = cooldown
        self._onset_started_at: datetime | None = None
        self._clearing_started_at: datetime | None = None
        self._in_cloud_event: bool = False
        self._cloud_samples: list[float] = []
        self._last_triggered_at: datetime | None = None

    def evaluate(self, data: Any, now: datetime) -> bool:
        if not self._runtime_is_active(data):
            self._reset_all()
            self._write_diagnostics(
                data,
                status="inactive",
                triggered=False,
                event_type=None,
                actual_kw=float(getattr(data, "solar_power_kw", 0.0) or 0.0),
                forecast_kw=None,
                ratio=None,
            )
            return False

        actual_kw = float(getattr(data, "solar_power_kw", 0.0) or 0.0)

        if self._in_cloud_event:
            forecast_kw = self._current_solcast_kw(data, now)
            ratio = (
                actual_kw / forecast_kw
                if forecast_kw is not None and forecast_kw >= MIN_FORECAST_KW
                else None
            )
            return self._handle_cloud_event(data, now, actual_kw, forecast_kw, ratio)

        forecast_kw = self._current_solcast_kw(data, now)

        if forecast_kw is None or forecast_kw < MIN_FORECAST_KW:
            self._reset_onset_window()
            self._write_diagnostics(
                data,
                status="no_forecast",
                triggered=False,
                event_type=None,
                actual_kw=actual_kw,
                forecast_kw=forecast_kw,
                ratio=None,
            )
            return False

        ratio = actual_kw / forecast_kw
        return self._handle_onset_detection(data, now, actual_kw, forecast_kw, ratio)

    def _handle_cloud_event(
        self,
        data: Any,
        now: datetime,
        actual_kw: float,
        forecast_kw: float | None,
        ratio: float | None,
    ) -> bool:
        depressed_avg = self._depressed_average()
        clearing_active = (
            depressed_avg > 0 and actual_kw > depressed_avg * CLEARING_RATIO
        )

        if clearing_active:
            if self._clearing_started_at is None:
                self._clearing_started_at = now

            elapsed = now - self._clearing_started_at
            if elapsed > CLEARING_DURATION:
                self._in_cloud_event = False
                self._cloud_samples = []
                self._clearing_started_at = None
                self._onset_started_at = None
                self._last_triggered_at = now
                data.cloud_event_solar_scale_factor = None
                self._write_diagnostics(
                    data,
                    status="triggered",
                    triggered=True,
                    event_type="clearing",
                    actual_kw=actual_kw,
                    forecast_kw=forecast_kw,
                    ratio=ratio,
                    depressed_avg_kw=depressed_avg,
                )
                return True

            self._write_diagnostics(
                data,
                status="clearing_pending",
                triggered=False,
                event_type=None,
                actual_kw=actual_kw,
                forecast_kw=forecast_kw,
                ratio=ratio,
                depressed_avg_kw=depressed_avg,
            )
            return False

        self._clearing_started_at = None
        self._cloud_samples.append(actual_kw)
        if len(self._cloud_samples) > MAX_CLOUD_SAMPLES:
            self._cloud_samples = self._cloud_samples[-MAX_CLOUD_SAMPLES:]

        self._write_diagnostics(
            data,
            status="cloud_event",
            triggered=False,
            event_type=None,
            actual_kw=actual_kw,
            forecast_kw=forecast_kw,
            ratio=ratio,
            depressed_avg_kw=self._depressed_average(),
        )
        return False

    def _handle_onset_detection(
        self,
        data: Any,
        now: datetime,
        actual_kw: float,
        forecast_kw: float,
        ratio: float,
    ) -> bool:
        cooldown_remaining = self._cooldown_remaining_seconds(now)
        if cooldown_remaining > 0:
            self._reset_onset_window()
            self._write_diagnostics(
                data,
                status="cooldown",
                triggered=False,
                event_type=None,
                actual_kw=actual_kw,
                forecast_kw=forecast_kw,
                ratio=ratio,
                cooldown_remaining_seconds=cooldown_remaining,
            )
            return False

        onset_active = ratio < ONSET_RATIO
        severe_active = ratio < SEVERE_RATIO

        if not onset_active:
            self._reset_onset_window()
            self._write_diagnostics(
                data,
                status="normal",
                triggered=False,
                event_type=None,
                actual_kw=actual_kw,
                forecast_kw=forecast_kw,
                ratio=ratio,
            )
            return False

        if severe_active:
            self._onset_started_at = None
            self._in_cloud_event = True
            self._cloud_samples = [actual_kw]
            self._clearing_started_at = None
            self._last_triggered_at = now
            data.cloud_event_solar_scale_factor = ratio
            self._write_diagnostics(
                data,
                status="triggered",
                triggered=True,
                event_type="onset_severe",
                actual_kw=actual_kw,
                forecast_kw=forecast_kw,
                ratio=ratio,
            )
            return True

        if self._onset_started_at is None:
            self._onset_started_at = now

        elapsed = now - self._onset_started_at
        if elapsed > ONSET_DURATION:
            self._onset_started_at = None
            self._in_cloud_event = True
            self._cloud_samples = [actual_kw]
            self._clearing_started_at = None
            self._last_triggered_at = now
            data.cloud_event_solar_scale_factor = ratio
            self._write_diagnostics(
                data,
                status="triggered",
                triggered=True,
                event_type="onset_moderate",
                actual_kw=actual_kw,
                forecast_kw=forecast_kw,
                ratio=ratio,
            )
            return True

        self._write_diagnostics(
            data,
            status="onset_pending",
            triggered=False,
            event_type=None,
            actual_kw=actual_kw,
            forecast_kw=forecast_kw,
            ratio=ratio,
        )
        return False

    def _current_solcast_kw(self, data: Any, now: datetime) -> float | None:
        entries = list(getattr(data, "solcast_today", []) or [])
        entries += list(getattr(data, "solcast_tomorrow", []) or [])
        period_delta = timedelta(minutes=SOLCAST_PERIOD_MINUTES)

        for entry in entries:
            if not isinstance(entry, dict):
                continue
            period_end_str = entry.get("period_end")
            if not period_end_str:
                continue
            try:
                period_end = datetime.fromisoformat(str(period_end_str))
            except (ValueError, TypeError):
                continue

            period_start = period_end - period_delta
            if period_start <= now < period_end:
                pv = (
                    entry.get("pv_estimate")
                    or entry.get("estimate")
                    or entry.get("pv_estimate10")
                    or entry.get("estimate10")
                    or 0.0
                )
                return float(pv)
        return None

    def _depressed_average(self) -> float:
        if not self._cloud_samples:
            return 0.0
        return sum(self._cloud_samples) / len(self._cloud_samples)

    def _cooldown_remaining_seconds(self, now: datetime) -> int:
        if self._last_triggered_at is None:
            return 0
        remaining = self._cooldown - (now - self._last_triggered_at)
        return max(0, int(remaining.total_seconds()))

    def _runtime_is_active(self, data: Any) -> bool:
        return (
            getattr(data, "optimizer_last_apply_status", "") == "ready_to_apply"
            and getattr(data, "optimizer_apply_plan", None) is not None
        )

    def _reset_onset_window(self) -> None:
        self._onset_started_at = None

    def _reset_all(self) -> None:
        self._onset_started_at = None
        self._clearing_started_at = None
        self._in_cloud_event = False
        self._cloud_samples = []

    def _write_diagnostics(
        self,
        data: Any,
        *,
        status: str,
        triggered: bool,
        event_type: str | None,
        actual_kw: float,
        forecast_kw: float | None,
        ratio: float | None,
        depressed_avg_kw: float | None = None,
        cooldown_remaining_seconds: int = 0,
    ) -> None:
        cooldown_until = None
        if self._last_triggered_at is not None:
            cooldown_until = (self._last_triggered_at + self._cooldown).isoformat()

        data.cloud_event_diagnostics = {
            "status": status,
            "triggered": triggered,
            "event_type": event_type,
            "actual_kw": round(actual_kw, 3),
            "forecast_kw": round(forecast_kw, 3) if forecast_kw is not None else None,
            "ratio": round(ratio, 4) if ratio is not None else None,
            "cloud_scale_factor": getattr(data, "cloud_event_solar_scale_factor", None),
            "depressed_avg_kw": round(depressed_avg_kw, 3)
            if depressed_avg_kw is not None
            else None,
            "onset_started_at": self._onset_started_at.isoformat()
            if self._onset_started_at
            else None,
            "clearing_started_at": self._clearing_started_at.isoformat()
            if self._clearing_started_at
            else None,
            "last_triggered_at": self._last_triggered_at.isoformat()
            if self._last_triggered_at
            else None,
            "cooldown_until": cooldown_until,
            "cooldown_remaining_seconds": cooldown_remaining_seconds,
        }
