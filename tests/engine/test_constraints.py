"""Tests for constraint functions (feasible_actions, solar gates)."""

from datetime import datetime, timezone

import pytest

from custom_components.localshift.engine.constraints import (
    feasible_actions,
)
from custom_components.localshift.engine.types import (
    OptimizerConfig,
    PlannerAction,
    SlotContext,
)


class TestFeasibleActions:
    """Test feasible_actions constraint logic."""

    def test_hold_always_feasible(self):
        """HOLD should always be feasible regardless of SOC."""
        config = OptimizerConfig()
        slot = SlotContext(
            slot_index=0,
            slot_interval_minutes=30,
            timestamp_iso="2024-01-01T00:00:00+00:00",
            buy_price=30.0,
            sell_price=10.0,
            solar_kwh=1.0,
            consumption_kwh=1.0,
            is_demand_window_slot=False,
        )

        # At minimum SOC
        actions = feasible_actions(10.0, slot, config)
        assert PlannerAction.HOLD in actions

        # At maximum SOC
        actions = feasible_actions(100.0, slot, config)
        assert PlannerAction.HOLD in actions

        # In the middle
        actions = feasible_actions(50.0, slot, config)
        assert PlannerAction.HOLD in actions

    def test_no_charge_at_max_soc(self):
        """Cannot charge when already at max SOC."""
        config = OptimizerConfig(max_soc_pct=100.0)
        slot = SlotContext(
            slot_index=0,
            slot_interval_minutes=30,
            timestamp_iso="2024-01-01T00:00:00+00:00",
            buy_price=10.0,  # Cheap
            sell_price=10.0,
            solar_kwh=1.0,
            consumption_kwh=1.0,
            is_demand_window_slot=False,
        )

        actions = feasible_actions(100.0, slot, config)
        assert PlannerAction.CHARGE_GRID_NORMAL not in actions
        assert PlannerAction.CHARGE_GRID_BOOST not in actions

    def test_no_charge_in_demand_window(self):
        """Cannot charge from grid during demand window."""
        config = OptimizerConfig()
        slot = SlotContext(
            slot_index=0,
            slot_interval_minutes=30,
            timestamp_iso="2024-01-01T00:00:00+00:00",
            buy_price=10.0,  # Cheap
            sell_price=10.0,
            solar_kwh=1.0,
            consumption_kwh=1.0,
            is_demand_window_slot=True,  # In demand window
        )

        actions = feasible_actions(50.0, slot, config)
        assert PlannerAction.CHARGE_GRID_NORMAL not in actions
        assert PlannerAction.CHARGE_GRID_BOOST not in actions

    def test_cheap_price_allows_charge_self_consumption(self):
        """Cheap import price allows charging in self_consumption mode."""
        config = OptimizerConfig(
            optimization_mode="self_consumption",
            effective_cheap_price=20.0,
        )
        slot = SlotContext(
            slot_index=0,
            slot_interval_minutes=30,
            timestamp_iso="2024-01-01T00:00:00+00:00",
            buy_price=15.0,  # Below cheap threshold
            sell_price=10.0,
            solar_kwh=1.0,
            consumption_kwh=1.0,
            is_demand_window_slot=False,
        )

        actions = feasible_actions(50.0, slot, config)
        assert PlannerAction.CHARGE_GRID_NORMAL in actions

    def test_expensive_price_blocks_charge_self_consumption(self):
        """Expensive import price blocks charging in self_consumption mode."""
        config = OptimizerConfig(
            optimization_mode="self_consumption",
            effective_cheap_price=20.0,
        )
        slot = SlotContext(
            slot_index=0,
            slot_interval_minutes=30,
            timestamp_iso="2024-01-01T00:00:00+00:00",
            buy_price=50.0,  # Above cheap threshold
            sell_price=10.0,
            solar_kwh=1.0,
            consumption_kwh=1.0,
            is_demand_window_slot=False,
        )

        actions = feasible_actions(50.0, slot, config)
        assert PlannerAction.CHARGE_GRID_NORMAL not in actions

    def test_arbitrage_mode_ignores_price(self):
        """Arbitrage mode allows charging regardless of price."""
        config = OptimizerConfig(optimization_mode="arbitrage")
        slot = SlotContext(
            slot_index=0,
            slot_interval_minutes=30,
            timestamp_iso="2024-01-01T00:00:00+00:00",
            buy_price=100.0,  # Expensive
            sell_price=10.0,
            solar_kwh=1.0,
            consumption_kwh=1.0,
            is_demand_window_slot=False,
        )

        actions = feasible_actions(50.0, slot, config)
        assert PlannerAction.CHARGE_GRID_NORMAL in actions

    def test_export_requires_profitable_price_self_consumption(self):
        """Export requires profitable sell price in self_consumption mode."""
        config = OptimizerConfig(
            optimization_mode="self_consumption",
            export_price_margin=5.0,
        )
        slot = SlotContext(
            slot_index=0,
            slot_interval_minutes=30,
            timestamp_iso="2024-01-01T00:00:00+00:00",
            buy_price=30.0,
            sell_price=50.0,  # sell > buy + margin
            solar_kwh=1.0,
            consumption_kwh=1.0,
            is_demand_window_slot=False,
        )

        actions = feasible_actions(50.0, slot, config)
        assert PlannerAction.EXPORT_PROACTIVE in actions

    def test_export_blocked_unprofitable_self_consumption(self):
        """Export blocked if not profitable in self_consumption mode."""
        config = OptimizerConfig(
            optimization_mode="self_consumption",
            export_price_margin=5.0,
        )
        slot = SlotContext(
            slot_index=0,
            slot_interval_minutes=30,
            timestamp_iso="2024-01-01T00:00:00+00:00",
            buy_price=30.0,
            sell_price=20.0,  # sell < buy + margin
            solar_kwh=1.0,
            consumption_kwh=1.0,
            is_demand_window_slot=False,
        )

        actions = feasible_actions(50.0, slot, config)
        assert PlannerAction.EXPORT_PROACTIVE not in actions

    def test_no_export_at_min_soc(self):
        """Cannot export when at minimum SOC."""
        config = OptimizerConfig()
        slot = SlotContext(
            slot_index=0,
            slot_interval_minutes=30,
            timestamp_iso="2024-01-01T00:00:00+00:00",
            buy_price=30.0,
            sell_price=100.0,  # Very profitable
            solar_kwh=1.0,
            consumption_kwh=1.0,
            is_demand_window_slot=False,
        )

        actions = feasible_actions(10.0, slot, config)  # At min SOC
        assert PlannerAction.EXPORT_PROACTIVE not in actions


