"""Cost tracking functionality for energy costs and savings."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

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

    # -------------------------------------------------------------------------
    # Cost Reconciliation (Issue #269)
    # -------------------------------------------------------------------------

    async def async_reconcile_with_statistics(
        self,
        grid_import_entity: str,
        grid_export_entity: str,
        estimated_import_cost: float,
        estimated_export_revenue: float,
        period_start: datetime,
        period_end: datetime,
        significance_threshold: float = 10.0,
    ) -> dict[str, Any]:
        """Reconcile accumulated costs against metered statistics.

        Issue #269: Validates cost estimates against actual metered data
        from Home Assistant's long-term statistics.

        Args:
            grid_import_entity: Entity ID for grid import energy (kWh)
            grid_export_entity: Entity ID for grid export energy (kWh)
            estimated_import_cost: Estimated import cost from accumulators
            estimated_export_revenue: Estimated export revenue from accumulators
            period_start: Start of reconciliation period
            period_end: End of reconciliation period
            significance_threshold: Percentage threshold for flagging variance

        Returns:
            ReconciliationReport as dictionary
        """
        from .coordinator_data import ReconciliationReport

        errors: list[str] = []

        # Fetch statistics for import entity
        import_kwh = await self._fetch_statistics_for_period(
            grid_import_entity, period_start, period_end
        )
        if import_kwh is None:
            errors.append(f"Could not fetch statistics for {grid_import_entity}")
            import_kwh = 0.0

        # Fetch statistics for export entity
        export_kwh = await self._fetch_statistics_for_period(
            grid_export_entity, period_start, period_end
        )
        if export_kwh is None:
            errors.append(f"Could not fetch statistics for {grid_export_entity}")
            export_kwh = 0.0

        # Get average prices for the period (simplified: use current prices)
        # In a full implementation, we would fetch historical prices
        state = self.hass.states.get(grid_import_entity)
        import_price = 0.0
        export_price = 0.0

        if state:
            # Try to get price from attributes or use a default
            import_price = float(state.attributes.get("price", 0.15))
        else:
            import_price = 0.15  # Default fallback

        state = self.hass.states.get(grid_export_entity)
        if state:
            export_price = float(state.attributes.get("price", 0.05))
        else:
            export_price = 0.05  # Default fallback

        # Calculate actual costs
        actual_import_cost = import_kwh * import_price
        actual_export_revenue = export_kwh * export_price

        # Calculate variances
        import_variance_pct = self._calculate_variance_pct(
            estimated_import_cost, actual_import_cost
        )
        export_variance_pct = self._calculate_variance_pct(
            estimated_export_revenue, actual_export_revenue
        )

        # Calculate total variance
        total_estimated = estimated_import_cost + estimated_export_revenue
        total_actual = actual_import_cost + actual_export_revenue
        total_variance_pct = self._calculate_variance_pct(total_estimated, total_actual)

        # Determine if variance is significant
        is_significant = (
            abs(import_variance_pct) > significance_threshold
            or abs(export_variance_pct) > significance_threshold
            or abs(total_variance_pct) > significance_threshold
        )

        if is_significant:
            _LOGGER.warning(
                "Cost variance detected: import=%.1f%%, export=%.1f%%, total=%.1f%%",
                import_variance_pct,
                export_variance_pct,
                total_variance_pct,
            )

        report = ReconciliationReport(
            timestamp=datetime.now(),
            period_start=period_start,
            period_end=period_end,
            estimated_import_cost=estimated_import_cost,
            actual_import_cost=actual_import_cost,
            import_variance_pct=import_variance_pct,
            estimated_export_revenue=estimated_export_revenue,
            actual_export_revenue=actual_export_revenue,
            export_variance_pct=export_variance_pct,
            total_variance_pct=total_variance_pct,
            is_significant=is_significant,
            significance_threshold=significance_threshold,
            errors=errors,
        )

        _LOGGER.info(
            "Cost reconciliation: estimated=$%.2f, actual=$%.2f, variance=%.1f%%",
            total_estimated,
            total_actual,
            total_variance_pct,
        )

        return report.to_dict()

    async def _fetch_statistics_for_period(
        self,
        entity_id: str,
        start_time: datetime,
        end_time: datetime,
    ) -> float | None:
        """Fetch total energy (kWh) from statistics for a time period.

        Args:
            entity_id: Entity ID to fetch statistics for
            start_time: Start of period
            end_time: End of period

        Returns:
            Total energy in kWh, or None if fetch failed
        """
        try:
            from homeassistant.components import recorder
            from homeassistant.components.recorder import statistics
        except ImportError:
            _LOGGER.warning("Recorder not available for statistics fetch")
            return None

        try:
            recorder_instance = recorder.get_instance(self.hass)
            result = await recorder_instance.async_add_executor_job(
                self._fetch_statistics_sync,
                entity_id,
                start_time,
                end_time,
            )
            return result
        except Exception as e:
            _LOGGER.warning("Failed to fetch statistics for %s: %s", entity_id, e)
            return None

    def _fetch_statistics_sync(
        self,
        entity_id: str,
        start_time: datetime,
        end_time: datetime,
    ) -> float:
        """Fetch statistics synchronously (runs in thread pool).

        Args:
            entity_id: Entity ID to fetch
            start_time: Start of period
            end_time: End of period

        Returns:
            Total energy in kWh
        """
        try:
            from homeassistant.components.recorder.statistics import (
                statistics_during_period,
            )
        except ImportError:
            _LOGGER.debug("statistics_during_period not available")
            return 0.0

        try:
            # Use statistics_during_period which is the correct API
            stats = statistics_during_period(
                self.hass,
                start_time,
                end_time,
                [entity_id],
                "hour",
                ["sum"],
            )

            if not stats:
                return 0.0

            if isinstance(stats, dict) and entity_id in stats:
                rows = stats[entity_id]
                if isinstance(rows, list):
                    # Sum all 'sum' values
                    total = 0.0
                    for row in rows:
                        if isinstance(row, dict):
                            val = row.get("sum")
                            if val is not None:
                                try:
                                    total += float(val)
                                except (TypeError, ValueError):
                                    pass
                    return total

            return 0.0

        except Exception as e:
            _LOGGER.warning("Error fetching statistics: %s", e)
            return 0.0

    def _calculate_variance_pct(self, estimated: float, actual: float) -> float:
        """Calculate variance percentage between estimated and actual.

        Args:
            estimated: Estimated value
            actual: Actual value

        Returns:
            Variance percentage (positive = overestimate, negative = underestimate)
        """
        if actual == 0:
            if estimated == 0:
                return 0.0
            # If actual is 0 but estimated is not, return 100% variance
            return 100.0 if estimated > 0 else -100.0

        return ((estimated - actual) / actual) * 100
