"""Cost tracking functionality for energy costs and savings."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..const import BATTERY_CAPACITY_KWH

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from ..coordinator.data import CoordinatorData

_LOGGER = logging.getLogger(__name__)

# Deadband (kW) below which a power flow is treated as idle/noise. Matches the
# 0.1 kW threshold used elsewhere for "charging"/"exporting" (binary_sensor.py,
# engine/excess_solar_signals.py).
_POWER_DEADBAND_KW = 0.1
# SOC headroom (%) below which the battery is treated as "full" for the purpose
# of the export-leak metric. Exporting while above this is not counted as a leak.
_BATTERY_FULL_SOC_PCT = 99.0


class CostTracker:
    """Tracks energy costs and savings over time."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the cost tracker."""
        self.hass = hass
        # Last-seen SOC (%), used to convert SOC deltas into kWh for the
        # grid-charge-efficiency metric (Issue #868). None until the first
        # sample is seen, so the first interval's gain is skipped (no baseline).
        self._last_soc_pct: float | None = None

    def accumulate_costs(self, data: CoordinatorData) -> None:
        """Accumulate per-minute energy costs from current power and price.

        Replaces YAML A16 (localshift_cost_accumulator).
        Formula: power_kW × price_$/kWh / 60 = $/min
        """
        # Grid import cost: positive grid power × buy price
        import_cost = max(data.grid_power_kw, 0.0) * data.general_price / 60
        data.grid_import_cost += import_cost

        # Grid export revenue: negative grid power (export) × sell price
        export_revenue = max(-data.grid_power_kw, 0.0) * data.feed_in_price / 60
        data.grid_export_revenue += export_revenue

        # Tesla/Teslemetry sign convention: battery_power_kw > 0 = DISCHARGING,
        # battery_power_kw < 0 = CHARGING (matches binary_sensor.py:228 and
        # engine/excess_solar_signals.py:173 which use < -0.1 for "charging").
        # Battery savings: discharge (positive power) × buy price (avoided purchase)
        savings = max(data.battery_power_kw, 0.0) * data.general_price / 60
        data.battery_savings += savings

        # Battery charge cost: charge (negative power) × buy price
        charge_cost = max(-data.battery_power_kw, 0.0) * data.general_price / 60
        data.battery_charge_cost += charge_cost

        # Daily energy accounting for the #868 performance metrics.
        self._accumulate_energy_kwh(data)

    def _accumulate_energy_kwh(self, data: CoordinatorData) -> None:
        """Accumulate per-minute kWh for the Issue #868 performance metrics.

        Sign conventions (see accumulate_costs above):
          grid_power_kw > 0    => importing from grid
          grid_power_kw < 0    => exporting to grid
          battery_power_kw < 0 => charging
          battery_power_kw > 0 => discharging

        All energy is power_kW / 60 (one minute of integration). The SOC-gain term
        is a daily proxy: it converts the SOC delta observed since the previous
        sample into kWh via BATTERY_CAPACITY_KWH, attributing it to grid charging
        only while the battery is being charged from the grid. It is an
        approximation (it ignores simultaneous solar charging) used only for the
        display-only grid_charge_efficiency ratio.
        """
        # Total grid import / export energy this minute.
        import_kw = max(data.grid_power_kw, 0.0)
        export_kw = max(-data.grid_power_kw, 0.0)
        if import_kw >= _POWER_DEADBAND_KW:
            data.grid_import_kwh_today += import_kw / 60
        if export_kw >= _POWER_DEADBAND_KW:
            data.grid_export_kwh_today += export_kw / 60

        # Grid energy flowing into the battery: only when both importing from the
        # grid AND charging the battery. The grid-to-battery rate cannot exceed
        # either flow, so take the min of the two.
        charge_kw = max(-data.battery_power_kw, 0.0)
        charging_from_grid = (
            import_kw >= _POWER_DEADBAND_KW and charge_kw >= _POWER_DEADBAND_KW
        )
        if charging_from_grid:
            grid_to_battery_kw = min(charge_kw, import_kw)
            data.grid_to_battery_kwh_today += grid_to_battery_kw / 60

        # SOC gain (kWh) observed since the previous sample, attributed to grid
        # charging when we are charging from the grid. Skip the first sample
        # (no baseline) and ignore SOC drops.
        if self._last_soc_pct is not None and charging_from_grid:
            soc_delta_pct = data.soc - self._last_soc_pct
            if soc_delta_pct > 0:
                data.soc_gain_during_grid_charge_kwh_today += (
                    soc_delta_pct / 100.0 * BATTERY_CAPACITY_KWH
                )
        self._last_soc_pct = data.soc

        # Export that occurred while the battery still had room to absorb it.
        # That exported energy could have been self-consumed into the battery,
        # so it counts toward the export-loss ratio.
        if export_kw >= _POWER_DEADBAND_KW and data.soc < _BATTERY_FULL_SOC_PCT:
            data.export_while_battery_not_full_kwh_today += export_kw / 60

    def reset_daily_accumulators(self, data: CoordinatorData) -> None:
        """Reset daily cost and energy accumulators and the target flag.

        Replaces YAML A12 (localshift_reset_target_reached).
        """
        data.grid_import_cost = 0.0
        data.grid_export_revenue = 0.0
        data.battery_savings = 0.0
        data.battery_charge_cost = 0.0
        data.target_reached_today = False
        self._reset_energy_accumulators(data)

    def _reset_energy_accumulators(self, data: CoordinatorData) -> None:
        """Reset the Issue #868 daily energy accumulators."""
        data.grid_import_kwh_today = 0.0
        data.grid_export_kwh_today = 0.0
        data.grid_to_battery_kwh_today = 0.0
        data.soc_gain_during_grid_charge_kwh_today = 0.0
        data.export_while_battery_not_full_kwh_today = 0.0
