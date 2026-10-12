"""Regression coverage for issue #975: one recompute per entity write.

Before the fix, every write to a LocalShift number/switch/select entity ran
the full recompute-and-evaluate cycle twice: once from the platform's own
explicit call, and once more from the config-entry update listener that HA
schedules whenever `async_update_entry` actually changes the options. Each
run invalidates the decision fingerprint, so a single user write invalidated
it twice.

The fix makes the entry update listener (`_async_options_updated` in
`custom_components/localshift/__init__.py`) the SOLE recompute trigger and
removes the platform-side calls from number.py, switch.py, and the
`OptimizationModeSelect` half of select.py. `BatteryModeSelect`'s hand-back
to "automatic" was left double-firing by #975 and is covered here since issue
#1029: the listener owns the recompute whenever the options really change,
and the select recomputes itself only when they do not (no listener fires).

`FakeConfigEntries` below mirrors the three behaviours of HA's real
`ConfigEntries.async_update_entry` that matter here: a write that does not
actually change `entry.options` fires no listener at all; a write that does
change it starts every registered listener as an eager task, so the listener
body runs synchronously INSIDE `async_update_entry` up to its first real
suspension (HA: `_async_save_and_notify` -> `hass.async_create_task`, whose
`eager_start` defaults to True); and whatever is left of each listener
finishes later (`async_flush` awaits the tasks, so nothing is left as a stray
un-awaited coroutine). The eager start is what makes "set the coordinator
state before persisting" a real ordering requirement on the platforms rather
than a nicety, and what lets these tests pin it.
"""

from __future__ import annotations

import asyncio
from types import MappingProxyType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.localshift import (
    _async_options_updated,
    async_setup_entry,
)
from custom_components.localshift.const import (
    CONF_BATTERY_TARGET,
    DEFAULT_BATTERY_TARGET,
    SWITCH_AUTOMATION_ENABLED,
    SWITCH_DRY_RUN,
)
from custom_components.localshift.coordinator import LocalShiftCoordinator
from custom_components.localshift.number import LocalShiftNumber
from custom_components.localshift.select import (
    BatteryModeSelect,
    OptimizationModeSelect,
)
from custom_components.localshift.switch import LocalShiftSwitch


class FakeConfigEntries:
    """Mirrors `ConfigEntries.async_update_entry`'s change-detection and
    listener-dispatch semantics closely enough to exercise the real update
    listener wiring end to end."""

    def __init__(self, hass):
        self.hass = hass
        self._pending: list[asyncio.Task] = []
        self.dispatched = 0

    def async_update_entry(self, entry, *, options=None, **_kwargs) -> bool:
        """Set entry.options and start listeners — but only on a real change,
        matching HA: `if not changed: return False` before anything fires.

        Each listener is started as an eager task, as HA does: it runs right
        here, before this method returns, up to its first real suspension."""
        if options is None or dict(entry.options) == dict(options):
            return False
        entry.options = MappingProxyType(dict(options))
        loop = asyncio.get_running_loop()
        for listener in list(entry.update_listeners):
            self.dispatched += 1
            self._pending.append(
                asyncio.Task(listener(self.hass, entry), loop=loop, eager_start=True)
            )
        return True

    async def async_flush(self, entry) -> None:
        """Await every listener task started so far (as HA's task runner
        would), surfacing any exception a listener raised."""
        pending, self._pending = self._pending, []
        for task in pending:
            await task


@pytest.fixture
def entry():
    """A bare config entry with real update-listener bookkeeping (a plain
    list + append, not a MagicMock, so registering and firing listeners is
    the real thing rather than something mocked away)."""
    entry = MagicMock()
    entry.entry_id = "test_entry_975"
    entry.data = {}
    entry.options = {}
    entry.update_listeners = []
    entry.add_update_listener = entry.update_listeners.append
    return entry


@pytest.fixture
def real_coordinator(mock_hass_with_services, entry):
    """A real LocalShiftCoordinator with only its computation/state-machine
    leaves stubbed — `async_recompute_and_evaluate` itself runs for real, so
    `_compute_derived_values` is the counter the issue asks for and
    `_state_machine.invalidate_decision_fingerprint` proves the fingerprint
    effect the issue is about."""
    coord = LocalShiftCoordinator(mock_hass_with_services, entry)
    coord._state_machine = MagicMock()
    coord._compute_derived_values = MagicMock()
    coord.notify_listeners = MagicMock()
    coord.async_evaluate_state_machine = AsyncMock()
    entry.runtime_data = coord
    return coord


