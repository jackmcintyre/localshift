"""The #826 load-forecast ceiling warns once per episode, not once per call (#1115).

On 2026-10-11 the clamp logged 155 WARNING lines in 46 minutes, all for hour 12:
every forecast call for an hour that is being clamped repeated the same line.
"""

import logging
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
from homeassistant.util import dt as dt_util

from custom_components.localshift.const import (
    LOAD_FORECAST_CEILING_FACTOR,
    LOAD_FORECAST_CEILING_LOG_QUIET_MIN,
)
from custom_components.localshift.forecast.load import LoadForecaster

PEAK_KW = 2.0
CEILING_KW = PEAK_KW * LOAD_FORECAST_CEILING_FACTOR
START = datetime(2026, 10, 11, 12, 13, tzinfo=UTC)


def _forecaster() -> LoadForecaster:
    entry = MagicMock()
    entry.options = {}
    return LoadForecaster(entry)


def _estimate(forecaster: LoadForecaster, hour: int, load_kw: float) -> float:
    """A present-tense estimate for ``hour`` off a live load of ``load_kw``."""
    kw, _ = forecaster.estimate_hourly_consumption_kw(
        hourly_avg_kw=dict.fromkeys(range(24), PEAK_KW),
        slot_hour=hour,
        current_hour=hour,
        current_load_kw=load_kw,
        recent_load_kw=load_kw,
        hours_ahead=0.0,
    )
    return kw


def _ceiling_lines(caplog: pytest.LogCaptureFixture, level: int) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.levelno == level and "LOAD_FORECAST_CEILING" in record.getMessage()
    ]


@pytest.fixture
def clock(monkeypatch):
    """A settable ``dt_util.utcnow`` for the forecaster module.

    The HA-local zone is pinned to UTC so the daily re-arm cannot depend on
    whatever zone an earlier test left behind.
    """
    monkeypatch.setattr(dt_util, "DEFAULT_TIME_ZONE", UTC)
    with patch(
        "custom_components.localshift.forecast.load.dt_util.utcnow",
        return_value=START,
    ) as mock_utcnow:
        yield mock_utcnow


def test_repeated_clamps_of_one_hour_warn_once(caplog, clock) -> None:
    forecaster = _forecaster()

    with caplog.at_level(logging.DEBUG):
        for step in range(6):
            clock.return_value = START + timedelta(seconds=20 * step)
            # The clamp itself is unchanged on every call, warned about or not.
            assert _estimate(forecaster, 12, 20.0 + step) == pytest.approx(CEILING_KW)

    warnings = _ceiling_lines(caplog, logging.WARNING)
    assert len(warnings) == 1
    assert warnings[0] == (
        "LOAD_FORECAST_CEILING (Issue #826): forecast 20.000 kW for hour 12 "
        "exceeds data-driven ceiling 6.000 kW; clamping"
    )
    # The repeats are still there for anyone who turns DEBUG on.
    assert len(_ceiling_lines(caplog, logging.DEBUG)) == 5


def test_a_second_episode_the_same_day_warns_again(caplog, clock) -> None:
    forecaster = _forecaster()
    quiet = timedelta(minutes=LOAD_FORECAST_CEILING_LOG_QUIET_MIN)

    with caplog.at_level(logging.DEBUG):
        _estimate(forecaster, 12, 20.0)
        # Still inside the quiet period: the same episode.
        clock.return_value = START + quiet - timedelta(seconds=1)
        _estimate(forecaster, 12, 20.0)
        # Unclamped calls in between neither warn nor reset anything.
        assert _estimate(forecaster, 12, 1.0) == pytest.approx(1.0)
        # No clamp for a full quiet period, then it comes back.
        clock.return_value += quiet
        assert _estimate(forecaster, 12, 20.0) == pytest.approx(CEILING_KW)

    assert len(_ceiling_lines(caplog, logging.WARNING)) == 2
    assert len(_ceiling_lines(caplog, logging.DEBUG)) == 1


def test_an_episode_that_keeps_clamping_stays_one_warning(caplog, clock) -> None:
    """The quiet period runs from the LAST clamp, so a clamp that carries on for
    longer than the period is still one episode."""
    forecaster = _forecaster()

    with caplog.at_level(logging.WARNING):
        for minute in range(0, 4 * LOAD_FORECAST_CEILING_LOG_QUIET_MIN, 5):
            clock.return_value = START + timedelta(minutes=minute)
            _estimate(forecaster, 12, 20.0)

    assert len(_ceiling_lines(caplog, logging.WARNING)) == 1


