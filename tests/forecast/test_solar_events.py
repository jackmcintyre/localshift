from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from custom_components.localshift.coordinator import CoordinatorData
from custom_components.localshift.forecast.solar_events import SolarEventDetector

BASE_NOW = datetime(2026, 3, 12, 12, 0, tzinfo=UTC)


def _make_data(
    *,
    now: datetime,
    actual_solar_kw: float,
    forecast_kw: float,
    optimizer_active: bool = True,
) -> CoordinatorData:
    data = CoordinatorData()
    data.solar_power_kw = actual_solar_kw

    if optimizer_active:
        data.optimizer_last_apply_status = "ready_to_apply"
        data.optimizer_apply_plan = {"battery_mode": "self_consumption"}
    else:
        data.optimizer_last_apply_status = "none"
        data.optimizer_apply_plan = None

    # Solcast integration provides period_start (not period_end)
    period_start = now.replace(second=0, microsecond=0)
    period_start = period_start - timedelta(minutes=period_start.minute % 30)
    data.solcast_today = [
        {"period_start": period_start.isoformat(), "pv_estimate": forecast_kw}
    ]
    data.cloud_event_diagnostics = {}
    data.cloud_event_solar_scale_factor = None
    return data


def test_detector_skips_when_optimizer_inactive() -> None:
    now = BASE_NOW
    data = _make_data(
        now=now, actual_solar_kw=1.0, forecast_kw=5.0, optimizer_active=False
    )
    detector = SolarEventDetector()

    assert detector.evaluate(data, now) is False
    assert data.cloud_event_diagnostics["status"] == "inactive"
    assert data.cloud_event_diagnostics["triggered"] is False


def test_detector_skips_when_no_solcast_data() -> None:
    now = BASE_NOW
    data = _make_data(now=now, actual_solar_kw=1.0, forecast_kw=5.0)
    data.solcast_today = []
    data.solcast_tomorrow = []
    detector = SolarEventDetector()

    assert detector.evaluate(data, now) is False
    assert data.cloud_event_diagnostics["status"] == "no_forecast"
    assert data.cloud_event_diagnostics["triggered"] is False


def test_detector_skips_when_forecast_below_minimum_kw() -> None:
    now = BASE_NOW
    data = _make_data(now=now, actual_solar_kw=0.05, forecast_kw=0.2)
    detector = SolarEventDetector()

    assert detector.evaluate(data, now) is False
    assert data.cloud_event_diagnostics["status"] == "no_forecast"


def test_no_trigger_when_production_normal() -> None:
    now = BASE_NOW
    data = _make_data(now=now, actual_solar_kw=4.0, forecast_kw=5.0)
    detector = SolarEventDetector()

    assert detector.evaluate(data, now) is False
    diag = data.cloud_event_diagnostics
    assert diag["status"] == "normal"
    assert diag["triggered"] is False
    assert diag["ratio"] == pytest.approx(0.8, rel=0.01)


def test_diagnostics_include_actual_and_forecast_kw() -> None:
    now = BASE_NOW
    data = _make_data(now=now, actual_solar_kw=4.0, forecast_kw=5.0)
    detector = SolarEventDetector()
    detector.evaluate(data, now)

    diag = data.cloud_event_diagnostics
    assert diag["actual_kw"] == pytest.approx(4.0)
    assert diag["forecast_kw"] == pytest.approx(5.0)


def test_moderate_onset_pending_before_window() -> None:
    now = BASE_NOW
    data = _make_data(now=now, actual_solar_kw=2.0, forecast_kw=5.0)
    detector = SolarEventDetector()

    detector.evaluate(data, now)
    assert detector.evaluate(data, now + timedelta(minutes=9)) is False
    assert data.cloud_event_diagnostics["status"] == "onset_pending"
    assert data.cloud_event_diagnostics["triggered"] is False


def test_moderate_onset_does_not_trigger_at_exactly_10_min() -> None:
    now = BASE_NOW
    data = _make_data(now=now, actual_solar_kw=2.0, forecast_kw=5.0)
    detector = SolarEventDetector()

    detector.evaluate(data, now)
    assert detector.evaluate(data, now + timedelta(minutes=10)) is False


def test_moderate_onset_triggers_after_window() -> None:
    now = BASE_NOW
    data = _make_data(now=now, actual_solar_kw=2.0, forecast_kw=5.0)
    detector = SolarEventDetector()

    assert detector.evaluate(data, now) is False
    assert detector.evaluate(data, now + timedelta(minutes=10)) is False
    assert detector.evaluate(data, now + timedelta(minutes=11)) is True

    diag = data.cloud_event_diagnostics
    assert diag["status"] == "triggered"
    assert diag["triggered"] is True
    assert diag["event_type"] == "onset_moderate"
    assert diag["ratio"] == pytest.approx(0.40, rel=0.01)


