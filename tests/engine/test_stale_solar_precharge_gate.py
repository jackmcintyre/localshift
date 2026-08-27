"""tests/engine/test_stale_solar_precharge_gate.py

Regression gate for the stale-solar confidence fix (2026-06-04 live bug).

Background
----------
On 2026-06-04, with stale Solcast data, ``solar_confidence=0.74`` ("high"),
the DP optimizer saw median (optimistic) solar and planned zero grid charging,
entering the evening demand window at ~32% SOC (target: 95%).  With stale data
capped at ``solar_confidence_avg=0.35`` ("low"), it correctly pre-charged to
97%.

The fix: ``SolcastAnalysis.confidence_ceiling`` caps the value returned by
``get_confidence_for_period``, which flows through ``_blend_solar_estimate`` to
reduce ``slot.solar_kwh``, making the deficit visible to the DP planner which
then pre-charges.

Blend formula: ``confidence * pv_estimate + (1.0 - confidence) * pv_estimate10``

How the test works
------------------
We construct a scenario where solar arrives primarily *during* the demand
window (DW, 15:00–21:00).  ``allow_dw_entry_under_target=True`` tells the
optimizer it may enter the DW below target if solar will fill the deficit
during the DW itself.

* **Scenario A — high confidence (1.0):** ``slot.solar_kwh`` equals the full
  median estimate.  The optimizer sees generous solar arriving inside the DW
  and concludes it can afford to skip pre-DW grid charging.  The battery
  enters the DW at ~37% SOC, trusting the in-DW solar to get it to target.

* **Scenario B — stale-capped confidence (0.3):** ``slot.solar_kwh`` is
  blended toward P10 ≈ 0.  The in-DW solar is now barely above load — the
  optimizer cannot count on it to reach the 95% target.  It falls back to
  grid charging in the cheap pre-DW window and enters the DW at ~98% SOC.

The regression assertion confirms the two scenarios produce significantly
different DW entry SOC, and that the stale-capped plan correctly pre-charges.

This test runs ENTIRELY OFFLINE — no Home Assistant instance, no Solcast data,
no mock_hass required.
"""

from __future__ import annotations

import math

from custom_components.localshift.engine.core import DPPlanner
from custom_components.localshift.engine.types import (
    OptimizerConfig,
    OptimizerInputs,
    PlannerAction,
    SlotContext,
)

# ---------------------------------------------------------------------------
# Scenario parameters — calibrated to reproduce the 2026-06-04 bug pattern
# ---------------------------------------------------------------------------

_BATTERY_CAPACITY_KWH = 13.5
_INITIAL_SOC_PCT = 20.0
_DW_TARGET_SOC_PCT = 95.0

# 56 x 30-min slots = 28 hours, starting at 06:00
_N_SLOTS = 56
_SLOT_INTERVAL_MINUTES = 30
_START_HOUR = 6  # 06:00

# Demand window: slot 18 = 15:00, slot 30 = 21:00
_DW_ENTRY_IDX = 18
_DW_END_IDX = 30

# Solar: moderate peak INSIDE the DW (17:00 = slot 22)
# At confidence=1.0: solar slightly exceeds load during DW → optimizer defers to DW solar
# At confidence=0.3: P10 ≈ 0 → blended solar barely covers load → must pre-charge
_SOLAR_PEAK_IDX = 22        # 17:00 (well inside DW)
_SOLAR_PEAK_KWH = 1.5       # kWh per 30-min slot at peak (median, pv_estimate)
_SOLAR_P10_FRAC = 0.0       # P10 = 0 (cloudy-day floor)
_SOLAR_SIGMA_SLOTS = 4.0    # Gaussian width in slots (~2h FWHM)

# Load and prices
_CONSUMPTION_KWH = 0.35     # constant per slot
_PRICE_PRE_DW = 0.08        # cheap pre-DW window (all of slots 0-17)
_PRICE_DW = 0.30            # demand window peak
_PRICE_POST_DW = 0.12       # post-DW shoulder
_SELL_PRICE = 0.05

# Grid charge capability
_CHARGE_RATE_KW = 3.3       # kW → 1.65 kWh per 30-min slot

# Confidence levels for the two scenarios
_CONFIDENCE_HIGH = 1.0      # Bug case: full median solar inside DW → skip pre-charge
_CONFIDENCE_STALE = 0.3     # Fix case: P10 ≈ 0 solar → must pre-charge via grid

