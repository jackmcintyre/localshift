"""Tests for Issue #956: sustained synthetic slot 0 must degrade status.

Covers the signal side of the feature (not the coordinator/sensor wiring,
which lives in tests/coordinator/test_entity_monitor_synthetic_degradation.py
and tests/sensors/test_status.py):

- slot_schedule.py still flags a gap-covering slot 0 as "synthetic" and its
  fallback WARNING now points at the configured forecast source, not sensor
  staleness.
- SlotBuilder.build_slots() surfaces that as SlotBuildMetadata.slot0_price_source.
- OptimizerFacade.run_inline() records exactly one sample per successful
  build into data.synthetic_slot_health, and the shadow-comparison build
  records none (a second sampling site would double-count evaluations and
  skew the rate against whichever comparison_mode is enabled).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from custom_components.localshift.coordinator.data import CoordinatorData
from custom_components.localshift.engine.optimizer_facade import OptimizerFacade
from custom_components.localshift.engine.slot_schedule import (
    compute_hybrid_slot_schedule,
)
from custom_components.localshift.engine.slots import SlotBuilder, SlotBuildMetadata

AEDT = timezone(timedelta(hours=11))


def _entry(start_time: str, duration: int, price: float) -> dict:
    return {"start_time": start_time, "duration": duration, "per_kwh": price}


class TestFallbackStillFlagsSynthetic:
    """Guards the signal the rate tracker reads: unchanged by the reword."""

    def test_gap_produces_synthetic_price_source(self):
        entries = [_entry("2026-03-16T12:45:01+11:00", 5, 0.30)]
        now = datetime(2026, 3, 16, 12, 33, 0, tzinfo=AEDT)

        slots, _metadata = compute_hybrid_slot_schedule(
            now, entries, "Australia/Sydney"
        )

        assert slots[0]["price_source"] == "synthetic"


class TestFallbackWarningWording:
    """Issue #956: the fallback message points at the configured source."""

    def test_warning_names_configured_forecast_source(self, caplog):
        entries = [_entry("2026-03-16T12:45:01+11:00", 5, 0.30)]
        now = datetime(2026, 3, 16, 12, 33, 0, tzinfo=AEDT)

        with caplog.at_level(logging.WARNING):
            compute_hybrid_slot_schedule(now, entries, "Australia/Sydney")

        messages = [r.message for r in caplog.records if "SYNTHETIC" in r.message]
        assert messages, "expected a SYNTHETIC SLOT FALLBACK warning"
        assert any("pricing_general_forecast" in m for m in messages)

    def test_warning_no_longer_blames_sensor_staleness(self, caplog):
        entries = [_entry("2026-03-16T12:45:01+11:00", 5, 0.30)]
        now = datetime(2026, 3, 16, 12, 33, 0, tzinfo=AEDT)

        with caplog.at_level(logging.WARNING):
            compute_hybrid_slot_schedule(now, entries, "Australia/Sydney")

        messages = [r.message for r in caplog.records if "SYNTHETIC" in r.message]
        assert messages
        assert not any("check the price sensor for staleness" in m for m in messages)


