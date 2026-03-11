"""Tests for HistoryFetcher helper methods extracted during complexity refactoring."""

from datetime import datetime
from unittest.mock import MagicMock, AsyncMock, patch
import pytest

from custom_components.localshift.forecast.history import (
    HistoryFetcher,
)


@pytest.fixture
def history_fetcher():
    """Create a HistoryFetcher instance for testing."""
    hass = MagicMock()
    hass.config.time_zone = "Australia/Sydney"
    entry = MagicMock()
    entry.options = {}
    return HistoryFetcher(hass, entry)


class TestComputeHourlyAverages:
    """Tests for _compute_hourly_averages helper."""

    def test_single_hour_with_values(self, history_fetcher):
        """Compute average for a single hour with multiple values."""
        samples = {12: [1.0, 2.0, 3.0]}
        averages, counts = history_fetcher._compute_hourly_averages(samples)

        assert averages[12] == 2.0
        assert counts[12] == 3

    def test_multiple_hours(self, history_fetcher):
        """Compute averages for multiple hours."""
        samples = {10: [1.0, 2.0], 14: [5.0, 10.0, 15.0]}
        averages, counts = history_fetcher._compute_hourly_averages(samples)

        assert averages[10] == 1.5
        assert counts[10] == 2
        assert averages[14] == 10.0
        assert counts[14] == 3

    def test_empty_hour_skipped(self, history_fetcher):
        """Skip hours with empty value lists."""
        samples = {12: [1.0, 2.0], 14: []}
        averages, counts = history_fetcher._compute_hourly_averages(samples)

        assert 12 in averages
        assert 14 not in averages
        assert 14 not in counts

    def test_empty_samples(self, history_fetcher):
        """Handle empty samples dict."""
        samples = {}
        averages, counts = history_fetcher._compute_hourly_averages(samples)

        assert averages == {}
        assert counts == {}

    def test_single_value(self, history_fetcher):
        """Handle hour with single value."""
        samples = {8: [5.0]}
        averages, counts = history_fetcher._compute_hourly_averages(samples)

        assert averages[8] == 5.0
        assert counts[8] == 1


class TestComputeCombinedProfile:
    """Tests for _compute_combined_profile helper."""

    def test_combines_weekday_and_weekend(self, history_fetcher):
        """Combine weekday and weekend samples correctly."""
        weekday = {12: [1.0, 2.0]}
        weekend = {12: [3.0, 4.0]}

        avg, counts = history_fetcher._compute_combined_profile(weekday, weekend)

        assert avg[12] == 2.5
        assert counts[12] == 4

    def test_weekday_only(self, history_fetcher):
        """Handle weekday-only samples."""
        weekday = {10: [1.0, 2.0], 14: [5.0]}
        weekend = {}

        avg, counts = history_fetcher._compute_combined_profile(weekday, weekend)

        assert avg[10] == 1.5
        assert counts[10] == 2
        assert avg[14] == 5.0
        assert counts[14] == 1

    def test_weekend_only(self, history_fetcher):
        """Handle weekend-only samples."""
        weekday = {}
        weekend = {10: [3.0, 6.0]}

        avg, counts = history_fetcher._compute_combined_profile(weekday, weekend)

        assert avg[10] == 4.5
        assert counts[10] == 2

    def test_different_hours(self, history_fetcher):
        """Handle weekday and weekend having different hours."""
        weekday = {10: [1.0], 14: [2.0]}
        weekend = {10: [3.0], 18: [4.0]}

        avg, counts = history_fetcher._compute_combined_profile(weekday, weekend)

        assert avg[10] == 2.0
        assert counts[10] == 2
        assert avg[14] == 2.0
        assert counts[14] == 1
        assert avg[18] == 4.0
        assert counts[18] == 1

    def test_all_24_hours_present(self, history_fetcher):
        """Ensure all 24 hours are checked."""
        weekday = {0: [1.0], 23: [2.0]}
        weekend = {12: [3.0]}

        avg, counts = history_fetcher._compute_combined_profile(weekday, weekend)

        assert len(avg) == 3
        assert 0 in avg
        assert 12 in avg
        assert 23 in avg


class TestComputeHistoricalProfiles:
    """Tests for _compute_historical_profiles helper."""

    def test_basic_profile_computation(self, history_fetcher):
        """Compute profiles from statistics rows."""
        rows = [
            {"start": 1704067200, "mean": 1.5},
            {"start": 1704070800, "mean": 2.5},
        ]
        local_tz = MagicMock()

        result = history_fetcher._compute_historical_profiles(rows, local_tz)

        assert "combined_avg" in result
        assert "combined_counts" in result
        assert "weekday_avg" in result
        assert "weekend_avg" in result
        assert "weekday_counts" in result
        assert "weekend_counts" in result
        assert "profile_source" in result
        assert "baseline_avg" in result
        assert "baseline_counts" in result
        assert "hvac_avg" in result
        assert "hvac_counts" in result

    def test_empty_rows(self, history_fetcher):
        """Handle empty rows input."""
        rows = []
        local_tz = MagicMock()

        result = history_fetcher._compute_historical_profiles(rows, local_tz)

        assert result["combined_avg"] == {}
        assert result["combined_counts"] == {}
        assert result["profile_source"] == "combined_fallback"


