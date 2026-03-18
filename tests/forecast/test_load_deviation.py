from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from custom_components.localshift.coordinator import CoordinatorData
from custom_components.localshift.forecast.load_deviation import LoadDeviationDetector


def _make_current_slot_data(*, now: datetime, actual_kw: float, forecast_kw: float):
    data = CoordinatorData()
    data.load_power_kw = actual_kw
    data.load_forecast_slots = [forecast_kw]
    data.optimizer_decisions = [
        {
            "timestamp_iso": (now - timedelta(minutes=1)).isoformat(),
            "slot_interval_minutes": 30,
        }
    ]
    data.optimizer_last_apply_status = "ready_to_apply"
    data.optimizer_apply_plan = {"battery_mode": "grid_charging"}
    data.load_deviation_diagnostics = {}
    return data


def test_detector_skips_when_optimizer_runtime_is_inactive() -> None:
    now = datetime(2026, 3, 12, 12, 0, tzinfo=UTC)
    data = _make_current_slot_data(now=now, actual_kw=2.5, forecast_kw=1.0)
    data.optimizer_last_apply_status = "blocked"
    data.optimizer_apply_plan = None

    detector = LoadDeviationDetector()

    assert detector.evaluate(data, now) is False
    diagnostics = data.load_deviation_diagnostics
    assert diagnostics["status"] == "inactive"
    assert diagnostics["triggered"] is False


def test_detector_skips_without_current_slot_forecast() -> None:
    now = datetime(2026, 3, 12, 12, 0, tzinfo=UTC)
    data = _make_current_slot_data(now=now, actual_kw=2.5, forecast_kw=1.0)
    data.load_forecast_slots = []

    detector = LoadDeviationDetector()

    assert detector.evaluate(data, now) is False
    diagnostics = data.load_deviation_diagnostics
    assert diagnostics["status"] == "no_current_slot"
    assert diagnostics["triggered"] is False


def test_detector_triggers_after_sustained_deviation_window() -> None:
    now = datetime(2026, 3, 12, 12, 0, tzinfo=UTC)
    data = _make_current_slot_data(now=now, actual_kw=2.2, forecast_kw=1.0)
    detector = LoadDeviationDetector()

    assert detector.evaluate(data, now) is False
    assert detector.evaluate(data, now + timedelta(minutes=10)) is False
    assert detector.evaluate(data, now + timedelta(minutes=11)) is True

    diagnostics = data.load_deviation_diagnostics
    assert diagnostics["status"] == "triggered"
    assert diagnostics["breach_type"] == "sustained"
    assert diagnostics["triggered"] is True


def test_detector_triggers_after_spike_window() -> None:
    now = datetime(2026, 3, 12, 12, 0, tzinfo=UTC)
    data = _make_current_slot_data(now=now, actual_kw=4.5, forecast_kw=1.0)
    detector = LoadDeviationDetector()

    assert detector.evaluate(data, now) is False
    assert detector.evaluate(data, now + timedelta(minutes=5)) is False
    assert detector.evaluate(data, now + timedelta(minutes=6)) is True

    diagnostics = data.load_deviation_diagnostics
    assert diagnostics["breach_type"] == "spike"
    assert diagnostics["triggered"] is True


def test_detector_enforces_cooldown_before_retrigger() -> None:
    now = datetime(2026, 3, 12, 12, 0, tzinfo=UTC)
    data = _make_current_slot_data(now=now, actual_kw=4.5, forecast_kw=1.0)
    detector = LoadDeviationDetector()

    detector.evaluate(data, now)
    assert detector.evaluate(data, now + timedelta(minutes=6)) is True
    assert detector.evaluate(data, now + timedelta(minutes=7)) is False

    diagnostics = data.load_deviation_diagnostics
    assert diagnostics["status"] == "cooldown"
    assert diagnostics["triggered"] is False


def test_detector_retriggers_after_cooldown_with_new_window() -> None:
    now = datetime(2026, 3, 12, 12, 0, tzinfo=UTC)
    data = _make_current_slot_data(now=now, actual_kw=4.5, forecast_kw=1.0)
    detector = LoadDeviationDetector()

    detector.evaluate(data, now)
    assert detector.evaluate(data, now + timedelta(minutes=6)) is True
    assert detector.evaluate(data, now + timedelta(minutes=21)) is False
    assert detector.evaluate(data, now + timedelta(minutes=27)) is True

    diagnostics = data.load_deviation_diagnostics
    assert diagnostics["breach_type"] == "spike"
    assert diagnostics["triggered"] is True


def _make_multi_decision_data(
    *,
    now: datetime,
    actual_kw: float,
    load_forecast_slots: list[float],
    decision_timestamps: list[datetime],
) -> CoordinatorData:
    """Create test data with multiple optimizer decisions at different times."""
    data = CoordinatorData()
    data.load_power_kw = actual_kw
    data.load_forecast_slots = load_forecast_slots
    data.optimizer_decisions = [
        {
            "timestamp_iso": ts.isoformat(),
            "slot_interval_minutes": 5 if i < 8 else 30,
        }
        for i, ts in enumerate(decision_timestamps)
    ]
    data.optimizer_last_apply_status = "ready_to_apply"
    data.optimizer_apply_plan = {"battery_mode": "grid_charging"}
    data.load_deviation_diagnostics = {}
    return data


