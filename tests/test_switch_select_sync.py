"""Regression coverage: switch entities mirror the coordinator's switch state.

`switch.localshift_automation_enabled` used to keep a private `_is_on`, loaded
from the entry options at init and only ever changed by its own
`async_turn_on` / `async_turn_off`. The battery-mode select changes automation
through the coordinator's switch-state bridge instead, so after a pick on the
select the switch entity kept showing its old value until the next restart or
an explicit switch service call — observed live on 2026-10-06, both ways round
(select -> automatic left the switch reading `off`; select -> a manual mode
left it reading `on`).

These tests run a real `LocalShiftCoordinator` (real switch-state bridge, real
listener fan-out, real `async_recompute_and_evaluate`) against the production
options-update listener, so they exercise the wiring the entity depends on
rather than a mock of it. Only the coordinator's compute / state-machine /
battery leaves are stubbed.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import Context

from custom_components.localshift import _async_options_updated
from custom_components.localshift.const import (
    SELECT_BATTERY_MODE,
    SELECT_OPTIONS,
    SWITCH_AUTOMATION_ENABLED,
    SWITCH_DEFAULTS,
    SWITCH_DRY_RUN,
)
from custom_components.localshift.coordinator import LocalShiftCoordinator
from custom_components.localshift.select import BatteryModeSelect
from custom_components.localshift.switch import (
    SWITCH_KEYS,
    SWITCH_STATE_PREFIX,
    LocalShiftSwitch,
)
from tests.coordinator.test_platform_recompute_ownership import FakeConfigEntries

AUTOMATION_OPTION = f"{SWITCH_STATE_PREFIX}{SWITCH_AUTOMATION_ENABLED}"
MANUAL_MODES = [o for o in SELECT_OPTIONS[SELECT_BATTERY_MODE] if o != "automatic"]


@pytest.fixture
def entry():
    """A bare config entry with real update-listener bookkeeping."""
    entry = MagicMock()
    entry.entry_id = "test_entry_switch_sync"
    entry.data = {}
    entry.options = {}
    entry.update_listeners = []
    entry.add_update_listener = entry.update_listeners.append
    return entry


@pytest.fixture
def hass(entry):
    """hass wired with the fake config_entries and the production listener."""
    hass = MagicMock()
    hass.config_entries = FakeConfigEntries(hass)
    entry.add_update_listener(_async_options_updated)
    return hass


@pytest.fixture
def coordinator(hass, entry):
    """A real coordinator with only its compute/state-machine/battery leaves
    stubbed — the switch-state bridge and `notify_listeners` are the real
    thing."""
    coord = LocalShiftCoordinator(hass, entry)
    coord._state_machine = MagicMock()
    coord._compute_derived_values = MagicMock()
    coord.async_evaluate_state_machine = AsyncMock()
    coord.async_set_battery_mode = AsyncMock(return_value=True)
    coord.async_set_self_consumption = AsyncMock()
    coord.reschedule_daily_summary_timer = MagicMock()
    coord._notification_service = None
    entry.runtime_data = coord
    return coord


async def _add_switch(coordinator, entry, hass, key=SWITCH_AUTOMATION_ENABLED):
    """Build a switch as the platform does and add it to hass.

    Returns the entity and the list of `is_on` values it wrote to the HA state
    machine, in order — what a dashboard or an automation would have seen.
    """
    switch = LocalShiftSwitch(coordinator, entry, key)
    switch.hass = hass
    written: list[bool] = []
    switch.async_write_ha_state = MagicMock(
        side_effect=lambda: written.append(switch.is_on)
    )
    await switch.async_added_to_hass()
    return switch, written


async def _add_battery_mode_select(coordinator, entry, hass):
    """Build the battery-mode select and add it to hass, picked by a user."""
    select = BatteryModeSelect(coordinator, entry)
    select.hass = hass
    select.async_write_ha_state = MagicMock()
    # A frontend dropdown pick always carries a user_id (Issue #934).
    select._context = Context(user_id="user-1")
    await select.async_added_to_hass()
    return select


class TestSelectDrivesAutomationSwitch:
    """A pick on the battery-mode select must show on the automation switch."""

    async def test_select_automatic_turns_switch_on(self, hass, entry, coordinator):
        entry.options = {AUTOMATION_OPTION: False, "manual_battery_mode": "hold"}
        switch, written = await _add_switch(coordinator, entry, hass)
        select = await _add_battery_mode_select(coordinator, entry, hass)
        assert switch.is_on is False

        await select.async_select_option("automatic")
        await hass.config_entries.async_flush(entry)

        assert switch.is_on is True
        assert written and written[-1] is True
        assert entry.options[AUTOMATION_OPTION] is True

    @pytest.mark.parametrize("mode", MANUAL_MODES)
    async def test_select_manual_mode_turns_switch_off(
        self, hass, entry, coordinator, mode
    ):
        entry.options = {AUTOMATION_OPTION: True}
        switch, written = await _add_switch(coordinator, entry, hass)
        select = await _add_battery_mode_select(coordinator, entry, hass)
        assert switch.is_on is True

        await select.async_select_option(mode)
        await hass.config_entries.async_flush(entry)

        assert switch.is_on is False
        assert written and written[-1] is False
        assert entry.options[AUTOMATION_OPTION] is False
        assert entry.options["manual_battery_mode"] == mode

    async def test_switch_follows_select_there_and_back(self, hass, entry, coordinator):
        """The 2026-10-05/06 sequence: manual at night, automatic next morning."""
        switch, written = await _add_switch(coordinator, entry, hass)
        select = await _add_battery_mode_select(coordinator, entry, hass)

        await select.async_select_option("self_consumption")
        await hass.config_entries.async_flush(entry)
        assert switch.is_on is False
        assert written[-1] is False

        await select.async_select_option("automatic")
        await hass.config_entries.async_flush(entry)
        assert switch.is_on is True
        assert written[-1] is True

    async def test_failed_manual_mode_still_shows_automation_off(
        self, hass, entry, coordinator
    ):
        """If the battery refuses the manual mode the select reverts, but
        automation has already been disabled — the switch must say so."""
        coordinator.async_set_battery_mode = AsyncMock(return_value=False)
        switch, written = await _add_switch(coordinator, entry, hass)
        select = await _add_battery_mode_select(coordinator, entry, hass)

        await select.async_select_option("grid_charging")
        await hass.config_entries.async_flush(entry)

        assert coordinator.get_switch_state(SWITCH_AUTOMATION_ENABLED) is False
        assert switch.is_on is False
        assert written and written[-1] is False


class TestSwitchReadsCoordinatorState:
    """`is_on` is a view over the coordinator bridge, not a private copy."""

    @pytest.mark.parametrize("key", SWITCH_KEYS)
    async def test_is_on_follows_the_bridge(self, hass, entry, coordinator, key):
        switch, _ = await _add_switch(coordinator, entry, hass, key)
        assert switch.is_on is SWITCH_DEFAULTS[key]

        coordinator.set_switch_state(key, not SWITCH_DEFAULTS[key])

        assert switch.is_on is (not SWITCH_DEFAULTS[key])

    async def test_coordinator_notify_writes_switch_state(
        self, hass, entry, coordinator
    ):
        switch, written = await _add_switch(coordinator, entry, hass)

        coordinator.set_switch_state(SWITCH_AUTOMATION_ENABLED, False)
        coordinator.notify_listeners()

        assert written == [False]

    async def test_removed_switch_stops_listening(self, hass, entry, coordinator):
        switch, written = await _add_switch(coordinator, entry, hass)

        switch._call_on_remove_callbacks()
        coordinator.notify_listeners()

        assert written == []

    def test_persisted_state_seeds_the_bridge(self, hass, entry, coordinator):
        """Restart path: the persisted option still decides the initial state."""
        entry.options = {AUTOMATION_OPTION: False}

        switch = LocalShiftSwitch(coordinator, entry, SWITCH_AUTOMATION_ENABLED)

        assert coordinator.get_switch_state(SWITCH_AUTOMATION_ENABLED) is False
        assert switch.is_on is False


class TestSwitchOwnControlsStillWork:
    """turn_on / turn_off keep driving the entity, the bridge and the options."""

    @pytest.mark.parametrize("key", SWITCH_KEYS)
    async def test_turn_off_updates_entity_bridge_and_options(
        self, hass, entry, coordinator, key
    ):
        entry.options = {f"{SWITCH_STATE_PREFIX}{key}": True}
        switch, written = await _add_switch(coordinator, entry, hass, key)

        await switch.async_turn_off()
        await hass.config_entries.async_flush(entry)

        assert switch.is_on is False
        assert written and set(written) == {False}
        assert coordinator.get_switch_state(key) is False
        assert entry.options[f"{SWITCH_STATE_PREFIX}{key}"] is False

    @pytest.mark.parametrize("key", SWITCH_KEYS)
    async def test_turn_on_updates_entity_bridge_and_options(
        self, hass, entry, coordinator, key
    ):
        entry.options = {f"{SWITCH_STATE_PREFIX}{key}": False}
        switch, written = await _add_switch(coordinator, entry, hass, key)

        await switch.async_turn_on()
        await hass.config_entries.async_flush(entry)

        assert switch.is_on is True
        assert written and set(written) == {True}
        assert coordinator.get_switch_state(key) is True
        assert entry.options[f"{SWITCH_STATE_PREFIX}{key}"] is True

    async def test_turn_off_automation_still_returns_to_self_consumption(
        self, hass, entry, coordinator
    ):
        switch, _ = await _add_switch(coordinator, entry, hass)

        await switch.async_turn_off()

        coordinator.async_set_self_consumption.assert_awaited_once()

    async def test_switch_change_survives_a_restart(self, hass, entry, coordinator):
        """Persistence round-trip: a fresh coordinator + switch built from the
        options the first one wrote come up in the same state."""
        switch, _ = await _add_switch(coordinator, entry, hass, SWITCH_DRY_RUN)
        await switch.async_turn_on()

        restarted = LocalShiftCoordinator(hass, entry)
        reborn = LocalShiftSwitch(restarted, entry, SWITCH_DRY_RUN)

        assert reborn.is_on is True
        assert restarted.get_switch_state(SWITCH_DRY_RUN) is True
