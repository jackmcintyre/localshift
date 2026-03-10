"""Test hard constraint enforcement for target SOC in self_consumption mode.

Issue #624: Terminal cost penalty too weak - optimizer accepts shortfall instead of charging.
"""

from custom_components.localshift.engine.optimizer_dp import (
    DPPlanner,
    OptimizerConfig,
    OptimizerInputs,
    PlannerAction,
    SlotContext,
)


def test_self_consumption_mode_enforces_target_as_hard_constraint():
    """Issue #624: In self_consumption mode, target SOC must be reached.

    The optimizer should treat target as a hard constraint, not a soft penalty.
    This means terminal_shortfall_pct should be 0 (or minimal) even if grid charging
    costs more than the old soft penalty rate.

    This test creates a scenario where the old soft penalty would allow shortfall
    but the hard constraint should force charging.
    """
    # Scenario modeled after real user output:
    # - Initial SOC: 42%
    # - Target: 100%
    # - DW starts at slot 10 (long pre-DW window)
    # - Solar brings SOC to ~70% by DW entry
    # - Cheap prices at $0.09 early, then expensive
    # - With soft penalty: optimizer accepts ~30% shortfall
    # - With hard constraint: optimizer MUST charge at cheap prices

    # Build slots: 20 pre-DW slots, 8 DW slots
    # Pre-DW: moderate solar, cheap prices (stay below effective_cheap_price=0.13)
    pre_dw_slots = []
    for i in range(20):
        # Solar varies - strong mid-day, weak early/late
        solar_kw = 1.5 if 5 <= i <= 15 else 0.5
        # Prices: cheap ($0.09-$0.11), stay below 0.13 to allow charging
        buy_price = 0.09 if i < 10 else 0.11
        pre_dw_slots.append(
            SlotContext(
                slot_index=i,
                timestamp_iso=f"2026-01-03T{8 + i // 2}:{(i % 2) * 30:02d}:00",
                slot_interval_minutes=30,
                buy_price=buy_price,
                sell_price=0.02,
                solar_kwh=solar_kw * 0.5,  # 30 min slot
                consumption_kwh=0.5,  # 30 min slot
            )
        )

    # DW slots: minimal solar, expensive
    dw_slots = []
    for i in range(8):
        dw_slots.append(
            SlotContext(
                slot_index=20 + i,
                timestamp_iso=f"2026-01-03T{18 + i // 2}:{(i % 2) * 30:02d}:00",
                slot_interval_minutes=30,
                buy_price=0.18,
                sell_price=0.10,
                solar_kwh=0.1,
                consumption_kwh=0.6,
                is_demand_window_entry=(i == 0),
                is_demand_window_slot=True,
            )
        )

    slots = pre_dw_slots + dw_slots

    config = OptimizerConfig(
        battery_capacity_kwh=13.5,
        demand_window_target_soc_pct=100.0,
        effective_cheap_price=0.13,
        optimization_mode="self_consumption",
        soc_bins=50,
        target_shortfall_penalty_per_pct=0.030,  # Old soft penalty rate
    )
    inputs = OptimizerInputs(
        cycle_id="test-hard-constraint",
        initial_soc_pct=42.0,
        slots=slots,
        config=config,
    )

    result = DPPlanner().plan(inputs)
    assert result.success

    # CRITICAL: Terminal shortfall should be 0 or minimal (< 5%)
    # The optimizer should charge even if it costs more than the soft penalty
    assert result.terminal_shortfall_pct < 5.0, (
        f"Expected near-zero shortfall with hard constraint, got {result.terminal_shortfall_pct}%"
    )

    # There should be grid charging actions in the plan
    charge_actions = [
        d.action
        for d in result.decisions
        if d.action
        in (PlannerAction.CHARGE_GRID_NORMAL, PlannerAction.CHARGE_GRID_BOOST)
    ]
    assert len(charge_actions) > 0, "Expected grid charging to reach target"


