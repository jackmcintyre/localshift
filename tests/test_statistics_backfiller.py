"""Tests for StatisticsBackfiller module.

Issue #267: Ground-truth validation of decision outcomes.
"""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.localshift.computation_engine_lib.statistics_backfiller import (
    BackfillReport,
    StatisticsBackfiller,
)


class TestBackfillReport:
    """Tests for BackfillReport dataclass."""

    def test_default_values(self):
        """Test default values are set correctly."""
        report = BackfillReport()
        assert report.decisions_validated == 0
        assert report.discrepancies_found == 0
        assert report.total_import_validated_kwh == 0.0
        assert report.total_export_validated_kwh == 0.0
        assert report.total_charge_validated_kwh == 0.0
        assert report.total_discharge_validated_kwh == 0.0
        assert report.avg_import_variance_pct == 0.0
        assert report.avg_export_variance_pct == 0.0
        assert report.avg_charge_variance_pct == 0.0
        assert report.avg_discharge_variance_pct == 0.0
        assert report.last_run is None
        assert report.period_start is None
        assert report.period_end is None
        assert report.errors == []
        assert report.comparisons == []

    def test_to_dict(self):
        """Test serialization to dictionary."""
        now = datetime(2026, 2, 26, 8, 0, 0)
        report = BackfillReport(
            decisions_validated=10,
            discrepancies_found=2,
            total_import_validated_kwh=15.5,
            last_run=now,
        )
        result = report.to_dict()

        assert result["decisions_validated"] == 10
        assert result["discrepancies_found"] == 2
        assert result["total_import_validated_kwh"] == 15.5
        assert result["last_run"] == "2026-02-26T08:00:00"
        assert result["errors"] == []

    def test_from_dict(self):
        """Test deserialization from dictionary."""
        data = {
            "decisions_validated": 15,
            "discrepancies_found": 3,
            "total_import_validated_kwh": 20.5,
            "last_run": "2026-02-26T08:00:00",
            "period_start": "2026-02-19T08:00:00",
            "period_end": "2026-02-26T08:00:00",
            "errors": ["test error"],
        }
        report = BackfillReport.from_dict(data)

        assert report.decisions_validated == 15
        assert report.discrepancies_found == 3
        assert report.total_import_validated_kwh == 20.5
        assert report.last_run == datetime(2026, 2, 26, 8, 0, 0)
        assert report.period_start == datetime(2026, 2, 19, 8, 0, 0)
        assert report.period_end == datetime(2026, 2, 26, 8, 0, 0)
        assert report.errors == ["test error"]

    def test_from_dict_invalid_datetime(self):
        """Test deserialization handles invalid datetime strings."""
        data = {
            "last_run": "invalid-datetime",
            "period_start": None,
        }
        report = BackfillReport.from_dict(data)

        assert report.last_run is None
        assert report.period_start is None


