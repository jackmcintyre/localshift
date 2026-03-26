from __future__ import annotations

from datetime import UTC, datetime, timedelta
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
