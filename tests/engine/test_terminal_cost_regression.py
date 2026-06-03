"""Tests for Issue #816: Optimizer defers grid charging past demand window.

Two root causes:
1. adjusted_solar_gain_pct double-counts within-horizon solar in terminal cost,
   making a real shortfall invisible.
2. can_solar_reach_target() kill switch completely zeroes terminal penalty when
   allow_dw_entry_under_target=True and solar simulation looks promising.

Both cause the optimizer to see no backward incentive to grid-charge before the DW.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from custom_components.localshift.engine.core import DPPlanner
from custom_components.localshift.engine.types import (
    OptimizerConfig,
    OptimizerInputs,
    PlannerAction,
    SlotContext,
)


def _make_shortfall_scenario(
    *,
    initial_soc_pct: float = 14.0,
    solar_kwh_per_slot: float = 0.8,
    consumption_kwh_per_slot: float = 0.4,
    n_pre_dw_slots: int = 6,
    buy_price: float = 0.12,
    allow_dw_entry_under_target: bool = True,
    target_soc: float = 95.0,
    accuracy: float = 0.319,
) -> OptimizerInputs:
    """Build a scenario mirroring the 2026-03-23 real failure:
    - Low initial SOC (14%)
    - Low solar (cloud event — only ~80% of normal)
    - Demand window in 3 hours
    - allow_dw_entry_under_target=True
    - Solar-only trajectory peaks around 82% (well below 95% target)
    """
    base = datetime(2026, 3, 23, 9, 0, tzinfo=timezone.utc)
    slots = []

    # Pre-DW slots with moderate solar and cheap prices
    for i in range(n_pre_dw_slots):
        ts = base + timedelta(minutes=30 * i)
        slots.append(
            SlotContext(
                slot_index=i,
                timestamp_iso=ts.isoformat(),
                slot_interval_minutes=30,
                buy_price=buy_price,
                sell_price=0.02,
                solar_kwh=solar_kwh_per_slot,
                consumption_kwh=consumption_kwh_per_slot,
                is_demand_window_entry=False,
                is_demand_window_slot=False,
            )
        )

    # DW entry slot
    dw_ts = base + timedelta(minutes=30 * n_pre_dw_slots)
    slots.append(
        SlotContext(
            slot_index=n_pre_dw_slots,
            timestamp_iso=dw_ts.isoformat(),
            slot_interval_minutes=30,
            buy_price=0.25,
            sell_price=0.02,
            solar_kwh=solar_kwh_per_slot,
            consumption_kwh=consumption_kwh_per_slot,
            is_demand_window_entry=True,
            is_demand_window_slot=True,
        )
    )

    # A few more DW slots
    for i in range(1, 3):
        ts = dw_ts + timedelta(minutes=30 * i)
        slots.append(
            SlotContext(
                slot_index=n_pre_dw_slots + i,
                timestamp_iso=ts.isoformat(),
                slot_interval_minutes=30,
                buy_price=0.25,
                sell_price=0.02,
                solar_kwh=solar_kwh_per_slot,
                consumption_kwh=consumption_kwh_per_slot,
                is_demand_window_entry=False,
                is_demand_window_slot=True,
            )
        )

    from unittest.mock import Mock

    tracker = Mock()
    tracker.metrics.accuracy = accuracy * 100  # expects 0-100

    config = OptimizerConfig(
        battery_capacity_kwh=13.5,
        charge_rate_kw=3.3,
        discharge_rate_kw=5.0,
        demand_window_target_soc_pct=target_soc,
        optimization_mode="self_consumption",
        allow_dw_entry_under_target=allow_dw_entry_under_target,
        effective_cheap_price=0.12,
        switching_penalty=0.02,
        target_shortfall_penalty_per_pct=0.030,
        soc_bins=40,
    )

    return OptimizerInputs(
        cycle_id="issue-816-test",
        initial_soc_pct=initial_soc_pct,
        slots=slots,
        config=config,
        solar_accuracy_tracker=tracker,
        all_solcast=[],
    )


class TestIssue816DoubleCountingFix:
    """Bug 1: adjusted_solar_gain_pct must NOT be added to effective_soc.

    The DP trajectory already captures within-horizon solar slot-by-slot.
    Adding adjusted_solar_gain_pct on top double-counts the same solar,
    making a real shortfall disappear from the terminal cost.
    """

    def test_terminal_shortfall_reflects_real_soc_not_inflated_by_solar_credit(self):
        """terminal_shortfall_pct must reflect actual projected SOC, not solar-inflated estimate.

        Before the fix: adjusted_solar_gain_pct (e.g. 16.96%) was added to effective_soc,
        making the terminal cost think SOC ~99% when real peak is ~82%.
        After the fix: terminal_shortfall_pct must be > 0 when solar-only trajectory
        cannot reach the target.
        """
        inputs = _make_shortfall_scenario(
            initial_soc_pct=14.0,
            solar_kwh_per_slot=0.8,
            allow_dw_entry_under_target=False,  # disable kill switch to isolate this bug
            buy_price=0.25,  # keep pre-DW price above cheap threshold so no grid import can mask the gap
        )
        result = DPPlanner().plan(inputs)

        assert result.success

        # Solar-only peak is well below 95%; the terminal shortfall must be non-zero
        # Pre-fix: shortfall would appear 0 because solar credit masks the gap.
        # Post-fix: shortfall correctly reflects the real projected SOC gap.
        assert result.terminal_shortfall_pct > 0, (
            f"terminal_shortfall_pct={result.terminal_shortfall_pct:.2f}% must be > 0 "
            f"when solar-only peak is ~82% and target is 95%. "
            f"Double-counting solar in effective_soc inflates the estimate."
        )

    def test_optimizer_schedules_grid_charging_before_dw_when_solar_insufficient(self):
        """When solar cannot reach target, optimizer must schedule grid charging BEFORE the DW.

        Before the fix: solar double-credit made shortfall invisible → no grid charging today.
        After the fix: terminal penalty is real → backward induction pulls charging pre-DW.
        """
        inputs = _make_shortfall_scenario(
            initial_soc_pct=14.0,
            solar_kwh_per_slot=0.8,
            allow_dw_entry_under_target=False,
        )
        result = DPPlanner().plan(inputs)

        assert result.success

        dw_entry_idx = 6  # n_pre_dw_slots=6
        pre_dw_decisions = [d for d in result.decisions if d.slot_index < dw_entry_idx]
        grid_charge_actions = {
            PlannerAction.CHARGE_GRID_NORMAL,
            PlannerAction.CHARGE_GRID_BOOST,
        }
        grid_charges_before_dw = [
            d for d in pre_dw_decisions if d.action in grid_charge_actions
        ]

        assert len(grid_charges_before_dw) > 0, (
            f"Optimizer must schedule grid charging before the DW when solar is insufficient. "
            f"Pre-DW actions: {[d.action for d in pre_dw_decisions]}"
        )


class TestIssue816KillSwitchFix:
    """Bug 2: can_solar_reach_target() kill switch must NOT zero out terminal penalty.

    When allow_dw_entry_under_target=True and solar simulation reaches target during DW,
    the terminal penalty was completely disabled (all SOC bins get cost 0).
    This removes all backward incentive to charge before the DW, even when the actual
    trajectory shows a significant shortfall.
    """

    def test_terminal_shortfall_nonzero_when_allow_dw_entry_under_target_true_and_solar_insufficient(
        self,
    ):
        """With allow_dw_entry_under_target=True, terminal shortfall must still be reported.

        Before the fix: solar kill switch zeroed out penalty → shortfall appeared 0.
        After the fix: terminal penalty active → shortfall reflects real trajectory.
        """
        # This scenario has allow_dw_entry_under_target=True but solar cannot actually
        # reach the 95% target before DW entry.
        inputs = _make_shortfall_scenario(
            initial_soc_pct=14.0,
            solar_kwh_per_slot=0.8,
            allow_dw_entry_under_target=True,  # Kill switch was triggered here
            accuracy=0.319,
        )
        result = DPPlanner().plan(inputs)

        assert result.success

        assert result.terminal_shortfall_pct > 0, (
            f"terminal_shortfall_pct={result.terminal_shortfall_pct:.2f}% must be > 0 "
            f"even when allow_dw_entry_under_target=True, when solar cannot reach target. "
            f"Kill switch was incorrectly zeroing the terminal penalty."
        )

    def test_optimizer_schedules_grid_charging_when_allow_dw_entry_under_target_true(
        self,
    ):
        """When solar is insufficient, must schedule pre-DW grid charging regardless of allow flag.

        Before the fix: kill switch → zero penalty → no backward incentive → all charging tomorrow.
        After the fix: terminal penalty drives charging before the DW.
        """
        inputs = _make_shortfall_scenario(
            initial_soc_pct=14.0,
            solar_kwh_per_slot=0.8,
            allow_dw_entry_under_target=True,
        )
        result = DPPlanner().plan(inputs)

        assert result.success

        dw_entry_idx = 6  # n_pre_dw_slots=6
        pre_dw_decisions = [d for d in result.decisions if d.slot_index < dw_entry_idx]
        grid_charge_actions = {
            PlannerAction.CHARGE_GRID_NORMAL,
            PlannerAction.CHARGE_GRID_BOOST,
        }
        grid_charges_before_dw = [
            d for d in pre_dw_decisions if d.action in grid_charge_actions
        ]

        assert len(grid_charges_before_dw) > 0, (
            f"Optimizer must schedule grid charging before the DW even when "
            f"allow_dw_entry_under_target=True, when solar is insufficient. "
            f"Pre-DW actions: {[d.action for d in pre_dw_decisions]}"
        )

    def test_solar_sufficient_day_does_not_grid_charge(self):
        """When solar IS genuinely sufficient, optimizer should NOT grid charge.

        This regression test ensures the fix doesn't cause unnecessary grid charging
        on sunny days where solar alone can reach the target.
        """
        # Very high solar: each 30-min slot produces 3 kWh → easily reaches 95%
        inputs = _make_shortfall_scenario(
            initial_soc_pct=40.0,
            solar_kwh_per_slot=3.0,  # abundant solar
            consumption_kwh_per_slot=0.3,
            allow_dw_entry_under_target=True,
            accuracy=0.95,
        )
        result = DPPlanner().plan(inputs)

        assert result.success

        # On a sunny day, solar should be able to reach target without grid charging
        # The optimizer should prefer free solar over paid grid charging
        dw_entry_idx = 6  # n_pre_dw_slots=6
        pre_dw_decisions = [d for d in result.decisions if d.slot_index < dw_entry_idx]
        total_grid_import_before_dw = sum(d.grid_import_kwh for d in pre_dw_decisions)

        # Verify solar can actually reach target (diagnostic check)
        assert (
            result.can_solar_reach_target is True
            or result.terminal_shortfall_pct == 0.0
        ), "With abundant solar, target should be reachable without grid charging"

        # On genuinely sunny days the optimizer must not consume paid grid energy
        assert total_grid_import_before_dw == pytest.approx(0.0, abs=1e-6), (
            "Optimizer should NOT import energy from the grid when solar is sufficient. "
            f"Pre-DW grid import total: {total_grid_import_before_dw:.3f} kWh"
        )


class TestIssue816DiagnosticsAfterFix:
    """After the fix, diagnostic fields must accurately reflect the real trajectory."""

    def test_diagnostic_fields_present(self):
        """Key diagnostic fields should be present for debugging and display."""
        inputs = _make_shortfall_scenario(
            initial_soc_pct=14.0,
            solar_kwh_per_slot=0.8,
            allow_dw_entry_under_target=False,
        )
        result = DPPlanner().plan(inputs)

        assert result.success
        # Core diagnostics should be present
        assert result.accuracy_discount_factor is not None
        assert result.peak_soc_pct is not None
        assert result.dw_entry_soc_pct is not None

    def test_dw_entry_soc_matches_actual_decision_trajectory(self):
        """dw_entry_soc_pct must match the predicted SOC in the decision at DW entry.

        Before the fix, effective_soc was inflated but dw_entry_soc from decisions
        was correct — creating a contradiction. After the fix, both should align.
        """
        inputs = _make_shortfall_scenario(
            initial_soc_pct=14.0,
            solar_kwh_per_slot=0.8,
            allow_dw_entry_under_target=False,
        )
        result = DPPlanner().plan(inputs)

        assert result.success

        if result.dw_entry_soc_pct is not None:
            # Find the DW entry decision (slot_index >= n_pre_dw_slots=6)
            dw_entry_decisions = [d for d in result.decisions if d.slot_index >= 6]
            if dw_entry_decisions:
                actual_dw_soc = dw_entry_decisions[0].predicted_soc_pct
                assert abs(result.dw_entry_soc_pct - actual_dw_soc) < 2.0, (
                    f"dw_entry_soc_pct={result.dw_entry_soc_pct:.2f}% must be close to "
                    f"actual DW entry SOC={actual_dw_soc:.2f}% in decisions. "
                    f"Large gap indicates solar inflation is still present."
                )
