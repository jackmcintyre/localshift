"""Tests for mode_configs module."""

import pytest

from custom_components.localshift.const import PROACTIVE_EXPORT_SOC_BUFFER_PERCENT
from custom_components.localshift.state.mode_configs import (
    MODE_CONFIG_BUILDERS,
    MODE_EXECUTORS,
    ModeConfig,
    calculate_proactive_export_reserve,
)


class TestModeConfigImports:
    """Test that ModeConfig and related items are importable."""

    def test_mode_config_is_dataclass(self):
        """Verify ModeConfig is properly defined."""
        config = ModeConfig(
            operation_mode="self_consumption",
            backup_reserve=10,
            export_mode="pv_only",
            grid_charging_allowed=False,
        )
        assert config.operation_mode == "self_consumption"
        assert config.backup_reserve == 10

    def test_mode_config_builders_defined(self):
        """Verify MODE_CONFIG_BUILDERS is defined."""
        assert len(MODE_CONFIG_BUILDERS) == 7

    def test_mode_executors_defined(self):
        """Verify MODE_EXECUTORS is defined."""
        assert len(MODE_EXECUTORS) == 7

    def test_mode_config_reserve_fields_default_none(self):
        """Reserve-tracking fields default to None (untracked).

        Issue #972: spike_discharge_reserve joins the family of tracked
        reserves (self_consumption / grid_charging / proactive_export).
        """
        config = ModeConfig(
            operation_mode="autonomous",
            backup_reserve=45,
            export_mode="battery_ok",
            grid_charging_allowed=False,
        )

        assert config.self_consumption_reserve is None
        assert config.grid_charging_reserve is None
        assert config.proactive_export_reserve is None
        assert config.spike_discharge_reserve is None


class TestCalculateProactiveExportReserve:
    """Issue #974: the ONE shared proactive-export reserve helper.

    Called by both _build_proactive_export_config (state machine) and
    BatteryController.set_proactive_export (controller) so the two sides can
    never drift again.
    """

    def test_buffer_applies_above_the_floor(self):
        """Well above the floor the SOC-buffer formula is unchanged."""
        assert calculate_proactive_export_reserve(50.0, 20.0) == 45.0

    def test_floors_at_minimum_target_soc(self):
        """The named case: SOC 22, minimum_target_soc 20 -> reserve 20."""
        assert calculate_proactive_export_reserve(22.0, 20.0) == 20.0

    def test_floors_when_soc_below_minimum_target(self):
        """SOC below the floor clamps to the floor (supersedes the old 4% floor)."""
        assert calculate_proactive_export_reserve(5.0, 20.0) == 20.0
        assert calculate_proactive_export_reserve(6.0, 20.0) == 20.0

    def test_floors_at_zero_soc(self):
        """SOC 0 (unavailable/empty entity) clamps to the floor (Issue #895)."""
        assert calculate_proactive_export_reserve(0.0, 20.0) == 20.0

    def test_uses_buffer_constant(self):
        """The buffer is PROACTIVE_EXPORT_SOC_BUFFER_PERCENT (5), not a literal."""
        assert calculate_proactive_export_reserve(30.0, 4.0) == (
            30.0 - PROACTIVE_EXPORT_SOC_BUFFER_PERCENT
        )
