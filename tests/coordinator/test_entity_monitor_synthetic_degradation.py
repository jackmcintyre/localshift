"""Tests for Issue #956: EntityMonitor escalates a sustained synthetic slot-0
rate into integration_status, and fires a one-shot startup WARNING when the
configured forecast source never produces a covering entry.

Deliberately a separate file from tests/coordinator/test_entity_monitor.py
(already ~24K) rather than adding to it.

Review fix: every test in this file now wires a validator-shaped MagicMock
onto ``_entity_validator`` (mirroring production -- ``coordinator.async_start``
unconditionally constructs an ``EntityValidator`` before ``EntityMonitor`` is
created) rather than stubbing the validator away with ``None``. That means
``check_entity_health()`` runs BOTH halves of the real composition every
time: ``_check_validator_health`` writes ``integration_status`` fresh from
the validator first, then the synthetic escalation layers on top of that --
exactly as it does live. The one place ``_entity_validator = None`` remains
is ``TestSyntheticPathRunsWithoutValidator``, which exists specifically to
cover that configuration.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from unittest.mock import MagicMock

from custom_components.localshift.coordinator.entity_monitor import EntityMonitor
from custom_components.localshift.coordinator.synthetic_slot_health import (
    CONSECUTIVE_TO_CLEAR,
    CONSECUTIVE_TO_DEGRADE,
    MIN_SAMPLES_FOR_RATE,
    STARTUP_CHECK_EVALUATIONS,
    SyntheticSlotHealth,
)

CONFIGURED_FORECAST_ENTITY = "sensor.amber_general_price"


def _degraded_tracker() -> SyntheticSlotHealth:
    """A tracker that is already past the degrade threshold."""
    health = SyntheticSlotHealth()
    t = 0
    for _i in range(CONSECUTIVE_TO_DEGRADE):
        health.samples = [
            (datetime(2026, 9, 6, 12, 0, 0, tzinfo=UTC), True)
        ] * (MIN_SAMPLES_FOR_RATE - 1)
        health.record(True, datetime(2026, 9, 6, 12, 0, t, tzinfo=UTC))
        t += 1
    assert health.degraded is True
    return health


def _mock_validator(status: str = "ok", message: str = "All systems operational"):
    """Build a validator-shaped MagicMock reproducing what a real
    ``EntityValidator`` returns on a clean cycle -- ``status`` is re-read
    from this mock's ``.value`` on every ``check_entity_health()`` call, so
    a test that flips the tracker's ``degraded`` flag and calls
    ``check_entity_health()`` again genuinely exercises the validator
    recomputing ``integration_status`` from scratch, the same as
    ``coordinator.py`` does live -- nothing in the test resets it by hand.
    """
    validator = MagicMock()
    validator.status.value = status
    validator.errors = []
    validator.warnings = []
    validator.get_user_friendly_message.return_value = message
    validator.get_required_entities_status.return_value = {}
    validator.get_health_summary.return_value = {"entities": {}, "last_check": ""}
    validator.check_all_localshift_entities.return_value = {}
    validator.check_orphaned_owned_entities.return_value = {}
    return validator


def _monitor_with_data(
    validator_status: str | None = "ok", **data_attrs
) -> tuple[EntityMonitor, MagicMock]:
    """Wire an EntityMonitor the way production wires one.

    ``validator_status="ok"`` (the default) or ``"error"`` installs a
    validator-shaped mock so ``_check_validator_health`` runs for real each
    call. Pass ``validator_status=None`` only to reproduce the no-validator
    configuration explicitly (see ``TestSyntheticPathRunsWithoutValidator``).
    """
    mock_coordinator = MagicMock()
    mock_coordinator._entity_validator = (
        None if validator_status is None else _mock_validator(status=validator_status)
    )
    mock_coordinator.entry.entry_id = "cfg_test"
    mock_coordinator.get_entity_id.return_value = CONFIGURED_FORECAST_ENTITY
    mock_data = MagicMock()
    mock_data.entity_warnings = []
    mock_data.integration_status = "ok"
    for key, value in data_attrs.items():
        setattr(mock_data, key, value)
    mock_coordinator.data = mock_data
    monitor = EntityMonitor(mock_coordinator)
    return monitor, mock_coordinator


class TestDegradeEscalation:
    def test_degraded_tracker_moves_ok_status_to_degraded(self):
        monitor, coordinator = _monitor_with_data(
            synthetic_slot_health=_degraded_tracker()
        )

        monitor.check_entity_health()

        assert coordinator.data.integration_status == "degraded"

    def test_degraded_tracker_appends_one_warning(self):
        monitor, coordinator = _monitor_with_data(
            synthetic_slot_health=_degraded_tracker()
        )

        monitor.check_entity_health()

        assert len(coordinator.data.entity_warnings) == 1

    def test_warning_names_the_rate(self):
        health = _degraded_tracker()
        monitor, coordinator = _monitor_with_data(synthetic_slot_health=health)

        monitor.check_entity_health()

        warning = coordinator.data.entity_warnings[0]
        assert f"{round(health.rate * 100)}%" in warning
        assert str(health.sample_count) in warning

    def test_warning_names_configured_forecast_entity(self):
        monitor, coordinator = _monitor_with_data(
            synthetic_slot_health=_degraded_tracker()
        )

        monitor.check_entity_health()

        # get_entity_id (not get_option) is the accessor: pricing entity
        # mappings live in entry.data, never entry.options.
        coordinator.get_entity_id.assert_any_call("pricing_general_forecast")
        warning = coordinator.data.entity_warnings[0]
        assert CONFIGURED_FORECAST_ENTITY in warning

    def test_error_status_is_never_downgraded_to_degraded(self):
        monitor, coordinator = _monitor_with_data(
            validator_status="error",
            synthetic_slot_health=_degraded_tracker(),
        )

        monitor.check_entity_health()

        assert coordinator.data.integration_status == "error"

    def test_error_status_still_gets_the_warning_appended(self):
        monitor, coordinator = _monitor_with_data(
            validator_status="error",
            synthetic_slot_health=_degraded_tracker(),
        )

        monitor.check_entity_health()

        assert len(coordinator.data.entity_warnings) == 1


class TestNotDegradedLeavesStatusAlone:
    def test_fresh_tracker_does_not_touch_status_or_warnings(self):
        monitor, coordinator = _monitor_with_data(
            synthetic_slot_health=SyntheticSlotHealth()
        )

        monitor.check_entity_health()

        assert coordinator.data.integration_status == "ok"
        assert coordinator.data.entity_warnings == []

    def test_clear_transition_removes_the_warning_on_a_later_call(self):
        """End-to-end: degrade, then clear, observed across two calls.

        The validator mock returns "ok" on both calls -- exactly as a real
        EntityValidator recomputing status from scratch every cycle would --
        so nothing in this test resets integration_status or entity_warnings
        by hand. If ``_check_validator_health`` ever regressed to an early
        return (or the validator itself short-circuited), the leftover
        "degraded" from the first call would still be sitting in
        ``integration_status`` on the second assert, and this test would
        fail.
        """
        health = _degraded_tracker()
        monitor, coordinator = _monitor_with_data(synthetic_slot_health=health)

        monitor.check_entity_health()
        assert coordinator.data.integration_status == "degraded"

        # Flush the window and record enough clean samples to clear.
        health.samples = []
        for i in range(CONSECUTIVE_TO_CLEAR):
            health.record(False, datetime(2026, 9, 6, 13, 0, i, tzinfo=UTC))
        assert health.degraded is False

        monitor.check_entity_health()

        assert coordinator.data.integration_status == "ok"
        assert coordinator.data.entity_warnings == []


class TestSyntheticPathRunsWithoutValidator:
    """Regression guard for the check_entity_health() restructure."""

    def test_synthetic_path_runs_when_validator_is_none(self):
        monitor, coordinator = _monitor_with_data(
            validator_status=None, synthetic_slot_health=_degraded_tracker()
        )
        assert coordinator._entity_validator is None

        monitor.check_entity_health()

        assert coordinator.data.integration_status == "degraded"

    def test_no_crash_when_synthetic_slot_health_is_a_plain_mock(self):
        """A coordinator.data built from a bare MagicMock (as in the existing
        TestCheckEntityHealth suite) must not crash or leak Mock truthiness
        into integration_status. ``synthetic_slot_health`` is left unset so
        ``mock_data.synthetic_slot_health`` is itself an auto-generated
        MagicMock -- the isinstance(SyntheticSlotHealth) guard must reject it."""
        monitor, coordinator = _monitor_with_data(validator_status=None)

        monitor.check_entity_health()

        assert coordinator.data.integration_status == "ok"
        assert coordinator.data.entity_warnings == []


class TestStartupWarning:
    def _tracker_needing_startup_warning(self) -> SyntheticSlotHealth:
        health = SyntheticSlotHealth()
        for i in range(STARTUP_CHECK_EVALUATIONS):
            health.record(True, datetime(2026, 9, 6, 12, 0, i, tzinfo=UTC))
        assert health.needs_startup_warning is True
        return health

    def test_logs_exactly_once_across_two_calls(self, caplog):
        health = self._tracker_needing_startup_warning()
        monitor, coordinator = _monitor_with_data(synthetic_slot_health=health)

        with caplog.at_level(logging.WARNING):
            monitor.check_entity_health()
            monitor.check_entity_health()

        startup_warnings = [
            r for r in caplog.records if "no entry from the configured" in r.message
        ]
        assert len(startup_warnings) == 1

    def test_warning_names_the_configured_entity(self, caplog):
        health = self._tracker_needing_startup_warning()
        monitor, coordinator = _monitor_with_data(synthetic_slot_health=health)

        with caplog.at_level(logging.WARNING):
            monitor.check_entity_health()

        startup_warnings = [
            r for r in caplog.records if "no entry from the configured" in r.message
        ]
        assert len(startup_warnings) == 1
        assert CONFIGURED_FORECAST_ENTITY in startup_warnings[0].message

    def test_sets_startup_warning_logged_flag(self):
        health = self._tracker_needing_startup_warning()
        monitor, coordinator = _monitor_with_data(synthetic_slot_health=health)

        monitor.check_entity_health()

        assert health.startup_warning_logged is True

    def test_a_covering_slot_never_needs_the_warning(self, caplog):
        health = SyntheticSlotHealth()
        health.record(False, datetime(2026, 9, 6, 12, 0, 0, tzinfo=UTC))
        for i in range(1, STARTUP_CHECK_EVALUATIONS + 3):
            health.record(True, datetime(2026, 9, 6, 12, 0, i, tzinfo=UTC))
        monitor, coordinator = _monitor_with_data(synthetic_slot_health=health)

        with caplog.at_level(logging.WARNING):
            monitor.check_entity_health()

        startup_warnings = [
            r for r in caplog.records if "no entry from the configured" in r.message
        ]
        assert startup_warnings == []
