from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from ..const import (
    CHARGE_RATE_CALIBRATION_SLOTS,
    CHARGE_RATE_MAX_KW,
    CHARGE_RATE_MIN_SAMPLES,
    CHARGE_RATE_POWER_THRESHOLD_KW,
    CHARGE_RATE_SOC_BIN_STEP,
)
from ..engine.optimizer_dp import PlannerAction

_LOGGER = logging.getLogger(__name__)


@dataclass
class ChargeRateCurve:
    """Charge rate curve model keyed by SOC percent."""

    bins: dict[int, float]
    sample_count: int
    normalized_mad: float
    min_samples: int

    @classmethod
    def from_bins(
        cls,
        bins: dict[int, float],
        sample_count: int = 0,
        normalized_mad: float = 0.0,
        min_samples: int = CHARGE_RATE_MIN_SAMPLES,
    ) -> ChargeRateCurve:
        """Create a curve from SOC bins and sample metadata."""
        return cls(
            bins=bins,
            sample_count=sample_count,
            normalized_mad=normalized_mad,
            min_samples=min_samples,
        )

    def rate_at_soc(self, soc_pct: float) -> float:
        """Return interpolated charge rate at SOC percentage."""
        sorted_bins = tuple(sorted(self.bins.items()))
        if not sorted_bins:
            return 0.0

        clamped_soc = max(0.0, min(100.0, soc_pct))
        min_soc, min_rate = sorted_bins[0]
        max_soc, max_rate = sorted_bins[-1]

        if clamped_soc <= min_soc:
            rate = float(min_rate)
        elif clamped_soc >= max_soc:
            rate = float(max_rate)
        else:
            rate = float(min_rate)
            for (lower_soc, lower_rate), (upper_soc, upper_rate) in zip(
                sorted_bins, sorted_bins[1:], strict=False
            ):
                if lower_soc <= clamped_soc <= upper_soc:
                    span = upper_soc - lower_soc
                    if span == 0:
                        rate = float(lower_rate)
                    else:
                        fraction = (clamped_soc - lower_soc) / span
                        rate = float(lower_rate) + fraction * (
                            float(upper_rate) - float(lower_rate)
                        )
                    break

        return max(0.0, min(rate, CHARGE_RATE_MAX_KW))

    @property
    def confidence(self) -> float:
        """Return confidence score based on samples and dispersion."""
        if self.min_samples <= 0:
            base = 1.0
        else:
            base = min(1.0, self.sample_count / self.min_samples)
        confidence = base * max(0.0, 1.0 - self.normalized_mad)
        return max(0.0, min(1.0, confidence))


@dataclass(slots=True)
class _SlotSample:
    start: datetime
    end: datetime
    power_kw: float | None
    soc_pct: float | None


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    sorted_values = sorted(values)
    mid = len(sorted_values) // 2
    if len(sorted_values) % 2 == 0:
        return (sorted_values[mid - 1] + sorted_values[mid]) / 2
    return sorted_values[mid]


