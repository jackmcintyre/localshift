"""Tests for cost functions (stage_cost, terminal_cost, penalty factors)."""

from datetime import UTC, datetime

from custom_components.localshift.engine.cost import (
    stage_cost,
    terminal_cost,
)
from custom_components.localshift.engine.types import (
    ObjectiveTerms,
    OptimizerConfig,
    PlannerAction,
    SlotContext,
)


class TestStageCost:
    """Test stage_cost computation."""

    def test_hold_action_minimal_cost(self):
        """HOLD action should have minimal cost."""
        config = OptimizerConfig()
        slot = SlotContext(
            slot_index=0,
            timestamp_iso=datetime.now(UTC).isoformat(),
            buy_price=30.0,
            sell_price=10.0,
            solar_kwh=1.0,
            consumption_kwh=1.0,
            is_demand_window_slot=False,
            slot_interval_minutes=5,
        )

        terms = stage_cost(PlannerAction.HOLD, 0.0, 0.0, slot, config)

        assert isinstance(terms, ObjectiveTerms)
        assert terms.import_cost == 0.0
        assert terms.export_revenue == 0.0

    def test_import_cost_calculation(self):
        """Import cost calculated correctly."""
        config = OptimizerConfig()
        slot = SlotContext(
            slot_index=0,
            timestamp_iso=datetime.now(UTC).isoformat(),
            buy_price=30.0,
            sell_price=10.0,
            solar_kwh=0.0,
            consumption_kwh=2.0,
            is_demand_window_slot=False,
            slot_interval_minutes=5,
        )

        # 1 kWh imported at 30 c/kWh = 30 cents
        terms = stage_cost(PlannerAction.CHARGE_GRID_NORMAL, 1.0, 0.0, slot, config)
        assert terms.import_cost == 30.0

    def test_export_revenue_calculation(self):
        """Export revenue calculated correctly."""
        config = OptimizerConfig()
        slot = SlotContext(
            slot_index=0,
            timestamp_iso=datetime.now(UTC).isoformat(),
            buy_price=30.0,
            sell_price=10.0,
            solar_kwh=0.0,
            consumption_kwh=2.0,
            is_demand_window_slot=False,
            slot_interval_minutes=5,
        )

        # 1 kWh exported at 10 c/kWh = 10 cents revenue
        terms = stage_cost(PlannerAction.EXPORT_PROACTIVE, 0.0, 1.0, slot, config)
        assert terms.export_revenue == 10.0

    def test_cycle_penalty_applied(self):
        """Cycle penalty applied for grid import/export."""
        config = OptimizerConfig(cycle_penalty_per_kwh=0.5)
        slot = SlotContext(
            slot_index=0,
            timestamp_iso=datetime.now(UTC).isoformat(),
            buy_price=30.0,
            sell_price=10.0,
            solar_kwh=0.0,
            consumption_kwh=2.0,
            is_demand_window_slot=False,
            slot_interval_minutes=5,
        )
        terms = stage_cost(PlannerAction.CHARGE_GRID_NORMAL, 1.0, 0.0, slot, config)
        assert terms.cycle_penalty == 0.5  # 1 kWh * 0.5 c/kWh

    def test_switching_penalty_for_mode_switch(self):
        """Switching penalty applied when action changes."""
        config = OptimizerConfig(switching_penalty=100.0)
        slot = SlotContext(
            slot_index=0,
            timestamp_iso=datetime.now(UTC).isoformat(),
            buy_price=30.0,
            sell_price=10.0,
            solar_kwh=1.0,
            consumption_kwh=1.0,
            is_demand_window_slot=False,
            slot_interval_minutes=5,
        )

        terms = stage_cost(
            PlannerAction.CHARGE_GRID_NORMAL, 0.0, 0.0, slot, config, is_switch=True
        )
        assert terms.switching_penalty == 100.0

        terms = stage_cost(
            PlannerAction.CHARGE_GRID_NORMAL, 0.0, 0.0, slot, config, is_switch=False
        )
        assert terms.switching_penalty == 0.0

    def test_negative_sell_price_clamped(self):
        """Negative sell price should be clamped to 0."""
        config = OptimizerConfig()
        slot = SlotContext(
            slot_index=0,
            timestamp_iso=datetime.now(UTC).isoformat(),
            buy_price=30.0,
            sell_price=-10.0,  # Negative price
            solar_kwh=3.0,
            consumption_kwh=1.0,
            is_demand_window_slot=False,
            slot_interval_minutes=5,
        )

        terms = stage_cost(PlannerAction.EXPORT_PROACTIVE, 0.0, 1.0, slot, config)
        assert terms.export_revenue == -10.0  # Issue #719: no longer clamped

    def test_stage_cost_returns_objective_terms(self):
        """stage_cost returns ObjectiveTerms with all expected fields."""
        config = OptimizerConfig()
        slot = SlotContext(
            slot_index=0,
            timestamp_iso=datetime.now(UTC).isoformat(),
            buy_price=30.0,
            sell_price=10.0,
            solar_kwh=1.0,
            consumption_kwh=1.0,
            is_demand_window_slot=False,
            slot_interval_minutes=5,
        )

        terms = stage_cost(PlannerAction.HOLD, 0.0, 0.0, slot, config)

        # Check all expected fields exist
        assert hasattr(terms, "import_cost")
        assert hasattr(terms, "export_revenue")
        assert hasattr(terms, "cycle_penalty")
        assert hasattr(terms, "shortfall_penalty")
        assert hasattr(terms, "switching_penalty")

    def test_stage_cost_with_solar_penalty_factor(self):
        """Solar opportunity penalty applied with factor."""
        config = OptimizerConfig(optimization_mode="self_consumption")
        slot = SlotContext(
            slot_index=0,
            timestamp_iso=datetime.now(UTC).isoformat(),
            buy_price=20.0,
            sell_price=10.0,
            solar_kwh=0.0,  # No solar
            consumption_kwh=1.0,
            is_demand_window_slot=False,
            slot_interval_minutes=5,
        )

        terms = stage_cost(
            PlannerAction.CHARGE_GRID_NORMAL,
            1.0,  # 1 kWh import
            0.0,
            slot,
            config,
            solar_opportunity_penalty_factor=0.5,
        )

        # Solar opportunity penalty should be > 0
        assert terms.solar_opportunity_penalty > 0

    def test_uncertainty_penalty_short_horizon(self):
        """Uncertainty penalty applied when horizon is short."""
        config = OptimizerConfig(forecast_horizon_hours=10.0)  # Short horizon
        slot = SlotContext(
            slot_index=0,
            timestamp_iso=datetime.now(UTC).isoformat(),
            buy_price=30.0,
            sell_price=10.0,
            solar_kwh=0.0,
            consumption_kwh=1.0,
            is_demand_window_slot=False,
            slot_interval_minutes=5,
        )

        terms = stage_cost(PlannerAction.CHARGE_GRID_NORMAL, 1.0, 0.0, slot, config)

        # Uncertainty penalty should be > 0 for short horizon
        assert terms.uncertainty_penalty > 0

    def test_futile_cycling_penalty_applied(self):
        """Futile cycling penalty applied for grid charging."""
        config = OptimizerConfig(charge_efficiency=0.95, discharge_efficiency=0.95)
        slot = SlotContext(
            slot_index=0,
            timestamp_iso=datetime.now(UTC).isoformat(),
            buy_price=30.0,
            sell_price=10.0,
            solar_kwh=0.0,
            consumption_kwh=1.0,
            is_demand_window_slot=False,
            slot_interval_minutes=5,
        )

        terms = stage_cost(
            PlannerAction.CHARGE_GRID_NORMAL,
            1.0,
            0.0,
            slot,
            config,
            futile_cycling_penalty_factor=0.5,
        )

        # Futile cycling penalty should be > 0
        assert terms.futile_cycling_penalty > 0

    def test_self_consumption_value_with_soc_cap(self):
        """Self-consumption value capped by available SOC."""
        config = OptimizerConfig(optimization_mode="self_consumption")
        slot = SlotContext(
            slot_index=0,
            timestamp_iso=datetime.now(UTC).isoformat(),
            buy_price=30.0,
            sell_price=10.0,
            solar_kwh=0.0,
            consumption_kwh=5.0,  # High consumption
            is_demand_window_slot=False,
            slot_interval_minutes=5,
        )

        terms = stage_cost(
            PlannerAction.HOLD,
            0.0,
            0.0,
            slot,
            config,
            soc_pct=50.0,  # Current SOC
        )

        # Self-consumption value should be capped by available battery
        assert terms.self_consumption_value >= 0


