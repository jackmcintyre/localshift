"""Tests for target-first charge eligibility (2026-06-12).

Live incident 2026-06-12: with the 3pm force-charge guardrail removed, a plan computed
at 09:02 with SOC 16% and the demand window at 17:00 (target 95%) projected DW entry at
48.5%. Root cause: grid charging was feasible ONLY in slots at/below the cheap-percentile
threshold ($0.11), and only ~1.2h of such slot-time preceded the DW — so the #624 hard
target constraint and the shortfall penalty had no feasible path to buy. The plan held at
11.2–13 ¢ midday, then scheduled 13–15.7 ¢ imports all evening off an empty battery. A
0.3 ¢ price wobble around the threshold moved projected DW entry by ~19 SOC points
between two runs 16 minutes apart.

``compute_pre_dw_charge_thresholds`` makes the target fundable: per-slot pre-DW
thresholds = max(legacy cheap threshold, urgency ramp at the slot's own time, the
"water level" — the marginal price of the cheapest sufficient set of pre-DW slots),
clamped to the operator's ``max_precharge_price`` ceiling. Post-DW slots are untouched
(#800 overnight-sawtooth protection).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from custom_components.localshift.engine.constraints import (
    cheap_threshold_for_slot,
    compute_pre_dw_charge_thresholds,
    feasible_actions,
)
from custom_components.localshift.engine.core import DPPlanner
from custom_components.localshift.engine.dp_math import urgency_ramp_price
from custom_components.localshift.engine.types import (
    OptimizerConfig,
    OptimizerInputs,
    PlannerAction,
    SlotContext,
)

INTERVAL = 30
_CHARGE_ACTIONS = (PlannerAction.CHARGE_GRID_NORMAL, PlannerAction.CHARGE_GRID_BOOST)


def _slots(pre_dw_prices: list[float], dw_prices: list[float]) -> list[SlotContext]:
    """30-min slots: ``pre_dw_prices`` then the demand window at ``len(pre_dw_prices)``."""
    base = datetime(2026, 6, 12, 9, 0)
    n_pre = len(pre_dw_prices)
    out: list[SlotContext] = []
    for i, price in enumerate([*pre_dw_prices, *dw_prices]):
        t = base + timedelta(minutes=INTERVAL * i)
        is_dw = i >= n_pre
        out.append(
            SlotContext(
                slot_index=i,
                timestamp_iso=t.isoformat(),
                slot_interval_minutes=INTERVAL,
                buy_price=price,
                sell_price=0.04,
                solar_kwh=0.0,
                consumption_kwh=0.25,
                is_demand_window_entry=(i == n_pre),
                is_demand_window_slot=is_dw,
            )
        )
    return out


def _config(**overrides) -> OptimizerConfig:
    defaults = dict(
        optimization_mode="self_consumption",
        demand_window_target_soc_pct=95.0,
        battery_capacity_kwh=13.5,
        charge_rate_kw=3.3,
        boost_charge_rate_kw=5.0,
        charge_efficiency=0.92,
        effective_cheap_price=0.10,
        base_cheap_price=0.10,
        max_precharge_price=0.20,
        max_soc_pct=100.0,
        min_soc_pct=10.0,
    )
    defaults.update(overrides)
    return OptimizerConfig(**defaults)


class TestComputePreDwChargeThresholds:
    """Unit coverage for the per-slot threshold precompute."""

    def test_water_level_is_marginal_price_of_cheapest_sufficient_set(self):
        """Deficit needs 5 of 12 pre-DW slots; the 5th cheapest price sets the water level.

        SOC 20 → 95 on 13.5 kWh needs 10.125 kWh stored; boost stores 2.3 kWh per 30-min
        slot, so 5 slots suffice: four at $0.10 plus one at $0.12 → water level $0.12.
        Slots 0–3 sit 6–4.5h before the DW — outside the deficit-derived urgency window
        (~4h) where the ramp contributes nothing — so their threshold IS the water level.
        """
        slots = _slots([0.10] * 4 + [0.12] * 4 + [0.16] * 4, [0.30] * 2)
        config = _config()
        thresholds = compute_pre_dw_charge_thresholds(slots, config, 12, 20.0)
        assert thresholds is not None
        for j in range(4):
            assert thresholds[j] == 0.12

    def test_thresholds_never_below_legacy_and_ramp_rises_toward_dw(self):
        slots = _slots([0.10] * 4 + [0.12] * 4 + [0.16] * 4, [0.30] * 2)
        config = _config()
        thresholds = compute_pre_dw_charge_thresholds(slots, config, 12, 20.0)
        assert thresholds is not None
        for j in range(12):
            legacy = cheap_threshold_for_slot(config, j, 12)
            assert thresholds[j] >= legacy
        # Inside the window the ramp lifts later slots above the flat water level.
        assert thresholds[11] > thresholds[0]
        assert thresholds[11] <= config.max_precharge_price

    def test_insufficient_capacity_authorizes_the_full_ceiling(self):
        """When every pre-DW slot together cannot close the deficit, water = ceiling."""
        slots = _slots([0.13, 0.14], [0.30] * 2)  # 2 slots = 4.6 kWh < 11.5 kWh needed
        config = _config()
        thresholds = compute_pre_dw_charge_thresholds(slots, config, 2, 10.0)
        assert thresholds is not None
        assert thresholds[0] == 0.20
        assert thresholds[1] == 0.20

    def test_post_dw_slots_keep_legacy_thresholds(self):
        """#800 protection: slots at/after the DW entry are byte-for-byte legacy."""
        slots = _slots([0.10] * 4, [0.30] * 3)
        config = _config(effective_cheap_price=0.15, base_cheap_price=0.10)
        thresholds = compute_pre_dw_charge_thresholds(slots, config, 4, 10.0)
        assert thresholds is not None
        for j in range(4, 7):
            assert thresholds[j] == cheap_threshold_for_slot(config, j, 4)

    def test_no_deficit_means_no_water_level(self):
        """At/above target only the ramp term remains; far slots stay legacy."""
        slots = _slots([0.10] * 4 + [0.12] * 8, [0.30] * 2)
        config = _config()
        thresholds = compute_pre_dw_charge_thresholds(slots, config, 12, 96.0)
        assert thresholds is not None
        # Slot 0 is 6h out — outside any urgency window — so legacy applies untouched.
        assert thresholds[0] == cheap_threshold_for_slot(config, 0, 12)

    def test_inert_without_dw_mode_or_ceiling(self):
        slots = _slots([0.10] * 4, [0.30] * 2)
        assert compute_pre_dw_charge_thresholds(slots, _config(), None, 20.0) is None
        assert (
            compute_pre_dw_charge_thresholds(
                slots, _config(max_precharge_price=None), 4, 20.0
            )
            is None
        )
        assert (
            compute_pre_dw_charge_thresholds(
                slots, _config(optimization_mode="arbitrage"), 4, 20.0
            )
            is None
        )
        # A ceiling at/below the ramp base can only be inert, never tighten the gate.
        assert (
            compute_pre_dw_charge_thresholds(
                slots, _config(max_precharge_price=0.10), 4, 20.0
            )
            is None
        )

    def test_solar_reduces_required_charge(self):
        """Expected solar gain (accuracy-discounted) shrinks the funded slot set."""
        slots = _slots([0.10] * 4 + [0.12] * 4 + [0.16] * 4, [0.30] * 2)
        for s in slots[:12]:
            s.solar_kwh = 1.5
        config = _config(solar_forecast_accuracy=1.0)
        thresholds = compute_pre_dw_charge_thresholds(slots, config, 12, 20.0)
        assert thresholds is not None
        # 12 × (1.5 − 0.25) kWh ≈ 13.8 kWh stored-equivalent of solar wipes the deficit:
        # no water level, slot 0 (outside the ramp window) stays at legacy.
        assert thresholds[0] == cheap_threshold_for_slot(config, 0, 12)