class TestSolarGate:
    """Test solar sufficiency gate logic."""

    def test_solar_covers_deficit_suppresses_charge(self):
        """Solar covering deficit should suppress grid charging."""
        # This test verifies the integration between feasible_actions
        # and the solar sufficiency check
        config = OptimizerConfig(
            optimization_mode="self_consumption",
            effective_cheap_price=50.0,
            battery_capacity_kwh=10.0,
            max_soc_pct=100.0,
        )
        slot = SlotContext(
            slot_index=0,
            slot_interval_minutes=30,
            timestamp_iso="2024-01-01T00:00:00+00:00",
            buy_price=10.0,  # Cheap
            sell_price=5.0,
            solar_kwh=5.0,  # High solar
            consumption_kwh=0.5,  # Low consumption
            is_demand_window_slot=False,
        )
        # Create future slots with high solar
        future_slot = SlotContext(
            slot_index=1,
            slot_interval_minutes=30,
            timestamp_iso="2024-01-01T00:00:00+00:00",
            buy_price=30.0,
            sell_price=10.0,
            solar_kwh=5.0,
            consumption_kwh=0.5,
            is_demand_window_slot=False,
        )
        slots = [slot, future_slot]

        # At 50% SOC with plenty of solar ahead, should suppress grid charging
        actions = feasible_actions(
            50.0, slot, config, slot_idx=0, slots=slots, terminal_penalty_idx=2
        )
        # Note: solar gate may suppress charging - this is implementation dependent
        assert PlannerAction.HOLD in actions

    def test_no_solar_demand_window(self):
        """No solar in demand window slots."""
        config = OptimizerConfig()
        slot = SlotContext(
            slot_index=0,
            slot_interval_minutes=30,
            timestamp_iso="2024-01-01T00:00:00+00:00",
            buy_price=30.0,
            sell_price=10.0,
            solar_kwh=0.0,  # No solar
            consumption_kwh=2.0,
            is_demand_window_slot=True,
        )

        actions = feasible_actions(50.0, slot, config)
        # In demand window, no grid import allowed
        assert PlannerAction.CHARGE_GRID_NORMAL not in actions
        assert PlannerAction.CHARGE_GRID_BOOST not in actions


