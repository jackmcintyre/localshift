"""Behavioural scenario tests for demand-charge awareness (P1a).

IMPORTANT FINDING (documented by these tests): the existing optimizer already
pre-charges to fully cover the demand window whenever a feasible pre-DW charging
window exists — driven by the self_consumption_value credit — so on a forecast
plan it already shows ~zero DW grid import. The flat demand penalty therefore
does NOT change the planned action in that case; its job is to *price* the DW
import that remains when pre-charging is INFEASIBLE (e.g. the plan starts mid-DW,
or the pre-DW window is too short). These tests pin both facts so the behaviour
is explicit and regressions are caught.
"""

import pytest

from custom_components.localshift.engine.optimizer_dp import DPPlanner
from custom_components.localshift.engine.types import (
    OptimizerConfig,
    OptimizerInputs,
    SlotContext,
)


def _config(rate: float) -> OptimizerConfig:
    return OptimizerConfig(
        battery_capacity_kwh=13.5,
        charge_rate_kw=3.3,
        boost_charge_rate_kw=5.0,
        solar_charge_rate_kw=5.0,
        discharge_rate_kw=5.0,
        charge_efficiency=0.92,
        discharge_efficiency=0.95,
        min_soc_pct=10.0,
        max_soc_pct=100.0,
        demand_window_target_soc_pct=40.0,
        optimization_mode="self_consumption",
        target_shortfall_penalty_per_pct=0.03,
        cycle_penalty_per_kwh=0.08,
        effective_cheap_price=0.30,
        forecast_horizon_hours=24.0,
        demand_window_import_penalty_per_kwh=rate,
        demand_charge_active=True,
    )


def _feasible_precharge_inputs(rate: float) -> OptimizerInputs:
    """A pre-DW charging window exists, so the battery CAN cover the DW."""
    slots: list[SlotContext] = []
    for i in range(4):  # pre-DW window, charging feasible
        slots.append(
            SlotContext(
                slot_index=i,
                timestamp_iso=f"2026-06-02T{11 + i:02d}:00:00+10:00",
                slot_interval_minutes=60,
                buy_price=0.25,
                sell_price=0.05,
                solar_kwh=0.0,
                consumption_kwh=0.5,
                is_demand_window_slot=False,
            )
        )
    for j in range(6):  # demand window
        slots.append(
            SlotContext(
                slot_index=4 + j,
                timestamp_iso=f"2026-06-02T{15 + j:02d}:00:00+10:00",
                slot_interval_minutes=60,
                buy_price=0.18,
                sell_price=0.05,
                solar_kwh=0.0,
                consumption_kwh=1.8,
                is_demand_window_slot=True,
                is_demand_window_entry=(j == 0),
            )
        )
    return OptimizerInputs(
        cycle_id=f"feasible-{rate}",
        initial_soc_pct=25.0,
        slots=slots,
        config=_config(rate),
    )


def _mid_dw_inputs(rate: float) -> OptimizerInputs:
    """No pre-DW window (plan starts inside the DW) with a near-empty battery, so
    grid import during the DW is FORCED — the case the penalty must price."""
    slots: list[SlotContext] = []
    for j in range(6):
        slots.append(
            SlotContext(
                slot_index=j,
                timestamp_iso=f"2026-06-02T{15 + j:02d}:00:00+10:00",
                slot_interval_minutes=60,
                buy_price=0.18,
                sell_price=0.05,
                solar_kwh=0.0,
                consumption_kwh=0.8,
                is_demand_window_slot=True,
                is_demand_window_entry=(j == 0),
            )
        )
    return OptimizerInputs(
        cycle_id=f"middw-{rate}",
        initial_soc_pct=12.0,
        slots=slots,
        config=_config(rate),
    )


def _in_dw(decision) -> bool:
    # DW spans 15:00-21:00 in these scenarios; identify by the slot's wall-clock hour.
    return 15 <= int(decision.timestamp_iso[11:13]) < 21


def _dw_import(result) -> float:
    return sum(d.grid_import_kwh for d in result.decisions if _in_dw(d))


def test_existing_optimizer_already_covers_dw_when_precharge_feasible():
    """Documents the finding: with a pre-DW window, DW import is ~0 even rate=0."""
    base = DPPlanner().plan(_feasible_precharge_inputs(0.0))
    assert base.success
    assert _dw_import(base) < 0.01  # already covered without any demand penalty


def test_penalty_prices_forced_dw_import():
    """When DW import is FORCED (no pre-DW window), the penalty appears in the
    objective and raises net cost, while the (forced) action is unchanged."""
    base = DPPlanner().plan(_mid_dw_inputs(0.0))
    pen = DPPlanner().plan(_mid_dw_inputs(5.0))

    base_dw_import = _dw_import(base)
    pen_dw_import = _dw_import(pen)

    # Import is forced (battery can't charge in-DW), so it's unchanged...
    assert base_dw_import > 0.5
    assert pen_dw_import == base_dw_import

    # ...but the penalty is now priced into the DW slots' objective terms.
    base_pen = sum(d.objective_terms.demand_charge_penalty for d in base.decisions)
    pen_pen = sum(d.objective_terms.demand_charge_penalty for d in pen.decisions)
    assert base_pen == 0.0
    assert pen_pen > 0.0
    # The priced penalty equals rate * forced DW import.
    assert pen_pen == pytest.approx(5.0 * pen_dw_import, rel=1e-6)