@pytest.fixture
def fake_hass(mock_hass_with_services, entry, real_coordinator):
    """hass wired with the fake config_entries and the production listener
    registered exactly as `async_setup_entry` registers it."""
    hass = mock_hass_with_services
    hass.config_entries = FakeConfigEntries(hass)
    entry.add_update_listener(_async_options_updated)
    return hass


class TestNumberWriteRecomputeOwnership:
    """number.py: async_set_native_value must not double-fire the recompute."""

    async def test_number_write_recomputes_exactly_once(
        self, fake_hass, entry, real_coordinator
    ):
        number = LocalShiftNumber(
            real_coordinator,
            entry,
            CONF_BATTERY_TARGET,
            "Battery Target",
            DEFAULT_BATTERY_TARGET,
        )
        number.hass = fake_hass
        number._attr_entity_id = "number.localshift_battery_target"

        with patch.object(number, "async_write_ha_state"):
            await number.async_set_native_value(90)
        await fake_hass.config_entries.async_flush(entry)

        assert real_coordinator._compute_derived_values.call_count == 1
        assert fake_hass.config_entries.dispatched == 1
        assert (
            real_coordinator._state_machine.invalidate_decision_fingerprint.call_count
            == 1
        )

    async def test_number_write_persists_before_the_listener_runs(
        self, fake_hass, entry, real_coordinator
    ):
        """Pins the ordering the new owner depends on: options must already
        hold the new value by the time the listener (the recompute trigger)
        runs, or the recompute would read the stale value."""
        seen = {}

        async def spy_listener(hass, entry):
            seen["value"] = entry.options.get(CONF_BATTERY_TARGET)
            await _async_options_updated(hass, entry)

        entry.update_listeners.clear()
        entry.add_update_listener(spy_listener)

        number = LocalShiftNumber(
            real_coordinator,
            entry,
            CONF_BATTERY_TARGET,
            "Battery Target",
            DEFAULT_BATTERY_TARGET,
        )
        number.hass = fake_hass
        number._attr_entity_id = "number.localshift_battery_target"

        with patch.object(number, "async_write_ha_state"):
            await number.async_set_native_value(90)
        await fake_hass.config_entries.async_flush(entry)

        assert seen["value"] == 90

    async def test_no_op_write_recomputes_zero_times(
        self, fake_hass, entry, real_coordinator
    ):
        """HA's documented behaviour: an unchanged entry fires no listener.
        Re-selecting the same value no longer forces a re-decision — the
        periodic tick still evaluates, and an actual value change (tested
        above) still recomputes once."""
        entry.options = {CONF_BATTERY_TARGET: 90}
        number = LocalShiftNumber(
            real_coordinator,
            entry,
            CONF_BATTERY_TARGET,
            "Battery Target",
            DEFAULT_BATTERY_TARGET,
        )
        number.hass = fake_hass
        number._attr_entity_id = "number.localshift_battery_target"

        with patch.object(number, "async_write_ha_state"):
            await number.async_set_native_value(90)
        await fake_hass.config_entries.async_flush(entry)

        assert real_coordinator._compute_derived_values.call_count == 0
        assert fake_hass.config_entries.dispatched == 0


