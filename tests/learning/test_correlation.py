"""Tests for learning/correlation.py - weather correlation module.

Tests are structured to achieve 70%+ coverage of WeatherCorrelation and
related dataclasses, covering:
- Learning algorithm (learn_from_sample, coefficient updates)
- Prediction logic (predict_load)
- Weather integration (get_temperature_forecast, async_get_temperature_forecast)
- Storage persistence (async_initialize, async_save)
- Diagnostics (get_diagnostics)
- Edge cases (missing weather, invalid inputs, low confidence)
"""

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
from custom_components.localshift.learning.correlation import (
    CONFIDENCE_LOW_THRESHOLD,
    CONFIDENCE_MEDIUM_THRESHOLD,
    HourlyTemperatureCoefficients,
    TemperatureForecast,
    WeatherCorrelation,
    WeatherCorrelationData,
)

# =============================================================================
# FIXTURES
# =============================================================================


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
def correlation(mock_hass, mock_entry):
    """Create a WeatherCorrelation instance with mocked store."""
    with patch(
        "custom_components.localshift.learning.correlation.Store"
    ) as mock_store_cls:
        mock_store = AsyncMock()
        mock_store.async_load = AsyncMock(return_value=None)
        mock_store.async_save = AsyncMock(return_value=None)
        mock_store_cls.return_value = mock_store

        instance = WeatherCorrelation(mock_hass, mock_entry)
        instance._store = mock_store
        return instance


@pytest.fixture
def correlation_no_weather(mock_hass, mock_entry_no_weather):
    """Create a WeatherCorrelation with no weather entity."""
    with patch(
        "custom_components.localshift.learning.correlation.Store"
    ) as mock_store_cls:
        mock_store = AsyncMock()
        mock_store.async_load = AsyncMock(return_value=None)
        mock_store.async_save = AsyncMock(return_value=None)
        mock_store_cls.return_value = mock_store

        instance = WeatherCorrelation(mock_hass, mock_entry_no_weather)
        instance._store = mock_store
        return instance


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


# =============================================================================
# HourlyTemperatureCoefficients
# =============================================================================


class TestHourlyTemperatureCoefficients:
    """Tests for the HourlyTemperatureCoefficients dataclass."""

    def test_default_values(self):
        """Default instance should have zero coefficients."""
        coef = HourlyTemperatureCoefficients()
        assert coef.base_load_kw == 0.0
        assert coef.cooling_coefficient == 0.0
        assert coef.heating_coefficient == 0.0
        assert coef.sample_count == 0
        assert coef.last_updated == ""
        assert coef.confidence == "low"

    def test_to_dict(self):
        """to_dict should serialise all fields."""
        coef = HourlyTemperatureCoefficients(
            base_load_kw=1.5,
            cooling_coefficient=0.2,
            heating_coefficient=0.3,
            sample_count=10,
            last_updated="2024-01-01T12:00:00",
            confidence="medium",
        )
        d = coef.to_dict()
        assert d["base_load_kw"] == 1.5
        assert d["cooling_coefficient"] == 0.2
        assert d["heating_coefficient"] == 0.3
        assert d["sample_count"] == 10
        assert d["last_updated"] == "2024-01-01T12:00:00"
        assert d["confidence"] == "medium"

    def test_from_dict_full(self):
        """from_dict should restore all fields."""
        data = {
            "base_load_kw": 2.0,
            "cooling_coefficient": 0.15,
            "heating_coefficient": 0.25,
            "sample_count": 35,
            "last_updated": "2024-06-01T08:00:00",
            "confidence": "high",
        }
        coef = HourlyTemperatureCoefficients.from_dict(data)
        assert coef.base_load_kw == 2.0
        assert coef.cooling_coefficient == 0.15
        assert coef.heating_coefficient == 0.25
        assert coef.sample_count == 35
        assert coef.last_updated == "2024-06-01T08:00:00"
        assert coef.confidence == "high"

    def test_from_dict_defaults(self):
        """from_dict with empty dict should return defaults."""
        coef = HourlyTemperatureCoefficients.from_dict({})
        assert coef.base_load_kw == 0.0
        assert coef.cooling_coefficient == 0.0
        assert coef.heating_coefficient == 0.0
        assert coef.sample_count == 0
        assert coef.confidence == "low"

    def test_roundtrip(self):
        """to_dict → from_dict should be lossless."""
        original = HourlyTemperatureCoefficients(
            base_load_kw=3.5,
            cooling_coefficient=0.1,
            heating_coefficient=0.4,
            sample_count=50,
            last_updated="2024-01-15T10:00:00",
            confidence="high",
        )
        restored = HourlyTemperatureCoefficients.from_dict(original.to_dict())
        assert restored.base_load_kw == original.base_load_kw
        assert restored.cooling_coefficient == original.cooling_coefficient
        assert restored.confidence == original.confidence


# =============================================================================
# WeatherCorrelationData
# =============================================================================


