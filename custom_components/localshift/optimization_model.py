"""Pyomo optimization model for battery charging schedule.

Issue #363: MVP Optimization Engine Prototype

This module defines the mathematical optimization model for finding
the optimal grid charging schedule to minimize cost while meeting
battery SOC targets.

Uses HiGHS solver via Pyomo for fast LP/MIP solving.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from pyomo import environ as pyo

_LOGGER = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# Constants (MVP - Hard Limits)
# -----------------------------------------------------------------------------

# Battery hardware constraints
BATTERY_CAPACITY = 13.5  # kWh - Powerwall 2
INVERTER_MAX = 5.0  # kW - hardware limit
GRID_CHARGE_MAX = 3.3  # kW - backup mode rate
SOC_MIN = 0.20  # 20% safety floor

# Efficiency parameters (defaults, can be overridden)
CHARGE_EFFICIENCY = 0.92  # AC→DC
DISCHARGE_EFFICIENCY = 0.95  # DC→AC

# Time resolution for optimization
SLOT_DURATION_MINUTES = 30  # 30-minute time slots


class OptimizationModel:
    """Pyomo optimization model for battery charging.

    This model optimizes grid charging decisions to minimize cost
    while ensuring the battery reaches target SOC by the demand window.

    Decision Variables:
        - grid_charge[t]: Grid charging power at time t (kW)
        - soc[t]: State of charge at time t (%)

    Objective:
        - Minimize total grid charging cost

    Constraints:
        - SOC balance equations (charge/discharge efficiency)
        - SOC minimum floor (20%)
        - SOC target at demand window
        - Grid charging rate limit (3.3 kW)
        - Solar charging from forecast (input parameter)
        - Load consumption from forecast (input parameter)
    """

    def __init__(
        self,
        battery_capacity_kwh: float = BATTERY_CAPACITY,
        grid_charge_max_kw: float = GRID_CHARGE_MAX,
        soc_min: float = SOC_MIN,
        charge_efficiency: float = CHARGE_EFFICIENCY,
        discharge_efficiency: float = DISCHARGE_EFFICIENCY,
        slot_duration_minutes: int = SLOT_DURATION_MINUTES,
    ):
        """Initialize optimization model with parameters.

        Args:
            battery_capacity_kwh: Battery capacity in kWh
            grid_charge_max_kw: Maximum grid charging rate in kW
            soc_min: Minimum SOC floor (0.0-1.0)
            charge_efficiency: AC→DC charging efficiency (0.0-1.0)
            discharge_efficiency: DC→AC discharge efficiency (0.0-1.0)
            slot_duration_minutes: Time slot duration in minutes
        """
        self.battery_capacity_kwh = battery_capacity_kwh
        self.grid_charge_max_kw = grid_charge_max_kw
        self.soc_min = soc_min
        self.charge_efficiency = charge_efficiency
        self.discharge_efficiency = discharge_efficiency
        self.slot_duration_minutes = slot_duration_minutes
        self.slot_duration_hours = slot_duration_minutes / 60.0

        # Model instance (created at solve time)
        self._model: pyo.ConcreteModel | None = None
        self._results: Any = None

    def build_model(
        self,
        initial_soc: float,
        target_soc: float,
        demand_window_start: datetime | None,
        forecast_horizon_end: datetime,
        prices: list[dict[str, Any]],
        solar_forecast: list[dict[str, Any]],
        consumption_forecast: list[dict[str, Any]],
        now: datetime,
    ) -> pyo.ConcreteModel:
        """Build the Pyomo optimization model.

        Args:
            initial_soc: Current battery SOC (0.0-1.0)
            target_soc: Target SOC to reach (0.0-1.0)
            demand_window_start: When demand window starts (target deadline)
            forecast_horizon_end: End of forecast horizon
            prices: List of price forecasts [{datetime, price_per_kwh}, ...]
            solar_forecast: List of solar forecasts [{datetime, power_kw}, ...]
            consumption_forecast: List of consumption forecasts [{datetime, power_kw}, ...]
            now: Current time

        Returns:
            Pyomo ConcreteModel ready for solving
        """
        model = pyo.ConcreteModel(name="LocalShift_Charging_Optimization")

        # ---------------------------------------------------------------------
        # Generate time slots
        # ---------------------------------------------------------------------
        time_slots = []
        t = now
        while t < forecast_horizon_end:
            time_slots.append(t)
            t += timedelta(minutes=self.slot_duration_minutes)

        if not time_slots:
            _LOGGER.warning("No time slots in optimization horizon")
            time_slots = [now]

        num_slots = len(time_slots)
        slot_idx = list(range(num_slots))

        # Find demand window slot index
        dw_slot_idx = None
        if demand_window_start:
            for i, slot in enumerate(time_slots):
                if slot >= demand_window_start:
                    dw_slot_idx = i
                    break
            if dw_slot_idx is None:
                dw_slot_idx = num_slots - 1  # Default to last slot

        # ---------------------------------------------------------------------
        # Build price, solar, consumption arrays
        # ---------------------------------------------------------------------
        price_array = self._build_array(prices, time_slots, "price_per_kwh", 0.0)
        solar_array = self._build_array(solar_forecast, time_slots, "power_kw", 0.0)
        consumption_array = self._build_array(
            consumption_forecast, time_slots, "power_kw", 0.0
        )

        # ---------------------------------------------------------------------
        # Sets
        # ---------------------------------------------------------------------
        model.T = pyo.Set(initialize=slot_idx, ordered=True)

        # ---------------------------------------------------------------------
        # Parameters
        # ---------------------------------------------------------------------
        model.initial_soc = pyo.Param(initialize=initial_soc, within=pyo.Reals)
        model.target_soc = pyo.Param(initialize=target_soc, within=pyo.Reals)
        model.soc_min = pyo.Param(initialize=self.soc_min, within=pyo.Reals)
        model.battery_capacity = pyo.Param(
            initialize=self.battery_capacity_kwh, within=pyo.Reals
        )
        model.grid_charge_max = pyo.Param(
            initialize=self.grid_charge_max_kw, within=pyo.Reals
        )
        model.charge_eff = pyo.Param(
            initialize=self.charge_efficiency, within=pyo.Reals
        )
        model.discharge_eff = pyo.Param(
            initialize=self.discharge_efficiency, within=pyo.Reals
        )
        model.slot_duration = pyo.Param(
            initialize=self.slot_duration_hours, within=pyo.Reals
        )

        # Time-varying parameters
        def price_init(model, t):
            return price_array[t]

        model.price = pyo.Param(model.T, initialize=price_init, within=pyo.Reals)

        def solar_init(model, t):
            return solar_array[t]

        model.solar = pyo.Param(model.T, initialize=solar_init, within=pyo.Reals)

        def consumption_init(model, t):
            return consumption_array[t]

        model.consumption = pyo.Param(
            model.T, initialize=consumption_init, within=pyo.Reals
        )

        # Demand window index
        model.dw_slot = pyo.Param(
            initialize=dw_slot_idx if dw_slot_idx is not None else num_slots - 1,
            within=pyo.Integers,
        )

        # ---------------------------------------------------------------------
        # Decision Variables
        # ---------------------------------------------------------------------
        # Grid charging power (kW) - continuous, non-negative
        model.grid_charge = pyo.Var(
            model.T, bounds=(0, self.grid_charge_max_kw), within=pyo.Reals
        )

        # SOC at each time slot (0.0-1.0)
        model.soc = pyo.Var(model.T, bounds=(0, 1), within=pyo.Reals)

        # ---------------------------------------------------------------------
        # Constraints
        # ---------------------------------------------------------------------

        # Initial SOC constraint
        def initial_soc_rule(model):
            return model.soc[0] == model.initial_soc

        model.initial_soc_con = pyo.Constraint(rule=initial_soc_rule)

        # SOC balance equations for each time slot
        def soc_balance_rule(model, t):
            if t == 0:
                return pyo.Constraint.Skip

            # Energy flows in this slot (kWh)
            grid_energy = model.grid_charge[t] * model.slot_duration
            solar_energy = model.solar[t] * model.slot_duration
            consumption_energy = model.consumption[t] * model.slot_duration

            # Net energy change accounting for efficiency
            # Charging: grid + solar (AC→DC efficiency)
            # Discharging: consumption (DC→AC efficiency)
            charge_energy = (grid_energy + solar_energy) * model.charge_eff
            discharge_energy = consumption_energy / model.discharge_eff

            # SOC change (energy / capacity)
            soc_change = (charge_energy - discharge_energy) / model.battery_capacity

            return model.soc[t] == model.soc[t - 1] + soc_change

        model.soc_balance_con = pyo.Constraint(model.T, rule=soc_balance_rule)

        # SOC minimum floor constraint
        def soc_min_rule(model, t):
            return model.soc[t] >= model.soc_min

        model.soc_min_con = pyo.Constraint(model.T, rule=soc_min_rule)

        # SOC target at demand window
        def soc_target_rule(model):
            if model.dw_slot is None:
                return pyo.Constraint.Skip
            return model.soc[model.dw_slot] >= model.target_soc

        model.soc_target_con = pyo.Constraint(rule=soc_target_rule)

        # ---------------------------------------------------------------------
        # Objective: Minimize total grid charging cost
        # ---------------------------------------------------------------------
        def objective_rule(model):
            total_cost = sum(
                model.grid_charge[t]
                * model.price[t]
                * model.slot_duration  # kW * $/kWh * h = $
                for t in model.T
            )
            return total_cost

        model.objective = pyo.Objective(rule=objective_rule, sense=pyo.minimize)

        # Store time slots for result extraction
        model._time_slots = time_slots

        self._model = model
        return model

    def _build_array(
        self,
        forecasts: list[dict[str, Any]],
        time_slots: list[datetime],
        value_key: str,
        default: float,
    ) -> list[float]:
        """Build a value array aligned to time slots.

        Args:
            forecasts: List of forecast dictionaries
            time_slots: List of time slot start times
            value_key: Key to extract from forecast dict
            default: Default value when no forecast matches

        Returns:
            List of values aligned to time slots
        """
        result = []

        for slot_start in time_slots:
            slot_end = slot_start + timedelta(minutes=self.slot_duration_minutes)
            best_value = default
            best_overlap = timedelta(0)

            for forecast in forecasts:
                # Parse datetime from forecast
                fc_time = forecast.get("datetime")
                if isinstance(fc_time, str):
                    try:
                        fc_time = datetime.fromisoformat(fc_time)
                    except (ValueError, TypeError):
                        continue

                if fc_time is None:
                    continue

                # Check if forecast falls within this slot
                if slot_start <= fc_time < slot_end:
                    # Use this forecast
                    value = forecast.get(value_key, default)
                    if value is not None:
                        best_value = float(value)
                    break
                else:
                    # Check for overlap (forecast period might span slots)
                    fc_end = forecast.get("period_end")
                    if isinstance(fc_end, str):
                        try:
                            fc_end = datetime.fromisoformat(fc_end)
                        except (ValueError, TypeError):
                            fc_end = None

                    if fc_end and fc_time < slot_end and fc_end > slot_start:
                        overlap = min(slot_end, fc_end) - max(slot_start, fc_time)
                        if overlap > best_overlap:
                            best_overlap = overlap
                            value = forecast.get(value_key, default)
                            if value is not None:
                                best_value = float(value)

            result.append(best_value)

        return result

    def solve(
        self,
        solver_name: str = "highs",
        time_limit_seconds: float = 10.0,
        mip_gap: float = 0.01,
    ) -> dict[str, Any]:
        """Solve the optimization model.

        Args:
            solver_name: Solver to use ('highs', 'glpk', 'cbc')
            time_limit_seconds: Maximum solve time
            mip_gap: Acceptable optimality gap (1% = 0.01)

        Returns:
            Dictionary with solve results:
                - status: Solver status string
                - is_optimal: True if optimal solution found
                - solve_time: Solve time in seconds
                - grid_charge: List of grid charge values per slot
                - soc: List of SOC values per slot
                - total_cost: Total grid charging cost
        """
        if self._model is None:
            return {
                "status": "error",
                "is_optimal": False,
                "error": "Model not built",
            }

        try:
            # Create solver
            solver = pyo.SolverFactory(solver_name)

            # Set solver options
            if solver_name == "highs":
                solver.options["time_limit"] = time_limit_seconds
                solver.options["mip_gap"] = mip_gap
            elif solver_name == "glpk":
                solver.options["tmlim"] = int(time_limit_seconds)
                solver.options["mipgap"] = mip_gap

            # Solve
            import time

            start_time = time.time()
            self._results = solver.solve(self._model, tee=False)
            solve_time = time.time() - start_time

            # Extract results
            status = str(self._results.solver.status)
            termination_condition = str(self._results.solver.termination_condition)
            is_optimal = termination_condition == pyo.TerminationCondition.optimal

            # Extract variable values
            grid_charge = []
            soc_values = []

            if is_optimal or termination_condition in [
                pyo.TerminationCondition.feasible,
                "feasible",
            ]:
                for t in self._model.T:
                    gc_val = pyo.value(self._model.grid_charge[t])
                    soc_val = pyo.value(self._model.soc[t])
                    grid_charge.append(gc_val if gc_val is not None else 0.0)
                    soc_values.append(soc_val if soc_val is not None else 0.0)

                total_cost = pyo.value(self._model.objective)
            else:
                total_cost = None

            return {
                "status": status,
                "termination_condition": termination_condition,
                "is_optimal": is_optimal,
                "solve_time": solve_time,
                "grid_charge": grid_charge,
                "soc": soc_values,
                "total_cost": total_cost,
                "time_slots": self._model._time_slots,
            }

        except Exception as e:
            _LOGGER.error("Optimization solve failed: %s", e)
            return {
                "status": "error",
                "is_optimal": False,
                "error": str(e),
                "solve_time": 0.0,
            }

    def get_model(self) -> pyo.ConcreteModel | None:
        """Return the current Pyomo model."""
        return self._model