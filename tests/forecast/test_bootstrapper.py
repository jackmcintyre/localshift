"""Tests for ForecastBootstrapper."""

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.localshift.coordinator import CoordinatorData
from custom_components.localshift.forecast.bootstrapper import ForecastBootstrapper
from tests.fixtures.ha_entities import MockState, MockStates


@pytest.mark.asyncio
async def test_forecast_bootstrapper_ready_triggers_compute(
    mock_hass_with_forecasts,
):
    """When Solcast is ready, bootstrapper computes and evaluates."""
    data = CoordinatorData()

    compute_derived_values = MagicMock()
    notify_listeners = MagicMock()
    evaluate_state_machine = AsyncMock()

    bootstrapper = ForecastBootstrapper(
        mock_hass_with_forecasts,
        data,
        lambda _key: "sensor.solcast_pv_forecast_forecast_today",
        lambda: None,
        compute_derived_values,
        notify_listeners,
        evaluate_state_machine,
        timedelta(seconds=1),
        1,
    )

    await bootstrapper.wait_for_solcast_and_compute()

    assert data.forecast_ready is True
    assert data.forecast_status in {"ready", "partial"}
    assert bootstrapper.forecast_computed_on_startup is True
    assert bootstrapper.retry_count == 0
    compute_derived_values.assert_called_once()
    notify_listeners.assert_called_once()
    evaluate_state_machine.assert_awaited_once()


@pytest.mark.asyncio
async def test_forecast_bootstrapper_max_retries_fallback():
    """When retries are exhausted, bootstrapper still computes once."""
    hass = MagicMock()
    hass.async_create_task = MagicMock()

    states = MockStates({
        "sensor.solcast_pv_forecast_forecast_today": MockState(
            entity_id="sensor.solcast_pv_forecast_forecast_today",
            state="unknown",
            attributes={},
        )
    })
    hass.states.get = states.get

    data = CoordinatorData()
    compute_derived_values = MagicMock()
    notify_listeners = MagicMock()
    evaluate_state_machine = AsyncMock()

    bootstrapper = ForecastBootstrapper(
        hass,
        data,
        lambda _key: "sensor.solcast_pv_forecast_forecast_today",
        lambda: None,
        compute_derived_values,
        notify_listeners,
        evaluate_state_machine,
        timedelta(seconds=1),
        0,
    )

    await bootstrapper.wait_for_solcast_and_compute()

    assert bootstrapper.forecast_computed_on_startup is True
    assert bootstrapper.retry_count == 0
    compute_derived_values.assert_called_once()
    notify_listeners.assert_called_once()
    evaluate_state_machine.assert_awaited_once()
    hass.async_create_task.assert_not_called()


def _unready_bootstrapper(max_retries: int):
    hass = MagicMock()
    hass.async_create_task = MagicMock()
    states = MockStates({
        "sensor.solcast_pv_forecast_forecast_today": MockState(
            entity_id="sensor.solcast_pv_forecast_forecast_today",
            state="unknown",
            attributes={},
        )
    })
    hass.states.get = states.get
    read_state = MagicMock()
    compute_derived_values = MagicMock()
    evaluate_state_machine = AsyncMock()
    bootstrapper = ForecastBootstrapper(
        hass,
        CoordinatorData(),
        lambda _key: "sensor.solcast_pv_forecast_forecast_today",
        read_state,
        compute_derived_values,
        MagicMock(),
        evaluate_state_machine,
        timedelta(seconds=0),
        max_retries,
    )
    return bootstrapper, read_state, compute_derived_values, evaluate_state_machine


@pytest.mark.asyncio
async def test_retry_after_the_sleep_computes_when_running():
    """Control: out of retries, the pass after the sleep computes."""
    bootstrapper, read_state, compute, evaluate = _unready_bootstrapper(0)

    await bootstrapper._retry_solcast_check()

    read_state.assert_called_once()
    compute.assert_called_once()
    evaluate.assert_awaited_once()


@pytest.mark.asyncio
async def test_retry_that_wakes_after_stop_ends_the_chain(caplog):
    """Issue #1149: stopped while the retry slept. It must not read state,
    recompute, evaluate, queue another retry or report Solcast as missing."""
    bootstrapper, read_state, compute, evaluate = _unready_bootstrapper(0)

    bootstrapper.stop()
    with caplog.at_level("INFO"):
        await bootstrapper._retry_solcast_check()

    read_state.assert_not_called()
    compute.assert_not_called()
    evaluate.assert_not_awaited()
    bootstrapper.hass.async_create_task.assert_not_called()
    assert "Solcast data not available" not in caplog.text
