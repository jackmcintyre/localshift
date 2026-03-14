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
    """Test _derive_negative_fit_avoidance_context method (recoverability model)."""

    @staticmethod
    def _make_slot(
        idx: int,
        sell_price: float,
        solar_kwh: float = 0.0,
        consumption_kwh: float = 0.0,
        is_demand_window: bool = False,
    ) -> SlotContext:
        """Helper to create a slot for negative-FIT tests."""
        return SlotContext(
            slot_index=idx,
            timestamp_iso=f"2026-01-03T{(idx // 2):02d}:{(idx % 2) * 30:02d}:00",
            slot_interval_minutes=30,
            buy_price=0.10,
            sell_price=sell_price,
            solar_kwh=solar_kwh,
            consumption_kwh=consumption_kwh,
            is_demand_window_slot=is_demand_window,
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

    def test_no_positive_slots_before_risk_returns_none(self):
        """Returns None when no positive-FIT slots exist before risk window."""
        planner = DPPlanner()
        slots = [
            self._make_slot(i, sell_price=0.0 if i < 5 else -0.05) for i in range(10)
        ]
        inputs = OptimizerInputs(
            cycle_id="test",
            initial_soc_pct=50.0,
            slots=slots,
            config=OptimizerConfig(),
        )
        ctx = planner._derive_negative_fit_avoidance_context(inputs)
        assert ctx is None

    def test_no_recovery_solar_returns_none(self):
        """Returns None when insufficient solar to recover to target."""
        planner = DPPlanner()
        config = OptimizerConfig(demand_window_target_soc_pct=80.0)
        slots = [
            self._make_slot(i, sell_price=0.08 if i < 5 else -0.05) for i in range(10)
        ]
        inputs = OptimizerInputs(
            cycle_id="test",
            initial_soc_pct=50.0,
            slots=slots,
            config=config,
        )
        ctx = planner._derive_negative_fit_avoidance_context(inputs)
        assert ctx is None

    def test_ha_style_case_with_solar_during_bad_fit_creates_context(self):
        """HA-style: solar arrives DURING bad-FIT window, still creates context."""
        from custom_components.localshift.engine.types import (
            NegativeFitAvoidanceContext,
        )

        planner = DPPlanner()
        config = OptimizerConfig(
            demand_window_target_soc_pct=100.0,
            battery_capacity_kwh=13.5,
        )
        slots = []
        for i in range(24):
            if i < 12:
                sell = 0.07
                solar = 0.0
            else:
                sell = -0.02
                solar = 2.5
            slots.append(self._make_slot(i, sell_price=sell, solar_kwh=solar))
        inputs = OptimizerInputs(
            cycle_id="test",
            initial_soc_pct=58.0,
            slots=slots,
            config=config,
        )
        ctx = planner._derive_negative_fit_avoidance_context(inputs)
        assert ctx is not None
        assert isinstance(ctx, NegativeFitAvoidanceContext)
        assert ctx.risk_window_start_idx == 12
        assert ctx.risk_window_end_idx == 23
        assert ctx.required_headroom_kwh > 0

    def test_recoverability_floor_allows_below_target(self):
        """Recoverability floor can be below target when recovery is feasible."""
        from custom_components.localshift.engine.types import (
            NegativeFitAvoidanceContext,
        )

        planner = DPPlanner()
        config = OptimizerConfig(
            demand_window_target_soc_pct=100.0,
            min_soc_pct=10.0,
            battery_capacity_kwh=13.5,
        )
        slots = []
        for i in range(20):
            if i < 10:
                sell = 0.07
                solar = 0.0
            else:
                sell = -0.02
                solar = 2.0
            slots.append(self._make_slot(i, sell_price=sell, solar_kwh=solar))
        inputs = OptimizerInputs(
            cycle_id="test",
            initial_soc_pct=58.0,
            slots=slots,
            config=config,
        )
        ctx = planner._derive_negative_fit_avoidance_context(inputs)
        assert ctx is not None
        floor_pct = planner._compute_recoverability_floor_pct(
            current_soc_pct=58.0,
            slot_idx=5,
            context=ctx,
            config=config,
            inputs=inputs,
        )
        assert floor_pct < config.demand_window_target_soc_pct
        assert floor_pct >= config.min_soc_pct

    def test_recoverability_floor_stays_high_with_weak_solar(self):
        """Recoverability floor stays near target when recovery solar is weak."""
        from custom_components.localshift.engine.types import (
            NegativeFitAvoidanceContext,
        )

        planner = DPPlanner()
        config = OptimizerConfig(
            demand_window_target_soc_pct=100.0,
            min_soc_pct=10.0,
            battery_capacity_kwh=13.5,
        )
        slots = []
        for i in range(20):
            if i < 10:
                sell = 0.07
                solar = 0.0
            else:
                sell = -0.02
                solar = 0.1
            slots.append(self._make_slot(i, sell_price=sell, solar_kwh=solar))
        inputs = OptimizerInputs(
            cycle_id="test",
            initial_soc_pct=95.0,
            slots=slots,
            config=config,
        )
        ctx = planner._derive_negative_fit_avoidance_context(inputs)
        assert ctx is not None
        floor_pct = planner._compute_recoverability_floor_pct(
            current_soc_pct=95.0,
            slot_idx=5,
            context=ctx,
            config=config,
            inputs=inputs,
        )
        assert floor_pct >= 80.0

    def test_context_stops_when_headroom_sufficient(self):
        """Context reflects when enough headroom already exists."""
        planner = DPPlanner()
        config = OptimizerConfig(
            demand_window_target_soc_pct=100.0,
            battery_capacity_kwh=13.5,
        )
        slots = []
        for i in range(20):
            if i < 10:
                sell = 0.07
                solar = 0.0
            else:
                sell = -0.02
                solar = 0.5
            slots.append(self._make_slot(i, sell_price=sell, solar_kwh=solar))
        inputs = OptimizerInputs(
            cycle_id="test",
            initial_soc_pct=20.0,
            slots=slots,
            config=config,
        )
        ctx = planner._derive_negative_fit_avoidance_context(inputs)
        assert ctx is None or ctx.required_headroom_kwh <= 0.5
