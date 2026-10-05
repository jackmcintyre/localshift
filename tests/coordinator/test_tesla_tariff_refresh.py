"""EntityMonitor.refresh_tesla_tariff and its wiring (Issue #1097)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.localshift.const import (
    CONF_TESLEMETRY_SELL_TARIFF,
    DEFAULT_ENTITY_IDS,
    TESLA_TARIFF_LOOKAHEAD_HOURS,
)
from custom_components.localshift.coordinator import entity_monitor as monitor_module
from custom_components.localshift.coordinator.data import CoordinatorData
from custom_components.localshift.coordinator.entity_monitor import EntityMonitor
from custom_components.localshift.coordinator.tick_scheduler import TickScheduler

AEDT = timezone(timedelta(hours=11))
NOW = datetime(2026, 10, 5, 21, 25, tzinfo=AEDT)
CALENDAR = "calendar.my_home_sell_tariff"
OFF_PEAK = (
    datetime(2026, 10, 5, 21, 0, tzinfo=AEDT),
    datetime(2026, 10, 6, 4, 0, tzinfo=AEDT),
)
EVENTS = [
    {
        "start": "2026-10-05T21:00:00+11:00",
        "end": "2026-10-06T04:00:00+11:00",
        "summary": "Off peak: 0.05/kWh",
    },
    {
        "start": "2026-10-06T04:00:00+11:00",
        "end": "2026-10-06T21:00:00+11:00",
        "summary": "On peak: 0.50/kWh",
    },
]


@pytest.fixture
def coordinator():
    coordinator = MagicMock()
    coordinator.data = CoordinatorData()
    coordinator.get_entity_id = MagicMock(return_value=CALENDAR)
    coordinator.hass.services.async_call = AsyncMock(
        return_value={CALENDAR: {"events": EVENTS}}
    )
    return coordinator


async def _refresh(coordinator) -> None:
    with patch.object(monitor_module.dt_util, "now", return_value=NOW):
        await EntityMonitor(coordinator).refresh_tesla_tariff()


def test_sell_tariff_calendar_has_a_default_entity():
    assert DEFAULT_ENTITY_IDS[CONF_TESLEMETRY_SELL_TARIFF] == CALENDAR


@pytest.mark.asyncio
async def test_refresh_stores_the_off_peak_periods(coordinator):
    await _refresh(coordinator)

    assert coordinator.data.export_blocked_periods == [OFF_PEAK]


@pytest.mark.asyncio
async def test_refresh_asks_the_configured_calendar_for_the_lookahead(coordinator):
    await _refresh(coordinator)

    coordinator.get_entity_id.assert_called_once_with(CONF_TESLEMETRY_SELL_TARIFF)
    coordinator.hass.services.async_call.assert_awaited_once_with(
        "calendar",
        "get_events",
        {
            "entity_id": CALENDAR,
            "start_date_time": NOW.isoformat(),
            "end_date_time": (
                NOW + timedelta(hours=TESLA_TARIFF_LOOKAHEAD_HOURS)
            ).isoformat(),
        },
        blocking=True,
        return_response=True,
    )


@pytest.mark.asyncio
async def test_failed_read_keeps_the_previous_periods(coordinator):
    coordinator.data.export_blocked_periods = [OFF_PEAK]
    coordinator.hass.services.async_call = AsyncMock(
        side_effect=RuntimeError("calendar entity not ready")
    )

    await _refresh(coordinator)

    assert coordinator.data.export_blocked_periods == [OFF_PEAK]


@pytest.mark.asyncio
async def test_persistent_failure_warns_once_then_again_after_recovery(
    coordinator, caplog
):
    """This runs every five minutes; a dead calendar must not flood the log."""
    monitor = EntityMonitor(coordinator)
    failing = AsyncMock(side_effect=RuntimeError("calendar entity not ready"))
    working = coordinator.hass.services.async_call

    async def refresh(call) -> int:
        coordinator.hass.services.async_call = call
        caplog.clear()
        with (
            caplog.at_level("WARNING", logger=monitor_module.__name__),
            patch.object(monitor_module.dt_util, "now", return_value=NOW),
        ):
            await monitor.refresh_tesla_tariff()
        return len(caplog.records)

    assert await refresh(failing) == 1
    assert await refresh(failing) == 0
    assert await refresh(working) == 0
    assert await refresh(failing) == 1


@pytest.mark.parametrize(
    "response",
    [None, {}, {CALENDAR: {}}, {CALENDAR: {"events": []}}, "unexpected", [1, 2]],
)
@pytest.mark.asyncio
async def test_empty_response_keeps_the_previous_periods(coordinator, response):
    coordinator.data.export_blocked_periods = [OFF_PEAK]
    coordinator.hass.services.async_call = AsyncMock(return_value=response)

    await _refresh(coordinator)

    assert coordinator.data.export_blocked_periods == [OFF_PEAK]


@pytest.mark.asyncio
async def test_tariff_changed_to_a_single_rate_clears_the_periods(coordinator):
    coordinator.data.export_blocked_periods = [OFF_PEAK]
    flat = [{**event, "summary": "All day: 0.30/kWh"} for event in EVENTS]
    coordinator.hass.services.async_call = AsyncMock(
        return_value={CALENDAR: {"events": flat}}
    )

    await _refresh(coordinator)

    assert coordinator.data.export_blocked_periods == []


@pytest.mark.asyncio
async def test_no_calendar_configured_reads_nothing(coordinator):
    coordinator.get_entity_id = MagicMock(return_value="")

    await _refresh(coordinator)

    coordinator.hass.services.async_call.assert_not_awaited()
    assert coordinator.data.export_blocked_periods == []


def test_medium_tick_schedules_the_refresh():
    """Nothing else calls it after startup, so the tick has to."""
    coordinator = MagicMock()
    coordinator.data = CoordinatorData()
    coordinator.state_machine.startup_grace_until = None
    coordinator.computation_engine = None
    coordinator.decision_telemetry = None
    coordinator.entity_monitor.refresh_tesla_tariff = MagicMock(
        return_value="tariff-refresh-coroutine"
    )
    scheduler = TickScheduler(coordinator)
    scheduler._backfill_solar_actual = MagicMock()

    scheduler.handle_medium_tick(NOW)

    coordinator.entity_monitor.refresh_tesla_tariff.assert_called_once_with()
    scheduled = [
        call.args for call in coordinator.hass.async_create_task.call_args_list
    ]
    assert ("tariff-refresh-coroutine", "localshift_tesla_tariff") in scheduled