class TestAsyncGetHistoricalHourlyAverages:
    """Tests for async_get_historical_hourly_verages method."""

    @pytest.mark.asyncio
    async def test_returns_cached_data(self, history_fetcher):
        """Return cached data when cache is valid."""
        history_fetcher._historical_load_cache = {10: 1.5, 12: 2.0}
        history_fetcher._historical_load_sample_counts = {10: 5, 12: 8}
        history_fetcher._historical_load_source = "statistics"
        from datetime import datetime
        from homeassistant.util import dt as dt_util

        history_fetcher._historical_load_cache_date = dt_util.now().strftime("%Y-%m-%d")

        result = await history_fetcher.async_get_historical_hourly_averages(
            "sensor.test"
        )

        assert result[0] == {10: 1.5, 12: 2.0}
        assert result[1] == {10: 5, 12: 8}
        assert result[2] == "statistics"

    @pytest.mark.asyncio
    async def test_fetches_new_data_when_cache_expired(self, history_fetcher):
        """Fetch new data when cache is expired."""
        history_fetcher._historical_load_cache_date = "2020-01-01"

        mock_result = {
            "combined_avg": {10: 1.5, 11: 2.0, 12: 2.5, 13: 3.0, 14: 2.8, 15: 2.2},
            "combined_counts": {10: 5, 11: 6, 12: 7, 13: 8, 14: 6, 15: 5},
            "weekday_avg": {10: 1.5},
            "weekend_avg": {10: 1.0},
            "weekday_counts": {10: 3},
            "weekend_counts": {10: 2},
            "profile_source": "weekday_weekend",
        }

        with patch(
            "homeassistant.components.recorder.get_instance"
        ) as mock_get_instance:
            mock_recorder = MagicMock()
            mock_recorder.async_add_executor_job = AsyncMock(return_value=mock_result)
            mock_get_instance.return_value = mock_recorder

            result = await history_fetcher.async_get_historical_hourly_averages(
                "sensor.test"
            )

            assert result[2] == "statistics"
            assert mock_get_instance.called


class TestFetchHistoricalDataSync:
    """Tests for _fetch_historical_data_sync method."""

    def test_returns_empty_on_import_failure(self, history_fetcher):
        """Return empty result when recorder import fails."""
        with patch.object(
            history_fetcher, "_import_recorder_statistics", return_value=None
        ):
            result = history_fetcher._fetch_historical_data_sync(
                "sensor.test", datetime(2024, 1, 15, 12, 0, 0)
            )

            assert result == history_fetcher._empty_result()

    def test_returns_empty_when_statistics_fn_unavailable(self, history_fetcher):
        """Return empty result when statistics function is unavailable."""
        mock_recorder = MagicMock()

        with (
            patch.object(
                history_fetcher,
                "_import_recorder_statistics",
                return_value=mock_recorder,
            ),
            patch.object(history_fetcher, "_list_statistic_ids", return_value=[]),
            patch.object(
                history_fetcher, "_resolve_statistic_id", return_value="sensor.test"
            ),
            patch.object(history_fetcher, "_get_statistics_fn", return_value=None),
        ):
            result = history_fetcher._fetch_historical_data_sync(
                "sensor.test", datetime(2024, 1, 15, 12, 0, 0)
            )

            assert result == history_fetcher._empty_result()

    def test_returns_empty_on_statistics_error(self, history_fetcher):
        """Return empty result when statistics fetch errors."""
        mock_recorder = MagicMock()
        mock_fn = MagicMock()

        with (
            patch.object(
                history_fetcher,
                "_import_recorder_statistics",
                return_value=mock_recorder,
            ),
            patch.object(history_fetcher, "_list_statistic_ids", return_value=[]),
            patch.object(
                history_fetcher, "_resolve_statistic_id", return_value="sensor.test"
            ),
            patch.object(history_fetcher, "_get_statistics_fn", return_value=mock_fn),
            patch.object(
                history_fetcher,
                "_fetch_statistics_data",
                return_value={"error": "failed"},
            ),
        ):
            result = history_fetcher._fetch_historical_data_sync(
                "sensor.test", datetime(2024, 1, 15, 12, 0, 0)
            )

            assert result == history_fetcher._empty_result()


class TestImportRecorderStatistics:
    """Tests for _import_recorder_statistics method."""

    def test_returns_module_on_success(self, history_fetcher):
        """Return module when import succeeds."""
        result = history_fetcher._import_recorder_statistics()
        assert result is not None


