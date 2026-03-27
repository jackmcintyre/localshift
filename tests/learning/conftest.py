from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.localshift.const import (
    CONF_COOLING_THRESHOLD,
    CONF_HEATING_THRESHOLD,
    CONF_WEATHER_ENTITY,
    DEFAULT_COOLING_THRESHOLD,
    DEFAULT_HEATING_THRESHOLD,
)
from custom_components.localshift.learning.correlation import WeatherCorrelation
from custom_components.localshift.learning.temperature import (
    TemperatureForecastProvider,
)
from custom_components.localshift.learning.anomaly import WeatherAnomalyDetector
from custom_components.localshift.engine.optimizer_dp import PlannerAction


@pytest.fixture
def mock_hass():
    """Create a minimal mock HomeAssistant instance."""
    hass = MagicMock()
    hass.states = MagicMock()
    hass.states.get = MagicMock(return_value=None)
    hass.services = MagicMock()
    hass.services.async_call = AsyncMock(return_value=None)
    return hass


@pytest.fixture
def mock_entry():
    """Create a mock ConfigEntry with weather correlation settings."""
    entry = MagicMock()
    entry.options = {
        CONF_WEATHER_ENTITY: "weather.home",
        CONF_COOLING_THRESHOLD: DEFAULT_COOLING_THRESHOLD,
        CONF_HEATING_THRESHOLD: DEFAULT_HEATING_THRESHOLD,
    }
    entry.data = {
        CONF_WEATHER_ENTITY: "weather.home",
    }
    return entry


@pytest.fixture
def mock_entry_no_weather():
    """Create a mock ConfigEntry with no weather entity configured."""
    entry = MagicMock()
    entry.options = {}
    entry.data = {}
    return entry


@pytest.fixture
def mock_weather_store() -> AsyncMock:
    store = AsyncMock()
    store.async_load.return_value = None
    store.async_save.return_value = None
    return store


@pytest.fixture
def correlation(mock_hass, mock_entry, mock_weather_store):
    """Create a WeatherCorrelation instance with mocked store."""
    with patch(
        "custom_components.localshift.learning.correlation.Store"
    ) as mock_store_cls:
        mock_store_cls.return_value = mock_weather_store

        instance = WeatherCorrelation(mock_hass, mock_entry)
        instance._store = mock_weather_store
        return instance


@pytest.fixture
def correlation_no_weather(mock_hass, mock_entry_no_weather, mock_weather_store):
    """Create a WeatherCorrelation with no weather entity."""
    with patch(
        "custom_components.localshift.learning.correlation.Store"
    ) as mock_store_cls:
        mock_store_cls.return_value = mock_weather_store

        instance = WeatherCorrelation(mock_hass, mock_entry_no_weather)
        instance._store = mock_weather_store
        return instance


@pytest.fixture
def weather_provider(mock_hass, mock_entry) -> TemperatureForecastProvider:
    return TemperatureForecastProvider(mock_hass, mock_entry, "weather.home")


@pytest.fixture
def weather_provider_no_weather(
    mock_hass, mock_entry_no_weather
) -> TemperatureForecastProvider:
    return TemperatureForecastProvider(mock_hass, mock_entry_no_weather, None)


@pytest.fixture
def anomaly_detector() -> WeatherAnomalyDetector:
    return WeatherAnomalyDetector({})


def make_weather_state(temperature: float = 22.0, forecast_list: list | None = None):
    """Create a mock weather entity state."""
    state = MagicMock()
    state.attributes = {
        "temperature": temperature,
        "forecast": forecast_list or [],
    }
    return state


def make_forecast_entry(
    hours_ahead: float = 1.0,
    temperature: float = 22.0,
    condition: str = "sunny",
    now: datetime | None = None,
) -> dict:
    """Create a single forecast entry dict."""
    if now is None:
        now = datetime.now(UTC)
    forecast_time = now + timedelta(hours=hours_ahead)
    return {
        "datetime": forecast_time.isoformat(),
        "temperature": temperature,
        "condition": condition,
    }


def _build_slot_history(
    start: datetime,
    slot_minutes: int,
    power_values: list[float],
    soc_start: float = 20.0,
    soc_step: float = 0.2,
) -> dict[str, list[tuple[datetime, float]]]:
    power_history: list[tuple[datetime, float]] = []
    soc_history: list[tuple[datetime, float]] = []
    slot_delta = timedelta(minutes=slot_minutes)
    for index, power in enumerate(power_values):
        slot_start = start + index * slot_delta
        power_time = slot_start + slot_delta / 2
        soc_time = slot_start + slot_delta
        power_history.append((power_time, float(power)))
        soc_history.append((soc_time, soc_start + soc_step * (index + 1)))
    return {
        "power_history": power_history,
        "soc_history": soc_history,
    }


@pytest.fixture
def history() -> dict[str, Any]:
    start = datetime(2024, 1, 1, 0, 0, tzinfo=UTC)
    slot_minutes = 15
    values = [3.2 for _ in range(120)]
    data = _build_slot_history(
        start, slot_minutes, values, soc_start=25.0, soc_step=0.2
    )
    return {
        "start": start,
        "slot_minutes": slot_minutes,
        "power_history": data["power_history"],
        "soc_history": data["soc_history"],
    }


@pytest.fixture
def decisions(history) -> list[SimpleNamespace]:
    decisions_list: list[SimpleNamespace] = []
    slot_minutes = history["slot_minutes"]
    slot_delta = timedelta(minutes=slot_minutes)
    for index in range(len(history["power_history"])):
        slot_start = history["start"] + index * slot_delta
        decisions_list.append(
            SimpleNamespace(
                timestamp=slot_start + timedelta(minutes=1),
                mode_chosen=(
                    PlannerAction.CHARGE_GRID_BOOST
                    if index % 2 == 0
                    else PlannerAction.CHARGE_GRID_NORMAL
                ),
            )
        )
    return decisions_list


@pytest.fixture
def storage() -> AsyncMock:
    store = AsyncMock()
    store.async_load.return_value = None
    store.async_save.return_value = None
    return store