def test_detector_uses_time_based_forecast_mapping_for_5min_decisions() -> None:
    """Regression: multiple 5-minute decisions should map to same 15-minute forecast slot.

    The bug was: detector used decision index directly instead of time-based mapping,
    causing incorrect forecast lookup (decision 4 read forecast slot 4 instead of slot 1).

    This test verifies that decision at index 4 reads from time-mapped forecast slot.
    """
    from unittest.mock import patch

    aus_tz = ZoneInfo("Australia/Sydney")

    base_time = datetime(2026, 3, 15, 11, 5, 0, tzinfo=aus_tz)

    load_forecast_slots = [1.0, 1.5, 2.0, 2.5, 3.0, 3.5] + [1.0] * 90

    decision_times = [
        base_time,
        base_time + timedelta(minutes=5),
        base_time + timedelta(minutes=10),
        base_time + timedelta(minutes=15),
        base_time + timedelta(minutes=20),
        base_time + timedelta(minutes=25),
    ]

    data = _make_multi_decision_data(
        now=base_time,
        actual_kw=2.0,
        load_forecast_slots=load_forecast_slots,
        decision_timestamps=decision_times,
    )

    def mock_find_slot(data):
        return 4

    with patch(
        "custom_components.localshift.forecast.load_deviation._find_current_slot_index",
        mock_find_slot,
    ):
        detector = LoadDeviationDetector()
        result = detector.evaluate(data, base_time)

    diagnostics = data.load_deviation_diagnostics
    assert diagnostics["current_slot_index"] == 4, (
        f"Expected decision index 4, got {diagnostics['current_slot_index']}"
    )

    decision_4_time = decision_times[4]
    elapsed = (decision_4_time - base_time).total_seconds() / 60.0
    expected_forecast_idx = min(int(elapsed // 15), len(load_forecast_slots) - 1)
    expected_forecast_kw = load_forecast_slots[expected_forecast_idx]
    assert diagnostics["forecast_kw"] == expected_forecast_kw, (
        f"Expected {expected_forecast_kw} (time-mapped index {expected_forecast_idx}), "
        f"got {diagnostics['forecast_kw']} (bug: using direct index 4 -> {load_forecast_slots[4]})"
    )


def test_detector_15min_boundary_crossing() -> None:
    """Decision at 11:35 (25 min from base 11:05) should map to forecast slot 1, not slot 2."""
    from unittest.mock import patch

    base_time = datetime(2026, 3, 15, 11, 5, 0, tzinfo=ZoneInfo("Australia/Sydney"))

    load_forecast_slots = [1.0, 1.5, 2.0, 2.5, 3.0, 3.5] + [1.0] * 90

    decision_times = [
        base_time,
        base_time + timedelta(minutes=25),
    ]

    data = _make_multi_decision_data(
        now=base_time,
        actual_kw=2.5,
        load_forecast_slots=load_forecast_slots,
        decision_timestamps=decision_times,
    )

    def mock_find_slot(data):
        return 1

    with patch(
        "custom_components.localshift.forecast.load_deviation._find_current_slot_index",
        mock_find_slot,
    ):
        detector = LoadDeviationDetector()
        detector.evaluate(data, base_time)

    diagnostics = data.load_deviation_diagnostics
    assert diagnostics["forecast_kw"] == 1.5


def test_detector_later_decision_would_fail_with_direct_indexing() -> None:
    """Decision index 4 should map to forecast slot 1 (time-based), not slot 4.

    With direct indexing: forecast_kw = load_forecast_slots[4] = 1.768
    With time-based mapping: forecast_kw = load_forecast_slots[1] = 1.651
    """
    from unittest.mock import patch

    base_time = datetime(2026, 3, 15, 11, 5, 0, tzinfo=ZoneInfo("Australia/Sydney"))

    load_forecast_slots = [1.651] * 4 + [1.768] * 4 + [1.509] * 88

    decision_times = [
        base_time,
        base_time + timedelta(minutes=25),
    ]

    data = _make_multi_decision_data(
        now=base_time,
        actual_kw=1.8,
        load_forecast_slots=load_forecast_slots,
        decision_timestamps=decision_times,
    )

    def mock_find_slot(data):
        return 1

    with patch(
        "custom_components.localshift.forecast.load_deviation._find_current_slot_index",
        mock_find_slot,
    ):
        detector = LoadDeviationDetector()
        detector.evaluate(data, base_time + timedelta(minutes=25))

    diagnostics = data.load_deviation_diagnostics
    assert diagnostics["current_slot_index"] == 1
    assert diagnostics["forecast_kw"] == 1.651


def test_detector_out_of_range_mapped_index_clamps() -> None:
    """Late decision timestamp should clamp to last valid forecast slot."""
    from unittest.mock import patch

    base_time = datetime(2026, 3, 15, 11, 5, 0, tzinfo=ZoneInfo("Australia/Sydney"))

    load_forecast_slots = [1.0, 2.0, 3.0]

    decision_times = [
        base_time,
        base_time + timedelta(hours=24),
    ]

    data = _make_multi_decision_data(
        now=base_time,
        actual_kw=3.5,
        load_forecast_slots=load_forecast_slots,
        decision_timestamps=decision_times,
    )

    def mock_find_slot(data):
        return 1

    with patch(
        "custom_components.localshift.forecast.load_deviation._find_current_slot_index",
        mock_find_slot,
    ):
        detector = LoadDeviationDetector()
        detector.evaluate(data, base_time + timedelta(hours=24))

    diagnostics = data.load_deviation_diagnostics
    assert diagnostics["forecast_kw"] == 3.0