class TestTerminalCost:
    """Test terminal_cost computation."""

    def test_no_shortfall_at_target(self):
        """No penalty when at target SOC."""
        config = OptimizerConfig(target_shortfall_penalty_per_pct=10.0)
        cost = terminal_cost(80.0, 80.0, config)
        assert cost == 0.0

    def test_no_shortfall_above_target(self):
        """No penalty when above target SOC."""
        config = OptimizerConfig(target_shortfall_penalty_per_pct=10.0)
        cost = terminal_cost(90.0, 80.0, config)
        assert cost == 0.0

    def test_shortfall_penalty_linear(self):
        """Penalty is linear in shortfall."""
        config = OptimizerConfig(target_shortfall_penalty_per_pct=10.0)

        # 10% shortfall
        cost = terminal_cost(70.0, 80.0, config)
        assert cost == 100.0  # 10 * 10

        # 20% shortfall
        cost = terminal_cost(60.0, 80.0, config)
        assert cost == 200.0  # 20 * 10

    def test_custom_penalty_rate(self):
        """Custom penalty rate applied correctly."""
        config = OptimizerConfig(target_shortfall_penalty_per_pct=50.0)

        cost = terminal_cost(70.0, 80.0, config)
        assert cost == 500.0  # 10 * 50


class TestObjectiveTerms:
    """Test ObjectiveTerms dataclass behavior."""

    def test_net_cost_computes_correctly(self):
        """net_cost property computes correctly."""
        terms = ObjectiveTerms(
            import_cost=100.0,
            export_revenue=50.0,  # Revenue is subtracted (negative cost)
            cycle_penalty=10.0,
            shortfall_penalty=5.0,
            switching_penalty=3.0,
        )

        expected = 100.0 - 50.0 + 10.0 + 5.0 + 3.0
        assert terms.net_cost == expected

    def test_net_cost_handles_zero_values(self):
        """net_cost handles zero values."""
        terms = ObjectiveTerms(
            import_cost=0.0,
            export_revenue=0.0,
            cycle_penalty=0.0,
            shortfall_penalty=0.0,
            switching_penalty=0.0,
        )
        assert terms.net_cost == 0.0

    def test_net_cost_negative_export(self):
        """Negative export revenue increases cost."""
        terms = ObjectiveTerms(
            import_cost=50.0,
            export_revenue=-20.0,  # Export cost money (e.g., grid charges)
            cycle_penalty=0.0,
            shortfall_penalty=0.0,
            switching_penalty=0.0,
        )

        # -(-20.0) = +20.0
        expected = 50.0 - (-20.0)
        assert terms.net_cost == expected