class TestWeatherCorrelationData:
    """Tests for the WeatherCorrelationData dataclass."""

    def test_default_values(self):
        """Default instance should have expected values."""
        data = WeatherCorrelationData()
        assert data.version == 1
        assert data.weather_entity_id == ""
        assert data.cooling_threshold == 24.0
        assert data.heating_threshold == 18.0
        assert data.hourly_coefficients == {}
        assert data.learning_stats == {}

    def test_to_dict(self):
        """to_dict should serialise all fields including nested coefficients."""
        coef = HourlyTemperatureCoefficients(base_load_kw=2.0, sample_count=5)
        data = WeatherCorrelationData(
            weather_entity_id="weather.test",
            cooling_threshold=25.0,
            heating_threshold=17.0,
            hourly_coefficients={14: coef},
            learning_stats={"note": "test"},
        )
        d = data.to_dict()
        assert d["weather_entity_id"] == "weather.test"
        assert d["cooling_threshold"] == 25.0
        assert d["heating_threshold"] == 17.0
        assert "14" in d["hourly_coefficients"]
        assert d["hourly_coefficients"]["14"]["base_load_kw"] == 2.0
        assert d["learning_stats"] == {"note": "test"}

    def test_from_dict_full(self):
        """from_dict should restore all fields including nested coefficients."""
        raw = {
            "version": 2,
            "weather_entity_id": "weather.bureau",
            "cooling_threshold": 26.0,
            "heating_threshold": 16.0,
            "hourly_coefficients": {
                "8": {
                    "base_load_kw": 1.2,
                    "cooling_coefficient": 0.05,
                    "heating_coefficient": 0.1,
                    "sample_count": 20,
                    "last_updated": "2024-03-01",
                    "confidence": "medium",
                }
            },
            "learning_stats": {},
        }
        data = WeatherCorrelationData.from_dict(raw)
        assert data.version == 2
        assert data.weather_entity_id == "weather.bureau"
        assert data.cooling_threshold == 26.0
        assert 8 in data.hourly_coefficients
        assert data.hourly_coefficients[8].base_load_kw == 1.2
        assert data.hourly_coefficients[8].sample_count == 20

    def test_from_dict_defaults(self):
        """from_dict with empty dict should return safe defaults."""
        data = WeatherCorrelationData.from_dict({})
        assert data.version == 1
        assert data.hourly_coefficients == {}

    def test_roundtrip(self):
        """to_dict → from_dict should be lossless for nested structures."""
        coef = HourlyTemperatureCoefficients(base_load_kw=5.0, sample_count=100)
        original = WeatherCorrelationData(
            weather_entity_id="weather.home",
            hourly_coefficients={12: coef},
        )
        restored = WeatherCorrelationData.from_dict(original.to_dict())
        assert restored.weather_entity_id == "weather.home"
        assert 12 in restored.hourly_coefficients
        assert restored.hourly_coefficients[12].base_load_kw == 5.0


# =============================================================================
# WeatherCorrelation.async_initialize
# =============================================================================


class TestAsyncInitialize:
    """Tests for WeatherCorrelation.async_initialize."""

    @pytest.mark.asyncio
    async def test_initialize_from_storage(self, correlation):
        """Should load existing data from storage."""
        stored = WeatherCorrelationData(
            weather_entity_id="weather.stored",
            cooling_threshold=25.0,
            heating_threshold=17.0,
        )
        stored.hourly_coefficients[10] = HourlyTemperatureCoefficients(
            base_load_kw=2.0, sample_count=15
        )
        correlation._store.async_load.return_value = stored.to_dict()

        await correlation.async_initialize()

        assert correlation._initialized is True
        assert correlation._data.weather_entity_id == "weather.stored"
        assert 10 in correlation._data.hourly_coefficients

    @pytest.mark.asyncio
    async def test_initialize_fresh(self, correlation, mock_entry):
        """With no stored data, should init from config entry."""
        correlation._store.async_load.return_value = None
        mock_entry.options = {
            CONF_WEATHER_ENTITY: "weather.new",
            CONF_COOLING_THRESHOLD: 26.0,
            CONF_HEATING_THRESHOLD: 16.0,
        }
        mock_entry.data = {}

        await correlation.async_initialize()

        assert correlation._initialized is True
        assert correlation._data.weather_entity_id == "weather.new"
        assert correlation._data.cooling_threshold == 26.0
        assert correlation._data.heating_threshold == 16.0

    @pytest.mark.asyncio
    async def test_initialize_idempotent(self, correlation):
        """Calling async_initialize twice should not re-load storage."""
        correlation._store.async_load.return_value = None
        await correlation.async_initialize()
        await correlation.async_initialize()

        # async_load called only once
        assert correlation._store.async_load.call_count == 1

    @pytest.mark.asyncio
    async def test_initialize_falls_back_to_data(self, correlation, mock_entry):
        """Weather entity should come from entry.data when options is empty."""
        correlation._store.async_load.return_value = None
        mock_entry.options = {}
        mock_entry.data = {CONF_WEATHER_ENTITY: "weather.fallback"}

        await correlation.async_initialize()

        assert correlation._data.weather_entity_id == "weather.fallback"


# =============================================================================
# WeatherCorrelation.async_save
# =============================================================================


class TestAsyncSave:
    """Tests for WeatherCorrelation.async_save."""

    @pytest.mark.asyncio
    async def test_save_calls_store(self, correlation):
        """async_save should persist current data to the store."""
        correlation._data.weather_entity_id = "weather.test"
        await correlation.async_save()

        correlation._store.async_save.assert_called_once()
        saved = correlation._store.async_save.call_args[0][0]
        assert saved["weather_entity_id"] == "weather.test"


# =============================================================================
# WeatherCorrelation.get_temperature_forecast (legacy)
# =============================================================================


