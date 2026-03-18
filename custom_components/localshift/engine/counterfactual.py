"""Counterfactual TOU baseline simulator for optimizer value measurement.

Issue #683: Implements a Time-of-Use (TOU) baseline strategy that charges during
the cheapest hours and discharges/self-consumes during peak hours. This provides
an objective measure of optimizer performance by comparing actual optimizer cost
against what a "smart but simple" TOU strategy would have achieved.

The TOU strategy:
- Charge during cheapest 4 hours of the day (typically overnight)
- Discharge/self-consume during peak hours
- No grid export unless battery is full and excess solar
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.util import dt as dt_util

from ..coordinator.data import PerformanceMetrics
from .optimizer_dp import PlannerAction

if TYPE_CHECKING:
    from ..coordinator.data import CoordinatorData

_LOGGER = logging.getLogger(__name__)

TOU_CHARGE_HOURS = 4
BATTERY_CAPACITY_KWH = 13.5
CHARGE_RATE_KW = 3.3
CHARGE_RATE_BOOST_KW = 5.0
DISCHARGE_RATE_KW = 5.0


@dataclass
class CounterfactualPeriod:
    """A single period in the counterfactual simulation."""

    start_time: datetime
    end_time: datetime
    tou_action: PlannerAction
    actual_action: PlannerAction
    price_per_kwh: float
    soc_start: float
    soc_end_tou: float
    soc_end_actual: float
    cost_tou: float
    cost_actual: float
    solar_kwh: float
    consumption_kwh: float

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat(),
            "tou_action": self.tou_action.value,
            "actual_action": self.actual_action.value,
            "price_per_kwh": self.price_per_kwh,
            "soc_start": self.soc_start,
            "soc_end_tou": self.soc_end_tou,
            "soc_end_actual": self.soc_end_actual,
            "cost_tou": self.cost_tou,
            "cost_actual": self.cost_actual,
            "solar_kwh": self.solar_kwh,
            "consumption_kwh": self.consumption_kwh,
        }


@dataclass
class CounterfactualResult:
    """Result of counterfactual simulation for a time period."""

    period_start: datetime
    period_end: datetime
    total_cost_tou: float
    total_cost_actual: float
    optimizer_advantage: float
    advantage_percent: float
    periods_simulated: int
    periods: list[CounterfactualPeriod] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "total_cost_tou": round(self.total_cost_tou, 4),
            "total_cost_actual": round(self.total_cost_actual, 4),
            "optimizer_advantage": round(self.optimizer_advantage, 4),
            "advantage_percent": round(self.advantage_percent, 2),
            "periods_simulated": self.periods_simulated,
            "periods": [p.to_dict() for p in self.periods],
        }


class TOUBaselineSimulator:
    """Simulates what a TOU baseline strategy would have done."""

    def __init__(self) -> None:
        """Initialize the TOU baseline simulator."""
        self._charge_hours: set[int] = set()
        self._daily_prices: dict[int, float] = {}

    def identify_cheapest_hours(
        self,
        price_forecast: list[dict[str, Any]],
        num_hours: int = TOU_CHARGE_HOURS,
    ) -> set[int]:
        """Identify the cheapest hours for charging from price forecast."""
        if not price_forecast:
            return {0, 1, 2, 3}

        hour_prices = []
        for entry in price_forecast:
            hour = entry.get("hour")
            price = entry.get("price")
            if hour is not None and price is not None:
                hour_prices.append((hour, price))

        if len(hour_prices) < num_hours:
            return set(range(num_hours))

        hour_prices.sort(key=lambda x: x[1])
        cheapest = {h for h, _ in hour_prices[:num_hours]}

        _LOGGER.debug(
            "TOU baseline: identified cheapest %d hours: %s",
            num_hours,
            sorted(cheapest),
        )

        return cheapest

    def simulate_tou_action(
        self,
        hour: int,
        soc: float,
        price: float,
        solar_kwh: float,
        consumption_kwh: float,
    ) -> tuple[PlannerAction, float, float]:
        """Simulate what TOU strategy would do in a given slot."""
        net_energy = solar_kwh - consumption_kwh

        if hour in self._charge_hours:
            if soc < 100.0:
                charge_needed = (100.0 - soc) * BATTERY_CAPACITY_KWH / 100.0
                max_charge = CHARGE_RATE_KW * 0.5
                actual_charge = min(charge_needed, max_charge)

                new_soc = min(soc + actual_charge * 100.0 / BATTERY_CAPACITY_KWH, 100.0)
                cost = actual_charge * price

                if actual_charge >= charge_needed * 0.9:
                    return PlannerAction.CHARGE_GRID_NORMAL, new_soc, cost
                return PlannerAction.CHARGE_GRID_BOOST, new_soc, cost

            return PlannerAction.HOLD, soc, 0.0

        if net_energy >= 0:
            charge_amount = min(
                net_energy,
                (100.0 - soc) * BATTERY_CAPACITY_KWH / 100.0,
            )
            new_soc = soc + charge_amount * 100.0 / BATTERY_CAPACITY_KWH

            if new_soc >= 99.0 and net_energy > charge_amount:
                export_kwh = net_energy - charge_amount
                revenue = export_kwh * price
                return PlannerAction.EXPORT_PROACTIVE, new_soc, -revenue

            return PlannerAction.HOLD, new_soc, 0.0

        discharge_needed = -net_energy
        max_discharge = min(
            discharge_needed,
            soc * BATTERY_CAPACITY_KWH / 100.0,
        )
        new_soc = soc - max_discharge * 100.0 / BATTERY_CAPACITY_KWH

        if max_discharge >= discharge_needed * 0.9:
            return PlannerAction.HOLD, new_soc, 0.0

        grid_kwh = discharge_needed - max_discharge
        cost = grid_kwh * price
        return PlannerAction.HOLD, new_soc, cost

    def simulate_period(
        self,
        decisions: list[Any],
        price_data: list[dict[str, Any]],
        solar_data: list[dict[str, Any]],
        consumption_data: list[dict[str, Any]],
    ) -> CounterfactualResult | None:
        """Run counterfactual simulation for a period."""
        if not decisions:
            return None

        self._charge_hours = self.identify_cheapest_hours(price_data)

        price_by_hour = {d["hour"]: d["price"] for d in price_data if "hour" in d}
        solar_by_hour = {
            d["hour"]: d.get("kwh", 0.0) for d in solar_data if "hour" in d
        }
        consumption_by_hour = {
            d["hour"]: d.get("kwh", 0.0) for d in consumption_data if "hour" in d
        }

        periods = []
        total_cost_tou = 0.0
        total_cost_actual = 0.0

        current_soc_tou = decisions[0].soc_at_decision if decisions else 50.0

        for decision in decisions:
            hour = decision.hour_of_day
            duration_hours = (decision.duration_minutes or 30.0) / 60.0

            price = price_by_hour.get(hour, decision.general_price_at_decision)
            solar_kwh = solar_by_hour.get(hour, 0.0) * duration_hours
            consumption_kwh = consumption_by_hour.get(hour, 0.5) * duration_hours

            tou_action, new_soc_tou, cost_tou = self.simulate_tou_action(
                hour,
                current_soc_tou,
                price,
                solar_kwh,
                consumption_kwh,
            )

            cost_actual = decision.actual_cost_during_period or 0.0
            soc_end_actual = decision.soc_at_decision + (
                decision.actual_soc_change or 0.0
            )

            period = CounterfactualPeriod(
                start_time=decision.timestamp,
                end_time=decision.timestamp
                + timedelta(minutes=decision.duration_minutes or 30.0),
                tou_action=tou_action,
                actual_action=decision.mode_chosen,
                price_per_kwh=price,
                soc_start=decision.soc_at_decision,
                soc_end_tou=new_soc_tou,
                soc_end_actual=soc_end_actual,
                cost_tou=cost_tou,
                cost_actual=cost_actual,
                solar_kwh=solar_kwh,
                consumption_kwh=consumption_kwh,
            )
            periods.append(period)

            total_cost_tou += cost_tou
            total_cost_actual += cost_actual
            current_soc_tou = new_soc_tou

        if not periods:
            return None

        optimizer_advantage = total_cost_tou - total_cost_actual
        advantage_percent = 0.0
        if abs(total_cost_tou) > 0.01:
            advantage_percent = (optimizer_advantage / abs(total_cost_tou)) * 100.0

        return CounterfactualResult(
            period_start=periods[0].start_time,
            period_end=periods[-1].end_time,
            total_cost_tou=total_cost_tou,
            total_cost_actual=total_cost_actual,
            optimizer_advantage=optimizer_advantage,
            advantage_percent=advantage_percent,
            periods_simulated=len(periods),
            periods=periods,
        )


class CounterfactualEvaluator:
    """Evaluates optimizer performance against TOU baseline."""

    def __init__(self) -> None:
        """Initialize the counterfactual evaluator."""
        self._simulator = TOUBaselineSimulator()
        self._daily_results: list[CounterfactualResult] = []
        self._last_evaluation: datetime | None = None

    def _build_price_data(self, data: CoordinatorData) -> list[dict[str, Any]]:
        """Build price data list from coordinator data."""
        price_data: list[dict[str, Any]] = []
        if hasattr(data, "general_forecast") and data.general_forecast:
            for entry in data.general_forecast:
                if isinstance(entry, dict):
                    price_data.append({
                        "hour": entry.get("hour", 0),
                        "price": entry.get("price", 0.0),
                    })
        if not price_data and hasattr(data, "general_price"):
            for h in range(24):
                price_data.append({"hour": h, "price": data.general_price})
        return price_data

    def _build_solar_data(self, data: CoordinatorData) -> list[dict[str, Any]]:
        """Build solar data list from coordinator data."""
        solar_data: list[dict[str, Any]] = []
        if hasattr(data, "solcast_today") and data.solcast_today:
            for entry in data.solcast_today:
                if isinstance(entry, dict):
                    solar_data.append({
                        "hour": entry.get("hour", 0),
                        "kwh": entry.get("kwh", 0.0),
                    })
        return solar_data

    def _build_consumption_data(self, data: CoordinatorData) -> list[dict[str, Any]]:
        """Build consumption data list from coordinator data."""
        consumption_data: list[dict[str, Any]] = []
        if (
            hasattr(data, "consumption_hourly_profile_kw")
            and data.consumption_hourly_profile_kw
        ):
            for hour, kw in data.consumption_hourly_profile_kw.items():
                consumption_data.append({"hour": hour, "kwh": kw})
        return consumption_data

    def evaluate_daily(
        self,
        decisions: list[Any],
        data: CoordinatorData,
    ) -> CounterfactualResult | None:
        """Run counterfactual evaluation for daily decisions."""
        if not decisions:
            return None

        result = self._simulator.simulate_period(
            decisions,
            self._build_price_data(data),
            self._build_solar_data(data),
            self._build_consumption_data(data),
        )

        if result:
            self._daily_results.append(result)
            self._last_evaluation = dt_util.now()

            cutoff = dt_util.now() - timedelta(days=7)
            self._daily_results = [
                r
                for r in self._daily_results
                if r.period_start.replace(tzinfo=None) >= cutoff.replace(tzinfo=None)
            ]

            _LOGGER.info(
                "Counterfactual evaluation complete: TOU=$%.2f, Actual=$%.2f, "
                "Advantage=$%.2f (%.1f%%)",
                result.total_cost_tou,
                result.total_cost_actual,
                result.optimizer_advantage,
                result.advantage_percent,
            )

        return result

    def get_rolling_advantage(self, days: int = 7) -> dict[str, float]:
        """Get rolling advantage metrics."""
        cutoff = dt_util.now() - timedelta(days=days)
        recent = [
            r
            for r in self._daily_results
            if r.period_start.replace(tzinfo=None) >= cutoff.replace(tzinfo=None)
        ]

        if not recent:
            return {
                "advantage_total": 0.0,
                "advantage_daily_avg": 0.0,
                "advantage_percent_avg": 0.0,
                "days_with_data": 0,
            }

        total_advantage = sum(r.optimizer_advantage for r in recent)
        avg_percent = sum(r.advantage_percent for r in recent) / len(recent)

        return {
            "advantage_total": total_advantage,
            "advantage_daily_avg": total_advantage / len(recent),
            "advantage_percent_avg": avg_percent,
            "days_with_data": len(recent),
        }

    def is_degrading(self, window_days: int = 7, threshold: float = -0.50) -> bool:
        """Check if optimizer advantage is trending negative.

        Returns True when the average optimizer advantage falls below ``threshold``
        (default -$0.50/day), meaning the optimizer is performing worse than the
        TOU baseline.

        When the time-based rolling window contains no data (e.g. all stored
        results are older than ``window_days``), the method falls back to
        evaluating all stored results so that degradation is still detected.

        Args:
            window_days: Number of days to include in the rolling average.
                Must be a positive integer.
            threshold: Dollar-per-day advantage below which degradation is
                declared. Must be negative (e.g. -0.50 means losing $0.50/day
                vs the TOU baseline is considered degradation).
        """
        metrics = self.get_rolling_advantage(window_days)
        if metrics["days_with_data"] > 0:
            return metrics["advantage_daily_avg"] < threshold

        # Fall back to all stored results when the time window has no data
        if not self._daily_results:
            return False
        avg = sum(r.optimizer_advantage for r in self._daily_results) / len(
            self._daily_results
        )
        return avg < threshold

    def update_performance_metrics(
        self,
        metrics: PerformanceMetrics,
        daily_result: CounterfactualResult | None = None,
    ) -> PerformanceMetrics:
        """Update performance metrics with counterfactual data."""
        rolling = self.get_rolling_advantage(7)

        metrics.counterfactual_tou_cost = (
            daily_result.total_cost_tou if daily_result else 0.0
        )
        metrics.counterfactual_actual_cost = (
            daily_result.total_cost_actual if daily_result else 0.0
        )
        metrics.optimizer_advantage_daily = (
            daily_result.optimizer_advantage if daily_result else 0.0
        )
        metrics.optimizer_advantage_7d = rolling["advantage_total"]
        metrics.optimizer_advantage_daily_avg = rolling["advantage_daily_avg"]
        metrics.optimizer_advantage_percent = (
            daily_result.advantage_percent if daily_result else 0.0
        )
        metrics.counterfactual_degrading = self.is_degrading()

        return metrics


class CounterfactualScoreIntegrator:
    """Integrates counterfactual scoring into decision quality."""

    def __init__(self) -> None:
        """Initialize the score integrator."""
        self._evaluator = CounterfactualEvaluator()

    def compute_counterfactual_score(
        self,
        advantage: float,
        expected_savings: float = 1.0,
    ) -> float:
        """Compute a normalized counterfactual score (0.0-1.0)."""
        if expected_savings <= 0:
            expected_savings = 1.0

        normalized = advantage / expected_savings
        score = 0.5 + (normalized * 0.5)

        return max(0.0, min(1.0, score))

    def blend_with_decision_score(
        self,
        base_score: float,
        counterfactual_score: float,
        counterfactual_weight: float = 0.3,
    ) -> float:
        """Blend base decision score with counterfactual score."""
        weight = max(0.0, min(1.0, counterfactual_weight))
        return base_score * (1.0 - weight) + counterfactual_score * weight

    def get_counterfactual_component(
        self,
        decisions: list[Any],
        data: CoordinatorData,
    ) -> dict[str, Any]:
        """Get counterfactual component data for a decision period."""
        result = self._evaluator.evaluate_daily(decisions, data)

        if result is None:
            return {
                "available": False,
                "score": 0.5,
                "advantage": 0.0,
            }

        score = self.compute_counterfactual_score(result.optimizer_advantage)

        return {
            "available": True,
            "score": round(score, 3),
            "advantage": round(result.optimizer_advantage, 2),
            "advantage_percent": round(result.advantage_percent, 1),
            "tou_cost": round(result.total_cost_tou, 2),
            "actual_cost": round(result.total_cost_actual, 2),
        }
