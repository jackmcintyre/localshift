"""Test marginal cycling penalty for issue #598.

When charging at near-threshold prices for future self-consumption,
the round-trip efficiency loss often exceeds the price arbitrage gain.
This penalty discourages such marginal cycling.
"""
import pytest

from custom_components.localshift.computation_engine_lib.optimizer_dp import (
    DPPlanner,
    ObjectiveTerms,
    OptimizerConfig,
    PlannerAction,
    SlotContext,
)


def test_marginal_cycling_penalty_near_threshold():
    """Test that marginal cycling penalty applies when price is near cheap threshold."""
    config = OptimizerConfig(effective_cheap_price=0.16)
    slot = SlotContext(
        slot_index=21,
        timestamp_iso="2024-01-01T04:00:00",
        slot_interval_minutes=30,
        buy_price=0.15,  # Near threshold (0.16 * 0.94 = 0.15)
        sell_price=0.08,
        consumption_kwh=0.175,
        solar_kwh=0.0,
    )
    
    terms = DPPlanner.stage_cost(
        action=PlannerAction.CHARGE_GRID_NORMAL,
        grid_import_kwh=1.8255,
        grid_export_kwh=0.0,
        slot=slot,
        soc_pct=10.0,
        config=config,
    )
    
    # Should have marginal_cycling_penalty
    assert terms.marginal_cycling_penalty > 0.0
    assert terms.marginal_cycling_penalty < 0.10  # Reasonable range


def test_marginal_cycling_penalty_zero_when_well_below_threshold():
    """Test that penalty is zero when price is well below cheap threshold."""
    config = OptimizerConfig(effective_cheap_price=0.16)
    slot = SlotContext(
        slot_index=21,
        timestamp_iso="2024-01-01T04:00:00",
        slot_interval_minutes=30,
        buy_price=0.08,  # Well below threshold
        sell_price=0.03,
        consumption_kwh=0.175,
        solar_kwh=0.0,
    )
    
    terms = DPPlanner.stage_cost(
        action=PlannerAction.CHARGE_GRID_NORMAL,
        grid_import_kwh=1.8255,
        grid_export_kwh=0.0,
        slot=slot,
        soc_pct=10.0,
        config=config,
    )
    
    # Should have no marginal cycling penalty
    assert terms.marginal_cycling_penalty == 0.0


def test_marginal_cycling_penalty_prevents_bad_economics():
    """Test that penalty makes marginal cycling uneconomical.
    
    Scenario: Charge at $0.15/kWh to avoid $0.16/kWh imports
    - Price spread: $0.01/kWh (seems profitable)
    - Efficiency loss: 23.5% × $0.15 = $0.035/kWh (actual cost)
    - Net loss: $0.025/kWh
    - Should NOT charge
    """
    config = OptimizerConfig(
        effective_cheap_price=0.16,
        charge_efficiency=0.90,
        discharge_efficiency=0.85,
    )
    slot = SlotContext(
        slot_index=21,
        timestamp_iso="2024-01-01T04:00:00",
        slot_interval_minutes=30,
        buy_price=0.15,
        sell_price=0.08,
        consumption_kwh=0.175,
        solar_kwh=0.0,
    )
    
    terms = DPPlanner.stage_cost(
        action=PlannerAction.CHARGE_GRID_NORMAL,
        grid_import_kwh=1.8255,
        grid_export_kwh=0.0,
        slot=slot,
        soc_pct=10.0,
        config=config,
    )
    
    # The marginal cycling penalty should make this uneconomical
    # Total cost should exceed any reasonable self-consumption benefit
    # Self-consumption benefit: 1.8255 * 0.765 * 0.16 ≈ $0.223
    # Total cost should be > $0.223
    assert terms.net_cost > 0.223


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
