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

            self_consumption_value = battery_for_load * max(0.0, slot.buy_price)

    # Issue #638: futile cycling penalty.
    # Penalizes grid charging when the charged energy will drain through house load
    # before reaching a useful period (solar surplus or demand window).
    # Formula: grid_import_kWh × (eff_loss + margin) × buy_price × drain_factor
    # The penalty includes efficiency loss plus a margin to discourage marginal cycling.
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

    return ObjectiveTerms(
        import_cost=import_cost,
        export_revenue=export_revenue,
        cycle_penalty=cycle_penalty,
        self_consumption_value=self_consumption_value,
        uncertainty_penalty=uncertainty_penalty,
        switching_penalty=switching_penalty,
        solar_opportunity_penalty=solar_opportunity_penalty,
        futile_cycling_penalty=futile_cycling_penalty,
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