class TestListStatisticIds:
    """Tests for _list_statistic_ids method."""

    def test_returns_list_on_success(self, history_fetcher):
        """Return list of statistic IDs."""
        mock_recorder = MagicMock()
        mock_recorder.list_statistic_ids = MagicMock(
            return_value=[{"statistic_id": "sensor.test"}]
        )

        result = history_fetcher._list_statistic_ids(mock_recorder)

        assert len(result) == 1
        assert result[0]["statistic_id"] == "sensor.test"

    def test_returns_empty_on_error(self, history_fetcher):
        """Return empty list on error."""
        mock_recorder = MagicMock()
        mock_recorder.list_statistic_ids = MagicMock(side_effect=Exception("error"))

        result = history_fetcher._list_statistic_ids(mock_recorder)

        assert result == []

    def test_returns_empty_when_not_callable(self, history_fetcher):
        """Return empty list when function is not callable."""
        mock_recorder = MagicMock()
        del mock_recorder.list_statistic_ids

        result = history_fetcher._list_statistic_ids(mock_recorder)

        assert result == []

    def test_returns_empty_when_not_list(self, history_fetcher):
        """Return empty list when result is not a list."""
        mock_recorder = MagicMock()
        mock_recorder.list_statistic_ids = MagicMock(return_value="not a list")

        result = history_fetcher._list_statistic_ids(mock_recorder)

        assert result == []

    def test_filters_non_dict_items(self, history_fetcher):
        """Filter out non-dict items from result."""
        mock_recorder = MagicMock()
        mock_recorder.list_statistic_ids = MagicMock(
            return_value=[{"statistic_id": "sensor.test"}, "invalid", 123]
        )

        result = history_fetcher._list_statistic_ids(mock_recorder)

        assert len(result) == 1
        assert result[0]["statistic_id"] == "sensor.test"


class TestGetStatisticsFn:
    """Tests for _get_statistics_fn method."""

    def test_returns_callable(self, history_fetcher):
        """Return callable function."""
        mock_recorder = MagicMock()
        mock_recorder.statistics_during_period = MagicMock()

        result = history_fetcher._get_statistics_fn(mock_recorder)

        assert result is not None

    def test_returns_none_when_not_callable(self, history_fetcher):
        """Return None when function is not callable."""
        mock_recorder = MagicMock()
        del mock_recorder.statistics_during_period

        result = history_fetcher._get_statistics_fn(mock_recorder)

        assert result is None


class TestFetchStatisticsData:
    """Tests for _fetch_statistics_data method."""

    def test_returns_rows_on_success(self, history_fetcher):
        """Return rows when statistics fetch succeeds."""
        mock_fn = MagicMock(
            return_value={
                "sensor.test": [
                    {"start": 1704067200, "mean": 1.5},
                    {"start": 1704070800, "mean": 2.5},
                ]
            }
        )

        result = history_fetcher._fetch_statistics_data(
            mock_fn, "sensor.test", datetime(2024, 1, 1), datetime(2024, 1, 15)
        )

        assert "rows" in result
        assert len(result["rows"]) == 2

    def test_returns_error_on_exception(self, history_fetcher):
        """Return error result on exception."""
        mock_fn = MagicMock(side_effect=Exception("error"))

        result = history_fetcher._fetch_statistics_data(
            mock_fn, "sensor.test", datetime(2024, 1, 1), datetime(2024, 1, 15)
        )

        assert "error" in result

    def test_returns_error_on_empty_data(self, history_fetcher):
        """Return error result when no data returned."""
        mock_fn = MagicMock(return_value={})

        result = history_fetcher._fetch_statistics_data(
            mock_fn, "sensor.test", datetime(2024, 1, 1), datetime(2024, 1, 15)
        )

        assert "error" in result


class TestResolveStatisticId:
    """Tests for _resolve_statistic_id method."""

    def test_returns_matching_id(self, history_fetcher):
        """Return matching statistic ID."""
        stat_ids = [
            {"statistic_id": "sensor.other"},
            {"statistic_id": "sensor.test"},
        ]

        result = history_fetcher._resolve_statistic_id("sensor.test", stat_ids)

        assert result == "sensor.test"

    def test_returns_entity_id_when_not_found(self, history_fetcher):
        """Return original entity ID when not found."""
        stat_ids = [{"statistic_id": "sensor.other"}]

        result = history_fetcher._resolve_statistic_id("sensor.test", stat_ids)

        assert result == "sensor.test"

    def test_handles_empty_list(self, history_fetcher):
        """Handle empty stat_ids list."""
        result = history_fetcher._resolve_statistic_id("sensor.test", [])

        assert result == "sensor.test"


class TestEmptyResult:
    """Tests for _empty_result method."""

    def test_returns_empty_structure(self, history_fetcher):
        """Return empty result structure."""
        result = history_fetcher._empty_result()

        assert result["combined_avg"] == {}
        assert result["combined_counts"] == {}
        assert result["weekday_avg"] == {}
        assert result["weekend_avg"] == {}
        assert result["profile_source"] == "unknown"


