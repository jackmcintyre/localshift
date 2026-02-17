"""Notification functionality for LocalShift integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .const import (
    CONF_BATTERY_TARGET,
    CONF_DEMAND_WINDOW_END,
    CONF_DEMAND_WINDOW_START,
    DEFAULT_BATTERY_TARGET,
    DEFAULT_DEMAND_WINDOW_END,
    DEFAULT_DEMAND_WINDOW_START,
    BatteryMode,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from .coordinator_data import CoordinatorData


class NotificationService:
    """Handles sending notifications for mode transitions and summaries."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, get_entity_id_func: callable
    ) -> None:
        """Initialize the notification service.

        Args:
            hass: Home Assistant instance
            entry: Config entry
            get_entity_id_func: Function to get entity IDs by config key
        """
        self.hass = hass
        self.entry = entry
        self._get_entity_id = get_entity_id_func

    async def send_notification(self, title: str, message: str) -> None:
        """Send a notification via the configured notify service."""
        service_target = self._get_entity_id("notify_service")
        # Parse "notify.mobile_app_xxx" into domain="notify", service="mobile_app_xxx"
        parts = service_target.split(".", 1)
        if len(parts) == 2:
            await self.hass.services.async_call(
                parts[0],
                parts[1],
                {"title": title, "message": message},
            )

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
                return (
                    f"Charging ended -- price ${data.general_price:.2f}/kWh "
                    f"(above ${data.cheap_charge_stop_price:.2f}/kWh)"
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

    async def send_transition_notification(
        self, old_mode: BatteryMode, new_mode: BatteryMode, data: CoordinatorData
    ) -> None:
        """Send a notification for a mode transition."""
        prefix = "Powerwall: "

        if new_mode == BatteryMode.SPIKE_DISCHARGE:
            title = f"{prefix}Price Spike!"
            message = (
                f"Price spike detected. Feed-in: ${data.feed_in_price:.2f}/kWh. "
                f"Battery at {data.soc:.0f}%. "
                f"Switching to force discharge (export)."
            )
        elif new_mode == BatteryMode.PROACTIVE_EXPORT:
            # Calculate the reserve that was set
            reserve = max(4, data.soc - 5)
            title = f"{prefix}Proactive Export"
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
            title = f"{prefix}Demand Window Active"
            message = (
                f"Demand window started ({dw_start}–{dw_end}). "
                f"Grid imports blocked. Battery at {data.soc:.0f}%."
            )
        elif new_mode == BatteryMode.GRID_CHARGING:
            title = f"{prefix}Cheap Grid Charging"
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
            title = f"{prefix}Boost Charging (5kW)"
            message = (
                f"Grid price is ${data.general_price:.2f}/kWh "
                f"(below threshold ${data.effective_cheap_price:.2f}/kWh). "
                f"Battery at {data.soc:.0f}%, target {target_pct}%. "
                f"Solar forecast insufficient — boost charging at ~5kW. "
                f"Net solar: {net_solar}kWh before demand window."
            )
        elif new_mode == BatteryMode.SELF_CONSUMPTION:
            title, message = self._self_consumption_notification(old_mode, data)
        elif new_mode == BatteryMode.MANUAL:
            title = f"{prefix}Manual Override"
            message = "Automation disabled or manual override active."
        else:
            title = f"{prefix}Mode Change"
            message = f"Mode changed: {old_mode.value} → {new_mode.value}"

        await self.send_notification(title, message)

    def _self_consumption_notification(
        self, old_mode: BatteryMode, data: CoordinatorData
    ) -> tuple[str, str]:
        """Generate notification text for returning to self consumption."""
        prefix = "Powerwall: "

        if old_mode == BatteryMode.SPIKE_DISCHARGE:
            return (
                f"{prefix}Spike Ended",
                f"Price spike has cleared. Feed-in now: "
                f"${data.feed_in_price:.2f}/kWh. "
                f"Battery at {data.soc:.0f}%. "
                f"Returning to self consumption.",
            )
        if old_mode == BatteryMode.PROACTIVE_EXPORT:
            return (
                f"{prefix}Proactive Export Ended",
                f"FIT has improved. Feed-in now: "
                f"${data.feed_in_price:.2f}/kWh. "
                f"Battery at {data.soc:.0f}%. "
                f"Returning to self consumption.",
            )
        if old_mode in (
            BatteryMode.GRID_CHARGING,
            BatteryMode.BOOST_CHARGING,
        ):
            return (
                f"{prefix}Charging Stopped",
                f"Grid price rose to ${data.general_price:.2f}/kWh "
                f"(above stop threshold "
                f"${data.cheap_charge_stop_price:.2f}/kWh). "
                f"Battery at {data.soc:.0f}%. "
                f"Returning to self consumption.",
            )
        if old_mode == BatteryMode.DEMAND_BLOCK:
            return (
                f"{prefix}Demand Window Ended",
                f"Demand window ended. Battery at {data.soc:.0f}%. "
                f"Returning to normal automation.",
            )
        return (
            f"{prefix}Self Consumption",
            f"Returning to self consumption. Battery at {data.soc:.0f}%.",
        )

    async def send_daily_summary(self, data: CoordinatorData) -> None:
        """Send end-of-day summary notification with energy and cost stats.

        Replaces YAML A15 (localshift_daily_summary). Reads daily energy
        from utility meter entities (still in YAML) and cost accumulators.
        """
        net = data.grid_import_cost - data.grid_export_revenue

        # Read daily energy from utility meter sensors (remain in YAML)
        import_kwh = self._read_float("sensor.grid_import_energy_daily", 0.0)
        export_kwh = self._read_float("sensor.grid_export_energy_daily", 0.0)
        solar_kwh = self._read_float("sensor.solar_production_energy_daily", 0.0)

        soc = round(data.soc)

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
        )

        await self.send_notification("Powerwall: Daily Summary", message)

    def _read_float(self, entity_id: str, default: float = 0.0) -> float:
        """Read a float value from an entity's state."""
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable"):
            return default
        try:
            return float(state.state)
        except (ValueError, TypeError):
            return default