class TestSlotBuildMetadataSlot0PriceSource:
    """SlotBuilder.build_slots() surfaces slot 0's price_source in metadata."""

    @pytest.fixture
    def builder(self):
        return SlotBuilder(
            config_options={
                "demand_window_start": "18:00:00",
                "demand_window_end": "22:00:00",
            },
            ha_timezone="UTC",
        )

    def _base_data(self, now: datetime) -> MagicMock:
        data = MagicMock()
        data.feed_in_forecast = [
            {
                "start_time": (now + timedelta(minutes=30 * i)).isoformat(),
                "per_kwh": 0.05,
                "duration": 30,
            }
            for i in range(4)
        ]
        data.solcast_today = []
        data.solcast_tomorrow = []
        data.load_forecast_slots = [0.5] * 96
        data.solcast_analysis_today = None
        data.solcast_analysis_tomorrow = None
        return data

    def test_slot0_price_source_is_forecast_current_when_covered(self, builder):
        now = datetime.now(UTC)
        data = self._base_data(now)
        # First entry covers "now": no synthetic fallback needed.
        data.general_forecast = [
            {
                "start_time": (now + timedelta(minutes=30 * i)).isoformat(),
                "per_kwh": 0.10,
                "duration": 30,
            }
            for i in range(4)
        ]

        _slots, metadata = builder.build_slots(data)

        assert metadata.slot0_price_source == "forecast_current"

    def test_slot0_price_source_is_synthetic_on_gap(self, builder):
        now = datetime.now(UTC)
        data = self._base_data(now)
        # Every entry starts well after "now": nothing covers it.
        data.general_forecast = [
            {
                "start_time": (now + timedelta(hours=2, minutes=30 * i)).isoformat(),
                "per_kwh": 0.10,
                "duration": 30,
            }
            for i in range(4)
        ]

        _slots, metadata = builder.build_slots(data)

        assert metadata.slot0_price_source == "synthetic"

    def test_slot0_price_source_defaults_unknown_when_no_slots(self):
        metadata = SlotBuildMetadata(
            total_slots=0,
            five_min_slots=0,
            thirty_min_slots=0,
            horizon_hours=0.0,
            slots_with_defaulted_solar=0,
            slots_with_defaulted_price=0,
            slots_with_defaulted_consumption=0,
        )
        assert metadata.slot0_price_source == "unknown"


class _StubSlotBuilderWithMetadata:
    """Minimal build_slots stub carrying a real SlotBuildMetadata."""

    def __init__(self, slot0_price_source: str, **_kwargs) -> None:
        self._slot0_price_source = slot0_price_source

    def build_slots(self, _data, now_dt=None, **_kwargs):
        metadata = SlotBuildMetadata(
            total_slots=1,
            five_min_slots=1,
            thirty_min_slots=0,
            horizon_hours=1.0,
            slots_with_defaulted_solar=0,
            slots_with_defaulted_price=0,
            slots_with_defaulted_consumption=0,
            slot0_price_source=self._slot0_price_source,
        )
        slot = MagicMock()
        slot.solar_kwh = 1.0
        slot.timestamp_iso = "2026-01-15T10:00:00+00:00"
        return [slot], metadata


def _make_stub_builder_cls(slot0_price_source: str):
    def factory(**kwargs):
        return _StubSlotBuilderWithMetadata(slot0_price_source, **kwargs)

    return factory


def _minimal_mock_result() -> MagicMock:
    """A planner result carrying just the fields run_inline's writeback needs."""
    result = MagicMock()
    result.success = True
    result.decisions = []
    result.projected_import_kwh = 0.0
    result.projected_export_kwh = 0.0
    result.projected_net_cost = 0.0
    result.terminal_shortfall_pct = 0.0
    result.can_solar_reach_target = False
    result.can_solar_reach_target_in_dw = False
    result.reason_code_histogram = {}
    result.planner_version = "test"
    result.total_slots = 1
    result.states_explored = 0
    result.forecast_accuracy = 1.0
    result.accuracy_discount_factor = 1.0
    result.peak_soc_pct = 50.0
    result.dw_entry_soc_pct = 50.0
    return result