class TestGetProfileForDay:
    """Tests for get_profile_for_day method."""

    def test_returns_weekday_profile(self, history_fetcher):
        """Return weekday profile for weekday date."""
        from datetime import date

        history_fetcher._weekday_hourly_avg_kw = {10: 1.5}
        history_fetcher._weekday_sample_counts = {10: 5}
        history_fetcher._weekend_hourly_avg_kw = {10: 2.0}
        history_fetcher._weekend_sample_counts = {10: 3}
        history_fetcher._profile_source = "weekday_weekend"

        avg, counts, source = history_fetcher.get_profile_for_day(date(2024, 1, 8))

        assert source == "weekday"
        assert avg[10] == 1.5

    def test_returns_weekend_profile(self, history_fetcher):
        """Return weekend profile for weekend date."""
        from datetime import date

        history_fetcher._weekday_hourly_avg_kw = {10: 1.5}
        history_fetcher._weekday_sample_counts = {10: 5}
        history_fetcher._weekend_hourly_avg_kw = {10: 2.0}
        history_fetcher._weekend_sample_counts = {10: 3}
        history_fetcher._profile_source = "weekday_weekend"

        avg, counts, source = history_fetcher.get_profile_for_day(date(2024, 1, 6))

        assert source == "weekend"
        assert avg[10] == 2.0

    def test_returns_combined_fallback(self, history_fetcher):
        """Return combined profile for fallback source."""
        from datetime import date

        history_fetcher._historical_load_cache = {10: 1.5}
        history_fetcher._historical_load_sample_counts = {10: 5}
        history_fetcher._profile_source = "combined_fallback"
        history_fetcher._weekday_hourly_avg_kw = {10: 1.0}
        history_fetcher._weekend_hourly_avg_kw = {10: 2.0}

        avg, counts, source = history_fetcher.get_profile_for_day(date(2024, 1, 8))

        assert source == "combined"
        assert avg[10] == 1.5

    def test_returns_empty_when_no_profiles(self, history_fetcher):
        """Return empty when no profiles available."""
        from datetime import date

        avg, counts, source = history_fetcher.get_profile_for_day(date(2024, 1, 8))

        assert source == "combined"
        assert avg == {}
        assert counts == {}


class TestAsyncGetRecentLoad1hr:
    """Tests for async_get_recent_load_1hr method."""

    @pytest.mark.asyncio
    async def test_returns_cached_value(self, history_fetcher):
        """Return cached value when cache is valid."""
        from datetime import datetime, timedelta
        from homeassistant.util import dt as dt_util

        history_fetcher._recent_load_1hr_kw = 2.5
        history_fetcher._recent_load_cache_time = dt_util.now()

        result = await history_fetcher.async_get_recent_load_1hr("sensor.test")

        assert result == 2.5

    @pytest.mark.asyncio
    async def test_fetches_new_value(self, history_fetcher):
        """Fetch new value when cache is expired."""
        from datetime import datetime

        mock_result = {
            "recent_avg_kw": 3.5,
            "samples": 10,
            "statistic_id": "sensor.test",
            "error": "",
        }

        with patch(
            "homeassistant.components.recorder.get_instance"
        ) as mock_get_instance:
            mock_recorder = MagicMock()
            mock_recorder.async_add_executor_job = AsyncMock(return_value=mock_result)
            mock_get_instance.return_value = mock_recorder

            result = await history_fetcher.async_get_recent_load_1hr("sensor.test")

            assert result == 3.5
            assert history_fetcher._recent_load_1hr_samples == 10

    @pytest.mark.asyncio
    async def test_handles_exception(self, history_fetcher):
        """Handle exception during fetch."""
        with patch(
            "homeassistant.components.recorder.get_instance"
        ) as mock_get_instance:
            mock_recorder = MagicMock()
            mock_recorder.async_add_executor_job = AsyncMock(
                side_effect=Exception("error")
            )
            mock_get_instance.return_value = mock_recorder

            result = await history_fetcher.async_get_recent_load_1hr("sensor.test")

            assert result == 0.0
            assert history_fetcher._recent_load_1hr_last_error == "error"


class TestFetchRecentLoadSync:
    """Tests for _fetch_recent_load_sync method."""

    def test_returns_error_on_import_failure(self, history_fetcher):
        """Return error when recorder import fails."""
        from datetime import datetime

        with patch.object(
            history_fetcher, "_import_recorder_statistics", return_value=None
        ):
            result = history_fetcher._fetch_recent_load_sync(
                "sensor.test", datetime(2024, 1, 15, 12, 0, 0)
            )

            assert "error" in result

    def test_returns_error_on_statistics_fn_unavailable(self, history_fetcher):
        """Return error when statistics function is unavailable."""
        from datetime import datetime

        mock_recorder = MagicMock()

        with (
            patch.object(
                history_fetcher,
                "_import_recorder_statistics",
                return_value=mock_recorder,
            ),
            patch.object(history_fetcher, "_list_statistic_ids", return_value=[]),
            patch.object(
                history_fetcher, "_resolve_statistic_id", return_value="sensor.test"
            ),
            patch.object(history_fetcher, "_get_statistics_fn", return_value=None),
        ):
            result = history_fetcher._fetch_recent_load_sync(
                "sensor.test", datetime(2024, 1, 15, 12, 0, 0)
            )

            assert "error" in result

    def test_returns_error_on_statistics_error(self, history_fetcher):
        """Return error when statistics fetch fails."""
        from datetime import datetime

        mock_recorder = MagicMock()
        mock_fn = MagicMock()

        with (
            patch.object(
                history_fetcher,
                "_import_recorder_statistics",
                return_value=mock_recorder,
            ),
            patch.object(history_fetcher, "_list_statistic_ids", return_value=[]),
            patch.object(
                history_fetcher, "_resolve_statistic_id", return_value="sensor.test"
            ),
            patch.object(history_fetcher, "_get_statistics_fn", return_value=mock_fn),
            patch.object(
                history_fetcher,
                "_fetch_statistics_data",
                return_value={"error": "failed"},
            ),
        ):
            result = history_fetcher._fetch_recent_load_sync(
                "sensor.test", datetime(2024, 1, 15, 12, 0, 0)
            )

            assert "error" in result

    def test_returns_average_on_success(self, history_fetcher):
        """Return average on successful fetch."""
        from datetime import datetime

        mock_recorder = MagicMock()
        mock_fn = MagicMock()

        with (
            patch.object(
                history_fetcher,
                "_import_recorder_statistics",
                return_value=mock_recorder,
            ),
            patch.object(history_fetcher, "_list_statistic_ids", return_value=[]),
            patch.object(
                history_fetcher, "_resolve_statistic_id", return_value="sensor.test"
            ),
            patch.object(history_fetcher, "_get_statistics_fn", return_value=mock_fn),
            patch.object(
                history_fetcher,
                "_fetch_statistics_data",
                return_value={"rows": [{"mean": 2.5}]},
            ),
        ):
            result = history_fetcher._fetch_recent_load_sync(
                "sensor.test", datetime(2024, 1, 15, 12, 0, 0)
            )

            assert result["recent_avg_kw"] == 2.5


