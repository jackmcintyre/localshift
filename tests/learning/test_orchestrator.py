"""Tests for LearningOrchestrator."""

import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.localshift.coordinator.data import (
    CoordinatorData,
    PerformanceMetrics,
)


def _make_orchestrator():
    from custom_components.localshift.learning.orchestrator import LearningOrchestrator

    hass = MagicMock()
    hass.async_create_task = MagicMock()
    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.options = {}
    return LearningOrchestrator(hass, entry, get_switch_state=lambda k: False)


class TestOrchestratorChargeRateLearning:
    def test_schedule_charge_rate_update_skips_recent(self, monkeypatch):
        orchestrator = _make_orchestrator()
        orchestrator.decision_tracker = MagicMock()
        orchestrator.charge_rate_learner = MagicMock()
        now = datetime(2024, 1, 1)
        monkeypatch.setattr(
            "custom_components.localshift.learning.orchestrator.dt_util.now",
            lambda: now,
        )
        orchestrator._last_charge_rate_update = now - timedelta(hours=12)

        data = CoordinatorData()
        orchestrator._schedule_charge_rate_update(data)

        orchestrator.hass.async_create_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_orchestrator_updates_charge_rate_curves(self):
        from custom_components.localshift.learning.orchestrator import (
            LearningOrchestrator,
        )

        hass = MagicMock()
        scheduled: list = []

        def _capture_task(coro, _name=None):
            scheduled.append(coro)
            return MagicMock()

        hass.async_create_task = _capture_task

        entry = MagicMock()
        entry.entry_id = "test_entry"
        orchestrator = LearningOrchestrator(
            hass, entry, get_switch_state=lambda k: True
        )

        mock_tracker = MagicMock()
        mock_tracker.save_pending = False
        mock_tracker._completed_decisions = []
        mock_tracker.backfill_outcomes = MagicMock()
        mock_tracker.get_daily_summary.return_value = PerformanceMetrics()
        mock_tracker.get_decision_log.return_value = []
        mock_tracker.get_recent_decisions.return_value = [MagicMock()]
        orchestrator.decision_tracker = mock_tracker

        curve_normal = MagicMock()
        curve_boost = MagicMock()
        charge_rate_learner = MagicMock()
        history_point = (datetime.now(), 1.0)
        charge_rate_learner.async_fetch_history = AsyncMock(
            return_value=([history_point], [history_point])
        )
        charge_rate_learner.update_from_history.return_value = True
        charge_rate_learner.get_curve.side_effect = lambda regime: (
            curve_normal if regime == "normal" else curve_boost
        )
        charge_rate_learner.diagnostics = {"labeled_sample_ratio": 1.0}
        charge_rate_learner.async_save = AsyncMock()
        orchestrator.charge_rate_learner = charge_rate_learner

        data = CoordinatorData()
        data.performance_metrics = PerformanceMetrics()

        orchestrator.update_medium_tick(data)

        assert scheduled
        await scheduled[0]

        for task in scheduled[1:]:
            if asyncio.iscoroutine(task):
                await task

        assert data.learning_enabled is True
        assert data.charge_rate_curves["normal"] is curve_normal
        assert data.charge_rate_curves["boost"] is curve_boost

    @pytest.mark.asyncio
    async def test_async_update_charge_rate_skips_without_learner(self):
        orchestrator = _make_orchestrator()
        orchestrator.decision_tracker = MagicMock()
        orchestrator.charge_rate_learner = None

        data = CoordinatorData()
        await orchestrator._async_update_charge_rate(data)

    @pytest.mark.asyncio
    async def test_async_update_charge_rate_skips_missing_history(self):
        orchestrator = _make_orchestrator()
        orchestrator.decision_tracker = MagicMock()
        learner = MagicMock()
        learner.async_fetch_history = AsyncMock(return_value=([], []))
        orchestrator.charge_rate_learner = learner

        data = CoordinatorData()
        await orchestrator._async_update_charge_rate(data)

        assert orchestrator._last_charge_rate_update is None

    @pytest.mark.asyncio
    async def test_async_update_charge_rate_skips_when_no_curves(self):
        orchestrator = _make_orchestrator()
        orchestrator.decision_tracker = MagicMock()
        learner = MagicMock()
        history_point = (datetime.now(), 1.0)
        learner.async_fetch_history = AsyncMock(
            return_value=([history_point], [history_point])
        )
        learner.update_from_history.return_value = True
        learner.get_curve.return_value = None
        orchestrator.charge_rate_learner = learner

        data = CoordinatorData()
        await orchestrator._async_update_charge_rate(data)

        assert orchestrator._last_charge_rate_update is None
        assert getattr(data, "charge_rate_curves", None) is None