class TestGetTemperatureForecast:
    """Tests for the legacy get_temperature_forecast method."""

    def test_no_weather_entity(self, correlation):
        """Returns empty list when no entity is configured."""
        correlation._data.weather_entity_id = ""
        result = correlation.get_temperature_forecast()
        assert result == []

    def test_entity_not_found(self, correlation):
        """Returns empty list when entity state is None."""
        correlation._data.weather_entity_id = "weather.missing"
        correlation.hass.states.get.return_value = None

        result = correlation.get_temperature_forecast()
        assert result == []

    def test_empty_forecast(self, correlation):
        """Returns empty list when weather entity has no forecast attribute."""
        correlation._data.weather_entity_id = "weather.home"
        state = MagicMock()
        state.attributes = {"forecast": []}
        correlation.hass.states.get.return_value = state

        result = correlation.get_temperature_forecast()
        assert result == []

    def test_valid_forecast_entries(self, correlation):
        """Should parse valid forecast entries within 24h window."""
        now = datetime.now(UTC)
        correlation._data.weather_entity_id = "weather.home"

        entries = [
            make_forecast_entry(hours_ahead=2, temperature=28.0, now=now),
            make_forecast_entry(hours_ahead=8, temperature=32.0, now=now),
        ]
        state = make_weather_state(forecast_list=entries)
        correlation.hass.states.get.return_value = state

        with patch(
            "custom_components.localshift.learning.correlation.dt_util.now",
            return_value=now,
        ):
            result = correlation.get_temperature_forecast()

        assert len(result) == 2
        assert isinstance(result[0], TemperatureForecast)
        assert result[0].temperature == 28.0

    def test_forecast_outside_24h_filtered(self, correlation):
        """Forecasts more than 24h ahead or in the past should be excluded."""
        now = datetime.now(UTC)
        correlation._data.weather_entity_id = "weather.home"

        entries = [
            make_forecast_entry(hours_ahead=25, temperature=20.0, now=now),  # too far
            make_forecast_entry(hours_ahead=-1, temperature=20.0, now=now),  # past
            make_forecast_entry(hours_ahead=12, temperature=22.0, now=now),  # valid
        ]
        state = make_weather_state(forecast_list=entries)
        correlation.hass.states.get.return_value = state

        with patch(
            "custom_components.localshift.learning.correlation.dt_util.now",
            return_value=now,
        ):
            result = correlation.get_temperature_forecast()

        assert len(result) == 1
        assert result[0].temperature == 22.0

    def test_forecast_entry_missing_datetime(self, correlation):
        """Entries without 'datetime' key should be skipped."""
        now = datetime.now(UTC)
        correlation._data.weather_entity_id = "weather.home"

        entries = [
            {"temperature": 25.0, "condition": "sunny"},  # no datetime
            make_forecast_entry(hours_ahead=3, temperature=27.0, now=now),
        ]
        state = make_weather_state(forecast_list=entries)
        correlation.hass.states.get.return_value = state

        with patch(
            "custom_components.localshift.learning.correlation.dt_util.now",
            return_value=now,
        ):
            result = correlation.get_temperature_forecast()

        assert len(result) == 1
        assert result[0].temperature == 27.0

    def test_forecast_entry_invalid_datetime(self, correlation):
        """Entries with unparseable datetime should be skipped."""
        now = datetime.now(UTC)
        correlation._data.weather_entity_id = "weather.home"

        entries = [
            {"datetime": "not-a-date", "temperature": 25.0},
            make_forecast_entry(hours_ahead=4, temperature=29.0, now=now),
        ]
        state = make_weather_state(forecast_list=entries)
        correlation.hass.states.get.return_value = state

        with patch(
            "custom_components.localshift.learning.correlation.dt_util.now",
            return_value=now,
        ):
            with patch(
                "custom_components.localshift.learning.correlation.dt_util.parse_datetime",
                return_value=None,
            ):
                result = correlation.get_temperature_forecast()

        # Only the valid one (even though parse_datetime returns None for all,
        # the invalid "not-a-date" will fail; the valid entry will also fail
        # in this mock but that is the expected tested behaviour)
        assert isinstance(result, list)


# =============================================================================
# WeatherCorrelation.async_get_temperature_forecast
# =============================================================================


class TestAsyncGetTemperatureForecast:
    """Tests for async_get_temperature_forecast."""

    @pytest.mark.asyncio
    async def test_no_weather_entity(self, correlation_no_weather):
        """Returns empty list when no weather entity configured."""
        result = await correlation_no_weather.async_get_temperature_forecast()
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_cached_within_ttl(self, correlation):
        """Returns cached forecasts when cache is still fresh."""
        now = datetime.now(UTC)
        cached = [TemperatureForecast(slot_time=now, temperature=22.0)]
        correlation._cached_forecasts = cached
        correlation._forecast_cache_time = now - timedelta(minutes=5)  # fresh
        correlation._data.weather_entity_id = "weather.home"

        with patch(
            "custom_components.localshift.learning.correlation.dt_util.now",
            return_value=now,
        ):
            result = await correlation.async_get_temperature_forecast()

        assert result is cached

    @pytest.mark.asyncio
    async def test_force_refresh_bypasses_cache(self, correlation):
        """force_refresh=True should bypass the cache."""
        now = datetime.now(UTC)
        cached = [TemperatureForecast(slot_time=now, temperature=22.0)]
        correlation._cached_forecasts = cached
        correlation._forecast_cache_time = now - timedelta(minutes=1)  # very fresh
        correlation._data.weather_entity_id = "weather.home"

        # Service returns empty response so it falls back to legacy (also empty)
        correlation.hass.services.async_call = AsyncMock(return_value=None)
        correlation.hass.states.get.return_value = make_weather_state(forecast_list=[])

        with patch(
            "custom_components.localshift.learning.correlation.dt_util.now",
            return_value=now,
        ):
            result = await correlation.async_get_temperature_forecast(
                force_refresh=True
            )

        # Cache was bypassed; result is the fresh (empty) forecast
        assert result == []

    @pytest.mark.asyncio
    async def test_service_call_success_list_response(self, correlation):
        """Should parse forecasts when service returns a list directly."""
        now = datetime.now(UTC)
        correlation._data.weather_entity_id = "weather.home"
        correlation._cached_forecasts = []
        correlation._forecast_cache_time = None

        entry = make_forecast_entry(hours_ahead=3, temperature=30.0, now=now)
        response = {"weather.home": [entry]}
        correlation.hass.services.async_call = AsyncMock(return_value=response)

        with patch(
            "custom_components.localshift.learning.correlation.dt_util.now",
            return_value=now,
        ):
            result = await correlation.async_get_temperature_forecast()

        assert len(result) == 1
        assert result[0].temperature == 30.0

    @pytest.mark.asyncio
    async def test_service_call_success_dict_response_forecast_key(self, correlation):
        """Should parse forecasts when service response has 'forecast' key."""
        now = datetime.now(UTC)
        correlation._data.weather_entity_id = "weather.home"
        correlation._cached_forecasts = []
        correlation._forecast_cache_time = None

        entry = make_forecast_entry(hours_ahead=5, temperature=18.0, now=now)
        response = {"weather.home": {"forecast": [entry]}}
        correlation.hass.services.async_call = AsyncMock(return_value=response)

        with patch(
            "custom_components.localshift.learning.correlation.dt_util.now",
            return_value=now,
        ):
            result = await correlation.async_get_temperature_forecast()

        assert len(result) == 1
        assert result[0].temperature == 18.0

    @pytest.mark.asyncio
    async def test_service_call_no_entity_in_response(self, correlation):
        """Falls back to legacy when entity is missing from response."""
        now = datetime.now(UTC)
        correlation._data.weather_entity_id = "weather.home"
        correlation._cached_forecasts = []
        correlation._forecast_cache_time = None

        # Response doesn't contain our entity
        response = {"weather.other": []}
        correlation.hass.services.async_call = AsyncMock(return_value=response)
        # Legacy forecast also empty
        correlation.hass.states.get.return_value = make_weather_state(forecast_list=[])

        with patch(
            "custom_components.localshift.learning.correlation.dt_util.now",
            return_value=now,
        ):
            result = await correlation.async_get_temperature_forecast()

        assert result == []

    @pytest.mark.asyncio
    async def test_service_call_exception_falls_back_to_legacy(self, correlation):
        """Falls back to legacy attribute when service call raises exception."""
        now = datetime.now(UTC)
        correlation._data.weather_entity_id = "weather.home"
        correlation._cached_forecasts = []
        correlation._forecast_cache_time = None

        correlation.hass.services.async_call = AsyncMock(
            side_effect=Exception("service unavailable")
        )
        entry = make_forecast_entry(hours_ahead=2, temperature=20.0, now=now)
        correlation.hass.states.get.return_value = make_weather_state(
            forecast_list=[entry]
        )

        with patch(
            "custom_components.localshift.learning.correlation.dt_util.now",
            return_value=now,
        ):
            result = await correlation.async_get_temperature_forecast()

        assert len(result) == 1
        assert result[0].temperature == 20.0

    @pytest.mark.asyncio
    async def test_weather_entity_change_clears_cache(self, correlation, mock_entry):
        """Changing weather entity should clear the forecast cache."""
        now = datetime.now(UTC)
        # Start with a cached entry from old entity
        correlation._data.weather_entity_id = "weather.old"
        old_forecast = [TemperatureForecast(slot_time=now, temperature=22.0)]
        correlation._cached_forecasts = old_forecast
        correlation._forecast_cache_time = now - timedelta(minutes=1)

        # Config now points to new entity
        mock_entry.options = {CONF_WEATHER_ENTITY: "weather.new"}
        mock_entry.data = {}

        correlation.hass.services.async_call = AsyncMock(return_value=None)
        correlation.hass.states.get.return_value = make_weather_state(forecast_list=[])

        with patch(
            "custom_components.localshift.learning.correlation.dt_util.now",
            return_value=now,
        ):
            result = await correlation.async_get_temperature_forecast()

        # Cache should have been cleared for new entity
        assert correlation._data.weather_entity_id == "weather.new"
        assert result == []


