"""Bounded terminal energy value (Issue #811 — horizon-end myopia).

Residual energy at the end of the planning horizon still has value: it displaces
a future grid import after the horizon rolls forward. The legacy terminal logic
priced that residual at exactly zero, so near the horizon end the DP treated
battery energy as worthless — making it too willing to dump value (export at
marginal prices, or forgo cheap storage it could have kept) purely because the
modeled horizon stopped.

The fix adds a small, bounded salvage credit at the horizon boundary
(``dp[n_slots]``):

    credit = usable_kwh(soc) x per_kwh
    per_kwh = min(future_buy_price x DISCOUNT, MAX_PER_KWH)

Bounds that keep it subordinate (never a reserve-hoarder):

1. Only SOC above ``min_soc_pct`` is credited (floor energy is unusable).
2. ``per_kwh`` is at most half the cheapest observed buy price, so charging to
   harvest salvage always loses at least half the outlay — hoarding can never
   pay.
3. ``per_kwh`` is capped absolutely (``TERMINAL_SALVAGE_MAX_PER_KWH``), so even
   pathological tariffs cannot create a reserve-seeking incentive.
4. The credit lives ONLY on the horizon boundary row — the strict-mode
   demand-window entry penalty (``terminal_penalty_by_bin``) is untouched, so
   hard feasibility and target attainment stay dominant by construction.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from custom_components.localshift.const import (
    TERMINAL_SALVAGE_DISCOUNT,
    TERMINAL_SALVAGE_MAX_PER_KWH,
)
from custom_components.localshift.engine.cost import terminal_salvage_value
from custom_components.localshift.engine.core import DPPlanner
from custom_components.localshift.engine.types import (
    OptimizerConfig,
    OptimizerInputs,
    SlotContext,
)

INTERVAL = 30


def _config(**overrides) -> OptimizerConfig:
    defaults = dict(
        min_soc_pct=10.0,
        max_soc_pct=100.0,
        battery_capacity_kwh=13.5,
        discharge_efficiency=0.95,
        demand_window_target_soc_pct=95.0,
        optimization_mode="self_consumption",
        effective_cheap_price=0.12,
        base_cheap_price=0.08,
        allow_dw_entry_under_target=False,
        target_shortfall_penalty_per_pct=0.03,
        soc_bins=100,
    )
    defaults.update(overrides)
    return OptimizerConfig(**defaults)


def _slot(i: int, buy: float, sell: float = 0.05, **kw) -> SlotContext:
    t = datetime(2026, 6, 3, 21, 0) + timedelta(minutes=INTERVAL * i)
    return SlotContext(
        slot_index=i,
        timestamp_iso=t.isoformat(),
        slot_interval_minutes=INTERVAL,
        buy_price=buy,
        sell_price=sell,
        solar_kwh=kw.get("solar_kwh", 0.0),
        consumption_kwh=kw.get("consumption_kwh", 0.3),
        is_demand_window_slot=kw.get("is_demand_window_slot", False),
        is_demand_window_entry=kw.get("is_demand_window_entry", False),
    )


class TestTerminalSalvageValueUnit:
    """The salvage credit formula itself."""

    def test_zero_at_or_below_min_soc(self):
        config = _config()
        assert terminal_salvage_value(10.0, config, 0.20) == 0.0
        assert terminal_salvage_value(5.0, config, 0.20) == 0.0

    def test_monotonic_in_soc(self):
        config = _config()
        low = terminal_salvage_value(30.0, config, 0.20)
        high = terminal_salvage_value(80.0, config, 0.20)
        assert low > 0.0
        assert high > low

    def test_scales_with_future_buy_below_cap(self):
        config = _config()
        # Both prices stay below the cap (buy * 0.5 < MAX_PER_KWH), where the
        # credit is proportional to the future buy price.
        cheap = terminal_salvage_value(50.0, config, 0.06)
        dear = terminal_salvage_value(50.0, config, 0.08)
        assert dear == pytest.approx(cheap * (0.08 / 0.06), rel=1e-6)

    def test_capped_absolutely(self):
        config = _config()
        per_kwh_at_spike = terminal_salvage_value(50.0, config, 0.50) / (
            (50.0 - 10.0) / 100.0 * 13.5 * 0.95
        )
        assert per_kwh_at_spike <= TERMINAL_SALVAGE_MAX_PER_KWH + 1e-12

    def test_charging_for_salvage_never_pays(self):
        """Half-discount: salvage per kWh is always < the cheapest buy price."""
        config = _config()
        for future_buy in (0.06, 0.12, 0.30, 0.60):
            per_kwh = terminal_salvage_value(50.0, config, future_buy) / (
                (50.0 - 10.0) / 100.0 * 13.5 * 0.95
            )
            assert per_kwh < future_buy
            assert per_kwh <= TERMINAL_SALVAGE_MAX_PER_KWH + 1e-12

    def test_zero_when_no_future_price(self):
        config = _config()
        assert terminal_salvage_value(80.0, config, 0.0) == 0.0

    def test_discount_bound(self):
        """per_kwh <= future_buy * DISCOUNT below the absolute cap."""
        config = _config()
        usable = (50.0 - 10.0) / 100.0 * 13.5 * 0.95
        per_kwh = terminal_salvage_value(50.0, config, 0.06) / usable
        assert per_kwh <= 0.06 * TERMINAL_SALVAGE_DISCOUNT + 1e-12


class TestTerminalSalvageBoundary:
    """The horizon-boundary DP row carries the credit; the DW-entry row does not."""

    def _boundary_row(self, slots, **overrides):
        config = _config(**overrides)
        planner = DPPlanner(config)
        inputs = OptimizerInputs(
            cycle_id="salvage-boundary",
            initial_soc_pct=50.0,
            slots=slots,
            config=config,
            all_solcast=[],
        )
        dw_entry_idx = next(
            (i for i, s in enumerate(slots) if s.is_demand_window_entry), None
        )
        soc_grid = [
            config.min_soc_pct
            + (config.max_soc_pct - config.min_soc_pct) * i / (config.soc_bins - 1)
            for i in range(config.soc_bins)
        ]
        dp, penalty_by_bin = planner._initialize_dp_tables(
            len(slots), soc_grid, config, dw_entry_idx, False, inputs, None
        )
        return dp[len(slots)], penalty_by_bin, soc_grid, config

    def test_boundary_credit_negative_and_monotone(self):
        slots = [_slot(i, buy=0.20) for i in range(12)]
        row, _, soc_grid, _ = self._boundary_row(slots)
        costs = [row[b][0] for b in range(len(soc_grid))]
        assert costs[-1] < 0.0  # residual energy has value
        assert all(
            costs[i + 1] <= costs[i] + 1e-12 for i in range(len(costs) - 1)
        )  # more SOC never worth less

    def test_boundary_credit_bounded_by_cap(self):
        slots = [_slot(i, buy=0.20) for i in range(12)]
        row, _, soc_grid, config = self._boundary_row(slots)
        for bin_idx, soc in enumerate(soc_grid):
            usable = max(0.0, soc - config.min_soc_pct) / 100.0 * (
                config.battery_capacity_kwh * config.discharge_efficiency
            )
            assert -row[bin_idx][0] <= usable * TERMINAL_SALVAGE_MAX_PER_KWH + 1e-12

    def test_boundary_zero_when_disabled(self):
        slots = [_slot(i, buy=0.20) for i in range(12)]
        row, _, soc_grid, _ = self._boundary_row(
            slots, terminal_salvage_enabled=False
        )
        assert all(row[b][0] == 0.0 for b in range(len(soc_grid)))

    def test_dw_entry_penalty_unaffected(self):
        """Subordination: the strict-mode DW-entry penalty ignores salvage."""
        slots = [
            _slot(i, buy=0.20, is_demand_window_slot=(6 <= i < 10),
                  is_demand_window_entry=(i == 6))
            for i in range(12)
        ]
        _, penalty_on, _, _ = self._boundary_row(slots)
        _, penalty_off, _, _ = self._boundary_row(
            slots, terminal_salvage_enabled=False
        )
        assert penalty_on  # a DW entry exists in this fixture
        assert penalty_on == penalty_off


class TestTerminalSalvageBehaviour:
    """Plan-level guards: no reserve-hoarding, no near-term decision churn."""

    def _overnight_charge_kwh(self, n: int, salvage: bool) -> float:
        """Horizon-invariance fixture: day-2 00:00-06:00 grid charging."""
        start = datetime(2026, 6, 3, 12, 0)

        def price(t: datetime) -> float:
            h = t.hour + t.minute / 60.0
            if t.day == 3 and 15.0 <= h < 21.0:
                return 0.30
            if t.day == 3 and 12.0 <= h < 15.0:
                return 0.16
            if t.day == 3 and h >= 21.0:
                return 0.13
            if t.day == 4 and 0.0 <= h < 6.0:
                return 0.06
            if t.day == 4 and 6.0 <= h < 8.0:
                return 0.16
            return 0.14

        def solar(t: datetime) -> float:
            h = t.hour + t.minute / 60.0
            if t.day == 3 and 9.0 <= h < 15.0:
                return 0.5
            if t.day == 4 and 9.0 <= h < 15.0:
                return 3.0
            return 0.0

        slots = []
        for i in range(n):
            t = start + timedelta(minutes=INTERVAL * i)
            h = t.hour + t.minute / 60.0
            slots.append(
                SlotContext(
                    slot_index=i,
                    timestamp_iso=t.isoformat(),
                    slot_interval_minutes=INTERVAL,
                    buy_price=price(t),
                    sell_price=0.05,
                    solar_kwh=solar(t),
                    consumption_kwh=0.3,
                    is_demand_window_entry=(t.day == 3 and abs(h - 15.0) < 1e-9),
                    is_demand_window_slot=(t.day == 3 and 15.0 <= h < 21.0),
                )
            )
        config = _config(terminal_salvage_enabled=salvage)
        inputs = OptimizerInputs(
            cycle_id="salvage-behaviour",
            initial_soc_pct=50.0,
            slots=slots,
            config=config,
            all_solcast=[],
        )
        result = DPPlanner(config).plan(inputs)
        assert result.success
        return sum(
            d.grid_import_kwh
            for d in result.decisions
            if d.action.value.startswith("charge_grid")
            and datetime.fromisoformat(d.timestamp_iso).day == 4
            and datetime.fromisoformat(d.timestamp_iso).hour < 6
        )

    def test_no_reserve_hoarding_overnight(self):
        """Salvage must not create charging that only feeds the boundary credit."""
        for n in (37, 43, 57):
            with_salvage = self._overnight_charge_kwh(n, salvage=True)
            without = self._overnight_charge_kwh(n, salvage=False)
            assert with_salvage <= without + 0.05, (
                f"horizon n={n}: salvage added {with_salvage - without:.2f} kWh "
                "overnight charging — reserve-hoarding regression"
            )

    def test_residual_soc_at_horizon_end_not_decreased(self):
        """With salvage the plan never ends with LESS residual energy."""
        with_soc = self._end_soc(57, salvage=True)
        without_soc = self._end_soc(57, salvage=False)
        assert with_soc >= without_soc - 1e-6

    def _end_soc(self, n: int, salvage: bool) -> float:
        start = datetime(2026, 6, 3, 12, 0)

        def price(t: datetime) -> float:
            h = t.hour + t.minute / 60.0
            if t.day == 3 and 15.0 <= h < 21.0:
                return 0.30
            if t.day == 3 and 12.0 <= h < 15.0:
                return 0.16
            if t.day == 3 and h >= 21.0:
                return 0.13
            if t.day == 4 and 0.0 <= h < 6.0:
                return 0.06
            if t.day == 4 and 6.0 <= h < 8.0:
                return 0.16
            return 0.14

        slots = []
        for i in range(n):
            t = start + timedelta(minutes=INTERVAL * i)
            h = t.hour + t.minute / 60.0
            slots.append(
                SlotContext(
                    slot_index=i,
                    timestamp_iso=t.isoformat(),
                    slot_interval_minutes=INTERVAL,
                    buy_price=price(t),
                    sell_price=0.05,
                    solar_kwh=0.5 if (t.day == 3 and 9.0 <= h < 15.0) else 0.0,
                    consumption_kwh=0.3,
                    is_demand_window_entry=(t.day == 3 and abs(h - 15.0) < 1e-9),
                    is_demand_window_slot=(t.day == 3 and 15.0 <= h < 21.0),
                )
            )
        config = _config(terminal_salvage_enabled=salvage)
        inputs = OptimizerInputs(
            cycle_id="salvage-end-soc",
            initial_soc_pct=50.0,
            slots=slots,
            config=config,
            all_solcast=[],
        )
        result = DPPlanner(config).plan(inputs)
        assert result.success
        return result.decisions[-1].predicted_soc_pct
