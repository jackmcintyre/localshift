from __future__ import annotations

from datetime import UTC, datetime, timedelta

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