class TestComputeRecentAverage:
    """Tests for _compute_recent_average method."""

    def test_computes_average(self, history_fetcher):
        """Compute average from rows."""
        data = {"rows": [{"mean": 1.0}, {"mean": 2.0}, {"mean": 3.0}]}

        result = history_fetcher._compute_recent_average(data, "sensor.test")

        assert result["recent_avg_kw"] == 2.0
        assert result["samples"] == 3

    def test_skips_none_values(self, history_fetcher):
        """Skip None mean values."""
        data = {"rows": [{"mean": 1.0}, {"mean": None}, {"mean": 3.0}]}

        result = history_fetcher._compute_recent_average(data, "sensor.test")

        assert result["recent_avg_kw"] == 2.0
        assert result["samples"] == 2

    def test_skips_unknown_values(self, history_fetcher):
        """Skip 'unknown' mean values."""
        data = {"rows": [{"mean": 1.0}, {"mean": "unknown"}, {"mean": 3.0}]}

        result = history_fetcher._compute_recent_average(data, "sensor.test")

        assert result["recent_avg_kw"] == 2.0

    def test_skips_unavailable_values(self, history_fetcher):
        """Skip 'unavailable' mean values."""
        data = {"rows": [{"mean": 1.0}, {"mean": "unavailable"}, {"mean": 3.0}]}

        result = history_fetcher._compute_recent_average(data, "sensor.test")

        assert result["recent_avg_kw"] == 2.0

    def test_skips_non_dict_rows(self, history_fetcher):
        """Skip non-dict rows."""
        data = {"rows": [{"mean": 1.0}, "invalid", {"mean": 3.0}]}

        result = history_fetcher._compute_recent_average(data, "sensor.test")

        assert result["recent_avg_kw"] == 2.0

    def test_returns_error_on_no_numeric_values(self, history_fetcher):
        """Return error when no numeric values."""
        data = {"rows": [{"mean": None}, {"mean": "unknown"}]}

        result = history_fetcher._compute_recent_average(data, "sensor.test")

        assert "error" in result

    def test_handles_empty_rows(self, history_fetcher):
        """Handle empty rows."""
        data = {"rows": []}

        result = history_fetcher._compute_recent_average(data, "sensor.test")

        assert "error" in result

    def test_handles_invalid_float_conversion(self, history_fetcher):
        """Handle values that can't convert to float."""
        data = {"rows": [{"mean": "not a number"}]}

        result = history_fetcher._compute_recent_average(data, "sensor.test")

        assert "error" in result


