"""SlotBuilder — construct SlotContext list from raw CoordinatorData.

This module builds SlotContext objects directly from raw coordinator data fields,
serving as the input preparation layer for the DP optimizer.

Design principles:
- Read raw data: general_forecast, feed_in_forecast, solcast_today/tomorrow, load_forecast_slots
- Apply adaptive param transforms: solar_confidence_factor to solar_kwh
- DO NOT apply consumption_forecast_bias (already applied by LoadForecaster)
- Compute demand window flags independently
- Return typed SlotBuildMetadata for diagnostics
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from ..coordinator_data import AdaptiveParameters
from .optimizer_dp import SlotContext
from .price_calculator import get_price_for_slot_or_none
from .slot_schedule import TOTAL_SLOTS, compute_hybrid_slot_schedule
from .solar_utils import get_solar_for_slot_by_interval

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
    ) -> None:
        """Store DW time config and timezone for slot generation.

        Args:
            config_options: Integration config options (for DW start/end parsing).
            ha_timezone: Home Assistant timezone string (e.g., "Australia/Sydney").

        """
        self._config_options = config_options
        self._ha_timezone = ha_timezone

    def build_slots(
        self,
        data: Any,  # CoordinatorData — avoid circular import
        adaptive_params: AdaptiveParameters | None,
        now_dt: datetime | None = None,
    ) -> tuple[list[SlotContext], SlotBuildMetadata]:
        """Build SlotContext list from raw coordinator data.

        Args:
            data: CoordinatorData instance with raw forecast fields.
            adaptive_params: AdaptiveParameters from learning system, or None.
            now_dt: Reference "now" datetime. When provided (e.g. in tests that
                mock time), slots are filtered relative to this time rather than
                the real wall-clock. Defaults to datetime.now().astimezone().

        Returns:
            (contexts, metadata) tuple where:
            - contexts: list of SlotContext objects for DP optimizer
            - metadata: SlotBuildMetadata with diagnostic counts

        """
        # Step 1: Get hybrid slot schedule from general_forecast
        now_local = (
            now_dt.astimezone() if now_dt is not None else datetime.now().astimezone()
        )
        hybrid_slots, hybrid_metadata = compute_hybrid_slot_schedule(
            now_local=now_local,
            general_forecast=data.general_forecast,
            ha_timezone=self._ha_timezone,
        )

        # Step 2: Parse demand window times from config
        dw_start_time = self._parse_time_option("demand_window_start")
        dw_end_time = self._parse_time_option("demand_window_end")

        # Step 3: Read adaptive parameters
        solar_confidence_factor = 1.0
        if adaptive_params is not None:
            solar_confidence_factor = adaptive_params.get(
                "solar_confidence_factor", 1.0
            )
        # Clamp to safe range [0.0, 2.0] to guard against corrupted learning values
        solar_confidence_factor = max(0.0, min(2.0, solar_confidence_factor))

        # Step 4: Combine solcast forecasts
        all_solcast = [*data.solcast_today, *data.solcast_tomorrow]

        # Step 5: Compute base slot for load_forecast_slots indexing
        current_5min = (now_local.minute // 5) * 5
        base_slot = now_local.replace(minute=current_5min, second=0, microsecond=0)

        # Resolve local timezone object once for DW time comparisons.
        # DW start/end times are always in the HA local timezone, so we must
        # convert each slot_start to local time before comparing.
        try:
            local_tz = ZoneInfo(self._ha_timezone)
        except Exception:
            local_tz = None

        # Step 6: Build SlotContext for each hybrid slot
        contexts: list[SlotContext] = []
        five_min_count = 0
        thirty_min_count = 0
        slots_with_defaulted_solar = 0
        slots_with_defaulted_price = 0
        slots_with_defaulted_consumption = 0

        prev_in_demand_window = False

        for i, slot in enumerate(hybrid_slots):
            slot_start: datetime = slot["start"]
            interval_minutes: int = slot["interval_minutes"]

            # Convert to local timezone for DW comparison.
            # slot_start may be in any tz (UTC when ha_timezone is not set in tests,
            # or local otherwise). DW times are always in HA local tz.
            if local_tz is not None and slot_start.tzinfo is not None:
                slot_time = slot_start.astimezone(local_tz).time()
            else:
                slot_time = slot_start.time()

            # Track slot duration counts
            if interval_minutes == 5:
                five_min_count += 1
            elif interval_minutes == 30:
                thirty_min_count += 1

            # Buy price: from hybrid slot (already computed from general_forecast)
            buy_price = float(slot.get("price", 0.0))

            # Sell price: lookup in feed_in_forecast
            sell_price_result = get_price_for_slot_or_none(
                data.feed_in_forecast, slot_start
            )
            sell_price = sell_price_result if sell_price_result is not None else 0.0

            # Solar: lookup in solcast and apply confidence factor
            solar_kwh = get_solar_for_slot_by_interval(
                all_solcast, slot_start, interval_minutes
            )
            solar_kwh = max(0.0, solar_kwh * solar_confidence_factor)
            if solar_kwh < 0.001:  # Effectively zero
                slots_with_defaulted_solar += 1

            # Consumption: lookup in load_forecast_slots by 15-min index
            # Phase 1 already applied consumption_forecast_bias here
            consumption_kwh = 0.0
            if data.load_forecast_slots:
                elapsed_min = (slot_start - base_slot).total_seconds() / 60.0
                fixed_idx = min(int(elapsed_min // 15), TOTAL_SLOTS - 1)
                if 0 <= fixed_idx < len(data.load_forecast_slots):
                    consumption_kw = data.load_forecast_slots[fixed_idx]
                    consumption_kwh = consumption_kw * interval_minutes / 60.0
                    if interval_minutes == 30 and 11 <= slot_start.hour <= 14:
                        _LOGGER.info(
                            "ISSUE_500 slot_builder: slot=%d time=%s interval=%d fixed_idx=%d "
                            "slots_len=%d kw_idx_%d=%.3f kwh=%.3f",
                            i,
                            slot_start.strftime("%H:%M"),
                            interval_minutes,
                            fixed_idx,
                            len(data.load_forecast_slots),
                            fixed_idx,
                            consumption_kw,
                            consumption_kwh,
                        )

            if consumption_kwh < 0.001:
                slots_with_defaulted_consumption += 1

            # Track price defaults
            if buy_price < 0.001 or sell_price < 0.001:
                slots_with_defaulted_price += 1

            # Demand window flags
            in_demand_window = dw_start_time <= slot_time < dw_end_time
            is_demand_window_entry = in_demand_window and not prev_in_demand_window

            # Build SlotContext
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
            )
            contexts.append(ctx)

            prev_in_demand_window = in_demand_window

        # Step 7: Build metadata
        metadata = SlotBuildMetadata(
            total_slots=len(contexts),
            five_min_slots=five_min_count,
            thirty_min_slots=thirty_min_count,
            horizon_hours=hybrid_metadata.get("horizon_hours", 24.0),
            solar_confidence_factor=solar_confidence_factor,
            slots_with_defaulted_solar=slots_with_defaulted_solar,
            slots_with_defaulted_price=slots_with_defaulted_price,
            slots_with_defaulted_consumption=slots_with_defaulted_consumption,
        )

        _LOGGER.debug(
            "SlotBuilder: built %d slots (%d x 5min, %d x 30min), "
            "solar_confidence=%.2f, defaulted: solar=%d price=%d consumption=%d",
            len(contexts),
            five_min_count,
            thirty_min_count,
            solar_confidence_factor,
            slots_with_defaulted_solar,
            slots_with_defaulted_price,
            slots_with_defaulted_consumption,
        )

        return contexts, metadata

    def _parse_time_option(self, key: str) -> time:
        """Parse time option from config_options.

        Args:
            key: Option key (e.g., "demand_window_start").

        Returns:
            time object parsed from "HH:MM:SS" or "HH:MM" format.

        """
        from ..const import (  # noqa: PLC0415
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