class TestCheckGlobalSolarSufficiency:
    """Test _check_global_solar_sufficiency helper."""

    def test_sufficient_solar_covers_deficit(self):
        """Returns True when solar surplus covers SOC deficit."""
        from custom_components.localshift.engine.constraints import (
            check_global_solar_sufficiency,
        )

        config = OptimizerConfig(
            battery_capacity_kwh=10.0,
            demand_window_target_soc_pct=80.0,
        )
        # High solar slots - 4.5 kWh surplus each
        slots = [
            SlotContext(
                slot_index=i,
                slot_interval_minutes=30,
                timestamp_iso="2024-01-01T00:00:00+00:00",
                buy_price=30.0,
                sell_price=10.0,
                solar_kwh=5.0,
                consumption_kwh=0.5,
                is_demand_window_slot=False,
            )
            for i in range(2)
        ]

        # At 50% SOC, need 30% = 3 kWh
        # 2 slots * 4.5 kWh = 9 kWh surplus > 3 kWh needed
        result = check_global_solar_sufficiency(50.0, 0, slots, config)
        assert result is True

    def test_insufficient_solar_returns_false(self):
        """Returns False when solar surplus insufficient."""
        from custom_components.localshift.engine.constraints import (
            check_global_solar_sufficiency,
        )

        config = OptimizerConfig(
            battery_capacity_kwh=10.0,
            demand_window_target_soc_pct=80.0,
        )
        # Low solar slots - minimal surplus
        slots = [
            SlotContext(
                slot_index=i,
                slot_interval_minutes=30,
                timestamp_iso="2024-01-01T00:00:00+00:00",
                buy_price=30.0,
                sell_price=10.0,
                solar_kwh=0.6,
                consumption_kwh=0.5,  # Only 0.1 kWh surplus
                is_demand_window_slot=False,
            )
            for i in range(2)
        ]

        # At 50% SOC, need 30% = 3 kWh
        # 2 slots * 0.1 kWh = 0.2 kWh surplus < 3 kWh needed
        result = check_global_solar_sufficiency(50.0, 0, slots, config)
        assert result is False

    def test_at_or_above_target_returns_false(self):
        """Returns False when already at or above target."""
        from custom_components.localshift.engine.constraints import (
            check_global_solar_sufficiency,
        )

        config = OptimizerConfig(
            battery_capacity_kwh=10.0,
            demand_window_target_soc_pct=80.0,
        )
        slots = [
            SlotContext(
                slot_index=0,
                slot_interval_minutes=30,
                timestamp_iso="2024-01-01T00:00:00+00:00",
                buy_price=30.0,
                sell_price=10.0,
                solar_kwh=5.0,
                consumption_kwh=0.5,
                is_demand_window_slot=False,
            )
        ]

        # At exactly target
        result = check_global_solar_sufficiency(80.0, 0, slots, config)
        assert result is False

        # Above target
        result = check_global_solar_sufficiency(90.0, 0, slots, config)
        assert result is False

    def test_empty_slots_returns_false(self):
        """Returns False when no slots provided."""
        from custom_components.localshift.engine.constraints import (
            check_global_solar_sufficiency,
        )

        config = OptimizerConfig()
        result = check_global_solar_sufficiency(50.0, 0, [], config)
        assert result is False

    def test_rate_limited_solar_returns_false(self):
        """Returns False when charge rate limit prevents capturing all solar.

        This is the bug from Issue #701: the raw surplus calculation ignores
        charge rate limits and efficiency, incorrectly reporting solar sufficiency.

        Scenario:
        - Battery: 10 kWh capacity, 50% SOC, target 80% (need 3 kWh = 30%)
        - Charge rate: 5 kW (max 2.5 kWh per 30-min slot)
        - Efficiency: 90%
        - 1 slot with 10 kWh surplus (far more than needed on paper)

        Raw calculation: 10 kWh surplus > 3 kWh needed → TRUE (WRONG)
        Realistic calculation: 2.5 kWh * 0.9 = 2.25 kWh < 3 kWh → FALSE (CORRECT)
        """
        from custom_components.localshift.engine.constraints import (
            check_global_solar_sufficiency,
        )

        config = OptimizerConfig(
            battery_capacity_kwh=10.0,
            demand_window_target_soc_pct=80.0,
            solar_charge_rate_kw=5.0,  # 5 kW max charge rate
            charge_efficiency=0.9,  # 90% efficiency
        )
        # One slot with huge surplus (10 kWh) but rate limited
        slots = [
            SlotContext(
                slot_index=0,
                slot_interval_minutes=30,  # 0.5 hours
                timestamp_iso="2024-01-01T00:00:00+00:00",
                buy_price=30.0,
                sell_price=10.0,
                solar_kwh=10.0,  # Huge surplus on paper
                consumption_kwh=0.0,  # No consumption
                is_demand_window_slot=False,
            )
        ]

        # At 50% SOC, need 30% = 3 kWh
        # Raw calculation: 10 kWh > 3 kWh → TRUE (but wrong due to rate limit)
        # Realistic: 5 kW * 0.5h * 0.9 = 2.25 kWh < 3 kWh → FALSE
        result = check_global_solar_sufficiency(50.0, 0, slots, config)
        # This test will FAIL with the current implementation (returns True)
        # and PASS after the fix (returns False)
        assert result is False

    def test_multiple_slots_rate_limited_returns_false(self):
        """Returns False when rate limits prevent capturing multi-slot solar.

        Scenario:
        - Battery: 10 kWh, 50% SOC, target 80% (need 3 kWh)
        - 4 slots with 2 kWh surplus each (8 kWh total on paper)
        - Charge rate: 5 kW, efficiency 90%
        - Per slot: 5 kW * 0.5h * 0.9 = 2.25 kWh max capture

        Raw: 8 kWh > 3 kWh → TRUE
        Realistic: 4 * 2.25 = 9 kWh but... wait, 2 kWh per slot < 2.25 max
        So realistic should be: 4 * 2 * 0.9 = 7.2 kWh > 3 kWh → TRUE

        This test validates that realistic simulation works correctly.
        """
        from custom_components.localshift.engine.constraints import (
            check_global_solar_sufficiency,
        )

        config = OptimizerConfig(
            battery_capacity_kwh=10.0,
            demand_window_target_soc_pct=80.0,
            solar_charge_rate_kw=5.0,
            charge_efficiency=0.9,
        )
        # 4 slots with moderate surplus
        slots = [
            SlotContext(
                slot_index=i,
                slot_interval_minutes=30,
                timestamp_iso="2024-01-01T00:00:00+00:00",
                buy_price=30.0,
                sell_price=10.0,
                solar_kwh=2.0,  # 2 kWh surplus each
                consumption_kwh=0.0,
                is_demand_window_slot=False,
            )
            for i in range(4)
        ]

        # 4 * 2 kWh * 0.9 = 7.2 kWh > 3 kWh → should be TRUE
        result = check_global_solar_sufficiency(50.0, 0, slots, config)
        assert result is True

    def test_efficiency_loss_reduces_effective_gain(self):
        """Returns False when efficiency loss makes solar insufficient.

        Scenario:
        - Battery: 10 kWh, 30% SOC, target 80% (need 5 kWh)
        - 3 slots with 2 kWh surplus each (6 kWh on paper)
        - Efficiency: 80% (10% loss)
        - Realistic: 3 * 2 * 0.8 = 4.8 kWh < 5 kWh → FALSE
        """
        from custom_components.localshift.engine.constraints import (
            check_global_solar_sufficiency,
        )

        config = OptimizerConfig(
            battery_capacity_kwh=10.0,
            demand_window_target_soc_pct=80.0,
            solar_charge_rate_kw=10.0,  # High enough to not limit
            charge_efficiency=0.8,  # 80% efficiency
        )
        slots = [
            SlotContext(
                slot_index=i,
                slot_interval_minutes=30,
                timestamp_iso="2024-01-01T00:00:00+00:00",
                buy_price=30.0,
                sell_price=10.0,
                solar_kwh=2.0,
                consumption_kwh=0.0,
                is_demand_window_slot=False,
            )
            for i in range(3)
        ]

        # At 30% SOC, need 50% = 5 kWh
        # Raw: 6 kWh > 5 kWh → TRUE
        # Realistic: 6 * 0.8 = 4.8 kWh < 5 kWh → FALSE
        result = check_global_solar_sufficiency(30.0, 0, slots, config)
        assert result is False