def _floor_time(timestamp: datetime, slot_minutes: int) -> datetime:
    minute = (timestamp.minute // slot_minutes) * slot_minutes
    return timestamp.replace(minute=minute, second=0, microsecond=0)


def _normalize_history(
    history: Iterable[Any] | None,
) -> list[tuple[datetime, float]]:
    if not history:
        return []
    normalized: list[tuple[datetime, float]] = []
    for entry in history:
        if isinstance(entry, tuple) and len(entry) == 2:
            timestamp, value = entry
        else:
            timestamp = getattr(entry, "last_updated", None) or getattr(
                entry, "last_changed", None
            )
            value = getattr(entry, "state", None)
        if timestamp is None or value in (None, "unknown", "unavailable"):
            continue
        try:
            normalized.append((timestamp, float(value)))
        except (TypeError, ValueError):
            continue
    normalized.sort(key=lambda item: item[0])
    return normalized


class ChargeRateLearner:
    """Learn charge-rate curves from historical power/SOC data."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        power_entity_id: str | None = None,
        soc_entity_id: str | None = None,
        slot_minutes: int = 15,
        monotonic_smoothing: bool = True,
    ) -> None:
        self.hass = hass
        self._entry_id = entry_id
        self._store = Store(
            hass,
            version=1,
            key=f"localshift.charge_rate_curves.{entry_id}",
        )
        self._power_entity_id = power_entity_id or ""
        self._soc_entity_id = soc_entity_id or ""
        self._slot_minutes = slot_minutes
        self._monotonic_smoothing = monotonic_smoothing

        self._curves: dict[str, ChargeRateCurve] = {}
        self._diagnostics: dict[str, Any] = {}
        self._updated_at: datetime | None = None

    @property
    def diagnostics(self) -> dict[str, Any]:
        diagnostics = dict(self._diagnostics)
        diagnostics["stale"] = self._is_stale()
        return diagnostics

    def _is_stale(self) -> bool:
        if self._updated_at is None:
            return True
        now = dt_util.now() or datetime.now(tz=self._updated_at.tzinfo)
        return now - self._updated_at >= timedelta(days=7)

    def get_curve(self, regime: str) -> ChargeRateCurve | None:
        curve = self._curves.get(regime)
        if curve is None:
            return None
        if curve.sample_count < curve.min_samples:
            return None
        return curve

    async def async_load(self) -> None:
        stored = await self._store.async_load()
        if not isinstance(stored, dict):
            return
        if stored.get("version") != 1:
            return

        curves_raw = stored.get("curves")
        if not isinstance(curves_raw, dict):
            return

        loaded_curves: dict[str, ChargeRateCurve] = {}
        for regime, curve_data in curves_raw.items():
            if not isinstance(curve_data, dict):
                continue
            bins = curve_data.get("bins")
            if not isinstance(bins, dict):
                continue
            sample_count = int(curve_data.get("sample_count", 0) or 0)
            normalized_mad = float(curve_data.get("normalized_mad", 0.0) or 0.0)
            min_samples = int(curve_data.get("min_samples", CHARGE_RATE_MIN_SAMPLES))
            loaded_curves[str(regime)] = ChargeRateCurve.from_bins(
                bins={int(k): float(v) for k, v in bins.items()},
                sample_count=sample_count,
                normalized_mad=normalized_mad,
                min_samples=min_samples,
            )

        updated_at = stored.get("updated_at")
        if isinstance(updated_at, str):
            try:
                self._updated_at = datetime.fromisoformat(updated_at)
            except ValueError:
                self._updated_at = None

        diagnostics = stored.get("diagnostics")
        if isinstance(diagnostics, dict):
            self._diagnostics = diagnostics

        if loaded_curves:
            self._curves = loaded_curves

    async def async_save(self) -> None:
        payload = {
            "version": 1,
            "updated_at": (self._updated_at.isoformat() if self._updated_at else None),
            "curves": {
                regime: {
                    "bins": curve.bins,
                    "sample_count": curve.sample_count,
                    "normalized_mad": curve.normalized_mad,
                    "min_samples": curve.min_samples,
                }
                for regime, curve in self._curves.items()
            },
            "diagnostics": self._diagnostics,
        }
        await self._store.async_save(payload)

    async def async_fetch_history(
        self,
    ) -> tuple[list[tuple[datetime, float]], list[tuple[datetime, float]]]:
        if not self._power_entity_id or not self._soc_entity_id:
            _LOGGER.debug("Charge rate history skipped: missing entity IDs")
            return [], []

        now = dt_util.now()
        if now is None:
            now = datetime.now()
        start = now - timedelta(days=30)

        try:
            from homeassistant.components.recorder import history as recorder_history
            from homeassistant.components.recorder import (
                statistics as recorder_statistics,
            )
        except Exception as err:
            _LOGGER.warning("Recorder history unavailable: %s", err)
            return [], []

        try:
            power_history = await self.hass.async_add_executor_job(
                recorder_statistics.statistics_during_period,
                self.hass,
                start,
                now,
                [self._power_entity_id],
                "5minute",
                {"mean"},
                None,
            )
        except Exception as err:
            _LOGGER.warning("Charge rate power history fetch failed: %s", err)
            return [], []

        try:
            soc_history = await recorder_history.get_significant_states(
                self.hass,
                start_time=start,
                end_time=now,
                entity_ids=[self._soc_entity_id],
            )
        except Exception as err:
            _LOGGER.warning("Charge rate SOC history fetch failed: %s", err)
            return [], []

        power_points: list[tuple[datetime, float]] = []
        if isinstance(power_history, dict):
            rows = power_history.get(self._power_entity_id) or []
            if isinstance(rows, list):
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    start_time = row.get("start")
                    mean_val = row.get("mean")
                    if start_time is None or mean_val in (
                        None,
                        "unknown",
                        "unavailable",
                    ):
                        continue
                    try:
                        power_points.append((start_time, float(mean_val)))
                    except (TypeError, ValueError):
                        continue

        soc_points: list[tuple[datetime, float]] = []
        if isinstance(soc_history, dict):
            for state in soc_history.get(self._soc_entity_id, []):
                timestamp = getattr(state, "last_updated", None)
                value = getattr(state, "state", None)
                if timestamp is None or value in (None, "unknown", "unavailable"):
                    continue
                try:
                    soc_points.append((timestamp, float(value)))
                except (TypeError, ValueError):
                    continue

        power_points.sort(key=lambda item: item[0])
        soc_points.sort(key=lambda item: item[0])
        return power_points, soc_points

    def update_from_history(
        self,
        power_history: Iterable[Any] | None,
        soc_history: Iterable[Any] | None,
        decisions: Iterable[Any] | None,
    ) -> bool:
        power_points = _normalize_history(power_history)
        soc_points = _normalize_history(soc_history)
        if not power_points or not soc_points:
            self._diagnostics = {
                "labeled_sample_ratio": 0.0,
                "missing_history": {
                    "power": 0 if power_points else 1,
                    "soc": 0 if soc_points else 1,
                },
                "decision_mismatch": 0,
                "missing_decisions": 0,
                "power_sign_inverted": False,
            }
            return False

        slot_delta = timedelta(minutes=self._slot_minutes)
        start_time = _floor_time(
            min(power_points[0][0], soc_points[0][0]), self._slot_minutes
        )
        end_time = max(power_points[-1][0], soc_points[-1][0])

        slot_samples: list[_SlotSample] = []
        power_index = 0
        soc_index = 0
        last_soc: float | None = None
        missing_power = 0
        missing_soc = 0

        slot_start = start_time
        while slot_start < end_time:
            slot_end = slot_start + slot_delta
            values: list[float] = []
            while (
                power_index < len(power_points)
                and power_points[power_index][0] < slot_end
            ):
                if power_points[power_index][0] >= slot_start:
                    values.append(power_points[power_index][1])
                power_index += 1

            power_mean = sum(values) / len(values) if values else None

            soc_updated = False
            while soc_index < len(soc_points) and soc_points[soc_index][0] <= slot_end:
                if soc_points[soc_index][0] >= slot_start:
                    soc_updated = True
                last_soc = soc_points[soc_index][1]
                soc_index += 1

            soc_value = last_soc
            if power_mean is None:
                missing_power += 1
            if soc_value is None or not soc_updated:
                missing_soc += 1

            slot_samples.append(
                _SlotSample(
                    start=slot_start,
                    end=slot_end,
                    power_kw=power_mean,
                    soc_pct=soc_value,
                )
            )
            slot_start = slot_end

        decisions_sorted = []
        if decisions:
            for decision in decisions:
                timestamp = getattr(decision, "timestamp", None)
                mode = getattr(decision, "mode_chosen", None)
                if timestamp is None or mode is None:
                    continue
                decisions_sorted.append((timestamp, mode))
        decisions_sorted.sort(key=lambda item: item[0])

        power_sign_inverted = self._calibrate_power_sign(slot_samples)
        if power_sign_inverted:
            for sample in slot_samples:
                if sample.power_kw is not None:
                    sample.power_kw *= -1

        regimes: dict[str, list[tuple[float, float]]] = {"normal": [], "boost": []}
        decision_index = 0
        labeled_slots = 0
        missing_decisions = 0
        decision_mismatch = 0

        for sample in slot_samples:
            if sample.power_kw is None or sample.soc_pct is None:
                continue

            decision = None
            while (
                decision_index < len(decisions_sorted)
                and decisions_sorted[decision_index][0] < sample.start
            ):
                decision_index += 1
            if decision_index < len(decisions_sorted):
                timestamp, mode = decisions_sorted[decision_index]
                if sample.start <= timestamp < sample.end:
                    decision = mode
                    decision_index += 1

            if decision is None:
                missing_decisions += 1
                continue

            regime = self._label_regime(decision)
            if regime is None:
                continue

            labeled_slots += 1
            if sample.power_kw < CHARGE_RATE_POWER_THRESHOLD_KW:
                decision_mismatch += 1
                continue

            regimes[regime].append((sample.soc_pct, sample.power_kw))

        curves: dict[str, ChargeRateCurve] = {}
        regime_diagnostics: dict[str, Any] = {}
        for regime, samples in regimes.items():
            if not samples:
                regime_diagnostics[regime] = {"sample_count": 0}
                continue

            curve, diagnostics = self._build_curve(samples)
            regime_diagnostics[regime] = diagnostics
            if curve is not None:
                curves[regime] = curve

        total_slots = len(slot_samples)
        labeled_ratio = labeled_slots / total_slots if total_slots else 0.0

        self._diagnostics = {
            "updated_at": (dt_util.now() or datetime.now()).isoformat(),
            "total_slots": total_slots,
            "labeled_slots": labeled_slots,
            "labeled_sample_ratio": labeled_ratio,
            "missing_history": {"power": missing_power, "soc": missing_soc},
            "missing_decisions": missing_decisions,
            "decision_mismatch": decision_mismatch,
            "power_sign_inverted": power_sign_inverted,
            "regimes": regime_diagnostics,
        }
        self._updated_at = dt_util.now() or datetime.now()
        self._curves = curves

        return labeled_slots > 0

    def _label_regime(self, decision: Any) -> str | None:
        try:
            mode_value = (
                decision
                if isinstance(decision, PlannerAction)
                else PlannerAction(str(decision))
            )
        except ValueError:
            return None

        if mode_value == PlannerAction.CHARGE_GRID_BOOST:
            return "boost"
        if mode_value == PlannerAction.CHARGE_GRID_NORMAL:
            return "normal"
        return None

    def _calibrate_power_sign(self, samples: list[_SlotSample]) -> bool:
        consecutive_mismatch = 0
        checked = 0
        prev_soc: float | None = None
        for sample in samples:
            if checked >= CHARGE_RATE_CALIBRATION_SLOTS:
                break
            if sample.power_kw is None or sample.soc_pct is None:
                prev_soc = sample.soc_pct
                continue
            if prev_soc is None:
                prev_soc = sample.soc_pct
                continue
            delta = sample.soc_pct - prev_soc
            prev_soc = sample.soc_pct
            if abs(sample.power_kw) < CHARGE_RATE_POWER_THRESHOLD_KW:
                continue
            if abs(delta) < 0.001:
                continue
            checked += 1
            power_sign = 1 if sample.power_kw >= 0 else -1
            soc_sign = 1 if delta >= 0 else -1
            if power_sign != soc_sign:
                consecutive_mismatch += 1
                if consecutive_mismatch > 3:
                    return True
            else:
                consecutive_mismatch = 0
        return False

    def _build_curve(
        self, samples: list[tuple[float, float]]
    ) -> tuple[ChargeRateCurve | None, dict[str, Any]]:
        rates = [rate for _, rate in samples]
        if not rates:
            return None, {"sample_count": 0}

        trim = int(len(rates) * 0.02)
        if trim > 0 and len(rates) > trim * 2:
            sorted_rates = sorted(rates)
            low = sorted_rates[trim]
            high = sorted_rates[-trim - 1]
            filtered = [(soc, rate) for soc, rate in samples if low <= rate <= high]
        else:
            filtered = samples

        if not filtered:
            return None, {"sample_count": 0}

        trimmed_rates = [rate for _, rate in filtered]
        median = _median(trimmed_rates)
        mad = _median([abs(rate - median) for rate in trimmed_rates])
        normalized_mad = mad / median if median > 0 else 0.0

        bins: dict[int, list[float]] = {}
        for soc, rate in filtered:
            bin_key = int(
                (max(0.0, min(100.0, soc)) // CHARGE_RATE_SOC_BIN_STEP)
                * CHARGE_RATE_SOC_BIN_STEP
            )
            bins.setdefault(bin_key, []).append(rate)

        averaged_bins: dict[int, float] = {
            key: sum(values) / len(values) for key, values in bins.items() if values
        }

        if self._monotonic_smoothing and averaged_bins:
            smoothed: dict[int, float] = {}
            last_rate: float | None = None
            for soc_bin in sorted(averaged_bins.keys()):
                rate = averaged_bins[soc_bin]
                if last_rate is not None and rate > last_rate:
                    rate = last_rate
                smoothed[soc_bin] = rate
                last_rate = rate
            averaged_bins = smoothed

        sample_count = len(trimmed_rates)
        curve = ChargeRateCurve.from_bins(
            averaged_bins,
            sample_count=sample_count,
            normalized_mad=normalized_mad,
            min_samples=CHARGE_RATE_MIN_SAMPLES,
        )

        diagnostics = {
            "sample_count": sample_count,
            "normalized_mad": normalized_mad,
            "trimmed_count": len(trimmed_rates),
            "bins": len(averaged_bins),
        }

        if sample_count < CHARGE_RATE_MIN_SAMPLES:
            return None, diagnostics

        return curve, diagnostics
