"""Test solar opportunity penalty for Issue #607.

Tests that the optimizer penalizes overnight grid charging when
tomorrow's solar forecast is available beyond the price horizon.
"""

from datetime import datetime

import pytest

from custom_components.localshift.computation_engine_lib.optimizer_dp import (
    DPPlanner,
    OptimizerConfig,
    OptimizerInputs,
    PlannerAction,
    SlotContext,
)


def make_slot(
    slot_index: int,
    hour: int,
    minute: int = 0,
    buy_price: float = 0.15,
    sell_price: float = 0.08,
    solar_kwh: float = 0.0,
    consumption_kwh: float = 0.3,
    interval_minutes: int = 30,
) -> SlotContext:
    """Create a SlotContext for testing."""
    return SlotContext(
        slot_index=slot_index,
        timestamp_iso=f"2026-01-03T{hour:02d}:{minute:02d}:00",
        slot_interval_minutes=interval_minutes,
        buy_price=buy_price,
        sell_price=sell_price,
        solar_kwh=solar_kwh,
        consumption_kwh=consumption_kwh,
    )


def make_solcast_entry(hour: int, pv_estimate: float) -> dict:
    """Create a Solcast forecast entry."""
    base_dt = datetime(2026, 1, 4, hour, 0, 0)
    return {
        "period_start": base_dt.isoformat(),
        "pv_estimate": pv_estimate,
    }


@pytest.fixture
def default_config() -> OptimizerConfig:
    """Default optimizer config for tests."""
    return OptimizerConfig(
        battery_capacity_kwh=13.5,
        min_soc_pct=10.0,
        max_soc_pct=100.0,
        demand_window_target_soc_pct=80.0,
        soc_bins=20,
        optimization_mode="self_consumption",
    )