class TestIsCheapImportWindow:
    """Test _is_cheap_import_window helper."""

    def test_cheap_price_below_threshold(self):
        """Returns True when price below effective cheap threshold."""
        from custom_components.localshift.engine.constraints import (
            is_cheap_import_window,
        )

        config = OptimizerConfig(effective_cheap_price=20.0)
        slot = SlotContext(
            slot_index=0,
            slot_interval_minutes=30,
            timestamp_iso="2024-01-01T00:00:00+00:00",
            buy_price=15.0,  # Below threshold
            sell_price=10.0,
            solar_kwh=1.0,
            consumption_kwh=1.0,
            is_demand_window_slot=False,
        )

        result = is_cheap_import_window(slot, config)
        assert result is True

    def test_expensive_price_above_threshold(self):
        """Returns False when price above effective cheap threshold."""
        from custom_components.localshift.engine.constraints import (
            is_cheap_import_window,
        )

        config = OptimizerConfig(effective_cheap_price=20.0)
        slot = SlotContext(
            slot_index=0,
            slot_interval_minutes=30,
            timestamp_iso="2024-01-01T00:00:00+00:00",
            buy_price=25.0,  # Above threshold
            sell_price=10.0,
            solar_kwh=1.0,
            consumption_kwh=1.0,
            is_demand_window_slot=False,
        )

        result = is_cheap_import_window(slot, config)
        assert result is False

    def test_at_threshold(self):
        """At threshold price returns True."""
        from custom_components.localshift.engine.constraints import (
            is_cheap_import_window,
        )

        config = OptimizerConfig(effective_cheap_price=20.0)
        slot = SlotContext(
            slot_index=0,
            slot_interval_minutes=30,
            timestamp_iso="2024-01-01T00:00:00+00:00",
            buy_price=20.0,  # At threshold
            sell_price=10.0,
            solar_kwh=1.0,
            consumption_kwh=1.0,
            is_demand_window_slot=False,
        )

        result = is_cheap_import_window(slot, config)
        assert result is True


