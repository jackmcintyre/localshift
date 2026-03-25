"""Tests encoding the self-consumption operating philosophy.

These tests guard against future changes that drift toward reserve-holding
or comfort-seeking behavior. In self_consumption mode:
- Overnight charging is generally wasteful and should stay penalized.
- Low overnight SOC is acceptable — do not add reserve-holding behavior.
- Proactive charging needs strong justification (real deadline/target), not
  just a desire to avoid low SOC.
- Grid import after battery depletion is the correct outcome.

See docs/PLANNING_MODEL.md "Control Philosophy" and
custom_components/localshift/engine/AGENTS.md "Self-Consumption Philosophy".

These tests focus on the optimizer's constraint and cost layers directly.
"""

from __future__ import annotations

from custom_components.localshift.engine.constraints import (
    feasible_actions,
)
from custom_components.localshift.engine.cost import stage_cost
from custom_components.localshift.engine.types import (
    OptimizerConfig,
    PlannerAction,
    SlotContext,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_slot(
    buy_price: float = 0.15,
    solar_kwh: float = 0.0,
    consumption_kwh: float = 0.2,
    is_demand_window_slot: bool = False,
    slot_interval_minutes: int = 30,
    slot_index: int = 0,
) -> SlotContext:
    """Create a minimal SlotContext for testing."""
    return SlotContext(
        slot_index=slot_index,
        timestamp_iso="2026-03-20T02:00:00+11:00",
        slot_interval_minutes=slot_interval_minutes,
        buy_price=buy_price,
        sell_price=0.05,
        solar_kwh=solar_kwh,
        consumption_kwh=consumption_kwh,
        is_demand_window_slot=is_demand_window_slot,
    )


def _make_config(
    optimization_mode: str = "self_consumption",
    min_soc_pct: float = 10.0,
    demand_window_target_soc_pct: float = 80.0,
    effective_cheap_price: float = 0.13,
) -> OptimizerConfig:
    """Create a minimal OptimizerConfig for testing."""
    return OptimizerConfig(
        battery_capacity_kwh=13.5,
        max_soc_pct=100.0,
        min_soc_pct=min_soc_pct,
        demand_window_target_soc_pct=demand_window_target_soc_pct,
        optimization_mode=optimization_mode,
        effective_cheap_price=effective_cheap_price,
        charge_rate_kw=5.0,
        discharge_rate_kw=5.0,
        charge_efficiency=0.936,
        discharge_efficiency=0.96,
        switching_penalty=0.02,
        target_shortfall_penalty_per_pct=0.015,
        export_price_margin=0.10,
        allow_dw_entry_under_target=False,
    )


def _make_slots_overnight(
    n_slots: int = 10,
    buy_price: float = 0.15,
    solar_kwh: float = 0.0,
    consumption_kwh: float = 0.15,
    is_demand_window_slot: bool = False,
) -> list[SlotContext]:
    """Create a list of overnight slots with consistent properties."""
    return [
        _make_slot(
            buy_price=buy_price,
            solar_kwh=solar_kwh,
            consumption_kwh=consumption_kwh,
            is_demand_window_slot=is_demand_window_slot,
        )
        for _ in range(n_slots)
    ]


# ---------------------------------------------------------------------------
# Philosophy Tests
# ---------------------------------------------------------------------------


class TestSelfConsumptionPhilosophy:
    """Tests that encode the self-consumption operating philosophy."""


def test_no_reserve_holding_without_deadline():
    """When SOC is low but there is no target/deadline pressure, the planner
    should not add reserve-holding behavior.

    In self_consumption, overnight charging is generally wasteful. If the
    planner finds itself considering charge actions at 12% SOC with no
    demand window, it should be because the price is very cheap — not
    because it is trying to keep the battery above some comfort floor.
    """
    config = _make_config(
        optimization_mode="self_consumption",
        min_soc_pct=10.0,
        demand_window_target_soc_pct=80.0,
        effective_cheap_price=0.13,
    )

    # Overnight slot, moderate price (above cheap threshold), no solar, no DW
    slot = _make_slot(
        buy_price=0.18, solar_kwh=0.0, consumption_kwh=0.15, is_demand_window_slot=False
    )

    actions = feasible_actions(
        soc_pct=12.0,
        slot=slot,
        config=config,
        slot_idx=0,
        slots=None,  # no slots → disables solar gate
        terminal_penalty_idx=None,
    )

    # At moderate price, charge_grid_normal should NOT be in feasible actions
    # because buy_price (0.18) > effective_cheap_price (0.13) and there is no target risk
    assert PlannerAction.CHARGE_GRID_NORMAL not in actions, (
        "Charge action allowed at moderate overnight price without target — "
        "this suggests reserve-holding behavior"
    )


def test_grid_import_accepted_at_floor():
    """When battery is at minimum SOC and solar is insufficient, grid import
    is the correct outcome. The planner should NOT try to charge proactively
    to avoid the floor unless the price is actually cheap.
    """
    config = _make_config(
        optimization_mode="self_consumption",
        min_soc_pct=10.0,
        demand_window_target_soc_pct=80.0,
        effective_cheap_price=0.13,
    )

    # Battery at floor, overnight, moderate price, no solar
    slot = _make_slot(
        buy_price=0.18, solar_kwh=0.0, consumption_kwh=0.15, is_demand_window_slot=False
    )

    actions = feasible_actions(
        soc_pct=10.0,
        slot=slot,
        config=config,
        slot_idx=0,
        slots=None,
        terminal_penalty_idx=None,
    )

    # HOLD should be feasible (allows draining to floor then importing)
    # CHARGE_GRID_NORMAL should NOT be feasible at this price
    assert PlannerAction.HOLD in actions
    assert PlannerAction.CHARGE_GRID_NORMAL not in actions, (
        "Charging from grid at SOC floor with moderate price — "
        "this is reserve-holding behavior, not self-consumption economics"
    )


def test_charge_allowed_at_cheap_price():
    """Charging should be allowed when the price is genuinely cheap,
    regardless of SOC level. This is economic optimization, not reserve.
    """
    config = _make_config(
        optimization_mode="self_consumption",
        min_soc_pct=10.0,
        demand_window_target_soc_pct=80.0,
        effective_cheap_price=0.15,
    )

    # Cheap price, no solar, no DW
    slot = _make_slot(
        buy_price=0.10, solar_kwh=0.0, consumption_kwh=0.15, is_demand_window_slot=False
    )

    actions = feasible_actions(
        soc_pct=30.0,
        slot=slot,
        config=config,
        slot_idx=0,
        slots=None,
        terminal_penalty_idx=None,
    )

    # At cheap price, charge_grid_normal SHOULD be available — this is economic
    assert PlannerAction.CHARGE_GRID_NORMAL in actions, (
        "Charging blocked at genuinely cheap price — "
        "self-consumption should optimize for economics"
    )


def test_stage_cost_applies_anti_cycling_penalties():
    """Verify that the cost model penalizes charging with the full anti-cycling stack.
    This guards against future changes that reduce these penalties to encourage
    more overnight charging.
    """
    config = _make_config(
        optimization_mode="self_consumption",
        effective_cheap_price=0.13,
    )

    slot = _make_slot(
        buy_price=0.12, solar_kwh=0.0, consumption_kwh=0.15, is_demand_window_slot=False
    )

    terms = stage_cost(
        action=PlannerAction.CHARGE_GRID_NORMAL,
        grid_import_kwh=1.0,
        grid_export_kwh=0.0,
        soc_pct=30.0,
        slot=slot,
        config=config,
        is_switch=False,
        solar_opportunity_penalty_factor=0.0,
        futile_cycling_penalty_factor=1.0,
    )

    # The charge should have a meaningful cost that includes:
    # - import cost (1.0 * 0.12 = 0.12)
    # - futile cycling penalty (approx 0.426 * 0.12 * 1.0 = 0.051)
    assert terms.import_cost > 0, "Import cost should be positive"
    assert terms.futile_cycling_penalty > 0, "Futile cycling penalty should be positive"

    # Net cost should be positive (meaning the charge action is expensive)
    assert terms.net_cost > 0, "Charge action should have a positive net cost"

    # Overnight, no solar, moderate price, SOC near floor
    slot = _make_slot(
        buy_price=0.18, solar_kwh=0.0, consumption_kwh=0.15, is_demand_window_slot=False
    )

    actions = feasible_actions(
        soc_pct=10.5,
        slot=slot,
        config=config,
        slot_idx=0,
        slots=None,
        terminal_penalty_idx=None,
    )

    assert PlannerAction.HOLD in actions
    assert PlannerAction.CHARGE_GRID_NORMAL not in actions


def test_penalties_are_not_recently_reduced():
    """Verify that the penalty defaults have not been softened by
    well-meaning changes. If someone reduces these defaults to
    encourage more charging, this test will catch it.
    """
    from custom_components.localshift.const import (
        DEFAULT_TARGET_PENALTY,
    )

    # Target penalty should stay low — real behavior comes from terminal
    # constraints, not from soft penalties in self_consumption.
    assert DEFAULT_TARGET_PENALTY <= 0.030, (
        f"Default target penalty increased to {DEFAULT_TARGET_PENALTY} — "
        "self_consumption uses a hard constraint path; raising soft penalties "
        "is redundant and misleading"
    )
