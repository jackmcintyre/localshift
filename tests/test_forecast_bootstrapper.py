"""Tests for ForecastBootstrapper."""

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.localshift.coordinator_data import CoordinatorData
from custom_components.localshift.forecast_bootstrapper import ForecastBootstrapper
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
    assert bootstrapper.solcast_ready is True
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
