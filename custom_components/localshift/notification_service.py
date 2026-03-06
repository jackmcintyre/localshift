"""Notification functionality for LocalShift integration."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from .const import (
    CONF_BATTERY_TARGET,
    CONF_DEMAND_WINDOW_END,
    CONF_DEMAND_WINDOW_START,
    DEFAULT_BATTERY_TARGET,
    DEFAULT_DEMAND_WINDOW_END,
    DEFAULT_DEMAND_WINDOW_START,
    SWITCH_NOTIFICATIONS_ENABLED,
    BatteryMode,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from .coordinator_data import CoordinatorData


_LOGGER = logging.getLogger(__name__)

# Branding prefix for all notifications
NOTIFICATION_PREFIX = "LocalShift: "


class NotificationService:
    """Handles sending notifications for mode transitions and summaries."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        get_entity_id_func: Callable,
        get_switch_state_func: Callable | None = None,
    ) -> None:
        """Initialize the notification service.

        Args:
            hass: Home Assistant instance
            entry: Config entry
            get_entity_id_func: Function to get entity IDs by config key
            get_switch_state_func: Function to get notification preference switch states

        """
        self.hass = hass
        self.entry = entry
        self._get_entity_id = get_entity_id_func
        self._get_switch_state = get_switch_state_func

    def _is_notification_enabled(self, switch_key: str) -> bool:
        """Check if a notification type is enabled.

        Args:
            switch_key: The switch key to check (e.g., SWITCH_NOTIFY_TRANSITIONS)

        Returns:
            True if notifications are enabled for this type, False otherwise

        """
        if self._get_switch_state is None:
            return True  # Default to enabled if no switch state function
        return self._get_switch_state(switch_key)

    def _get_dry_run_prefix(self) -> str:
        """Get the dry run prefix if dry run mode is active.

        Returns:
            "[Dry Run] " if dry run is enabled, empty string otherwise

        """
        if self._get_switch_state is None:
            return ""
        return "[Dry Run] " if self._get_switch_state("dry_run") else ""

    async def send_notification(self, title: str, message: str) -> None:
        """Send a notification via the configured notify service.

        Falls back to persistent notification if the notify service fails.

        Args:
            title: Notification title
            message: Notification message body

        """
        service_target = self._get_entity_id("notify_service")
        parts = service_target.split(".", 1)

        try:
            if len(parts) == 2:
                await self.hass.services.async_call(
                    parts[0],
                    parts[1],
                    {"title": title, "message": message},
                )
        except Exception:
            # Fallback to persistent notification
            _LOGGER.warning(
                "Failed to send notification via %s, falling back to persistent notification",
                service_target,
            )
            try:
                await self.hass.services.async_call(
                    "persistent_notification",
                    "create",
                    {
                        "title": title,
                        "message": message,
                        "notification_id": "localshift",
                    },
                )
            except Exception as e:
                _LOGGER.error("Failed to create persistent notification: %s", e)

    async def send_transition_notification(
        self, old_mode: BatteryMode, new_mode: BatteryMode, data: CoordinatorData
    ) -> None:
        """Send a notification for a mode transition.

        Args:
            old_mode: The previous battery mode
            new_mode: The new battery mode
            data: Current coordinator data

        """
        if not self._is_notification_enabled(SWITCH_NOTIFICATIONS_ENABLED):
            _LOGGER.debug("Notifications disabled, skipping transition")
            return

        dry_run_prefix = self._get_dry_run_prefix()

        if new_mode == BatteryMode.SPIKE_DISCHARGE:
            title = f"{dry_run_prefix}{NOTIFICATION_PREFIX}Price Spike!"
            message = (
                f"Price spike detected. Feed-in: ${data.feed_in_price:.2f}/kWh. "
                f"Battery at {data.soc:.0f}%. "
                f"Switching to force discharge (export)."
            )
        elif new_mode == BatteryMode.PROACTIVE_EXPORT:
            # Calculate the reserve that was set
            reserve = max(4, data.soc - 5)
            title = f"{dry_run_prefix}{NOTIFICATION_PREFIX}Proactive Export"
            message = (
                f"Forecast predicts low/negative FIT. Feed-in: ${data.feed_in_price:.2f}/kWh. "
                f"Battery at {data.soc:.0f}%. "
                f"Exporting with {reserve:.0f}% reserve (buffer from SOC-5%)."
            )
        elif new_mode == BatteryMode.DEMAND_BLOCK:
            dw_start = self.entry.options.get(
                CONF_DEMAND_WINDOW_START, DEFAULT_DEMAND_WINDOW_START
            )
            dw_end = self.entry.options.get(
                CONF_DEMAND_WINDOW_END, DEFAULT_DEMAND_WINDOW_END
            )
            title = f"{dry_run_prefix}{NOTIFICATION_PREFIX}Demand Window Active"
            message = (
                f"Demand window started ({dw_start}–{dw_end}). "
                f"Grid imports blocked. Battery at {data.soc:.0f}%."
            )
        elif new_mode == BatteryMode.GRID_CHARGING:
            title = f"{dry_run_prefix}{NOTIFICATION_PREFIX}Cheap Grid Charging"
            message = (
                f"Grid price is ${data.general_price:.2f}/kWh "
                f"(below threshold ${data.effective_cheap_price:.2f}/kWh). "
                f"Battery at {data.soc:.0f}%. Charging from grid at ~3.3kW."
            )
        elif new_mode == BatteryMode.BOOST_CHARGING:
            net_solar = data.solar_battery_forecast.get("net_solar_kwh", 0)
            target_pct = self.entry.options.get(
                CONF_BATTERY_TARGET, DEFAULT_BATTERY_TARGET
            )
            title = f"{dry_run_prefix}{NOTIFICATION_PREFIX}Boost Charging (5kW)"
            message = (
                f"Grid price is ${data.general_price:.2f}/kWh "
                f"(below threshold ${data.effective_cheap_price:.2f}/kWh). "
                f"Battery at {data.soc:.0f}%, target {target_pct}%. "
                f"Solar forecast insufficient — boost charging at ~5kW. "
                f"Net solar: {net_solar}kWh before demand window."
            )
        elif new_mode == BatteryMode.SELF_CONSUMPTION:
            title, message = self._self_consumption_notification(
                old_mode, data, dry_run_prefix
            )
        elif new_mode == BatteryMode.MANUAL:
            title = f"{dry_run_prefix}{NOTIFICATION_PREFIX}Manual Override"
            message = "Automation disabled or manual override active."
        else:
            title = f"{dry_run_prefix}{NOTIFICATION_PREFIX}Mode Change"
            message = f"Mode changed: {old_mode.value} → {new_mode.value}"

        await self.send_notification(title, message)

    def _self_consumption_notification(
        self, old_mode: BatteryMode, data: CoordinatorData, dry_run_prefix: str = ""
    ) -> tuple[str, str]:
        """Generate notification text for returning to self consumption.

        Args:
            old_mode: The previous battery mode
            data: Current coordinator data
            dry_run_prefix: Optional dry run prefix for title

        Returns:
            Tuple of (title, message)

        """
        if old_mode == BatteryMode.SPIKE_DISCHARGE:
            return (
                f"{dry_run_prefix}{NOTIFICATION_PREFIX}Spike Ended",
                f"Price spike has cleared. Feed-in now: "
                f"${data.feed_in_price:.2f}/kWh. "
                f"Battery at {data.soc:.0f}%. "
                f"Returning to self consumption.",
            )
        if old_mode == BatteryMode.PROACTIVE_EXPORT:
            return (
                f"{dry_run_prefix}{NOTIFICATION_PREFIX}Proactive Export Ended",
                f"FIT has improved. Feed-in now: "
                f"${data.feed_in_price:.2f}/kWh. "
                f"Battery at {data.soc:.0f}%. "
                f"Returning to self consumption.",
            )
        if old_mode in (
            BatteryMode.GRID_CHARGING,
            BatteryMode.BOOST_CHARGING,
        ):
            # Determine the actual reason for stopping grid charging
            # Issue #352: Don't assume price rose - check actual conditions
            price_above_stop = data.general_price > data.cheap_charge_stop_price
            price_above_effective = data.general_price > data.effective_cheap_price

            if price_above_stop:
                # Price genuinely rose above stop threshold
                return (
                    f"{dry_run_prefix}{NOTIFICATION_PREFIX}Charging Stopped",
                    f"Grid price rose to ${data.general_price:.2f}/kWh "
                    f"(above stop threshold "
                    f"${data.cheap_charge_stop_price:.2f}/kWh). "
                    f"Battery at {data.soc:.0f}%. "
                    f"Returning to self consumption.",
                )
            elif price_above_effective:
                # Price above effective threshold but not stop threshold
                return (
                    f"{dry_run_prefix}{NOTIFICATION_PREFIX}Charging Stopped",
                    f"Grid price at ${data.general_price:.2f}/kWh "
                    f"(above effective threshold "
                    f"${data.effective_cheap_price:.2f}/kWh). "
                    f"Battery at {data.soc:.0f}%. "
                    f"Returning to self consumption.",
                )
            else:
                # Price is still cheap - stopped for other reasons
                # (forecast complete, SOC target reached, solar sufficient, etc.)
                return (
                    f"{dry_run_prefix}{NOTIFICATION_PREFIX}Charging Complete",
                    f"Grid price at ${data.general_price:.2f}/kWh "
                    f"(still below threshold ${data.effective_cheap_price:.2f}/kWh). "
                    f"Battery at {data.soc:.0f}%. "
                    f"Scheduled charging complete or solar sufficient.",
                )
        if old_mode == BatteryMode.DEMAND_BLOCK:
            return (
                f"{dry_run_prefix}{NOTIFICATION_PREFIX}Demand Window Ended",
                f"Demand window ended. Battery at {data.soc:.0f}%. "
                f"Returning to normal automation.",
            )
        return (
            f"{dry_run_prefix}{NOTIFICATION_PREFIX}Self Consumption",
            f"Returning to self consumption. Battery at {data.soc:.0f}%.",
        )

    async def send_daily_summary(self, data: CoordinatorData) -> None:
        """Send end-of-day summary notification with energy and cost stats.

        Replaces YAML A15 (localshift_daily_summary). Reads daily energy
        from utility meter entities (still in YAML) and cost accumulators.

        Args:
            data: Current coordinator data

        """
        if not self._is_notification_enabled(SWITCH_NOTIFICATIONS_ENABLED):
            _LOGGER.debug("Notifications disabled, skipping daily summary")
            return

        dry_run_prefix = self._get_dry_run_prefix()
        net = data.grid_import_cost - data.grid_export_revenue

        # Read daily energy from utility meter sensors (remain in YAML)
        import_kwh = self._read_float("sensor.grid_import_energy_daily", 0.0)
        export_kwh = self._read_float("sensor.grid_export_energy_daily", 0.0)
        solar_kwh = self._read_float("sensor.solar_production_energy_daily", 0.0)

        soc = round(data.soc)

        # Learning system stats (Issue #170 Phase 5)
        metrics = data.performance_metrics
        learning_summary = ""
        if metrics.total_decisions_today > 0:
            quality_pct = round(metrics.avg_decision_score_today * 100)
            learning_summary = (
                f"\n\nLearning: {data.learning_status} | "
                f"Quality: {quality_pct}% | "
                f"Trend: {metrics.cost_trend}"
            )

        message = (
            f"Today so far:\n\n"
            f"Solar: {solar_kwh:.1f} kWh\n"
            f"Grid import: {import_kwh:.1f} kWh "
            f"(${data.grid_import_cost:.2f})\n"
            f"Grid export: {export_kwh:.1f} kWh "
            f"(${data.grid_export_revenue:.2f} revenue)\n"
            f"Net cost: ${net:.2f}\n\n"
            f"Battery savings: ${data.battery_savings:.2f}\n"
            f"Battery charge cost: ${data.battery_charge_cost:.2f}\n"
            f"SOC: {soc}%"
            f"{learning_summary}"
        )

        await self.send_notification(
            f"{dry_run_prefix}{NOTIFICATION_PREFIX}Daily Summary", message
        )

    async def send_health_correction_notification(
        self,
        mode: BatteryMode,
        data: CoordinatorData,
        mismatch_details: dict | None = None,
    ) -> None:
        """Notify when health check corrects hardware drift.

        Args:
            mode: The commanded mode that was corrected
            data: Current coordinator data
            mismatch_details: Dict with what mismatched (operation_mode, backup_reserve,
                            grid_charging_allowed). Used to suppress notifications for
                            expected Tesla cloud sync behavior (Issue #394).

        """
        if not self._is_notification_enabled(SWITCH_NOTIFICATIONS_ENABLED):
            _LOGGER.debug("Notifications disabled, skipping health correction")
            return

        # Issue #394: Suppress notification for Tesla cloud grid_charging sync
        # Tesla's cloud resets allow_charging_from_grid to True every ~60 minutes.
        # This is expected behavior - the health check corrects it within seconds.
        # Only suppress if:
        # 1. Mode is SELF_CONSUMPTION or DEMAND_BLOCK (where grid_charging should be False)
        # 2. ONLY grid_charging_allowed mismatched (not operation_mode or backup_reserve)
        if mismatch_details is not None and self._is_tesla_grid_charging_sync(
            mode, mismatch_details
        ):
            _LOGGER.info(
                "[HEALTH CHECK] Skipping notification - Tesla cloud sync for grid_charging "
                "(expected behavior, corrected automatically)"
            )
            return

        dry_run_prefix = self._get_dry_run_prefix()
        title = f"{dry_run_prefix}{NOTIFICATION_PREFIX}Health Check Correction"
        message = (
            f"Hardware state drifted from commanded mode '{mode.value}'. "
            f"Correction applied. Battery at {data.soc:.0f}%."
        )

        await self.send_notification(title, message)

    def _is_tesla_grid_charging_sync(
        self, mode: BatteryMode, mismatch_details: dict
    ) -> bool:
        """Check if this is Tesla's periodic grid_charging reset (Issue #394).

        Tesla's cloud resets allow_charging_from_grid to True approximately every
        60 minutes. This is expected behavior in SELF_CONSUMPTION and DEMAND_BLOCK
        modes where grid_charging should be False.

        Args:
            mode: The commanded mode
            mismatch_details: Dict with what mismatched

        Returns:
            True if this is just Tesla's grid_charging sync (should suppress notification)

        """
        # Only suppress for modes where grid_charging should be False
        if mode not in (BatteryMode.SELF_CONSUMPTION, BatteryMode.DEMAND_BLOCK):
            return False

        # Check if ONLY grid_charging_allowed mismatched
        only_grid_charging_mismatched = (
            mismatch_details.get("grid_charging_allowed", False)
            and not mismatch_details.get("operation_mode", False)
            and not mismatch_details.get("backup_reserve", False)
        )

        return only_grid_charging_mismatched

    async def send_transition_failed_notification(
        self, target_mode: BatteryMode, data: CoordinatorData
    ) -> None:
        """Notify when a mode transition fails.

        Args:
            target_mode: The mode that was attempted
            data: Current coordinator data

        """
        if not self._is_notification_enabled(SWITCH_NOTIFICATIONS_ENABLED):
            _LOGGER.debug("Notifications disabled, skipping transition failure")
            return

        dry_run_prefix = self._get_dry_run_prefix()
        title = f"{dry_run_prefix}{NOTIFICATION_PREFIX}Transition Failed"
        message = (
            f"Failed to transition to '{target_mode.value}' mode. "
            f"Battery at {data.soc:.0f}%. "
            f"Check Powerwall connectivity and try again."
        )

        await self.send_notification(title, message)

    async def send_automation_disabled_notification(
        self, data: CoordinatorData
    ) -> None:
        """Notify when automation is disabled.

        Args:
            data: Current coordinator data

        """
        if not self._is_notification_enabled(SWITCH_NOTIFICATIONS_ENABLED):
            _LOGGER.debug("Notifications disabled, skipping automation disabled")
            return

        dry_run_prefix = self._get_dry_run_prefix()
        title = f"{dry_run_prefix}{NOTIFICATION_PREFIX}Automation Disabled"
        message = (
            f"Automation has been disabled. "
            f"Battery returned to self consumption. "
            f"Battery at {data.soc:.0f}%."
        )

        await self.send_notification(title, message)

    async def send_manual_override_timeout_notification(
        self, data: CoordinatorData, timeout_hours: float
    ) -> None:
        """Notify when manual override auto-clears after timeout.

        Args:
            data: Current coordinator data
            timeout_hours: The timeout duration in hours

        """
        if not self._is_notification_enabled(SWITCH_NOTIFICATIONS_ENABLED):
            _LOGGER.debug("Notifications disabled, skipping manual override timeout")
            return

        dry_run_prefix = self._get_dry_run_prefix()
        title = f"{dry_run_prefix}{NOTIFICATION_PREFIX}Manual Override Timeout"
        message = (
            f"Manual override cleared after {timeout_hours:.1f} hours. "
            f"Automation resuming. Battery at {data.soc:.0f}%."
        )

        await self.send_notification(title, message)

    async def send_manual_action_notification(
        self, action: str, data: CoordinatorData
    ) -> None:
        """Send notification for manual button actions.

        Args:
            action: Description of the manual action
            data: Current coordinator data

        """
        if not self._is_notification_enabled(SWITCH_NOTIFICATIONS_ENABLED):
            _LOGGER.debug("Notifications disabled, skipping manual action")
            return

        dry_run_prefix = self._get_dry_run_prefix()
        title = f"{dry_run_prefix}{NOTIFICATION_PREFIX}{action}"
        message = f"{action} started. Battery at {data.soc:.0f}%."

        await self.send_notification(title, message)

    def generate_decision_reason(
        self, old_mode: BatteryMode, new_mode: BatteryMode, data: CoordinatorData
    ) -> str:
        """Generate a human-readable reason for a mode transition."""
        if new_mode == BatteryMode.SPIKE_DISCHARGE:
            return f"Price spike detected (feed-in ${data.feed_in_price:.2f}/kWh)"
        if new_mode == BatteryMode.PROACTIVE_EXPORT:
            return f"Forecast predicts low/negative FIT (feed-in ${data.feed_in_price:.2f}/kWh)"
        if new_mode == BatteryMode.DEMAND_BLOCK:
            return "Demand window active -- protecting from grid imports"
        if new_mode == BatteryMode.GRID_CHARGING:
            return (
                f"Price ${data.general_price:.2f}/kWh below threshold "
                f"${data.effective_cheap_price:.2f}/kWh, "
                f"SOC {data.soc:.0f}%"
            )
        if new_mode == BatteryMode.BOOST_CHARGING:
            return (
                f"Solar gap -- boost charging needed, "
                f"price ${data.general_price:.2f}/kWh, "
                f"SOC {data.soc:.0f}%"
            )
        if new_mode == BatteryMode.SELF_CONSUMPTION:
            if old_mode in (
                BatteryMode.GRID_CHARGING,
                BatteryMode.BOOST_CHARGING,
            ):
                # Issue #352: Check actual price vs threshold for accurate reason
                price_above_stop = data.general_price > data.cheap_charge_stop_price
                price_above_effective = data.general_price > data.effective_cheap_price

                if price_above_stop:
                    return (
                        f"Charging ended -- price ${data.general_price:.2f}/kWh "
                        f"(above stop threshold ${data.cheap_charge_stop_price:.2f}/kWh)"
                    )
                elif price_above_effective:
                    return (
                        f"Charging ended -- price ${data.general_price:.2f}/kWh "
                        f"(above effective threshold ${data.effective_cheap_price:.2f}/kWh)"
                    )
                else:
                    # Price still cheap - stopped for other reasons
                    return (
                        f"Charging complete -- price ${data.general_price:.2f}/kWh "
                        f"(still below threshold). SOC at {data.soc:.0f}%"
                    )
            if old_mode == BatteryMode.SPIKE_DISCHARGE:
                return "Price spike cleared"
            if old_mode == BatteryMode.PROACTIVE_EXPORT:
                return "Proactive export ended - FIT improved"
            if old_mode == BatteryMode.DEMAND_BLOCK:
                return "Demand window ended"
            return "Normal operation -- no special conditions active"
        if new_mode == BatteryMode.MANUAL:
            return "Automation disabled or manual override"
        return f"Mode changed: {old_mode.value} -> {new_mode.value}"

    def _read_float(self, entity_id: str, default: float = 0.0) -> float:
        """Read a float value from an entity's state."""
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable"):
            return default
        try:
            return float(state.state)
        except (ValueError, TypeError):
            return default
