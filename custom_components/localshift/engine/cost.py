"""Cost computation functions for the DP optimizer."""

from custom_components.localshift.engine.types import (
    ObjectiveTerms,
    OptimizerConfig,
    PlannerAction,
    SlotContext,
)


def stage_cost(
    action: PlannerAction,
    grid_import_kwh: float,
    grid_export_kwh: float,
    slot: SlotContext,
    config: OptimizerConfig,
    *,
    soc_pct: float | None = None,
    is_switch: bool = False,
    solar_opportunity_penalty_factor: float = 0.0,
    futile_cycling_penalty_factor: float = 0.0,
) -> ObjectiveTerms:
    """
    Compute per-slot stage cost terms for an action.

    In self-consumption mode, adds value for battery energy used to cover load.
    This makes the optimizer prefer keeping energy for household use over exporting
    unless the export price exceeds the self-consumption value + margin.

    Args:
        action: The planned action for this slot.
        grid_import_kwh: Grid imports during this slot (kWh).
        grid_export_kwh: Grid exports during this slot (kWh).
        slot: Per-slot context (price, solar, consumption, flags).
        config: Optimizer configuration and constraints.
        soc_pct: Current SOC percentage *before* this slot's transition.
                 When provided, caps self-consumption credit by the
                 battery's physical discharge capacity.
        is_switch: True if this action represents a mode switch from the
                   currently active hardware state.
        solar_opportunity_penalty_factor: Penalty factor for grid charging
            when solar can charge for free later.
        futile_cycling_penalty_factor: Penalty factor for grid charging
            when energy will drain through house load before being useful.

    Returns:
        ObjectiveTerms with all cost components broken down.
    """
    import_cost = grid_import_kwh * slot.buy_price
    export_revenue = grid_export_kwh * slot.sell_price
    cycle_kwh = grid_import_kwh + grid_export_kwh
    cycle_penalty = cycle_kwh * config.cycle_penalty_per_kwh

    # Switching penalty (Issue #524)
    # Adds a one-time cost hurdle to discourage frequent mode flip-flopping.
    switching_penalty = config.switching_penalty if is_switch else 0.0

    # Issue #610: horizon-aware solar opportunity cost
    # Penalizes grid import when significant solar is expected later in the horizon.
    sc_value = (
        max(0.0, slot.buy_price)
        if config.optimization_mode == "self_consumption"
        else 0.0
    )
    full_economic_benefit = import_cost + grid_import_kwh * sc_value
    solar_opportunity_penalty = full_economic_benefit * solar_opportunity_penalty_factor

    # Issue #431: uncertainty penalty for grid charging when horizon is short.
    uncertainty_penalty = 0.0
    if action in (
        PlannerAction.CHARGE_GRID_NORMAL,
        PlannerAction.CHARGE_GRID_BOOST,
    ):
        if config.forecast_horizon_hours < 20.0:
            horizon_penalty_factor = (20.0 - config.forecast_horizon_hours) / 20.0
            uncertainty_penalty = 0.05 * horizon_penalty_factor * grid_import_kwh

    # Calculate self-consumption value (Issue #406)
    # Battery energy used to cover household load has value because it avoids
    # buying from grid at retail price.
    # However, we subtract cycle_penalty_per_kwh to avoid subsidizing marginal cycling.
    # The battery must "earn" the cycle penalty back through the spread.
    self_consumption_value = 0.0
    if config.optimization_mode == "self_consumption":
        net_load = slot.consumption_kwh - slot.solar_kwh
        if net_load > 0:
            battery_for_load = max(0.0, net_load - grid_import_kwh - grid_export_kwh)

            if soc_pct is not None:
                slot_hours = slot.slot_interval_minutes / 60.0
                max_discharge_kwh = config.discharge_rate_kw * slot_hours
                available_kwh = max(
                    0.0,
                    (soc_pct - config.min_soc_pct)
                    / 100.0
                    * config.battery_capacity_kwh,
                )
                max_load_kwh = min(
                    max_discharge_kwh,
                    available_kwh * config.discharge_efficiency,
                )
                battery_for_load = min(battery_for_load, max_load_kwh)

            # Credit the battery at (buy_price - cycle_penalty), not full buy_price.
            # This prevents the credit from subsidizing marginal cycling — the battery
            # must "earn" the cycle penalty back through the spread.
            #
            # EXCEPTION (demand window): covering DW load is a deadline-driven
            # obligation that avoids an expensive demand-charge peak, not marginal
            # arbitrage. Subtracting the cycle penalty here wrongly zeroes the credit
            # whenever cycle_penalty >= DW buy_price, removing all incentive to
            # pre-charge for the DW (the root cause of under-target DW-entry SOC).
            # Credit DW coverage at full retail. The cycle penalty is still charged on
            # the grid import itself, and the futile-cycling / solar-opportunity
            # penalties still guard against wasteful charging. Consistent with the
            # demand-charge deadline rationale in docs/PLANNING_MODEL.md.
            if slot.is_demand_window_slot and config.demand_charge_active:
                sc_multiplier = max(0.0, slot.buy_price)
            else:
                sc_multiplier = max(0.0, slot.buy_price - config.cycle_penalty_per_kwh)
            self_consumption_value = battery_for_load * sc_multiplier

    # Issue #638: futile cycling penalty.
    # Penalizes grid charging when the charged energy will drain through house load
    # before reaching a useful period (solar surplus or demand window).
    # Formula: grid_import_kWh × (eff_loss + margin) × buy_price × drain_factor
    # The penalty includes efficiency loss plus a margin to discourage marginal cycling.
    # PHILOSOPHY NOTE: in self_consumption, overnight charging is generally wasteful
    # and should stay penalized. Do not reduce these penalties to encourage
    # reserve-holding behavior. See docs/PLANNING_MODEL.md "Control Philosophy".
    futile_cycling_penalty = 0.0
    if action in (
        PlannerAction.CHARGE_GRID_NORMAL,
        PlannerAction.CHARGE_GRID_BOOST,
    ):
        # Efficiency loss portion + margin to discourage marginal arbitrage
        # Old formula: eff_loss only (~12.6% of import)
        # New formula: eff_loss + margin (~50% of import) to prevent wasteful cycling
        eff_loss = 1.0 - config.charge_efficiency * config.discharge_efficiency
        futile_cycling_penalty = (
            grid_import_kwh
            * (eff_loss + 0.30)  # eff_loss (~12.6%) + margin (30%)
            * slot.buy_price
            * futile_cycling_penalty_factor
        )

    # P1a: demand-charge penalty.
    # Grid import during the demand window sets an expensive monthly $/kW network
    # peak that is invisible to the spot price. We price it as an elevated cost on
    # DW grid import REGARDLESS of action: charge actions are already forbidden in
    # the DW by feasible_actions(), so the import we are pricing here is the
    # HOLD/self-consumption draw when a depleted battery cannot cover load. This
    # propagates backward through the DP to pre-charge enough before the DW to
    # avoid the draw entirely. The induced pre-charge is justified by a real
    # economic deadline (the demand charge), consistent with the Control Philosophy
    # in docs/PLANNING_MODEL.md. Mirrors Amber SmartShift's elevated-DW-cost rule.
    demand_charge_penalty = 0.0
    if (
        slot.is_demand_window_slot
        and config.demand_charge_active
        and config.demand_window_import_penalty_per_kwh > 0.0
        and grid_import_kwh > 0.0
    ):
        demand_charge_penalty = (
            grid_import_kwh * config.demand_window_import_penalty_per_kwh
        )

    return ObjectiveTerms(
        import_cost=import_cost,
        export_revenue=export_revenue,
        cycle_penalty=cycle_penalty,
        self_consumption_value=self_consumption_value,
        uncertainty_penalty=uncertainty_penalty,
        switching_penalty=switching_penalty,
        solar_opportunity_penalty=solar_opportunity_penalty,
        futile_cycling_penalty=futile_cycling_penalty,
        demand_charge_penalty=demand_charge_penalty,
    )


def terminal_cost(
    final_soc_pct: float,
    target_soc_pct: float,
    config: OptimizerConfig,
) -> float:
    """
    Compute penalty for missing the demand-window SOC target.

    Returns 0 if target is met; positive penalty per % shortfall otherwise.

    Args:
        final_soc_pct: Battery SOC at end of horizon (%).
        target_soc_pct: Target SOC for demand window entry (%).
        config: Optimizer configuration with penalty rates.

    Returns:
        Terminal penalty in currency units (cents).
    """
    shortfall = max(0.0, target_soc_pct - final_soc_pct)
    return shortfall * config.target_shortfall_penalty_per_pct