class TestSwitchToggleRecomputeOwnership:
    """switch.py: async_turn_on/async_turn_off must not double-fire the
    recompute."""

    async def test_switch_toggle_recomputes_exactly_once(
        self, fake_hass, entry, real_coordinator
    ):
        entry.options = {f"switch_state_{SWITCH_DRY_RUN}": False}
        switch = LocalShiftSwitch(real_coordinator, entry, SWITCH_DRY_RUN)
        switch.hass = fake_hass
        switch._attr_entity_id = "switch.localshift_dry_run"

        with patch.object(switch, "async_write_ha_state"):
            await switch.async_turn_on()
        await fake_hass.config_entries.async_flush(entry)

        assert real_coordinator._compute_derived_values.call_count == 1
        assert fake_hass.config_entries.dispatched == 1
        assert (
            real_coordinator._state_machine.invalidate_decision_fingerprint.call_count
            == 1
        )

    async def test_switch_turn_off_automation_still_commands_self_consumption(
        self, fake_hass, entry, real_coordinator
    ):
        """The real safety argument for deferring the recompute: the switch
        sets `automation_enabled` False in the coordinator's switch-state
        bridge (synchronously, before persisting), so even though the
        recompute now lands via the listener (started inside
        `async_update_entry`) rather than from the switch's own call, the
        state the listener's evaluation sees is already correct — a stale
        interleave can't re-decide a mode using the old switch state."""
        entry.options = {f"switch_state_{SWITCH_AUTOMATION_ENABLED}": True}
        real_coordinator.async_set_self_consumption = AsyncMock()
        real_coordinator._notification_service = None

        switch = LocalShiftSwitch(real_coordinator, entry, SWITCH_AUTOMATION_ENABLED)
        switch.hass = fake_hass
        switch._attr_entity_id = "switch.localshift_automation_enabled"

        seen = {}

        async def spy_listener(hass, entry):
            seen["automation_enabled_during_listener"] = (
                real_coordinator.get_switch_state(SWITCH_AUTOMATION_ENABLED)
            )
            await _async_options_updated(hass, entry)

        entry.update_listeners.clear()
        entry.add_update_listener(spy_listener)

        with patch.object(switch, "async_write_ha_state"):
            await switch.async_turn_off()
        await fake_hass.config_entries.async_flush(entry)

        assert real_coordinator._compute_derived_values.call_count == 1
        real_coordinator.async_set_self_consumption.assert_awaited_once()
        assert seen["automation_enabled_during_listener"] is False


class TestOptimizationModeSelectRecomputeOwnership:
    """select.py: OptimizationModeSelect.async_select_option must not
    double-fire the recompute."""

    async def test_optimization_mode_select_recomputes_exactly_once(
        self, fake_hass, entry, real_coordinator
    ):
        entry.options = {}
        select = OptimizationModeSelect(real_coordinator, entry)
        select.hass = fake_hass
        select._attr_entity_id = "select.localshift_optimization_mode"

        with patch.object(select, "async_write_ha_state"):
            await select.async_select_option("arbitrage")
        await fake_hass.config_entries.async_flush(entry)

        assert real_coordinator._compute_derived_values.call_count == 1
        assert fake_hass.config_entries.dispatched == 1
        assert (
            real_coordinator._state_machine.invalidate_decision_fingerprint.call_count
            == 1
        )


