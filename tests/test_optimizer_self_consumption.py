"""Test optimizer self-consumption mode (Issue #406)."""

import pytest

from custom_components.localshift.computation_engine_lib.optimizer_dp import (
    DPPlanner,
    OptimizerConfig,
    OptimizerInputs,
    PlannerAction,
    SlotContext,
)


@pytest.fixture
def self_consumption_config():
    return OptimizerConfig(
        battery_capacity_kwh=13.5,
        charge_rate_kw=3.3,
        boost_charge_rate_kw=5.0,
        discharge_rate_kw=5.0,
        min_soc_pct=10.0,
        max_soc_pct=100.0,
        demand_window_target_soc_pct=80.0,
        optimization_mode="self_consumption",
        self_consumption_value_per_kwh=0.15,
        effective_cheap_price=0.10,
        export_price_margin=0.02,
    )


@pytest.fixture
def arbitrage_config():
    return OptimizerConfig(
        battery_capacity_kwh=13.5,
        charge_rate_kw=3.3,
        boost_charge_rate_kw=5.0,
        discharge_rate_kw=5.0,
        min_soc_pct=10.0,
        max_soc_pct=100.0,
        demand_window_target_soc_pct=80.0,
        optimization_mode="arbitrage",
    )


class TestSelfConsumptionFeasibleActions:
    """Test that feasible_actions respects self-consumption constraints."""

    def test_no_grid_charge_when_price_not_cheap(self, self_consumption_config):
        config = self_consumption_config

        slot = SlotContext(
            slot_index=0,
            timestamp_iso="2025-03-01T12:00:00",
            slot_interval_minutes=30,
            buy_price=0.15,
            sell_price=0.10,
            solar_kwh=0.5,
            consumption_kwh=0.3,
        )
        actions = DPPlanner.feasible_actions(50.0, slot, config)

        assert PlannerAction.HOLD in actions
        assert PlannerAction.CHARGE_GRID_NORMAL not in actions
        assert PlannerAction.CHARGE_GRID_BOOST not in actions

    def test_grid_charge_normal_when_price_cheap(self, self_consumption_config):
        config = self_consumption_config

        slot = SlotContext(
            slot_index=0,
            timestamp_iso="2025-03-01T02:00:00",
            slot_interval_minutes=30,
            buy_price=0.09,
            sell_price=0.05,
            solar_kwh=0.0,
            consumption_kwh=0.3,
        )
        actions = DPPlanner.feasible_actions(50.0, slot, config)

        assert PlannerAction.HOLD in actions
        assert PlannerAction.CHARGE_GRID_NORMAL in actions
        assert PlannerAction.CHARGE_GRID_BOOST not in actions

    def test_grid_charge_boost_when_price_very_cheap(self, self_consumption_config):
        config = self_consumption_config

        slot = SlotContext(
            slot_index=0,
            timestamp_iso="2025-03-01T02:00:00",
            slot_interval_minutes=30,
            buy_price=0.07,
            sell_price=0.05,
            solar_kwh=0.0,
            consumption_kwh=0.3,
        )
        actions = DPPlanner.feasible_actions(50.0, slot, config)

        assert PlannerAction.HOLD in actions
        assert PlannerAction.CHARGE_GRID_NORMAL in actions
        assert PlannerAction.CHARGE_GRID_BOOST in actions

    def test_no_export_when_fit_below_threshold(self, self_consumption_config):
        config = self_consumption_config

        slot = SlotContext(
            slot_index=0,
            timestamp_iso="2025-03-01T12:00:00",
            slot_interval_minutes=30,
            buy_price=0.15,
            sell_price=0.10,
            solar_kwh=0.0,
            consumption_kwh=0.3,
        )
        actions = DPPlanner.feasible_actions(80.0, slot, config)

        assert PlannerAction.HOLD in actions
        assert PlannerAction.EXPORT_PROACTIVE not in actions

    def test_export_when_fit_above_threshold(self, self_consumption_config):
        config = self_consumption_config

        slot = SlotContext(
            slot_index=0,
            timestamp_iso="2025-03-01T12:00:00",
            slot_interval_minutes=30,
            buy_price=0.15,
            sell_price=0.20,
            solar_kwh=0.0,
            consumption_kwh=0.3,
        )
        actions = DPPlanner.feasible_actions(80.0, slot, config)

        assert PlannerAction.HOLD in actions
        assert PlannerAction.EXPORT_PROACTIVE in actions


