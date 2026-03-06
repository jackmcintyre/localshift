"""Cost tracking functionality for energy costs and savings."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .coordinator_data import CoordinatorData

_LOGGER = logging.getLogger(__name__)


class CostTracker:
    """Tracks energy costs and savings over time."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the cost tracker."""
        self.hass = hass

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

        # Battery savings: battery discharge × buy price (avoided purchase)
        savings = max(-data.battery_power_kw, 0.0) * data.general_price / 60
        data.battery_savings += savings

        # Battery charge cost: battery charge × buy price
        charge_cost = max(data.battery_power_kw, 0.0) * data.general_price / 60
        data.battery_charge_cost += charge_cost

    def reset_daily_accumulators(self, data: CoordinatorData) -> None:
        """Reset daily cost accumulators and target flag.

        Replaces YAML A12 (localshift_reset_target_reached).
        """
        data.grid_import_cost = 0.0
        data.grid_export_revenue = 0.0
        data.battery_savings = 0.0
        data.battery_charge_cost = 0.0
        data.target_reached_today = False