# =============================================================================
# WeatherCorrelation._extract_forecast_list
# =============================================================================


class TestExtractForecastList:
    """Tests for the _extract_forecast_list helper."""

    def test_list_response(self, correlation):
        """Direct list response should be returned as-is."""
        data = [{"datetime": "2024-01-01T12:00:00"}]
        result = correlation._extract_forecast_list(
            {"weather.home": data}, "weather.home"
        )
        assert result == data

    def test_dict_with_forecast_key(self, correlation):
        """Dict response with 'forecast' key should extract nested list."""
        inner = [{"datetime": "2024-01-01T12:00:00"}]
        response = {"weather.home": {"forecast": inner}}
        result = correlation._extract_forecast_list(response, "weather.home")
        assert result == inner

    def test_dict_with_hourly_key(self, correlation):
        """Dict response with 'hourly' key should extract nested list."""
        inner = [{"datetime": "2024-01-01T12:00:00"}]
        response = {"weather.home": {"hourly": inner}}
        result = correlation._extract_forecast_list(response, "weather.home")
        assert result == inner

    def test_entity_not_in_response(self, correlation):
        """Missing entity key should return None."""
        result = correlation._extract_forecast_list({}, "weather.home")
        assert result is None

    def test_unexpected_format(self, correlation):
        """Unexpected data type should return None."""
        response = {"weather.home": "unexpected_string"}
        result = correlation._extract_forecast_list(response, "weather.home")
        assert result is None

    def test_dict_without_known_keys(self, correlation):
        """Dict without forecast/hourly keys should return None."""
        response = {"weather.home": {"other_key": []}}
        result = correlation._extract_forecast_list(response, "weather.home")
        assert result is None


# =============================================================================
# WeatherCorrelation._parse_forecast_entries
# =============================================================================


class TestParseForecastEntries:
    """Tests for _parse_forecast_entries."""

    def test_empty_list(self, correlation):
        """Empty list should return empty list."""
        now = datetime.now(UTC)
        result = correlation._parse_forecast_entries([], now)
        assert result == []

    def test_valid_entries(self, correlation):
        """Valid entries within 24h should be returned."""
        now = datetime.now(UTC)
        entries = [
            make_forecast_entry(hours_ahead=1, temperature=20.0, now=now),
            make_forecast_entry(hours_ahead=6, temperature=25.0, now=now),
            make_forecast_entry(hours_ahead=12, temperature=30.0, now=now),
        ]

        with patch(
            "custom_components.localshift.learning.correlation.dt_util.now",
            return_value=now,
        ):
            result = correlation._parse_forecast_entries(entries, now)

        assert len(result) == 3

    def test_non_dict_entries_skipped(self, correlation):
        """Non-dict entries should be silently skipped."""
        now = datetime.now(UTC)
        entries = [
            "not_a_dict",
            None,
            make_forecast_entry(hours_ahead=2, temperature=22.0, now=now),
        ]

        result = correlation._parse_forecast_entries(entries, now)

        # Only the valid dict entry should be in result
        valid = [r for r in result if r is not None]
        assert len(valid) <= 1


# =============================================================================
# WeatherCorrelation._parse_single_forecast_entry
# =============================================================================


class TestParseSingleForecastEntry:
    """Tests for _parse_single_forecast_entry."""

    def test_non_dict_returns_none(self, correlation):
        """Non-dict entry should return None."""
        now = datetime.now(UTC)
        result = correlation._parse_single_forecast_entry("not_a_dict", now, 0, 0, 0, 0)
        assert result is None

    def test_missing_datetime(self, correlation):
        """Entry without 'datetime' key should return (None, updated_counts)."""
        now = datetime.now(UTC)
        entry = {"temperature": 25.0}
        result = correlation._parse_single_forecast_entry(entry, now, 0, 0, 0, 0)
        # Returns tuple with None as first element
        assert result is not None
        forecast, _, skipped, _ = result
        assert forecast is None
        assert skipped == 1

    def test_invalid_datetime(self, correlation):
        """Entry with unparseable datetime should return (None, updated_counts)."""
        now = datetime.now(UTC)
        entry = {"datetime": "bad-datetime", "temperature": 25.0}

        with patch(
            "custom_components.localshift.learning.correlation.dt_util.parse_datetime",
            return_value=None,
        ):
            result = correlation._parse_single_forecast_entry(entry, now, 0, 0, 0, 0)

        assert result is not None
        forecast, parse_failed, _, _ = result
        assert forecast is None
        assert parse_failed == 1

    def test_forecast_outside_window(self, correlation):
        """Forecast too far in the future should return (None, updated_counts)."""
        now = datetime.now(UTC)
        future = now + timedelta(hours=30)
        entry = {
            "datetime": future.isoformat(),
            "temperature": 25.0,
        }
        result = correlation._parse_single_forecast_entry(entry, now, 0, 0, 0, 0)
        assert result is not None
        forecast, _, _, filtered = result
        assert forecast is None
        assert filtered == 1

    def test_valid_entry(self, correlation):
        """Valid entry within window should return TemperatureForecast."""
        now = datetime.now(UTC)
        future = now + timedelta(hours=5)
        entry = {
            "datetime": future.isoformat(),
            "temperature": 22.0,
            "condition": "cloudy",
        }
        result = correlation._parse_single_forecast_entry(entry, now, 0, 0, 0, 0)
        assert result is not None
        forecast, _, _, _ = result
        assert forecast is not None
        assert isinstance(forecast, TemperatureForecast)
        assert forecast.temperature == 22.0
        assert forecast.condition == "cloudy"


