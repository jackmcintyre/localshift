"""Statistics backfiller for validating decision outcomes against metered data.

Issue #267: Ground-truth validation of decision outcomes using Home Assistant's
Statistics API. This module fetches historical statistics and compares them
against the integration's estimated outcomes to validate decision quality.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.recorder import get_instance

# Import statistics functions - use statistics_during_period which is the correct API
try:
    from homeassistant.components.recorder.statistics import (
        statistics_during_period,
    )
except ImportError:
    # Fallback for older HA versions
    statistics_during_period = None

_LOGGER = logging.getLogger(__name__)


@dataclass
class BackfillReport:
    """Report from a backfill operation comparing estimated vs actual outcomes.

    Issue #267: Contains validation results for decision outcomes.
    """

    # Validation counts
    decisions_validated: int = 0
    discrepancies_found: int = 0

    # Validated totals
    total_import_validated_kwh: float = 0.0
    total_export_validated_kwh: float = 0.0
    total_charge_validated_kwh: float = 0.0
    total_discharge_validated_kwh: float = 0.0

    # Variance metrics
    avg_import_variance_pct: float = 0.0
    avg_export_variance_pct: float = 0.0
    avg_charge_variance_pct: float = 0.0
    avg_discharge_variance_pct: float = 0.0

    # Timing
    last_run: datetime | None = None
    period_start: datetime | None = None
    period_end: datetime | None = None

    # Errors
    errors: list[str] = field(default_factory=list)

    # Detailed comparison (for debugging)
    comparisons: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "decisions_validated": self.decisions_validated,
            "discrepancies_found": self.discrepancies_found,
            "total_import_validated_kwh": self.total_import_validated_kwh,
            "total_export_validated_kwh": self.total_export_validated_kwh,
            "total_charge_validated_kwh": self.total_charge_validated_kwh,
            "total_discharge_validated_kwh": self.total_discharge_validated_kwh,
            "avg_import_variance_pct": self.avg_import_variance_pct,
            "avg_export_variance_pct": self.avg_export_variance_pct,
            "avg_charge_variance_pct": self.avg_charge_variance_pct,
            "avg_discharge_variance_pct": self.avg_discharge_variance_pct,
            "last_run": self.last_run.isoformat() if self.last_run else None,
            "period_start": self.period_start.isoformat()
            if self.period_start
            else None,
            "period_end": self.period_end.isoformat() if self.period_end else None,
            "errors": self.errors,
            "comparisons": self.comparisons[:10],  # Limit for storage
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BackfillReport:
        """Create from dictionary (deserialization)."""
        last_run = None
        if data.get("last_run"):
            try:
                last_run = datetime.fromisoformat(data["last_run"])
            except (ValueError, TypeError):
                pass

        period_start = None
        if data.get("period_start"):
            try:
                period_start = datetime.fromisoformat(data["period_start"])
            except (ValueError, TypeError):
                pass

        period_end = None
        if data.get("period_end"):
            try:
                period_end = datetime.fromisoformat(data["period_end"])
            except (ValueError, TypeError):
                pass

        return cls(
            decisions_validated=data.get("decisions_validated", 0),
            discrepancies_found=data.get("discrepancies_found", 0),
            total_import_validated_kwh=data.get("total_import_validated_kwh", 0.0),
            total_export_validated_kwh=data.get("total_export_validated_kwh", 0.0),
            total_charge_validated_kwh=data.get("total_charge_validated_kwh", 0.0),
            total_discharge_validated_kwh=data.get(
                "total_discharge_validated_kwh", 0.0
            ),
            avg_import_variance_pct=data.get("avg_import_variance_pct", 0.0),
            avg_export_variance_pct=data.get("avg_export_variance_pct", 0.0),
            avg_charge_variance_pct=data.get("avg_charge_variance_pct", 0.0),
            avg_discharge_variance_pct=data.get("avg_discharge_variance_pct", 0.0),
            last_run=last_run,
            period_start=period_start,
            period_end=period_end,
            errors=data.get("errors", []),
            comparisons=data.get("comparisons", []),
        )


class StatisticsBackfiller:
    """Fetches statistics and validates decision outcomes.

    Issue #267: Provides ground-truth validation by comparing estimated
    outcomes from the decision log against actual metered statistics.

    Usage:
        backfiller = StatisticsBackfiller(hass, config)

        # Run backfill for last 7 days
        report = await backfiller.async_backfill_decision_outcomes(days=7)

        # Check for discrepancies
        if report.discrepancies_found > 0:
            _LOGGER.warning("Found %d discrepancies", report.discrepancies_found)
    """

    def __init__(
        self,
        hass: HomeAssistant,
        config: dict[str, Any],
    ) -> None:
        """Initialize the statistics backfiller.

        Args:
            hass: Home Assistant instance
            config: Configuration dictionary with entity IDs:
                - grid_import_entity: Entity for grid import statistics
                - grid_export_entity: Entity for grid export statistics
                - battery_charge_entity: Entity for battery charge statistics
                - battery_discharge_entity: Entity for battery discharge statistics
        """
        self._hass = hass
        self._config = config
        self._last_report: BackfillReport | None = None

    @property
    def last_report(self) -> BackfillReport | None:
        """Return the last backfill report."""
        return self._last_report

    async def async_backfill_decision_outcomes(
        self,
        decisions: list[dict[str, Any]],
        days: int = 7,
    ) -> BackfillReport:
        """Main entry point for backfill operation.

        Fetches statistics for the specified period and compares them
        against the estimated outcomes from the decision log.

        Args:
            decisions: List of decision records from decision_log
            days: Number of days to look back (default: 7)

        Returns:
            BackfillReport with validation results
        """
        now = datetime.now()
        period_start = now - timedelta(days=days)
        period_end = now

        report = BackfillReport(
            last_run=now,
            period_start=period_start,
            period_end=period_end,
        )

        # Get configured entity IDs
        grid_import_entity = self._config.get("grid_import_entity")
        grid_export_entity = self._config.get("grid_export_entity")
        battery_charge_entity = self._config.get("battery_charge_entity")
        battery_discharge_entity = self._config.get("battery_discharge_entity")

        if not grid_import_entity and not grid_export_entity:
            report.errors.append("No grid import/export entities configured")
            self._last_report = report
            return report

        # Filter decisions within the period
        period_decisions = self._filter_decisions_by_period(
            decisions, period_start, period_end
        )
        report.decisions_validated = len(period_decisions)

        if not period_decisions:
            _LOGGER.info(
                "No decisions found in period %s to %s", period_start, period_end
            )
            self._last_report = report
            return report

        # Fetch statistics for each configured entity
        try:
            # Fetch grid import statistics
            if grid_import_entity:
                import_stats = await self._fetch_statistics(
                    grid_import_entity, period_start, period_end
                )
                if import_stats:
                    report.total_import_validated_kwh = self._sum_statistics(
                        import_stats, period_start, period_end
                    )
                    _LOGGER.info(
                        "Validated grid import: %.2f kWh from %d statistics rows",
                        report.total_import_validated_kwh,
                        len(import_stats),
                    )

            # Fetch grid export statistics
            if grid_export_entity:
                export_stats = await self._fetch_statistics(
                    grid_export_entity, period_start, period_end
                )
                if export_stats:
                    report.total_export_validated_kwh = self._sum_statistics(
                        export_stats, period_start, period_end
                    )
                    _LOGGER.info(
                        "Validated grid export: %.2f kWh from %d statistics rows",
                        report.total_export_validated_kwh,
                        len(export_stats),
                    )

            # Fetch battery charge statistics
            if battery_charge_entity:
                charge_stats = await self._fetch_statistics(
                    battery_charge_entity, period_start, period_end
                )
                if charge_stats:
                    report.total_charge_validated_kwh = self._sum_statistics(
                        charge_stats, period_start, period_end
                    )
                    _LOGGER.info(
                        "Validated battery charge: %.2f kWh from %d statistics rows",
                        report.total_charge_validated_kwh,
                        len(charge_stats),
                    )

            # Fetch battery discharge statistics
            if battery_discharge_entity:
                discharge_stats = await self._fetch_statistics(
                    battery_discharge_entity, period_start, period_end
                )
                if discharge_stats:
                    report.total_discharge_validated_kwh = self._sum_statistics(
                        discharge_stats, period_start, period_end
                    )
                    _LOGGER.info(
                        "Validated battery discharge: %.2f kWh from %d statistics rows",
                        report.total_discharge_validated_kwh,
                        len(discharge_stats),
                    )

            # Compare estimated vs actual for each decision
            report.comparisons = self._validate_decisions(
                period_decisions,
                report.total_import_validated_kwh,
                report.total_export_validated_kwh,
            )

            # Count discrepancies (variance > 10%)
            report.discrepancies_found = sum(
                1 for c in report.comparisons if c.get("variance_pct", 0) > 10
            )

            # Calculate average variances
            if report.comparisons:
                report.avg_import_variance_pct = sum(
                    c.get("import_variance_pct", 0) for c in report.comparisons
                ) / len(report.comparisons)
                report.avg_export_variance_pct = sum(
                    c.get("export_variance_pct", 0) for c in report.comparisons
                ) / len(report.comparisons)

        except Exception as err:
            _LOGGER.error("Failed to fetch statistics: %s", err)
            report.errors.append(str(err))

        self._last_report = report
        _LOGGER.info(
            "Backfill completed: %d decisions validated, %d discrepancies found",
            report.decisions_validated,
            report.discrepancies_found,
        )
        return report

    async def _fetch_statistics(
        self,
        entity_id: str,
        start_time: datetime,
        end_time: datetime,
    ) -> list[dict[str, Any]]:
        """Fetch statistics from Home Assistant recorder.

        Args:
            entity_id: Entity ID to fetch statistics for
            start_time: Start of the period
            end_time: End of the period

        Returns:
            List of statistics dictionaries
        """
        _LOGGER.debug(
            "Fetching statistics for %s: %s to %s",
            entity_id,
            start_time.isoformat(),
            end_time.isoformat(),
        )

        # Check if statistics functions are available
        if statistics_during_period is None:
            _LOGGER.warning(
                "Statistics API not available in this Home Assistant version"
            )
            return []

        # Use the recorder's statistics_during_period function
        # This requires the recorder integration to be enabled
        try:
            stats = await get_instance(self._hass).async_add_executor_job(
                statistics_during_period,
                self._hass,
                start_time,
                end_time,
                [entity_id],
                "hour",
                ["sum", "state"],
            )
            return stats
        except Exception as err:
            _LOGGER.warning("Failed to fetch statistics for %s: %s", entity_id, err)
            return []

    def _filter_decisions_by_period(
        self,
        decisions: list[dict[str, Any]],
        start_time: datetime,
        end_time: datetime,
    ) -> list[dict[str, Any]]:
        """Filter decisions to only those within the specified period.

        Args:
            decisions: List of decision records
            start_time: Start of the period
            end_time: End of the period

        Returns:
            Filtered list of decisions
        """
        filtered = []
        for decision in decisions:
            timestamp_str = decision.get("timestamp")
            if not timestamp_str:
                continue

            try:
                timestamp = datetime.fromisoformat(timestamp_str)
                if start_time <= timestamp <= end_time:
                    filtered.append(decision)
            except (ValueError, TypeError):
                continue

        return filtered

    def _sum_statistics(
        self,
        stats: list[dict[str, Any]],
        start_time: datetime,
        end_time: datetime,
    ) -> float:
        """Sum statistics values for a time range.

        Args:
            stats: List of statistics dictionaries
            start_time: Start of the period
            end_time: End of the period

        Returns:
            Sum of statistics values in kWh
        """
        total = 0.0
        for stat in stats:
            # Statistics are typically in Wh, convert to kWh
            # The 'sum' field contains the accumulated value
            value = stat.get("sum", 0) or 0
            total += value

        # Convert Wh to kWh if needed (assuming stats are in Wh)
        # This depends on the entity's unit_of_measurement
        return total / 1000.0 if total > 100 else total

    def _validate_decisions(
        self,
        decisions: list[dict[str, Any]],
        actual_import_kwh: float,
        actual_export_kwh: float,
    ) -> list[dict[str, Any]]:
        """Compare estimated vs actual outcomes for each decision.

        Args:
            decisions: List of decision records
            actual_import_kwh: Actual grid import from statistics
            actual_export_kwh: Actual grid export from statistics

        Returns:
            List of comparison dictionaries
        """
        comparisons = []

        # Calculate estimated totals from decisions
        estimated_import_kwh = 0.0
        estimated_export_kwh = 0.0

        for decision in decisions:
            # Extract estimated values from decision
            # These fields depend on what's stored in decision_log
            estimated_import = decision.get("estimated_import_kwh", 0) or 0
            estimated_export = decision.get("estimated_export_kwh", 0) or 0

            estimated_import_kwh += estimated_import
            estimated_export_kwh += estimated_export

        # Calculate variances
        import_variance = 0.0
        if actual_import_kwh > 0:
            import_variance = (
                (estimated_import_kwh - actual_import_kwh) / actual_import_kwh * 100
            )

        export_variance = 0.0
        if actual_export_kwh > 0:
            export_variance = (
                (estimated_export_kwh - actual_export_kwh) / actual_export_kwh * 100
            )

        # Create comparison record
        comparison = {
            "timestamp": datetime.now().isoformat(),
            "decisions_count": len(decisions),
            "estimated_import_kwh": round(estimated_import_kwh, 3),
            "actual_import_kwh": round(actual_import_kwh, 3),
            "import_variance_pct": round(import_variance, 1),
            "estimated_export_kwh": round(estimated_export_kwh, 3),
            "actual_export_kwh": round(actual_export_kwh, 3),
            "export_variance_pct": round(export_variance, 1),
            "variance_pct": round(max(abs(import_variance), abs(export_variance)), 1),
        }
        comparisons.append(comparison)

        return comparisons

    async def async_check_statistics_support(self, entity_id: str) -> bool:
        """Check if an entity supports long-term statistics.

        Entities with state_class='measurement' or state_class='total'
        support long-term statistics.

        Args:
            entity_id: Entity ID to check

        Returns:
            True if the entity supports statistics
        """
        try:
            # Check if entity exists and has state_class
            state = self._hass.states.get(entity_id)
            if not state:
                _LOGGER.warning("Entity %s not found", entity_id)
                return False

            # Check for state_class attribute
            state_class = state.attributes.get("state_class")
            if state_class in ("measurement", "total", "total_increasing"):
                _LOGGER.debug(
                    "Entity %s supports statistics (state_class=%s)",
                    entity_id,
                    state_class,
                )
                return True

            _LOGGER.debug(
                "Entity %s does not support statistics (no state_class)", entity_id
            )
            return False

        except Exception as err:
            _LOGGER.warning(
                "Error checking statistics support for %s: %s", entity_id, err
            )
            return False