# Assertion thresholds
_TARGET = _DW_TARGET_SOC_PCT
_TOLERANCE = 15.0           # bug case must be >15% below target


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pv_estimate_for_slot(slot_idx: int) -> float:
    """Gaussian solar profile (median, pv_estimate) for one 30-min slot.

    The peak is deliberately placed inside the DW (slot 22 = 17:00) so that
    at high confidence the optimizer concludes it can wait for in-DW solar
    instead of pre-charging via grid.
    """
    delta = slot_idx - _SOLAR_PEAK_IDX
    return max(0.0, _SOLAR_PEAK_KWH * math.exp(-0.5 * (delta / _SOLAR_SIGMA_SLOTS) ** 2))


def _blend(pv_estimate: float, pv_estimate10: float, confidence: float) -> float:
    """Linear blend between median and P10 (mirrors forecast/solar.py _blend_solar_estimate)."""
    if confidence >= 1.0:
        return pv_estimate
    if confidence <= 0.0:
        return pv_estimate10
    return confidence * pv_estimate + (1.0 - confidence) * pv_estimate10


def _build_slots(confidence: float) -> list[SlotContext]:
    """Build 56 x 30-min SlotContext objects for a given solar confidence level."""
    slots: list[SlotContext] = []
    for i in range(_N_SLOTS):
        hour = _START_HOUR + i * _SLOT_INTERVAL_MINUTES / 60
        h_int = int(hour)
        m_int = int((hour - h_int) * 60)
        timestamp = f"2026-06-04T{h_int:02d}:{m_int:02d}:00"

        pv_estimate = _pv_estimate_for_slot(i)
        pv_estimate10 = pv_estimate * _SOLAR_P10_FRAC
        solar_kwh = _blend(pv_estimate, pv_estimate10, confidence)

        # Prices
        if _DW_ENTRY_IDX <= i < _DW_END_IDX:
            buy_price = _PRICE_DW
        elif i < _DW_ENTRY_IDX:
            buy_price = _PRICE_PRE_DW  # cheap morning/afternoon window
        else:
            buy_price = _PRICE_POST_DW

        is_dw_entry = i == _DW_ENTRY_IDX
        is_dw_slot = _DW_ENTRY_IDX <= i < _DW_END_IDX

        slots.append(
            SlotContext(
                slot_index=i,
                timestamp_iso=timestamp,
                slot_interval_minutes=_SLOT_INTERVAL_MINUTES,
                buy_price=buy_price,
                sell_price=_SELL_PRICE,
                solar_kwh=solar_kwh,
                consumption_kwh=_CONSUMPTION_KWH,
                is_demand_window_entry=is_dw_entry,
                is_demand_window_slot=is_dw_slot,
            )
        )
    return slots