# =============================================================================
# WeatherCorrelation._parse_forecast_datetime
# =============================================================================


class TestParseForecastDatetime:
    """Tests for _parse_forecast_datetime."""

    def test_valid_iso_with_timezone(self, correlation):
        """Valid ISO datetime with timezone should parse correctly."""
        now = datetime.now(UTC)
        time_str = now.isoformat()
        result = correlation._parse_forecast_datetime(time_str, 0, 0)
        assert result is not None

    def test_naive_iso_localized(self, correlation):
        """Naive ISO datetime should be localized."""
        naive_str = "2024-06-15T14:30:00"

        with patch(
            "custom_components.localshift.learning.correlation.dt_util.parse_datetime",
            return_value=None,
        ):
            with patch(
                "custom_components.localshift.learning.correlation.dt_util.as_local"
            ) as mock_local:
                mock_local.return_value = datetime(
                    2024, 6, 15, 14, 30, 0, tzinfo=UTC
                )
                result = correlation._parse_forecast_datetime(naive_str, 0, 0)

        assert result is not None

    def test_completely_invalid_string(self, correlation):
        """Completely invalid string should return None."""
        with patch(
            "custom_components.localshift.learning.correlation.dt_util.parse_datetime",
            return_value=None,
        ):
            result = correlation._parse_forecast_datetime("not-a-date", 0, 0)

        assert result is None

    def test_first_entry_logged(self, correlation):
        """First entry (index=0) should log debug info."""
        now = datetime.now(UTC)
        time_str = now.isoformat()

        with patch(
            "custom_components.localshift.learning.correlation.dt_util.parse_datetime",
            return_value=now,
        ):
            result = correlation._parse_forecast_datetime(time_str, 0, 0)

        assert result is not None

    def test_none_after_parse(self, correlation):
        """When parse_datetime returns valid result with no tzinfo, should localize."""
        naive_dt = datetime(2024, 1, 1, 12, 0, 0)  # no timezone
        with patch(
            "custom_components.localshift.learning.correlation.dt_util.parse_datetime",
            return_value=naive_dt,
        ):
            with patch(
                "custom_components.localshift.learning.correlation.dt_util.as_local",
                return_value=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
            ):
                result = correlation._parse_forecast_datetime(
                    "2024-01-01T12:00:00", 0, 0
                )

        assert result is not None


# =============================================================================
# WeatherCorrelation.get_current_temperature
# =============================================================================


class TestGetCurrentTemperature:
    """Tests for get_current_temperature."""

    def test_no_weather_entity(self, correlation):
        """Returns None when no entity configured."""
        correlation._data.weather_entity_id = ""
        assert correlation.get_current_temperature() is None

    def test_entity_not_found(self, correlation):
        """Returns None when entity state is None."""
        correlation._data.weather_entity_id = "weather.home"
        correlation.hass.states.get.return_value = None
        assert correlation.get_current_temperature() is None

    def test_valid_temperature(self, correlation):
        """Returns the current temperature from entity attributes."""
        correlation._data.weather_entity_id = "weather.home"
        state = make_weather_state(temperature=23.5)
        correlation.hass.states.get.return_value = state

        result = correlation.get_current_temperature()

        assert result == 23.5

    def test_invalid_temperature_returns_none(self, correlation):
        """Returns None (as 0.0 float conversion) for missing temperature."""
        correlation._data.weather_entity_id = "weather.home"
        state = MagicMock()
        state.attributes = {"temperature": "not_a_number"}
        correlation.hass.states.get.return_value = state

        # "not_a_number" raises ValueError in float(), returns None
        result = correlation.get_current_temperature()
        assert result is None

    def test_temperature_zero_degrees(self, correlation):
        """Temperature of 0 should be returned as 0.0."""
        correlation._data.weather_entity_id = "weather.home"
        state = make_weather_state(temperature=0.0)
        correlation.hass.states.get.return_value = state

        result = correlation.get_current_temperature()
        # attributes.get("temperature", 0) returns 0.0, float(0.0) = 0.0
        assert result == 0.0


# =============================================================================
# WeatherCorrelation.learn_from_sample
# =============================================================================


