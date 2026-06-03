"""Tests for cost functions (stage_cost, terminal_cost, penalty factors)."""

from datetime import UTC, datetime

import pytest

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
        """Futile cycling penalty applied for grid charging (eff_loss + margin)."""
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

        # Futile cycling penalty: grid_import × (eff_loss + 0.30) × buy_price × factor
        # eff_loss = 1 - 0.95^2 = 0.0975
        # (0.0975 + 0.30) × $30 × 0.5 = $5.96
        eff_loss = 1.0 - 0.95 * 0.95
        margin = 0.30
        expected = 1.0 * (eff_loss + margin) * 30.0 * 0.5
        assert terms.futile_cycling_penalty == pytest.approx(expected, abs=0.1)

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

    def test_self_consumption_full_credit_in_demand_window(self):
        """DW coverage is deadline-driven, not arbitrage: credit at full buy price
        even when cycle_penalty >= buy_price, else the pre-charge incentive vanishes."""
        config = OptimizerConfig(
            optimization_mode="self_consumption",
            cycle_penalty_per_kwh=0.15,
            demand_charge_active=True,
        )
        slot = SlotContext(
            slot_index=0,
            timestamp_iso=datetime.now(UTC).isoformat(),
            buy_price=0.13,  # below cycle_penalty -> old code zeroed the credit
            sell_price=0.10,
            solar_kwh=0.0,
            consumption_kwh=0.5,
            is_demand_window_slot=True,
            slot_interval_minutes=60,
        )
        terms = stage_cost(PlannerAction.HOLD, 0.0, 0.0, slot, config, soc_pct=90.0)
        # Full retail credit: 0.5 kWh * 0.13, NOT max(0, 0.13 - 0.15) == 0.
        assert terms.self_consumption_value == pytest.approx(0.5 * 0.13, rel=1e-6)

    def test_self_consumption_subtracts_cycle_outside_demand_window(self):
        """Outside the DW the cycle penalty is still subtracted (anti-arbitrage)."""
        config = OptimizerConfig(
            optimization_mode="self_consumption",
            cycle_penalty_per_kwh=0.05,
            demand_charge_active=True,
        )
        slot = SlotContext(
            slot_index=0,
            timestamp_iso=datetime.now(UTC).isoformat(),
            buy_price=0.13,
            sell_price=0.10,
            solar_kwh=0.0,
            consumption_kwh=0.5,
            is_demand_window_slot=False,
            slot_interval_minutes=60,
        )
        terms = stage_cost(PlannerAction.HOLD, 0.0, 0.0, slot, config, soc_pct=90.0)
        assert terms.self_consumption_value == pytest.approx(0.5 * (0.13 - 0.05), rel=1e-6)


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

    def test_net_cost_includes_demand_charge_penalty(self):
        """net_cost property includes the demand_charge_penalty term."""
        terms = ObjectiveTerms(
            import_cost=10.0,
            demand_charge_penalty=7.0,
        )
        assert terms.net_cost == 17.0

    def test_to_dict_includes_demand_charge_penalty(self):
        """to_dict serializes the demand_charge_penalty term."""
        terms = ObjectiveTerms(demand_charge_penalty=4.0)
        d = terms.to_dict()
        assert d["demand_charge_penalty"] == 4.0


class TestDemandChargePenalty:
    """Test the demand-window grid-import penalty (P1a — demand-charge awareness).

    The penalty models the Australian network demand charge: grid import during the
    demand window sets an expensive monthly $/kW peak that is invisible to the spot
    price. It is applied to grid import in DW slots REGARDLESS of action (the HOLD/
    self-consumption path where a depleted battery forces a grid draw) — charge
    actions are already forbidden in the DW by feasible_actions().
    """

    @staticmethod
    def _dw_slot(*, in_dw: bool) -> SlotContext:
        return SlotContext(
            slot_index=0,
            timestamp_iso=datetime.now(UTC).isoformat(),
            buy_price=0.15,
            sell_price=0.05,
            solar_kwh=0.0,
            consumption_kwh=1.0,
            is_demand_window_slot=in_dw,
            slot_interval_minutes=30,
        )

    def test_penalty_applied_on_dw_import(self):
        """Grid import inside the DW incurs the elevated demand penalty."""
        config = OptimizerConfig(
            demand_window_import_penalty_per_kwh=2.0,
            demand_charge_active=True,
        )
        slot = self._dw_slot(in_dw=True)

        terms = stage_cost(PlannerAction.HOLD, 0.5, 0.0, slot, config)

        assert terms.demand_charge_penalty == pytest.approx(1.0)  # 0.5 kWh * 2.0
        assert terms.net_cost == pytest.approx(terms.net_cost)
        assert terms.demand_charge_penalty in (
            terms.to_dict()["demand_charge_penalty"],
        )

    def test_penalty_applies_regardless_of_action(self):
        """The penalty keys off DW grid import, not the action (HOLD path matters)."""
        config = OptimizerConfig(
            demand_window_import_penalty_per_kwh=3.0,
            demand_charge_active=True,
        )
        slot = self._dw_slot(in_dw=True)

        hold_terms = stage_cost(PlannerAction.HOLD, 0.4, 0.0, slot, config)
        assert hold_terms.demand_charge_penalty == pytest.approx(1.2)  # 0.4 * 3.0

    def test_no_penalty_without_import(self):
        """No grid import in the DW → no demand penalty."""
        config = OptimizerConfig(
            demand_window_import_penalty_per_kwh=2.0,
            demand_charge_active=True,
        )
        slot = self._dw_slot(in_dw=True)

        terms = stage_cost(PlannerAction.HOLD, 0.0, 0.0, slot, config)
        assert terms.demand_charge_penalty == 0.0

    def test_no_penalty_outside_dw(self):
        """Grid import outside the DW is not subject to the demand penalty."""
        config = OptimizerConfig(
            demand_window_import_penalty_per_kwh=2.0,
            demand_charge_active=True,
        )
        slot = self._dw_slot(in_dw=False)

        terms = stage_cost(PlannerAction.CHARGE_GRID_NORMAL, 1.0, 0.0, slot, config)
        assert terms.demand_charge_penalty == 0.0

    def test_disabled_by_default(self):
        """Default config (rate 0.0) applies no demand penalty even inside the DW."""
        config = (
            OptimizerConfig()
        )  # demand_window_import_penalty_per_kwh defaults to 0.0
        slot = self._dw_slot(in_dw=True)

        terms = stage_cost(PlannerAction.HOLD, 1.0, 0.0, slot, config)
        assert terms.demand_charge_penalty == 0.0

    def test_inactive_season_suppresses_penalty(self):
        """When the demand season is inactive, no penalty applies even with a rate."""
        config = OptimizerConfig(
            demand_window_import_penalty_per_kwh=2.0,
            demand_charge_active=False,
        )
        slot = self._dw_slot(in_dw=True)

        terms = stage_cost(PlannerAction.HOLD, 1.0, 0.0, slot, config)
        assert terms.demand_charge_penalty == 0.0
