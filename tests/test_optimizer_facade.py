"""Unit tests for OptimizerFacade."""

from datetime import UTC, datetime

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