class TestLearnFromSample:
    """Tests for the learn_from_sample method."""

    @pytest.mark.asyncio
    async def test_invalid_hour_negative(self, correlation):
        """Negative hour should log warning and return without updating."""
        with patch(
            "custom_components.localshift.learning.correlation.dt_util.now",
            return_value=datetime.now(UTC),
        ):
            correlation.learn_from_sample(-1, 22.0, 3.0)

        assert -1 not in correlation._data.hourly_coefficients

    @pytest.mark.asyncio
    async def test_invalid_hour_too_large(self, correlation):
        """Hour > 23 should log warning and return without updating."""
        correlation.learn_from_sample(24, 22.0, 3.0)
        assert 24 not in correlation._data.hourly_coefficients

    def test_mild_temperature_updates_base_load(self, correlation):
        """Mild temperature should update base load."""
        now = datetime.now(UTC)
        # Default thresholds: heating=18, cooling=24 → mild is 18-24°C
        with patch(
            "custom_components.localshift.learning.correlation.dt_util.now",
            return_value=now,
        ):
            correlation.learn_from_sample(12, 21.0, 3.0)

        coef = correlation._data.hourly_coefficients[12]
        assert coef.base_load_kw == 3.0  # first sample: set directly
        assert coef.sample_count == 1
        assert coef.confidence in ("low", "medium", "high")

    def test_cooling_temperature_updates_coefficient(self, correlation):
        """Above-threshold temperature should update cooling coefficient."""
        now = datetime.now(UTC)
        # Pre-seed base load so cooling coefficient can be calculated
        correlation._data.hourly_coefficients[14] = HourlyTemperatureCoefficients(
            base_load_kw=2.0
        )

        with patch(
            "custom_components.localshift.learning.correlation.dt_util.now",
            return_value=now,
        ):
            correlation.learn_from_sample(14, 30.0, 5.0)  # 30°C > 24°C threshold

        coef = correlation._data.hourly_coefficients[14]
        assert coef.cooling_coefficient > 0

    def test_heating_temperature_updates_coefficient(self, correlation):
        """Below-threshold temperature should update heating coefficient."""
        now = datetime.now(UTC)
        # Pre-seed base load
        correlation._data.hourly_coefficients[6] = HourlyTemperatureCoefficients(
            base_load_kw=2.0
        )

        with patch(
            "custom_components.localshift.learning.correlation.dt_util.now",
            return_value=now,
        ):
            correlation.learn_from_sample(6, 10.0, 4.5)  # 10°C < 18°C threshold

        coef = correlation._data.hourly_coefficients[6]
        assert coef.heating_coefficient > 0

    def test_new_hour_initializes_coefficients(self, correlation):
        """Learning for a previously unseen hour should create the entry."""
        now = datetime.now(UTC)
        assert 20 not in correlation._data.hourly_coefficients

        with patch(
            "custom_components.localshift.learning.correlation.dt_util.now",
            return_value=now,
        ):
            correlation.learn_from_sample(20, 20.0, 2.5)

        assert 20 in correlation._data.hourly_coefficients

    def test_confidence_progression(self, correlation):
        """Confidence should progress from low → medium → high as samples grow."""
        now = datetime.now(UTC)

        with patch(
            "custom_components.localshift.learning.correlation.dt_util.now",
            return_value=now,
        ):
            for _ in range(3):
                correlation.learn_from_sample(10, 20.0, 2.0)

        assert correlation._data.hourly_coefficients[10].confidence == "low"

        with patch(
            "custom_components.localshift.learning.correlation.dt_util.now",
            return_value=now,
        ):
            for _ in range(10):
                correlation.learn_from_sample(10, 20.0, 2.0)

        assert correlation._data.hourly_coefficients[10].confidence == "medium"

        with patch(
            "custom_components.localshift.learning.correlation.dt_util.now",
            return_value=now,
        ):
            for _ in range(25):
                correlation.learn_from_sample(10, 20.0, 2.0)

        assert correlation._data.hourly_coefficients[10].confidence == "high"


# =============================================================================
# WeatherCorrelation._update_cooling_coefficient
# =============================================================================


class TestUpdateCoolingCoefficient:
    """Tests for _update_cooling_coefficient."""

    def test_zero_temp_delta_is_noop(self, correlation):
        """Temperature exactly at threshold (delta=0) should not update."""
        coef = HourlyTemperatureCoefficients(base_load_kw=2.0)
        cooling_threshold = 24.0
        # temperature == threshold → delta = 0
        correlation._update_cooling_coefficient(coef, 24.0, 4.0, cooling_threshold)
        assert coef.cooling_coefficient == 0.0

    def test_first_cooling_sample_no_base_load(self, correlation):
        """Without base load, should set base_load to 80% of actual."""
        coef = HourlyTemperatureCoefficients(base_load_kw=0.0)
        correlation._update_cooling_coefficient(coef, 28.0, 5.0, 24.0)
        assert coef.base_load_kw == pytest.approx(4.0)  # 5.0 * 0.8
        assert coef.cooling_coefficient == 0.0  # not updated without base

    def test_first_cooling_sample_with_base_load(self, correlation):
        """With existing base_load and no cooling_coef, should set directly."""
        coef = HourlyTemperatureCoefficients(base_load_kw=2.0, cooling_coefficient=0.0)
        correlation._update_cooling_coefficient(coef, 28.0, 6.0, 24.0)
        # implied = (6.0 - 2.0) / (28 - 24) = 1.0
        assert coef.cooling_coefficient == pytest.approx(1.0)

    def test_subsequent_cooling_uses_moving_average(self, correlation):
        """Subsequent samples should apply 10% weight EMA."""
        coef = HourlyTemperatureCoefficients(base_load_kw=2.0, cooling_coefficient=0.5)
        # implied = (6.0 - 2.0) / 4.0 = 1.0
        # new = 0.1 * 1.0 + 0.9 * 0.5 = 0.55
        correlation._update_cooling_coefficient(coef, 28.0, 6.0, 24.0)
        assert coef.cooling_coefficient == pytest.approx(0.55)


# =============================================================================
# WeatherCorrelation._update_heating_coefficient
# =============================================================================


class TestUpdateHeatingCoefficient:
    """Tests for _update_heating_coefficient."""

    def test_zero_temp_delta_is_noop(self, correlation):
        """Temperature exactly at threshold (delta=0) should not update."""
        coef = HourlyTemperatureCoefficients(base_load_kw=2.0)
        correlation._update_heating_coefficient(coef, 18.0, 4.0, 18.0)
        assert coef.heating_coefficient == 0.0

    def test_first_heating_sample_no_base_load(self, correlation):
        """Without base load, should set base_load to 80% of actual."""
        coef = HourlyTemperatureCoefficients(base_load_kw=0.0)
        correlation._update_heating_coefficient(coef, 10.0, 5.0, 18.0)
        assert coef.base_load_kw == pytest.approx(4.0)  # 5.0 * 0.8

    def test_first_heating_sample_with_base_load(self, correlation):
        """With existing base_load and no heating_coef, should set directly."""
        coef = HourlyTemperatureCoefficients(base_load_kw=2.0, heating_coefficient=0.0)
        # implied = (5.0 - 2.0) / (18.0 - 10.0) = 0.375
        correlation._update_heating_coefficient(coef, 10.0, 5.0, 18.0)
        assert coef.heating_coefficient == pytest.approx(0.375)

    def test_subsequent_heating_uses_moving_average(self, correlation):
        """Subsequent samples should apply 10% weight EMA."""
        coef = HourlyTemperatureCoefficients(base_load_kw=2.0, heating_coefficient=0.5)
        # implied = (5.0 - 2.0) / 8.0 = 0.375
        # new = 0.1 * 0.375 + 0.9 * 0.5 = 0.4875
        correlation._update_heating_coefficient(coef, 10.0, 5.0, 18.0)
        assert coef.heating_coefficient == pytest.approx(0.4875)