def test_each_clamped_hour_warns_for_itself(caplog, clock) -> None:
    forecaster = _forecaster()

    with caplog.at_level(logging.WARNING):
        _estimate(forecaster, 12, 20.0)
        _estimate(forecaster, 13, 20.0)
        _estimate(forecaster, 12, 20.0)

    warnings = _ceiling_lines(caplog, logging.WARNING)
    assert len(warnings) == 2
    assert "for hour 12" in warnings[0]
    assert "for hour 13" in warnings[1]


def test_a_new_forecaster_warns_on_its_first_clamp(caplog, clock) -> None:
    """A restart builds a new forecaster, so a clamp still running across it is
    announced again instead of continuing silently."""
    with caplog.at_level(logging.WARNING):
        _estimate(_forecaster(), 12, 20.0)
        _estimate(_forecaster(), 12, 20.0)

    assert len(_ceiling_lines(caplog, logging.WARNING)) == 2


# --- Daily re-arm -----------------------------------------------------------
#
# The quiet period runs from the last clamp, so a clamp that recurs on every
# cycle for days would warn once and then stay silent until a restart. Each
# HA-local calendar day on which an hour is clamped warns at least once.

SYDNEY = ZoneInfo("Australia/Sydney")  # UTC+11 on these dates


def _sydney(day: int, hour: int, minute: int) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=SYDNEY).astimezone(UTC)


def test_an_uninterrupted_clamp_warns_again_on_a_new_local_day(
    caplog, clock, monkeypatch
) -> None:
    monkeypatch.setattr(dt_util, "DEFAULT_TIME_ZONE", SYDNEY)
    forecaster = _forecaster()
    start = _sydney(11, 23, 40)

    with caplog.at_level(logging.DEBUG):
        # 23:40 on the 11th to 00:35 on the 12th, never 15 minutes quiet.
        for minute in range(0, 60, 5):
            clock.return_value = start + timedelta(minutes=minute)
            # Logging only: the clamp is the same on every call.
            assert _estimate(forecaster, 23, 20.0) == pytest.approx(CEILING_KW)

    # One for the 11th, one for the first clamp of the 12th, and the rest of
    # the 12th is the same episode.
    assert len(_ceiling_lines(caplog, logging.WARNING)) == 2
    assert len(_ceiling_lines(caplog, logging.DEBUG)) == 10


def test_the_day_boundary_is_local_midnight_not_utc_midnight(
    caplog, clock, monkeypatch
) -> None:
    """10:40 to 11:35 Sydney time is one local day, though it crosses 00:00 UTC."""
    monkeypatch.setattr(dt_util, "DEFAULT_TIME_ZONE", SYDNEY)
    forecaster = _forecaster()
    start = _sydney(12, 10, 40)
    assert start.date() != (start + timedelta(minutes=55)).date()

    with caplog.at_level(logging.DEBUG):
        for minute in range(0, 60, 5):
            clock.return_value = start + timedelta(minutes=minute)
            assert _estimate(forecaster, 10, 20.0) == pytest.approx(CEILING_KW)

    assert len(_ceiling_lines(caplog, logging.WARNING)) == 1
    assert len(_ceiling_lines(caplog, logging.DEBUG)) == 11


def test_a_clamp_that_runs_for_days_warns_once_per_day_and_stays_bounded(
    caplog, clock
) -> None:
    forecaster = _forecaster()
    start = datetime(2026, 10, 11, 0, 5, tzinfo=UTC)

    with caplog.at_level(logging.WARNING):
        for step in range(3 * 24 * 6):  # every 10 minutes for three days
            clock.return_value = start + timedelta(minutes=10 * step)
            for hour in range(24):
                assert _estimate(forecaster, hour, 20.0) == pytest.approx(CEILING_KW)

    warnings = _ceiling_lines(caplog, logging.WARNING)
    assert len(warnings) == 3 * 24
    assert sum("for hour 12 " in line for line in warnings) == 3
    latch_state = [
        value
        for name, value in vars(forecaster).items()
        if name.startswith("_ceiling_clamp") and isinstance(value, dict)
    ]
    assert latch_state
    assert all(len(state) <= 24 for state in latch_state)
