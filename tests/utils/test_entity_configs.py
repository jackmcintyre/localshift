"""Tests for entity_configs module.

These tests verify the extracted configuration dictionaries and EntityCategory enum.
"""

import pytest

from custom_components.localshift.utils.entity_configs import (
    ENTITY_CONFIG,
    FAILURE_THRESHOLD_ERROR,
    FAILURE_THRESHOLD_WARNING,
    LOCALSHIFT_ENTITY_CONFIG,
    STALENESS_THRESHOLDS,
    EntityCategory,
)
from custom_components.localshift.const import (
    CONF_TESLEMETRY_OPERATION_MODE,
    CONF_TESLEMETRY_SOC,
    CONF_PRICING_GENERAL_PRICE,
)


class TestEntityCategory:
    """Tests for EntityCategory enum."""

    def test_has_required_category(self):
        """EntityCategory should have REQUIRED."""
        assert EntityCategory.REQUIRED.value == "required"

    def test_has_recommended_category(self):
        """EntityCategory should have RECOMMENDED."""
        assert EntityCategory.RECOMMENDED.value == "recommended"

    def test_has_optional_category(self):
        """EntityCategory should have OPTIONAL."""
        assert EntityCategory.OPTIONAL.value == "optional"

    def test_all_categories_defined(self):
        """Should have exactly 3 categories."""
        assert len(list(EntityCategory)) == 3


class TestEntityConfig:
    """Tests for ENTITY_CONFIG dictionary."""

    def test_contains_teslemetry_operation_mode(self):
        """ENTITY_CONFIG should contain Teslemetry operation mode."""
        assert CONF_TESLEMETRY_OPERATION_MODE in ENTITY_CONFIG
        config = ENTITY_CONFIG[CONF_TESLEMETRY_OPERATION_MODE]
        assert config["category"] == EntityCategory.REQUIRED
        assert "valid_values" in config

    def test_contains_teslemetry_soc(self):
        """ENTITY_CONFIG should contain Teslemetry SOC."""
        assert CONF_TESLEMETRY_SOC in ENTITY_CONFIG
        config = ENTITY_CONFIG[CONF_TESLEMETRY_SOC]
        assert config["category"] == EntityCategory.REQUIRED
        assert config["min_value"] == 0
        assert config["max_value"] == 100

    def test_contains_pricing_general_price(self):
        """ENTITY_CONFIG should contain general price."""
        assert CONF_PRICING_GENERAL_PRICE in ENTITY_CONFIG
        config = ENTITY_CONFIG[CONF_PRICING_GENERAL_PRICE]
        assert config["category"] == EntityCategory.REQUIRED


class TestLocalshiftEntityConfig:
    """Tests for LOCALSHIFT_ENTITY_CONFIG dictionary."""

    def test_contains_required_sensors(self):
        """LOCALSHIFT_ENTITY_CONFIG should contain required sensors."""
        assert "sensor.localshift_optimizer_plan" in LOCALSHIFT_ENTITY_CONFIG
        assert "sensor.localshift_forecast_battery" in LOCALSHIFT_ENTITY_CONFIG

    def test_contains_binary_sensors(self):
        """LOCALSHIFT_ENTITY_CONFIG should contain binary sensors."""
        assert "binary_sensor.localshift_charge_forced" in LOCALSHIFT_ENTITY_CONFIG
        assert "binary_sensor.localshift_discharge_forced" in LOCALSHIFT_ENTITY_CONFIG

    def test_contains_switches(self):
        """LOCALSHIFT_ENTITY_CONFIG should contain switches."""
        assert "switch.localshift_automation_enabled" in LOCALSHIFT_ENTITY_CONFIG

    def test_all_entries_have_category(self):
        """All entries should have a category."""
        for entity_id, config in LOCALSHIFT_ENTITY_CONFIG.items():
            assert "category" in config, f"Missing category for {entity_id}"
            assert isinstance(config["category"], EntityCategory)


class TestStalenessThresholds:
    """Tests for STALENESS_THRESHOLDS dictionary."""

    def test_contains_soc_threshold(self):
        """STALENESS_THRESHOLDS should contain SOC threshold."""
        from datetime import timedelta

        assert CONF_TESLEMETRY_SOC in STALENESS_THRESHOLDS
        assert STALENESS_THRESHOLDS[CONF_TESLEMETRY_SOC] == timedelta(minutes=30)

    def test_soc_threshold_is_30_minutes(self):
        """SOC staleness threshold should be 30 minutes (increased from 5)."""
        from datetime import timedelta

        assert STALENESS_THRESHOLDS[CONF_TESLEMETRY_SOC] == timedelta(minutes=30)


class TestFailureThresholds:
    """Tests for failure threshold constants."""

    def test_warning_threshold_is_3(self):
        """Warning threshold should be 3 failures."""
        assert FAILURE_THRESHOLD_WARNING == 3

    def test_error_threshold_is_10(self):
        """Error threshold should be 10 failures."""
        assert FAILURE_THRESHOLD_ERROR == 10

    def test_error_greater_than_warning(self):
        """Error threshold should be greater than warning."""
        assert FAILURE_THRESHOLD_ERROR > FAILURE_THRESHOLD_WARNING
