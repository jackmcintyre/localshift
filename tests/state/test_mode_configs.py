"""Tests for mode_configs module."""

import pytest

from custom_components.localshift.state.mode_configs import (
    MODE_CONFIG_BUILDERS,
    MODE_EXECUTORS,
    ModeConfig,
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