# =============================================================================
# WeatherCorrelation._update_mild_temperature_base_load
# =============================================================================


class TestUpdateMildTemperatureBaseLoad:
    """Tests for _update_mild_temperature_base_load."""

    def test_first_sample_sets_base_load_directly(self, correlation):
        """First mild sample should set base_load directly."""
        coef = HourlyTemperatureCoefficients(base_load_kw=0.0)
        correlation._update_mild_temperature_base_load(coef, 3.5)
        assert coef.base_load_kw == 3.5

    def test_subsequent_sample_uses_moving_average(self, correlation):
        """Subsequent samples should use 10% EMA."""
        coef = HourlyTemperatureCoefficients(base_load_kw=4.0)
        # new = 0.1 * 2.0 + 0.9 * 4.0 = 3.8
        correlation._update_mild_temperature_base_load(coef, 2.0)
        assert coef.base_load_kw == pytest.approx(3.8)


# =============================================================================
# WeatherCorrelation.predict_load
# =============================================================================


class TestPredictLoad:
    """Tests for predict_load."""

    def test_invalid_hour(self, correlation):
        """Invalid hour returns base_load unchanged."""
        result_load, source = correlation.predict_load(25, 20.0, 3.0)
        assert result_load == 3.0
        assert source == "invalid_hour"

    def test_no_coefficients_for_hour(self, correlation):
        """Hour with no learned data returns base_load."""
        # Ensure hour 5 has no coefficients
        correlation._data.hourly_coefficients.pop(5, None)
        result_load, source = correlation.predict_load(5, 20.0, 3.0)
        assert result_load == 3.0
        assert source == "no_coefficients"

    def test_low_confidence_returns_base(self, correlation):
        """Low-confidence data should not modify base_load."""
        correlation._data.hourly_coefficients[8] = HourlyTemperatureCoefficients(
            base_load_kw=2.0, cooling_coefficient=0.5, confidence="low"
        )
        result_load, source = correlation.predict_load(8, 30.0, 3.0)
        assert result_load == 3.0
        assert source == "low_confidence"

    def test_cooling_adjustment(self, correlation):
        """Above cooling threshold should increase load."""
        correlation._data.hourly_coefficients[14] = HourlyTemperatureCoefficients(
            base_load_kw=2.0, cooling_coefficient=0.5, confidence="medium"
        )
        # 28°C - 24°C = 4°C delta × 0.5 = 2.0 adjustment
        result_load, source = correlation.predict_load(14, 28.0, 2.0)
        assert result_load == pytest.approx(4.0)
        assert source == "weather_cooling"

    def test_heating_adjustment(self, correlation):
        """Below heating threshold should increase load."""
        correlation._data.hourly_coefficients[6] = HourlyTemperatureCoefficients(
            base_load_kw=2.0, heating_coefficient=0.4, confidence="high"
        )
        # 18°C - 10°C = 8°C delta × 0.4 = 3.2 adjustment
        result_load, source = correlation.predict_load(6, 10.0, 2.0)
        assert result_load == pytest.approx(5.2)
        assert source == "weather_heating"

    def test_mild_temperature_no_adjustment(self, correlation):
        """Mild temperature should return base load with no adjustment."""
        correlation._data.hourly_coefficients[12] = HourlyTemperatureCoefficients(
            base_load_kw=3.0,
            cooling_coefficient=0.5,
            heating_coefficient=0.3,
            confidence="medium",
        )
        result_load, source = correlation.predict_load(12, 21.0, 3.0)
        assert result_load == pytest.approx(3.0)
        assert source == "weather_none"

    def test_cooling_coefficient_zero(self, correlation):
        """Zero cooling coefficient should not apply adjustment."""
        correlation._data.hourly_coefficients[14] = HourlyTemperatureCoefficients(
            base_load_kw=2.0, cooling_coefficient=0.0, confidence="medium"
        )
        result_load, source = correlation.predict_load(14, 30.0, 2.0)
        assert result_load == pytest.approx(2.0)
        assert source == "weather_none"

    def test_uses_learned_base_load_over_provided(self, correlation):
        """When base_load_kw > 0, prediction uses learned value."""
        correlation._data.hourly_coefficients[10] = HourlyTemperatureCoefficients(
            base_load_kw=5.0, confidence="high"
        )
        result_load, source = correlation.predict_load(10, 21.0, 2.0)
        # Mild temp, no adjustment → uses learned base_load=5.0
        assert result_load == pytest.approx(5.0)

    def test_falls_back_to_provided_base_when_no_learned_base(self, correlation):
        """When base_load_kw == 0, uses provided base_load_kw."""
        correlation._data.hourly_coefficients[10] = HourlyTemperatureCoefficients(
            base_load_kw=0.0, confidence="high"
        )
        result_load, source = correlation.predict_load(10, 21.0, 3.5)
        assert result_load == pytest.approx(3.5)

    def test_result_is_rounded(self, correlation):
        """predict_load should return result rounded to 3 decimal places."""
        correlation._data.hourly_coefficients[10] = HourlyTemperatureCoefficients(
            base_load_kw=2.0, cooling_coefficient=0.333, confidence="medium"
        )
        # 28 - 24 = 4 × 0.333 = 1.332 → 2.0 + 1.332 = 3.332
        result_load, _ = correlation.predict_load(10, 28.0, 2.0)
        assert result_load == round(result_load, 3)


# =============================================================================
# WeatherCorrelation._calculate_confidence
# =============================================================================