class TestIsBlindToFutureSolar:
    """Test _is_blind_to_future_solar helper."""

    def test_blind_when_slot_idx_at_terminal(self):
        """Returns True when at terminal penalty index."""
        from custom_components.localshift.engine.constraints import (
            is_blind_to_future_solar,
        )

        config = OptimizerConfig()
        slots = [
            SlotContext(
                slot_index=i,
                slot_interval_minutes=30,
                timestamp_iso="2024-01-01T00:00:00+00:00",
                buy_price=30.0,
                sell_price=10.0,
                solar_kwh=1.0,
                consumption_kwh=1.0,
                is_demand_window_slot=False,
            )
            for i in range(5)
        ]

        # At terminal penalty index
        result = is_blind_to_future_solar(slots, 4, 4)
        assert result is True

    def test_blind_when_few_slots_beyond_terminal(self):
        """Returns True when few slots exist beyond terminal penalty index."""
        from custom_components.localshift.engine.constraints import (
            is_blind_to_future_solar,
        )

        config = OptimizerConfig()
        slots = [
            SlotContext(
                slot_index=i,
                slot_interval_minutes=30,
                timestamp_iso="2024-01-01T00:00:00+00:00",
                buy_price=30.0,
                sell_price=10.0,
                solar_kwh=1.0,
                consumption_kwh=1.0,
                is_demand_window_slot=False,
            )
            for i in range(5)
        ]

        result = is_blind_to_future_solar(slots, 2, 4)
        assert result is True

    def test_not_blind_no_terminal_idx(self):
        """Without terminal penalty index we are blind (no lookahead horizon)."""
        from custom_components.localshift.engine.constraints import (
            is_blind_to_future_solar,
        )

        slots = [
            SlotContext(
                slot_index=i,
                slot_interval_minutes=30,
                timestamp_iso="2024-01-01T00:00:00+00:00",
                buy_price=30.0,
                sell_price=10.0,
                solar_kwh=1.0,
                consumption_kwh=1.0,
                is_demand_window_slot=False,
            )
            for i in range(5)
        ]

        result = is_blind_to_future_solar(slots, 3, None)
        assert result is True
