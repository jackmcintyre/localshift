"""Linear Programming optimizer for battery scheduling.

This module implements an LP-based approach to battery schedule optimization,
replacing the multi-pass heuristic system with a mathematically optimal solver.

Issue #396: POC for LP-based battery optimization.
"""

from __future__ import annotations

import asyncio
import functools
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)

# Lazy import to avoid startup overhead
_pulp = None
_HIGH_AVAILABLE = None


def _get_pulp():
    """Lazy load PuLP to avoid import overhead at startup."""
    global _pulp
    if _pulp is None:
        try:
            import pulp

            _pulp = pulp
        except ImportError:
            _LOGGER.warning("PuLP not installed - LP optimizer unavailable")
            return None
    return _pulp


@dataclass
class OptimizerConfig:
    """Configuration for LP optimizer."""

    battery_capacity_kwh: float = 13.5
    max_charge_rate_kw: float = 5.0
    max_discharge_rate_kw: float = 5.0
    charge_efficiency: float = 0.92
    discharge_efficiency: float = 0.95
    min_soc_kwh: float = 0.0
    solver_timeout_seconds: int = 5


@dataclass
class SlotData:
    """Pre-computed data for a single time slot."""

    time: datetime
    interval_minutes: int
    solar_kwh: float
    load_kwh: float
    buy_price: float
    sell_price: float


@dataclass
class SlotDecision:
    """Optimized decision for a single time slot."""

    time: datetime
    interval_minutes: int
    grid_import_kwh: float
    export_kwh: float
    soc_kwh: float
    solar_kwh: float
    load_kwh: float