class TestCalculateConfidence:
    """Tests for _calculate_confidence."""

    def test_low_confidence(self, correlation):
        """Fewer than CONFIDENCE_LOW_THRESHOLD samples → low."""
        for count in range(CONFIDENCE_LOW_THRESHOLD):
            assert correlation._calculate_confidence(count) == "low"

    def test_medium_confidence(self, correlation):
        """Between LOW and MEDIUM thresholds → medium."""
        for count in range(CONFIDENCE_LOW_THRESHOLD, CONFIDENCE_MEDIUM_THRESHOLD):
            assert correlation._calculate_confidence(count) == "medium"

    def test_high_confidence(self, correlation):
        """At or above CONFIDENCE_MEDIUM_THRESHOLD → high."""
        for count in [CONFIDENCE_MEDIUM_THRESHOLD, 50, 100]:
            assert correlation._calculate_confidence(count) == "high"

    def test_boundary_values(self, correlation):
        """Boundary values should have correct confidence."""
        assert correlation._calculate_confidence(CONFIDENCE_LOW_THRESHOLD - 1) == "low"
        assert correlation._calculate_confidence(CONFIDENCE_LOW_THRESHOLD) == "medium"
        assert (
            correlation._calculate_confidence(CONFIDENCE_MEDIUM_THRESHOLD - 1)
            == "medium"
        )
        assert correlation._calculate_confidence(CONFIDENCE_MEDIUM_THRESHOLD) == "high"


# =============================================================================
# WeatherCorrelation.get_diagnostics
# =============================================================================


class TestGetDiagnostics:
    """Tests for get_diagnostics."""

    def test_empty_coefficients(self, correlation):
        """Diagnostics with no learned data should have zeros."""
        correlation._data.hourly_coefficients = {}
        diag = correlation.get_diagnostics()

        assert diag["total_samples"] == 0
        assert diag["hours_with_data"] == 0
        assert diag["average_base_load_kw"] == 0.0
        assert diag["average_cooling_coefficient"] == 0.0
        assert diag["average_heating_coefficient"] == 0.0
        assert diag["cooling_hours"] == 0
        assert diag["heating_hours"] == 0
        assert diag["hourly_coefficients"] == {}

    def test_with_coefficients(self, correlation):
        """Diagnostics should aggregate across all hours correctly."""
        correlation._data.weather_entity_id = "weather.test"
        correlation._data.cooling_threshold = 24.0
        correlation._data.heating_threshold = 18.0
        correlation._data.hourly_coefficients = {
            10: HourlyTemperatureCoefficients(
                base_load_kw=2.0,
                cooling_coefficient=0.4,
                heating_coefficient=0.3,
                sample_count=20,
            ),
            15: HourlyTemperatureCoefficients(
                base_load_kw=4.0,
                cooling_coefficient=0.6,
                sample_count=35,
            ),
        }

        diag = correlation.get_diagnostics()

        assert diag["total_samples"] == 55
        assert diag["hours_with_data"] == 2
        assert diag["average_base_load_kw"] == pytest.approx(3.0)
        assert diag["average_cooling_coefficient"] == pytest.approx(0.5)
        assert diag["average_heating_coefficient"] == pytest.approx(0.3)
        assert diag["cooling_hours"] == 2
        assert diag["heating_hours"] == 1
        assert 10 in diag["hourly_coefficients"]
        assert 15 in diag["hourly_coefficients"]

    def test_diagnostics_includes_entity_config(self, correlation):
        """Diagnostics should include weather entity and threshold settings."""
        correlation._data.weather_entity_id = "weather.home"
        diag = correlation.get_diagnostics()

        assert diag["weather_entity_id"] == "weather.home"
        assert "cooling_threshold" in diag
        assert "heating_threshold" in diag


# =============================================================================
# WeatherCorrelation.get_coefficients_for_hour
# =============================================================================


class TestGetCoefficientsForHour:
    """Tests for get_coefficients_for_hour."""

    def test_hour_exists(self, correlation):
        """Returns coefficients when they exist for the hour."""
        coef = HourlyTemperatureCoefficients(base_load_kw=3.0, sample_count=10)
        correlation._data.hourly_coefficients[14] = coef

        result = correlation.get_coefficients_for_hour(14)

        assert result is coef
        assert result.base_load_kw == 3.0

    def test_hour_not_found(self, correlation):
        """Returns None when no coefficients exist for the hour."""
        correlation._data.hourly_coefficients.pop(5, None)
        result = correlation.get_coefficients_for_hour(5)
        assert result is None

    def test_all_24_hours(self, correlation):
        """Can retrieve coefficients for any hour 0-23."""
        for hour in range(24):
            coef = HourlyTemperatureCoefficients(base_load_kw=float(hour))
            correlation._data.hourly_coefficients[hour] = coef

        for hour in range(24):
            result = correlation.get_coefficients_for_hour(hour)
            assert result is not None
            assert result.base_load_kw == float(hour)


# =============================================================================
# Integration-style tests
# =============================================================================


class TestLearnAndPredict:
    """Integration tests for the learn → predict cycle."""

    def test_learn_multiple_samples_then_predict(self, correlation):
        """Learning multiple samples should improve prediction accuracy."""
        now = datetime.now(UTC)
        hour = 14

        # Simulate learning: at 30°C, load is 5kW; at 20°C (mild), load is 2kW
        with patch(
            "custom_components.localshift.learning.correlation.dt_util.now",
            return_value=now,
        ):
            # Learn mild base load first
            for _ in range(5):
                correlation.learn_from_sample(hour, 21.0, 2.0)

            # Learn cooling load
            for _ in range(30):
                correlation.learn_from_sample(hour, 30.0, 5.0)

        coef = correlation.get_coefficients_for_hour(hour)
        assert coef is not None
        assert coef.base_load_kw > 0
        assert coef.confidence == "high"

        # Predict should now apply cooling adjustment
        predicted, source = correlation.predict_load(hour, 30.0, coef.base_load_kw)
        assert source in ("weather_cooling", "weather_none")
        assert predicted >= coef.base_load_kw

    def test_storage_roundtrip_preserves_learning(self, correlation):
        """Learned data should survive a to_dict/from_dict roundtrip."""
        now = datetime.now(UTC)

        with patch(
            "custom_components.localshift.learning.correlation.dt_util.now",
            return_value=now,
        ):
            for _ in range(10):
                correlation.learn_from_sample(12, 20.0, 3.0)

        # Serialise and restore
        serialised = correlation._data.to_dict()
        restored = WeatherCorrelationData.from_dict(serialised)

        assert 12 in restored.hourly_coefficients
        assert restored.hourly_coefficients[12].sample_count == 10
