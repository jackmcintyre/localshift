"""Unit tests for OptimizerFacade."""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from custom_components.localshift.engine.optimizer_facade import (
    OptimizerFacade,
)
from custom_components.localshift.coordinator import CoordinatorData


class _StubSlotBuilder:
    def __init__(self, **_kwargs) -> None:
        pass

    def build_slots(self, _data, _adaptive_params, now_dt=None):
        return [], None


def test_run_inline_no_slots_leaves_optimizer_fields():
    """Optimizer facade should return early when no slots are built."""
    data = CoordinatorData()
    data.optimizer_decisions = [{"action": "hold"}]

    facade = OptimizerFacade(slot_builder_cls=_StubSlotBuilder)
    now_dt = datetime(2026, 2, 16, 10, 0, 0, tzinfo=UTC)
    facade.run_inline(data=data, now_dt=now_dt, config_options={})

    assert data.optimizer_decisions == [{"action": "hold"}]


def test_facade_wires_solar_can_reach_target_in_dw_correctly():
    """Issue #633: Facade must use result.can_solar_reach_target_in_dw (not can_solar_reach_target).

    The two fields can diverge (e.g. broad horizon check passes but DW-specific fails).
    Facade must wire the DW-specific field.
    """
    mock_result = MagicMock()
    mock_result.success = True
    mock_result.decisions = []
    mock_result.projected_import_kwh = 0.0
    mock_result.projected_export_kwh = 0.0
    mock_result.projected_net_cost = 0.0
    mock_result.terminal_shortfall_pct = 0.0
    mock_result.can_solar_reach_target = False
    mock_result.can_solar_reach_target_in_dw = True
    mock_result.reason_code_histogram = {}
    mock_result.planner_version = "test"
    mock_result.total_slots = 0
    mock_result.states_explored = 0

    mock_metadata = MagicMock()
    mock_metadata.horizon_hours = 24
    mock_metadata.to_parity_dict.return_value = {}

    class _StubSlotBuilderWithSlots:
        def __init__(self, **_kwargs):
            pass

        def build_slots(self, _data, _adaptive_params, now_dt=None):
            return [MagicMock()], mock_metadata

    with patch(
        "custom_components.localshift.engine.optimizer_facade.DPPlanner"
    ) as MockPlanner:
        MockPlanner.return_value.plan.return_value = mock_result

        data = CoordinatorData()
        data.soc = 50.0
        config_options = {
            "allow_dw_entry_under_target": True,
            "demand_window_target_soc_pct": 80.0,
        }
        facade = OptimizerFacade(slot_builder_cls=_StubSlotBuilderWithSlots)
        facade.run_inline(
            data=data,
            now_dt=datetime(2026, 1, 3, 10, 0, tzinfo=UTC),
            config_options=config_options,
        )

    assert data.solar_can_reach_target_in_dw is True, (
        "Facade must wire solar_can_reach_target_in_dw from result.can_solar_reach_target_in_dw"
    )