class LPOptimizer:
    """Linear programming optimizer for battery scheduling.

    This optimizer formulates the battery scheduling problem as a linear
    program and uses PuLP + HiGHS to find the optimal solution.

    The optimization minimizes total electricity cost while ensuring:
    - Battery SOC meets target at demand window start
    - Battery stays within capacity and rate limits
    - Physical constraints (efficiency losses) are respected
    """

    def __init__(self, hass: Any, config: OptimizerConfig | None = None) -> None:
        """Initialize the LP optimizer.

        Args:
            hass: Home Assistant instance (for async executor)
            config: Optimizer configuration (uses defaults if not provided)
        """
        self._hass = hass
        self._config = config or OptimizerConfig()
        self._solver_available: bool | None = None

    async def async_is_available(self) -> bool:
        """Check if LP solver is available.

        Tests the solver with a tiny problem to verify it works.

        Returns:
            True if solver is available and working, False otherwise
        """
        if self._solver_available is not None:
            return self._solver_available

        try:
            pulp = _get_pulp()
            if pulp is None:
                self._solver_available = False
                return False

            # Test solve a tiny problem
            prob = pulp.LpProblem("test", pulp.LpMinimize)
            x = pulp.LpVariable("x", lowBound=0)
            prob += x
            prob += x >= 1

            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: prob.solve(self._get_solver(pulp)),
            )

            self._solver_available = result == pulp.LpStatusOptimal

            if self._solver_available:
                _LOGGER.info("LP optimizer available using HiGHS solver")
            else:
                _LOGGER.warning("LP solver test failed: %s", pulp.LpStatus[result])

        except Exception as e:
            _LOGGER.warning("LP solver not available: %s", e)
            self._solver_available = False

        return self._solver_available

    def _get_solver(self, pulp):
        """Get the LP solver with appropriate settings."""
        # Try HiGHS first (fast, pip-installable)
        try:
            return pulp.HiGHS_CMD(msg=False, timeLimit=self._config.solver_timeout_seconds)
        except Exception:
            pass

        # Fallback to CBC (included with PuLP)
        return pulp.PULP_CBC_CMD(msg=False, timeLimit=self._config.solver_timeout_seconds)

    async def async_optimize(
        self,
        slots: list[SlotData],
        current_soc_kwh: float,
        target_soc_kwh: float,
        target_slot_idx: int,
    ) -> dict[str, Any]:
        """Run optimization to find optimal battery schedule.

        Args:
            slots: Pre-computed slot data (solar, load, prices)
            current_soc_kwh: Current battery SOC in kWh
            target_soc_kwh: Target SOC at demand window start in kWh
            target_slot_idx: Index of the demand window start slot

        Returns:
            Dict with:
                - status: "optimal", "infeasible", "timeout", or "error"
                - schedule: List of SlotDecision (if optimal)
                - total_cost: Total electricity cost (if optimal)
                - message: Error message (if not optimal)
        """
        if not await self.async_is_available():
            return {
                "status": "unavailable",
                "schedule": None,
                "total_cost": None,
                "message": "LP solver not available",
            }

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            functools.partial(
                self._optimize_sync,
                slots=slots,
                current_soc_kwh=current_soc_kwh,
                target_soc_kwh=target_soc_kwh,
                target_slot_idx=target_slot_idx,
            ),
        )

    def _optimize_sync(
        self,
        slots: list[SlotData],
        current_soc_kwh: float,
        target_soc_kwh: float,
        target_slot_idx: int,
    ) -> dict[str, Any]:
        """Synchronous optimization - runs in thread pool.

        This formulates and solves the LP problem:
        - Decision variables: grid_import[t], export[t], soc[t] for each slot
        - Objective: minimize total cost
        - Constraints: SOC dynamics, capacity, rates, target
        """
        pulp = _get_pulp()
        if pulp is None:
            return {"status": "error", "message": "PuLP not available"}

        try:
            prob = pulp.LpProblem("Battery_Schedule", pulp.LpMinimize)
            n_slots = len(slots)

            if n_slots == 0:
                return {"status": "error", "message": "No slots provided"}

            # Decision variables
            # grid_import[t]: kWh imported from grid in slot t
            grid_import = [
                pulp.LpVariable(f"g_{t}", lowBound=0) for t in range(n_slots)
            ]

            # export[t]: kWh exported to grid in slot t
            export = [pulp.LpVariable(f"e_{t}", lowBound=0) for t in range(n_slots)]

            # soc[t]: battery SOC at end of slot t (kWh)
            soc = [
                pulp.LpVariable(
                    f"s_{t}",
                    lowBound=self._config.min_soc_kwh,
                    upBound=self._config.battery_capacity_kwh,
                )
                for t in range(n_slots)
            ]

            # Objective: minimize total cost
            # Cost = sum(grid_import * buy_price - export * sell_price)
            prob += pulp.lpSum(
                grid_import[t] * slots[t].buy_price - export[t] * slots[t].sell_price
                for t in range(n_slots)
            ), "Total_Cost"

            # Constraints
            for t in range(n_slots):
                slot_hours = slots[t].interval_minutes / 60.0

                # Previous SOC (current for t=0, or soc[t-1] for t>0)
                if t == 0:
                    prev_soc = current_soc_kwh
                else:
                    prev_soc = soc[t - 1]

                # SOC transition constraint
                # soc[t] = soc[t-1] + η_charge * (grid_import + solar) - (1/η_discharge) * (load + export)
                net_energy = (
                    self._config.charge_efficiency
                    * (grid_import[t] + slots[t].solar_kwh)
                    - (1.0 / self._config.discharge_efficiency)
                    * (slots[t].load_kwh + export[t])
                )
                prob += soc[t] == prev_soc + net_energy, f"SOC_Transition_{t}"

                # Charge rate limit
                max_import = self._config.max_charge_rate_kw * slot_hours
                prob += grid_import[t] <= max_import, f"Charge_Rate_{t}"

                # Discharge rate limit
                max_export = self._config.max_discharge_rate_kw * slot_hours
                prob += export[t] <= max_export, f"Discharge_Rate_{t}"

            # Target constraint: SOC at DW start >= target
            if target_slot_idx < n_slots:
                prob += (
                    soc[target_slot_idx] >= target_soc_kwh,
                    f"Target_SOC_{target_slot_idx}",
                )

            # Solve
            solver = self._get_solver(pulp)
            status = prob.solve(solver)

            # Check result
            status_str = pulp.LpStatus[status]

            if status_str != "Optimal":
                _LOGGER.warning(
                    "LP optimization did not find optimal solution: %s",
                    status_str,
                )
                return {
                    "status": status_str.lower(),
                    "schedule": None,
                    "total_cost": None,
                    "message": f"Solver returned: {status_str}",
                }

            # Extract solution
            schedule = []
            for t in range(n_slots):
                schedule.append(
                    SlotDecision(
                        time=slots[t].time,
                        interval_minutes=slots[t].interval_minutes,
                        grid_import_kwh=pulp.value(grid_import[t]) or 0.0,
                        export_kwh=pulp.value(export[t]) or 0.0,
                        soc_kwh=pulp.value(soc[t]) or 0.0,
                        solar_kwh=slots[t].solar_kwh,
                        load_kwh=slots[t].load_kwh,
                    )
                )

            total_cost = pulp.value(prob.objective) or 0.0

            _LOGGER.info(
                "LP optimization complete: status=%s, cost=$%.2f, slots=%d",
                status_str,
                total_cost,
                n_slots,
            )

            return {
                "status": "optimal",
                "schedule": schedule,
                "total_cost": total_cost,
                "message": None,
            }

        except Exception as e:
            _LOGGER.error("LP optimization failed: %s", e)
            return {"status": "error", "schedule": None, "total_cost": None, "message": str(e)}

    def get_grid_charge_slots(
        self,
        result: dict[str, Any],
        min_import_kwh: float = 0.1,
    ) -> list[dict[str, Any]]:
        """Extract grid charging slots from optimization result.

        Args:
            result: Result from async_optimize()
            min_import_kwh: Minimum import to consider as "charging"

        Returns:
            List of dicts with slot time and import amount
        """
        if result.get("status") != "optimal" or result.get("schedule") is None:
            return []

        charge_slots = []
        for slot in result["schedule"]:
            if slot.grid_import_kwh >= min_import_kwh:
                charge_slots.append(
                    {
                        "time": slot.time,
                        "interval_minutes": slot.interval_minutes,
                        "grid_import_kwh": slot.grid_import_kwh,
                    }
                )

        return charge_slots

    def get_export_slots(
        self,
        result: dict[str, Any],
        min_export_kwh: float = 0.1,
    ) -> list[dict[str, Any]]:
        """Export slots from optimization result.

        Args:
            result: Result from async_optimize()
            min_export_kwh: Minimum export to consider

        Returns:
            List of dicts with slot time and export amount
        """
        if result.get("status") != "optimal" or result.get("schedule") is None:
            return []

        export_slots = []
        for slot in result["schedule"]:
            if slot.export_kwh >= min_export_kwh:
                export_slots.append(
                    {
                        "time": slot.time,
                        "interval_minutes": slot.interval_minutes,
                        "export_kwh": slot.export_kwh,
                    }
                )

        return export_slots


