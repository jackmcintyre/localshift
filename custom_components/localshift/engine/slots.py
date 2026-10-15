"""SlotBuilder — construct SlotContext list from raw CoordinatorData.

This module builds SlotContext objects directly from raw coordinator data fields,
serving as the input preparation layer for the DP optimizer.

Design principles:
- Read raw data: general_forecast, feed_in_forecast, solcast_today/tomorrow, load_forecast_slots
- Compute demand window flags independently
- Return typed SlotBuildMetadata for diagnostics
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from ..const import CONF_DEMAND_WINDOW_FROM_AMBER, BatteryMode
from ..forecast.solar import get_solar_for_slot_by_interval, solcast_span
from ..forecast.solar_accuracy import SolarAccuracyTracker
from ..utils.export_availability import export_available_at
from .price_calculator import get_price_for_slot_or_none
from .slot_schedule import compute_hybrid_slot_schedule, find_current_entry
from .types import SlotContext

_LOGGER = logging.getLogger(__name__)

# Issue #1119: Amber rolls each interval over carrying the PREVIOUS interval's
# price flagged `estimate: true`, and confirms the real one some seconds later
# (15-41 s across the 2026-10-12 spike). While slot 0's sell price is such an
# unconfirmed reading, an export may continue but not start. The grace is
# bounded so a feed that never confirms cannot block export for the interval:
# past it, the estimate is the best price there is and is acted on.
ESTIMATE_CONFIRMATION_GRACE_S = 90.0


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

    slots_with_defaulted_solar: int
    """Slots no Solcast period covers (night slots with 0 kWh are not defaults)."""

    slots_with_defaulted_price: int
    """Slots with no buy price, or no feed-in entry at or before the slot."""

    slots_with_defaulted_consumption: int = 0
    """Slots where load_forecast_slots was unavailable (fallback to 0.0)."""

    all_solcast: list[dict[str, Any]] = field(default_factory=list)
    """Full solar forecast (today + tomorrow) for penalty calculation (Issue #607)."""

    slot0_price_source: str = "unknown"
    """slots[0].price_source ("forecast_current", "synthetic", "5min", "30min")
    from the just-built hybrid schedule, or "unknown" when no slots were built.
    Read by OptimizerFacade.run_inline to feed the synthetic-slot-0 rolling
    rate tracker (Issue #956)."""

    slot0_price: dict[str, Any] = field(default_factory=dict)
    """What slot 0 was priced on (Issue #1119), published on the optimizer
    summary: ``buy``/``sell`` ($/kWh), ``buy_source``/``sell_source``
    ("forecast_current" = the entry covering now; "forecast_window" = no
    feed-in entry covers now, 15-minute lookup used), ``buy_estimate``/
    ``sell_estimate`` (Amber's flag: True until the interval's price is
    confirmed; None when the source carries none), ``export_start_deferred``
    (an unconfirmed sell price kept slot 0 from starting an export) and
    ``live_buy``/``live_sell`` (the price sensors' own states)."""

    next_demand_window_gap_min: float | None = None
    """Minutes from the end of the last slot to the next demand-window start
    (issue #1033), or None when the last slot is inside a window or there are
    no slots. Passed to the planner as
    ``OptimizerInputs.next_demand_window_gap_min``."""

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
        }


