"""Charging schedule data structures for the optimization engine.

Issue #363: MVP Optimization Engine Prototype
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class ChargingSlot:
    """Represents a single charging time slot in the schedule.

    Attributes:
        slot_start: Start time of the charging slot
        slot_end: End time of the charging slot
        grid_charge_kw: Grid charging power in kW (0 = no grid charging)
        solar_charge_kw: Solar charging power in kW (0 = no solar charging)
        discharge_kw: Discharge power in kW (0 = no discharge)
        soc_start: State of charge at slot start (%)
        soc_end: Predicted state of charge at slot end (%)
        price_per_kwh: Grid price during this slot ($/kWh)
        cost: Total cost for this slot ($)
        reason: Human-readable explanation for the decision
    """

    slot_start: datetime
    slot_end: datetime
    grid_charge_kw: float = 0.0
    solar_charge_kw: float = 0.0
    discharge_kw: float = 0.0
    soc_start: float = 0.0
    soc_end: float = 0.0
    price_per_kwh: float = 0.0
    cost: float = 0.0
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "slot_start": self.slot_start.isoformat(),
            "slot_end": self.slot_end.isoformat(),
            "grid_charge_kw": self.grid_charge_kw,
            "solar_charge_kw": self.solar_charge_kw,
            "discharge_kw": self.discharge_kw,
            "soc_start": self.soc_start,
            "soc_end": self.soc_end,
            "price_per_kwh": self.price_per_kwh,
            "cost": self.cost,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ChargingSlot:
        """Create from dictionary (deserialization)."""
        return cls(
            slot_start=datetime.fromisoformat(data["slot_start"]),
            slot_end=datetime.fromisoformat(data["slot_end"]),
            grid_charge_kw=data.get("grid_charge_kw", 0.0),
            solar_charge_kw=data.get("solar_charge_kw", 0.0),
            discharge_kw=data.get("discharge_kw", 0.0),
            soc_start=data.get("soc_start", 0.0),
            soc_end=data.get("soc_end", 0.0),
            price_per_kwh=data.get("price_per_kwh", 0.0),
            cost=data.get("cost", 0.0),
            reason=data.get("reason", ""),
        )


@dataclass
class ChargingSchedule:
    """Complete charging schedule from optimization.

    This is the output of the optimization engine, containing:
    - Full schedule of charging slots
    - SOC trajectory over time
    - Total cost and summary metrics
    - Comparison with rule-based decisions

    Attributes:
        slots: List of charging slots in chronological order
        soc_trajectory: List of (datetime, SOC) pairs for plotting
        total_cost: Total expected cost for the schedule ($)
        total_grid_charge_kwh: Total grid charging energy (kWh)
        total_solar_charge_kwh: Total solar charging energy (kWh)
        total_discharge_kwh: Total discharge energy (kWh)
        final_soc: Predicted final SOC (%)
        demand_window_target_soc: Predicted SOC at demand window start (%)
        optimization_time: Time when optimization was run
        solve_time_seconds: Time to solve the optimization (seconds)
        solver_status: Pyomo solver status string
        is_optimal: Whether an optimal solution was found
        comparison_vs_rule: Comparison metrics vs rule-based approach
    """

    slots: list[ChargingSlot] = field(default_factory=list)
    soc_trajectory: list[tuple[datetime, float]] = field(default_factory=list)
    total_cost: float = 0.0
    total_grid_charge_kwh: float = 0.0
    total_solar_charge_kwh: float = 0.0
    total_discharge_kwh: float = 0.0
    final_soc: float = 0.0
    demand_window_target_soc: float = 0.0
    optimization_time: datetime | None = None
    solve_time_seconds: float = 0.0
    solver_status: str = "not_run"
    is_optimal: bool = False
    comparison_vs_rule: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "slots": [slot.to_dict() for slot in self.slots],
            "soc_trajectory": [
                (dt.isoformat(), soc) for dt, soc in self.soc_trajectory
            ],
            "total_cost": self.total_cost,
            "total_grid_charge_kwh": self.total_grid_charge_kwh,
            "total_solar_charge_kwh": self.total_solar_charge_kwh,
            "total_discharge_kwh": self.total_discharge_kwh,
            "final_soc": self.final_soc,
            "demand_window_target_soc": self.demand_window_target_soc,
            "optimization_time": (
                self.optimization_time.isoformat() if self.optimization_time else None
            ),
            "solve_time_seconds": self.solve_time_seconds,
            "solver_status": self.solver_status,
            "is_optimal": self.is_optimal,
            "comparison_vs_rule": self.comparison_vs_rule,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ChargingSchedule:
        """Create from dictionary (deserialization)."""
        return cls(
            slots=[ChargingSlot.from_dict(s) for s in data.get("slots", [])],
            soc_trajectory=[
                (datetime.fromisoformat(dt), soc)
                for dt, soc in data.get("soc_trajectory", [])
            ],
            total_cost=data.get("total_cost", 0.0),
            total_grid_charge_kwh=data.get("total_grid_charge_kwh", 0.0),
            total_solar_charge_kwh=data.get("total_solar_charge_kwh", 0.0),
            total_discharge_kwh=data.get("total_discharge_kwh", 0.0),
            final_soc=data.get("final_soc", 0.0),
            demand_window_target_soc=data.get("demand_window_target_soc", 0.0),
            optimization_time=(
                datetime.fromisoformat(data["optimization_time"])
                if data.get("optimization_time")
                else None
            ),
            solve_time_seconds=data.get("solve_time_seconds", 0.0),
            solver_status=data.get("solver_status", "not_run"),
            is_optimal=data.get("is_optimal", False),
            comparison_vs_rule=data.get("comparison_vs_rule", {}),
        )

    def get_grid_charging_slots(self) -> list[ChargingSlot]:
        """Return only slots with grid charging."""
        return [s for s in self.slots if s.grid_charge_kw > 0]

    def get_discharge_slots(self) -> list[ChargingSlot]:
        """Return only slots with discharge."""
        return [s for s in self.slots if s.discharge_kw > 0]

    def get_total_grid_charge_cost(self) -> float:
        """Return total cost of grid charging."""
        return sum(s.cost for s in self.slots if s.grid_charge_kw > 0)


@dataclass
class OptimizationComparison:
    """Comparison between optimized and rule-based decisions.

    Used for shadow mode logging to understand differences
    between the optimization engine and current rule-based logic.

    Attributes:
        rule_total_cost: Total cost using rule-based approach ($)
        optimized_total_cost: Total cost using optimization ($)
        cost_savings: Cost savings from optimization ($)
        cost_savings_pct: Cost savings percentage (%)
        rule_grid_charge_kwh: Grid charging with rule-based (kWh)
        optimized_grid_charge_kwh: Grid charging with optimization (kWh)
        rule_final_soc: Final SOC with rule-based (%)
        optimized_final_soc: Final SOC with optimization (%)
        rule_dw_soc: SOC at demand window with rule-based (%)
        optimized_dw_soc: SOC at demand window with optimization (%)
        decision_differences: List of slots where decisions differ
        key_differences: Human-readable summary of key differences
    """

    rule_total_cost: float = 0.0
    optimized_total_cost: float = 0.0
    cost_savings: float = 0.0
    cost_savings_pct: float = 0.0
    rule_grid_charge_kwh: float = 0.0
    optimized_grid_charge_kwh: float = 0.0
    rule_final_soc: float = 0.0
    optimized_final_soc: float = 0.0
    rule_dw_soc: float = 0.0
    optimized_dw_soc: float = 0.0
    decision_differences: list[dict[str, Any]] = field(default_factory=list)
    key_differences: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "rule_total_cost": self.rule_total_cost,
            "optimized_total_cost": self.optimized_total_cost,
            "cost_savings": self.cost_savings,
            "cost_savings_pct": self.cost_savings_pct,
            "rule_grid_charge_kwh": self.rule_grid_charge_kwh,
            "optimized_grid_charge_kwh": self.optimized_grid_charge_kwh,
            "rule_final_soc": self.rule_final_soc,
            "optimized_final_soc": self.optimized_final_soc,
            "rule_dw_soc": self.rule_dw_soc,
            "optimized_dw_soc": self.optimized_dw_soc,
            "decision_differences": self.decision_differences,
            "key_differences": self.key_differences,
        }