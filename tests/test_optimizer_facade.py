"""Unit tests for OptimizerFacade."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from custom_components.localshift.const import BatteryMode
from custom_components.localshift.coordinator import CoordinatorData
from custom_components.localshift.engine.optimizer_facade import (
    OptimizerFacade,
)


class _StubSlotBuilder:
    def __init__(self, **_kwargs) -> None:
        pass

    def build_slots(self, _data, _adaptive_params, now_dt=None):
        return [], None


class _StubSlotBuilderWithOneSlot:
    def __init__(self, **_kwargs) -> None:
        pass

    def build_slots(self, _data, _adaptive_params, now_dt=None):
        return [
            SimpleNamespace(solar_kwh=1.0, timestamp_iso="2026-01-15T10:00:00+00:00")
        ], MagicMock()


class _ExplodingSlotBuilder:
    def __init__(self, **_kwargs) -> None:
        pass

    def build_slots(self, _data, _adaptive_params, now_dt=None):
        raise RuntimeError("boom")


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


def test_apply_bias_correction_to_slots_uses_tracker_combined_correction():
    tracker = MagicMock()
    tracker.apply_bias_correction.side_effect = [0.5, 0.0]

    slots = [
        SimpleNamespace(solar_kwh=2.0, timestamp_iso="2026-01-15T10:00:00+00:00"),
        SimpleNamespace(solar_kwh=1.0, timestamp_iso="2026-01-15T10:15:00+00:00"),
    ]

    facade = OptimizerFacade(slot_builder_cls=_StubSlotBuilder)
    facade.set_solar_accuracy_tracker(tracker)

    with patch(
        "custom_components.localshift.engine.optimizer_facade.dt_util.now",
        return_value=datetime(2026, 1, 15, 10, 0, tzinfo=UTC),
    ):
        facade._apply_bias_correction_to_slots(slots, "sunny")

    assert slots[0].solar_kwh == 0.5
    assert slots[1].solar_kwh == 0.0
    tracker.apply_bias_correction.assert_any_call(2.0, "morning", "sunny", "summer")
    tracker.apply_bias_correction.assert_any_call(1.0, "morning", "sunny", "summer")


def test_apply_bias_correction_to_slots_includes_zero_forecasts():
    tracker = MagicMock()
    tracker.apply_bias_correction.return_value = 0.4

    slots = [
        SimpleNamespace(solar_kwh=0.0, timestamp_iso="2026-01-15T10:00:00+00:00"),
    ]

    facade = OptimizerFacade(slot_builder_cls=_StubSlotBuilder)
    facade.set_solar_accuracy_tracker(tracker)
    facade._apply_bias_correction_to_slots(slots, "sunny")

    assert slots[0].solar_kwh == 0.4
    tracker.apply_bias_correction.assert_called_once_with(
        0.0, "morning", "sunny", "summer"
    )


def test_record_forecasts_for_slots_records_only_backfillable_timestamps():
    tracker = MagicMock()
    slots = [
        SimpleNamespace(solar_kwh=0.0, timestamp_iso="2026-01-15T10:00:00+00:00"),
        SimpleNamespace(solar_kwh=1.0, timestamp_iso="2026-01-15T10:15:00+00:00"),
        SimpleNamespace(solar_kwh=0.2, timestamp_iso="2026-01-15T10:30:00+00:00"),
    ]

    facade = OptimizerFacade(slot_builder_cls=_StubSlotBuilder)
    facade.set_solar_accuracy_tracker(tracker)
    facade._record_forecasts_for_slots(slots, "sunny")

    assert tracker.record_forecast.call_count == 2
    tracker.record_forecast.assert_any_call(
        period_start=datetime(2026, 1, 15, 10, 0, tzinfo=UTC),
        forecast_kwh=0.0,
        weather_condition="sunny",
    )
    tracker.record_forecast.assert_any_call(
        period_start=datetime(2026, 1, 15, 10, 30, tzinfo=UTC),
        forecast_kwh=0.2,
        weather_condition="sunny",
    )


@pytest.mark.parametrize(
    ("hour", "expected"),
    [
        (13, "afternoon"),
        (19, "evening"),
        (3, "night"),
    ],
)
def test_get_time_of_day_maps_remaining_buckets(hour: int, expected: str):
    assert (
        OptimizerFacade._get_time_of_day(datetime(2026, 1, 15, hour, 0, tzinfo=UTC))
        == expected
    )


@pytest.mark.parametrize(
    ("month", "expected"),
    [
        (4, "autumn"),
        (7, "winter"),
        (10, "spring"),
    ],
)
def test_get_season_maps_remaining_buckets(month: int, expected: str):
    assert (
        OptimizerFacade._get_season(datetime(2026, month, 15, 10, 0, tzinfo=UTC))
        == expected
    )


def test_run_inline_skips_invalid_soc_before_planner_call():
    planner = MagicMock()
    data = CoordinatorData()
    data.soc = 50.0

    facade = OptimizerFacade(
        planner=planner, slot_builder_cls=_StubSlotBuilderWithOneSlot
    )

    with patch(
        "custom_components.localshift.engine.optimizer_facade._normalize_initial_soc",
        return_value=(None, {"error": "invalid_soc"}),
    ):
        facade.run_inline(
            data=data,
            now_dt=datetime(2026, 1, 15, 10, 0, tzinfo=UTC),
            config_options={},
        )

    planner.plan.assert_not_called()


def test_run_inline_logs_and_swallows_unexpected_exception(caplog):
    facade = OptimizerFacade(slot_builder_cls=_ExplodingSlotBuilder)

    with caplog.at_level("WARNING"):
        facade.run_inline(
            data=CoordinatorData(),
            now_dt=datetime(2026, 1, 15, 10, 0, tzinfo=UTC),
            config_options={},
        )

    assert "Inline DP optimizer failed (non-blocking): boom" in caplog.text


def test_assign_active_mode_sets_mode_and_apply_status():
    facade = OptimizerFacade(slot_builder_cls=_StubSlotBuilder)
    data = CoordinatorData()
    data.active_mode = BatteryMode.SELF_CONSUMPTION
    data.optimizer_decisions = [{"action": "charge_grid_normal"}]
    result = MagicMock()
    optimizer_config = MagicMock()
    decision_time = datetime(2026, 1, 15, 10, 0, tzinfo=UTC)

    with (
        patch(
            "custom_components.localshift.engine.optimizer_facade.OptimizerSafetyGate"
        ) as mock_gate,
        patch(
            "custom_components.localshift.engine.optimizer_facade._find_current_slot_index",
            return_value=2,
        ),
        patch(
            "custom_components.localshift.engine.optimizer_facade._derive_runtime_apply_plan",
            return_value={
                "battery_mode": BatteryMode.GRID_CHARGING.value,
                "action": "charge_grid_normal",
            },
        ),
        patch(
            "custom_components.localshift.engine.optimizer_facade.dt_util.now",
            return_value=decision_time,
        ),
    ):
        mock_gate.return_value.check_admission.return_value = SimpleNamespace(
            allowed=True,
            block_reason=None,
        )
        facade._assign_active_mode(data, result, optimizer_config, {})

    assert data.optimizer_apply_plan == {
        "battery_mode": BatteryMode.GRID_CHARGING.value,
        "action": "charge_grid_normal",
    }
    assert data.active_mode == BatteryMode.GRID_CHARGING
    assert data.optimizer_last_apply_status == "ready_to_apply"
    assert data.optimizer_safety_block_reason == ""
    assert data.decision_timestamp == decision_time
    assert data.decision_mode == BatteryMode.GRID_CHARGING


def test_assign_active_mode_falls_back_on_invalid_battery_mode():
    facade = OptimizerFacade(slot_builder_cls=_StubSlotBuilder)
    data = CoordinatorData()
    data.active_mode = BatteryMode.GRID_CHARGING
    data.optimizer_decisions = [{"action": "hold"}]
    result = MagicMock()
    optimizer_config = MagicMock()

    with (
        patch(
            "custom_components.localshift.engine.optimizer_facade.OptimizerSafetyGate"
        ) as mock_gate,
        patch(
            "custom_components.localshift.engine.optimizer_facade._find_current_slot_index",
            return_value=0,
        ),
        patch(
            "custom_components.localshift.engine.optimizer_facade._derive_runtime_apply_plan",
            return_value={"battery_mode": "not-a-mode", "action": "hold"},
        ),
    ):
        mock_gate.return_value.check_admission.return_value = SimpleNamespace(
            allowed=True,
            block_reason=None,
        )
        facade._assign_active_mode(data, result, optimizer_config, {})

    assert data.active_mode == BatteryMode.SELF_CONSUMPTION
    assert data.optimizer_last_apply_status == "fallback"
