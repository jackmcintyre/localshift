"""Tests for pattern_types dataclasses (Issue #646).

Tests for the extracted dataclasses from pattern_analyzer.py.
"""

from datetime import datetime

import pytest

from custom_components.localshift.engine.pattern_types import (
    BiasCorrection,
    DimensionStats,
    PatternBucket,
    PatternReport,
)


class TestPatternBucket:
    """Tests for PatternBucket dataclass."""

    def test_pattern_bucket_creation(self):
        """Test creating a PatternBucket."""
        bucket = PatternBucket(
            key="monday",
            dimension="day_of_week",
            sample_count=10,
            mean_score=0.75,
            std_score=0.1,
            over_charge_rate=0.2,
            under_charge_rate=0.1,
            export_loss_rate=0.05,
        )
        assert bucket.key == "monday"
        assert bucket.dimension == "day_of_week"
        assert bucket.sample_count == 10
        assert bucket.mean_score == 0.75

    def test_pattern_bucket_defaults(self):
        """Test PatternBucket default values."""
        bucket = PatternBucket(key="test", dimension="test_dim")
        assert bucket.sample_count == 0
        assert bucket.mean_score == 0.0
        assert bucket.std_score == 0.0

    def test_pattern_bucket_to_dict(self):
        """Test PatternBucket serialization."""
        bucket = PatternBucket(
            key="sunny",
            dimension="weather",
            sample_count=20,
            mean_score=0.8,
            std_score=0.05,
        )
        data = bucket.to_dict()
        assert data["key"] == "sunny"
        assert data["dimension"] == "weather"
        assert data["sample_count"] == 20
        assert data["mean_score"] == 0.8

    def test_pattern_bucket_from_dict(self):
        """Test PatternBucket deserialization."""
        data = {
            "key": "winter",
            "dimension": "season",
            "sample_count": 30,
            "mean_score": 0.65,
            "std_score": 0.15,
            "over_charge_rate": 0.1,
            "under_charge_rate": 0.2,
            "export_loss_rate": 0.05,
        }
        bucket = PatternBucket.from_dict(data)
        assert bucket.key == "winter"
        assert bucket.sample_count == 30

    def test_pattern_bucket_from_dict_partial(self):
        """Test PatternBucket deserialization with partial data."""
        data = {"key": "hour_12", "dimension": "hour_of_day"}
        bucket = PatternBucket.from_dict(data)
        assert bucket.key == "hour_12"
        assert bucket.sample_count == 0
        assert bucket.mean_score == 0.0


class TestDimensionStats:
    """Tests for DimensionStats dataclass."""

    def test_dimension_stats_creation(self):
        """Test creating DimensionStats."""
        stats = DimensionStats(
            dimension="day_of_week",
            global_mean=0.7,
            global_std=0.1,
        )
        assert stats.dimension == "day_of_week"
        assert stats.global_mean == 0.7
        assert stats.global_std == 0.1

    def test_dimension_stats_with_groups(self):
        """Test DimensionStats with PatternBucket groups."""
        bucket1 = PatternBucket(key="monday", dimension="day_of_week", sample_count=10)
        bucket2 = PatternBucket(key="tuesday", dimension="day_of_week", sample_count=15)
        stats = DimensionStats(
            dimension="day_of_week",
            groups={"monday": bucket1, "tuesday": bucket2},
        )
        assert len(stats.groups) == 2
        assert stats.groups["monday"].sample_count == 10

    def test_dimension_stats_to_dict(self):
        """Test DimensionStats serialization."""
        bucket = PatternBucket(key="cloudy", dimension="weather", sample_count=5)
        stats = DimensionStats(
            dimension="weather",
            groups={"cloudy": bucket},
            global_mean=0.6,
            global_std=0.12,
        )
        data = stats.to_dict()
        assert data["dimension"] == "weather"
        assert "cloudy" in data["groups"]
        assert data["global_mean"] == 0.6

    def test_dimension_stats_from_dict(self):
        """Test DimensionStats deserialization."""
        data = {
            "dimension": "season",
            "groups": {
                "summer": {
                    "key": "summer",
                    "dimension": "season",
                    "sample_count": 25,
                    "mean_score": 0.85,
                }
            },
            "global_mean": 0.75,
            "global_std": 0.1,
        }
        stats = DimensionStats.from_dict(data)
        assert stats.dimension == "season"
        assert "summer" in stats.groups
        assert stats.groups["summer"].sample_count == 25


