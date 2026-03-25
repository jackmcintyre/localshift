"""Tests for solar.py functions."""

from datetime import UTC

from custom_components.localshift.engine.solar import (
    can_solar_reach_target,
    can_solar_reach_target_feasible,
    projected_solar_soc_gain_pct,
)
from custom_components.localshift.engine.types import (
    OptimizerConfig,
    OptimizerInputs,
    SlotContext,
)


class TestCanSolarReachTarget:
    """Test can_solar_reach_target function."""

    def test_can_solar_reach_target_with_allow_dw_entry_under_target_true(self):
        """Test that can_solar_reach_target returns True when solar can reach target during DW."""
        config = OptimizerConfig(
            battery_capacity_kwh=13.5,
            demand_window_target_soc_pct=90.0,
            allow_dw_entry_under_target=True,
            solar_charge_rate_kw=5.0,
            charge_efficiency=0.92,
            discharge_efficiency=0.95,
            min_soc_pct=0.0,
        )

        slots = [
            SlotContext(
                slot_index=0,
                timestamp_iso="2024-01-01T12:00:00+00:00",
                slot_interval_minutes=30,
                buy_price=0.12,
                sell_price=0.10,
                solar_kwh=3.0,
                consumption_kwh=0.5,
                is_demand_window_slot=False,
            ),
            SlotContext(
                slot_index=1,
                timestamp_iso="2024-01-01T12:30:00+00:00",
                slot_interval_minutes=30,
                buy_price=0.12,
                sell_price=0.10,
                solar_kwh=3.0,
                consumption_kwh=0.5,
                is_demand_window_slot=False,
            ),
            SlotContext(
                slot_index=2,
                timestamp_iso="2024-01-01T13:00:00+00:00",
                slot_interval_minutes=30,
                buy_price=0.30,
                sell_price=0.25,
                solar_kwh=3.0,
                consumption_kwh=0.5,
                is_demand_window_slot=True,
            ),
            SlotContext(
                slot_index=3,
                timestamp_iso="2024-01-01T13:30:00+00:00",
                slot_interval_minutes=30,
                buy_price=0.30,
                sell_price=0.25,
                solar_kwh=3.0,
                consumption_kwh=0.5,
                is_demand_window_slot=True,
            ),
        ]

        inputs = OptimizerInputs(
            cycle_id="test-cycle",
            config=config,
            slots=slots,
            initial_soc_pct=45.0,
        )

        demand_bounds: dict[str, int | None] = {"entry_idx": 2, "end_idx": 3}

        result = can_solar_reach_target(inputs, slots, config, demand_bounds)
        assert result is True

    def test_can_solar_reach_target_with_allow_dw_entry_under_target_false(self):
        """Test that can_solar_reach_target returns False when allow_dw_entry_under_target is False."""
        config = OptimizerConfig(
            battery_capacity_kwh=13.5,
            demand_window_target_soc_pct=90.0,
            allow_dw_entry_under_target=False,
            solar_charge_rate_kw=5.0,
            charge_efficiency=0.92,
            discharge_efficiency=0.95,
            min_soc_pct=0.0,
        )

        slots = [
            SlotContext(
                slot_index=0,
                timestamp_iso="2024-01-01T12:00:00+00:00",
                slot_interval_minutes=30,
                buy_price=0.12,
                sell_price=0.10,
                solar_kwh=2.0,
                consumption_kwh=0.5,
                is_demand_window_slot=False,
            ),
            SlotContext(
                slot_index=1,
                timestamp_iso="2024-01-01T12:30:00+00:00",
                slot_interval_minutes=30,
                buy_price=0.12,
                sell_price=0.10,
                solar_kwh=2.0,
                consumption_kwh=0.5,
                is_demand_window_slot=False,
            ),
            SlotContext(
                slot_index=2,
                timestamp_iso="2024-01-01T13:00:00+00:00",
                slot_interval_minutes=30,
                buy_price=0.30,
                sell_price=0.25,
                solar_kwh=2.0,
                consumption_kwh=0.5,
                is_demand_window_slot=True,
            ),
            SlotContext(
                slot_index=3,
                timestamp_iso="2024-01-01T13:30:00+00:00",
                slot_interval_minutes=30,
                buy_price=0.30,
                sell_price=0.25,
                solar_kwh=2.0,
                consumption_kwh=0.5,
                is_demand_window_slot=True,
            ),
        ]

        inputs = OptimizerInputs(
            cycle_id="test-cycle",
            config=config,
            slots=slots,
            initial_soc_pct=45.0,
        )

        demand_bounds: dict[str, int | None] = {"entry_idx": 2, "end_idx": 3}

        result = can_solar_reach_target(inputs, slots, config, demand_bounds)
        assert result is False


