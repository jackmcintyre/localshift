"""Unit tests for OptimizerFacade."""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest

from custom_components.localshift.const import BatteryMode
from custom_components.localshift.coordinator import CoordinatorData
from custom_components.localshift.engine.optimizer_facade import (
    OptimizerFacade,
)
from custom_components.localshift.engine.optimizer_runner import (
    _current_slot_debug_info,
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
    # Mode-decision debug fields (PR C): the early exit marks a non-optimizer
    # fallback so the fields never go stale.
    assert data.debug_mode_source == "fallback"
    assert data.debug_forecast_slot_found is False


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


def test_apply_cloud_scale_factor_only_scales_rolling_30_minute_window():
    facade = OptimizerFacade(slot_builder_cls=_StubSlotBuilder)
    data = CoordinatorData()
    data.cloud_event_solar_scale_factor = 0.25
    now_dt = datetime(2026, 1, 15, 10, 0, tzinfo=UTC)
    slots = [
        SimpleNamespace(
            solar_kwh=4.0,
            timestamp_iso="2026-01-15T09:45:00+00:00",
            slot_interval_minutes=15,
        ),
        SimpleNamespace(
            solar_kwh=4.0,
            timestamp_iso="2026-01-15T10:00:00+00:00",
            slot_interval_minutes=15,
        ),
        SimpleNamespace(
            solar_kwh=4.0,
            timestamp_iso="2026-01-15T10:15:00+00:00",
            slot_interval_minutes=15,
        ),
        SimpleNamespace(
            solar_kwh=4.0,
            timestamp_iso="2026-01-15T10:30:00+00:00",
            slot_interval_minutes=15,
        ),
    ]

    facade._apply_cloud_scale_factor_to_slots(slots, data, now_dt)

    assert [slot.solar_kwh for slot in slots] == [4.0, 1.0, 1.0, 4.0]


def test_apply_cloud_scale_factor_scales_current_partially_elapsed_slot():
    facade = OptimizerFacade(slot_builder_cls=_StubSlotBuilder)
    data = CoordinatorData()
    data.cloud_event_solar_scale_factor = 0.25
    now_dt = datetime(2026, 1, 15, 10, 7, tzinfo=UTC)
    slots = [
        SimpleNamespace(
            solar_kwh=4.0,
            timestamp_iso="2026-01-15T10:00:00+00:00",
            slot_interval_minutes=15,
        ),
        SimpleNamespace(
            solar_kwh=4.0,
            timestamp_iso="2026-01-15T10:15:00+00:00",
            slot_interval_minutes=15,
        ),
        SimpleNamespace(
            solar_kwh=4.0,
            timestamp_iso="2026-01-15T10:30:00+00:00",
            slot_interval_minutes=15,
        ),
        SimpleNamespace(
            solar_kwh=4.0,
            timestamp_iso="2026-01-15T10:45:00+00:00",
            slot_interval_minutes=15,
        ),
    ]

    facade._apply_cloud_scale_factor_to_slots(slots, data, now_dt)

    assert [slot.solar_kwh for slot in slots] == [1.0, 1.0, 1.0, 4.0]


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
        is_boost=False,
    )
    tracker.record_forecast.assert_any_call(
        period_start=datetime(2026, 1, 15, 10, 30, tzinfo=UTC),
        forecast_kwh=0.2,
        weather_condition="sunny",
        is_boost=False,
    )


def test_record_forecasts_for_slots_skips_5min_hybrid_slots():
    """A near-term 5-min hybrid slot landing on a :00/:30 boundary must not
    be recorded as the period forecast: its solar_kwh covers ~5 minutes, and
    comparing it against a 30-min actual reads as a systematic ~6x
    under-forecast."""
    tracker = MagicMock()
    slots = [
        SimpleNamespace(
            solar_kwh=0.1,
            timestamp_iso="2026-06-12T10:00:01+10:00",
            slot_interval_minutes=5,
        ),
        SimpleNamespace(
            solar_kwh=0.6,
            timestamp_iso="2026-06-12T10:30:01+10:00",
            slot_interval_minutes=30,
        ),
    ]

    facade = OptimizerFacade(slot_builder_cls=_StubSlotBuilder)
    facade.set_solar_accuracy_tracker(tracker)
    facade._record_forecasts_for_slots(slots, "sunny")

    assert tracker.record_forecast.call_count == 1
    assert (
        tracker.record_forecast.call_args.kwargs["period_start"].isoformat()
        == "2026-06-12T10:30:01+10:00"
    )