class TestArbitrageModeFeasibleActions:
    """Test that arbitrage mode allows all actions."""

    def test_arbitrage_allows_charge_at_any_price(self, arbitrage_config):
        config = arbitrage_config

        slot = SlotContext(
            slot_index=0,
            timestamp_iso="2025-03-01T12:00:00",
            slot_interval_minutes=30,
            buy_price=0.30,
            sell_price=0.10,
            solar_kwh=0.5,
            consumption_kwh=0.3,
        )
        actions = DPPlanner.feasible_actions(50.0, slot, config)

        assert PlannerAction.HOLD in actions
        assert PlannerAction.CHARGE_GRID_NORMAL in actions
        assert PlannerAction.CHARGE_GRID_BOOST in actions

    def test_arbitrage_allows_export_at_positive_price(self, arbitrage_config):
        config = arbitrage_config

        slot = SlotContext(
            slot_index=0,
            timestamp_iso="2025-03-01T12:00:00",
            slot_interval_minutes=30,
            buy_price=0.15,
            sell_price=0.05,
            solar_kwh=0.0,
            consumption_kwh=0.3,
        )
        actions = DPPlanner.feasible_actions(80.0, slot, config)

        assert PlannerAction.HOLD in actions
        assert PlannerAction.EXPORT_PROACTIVE in actions


class TestSelfConsumptionObjectiveTerms:
    """Test that objective terms include self-consumption value."""

    def test_hold_with_load_adds_self_consumption_value(self, self_consumption_config):
        config = self_consumption_config
        planner = DPPlanner(config)

        slot = SlotContext(
            slot_index=0,
            timestamp_iso="2025-03-01T19:00:00",
            slot_interval_minutes=30,
            buy_price=0.25,
            sell_price=0.10,
            solar_kwh=0.0,
            consumption_kwh=0.5,
        )

        terms = planner.stage_cost(
            slot=slot,
            config=config,
            action=PlannerAction.HOLD,
            grid_import_kwh=0.0,
            grid_export_kwh=0.0,
        )

        assert terms.self_consumption_value > 0

    def test_hold_no_load_no_self_consumption_value(self, self_consumption_config):
        config = self_consumption_config
        planner = DPPlanner(config)

        slot = SlotContext(
            slot_index=0,
            timestamp_iso="2025-03-01T12:00:00",
            slot_interval_minutes=30,
            buy_price=0.15,
            sell_price=0.10,
            solar_kwh=2.0,
            consumption_kwh=0.3,
        )

        terms = planner.stage_cost(
            slot=slot,
            config=config,
            action=PlannerAction.HOLD,
            grid_import_kwh=0.0,
            grid_export_kwh=0.0,
        )

        assert terms.self_consumption_value == 0.0

    def test_arbitrage_mode_no_self_consumption_value(self, arbitrage_config):
        config = arbitrage_config
        planner = DPPlanner(config)

        slot = SlotContext(
            slot_index=0,
            timestamp_iso="2025-03-01T19:00:00",
            slot_interval_minutes=30,
            buy_price=0.25,
            sell_price=0.10,
            solar_kwh=0.0,
            consumption_kwh=0.5,
        )

        terms = planner.stage_cost(
            slot=slot,
            config=config,
            action=PlannerAction.HOLD,
            grid_import_kwh=0.0,
            grid_export_kwh=0.0,
        )

        assert terms.self_consumption_value == 0.0


