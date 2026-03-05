"""Pattern recognition and bias detection for the learning system.

Issue #170 Phase 3: Analyzes decision outcome data by contextual dimensions
to detect systematic biases and feed corrections into the parameter optimizer.
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from ..const import DOMAIN, OPTIMIZABLE_PARAMS
from .optimizer_dp import PlannerAction

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .decision_outcome_tracker import DecisionRecord

_LOGGER = logging.getLogger(__name__)

# Minimum samples per group before considering it for bias detection
MIN_SAMPLES_FOR_BIAS = 10

# Minimum weeks a pattern must persist before becoming actionable
MIN_WEEKS_OBSERVED = 2

# Standard deviation threshold for bias detection
BIAS_STD_DEV_THRESHOLD = 1.0


@dataclass
class PatternBucket:
    """Aggregated stats for a dimension group.

    Attributes:
        key: The group identifier (e.g., "monday", "cloudy", "summer")
        dimension: The dimension name (e.g., "day_of_week", "weather")
        sample_count: Number of decisions in this bucket
        mean_score: Average outcome score (0.0-1.0)
        std_score: Standard deviation of scores
        over_charge_rate: Fraction of grid-charge decisions that were unnecessary
        under_charge_rate: Fraction of decisions where SOC dropped below target
        export_loss_rate: Fraction of decisions with grid-purchased energy exported
    """

    key: str
    dimension: str
    sample_count: int = 0
    mean_score: float = 0.0
    std_score: float = 0.0
    over_charge_rate: float = 0.0
    under_charge_rate: float = 0.0
    export_loss_rate: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "key": self.key,
            "dimension": self.dimension,
            "sample_count": self.sample_count,
            "mean_score": self.mean_score,
            "std_score": self.std_score,
            "over_charge_rate": self.over_charge_rate,
            "under_charge_rate": self.under_charge_rate,
            "export_loss_rate": self.export_loss_rate,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PatternBucket:
        """Create from dictionary."""
        return cls(
            key=data["key"],
            dimension=data["dimension"],
            sample_count=data.get("sample_count", 0),
            mean_score=data.get("mean_score", 0.0),
            std_score=data.get("std_score", 0.0),
            over_charge_rate=data.get("over_charge_rate", 0.0),
            under_charge_rate=data.get("under_charge_rate", 0.0),
            export_loss_rate=data.get("export_loss_rate", 0.0),
        )


@dataclass
class DimensionStats:
    """Stats for all groups within a dimension.

    Attributes:
        dimension: The dimension name
        groups: Mapping of group key to PatternBucket
        global_mean: Mean score across all groups
        global_std: Standard deviation across all groups
    """

    dimension: str
    groups: dict[str, PatternBucket] = field(default_factory=dict)
    global_mean: float = 0.0
    global_std: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "dimension": self.dimension,
            "groups": {k: v.to_dict() for k, v in self.groups.items()},
            "global_mean": self.global_mean,
            "global_std": self.global_std,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DimensionStats:
        """Create from dictionary."""
        groups = {
            k: PatternBucket.from_dict(v) for k, v in data.get("groups", {}).items()
        }
        return cls(
            dimension=data["dimension"],
            groups=groups,
            global_mean=data.get("global_mean", 0.0),
            global_std=data.get("global_std", 0.0),
        )


@dataclass
class BiasCorrection:
    """A detected bias with recommended parameter adjustment.

    Attributes:
        condition: Human-readable condition description
        dimension: Which dimension this bias was found in
        group_key: Specific group (e.g., "monday", "cloudy")
        param_name: Which optimizable parameter to adjust
        adjustment: Recommended adjustment value
        confidence: 0.0-1.0 confidence in this correction
        sample_count: How many decisions support this
        weeks_observed: How many weeks this pattern has persisted
    """

    condition: str
    dimension: str
    group_key: str
    param_name: str
    adjustment: float
    confidence: float
    sample_count: int = 0
    weeks_observed: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "condition": self.condition,
            "dimension": self.dimension,
            "group_key": self.group_key,
            "param_name": self.param_name,
            "adjustment": self.adjustment,
            "confidence": self.confidence,
            "sample_count": self.sample_count,
            "weeks_observed": self.weeks_observed,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BiasCorrection:
        """Create from dictionary."""
        return cls(
            condition=data["condition"],
            dimension=data["dimension"],
            group_key=data["group_key"],
            param_name=data["param_name"],
            adjustment=data["adjustment"],
            confidence=data["confidence"],
            sample_count=data.get("sample_count", 0),
            weeks_observed=data.get("weeks_observed", 0),
        )


@dataclass
class PatternReport:
    """Full pattern analysis report.

    Attributes:
        generated_at: When this report was generated
        dimensions: Analysis results per dimension
        biases_detected: List of detected biases
        data_points_analyzed: Total decisions analyzed
    """

    generated_at: datetime = field(default_factory=dt_util.now)
    dimensions: dict[str, DimensionStats] = field(default_factory=dict)
    biases_detected: list[BiasCorrection] = field(default_factory=list)
    data_points_analyzed: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "generated_at": self.generated_at.isoformat(),
            "dimensions": {k: v.to_dict() for k, v in self.dimensions.items()},
            "biases_detected": [b.to_dict() for b in self.biases_detected],
            "data_points_analyzed": self.data_points_analyzed,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PatternReport:
        """Create from dictionary."""
        generated_at = dt_util.now()
        if data.get("generated_at"):
            try:
                generated_at = datetime.fromisoformat(data["generated_at"])
            except (ValueError, TypeError):
                pass

        dimensions = {
            k: DimensionStats.from_dict(v)
            for k, v in data.get("dimensions", {}).items()
        }
        biases = [BiasCorrection.from_dict(b) for b in data.get("biases_detected", [])]

        return cls(
            generated_at=generated_at,
            dimensions=dimensions,
            biases_detected=biases,
            data_points_analyzed=data.get("data_points_analyzed", 0),
        )

    def get_summary(self) -> dict[str, Any]:
        """Get a summary for CoordinatorData."""
        return {
            "generated_at": self.generated_at.isoformat(),
            "biases_count": len(self.biases_detected),
            "data_points": self.data_points_analyzed,
            "dimensions_analyzed": list(self.dimensions.keys()),
            "top_biases": [
                {
                    "condition": b.condition,
                    "param": b.param_name,
                    "adjustment": round(b.adjustment, 3),
                    "confidence": round(b.confidence, 2),
                }
                for b in self.biases_detected[:5]  # Top 5
            ],
        }


class PatternAnalyzer:
    """Detect systematic biases in decision quality across contextual dimensions.

    Analyzes decision outcome data grouped by:
    1. Day of week (Mon-Sun)
    2. Hour of day (0-23)
    3. Weather condition (sunny, cloudy, rainy, etc.)
    4. Season (summer, autumn, winter, spring)
    5. Price regime (low, medium, high)
    6. Solar availability (high/medium/low)
    """

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Initialize the pattern analyzer.

        Args:
            hass: Home Assistant instance
            entry_id: Config entry ID for storage isolation
        """
        self._store = Store(
            hass, version=1, key=f"{DOMAIN}.pattern_analysis.{entry_id}"
        )
        self._pattern_data: dict[str, list[tuple[datetime, float, dict]]] = defaultdict(
            list
        )  # dimension_key -> [(timestamp, score, metrics)]
        self._last_report: PatternReport | None = None
        self._weeks_of_data: int = 0
        self._last_analysis_time: datetime | None = None

    def analyze(self, decisions: list[DecisionRecord]) -> PatternReport:
        """Run full pattern analysis across all dimensions.

        Args:
            decisions: List of decision records with outcomes

        Returns:
            PatternReport with all analysis results
        """
        if not decisions:
            _LOGGER.debug("No decisions provided for pattern analysis")
            return PatternReport()

        now = dt_util.now()
        report = PatternReport(
            generated_at=now,
            data_points_analyzed=len(decisions),
        )

        # Analyze each dimension
        dimensions = [
            ("day_of_week", self._key_day_of_week),
            ("hour_of_day", self._key_hour_of_day),
            ("weather_condition", self._key_weather_condition),
            ("season", self._key_season),
            ("price_regime", self._key_price_regime),
            ("solar_availability", self._key_solar_availability),
        ]

        for dimension_name, key_func in dimensions:
            stats = self._analyze_dimension(decisions, dimension_name, key_func)
            report.dimensions[dimension_name] = stats

        # Detect biases from the analysis
        report.biases_detected = self.detect_biases(report, decisions)

        # Calculate weeks of data span
        if decisions:
            timestamps = [d.timestamp for d in decisions]
            min_time = min(timestamps)
            max_time = max(timestamps)
            self._weeks_of_data = max(1, int((max_time - min_time).days / 7))

        self._last_report = report
        self._last_analysis_time = now

        _LOGGER.info(
            "Pattern analysis complete: %d decisions analyzed, %d biases detected",
            len(decisions),
            len(report.biases_detected),
        )

        return report

    def _analyze_dimension(
        self,
        decisions: list[DecisionRecord],
        dimension: str,
        key_func: Callable[[DecisionRecord], str],
    ) -> DimensionStats:
        """Group decisions by dimension key, compute stats per group.

        Args:
            decisions: Decision records to analyze
            dimension: Dimension name
            key_func: Function to extract dimension key from a decision

        Returns:
            DimensionStats with grouped analysis
        """
        stats = DimensionStats(dimension=dimension)

        # Group decisions by key
        groups: dict[str, list[DecisionRecord]] = defaultdict(list)
        for decision in decisions:
            key = key_func(decision)
            groups[key].append(decision)

        # Compute stats for each group
        all_scores: list[float] = []

        for key, group_decisions in groups.items():
            bucket = self._compute_bucket_stats(key, dimension, group_decisions)
            stats.groups[key] = bucket

            # Collect scores for global stats
            for d in group_decisions:
                if d.outcome_score is not None:
                    all_scores.append(d.outcome_score)

        # Compute global stats
        if all_scores:
            stats.global_mean = sum(all_scores) / len(all_scores)
            if len(all_scores) > 1:
                variance = sum((s - stats.global_mean) ** 2 for s in all_scores) / (
                    len(all_scores) - 1
                )
                stats.global_std = math.sqrt(variance)

        return stats

    def _compute_bucket_stats(
        self, key: str, dimension: str, decisions: list[DecisionRecord]
    ) -> PatternBucket:
        """Compute statistics for a single bucket.

        Args:
            key: Bucket key
            dimension: Dimension name
            decisions: Decisions in this bucket

        Returns:
            PatternBucket with computed statistics
        """
        scores = [d.outcome_score for d in decisions if d.outcome_score is not None]
        sample_count = len(scores)

        if sample_count == 0:
            return PatternBucket(key=key, dimension=dimension)

        mean_score = sum(scores) / sample_count

        # Standard deviation
        if sample_count > 1:
            variance = sum((s - mean_score) ** 2 for s in scores) / (sample_count - 1)
            std_score = math.sqrt(variance)
        else:
            std_score = 0.0

        # Over-charge rate: grid charge decisions that resulted in export
        # Issue #449 Phase 7: compare against PlannerAction (DP-native) values
        _grid_charge_actions = {
            PlannerAction.CHARGE_GRID_NORMAL,
            PlannerAction.CHARGE_GRID_BOOST,
        }
        grid_charge_count = sum(
            1 for d in decisions if d.mode_chosen in _grid_charge_actions
        )
        over_charge_count = sum(
            1
            for d in decisions
            if d.mode_chosen in _grid_charge_actions
            and d.actual_export_kwh is not None
            and d.actual_export_kwh > 0.5
        )
        over_charge_rate = (
            over_charge_count / grid_charge_count if grid_charge_count > 0 else 0.0
        )

        # Under-charge rate: SOC dropped below target
        under_charge_count = sum(
            1
            for d in decisions
            if d.actual_soc_change is not None
            and d.actual_soc_change < -10  # Lost more than 10% SOC unexpectedly
        )
        under_charge_rate = under_charge_count / sample_count

        # Export loss rate: exported grid-purchased energy
        export_loss_count = sum(
            1
            for d in decisions
            if d.actual_export_kwh is not None
            and d.actual_export_kwh > 0.5
            and d.actual_import_kwh is not None
            and d.actual_import_kwh > 0.5
        )
        export_loss_rate = export_loss_count / sample_count

        return PatternBucket(
            key=key,
            dimension=dimension,
            sample_count=sample_count,
            mean_score=mean_score,
            std_score=std_score,
            over_charge_rate=over_charge_rate,
            under_charge_rate=under_charge_rate,
            export_loss_rate=export_loss_rate,
        )

    def detect_biases(
        self, report: PatternReport, decisions: list[DecisionRecord]
    ) -> list[BiasCorrection]:
        """Identify actionable biases from pattern report.

        A bias is detected when:
        - A dimension group's mean score is >1 std dev below global mean
        - The group has at least MIN_SAMPLES_FOR_BIAS samples
        - The pattern has persisted for MIN_WEEKS_OBSERVED

        Args:
            report: Pattern analysis report
            decisions: Original decision records (for weeks calculation)

        Returns:
            List of BiasCorrection objects
        """
        biases: list[BiasCorrection] = []

        # Check if we have enough weeks of data for reliable bias detection
        if self._weeks_of_data < MIN_WEEKS_OBSERVED:
            _LOGGER.debug(
                "Skipping bias detection: only %d weeks of data (need %d)",
                self._weeks_of_data,
                MIN_WEEKS_OBSERVED,
            )
            return biases

        for dimension_name, stats in report.dimensions.items():
            if stats.global_std == 0:
                continue  # No variance, no biases

            for key, bucket in stats.groups.items():
                # Check minimum sample count
                if bucket.sample_count < MIN_SAMPLES_FOR_BIAS:
                    continue

                # Check if score is significantly below global mean
                score_diff = stats.global_mean - bucket.mean_score
                if score_diff <= stats.global_std * BIAS_STD_DEV_THRESHOLD:
                    continue  # Not significantly worse

                # Determine which parameter and adjustment
                correction = self._map_bias_to_correction(
                    dimension_name, key, bucket, stats
                )
                if correction:
                    biases.append(correction)

        # Sort by confidence (highest first)
        biases.sort(key=lambda b: b.confidence, reverse=True)

        return biases

    def _map_bias_to_correction(
        self,
        dimension: str,
        key: str,
        bucket: PatternBucket,
        stats: DimensionStats,
    ) -> BiasCorrection | None:
        """Map a detected bias pattern to a parameter adjustment.

        Args:
            dimension: Dimension name
            key: Group key
            bucket: Bucket statistics
            stats: Full dimension stats

        Returns:
            BiasCorrection or None if no mapping exists
        """
        confidence = self._calculate_bias_confidence(bucket, stats)

        for check_fn in [
            self._check_over_charge_adjustment,
            self._check_export_loss_adjustment,
            self._check_under_charge_adjustment,
            self._check_generic_low_score_adjustment,
        ]:
            result = check_fn(dimension, key, bucket, stats, confidence)
            if result is not None:
                return result
        return None

    def _calculate_bias_confidence(
        self, bucket: PatternBucket, stats: DimensionStats
    ) -> float:
        """Calculate confidence for bias correction.

        Args:
            bucket: Bucket statistics
            stats: Full dimension stats

        Returns:
            Confidence score (0.0 to 1.0)
        """
        score_diff = stats.global_mean - bucket.mean_score
        severity = (
            score_diff / (stats.global_std * BIAS_STD_DEV_THRESHOLD)
            if stats.global_std > 0
            else 0
        )
        sample_confidence = min(1.0, bucket.sample_count / 50.0)
        variance_confidence = 1.0 - min(1.0, bucket.std_score * 2)
        return min(
            1.0, (severity * 0.4 + sample_confidence * 0.4 + variance_confidence * 0.2)
        )

    def _check_over_charge_adjustment(
        self,
        dimension: str,
        key: str,
        bucket: PatternBucket,
        stats: DimensionStats,
        confidence: float,
    ) -> BiasCorrection | None:
        """Check for over-charge rate adjustments.

        Args:
            dimension: Dimension name
            key: Group key
            bucket: Bucket statistics
            stats: Dimension stats
            confidence: Calculated confidence

        Returns:
            BiasCorrection or None
        """
        if bucket.over_charge_rate <= 0.3:
            return None

        if dimension == "weather_condition" and key in ("cloudy", "rainy"):
            return self._create_correction(
                "solar_confidence_factor",
                -0.1,
                f"{key.title()} weather has {bucket.over_charge_rate:.0%} over-charge rate",
                dimension,
                key,
                bucket,
                confidence,
            )
        elif dimension == "day_of_week":
            return self._create_correction(
                "solar_confidence_factor",
                -0.05,
                f"{key.title()}s have {bucket.over_charge_rate:.0%} over-charge rate",
                dimension,
                key,
                bucket,
                confidence,
            )
        elif dimension == "solar_availability" and key == "low":
            return self._create_correction(
                "solar_confidence_factor",
                -0.1,
                "Low solar days have high over-charge rate",
                dimension,
                key,
                bucket,
                confidence,
            )
        return None

    def _check_export_loss_adjustment(
        self,
        dimension: str,
        key: str,
        bucket: PatternBucket,
        stats: DimensionStats,
        confidence: float,
    ) -> BiasCorrection | None:
        """Check for export loss adjustments.

        Args:
            dimension: Dimension name
            key: Group key
            bucket: Bucket statistics
            stats: Dimension stats
            confidence: Calculated confidence

        Returns:
            BiasCorrection or None
        """
        if bucket.export_loss_rate <= 0.2:
            return None

        if dimension == "price_regime" and key == "low":
            return self._create_correction(
                "cheap_price_bias",
                -1.0,
                "Low price periods have high export loss",
                dimension,
                key,
                bucket,
                confidence,
            )
        return self._create_correction(
            "grid_charge_soc_headroom",
            -2.0,
            f"{key} has {bucket.export_loss_rate:.0%} export loss rate",
            dimension,
            key,
            bucket,
            confidence,
        )

    def _check_under_charge_adjustment(
        self,
        dimension: str,
        key: str,
        bucket: PatternBucket,
        stats: DimensionStats,
        confidence: float,
    ) -> BiasCorrection | None:
        """Check for under-charge adjustments.

        Args:
            dimension: Dimension name
            key: Group key
            bucket: Bucket statistics
            stats: Dimension stats
            confidence: Calculated confidence

        Returns:
            BiasCorrection or None
        """
        if bucket.under_charge_rate <= 0.2:
            return None

        if dimension == "weather_condition" and key in ("sunny", "clear"):
            return self._create_correction(
                "solar_confidence_factor",
                0.1,
                f"{key.title()} weather has {bucket.under_charge_rate:.0%} under-charge rate",
                dimension,
                key,
                bucket,
                confidence,
            )
        elif dimension == "solar_availability" and key == "high":
            return self._create_correction(
                "overnight_drain_safety_margin",
                -2.0,
                "High solar days have under-charge issues",
                dimension,
                key,
                bucket,
                confidence,
            )
        return self._create_correction(
            "cheap_price_bias",
            0.5,
            f"{key} has {bucket.under_charge_rate:.0%} under-charge rate",
            dimension,
            key,
            bucket,
            confidence,
        )

    def _check_generic_low_score_adjustment(
        self,
        dimension: str,
        key: str,
        bucket: PatternBucket,
        stats: DimensionStats,
        confidence: float,
    ) -> BiasCorrection | None:
        """Check for generic low score adjustments.

        Args:
            dimension: Dimension name
            key: Group key
            bucket: Bucket statistics
            stats: Dimension stats
            confidence: Calculated confidence

        Returns:
            BiasCorrection or None
        """
        score_diff = stats.global_mean - bucket.mean_score
        if score_diff <= stats.global_std * 1.5:
            return None

        if dimension == "hour_of_day":
            hour = int(key.split("_")[0]) if "_" in key else int(key)
            if 6 <= hour <= 10:
                return self._create_correction(
                    "consumption_forecast_bias",
                    0.1,
                    f"Hour {hour} has consistently low scores",
                    dimension,
                    key,
                    bucket,
                    confidence,
                )
            elif 17 <= hour <= 21:
                return self._create_correction(
                    "overnight_drain_safety_margin",
                    2.0,
                    f"Evening hour {hour} has consistently low scores",
                    dimension,
                    key,
                    bucket,
                    confidence,
                )
        elif dimension == "season":
            if key == "winter":
                return self._create_correction(
                    "overnight_drain_safety_margin",
                    3.0,
                    "Winter has consistently low decision scores",
                    dimension,
                    key,
                    bucket,
                    confidence,
                )
            elif key == "summer":
                return self._create_correction(
                    "solar_confidence_factor",
                    0.1,
                    "Summer has consistently low decision scores",
                    dimension,
                    key,
                    bucket,
                    confidence,
                )
        return None

    def _create_correction(
        self,
        param_name: str,
        adjustment: float,
        condition: str,
        dimension: str,
        key: str,
        bucket: PatternBucket,
        confidence: float,
    ) -> BiasCorrection | None:
        """Create a bias correction after validation.

        Args:
            param_name: Parameter to adjust
            adjustment: Adjustment value
            condition: Condition description
            dimension: Dimension name
            key: Group key
            bucket: Bucket statistics
            confidence: Confidence score

        Returns:
            BiasCorrection or None if invalid
        """
        if param_name not in OPTIMIZABLE_PARAMS:
            _LOGGER.warning("Unknown parameter %s in bias mapping", param_name)
            return None

        param_def = OPTIMIZABLE_PARAMS[param_name]
        adjustment = max(
            param_def.min_val - param_def.default,
            min(param_def.max_val - param_def.default, adjustment),
        )

        return BiasCorrection(
            condition=condition,
            dimension=dimension,
            group_key=key,
            param_name=param_name,
            adjustment=adjustment,
            confidence=confidence,
            sample_count=bucket.sample_count,
            weeks_observed=self._weeks_of_data,
        )

    # Dimension key extraction functions
    def _key_day_of_week(self, decision: DecisionRecord) -> str:
        """Extract day of week key."""
        days = [
            "monday",
            "tuesday",
            "wednesday",
            "thursday",
            "friday",
            "saturday",
            "sunday",
        ]
        return days[decision.day_of_week]

    def _key_hour_of_day(self, decision: DecisionRecord) -> str:
        """Extract hour of day key."""
        return f"{decision.hour_of_day:02d}"

    def _key_weather_condition(self, decision: DecisionRecord) -> str:
        """Extract weather condition key."""
        condition = decision.weather_condition.lower()

        # Normalize weather conditions
        if "sunny" in condition or "clear" in condition:
            return "sunny"
        elif "cloudy" in condition or "overcast" in condition:
            return "cloudy"
        elif "rain" in condition or "shower" in condition:
            return "rainy"
        elif "snow" in condition or "hail" in condition:
            return "snow"
        elif "fog" in condition or "mist" in condition:
            return "foggy"
        else:
            return "unknown"

    def _key_season(self, decision: DecisionRecord) -> str:
        """Extract season key from timestamp."""
        month = decision.timestamp.month
        if month in (12, 1, 2):
            return "summer"  # Southern hemisphere
        elif month in (3, 4, 5):
            return "autumn"
        elif month in (6, 7, 8):
            return "winter"
        else:
            return "spring"

    def _key_price_regime(self, decision: DecisionRecord) -> str:
        """Extract price regime key from decision context."""
        price = decision.general_price_at_decision

        # Determine price regime relative to typical prices
        # These thresholds are approximate c/kWh values
        if price < 0.10:  # 10 c/kWh
            return "low"
        elif price < 0.25:  # 25 c/kWh
            return "medium"
        else:
            return "high"

    def _key_solar_availability(self, decision: DecisionRecord) -> str:
        """Extract solar availability key from forecast."""
        solar_remaining = decision.forecast_solar_remaining_kwh

        if solar_remaining > 15:
            return "high"
        elif solar_remaining > 5:
            return "medium"
        else:
            return "low"

    def get_last_report(self) -> PatternReport | None:
        """Return the most recent pattern report.

        Returns:
            Last PatternReport or None if never run
        """
        return self._last_report

    def should_run_analysis(self, days_since_last: int, new_decisions: int) -> bool:
        """Check if pattern analysis should run.

        Args:
            days_since_last: Days since last analysis
            new_decisions: Number of new decisions since last analysis

        Returns:
            True if analysis should run
        """
        # Run at least weekly
        if days_since_last >= 7:
            return True

        # Run if we have significant new data
        if new_decisions >= 50:
            return True

        return False

    def reset(self) -> None:
        """Reset all pattern data."""
        self._pattern_data.clear()
        self._last_report = None
        self._weeks_of_data = 0
        self._last_analysis_time = None
        _LOGGER.info("Pattern analyzer reset")

    async def async_save(self) -> None:
        """Persist pattern analysis state to storage."""
        data = {
            "last_report": self._last_report.to_dict() if self._last_report else None,
            "weeks_of_data": self._weeks_of_data,
            "last_analysis_time": self._last_analysis_time.isoformat()
            if self._last_analysis_time
            else None,
        }
        await self._store.async_save(data)
        _LOGGER.debug("Pattern analyzer state saved")

    async def async_load(self) -> None:
        """Restore pattern analysis state from storage."""
        data = await self._store.async_load()
        if data is None:
            _LOGGER.debug("No saved pattern analyzer state found")
            return

        if data.get("last_report"):
            self._last_report = PatternReport.from_dict(data["last_report"])

        self._weeks_of_data = data.get("weeks_of_data", 0)

        if data.get("last_analysis_time"):
            try:
                self._last_analysis_time = datetime.fromisoformat(
                    data["last_analysis_time"]
                )
            except (ValueError, TypeError):
                self._last_analysis_time = None

        _LOGGER.debug(
            "Pattern analyzer state loaded: %d biases, %d weeks of data",
            len(self._last_report.biases_detected) if self._last_report else 0,
            self._weeks_of_data,
        )

    def get_diagnostics(self) -> dict[str, Any]:
        """Get diagnostic information about the analyzer.

        Returns:
            Dictionary with analyzer state for diagnostics
        """
        return {
            "last_analysis": self._last_analysis_time.isoformat()
            if self._last_analysis_time
            else None,
            "weeks_of_data": self._weeks_of_data,
            "biases_detected": len(self._last_report.biases_detected)
            if self._last_report
            else 0,
            "last_report_summary": self._last_report.get_summary()
            if self._last_report
            else None,
        }
