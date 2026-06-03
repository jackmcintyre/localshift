"""Tests for learning/temperature.py - temperature forecast module."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.localshift.const import CONF_WEATHER_ENTITY
from custom_components.localshift.learning.temperature import TemperatureForecast
from tests.learning.conftest import make_forecast_entry, make_weather_state


class TestGetTemperatureForecast:
    """Tests for the legacy get_temperature_forecast method."""

    def test_no_weather_entity(self, weather_provider):
        """Returns empty list when no entity is configured."""
        weather_provider._weather_entity_id = ""
        result = weather_provider.get_temperature_forecast()
        assert result == []

    def test_entity_not_found(self, weather_provider):
        """Returns empty list when entity state is None."""
        weather_provider._weather_entity_id = "weather.missing"
        weather_provider.hass.states.get.return_value = None

        result = weather_provider.get_temperature_forecast()
        assert result == []

    def test_empty_forecast(self, weather_provider):
        """Returns empty list when weather entity has no forecast attribute."""
        weather_provider._weather_entity_id = "weather.home"
        state = MagicMock()
        state.attributes = {"forecast": []}
        weather_provider.hass.states.get.return_value = state

        result = weather_provider.get_temperature_forecast()
        assert result == []

    def test_valid_forecast_entries(self, weather_provider):
        """Should parse valid forecast entries within 24h window."""
        now = datetime.now(UTC)
        weather_provider._weather_entity_id = "weather.home"

        entries = [
            make_forecast_entry(hours_ahead=2, temperature=28.0, now=now),
            make_forecast_entry(hours_ahead=8, temperature=32.0, now=now),
        ]
        state = make_weather_state(forecast_list=entries)
        weather_provider.hass.states.get.return_value = state

        with patch(
            "custom_components.localshift.learning.temperature.dt_util.now",
            return_value=now,
        ):
            result = weather_provider.get_temperature_forecast()

        assert len(result) == 2
        assert isinstance(result[0], TemperatureForecast)
        assert result[0].temperature == 28.0

    def test_forecast_outside_24h_filtered(self, weather_provider):
        """Forecasts more than 24h ahead or in the past should be excluded."""
        now = datetime.now(UTC)
        weather_provider._weather_entity_id = "weather.home"

        entries = [
            make_forecast_entry(hours_ahead=25, temperature=20.0, now=now),  # too far
            make_forecast_entry(hours_ahead=-1, temperature=20.0, now=now),  # past
            make_forecast_entry(hours_ahead=12, temperature=22.0, now=now),  # valid
        ]
        state = make_weather_state(forecast_list=entries)
        weather_provider.hass.states.get.return_value = state

        with patch(
            "custom_components.localshift.learning.temperature.dt_util.now",
            return_value=now,
        ):
            result = weather_provider.get_temperature_forecast()

        assert len(result) == 1
        assert result[0].temperature == 22.0

    def test_forecast_entry_missing_datetime(self, weather_provider):
        """Entries without 'datetime' key should be skipped."""
        now = datetime.now(UTC)
        weather_provider._weather_entity_id = "weather.home"

        entries = [
            {"temperature": 25.0, "condition": "sunny"},  # no datetime
            make_forecast_entry(hours_ahead=3, temperature=27.0, now=now),
        ]
        state = make_weather_state(forecast_list=entries)
        weather_provider.hass.states.get.return_value = state

        with patch(
            "custom_components.localshift.learning.temperature.dt_util.now",
            return_value=now,
        ):
            result = weather_provider.get_temperature_forecast()

        assert len(result) == 1
        assert result[0].temperature == 27.0

    def test_forecast_entry_invalid_datetime(self, weather_provider):
        """Entries with unparseable datetime should be skipped."""
        now = datetime.now(UTC)
        weather_provider._weather_entity_id = "weather.home"

        entries = [
            {"datetime": "not-a-date", "temperature": 25.0},
            make_forecast_entry(hours_ahead=4, temperature=29.0, now=now),
        ]
        state = make_weather_state(forecast_list=entries)
        weather_provider.hass.states.get.return_value = state

        with patch(
            "custom_components.localshift.learning.temperature.dt_util.now",
            return_value=now,
        ):
            with patch(
                "custom_components.localshift.learning.temperature.dt_util.parse_datetime",
                return_value=None,
            ):
                result = weather_provider.get_temperature_forecast()

        assert isinstance(result, list)


class TestAsyncGetTemperatureForecast:
    """Tests for async_get_temperature_forecast."""

    @pytest.mark.asyncio
    async def test_no_weather_entity(self, weather_provider_no_weather):
        """Returns empty list when no weather entity configured."""
        result = await weather_provider_no_weather.async_get_temperature_forecast()
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_cached_within_ttl(self, weather_provider):
        """Returns cached forecasts when cache is still fresh."""
        now = datetime.now(UTC)
        cached = [TemperatureForecast(slot_time=now, temperature=22.0)]
        weather_provider._cached_forecasts = cached
        weather_provider._forecast_cache_time = now - timedelta(minutes=5)  # fresh
        weather_provider._weather_entity_id = "weather.home"

        with patch(
            "custom_components.localshift.learning.temperature.dt_util.now",
            return_value=now,
        ):
            result = await weather_provider.async_get_temperature_forecast()

        assert result is cached

    @pytest.mark.asyncio
    async def test_force_refresh_bypasses_cache(self, weather_provider):
        """force_refresh=True should bypass the cache."""
        now = datetime.now(UTC)
        cached = [TemperatureForecast(slot_time=now, temperature=22.0)]
        weather_provider._cached_forecasts = cached
        weather_provider._forecast_cache_time = now - timedelta(minutes=1)  # very fresh
        weather_provider._weather_entity_id = "weather.home"

        # Service returns empty response so it falls back to legacy (also empty)
        weather_provider.hass.services.async_call = AsyncMock(return_value=None)
        weather_provider.hass.states.get.return_value = make_weather_state(
            forecast_list=[]
        )

        with patch(
            "custom_components.localshift.learning.temperature.dt_util.now",
            return_value=now,
        ):
            result = await weather_provider.async_get_temperature_forecast(
                force_refresh=True
            )

        assert result == []

    @pytest.mark.asyncio
    async def test_service_call_success_list_response(self, weather_provider):
        """Should parse forecasts when service returns a list directly."""
        now = datetime.now(UTC)
        weather_provider._weather_entity_id = "weather.home"
        weather_provider._cached_forecasts = []
        weather_provider._forecast_cache_time = None

        entry = make_forecast_entry(hours_ahead=3, temperature=30.0, now=now)
        response = {"weather.home": [entry]}
        weather_provider.hass.services.async_call = AsyncMock(return_value=response)

        with patch(
            "custom_components.localshift.learning.temperature.dt_util.now",
            return_value=now,
        ):
            result = await weather_provider.async_get_temperature_forecast()

        assert len(result) == 1
        assert result[0].temperature == 30.0

    @pytest.mark.asyncio
    async def test_service_call_success_dict_response_forecast_key(
        self, weather_provider
    ):
        """Should parse forecasts when service response has 'forecast' key."""
        now = datetime.now(UTC)
        weather_provider._weather_entity_id = "weather.home"
        weather_provider._cached_forecasts = []
        weather_provider._forecast_cache_time = None

        entry = make_forecast_entry(hours_ahead=5, temperature=18.0, now=now)
        response = {"weather.home": {"forecast": [entry]}}
        weather_provider.hass.services.async_call = AsyncMock(return_value=response)

        with patch(
            "custom_components.localshift.learning.temperature.dt_util.now",
            return_value=now,
        ):
            result = await weather_provider.async_get_temperature_forecast()

        assert len(result) == 1
        assert result[0].temperature == 18.0

    @pytest.mark.asyncio
    async def test_service_call_no_entity_in_response(self, weather_provider):
        """Falls back to legacy when entity is missing from response."""
        now = datetime.now(UTC)
        weather_provider._weather_entity_id = "weather.home"
        weather_provider._cached_forecasts = []
        weather_provider._forecast_cache_time = None

        response = {"weather.other": []}
        weather_provider.hass.services.async_call = AsyncMock(return_value=response)
        weather_provider.hass.states.get.return_value = make_weather_state(
            forecast_list=[]
        )

        with patch(
            "custom_components.localshift.learning.temperature.dt_util.now",
            return_value=now,
        ):
            result = await weather_provider.async_get_temperature_forecast()

        assert result == []

    @pytest.mark.asyncio
    async def test_service_call_exception_falls_back_to_legacy(self, weather_provider):
        """Falls back to legacy attribute when service call raises exception."""
        now = datetime.now(UTC)
        weather_provider._weather_entity_id = "weather.home"
        weather_provider._cached_forecasts = []
        weather_provider._forecast_cache_time = None

        weather_provider.hass.services.async_call = AsyncMock(
            side_effect=Exception("service unavailable")
        )
        entry = make_forecast_entry(hours_ahead=2, temperature=20.0, now=now)
        weather_provider.hass.states.get.return_value = make_weather_state(
            forecast_list=[entry]
        )

        with patch(
            "custom_components.localshift.learning.temperature.dt_util.now",
            return_value=now,
        ):
            result = await weather_provider.async_get_temperature_forecast()

        assert len(result) == 1
        assert result[0].temperature == 20.0

    @pytest.mark.asyncio
    async def test_weather_entity_change_clears_cache(
        self, weather_provider, mock_entry
    ):
        """Changing weather entity should clear the forecast cache."""
        now = datetime.now(UTC)
        weather_provider._weather_entity_id = "weather.old"
        old_forecast = [TemperatureForecast(slot_time=now, temperature=22.0)]
        weather_provider._cached_forecasts = old_forecast
        weather_provider._forecast_cache_time = now - timedelta(minutes=1)

        mock_entry.options = {CONF_WEATHER_ENTITY: "weather.new"}
        mock_entry.data = {}

        weather_provider.hass.services.async_call = AsyncMock(return_value=None)
        weather_provider.hass.states.get.return_value = make_weather_state(
            forecast_list=[]
        )

        with patch(
            "custom_components.localshift.learning.temperature.dt_util.now",
            return_value=now,
        ):
            result = await weather_provider.async_get_temperature_forecast()

        assert weather_provider._weather_entity_id == "weather.new"
        assert result == []


class TestExtractForecastList:
    """Tests for the _extract_forecast_list helper."""

    def test_list_response(self, weather_provider):
        """Direct list response should be returned as-is."""
        data = [{"datetime": "2024-01-01T12:00:00"}]
        result = weather_provider._extract_forecast_list(
            {"weather.home": data}, "weather.home"
        )
        assert result == data

    def test_dict_with_forecast_key(self, weather_provider):
        """Dict response with 'forecast' key should extract nested list."""
        inner = [{"datetime": "2024-01-01T12:00:00"}]
        response = {"weather.home": {"forecast": inner}}
        result = weather_provider._extract_forecast_list(response, "weather.home")
        assert result == inner

    def test_dict_with_hourly_key(self, weather_provider):
        """Dict response with 'hourly' key should extract nested list."""
        inner = [{"datetime": "2024-01-01T12:00:00"}]
        response = {"weather.home": {"hourly": inner}}
        result = weather_provider._extract_forecast_list(response, "weather.home")
        assert result == inner

    def test_entity_not_in_response(self, weather_provider):
        """Missing entity key should return None."""
        result = weather_provider._extract_forecast_list({}, "weather.home")
        assert result is None

    def test_unexpected_format(self, weather_provider):
        """Unexpected data type should return None."""
        response = {"weather.home": "unexpected_string"}
        result = weather_provider._extract_forecast_list(response, "weather.home")
        assert result is None

    def test_dict_without_known_keys(self, weather_provider):
        """Dict without forecast/hourly keys should return None."""
        response = {"weather.home": {"other_key": []}}
        result = weather_provider._extract_forecast_list(response, "weather.home")
        assert result is None


class TestParseForecastEntries:
    """Tests for _parse_forecast_entries."""

    def test_empty_list(self, weather_provider):
        """Empty list should return empty list."""
        now = datetime.now(UTC)
        result = weather_provider._parse_forecast_entries([], now)
        assert result == []

    def test_valid_entries(self, weather_provider):
        """Valid entries within 24h should be returned."""
        now = datetime.now(UTC)
        entries = [
            make_forecast_entry(hours_ahead=1, temperature=20.0, now=now),
            make_forecast_entry(hours_ahead=6, temperature=25.0, now=now),
            make_forecast_entry(hours_ahead=12, temperature=30.0, now=now),
        ]

        with patch(
            "custom_components.localshift.learning.temperature.dt_util.now",
            return_value=now,
        ):
            result = weather_provider._parse_forecast_entries(entries, now)

        assert len(result) == 3

    def test_non_dict_entries_skipped(self, weather_provider):
        """Non-dict entries should be silently skipped."""
        now = datetime.now(UTC)
        entries = [
            "not_a_dict",
            None,
            make_forecast_entry(hours_ahead=2, temperature=22.0, now=now),
        ]

        result = weather_provider._parse_forecast_entries(entries, now)

        valid = [r for r in result if r is not None]
        assert len(valid) <= 1


class TestParseSingleForecastEntry:
    """Tests for _parse_single_forecast_entry."""

    def test_non_dict_returns_none(self, weather_provider):
        """Non-dict entry should return None."""
        now = datetime.now(UTC)
        result = weather_provider._parse_single_forecast_entry(
            "not_a_dict", now, 0, 0, 0, 0
        )
        assert result is None

    def test_missing_datetime(self, weather_provider):
        """Entry without 'datetime' key should return (None, updated_counts)."""
        now = datetime.now(UTC)
        entry = {"temperature": 25.0}
        result = weather_provider._parse_single_forecast_entry(entry, now, 0, 0, 0, 0)
        assert result is not None
        forecast, _, skipped, _ = result
        assert forecast is None
        assert skipped == 1

    def test_invalid_datetime(self, weather_provider):
        """Entry with unparseable datetime should return (None, updated_counts)."""
        now = datetime.now(UTC)
        entry = {"datetime": "bad-datetime", "temperature": 25.0}

        with patch(
            "custom_components.localshift.learning.temperature.dt_util.parse_datetime",
            return_value=None,
        ):
            result = weather_provider._parse_single_forecast_entry(
                entry, now, 0, 0, 0, 0
            )

        assert result is not None
        forecast, parse_failed, _, _ = result
        assert forecast is None
        assert parse_failed == 1

    def test_forecast_outside_window(self, weather_provider):
        """Forecast too far in the future should return (None, updated_counts)."""
        now = datetime.now(UTC)
        future = now + timedelta(hours=30)
        entry = {
            "datetime": future.isoformat(),
            "temperature": 25.0,
        }
        result = weather_provider._parse_single_forecast_entry(entry, now, 0, 0, 0, 0)
        assert result is not None
        forecast, _, _, filtered = result
        assert forecast is None
        assert filtered == 1

    def test_valid_entry(self, weather_provider):
        """Valid entry within window should return TemperatureForecast."""
        now = datetime.now(UTC)
        future = now + timedelta(hours=5)
        entry = {
            "datetime": future.isoformat(),
            "temperature": 22.0,
            "condition": "cloudy",
        }
        result = weather_provider._parse_single_forecast_entry(entry, now, 0, 0, 0, 0)
        assert result is not None
        forecast, _, _, _ = result
        assert forecast is not None
        assert isinstance(forecast, TemperatureForecast)
        assert forecast.temperature == 22.0
        assert forecast.condition == "cloudy"


class TestParseForecastDatetime:
    """Tests for _parse_forecast_datetime."""

    def test_valid_iso_with_timezone(self, weather_provider):
        """Valid ISO datetime with timezone should parse correctly."""
        now = datetime.now(UTC)
        time_str = now.isoformat()
        result = weather_provider._parse_forecast_datetime(time_str, 0, 0)
        assert result is not None

    def test_naive_iso_localized(self, weather_provider):
        """Naive ISO datetime should be localized."""
        naive_str = "2024-06-15T14:30:00"

        with patch(
            "custom_components.localshift.learning.temperature.dt_util.parse_datetime",
            return_value=None,
        ):
            with patch(
                "custom_components.localshift.learning.temperature.dt_util.as_local"
            ) as mock_local:
                mock_local.return_value = datetime(2024, 6, 15, 14, 30, 0, tzinfo=UTC)
                result = weather_provider._parse_forecast_datetime(naive_str, 0, 0)

        assert result is not None

    def test_completely_invalid_string(self, weather_provider):
        """Completely invalid string should return None."""
        with patch(
            "custom_components.localshift.learning.temperature.dt_util.parse_datetime",
            return_value=None,
        ):
            result = weather_provider._parse_forecast_datetime("not-a-date", 0, 0)

        assert result is None

    def test_first_entry_logged(self, weather_provider):
        """First entry (index=0) should log debug info."""
        now = datetime.now(UTC)
        time_str = now.isoformat()

        with patch(
            "custom_components.localshift.learning.temperature.dt_util.parse_datetime",
            return_value=now,
        ):
            result = weather_provider._parse_forecast_datetime(time_str, 0, 0)

        assert result is not None

    def test_none_after_parse(self, weather_provider):
        """When parse_datetime returns valid result with no tzinfo, should localize."""
        naive_dt = datetime(2024, 1, 1, 12, 0, 0)
        with patch(
            "custom_components.localshift.learning.temperature.dt_util.parse_datetime",
            return_value=naive_dt,
        ):
            with patch(
                "custom_components.localshift.learning.temperature.dt_util.as_local",
                return_value=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
            ):
                result = weather_provider._parse_forecast_datetime(
                    "2024-01-01T12:00:00", 0, 0
                )

        assert result is not None


class TestGetCurrentTemperature:
    """Tests for get_current_temperature."""

    def test_no_weather_entity(self, weather_provider):
        """Returns None when no entity configured."""
        weather_provider._weather_entity_id = ""
        assert weather_provider.get_current_temperature() is None

    def test_entity_not_found(self, weather_provider):
        """Returns None when entity state is None."""
        weather_provider._weather_entity_id = "weather.home"
        weather_provider.hass.states.get.return_value = None
        assert weather_provider.get_current_temperature() is None

    def test_valid_temperature(self, weather_provider):
        """Returns the current temperature from entity attributes."""
        weather_provider._weather_entity_id = "weather.home"
        state = make_weather_state(temperature=23.5)
        weather_provider.hass.states.get.return_value = state

        result = weather_provider.get_current_temperature()

        assert result == 23.5

    def test_invalid_temperature_returns_none(self, weather_provider):
        """Returns None (as 0.0 float conversion) for missing temperature."""
        weather_provider._weather_entity_id = "weather.home"
        state = MagicMock()
        state.attributes = {"temperature": "not_a_number"}
        weather_provider.hass.states.get.return_value = state

        result = weather_provider.get_current_temperature()
        assert result is None

    def test_temperature_zero_degrees(self, weather_provider):
        """Temperature of 0 should be returned as 0.0."""
        weather_provider._weather_entity_id = "weather.home"
        state = make_weather_state(temperature=0.0)
        weather_provider.hass.states.get.return_value = state

        result = weather_provider.get_current_temperature()
        assert result == 0.0