def test_hard_constraint_respects_allow_dw_entry_under_target():
    """Issue #624: Hard constraint should respect allow_dw_entry_under_target.

    When allow_dw_entry_under_target=True and solar can reach target during DW,
    optimizer should not force grid charging before DW.
    """
    # Scenario:
    # - Initial SOC: 50%
    # - Target: 80%
    # - Pre-DW (slots 0-3): Cheap prices but low solar
    # - DW (slots 4-7): High solar - can reach target
    # - Expected: No grid charging because solar reaches target in DW

    pre_dw_slots = [
        SlotContext(
            slot_index=i,
            timestamp_iso=f"2026-01-03T{10 + i}:00:00",
            slot_interval_minutes=60,
            buy_price=0.10,
            sell_price=0.05,
            solar_kwh=0.5,
            consumption_kwh=0.5,
        )
        for i in range(4)
    ]
    dw_slots = [
        SlotContext(
            slot_index=4 + i,
            timestamp_iso=f"2026-01-03T{14 + i}:00:00",
            slot_interval_minutes=60,
            buy_price=0.15,
            sell_price=0.05,
            solar_kwh=3.0,  # High solar - can reach target
            consumption_kwh=0.5,
            is_demand_window_entry=(i == 0),
            is_demand_window_slot=True,
        )
        for i in range(4)
    ]
    slots = pre_dw_slots + dw_slots

    config = OptimizerConfig(
        battery_capacity_kwh=13.5,
        demand_window_target_soc_pct=80.0,
        effective_cheap_price=0.12,
        allow_dw_entry_under_target=True,
        optimization_mode="self_consumption",
        soc_bins=20,
        target_shortfall_penalty_per_pct=0.030,
    )
    inputs = OptimizerInputs(
        cycle_id="test-hard-constraint-dw-anywhere",
        initial_soc_pct=50.0,
        slots=slots,
        config=config,
    )

    result = DPPlanner().plan(inputs)
    assert result.success

    # Pre-DW slots should not grid charge since solar can reach target in DW
    for decision in result.decisions[:4]:
        assert decision.action not in (
            PlannerAction.CHARGE_GRID_NORMAL,
            PlannerAction.CHARGE_GRID_BOOST,
        ), (
            f"Pre-DW slot {decision.slot_index} should not grid-charge when solar reaches target in DW"
        )


def test_arbitrage_mode_uses_soft_penalty_not_hard_constraint():
    """Issue #624: Arbitrage mode should use soft penalty, not hard constraint.

    In arbitrage mode, the optimizer should be allowed to skip target if
    it's not economically favorable.
    """
    # Scenario:
    # - Initial SOC: 42%
    # - Target: 100%
    # - High prices everywhere
    # - Expected: Arbitrage mode can accept shortfall (soft penalty)

    slots = [
        SlotContext(
            slot_index=i,
            timestamp_iso=f"2026-01-03T{12 + i}:00:00",
            slot_interval_minutes=60,
            buy_price=0.20,  # expensive
            sell_price=0.10,
            solar_kwh=0.3,
            consumption_kwh=0.5,
            is_demand_window_entry=(i == 4),
            is_demand_window_slot=(i >= 4),
        )
        for i in range(8)
    ]

    config = OptimizerConfig(
        battery_capacity_kwh=13.5,
        demand_window_target_soc_pct=100.0,
        effective_cheap_price=0.13,
        optimization_mode="arbitrage",  # NOT self_consumption
        soc_bins=30,
        target_shortfall_penalty_per_pct=0.030,
    )
    inputs = OptimizerInputs(
        cycle_id="test-arbitrage-soft-penalty",
        initial_soc_pct=42.0,
        slots=slots,
        config=config,
    )

    result = DPPlanner().plan(inputs)
    assert result.success

    # Arbitrage mode may accept shortfall if charging is too expensive
    # (This test just verifies it doesn't crash - we're not asserting shortfall behavior)