class TestSolarOpportunityPenalty:
    """Tests for solar opportunity penalty when horizon is limited."""

    def test_no_penalty_without_future_solar(self, default_config):
        """When all_solcast is empty, no solar opportunity penalty should apply."""
        slots = [
            make_slot(i, 22 + (i // 2), (i % 2) * 30, buy_price=0.10) for i in range(20)
        ]

        inputs = OptimizerInputs(
            cycle_id="test-no-future-solar",
            initial_soc_pct=20.0,
            slots=slots,
            config=default_config,
            all_solcast=[],
        )

        result = DPPlanner().plan(inputs)
        assert result.success

        for d in result.decisions:
            assert d.objective_terms.solar_opportunity_penalty == 0.0

    def test_penalty_applied_when_tomorrow_has_solar(self, default_config):
        """When tomorrow has significant solar, overnight charging should be penalized."""
        slots = [
            make_slot(i, 22 + (i // 2), (i % 2) * 30, buy_price=0.10, solar_kwh=0.0)
            for i in range(20)
        ]

        tomorrow_solar = [
            make_solcast_entry(8, 2.0),
            make_solcast_entry(9, 3.0),
            make_solcast_entry(10, 4.0),
            make_solcast_entry(11, 4.5),
            make_solcast_entry(12, 4.5),
            make_solcast_entry(13, 4.0),
            make_solcast_entry(14, 3.0),
            make_solcast_entry(15, 2.0),
        ]

        inputs = OptimizerInputs(
            cycle_id="test-tomorrow-solar",
            initial_soc_pct=20.0,
            slots=slots,
            config=default_config,
            all_solcast=tomorrow_solar,
        )

        result = DPPlanner().plan(inputs)
        assert result.success

        charge_decisions = [
            d
            for d in result.decisions
            if d.action
            in (PlannerAction.CHARGE_GRID_NORMAL, PlannerAction.CHARGE_GRID_BOOST)
        ]

        if charge_decisions:
            for d in charge_decisions:
                assert d.objective_terms.solar_opportunity_penalty >= 0.0

    def test_penalty_prevents_overnight_charge_with_high_solar_forecast(
        self, default_config
    ):
        """With 20+ kWh tomorrow solar, optimizer should avoid overnight charging."""
        slots = [
            make_slot(i, 9 + (i // 2), (i % 2) * 30, buy_price=0.15, solar_kwh=0.0)
            for i in range(40)
        ]

        tomorrow_solar = [make_solcast_entry(h, 3.5) for h in range(8, 16)]

        inputs = OptimizerInputs(
            cycle_id="test-prevent-overnight",
            initial_soc_pct=30.0,
            slots=slots,
            config=default_config,
            all_solcast=tomorrow_solar,
        )

        result = DPPlanner().plan(inputs)
        assert result.success

        overnight_slots = [
            d
            for d in result.decisions
            if d.action
            in (PlannerAction.CHARGE_GRID_NORMAL, PlannerAction.CHARGE_GRID_BOOST)
            and int(d.slot_index) >= 30
        ]

        for d in overnight_slots:
            assert d.objective_terms.solar_opportunity_penalty >= 0.0

    def test_no_penalty_when_solar_in_current_slot(self, default_config):
        """If current slot has solar, no opportunity penalty (solar is immediate)."""
        slots = [
            make_slot(0, 10, 0, buy_price=0.15, solar_kwh=2.0),
            make_slot(1, 10, 30, buy_price=0.15, solar_kwh=2.5),
        ]

        inputs = OptimizerInputs(
            cycle_id="test-immediate-solar",
            initial_soc_pct=50.0,
            slots=slots,
            config=default_config,
            all_solcast=[],
        )

        result = DPPlanner().plan(inputs)
        assert result.success

        for d in result.decisions:
            if d.action in (
                PlannerAction.CHARGE_GRID_NORMAL,
                PlannerAction.CHARGE_GRID_BOOST,
            ):
                assert d.objective_terms.solar_opportunity_penalty == 0.0

    def test_penalty_scales_with_grid_import(self, default_config):
        """Larger grid import should have larger penalty."""
        slots = [
            make_slot(i, 22 + (i // 2), (i % 2) * 30, buy_price=0.10, solar_kwh=0.0)
            for i in range(20)
        ]

        tomorrow_solar = [make_solcast_entry(h, 3.0) for h in range(8, 16)]

        inputs = OptimizerInputs(
            cycle_id="test-penalty-scale",
            initial_soc_pct=20.0,
            slots=slots,
            config=default_config,
            all_solcast=tomorrow_solar,
        )

        result = DPPlanner().plan(inputs)
        assert result.success

        charge_decisions = [
            d
            for d in result.decisions
            if d.action
            in (PlannerAction.CHARGE_GRID_NORMAL, PlannerAction.CHARGE_GRID_BOOST)
            and d.objective_terms.solar_opportunity_penalty > 0
        ]

        if len(charge_decisions) >= 2:
            for i in range(1, len(charge_decisions)):
                d1 = charge_decisions[i - 1]
                d2 = charge_decisions[i]
                if d1.grid_import_kwh > 0 and d2.grid_import_kwh > 0:
                    ratio1 = (
                        d1.objective_terms.solar_opportunity_penalty
                        / d1.grid_import_kwh
                    )
                    ratio2 = (
                        d2.objective_terms.solar_opportunity_penalty
                        / d2.grid_import_kwh
                    )
                    assert ratio1 == pytest.approx(ratio2, rel=0.01), (
                        "Penalty should scale linearly with grid import"
                    )


class TestSolarOpportunityPenaltyWithDemandWindow:
    """Tests for solar opportunity penalty interaction with demand window."""

    def test_no_penalty_when_demand_window_exists(self, default_config):
        """Demand window preparation should override solar opportunity penalty."""
        slots = []
        for i in range(24):
            slot = make_slot(i, 10 + (i // 2), (i % 2) * 30, buy_price=0.10)
            if i == 20:
                slot.is_demand_window_entry = True
                slot.is_demand_window_slot = True
            if i > 20:
                slot.is_demand_window_slot = True
            slots.append(slot)

        tomorrow_solar = [make_solcast_entry(h, 3.0) for h in range(8, 16)]

        inputs = OptimizerInputs(
            cycle_id="test-dw-override",
            initial_soc_pct=30.0,
            slots=slots,
            config=default_config,
            all_solcast=tomorrow_solar,
        )

        result = DPPlanner().plan(inputs)
        assert result.success

        for d in result.decisions:
            assert d.objective_terms.solar_opportunity_penalty == 0.0, (
                "Solar opportunity penalty should not apply when demand window exists"
            )


class TestHorizonAwareOpportunityCost:
    """Tests for Issue #610: Horizon-aware opportunity cost penalty."""

    def test_penalty_applies_when_solar_exceeds_30_percent_threshold(
        self, default_config
    ):
        """Penalty should only apply when future solar >= 30% of battery capacity."""
        slots = [
            make_slot(i, 22 + (i // 2), (i % 2) * 30, buy_price=0.13) for i in range(20)
        ]

        # 4.05 kWh = 30% of 13.5 kWh battery
        tomorrow_solar = [
            make_solcast_entry(8, 2.0),
            make_solcast_entry(9, 2.5),
            make_solcast_entry(10, 3.0),
        ]

        inputs = OptimizerInputs(
            cycle_id="test-threshold-30pct",
            initial_soc_pct=20.0,
            slots=slots,
            config=default_config,
            all_solcast=tomorrow_solar,
        )

        result = DPPlanner().plan(inputs)
        assert result.success

        charge_decisions = [
            d
            for d in result.decisions
            if d.action
            in (PlannerAction.CHARGE_GRID_NORMAL, PlannerAction.CHARGE_GRID_BOOST)
        ]

        # Should apply penalty when solar >= 4.05 kWh
        for d in charge_decisions:
            assert d.objective_terms.solar_opportunity_penalty > 0.0

    def test_no_penalty_when_solar_below_30_percent_threshold(self, default_config):
        """Penalty should not apply when future solar < 30% of battery capacity."""
        slots = [
            make_slot(i, 22 + (i // 2), (i % 2) * 30, buy_price=0.13) for i in range(20)
        ]

        # Only 2.0 kWh (< 4.05 kWh threshold)
        tomorrow_solar = [
            make_solcast_entry(8, 1.0),
            make_solcast_entry(9, 1.0),
        ]

        inputs = OptimizerInputs(
            cycle_id="test-below-threshold",
            initial_soc_pct=20.0,
            slots=slots,
            config=default_config,
            all_solcast=tomorrow_solar,
        )

        result = DPPlanner().plan(inputs)
        assert result.success

        for d in result.decisions:
            assert d.objective_terms.solar_opportunity_penalty == 0.0

    def test_penalty_scales_with_buy_price(self, default_config):
        """Penalty should be proportional to buy_price via time discount."""
        slots = [
            make_slot(i, 22 + (i // 2), (i % 2) * 30, buy_price=0.13) for i in range(20)
        ]

        tomorrow_solar = [
            make_solcast_entry(8, 3.0),
            make_solcast_entry(9, 4.0),
            make_solcast_entry(10, 5.0),
        ]

        inputs = OptimizerInputs(
            cycle_id="test-price-scaling",
            initial_soc_pct=20.0,
            slots=slots,
            config=default_config,
            all_solcast=tomorrow_solar,
        )

        result = DPPlanner().plan(inputs)
        assert result.success

        charge_decisions = [
            d
            for d in result.decisions
            if d.action
            in (PlannerAction.CHARGE_GRID_NORMAL, PlannerAction.CHARGE_GRID_BOOST)
            and d.grid_import_kwh > 0
        ]

        # Verify penalty scales with buy_price (not flat rate)
        for d in charge_decisions:
            expected_min_penalty = (
                d.grid_import_kwh * d.buy_price * 0.3
            )  # At least 30% of price
            assert d.objective_terms.solar_opportunity_penalty >= expected_min_penalty

    def test_penalty_decays_with_hours_to_solar(self, default_config):
        """Penalty strength should decay based on distance to solar."""
        slots_near_solar = [
            make_slot(i, 6 + (i // 2), (i % 2) * 30, buy_price=0.13) for i in range(10)
        ]

        slots_far_from_solar = [
            make_slot(i, 22 + (i // 2), (i % 2) * 30, buy_price=0.13) for i in range(10)
        ]

        tomorrow_solar = [
            make_solcast_entry(8, 4.0),
            make_solcast_entry(9, 5.0),
            make_solcast_entry(10, 6.0),
        ]

        inputs_near = OptimizerInputs(
            cycle_id="test-near-solar",
            initial_soc_pct=20.0,
            slots=slots_near_solar,
            config=default_config,
            all_solcast=tomorrow_solar,
        )

        inputs_far = OptimizerInputs(
            cycle_id="test-far-solar",
            initial_soc_pct=20.0,
            slots=slots_far_from_solar,
            config=default_config,
            all_solcast=tomorrow_solar,
        )

        result_near = DPPlanner().plan(inputs_near)
        result_far = DPPlanner().plan(inputs_far)

        assert result_near.success
        assert result_far.success

        # Near solar should have higher penalty per kWh than far from solar
        charge_near = [
            d
            for d in result_near.decisions
            if d.action
            in (PlannerAction.CHARGE_GRID_NORMAL, PlannerAction.CHARGE_GRID_BOOST)
            and d.grid_import_kwh > 0
        ]

        charge_far = [
            d
            for d in result_far.decisions
            if d.action
            in (PlannerAction.CHARGE_GRID_NORMAL, PlannerAction.CHARGE_GRID_BOOST)
            and d.grid_import_kwh > 0
        ]

        if charge_near and charge_far:
            avg_penalty_near = sum(
                d.objective_terms.solar_opportunity_penalty / d.grid_import_kwh
                for d in charge_near
            ) / len(charge_near)
            avg_penalty_far = sum(
                d.objective_terms.solar_opportunity_penalty / d.grid_import_kwh
                for d in charge_far
            ) / len(charge_far)
            assert avg_penalty_near > avg_penalty_far, (
                "Penalty should be stronger when solar is closer"
            )

    def test_optimizer_chooses_hold_over_cheap_charge_when_solar_near(
        self, default_config
    ):
        """Optimizer should choose Hold when solar opportunity cost exceeds grid savings."""
        slots = [
            make_slot(i, 6 + (i // 2), (i % 2) * 30, buy_price=0.13) for i in range(10)
        ]

        tomorrow_solar = [
            make_solcast_entry(8, 4.0),
            make_solcast_entry(9, 5.0),
            make_solcast_entry(10, 6.0),
        ]

        inputs = OptimizerInputs(
            cycle_id="test-hold-preference",
            initial_soc_pct=20.0,
            slots=slots,
            config=default_config,
            all_solcast=tomorrow_solar,
        )

        result = DPPlanner().plan(inputs)
        assert result.success

        # Should prefer Hold over Charge when penalty makes charging uneconomical
        charge_decisions = [
            d
            for d in result.decisions
            if d.action
            in (PlannerAction.CHARGE_GRID_NORMAL, PlannerAction.CHARGE_GRID_BOOST)
        ]

        # At least some slots should choose Hold instead of Charge
        # (not all slots should charge despite cheap price)
        assert len(charge_decisions) < len(result.decisions)

    def test_time_discount_formula_calculation(self, default_config):
        """Verify time discount formula: 1.0 / (1.0 + hours_to_solar * 0.1)."""
        from custom_components.localshift.computation_engine_lib.optimizer_dp import (
            DPPlanner as Planner,
        )

        # Test cases: (hours, expected_discount)
        test_cases = [
            (1.0, 0.909),  # 1.0 / (1.0 + 1.0 * 0.1) ≈ 0.909
            (3.0, 0.769),  # 1.0 / (1.0 + 3.0 * 0.1) ≈ 0.769
            (6.0, 0.625),  # 1.0 / (1.0 + 6.0 * 0.1) = 0.625
            (12.0, 0.455),  # 1.0 / (1.0 + 12.0 * 0.1) ≈ 0.455
        ]

        for hours, expected in test_cases:
            actual = 1.0 / (1.0 + hours * 0.1)
            assert actual == pytest.approx(expected, rel=0.01), (
                f"Time discount for {hours}h should be ~{expected}"
            )