class TestFetchStatisticsDataEdgeCases:
    """Tests for _fetch_statistics_data edge cases."""

    def test_returns_error_on_non_dict_return(self, history_fetcher):
        """Return error when statistics returns non-dict."""
        from datetime import datetime

        mock_fn = MagicMock(return_value="not a dict")

        result = history_fetcher._fetch_statistics_data(
            mock_fn, "sensor.test", datetime(2024, 1, 1), datetime(2024, 1, 15)
        )

        assert "error" in result

    def test_returns_error_on_empty_data(self, history_fetcher):
        """Return error when statistics data is empty."""
        from datetime import datetime

        mock_fn = MagicMock(return_value={})

        result = history_fetcher._fetch_statistics_data(
            mock_fn, "sensor.test", datetime(2024, 1, 1), datetime(2024, 1, 15)
        )

        assert "error" in result

    def test_returns_error_on_entity_not_in_data(self, history_fetcher):
        """Return error when entity not in data."""
        from datetime import datetime

        mock_fn = MagicMock(return_value={"sensor.other": []})

        result = history_fetcher._fetch_statistics_data(
            mock_fn, "sensor.test", datetime(2024, 1, 1), datetime(2024, 1, 15)
        )

        assert "error" in result

    def test_returns_error_on_empty_rows(self, history_fetcher):
        """Return error when rows are empty."""
        from datetime import datetime

        mock_fn = MagicMock(return_value={"sensor.test": []})

        result = history_fetcher._fetch_statistics_data(
            mock_fn, "sensor.test", datetime(2024, 1, 1), datetime(2024, 1, 15)
        )

        assert "error" in result

    def test_returns_error_on_non_list_rows(self, history_fetcher):
        """Return error when rows is not a list."""
        from datetime import datetime

        mock_fn = MagicMock(return_value={"sensor.test": "not a list"})

        result = history_fetcher._fetch_statistics_data(
            mock_fn, "sensor.test", datetime(2024, 1, 1), datetime(2024, 1, 15)
        )

        assert "error" in result

    def test_returns_error_on_no_dict_rows(self, history_fetcher):
        """Return error when no valid dict rows."""
        from datetime import datetime

        mock_fn = MagicMock(return_value={"sensor.test": ["invalid", 123]})

        result = history_fetcher._fetch_statistics_data(
            mock_fn, "sensor.test", datetime(2024, 1, 1), datetime(2024, 1, 15)
        )

        assert "error" in result

    def test_returns_empty_when_no_profiles(self, history_fetcher):
        """Return empty when no profiles available."""
        from datetime import date

        history_fetcher._weekday_hourly_avg_kw = {}
        history_fetcher._weekend_hourly_avg_kw = {}

        avg, counts, source = history_fetcher.get_profile_for_day(date(2024, 1, 8))

        assert avg == {}
        assert counts == {}
        assert source == "combined"

    def test_weekend_falls_back_to_combined(self, history_fetcher):
        """Weekend falls back to combined when no weekend profile."""
        from datetime import date

        history_fetcher._weekday_hourly_avg_kw = {10: 1.5}
        history_fetcher._weekend_hourly_avg_kw = {}
        history_fetcher._historical_load_cache = {10: 2.0}
        history_fetcher._historical_load_sample_counts = {10: 5}
        history_fetcher._profile_source = "weekday_weekend"

        avg, counts, source = history_fetcher.get_profile_for_day(date(2024, 1, 6))

        assert source == "combined"
        assert avg[10] == 2.0

    def test_weekday_falls_back_to_combined(self, history_fetcher):
        """Weekday falls back to combined when no weekday profile."""
        from datetime import date

        history_fetcher._weekday_hourly_avg_kw = {}
        history_fetcher._weekend_hourly_avg_kw = {10: 2.0}
        history_fetcher._historical_load_cache = {10: 1.5}
        history_fetcher._historical_load_sample_counts = {10: 5}
        history_fetcher._profile_source = "weekday_weekend"

        avg, counts, source = history_fetcher.get_profile_for_day(date(2024, 1, 8))

        assert source == "combined"
        assert avg[10] == 1.5


class TestGetWeekdayWeekendProfiles:
    """Tests for get_weekday_profile, get_weekend_profile, get_profile_source."""

    def test_get_weekday_profile(self, history_fetcher):
        """Get weekday profile data."""
        history_fetcher._weekday_hourly_avg_kw = {10: 1.5, 14: 2.0}
        history_fetcher._weekday_sample_counts = {10: 5, 14: 8}

        avg, counts = history_fetcher.get_weekday_profile()

        assert avg == {10: 1.5, 14: 2.0}
        assert counts == {10: 5, 14: 8}

    def test_get_weekend_profile(self, history_fetcher):
        """Get weekend profile data."""
        history_fetcher._weekend_hourly_avg_kw = {10: 1.0}
        history_fetcher._weekend_sample_counts = {10: 3}

        avg, counts = history_fetcher.get_weekend_profile()

        assert avg == {10: 1.0}
        assert counts == {10: 3}

    def test_get_profile_source(self, history_fetcher):
        """Get profile source."""
        history_fetcher._profile_source = "weekday_weekend"
        assert history_fetcher.get_profile_source() == "weekday_weekend"

    def test_get_cached_hourly_averages(self, history_fetcher):
        """Get cached hourly averages."""
        history_fetcher._historical_load_cache = {10: 1.5}
        assert history_fetcher.get_cached_hourly_averages() == {10: 1.5}

    def test_clear_historical_cache(self, history_fetcher):
        """Clear historical cache."""
        history_fetcher._historical_load_cache = {10: 1.5}
        history_fetcher._historical_load_cache_date = "2024-01-01"

        history_fetcher.clear_historical_cache()

        assert history_fetcher._historical_load_cache == {}
        assert history_fetcher._historical_load_cache_date == ""


class TestDetermineProfileSource:
    """Tests for _determine_profile_source method."""

    def test_returns_weekday_weekend_when_sufficient(self, history_fetcher):
        """Return weekday_weekend when sufficient samples."""
        weekday_counts = {i: 10 for i in range(24)}
        weekend_counts = {i: 10 for i in range(24)}

        result = history_fetcher._determine_profile_source(
            weekday_counts, weekend_counts
        )

        assert result == "weekday_weekend"

    def test_returns_combined_when_insufficient_weekday(self, history_fetcher):
        """Return combined_fallback when weekday insufficient."""
        weekday_counts = {i: 2 for i in range(24)}
        weekend_counts = {i: 10 for i in range(24)}

        result = history_fetcher._determine_profile_source(
            weekday_counts, weekend_counts
        )

        assert result == "combined_fallback"

    def test_returns_combined_when_insufficient_weekend(self, history_fetcher):
        """Return combined_fallback when weekend insufficient."""
        weekday_counts = {i: 10 for i in range(24)}
        weekend_counts = {i: 2 for i in range(24)}

        result = history_fetcher._determine_profile_source(
            weekday_counts, weekend_counts
        )

        assert result == "combined_fallback"