def test_record_forecasts_for_slots_passes_boost_flag():
    tracker = MagicMock()
    slots = [
        SimpleNamespace(solar_kwh=0.2, timestamp_iso="2026-01-15T10:30:00+00:00"),
    ]

    facade = OptimizerFacade(slot_builder_cls=_StubSlotBuilder)
    facade.set_solar_accuracy_tracker(tracker)
    facade._record_forecasts_for_slots(slots, "sunny", is_boost=True)

    tracker.record_forecast.assert_called_once_with(
        period_start=datetime(2026, 1, 15, 10, 30, tzinfo=UTC),
        forecast_kwh=0.2,
        weather_condition="sunny",
        is_boost=True,
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
    # #622 gate replacement: an allowed evaluation may commit a fresh mode.
    data.mode_decision_allowed = True
    # A decision whose window brackets "now" (huge interval) so the current-slot
    # lookup reports a real match and the debug_* fields are populated.
    data.optimizer_decisions = [
        {
            "action": "charge_grid_normal",
            "timestamp_iso": "2020-01-01T00:00:00+00:00",
            "slot_interval_minutes": 9_999_999,
        }
    ]
    result = MagicMock()
    optimizer_config = MagicMock()
    decision_time = datetime(2026, 1, 15, 10, 0, tzinfo=UTC)

    with (
        patch(
            "custom_components.localshift.engine.optimizer_facade.OptimizerSafetyGate"
        ) as mock_gate,
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
    # Mode-decision debug fields (PR C): a real slot match attributed to the
    # optimizer.
    assert data.debug_mode_source == "optimizer"
    assert data.debug_forecast_slot_found is True
    assert data.debug_forecast_slot_time == "00:00"
    assert data.debug_first_forecast_slot_time == "00:00"
    assert data.debug_time_gap_seconds > 0


def test_assign_active_mode_falls_back_on_invalid_battery_mode():
    facade = OptimizerFacade(slot_builder_cls=_StubSlotBuilder)
    data = CoordinatorData()
    data.active_mode = BatteryMode.GRID_CHARGING
    # #622 gate replacement: an allowed evaluation may commit a fresh mode.
    data.mode_decision_allowed = True
    data.optimizer_decisions = [{"action": "hold"}]
    result = MagicMock()
    optimizer_config = MagicMock()

    with (
        patch(
            "custom_components.localshift.engine.optimizer_facade.OptimizerSafetyGate"
        ) as mock_gate,
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
    assert data.debug_mode_source == "fallback"


def _frozen_data_with_decided_mode(decided: BatteryMode) -> CoordinatorData:
    """A frozen evaluation: active_mode already decided, decision NOT allowed."""
    data = CoordinatorData()
    data.active_mode = decided
    data.mode_decision_allowed = False
    data.debug_mode_source = "optimizer"  # source of the held decision
    return data


def test_assign_active_mode_frozen_optimizer_branch_pins_mode():
    """#622 gate replacement: frozen + normal optimizer branch holds active_mode
    and records debug_plan_mode_pending instead."""
    facade = OptimizerFacade(slot_builder_cls=_StubSlotBuilder)
    data = _frozen_data_with_decided_mode(BatteryMode.SELF_CONSUMPTION)
    data.optimizer_decisions = [
        {
            "action": "charge_grid_normal",
            "timestamp_iso": "2020-01-01T00:00:00+00:00",
            "slot_interval_minutes": 9_999_999,
        }
    ]

    with (
        patch(
            "custom_components.localshift.engine.optimizer_facade.OptimizerSafetyGate"
        ) as mock_gate,
        patch(
            "custom_components.localshift.engine.optimizer_facade._derive_runtime_apply_plan",
            return_value={
                "battery_mode": BatteryMode.GRID_CHARGING.value,
                "action": "charge_grid_normal",
            },
        ),
    ):
        mock_gate.return_value.check_admission.return_value = SimpleNamespace(
            allowed=True, block_reason=None
        )
        facade._assign_active_mode(data, MagicMock(), MagicMock(), {})

    # Mode held, no decision-lag write, plan surfaced as pending.
    assert data.active_mode == BatteryMode.SELF_CONSUMPTION
    assert data.decision_timestamp is None
    assert data.decision_mode is None
    assert data.debug_plan_mode_pending == BatteryMode.GRID_CHARGING.value
    # debug_mode_source reflects the HELD decision, not the frozen plan.
    assert data.debug_mode_source == "optimizer"
    # Observability (apply status) still refreshes.
    assert data.optimizer_last_apply_status == "ready_to_apply"


def test_assign_active_mode_frozen_safety_block_pins_mode():
    """#622 gate replacement: frozen + safety-gate block holds active_mode."""
    facade = OptimizerFacade(slot_builder_cls=_StubSlotBuilder)
    data = _frozen_data_with_decided_mode(BatteryMode.GRID_CHARGING)

    with patch(
        "custom_components.localshift.engine.optimizer_facade.OptimizerSafetyGate"
    ) as mock_gate:
        mock_gate.return_value.check_admission.return_value = SimpleNamespace(
            allowed=False, block_reason="stale forecast"
        )
        facade._assign_active_mode(data, MagicMock(), MagicMock(), {})

    # Block would default to SELF_CONSUMPTION, but frozen → hold GRID_CHARGING.
    assert data.active_mode == BatteryMode.GRID_CHARGING
    assert data.decision_timestamp is None
    assert data.debug_plan_mode_pending == BatteryMode.SELF_CONSUMPTION.value
    # Block status is observability and still recorded.
    assert data.optimizer_last_apply_status == "blocked"
    assert data.optimizer_safety_block_reason == "stale forecast"


def test_assign_active_mode_frozen_valueerror_pins_mode():
    """#622 gate replacement: frozen + invalid-mode fallback holds active_mode."""
    facade = OptimizerFacade(slot_builder_cls=_StubSlotBuilder)
    data = _frozen_data_with_decided_mode(BatteryMode.GRID_CHARGING)
    data.optimizer_decisions = [{"action": "hold"}]

    with (
        patch(
            "custom_components.localshift.engine.optimizer_facade.OptimizerSafetyGate"
        ) as mock_gate,
        patch(
            "custom_components.localshift.engine.optimizer_facade._derive_runtime_apply_plan",
            return_value={"battery_mode": "not-a-mode", "action": "hold"},
        ),
    ):
        mock_gate.return_value.check_admission.return_value = SimpleNamespace(
            allowed=True, block_reason=None
        )
        facade._assign_active_mode(data, MagicMock(), MagicMock(), {})

    # Fallback would default to SELF_CONSUMPTION, but frozen → hold GRID_CHARGING.
    assert data.active_mode == BatteryMode.GRID_CHARGING
    assert data.decision_timestamp is None
    assert data.debug_plan_mode_pending == BatteryMode.SELF_CONSUMPTION.value
    assert data.optimizer_last_apply_status == "fallback"


def test_current_slot_debug_info_matched():
    """A decision whose window brackets now reports a real match."""
    data = CoordinatorData()
    data.optimizer_decisions = [
        {
            "timestamp_iso": "2020-01-01T00:00:00+00:00",
            "slot_interval_minutes": 9_999_999,
        }
    ]
    idx, found, matched, first, gap = _current_slot_debug_info(data)
    assert idx == 0
    assert found is True
    assert matched == "00:00"
    assert first == "00:00"
    assert gap > 0


def test_current_slot_debug_info_past_window_no_match():
    """A past slot whose window has closed reports found=False with the
    first-slot diagnostics still populated (the silent idx=0 fallback)."""
    data = CoordinatorData()
    data.optimizer_decisions = [
        {"timestamp_iso": "2020-01-01T00:00:00+00:00", "slot_interval_minutes": 30}
    ]
    idx, found, matched, first, gap = _current_slot_debug_info(data)
    assert idx == 0
    assert found is False
    assert matched == ""
    assert first == "00:00"
    assert gap > 0


def test_current_slot_debug_info_empty_decisions():
    """No decisions -> no match and no diagnostics."""
    data = CoordinatorData()
    data.optimizer_decisions = []
    assert _current_slot_debug_info(data) == (0, False, "", "", 0.0)


def test_current_slot_debug_info_malformed_timestamp():
    """An unparsable timestamp is skipped; with no parsable slot, no match."""
    data = CoordinatorData()
    data.optimizer_decisions = [{"timestamp_iso": "not-a-timestamp"}]
    assert _current_slot_debug_info(data) == (0, False, "", "", 0.0)


class _ShadowSlotBuilderWithOneSlot:
    def __init__(self, **_kwargs) -> None:
        pass

    def build_slots(
        self,
        _data,
        _adaptive_params,
        now_dt=None,
        override_general_forecast=None,
        override_feed_in_forecast=None,
    ):
        assert now_dt is not None
        assert override_general_forecast is not None
        assert override_feed_in_forecast is not None
        return [SimpleNamespace(timestamp_iso="2026-01-15T10:00:00+00:00")], MagicMock()


class _ShadowSlotBuilderEmpty:
    def __init__(self, **_kwargs) -> None:
        pass

    def build_slots(self, *_args, **_kwargs):
        return [], MagicMock()


class _ShadowSlotBuilderBoom:
    def __init__(self, **_kwargs) -> None:
        pass

    def build_slots(self, *_args, **_kwargs):
        raise RuntimeError("shadow boom")


def test_run_shadow_comparison_disabled_returns_early():
    facade = OptimizerFacade(slot_builder_cls=_StubSlotBuilder)
    data = CoordinatorData()
    data.general_price_shadow = 0.25

    facade._run_shadow_comparison(
        data,
        datetime(2026, 1, 15, 10, 0, tzinfo=UTC),
        {"comparison_mode": "disabled"},
        MagicMock(all_solcast=[]),
    )

    assert data.primary_decision == ""
    assert data.shadow_decision == ""


def test_run_shadow_comparison_resets_when_shadow_price_unavailable():
    facade = OptimizerFacade(slot_builder_cls=_StubSlotBuilder)
    data = CoordinatorData()
    data.general_price_shadow = 0.0
    data.comparison_match = False
    data.primary_decision = "old"
    data.shadow_decision = "other"
    data.price_delta = 99.0

    facade._run_shadow_comparison(
        data,
        datetime(2026, 1, 15, 10, 0, tzinfo=UTC),
        {"comparison_mode": "enabled"},
        MagicMock(all_solcast=[]),
    )

    assert data.comparison_match is True
    assert data.primary_decision == ""
    assert data.shadow_decision == ""
    assert data.price_delta == 0.0


def test_run_shadow_comparison_threads_solcast_analysis_and_logs_mismatch():
    facade = OptimizerFacade(slot_builder_cls=_ShadowSlotBuilderWithOneSlot)
    facade._planner = MagicMock(
        plan=MagicMock(return_value=SimpleNamespace(decisions=[SimpleNamespace()]))
    )
    data = CoordinatorData()
    data.general_price_shadow = 0.15
    data.general_price = 0.25
    data.general_forecast_shadow = cast(Any, [{"start": "shadow"}])
    data.feed_in_forecast_shadow = cast(Any, [{"start": "shadow-fit"}])
    data.adaptive_params = cast(Any, None)
    data.soc = 55.0
    data.active_mode = BatteryMode.SELF_CONSUMPTION
    data.solcast_analysis_today = cast(Any, object())
    data.solcast_analysis_tomorrow = cast(Any, object())
    data.decision_log = []

    with (
        patch(
            "custom_components.localshift.engine.optimizer_facade._build_optimizer_config",
            return_value=MagicMock(),
        ),
        patch(
            "custom_components.localshift.engine.optimizer_facade._normalize_initial_soc",
            return_value=(55.0, {}),
        ),
        patch(
            "custom_components.localshift.engine.optimizer_facade._serialize_decision",
            return_value={"action": "hold"},
        ),
        patch(
            "custom_components.localshift.engine.optimizer_facade._find_current_slot_index",
            return_value=0,
        ),
        patch(
            "custom_components.localshift.engine.optimizer_facade._derive_runtime_apply_plan",
            return_value={"battery_mode": BatteryMode.GRID_CHARGING.value},
        ),
    ):
        facade._run_shadow_comparison(
            data,
            datetime(2026, 1, 15, 10, 0, tzinfo=UTC),
            {"comparison_mode": "enabled"},
            MagicMock(all_solcast=[{"period_start": "2026-01-15T10:00:00+00:00"}]),
        )

    inputs = facade._planner.plan.call_args.args[0]
    assert inputs.solcast_analysis_today is data.solcast_analysis_today
    assert inputs.solcast_analysis_tomorrow is data.solcast_analysis_tomorrow
    assert data.primary_decision == BatteryMode.SELF_CONSUMPTION.value
    assert data.shadow_decision == BatteryMode.GRID_CHARGING.value
    assert data.comparison_match is False
    assert data.price_delta == pytest.approx(0.10)
    assert len(data.decision_log) == 1


def test_run_shadow_comparison_handles_empty_slots_invalid_soc_and_exceptions(caplog):
    data = CoordinatorData()
    data.general_price_shadow = 0.15
    data.general_forecast_shadow = cast(Any, [{"start": "shadow"}])
    data.feed_in_forecast_shadow = cast(Any, [{"start": "shadow-fit"}])
    data.adaptive_params = cast(Any, None)
    data.soc = 55.0

    empty_facade = OptimizerFacade(slot_builder_cls=_ShadowSlotBuilderEmpty)
    with caplog.at_level("WARNING"):
        empty_facade._run_shadow_comparison(
            data,
            datetime(2026, 1, 15, 10, 0, tzinfo=UTC),
            {"comparison_mode": "enabled"},
            MagicMock(all_solcast=[]),
        )
    assert "Shadow optimizer: no slots available" in caplog.text

    invalid_soc_facade = OptimizerFacade(slot_builder_cls=_ShadowSlotBuilderWithOneSlot)
    with patch(
        "custom_components.localshift.engine.optimizer_facade._normalize_initial_soc",
        return_value=(None, {"error": "invalid"}),
    ):
        with caplog.at_level("WARNING"):
            invalid_soc_facade._run_shadow_comparison(
                data,
                datetime(2026, 1, 15, 10, 0, tzinfo=UTC),
                {"comparison_mode": "enabled"},
                MagicMock(all_solcast=[]),
            )
    assert "Shadow optimizer: invalid SOC" in caplog.text

    exploding_facade = OptimizerFacade(slot_builder_cls=_ShadowSlotBuilderBoom)
    with caplog.at_level("WARNING"):
        exploding_facade._run_shadow_comparison(
            data,
            datetime(2026, 1, 15, 10, 0, tzinfo=UTC),
            {"comparison_mode": "enabled"},
            MagicMock(all_solcast=[]),
        )
    assert "Shadow optimizer failed: shadow boom" in caplog.text


def test_log_comparison_mismatch_trims_decision_log_to_50_entries():
    facade = OptimizerFacade(slot_builder_cls=_StubSlotBuilder)
    data = CoordinatorData()
    data.decision_log = [{"idx": i} for i in range(50)]

    facade._log_comparison_mismatch(data, "self_consumption", "grid_charging", 0.12)

    assert len(data.decision_log) == 50
    assert data.decision_log[-1]["old_mode"] == "self_consumption"
    assert data.decision_log[-1]["new_mode"] == "grid_charging"