def test_moderate_onset_scale_factor_set_to_ratio() -> None:
    now = BASE_NOW
    data = _make_data(now=now, actual_solar_kw=2.0, forecast_kw=5.0)
    detector = SolarEventDetector()

    detector.evaluate(data, now)
    detector.evaluate(data, now + timedelta(minutes=11))

    assert data.cloud_event_solar_scale_factor == pytest.approx(0.40, rel=0.01)


def test_onset_window_resets_when_production_recovers_mid_window() -> None:
    now = BASE_NOW
    data_cloud = _make_data(now=now, actual_solar_kw=2.0, forecast_kw=5.0)
    data_sunny = _make_data(now=now, actual_solar_kw=4.5, forecast_kw=5.0)
    detector = SolarEventDetector()

    detector.evaluate(data_cloud, now)
    detector.evaluate(data_cloud, now + timedelta(minutes=5))
    detector.evaluate(data_sunny, now + timedelta(minutes=7))

    detector.evaluate(data_cloud, now + timedelta(minutes=8))
    assert detector.evaluate(data_cloud, now + timedelta(minutes=19)) is True


def test_severe_onset_triggers_immediately_on_first_call() -> None:
    now = BASE_NOW
    data = _make_data(now=now, actual_solar_kw=1.0, forecast_kw=5.0)
    detector = SolarEventDetector()

    assert detector.evaluate(data, now) is True
    diag = data.cloud_event_diagnostics
    assert diag["status"] == "triggered"
    assert diag["event_type"] == "onset_severe"
    assert diag["triggered"] is True


def test_severe_onset_scale_factor_reflects_actual_ratio() -> None:
    now = BASE_NOW
    data = _make_data(now=now, actual_solar_kw=0.8, forecast_kw=5.0)
    detector = SolarEventDetector()

    detector.evaluate(data, now)
    assert data.cloud_event_solar_scale_factor == pytest.approx(0.16, rel=0.01)


def test_severe_onset_no_window_required() -> None:
    now = BASE_NOW
    data = _make_data(now=now, actual_solar_kw=1.0, forecast_kw=5.0)
    detector = SolarEventDetector()

    result = detector.evaluate(data, now)
    assert result is True


def test_cooldown_blocks_onset_retrigger_after_clearing() -> None:
    now = BASE_NOW
    data_cloud = _make_data(now=now, actual_solar_kw=1.0, forecast_kw=5.0)
    detector = SolarEventDetector()
    detector.evaluate(data_cloud, now)

    data_sunny = _make_data(now=now, actual_solar_kw=4.5, forecast_kw=5.0)
    detector.evaluate(data_sunny, now + timedelta(minutes=16))
    assert detector.evaluate(data_sunny, now + timedelta(minutes=27)) is True

    detector.evaluate(data_cloud, now + timedelta(minutes=28))
    assert data_cloud.cloud_event_diagnostics["status"] == "cooldown"
    assert data_cloud.cloud_event_diagnostics["triggered"] is False


def test_cooldown_remaining_seconds_written_to_diagnostics() -> None:
    now = BASE_NOW
    data_cloud = _make_data(now=now, actual_solar_kw=1.0, forecast_kw=5.0)
    detector = SolarEventDetector()
    detector.evaluate(data_cloud, now)

    data_sunny = _make_data(now=now, actual_solar_kw=4.5, forecast_kw=5.0)
    detector.evaluate(data_sunny, now + timedelta(minutes=16))
    detector.evaluate(data_sunny, now + timedelta(minutes=27))

    data_cloud2 = _make_data(now=now, actual_solar_kw=1.0, forecast_kw=5.0)
    detector.evaluate(data_cloud2, now + timedelta(minutes=28))
    remaining = data_cloud2.cloud_event_diagnostics["cooldown_remaining_seconds"]
    assert remaining > 0


def test_cooldown_does_not_block_clearing_detection() -> None:
    now = BASE_NOW
    data_cloud = _make_data(now=now, actual_solar_kw=1.0, forecast_kw=5.0)
    detector = SolarEventDetector()
    detector.evaluate(data_cloud, now)

    data_sunny = _make_data(now=now, actual_solar_kw=4.5, forecast_kw=5.0)
    detector.evaluate(data_sunny, now + timedelta(minutes=1))
    diag = data_sunny.cloud_event_diagnostics
    assert diag["status"] in ("clearing_pending", "cloud_event")


def test_cloud_event_status_written_while_in_cloud_but_not_clearing() -> None:
    now = BASE_NOW
    data = _make_data(now=now, actual_solar_kw=1.0, forecast_kw=5.0)
    detector = SolarEventDetector()
    detector.evaluate(data, now)

    data2 = _make_data(now=now, actual_solar_kw=1.1, forecast_kw=5.0)
    detector.evaluate(data2, now + timedelta(minutes=16))
    diag = data2.cloud_event_diagnostics
    assert diag["status"] == "cloud_event"
    assert diag["triggered"] is False


