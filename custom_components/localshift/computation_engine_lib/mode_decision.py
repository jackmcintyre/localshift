"""Active mode and decision-log helpers for computation engine."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime

from ..const import DISCHARGE_EARLIEST_HOUR, BatteryMode
from ..coordinator_data import CoordinatorData

_LOGGER = logging.getLogger(__name__)


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

        if forecast_entry.get("grid_charge_boost"):
            if grid_import_kwh > grid_import_threshold:
                data.active_mode = BatteryMode.BOOST_CHARGING
                _LOGGER.info(
                    "Forecast-driven: BOOST_CHARGING at %s, import=%.3f kWh",
                    now_dt.strftime("%H:%M"),
                    grid_import_kwh,
                )
                return
            _LOGGER.debug(
                "grid_charge_boost=True but grid_import_kwh=0, checking grid_charge"
            )

        if forecast_entry.get("grid_charge"):
            if grid_import_kwh > grid_import_threshold:
                data.active_mode = BatteryMode.GRID_CHARGING
                _LOGGER.info(
                    "Forecast-driven: GRID_CHARGING at %s, import=%.3f kWh",
                    now_dt.strftime("%H:%M"),
                    grid_import_kwh,
                )
                return
            _LOGGER.debug(
                "grid_charge=True but grid_import_kwh=%.3f, staying in self-consumption",
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
