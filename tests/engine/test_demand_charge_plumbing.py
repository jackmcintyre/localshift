"""Tests for demand-charge config plumbing (P1a).

Covers the pure season-active helper and the mapping of demand-charge options
into OptimizerConfig via _build_optimizer_config.
"""

from types import SimpleNamespace

from custom_components.localshift.const import (
    CONF_DEMAND_WINDOW_IMPORT_PENALTY,
)
from custom_components.localshift.engine.optimizer_runner import _build_optimizer_config
from custom_components.localshift.engine.utils import demand_season_active


class TestDemandSeasonActive:
    """The pure month-vs-active-months helper (no clock dependency)."""

    def test_empty_active_months_is_always_active(self):
        assert demand_season_active(1, []) is True
        assert demand_season_active(7, []) is True

    def test_month_in_active_months(self):
        ausgrid = [1, 2, 3, 6, 7, 8]
        assert demand_season_active(6, ausgrid) is True  # June (winter)

    def test_month_not_in_active_months(self):
        ausgrid = [1, 2, 3, 6, 7, 8]
        assert demand_season_active(5, ausgrid) is False  # May (shoulder, no DW charge)
        assert demand_season_active(10, ausgrid) is False  # October

    def test_none_active_months_is_always_active(self):
        # Defensive: None should behave like "no restriction".
        assert demand_season_active(4, None) is True


class TestBuildOptimizerConfigDemand:
    """_build_optimizer_config threads demand-charge options into OptimizerConfig."""

    def test_defaults_disable_demand_charge(self):
        cfg = _build_optimizer_config(SimpleNamespace(), {})
        assert cfg.demand_window_import_penalty_per_kwh == 0.0
        assert (
            cfg.demand_charge_active is True
        )  # active flag defaults on; rate gates it

    def test_reads_penalty_and_active_flag(self):
        cfg = _build_optimizer_config(
            SimpleNamespace(),
            {
                CONF_DEMAND_WINDOW_IMPORT_PENALTY: 3.5,
                "demand_charge_active": False,
            },
        )
        assert cfg.demand_window_import_penalty_per_kwh == 3.5
        assert cfg.demand_charge_active is False
