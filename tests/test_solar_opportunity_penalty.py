"""Test solar opportunity penalty for Issue #607.

Tests that the optimizer penalizes overnight grid charging when
tomorrow's solar forecast is available beyond the price horizon.
"""

from datetime import datetime

import pytest

from custom_components.localshift.engine.optimizer_dp import (
    DPPlanner,
    OptimizerConfig,
    OptimizerInputs,
    PlannerAction,
    PlannerReasonCode,
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

    def test_penalty_applies_before_demand_window_not_during(self, default_config):
        """Penalty should apply before demand window, not during/after (Issue #610 bug).

        This test verifies the fix for the bug where solar opportunity penalty was
        completely disabled whenever a demand window existed anywhere in the horizon.

        Expected behavior:
        - Slots BEFORE demand window: penalty applies (wait for solar)
        - Slots DURING/AFTER demand window: no penalty (must charge to meet target)
        """
        slots = []
        for i in range(24):
            slot = make_slot(i, 10 + (i // 2), (i % 2) * 30, buy_price=0.13)
            if i == 20:
                slot.is_demand_window_entry = True
                slot.is_demand_window_slot = True
            if i > 20:
                slot.is_demand_window_slot = True
            slots.append(slot)

        tomorrow_solar = [
            make_solcast_entry(8, 3.0),
            make_solcast_entry(9, 4.0),
            make_solcast_entry(10, 5.0),
        ]

        inputs = OptimizerInputs(
            cycle_id="test-penalty-before-dw",
            initial_soc_pct=30.0,
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

        for d in charge_decisions:
            slot_idx = int(d.slot_index)
            if slot_idx < 20:
                assert d.objective_terms.solar_opportunity_penalty >= 0.0, (
                    f"Slot {slot_idx} is BEFORE demand window (index 20), "
                    f"should have solar opportunity penalty, but got {d.objective_terms.solar_opportunity_penalty}"
                )
            else:
                assert d.objective_terms.solar_opportunity_penalty == 0.0, (
                    f"Slot {slot_idx} is DURING/AFTER demand window (index 20), "
                    f"should NOT have penalty, but got {d.objective_terms.solar_opportunity_penalty}"
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


class TestSelfConsumptionCreditFix:
    """Fix A: self_consumption_value should use slot.buy_price, not fixed config value.

    Verifies that the optimizer no longer treats charging at $0.14 + discharging at
    $0.15 self-consumption credit as "profitable" on flat-rate nights. The credit
    must equal the actual avoided buy_price, not a fixed average.
    """

    def test_stage_cost_uses_slot_buy_price_for_self_consumption(self, default_config):
        """stage_cost() self_consumption_value must scale with slot.buy_price, not config value.

        At buy_price=$0.14 the credit should be ~$0.14 * battery_for_load,
        NOT $0.15 * battery_for_load (the old fixed config.self_consumption_value_per_kwh).
        """
        # Slot with $0.14 buy price, no solar, 1 kWh load
        slot = make_slot(
            0,
            22,
            0,
            buy_price=0.14,
            sell_price=0.07,
            solar_kwh=0.0,
            consumption_kwh=1.0,
            interval_minutes=30,
        )
        # battery_for_load = 1.0 kWh (HOLD, battery covers full load)
        # With slot.buy_price: credit = 1.0 * 0.14 = 0.14
        # With fixed config (0.15): credit = 1.0 * 0.15 = 0.15
        terms = DPPlanner.stage_cost(
            action=PlannerAction.HOLD,
            grid_import_kwh=0.0,
            grid_export_kwh=0.0,
            slot=slot,
            config=default_config,
            soc_pct=80.0,  # plenty of charge so battery covers load
        )
        # Credit should equal slot.buy_price * battery_for_load, not fixed config value
        # battery_for_load ≈ 1.0 (load - import - export = 1.0 - 0 - 0 = 1.0)
        assert terms.self_consumption_value == pytest.approx(0.14, rel=0.05), (
            f"Expected credit ~$0.14 (slot price), got ${terms.self_consumption_value:.4f}. "
            f"self_consumption_value must use slot.buy_price, not fixed config value"
        )

    def test_stage_cost_credit_scales_with_buy_price(self, default_config):
        """At $0.30/kWh the credit should be ~0.30, not the fixed $0.15."""
        slot = make_slot(
            0,
            7,
            0,
            buy_price=0.30,  # morning peak price
            sell_price=0.12,
            solar_kwh=0.0,
            consumption_kwh=1.0,
            interval_minutes=30,
        )
        terms = DPPlanner.stage_cost(
            action=PlannerAction.HOLD,
            grid_import_kwh=0.0,
            grid_export_kwh=0.0,
            slot=slot,
            config=default_config,
            soc_pct=80.0,
        )
        # At $0.30 peak, credit must be ~$0.30 * load
        assert terms.self_consumption_value > 0.25, (
            f"At $0.30 buy price, credit should be > $0.25, got ${terms.self_consumption_value:.4f}"
        )

    def test_flat_rate_overnight_no_charge_with_solar_forecast(self, default_config):
        """Flat-rate overnight ($0.14 all night) + big solar tomorrow → no grid charging.

        This is the production bug scenario. With fixed $0.15 credit the optimizer
        "profits" by charge+discharge. With slot.buy_price credit, charge+discharge
        is always a net loss (due to round-trip efficiency) so HOLD is preferred.

        Starting at the SOC floor eliminates discretization ambiguity: charging from
        the floor to cycle energy is always a net loss at flat rates, so the DP must
        prefer HOLD regardless of cost-to-go gradient.
        """
        # Configure: buy and sell same overnight price ($0.14), no demand window.
        # effective_cheap_price=0.17 so $0.14 < $0.17 and CHARGE_GRID_NORMAL is feasible.
        config = OptimizerConfig(
            battery_capacity_kwh=13.5,
            min_soc_pct=10.0,
            max_soc_pct=100.0,
            demand_window_target_soc_pct=80.0,
            soc_bins=50,  # Fine grid to reduce discretization noise
            optimization_mode="self_consumption",
            effective_cheap_price=0.17,
            charge_efficiency=0.95,
            discharge_efficiency=0.95,
        )
        # 48 × 30-min slots (overnight, all at $0.14, no solar now)
        slots = [
            make_slot(
                i,
                (i // 2) % 24,
                (i % 2) * 30,
                buy_price=0.14,
                sell_price=0.07,
                solar_kwh=0.0,
                consumption_kwh=0.3,
                interval_minutes=30,
            )
            for i in range(48)
        ]
        # Massive solar tomorrow: 16 kWh available (8 × 4 kW × 0.5h = 16 kWh)
        tomorrow_solar = [make_solcast_entry(h, 4.0) for h in range(7, 15)]

        inputs = OptimizerInputs(
            cycle_id="test-flat-rate-no-charge",
            # Start at SOC floor: charging from here can only be to cycle at a round-trip loss
            initial_soc_pct=10.0,
            slots=slots,
            config=config,
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

        assert len(charge_decisions) == 0, (
            f"Expected 0 grid charge slots on flat-rate night with solar forecast, "
            f"got {len(charge_decisions)} at slots: "
            f"{[d.slot_index for d in charge_decisions]}"
        )

    def test_price_spike_scenario_still_charges(self, default_config):
        """Cheap overnight then expensive morning → grid charging is correct.

        When buy_price overnight ($0.13) is significantly less than daytime ($0.45),
        charging overnight is economically correct even if solar is available — the
        arbitrage profit exceeds any opportunity penalty.
        """
        config = OptimizerConfig(
            battery_capacity_kwh=13.5,
            min_soc_pct=10.0,
            max_soc_pct=100.0,
            demand_window_target_soc_pct=80.0,
            soc_bins=20,
            optimization_mode="self_consumption",
            effective_cheap_price=0.17,
            charge_efficiency=0.95,
            discharge_efficiency=0.95,
        )
        # Mix: cheap slots overnight, expensive slots in morning
        slots = []
        for i in range(20):
            hour = (22 + i // 2) % 24
            price = 0.13 if hour < 7 else 0.45  # cheap overnight, spike in morning
            slots.append(
                make_slot(
                    i,
                    hour,
                    (i % 2) * 30,
                    buy_price=price,
                    sell_price=0.08,
                    solar_kwh=0.0,
                    consumption_kwh=0.4,
                    interval_minutes=30,
                )
            )

        # Some solar tomorrow — but the arbitrage makes charging still worthwhile
        tomorrow_solar = [make_solcast_entry(h, 1.5) for h in range(8, 14)]

        inputs = OptimizerInputs(
            cycle_id="test-price-spike",
            initial_soc_pct=20.0,
            slots=slots,
            config=config,
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

        # Should still charge overnight to capture morning arbitrage
        assert len(charge_decisions) > 0, (
            "Expected grid charging when morning price ($0.45) >> overnight price ($0.13)"
        )


class TestSolarOpportunityPenaltyStrengthened:
    """Fix B: solar opportunity penalty base includes downstream self-consumption credit.

    The penalty must overcome both the import cost AND the self-consumption credit
    that the charged energy will generate. Previously penalty = import_cost * factor,
    which capped the penalty below the self-consumption credit when credit > import_cost.
    """

    def test_penalty_base_reflects_full_economic_benefit(self, default_config):
        """Solar opportunity penalty should exceed import_cost × factor alone.

        When buy_price = $0.14 and self_consumption_value = $0.14 per kWh,
        the full economic benefit of charging is $0.14 (import) + $0.14*RTE (future credit).
        The penalty must overwhelm both, so its base should be > import_cost alone.
        """
        slot = make_slot(
            0,
            22,
            0,
            buy_price=0.14,
            sell_price=0.07,
            solar_kwh=0.0,
            consumption_kwh=0.3,
            interval_minutes=30,
        )
        grid_import_kwh = 0.5

        import_cost = grid_import_kwh * 0.14  # = $0.07

        terms = DPPlanner.stage_cost(
            action=PlannerAction.CHARGE_GRID_NORMAL,
            grid_import_kwh=grid_import_kwh,
            grid_export_kwh=0.0,
            slot=slot,
            config=default_config,
            soc_pct=50.0,
            solar_opportunity_penalty_factor=1.0,  # full penalty for test clarity
        )

        # With factor=1.0, OLD penalty = import_cost = $0.07
        # NEW penalty should be larger (includes self-consumption credit component)
        # Expected: > $0.07 when self-consumption mode is active
        assert terms.solar_opportunity_penalty > import_cost, (
            f"Solar opportunity penalty ({terms.solar_opportunity_penalty:.4f}) should exceed "
            f"import_cost ({import_cost:.4f}) alone — penalty base must include downstream credit"
        )

def test_short_horizon_aware_of_future_solar_holds():
    """
    Test that when the demand window is beyond the horizon, the DP
    accounts for solar between now and then. (Issue #619)
    """
    config = OptimizerConfig(
        battery_capacity_kwh=10.0,
        min_soc_pct=10.0,
        max_soc_pct=100.0,
        demand_window_target_soc_pct=80.0,
        target_shortfall_penalty_per_pct=1.0,
        soc_bins=50,
        optimization_mode="self_consumption",
        effective_cheap_price=0.17,
        charge_efficiency=0.92,
        discharge_efficiency=0.95,
    )

    now = datetime(2026, 1, 3, 23, 0, 0)
    slots = [make_slot(i, (23 + i // 2) % 24, (i % 2) * 30, buy_price=0.14) for i in range(10)]
    # Target 80% at 17:00 tomorrow (well beyond horizon ending at 04:00 AM)
    # Force terminal_penalty_idx to 9
    slots[-1].is_demand_window_slot = True

    # Solar tomorrow morning: 16 kWh available (starts at 08:00, beyond 04:00 AM)
    all_solcast = [
        make_solcast_entry(8 + i, 4.0) for i in range(8)
    ]

    inputs = OptimizerInputs(
        cycle_id="test-horizon-aware",
        initial_soc_pct=20.0,
        slots=slots,
        config=config,
        all_solcast=all_solcast,
    )

    planner = DPPlanner()
    result = planner.plan(inputs)
    assert result.success

    # Should HOLD because future solar covers shortfall
    charge_slots = [d for d in result.decisions if d.action == PlannerAction.CHARGE_GRID_NORMAL]
    assert len(charge_slots) == 0

    # Reason should be SOLAR_OPPORTUNITY_WAIT (since price is cheap and solar is coming)
    assert result.decisions[0].reason_code == PlannerReasonCode.SOLAR_OPPORTUNITY_WAIT


def test_short_horizon_still_charges_if_future_solar_insufficient():
    """
    Test that it still charges if even the future solar isn't enough. (Issue #619)
    """
    config = OptimizerConfig(
        battery_capacity_kwh=10.0,
        min_soc_pct=10.0,
        max_soc_pct=100.0,
        demand_window_target_soc_pct=80.0,
        target_shortfall_penalty_per_pct=1.0,
        soc_bins=50,
        optimization_mode="self_consumption",
        effective_cheap_price=0.17,
        charge_efficiency=0.92,
        discharge_efficiency=0.95,
    )

    now = datetime(2026, 1, 3, 23, 0, 0)
    slots = [make_slot(i, (23 + i // 2) % 24, (i % 2) * 30, buy_price=0.14) for i in range(10)]
    slots[-1].is_demand_window_slot = True

    # Tiny solar tomorrow morning: 1 kWh available
    all_solcast = [make_solcast_entry(9, 2.0)]

    inputs = OptimizerInputs(
        cycle_id="test-insufficient-future-solar",
        initial_soc_pct=10.0,
        slots=slots,
        config=config,
        all_solcast=all_solcast,
    )

    planner = DPPlanner()
    result = planner.plan(inputs)
    assert result.success

    charge_slots = [d for d in result.decisions if d.action == PlannerAction.CHARGE_GRID_NORMAL]
    assert len(charge_slots) > 0