class TestGateIntegration:
    """The thresholds actually unlock charge actions in feasible_actions."""

    def _attached_config_and_slots(self):
        slots = _slots([0.10] * 4 + [0.12] * 4 + [0.16] * 4, [0.30] * 2)
        config = _config()
        config.pre_dw_charge_thresholds = compute_pre_dw_charge_thresholds(
            slots, config, 12, 20.0
        )
        return config, slots

    def test_water_level_slot_becomes_chargeable(self):
        config, slots = self._attached_config_and_slots()
        # Slot 5 at $0.12 is above the legacy $0.10 gate but at the water level.
        actions = feasible_actions(
            20.0, slots[5], config, slot_idx=5, slots=slots, terminal_penalty_idx=12
        )
        assert any(a in _CHARGE_ACTIONS for a in actions)

    def test_legacy_gate_without_thresholds_still_blocks(self):
        config, slots = self._attached_config_and_slots()
        config.pre_dw_charge_thresholds = None
        actions = feasible_actions(
            20.0, slots[5], config, slot_idx=5, slots=slots, terminal_penalty_idx=12
        )
        assert not any(a in _CHARGE_ACTIONS for a in actions)


class TestEndToEndPlan:
    """DP-level replay of the 2026-06-12 shape: thin spreads, few cheap slots."""

    # Live 09:02 pre-DW buy prices (thinned): only 4 slots at/below the $0.11 gate.
    PRE_DW = [
        0.110,
        0.110,
        0.114,
        0.117,
        0.118,
        0.122,
        0.124,
        0.130,
        0.121,
        0.111,
        0.106,
        0.108,
        0.112,
        0.123,
        0.134,
        0.142,
    ]
    DW = [0.150, 0.156, 0.154, 0.151]

    def _plan(self, max_precharge_price: float | None):
        slots = _slots(self.PRE_DW, self.DW)
        config = _config(
            effective_cheap_price=0.11,
            base_cheap_price=0.11,
            max_precharge_price=max_precharge_price,
            min_cycle_saving=0.25,
            target_shortfall_penalty_per_pct=0.08,
            switching_penalty=0.08,
            soc_bins=100,
        )
        inputs = OptimizerInputs(
            cycle_id="undercharge-2026-06-12",
            initial_soc_pct=10.0,
            slots=slots,
            config=config,
            all_solcast=[],
        )
        return DPPlanner(config).plan(inputs)

    def test_without_ceiling_the_target_is_structurally_unreachable(self):
        """Pin the broken shape so the fix assertion below is meaningful."""
        result = self._plan(max_precharge_price=None)
        assert result.success
        # Only ~4 cheap slots × ~17 pts/slot from 10% → far short of 95%.
        assert result.decisions[16].predicted_soc_pct < 85.0

    def test_with_ceiling_the_plan_funds_the_target(self):
        result = self._plan(max_precharge_price=0.20)
        assert result.success
        # 16 pre-DW slots offer ~37 kWh of boost capacity against a 11.5 kWh deficit;
        # the charge taper above 80% is the only physical drag, so the plan should
        # come within a few points of the 95% target instead of stalling in the 60s.
        assert result.decisions[16].predicted_soc_pct >= 90.0
        # And it must still be funded cheapest-first: no charging in the DW itself.
        assert not any(
            d.action in _CHARGE_ACTIONS for d in result.decisions if d.slot_index >= 16
        )
