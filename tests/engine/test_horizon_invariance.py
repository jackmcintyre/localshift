"""Horizon-invariance gate (Issue #811/#816 — horizon-end myopia).

In strict target mode the shortfall penalty used to be applied at the END of the planning
horizon (``dp[n_slots]``) rather than at the demand-window entry. On a genuinely-cheap night
with insufficient solar to refill by that boundary, the optimizer grid-charged overnight purely
to hit a target at *wherever the horizon happened to be cut*. Because the production horizon is
a rolling ~24-48h window, that boundary lands at a different clock time every cycle, so the
overnight recommendation swung with it (measured: ~16 kWh when the horizon ended pre-solar vs
~5.6 kWh when it extended past the morning solar refill — same near-term scenario).

The fix relocates the strict-mode penalty to the demand-window entry, so near-term decisions no
longer depend on the arbitrary horizon cutoff. These tests hold the near-term scenario fixed and
vary only the horizon length.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from custom_components.localshift.engine.core import DPPlanner
from custom_components.localshift.engine.types import (
    OptimizerConfig,
    OptimizerInputs,
    PlannerAction,
    SlotContext,
)

INTERVAL = 30
_START = datetime(2026, 6, 3, 12, 0)  # day-1 noon
_CHARGE = (PlannerAction.CHARGE_GRID_NORMAL, PlannerAction.CHARGE_GRID_BOOST)


def _price(t: datetime) -> float:
    h = t.hour + t.minute / 60.0
    if t.day == 3 and 15.0 <= h < 21.0:
        return 0.30  # day-1 evening demand window
    if t.day == 3 and 12.0 <= h < 15.0:
        return 0.16
    if t.day == 3 and h >= 21.0:
        return 0.13
    if t.day == 4 and 0.0 <= h < 6.0:
        return 0.06  # day-2 overnight: GENUINELY cheap (<= base), charging is feasible
    if t.day == 4 and 6.0 <= h < 8.0:
        return 0.16
    return 0.14


def _solar(t: datetime) -> float:
    h = t.hour + t.minute / 60.0
    if t.day == 3 and 9.0 <= h < 15.0:
        return 0.5
    if t.day == 4 and 9.0 <= h < 15.0:
        return (
            3.0  # day-2 solar refills to target — only visible to a long-enough horizon
        )
    return 0.0


def _build_slots(n: int) -> list[SlotContext]:
    slots: list[SlotContext] = []
    for i in range(n):
        t = _START + timedelta(minutes=INTERVAL * i)
        h = t.hour + t.minute / 60.0
        is_dw = t.day == 3 and 15.0 <= h < 21.0
        slots.append(
            SlotContext(
                slot_index=i,
                timestamp_iso=t.isoformat(),
                slot_interval_minutes=INTERVAL,
                buy_price=_price(t),
                sell_price=0.05,
                solar_kwh=_solar(t),
                consumption_kwh=0.3,
                is_demand_window_entry=(t.day == 3 and abs(h - 15.0) < 1e-9),
                is_demand_window_slot=is_dw,
            )
        )
    return slots


def _overnight_charge_kwh(n: int) -> float:
    """Day-2 00:00-06:00 grid charge for a horizon of n slots (strict mode)."""
    config = OptimizerConfig(
        min_soc_pct=10.0,
        max_soc_pct=100.0,
        demand_window_target_soc_pct=95.0,
        optimization_mode="self_consumption",
        effective_cheap_price=0.12,
        base_cheap_price=0.08,
        allow_dw_entry_under_target=False,  # strict mode
        target_shortfall_penalty_per_pct=0.03,
        soc_bins=100,
    )
    inputs = OptimizerInputs(
        cycle_id="horizon-invariance",
        initial_soc_pct=50.0,
        slots=_build_slots(n),
        config=config,
        all_solcast=[],
    )
    result = DPPlanner(config).plan(inputs)
    assert result.success
    return sum(
        d.grid_import_kwh
        for d in result.decisions
        if d.action in _CHARGE
        and datetime.fromisoformat(d.timestamp_iso).day == 4
        and datetime.fromisoformat(d.timestamp_iso).hour < 6
    )


# day1 12:00 + 30-min slots: day2 06:00 = slot 36; day2 09:00 = slot 42; day2 16:00 = slot 56
_SHORT = 37  # horizon ends pre-solar (the boundary that used to drive over-charging)
_MED = 43  # horizon ends as solar starts
_LONG = 57  # horizon extends past the full solar refill


class TestHorizonInvariance:
    """Near-term overnight charging must not depend on the arbitrary horizon cutoff."""

    def test_short_horizon_does_not_overcharge_vs_long(self):
        """A pre-solar cutoff must not grid-charge MORE than a horizon that sees the refill.

        This is the core myopia signature: on the old code the short horizon charged ~3x
        the long horizon to hit a phantom end-of-horizon target.
        """
        short = _overnight_charge_kwh(_SHORT)
        long = _overnight_charge_kwh(_LONG)
        assert short <= long + 0.5, (
            f"short-horizon overnight charge ({short:.2f} kWh) exceeds long-horizon "
            f"({long:.2f} kWh) — horizon-end target is leaking into near-term decisions"
        )

    def test_overnight_charge_converges_with_horizon_length(self):
        """Once the horizon is long enough to see the refill, extending it changes nothing."""
        med = _overnight_charge_kwh(_MED)
        long = _overnight_charge_kwh(_LONG)
        assert abs(med - long) <= 0.5, (
            f"overnight charge still depends on horizon length: med={med:.2f} kWh, "
            f"long={long:.2f} kWh"
        )

    def test_no_runaway_overnight_charge(self):
        """Sanity bound: a single battery's overnight charge can't be a multiple of capacity.

        The old myopia drove ~16 kWh into a 13.5 kWh battery (charge + simultaneous load
        coverage) to chase the boundary target.
        """
        assert _overnight_charge_kwh(_SHORT) < 13.5
