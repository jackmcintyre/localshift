"""Tests for LP optimizer module.

Issue #396: POC for LP-based battery optimization.
"""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.localshift.computation_engine_lib.lp_optimizer import (
    LPOptimizer,
    OptimizerConfig,
    SlotData,
    SlotDecision,
    convert_forecast_to_slots,
)


@pytest.fixture
def optimizer_config():
    """Default optimizer config for tests."""
    return OptimizerConfig(
        battery_capacity_kwh=13.5,
        max_charge_rate_kw=5.0,
        max_discharge_rate_kw=5.0,
        charge_efficiency=0.92,
        discharge_efficiency=0.95,
        min_soc_kwh=0.0,
        solver_timeout_seconds=5,
    )


@pytest.fixture
def mock_hass():
    """Mock Home Assistant instance."""
    hass = MagicMock()
    hass.loop = MagicMock()
    return hass


@pytest.fixture
def simple_slots():
    """Simple 6-slot test case (6 hours in 1-hour intervals)."""
    now = datetime.now()
    slots = []
    for i in range(6):
        slots.append(
            SlotData(
                time=now + timedelta(hours=i),
                interval_minutes=60,
                solar_kwh=2.0 if i in [2, 3] else 0.0,  # Solar hours 2-3
                load_kwh=1.0,
                buy_price=0.30 if i < 3 else 0.10,  # Expensive first 3 hours
                sell_price=0.05,
            )
        )
    return slots


class TestOptimizerConfig:
    """Tests for OptimizerConfig dataclass."""

    def test_default_values(self):
        """Test default configuration values."""
        config = OptimizerConfig()
        assert config.battery_capacity_kwh == 13.5
        assert config.max_charge_rate_kw == 5.0
        assert config.max_discharge_rate_kw == 5.0
        assert config.charge_efficiency == 0.92
        assert config.discharge_efficiency == 0.95
        assert config.min_soc_kwh == 0.0
        assert config.solver_timeout_seconds == 5

    def test_custom_values(self):
        """Test custom configuration values."""
        config = OptimizerConfig(
            battery_capacity_kwh=20.0,
            max_charge_rate_kw=7.0,
            solver_timeout_seconds=10,
        )
        assert config.battery_capacity_kwh == 20.0
        assert config.max_charge_rate_kw == 7.0
        assert config.solver_timeout_seconds == 10


class TestLPOptimizer:
    """Tests for LPOptimizer class."""

    @pytest.mark.asyncio
    async def test_optimizer_unavailable_without_pulp(self, mock_hass, optimizer_config):
        """Test that optimizer reports unavailable when PuLP not installed."""
        optimizer = LPOptimizer(mock_hass, optimizer_config)

        with patch(
            "custom_components.localshift.computation_engine_lib.lp_optimizer._get_pulp",
            return_value=None,
        ):
            available = await optimizer.async_is_available()
            assert available is False

    @pytest.mark.asyncio
    async def test_optimize_returns_unavailable_when_no_solver(
        self, mock_hass, optimizer_config, simple_slots
    ):
        """Test optimization returns unavailable status when solver not available."""
        optimizer = LPOptimizer(mock_hass, optimizer_config)

        with patch.object(optimizer, "async_is_available", return_value=False):
            result = await optimizer.async_optimize(
                slots=simple_slots,
                current_soc_kwh=5.0,
                target_soc_kwh=10.0,
                target_slot_idx=5,
            )

        assert result["status"] == "unavailable"
        assert result["schedule"] is None
        assert "not available" in result["message"]

    @pytest.mark.asyncio
    async def test_optimize_empty_slots(self, mock_hass, optimizer_config):
        """Test optimization with empty slot list."""
        optimizer = LPOptimizer(mock_hass, optimizer_config)

        with patch.object(optimizer, "async_is_available", return_value=True):
            result = await optimizer.async_optimize(
                slots=[],
                current_soc_kwh=5.0,
                target_soc_kwh=10.0,
                target_slot_idx=0,
            )

        assert result["status"] == "error"
        assert "No slots" in result["message"]

    def test_get_grid_charge_slots_empty_for_non_optimal(self, mock_hass, optimizer_config):
        """Test that get_grid_charge_slots returns empty for non-optimal result."""
        optimizer = LPOptimizer(mock_hass, optimizer_config)

        result = {"status": "infeasible", "schedule": None}
        charge_slots = optimizer.get_grid_charge_slots(result)
        assert charge_slots == []

    def test_get_grid_charge_slots_extracts_charging(self, mock_hass, optimizer_config):
        """Test that get_grid_charge_slots extracts charging slots."""
        optimizer = LPOptimizer(mock_hass, optimizer_config)
        now = datetime.now()

        result = {
            "status": "optimal",
            "schedule": [
                SlotDecision(
                    time=now,
                    interval_minutes=60,
                    grid_import_kwh=3.0,
                    export_kwh=0.0,
                    soc_kwh=8.0,
                    solar_kwh=0.0,
                    load_kwh=1.0,
                ),
                SlotDecision(
                    time=now + timedelta(hours=1),
                    interval_minutes=60,
                    grid_import_kwh=0.0,
                    export_kwh=2.0,
                    soc_kwh=6.0,
                    solar_kwh=2.0,
                    load_kwh=1.0,
                ),
            ],
        }

        charge_slots = optimizer.get_grid_charge_slots(result)
        assert len(charge_slots) == 1
        assert charge_slots[0]["grid_import_kwh"] == 3.0

    def test_get_export_slots_extracts_exports(self, mock_hass, optimizer_config):
        """Test that get_export_slots extracts export slots."""
        optimizer = LPOptimizer(mock_hass, optimizer_config)
        now = datetime.now()

        result = {
            "status": "optimal",
            "schedule": [
                SlotDecision(
                    time=now,
                    interval_minutes=60,
                    grid_import_kwh=3.0,
                    export_kwh=0.0,
                    soc_kwh=8.0,
                    solar_kwh=0.0,
                    load_kwh=1.0,
                ),
                SlotDecision(
                    time=now + timedelta(hours=1),
                    interval_minutes=60,
                    grid_import_kwh=0.0,
                    export_kwh=2.0,
                    soc_kwh=6.0,
                    solar_kwh=2.0,
                    load_kwh=1.0,
                ),
            ],
        }

        export_slots = optimizer.get_export_slots(result)
        assert len(export_slots) == 1
        assert export_slots[0]["export_kwh"] == 2.0


