"""Optimization engine for battery charging schedule.

Issue #363: MVP Optimization Engine Prototype

This module provides the main entry point for running optimization
in shadow mode - computing optimal schedules and comparing them
against the rule-based approach without applying changes.

Architecture:
    CoordinatorData → OptimizationEngine → ChargingSchedule
                              ↓
                        (Shadow Mode: Log only, don't apply)
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from .charging_schedule import ChargingSchedule, ChargingSlot, OptimizationComparison
from .optimization_model import OptimizationModel

if TYPE_CHECKING:
    from .coordinator_data import CoordinatorData

_LOGGER = logging.getLogger(__name__)

# Shadow mode configuration
SHADOW_MODE_ENABLED = True  # Run optimization in shadow mode by default
OPTIMIZATION_INTERVAL_MINUTES = 30  # Re-run optimization every 30 minutes


class OptimizationEngine:
    """Optimization engine for battery charging decisions.

    This engine runs in shadow mode, computing optimal charging schedules
    and comparing them against the current rule-based approach. Results
    are logged for analysis but not applied to the system.

    Usage:
        engine = OptimizationEngine()
        schedule = engine.run_optimization(coordinator_data)
        # schedule contains optimal decisions + comparison vs rule-based
    """

    def __init__(
        self,
        shadow_mode: bool = SHADOW_MODE_ENABLED,
        solver: str = "highs",
        time_limit_seconds: float = 10.0,
    ) -> None:
        """Initialize the optimization engine.

        Args:
            shadow_mode: If True, only log results without applying
            solver: Optimization solver to use ('highs', 'glpk', 'cbc')
            time_limit_seconds: Maximum solve time
        """
        self.shadow_mode = shadow_mode
        self.solver = solver
        self.time_limit_seconds = time_limit_seconds

        # Last optimization results
        self._last_schedule: ChargingSchedule | None = None
        self._last_optimization_time: datetime | None = None

    def run_optimization(
        self,
        data: CoordinatorData,
        now: datetime | None = None,
    ) -> ChargingSchedule | None:
        """Run optimization and return the optimal charging schedule.

        This method:
        1. Extracts data from CoordinatorData
        2. Builds and solves the optimization model
        3. Creates ChargingSchedule from results
        4. Compares with rule-based decisions
        5. Logs differences (shadow mode)

        Args:
            data: Current coordinator data with forecasts and state
            now: Current time (defaults to datetime.now())

        Returns:
            ChargingSchedule with optimal decisions, or None if optimization failed
        """
        if now is None:
            now = datetime.now()

        # Check if we have sufficient data
        if not self._can_optimize(data):
            _LOGGER.debug("Insufficient data for optimization")
            return None

        try:
            # Extract parameters from coordinator data
            initial_soc = data.soc / 100.0  # Convert % to 0.0-1.0
            target_soc = data.battery_target_soc / 100.0  # Convert % to 0.0-1.0

            # Get demand window start time
            demand_window_start = self._get_demand_window_start(data, now)

            # Forecast horizon: end of tomorrow
            forecast_horizon_end = (now + timedelta(days=2)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )

            # Build price forecast
            prices = self._build_price_forecast(data, now)

            # Build solar forecast
            solar_forecast = self._build_solar_forecast(data, now)

            # Build consumption forecast
            consumption_forecast = self._build_consumption_forecast(data, now)

            _LOGGER.info(
                "Running optimization: SOC=%.0f%% -> target=%.0f%%, "
                "prices=%d slots, solar=%d slots, consumption=%d slots",
                data.soc,
                data.battery_target_soc,
                len(prices),
                len(solar_forecast),
                len(consumption_forecast),
            )

            # Create and build the optimization model
            model = OptimizationModel()
            model.build_model(
                initial_soc=initial_soc,
                target_soc=target_soc,
                demand_window_start=demand_window_start,
                forecast_horizon_end=forecast_horizon_end,
                prices=prices,
                solar_forecast=solar_forecast,
                consumption_forecast=consumption_forecast,
                now=now,
            )

            # Solve the model
            results = model.solve(
                solver_name=self.solver,
                time_limit_seconds=self.time_limit_seconds,
            )

            if not results.get("is_optimal") and results.get("status") == "error":
                _LOGGER.error("Optimization failed: %s", results.get("error"))
                return None

            # Build charging schedule from results
            schedule = self._build_schedule(results, data, now)

            # Compare with rule-based decisions
            comparison = self._compare_with_rule_based(schedule, data, now)
            schedule.comparison_vs_rule = comparison.to_dict()

            # Log shadow mode comparison
            if self.shadow_mode:
                self._log_shadow_mode_comparison(schedule, comparison)

            # Store results
            self._last_schedule = schedule
            self._last_optimization_time = now

            return schedule

        except Exception as e:
            _LOGGER.error("Optimization engine error: %s", e, exc_info=True)
            return None

    def _can_optimize(self, data: CoordinatorData) -> bool:
        """Check if we have sufficient data to run optimization.

        Args:
            data: Coordinator data to check

        Returns:
            True if optimization can proceed
        """
        # Need current SOC
        if data.soc <= 0:
            _LOGGER.debug("Missing current SOC")
            return False

        # Need price data
        if not data.prices_available:
            _LOGGER.debug("Price data not available")
            return False

        # Need at least some price forecast
        if not data.general_forecast:
            _LOGGER.debug("Missing price forecast")
            return False

        # Need consumption profile
        if not data.consumption_hourly_profile_kw:
            _LOGGER.debug("Missing consumption profile")
            return False

        return True

    def _get_demand_window_start(
        self, data: CoordinatorData, now: datetime
    ) -> datetime | None:
        """Get the demand window start time.

        Args:
            data: Coordinator data
            now: Current time

        Returns:
            Demand window start datetime, or None if not applicable
        """
        # Parse demand window start from config (HH:MM:SS format)
        # This is stored in the config entry options
        # For now, use a default of 15:00
        dw_start_time = "15:00:00"

        # Today's demand window
        hour, minute, second = map(int, dw_start_time.split(":"))
        dw_today = now.replace(hour=hour, minute=minute, second=second, microsecond=0)

        # If we've passed today's DW, use tomorrow's
        if now >= dw_today:
            return dw_today + timedelta(days=1)
        return dw_today

    def _build_price_forecast(
        self, data: CoordinatorData, now: datetime
    ) -> list[dict[str, Any]]:
        """Build price forecast for optimization.

        Args:
            data: Coordinator data with price forecasts
            now: Current time

        Returns:
            List of price forecast dictionaries
        """
        prices = []

        # Add current price
        if data.general_price > 0:
            prices.append(
                {
                    "datetime": now.isoformat(),
                    "price_per_kwh": data.general_price,
                }
            )

        # Add forecast prices
        for forecast in data.general_forecast:
            fc_time = forecast.get("datetime")
            if fc_time is None:
                continue

            price = forecast.get("price_per_kwh") or forecast.get("price")
            if price is not None:
                prices.append(
                    {
                        "datetime": fc_time,
                        "price_per_kwh": float(price),
                    }
                )

        return prices

    def _build_solar_forecast(
        self, data: CoordinatorData, now: datetime
    ) -> list[dict[str, Any]]:
        """Build solar generation forecast for optimization.

        Args:
            data: Coordinator data with Solcast forecasts
            now: Current time

        Returns:
            List of solar forecast dictionaries
        """
        solar = []

        # Combine today and tomorrow forecasts
        solcast_forecasts = data.solcast_today + data.solcast_tomorrow

        for forecast in solcast_forecasts:
            fc_time = forecast.get("period_start") or forecast.get("datetime")
            if fc_time is None:
                continue

            # Handle both datetime objects and strings
            if isinstance(fc_time, str):
                try:
                    fc_time = datetime.fromisoformat(fc_time)
                except (ValueError, TypeError):
                    continue

            # Skip past forecasts
            if fc_time < now:
                continue

            # Get expected power (kW)
            power_kw = forecast.get("pv_estimate") or forecast.get("power_kw") or 0
            if isinstance(power_kw, (int, float)):
                solar.append(
                    {
                        "datetime": fc_time.isoformat(),
                        "power_kw": float(power_kw),
                    }
                )

        return solar

    def _build_consumption_forecast(
        self, data: CoordinatorData, now: datetime
    ) -> list[dict[str, Any]]:
        """Build consumption forecast for optimization.

        Uses the hourly consumption profile from coordinator data.

        Args:
            data: Coordinator data with consumption profile
            now: Current time

        Returns:
            List of consumption forecast dictionaries
        """
        consumption = []

        # Get the consumption profile (hour -> kW)
        profile = data.consumption_hourly_profile_kw

        if not profile:
            return consumption

        # Generate forecast for next 48 hours
        for hour_offset in range(48):
            t = now + timedelta(hours=hour_offset)
            hour = t.hour

            # Get consumption for this hour from profile
            power_kw = profile.get(hour, 0.5)  # Default 500W if missing

            consumption.append(
                {
                    "datetime": t.isoformat(),
                    "power_kw": power_kw,
                }
            )

        return consumption

    def _build_schedule(
        self,
        results: dict[str, Any],
        data: CoordinatorData,
        now: datetime,
    ) -> ChargingSchedule:
        """Build ChargingSchedule from optimization results.

        Args:
            results: Optimization results dictionary
            data: Original coordinator data
            now: Current time

        Returns:
            ChargingSchedule with all slot details
        """
        schedule = ChargingSchedule(
            optimization_time=now,
            solve_time_seconds=results.get("solve_time", 0.0),
            solver_status=results.get("status", "unknown"),
            is_optimal=results.get("is_optimal", False),
        )

        time_slots = results.get("time_slots", [])
        grid_charges = results.get("grid_charge", [])
        soc_values = results.get("soc", [])

        if not time_slots:
            return schedule

        # Build slots
        for i, slot_start in enumerate(time_slots):
            slot_end = slot_start + timedelta(minutes=30)

            grid_charge_kw = grid_charges[i] if i < len(grid_charges) else 0.0
            soc_start = soc_values[i - 1] if i > 0 and i - 1 < len(soc_values) else data.soc / 100
            soc_end = soc_values[i] if i < len(soc_values) else soc_start

            # Get price for this slot
            price = data.general_price  # Default to current price
            for fc in data.general_forecast:
                fc_time = fc.get("datetime")
                if isinstance(fc_time, str):
                    try:
                        fc_time = datetime.fromisoformat(fc_time)
                    except (ValueError, TypeError):
                        continue
                if fc_time and slot_start <= fc_time < slot_end:
                    price = fc.get("price_per_kwh") or fc.get("price") or price
                    break

            # Calculate cost
            slot_duration_hours = 0.5  # 30 minutes
            cost = grid_charge_kw * price * slot_duration_hours

            # Determine reason
            reason = ""
            if grid_charge_kw > 0:
                if price <= data.effective_cheap_price:
                    reason = f"Grid charging at low price (${price:.3f}/kWh)"
                else:
                    reason = f"Grid charging to meet target (${price:.3f}/kWh)"
            else:
                reason = "No grid charging"

            slot = ChargingSlot(
                slot_start=slot_start,
                slot_end=slot_end,
                grid_charge_kw=grid_charge_kw,
                soc_start=soc_start * 100,  # Convert to %
                soc_end=soc_end * 100,  # Convert to %
                price_per_kwh=price,
                cost=cost,
                reason=reason,
            )
            schedule.slots.append(slot)

            # Add to SOC trajectory
            schedule.soc_trajectory.append((slot_start, soc_start * 100))

        # Calculate totals
        schedule.total_grid_charge_kwh = sum(
            s.grid_charge_kw * 0.5 for s in schedule.slots  # 30-min slots
        )
        schedule.total_cost = sum(s.cost for s in schedule.slots)

        # Get final SOC and DW SOC
        if schedule.slots:
            schedule.final_soc = schedule.slots[-1].soc_end

            # Find DW slot
            dw_start = self._get_demand_window_start(data, now)
            if dw_start:
                for slot in schedule.slots:
                    if slot.slot_start >= dw_start:
                        schedule.demand_window_target_soc = slot.soc_end
                        break

        return schedule

    def _compare_with_rule_based(
        self,
        schedule: ChargingSchedule,
        data: CoordinatorData,
        now: datetime,
    ) -> OptimizationComparison:
        """Compare optimized schedule with rule-based decisions.

        Args:
            schedule: Optimized charging schedule
            data: Coordinator data with rule-based decisions
            now: Current time

        Returns:
            OptimizationComparison with differences
        """
        comparison = OptimizationComparison()

        # Get rule-based charging decisions
        rule_decisions = data.forecast_charging_decisions

        # Calculate rule-based totals
        rule_grid_charge_kwh = 0.0
        rule_cost = 0.0

        for decision in rule_decisions:
            if decision.should_grid_charge or decision.should_boost:
                charge_kw = decision.charge_amount_kwh / 0.5 if decision.charge_amount_kwh > 0 else 3.3  # Assume 30-min slot
                rule_grid_charge_kwh += decision.charge_amount_kwh
                rule_cost += decision.charge_amount_kwh * decision.price_per_kwh

        comparison.rule_grid_charge_kwh = rule_grid_charge_kwh
        comparison.rule_total_cost = rule_cost
        comparison.rule_final_soc = data.solar_battery_forecast.get("final_soc", data.soc)
        comparison.rule_dw_soc = data.solar_battery_forecast.get("predicted_soc", data.soc)

        # Optimized values
        comparison.optimized_grid_charge_kwh = schedule.total_grid_charge_kwh
        comparison.optimized_total_cost = schedule.total_cost
        comparison.optimized_final_soc = schedule.final_soc
        comparison.optimized_dw_soc = schedule.demand_window_target_soc

        # Calculate savings
        comparison.cost_savings = rule_cost - schedule.total_cost
        if rule_cost > 0:
            comparison.cost_savings_pct = (
                comparison.cost_savings / rule_cost * 100
            )

        # Find decision differences
        comparison.decision_differences = self._find_decision_differences(
            schedule, rule_decisions, now
        )

        # Generate key differences summary
        comparison.key_differences = self._generate_key_differences(comparison)

        return comparison

    def _find_decision_differences(
        self,
        schedule: ChargingSchedule,
        rule_decisions: list[Any],
        now: datetime,
    ) -> list[dict[str, Any]]:
        """Find slots where optimized and rule-based decisions differ.

        Args:
            schedule: Optimized schedule
            rule_decisions: Rule-based charging decisions
            now: Current time

        Returns:
            List of difference dictionaries
        """
        differences = []

        for slot in schedule.get_grid_charging_slots():
            # Find corresponding rule-based decision
            rule_decision = None
            for decision in rule_decisions:
                if abs((decision.slot_start - slot.slot_start).total_seconds()) < 1800:
                    rule_decision = decision
                    break

            if rule_decision:
                rule_charge = rule_decision.charge_amount_kwh / 0.5 if rule_decision.charge_amount_kwh > 0 else 0
                opt_charge = slot.grid_charge_kw

                # Check if significantly different (> 0.5 kW difference)
                if abs(rule_charge - opt_charge) > 0.5:
                    differences.append(
                        {
                            "slot_start": slot.slot_start.isoformat(),
                            "rule_charge_kw": rule_charge,
                            "optimized_charge_kw": opt_charge,
                            "price": slot.price_per_kwh,
                            "difference_kw": opt_charge - rule_charge,
                        }
                    )
            else:
                # Optimized wants to charge, rule-based doesn't
                differences.append(
                    {
                        "slot_start": slot.slot_start.isoformat(),
                        "rule_charge_kw": 0,
                        "optimized_charge_kw": slot.grid_charge_kw,
                        "price": slot.price_per_kwh,
                        "difference_kw": slot.grid_charge_kw,
                    }
                )

        return differences

    def _generate_key_differences(
        self, comparison: OptimizationComparison
    ) -> list[str]:
        """Generate human-readable summary of key differences.

        Args:
            comparison: Comparison results

        Returns:
            List of summary strings
        """
        differences = []

        if comparison.cost_savings > 0.01:
            differences.append(
                f"Optimization saves ${comparison.cost_savings:.2f} "
                f"({comparison.cost_savings_pct:.1f}%)"
            )
        elif comparison.cost_savings < -0.01:
            differences.append(
                f"Optimization costs ${-comparison.cost_savings:.2f} more"
            )

        if abs(comparison.optimized_grid_charge_kwh - comparison.rule_grid_charge_kwh) > 0.5:
            diff = comparison.optimized_grid_charge_kwh - comparison.rule_grid_charge_kwh
            if diff > 0:
                differences.append(f"Optimization charges {diff:.1f} kWh more")
            else:
                differences.append(f"Optimization charges {-diff:.1f} kWh less")

        if abs(comparison.optimized_dw_soc - comparison.rule_dw_soc) > 5:
            diff = comparison.optimized_dw_soc - comparison.rule_dw_soc
            if diff > 0:
                differences.append(
                    f"Optimization reaches {diff:.0f}% higher SOC at demand window"
                )
            else:
                differences.append(
                    f"Optimization reaches {-diff:.0f}% lower SOC at demand window"
                )

        if not differences:
            differences.append("No significant differences detected")

        return differences

    def _log_shadow_mode_comparison(
        self, schedule: ChargingSchedule, comparison: OptimizationComparison
    ) -> None:
        """Log shadow mode comparison for analysis.

        Args:
            schedule: Optimized schedule
            comparison: Comparison with rule-based
        """
        _LOGGER.info(
            "[SHADOW MODE] Optimization complete: "
            "solve_time=%.2fs, optimal=%s, cost=$%.2f, "
            "grid_charge=%.1f kWh, final_soc=%.0f%%, dw_soc=%.0f%%",
            schedule.solve_time_seconds,
            schedule.is_optimal,
            schedule.total_cost,
            schedule.total_grid_charge_kwh,
            schedule.final_soc,
            schedule.demand_window_target_soc,
        )

        _LOGGER.info(
            "[SHADOW MODE] Comparison vs rule-based: "
            "cost_savings=$%.2f (%.1f%%), "
            "rule_charge=%.1f kWh, opt_charge=%.1f kWh",
            comparison.cost_savings,
            comparison.cost_savings_pct,
            comparison.rule_grid_charge_kwh,
            comparison.optimized_grid_charge_kwh,
        )

        for diff in comparison.key_differences:
            _LOGGER.info("[SHADOW MODE] Key difference: %s", diff)

        # Log detailed decision differences at debug level
        for diff in comparison.decision_differences[:5]:  # Limit to first 5
            _LOGGER.debug(
                "[SHADOW MODE] Decision diff at %s: rule=%.1f kW, opt=%.1f kW",
                diff["slot_start"],
                diff["rule_charge_kw"],
                diff["optimized_charge_kw"],
            )

    def get_last_schedule(self) -> ChargingSchedule | None:
        """Get the last computed schedule."""
        return self._last_schedule

    def get_last_optimization_time(self) -> datetime | None:
        """Get the time of the last optimization."""
        return self._last_optimization_time

    def should_run_optimization(self, now: datetime) -> bool:
        """Check if enough time has passed since last optimization.

        Args:
            now: Current time

        Returns:
            True if optimization should run
        """
        if self._last_optimization_time is None:
            return True

        elapsed = (now - self._last_optimization_time).total_seconds() / 60
        return elapsed >= OPTIMIZATION_INTERVAL_MINUTES