class TestRunInlineRecordsSyntheticSample:
    """OptimizerFacade.run_inline feeds data.synthetic_slot_health exactly once."""

    def test_records_one_synthetic_sample(self):
        data = CoordinatorData()
        data.soc = 50.0
        facade = OptimizerFacade(
            slot_builder_cls=_make_stub_builder_cls("synthetic")
        )

        with patch(
            "custom_components.localshift.engine.optimizer_facade.DPPlanner"
        ) as MockPlanner:
            MockPlanner.return_value.plan.return_value = _minimal_mock_result()
            facade.run_inline(
                data=data,
                now_dt=datetime(2026, 2, 16, 10, 0, tzinfo=UTC),
                config_options={},
            )

        assert data.synthetic_slot_health.sample_count == 1
        assert data.synthetic_slot_health.rate == 1.0

    def test_records_one_non_synthetic_sample(self):
        data = CoordinatorData()
        data.soc = 50.0
        facade = OptimizerFacade(
            slot_builder_cls=_make_stub_builder_cls("forecast_current")
        )

        with patch(
            "custom_components.localshift.engine.optimizer_facade.DPPlanner"
        ) as MockPlanner:
            MockPlanner.return_value.plan.return_value = _minimal_mock_result()
            facade.run_inline(
                data=data,
                now_dt=datetime(2026, 2, 16, 10, 0, tzinfo=UTC),
                config_options={},
            )

        assert data.synthetic_slot_health.sample_count == 1
        assert data.synthetic_slot_health.rate == 0.0

    def test_two_calls_record_exactly_two_samples(self):
        data = CoordinatorData()
        data.soc = 50.0
        facade = OptimizerFacade(
            slot_builder_cls=_make_stub_builder_cls("synthetic")
        )

        with patch(
            "custom_components.localshift.engine.optimizer_facade.DPPlanner"
        ) as MockPlanner:
            MockPlanner.return_value.plan.return_value = _minimal_mock_result()
            for _ in range(2):
                facade.run_inline(
                    data=data,
                    now_dt=datetime(2026, 2, 16, 10, 0, tzinfo=UTC),
                    config_options={},
                )

        assert data.synthetic_slot_health.sample_count == 2

    def test_empty_slots_records_a_non_covering_sample(self):
        """The no-slots branch is the total-outage case Issue #956 must catch:

        a configured forecast source that yields nothing covering "now" at all
        means ``_ensure_current_slot_coverage`` never even ran, so slot 0 was
        never priced synthetic OR real. That must still count as a
        non-covering sample -- otherwise a total outage never trips the rate,
        never satisfies ``needs_startup_warning``, and never degrades
        ``integration_status`` (the exact gap the code review caught: an
        empty/None ``general_forecast`` produced zero slots and the tracker
        never heard about it).
        """

        class _EmptySlotBuilder:
            def __init__(self, **_kwargs) -> None:
                pass

            def build_slots(self, _data, now_dt=None, **_kwargs):
                return [], None

        data = CoordinatorData()
        facade = OptimizerFacade(slot_builder_cls=_EmptySlotBuilder)

        facade.run_inline(
            data=data,
            now_dt=datetime(2026, 2, 16, 10, 0, tzinfo=UTC),
            config_options={},
        )

        assert data.synthetic_slot_health.sample_count == 1
        assert data.synthetic_slot_health.rate == 1.0
        assert data.synthetic_slot_health.covering_slot_seen is False

    def test_repeated_empty_slots_trips_startup_warning_and_degrades(self):
        """A total, sustained forecast-source outage must reach both signals."""

        class _EmptySlotBuilder:
            def __init__(self, **_kwargs) -> None:
                pass

            def build_slots(self, _data, now_dt=None, **_kwargs):
                return [], None

        data = CoordinatorData()
        facade = OptimizerFacade(slot_builder_cls=_EmptySlotBuilder)

        # MIN_SAMPLES_FOR_RATE (6) samples before the rate is trusted, then
        # CONSECUTIVE_TO_DEGRADE (3) more evaluations at that rate to flip
        # `degraded` -- 8 calls covers both.
        for i in range(8):
            facade.run_inline(
                data=data,
                now_dt=datetime(2026, 2, 16, 10, i, tzinfo=UTC),
                config_options={},
            )

        assert data.synthetic_slot_health.sample_count == 8
        assert data.synthetic_slot_health.needs_startup_warning is True
        assert data.synthetic_slot_health.degraded is True

    def test_shadow_comparison_build_does_not_record(self):
        """Only the primary build feeds the tracker, never the shadow build."""
        data = CoordinatorData()
        data.soc = 50.0
        data.general_price_shadow = 0.30  # enables the shadow path
        facade = OptimizerFacade(
            slot_builder_cls=_make_stub_builder_cls("forecast_current")
        )

        with patch(
            "custom_components.localshift.engine.optimizer_facade.DPPlanner"
        ) as MockPlanner:
            MockPlanner.return_value.plan.return_value = _minimal_mock_result()
            facade.run_inline(
                data=data,
                now_dt=datetime(2026, 2, 16, 10, 0, tzinfo=UTC),
                config_options={"comparison_mode": "enabled"},
            )

        # One sample from the primary build only -- the shadow build (also
        # using the same stub class here) must not add a second.
        assert data.synthetic_slot_health.sample_count == 1
