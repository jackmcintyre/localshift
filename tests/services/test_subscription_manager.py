"""SubscriptionManager subscribes nothing once stopped (Issue #1149)."""

from __future__ import annotations

from datetime import time, timedelta
from unittest.mock import MagicMock, patch

from custom_components.localshift.services.subscription_manager import (
    SubscriptionManager,
)

_MODULE = "custom_components.localshift.services.subscription_manager"


def _manager() -> SubscriptionManager:
    interval = timedelta(minutes=1)
    return SubscriptionManager(
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        interval,
        interval,
        interval,
        interval,
    )


@patch(f"{_MODULE}.async_track_time_interval")
@patch(f"{_MODULE}.async_track_state_change_event")
def test_reschedule_while_running_replaces_the_timer(_state, _interval):
    """Control: a running manager does re-arm the daily summary."""
    manager = _manager()
    first, second = MagicMock(), MagicMock()
    with patch(f"{_MODULE}.async_track_time_change", side_effect=[MagicMock(), first]):
        manager.start(["sensor.price"], time(21, 0))
    with patch(f"{_MODULE}.async_track_time_change", return_value=second) as track:
        manager.reschedule_daily_summary(time(22, 0))

    first.assert_called_once()
    track.assert_called_once()
    assert manager._unsub_daily_summary is second


@patch(f"{_MODULE}.async_track_time_interval")
@patch(f"{_MODULE}.async_track_state_change_event")
def test_reschedule_after_stop_subscribes_nothing(_state, _interval):
    """A late options update must not re-arm the summary on a stopped
    manager: nothing would ever cancel it again."""
    manager = _manager()
    with patch(f"{_MODULE}.async_track_time_change"):
        manager.start(["sensor.price"], time(21, 0))
    manager.stop()

    with patch(f"{_MODULE}.async_track_time_change") as track:
        manager.reschedule_daily_summary(time(22, 0))

    track.assert_not_called()
    assert manager._unsub_daily_summary is None
