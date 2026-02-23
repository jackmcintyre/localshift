"""Multi-objective optimization controller for the learning system.

Issue #170 Phase 4: A high-level controller that sits above individual decision
engines, providing real-time parameter adjustments based on current conditions
and learned weights.

This is the only phase with active behavioral impact - it applies contextual
adjustments to the adaptive parameters learned in Phases 1-3.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from ..coordinator_data import AdaptiveParameters

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from ..coordinator_data import CoordinatorData
    from .decision_outcome_tracker import DecisionOutcomeTracker, DecisionRecord
    from .parameter_optimizer import ParameterOptimizer
    from .pattern_analyzer import PatternAnalyzer

_LOGGER = logging.getLogger(__name__)


@dataclass
class ObjectiveWeights:
    """Weights for competing optimization objectives.

    These weights determine how the controller balances different goals
    when making real-time parameter adjustments. The weights are learned
    over time based on which objectives correlate with better outcomes.
    """

    cost_minimization: float = 0.50  # Primary: minimize net electricity cost
    export_avoidance: float = 0.20  # Avoid exporting grid-purchased energy
    target_achievement: float = 0.20  # Reach SOC target by demand window
    cycle_reduction: float = 0.10  # Minimize battery charge/discharge cycles

    def normalize(self) -> ObjectiveWeights:
        """Ensure weights sum to 1.0 for consistent scoring."""
        total = (
            self.cost_minimization
            + self.export_avoidance
            + self.target_achievement
            + self.cycle_reduction
        )
        if total > 0:
            return ObjectiveWeights(
                cost_minimization=self.cost_minimization / total,
                export_avoidance=self.export_avoidance / total,
                target_achievement=self.target_achievement / total,
                cycle_reduction=self.cycle_reduction / total,
            )
        return ObjectiveWeights()

    def to_dict(self) -> dict[str, float]:
        """Convert to dictionary for serialization."""
        return {
            "cost_minimization": self.cost_minimization,
            "export_avoidance": self.export_avoidance,
            "target_achievement": self.target_achievement,
            "cycle_reduction": self.cycle_reduction,
        }

    @classmethod
    def from_dict(cls, data: dict[str, float]) -> ObjectiveWeights:
        """Create from dictionary (deserialization)."""
        return cls(
            cost_minimization=data.get("cost_minimization", 0.50),
            export_avoidance=data.get("export_avoidance", 0.20),
            target_achievement=data.get("target_achievement", 0.20),
            cycle_reduction=data.get("cycle_reduction", 0.10),
        )


@dataclass
class ContextualAdjustment:
    """Represents an active contextual adjustment to parameters."""

    param_name: str
    adjustment: float
    reason: str
    expires_at: datetime | None = None


class OptimizationController:
    """Multi-objective controller that adjusts decision parameters in real-time.

    This controller integrates:
    1. Base adaptive parameters from ParameterOptimizer (Phase 2)
    2. Contextual adjustments based on current conditions
    3. Active bias corrections from PatternAnalyzer (Phase 3)
    4. Multi-objective weight optimization

    The controller is called every computation cycle to provide the final
    AdaptiveParameters that will be used by the decision engines.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        decision_tracker: DecisionOutcomeTracker,
        param_optimizer: ParameterOptimizer,
        pattern_analyzer: PatternAnalyzer,
    ) -> None:
        """Initialize the optimization controller.

        Args:
            hass: Home Assistant instance
            entry_id: Config entry ID for storage key
            decision_tracker: Decision outcome tracker (Phase 1)
            param_optimizer: Parameter optimizer (Phase 2)
            pattern_analyzer: Pattern analyzer (Phase 3)
        """
        self._hass = hass
        self._store = Store(
            hass, version=1, key=f"localshift.opt_controller.{entry_id}"
        )
        self._tracker = decision_tracker
        self._optimizer = param_optimizer
        self._analyzer = pattern_analyzer

        self._weights = ObjectiveWeights()
        self._weight_history: list[tuple[datetime, ObjectiveWeights]] = []
        self._learning_enabled: bool = False  # Must be explicitly enabled via switch

        # Track active contextual adjustments for observability
        self._active_contextual_adjustments: list[ContextualAdjustment] = []

        # Track last weight update time
        self._last_weight_update: datetime | None = None

    def set_learning_enabled(self, enabled: bool) -> None:
        """Enable or disable learning system active optimization.

        When disabled, evaluate() returns zero-offset parameters.
        This is controlled by the EnableLearningSwitch.
        """
        self._learning_enabled = enabled
        _LOGGER.info(
            "Learning system active optimization: %s",
            "enabled" if enabled else "disabled",
        )

    @property
    def learning_enabled(self) -> bool:
        """Return whether active optimization is enabled."""
        return self._learning_enabled

    @property
    def weights(self) -> ObjectiveWeights:
        """Return current objective weights."""
        return self._weights

    def evaluate(self, data: CoordinatorData) -> AdaptiveParameters:
        """Real-time parameter evaluation considering current context.

        Called every computation cycle (every periodic tick).

        Steps:
        1. If learning disabled, return zero-offset parameters
        2. Start with base adaptive params from ParameterOptimizer
        3. Apply contextual adjustments based on current conditions
        4. Apply active bias corrections from PatternAnalyzer
        5. Clamp all parameters to bounds
        6. Return final AdaptiveParameters

        Args:
            data: Current coordinator data

        Returns:
            Final AdaptiveParameters to use in decision engines
        """
        # Clear previous contextual adjustments
        self._active_contextual_adjustments.clear()

        # If learning disabled, return zero-offset parameters
        if not self._learning_enabled:
            return AdaptiveParameters()

        # Get base parameters from optimizer
        base_params = (
            data.adaptive_params if data.adaptive_params else AdaptiveParameters()
        )

        # Create a copy to modify
        params = AdaptiveParameters(
            values=dict(base_params.values),
            confidence=dict(base_params.confidence),
            last_updated=base_params.last_updated,
            update_count=base_params.update_count,
        )

        # Apply contextual adjustments (heuristic layer)
        params = self._apply_contextual_adjustments(params, data)

        # Apply active bias corrections from pattern analyzer
        params = self._apply_active_bias_corrections(params, data)

        # Clamp all parameters to bounds
        params = self._clamp_parameters(params)

        return params

    def _apply_contextual_adjustments(
        self, params: AdaptiveParameters, data: CoordinatorData
    ) -> AdaptiveParameters:
        """Adjust parameters based on current real-time conditions.

        Contextual rules are heuristic overlays on top of learned params.
        They handle edge cases and emergency situations that the learning
        system might not have seen enough examples of.

        Args:
            params: Current parameters to adjust
            data: Current coordinator data

        Returns:
            Adjusted parameters
        """
        now = dt_util.now()

        # Rule 1: SOC Emergency
        # If SOC < 15% and before demand window, charge more aggressively
        if data.soc < 15.0 and not data.demand_window_active:
            adjustment = 3.0  # Boost cheap_price_bias significantly
            current = params.get("cheap_price_bias", 0.0)
            params.values["cheap_price_bias"] = current + adjustment
            self._active_contextual_adjustments.append(
                ContextualAdjustment(
                    param_name="cheap_price_bias",
                    adjustment=adjustment,
                    reason=f"SOC emergency: {data.soc:.1f}% < 15%",
                )
            )
            _LOGGER.debug(
                "SOC emergency adjustment: cheap_price_bias +%.1f (SOC=%.1f%%)",
                adjustment,
                data.soc,
            )

        # Rule 2: Export Leak Detection
        # If export_loss_ratio > 0.3 today, be more conservative about exporting
        if data.performance_metrics.export_loss_ratio > 0.3:
            adjustment = 1.0  # Increase export threshold adjustment
            current = params.get("export_threshold_adjustment", 0.0)
            params.values["export_threshold_adjustment"] = current + adjustment
            self._active_contextual_adjustments.append(
                ContextualAdjustment(
                    param_name="export_threshold_adjustment",
                    adjustment=adjustment,
                    reason=f"Export leak detected: {data.performance_metrics.export_loss_ratio:.1%} loss",
                )
            )
            _LOGGER.debug(
                "Export leak adjustment: export_threshold_adjustment +%.1f (loss=%.1f%%)",
                adjustment,
                data.performance_metrics.export_loss_ratio * 100,
            )

        # Rule 3: Forecast Confidence
        # If forecast accuracy is low, be more pessimistic about solar
        avg_accuracy = (
            (data.forecast_accuracy_soc_1h + data.forecast_accuracy_soc_4h) / 2.0
            if data.forecast_accuracy_soc_1h > 0
            else 100.0
        )

        if avg_accuracy < 50.0:
            # Reduce solar confidence factor (be more pessimistic)
            current = params.get("solar_confidence_factor", 1.0)
            adjustment = -0.2
            params.values["solar_confidence_factor"] = max(0.5, current + adjustment)

            # Increase overnight drain safety margin
            current_margin = params.get("overnight_drain_safety_margin", 0.0)
            margin_adjustment = 3.0
            params.values["overnight_drain_safety_margin"] = (
                current_margin + margin_adjustment
            )

            self._active_contextual_adjustments.append(
                ContextualAdjustment(
                    param_name="solar_confidence_factor",
                    adjustment=adjustment,
                    reason=f"Low forecast accuracy: {avg_accuracy:.1f}%",
                )
            )
            self._active_contextual_adjustments.append(
                ContextualAdjustment(
                    param_name="overnight_drain_safety_margin",
                    adjustment=margin_adjustment,
                    reason=f"Low forecast accuracy: {avg_accuracy:.1f}%",
                )
            )
            _LOGGER.debug(
                "Low forecast accuracy adjustment: solar_confidence=%.2f, margin +%.1f",
                params.values.get("solar_confidence_factor", 1.0),
                margin_adjustment,
            )

        # Rule 4: Approaching Demand Window
        # If within 2 hours of demand window and SOC is below target, charge more aggressively
        if self._is_approaching_demand_window(now, data):
            soc_gap = data.battery_target_soc - data.soc
            if soc_gap > 10:  # More than 10% below target
                adjustment = min(soc_gap / 5.0, 3.0)  # Scale with gap, max 3.0
                current = params.get("cheap_price_bias", 0.0)
                params.values["cheap_price_bias"] = current + adjustment
                self._active_contextual_adjustments.append(
                    ContextualAdjustment(
                        param_name="cheap_price_bias",
                        adjustment=adjustment,
                        reason=f"Approaching DW with SOC gap: {soc_gap:.1f}%",
                    )
                )
                _LOGGER.debug(
                    "Approaching DW adjustment: cheap_price_bias +%.1f (SOC gap=%.1f%%)",
                    adjustment,
                    soc_gap,
                )

        return params

    def _apply_active_bias_corrections(
        self, params: AdaptiveParameters, data: CoordinatorData
    ) -> AdaptiveParameters:
        """Apply relevant bias corrections for current context.

        Matches current conditions (day_of_week, weather, etc.) against
        active BiasCorrection entries and applies matching adjustments.

        Args:
            params: Current parameters to adjust
            data: Current coordinator data

        Returns:
            Adjusted parameters
        """
        if not data.active_bias_corrections:
            return params

        now = dt_util.now()
        current_day = now.weekday()  # 0=Monday, 6=Sunday
        current_weather = data.weather_condition.lower()

        for correction in data.active_bias_corrections:
            # Check if correction applies to current context
            applies = False

            dimension = correction.get("dimension", "")
            group_key = correction.get("group_key", "")

            if dimension == "day_of_week":
                # Check if current day matches
                day_names = [
                    "monday",
                    "tuesday",
                    "wednesday",
                    "thursday",
                    "friday",
                    "saturday",
                    "sunday",
                ]
                if group_key.lower() == day_names[current_day]:
                    applies = True

            elif dimension == "weather":
                # Check if current weather matches (partial match)
                if (
                    group_key.lower() in current_weather
                    or current_weather in group_key.lower()
                ):
                    applies = True

            elif dimension == "hour_of_day":
                # Check if current hour matches
                try:
                    hour = int(group_key)
                    if hour == now.hour:
                        applies = True
                except (ValueError, TypeError):
                    pass

            elif dimension == "season":
                # Season matching (approximate by month)
                month = now.month
                season = self._get_season(month)
                if group_key.lower() == season:
                    applies = True

            if applies:
                param_name = correction.get("param_name", "")
                adjustment = correction.get("adjustment", 0.0)
                confidence = correction.get("confidence", 0.0)

                # Only apply high-confidence corrections directly
                if confidence >= 0.5 and param_name:
                    current = params.get(param_name, 0.0)
                    params.values[param_name] = current + adjustment
                    self._active_contextual_adjustments.append(
                        ContextualAdjustment(
                            param_name=param_name,
                            adjustment=adjustment,
                            reason=f"Bias correction ({dimension}={group_key})",
                        )
                    )
                    _LOGGER.debug(
                        "Applied bias correction: %s +%.2f (%s=%s, confidence=%.2f)",
                        param_name,
                        adjustment,
                        dimension,
                        group_key,
                        confidence,
                    )

        return params

    def _clamp_parameters(self, params: AdaptiveParameters) -> AdaptiveParameters:
        """Clamp all parameters to their defined bounds.

        Uses OPTIMIZABLE_PARAMS from const.py to get min/max bounds.

        Args:
            params: Parameters to clamp

        Returns:
            Clamped parameters
        """
        from ..const import OPTIMIZABLE_PARAMS

        for param_name, value in list(params.values.items()):
            if param_name in OPTIMIZABLE_PARAMS:
                param_def = OPTIMIZABLE_PARAMS[param_name]
                clamped = max(param_def.min_val, min(param_def.max_val, value))
                if clamped != value:
                    params.values[param_name] = clamped
                    _LOGGER.debug(
                        "Clamped %s from %.2f to %.2f (bounds: [%.2f, %.2f])",
                        param_name,
                        value,
                        clamped,
                        param_def.min_val,
                        param_def.max_val,
                    )

        return params

    def _is_approaching_demand_window(
        self, now: datetime, data: CoordinatorData
    ) -> bool:
        """Check if we're approaching the demand window.

        Returns True if within 2 hours of demand window start.
        """
        from ..const import DEFAULT_DEMAND_WINDOW_START

        # Parse demand window start time
        try:
            time_str = DEFAULT_DEMAND_WINDOW_START
            parts = time_str.split(":")
            dw_start_hour = int(parts[0])
            dw_start_minute = int(parts[1]) if len(parts) > 1 else 0
        except (ValueError, IndexError):
            dw_start_hour = 15  # Default 3 PM
            dw_start_minute = 0

        # Calculate hours until demand window
        current_minutes = now.hour * 60 + now.minute
        dw_start_minutes = dw_start_hour * 60 + dw_start_minute

        # Handle same-day window
        minutes_until_dw = dw_start_minutes - current_minutes

        # If negative, it's tomorrow's window
        if minutes_until_dw < 0:
            minutes_until_dw += 24 * 60

        hours_until_dw = minutes_until_dw / 60.0

        return hours_until_dw <= 2.0 and hours_until_dw > 0

    def _get_season(self, month: int) -> str:
        """Get season name from month number.

        Southern hemisphere seasons (Australia).
        """
        if month in (12, 1, 2):
            return "summer"
        elif month in (3, 4, 5):
            return "autumn"
        elif month in (6, 7, 8):
            return "winter"
        else:  # 9, 10, 11
            return "spring"

    def update_weights(self, decisions: list[DecisionRecord]) -> None:
        """Adjust objective weights based on historical performance.

        If certain objectives consistently correlate with better outcomes,
        increase their weight. Uses a simple gradient-based adjustment.

        Weight adjustments are small (max 0.05 per update) and logged.

        Args:
            decisions: Recent decision records to analyze
        """
        if len(decisions) < 20:
            _LOGGER.debug(
                "Not enough decisions for weight update: %d < 20", len(decisions)
            )
            return

        # Analyze which objectives correlate with better outcomes
        # For now, use a simple heuristic approach

        # Get current performance metrics
        # If export losses are high, increase export_avoidance
        export_loss_decisions = [
            d
            for d in decisions
            if d.actual_export_kwh is not None and d.actual_export_kwh > 0.5
        ]
        if export_loss_decisions:
            export_loss_rate = len(export_loss_decisions) / len(decisions)
            if export_loss_rate > 0.2:  # More than 20% of decisions had export loss
                adjustment = min(0.05, export_loss_rate * 0.1)
                self._weights.export_avoidance = min(
                    0.40, self._weights.export_avoidance + adjustment
                )
                # Rebalance by reducing other weights proportionally
                self._weights = self._weights.normalize()
                _LOGGER.info(
                    "Increased export_avoidance weight by %.3f due to %.1f%% export loss rate",
                    adjustment,
                    export_loss_rate * 100,
                )

        # If target is rarely hit, increase target_achievement
        target_miss_decisions = [
            d
            for d in decisions
            if d.actual_soc_change is not None
            and d.battery_target_soc > 0
            and (d.soc_at_decision + d.actual_soc_change) < d.battery_target_soc - 10
        ]
        if target_miss_decisions:
            target_miss_rate = len(target_miss_decisions) / len(decisions)
            if target_miss_rate > 0.3:  # More than 30% missed target by >10%
                adjustment = min(0.05, target_miss_rate * 0.1)
                self._weights.target_achievement = min(
                    0.40, self._weights.target_achievement + adjustment
                )
                self._weights = self._weights.normalize()
                _LOGGER.info(
                    "Increased target_achievement weight by %.3f due to %.1f%% target miss rate",
                    adjustment,
                    target_miss_rate * 100,
                )

        # Record weight update
        now = dt_util.now()
        self._last_weight_update = now
        self._weight_history.append(
            (
                now,
                ObjectiveWeights(
                    cost_minimization=self._weights.cost_minimization,
                    export_avoidance=self._weights.export_avoidance,
                    target_achievement=self._weights.target_achievement,
                    cycle_reduction=self._weights.cycle_reduction,
                ),
            )
        )

        # Keep only last 30 weight history entries
        if len(self._weight_history) > 30:
            self._weight_history = self._weight_history[-30:]

    def score_decision(
        self, record: DecisionRecord, weights: ObjectiveWeights | None = None
    ) -> float:
        """Multi-objective scoring of a decision outcome.

        Score = weighted sum of component scores, each normalized to [0, 1]:
        - cost_score: 1.0 if net cost was optimal, 0.0 if worst case
        - export_score: 1.0 if no unnecessary exports, 0.0 if all grid charge exported
        - target_score: 1.0 if SOC target reached, 0.0 if missed by >20%
        - cycle_score: 1.0 if mode stable, 0.0 if rapid cycling detected

        Args:
            record: Decision record to score
            weights: Weights to use (defaults to current weights)

        Returns:
            Score from 0.0 to 1.0
        """
        if weights is None:
            weights = self._weights

        scores = {}

        # Cost score (simplified - uses outcome score as proxy)
        if record.outcome_score is not None:
            scores["cost"] = record.outcome_score
        else:
            scores["cost"] = 0.5  # Neutral if no outcome

        # Export score
        if record.actual_export_kwh is not None and record.actual_export_kwh > 0.1:
            if record.mode_chosen.value in ("proactive_export", "spike_discharge"):
                # These modes are supposed to export
                scores["export"] = 0.8
            elif record.mode_chosen.value == "grid_charging":
                # Bad: grid charged then exported
                scores["export"] = 0.2
            else:
                scores["export"] = 0.5
        else:
            # No export or negligible
            scores["export"] = 1.0

        # Target score
        if record.battery_target_soc > 0 and record.actual_soc_change is not None:
            end_soc = record.soc_at_decision + record.actual_soc_change
            target_diff = abs(end_soc - record.battery_target_soc)

            if target_diff <= 5:
                scores["target"] = 1.0
            elif target_diff <= 10:
                scores["target"] = 0.8
            elif target_diff <= 20:
                scores["target"] = 0.5
            else:
                scores["target"] = max(0.0, 1.0 - target_diff / 50.0)
        else:
            scores["target"] = 0.5  # Neutral if no target

        # Cycle score (penalize rapid mode changes)
        if record.duration_minutes is not None:
            if record.duration_minutes < 5:
                scores["cycle"] = 0.3
            elif record.duration_minutes < 10:
                scores["cycle"] = 0.6
            else:
                scores["cycle"] = 1.0
        else:
            scores["cycle"] = 0.5

        # Compute weighted score
        normalized_weights = weights.normalize()
        total_score = (
            normalized_weights.cost_minimization * scores["cost"]
            + normalized_weights.export_avoidance * scores["export"]
            + normalized_weights.target_achievement * scores["target"]
            + normalized_weights.cycle_reduction * scores["cycle"]
        )

        return max(0.0, min(1.0, total_score))

    def get_active_adjustments(self) -> list[dict[str, Any]]:
        """Return list of active contextual adjustments for observability.

        Returns:
            List of adjustment dictionaries with param_name, adjustment, reason
        """
        return [
            {
                "param_name": adj.param_name,
                "adjustment": adj.adjustment,
                "reason": adj.reason,
            }
            for adj in self._active_contextual_adjustments
        ]

    def get_weight_history(self, limit: int = 10) -> list[dict[str, Any]]:
        """Return recent weight history for observability.

        Args:
            limit: Maximum number of entries to return

        Returns:
            List of weight history entries
        """
        history = self._weight_history[-limit:]
        return [
            {
                "timestamp": ts.isoformat(),
                "weights": weights.to_dict(),
            }
            for ts, weights in history
        ]

    async def async_save(self) -> None:
        """Persist controller state including weights."""
        data = {
            "weights": self._weights.to_dict(),
            "weight_history": [
                {"timestamp": ts.isoformat(), "weights": w.to_dict()}
                for ts, w in self._weight_history[-30:]  # Keep last 30
            ],
            "learning_enabled": self._learning_enabled,
            "last_weight_update": (
                self._last_weight_update.isoformat()
                if self._last_weight_update
                else None
            ),
        }
        await self._store.async_save(data)
        _LOGGER.debug("Optimization controller state saved")

    async def async_load(self) -> None:
        """Restore controller state from HA storage."""
        data = await self._store.async_load()

        if data is None:
            _LOGGER.debug("No saved optimization controller state found")
            return

        # Restore weights
        if "weights" in data:
            self._weights = ObjectiveWeights.from_dict(data["weights"])

        # Restore weight history
        if "weight_history" in data:
            self._weight_history.clear()
            for entry in data["weight_history"]:
                try:
                    ts = datetime.fromisoformat(entry["timestamp"])
                    weights = ObjectiveWeights.from_dict(entry["weights"])
                    self._weight_history.append((ts, weights))
                except (KeyError, ValueError, TypeError) as e:
                    _LOGGER.warning("Failed to load weight history entry: %s", e)

        # Restore learning enabled state
        self._learning_enabled = data.get("learning_enabled", False)

        # Restore last weight update time
        if data.get("last_weight_update"):
            try:
                self._last_weight_update = datetime.fromisoformat(
                    data["last_weight_update"]
                )
            except (ValueError, TypeError):
                pass

        _LOGGER.info(
            "Optimization controller state loaded: learning=%s, weights=%s",
            self._learning_enabled,
            self._weights.to_dict(),
        )