class TestCanSolarReachTargetFeasible:
    """Test can_solar_reach_target_feasible function."""

    def test_can_solar_reach_target_feasible_returns_true_when_solar_sufficient(self):
        """Test that can_solar_reach_target_feasible returns True when solar can reach target."""
        config = OptimizerConfig(
            battery_capacity_kwh=13.5,
            demand_window_target_soc_pct=90.0,
            allow_dw_entry_under_target=False,
            solar_charge_rate_kw=5.0,
            charge_efficiency=0.92,
            discharge_efficiency=0.95,
            min_soc_pct=0.0,
        )

        slots = [
            SlotContext(
                slot_index=0,
                timestamp_iso="2024-01-01T12:00:00+00:00",
                slot_interval_minutes=30,
                buy_price=0.12,
                sell_price=0.10,
                solar_kwh=3.0,
                consumption_kwh=0.5,
                is_demand_window_slot=False,
            ),
            SlotContext(
                slot_index=1,
                timestamp_iso="2024-01-01T12:30:00+00:00",
                slot_interval_minutes=30,
                buy_price=0.12,
                sell_price=0.10,
                solar_kwh=3.0,
                consumption_kwh=0.5,
                is_demand_window_slot=False,
            ),
            SlotContext(
                slot_index=2,
                timestamp_iso="2024-01-01T13:00:00+00:00",
                slot_interval_minutes=30,
                buy_price=0.30,
                sell_price=0.25,
                solar_kwh=3.0,
                consumption_kwh=0.5,
                is_demand_window_slot=True,
            ),
            SlotContext(
                slot_index=3,
                timestamp_iso="2024-01-01T13:30:00+00:00",
                slot_interval_minutes=30,
                buy_price=0.30,
                sell_price=0.25,
                solar_kwh=3.0,
                consumption_kwh=0.5,
                is_demand_window_slot=True,
            ),
        ]

        inputs = OptimizerInputs(
            cycle_id="test-cycle",
            config=config,
            slots=slots,
            initial_soc_pct=45.0,
        )

        # Terminal penalty index is at DW entry (index 2)
        terminal_penalty_idx = 2

        result = can_solar_reach_target_feasible(
            inputs, slots, config, terminal_penalty_idx
        )
        assert result is True

    def test_can_solar_reach_target_feasible_returns_false_when_solar_insufficient(
        self,
    ):
        """Test that can_solar_reach_target_feasible returns False when solar cannot reach target."""
        config = OptimizerConfig(
            battery_capacity_kwh=13.5,
            demand_window_target_soc_pct=90.0,
            allow_dw_entry_under_target=False,
            solar_charge_rate_kw=5.0,
            charge_efficiency=0.92,
            discharge_efficiency=0.95,
            min_soc_pct=0.0,
        )

        slots = [
            SlotContext(
                slot_index=0,
                timestamp_iso="2024-01-01T12:00:00+00:00",
                slot_interval_minutes=30,
                buy_price=0.12,
                sell_price=0.10,
                solar_kwh=2.0,
                consumption_kwh=0.5,
                is_demand_window_slot=False,
            ),
            SlotContext(
                slot_index=1,
                timestamp_iso="2024-01-01T12:30:00+00:00",
                slot_interval_minutes=30,
                buy_price=0.12,
                sell_price=0.10,
                solar_kwh=2.0,
                consumption_kwh=0.5,
                is_demand_window_slot=False,
            ),
            SlotContext(
                slot_index=2,
                timestamp_iso="2024-01-01T13:00:00+00:00",
                slot_interval_minutes=30,
                buy_price=0.30,
                sell_price=0.25,
                solar_kwh=2.0,
                consumption_kwh=0.5,
                is_demand_window_slot=True,
            ),
            SlotContext(
                slot_index=3,
                timestamp_iso="2024-01-01T13:30:00+00:00",
                slot_interval_minutes=30,
                buy_price=0.30,
                sell_price=0.25,
                solar_kwh=2.0,
                consumption_kwh=0.5,
                is_demand_window_slot=True,
            ),
        ]

        inputs = OptimizerInputs(
            cycle_id="test-cycle",
            config=config,
            slots=slots,
            initial_soc_pct=45.0,
        )

        # Terminal penalty index is at DW entry (index 2)
        terminal_penalty_idx = 2

        result = can_solar_reach_target_feasible(
            inputs, slots, config, terminal_penalty_idx
        )
        assert result is False

    def test_can_solar_reach_target_feasible_not_affected_by_allow_dw_entry_under_target(
        self,
    ):
        """Test that can_solar_reach_target_feasible is not affected by allow_dw_entry_under_target."""
        config_with_gate = OptimizerConfig(
            battery_capacity_kwh=13.5,
            demand_window_target_soc_pct=90.0,
            allow_dw_entry_under_target=True,
            solar_charge_rate_kw=5.0,
            charge_efficiency=0.92,
            discharge_efficiency=0.95,
            min_soc_pct=0.0,
        )

        config_without_gate = OptimizerConfig(
            battery_capacity_kwh=13.5,
            demand_window_target_soc_pct=90.0,
            allow_dw_entry_under_target=False,
            solar_charge_rate_kw=5.0,
            charge_efficiency=0.92,
            discharge_efficiency=0.95,
            min_soc_pct=0.0,
        )

        slots = [
            SlotContext(
                slot_index=0,
                timestamp_iso="2024-01-01T12:00:00+00:00",
                slot_interval_minutes=30,
                buy_price=0.12,
                sell_price=0.10,
                solar_kwh=3.0,
                consumption_kwh=0.5,
                is_demand_window_slot=False,
            ),
            SlotContext(
                slot_index=1,
                timestamp_iso="2024-01-01T12:30:00+00:00",
                slot_interval_minutes=30,
                buy_price=0.12,
                sell_price=0.10,
                solar_kwh=3.0,
                consumption_kwh=0.5,
                is_demand_window_slot=False,
            ),
            SlotContext(
                slot_index=2,
                timestamp_iso="2024-01-01T13:00:00+00:00",
                slot_interval_minutes=30,
                buy_price=0.30,
                sell_price=0.25,
                solar_kwh=3.0,
                consumption_kwh=0.5,
                is_demand_window_slot=True,
            ),
            SlotContext(
                slot_index=3,
                timestamp_iso="2024-01-01T13:30:00+00:00",
                slot_interval_minutes=30,
                buy_price=0.30,
                sell_price=0.25,
                solar_kwh=3.0,
                consumption_kwh=0.5,
                is_demand_window_slot=True,
            ),
        ]

        inputs_with_gate = OptimizerInputs(
            cycle_id="test-cycle",
            config=config_with_gate,
            slots=slots,
            initial_soc_pct=45.0,
        )

        inputs_without_gate = OptimizerInputs(
            cycle_id="test-cycle",
            config=config_without_gate,
            slots=slots,
            initial_soc_pct=45.0,
        )

        # Terminal penalty index is at DW entry (index 2)
        terminal_penalty_idx = 2

        result_with_gate = can_solar_reach_target_feasible(
            inputs_with_gate, slots, config_with_gate, terminal_penalty_idx
        )
        result_without_gate = can_solar_reach_target_feasible(
            inputs_without_gate, slots, config_without_gate, terminal_penalty_idx
        )

        # Both should return True since can_solar_reach_target_feasible is not affected by the gate
        assert result_with_gate is True
        assert result_without_gate is True


