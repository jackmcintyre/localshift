"""Data types for pattern analysis (Issue #646).

Extracted from pattern_analyzer.py for better separation of concerns.
These are pure data containers with serialization logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from homeassistant.util import dt as dt_util


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
                for b in self.biases_detected[:5]
            ],
        }