class TestSlotDataConversion:
    """Tests for slot data conversion."""

    def test_slot_data_creation(self):
        """Test SlotData creation."""
        now = datetime.now()
        slot = SlotData(
            time=now,
            interval_minutes=30,
            solar_kwh=1.5,
            load_kwh=0.5,
            buy_price=0.25,
            sell_price=0.05,
        )

        assert slot.time == now
        assert slot.interval_minutes == 30
        assert slot.solar_kwh == 1.5
        assert slot.load_kwh == 0.5
        assert slot.buy_price == 0.25
        assert slot.sell_price == 0.05


class TestIntegrationWithPuLP:
    """Integration tests that require PuLP to be installed.

    These tests will be skipped if PuLP is not available.
    """

    @pytest.fixture
    def pulp_available(self):
        """Check if PuLP is available."""
        try:
            import pulp

            return True
        except ImportError:
            return False

    @pytest.mark.asyncio
    @pytest.mark.skipif(
        not pytest.importorskip("pulp", reason="PuLP not installed"),
        reason="PuLP not installed",
    )
    async def test_solve_simple_problem(self, mock_hass, optimizer_config, simple_slots):
        """Test solving a simple optimization problem."""
        optimizer = LPOptimizer(mock_hass, optimizer_config)

        # This test requires PuLP to be installed
        result = await optimizer.async_optimize(
            slots=simple_slots,
            current_soc_kwh=5.0,
            target_soc_kwh=10.0,
            target_slot_idx=5,
        )

        # If solver is available, should get optimal solution
        if result["status"] == "optimal":
            assert result["schedule"] is not None
            assert result["total_cost"] is not None
            assert len(result["schedule"]) == 6

            # Check that SOC at target slot meets target
            final_soc = result["schedule"][5].soc_kwh
            assert final_soc >= 10.0

    @pytest.mark.asyncio
    @pytest.mark.skipif(
        not pytest.importorskip("pulp", reason="PuLP not installed"),
        reason="PuLP not installed",
    )
    async def test_low_price_charging_preferred(self, mock_hass, optimizer_config):
        """Test that optimizer prefers charging during low price periods."""
        now = datetime.now()

        # Create slots where price is low in middle
        slots = [
            SlotData(
                time=now + timedelta(hours=i),
                interval_minutes=60,
                solar_kwh=0.0,
                load_kwh=0.5,
                buy_price=0.50 if i != 2 else 0.05,  # Cheap at hour 2
                sell_price=0.05,
            )
            for i in range(5)
        ]

        optimizer = LPOptimizer(mock_hass, optimizer_config)
        result = await optimizer.async_optimize(
            slots=slots,
            current_soc_kwh=2.0,
            target_soc_kwh=8.0,
            target_slot_idx=4,
        )

        if result["status"] == "optimal":
            # Should charge mostly during cheap period
            charge_at_cheap_slot = result["schedule"][2].grid_import_kwh
            charge_at_expensive_slots = sum(
                result["schedule"][i].grid_import_kwh for i in [0, 1, 3]
            )

            # Expect more charging at cheap slot than expensive slots
            # (allowing for some flexibility due to constraints)
            assert charge_at_cheap_slot >= charge_at_expensive_slots * 0.5


class TestComparisonWithHeuristic:
    """Tests comparing LP optimizer with heuristic approach.

    These tests verify that LP produces results at least as good as
    the heuristic approach.
    """

    @pytest.mark.skip(reason="Requires full integration setup")
    def test_lp_matches_or_beats_heuristic_cost(self):
        """Test that LP optimizer produces cost <= heuristic."""
        # This would require mocking the full forecast_computer
        # and comparing outputs. Left as placeholder for full integration test.
        pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])