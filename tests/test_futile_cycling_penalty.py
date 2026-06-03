"""Tests for futile cycling penalty and SC discount (Issue #638).

Issue #638: Wasteful overnight Grid→Battery→House cycling.
At midnight SOC hits 10%, optimizer charges 3.3 kWh from grid at $0.14/kWh,
house load drains it back to 10% by 7am. Energy never reaches DW or solar.
Effective cost ~$0.21/kWh vs $0.14/kWh direct grid draw.

Two fixes:
  Fix A: futile_cycling_penalty — penalises grid charging when energy will be
         consumed by house load before reaching a useful period (solar surplus or DW).
  Fix B: SC discount — reduces self_consumption_value when SOC is near the floor
         and no solar is available (energy is likely grid-charged, not solar-charged).
"""

from datetime import datetime

import pytest

from custom_components.localshift.engine.cost import stage_cost
from custom_components.localshift.engine.penalties import (
    get_futile_cycling_penalty_factor,
)
from custom_components.localshift.engine.optimizer_dp import (
    DPPlanner,
    ObjectiveTerms,
    OptimizerConfig,
    OptimizerInputs,
    PlannerAction,
    SlotContext,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_slot(
    slot_index: int,
    hour: int,
    minute: int = 0,
    buy_price: float = 0.14,
    sell_price: float = 0.06,
    solar_kwh: float = 0.0,
    consumption_kwh: float = 0.25,
    is_demand_window_slot: bool = False,
    is_demand_window_entry: bool = False,
    interval_minutes: int = 30,
) -> SlotContext:
    """Create a SlotContext for overnight / morning scenarios."""
    return SlotContext(
        slot_index=slot_index,
        timestamp_iso=f"2026-03-11T{hour:02d}:{minute:02d}:00",
        slot_interval_minutes=interval_minutes,
        buy_price=buy_price,
        sell_price=sell_price,
        solar_kwh=solar_kwh,
        consumption_kwh=consumption_kwh,
        is_demand_window_entry=is_demand_window_entry,
        is_demand_window_slot=is_demand_window_slot,
    )


def make_overnight_slots(
    n_overnight: int = 12,
    n_morning_solar: int = 4,
    buy_price: float = 0.14,
    solar_kwh_per_slot: float = 1.5,
    consumption_kwh: float = 0.25,
) -> list[SlotContext]:
    """Build a typical overnight + morning sequence.

    n_overnight slots at 30-min intervals from midnight, no solar.
    n_morning_solar slots after with solar surplus (solar_kwh > consumption_kwh).
    """
    slots = []
    for i in range(n_overnight):
        hour = (i // 2) % 24
        minute = (i % 2) * 30
        slots.append(
            make_slot(
                i,
                hour,
                minute,
                buy_price=buy_price,
                solar_kwh=0.0,
                consumption_kwh=consumption_kwh,
            )
        )
    # Morning solar slots (surplus: solar > consumption)
    for j in range(n_morning_solar):
        i = n_overnight + j
        hour = 7 + (i - n_overnight) // 2
        minute = ((i - n_overnight) % 2) * 30
        slots.append(
            make_slot(
                i,
                hour,
                minute,
                buy_price=buy_price,
                solar_kwh=solar_kwh_per_slot,
                consumption_kwh=consumption_kwh,
            )
        )
    return slots


@pytest.fixture
def default_config() -> OptimizerConfig:
    """Default optimizer config for overnight/morning scenarios."""
    return OptimizerConfig(
        battery_capacity_kwh=13.5,
        min_soc_pct=10.0,
        max_soc_pct=100.0,
        demand_window_target_soc_pct=80.0,
        soc_bins=20,
        optimization_mode="self_consumption",
        effective_cheap_price=0.20,  # price gate is permissive so optimizer can choose
    )


# ---------------------------------------------------------------------------
# Fix A: futile_cycling_penalty field on ObjectiveTerms
# ---------------------------------------------------------------------------


class TestFutileCyclingPenaltyField:
    """ObjectiveTerms must expose the new futile_cycling_penalty field."""

    def test_futile_cycling_penalty_field_exists(self):
        """ObjectiveTerms should have a futile_cycling_penalty field, defaulting to 0."""
        terms = ObjectiveTerms()
        assert hasattr(terms, "futile_cycling_penalty")
        assert terms.futile_cycling_penalty == 0.0

    def test_futile_cycling_penalty_included_in_net_cost(self):
        """futile_cycling_penalty must be added into net_cost."""
        terms = ObjectiveTerms(import_cost=0.10, futile_cycling_penalty=0.05)
        assert terms.net_cost == pytest.approx(0.15, abs=1e-6)

    def test_futile_cycling_penalty_in_to_dict(self):
        """futile_cycling_penalty must appear in to_dict() output."""
        terms = ObjectiveTerms(futile_cycling_penalty=0.03)
        d = terms.to_dict()
        assert "futile_cycling_penalty" in d
        assert d["futile_cycling_penalty"] == pytest.approx(0.03)

    def test_net_cost_zero_penalty_unchanged(self):
        """With futile_cycling_penalty=0.0 (default) net_cost is unaffected."""
        terms_old = ObjectiveTerms(import_cost=0.20)
        terms_new = ObjectiveTerms(import_cost=0.20, futile_cycling_penalty=0.0)
        assert terms_old.net_cost == pytest.approx(terms_new.net_cost, abs=1e-9)


# ---------------------------------------------------------------------------
# Fix A: _get_futile_cycling_penalty_factor method
# ---------------------------------------------------------------------------


class TestFutileCyclingPenaltyFactor:
    """DPPlanner._get_futile_cycling_penalty_factor returns 0-1 drain fraction."""

    def test_no_penalty_when_solar_surplus_ahead(self, default_config):
        """Factor should be 0 when the very next slot has solar surplus."""
        # Slot 0: overnight charge candidate
        # Slot 1: solar surplus (solar > consumption)
        slots = [
            make_slot(0, 6, 0, solar_kwh=0.0, consumption_kwh=0.25),
            make_slot(1, 6, 30, solar_kwh=1.5, consumption_kwh=0.25),  # surplus
        ]
        planner = DPPlanner()
        factor = get_futile_cycling_penalty_factor(
            action=PlannerAction.CHARGE_GRID_NORMAL,
            slot_idx=0,
            slots=slots,
            config=default_config,
            soc_after_charge_pct=30.0,
            charge_kwh=2.7,  # 3 kW * 0.5h * 0.9 eff
        )
        assert factor == pytest.approx(0.0, abs=1e-6)

    def test_no_penalty_when_dw_slot_ahead(self, default_config):
        """Factor should be 0 when a demand-window slot appears before energy drains."""
        slots = [
            make_slot(0, 6, 0, solar_kwh=0.0, consumption_kwh=0.1),
            make_slot(
                1, 6, 30, solar_kwh=0.0, consumption_kwh=0.1, is_demand_window_slot=True
            ),
        ]
        planner = DPPlanner()
        factor = get_futile_cycling_penalty_factor(
            action=PlannerAction.CHARGE_GRID_NORMAL,
            slot_idx=0,
            slots=slots,
            config=default_config,
            soc_after_charge_pct=30.0,
            charge_kwh=2.0,
        )
        assert factor == pytest.approx(0.0, abs=1e-6)

    def test_full_penalty_when_energy_drains_before_useful_period(self, default_config):
        """Factor close to 1.0 when house load drains nearly all charged energy before solar/DW."""
        # 12 overnight slots, no solar, no DW. High consumption will drain almost all.
        slots = make_overnight_slots(n_overnight=12, n_morning_solar=0)
        planner = DPPlanner()
        # Charge only 1 kWh; overnight consumption at 0.25 kWh/slot will drain it in 4 slots.
        # Factor may not reach exactly 1.0 because SOC physically floors at min_soc,
        # leaving a tiny residual in the battery. Allow abs=0.10 tolerance.
        factor = get_futile_cycling_penalty_factor(
            action=PlannerAction.CHARGE_GRID_NORMAL,
            slot_idx=0,
            slots=slots,
            config=default_config,
            soc_after_charge_pct=17.4,  # ~1 kWh above floor in 13.5 kWh battery
            charge_kwh=1.0,
        )
        assert factor == pytest.approx(1.0, abs=0.10)

    def test_partial_penalty_when_some_energy_retained(self, default_config):
        """Factor is between 0 and 1 when partial drain occurs before solar."""
        # 4 overnight slots draining 0.25 kWh each = 1 kWh drained,
        # then 4 solar slots.  If we charge 2 kWh, ~50% drains.
        slots = make_overnight_slots(
            n_overnight=4,
            n_morning_solar=4,
            consumption_kwh=0.25,
            solar_kwh_per_slot=1.5,
        )
        planner = DPPlanner()
        factor = get_futile_cycling_penalty_factor(
            action=PlannerAction.CHARGE_GRID_NORMAL,
            slot_idx=0,
            slots=slots,
            config=default_config,
            soc_after_charge_pct=24.8,  # ~2 kWh above floor
            charge_kwh=2.0,
        )
        # ~1 kWh drained from 2 kWh → factor ≈ 0.5
        assert 0.3 < factor < 0.7

    def test_no_penalty_for_hold_action(self, default_config):
        """HOLD action is not grid charging — factor must be 0."""
        slots = make_overnight_slots(n_overnight=12, n_morning_solar=0)
        planner = DPPlanner()
        factor = get_futile_cycling_penalty_factor(
            action=PlannerAction.HOLD,
            slot_idx=0,
            slots=slots,
            config=default_config,
            soc_after_charge_pct=10.0,
            charge_kwh=0.0,
        )
        assert factor == 0.0

    def test_no_penalty_for_export_action(self, default_config):
        """EXPORT_PROACTIVE action is not grid charging — factor must be 0."""
        slots = make_overnight_slots(n_overnight=12, n_morning_solar=0)
        planner = DPPlanner()
        factor = get_futile_cycling_penalty_factor(
            action=PlannerAction.EXPORT_PROACTIVE,
            slot_idx=0,
            slots=slots,
            config=default_config,
            soc_after_charge_pct=50.0,
            charge_kwh=0.0,
        )
        assert factor == 0.0

    def test_zero_charge_kwh_gives_zero_factor(self, default_config):
        """If no energy is charged there is nothing to drain — factor must be 0."""
        slots = make_overnight_slots(n_overnight=12, n_morning_solar=0)
        planner = DPPlanner()
        factor = get_futile_cycling_penalty_factor(
            action=PlannerAction.CHARGE_GRID_NORMAL,
            slot_idx=0,
            slots=slots,
            config=default_config,
            soc_after_charge_pct=10.0,
            charge_kwh=0.0,
        )
        assert factor == 0.0


# ---------------------------------------------------------------------------
# Fix A: stage_cost() exposes futile_cycling_penalty_factor kwarg
# ---------------------------------------------------------------------------


class TestStageCostFutilePenalty:
    """stage_cost() must accept and apply futile_cycling_penalty_factor."""

    def _make_charge_slot(self) -> SlotContext:
        return make_slot(0, 0, 0, buy_price=0.14, solar_kwh=0.0, consumption_kwh=0.25)

    def test_zero_factor_gives_zero_penalty(self, default_config):
        """With factor=0, futile_cycling_penalty in ObjectiveTerms should be 0."""
        from custom_components.localshift.engine.optimizer_dp import DPPlanner

        slot = self._make_charge_slot()
        terms = stage_cost(
            action=PlannerAction.CHARGE_GRID_NORMAL,
            grid_import_kwh=1.65,
            grid_export_kwh=0.0,
            slot=slot,
            config=default_config,
            soc_pct=30.0,
            futile_cycling_penalty_factor=0.0,
        )
        assert terms.futile_cycling_penalty == pytest.approx(0.0, abs=1e-9)

    def test_nonzero_factor_gives_positive_penalty(self, default_config):
        """With factor=1.0, penalty equals (eff_loss + margin) × import cost of wasted energy."""
        from custom_components.localshift.engine.optimizer_dp import DPPlanner

        slot = self._make_charge_slot()
        grid_import = 1.65  # kWh
        buy_price = 0.14
        charge_eff = default_config.charge_efficiency
        discharge_eff = default_config.discharge_efficiency
        eff_loss = 1.0 - charge_eff * discharge_eff

        terms = stage_cost(
            action=PlannerAction.CHARGE_GRID_NORMAL,
            grid_import_kwh=grid_import,
            grid_export_kwh=0.0,
            slot=slot,
            config=default_config,
            soc_pct=30.0,
            futile_cycling_penalty_factor=1.0,
        )
        # New formula: (eff_loss + 0.30) × buy_price × factor
        margin = 0.30
        expected = grid_import * (eff_loss + margin) * buy_price
        assert terms.futile_cycling_penalty == pytest.approx(expected, rel=0.01)

    def test_penalty_scales_with_factor(self, default_config):
        """futile_cycling_penalty scales linearly with the factor."""
        from custom_components.localshift.engine.optimizer_dp import DPPlanner

        slot = self._make_charge_slot()
        terms_half = stage_cost(
            action=PlannerAction.CHARGE_GRID_NORMAL,
            grid_import_kwh=2.0,
            grid_export_kwh=0.0,
            slot=slot,
            config=default_config,
            soc_pct=30.0,
            futile_cycling_penalty_factor=0.5,
        )
        terms_full = stage_cost(
            action=PlannerAction.CHARGE_GRID_NORMAL,
            grid_import_kwh=2.0,
            grid_export_kwh=0.0,
            slot=slot,
            config=default_config,
            soc_pct=30.0,
            futile_cycling_penalty_factor=1.0,
        )
        assert terms_full.futile_cycling_penalty == pytest.approx(
            2 * terms_half.futile_cycling_penalty, rel=0.01
        )

    def test_hold_action_never_gets_penalty(self, default_config):
        """HOLD action must never receive a futile_cycling_penalty."""
        from custom_components.localshift.engine.optimizer_dp import DPPlanner

        slot = self._make_charge_slot()
        terms = stage_cost(
            action=PlannerAction.HOLD,
            grid_import_kwh=0.25,
            grid_export_kwh=0.0,
            slot=slot,
            config=default_config,
            soc_pct=30.0,
            futile_cycling_penalty_factor=1.0,
        )
        assert terms.futile_cycling_penalty == pytest.approx(0.0, abs=1e-9)

    def test_penalty_included_in_net_cost(self, default_config):
        """futile_cycling_penalty must increase net_cost."""
        from custom_components.localshift.engine.optimizer_dp import DPPlanner

        slot = self._make_charge_slot()
        terms_no_penalty = stage_cost(
            action=PlannerAction.CHARGE_GRID_NORMAL,
            grid_import_kwh=1.65,
            grid_export_kwh=0.0,
            slot=slot,
            config=default_config,
            soc_pct=30.0,
            futile_cycling_penalty_factor=0.0,
        )
        terms_with_penalty = stage_cost(
            action=PlannerAction.CHARGE_GRID_NORMAL,
            grid_import_kwh=1.65,
            grid_export_kwh=0.0,
            slot=slot,
            config=default_config,
            soc_pct=30.0,
            futile_cycling_penalty_factor=1.0,
        )
        assert terms_with_penalty.net_cost > terms_no_penalty.net_cost


# ---------------------------------------------------------------------------
# Fix B: SC discount — self_consumption_value reduced near SOC floor at night
# ---------------------------------------------------------------------------


class TestSCDiscountNearFloor:
    """SC value is NOT artificially discounted near the SOC floor.
    The SC credit scales naturally with available battery energy — at min_soc it is
    physically zero (no energy to discharge), and it grows proportionally with SOC above
    the floor. No artificial discount ramp is applied (Issue #638 decided against it
    because it caused regressions in legitimate arbitrage scenarios)."""

    def test_sc_full_credit_at_high_soc_no_solar(self, default_config):
        """At SOC well above floor (e.g. 60%) with no solar, SC value is present."""
        from custom_components.localshift.engine.optimizer_dp import DPPlanner

        slot = make_slot(0, 2, 0, solar_kwh=0.0, consumption_kwh=0.25, buy_price=0.14)
        terms = stage_cost(
            action=PlannerAction.HOLD,
            grid_import_kwh=0.0,
            grid_export_kwh=0.0,
            slot=slot,
            config=default_config,
            soc_pct=60.0,
        )
        # At 60% SOC there's plenty of battery energy — SC value should be present
        # (battery covers the 0.25 kWh load)
        assert terms.self_consumption_value > 0.0

    def test_sc_zero_at_soc_floor_no_solar(self, default_config):
        """At SOC = min_soc (10%) with no solar, SC value is 0 — physically unavailable."""
        from custom_components.localshift.engine.optimizer_dp import DPPlanner

        slot = make_slot(0, 2, 0, solar_kwh=0.0, consumption_kwh=0.25, buy_price=0.14)
        terms = stage_cost(
            action=PlannerAction.HOLD,
            grid_import_kwh=0.0,
            grid_export_kwh=0.0,
            slot=slot,
            config=default_config,
            soc_pct=default_config.min_soc_pct,
        )
        # At min SOC there is no energy to discharge — SC value is physically 0
        # (clamped by available_kwh = 0 in the transition, not an artificial discount)
        assert terms.self_consumption_value == pytest.approx(0.0, abs=1e-6)

    def test_sc_scales_with_available_soc(self, default_config):
        """SC value scales naturally with available battery energy, no artificial discount."""
        from custom_components.localshift.engine.optimizer_dp import DPPlanner

        # Use a large consumption so battery capacity is the binding constraint,
        # not the load amount. At SOC=15% (floor+5pp) available ≈ 0.641 kWh;
        # at SOC=50% (floor+40pp) available ≈ 1.65 kWh (rate-limited). Both are
        # less than 3.0 kWh consumption, so SC value tracks available battery energy.
        slot = make_slot(0, 2, 0, solar_kwh=0.0, consumption_kwh=3.0, buy_price=0.14)

        # At floor+5pp there is a small amount of energy available
        terms_low = stage_cost(
            action=PlannerAction.HOLD,
            grid_import_kwh=0.0,
            grid_export_kwh=0.0,
            slot=slot,
            config=default_config,
            soc_pct=default_config.min_soc_pct + 5.0,  # 15%
        )

        # At floor+40pp there is much more energy available
        terms_high = stage_cost(
            action=PlannerAction.HOLD,
            grid_import_kwh=0.0,
            grid_export_kwh=0.0,
            slot=slot,
            config=default_config,
            soc_pct=default_config.min_soc_pct + 40.0,  # 50%
        )

        # SC value should increase with SOC (more energy available to cover load)
        assert terms_high.self_consumption_value > terms_low.self_consumption_value

    def test_sc_no_artificial_discount_at_mid_soc(self, default_config):
        """SC credit at mid-SOC equals battery_for_load * buy_price."""
        from custom_components.localshift.engine.optimizer_dp import DPPlanner

        # At SOC=25% (floor+15pp), available battery is 15/90 of capacity ≈ 0.675 kWh
        # Consumption = 0.25 kWh — battery can cover it fully
        slot = make_slot(0, 2, 0, solar_kwh=0.0, consumption_kwh=0.25, buy_price=0.14)
        capacity_kwh = default_config.battery_capacity_kwh
        soc_pct = default_config.min_soc_pct + 15.0
        terms = stage_cost(
            action=PlannerAction.HOLD,
            grid_import_kwh=0.0,
            grid_export_kwh=0.0,
            slot=slot,
            config=default_config,
            soc_pct=soc_pct,
        )

        # Available energy well exceeds the 0.25 kWh load — battery should cover it fully
        available_kwh = (soc_pct - default_config.min_soc_pct) / 100.0 * capacity_kwh
        assert available_kwh > 0.25  # ensure the scenario is sensible
        # SC value = full load × buy_price
        expected_sc = 0.25 * 0.14
        assert terms.self_consumption_value == pytest.approx(expected_sc, rel=0.05)


# ---------------------------------------------------------------------------
# End-to-end integration tests
# ---------------------------------------------------------------------------


class TestEndToEndOvernightBehavior:
    """End-to-end planner tests for the overnight cycling scenario."""

    def test_overnight_futile_charge_has_significant_penalty(self):
        """When grid charging at night is futile, the decision must carry meaningful penalty.

        This is the core Issue #638 scenario: $0.14/kWh overnight, no solar, no DW.
        The futile cycling penalty must be applied to any overnight grid charge decision
        so that the optimizer correctly accounts for the efficiency losses of round-tripping
        energy through the battery when it will all drain back before any useful period.
        """
        # 12 overnight slots ($0.14/kWh, no solar), no DW, no morning solar
        slots = make_overnight_slots(
            n_overnight=12,
            n_morning_solar=0,
            buy_price=0.14,
            consumption_kwh=0.25,
        )

        # Config with permissive price gate so optimizer can charge if it wants to
        config = OptimizerConfig(
            battery_capacity_kwh=13.5,
            min_soc_pct=10.0,
            max_soc_pct=100.0,
            demand_window_target_soc_pct=80.0,
            soc_bins=20,
            optimization_mode="self_consumption",
            effective_cheap_price=0.20,
        )

        inputs = OptimizerInputs(
            cycle_id="test-638-overnight-penalty",
            initial_soc_pct=10.0,
            slots=slots,
            config=config,
            all_solcast=[],
        )

        result = DPPlanner().plan(inputs)
        assert result.success

        charge_decisions = [
            d
            for d in result.decisions
            if d.action
            in (PlannerAction.CHARGE_GRID_NORMAL, PlannerAction.CHARGE_GRID_BOOST)
        ]

        # If the optimizer chose to charge (it may or may not), all such decisions
        # must carry a non-zero futile_cycling_penalty reflecting the wasted round-trip.
        for d in charge_decisions:
            assert d.objective_terms.futile_cycling_penalty > 0.0, (
                f"Slot {d.slot_index}: overnight grid charge has zero futile penalty "
                "but all energy drains before any useful period"
            )
            # The penalty should be meaningful: efficiency loss × import_cost
            # At factor=1.0: penalty ≈ import_kwh × (1 - 0.92×0.95) × buy_price
            #                        = import_kwh × 0.126 × 0.14 ≈ 1.2% of import_cost
            expected_min_penalty = d.grid_import_kwh * 0.10 * d.buy_price
            assert d.objective_terms.futile_cycling_penalty >= expected_min_penalty, (
                f"Slot {d.slot_index}: futile penalty {d.objective_terms.futile_cycling_penalty:.4f} "
                f"too small (expected >= {expected_min_penalty:.4f})"
            )

    def test_large_price_spread_still_triggers_charge(self, default_config):
        """When overnight price is very cheap ($0.05) vs DW ($0.40), charging is justified.

        The futile cycling penalty is based on efficiency losses (~13% of import cost).
        At $0.05/kWh the loss is only $0.007/kWh — much smaller than the $0.35 spread.
        Charging should still be preferred.
        """
        # Cheap overnight then expensive demand-window
        slots = []
        # 6 overnight slots at $0.05
        for i in range(6):
            slots.append(
                make_slot(
                    i,
                    1 + i // 2,
                    (i % 2) * 30,
                    buy_price=0.05,
                    solar_kwh=0.0,
                    consumption_kwh=0.25,
                )
            )
        # 2 demand-window slots at $0.40
        for j in range(2):
            slots.append(
                make_slot(
                    6 + j,
                    7 + j // 2,
                    (j % 2) * 30,
                    buy_price=0.40,
                    solar_kwh=0.0,
                    consumption_kwh=0.25,
                    is_demand_window_slot=(j == 0),
                    is_demand_window_entry=(j == 0),
                )
            )

        inputs = OptimizerInputs(
            cycle_id="test-638-large-spread",
            initial_soc_pct=10.0,
            slots=slots,
            config=default_config,
            all_solcast=[],
        )

        result = DPPlanner().plan(inputs)
        assert result.success

        overnight_decisions = result.decisions[:6]
        grid_charges = [
            d
            for d in overnight_decisions
            if d.action
            in (PlannerAction.CHARGE_GRID_NORMAL, PlannerAction.CHARGE_GRID_BOOST)
        ]
        # At $0.05/kWh with $0.40 DW, arbitrage is clearly worth it
        assert len(grid_charges) > 0, (
            "Optimizer should charge at $0.05 when DW is $0.40 — arbitrage is "
            f"profitable. Actions: {[d.action.name for d in overnight_decisions]}"
        )

    def test_futile_charge_decisions_carry_nonzero_penalty(self, default_config):
        """Any grid charge decision where energy will drain before solar should have
        a non-zero futile_cycling_penalty in its objective_terms."""
        # All overnight, no solar ever — every charge is futile
        slots = [
            make_slot(i, i // 2, (i % 2) * 30, solar_kwh=0.0, consumption_kwh=0.25)
            for i in range(20)
        ]
        # Use a config where effective_cheap_price is high enough to allow charging
        config = OptimizerConfig(
            battery_capacity_kwh=13.5,
            min_soc_pct=10.0,
            max_soc_pct=100.0,
            demand_window_target_soc_pct=80.0,
            soc_bins=20,
            optimization_mode="self_consumption",
            effective_cheap_price=0.30,  # very permissive
        )

        inputs = OptimizerInputs(
            cycle_id="test-638-penalty-in-terms",
            initial_soc_pct=20.0,
            slots=slots,
            config=config,
            all_solcast=[],
        )

        result = DPPlanner().plan(inputs)
        assert result.success

        charge_decisions = [
            d
            for d in result.decisions
            if d.action
            in (PlannerAction.CHARGE_GRID_NORMAL, PlannerAction.CHARGE_GRID_BOOST)
        ]

        # If any charges happened, they should ALL have a non-zero futile penalty
        for d in charge_decisions:
            assert d.objective_terms.futile_cycling_penalty > 0.0, (
                f"Slot {d.slot_index}: grid charge decision has zero futile "
                "penalty but all energy drains before any useful period"
            )


# ---------------------------------------------------------------------------
# Later-Cheaper-Window Regression Tests
# ---------------------------------------------------------------------------


class TestLaterCheaperWindow:
    """Regression tests: optimizer should prefer waiting when a cheaper charge window exists.

    Issue: overnight charges at $0.16 when $0.10 is available at 05:00.
    The futile-cycling penalty should recognise a future cheaper charge window
    as a "useful period" — charging now is futile if the energy drains
    through house load before the cheaper window arrives AND the cheaper window
    is reachable (SOC stays above min_soc).
    """

    def _make_cheap_window_config(self) -> OptimizerConfig:
        """Config for cheap window scenario."""
        return OptimizerConfig(
            battery_capacity_kwh=13.5,
            demand_window_target_soc_pct=80.0,
            min_soc_pct=10.0,
            effective_cheap_price=0.14,  # $0.14 is the "cheap" threshold
            charge_efficiency=0.92,
            discharge_efficiency=0.95,
        )

    def test_factor_lower_when_cheaper_window_is_useful_period(self):
        """Factor LOWER when a cheaper feasible window is reachable — drain stops there.

        Scenario: Charge 2.7 kWh at 22:00 ($0.16). House drains 0.45 kWh/slot.
        SOC = 30% → min_soc fires at slot 5 (5 × 0.45 = 2.25 kWh > available at slot 5).

        With cheap window at slot 5: fix fires BEFORE drain in slot 5 → break → no drain at slot 5
        → 4 full slots × 0.45 = 1.80 kWh drained → factor = 0.667

        Without cheap window: min_soc check fires after drain in slot 5 → 5 slots drained = 2.25 kWh
        → but wait, battery_used=0.324 (all available) < 0.45, soc drops to 10%, min_soc breaks
        → factor = 2.25/2.70 = 0.833

        Let me recalculate step by step for consumption=0.45 kWh/slot, charge=2.7 kWh, soc=30%:
        - 2.7 kWh available. Each slot drains 0.45.
        - After slot 1: soc = 30 - (0.45/0.95/13.5)*100 = 26.48%, available = 2.25 kWh
        - After slot 2: soc = 22.96%, available = 1.80 kWh
        - After slot 3: soc = 19.44%, available = 1.35 kWh
        - After slot 4: soc = 15.93%, available = 0.90 kWh
        - At slot 5: net_load=0.45, available=0.90*0.95=0.855 > 0.45 → battery_used=0.45
          → drain 0.45, soc = 15.93 - (0.45/0.95/13.5)*100 = 12.41%, available = 0.324 kWh
          → soc <= min_soc? 12.41 > 10 → NO break here
        - At slot 6: net_load=0.45, available=0.324*0.95=0.308 < 0.45 → battery_used=0.308
          → soc = 12.41 - (0.308/0.95/13.5)*100 = 10.0%, available = 0
          → soc <= min_soc? 10.0 <= 10 → YES break

        So min_soc fires at slot 6 (trying to drain, after slot 5 SOC=12.41%).
        With cheap window at slot 5:
        - Slot 5: cheap check fires BEFORE drain → break → no drain in slot 5
        - Total drain = 4 slots × 0.45 = 1.80 kWh → factor = 0.667

        Without cheap window at slot 5:
        - Slots 1-5 drain: 4 full + partial in slot 5 = 1.80 + 0.324 = 2.124 kWh
        - Slot 6: min_soc fires (battery_used < net_load)
        - Total drain = 2.124 kWh → factor = 0.787

        Hmm, different but not dramatically. Let me recalculate.

        Actually wait, in slot 5 with cheap window at slot 5:
        - The cheap window check fires for slot 5 BEFORE the drain calculation
        - So slot 5 is NOT drained at all! Break fires immediately.
        - Total drain = 4 slots × 0.45 = 1.80 kWh → factor = 0.667

        Without cheap window:
        - Slots 1-5 drain (5 slots): 4 full (1.80) + slot 5 (0.45) = 2.25 kWh
        - Slot 6: min_soc fires
        - factor = 2.25/2.70 = 0.833

        YES! This gives the right comparison.
        """
        config = self._make_cheap_window_config()
        # 6 high-price slots (0-5 at $0.16, 0.45 kWh/slot) + cheap window at slot 5
        # charge=2.7 kWh → min_soc fires at slot 6 (not slot 5)
        # Cheap window at slot 5: fix fires BEFORE drain in slot 5
        slots = [
            make_slot(0, 22, 0, buy_price=0.16, solar_kwh=0.0, consumption_kwh=0.45),
            make_slot(1, 22, 30, buy_price=0.16, solar_kwh=0.0, consumption_kwh=0.45),
            make_slot(2, 23, 0, buy_price=0.16, solar_kwh=0.0, consumption_kwh=0.45),
            make_slot(3, 23, 30, buy_price=0.16, solar_kwh=0.0, consumption_kwh=0.45),
            make_slot(4, 0, 0, buy_price=0.16, solar_kwh=0.0, consumption_kwh=0.45),
            make_slot(5, 0, 30, buy_price=0.10, solar_kwh=0.0, consumption_kwh=0.45),
            make_slot(6, 1, 0, buy_price=0.16, solar_kwh=0.0, consumption_kwh=0.45),
        ]

        # With cheap window: fix fires at slot 5 (before drain) → break → 1.80 kWh → factor=0.667
        factor_with_cheaper = get_futile_cycling_penalty_factor(
            action=PlannerAction.CHARGE_GRID_NORMAL,
            slot_idx=0,
            slots=slots,
            config=config,
            soc_after_charge_pct=30.0,
            charge_kwh=2.7,
        )

        # Without cheap window: min_soc fires at slot 6 (after draining 5 slots) → 2.25 kWh → factor=0.833
        slots_no_cheap = slots[:6]
        slots_no_cheap[5] = make_slot(
            5, 0, 30, buy_price=0.16, solar_kwh=0.0, consumption_kwh=0.45
        )
        factor_without_cheap = get_futile_cycling_penalty_factor(
            action=PlannerAction.CHARGE_GRID_NORMAL,
            slot_idx=0,
            slots=slots_no_cheap,
            config=config,
            soc_after_charge_pct=30.0,
            charge_kwh=2.7,
        )

        # With fix: cheap window at slot 5 fires BEFORE drain → break → 1.80 kWh → factor=0.667
        # Buggy: drain through slot 5, min_soc fires at slot 6 → 2.25 kWh → factor=0.833
        assert factor_with_cheaper < factor_without_cheap, (
            f"Factor WITH cheaper window ({factor_with_cheaper:.3f}) should be LOWER "
            f"than WITHOUT ({factor_without_cheap:.3f}) — with the fix, drain stops at the "
            "cheaper window before min_soc fires (less drain), buggy code drains to min_soc "
            f"(more drain: 1.80 kWh vs 2.25 kWh)"
        )

    def test_factor_high_when_cheaper_window_beyond_min_soc(self):
        """Factor is high when energy drains to min_soc before reaching cheaper window.

        Scenario: Charge 3 kWh at 22:00 ($0.16). House drains 0.5 kWh/slot.
        6 slots until 01:00. Battery hits min_soc (10%) at slot 4.
        Slot 5 ($0.10 cheaper) is unreachable → high futile factor.
        """
        config = self._make_cheap_window_config()
        slots = [
            make_slot(
                i,
                22 + i // 2,
                (i % 2) * 30,
                buy_price=0.16,
                solar_kwh=0.0,
                consumption_kwh=0.50,
            )
            for i in range(6)
        ]
        slots.append(
            make_slot(6, 1, 0, buy_price=0.10, solar_kwh=0.0, consumption_kwh=0.50)
        )

        factor = get_futile_cycling_penalty_factor(
            action=PlannerAction.CHARGE_GRID_NORMAL,
            slot_idx=0,
            slots=slots,
            config=config,
            soc_after_charge_pct=32.0,  # 3 kWh above floor
            charge_kwh=3.0,
        )

        # Battery hits floor before cheaper window → high futile factor
        assert factor > 0.7, (
            f"Expected high futile factor (>0.7) when battery drains to min_soc "
            f"before cheaper window, got {factor:.3f}"
        )