class SlotBuilder:
    """Builds SlotContext list from raw CoordinatorData.

    Reads:
    - data.general_forecast / data.feed_in_forecast — Amber buy/sell price series
    - data.solcast_today / data.solcast_tomorrow — solar forecast
    - data.load_forecast_slots — per-slot load kW (from Phase 1 LoadForecaster)
    - DW config options — demand window entry/slot flags per slot

    Usage:
        slot_builder = SlotBuilder(config_options, ha_timezone)
        slots, metadata = slot_builder.build_slots(data)
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
        now_dt: datetime | None = None,
        override_general_forecast: list[dict[str, Any]] | None = None,
        override_feed_in_forecast: list[dict[str, Any]] | None = None,
    ) -> tuple[list[SlotContext], SlotBuildMetadata]:
        """Build SlotContext list from raw coordinator data.

        Args:
            data: CoordinatorData with prices, forecasts, etc.
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
            # Issue #1036: a shadow build must not overwrite the primary
            # build's remembered horizon signature (log level only).
            log_scope=(
                "shadow" if override_general_forecast is not None else "primary"
            ),
        )

        dw_start_time = self._parse_time_option("demand_window_start")
        dw_end_time = self._parse_time_option("demand_window_end")

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
            base_slot=base_slot,
            local_tz=local_tz,
            dw_start_time=dw_start_time,
            dw_end_time=dw_end_time,
            feed_in_forecast=feed_in_forecast,
            resolver=resolver,
        )

        slot0_price = (
            self._price_slot0_from_current_interval(
                contexts, hybrid_slots, data, feed_in_forecast, now_local
            )
            or {}
        )

        metadata = SlotBuildMetadata(
            total_slots=len(contexts),
            five_min_slots=counts["five_min"],
            thirty_min_slots=counts["thirty_min"],
            horizon_hours=hybrid_metadata.get("horizon_hours", 24.0),
            slots_with_defaulted_solar=counts["defaulted_solar"],
            slots_with_defaulted_price=counts["defaulted_price"],
            slots_with_defaulted_consumption=counts["defaulted_consumption"],
            all_solcast=all_solcast,
            slot0_price_source=(contexts[0].price_source if contexts else "unknown"),
            slot0_price=slot0_price,
            next_demand_window_gap_min=self._next_demand_window_gap_min(
                contexts, local_tz, dw_start_time
            ),
        )

        _LOGGER.debug(
            "SlotBuilder: built %d slots (%d x 5min, %d x 30min), "
            "defaulted: solar=%d price=%d consumption=%d",
            len(contexts),
            counts["five_min"],
            counts["thirty_min"],
            counts["defaulted_solar"],
            counts["defaulted_price"],
            counts["defaulted_consumption"],
        )

        return contexts, metadata

    def _price_slot0_from_current_interval(
        self,
        contexts: list[SlotContext],
        hybrid_slots: list[dict[str, Any]],
        data: Any,
        feed_in_forecast: list[dict[str, Any]],
        now_local: datetime,
    ) -> dict[str, Any] | None:
        """Price slot 0's sell side from the feed-in entry covering now (#1119).

        Slot 0 is the only slot that is acted on. Its buy price is already the
        general-forecast entry covering now; its sell price came from a
        15-minute lookup, i.e. the MEAN of the current interval and the next
        two 5-minute forecasts. On 2026-10-12 that turned a confirmed $24.23
        into $9.49 and a confirmed $0.36 into $2.69, and the plan exported
        into the 36c interval. Later slots keep the existing lookup.

        An unconfirmed reading is used as the price — it is the best there is —
        but may not START an export during the confirmation grace (see
        ``ESTIMATE_CONFIRMATION_GRACE_S``). An export already running is left
        alone: stopping it for the seconds until confirmation would add a
        drop-out per interval.

        Returns:
            The ``SlotBuildMetadata.slot0_price`` payload, or None with no slots.

        """
        if not contexts:
            return None

        ctx = contexts[0]
        sell_source = "forecast_window"
        sell_estimate: bool | None = None
        deferred = False

        current = find_current_entry(feed_in_forecast, now_local, self._ha_timezone)
        if current is not None:
            entry, origin = current
            ctx.sell_price = float(entry.get("per_kwh", 0.0))
            sell_source = "forecast_current"
            flag = entry.get("estimate")
            sell_estimate = flag if isinstance(flag, bool) else None

            elapsed_s = (now_local - origin).total_seconds()
            exporting = (
                getattr(data, "active_mode", None) == BatteryMode.PROACTIVE_EXPORT
            )
            if (
                sell_estimate is True
                and elapsed_s < ESTIMATE_CONFIRMATION_GRACE_S
                and not exporting
            ):
                deferred = ctx.export_available
                ctx.export_available = False

        buy_estimate = hybrid_slots[0].get("estimate") if hybrid_slots else None

        def _live(name: str) -> float | None:
            value = getattr(data, name, None)
            return round(value, 4) if isinstance(value, (int, float)) else None

        return {
            "buy": round(ctx.buy_price, 4),
            "sell": round(ctx.sell_price, 4),
            "buy_source": ctx.price_source,
            "sell_source": sell_source,
            "buy_estimate": buy_estimate if isinstance(buy_estimate, bool) else None,
            "sell_estimate": sell_estimate,
            "export_start_deferred": deferred,
            "live_buy": _live("general_price"),
            "live_sell": _live("feed_in_price"),
        }

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

    def _next_demand_window_gap_min(
        self,
        contexts: list[SlotContext],
        local_tz: ZoneInfo | None,
        dw_start_time: time,
    ) -> float | None:
        """Minutes from the end of the last slot to the next demand-window start.

        Issue #1033: the price feed is 24 hours long, so in the afternoon the
        horizon ends at or just short of tomorrow's window and the planner, which
        has no clock, cannot tell a window is about to open. 0 means the feed
        ends as the window opens. None when the last slot is inside a window
        (the planner already sees it) or the gap cannot be measured.
        """
        if not contexts or contexts[-1].is_demand_window_slot:
            return None
        last = contexts[-1]
        try:
            horizon_end = datetime.fromisoformat(last.timestamp_iso) + timedelta(
                minutes=last.slot_interval_minutes
            )
        except (ValueError, TypeError):
            return None
        if local_tz is not None and horizon_end.tzinfo is not None:
            horizon_end = horizon_end.astimezone(local_tz)
        next_start = horizon_end.replace(
            hour=dw_start_time.hour,
            minute=dw_start_time.minute,
            second=0,
            microsecond=0,
        )
        if next_start < horizon_end:
            # A window that opens part-way through the last slot (14:55 inside
            # 14:30-15:00) has started by the time the feed ends: the slot is
            # not flagged because it starts before the window, but the gap is
            # zero, not a day.
            last_start = horizon_end - timedelta(minutes=last.slot_interval_minutes)
            if next_start > last_start:
                return 0.0
            next_start += timedelta(days=1)
        return (next_start - horizon_end).total_seconds() / 60.0

    def _process_all_slots(
        self,
        hybrid_slots: list[dict[str, Any]],
        data: Any,
        all_solcast: list[dict[str, Any]],
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
        solar_span = solcast_span(all_solcast)

        for i, slot in enumerate(hybrid_slots):
            ctx, slot_counts, in_demand_window = self._process_single_slot(
                i=i,
                slot=slot,
                data=data,
                all_solcast=all_solcast,
                base_slot=base_slot,
                local_tz=local_tz,
                dw_start_time=dw_start_time,
                dw_end_time=dw_end_time,
                prev_in_demand_window=prev_in_demand_window,
                feed_in_forecast=feed_in_forecast,
                resolver=resolver,
                solar_span=solar_span,
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
        base_slot: datetime,
        local_tz: ZoneInfo | None,
        dw_start_time: time,
        dw_end_time: time,
        prev_in_demand_window: bool,
        feed_in_forecast: list[dict[str, Any]] | None = None,
        resolver: Any | None = None,
        solar_span: tuple[datetime, datetime] | None = None,
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

        # Issue #987: a default is a slot the lookup had no entry for, not a
        # legitimate zero (night solar) or zero/negative price (negative FIT).
        buy_raw = slot.get("price")
        buy_price = float(buy_raw) if buy_raw is not None else 0.0
        # Use override feed_in_forecast if provided (for shadow optimizer)
        sell_raw = get_price_for_slot_or_none(
            feed_in_forecast if feed_in_forecast is not None else data.feed_in_forecast,
            slot_start,
        )
        sell_price = sell_raw if sell_raw is not None else 0.0
        if buy_raw is None or sell_raw is None:
            counts["defaulted_price"] = 1

        # Get confidence from resolver (defaults to 1.0 if no resolver)
        confidence = resolver.get_confidence(slot_start) if resolver else 1.0
        solar_kwh = self._get_solar_kwh(
            all_solcast,
            slot_start,
            interval_minutes,
            confidence,
        )
        slot_end = slot_start + timedelta(minutes=interval_minutes)
        if solar_span is None or not (
            solar_span[0] < slot_end and slot_start < solar_span[1]
        ):
            counts["defaulted_solar"] = 1

        consumption_kwh = self._get_consumption_kwh(
            data.load_forecast_slots, slot_start, base_slot, interval_minutes
        )
        if consumption_kwh < 0.001:
            counts["defaulted_consumption"] = 1

        # Issue #896: handle cross-midnight (overnight) demand windows where
        # end <= start (e.g. 22:00 -> 06:00). The naive ``start <= t < end``
        # comparison is false for every slot of the day in that case, silently
        # disabling the entire feature. When end <= start, a slot is in the DW
        # if its time is >= start OR < end (the window wraps past midnight).
        if dw_end_time <= dw_start_time:
            in_demand_window = slot_time >= dw_start_time or slot_time < dw_end_time
        else:
            in_demand_window = dw_start_time <= slot_time < dw_end_time
        # Issue #1104: with the switch on, Amber Express's per-interval flag
        # decides. A slot without a boolean flag (base Amber, a synthetic or
        # extended slot, beyond the feed) keeps the clock window above, so a
        # feed problem can only add window, never remove it.
        amber_flag = slot.get("demand_window")
        if isinstance(amber_flag, bool) and self._config_options.get(
            CONF_DEMAND_WINDOW_FROM_AMBER, False
        ):
            in_demand_window = amber_flag
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

    def _get_solar_kwh(
        self,
        all_solcast: list[dict[str, Any]],
        slot_start: datetime,
        interval_minutes: int,
        confidence: float = 1.0,
    ) -> float:
        """Get solar kWh for a slot.

        Returns the raw forecast for the slot; any bias correction is
        applied later by OptimizerFacade.
        """
        solar_kwh = get_solar_for_slot_by_interval(
            all_solcast, slot_start, interval_minutes, confidence
        )

        return max(0.0, solar_kwh)

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

        # Fallback to the same default the engine uses, never a third value.
        return datetime.strptime(defaults.get(key, "18:00:00"), "%H:%M:%S").time()
