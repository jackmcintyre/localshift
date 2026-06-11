"""Tests for price_calculator.py helper functions."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from custom_components.localshift.engine.price_calculator import (
    _STALE_TARGET_REACHED_SOC_DEADBAND_PCT,
    _collect_prices_by_source,
    _compute_price_slot_data,
    _parse_price_entry,
    _target_reached_blocks_urgency,
    get_price_for_slot,
    get_price_for_slot_or_none,
    get_price_for_slot_with_source,
)
from custom_components.localshift.engine.price_calculator import PriceCalculator


def _make_utc_datetime(offset_minutes: int = 0) -> datetime:
    """Create a timezone-aware UTC datetime."""
    return datetime.now(timezone.utc) + timedelta(minutes=offset_minutes)


def _make_price_calculator() -> PriceCalculator:
    """Fresh PriceCalculator with deterministic base=0.10 and max_pre_charge_price=0.50."""

    def parse_dt(val):
        if val is None:
            return None
        try:
            return datetime.fromisoformat(val.replace("Z", "+00:00"))
        except Exception:
            return None

    entry = MagicMock()
    entry.options = {"max_pre_charge_price": 0.50}
    return PriceCalculator(
        entry=entry,
        parse_forecast_dt=parse_dt,
        percentile_func=lambda x, y: 0.10,
        sum_solar_before_target=lambda x, y, z: 10.0,
        get_expected_load_kw=lambda x, y: 1.0,
    )


class TestParsePriceEntry:
    """Tests for _parse_price_entry helper."""

    def test_parse_valid_entry(self):
        """Test parsing a valid price entry."""
        now = _make_utc_datetime()
        entry = {
            "start_time": now.isoformat(),
            "per_kwh": 0.15,
            "duration": 5,
        }
        result = _parse_price_entry(entry, now, now + timedelta(minutes=15))
        assert result is not None
        assert result["price"] == 0.15
        assert result["duration_minutes"] == 5
        assert result["is_overlap"] is True

    def test_parse_entry_overlapping_slot(self):
        """Test entry that overlaps with slot."""
        now = _make_utc_datetime()
        entry = {
            "start_time": (now - timedelta(minutes=5)).isoformat(),
            "per_kwh": 0.20,
            "duration": 30,
        }
        result = _parse_price_entry(entry, now, now + timedelta(minutes=15))
        assert result is not None
        assert result["is_overlap"] is True

    def test_parse_entry_not_overlapping_slot(self):
        """Test entry that does not overlap with slot."""
        now = _make_utc_datetime()
        entry = {
            "start_time": (now + timedelta(minutes=30)).isoformat(),
            "per_kwh": 0.25,
            "duration": 5,
        }
        result = _parse_price_entry(entry, now, now + timedelta(minutes=15))
        assert result is not None
        assert result["is_overlap"] is False

    def test_parse_entry_is_fallback_candidate(self):
        """Test entry that is a fallback candidate."""
        now = _make_utc_datetime()
        entry = {
            "start_time": (now - timedelta(minutes=10)).isoformat(),
            "per_kwh": 0.18,
            "duration": 30,
        }
        result = _parse_price_entry(entry, now, now + timedelta(minutes=15))
        assert result is not None
        assert result["is_fallback_candidate"] is True

    def test_parse_entry_not_fallback_candidate(self):
        """Test entry that starts after slot_start."""
        now = _make_utc_datetime()
        entry = {
            "start_time": (now + timedelta(minutes=5)).isoformat(),
            "per_kwh": 0.22,
            "duration": 5,
        }
        result = _parse_price_entry(entry, now, now + timedelta(minutes=15))
        assert result is not None
        assert result["is_fallback_candidate"] is False

    def test_parse_invalid_entry_not_dict(self):
        """Test that non-dict entries return None."""
        result = _parse_price_entry(
            "not a dict", _make_utc_datetime(), _make_utc_datetime()
        )
        assert result is None

    def test_parse_entry_missing_start_time(self):
        """Test that entries without start_time return None."""
        entry = {"per_kwh": 0.15}
        result = _parse_price_entry(entry, _make_utc_datetime(), _make_utc_datetime())
        assert result is None

    def test_parse_entry_default_duration(self):
        """Test default duration of 5 minutes."""
        now = _make_utc_datetime()
        entry = {
            "start_time": now.isoformat(),
            "per_kwh": 0.10,
        }
        result = _parse_price_entry(entry, now, now + timedelta(minutes=15))
        assert result is not None
        assert result["duration_minutes"] == 5

    def test_parse_entry_default_price(self):
        """Test default price of 0.0."""
        now = _make_utc_datetime()
        entry = {
            "start_time": now.isoformat(),
            "duration": 30,
        }
        result = _parse_price_entry(entry, now, now + timedelta(minutes=15))
        assert result is not None
        assert result["price"] == 0.0


class TestComputePriceSlotData:
    """Tests for _compute_price_slot_data helper."""

    def test_empty_forecast_returns_empty(self):
        """Test empty forecast returns empty lists."""
        prices, fallback, start = _compute_price_slot_data([], _make_utc_datetime())
        assert prices == []
        assert fallback == 0.0
        assert start is None

    def test_single_overlapping_price(self):
        """Test single price overlapping slot."""
        now = _make_utc_datetime()
        forecast = [
            {
                "start_time": now.isoformat(),
                "per_kwh": 0.20,
                "duration": 30,
            }
        ]
        prices, fallback, _ = _compute_price_slot_data(forecast, now)
        assert prices == [0.20]
        assert fallback == 0.20

    def test_multiple_overlapping_prices_averaged(self):
        """Test multiple overlapping prices are collected."""
        now = _make_utc_datetime()
        forecast = [
            {
                "start_time": now.isoformat(),
                "per_kwh": 0.10,
                "duration": 5,
            },
            {
                "start_time": (now + timedelta(minutes=5)).isoformat(),
                "per_kwh": 0.15,
                "duration": 5,
            },
        ]
        prices, _, _ = _compute_price_slot_data(forecast, now)
        assert 0.10 in prices
        assert 0.15 in prices

    def test_fallback_from_prior_entry(self):
        """Test fallback from entry starting before slot."""
        now = _make_utc_datetime()
        forecast = [
            {
                "start_time": (now - timedelta(minutes=30)).isoformat(),
                "per_kwh": 0.25,
                "duration": 30,
            },
            {
                "start_time": (now + timedelta(minutes=30)).isoformat(),
                "per_kwh": 0.30,
                "duration": 30,
            },
        ]
        prices, fallback, _ = _compute_price_slot_data(forecast, now)
        assert prices == []
        assert fallback == 0.25

    def test_naive_datetime_converted_to_local(self):
        """Test naive datetime is converted to local timezone."""
        naive_now = datetime.now()
        forecast = [
            {
                "start_time": naive_now.isoformat(),
                "per_kwh": 0.12,
                "duration": 30,
            }
        ]
        prices, fallback, _ = _compute_price_slot_data(forecast, naive_now)
        assert prices == [0.12]


class TestCollectPricesBySource:
    """Tests for _collect_prices_by_source helper."""

    def test_empty_forecast(self):
        """Test empty forecast returns empty data."""
        result = _collect_prices_by_source([], _make_utc_datetime(), 15)
        assert result["prices_5min"] == []
        assert result["prices_30min"] == []
        assert result["fallback_price"] == 0.0
        assert result["fallback_source"] == "unknown"

    def test_5min_prices_collected(self):
        """Test 5-minute prices are collected separately."""
        now = _make_utc_datetime()
        forecast = [
            {
                "start_time": now.isoformat(),
                "per_kwh": 0.10,
                "duration": 5,
            },
            {
                "start_time": (now + timedelta(minutes=5)).isoformat(),
                "per_kwh": 0.12,
                "duration": 5,
            },
        ]
        result = _collect_prices_by_source(forecast, now, 15)
        assert result["prices_5min"] == [0.10, 0.12]
        assert result["prices_30min"] == []

    def test_30min_prices_collected(self):
        """Test 30-minute prices are collected separately."""
        now = _make_utc_datetime()
        forecast = [
            {
                "start_time": now.isoformat(),
                "per_kwh": 0.15,
                "duration": 30,
            }
        ]
        result = _collect_prices_by_source(forecast, now, 15)
        assert result["prices_30min"] == [0.15]
        assert result["prices_5min"] == []

    def test_mixed_sources(self):
        """Test mixed 5min and 30min data."""
        now = _make_utc_datetime()
        forecast = [
            {
                "start_time": now.isoformat(),
                "per_kwh": 0.10,
                "duration": 5,
            },
            {
                "start_time": (now + timedelta(minutes=5)).isoformat(),
                "per_kwh": 0.20,
                "duration": 30,
            },
        ]
        result = _collect_prices_by_source(forecast, now, 15)
        assert result["prices_5min"] == [0.10]
        assert result["prices_30min"] == [0.20]

    def test_fallback_tracking(self):
        """Test fallback price and source tracking."""
        now = _make_utc_datetime()
        forecast = [
            {
                "start_time": (now - timedelta(minutes=10)).isoformat(),
                "per_kwh": 0.18,
                "duration": 30,
            }
        ]
        result = _collect_prices_by_source(forecast, now, 15)
        assert result["fallback_price"] == 0.18
        assert result["fallback_source"] == "30min"

    def test_custom_interval_minutes(self):
        """Test custom interval minutes for slot end."""
        now = _make_utc_datetime()
        forecast = [
            {
                "start_time": now.isoformat(),
                "per_kwh": 0.25,
                "duration": 30,
            }
        ]
        result = _collect_prices_by_source(forecast, now, 30)
        assert result["prices_30min"] == [0.25]


class TestGetPriceForSlot:
    """Tests for get_price_for_slot function."""

    def test_empty_forecast_returns_zero(self):
        """Test empty forecast returns 0.0."""
        assert get_price_for_slot([], _make_utc_datetime()) == 0.0

    def test_single_price_forecast(self):
        """Test single price forecast."""
        now = _make_utc_datetime()
        forecast = [
            {
                "start_time": now.isoformat(),
                "per_kwh": 0.25,
                "duration": 30,
            }
        ]
        assert get_price_for_slot(forecast, now) == 0.25

    def test_multiple_prices_averaged(self):
        """Test multiple prices are averaged."""
        now = _make_utc_datetime()
        forecast = [
            {
                "start_time": now.isoformat(),
                "per_kwh": 0.20,
                "duration": 5,
            },
            {
                "start_time": (now + timedelta(minutes=5)).isoformat(),
                "per_kwh": 0.30,
                "duration": 5,
            },
        ]
        assert get_price_for_slot(forecast, now) == 0.25

    def test_fallback_when_no_overlap(self):
        """Test fallback when no prices overlap."""
        now = _make_utc_datetime()
        forecast = [
            {
                "start_time": (now - timedelta(minutes=30)).isoformat(),
                "per_kwh": 0.15,
                "duration": 30,
            }
        ]
        assert get_price_for_slot(forecast, now) == 0.15


class TestGetPriceForSlotWithSource:
    """Tests for get_price_for_slot_with_source function."""

    def test_empty_forecast_returns_unknown(self):
        """Test empty forecast returns 0.0, unknown."""
        price, source = get_price_for_slot_with_source([], _make_utc_datetime())
        assert price == 0.0
        assert source == "unknown"

    def test_5min_data_preferred(self):
        """Test 5-min data is preferred over 30-min."""
        now = _make_utc_datetime()
        forecast = [
            {
                "start_time": now.isoformat(),
                "per_kwh": 0.10,
                "duration": 5,
            }
        ]
        price, source = get_price_for_slot_with_source(forecast, now)
        assert price == 0.10
        assert source == "5min"

    def test_30min_data_fallback(self):
        """Test 30-min data is used when no 5-min available."""
        now = _make_utc_datetime()
        forecast = [
            {
                "start_time": now.isoformat(),
                "per_kwh": 0.15,
                "duration": 30,
            }
        ]
        price, source = get_price_for_slot_with_source(forecast, now)
        assert price == 0.15
        assert source == "30min"

    def test_5min_priority_over_30min(self):
        """Test 5-min data takes priority when both available."""
        now = _make_utc_datetime()
        forecast = [
            {
                "start_time": now.isoformat(),
                "per_kwh": 0.10,
                "duration": 5,
            },
            {
                "start_time": now.isoformat(),
                "per_kwh": 0.20,
                "duration": 30,
            },
        ]
        price, source = get_price_for_slot_with_source(forecast, now)
        assert price == 0.10
        assert source == "5min"

    def test_custom_interval(self):
        """Test custom interval minutes."""
        now = _make_utc_datetime()
        forecast = [
            {
                "start_time": now.isoformat(),
                "per_kwh": 0.22,
                "duration": 30,
            }
        ]
        price, source = get_price_for_slot_with_source(forecast, now, 30)
        assert price == 0.22
        assert source == "30min"

    def test_fallback_source(self):
        """Test fallback when no overlapping data."""
        now = _make_utc_datetime()
        forecast = [
            {
                "start_time": (now - timedelta(minutes=10)).isoformat(),
                "per_kwh": 0.18,
                "duration": 5,
            }
        ]
        price, source = get_price_for_slot_with_source(forecast, now)
        assert price == 0.18
        assert source == "5min"


class TestGetPriceForSlotOrNull:
    """Tests for get_price_for_slot_or_none function."""

    def test_empty_forecast_returns_none(self):
        """Test empty forecast returns None."""
        assert get_price_for_slot_or_none([], _make_utc_datetime()) is None

    def test_overlapping_price_returns_value(self):
        """Test overlapping price returns value."""
        now = _make_utc_datetime()
        forecast = [
            {
                "start_time": now.isoformat(),
                "per_kwh": 0.30,
                "duration": 30,
            }
        ]
        assert get_price_for_slot_or_none(forecast, now) == 0.30

    def test_no_overlap_no_fallback_returns_none(self):
        """Test None when no overlap and no fallback candidate."""
        now = _make_utc_datetime()
        forecast = [
            {
                "start_time": (now + timedelta(minutes=30)).isoformat(),
                "per_kwh": 0.25,
                "duration": 30,
            }
        ]
        assert get_price_for_slot_or_none(forecast, now) is None

    def test_fallback_returns_value(self):
        """Test fallback price when no overlap but fallback exists."""
        now = _make_utc_datetime()
        forecast = [
            {
                "start_time": (now - timedelta(minutes=10)).isoformat(),
                "per_kwh": 0.20,
                "duration": 30,
            }
        ]
        assert get_price_for_slot_or_none(forecast, now) == 0.20


class TestPriceCalculatorInit:
    """Tests for PriceCalculator initialization."""

    def test_init_stores_dependencies(self):
        """Test __init__ stores all dependencies."""
        entry = MagicMock()
        entry.options = {}
        calc = PriceCalculator(
            entry=entry,
            parse_forecast_dt=lambda x: None,
            percentile_func=lambda x, y: 0.0,
            sum_solar_before_target=lambda x, y, z: 0.0,
            get_expected_load_kw=lambda x, y: 0.0,
        )
        assert calc.entry is entry
        assert calc._smoothed_effective_cheap_price is None


class TestPriceCalculatorHysteresis:
    """Tests for _apply_threshold_hysteresis method."""

    @pytest.fixture
    def calculator(self):
        """Create a PriceCalculator instance."""
        entry = MagicMock()
        entry.options = {}
        return PriceCalculator(
            entry=entry,
            parse_forecast_dt=lambda x: None,
            percentile_func=lambda x, y: 0.0,
            sum_solar_before_target=lambda x, y, z: 0.0,
            get_expected_load_kw=lambda x, y: 0.0,
        )

    def test_first_call_initializes(self, calculator):
        """Test first call initializes smoothed value."""
        result = calculator._apply_threshold_hysteresis(0.15)
        assert result == 0.15
        assert calculator._smoothed_effective_cheap_price == 0.15

    def test_small_change_ignored(self, calculator):
        """Test changes below hysteresis threshold are ignored."""
        calculator._apply_threshold_hysteresis(0.15)
        result = calculator._apply_threshold_hysteresis(0.16)
        assert result == 0.15

    def test_large_change_applied(self, calculator):
        """Test changes above hysteresis threshold are applied."""
        calculator._apply_threshold_hysteresis(0.15)
        result = calculator._apply_threshold_hysteresis(0.20)
        assert result != 0.15
        assert 0.15 < result < 0.20

    def test_smoothing_applied(self, calculator):
        """Test EMA smoothing is applied."""
        calculator._apply_threshold_hysteresis(0.10)
        result = calculator._apply_threshold_hysteresis(0.20)
        expected = 0.3 * 0.20 + 0.7 * 0.10
        assert abs(result - round(expected, 2)) < 0.01


class TestPriceCalculatorFitPrice:
    """Tests for _get_fit_price_for_period method."""

    @pytest.fixture
    def calculator(self):
        """Create a PriceCalculator instance with parse_forecast_dt."""
        from homeassistant.util import dt as dt_util

        def parse_dt(val):
            if val is None:
                return None
            try:
                from datetime import datetime

                return datetime.fromisoformat(val.replace("Z", "+00:00"))
            except Exception:
                return None

        entry = MagicMock()
        entry.options = {}
        return PriceCalculator(
            entry=entry,
            parse_forecast_dt=parse_dt,
            percentile_func=lambda x, y: 0.0,
            sum_solar_before_target=lambda x, y, z: 0.0,
            get_expected_load_kw=lambda x, y: 0.0,
        )

    def test_matching_period(self, calculator):
        """Test FIT price returned for matching period."""
        now = _make_utc_datetime()
        forecast = [
            {
                "start_time": now.isoformat(),
                "end_time": (now + timedelta(minutes=30)).isoformat(),
                "per_kwh": 0.08,
            }
        ]
        mid = now + timedelta(minutes=15)
        result = calculator._get_fit_price_for_period(forecast, mid)
        assert result == 0.08

    def test_no_matching_period(self, calculator):
        """Test 0.0 returned when no period matches."""
        now = _make_utc_datetime()
        forecast = [
            {
                "start_time": (now + timedelta(hours=1)).isoformat(),
                "end_time": (now + timedelta(hours=2)).isoformat(),
                "per_kwh": 0.10,
            }
        ]
        mid = now + timedelta(minutes=15)
        result = calculator._get_fit_price_for_period(forecast, mid)
        assert result == 0.0

    def test_multiple_periods(self, calculator):
        """Test first matching period is used."""
        now = _make_utc_datetime()
        forecast = [
            {
                "start_time": (now - timedelta(hours=1)).isoformat(),
                "end_time": now.isoformat(),
                "per_kwh": 0.05,
            },
            {
                "start_time": now.isoformat(),
                "end_time": (now + timedelta(minutes=30)).isoformat(),
                "per_kwh": 0.07,
            },
        ]
        mid = now + timedelta(minutes=10)
        result = calculator._get_fit_price_for_period(forecast, mid)
        assert result == 0.07


class TestPriceCalculatorWeightedFit:
    """Tests for _compute_weighted_fit method."""

    @pytest.fixture
    def calculator(self):
        """Create a PriceCalculator instance."""
        from homeassistant.util import dt as dt_util

        def parse_dt(val):
            if val is None:
                return None
            try:
                from datetime import datetime

                return datetime.fromisoformat(val.replace("Z", "+00:00"))
            except Exception:
                return None

        entry = MagicMock()
        entry.options = {}
        return PriceCalculator(
            entry=entry,
            parse_forecast_dt=parse_dt,
            percentile_func=lambda x, y: 0.0,
            sum_solar_before_target=lambda x, y, z: 0.0,
            get_expected_load_kw=lambda x, y: 0.0,
        )

    def test_single_period(self, calculator):
        """Test weighted fit for single solar period."""
        now = _make_utc_datetime()
        target = now + timedelta(hours=4)

        solcast = [
            {
                "period_start": now.isoformat(),
                "pv_estimate": 2.0,
            }
        ]
        fit_forecast = [
            {
                "start_time": now.isoformat(),
                "end_time": (now + timedelta(minutes=30)).isoformat(),
                "per_kwh": 0.10,
            }
        ]

        weighted_sum, total_solar = calculator._compute_weighted_fit(
            solcast, fit_forecast, now, target
        )
        assert total_solar == 2.0
        assert weighted_sum == 0.20

    def test_multiple_periods(self, calculator):
        """Test weighted fit for multiple solar periods."""
        now = _make_utc_datetime()
        target = now + timedelta(hours=4)

        solcast = [
            {
                "period_start": now.isoformat(),
                "pv_estimate": 1.0,
            },
            {
                "period_start": (now + timedelta(minutes=30)).isoformat(),
                "pv_estimate": 2.0,
            },
        ]
        fit_forecast = [
            {
                "start_time": now.isoformat(),
                "end_time": (now + timedelta(hours=2)).isoformat(),
                "per_kwh": 0.08,
            }
        ]

        weighted_sum, total_solar = calculator._compute_weighted_fit(
            solcast, fit_forecast, now, target
        )
        assert total_solar == 3.0
        assert weighted_sum == 0.24

    def test_no_solar_in_range(self, calculator):
        """Test zero solar when no periods in range."""
        now = _make_utc_datetime()
        target = now + timedelta(hours=2)

        solcast = [
            {
                "period_start": (now + timedelta(hours=4)).isoformat(),
                "pv_estimate": 2.0,
            }
        ]
        fit_forecast = []

        weighted_sum, total_solar = calculator._compute_weighted_fit(
            solcast, fit_forecast, now, target
        )
        assert total_solar == 0.0
        assert weighted_sum == 0.0

    def test_zero_solar_skipped(self, calculator):
        """Test periods with zero solar are skipped."""
        now = _make_utc_datetime()
        target = now + timedelta(hours=4)

        solcast = [
            {
                "period_start": now.isoformat(),
                "pv_estimate": 0.0,
            }
        ]
        fit_forecast = []

        weighted_sum, total_solar = calculator._compute_weighted_fit(
            solcast, fit_forecast, now, target
        )
        assert total_solar == 0.0


class TestPriceCalculatorSolarWeightedAvgFit:
    """Tests for compute_solar_weighted_avg_fit method."""

    @pytest.fixture
    def calculator(self):
        """Create a PriceCalculator instance."""
        from homeassistant.util import dt as dt_util

        def parse_dt(val):
            if val is None:
                return None
            try:
                from datetime import datetime

                return datetime.fromisoformat(val.replace("Z", "+00:00"))
            except Exception:
                return None

        entry = MagicMock()
        entry.options = {}
        return PriceCalculator(
            entry=entry,
            parse_forecast_dt=parse_dt,
            percentile_func=lambda x, y: 0.0,
            sum_solar_before_target=lambda x, y, z: 0.0,
            get_expected_load_kw=lambda x, y: 0.0,
        )

    def test_after_dw_returns_zero(self, calculator):
        """Test zero values when after demand window."""
        data = MagicMock()
        data.solcast_today = []
        data.solcast_tomorrow = []
        data.feed_in_forecast = []

        calculator.compute_solar_weighted_avg_fit(
            data, _make_utc_datetime(), 18, after_dw=True
        )
        assert data.solar_weighted_avg_fit == 0.0
        assert data.solar_remaining_kwh == 0.0

    def test_computes_weighted_average(self, calculator):
        """Test weighted average is computed correctly."""
        now = _make_utc_datetime()
        target_hour = (now + timedelta(hours=6)).hour
        data = MagicMock()
        data.solcast_today = [
            {
                "period_start": now.isoformat(),
                "pv_estimate": 2.0,
            }
        ]
        data.solcast_tomorrow = []
        data.feed_in_forecast = [
            {
                "start_time": now.isoformat(),
                "end_time": (now + timedelta(minutes=30)).isoformat(),
                "per_kwh": 0.10,
            }
        ]

        calculator.compute_solar_weighted_avg_fit(data, now, target_hour, after_dw=False)
        assert data.solar_weighted_avg_fit == 0.10
        assert data.solar_remaining_kwh == 2.0

    def test_no_solar_defaults_to_zero(self, calculator):
        """Test zero values when no solar forecast."""
        now = _make_utc_datetime()
        target_hour = (now + timedelta(hours=6)).hour
        data = MagicMock()
        data.solcast_today = []
        data.solcast_tomorrow = []
        data.feed_in_forecast = []

        calculator.compute_solar_weighted_avg_fit(data, now, target_hour, after_dw=False)
        assert data.solar_weighted_avg_fit == 0.0
        assert data.solar_remaining_kwh == 0.0


class TestPriceCalculatorCollectForecastPrices:
    """Tests for _collect_forecast_prices_and_base method."""

    @pytest.fixture
    def calculator(self):
        """Create a PriceCalculator instance."""
        from homeassistant.util import dt as dt_util

        def parse_dt(val):
            if val is None:
                return None
            try:
                from datetime import datetime

                return datetime.fromisoformat(val.replace("Z", "+00:00"))
            except Exception:
                return None

        entry = MagicMock()
        entry.options = {
            "cheap_price_percentile": 0.2,
            "max_pre_charge_price": 0.30,
        }
        return PriceCalculator(
            entry=entry,
            parse_forecast_dt=parse_dt,
            percentile_func=lambda prices, pct: (
                sorted(prices)[int(len(prices) * pct)] if prices else 0.0
            ),
            sum_solar_before_target=lambda x, y, z: 0.0,
            get_expected_load_kw=lambda x, y: 0.0,
        )

    def test_collects_prices_in_horizon(self, calculator):
        """Test prices within horizon are collected."""
        now = _make_utc_datetime()
        forecast = [
            {
                "start_time": now.isoformat(),
                "per_kwh": 0.10,
            },
            {
                "start_time": (now + timedelta(hours=1)).isoformat(),
                "per_kwh": 0.15,
            },
            {
                "start_time": (now + timedelta(hours=30)).isoformat(),
                "per_kwh": 0.20,
            },
        ]

        prices, base, max_price = calculator._collect_forecast_prices_and_base(
            forecast, now, 24.0
        )
        assert len(prices) == 2
        assert 0.10 in prices
        assert 0.15 in prices

    def test_empty_forecast_uses_max_precharge(self, calculator):
        """Test empty forecast uses max precharge price."""
        now = _make_utc_datetime()
        prices, base, max_price = calculator._collect_forecast_prices_and_base(
            [], now, 24.0
        )
        assert prices == []
        assert base == 0.30
        assert max_price == 0.30

    def test_horizon_factor_applied(self, calculator):
        """Test horizon factor scales percentile."""
        now = _make_utc_datetime()
        forecast = [
            {
                "start_time": now.isoformat(),
                "per_kwh": 0.10,
            },
        ]

        prices, base, max_price = calculator._collect_forecast_prices_and_base(
            forecast, now, 12.0
        )
        assert len(prices) == 1


class TestPriceCalculatorUrgencyAdjustedPrice:
    """Tests for _calculate_urgency_adjusted_price method."""

    @pytest.fixture
    def calculator(self):
        """Create a PriceCalculator instance."""
        from homeassistant.util import dt as dt_util

        def parse_dt(val):
            if val is None:
                return None
            try:
                from datetime import datetime

                return datetime.fromisoformat(val.replace("Z", "+00:00"))
            except Exception:
                return None

        entry = MagicMock()
        entry.options = {"max_pre_charge_price": 0.50}
        return PriceCalculator(
            entry=entry,
            parse_forecast_dt=parse_dt,
            percentile_func=lambda x, y: 0.0,
            sum_solar_before_target=lambda x, y, z: 0.0,
            get_expected_load_kw=lambda x, y: 0.0,
        )

    def test_no_urgency_when_far_from_target(self, calculator):
        """Test no urgency adjustment when far from target."""
        now = _make_utc_datetime()
        target_hour = (now + timedelta(hours=6)).hour
        data = MagicMock()
        data.soc = 50.0
        data.general_forecast = [
            {
                "start_time": now.isoformat(),
                "per_kwh": 0.10,
            }
        ]

        result = calculator._calculate_urgency_adjusted_price(
            data, now, target_hour, base=0.10, max_price=0.50, target_pct=0.0
        )
        assert result == 0.10

    def test_high_urgency_near_target(self, calculator):
        """Test urgency increases price near target."""
        now = _make_utc_datetime().replace(minute=0, second=0, microsecond=0)
        target_hour = (now + timedelta(hours=1)).hour
        data = MagicMock()
        data.soc = 50.0
        data.general_forecast = []

        result = calculator._calculate_urgency_adjusted_price(
            data, now, target_hour, base=0.10, max_price=0.50, target_pct=0.0
        )
        assert result > 0.10

    def test_respects_forecast_floor(self, calculator):
        """Test price respects minimum forecast price."""
        now = _make_utc_datetime().replace(minute=0, second=0, microsecond=0)
        target_hour = (now + timedelta(hours=1)).hour
        data = MagicMock()
        data.soc = 50.0
        data.general_forecast = [
            {
                "start_time": now.isoformat(),
                "per_kwh": 0.25,
            }
        ]

        result = calculator._calculate_urgency_adjusted_price(
            data, now, target_hour, base=0.10, max_price=0.50, target_pct=0.0
        )
        assert result >= 0.25

    def test_capped_at_max_price(self, calculator):
        """Test urgency price is capped at max_price."""
        now = _make_utc_datetime().replace(minute=0, second=0, microsecond=0)
        target_hour = (now + timedelta(minutes=30)).hour
        data = MagicMock()
        data.soc = 50.0
        data.general_forecast = []

        result = calculator._calculate_urgency_adjusted_price(
            data, now, target_hour, base=0.40, max_price=0.50, target_pct=0.0
        )
        assert result <= 0.50

    def test_deep_deficit_widens_urgency_window(self, calculator):
        """A deep SOC deficit engages urgency at 4.1h out, where a fixed 4h window cannot.

        With a forecast floor pinned at the base price, urgency only lifts the threshold
        when the deficit-derived window reaches past ``hours_left``. From 11% -> 95% the
        window is ~4.235h, so at 4.1h out urgency is engaged (> base); the legacy 4h window
        (target_pct=0) and a near-target deficit both leave the threshold at base.
        """
        # now 10:54, target 15:00 -> exactly 4.1h of runway.
        now = datetime(2026, 6, 11, 10, 54, tzinfo=timezone.utc)
        target_hour = 15
        # Forecast floor == base so urgency_price is what drives the result, not the floor.
        forecast = [{"start_time": now.isoformat(), "per_kwh": 0.10}]

        def _call(soc, target_pct):
            data = MagicMock()
            data.soc = soc
            data.general_forecast = forecast
            return calculator._calculate_urgency_adjusted_price(
                data, now, target_hour, base=0.10, max_price=0.50, target_pct=target_pct
            )

        deep = _call(soc=11.0, target_pct=95.0)
        legacy = _call(soc=11.0, target_pct=0.0)
        near_target = _call(soc=90.0, target_pct=95.0)

        assert deep > 0.10, "deep deficit must engage urgency at 4.1h out"
        assert legacy == 0.10, "legacy 4h window: no urgency beyond 4h out"
        assert near_target == 0.10, "shallow deficit stays at the 4h floor"


class TestComputeEffectiveCheapPricePreliminary:
    """Tests for compute_effective_cheap_price_preliminary method."""

    @pytest.fixture
    def calculator(self):
        """Create a PriceCalculator instance."""
        from datetime import datetime

        def parse_dt(val):
            if val is None:
                return None
            try:
                return datetime.fromisoformat(val.replace("Z", "+00:00"))
            except Exception:
                return None

        entry = MagicMock()
        entry.options = {"max_pre_charge_price": 0.50}
        return PriceCalculator(
            entry=entry,
            parse_forecast_dt=parse_dt,
            percentile_func=lambda x, y: 0.10,
            sum_solar_before_target=lambda x, y, z: 10.0,
            get_expected_load_kw=lambda x, y: 1.0,
        )

    def test_no_urgency_when_solar_can_reach(self, calculator):
        """Test base price when solar can reach target."""
        now = _make_utc_datetime()
        data = MagicMock()
        data.general_forecast = [{"start_time": now.isoformat(), "per_kwh": 0.15}]
        data.forecast_horizon_hours = 12.0
        data.soc = 80.0
        data.target_reached_today = False
        data.solcast_today = []
        data.solcast_tomorrow = []

        calculator.compute_effective_cheap_price_preliminary(
            data, now, before_dw=True, target_hour=18, target_pct=70.0
        )
        assert data.effective_cheap_price == 0.10

    def test_urgency_when_solar_gap_and_before_dw(self, calculator):
        """Test urgency price when solar gap and before demand window."""
        now = _make_utc_datetime()
        data = MagicMock()
        data.general_forecast = [{"start_time": now.isoformat(), "per_kwh": 0.10}]
        data.forecast_horizon_hours = 12.0
        data.soc = 30.0
        data.target_reached_today = False
        data.solcast_today = []
        data.solcast_tomorrow = []

        calculator.compute_effective_cheap_price_preliminary(
            data, now, before_dw=True, target_hour=18, target_pct=80.0
        )
        assert data.effective_cheap_price is not None

    def test_base_price_when_target_reached(self, calculator):
        """Base price when target reached today AND battery still near target.

        The target-reached latch only suppresses urgency within a SOC deadband of the
        target (a stale latch with a low SOC must NOT suppress pre-charge — see
        TestComputeEffectiveCheapPriceStaleLatch). Here SOC is within the deadband, so
        the latch is honored and the threshold stays at base.
        """
        now = _make_utc_datetime()
        data = MagicMock()
        data.general_forecast = [{"start_time": now.isoformat(), "per_kwh": 0.15}]
        data.forecast_horizon_hours = 12.0
        data.soc = 75.0  # within deadband of target_pct=80 (80 - 20 = 60)
        data.target_reached_today = True
        data.solcast_today = []
        data.solcast_tomorrow = []

        calculator.compute_effective_cheap_price_preliminary(
            data, now, before_dw=True, target_hour=18, target_pct=80.0
        )
        assert data.effective_cheap_price == 0.10

    def test_base_price_when_not_before_dw(self, calculator):
        """Test base price when not before demand window."""
        now = _make_utc_datetime()
        data = MagicMock()
        data.general_forecast = [{"start_time": now.isoformat(), "per_kwh": 0.15}]
        data.forecast_horizon_hours = 12.0
        data.soc = 30.0
        data.target_reached_today = False
        data.solcast_today = []
        data.solcast_tomorrow = []

        calculator.compute_effective_cheap_price_preliminary(
            data, now, before_dw=False, target_hour=18, target_pct=80.0
        )
        assert data.effective_cheap_price == 0.10


class TestComputeEffectiveCheapPrice:
    """Tests for compute_effective_cheap_price method."""

    @pytest.fixture
    def calculator(self):
        """Create a PriceCalculator instance."""
        from datetime import datetime

        def parse_dt(val):
            if val is None:
                return None
            try:
                return datetime.fromisoformat(val.replace("Z", "+00:00"))
            except Exception:
                return None

        entry = MagicMock()
        entry.options = {"max_pre_charge_price": 0.50}
        return PriceCalculator(
            entry=entry,
            parse_forecast_dt=parse_dt,
            percentile_func=lambda x, y: 0.10,
            sum_solar_before_target=lambda x, y, z: 10.0,
            get_expected_load_kw=lambda x, y: 1.0,
        )

    def test_base_price_when_no_solar_gap(self, calculator):
        """Test base price when solar can reach target."""
        now = _make_utc_datetime()
        data = MagicMock()
        data.general_forecast = [{"start_time": now.isoformat(), "per_kwh": 0.15}]
        data.forecast_horizon_hours = 12.0
        data.solar_can_reach_target = True
        data.target_reached_today = False

        calculator.compute_effective_cheap_price(
            data, now, target_hour=18, before_dw=True
        )
        assert data.effective_cheap_price == 0.10

    def test_urgency_when_solar_gap(self, calculator):
        """Test urgency price when solar cannot reach target."""
        now = _make_utc_datetime()
        data = MagicMock()
        data.soc = 50.0
        data.general_forecast = [{"start_time": now.isoformat(), "per_kwh": 0.10}]
        data.forecast_horizon_hours = 12.0
        data.solar_can_reach_target = False
        data.target_reached_today = False

        calculator.compute_effective_cheap_price(
            data, now, target_hour=18, before_dw=True
        )
        assert data.effective_cheap_price is not None

    def test_base_price_when_not_before_dw(self, calculator):
        """Test base price when not before demand window."""
        now = _make_utc_datetime()
        data = MagicMock()
        data.general_forecast = [{"start_time": now.isoformat(), "per_kwh": 0.15}]
        data.forecast_horizon_hours = 12.0
        data.solar_can_reach_target = False
        data.target_reached_today = False

        calculator.compute_effective_cheap_price(
            data, now, target_hour=18, before_dw=False
        )
        assert data.effective_cheap_price == 0.10


class TestTargetReachedBlocksUrgency:
    """Tests for the stale-latch SOC guard (_target_reached_blocks_urgency).

    Regression for the demand-window pre-charge failure: target_reached_today is a daily
    latch whose only live reset is a single midnight event. A stale latch must not suppress
    urgency pre-charge while the battery sits far below target before the demand window.
    """

    def test_latch_clear_never_blocks(self):
        data = MagicMock()
        data.target_reached_today = False
        data.soc = 12.0
        assert _target_reached_blocks_urgency(data, target_pct=95.0) is False

    def test_stale_latch_with_low_soc_does_not_block(self):
        # The bug: latch True but battery far below target before the DW must NOT
        # suppress urgency pre-charge.
        data = MagicMock()
        data.target_reached_today = True
        data.soc = 12.0
        assert _target_reached_blocks_urgency(data, target_pct=95.0) is False

    def test_latch_honored_within_deadband(self):
        # Near target: honor the latch so we don't re-charge (sawtooth avoidance).
        data = MagicMock()
        data.target_reached_today = True
        data.soc = 95.0
        assert _target_reached_blocks_urgency(data, target_pct=95.0) is True

    def test_deadband_boundary(self):
        data = MagicMock()
        data.target_reached_today = True
        boundary = 95.0 - _STALE_TARGET_REACHED_SOC_DEADBAND_PCT
        data.soc = boundary
        assert _target_reached_blocks_urgency(data, target_pct=95.0) is True
        data.soc = boundary - 0.1
        assert _target_reached_blocks_urgency(data, target_pct=95.0) is False

    def test_no_target_context_preserves_legacy(self):
        # target_pct<=0 (legacy callers) -> latch alone suppresses, as before.
        data = MagicMock()
        data.target_reached_today = True
        data.soc = 12.0
        assert _target_reached_blocks_urgency(data, target_pct=0.0) is True


class TestComputeEffectiveCheapPriceStaleLatch:
    """End-to-end: a stale latch + low SOC before the DW still funds pre-charge."""

    def _data(self, now, soc, latched):
        data = MagicMock()
        data.general_forecast = [{"start_time": now.isoformat(), "per_kwh": 0.10}]
        data.forecast_horizon_hours = 12.0
        data.solar_can_reach_target = False  # solar_gap -> urgency eligible
        data.target_reached_today = latched
        data.soc = soc
        return data

    def test_stale_latch_low_soc_still_inflates_threshold(self):
        # Fixed time 2h before target so the urgency path yields a value above base
        # (urgency = 1 - 2/4 = 0.5 -> 0.10 + (0.50-0.10)*0.5 = 0.30).
        now = datetime(2026, 6, 4, 16, 0, tzinfo=timezone.utc)

        ref = _make_price_calculator()
        d_ref = self._data(now, soc=12.0, latched=False)
        ref.compute_effective_cheap_price(
            d_ref, now, before_dw=True, target_hour=18, target_pct=95.0
        )

        stuck = _make_price_calculator()
        d_stuck = self._data(now, soc=12.0, latched=True)
        stuck.compute_effective_cheap_price(
            d_stuck, now, before_dw=True, target_hour=18, target_pct=95.0
        )

        honored = _make_price_calculator()
        d_hon = self._data(now, soc=95.0, latched=True)
        honored.compute_effective_cheap_price(
            d_hon, now, before_dw=True, target_hour=18, target_pct=95.0
        )

        # Urgency path produces a threshold strictly above base (test is meaningful).
        assert d_ref.effective_cheap_price > 0.10
        # Stuck latch + low SOC behaves like no latch at all -> pre-charge funded.
        assert d_stuck.effective_cheap_price == d_ref.effective_cheap_price
        # Latch honored only when SOC is within the deadband -> base, no pre-charge.
        assert d_hon.effective_cheap_price == 0.10


class TestGetFitPriceForPeriod:
    """Tests for _get_fit_price_for_period method."""

    @pytest.fixture
    def calculator(self):
        """Create a PriceCalculator instance."""
        from datetime import datetime

        def parse_dt(val):
            if val is None:
                return None
            try:
                return datetime.fromisoformat(val.replace("Z", "+00:00"))
            except Exception:
                return None

        entry = MagicMock()
        entry.options = {}
        return PriceCalculator(
            entry=entry,
            parse_forecast_dt=parse_dt,
            percentile_func=lambda x, y: 0.0,
            sum_solar_before_target=lambda x, y, z: 0.0,
            get_expected_load_kw=lambda x, y: 0.0,
        )

    def test_returns_price_when_in_period(self, calculator):
        """Test returns FIT price when time is within forecast period."""
        now = _make_utc_datetime()
        feed_in_forecast = [
            {
                "start_time": now.isoformat(),
                "end_time": (now + timedelta(minutes=30)).isoformat(),
                "per_kwh": 0.12,
            }
        ]
        mid_local = now + timedelta(minutes=10)

        result = calculator._get_fit_price_for_period(feed_in_forecast, mid_local)
        assert result == 0.12

    def test_returns_zero_when_no_match(self, calculator):
        """Test returns zero when no forecast period matches."""
        now = _make_utc_datetime()
        feed_in_forecast = [
            {
                "start_time": (now + timedelta(hours=1)).isoformat(),
                "end_time": (now + timedelta(hours=2)).isoformat(),
                "per_kwh": 0.12,
            }
        ]
        mid_local = now + timedelta(minutes=10)

        result = calculator._get_fit_price_for_period(feed_in_forecast, mid_local)
        assert result == 0.0

    def test_skips_invalid_entries(self, calculator):
        """Test skips entries with invalid start/end times."""
        now = _make_utc_datetime()
        feed_in_forecast = [
            {"start_time": None, "end_time": None, "per_kwh": 0.12},
        ]
        mid_local = now + timedelta(minutes=10)

        result = calculator._get_fit_price_for_period(feed_in_forecast, mid_local)
        assert result == 0.0


class TestComputeWeightedFit:
    """Tests for _compute_weighted_fit method."""

    @pytest.fixture
    def calculator(self):
        """Create a PriceCalculator instance."""
        from datetime import datetime

        def parse_dt(val):
            if val is None:
                return None
            try:
                return datetime.fromisoformat(val.replace("Z", "+00:00"))
            except Exception:
                return None

        entry = MagicMock()
        entry.options = {}
        return PriceCalculator(
            entry=entry,
            parse_forecast_dt=parse_dt,
            percentile_func=lambda x, y: 0.0,
            sum_solar_before_target=lambda x, y, z: 0.0,
            get_expected_load_kw=lambda x, y: 0.0,
        )

    def test_computes_weighted_sum(self, calculator):
        """Test computes weighted sum correctly."""
        now = _make_utc_datetime()
        all_solcast = [
            {
                "period_start": now.isoformat(),
                "pv_estimate": 2.0,
            }
        ]
        feed_in_forecast = [
            {
                "start_time": now.isoformat(),
                "end_time": (now + timedelta(minutes=30)).isoformat(),
                "per_kwh": 0.10,
            }
        ]
        target_dt = now + timedelta(hours=6)

        weighted_sum, total_solar = calculator._compute_weighted_fit(
            all_solcast, feed_in_forecast, now, target_dt
        )
        assert total_solar == 2.0
        assert weighted_sum == 0.2

    def test_skips_zero_solar(self, calculator):
        """Test skips periods with zero solar."""
        now = _make_utc_datetime()
        all_solcast = [
            {
                "period_start": now.isoformat(),
                "pv_estimate": 0.0,
            }
        ]
        feed_in_forecast = []
        target_dt = now + timedelta(hours=6)

        weighted_sum, total_solar = calculator._compute_weighted_fit(
            all_solcast, feed_in_forecast, now, target_dt
        )
        assert total_solar == 0.0

    def test_skips_invalid_period_start(self, calculator):
        """Test skips periods with invalid period_start."""
        now = _make_utc_datetime()
        all_solcast = [
            {"period_start": None, "pv_estimate": 2.0},
        ]
        feed_in_forecast = []
        target_dt = now + timedelta(hours=6)

        weighted_sum, total_solar = calculator._compute_weighted_fit(
            all_solcast, feed_in_forecast, now, target_dt
        )
        assert total_solar == 0.0