def convert_forecast_to_slots(
    hybrid_slots: list[dict],
    all_solcast: list[dict],
    general_forecast: list[dict],
    fit_prices: dict[str, float],
    estimate_load_func,
    historical_avg_kw: dict[int, float],
    current_load_kw: float,
    recent_load_kw: float,
    current_hour: int | None = None,
) -> list[SlotData]:
    """Convert hybrid slot format to SlotData for optimizer.

    This is a bridge function to convert existing forecast data into
    the format expected by the LP optimizer.

    Args:
        hybrid_slots: List of slot dicts with 'start' and 'interval_minutes'
        all_solcast: Solcast forecast data
        general_forecast: Amber/general price forecast
        fit_prices: FIT prices by time period
        estimate_load_func: Function to estimate load (historical, hour, ...)
        historical_avg_kw: Historical load profile
        current_load_kw: Current load
        recent_load_kw: Recent average load
        current_hour: Current hour

    Returns:
        List of SlotData ready for optimization
    """
    from .solar_utils import get_solar_for_slot_by_interval

    slots = []

    for slot in hybrid_slots:
        slot_start = slot["start"]
        interval_minutes = slot["interval_minutes"]

        # Get solar
        solar_kwh = get_solar_for_slot_by_interval(all_solcast, slot_start, interval_minutes)

        # Get load
        load_kw, _ = estimate_load_func(
            historical_avg_kw,
            slot_start.hour,
            current_hour,
            current_load_kw,
            recent_load_kw,
        )
        slot_hours = interval_minutes / 60.0
        load_kwh = load_kw * slot_hours

        # Get buy price
        buy_price = _get_price_for_slot(general_forecast, slot_start)

        # Get sell price (FIT)
        sell_price = _get_fit_for_slot(slot_start, fit_prices)

        slots.append(
            SlotData(
                time=slot_start,
                interval_minutes=interval_minutes,
                solar_kwh=solar_kwh,
                load_kwh=load_kwh,
                buy_price=buy_price,
                sell_price=sell_price,
            )
        )

    return slots


def _get_price_for_slot(general_forecast: list[dict], slot_start: datetime) -> float:
    """Get buy price for a slot from general forecast."""
    from .price_calculator import get_price_for_slot

    price = get_price_for_slot(general_forecast, slot_start)
    return price if price is not None and price > 0 else 0.30  # Default fallback


def _get_fit_for_slot(slot_start: datetime, fit_prices: dict[str, float]) -> float:
    """Get FIT price for a slot."""
    from ..const import FIT_RATE_PEAK, FIT_RATE_OFF_PEAK, FIT_RATE_SHOULDER

    hour = slot_start.hour

    # Determine period
    if 14 <= hour < 20:  # Peak solar hours (negative FIT typically)
        return fit_prices.get("peak", FIT_RATE_PEAK)
    elif 7 <= hour < 14 or 20 <= hour < 22:  # Shoulder
        return fit_prices.get("shoulder", FIT_RATE_SHOULDER)
    else:  # Off-peak
        return fit_prices.get("off_peak", FIT_RATE_OFF_PEAK)