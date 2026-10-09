"""SlotBuilder — construct SlotContext list from raw CoordinatorData.

This module builds SlotContext objects directly from raw coordinator data fields,
serving as the input preparation layer for the DP optimizer.

Design principles:
- Read raw data: general_forecast, feed_in_forecast, solcast_today/tomorrow, load_forecast_slots
- Apply adaptive param transforms: solar_confidence_factor to solar_kwh
- DO NOT apply consumption_forecast_bias (already applied by LoadForecaster)
- Compute demand window flags independently
- Return typed SlotBuildMetadata for diagnostics

``apply_price_block_flags`` is the one place those flags are rewritten after the
build: with ``switch.localshift_price_block_target`` on, the price block replaces
the clock window as the thing that sets them (docs/PRICE_BLOCK_TARGET.md).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from ..coordinator.data import AdaptiveParameters
from ..forecast.solar import get_solar_for_slot_by_interval
from ..forecast.solar_accuracy import SolarAccuracyTracker
from ..utils.export_availability import export_available_at
from .optimizer_dp import SlotContext
from .price_calculator import get_price_for_slot_or_none
from .slot_schedule import compute_hybrid_slot_schedule
from .target_block import TargetBlock, find_target_block
from .types import OptimizerConfig

_LOGGER = logging.getLogger(__name__)


@dataclass
class SlotBuildMetadata:
    """Diagnostics from a SlotBuilder.build_slots() call.

    Replaces the legacy parity_info dict with a typed dataclass.
    Provides backward-compatible to_parity_dict() for existing callers.
    """

    total_slots: int
    """Total number of slots built."""

    five_min_slots: int
    """Count of 5-minute duration slots."""

    thirty_min_slots: int
    """Count of 30-minute duration slots."""

    horizon_hours: float
    """Forecast horizon in hours."""

    solar_confidence_factor: float
    """Adaptive parameter value actually applied to solar forecasts."""

    slots_with_defaulted_solar: int
    """Slots where solcast returned 0.0 kWh."""

    slots_with_defaulted_price: int
    """Slots where buy or sell price was 0.0 $/kWh."""

    slots_with_defaulted_consumption: int = 0
    """Slots where load_forecast_slots was unavailable (fallback to 0.0)."""

    all_solcast: list[dict[str, Any]] = field(default_factory=list)
    """Full solar forecast (today + tomorrow) for penalty calculation (Issue #607)."""

    slot0_price_source: str = "unknown"
    """slots[0].price_source ("forecast_current", "synthetic", "5min", "30min")
    from the just-built hybrid schedule, or "unknown" when no slots were built.
    Read by OptimizerFacade.run_inline to feed the synthetic-slot-0 rolling
    rate tracker (Issue #956)."""

    def to_parity_dict(self) -> dict[str, Any]:
        """Convert to legacy parity_info dict format for backward compatibility.

        Returns dict matching the old _build_slot_contexts() return schema.
        """
        total_fields = (
            self.total_slots * 4
        )  # buy_price, sell_price, solar_kwh, consumption_kwh
        defaulted_count = (
            self.slots_with_defaulted_solar
            + self.slots_with_defaulted_price
            + self.slots_with_defaulted_consumption
        )
        populated_fields = total_fields - defaulted_count
        completeness_pct = (
            (populated_fields / total_fields * 100) if total_fields > 0 else 0.0
        )

        return {
            "total_slots": self.total_slots,
            "total_fields_checked": total_fields,
            "populated_fields": populated_fields,
            "defaulted_fields": {
                "solar_kwh": self.slots_with_defaulted_solar,
                "price": self.slots_with_defaulted_price,
                "consumption_kwh": self.slots_with_defaulted_consumption,
            },
            "completeness_pct": round(completeness_pct, 1),
            "five_min_slots": self.five_min_slots,
            "thirty_min_slots": self.thirty_min_slots,
            "horizon_hours": self.horizon_hours,
            "solar_confidence_factor": self.solar_confidence_factor,
        }


class SlotBuilder:
    """Builds SlotContext list from raw CoordinatorData without touching daily_forecast.

    Reads:
    - data.general_forecast / data.feed_in_forecast — Amber buy/sell price series
    - data.solcast_today / data.solcast_tomorrow — solar forecast
    - data.load_forecast_slots — per-slot load kW (from Phase 1 LoadForecaster)
    - DW config options — demand window entry/slot flags per slot

    Applies:
    - solar_confidence_factor: solar_kwh *= factor (clamped >= 0)
    - consumption_forecast_bias: already applied by LoadForecaster; not re-applied here

    Usage:
        slot_builder = SlotBuilder(config_options, ha_timezone)
        slots, metadata = slot_builder.build_slots(data, adaptive_params)
    """

    def __init__(
        self,
        config_options: dict[str, Any],
        ha_timezone: str,
        solar_accuracy_tracker: SolarAccuracyTracker | None = None,
    ) -> None:
        """Store DW time config and timezone for slot generation.

        Args:
            config_options: Integration config options (for DW start/end parsing).
            ha_timezone: Home Assistant timezone string (e.g., "Australia/Sydney").
            solar_accuracy_tracker: Optional tracker for bias correction readiness check.

        """
        self._config_options = config_options
        self._ha_timezone = ha_timezone
        self._solar_accuracy_tracker = solar_accuracy_tracker

    def build_slots(
        self,
        data: Any,
        adaptive_params: AdaptiveParameters | None,
        now_dt: datetime | None = None,
        override_general_forecast: list[dict[str, Any]] | None = None,
        override_feed_in_forecast: list[dict[str, Any]] | None = None,
    ) -> tuple[list[SlotContext], SlotBuildMetadata]:
        """Build SlotContext list from raw coordinator data.

        Args:
            data: CoordinatorData with prices, forecasts, etc.
            adaptive_params: Adaptive parameters for solar confidence.
            now_dt: Current datetime (optional, defaults to now).
            override_general_forecast: Shadow forecast to use instead of data.general_forecast.
            override_feed_in_forecast: Shadow forecast to use instead of data.feed_in_forecast.

        """
        now_local = (
            now_dt.astimezone() if now_dt is not None else datetime.now().astimezone()
        )

        # Use override forecasts if provided (for shadow optimizer runs)
        general_forecast = (
            override_general_forecast
            if override_general_forecast is not None
            else data.general_forecast
        )
        feed_in_forecast = (
            override_feed_in_forecast
            if override_feed_in_forecast is not None
            else data.feed_in_forecast
        )

        hybrid_slots, hybrid_metadata = compute_hybrid_slot_schedule(
            now_local=now_local,
            general_forecast=general_forecast,
            ha_timezone=self._ha_timezone,
        )

        dw_start_time = self._parse_time_option("demand_window_start")
        dw_end_time = self._parse_time_option("demand_window_end")

        solar_confidence_factor = self._get_solar_confidence_factor(adaptive_params)
        all_solcast = [*data.solcast_today, *data.solcast_tomorrow]
        base_slot = self._compute_base_slot(now_local)
        local_tz = self._get_local_timezone()

        # Create ConfidenceResolver for cross-day confidence lookup
        from custom_components.localshift.forecast.analysis_resolver import (
            ConfidenceResolver,
        )

        resolver = ConfidenceResolver(
            getattr(data, "solcast_analysis_today", None),
            getattr(data, "solcast_analysis_tomorrow", None),
            absent_confidence=getattr(data, "solar_absent_confidence", 1.0),
        )

        contexts, counts = self._process_all_slots(
            hybrid_slots=hybrid_slots,
            data=data,
            all_solcast=all_solcast,
            solar_confidence_factor=solar_confidence_factor,
            base_slot=base_slot,
            local_tz=local_tz,
            dw_start_time=dw_start_time,
            dw_end_time=dw_end_time,
            feed_in_forecast=feed_in_forecast,
            resolver=resolver,
        )

        metadata = SlotBuildMetadata(
            total_slots=len(contexts),
            five_min_slots=counts["five_min"],
            thirty_min_slots=counts["thirty_min"],
            horizon_hours=hybrid_metadata.get("horizon_hours", 24.0),
            solar_confidence_factor=solar_confidence_factor,
            slots_with_defaulted_solar=counts["defaulted_solar"],
            slots_with_defaulted_price=counts["defaulted_price"],
            slots_with_defaulted_consumption=counts["defaulted_consumption"],
            all_solcast=all_solcast,
            slot0_price_source=(contexts[0].price_source if contexts else "unknown"),
        )

        _LOGGER.debug(
            "SlotBuilder: built %d slots (%d x 5min, %d x 30min), "
            "solar_confidence=%.2f, defaulted: solar=%d price=%d consumption=%d",
            len(contexts),
            counts["five_min"],
            counts["thirty_min"],
            solar_confidence_factor,
            counts["defaulted_solar"],
            counts["defaulted_price"],
            counts["defaulted_consumption"],
        )

        return contexts, metadata

    def _get_solar_confidence_factor(
        self, adaptive_params: AdaptiveParameters | None
    ) -> float:
        """Extract and clamp solar_confidence_factor from adaptive params."""
        factor = 1.0
        if adaptive_params is not None:
            factor = adaptive_params.get("solar_confidence_factor", 1.0)
        return max(0.0, min(2.0, factor))

    def _compute_base_slot(self, now_local: datetime) -> datetime:
        """Compute base slot time for load_forecast_slots indexing."""
        current_5min = (now_local.minute // 5) * 5
        return now_local.replace(minute=current_5min, second=0, microsecond=0)

    def _get_local_timezone(self) -> ZoneInfo | None:
        """Resolve local timezone object for DW time comparisons."""
        try:
            return ZoneInfo(self._ha_timezone)
        except Exception:
            return None

    def _process_all_slots(
        self,
        hybrid_slots: list[dict[str, Any]],
        data: Any,
        all_solcast: list[dict[str, Any]],
        solar_confidence_factor: float,
        base_slot: datetime,
        local_tz: ZoneInfo | None,
        dw_start_time: time,
        dw_end_time: time,
        feed_in_forecast: list[dict[str, Any]] | None = None,
        resolver: Any | None = None,
    ) -> tuple[list[SlotContext], dict[str, int]]:
        """Process all hybrid slots and return contexts with counts."""
        contexts: list[SlotContext] = []
        counts = {
            "five_min": 0,
            "thirty_min": 0,
            "defaulted_solar": 0,
            "defaulted_price": 0,
            "defaulted_consumption": 0,
        }
        prev_in_demand_window = False

        for i, slot in enumerate(hybrid_slots):
            ctx, slot_counts, in_demand_window = self._process_single_slot(
                i=i,
                slot=slot,
                data=data,
                all_solcast=all_solcast,
                solar_confidence_factor=solar_confidence_factor,
                base_slot=base_slot,
                local_tz=local_tz,
                dw_start_time=dw_start_time,
                dw_end_time=dw_end_time,
                prev_in_demand_window=prev_in_demand_window,
                feed_in_forecast=feed_in_forecast,
                resolver=resolver,
            )
            contexts.append(ctx)
            for key in counts:
                counts[key] += slot_counts.get(key, 0)
            prev_in_demand_window = in_demand_window

        return contexts, counts

    def _process_single_slot(
        self,
        i: int,
        slot: dict[str, Any],
        data: Any,
        all_solcast: list[dict[str, Any]],
        solar_confidence_factor: float,
        base_slot: datetime,
        local_tz: ZoneInfo | None,
        dw_start_time: time,
        dw_end_time: time,
        prev_in_demand_window: bool,
        feed_in_forecast: list[dict[str, Any]] | None = None,
        resolver: Any | None = None,
    ) -> tuple[SlotContext, dict[str, int], bool]:
        """Process a single slot, returning (context, counts, in_demand_window)."""
        slot_start: datetime = slot["start"]
        interval_minutes: int = slot["interval_minutes"]

        slot_time = self._get_slot_time_for_dw(slot_start, local_tz)

        counts: dict[str, int] = {
            "five_min": 1 if interval_minutes == 5 else 0,
            "thirty_min": 1 if interval_minutes == 30 else 0,
            "defaulted_solar": 0,
            "defaulted_price": 0,
            "defaulted_consumption": 0,
        }

        buy_price = float(slot.get("price", 0.0))
        # Use override feed_in_forecast if provided (for shadow optimizer)
        sell_price = self._get_sell_price(
            feed_in_forecast if feed_in_forecast is not None else data.feed_in_forecast,
            slot_start,
        )

        # Get confidence from resolver (defaults to 1.0 if no resolver)
        confidence = resolver.get_confidence(slot_start) if resolver else 1.0
        solar_kwh = self._get_solar_kwh(
            all_solcast,
            slot_start,
            interval_minutes,
            solar_confidence_factor,
            confidence,
        )
        if solar_kwh < 0.001:
            counts["defaulted_solar"] = 1

        consumption_kwh = self._get_consumption_kwh(
            data.load_forecast_slots, slot_start, base_slot, interval_minutes
        )
        if consumption_kwh < 0.001:
            counts["defaulted_consumption"] = 1

        if buy_price < 0.001 or sell_price < 0.001:
            counts["defaulted_price"] = 1

        # Issue #896: handle cross-midnight (overnight) demand windows where
        # end <= start (e.g. 22:00 -> 06:00). The naive ``start <= t < end``
        # comparison is false for every slot of the day in that case, silently
        # disabling the entire feature. When end <= start, a slot is in the DW
        # if its time is >= start OR < end (the window wraps past midnight).
        if dw_end_time <= dw_start_time:
            in_demand_window = slot_time >= dw_start_time or slot_time < dw_end_time
        else:
            in_demand_window = dw_start_time <= slot_time < dw_end_time
        is_demand_window_entry = in_demand_window and not prev_in_demand_window

        ctx = SlotContext(
            slot_index=i,
            timestamp_iso=slot_start.isoformat(),
            slot_interval_minutes=interval_minutes,
            buy_price=buy_price,
            sell_price=sell_price,
            solar_kwh=solar_kwh,
            consumption_kwh=consumption_kwh,
            is_demand_window_entry=is_demand_window_entry,
            is_demand_window_slot=in_demand_window,
            price_source=slot.get("price_source", "unknown"),
            export_available=self._export_available(data, slot_start),
        )

        return ctx, counts, in_demand_window

    def _export_available(self, data: Any, slot_start: datetime) -> bool:
        """Whether export selected in this slot would physically export (#1097)."""
        blocked = getattr(data, "export_blocked_periods", None)
        suppressed_until = getattr(data, "export_suppressed_until", None)
        # ``data`` is loosely typed here (callers pass bare mocks and replay
        # stand-ins): anything but the real field type means "no restriction".
        return export_available_at(
            slot_start,
            blocked if isinstance(blocked, list) else None,
            suppressed_until if isinstance(suppressed_until, datetime) else None,
        )

    def _get_slot_time_for_dw(
        self, slot_start: datetime, local_tz: ZoneInfo | None
    ) -> time:
        """Get slot time in local timezone for demand window comparison."""
        if local_tz is not None and slot_start.tzinfo is not None:
            return slot_start.astimezone(local_tz).time()
        return slot_start.time()

    def _get_sell_price(
        self, feed_in_forecast: list[dict[str, Any]], slot_start: datetime
    ) -> float:
        """Get sell price for a slot from feed_in_forecast."""
        result = get_price_for_slot_or_none(feed_in_forecast, slot_start)
        return result if result is not None else 0.0

    def _get_solar_kwh(
        self,
        all_solcast: list[dict[str, Any]],
        slot_start: datetime,
        interval_minutes: int,
        solar_confidence_factor: float,
        confidence: float = 1.0,
    ) -> float:
        """Get solar kWh for a slot.

        If bias correction has sufficient samples, return raw solar
        (bias correction will be applied by OptimizerFacade).
        Otherwise, apply solar_confidence_factor as fallback.
        """
        solar_kwh = get_solar_for_slot_by_interval(
            all_solcast, slot_start, interval_minutes, confidence
        )

        # Check if bias correction is ready
        if (
            self._solar_accuracy_tracker is not None
            and self._solar_accuracy_tracker.has_sufficient_samples()
        ):
            # Bias correction will handle it - return raw
            return max(0.0, solar_kwh)

        # Fall back to solar_confidence_factor
        return max(0.0, solar_kwh * solar_confidence_factor)

    def _get_consumption_kwh(
        self,
        load_forecast_slots: list[float],
        slot_start: datetime,
        base_slot: datetime,
        interval_minutes: int,
    ) -> float:
        """Get consumption kWh for a slot from load_forecast_slots."""
        if not load_forecast_slots:
            return 0.0

        elapsed_min = (slot_start - base_slot).total_seconds() / 60.0
        slot_start_min = elapsed_min
        slot_end_min = elapsed_min + interval_minutes

        start_bin = int(slot_start_min // 15)
        end_bin = int((slot_end_min - 1e-9) // 15)

        if end_bin < 0 or start_bin >= len(load_forecast_slots):
            return 0.0

        start_bin = max(0, start_bin)
        end_bin = min(end_bin, len(load_forecast_slots) - 1)

        consumption_kwh = 0.0
        for idx in range(start_bin, end_bin + 1):
            bin_start = idx * 15
            bin_end = bin_start + 15
            overlap = max(
                0.0, min(slot_end_min, bin_end) - max(slot_start_min, bin_start)
            )
            if overlap <= 0.0:
                continue
            consumption_kwh += load_forecast_slots[idx] * overlap / 60.0

        return consumption_kwh

    def _parse_time_option(self, key: str) -> time:
        """Parse time option from config_options.

        Args:
            key: Option key (e.g., "demand_window_start").

        Returns:
            time object parsed from "HH:MM:SS" or "HH:MM" format.

        """
        from custom_components.localshift.const import (
            DEFAULT_DEMAND_WINDOW_END,
            DEFAULT_DEMAND_WINDOW_START,
        )

        defaults = {
            "demand_window_start": DEFAULT_DEMAND_WINDOW_START,
            "demand_window_end": DEFAULT_DEMAND_WINDOW_END,
        }

        value = self._config_options.get(key, defaults.get(key, "18:00:00"))

        # Handle various formats
        if isinstance(value, time):
            return value

        if isinstance(value, str):
            # Try parsing with seconds first, then without
            for fmt in ["%H:%M:%S", "%H:%M"]:
                try:
                    return datetime.strptime(value, fmt).time()
                except ValueError:
                    continue

        # Fallback to default
        return time(18, 0)


def _slot_index_at(slots: list[SlotContext], stamp: Any) -> int | None:
    """Index of the slot covering ``stamp``, or None when the horizon has none.

    The horizon slides and re-slices between plans (a 30-minute slot becomes
    5-minute slots as it nears), so a boundary is identified by its time and
    resolved to whichever slot now contains it.
    """
    if not isinstance(stamp, str):
        return None
    try:
        when = datetime.fromisoformat(stamp)
        for slot in slots:
            start = datetime.fromisoformat(slot.timestamp_iso)
            if 0 <= (when - start).total_seconds() < slot.slot_interval_minutes * 60:
                return slot.slot_index
    except (TypeError, ValueError):
        # Unparseable, or aware against naive: no usable previous entry.
        return None
    return None


def apply_price_block_flags(
    slots: list[SlotContext],
    config: OptimizerConfig,
    data: Any,
    *,
    persist: bool = True,
) -> TargetBlock | None:
    """Let the price block, not the clock window, set the demand-window flags.

    With ``config.price_block_target`` off this changes no slot. With it on, the
    clock window's flags are cleared from every slot and the detected block
    takes their place: ``is_demand_window_slot`` across the block and
    ``is_demand_window_entry`` on its first slot. When no block is found every
    flag stays clear, so the planner has no deadline.

    Nothing downstream is told about the block. The terminal penalty, hard
    floor, water level, urgency window and import ban all read these two flags
    and follow, which is the whole design (docs/PRICE_BLOCK_TARGET.md). The
    import ban inside the block is an accepted side effect for slice 1.

    Call it on the slots the planner will actually solve, after any solar
    correction: a slot is only dear when it carries positive net load.

    Args:
        slots: The planning horizon, modified in place.
        config: Optimizer configuration (the switch, spread and duration).
        data: Coordinator data. ``target_block_entry_iso`` from the previous
            plan feeds the detector's hysteresis.
        persist: Record this plan's entry for the next one. False for a plan
            that must not move the live boundary (the shadow comparison, which
            solves on a different price series).

    Returns:
        The block applied, or None when the switch is off or nothing qualified.

    """
    if not config.price_block_target:
        if persist:
            data.target_block_entry_idx = None
            data.target_block_entry_iso = None
        return None

    previous_entry_idx = _slot_index_at(
        slots, getattr(data, "target_block_entry_iso", None)
    )
    block = find_target_block(slots, config, previous_entry_idx)

    for slot in slots:
        slot.is_demand_window_slot = False
        slot.is_demand_window_entry = False
    if block is not None:
        for slot in slots[block.entry_idx : block.end_idx + 1]:
            slot.is_demand_window_slot = True
        slots[block.entry_idx].is_demand_window_entry = True
        _LOGGER.debug("Price block target: %s", block.reason)

    if persist:
        data.target_block_entry_idx = block.entry_idx if block else None
        data.target_block_entry_iso = (
            slots[block.entry_idx].timestamp_iso if block else None
        )
    return block
