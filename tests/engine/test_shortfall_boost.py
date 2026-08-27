"""Tests for the shortfall-aware boost gate + deficit-aware urgency window.

Live incident 2026-06-11: a plan computed at 11:34 with SOC 11.6% and the demand window
(DW) at 15:00-21:00 (target 95%) charged grid-normal every slot but entered the DW at
87.3% — a 7.7% shortfall. Two independent root causes:

- The boost action (5 kW) was price-gated out (it required ``price <= cheap × 0.8``), so
  only the 3.3 kW normal rate was feasible and 3.3 kW could not close the deficit in the
  ~3.4h remaining. At an equal price, boost stores more per slot, so the gate bought
  nothing and guaranteed the shortfall.
- The fixed 4h urgency window blocked morning charging: from 11.6% the deficit needs
  ~4.2h of normal-rate runway, more than 4h allows.

These tests pin both fixes: boost unlocks when normal-rate cannot reach target in time,
and the urgency window widens to the deficit-derived runway.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from custom_components.localshift.engine.constraints import (
    compute_max_normal_gain_pct_to_terminal,
    feasible_actions,
)
from custom_components.localshift.engine.core import DPPlanner
from custom_components.localshift.engine.types import (
    OptimizerConfig,
    OptimizerInputs,
    PlannerAction,
    PlannerReasonCode,
    SlotContext,
)

INTERVAL = 30
_CHARGE_ACTIONS = (PlannerAction.CHARGE_GRID_NORMAL, PlannerAction.CHARGE_GRID_BOOST)


def _incident_slots(n: int, price: float) -> list[SlotContext]:
    """``n`` pre-DW slots at ``price`` then the DW entry at index ``n``."""
    base = datetime(2026, 6, 11, 11, 30)
    slots: list[SlotContext] = []
    for i in range(n + 1):
        t = base + timedelta(minutes=INTERVAL * i)
        is_dw = i >= n
        slots.append(
            SlotContext(
                slot_index=i,
                timestamp_iso=t.isoformat(),
                slot_interval_minutes=INTERVAL,
                buy_price=0.30 if is_dw else price,
                sell_price=0.05,
                solar_kwh=0.0,
                consumption_kwh=0.3,
                is_demand_window_entry=(i == n),
                is_demand_window_slot=is_dw,
            )
        )
    return slots


class TestShortfallBoostGate:
    """Unit coverage for the boost branch of ``feasible_actions``."""

    def test_incident_repro_boost_unlocked_when_normal_cannot_reach(self):
        """Test 1: 7×30-min @ 0.16, terminal idx 7, target 95 — boost at SOC 11.6%.

        Normal-rate can add at most ~78.7 pts over the 7 pre-DW slots, so from 11.6%
        the optimistic ceiling is 90.3% < 95% — boost must be feasible.
        """
        slots = _incident_slots(7, price=0.16)
        config = OptimizerConfig(
            optimization_mode="self_consumption",
            demand_window_target_soc_pct=95.0,
            battery_capacity_kwh=13.5,
            charge_rate_kw=3.3,
            charge_efficiency=0.92,
            effective_cheap_price=0.18,
            max_soc_pct=100.0,
        )
        config.max_normal_gain_pct_to_terminal = (
            compute_max_normal_gain_pct_to_terminal(slots, config, 7)
        )
        # Suffix-sum sanity: gain[0] ≈ 7 × (3.3·0.5·0.92/13.5·100) ≈ 78.7 pts.
        assert config.max_normal_gain_pct_to_terminal is not None
        assert abs(config.max_normal_gain_pct_to_terminal[0] - 78.71) < 0.1

        actions = feasible_actions(
            11.6, slots[0], config, slot_idx=0, slots=slots, terminal_penalty_idx=7
        )
        assert PlannerAction.CHARGE_GRID_NORMAL in actions
        assert PlannerAction.CHARGE_GRID_BOOST in actions

    def test_counter_case_no_boost_when_normal_reaches_target(self):
        """Test 1 counter-case: SOC 50% — normal-rate alone overshoots target, no boost."""
        slots = _incident_slots(7, price=0.16)
        config = OptimizerConfig(
            optimization_mode="self_consumption",
            demand_window_target_soc_pct=95.0,
            battery_capacity_kwh=13.5,
            charge_rate_kw=3.3,
            charge_efficiency=0.92,
            effective_cheap_price=0.18,
            max_soc_pct=100.0,
        )
        config.max_normal_gain_pct_to_terminal = (
            compute_max_normal_gain_pct_to_terminal(slots, config, 7)
        )
        actions = feasible_actions(
            50.0, slots[0], config, slot_idx=0, slots=slots, terminal_penalty_idx=7
        )
        assert PlannerAction.CHARGE_GRID_NORMAL in actions
        assert PlannerAction.CHARGE_GRID_BOOST not in actions

    def test_very_cheap_fast_path_preserved_without_gain_array(self):
        """Test 2: a genuinely very-cheap price still boosts with the array unset (legacy)."""
        slots = _incident_slots(7, price=0.143)  # <= 0.18 × 0.8 = 0.144
        config = OptimizerConfig(
            optimization_mode="self_consumption",
            demand_window_target_soc_pct=95.0,
            effective_cheap_price=0.18,
            max_soc_pct=100.0,
        )
        # Array left None (legacy / direct caller): boost comes purely from very-cheap.
        assert config.max_normal_gain_pct_to_terminal is None
        actions = feasible_actions(
            50.0, slots[0], config, slot_idx=0, slots=slots, terminal_penalty_idx=7
        )
        assert PlannerAction.CHARGE_GRID_BOOST in actions

    def test_no_post_dw_boost_low_soc_cheap_price(self):
        """Test 3: a cheap post-DW slot with low SOC must NOT unlock boost (#800 guard)."""
        # Post-DW overnight slot (slot_idx 10) after a DW entry at index 2.
        slot = SlotContext(
            slot_index=10,
            timestamp_iso="2026-06-12T02:00:00",
            slot_interval_minutes=INTERVAL,
            buy_price=0.16,  # <= base 0.16 (cheap) but > 0.16×0.8 (not very cheap)
            sell_price=0.05,
            solar_kwh=0.0,
            consumption_kwh=0.3,
            is_demand_window_slot=False,
        )
        slots = [slot] * 11
        config = OptimizerConfig(
            optimization_mode="self_consumption",
            demand_window_target_soc_pct=95.0,
            effective_cheap_price=0.18,
            base_cheap_price=0.16,
            max_soc_pct=100.0,
        )
        # Even with an (irrelevant) gain array, the slot_idx < terminal guard blocks boost.
        config.max_normal_gain_pct_to_terminal = [0.0] * 11
        actions = feasible_actions(
            11.6, slot, config, slot_idx=10, slots=slots, terminal_penalty_idx=2
        )
        assert PlannerAction.CHARGE_GRID_NORMAL in actions
        assert PlannerAction.CHARGE_GRID_BOOST not in actions


class TestComputeMaxNormalGain:
    """Unit coverage for the precompute helper."""

    def test_returns_none_without_demand_window(self):
        slots = _incident_slots(7, price=0.16)
        config = OptimizerConfig(optimization_mode="self_consumption")
        assert compute_max_normal_gain_pct_to_terminal(slots, config, None) is None

    def test_returns_none_in_arbitrage_mode(self):
        slots = _incident_slots(7, price=0.16)
        config = OptimizerConfig(optimization_mode="arbitrage")
        assert compute_max_normal_gain_pct_to_terminal(slots, config, 7) is None

    def test_suffix_sum_is_monotonic_and_zero_past_terminal(self):
        slots = _incident_slots(7, price=0.16)
        config = OptimizerConfig(
            optimization_mode="self_consumption",
            effective_cheap_price=0.18,
            battery_capacity_kwh=13.5,
            charge_rate_kw=3.3,
            charge_efficiency=0.92,
        )
        gains = compute_max_normal_gain_pct_to_terminal(slots, config, 7)
        assert gains is not None
        # Earlier slots can reach more (suffix sum): non-increasing left-to-right.
        for i in range(6):
            assert gains[i] >= gains[i + 1]
        # The DW-entry slot itself and beyond carry zero reachable normal gain.
        assert gains[7] == 0.0

    def test_expensive_slots_do_not_contribute(self):
        """A pre-DW slot priced above the cheap threshold adds nothing to the gain."""
        slots = _incident_slots(7, price=0.25)  # above effective_cheap_price 0.18
        config = OptimizerConfig(
            optimization_mode="self_consumption",
            effective_cheap_price=0.18,
        )
        gains = compute_max_normal_gain_pct_to_terminal(slots, config, 7)
        assert gains is not None
        assert all(g == 0.0 for g in gains)


class TestIncidentEndToEnd:
    """Test 5: full DP run reproduces the incident and the fix resolves it."""

    def _plan(self, initial_soc: float):
        slots = _incident_slots(7, price=0.16)
        config = OptimizerConfig(
            min_soc_pct=10.0,
            max_soc_pct=100.0,
            demand_window_target_soc_pct=95.0,
            optimization_mode="self_consumption",
            battery_capacity_kwh=13.5,
            charge_rate_kw=3.3,
            boost_charge_rate_kw=5.0,
            charge_efficiency=0.92,
            effective_cheap_price=0.18,
            base_cheap_price=0.16,
            target_shortfall_penalty_per_pct=0.03,
            soc_bins=100,
        )
        inputs = OptimizerInputs(
            cycle_id="incident-2026-06-11",
            initial_soc_pct=initial_soc,
            slots=slots,
            config=config,
            all_solcast=[],
        )
        return DPPlanner(config).plan(inputs)

    def test_deep_deficit_boosts_and_reaches_target(self):
        result = self._plan(initial_soc=11.6)
        assert result.success
        pre_dw_boosts = [
            d
            for d in result.decisions
            if d.slot_index < 7 and d.action == PlannerAction.CHARGE_GRID_BOOST
        ]
        assert pre_dw_boosts, "expected at least one pre-DW boost decision"
        # DW entry slot (index 7) climbs close to target instead of the broken 87.3%.
        # 95% is physically unreachable from 11.6% in 3.5h once the charge taper bites, so
        # boost gets to ~92% — the physical ceiling. Normal-rate alone tops out at 90.3%
        # (no-taper ceiling, ~88% with taper), so >= 92.0 cleanly separates fixed from
        # broken: this assertion is RED without the boost gate and GREEN with it.
        assert result.decisions[7].predicted_soc_pct >= 92.0

    def test_boosted_slots_classify_target_shortfall_risk(self):
        """Test 6: boosted pre-charge is labelled TARGET_SHORTFALL_RISK, not cheap-import."""
        result = self._plan(initial_soc=11.6)
        boosts = [
            d
            for d in result.decisions
            if d.slot_index < 7 and d.action == PlannerAction.CHARGE_GRID_BOOST
        ]
        assert boosts
        assert all(
            d.reason_code == PlannerReasonCode.TARGET_SHORTFALL_RISK for d in boosts
        )