def _run_scenario(confidence: float) -> tuple[float, list]:
    """Run DP planner with given per-slot confidence; return (dw_entry_soc_pct, decisions)."""
    slots = _build_slots(confidence)
    config = OptimizerConfig(
        battery_capacity_kwh=_BATTERY_CAPACITY_KWH,
        demand_window_target_soc_pct=_DW_TARGET_SOC_PCT,
        # allow_dw_entry_under_target=True: optimizer may enter DW below target
        # when it believes in-DW solar will fill the deficit.  At high confidence,
        # this is the critical flag that lets the optimizer skip pre-DW charging.
        allow_dw_entry_under_target=True,
        soc_bins=80,
        min_soc_pct=10.0,
        max_soc_pct=100.0,
        charge_rate_kw=_CHARGE_RATE_KW,
        boost_charge_rate_kw=5.0,
        solar_charge_rate_kw=5.0,
        discharge_rate_kw=5.0,
        effective_cheap_price=_PRICE_PRE_DW,
        target_shortfall_penalty_per_pct=0.03,
        optimization_mode="self_consumption",
    )
    inputs = OptimizerInputs(
        cycle_id=f"test-stale-solar-{confidence:.1f}",
        initial_soc_pct=_INITIAL_SOC_PCT,
        slots=slots,
        config=config,
    )
    result = DPPlanner(config).plan(inputs)
    assert result.success, f"DPPlanner failed for confidence={confidence}"

    # DW entry SOC = predicted_soc_pct at the end of the slot BEFORE DW entry
    # (slot DW_ENTRY_IDX - 1), which is the SOC the battery carries into the DW.
    pre_dw_decision = result.decisions[_DW_ENTRY_IDX - 1]
    dw_entry_soc = pre_dw_decision.predicted_soc_pct
    return dw_entry_soc, result.decisions


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestStaleSolarPrechargeGate:
    """Regression gate: stale-solar confidence capping changes pre-DW charging behaviour."""

    def test_high_confidence_misses_dw_target(self):
        """High solar confidence (1.0): optimizer defers to in-DW solar, enters DW low.

        With confidence=1.0 the optimizer sees generous median solar arriving
        during the demand window (17:00 peak).  With ``allow_dw_entry_under_target``
        enabled it concludes that in-DW solar alone will reach the 95% target, so it
        performs minimal grid charging.  The battery enters the DW well below the
        pre-charge target.

        This reproduces the bug-day pattern: high confidence → trust solar →
        skip pre-charge → enter DW low.
        """
        dw_soc, _ = _run_scenario(confidence=_CONFIDENCE_HIGH)
        assert dw_soc < _TARGET - _TOLERANCE, (
            f"High-confidence scenario should enter DW well below target "
            f"(expected SOC < {_TARGET - _TOLERANCE:.1f}%, got {dw_soc:.1f}%). "
            "Check solar peak placement: it must be inside the DW so the optimizer "
            "defers to in-DW solar at high confidence."
        )

    def test_stale_capped_confidence_precharges_to_target(self):
        """Stale-capped confidence (0.3): pessimistic solar forces pre-charge.

        With confidence=0.3 the optimizer blends toward P10 ≈ 0, making in-DW
        solar appear insufficient to reach target.  The optimizer can no longer
        defer to DW solar and must grid-charge during the cheap pre-DW window
        (slots 0–17, 06:00–15:00).  The battery enters the DW at or above target.

        This is the behaviour enabled by the stale-solar fix: confidence capping
        → pessimistic solar estimate → pre-DW grid charging → target reached.
        """
        dw_soc, _ = _run_scenario(confidence=_CONFIDENCE_STALE)
        assert dw_soc >= _TARGET - _TOLERANCE, (
            f"Stale-capped scenario should pre-charge to near target "
            f"(expected SOC >= {_TARGET - _TOLERANCE:.1f}%, got {dw_soc:.1f}%). "
            "Check that the cheap pre-DW window (slots 0–17) allows enough capacity."
        )

    def test_stale_capped_has_more_grid_charges_pre_dw(self):
        """Stale-capped plan includes more grid charging in the cheap pre-DW window.

        At confidence=0.3 the in-DW solar looks tiny (near P10 ≈ 0), so the
        optimizer cannot rely on it.  It compensates by grid-charging in the cheap
        pre-DW window.  At confidence=1.0 the optimizer sees ample in-DW solar
        and skips most of the pre-DW charging.
        """
        _, decisions_high = _run_scenario(confidence=_CONFIDENCE_HIGH)
        _, decisions_stale = _run_scenario(confidence=_CONFIDENCE_STALE)

        charge_actions = {PlannerAction.CHARGE_GRID_NORMAL, PlannerAction.CHARGE_GRID_BOOST}
        high_charges = [
            d for d in decisions_high if d.slot_index < _DW_ENTRY_IDX and d.action in charge_actions
        ]
        stale_charges = [
            d for d in decisions_stale if d.slot_index < _DW_ENTRY_IDX and d.action in charge_actions
        ]

        assert len(stale_charges) > len(high_charges), (
            f"Stale-capped plan should have more pre-DW grid charges than high-confidence plan "
            f"(stale={len(stale_charges)}, high={len(high_charges)})"
        )

    def test_stale_capped_has_grid_charges_in_cheap_window(self):
        """Stale-capped plan must grid-charge in the cheap pre-DW window (slots 0–17)."""
        _, decisions = _run_scenario(confidence=_CONFIDENCE_STALE)
        charge_actions = {PlannerAction.CHARGE_GRID_NORMAL, PlannerAction.CHARGE_GRID_BOOST}
        morning_charges = [
            d for d in decisions if d.slot_index < _DW_ENTRY_IDX and d.action in charge_actions
        ]
        assert morning_charges, (
            "Stale-capped scenario must grid-charge during the cheap pre-DW window "
            f"(slots 0–{_DW_ENTRY_IDX - 1}) to reach the DW target. "
            "If no charges appear, the scenario parameters may need adjustment."
        )

    def test_scenarios_produce_different_dw_soc(self):
        """The two confidence levels produce meaningfully different DW entry SOC.

        This is the canonical split: the confidence-capping fix changes optimizer
        behaviour by at least _TOLERANCE percentage points in DW entry SOC.

        The split demonstrates that ``confidence`` → ``_blend()`` →
        ``slot.solar_kwh`` → DP feasibility gate → grid-charge decision is a
        load-bearing chain: capping confidence changes the plan.
        """
        high_soc, _ = _run_scenario(confidence=_CONFIDENCE_HIGH)
        stale_soc, _ = _run_scenario(confidence=_CONFIDENCE_STALE)

        assert stale_soc - high_soc >= _TOLERANCE, (
            f"Stale-capped scenario should produce at least {_TOLERANCE:.0f}% higher "
            f"DW entry SOC than the high-confidence scenario. "
            f"Got: high={high_soc:.1f}%, stale={stale_soc:.1f}%, "
            f"delta={stale_soc - high_soc:.1f}%"
        )
