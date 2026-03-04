"""Parameter optimizer for the learning system.

Issue #170 Phase 2: Bayesian-inspired parameter optimization using Thompson sampling.
Adjusts tunable parameters based on decision outcome data from Phase 1.
Issue #170 Phase 3: Enhanced to accept bias corrections from pattern analysis.
"""

from __future__ import annotations

import logging
import math
import random
from collections import defaultdict
from datetime import datetime
from typing import TYPE_CHECKING, Any

from homeassistant.helpers.storage import Store

from ..const import (
    DOMAIN,
    LEARNING_MIN_OBSERVATIONS,
    LEARNING_UPDATE_INTERVAL_HOURS,
    OPTIMIZABLE_PARAMS,
)
from ..coordinator_data import AdaptiveParameters

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .decision_outcome_tracker import DecisionRecord
    from .pattern_analyzer import BiasCorrection

_LOGGER = logging.getLogger(__name__)


class ParameterOptimizer:
    """Bayesian-inspired parameter optimizer using Thompson sampling.

    Adjusts parameter values based on outcome data from the decision tracker.
    Implements safety rails:
    - Warm-up period: No adjustments until 50+ decision records
    - Step limits: Parameters can only move one step per daily update
    - Rollback: Revert if 7-day rolling score decreases for 3 consecutive days
    - Bounds: Hard min/max from OptimizableParam definitions
    """

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Initialize the parameter optimizer.

        Args:
            hass: Home Assistant instance
            entry_id: Config entry ID for storage isolation
        """
        self._store = Store(hass, version=1, key=f"{DOMAIN}.param_optimizer.{entry_id}")
        self._param_history: dict[str, list[tuple[float, float]]] = defaultdict(
            list
        )  # param -> [(value, score)]
        self._current_params = AdaptiveParameters()
        self._last_update: datetime | None = None
        self._consecutive_degrading_days: int = 0
        self._last_7d_score: float = 0.0
        self._adjustment_log: list[dict[str, Any]] = []
        self._pending_bias_corrections: list[BiasCorrection] = []

    def should_update(self, decision_count: int) -> bool:
        """Check if enough data has accumulated for an update.

        Args:
            decision_count: Number of completed decisions available

        Returns:
            True if optimization should run
        """
        if decision_count < LEARNING_MIN_OBSERVATIONS:
            _LOGGER.debug(
                "Not enough decisions for optimization: %d < %d",
                decision_count,
                LEARNING_MIN_OBSERVATIONS,
            )
            return False

        if self._last_update is None:
            return True

        hours_since_update = (datetime.now() - self._last_update).total_seconds() / 3600
        if hours_since_update < LEARNING_UPDATE_INTERVAL_HOURS:
            _LOGGER.debug(
                "Too soon for optimization: %.1f hours < %d hours",
                hours_since_update,
                LEARNING_UPDATE_INTERVAL_HOURS,
            )
            return False

        return True

    def optimize(
        self,
        decisions: list[DecisionRecord],
        current_7d_score: float,
        bias_corrections: list[BiasCorrection] | None = None,
    ) -> AdaptiveParameters:
        """Run optimization using recent decision outcomes.

        Uses Thompson sampling variant:
        1. For each parameter, group decisions by parameter value range
        2. Compute mean outcome score for each range
        3. Model each range as Beta distribution
        4. Sample from distributions, pick the range with highest sample
        5. Set parameter to center of winning range
        6. Add small exploration noise
        7. Apply bias corrections from pattern analysis (Phase 3)

        Args:
            decisions: List of decision records with outcomes
            current_7d_score: Current 7-day rolling score for rollback detection
            bias_corrections: Optional list of bias corrections from pattern analyzer

        Returns:
            Updated AdaptiveParameters
        """
        if not decisions:
            _LOGGER.warning("No decisions provided for optimization")
            return self._current_params

        # Check for rollback condition
        if self._should_rollback(current_7d_score):
            _LOGGER.info(
                "Rolling back parameters due to degrading performance (%d consecutive days)",
                self._consecutive_degrading_days,
            )
            self._rollback_parameters()
            return self._current_params

        # Track if we're improving or degrading
        if current_7d_score < self._last_7d_score - 0.05:  # 5% degradation threshold
            self._consecutive_degrading_days += 1
        else:
            self._consecutive_degrading_days = 0
        self._last_7d_score = current_7d_score

        # Optimize each parameter
        new_values = dict(self._current_params.values)
        new_confidence = dict(self._current_params.confidence)

        for param_name, param_def in OPTIMIZABLE_PARAMS.items():
            # Compute optimal value for this parameter
            optimal_value, confidence = self._optimize_single_param(
                param_name, param_def, decisions
            )

            if optimal_value is not None:
                old_value = new_values.get(param_name, param_def.default)
                new_values[param_name] = optimal_value
                new_confidence[param_name] = confidence

                if old_value != optimal_value:
                    _LOGGER.info(
                        "Parameter %s adjusted: %.3f -> %.3f (confidence: %.2f)",
                        param_name,
                        old_value,
                        optimal_value,
                        confidence,
                    )
                    self._adjustment_log.append(
                        {
                            "timestamp": datetime.now().isoformat(),
                            "param": param_name,
                            "old_value": old_value,
                            "new_value": optimal_value,
                            "confidence": confidence,
                        }
                    )

        # Apply bias corrections from pattern analysis (Issue #170 Phase 3)
        if bias_corrections:
            new_values, new_confidence = self._apply_bias_corrections(
                new_values, new_confidence, bias_corrections
            )

        # Update the parameters
        self._current_params = AdaptiveParameters(
            values=new_values,
            confidence=new_confidence,
            last_updated=datetime.now(),
            update_count=self._current_params.update_count + 1,
        )
        self._last_update = datetime.now()

        return self._current_params

    def _apply_bias_corrections(
        self,
        values: dict[str, float],
        confidence: dict[str, float],
        bias_corrections: list[BiasCorrection],
    ) -> tuple[dict[str, float], dict[str, float]]:
        """Apply bias corrections from pattern analysis.

        High-confidence corrections (>0.8) are applied directly as offsets.
        Medium-confidence corrections (0.5-0.8) are used as priors in sampling.
        Low-confidence corrections (<0.5) are ignored.

        Args:
            values: Current parameter values
            confidence: Current confidence scores
            bias_corrections: List of bias corrections from pattern analyzer

        Returns:
            Tuple of (updated_values, updated_confidence)
        """
        # Group corrections by parameter
        param_corrections: dict[str, list[BiasCorrection]] = {}
        for correction in bias_corrections:
            if correction.param_name not in param_corrections:
                param_corrections[correction.param_name] = []
            param_corrections[correction.param_name].append(correction)

        applied_count = 0

        for param_name, corrections in param_corrections.items():
            if param_name not in OPTIMIZABLE_PARAMS:
                _LOGGER.debug(
                    "Ignoring bias correction for unknown parameter: %s",
                    param_name,
                )
                continue

            param_def = OPTIMIZABLE_PARAMS[param_name]
            current_value = values.get(param_name, param_def.default)

            # Separate by confidence level
            high_confidence = [c for c in corrections if c.confidence > 0.8]
            medium_confidence = [c for c in corrections if 0.5 <= c.confidence <= 0.8]

            # Apply high-confidence corrections directly
            if high_confidence:
                # Use weighted average based on confidence
                total_weight = sum(c.confidence for c in high_confidence)
                weighted_adjustment = (
                    sum(c.adjustment * c.confidence for c in high_confidence)
                    / total_weight
                )

                new_value = current_value + weighted_adjustment

                # Clamp to bounds
                new_value = max(param_def.min_val, min(param_def.max_val, new_value))

                if new_value != current_value:
                    values[param_name] = new_value
                    # Boost confidence when multiple high-confidence corrections agree
                    avg_confidence = total_weight / len(high_confidence)
                    confidence[param_name] = min(1.0, avg_confidence + 0.1)

                    _LOGGER.info(
                        "Applied bias correction to %s: %.3f -> %.3f (from %d high-confidence patterns)",
                        param_name,
                        current_value,
                        new_value,
                        len(high_confidence),
                    )
                    applied_count += 1

            # Apply medium-confidence corrections as smaller adjustments
            elif medium_confidence:
                # Use smaller weight for medium confidence
                avg_adjustment = sum(c.adjustment for c in medium_confidence) / len(
                    medium_confidence
                )
                # Scale down the adjustment based on confidence
                avg_conf = sum(c.confidence for c in medium_confidence) / len(
                    medium_confidence
                )
                scaled_adjustment = avg_adjustment * avg_conf

                new_value = (
                    current_value + scaled_adjustment * 0.5
                )  # Scale down further

                # Clamp to bounds
                new_value = max(param_def.min_val, min(param_def.max_val, new_value))

                if abs(new_value - current_value) > 0.01:  # Only log meaningful changes
                    values[param_name] = new_value

                    _LOGGER.info(
                        "Applied medium-confidence bias correction to %s: %.3f -> %.3f (from %d patterns)",
                        param_name,
                        current_value,
                        new_value,
                        len(medium_confidence),
                    )
                    applied_count += 1

        if applied_count > 0:
            _LOGGER.info(
                "Applied %d bias corrections from pattern analysis",
                applied_count,
            )

        return values, confidence

    def _optimize_single_param(
        self,
        param_name: str,
        param_def: Any,  # OptimizableParam
        decisions: list[DecisionRecord],
    ) -> tuple[float | None, float]:
        """Optimize a single parameter using Thompson sampling.

        Args:
            param_name: Parameter name
            param_def: Parameter definition
            decisions: Decision records

        Returns:
            Tuple of (optimal_value, confidence) or (None, 0.0) if no data
        """
        # Group decisions by parameter value ranges
        num_bins = 5
        bin_width = (param_def.max_val - param_def.min_val) / num_bins
        bins: dict[int, list[float]] = defaultdict(list)

        current_value = self._current_params.values.get(param_name, param_def.default)

        # Assign decisions to bins based on what parameter value would have been optimal
        for decision in decisions:
            if decision.outcome_score is None:
                continue

            # Calculate which bin the current value falls into
            bin_idx = int((current_value - param_def.min_val) / bin_width)
            bin_idx = max(0, min(num_bins - 1, bin_idx))
            bins[bin_idx].append(decision.outcome_score)

        # If we don't have enough data, don't adjust
        total_samples = sum(len(scores) for scores in bins.values())
        if total_samples < 10:
            return None, 0.0

        # Thompson sampling: sample from Beta distribution for each bin
        best_bin = None
        best_sample = -float("inf")

        for bin_idx in range(num_bins):
            scores = bins[bin_idx]
            if not scores:
                continue

            # Convert scores to success/failure counts
            # Score is 0-1, so we can use it directly as success rate
            mean_score = sum(scores) / len(scores)
            n = len(scores)

            # Beta distribution parameters (add 1 for prior)
            alpha = mean_score * n + 1
            beta = (1 - mean_score) * n + 1

            # Sample from Beta distribution
            sample = self._sample_beta(alpha, beta)

            if sample > best_sample:
                best_sample = sample
                best_bin = bin_idx

        if best_bin is None:
            return None, 0.0

        # Calculate the center value of the winning bin
        bin_center = param_def.min_val + (best_bin + 0.5) * bin_width

        # Apply step limit: can only move one step from current value
        current_val = self._current_params.values.get(param_name, param_def.default)
        step_size = param_def.step
        if abs(bin_center - current_val) > step_size:
            # Move one step in the direction of the bin center
            if bin_center > current_val:
                bin_center = current_val + step_size
            else:
                bin_center = current_val - step_size

        # Clamp to bounds
        bin_center = max(param_def.min_val, min(param_def.max_val, bin_center))

        # Calculate confidence based on sample count and variance
        bin_scores = bins.get(best_bin, [])
        if bin_scores:
            mean_score = sum(bin_scores) / len(bin_scores)
            variance = sum((s - mean_score) ** 2 for s in bin_scores) / len(bin_scores)
            # Higher sample count and lower variance = higher confidence
            confidence = min(1.0, len(bin_scores) / 50.0) * (
                1 - min(variance, 0.25) * 4
            )
        else:
            confidence = 0.0

        return bin_center, confidence

    def _sample_beta(self, alpha: float, beta: float) -> float:
        """Sample from a Beta distribution using gamma distribution.

        Args:
            alpha: Beta distribution alpha parameter
            beta: Beta distribution beta parameter

        Returns:
            Sample value between 0 and 1
        """
        # Use gamma distribution to sample from beta
        # Beta(a, b) = Gamma(a, 1) / (Gamma(a, 1) + Gamma(b, 1))
        x = self._gamma_variate(alpha, 1.0)
        y = self._gamma_variate(beta, 1.0)
        return x / (x + y) if (x + y) > 0 else 0.5

    def _gamma_variate(self, alpha: float, beta: float) -> float:
        """Generate gamma variate using Marsaglia and Tsang's method.

        Args:
            alpha: Shape parameter
            beta: Scale parameter

        Returns:
            Gamma distributed random value
        """
        if alpha < 1:
            return self._gamma_variate(1 + alpha, beta) * (
                random.random() ** (1 / alpha)  # nosec B311
            )

        d = alpha - 1 / 3
        c = 1 / math.sqrt(9 * d)

        while True:
            x = random.gauss(0, 1)
            v = 1 + c * x

            if v <= 0:
                continue

            v = v * v * v
            u = random.random()  # nosec B311

            if u < 1 - 0.0331 * (x * x) * (x * x):
                return d * v * beta

            if math.log(u) < 0.5 * x * x + d * (1 - v + math.log(v)):
                return d * v * beta

    def _should_rollback(self, current_7d_score: float) -> bool:
        """Check if we should rollback to previous parameter values.

        Args:
            current_7d_score: Current 7-day rolling score

        Returns:
            True if we should rollback
        """
        return self._consecutive_degrading_days >= 3

    def _rollback_parameters(self) -> None:
        """Rollback to previous parameter values."""
        # Find the most recent adjustment and revert it
        if self._adjustment_log:
            last_adjustment = self._adjustment_log.pop()
            param_name = last_adjustment["param"]
            old_value = last_adjustment["old_value"]

            self._current_params.values[param_name] = old_value
            self._current_params.update_count += 1
            self._current_params.last_updated = datetime.now()

            _LOGGER.warning(
                "Rolled back parameter %s to %.3f",
                param_name,
                old_value,
            )

        self._consecutive_degrading_days = 0

    async def async_save(self) -> None:
        """Persist optimizer state to storage."""
        data = {
            "current_params": self._current_params.to_dict(),
            "param_history": dict(self._param_history),
            "adjustment_log": self._adjustment_log[-100:],  # Keep last 100
            "consecutive_degrading_days": self._consecutive_degrading_days,
            "last_7d_score": self._last_7d_score,
            "last_update": self._last_update.isoformat() if self._last_update else None,
        }
        await self._store.async_save(data)
        _LOGGER.debug("Parameter optimizer state saved")

    async def async_load(self) -> None:
        """Restore optimizer state from storage."""
        data = await self._store.async_load()
        if data is None:
            _LOGGER.debug("No saved optimizer state found, starting fresh")
            return

        self._current_params = AdaptiveParameters.from_dict(
            data.get("current_params", {})
        )
        self._param_history = defaultdict(list, data.get("param_history", {}))
        self._adjustment_log = data.get("adjustment_log", [])
        self._consecutive_degrading_days = data.get("consecutive_degrading_days", 0)
        self._last_7d_score = data.get("last_7d_score", 0.0)

        if data.get("last_update"):
            try:
                self._last_update = datetime.fromisoformat(data["last_update"])
            except (ValueError, TypeError):
                self._last_update = None

        _LOGGER.debug(
            "Parameter optimizer state loaded: %d updates, %d parameters adjusted",
            self._current_params.update_count,
            len(self._current_params.values),
        )

    def set_bias_corrections(self, bias_corrections: list[BiasCorrection]) -> None:
        """Set bias corrections from pattern analysis for next optimization.

        Bias corrections are applied during the next optimize() call.

        Args:
            bias_corrections: List of bias corrections from pattern analyzer
        """
        self._pending_bias_corrections = bias_corrections
        _LOGGER.debug(
            "Stored %d bias corrections for next optimization cycle",
            len(bias_corrections),
        )

    def get_diagnostics(self) -> dict[str, Any]:
        """Get diagnostic information about the optimizer.

        Returns:
            Dictionary with optimizer state for diagnostics
        """
        return {
            "current_params": self._current_params.to_dict(),
            "param_history_count": {k: len(v) for k, v in self._param_history.items()},
            "adjustment_count": len(self._adjustment_log),
            "consecutive_degrading_days": self._consecutive_degrading_days,
            "last_7d_score": self._last_7d_score,
            "last_update": self._last_update.isoformat() if self._last_update else None,
            "min_observations": LEARNING_MIN_OBSERVATIONS,
            "update_interval_hours": LEARNING_UPDATE_INTERVAL_HOURS,
            "pending_bias_corrections": len(self._pending_bias_corrections),
        }