class TestStatisticsBackfiller:
    """Tests for StatisticsBackfiller class."""

    @pytest.fixture
    def hass(self):
        """Create a mock Home Assistant instance."""
        hass = MagicMock()
        hass.states = MagicMock()
        return hass

    @pytest.fixture
    def config(self):
        """Create a test configuration."""
        return {
            "grid_import_entity": "sensor.grid_import",
            "grid_export_entity": "sensor.grid_export",
            "battery_charge_entity": "sensor.battery_charge",
            "battery_discharge_entity": "sensor.battery_discharge",
        }

    @pytest.fixture
    def backfiller(self, hass, config):
        """Create a StatisticsBackfiller instance."""
        return StatisticsBackfiller(hass, config)

    def test_init(self, hass, config):
        """Test initialization."""
        backfiller = StatisticsBackfiller(hass, config)

        assert backfiller._hass == hass
        assert backfiller._config == config
        assert backfiller._last_report is None

    def test_last_report_property(self, backfiller):
        """Test last_report property."""
        assert backfiller.last_report is None

        report = BackfillReport(decisions_validated=5)
        backfiller._last_report = report
        assert backfiller.last_report == report

    @pytest.mark.asyncio
    async def test_backfill_no_entities_configured(self, hass):
        """Test backfill when no entities are configured."""
        backfiller = StatisticsBackfiller(hass, {})
        decisions = [{"timestamp": datetime.now().isoformat()}]

        report = await backfiller.async_backfill_decision_outcomes(decisions)

        assert "No grid import/export entities configured" in report.errors
        assert report.decisions_validated == 0

    @pytest.mark.asyncio
    async def test_backfill_no_decisions_in_period(self, backfiller):
        """Test backfill when no decisions are in the period."""
        # Decision from 30 days ago (outside default 7-day window)
        old_timestamp = (datetime.now() - timedelta(days=30)).isoformat()
        decisions = [{"timestamp": old_timestamp}]

        report = await backfiller.async_backfill_decision_outcomes(decisions)

        assert report.decisions_validated == 0
        assert report.discrepancies_found == 0

    @pytest.mark.asyncio
    async def test_backfill_with_statistics(self, backfiller, hass):
        """Test backfill with successful statistics fetch."""
        now = datetime.now()
        decisions = [
            {
                "timestamp": now.isoformat(),
                "estimated_import_kwh": 10.0,
                "estimated_export_kwh": 5.0,
            }
        ]

        # Mock the statistics fetch
        with patch.object(
            backfiller,
            "_fetch_statistics",
            new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.return_value = [{"sum": 10000}]  # 10000 Wh = 10 kWh

            report = await backfiller.async_backfill_decision_outcomes(decisions)

            assert report.decisions_validated == 1
            # Statistics were fetched for import and export
            assert mock_fetch.call_count >= 2

    def test_filter_decisions_by_period(self, backfiller):
        """Test filtering decisions by time period."""
        now = datetime.now()
        decisions = [
            {"timestamp": (now - timedelta(days=1)).isoformat(), "id": 1},
            {"timestamp": (now - timedelta(days=10)).isoformat(), "id": 2},
            {"timestamp": (now - timedelta(days=3)).isoformat(), "id": 3},
            {"timestamp": "invalid", "id": 4},  # Invalid timestamp
        ]

        start = now - timedelta(days=7)
        end = now

        filtered = backfiller._filter_decisions_by_period(decisions, start, end)

        # Should include decisions from 1 and 3 days ago, but not 10 days ago
        assert len(filtered) == 2
        assert filtered[0]["id"] == 1
        assert filtered[1]["id"] == 3

    def test_sum_statistics(self, backfiller):
        """Test summing statistics values."""
        stats = [
            {"sum": 5000},  # 5000 Wh
            {"sum": 3000},  # 3000 Wh
            {"sum": 2000},  # 2000 Wh
        ]

        total = backfiller._sum_statistics(stats, datetime.now(), datetime.now())

        # Total should be converted from Wh to kWh: 10000 Wh = 10 kWh
        assert total == 10.0

    def test_sum_statistics_small_values(self, backfiller):
        """Test summing statistics with small values (no Wh to kWh conversion)."""
        stats = [
            {"sum": 5},
            {"sum": 3},
            {"sum": 2},
        ]

        total = backfiller._sum_statistics(stats, datetime.now(), datetime.now())

        # Small values should not be converted
        assert total == 10.0

    def test_validate_decisions(self, backfiller):
        """Test validating decisions against actuals."""
        decisions = [
            {
                "timestamp": datetime.now().isoformat(),
                "estimated_import_kwh": 10.0,
                "estimated_export_kwh": 5.0,
            },
            {
                "timestamp": datetime.now().isoformat(),
                "estimated_import_kwh": 8.0,
                "estimated_export_kwh": 3.0,
            },
        ]

        comparisons = backfiller._validate_decisions(
            decisions,
            actual_import_kwh=18.0,  # Matches estimates
            actual_export_kwh=8.0,   # Matches estimates
        )

        assert len(comparisons) == 1  # Single comparison record
        assert comparisons[0]["decisions_count"] == 2
        assert comparisons[0]["estimated_import_kwh"] == 18.0
        assert comparisons[0]["actual_import_kwh"] == 18.0

    def test_validate_decisions_with_variance(self, backfiller):
        """Test validating decisions with variance."""
        decisions = [
            {
                "timestamp": datetime.now().isoformat(),
                "estimated_import_kwh": 10.0,
                "estimated_export_kwh": 5.0,
            },
        ]

        comparisons = backfiller._validate_decisions(
            decisions,
            actual_import_kwh=12.0,  # 20% higher than estimate
            actual_export_kwh=4.0,   # 20% lower than estimate
        )

        assert comparisons[0]["import_variance_pct"] == pytest.approx(-16.67, rel=0.1)
        assert comparisons[0]["export_variance_pct"] == pytest.approx(25.0, rel=0.1)

    @pytest.mark.asyncio
    async def test_check_statistics_support_measurement(self, backfiller, hass):
        """Test checking if entity supports statistics (measurement)."""
        state = MagicMock()
        state.attributes = {"state_class": "measurement"}
        hass.states.get.return_value = state

        result = await backfiller.async_check_statistics_support("sensor.test")

        assert result is True

    @pytest.mark.asyncio
    async def test_check_statistics_support_total(self, backfiller, hass):
        """Test checking if entity supports statistics (total)."""
        state = MagicMock()
        state.attributes = {"state_class": "total_increasing"}
        hass.states.get.return_value = state

        result = await backfiller.async_check_statistics_support("sensor.test")

        assert result is True

    @pytest.mark.asyncio
    async def test_check_statistics_support_no_state_class(self, backfiller, hass):
        """Test checking if entity supports statistics (no state_class)."""
        state = MagicMock()
        state.attributes = {}
        hass.states.get.return_value = state

        result = await backfiller.async_check_statistics_support("sensor.test")

        assert result is False

    @pytest.mark.asyncio
    async def test_check_statistics_support_entity_not_found(self, backfiller, hass):
        """Test checking statistics support when entity not found."""
        hass.states.get.return_value = None

        result = await backfiller.async_check_statistics_support("sensor.test")

        assert result is False

    @pytest.mark.asyncio
    async def test_fetch_statistics_api_unavailable(self, backfiller):
        """Test fetching statistics when API is unavailable."""
        with patch(
            "custom_components.localshift.computation_engine_lib.statistics_backfiller.statistics_during_period",
            None,
        ):
            result = await backfiller._fetch_statistics(
                "sensor.test",
                datetime.now() - timedelta(days=1),
                datetime.now(),
            )

            assert result == []