class TestBatteryModeSelectHandBackRecomputeOwnership:
    """select.py: BatteryModeSelect's hand-back from a manual mode to
    "automatic" must run exactly one recompute-and-evaluate cycle (issue
    #1029) — it used to persist the option (listener recompute) AND await the
    coordinator explicitly (second recompute)."""

    AUTOMATION_OPTION = f"switch_state_{SWITCH_AUTOMATION_ENABLED}"

    def _make_select(self, fake_hass, entry, real_coordinator):
        select = BatteryModeSelect(real_coordinator, entry)
        select.hass = fake_hass
        select._attr_entity_id = "select.localshift_battery_mode"
        return select

    async def test_hand_back_from_manual_recomputes_exactly_once(
        self, fake_hass, entry, real_coordinator
    ):
        entry.options = {self.AUTOMATION_OPTION: False, "manual_battery_mode": "hold"}
        real_coordinator.set_switch_state(SWITCH_AUTOMATION_ENABLED, False)
        real_coordinator.set_manual_override(True, reason="test")
        select = self._make_select(fake_hass, entry, real_coordinator)

        with patch.object(select, "async_write_ha_state"):
            await select.async_select_option("automatic")
        await fake_hass.config_entries.async_flush(entry)

        assert entry.options[self.AUTOMATION_OPTION] is True
        assert fake_hass.config_entries.dispatched == 1
        assert real_coordinator._compute_derived_values.call_count == 1
        assert real_coordinator.async_evaluate_state_machine.await_count == 1
        assert (
            real_coordinator._state_machine.invalidate_decision_fingerprint.call_count
            == 1
        )

    async def test_hand_back_recompute_sees_automation_on_and_override_cleared(
        self, fake_hass, entry, real_coordinator
    ):
        """The one cycle now lands via the listener, which HA starts eagerly
        inside `async_update_entry`. It must evaluate against the handed-back
        state — automation ON, manual override cleared — never the manual
        state the user just left, so the select has to clear both BEFORE it
        persists. With no second recompute to paper over it (issue #1029),
        clearing after the persist would plan the hand-back against manual
        state until the next tick."""
        entry.options = {self.AUTOMATION_OPTION: False, "manual_battery_mode": "hold"}
        real_coordinator.set_switch_state(SWITCH_AUTOMATION_ENABLED, False)
        real_coordinator.set_manual_override(True, reason="test")
        select = self._make_select(fake_hass, entry, real_coordinator)

        seen: list[tuple[bool, bool, object]] = []
        real_coordinator.async_evaluate_state_machine = AsyncMock(
            side_effect=lambda: seen.append((
                real_coordinator.get_switch_state(SWITCH_AUTOMATION_ENABLED),
                real_coordinator.data.manual_override,
                real_coordinator.data.manual_override_set_at,
            ))
        )

        with patch.object(select, "async_write_ha_state"):
            await select.async_select_option("automatic")
        await fake_hass.config_entries.async_flush(entry)

        assert seen == [(True, False, None)]

    async def test_automatic_with_unchanged_options_still_recomputes_once(
        self, fake_hass, entry, real_coordinator
    ):
        """Automation already persisted ON: `async_update_entry` changes
        nothing, HA fires no listener, so the select's own recompute is the
        only one — one cycle, not zero."""
        entry.options = {self.AUTOMATION_OPTION: True}
        select = self._make_select(fake_hass, entry, real_coordinator)

        with patch.object(select, "async_write_ha_state"):
            await select.async_select_option("automatic")
        await fake_hass.config_entries.async_flush(entry)

        assert fake_hass.config_entries.dispatched == 0
        assert real_coordinator._compute_derived_values.call_count == 1
        assert real_coordinator.async_evaluate_state_machine.await_count == 1
        assert (
            real_coordinator._state_machine.invalidate_decision_fingerprint.call_count
            == 1
        )


class TestOptionsFlowSaveStillRecomputesOnce:
    """The listener is also what options-flow saves go through (they never
    call a platform method), so it must keep doing its upkeep — reschedule
    the daily-summary timer and reset entity tracking — before the single
    recompute."""

    async def test_options_flow_save_still_recomputes_once(
        self, mock_hass_with_services, entry, real_coordinator
    ):
        order: list[str] = []
        real_coordinator.reschedule_daily_summary_timer = MagicMock(
            side_effect=lambda: order.append("reschedule_timer")
        )
        real_coordinator.reset_entity_tracking_on_options_change = MagicMock(
            side_effect=lambda: order.append("reset_entity_tracking")
        )
        real_coordinator._compute_derived_values = MagicMock(
            side_effect=lambda: order.append("compute")
        )

        await _async_options_updated(mock_hass_with_services, entry)

        assert order == ["reschedule_timer", "reset_entity_tracking", "compute"]
        assert real_coordinator._compute_derived_values.call_count == 1
        assert (
            real_coordinator._state_machine.invalidate_decision_fingerprint.call_count
            == 1
        )


class TestSetupEntryRegistersTheOptionsListener:
    """Closes the loop between "the listener does the work" (tests above)
    and "the listener is actually wired up" at integration setup."""

    async def test_setup_entry_registers_the_options_listener(
        self, mock_hass_with_services
    ):
        entry = MagicMock()
        entry.data = {}
        entry.options = {}
        entry.update_listeners = []
        entry.add_update_listener = MagicMock(side_effect=entry.update_listeners.append)
        entry.async_on_unload = MagicMock()

        mock_hass_with_services.config_entries = MagicMock()
        mock_hass_with_services.config_entries.async_forward_entry_setups = AsyncMock()

        with patch(
            "custom_components.localshift.LocalShiftCoordinator"
        ) as mock_coordinator_cls:
            mock_coordinator_cls.return_value.async_start = AsyncMock()
            result = await async_setup_entry(mock_hass_with_services, entry)

        assert result is True
        entry.add_update_listener.assert_called_once_with(_async_options_updated)
        entry.async_on_unload.assert_called_once()
        assert entry.update_listeners == [_async_options_updated]
