"""Charge-rate taper (CV-phase) modelling for grid charging.

A Powerwall delivers near-constant power up to a "knee" SOC, then enters the
constant-voltage phase where charge power falls toward zero as it approaches full.
The optimizer previously modelled a flat charge rate all the way to ``max_soc_pct``,
so it believed it could add the final ~15-20% as fast as the bulk-charge region. That
produced over-optimistic last-minute top-ups that physically fell short of target
(the rate is lower than expected as the battery fills).

These tests pin the taper: identical to the old flat model below the knee (backward
compatible), strictly more conservative above it, monotonic, and never predicting an
infinitely-slow final approach (the ``charge_taper_min_factor`` floor).
"""

from __future__ import annotations

from custom_components.localshift.engine.transitions import transition
from custom_components.localshift.engine.types import (
    OptimizerConfig,
    PlannerAction,
    SlotContext,
)

_CAP = 13.5
_EFF = 0.92


def _config(**overrides) -> OptimizerConfig:
    base = dict(
        battery_capacity_kwh=_CAP,
        charge_efficiency=_EFF,
        boost_charge_rate_kw=5.0,
        charge_rate_kw=3.3,
        min_soc_pct=10.0,
        max_soc_pct=100.0,
        charge_taper_start_pct=80.0,
        charge_taper_min_factor=0.2,
    )
    base.update(overrides)
    return OptimizerConfig(**base)


def _slot(minutes: int = 30, *, solar: float = 0.0, load: float = 0.3) -> SlotContext:
    return SlotContext(
        slot_index=0,
        timestamp_iso="2026-06-08T14:00:00",
        slot_interval_minutes=minutes,
        buy_price=0.10,
        sell_price=0.05,
        solar_kwh=solar,
        consumption_kwh=load,
    )


def _charge(soc: float, config: OptimizerConfig, *, boost: bool = True, **slot_kw):
    action = (
        PlannerAction.CHARGE_GRID_BOOST if boost else PlannerAction.CHARGE_GRID_NORMAL
    )
    return transition(soc, action, _slot(**slot_kw), config)


def _flat_next_soc(soc: float, rate_kw: float, minutes: int, config: OptimizerConfig):
    """The pre-taper model: flat rate, stored = rate * eff * hours."""
    stored = rate_kw * config.charge_efficiency * (minutes / 60.0)
    return soc + stored / config.battery_capacity_kwh * 100.0


class TestTaperBelowKnee:
    """Below the knee the taper must be a no-op (exact backward compatibility)."""

    def test_charge_entirely_below_knee_matches_flat_model(self):
        config = _config()
        next_soc, _, _ = _charge(50.0, config)
        assert next_soc == _flat_next_soc(50.0, 5.0, 30, config)

    def test_charge_ending_just_below_knee_unchanged(self):
        config = _config()
        # 60% + (5kW boost, 30min) flat would land ~77% — still under the 80% knee.
        next_soc, _, _ = _charge(60.0, config)
        assert next_soc == _flat_next_soc(60.0, 5.0, 30, config)
        assert next_soc < config.charge_taper_start_pct


class TestTaperAboveKnee:
    """Crossing the knee must derate the charge relative to the flat model."""

    def test_crossing_knee_is_more_conservative(self):
        config = _config()
        # The live morning slot: 73.8% -> the flat model predicted ~90.8%.
        tapered, _, _ = _charge(73.8, config)
        flat = _flat_next_soc(73.8, 5.0, 30, config)
        assert tapered < flat - 1.0  # at least a full SOC point lower
        assert tapered > 73.8  # but it still charges

    def test_deeper_into_taper_derates_harder(self):
        config = _config()
        # Higher starting SOC sits deeper in the CV region -> larger shortfall vs flat.
        gap_low, _, _ = _charge(78.0, config)
        gap_high, _, _ = _charge(90.0, config)
        shortfall_low = _flat_next_soc(78.0, 5.0, 30, config) - gap_low
        # flat from 90% clips at 100; compare against the uncapped flat projection.
        shortfall_high = _flat_next_soc(90.0, 5.0, 30, config) - gap_high
        assert shortfall_high > shortfall_low

    def test_grid_import_tracks_reduced_charge(self):
        config = _config()
        # Less energy actually stored => less grid drawn for charging than the flat model.
        _, tapered_import, _ = _charge(78.0, config, load=0.0)
        flat_stored = 5.0 * config.charge_efficiency * 0.5
        flat_import = flat_stored / config.charge_efficiency
        assert tapered_import < flat_import


class TestTaperBounds:
    """Monotonicity, ceiling, and the non-zero rate floor."""

    def test_never_exceeds_max_soc(self):
        config = _config()
        for start in (95.0, 98.0, 99.5):
            next_soc, _, _ = _charge(start, config)
            assert next_soc <= config.max_soc_pct + 1e-9

    def test_still_charges_near_full(self):
        """The min-factor floor keeps a positive rate so target stays reachable."""
        config = _config()
        next_soc, grid_import, _ = _charge(99.0, config, load=0.0)
        assert next_soc > 99.0
        assert grid_import > 0.0

    def test_normal_rate_also_tapers(self):
        config = _config()
        tapered, _, _ = _charge(85.0, config, boost=False)
        flat = _flat_next_soc(85.0, 3.3, 30, config)
        assert tapered < flat

    def test_higher_min_factor_charges_faster(self):
        """A gentler taper (higher floor) stores more above the knee."""
        soft = _config(charge_taper_min_factor=0.6)
        hard = _config(charge_taper_min_factor=0.1)
        soft_soc, _, _ = _charge(85.0, soft, load=0.0)
        hard_soc, _, _ = _charge(85.0, hard, load=0.0)
        assert soft_soc > hard_soc