class TestBiasCorrection:
    """Tests for BiasCorrection dataclass."""

    def test_bias_correction_creation(self):
        """Test creating a BiasCorrection."""
        bc = BiasCorrection(
            condition="weather=cloudy",
            dimension="weather",
            group_key="cloudy",
            param_name="solar_confidence_factor",
            adjustment=0.1,
            confidence=0.7,
            sample_count=50,
            weeks_observed=4,
        )
        assert bc.condition == "weather=cloudy"
        assert bc.param_name == "solar_confidence_factor"
        assert bc.adjustment == 0.1
        assert bc.confidence == 0.7

    def test_bias_correction_to_dict(self):
        """Test BiasCorrection serialization."""
        bc = BiasCorrection(
            condition="test_condition",
            dimension="test_dim",
            group_key="test_key",
            param_name="test_param",
            adjustment=1.5,
            confidence=0.8,
            sample_count=100,
            weeks_observed=2,
        )
        data = bc.to_dict()
        assert data["condition"] == "test_condition"
        assert data["param_name"] == "test_param"
        assert data["adjustment"] == 1.5

    def test_bias_correction_from_dict(self):
        """Test BiasCorrection deserialization."""
        data = {
            "condition": "test_condition",
            "dimension": "test_dim",
            "group_key": "test_key",
            "param_name": "test_param",
            "adjustment": 2.0,
            "confidence": 0.9,
            "sample_count": 75,
            "weeks_observed": 3,
        }
        bc = BiasCorrection.from_dict(data)
        assert bc.condition == "test_condition"
        assert bc.param_name == "test_param"
        assert bc.adjustment == 2.0
        assert bc.confidence == 0.9


class TestPatternReport:
    """Tests for PatternReport dataclass."""

    def test_pattern_report_creation(self):
        """Test creating a PatternReport."""
        report = PatternReport(data_points_analyzed=100)
        assert report.data_points_analyzed == 100
        assert report.dimensions == {}
        assert report.biases_detected == []

    def test_pattern_report_with_dimensions(self):
        """Test PatternReport with dimensions and biases."""
        dim_stats = DimensionStats(dimension="weather")
        bias = BiasCorrection(
            condition="test",
            dimension="weather",
            group_key="cloudy",
            param_name="test_param",
            adjustment=0.5,
            confidence=0.8,
        )
        report = PatternReport(
            dimensions={"weather": dim_stats},
            biases_detected=[bias],
            data_points_analyzed=50,
        )
        assert "weather" in report.dimensions
        assert len(report.biases_detected) == 1

    def test_pattern_report_to_dict(self):
        """Test PatternReport serialization."""
        report = PatternReport(data_points_analyzed=200)
        data = report.to_dict()
        assert "generated_at" in data
        assert data["data_points_analyzed"] == 200

    def test_pattern_report_from_dict(self):
        """Test PatternReport deserialization."""
        data = {
            "generated_at": "2024-01-15T10:30:00",
            "dimensions": {
                "day_of_week": {
                    "dimension": "day_of_week",
                    "groups": {},
                    "global_mean": 0.7,
                    "global_std": 0.1,
                }
            },
            "biases_detected": [],
            "data_points_analyzed": 150,
        }
        report = PatternReport.from_dict(data)
        assert report.data_points_analyzed == 150
        assert "day_of_week" in report.dimensions

    def test_pattern_report_get_summary(self):
        """Test PatternReport.get_summary()."""
        bias = BiasCorrection(
            condition="Low solar days",
            dimension="solar_availability",
            group_key="low",
            param_name="solar_confidence_factor",
            adjustment=-0.1,
            confidence=0.75,
        )
        report = PatternReport(
            biases_detected=[bias],
            data_points_analyzed=100,
        )
        summary = report.get_summary()
        assert summary["data_points"] == 100
        assert summary["biases_count"] == 1
        assert len(summary["top_biases"]) == 1
        assert summary["top_biases"][0]["param"] == "solar_confidence_factor"

    def test_pattern_report_from_dict_invalid_timestamp(self):
        """Test PatternReport.from_dict() handles invalid timestamp gracefully."""
        data = {
            "generated_at": "invalid-timestamp-format",
            "dimensions": {},
            "biases_detected": [],
            "data_points_analyzed": 50,
        }
        report = PatternReport.from_dict(data)
        assert report.data_points_analyzed == 50
