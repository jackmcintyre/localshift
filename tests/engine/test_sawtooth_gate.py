"""Gate test for the overnight cheap-window sawtooth (Issue #800).

Background
----------
``effective_cheap_price`` is computed for *now* and may be inflated above the
genuinely-cheap percentile base by today's low-solar urgency (so the optimizer will pay
more to pre-charge before *today's* demand window). The DP applies that single scalar to
every slot in the (multi-day) horizon, so *tomorrow night's* slots — priced just under the
inflated threshold but above the real base — get classed as "CHEAP IMPORT WINDOW". A
marginal grid charge then fires and immediately drains through house load before any useful
period: a net-negative "sawtooth" SOC (Issue #800 "overnight SOC floor bounce").

Because the optimizer re-plans every cycle, today's urgency price is only ever legitimately
needed for the *near* (pre-demand-window) slots; far slots are re-evaluated with their own
urgency when they become "now". The fix gates slots at/after the demand-window entry on the
un-inflated ``base_cheap_price`` instead.

These tests reproduce the sawtooth deterministically and assert the fix removes it without
suppressing genuinely-cheap (<= base) overnight charging.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from custom_components.localshift.engine.constraints import (
    _cheap_threshold_for_slot,
    feasible_actions,
)
from custom_components.localshift.engine.core import DPPlanner
from custom_components.localshift.engine.types import (
    OptimizerConfig,
    OptimizerInputs,
    PlannerAction,
    SlotContext,
)

INTERVAL = 30
_START = datetime(2026, 6, 3, 12, 0)  # day-1 noon

# Price bands ($/kWh)
_BASE_CHEAP = 0.08  # genuinely-cheap percentile base
_INFLATED_CHEAP = 0.12  # today's urgency-inflated "now" value
_OVERNIGHT = 0.119  # just under the inflated threshold, well above the real base
_MORNING_PEAK = 0.16
_DW_PEAK = 0.30

_CHARGE_ACTIONS = (PlannerAction.CHARGE_GRID_NORMAL, PlannerAction.CHARGE_GRID_BOOST)


def _price_for(t: datetime) -> float:
    h = t.hour + t.minute / 60.0
    if t.day == 3 and 15.0 <= h < 21.0:
        return _DW_PEAK  # day-1 evening demand window
    if t.day == 3 and 12.0 <= h < 15.0:
        return 0.16  # day-1 pre-DW shoulder (above both thresholds: no pre-charge here)
    if t.day == 3 and h >= 21.0:
        return 0.13  # day-1 late evening
    if t.day == 4 and 0.0 <= h < 6.0:
        return _OVERNIGHT  # day-2 overnight: "cheap" only by the inflated threshold
    if t.day == 4 and 6.0 <= h < 8.0:
        return _MORNING_PEAK  # day-2 morning peak (the drain target)
    return 0.14


def _solar_for(t: datetime) -> float:
    h = t.hour + t.minute / 60.0
    if t.day == 3 and 9.0 <= h < 15.0:
        return 0.5
    # Strong day-2 morning solar refills to target by horizon end, so the
    # end-of-horizon target does NOT motivate the overnight charge — isolating the
    # pure cheap-window arbitrage sawtooth.
    if t.day == 4 and 9.0 <= h < 15.0:
        return 3.0
    return 0.0


def _build_slots(n: int) -> list[SlotContext]:
    slots: list[SlotContext] = []
    for i in range(n):
        t = _START + timedelta(minutes=INTERVAL * i)
        h = t.hour + t.minute / 60.0
        is_dw = t.day == 3 and 15.0 <= h < 21.0
        is_dw_entry = t.day == 3 and abs(h - 15.0) < 1e-9
        slots.append(
            SlotContext(
                slot_index=i,
                timestamp_iso=t.isoformat(),
                slot_interval_minutes=INTERVAL,
                buy_price=_price_for(t),
                sell_price=0.05,
                solar_kwh=_solar_for(t),
                consumption_kwh=0.3,
                is_demand_window_entry=is_dw_entry,
                is_demand_window_slot=is_dw,
            )
        )
    return slots


def _plan(base_cheap_price: float | None):
    slots = _build_slots(56)  # 12:00 day-1 -> 16:00 day-2 (28h)
    config = OptimizerConfig(
        min_soc_pct=10.0,
        max_soc_pct=100.0,
        demand_window_target_soc_pct=95.0,
        optimization_mode="self_consumption",
        effective_cheap_price=_INFLATED_CHEAP,
        base_cheap_price=base_cheap_price,
        target_shortfall_penalty_per_pct=0.03,
        soc_bins=100,
    )
    inputs = OptimizerInputs(
        cycle_id="sawtooth-gate",
        initial_soc_pct=50.0,
        slots=slots,
        config=config,
        all_solcast=[],
    )
    return DPPlanner(config).plan(inputs)


def _dw_entry_idx(decisions) -> int:
    for d in decisions:
        t = datetime.fromisoformat(d.timestamp_iso)
        if t.day == 3 and t.hour == 15 and t.minute == 0:
            return d.slot_index
    raise AssertionError("demand-window entry slot not found in scenario")


def _post_dw_overnight_charges(result):
    """Grid charges in post-DW slots priced above the real base (the sawtooth)."""
    entry = _dw_entry_idx(result.decisions)
    return [
        d
        for d in result.decisions
        if d.slot_index >= entry
        and d.action in _CHARGE_ACTIONS
        and d.buy_price > _BASE_CHEAP
    ]


class TestSawtoothGate:
    """End-to-end DP behaviour for the overnight sawtooth."""

    def test_inflated_threshold_reproduces_sawtooth_when_unguarded(self):
        """Without base gating (base_cheap_price=None) the inflated threshold leaks.

        Documents the bug: post-DW overnight slots (price > base, <= inflated) get
        charged then drained — the sawtooth.
        """
        result = _plan(base_cheap_price=None)
        charges = _post_dw_overnight_charges(result)
        assert charges, (
            "expected the unguarded inflated threshold to produce net-negative "
            "post-DW overnight grid charging (the sawtooth)"
        )
        # All such charges are classed CHEAP_IMPORT_WINDOW, matching the field report.
        assert any(c.reason_code.value == "CHEAP_IMPORT_WINDOW" for c in charges)

    def test_base_gating_eliminates_sawtooth(self):
        """With base gating, no post-DW overnight charge above the real base occurs."""
        result = _plan(base_cheap_price=_BASE_CHEAP)
        charges = _post_dw_overnight_charges(result)
        assert charges == [], (
            "post-DW slots priced above the genuinely-cheap base must not grid charge; "
            f"got charges at slots {[c.slot_index for c in charges]}"
        )

    def test_base_gating_preserves_morning_solar_refill(self):
        """Target is still reached by horizon end via solar (no functional loss)."""
        result = _plan(base_cheap_price=_BASE_CHEAP)
        assert result.success
        # End-of-horizon SOC reaches the target from free morning solar alone.
        assert result.decisions[-1].predicted_soc_pct >= 94.0

    def test_genuinely_cheap_overnight_still_charges(self):
        """A post-DW overnight slot priced <= base is still a feasible charge window.

        The fix must only remove urgency-inflation leakage, not block real cheap charging.
        """
        config = OptimizerConfig(
            optimization_mode="self_consumption",
            effective_cheap_price=_INFLATED_CHEAP,
            base_cheap_price=_BASE_CHEAP,
            max_soc_pct=100.0,
        )
        cheap_slot = SlotContext(
            slot_index=30,
            timestamp_iso="2026-06-04T03:00:00",
            slot_interval_minutes=INTERVAL,
            buy_price=0.07,  # <= base
            sell_price=0.05,
            solar_kwh=0.0,
            consumption_kwh=0.3,
        )
        actions = feasible_actions(
            50.0, cheap_slot, config, slot_idx=30, terminal_penalty_idx=6
        )
        assert PlannerAction.CHARGE_GRID_NORMAL in actions

    def test_negative_base_gates_post_dw_to_sub_zero_only(self):
        """Negative-market base: post-DW charging only when price is at/below it.

        Guards the negative-wholesale regression (a non-positive base must remain an
        active, meaningful threshold rather than disabling the gate or blocking all).
        """
        config = OptimizerConfig(
            optimization_mode="self_consumption",
            effective_cheap_price=_INFLATED_CHEAP,
            base_cheap_price=-0.02,
            max_soc_pct=100.0,
        )

        def _slot(price: float) -> SlotContext:
            return SlotContext(
                slot_index=30,
                timestamp_iso="2026-06-04T03:00:00",
                slot_interval_minutes=INTERVAL,
                buy_price=price,
                sell_price=0.05,
                solar_kwh=0.0,
                consumption_kwh=0.3,
            )

        # A normal positive overnight price is NOT cheap under a negative base.
        actions = feasible_actions(
            50.0, _slot(0.05), config, slot_idx=30, terminal_penalty_idx=6
        )
        assert PlannerAction.CHARGE_GRID_NORMAL not in actions
        # A price at/below the negative base IS a feasible charge window.
        actions = feasible_actions(
            50.0, _slot(-0.03), config, slot_idx=30, terminal_penalty_idx=6
        )
        assert PlannerAction.CHARGE_GRID_NORMAL in actions


class TestCheapThresholdForSlot:
    """Unit tests for the per-slot cheap-threshold selector."""

    def _config(self, base):
        return OptimizerConfig(
            effective_cheap_price=_INFLATED_CHEAP, base_cheap_price=base
        )

    def test_pre_dw_slot_uses_effective_cheap_price(self):
        """Slots before the demand window keep the urgency-aware threshold."""
        config = self._config(_BASE_CHEAP)
        assert (
            _cheap_threshold_for_slot(config, slot_idx=2, terminal_penalty_idx=6)
            == _INFLATED_CHEAP
        )

    def test_post_dw_slot_uses_base_cheap_price(self):
        """Slots at/after the demand window gate on the un-inflated base."""
        config = self._config(_BASE_CHEAP)
        assert (
            _cheap_threshold_for_slot(config, slot_idx=30, terminal_penalty_idx=6)
            == _BASE_CHEAP
        )
        # The DW entry slot itself counts as "at/after".
        assert (
            _cheap_threshold_for_slot(config, slot_idx=6, terminal_penalty_idx=6)
            == _BASE_CHEAP
        )

    def test_none_base_falls_back_to_effective(self):
        """Backward compat: unset base => effective threshold everywhere."""
        config = self._config(None)
        assert (
            _cheap_threshold_for_slot(config, slot_idx=30, terminal_penalty_idx=6)
            == _INFLATED_CHEAP
        )

    def test_no_terminal_penalty_uses_effective(self):
        """With no demand window there is no urgency leak to correct."""
        config = self._config(_BASE_CHEAP)
        assert (
            _cheap_threshold_for_slot(config, slot_idx=30, terminal_penalty_idx=None)
            == _INFLATED_CHEAP
        )

    def test_base_above_effective_never_raises_threshold(self):
        """min() guard: a base above effective must not loosen the post-DW gate."""
        config = OptimizerConfig(
            effective_cheap_price=_BASE_CHEAP, base_cheap_price=_INFLATED_CHEAP
        )
        assert (
            _cheap_threshold_for_slot(config, slot_idx=30, terminal_penalty_idx=6)
            == _BASE_CHEAP
        )