class TestCalculateBaselineProfile:
    """Tests for calculate_baseline_profile method."""

    def test_calculates_baseline(self, history_fetcher):
        """Calculate baseline from samples."""
        non_hvac_samples = {10: [1.0, 2.0, 3.0, 4.0, 5.0]}

        result = history_fetcher.calculate_baseline_profile(non_hvac_samples)

        assert 10 in result

    def test_handles_empty_samples(self, history_fetcher):
        """Handle empty samples dict."""
        result = history_fetcher.calculate_baseline_profile({})

        assert result == {}


class TestSeparateSamplesByDayType:
    """Tests for _separate_samples_by_day_type method."""

    def test_separates_weekday_weekend(self, history_fetcher):
        """Separate samples into weekday and weekend."""
        from datetime import datetime, timezone

        rows = [
            {"start": 1704067200, "mean": 1.5},
            {"start": 1704153600, "mean": 2.5},
        ]
        local_tz = timezone.utc

        weekday, weekend = history_fetcher._separate_samples_by_day_type(rows, local_tz)

        assert isinstance(weekday, dict)
        assert isinstance(weekend, dict)

    def test_handles_non_dict_row(self, history_fetcher):
        """Skip rows that are not dicts."""
        from datetime import timezone

        rows = ["not a dict", {"start": 1704067200, "mean": 1.5}]
        local_tz = timezone.utc

        weekday, weekend = history_fetcher._separate_samples_by_day_type(rows, local_tz)

        assert len(weekday[0]) == 0 or len(weekday[0]) == 1

    def test_handles_datetime_start(self, history_fetcher):
        """Handle datetime start values."""
        from datetime import datetime, timezone

        rows = [
            {"start": datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc), "mean": 1.5}
        ]
        local_tz = timezone.utc

        weekday, weekend = history_fetcher._separate_samples_by_day_type(rows, local_tz)

        assert len(weekday[12]) == 1 or len(weekend[12]) == 1

    def test_handles_string_start(self, history_fetcher):
        """Handle string start values."""
        from datetime import timezone

        rows = [{"start": "2024-01-01T12:00:00+00:00", "mean": 1.5}]
        local_tz = timezone.utc

        weekday, weekend = history_fetcher._separate_samples_by_day_type(rows, local_tz)

        assert len(weekday[12]) == 1 or len(weekend[12]) == 1

    def test_handles_invalid_start(self, history_fetcher):
        """Skip rows with invalid start values."""
        from datetime import timezone

        rows = [{"start": "invalid", "mean": 1.5}]
        local_tz = timezone.utc

        weekday, weekend = history_fetcher._separate_samples_by_day_type(rows, local_tz)

        assert all(len(v) == 0 for v in weekday.values())

    def test_handles_none_mean(self, history_fetcher):
        """Skip rows with None mean."""
        from datetime import timezone

        rows = [{"start": 1704067200, "mean": None}]
        local_tz = timezone.utc

        weekday, weekend = history_fetcher._separate_samples_by_day_type(rows, local_tz)

        assert all(len(v) == 0 for v in weekday.values())

    def test_handles_unknown_mean(self, history_fetcher):
        """Skip rows with unknown mean."""
        from datetime import timezone

        rows = [{"start": 1704067200, "mean": "unknown"}]
        local_tz = timezone.utc

        weekday, weekend = history_fetcher._separate_samples_by_day_type(rows, local_tz)

        assert all(len(v) == 0 for v in weekday.values())

    def test_handles_invalid_mean_type(self, history_fetcher):
        """Skip rows with invalid mean type."""
        from datetime import timezone

        rows = [{"start": 1704067200, "mean": "not-a-number"}]
        local_tz = timezone.utc

        weekday, weekend = history_fetcher._separate_samples_by_day_type(rows, local_tz)

        assert all(len(v) == 0 for v in weekday.values())


class TestSeparateHvacLoad:
    """Tests for _separate_hvac_load method."""

    def test_separates_without_climate_states(self, history_fetcher):
        """Separate load without climate states."""
        weekday = {h: [] for h in range(24)}
        weekend = {h: [] for h in range(24)}
        weekday[10] = [1.0, 2.0]
        weekend[10] = [3.0]

        non_hvac, hvac = history_fetcher._separate_hvac_load(weekday, weekend, None)

        assert non_hvac[10] == [1.0, 2.0, 3.0]
        assert 10 not in hvac

    def test_separates_with_hvac_active(self, history_fetcher):
        """Separate load with HVAC active climate states."""
        weekday = {h: [] for h in range(24)}
        weekend = {h: [] for h in range(24)}
        weekday[10] = [1.0, 2.0]
        weekend[10] = [3.0]
        climate_states = {"climate.test": {"hvac_action": "cooling"}}

        non_hvac, hvac = history_fetcher._separate_hvac_load(
            weekday, weekend, climate_states
        )

        assert 10 not in non_hvac
        assert hvac[10] == [1.0, 2.0, 3.0]


