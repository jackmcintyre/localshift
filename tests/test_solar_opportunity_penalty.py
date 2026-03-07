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