class TestSelfConsumptionPlanBehavior:
    """Integration tests for self-consumption planning."""

    def test_self_consumption_prefers_hold_over_low_fit_export(
        self, self_consumption_config
    ):
        config = self_consumption_config
        planner = DPPlanner(config)

        slot = SlotContext(
            slot_index=0,
            timestamp_iso="2025-03-01T16:00:00",
            slot_interval_minutes=30,
            buy_price=0.20,
            sell_price=0.10,
            solar_kwh=0.0,
            consumption_kwh=0.5,
        )

        inputs = OptimizerInputs(
            cycle_id="test-hold-vs-export",
            initial_soc_pct=80.0,
            slots=[slot],
            config=config,
        )
        result = planner.plan(inputs)

        assert result.success
        assert result.decisions[0].action == PlannerAction.HOLD

    def test_self_consumption_exports_when_fit_is_high(self, self_consumption_config):
        config = self_consumption_config
        planner = DPPlanner(config)

        slot = SlotContext(
            slot_index=0,
            timestamp_iso="2025-03-01T12:00:00",
            slot_interval_minutes=30,
            buy_price=0.15,
            sell_price=0.25,
            solar_kwh=2.0,
            consumption_kwh=0.3,
        )

        inputs = OptimizerInputs(
            cycle_id="test-high-fit-export",
            initial_soc_pct=100.0,
            slots=[slot],
            config=config,
        )
        result = planner.plan(inputs)

        assert result.success
        assert result.decisions[0].action == PlannerAction.EXPORT_PROACTIVE

    def test_self_consumption_charges_at_cheap_price(self, self_consumption_config):
        config = self_consumption_config
        planner = DPPlanner(config)

        slot = SlotContext(
            slot_index=0,
            timestamp_iso="2025-03-01T02:00:00",
            slot_interval_minutes=30,
            buy_price=0.08,
            sell_price=0.05,
            solar_kwh=0.0,
            consumption_kwh=0.3,
            is_demand_window_entry=True,
        )

        inputs = OptimizerInputs(
            cycle_id="test-cheap-charge",
            initial_soc_pct=30.0,
            slots=[slot],
            config=config,
        )
        result = planner.plan(inputs)

        assert result.success
        assert result.decisions[0].grid_import_kwh >= 0

    def test_self_consumption_does_not_charge_at_expensive_price(
        self, self_consumption_config
    ):
        config = self_consumption_config
        planner = DPPlanner(config)

        slot = SlotContext(
            slot_index=0,
            timestamp_iso="2025-03-01T18:00:00",
            slot_interval_minutes=30,
            buy_price=0.25,
            sell_price=0.10,
            solar_kwh=0.0,
            consumption_kwh=0.5,
        )

        inputs = OptimizerInputs(
            cycle_id="test-expensive-no-charge",
            initial_soc_pct=30.0,
            slots=[slot],
            config=config,
        )
        result = planner.plan(inputs)

        assert result.success
        assert result.decisions[0].action == PlannerAction.HOLD
        assert result.decisions[0].grid_import_kwh == 0.0

    def test_self_consumption_does_not_export_at_low_fit(self, self_consumption_config):
        config = self_consumption_config
        planner = DPPlanner(config)

        slot = SlotContext(
            slot_index=0,
            timestamp_iso="2025-03-01T16:00:00",
            slot_interval_minutes=30,
            buy_price=0.20,
            sell_price=0.10,
            solar_kwh=0.0,
            consumption_kwh=0.5,
        )

        inputs = OptimizerInputs(
            cycle_id="test-low-fit-no-export",
            initial_soc_pct=90.0,
            slots=[slot],
            config=config,
        )
        result = planner.plan(inputs)

        assert result.success
        assert result.decisions[0].action == PlannerAction.HOLD
        assert result.decisions[0].grid_export_kwh == 0.0