class TestFetchHistoricalDataSyncHappyPath:
    """Tests for _fetch_historical_data_sync happy path."""

    def test_returns_profiles_with_valid_data(self, history_fetcher):
        """Return computed profiles with valid statistics data."""
        mock_recorder = MagicMock()
        mock_fn = MagicMock()

        with (
            patch.object(
                history_fetcher,
                "_import_recorder_statistics",
                return_value=mock_recorder,
            ),
            patch.object(history_fetcher, "_list_statistic_ids", return_value=[]),
            patch.object(
                history_fetcher, "_resolve_statistic_id", return_value="sensor.test"
            ),
            patch.object(history_fetcher, "_get_statistics_fn", return_value=mock_fn),
            patch.object(
                history_fetcher,
                "_fetch_statistics_data",
                return_value={"rows": [{"start": 1704067200, "mean": 1.5}]},
            ),
        ):
            result = history_fetcher._fetch_historical_data_sync(
                "sensor.test", datetime(2024, 1, 15, 12, 0, 0)
            )

            assert "combined_avg" in result
            assert "weekday_avg" in result
            assert "weekend_avg" in result


class TestAsyncGetHistoricalHourlyAveragesInsufficientData:
    """Tests for insufficient data fallback."""

    @pytest.mark.asyncio
    async def test_insufficient_data_sets_fallback_source(self, history_fetcher):
        """Set fallback source when less than 6 hours of data."""
        history_fetcher._historical_load_cache_date = "2020-01-01"

        mock_result = {
            "combined_avg": {10: 1.5, 11: 2.0},
            "combined_counts": {10: 5, 11: 6},
            "weekday_avg": {},
            "weekend_avg": {},
            "weekday_counts": {},
            "weekend_counts": {},
            "profile_source": "combined_fallback",
        }

        with patch(
            "homeassistant.components.recorder.get_instance"
        ) as mock_get_instance:
            mock_recorder = MagicMock()
            mock_recorder.async_add_executor_job = AsyncMock(return_value=mock_result)
            mock_get_instance.return_value = mock_recorder

            result = await history_fetcher.async_get_historical_hourly_averages(
                "sensor.test"
            )

            assert result[2] == "live_load_fallback"
            assert history_fetcher._profile_source == "live_load_fallback"


class TestSeparateSamplesWeekend:
    """Tests for weekend data handling in _separate_samples_by_day_type."""

    def test_handles_weekend_date(self, history_fetcher):
        """Handle weekend dates correctly - covers line 408."""
        from datetime import timezone

        rows = [{"start": 1704585600, "mean": 2.0}]
        local_tz = timezone.utc

        weekday, weekend = history_fetcher._separate_samples_by_day_type(rows, local_tz)

        has_weekend_data = any(len(v) > 0 for v in weekend.values())
        assert has_weekend_data


class TestCalculateBaselineEmptyValues:
    """Tests for empty values handling in calculate_baseline_profile."""

    def test_handles_empty_values_list(self, history_fetcher):
        """Handle empty values list - covers line 497."""
        non_hvac_samples = {10: []}

        result = history_fetcher.calculate_baseline_profile(non_hvac_samples)

        assert result[10] == 0.0


class TestResolveStatisticIdNonDict:
    """Tests for non-dict handling in _resolve_statistic_id."""

    def test_handles_non_dict_stat_id(self, history_fetcher):
        """Handle non-dict items in stat_ids - covers line 564."""
        stat_ids = ["not a dict", {"statistic_id": "sensor.test"}]

        result = history_fetcher._resolve_statistic_id("sensor.test", stat_ids)

        assert result == "sensor.test"


class TestSeparateHvacLoadActions:
    """Tests for different HVAC actions."""

    def test_handles_heating_action(self, history_fetcher):
        """Handle heating HVAC action."""
        weekday = {h: [] for h in range(24)}
        weekend = {h: [] for h in range(24)}
        weekday[10] = [1.0]
        climate_states = {"climate.test": {"hvac_action": "heating"}}

        non_hvac, hvac = history_fetcher._separate_hvac_load(
            weekday, weekend, climate_states
        )

        assert hvac[10] == [1.0]

    def test_handles_drying_action(self, history_fetcher):
        """Handle drying HVAC action."""
        weekday = {h: [] for h in range(24)}
        weekend = {h: [] for h in range(24)}
        weekday[10] = [1.0]
        climate_states = {"climate.test": {"hvac_action": "drying"}}

        non_hvac, hvac = history_fetcher._separate_hvac_load(
            weekday, weekend, climate_states
        )

        assert hvac[10] == [1.0]

    def test_handles_off_action(self, history_fetcher):
        """Handle off HVAC action - no HVAC load."""
        weekday = {h: [] for h in range(24)}
        weekend = {h: [] for h in range(24)}
        weekday[10] = [1.0]
        climate_states = {"climate.test": {"hvac_action": "off"}}

        non_hvac, hvac = history_fetcher._separate_hvac_load(
            weekday, weekend, climate_states
        )

        assert non_hvac[10] == [1.0]
        assert 10 not in hvac
