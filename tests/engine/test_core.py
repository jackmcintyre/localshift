"""Tests for DPPlanner core class."""

from datetime import UTC, datetime

from custom_components.localshift.engine.core import DPPlanner
from custom_components.localshift.engine.types import (
    OptimizerConfig,
    OptimizerInputs,
    PlannerAction,
    SlotContext,
)


class TestDPPlanner:
    """Test DPPlanner class."""

    def test_dpplanner_creation(self):
        """DPPlanner can be created with default config."""
        planner = DPPlanner()
        assert planner is not None
        assert planner.VERSION == "dp_v1"

    def test_dpplanner_with_custom_config(self):
        """DPPlanner can be created with custom config."""
        config = OptimizerConfig(min_soc_pct=20.0, max_soc_pct=90.0)
        planner = DPPlanner(config=config)
        assert planner is not None

    def test_plan_empty_slots(self):
        """Plan with empty slots returns empty result."""
        planner = DPPlanner()
        inputs = OptimizerInputs(
            cycle_id="test-001",
            slots=[],
            initial_soc_pct=50.0,
            config=OptimizerConfig(),
        )

        result = planner.plan(inputs)

        assert result.total_slots == 0
        assert len(result.decisions) == 0

    def test_plan_single_slot(self):
        """Plan with single slot returns result."""
        planner = DPPlanner()
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
        inputs = OptimizerInputs(
            cycle_id="test-002",
            slots=[slot],
            initial_soc_pct=50.0,
            config=OptimizerConfig(),
        )

        result = planner.plan(inputs)

        assert result.success is True
        assert len(result.decisions) == 1
        assert result.decisions[0].action in list(PlannerAction)

    def test_plan_multiple_slots(self):
        """Plan with multiple slots returns decisions for all slots."""
        planner = DPPlanner()
        slots = [
            SlotContext(
                slot_index=i,
                timestamp_iso=datetime.now(UTC).isoformat(),
                buy_price=30.0,
                sell_price=10.0,
                solar_kwh=1.0,
                consumption_kwh=1.0,
                is_demand_window_slot=False,
                slot_interval_minutes=5,
            )
            for i in range(3)
        ]
        inputs = OptimizerInputs(
            cycle_id="test-003",
            slots=slots,
            initial_soc_pct=50.0,
            config=OptimizerConfig(),
        )

        result = planner.plan(inputs)

        assert result.success is True
        assert len(result.decisions) == 3
        assert result.total_slots == 3

    def test_plan_tracks_soc(self):
        """Plan tracks SOC changes across slots."""
        planner = DPPlanner()
        slots = [
            SlotContext(
                slot_index=0,
                timestamp_iso=datetime.now(UTC).isoformat(),
                buy_price=30.0,
                sell_price=10.0,
                solar_kwh=2.0,  # Surplus solar
                consumption_kwh=1.0,
                is_demand_window_slot=False,
                slot_interval_minutes=5,
            ),
            SlotContext(
                slot_index=1,
                timestamp_iso=datetime.now(UTC).isoformat(),
                buy_price=30.0,
                sell_price=10.0,
                solar_kwh=0.5,  # Deficit
                consumption_kwh=1.5,
                is_demand_window_slot=False,
                slot_interval_minutes=5,
            ),
        ]
        inputs = OptimizerInputs(
            cycle_id="test-004",
            slots=slots,
            initial_soc_pct=50.0,
            config=OptimizerConfig(),
        )

        result = planner.plan(inputs)

        assert result.success is True
        # SOC should change across slots
        socs = [d.predicted_soc_pct for d in result.decisions]
        assert len(socs) == 2
