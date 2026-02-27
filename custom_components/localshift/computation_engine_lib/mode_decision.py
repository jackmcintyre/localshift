"""Active mode and decision-log helpers for computation engine."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime

from ..const import DISCHARGE_EARLIEST_HOUR, BatteryMode
from ..coordinator_data import CoordinatorData

_LOGGER = logging.getLogger(__name__)

# Threshold for "very cheap" price (80% of effective cheap price)
VERY_CHEAP_PRICE_FACTOR = 0.8


class ModeDecisionEngine:
    """Compute active battery mode and maintain decision logs."""

    def __init__(
        self,
        get_switch_state: Callable[[str], bool],
        get_forecast_entry_for_now: Callable[[CoordinatorData, datetime], dict | None],
    ) -> None:
        """Initialize decision engine dependencies."""
        self._get_switch_state = get_switch_state
        self._get_forecast_entry_for_now = get_forecast_entry_for_now

    def _should_grid_charge_with_live_price(
        self,
        data: CoordinatorData,
        now_dt: datetime,
        forecast_entry: dict,
    ) -> tuple[bool, bool]:
        """Validate grid charging decision using live spot price.

        Issue #341: The forecast stores grid_charge_boost based on forecast prices,
        but for the current slot we should validate with the actual live spot price
        to avoid mode flapping when forecast and live prices differ.

        This method VALIDATES the forecast decision with live prices:
        - If forecast says charge but live price is NOT cheap → don't charge (fixes #341)
        - If forecast says charge and live price IS cheap → charge (normal case)
        - If forecast says boost and live price is VERY cheap → boost
        - If forecast says boost but live price is only cheap → regular charge

        Args:
            data: CoordinatorData with live prices and state
            now_dt: Current datetime
            forecast_entry: The forecast entry for the current slot

        Returns:
            Tuple of (should_charge, should_boost)
        """
        # Get live values
        live_price = data.general_price
        effective_cheap_price = data.effective_cheap_price
        is_currently_grid_charging = data.force_charge_active

        # Get forecast flags
        forecast_says_charge = forecast_entry.get("grid_charge", False)
        forecast_says_boost = forecast_entry.get("grid_charge_boost", False)

        # Price thresholds
        price_is_cheap = live_price <= effective_cheap_price
        price_is_very_cheap = live_price <= (
            effective_cheap_price * VERY_CHEAP_PRICE_FACTOR
        )

        # HYSTERESIS: If currently grid charging and price is still cheap,
        # continue charging to avoid flip-flopping
        if is_currently_grid_charging and price_is_cheap:
            _LOGGER.info(
                "LIVE_PRICE HYSTERESIS: Continuing grid charge at %s (live_price=$%.2f, threshold=$%.2f, SOC=%.1f%%)",
                now_dt.strftime("%H:%M"),
                live_price,
                effective_cheap_price,
                data.soc,
            )
            if price_is_very_cheap:
                return True, True
            return True, False

        # If forecast didn't plan to charge, don't start based on live price alone
        # (The forecast has already done solar simulation to determine if charging is needed)
        if not forecast_says_charge and not forecast_says_boost:
            return False, False

        # Forecast says to charge - validate with live price
        if not price_is_cheap:
            # Issue #341 fix: Forecast said charge based on forecast price,
            # but live price is NOT cheap - don't charge
            _LOGGER.info(
                "LIVE_PRICE BLOCK: Forecast said charge at %s but live_price=$%.2f > threshold=$%.2f - NOT charging",
                now_dt.strftime("%H:%M"),
                live_price,
                effective_cheap_price,
            )
            return False, False

        # Live price IS cheap - proceed with charging
        # Determine boost vs regular based on live price, not forecast flag
        if forecast_says_boost:
            if price_is_very_cheap:
                _LOGGER.info(
                    "LIVE_PRICE BOOST: Forecast boost confirmed at %s (live_price=$%.2f <= $%.2f)",
                    now_dt.strftime("%H:%M"),
                    live_price,
                    effective_cheap_price * VERY_CHEAP_PRICE_FACTOR,
                )
                return True, True
            else:
                # Forecast said boost, but live price is only cheap (not very cheap)
                # Downgrade to regular charge
                _LOGGER.info(
                    "LIVE_PRICE DOWNGRADE: Forecast boost at %s but live_price=$%.2f not very cheap (threshold=$%.2f) - regular charge",
                    now_dt.strftime("%H:%M"),
                    live_price,
                    effective_cheap_price * VERY_CHEAP_PRICE_FACTOR,
                )
                return True, False

        # Forecast said regular charge, live price confirms it's cheap
        _LOGGER.info(
            "LIVE_PRICE CHARGE: Forecast charge confirmed at %s (live_price=$%.2f <= threshold=$%.2f)",
            now_dt.strftime("%H:%M"),
            live_price,
            effective_cheap_price,
        )
        return True, False

    def compute_active_mode(self, data: CoordinatorData, now_dt: datetime) -> None:
        """Compute active battery mode from forecast and current constraints."""
        automation_enabled = self._get_switch_state("automation_enabled")
        spike_discharge_enabled = self._get_switch_state("spike_discharge_enabled")

        if not automation_enabled:
            data.active_mode = BatteryMode.SELF_CONSUMPTION
            return

        # Always respect manual override - user is in control
        if data.manual_override:
            data.active_mode = BatteryMode.MANUAL
            data.debug_mode_source = "manual_override"
            return

        # Issue #319: Defer grid charging decisions when forecast data is not ready
        # This prevents BOOST mode from being triggered at startup when Solcast
        # data hasn't been received yet.
        if not data.forecast_ready:
            data.debug_mode_source = "forecast_not_ready"
            _LOGGER.info(
                "Forecast not ready (status=%s), defaulting to self-consumption - "
                "deferring grid charging decisions until forecast data is available",
                data.forecast_status,
            )
            data.active_mode = BatteryMode.SELF_CONSUMPTION
            return

        # Issue #330: Defer grid charging decisions when price data is unavailable
        # This prevents BOOST mode from being triggered when prices are "unavailable"
        # which would otherwise be treated as $0 and incorrectly trigger cheap charging.
        if not data.prices_available:
            data.debug_mode_source = "prices_unavailable"
            _LOGGER.warning(
                "Price data unavailable (prices_available=False), defaulting to self-consumption - "
                "deferring grid charging decisions until price data is available"
            )
            data.active_mode = BatteryMode.SELF_CONSUMPTION
            return

        data.proactive_export_active = False

        current_hour = now_dt.hour
        in_discharge_window = current_hour >= DISCHARGE_EARLIEST_HOUR

        forecast_entry = self._get_forecast_entry_for_now(data, now_dt)
        if not forecast_entry:
            data.debug_mode_source = "no_forecast"
            _LOGGER.warning(
                "Forecast unavailable, defaulting to self-consumption (no fallback logic)"
            )
            data.active_mode = BatteryMode.SELF_CONSUMPTION
            return

        data.debug_mode_source = "forecast"

        _LOGGER.debug(
            "Mode decision at %s: slot_time=%s, grid_charge=%s, grid_charge_boost=%s, grid_import_kwh=%.3f, proactive_export=%s, soc=%.1f%%",
            now_dt.strftime("%H:%M"),
            forecast_entry.get("timestamp", "unknown")[-14:-9],
            forecast_entry.get("grid_charge", False),
            forecast_entry.get("grid_charge_boost", False),
            forecast_entry.get("grid_import_kwh", 0),
            forecast_entry.get("proactive_export", False),
            data.soc,
        )

        grid_import_kwh = forecast_entry.get("grid_import_kwh", 0)
        grid_import_threshold = 0.01

        # Issue #341: Use live spot price for current slot mode decision
        # The forecast stores grid_charge_boost based on forecast prices, but for
        # the current slot we should use the actual live spot price to avoid
        # mode flapping when forecast and live prices differ.
        should_charge, should_boost = self._should_grid_charge_with_live_price(
            data, now_dt, forecast_entry
        )

        if should_boost:
            if grid_import_kwh > grid_import_threshold:
                data.active_mode = BatteryMode.BOOST_CHARGING
                data.debug_mode_source = "live_price_boost"
                _LOGGER.info(
                    "Live-price-driven: BOOST_CHARGING at %s, import=%.3f kWh (live_price=$%.2f)",
                    now_dt.strftime("%H:%M"),
                    grid_import_kwh,
                    data.general_price,
                )
                return
            _LOGGER.debug(
                "should_boost=True but grid_import_kwh=0, checking regular charge"
            )

        if should_charge:
            if grid_import_kwh > grid_import_threshold:
                data.active_mode = BatteryMode.GRID_CHARGING
                data.debug_mode_source = "live_price_charge"
                _LOGGER.info(
                    "Live-price-driven: GRID_CHARGING at %s, import=%.3f kWh (live_price=$%.2f)",
                    now_dt.strftime("%H:%M"),
                    grid_import_kwh,
                    data.general_price,
                )
                return
            _LOGGER.debug(
                "should_charge=True but grid_import_kwh=%.3f, staying in self-consumption",
                grid_import_kwh,
            )

        if forecast_entry.get("proactive_export"):
            export_amount = forecast_entry.get("export_amount_kwh", 0.0)
            export_threshold = 0.01

            if export_amount > export_threshold:
                data.active_mode = BatteryMode.PROACTIVE_EXPORT
                data.proactive_export_active = True
                _LOGGER.info(
                    "Forecast-driven: PROACTIVE_EXPORT at %s, amount=%.2f kWh",
                    now_dt.strftime("%H:%M"),
                    export_amount,
                )
                return
            _LOGGER.debug(
                "proactive_export=True but export_amount_kwh=%.3f, staying in self-consumption",
                export_amount,
            )

        if data.price_spike and spike_discharge_enabled and in_discharge_window:
            data.active_mode = BatteryMode.SPIKE_DISCHARGE
        elif data.demand_window_active:
            data.active_mode = BatteryMode.DEMAND_BLOCK
        elif data.manual_override:
            data.active_mode = BatteryMode.MANUAL
        else:
            _LOGGER.debug(
                "Mode fallthrough to SELF_CONSUMPTION at %s: "
                "grid_charge=%s grid_boost=%s proactive=%s "
                "spike=%s dw=%s manual=%s",
                now_dt.strftime("%H:%M"),
                forecast_entry.get("grid_charge"),
                forecast_entry.get("grid_charge_boost"),
                forecast_entry.get("proactive_export"),
                data.price_spike,
                data.demand_window_active,
                data.manual_override,
            )
            data.active_mode = BatteryMode.SELF_CONSUMPTION

    def add_to_decision_log(
        self,
        data: CoordinatorData,
        now_dt: datetime,
        previous_active_mode: BatteryMode | None,
        mode_change: bool,
    ) -> BatteryMode:
        """Add entry to decision log and return the newly active mode."""
        old_mode = previous_active_mode
        new_mode = data.active_mode

        old_mode_display = old_mode.display_name if old_mode else "Unknown"
        new_mode_display = new_mode.display_name if new_mode else "Unknown"

        if mode_change:
            reason = f"Mode changed: {old_mode_display} -> {new_mode_display}"
        else:
            reason = f"Status update: {new_mode_display}"

        entry = {
            "timestamp": now_dt.isoformat(),
            "old_mode": old_mode.value if old_mode else "unknown",
            "new_mode": new_mode.value if new_mode else "unknown",
            "old_mode_display": old_mode_display,
            "new_mode_display": new_mode_display,
            "buy_price": round(data.general_price, 2),
            "sell_price": round(data.feed_in_price, 2),
            "soc": round(data.soc),
            "effective_threshold": data.effective_cheap_price,
            "reason": reason,
        }
        data.decision_log.append(entry)
        if len(data.decision_log) > 50:
            data.decision_log = data.decision_log[-50:]

        return new_mode