def test_depressed_avg_accumulated_from_cloud_samples() -> None:
    now = BASE_NOW
    data_cloud = _make_data(now=now, actual_solar_kw=1.0, forecast_kw=5.0)
    detector = SolarEventDetector()
    detector.evaluate(data_cloud, now)

    data2 = _make_data(now=now, actual_solar_kw=1.5, forecast_kw=5.0)
    detector.evaluate(data2, now + timedelta(minutes=16))

    diag = data2.cloud_event_diagnostics
    assert diag.get("depressed_avg_kw") is not None
    assert 0 < diag["depressed_avg_kw"] < 3.0


def test_clearing_pending_before_window() -> None:
    now = BASE_NOW
    data_cloud = _make_data(now=now, actual_solar_kw=1.0, forecast_kw=5.0)
    detector = SolarEventDetector()
    detector.evaluate(data_cloud, now)

    data_sunny = _make_data(now=now, actual_solar_kw=4.5, forecast_kw=5.0)
    detector.evaluate(data_sunny, now + timedelta(minutes=16))
    assert detector.evaluate(data_sunny, now + timedelta(minutes=25)) is False
    assert data_sunny.cloud_event_diagnostics["status"] == "clearing_pending"


def test_clearing_triggers_after_10_minute_window() -> None:
    now = BASE_NOW
    data_cloud = _make_data(now=now, actual_solar_kw=1.0, forecast_kw=5.0)
    detector = SolarEventDetector()
    detector.evaluate(data_cloud, now)

    data_sunny = _make_data(now=now, actual_solar_kw=4.5, forecast_kw=5.0)
    detector.evaluate(data_sunny, now + timedelta(minutes=16))
    assert detector.evaluate(data_sunny, now + timedelta(minutes=27)) is True

    diag = data_sunny.cloud_event_diagnostics
    assert diag["status"] == "triggered"
    assert diag["event_type"] == "clearing"
    assert diag["triggered"] is True


def test_cloud_scale_factor_reset_to_none_on_clearing() -> None:
    now = BASE_NOW
    data_cloud = _make_data(now=now, actual_solar_kw=1.0, forecast_kw=5.0)
    detector = SolarEventDetector()
    detector.evaluate(data_cloud, now)
    assert data_cloud.cloud_event_solar_scale_factor == pytest.approx(0.2, rel=0.01)

    data_sunny = _make_data(now=now, actual_solar_kw=4.5, forecast_kw=5.0)
    detector.evaluate(data_sunny, now + timedelta(minutes=16))
    detector.evaluate(data_sunny, now + timedelta(minutes=27))
    assert data_sunny.cloud_event_solar_scale_factor is None


def test_clearing_window_resets_if_production_drops_again() -> None:
    now = BASE_NOW
    data_cloud = _make_data(now=now, actual_solar_kw=1.0, forecast_kw=5.0)
    detector = SolarEventDetector()
    detector.evaluate(data_cloud, now)

    data_sunny = _make_data(now=now, actual_solar_kw=4.5, forecast_kw=5.0)
    data_cloudy2 = _make_data(now=now, actual_solar_kw=0.8, forecast_kw=5.0)

    detector.evaluate(data_sunny, now + timedelta(minutes=16))
    detector.evaluate(data_sunny, now + timedelta(minutes=20))
    detector.evaluate(data_cloudy2, now + timedelta(minutes=22))

    detector.evaluate(data_sunny, now + timedelta(minutes=23))
    assert detector.evaluate(data_sunny, now + timedelta(minutes=33)) is False
    assert detector.evaluate(data_sunny, now + timedelta(minutes=34)) is True


def test_falls_back_to_solcast_tomorrow_when_today_empty() -> None:
    now = BASE_NOW
    data = _make_data(now=now, actual_solar_kw=1.0, forecast_kw=5.0)
    data.solcast_today = []
    period_start = now.replace(second=0, microsecond=0)
    period_start = period_start - timedelta(minutes=period_start.minute % 30)
    data.solcast_tomorrow = [
        {"period_start": period_start.isoformat(), "pv_estimate": 5.0}
    ]
    detector = SolarEventDetector()

    assert detector.evaluate(data, now) is True


def test_slot_not_found_when_now_outside_all_periods() -> None:
    now = BASE_NOW
    data = _make_data(now=now, actual_solar_kw=1.0, forecast_kw=5.0)
    # Set period_start to 5 hours from now, so current time is outside the slot
    period_start = now + timedelta(hours=5)
    period_start = period_start.replace(second=0, microsecond=0)
    period_start = period_start - timedelta(minutes=period_start.minute % 30)
    data.solcast_today = [
        {
            "period_start": period_start.isoformat(),
            "pv_estimate": 5.0,
        }
    ]
    detector = SolarEventDetector()
    assert detector.evaluate(data, now) is False
    assert data.cloud_event_diagnostics["status"] == "no_forecast"
    assert data.cloud_event_diagnostics["triggered"] is False
    detector = SolarEventDetector()
