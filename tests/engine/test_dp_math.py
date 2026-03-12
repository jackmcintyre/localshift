"""Tests for dp_math module — SOC grid, bin mapping, interpolation, simulation."""

from datetime import datetime

import pytest

from custom_components.localshift.engine.dp_math import (
    _build_soc_grid,
    _interpolate_cost_to_soc,
    _map_soc_to_bin,
    _simulate_max_soc_in_demand_window,
    _simulate_solar_only_terminal_soc,
)
from custom_components.localshift.engine.types import OptimizerConfig, SlotContext


def test_build_soc_grid_defaults():
    """SOC grid should include boundaries with default 21 bins."""
    config = OptimizerConfig(min_soc_pct=10, max_soc_pct=100, soc_bins=21)
    grid = _build_soc_grid(config)
    assert len(grid) == 21
    assert grid[0] == 10.0
    assert grid[-1] == 100.0


def test_build_soc_grid_single_bin():
    """Single bin should return just min_soc."""
    config = OptimizerConfig(min_soc_pct=20, max_soc_pct=90, soc_bins=1)
    grid = _build_soc_grid(config)
    assert grid == [20.0]


def test_map_soc_to_bin_empty_grid():
    """Empty grid should return 0."""
    assert _map_soc_to_bin(50.0, []) == 0


def test_map_soc_to_bin_exact_match():
    """Exact SOC match should return that bin index."""
    grid = [10.0, 20.0, 30.0, 40.0, 50.0]
    assert _map_soc_to_bin(30.0, grid) == 2


def test_map_soc_to_bin_interpolation():
    """SOC between bins should map to nearest."""
    grid = [10.0, 20.0, 30.0]
    assert _map_soc_to_bin(24.0, grid) == 1  # Closer to 20 than 30
    assert _map_soc_to_bin(26.0, grid) == 2  # Closer to 30 than 20


def test_interpolate_cost_to_soc_empty():
    """Empty inputs should return inf."""
    assert _interpolate_cost_to_soc(50.0, [], {}) == float("inf")


def test_interpolate_cost_to_soc_exact_match():
    """Exact grid match should return cost directly."""
    grid = [10.0, 50.0, 100.0]
    cost_table = {0: 100.0, 1: 50.0, 2: 0.0}
    assert _interpolate_cost_to_soc(50.0, grid, cost_table) == 50.0


def test_interpolate_cost_to_soc_interpolation():
    """Linear interpolation between bins."""
    grid = [0.0, 50.0, 100.0]
    cost_table = {0: 100.0, 1: 50.0, 2: 0.0}
    # Midpoint between bin 0 and 1
    result = _interpolate_cost_to_soc(25.0, grid, cost_table)
    assert result == 75.0  # Linear interpolation: 100 - 0.5 * 50


def test_simulate_solar_only_terminal_soc_no_slots():
    """No slots should return initial SOC."""
    config = OptimizerConfig(battery_capacity_kwh=13.5)
    result = _simulate_solar_only_terminal_soc(
        initial_soc_pct=50.0,
        slots=[],
        terminal_penalty_idx=None,
        config=config,
    )
    assert result == 50.0


def test_simulate_solar_only_terminal_soc_with_charging():
    """Solar charging should increase SOC."""
    slot = SlotContext(
        slot_index=0,
        timestamp_iso=datetime.now().isoformat(),
        buy_price=0.30,
        sell_price=0.05,
        solar_kwh=5.0,  # 5 kWh solar
        consumption_kwh=1.0,  # 1 kWh consumption
        slot_interval_minutes=30,
    )
    config = OptimizerConfig(
        battery_capacity_kwh=13.5,
        charge_rate_kw=5.0,
        charge_efficiency=0.97, discharge_efficiency=0.97,
        min_soc_pct=10.0,
    )
    result = _simulate_solar_only_terminal_soc(
        initial_soc_pct=50.0,
        slots=[slot],
        terminal_penalty_idx=None,
        config=config,
    )
    # Net solar: 5 - 1 = 4 kWh
    # Max slot transfer: 5.0 kW * 0.5 h = 2.5 kWh
    # Actual transfer: min(4, 2.5) = 2.5 kWh
    # After efficiency: 2.5 * 0.95 = 2.375 kWh
    # SOC delta: 2.375 / 13.5 * 100 = ~17.6%
    assert result > 50.0


def test_simulate_max_soc_in_demand_window():
    """Should track max SOC within demand window slots."""
    slots = [
        SlotContext(
            slot_index=i,
            timestamp_iso=datetime.now().isoformat(),
            buy_price=0.30,
            sell_price=0.05,
            solar_kwh=3.0,
            consumption_kwh=1.0,
            slot_interval_minutes=30,
            is_demand_window_slot=(i == 1),  # Slot 1 is in DW
        )
        for i in range(3)
    ]
    config = OptimizerConfig(
        battery_capacity_kwh=13.5,
        charge_rate_kw=5.0,
        charge_efficiency=0.97, discharge_efficiency=0.97,
        min_soc_pct=10.0,
    )
    result = _simulate_max_soc_in_demand_window(
        initial_soc_pct=50.0,
        slots=slots,
        config=config,
        demand_bounds=None,
    )
    assert result >= 50.0


def test_simulate_max_soc_with_demand_bounds():
    """Should respect demand_bounds for scoping."""
    slots = [
        SlotContext(
            slot_index=i,
            timestamp_iso=datetime.now().isoformat(),
            buy_price=0.30,
            sell_price=0.05,
            solar_kwh=5.0 if i < 2 else 0.0,
            consumption_kwh=1.0,
            slot_interval_minutes=30,
            is_demand_window_slot=(i in [0, 1]),
        )
        for i in range(4)
    ]
    config = OptimizerConfig(
        battery_capacity_kwh=13.5,
        charge_rate_kw=5.0,
        charge_efficiency=0.97, discharge_efficiency=0.97,
        min_soc_pct=10.0,
    )
    # Only track slots 0-1 (first DW block)
    result = _simulate_max_soc_in_demand_window(
        initial_soc_pct=50.0,
        slots=slots,
        config=config,
        demand_bounds={"entry_idx": 0, "end_idx": 1},
    )
    assert result >= 50.0