class TestProjectedSolarSocGainPct:
    """Tests for projected_solar_soc_gain_pct with simulation-based calculation."""

    def test_projected_solar_gain_respects_charge_rate_and_efficiency(self):
        """Simulation caps gain at solar_charge_rate, not at full solar_kwh.

        Even with 6.0 kWh solar in one slot, the gain is capped by
        solar_charge_rate_kw * slot_hours * efficiency.
        """
        from datetime import datetime

        base = datetime(2026, 3, 20, 10, 0, tzinfo=UTC)
        slots = [
            SlotContext(
                slot_index=0,
                timestamp_iso=base.isoformat(),
                slot_interval_minutes=30,
                buy_price=0.30,
                sell_price=0.05,
                solar_kwh=6.0,
                consumption_kwh=0.1,
                is_demand_window_entry=True,
                is_demand_window_slot=True,
            )
        ]
        config = OptimizerConfig(
            battery_capacity_kwh=13.5,
            charge_rate_kw=5.0,
            solar_charge_rate_kw=1.0,
            discharge_rate_kw=5.0,
            charge_efficiency=0.9,
            demand_window_target_soc_pct=95.0,
            optimization_mode="self_consumption",
            allow_dw_entry_under_target=False,
            switching_penalty=0.02,
            target_shortfall_penalty_per_pct=0.015,
        )

        gain_pct = projected_solar_soc_gain_pct(
            slot_idx=0,
            slots=slots,
            terminal_penalty_idx=1,
            battery_capacity_kwh=config.battery_capacity_kwh,
            initial_soc_pct=60.0,
            config=config,
        )

        assert gain_pct < 10.0
