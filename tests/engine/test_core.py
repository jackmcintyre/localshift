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


class TestNegativeFitAvoidanceContext:
    """Test _derive_negative_fit_avoidance_context method."""

    @staticmethod
    def _make_slot(idx: int, sell_price: float, solar_kwh: float = 0.0, consumption_kwh: float = 0.0) -> SlotContext:
        """Helper to create a slot for negative-FIT tests."""
        return SlotContext(
            slot_index=idx,
            timestamp_iso=f"2026-01-03T{(idx // 2):02d}:{(idx % 2) * 30:02d}:00",
            slot_interval_minutes=30,
            buy_price=0.10,
            sell_price=sell_price,
            solar_kwh=solar_kwh,
            consumption_kwh=consumption_kwh,
        )

    def test_no_negative_fit_window_returns_none(self):
        """Returns None when no negative-FIT window in horizon."""
        planner = DPPlanner()
        slots = [self._make_slot(i, sell_price=0.08) for i in range(10)]
        inputs = OptimizerInputs(
            cycle_id="test",
            initial_soc_pct=50.0,
            slots=slots,
            config=OptimizerConfig(),
        )
        ctx = planner._derive_negative_fit_avoidance_context(inputs)
        assert ctx is None

    def test_no_overflow_returns_none(self):
        """Returns None when no forecast overflow projected."""
        planner = DPPlanner()
        slots = [self._make_slot(i, sell_price=0.08 if i < 5 else -0.05) for i in range(10)]
        inputs = OptimizerInputs(
            cycle_id="test",
            initial_soc_pct=50.0,
            slots=slots,
            config=OptimizerConfig(),
        )
        ctx = planner._derive_negative_fit_avoidance_context(inputs)
        assert ctx is None

    def test_no_positive_slots_returns_none(self):
        """Returns None when no earlier positive-FIT slots."""
        planner = DPPlanner()
        slots = [self._make_slot(i, sell_price=0.08 if i >= 5 else 0.0) for i in range(10)]
        slots[5] = self._make_slot(5, sell_price=-0.05)
        inputs = OptimizerInputs(
            cycle_id="test",
            initial_soc_pct=50.0,
            slots=slots,
            config=OptimizerConfig(),
        )
        ctx = planner._derive_negative_fit_avoidance_context(inputs)
        assert ctx is None

    def test_computes_floor_when_conditions_met(self):
        """Computes correct temporary_floor_pct when all conditions met."""
        from custom_components.localshift.engine.types import NegativeFitAvoidanceContext
        planner = DPPlanner()
        config = OptimizerConfig(demand_window_target_soc_pct=80.0)
        slots = []
        for i in range(6):
            sell = 0.08 if i < 4 else -0.05
            solar = 2.0 if i == 0 else 0.0
            slots.append(self._make_slot(i, sell_price=sell, solar_kwh=solar))
        inputs = OptimizerInputs(
            cycle_id="test",
            initial_soc_pct=90.0,
            slots=slots,
            config=config,
        )
        ctx = planner._derive_negative_fit_avoidance_context(inputs)
        assert ctx is not None
        assert isinstance(ctx, NegativeFitAvoidanceContext)
        assert 0 < ctx.allowed_headroom_pct <= 20.0
        assert abs(ctx.temporary_floor_pct - (80.0 - ctx.allowed_headroom_pct)) < 0.01
