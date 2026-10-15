"""Spike export ships switched OFF, including on installs that stored it ON (#1120).

``switch.localshift_spike_discharge_enabled`` used to default ON, and its state
is persisted in the config entry's options, so every existing install has
``on`` stored. A changed default alone would leave the rule ON there. The
config entry migration 1.1 -> 1.2 turns it OFF once; after that the owner's
choice must survive restarts.

These drive Home Assistant's own ``ConfigEntry.async_migrate`` (the gate that
decides whether ``async_migrate_entry`` runs at all, from the stored version
against ``LocalShiftConfigFlow``'s), then build the real switch entity from the
entry, as setup does.
"""

from __future__ import annotations

from types import MappingProxyType, SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntry

import custom_components.localshift as localshift
from custom_components.localshift.config_flow import LocalShiftConfigFlow
from custom_components.localshift.const import (
    CONF_BATTERY_TARGET,
    DOMAIN,
    SWITCH_DEFAULTS,
    SWITCH_DRY_RUN,
    SWITCH_SPIKE_DISCHARGE_ENABLED,
)
from custom_components.localshift.switch import SWITCH_STATE_PREFIX, LocalShiftSwitch

SPIKE_OPTION = f"{SWITCH_STATE_PREFIX}{SWITCH_SPIKE_DISCHARGE_ENABLED}"
DRY_RUN_OPTION = f"{SWITCH_STATE_PREFIX}{SWITCH_DRY_RUN}"


def _entry(minor_version: int, options: dict[str, Any]) -> ConfigEntry:
    entry = ConfigEntry(
        data={},
        discovery_keys=MappingProxyType({}),
        domain=DOMAIN,
        minor_version=minor_version,
        options=options,
        source="user",
        subentries_data=None,
        title="LocalShift",
        unique_id=None,
        version=1,
    )

    async def _component():
        return localshift

    # Where Home Assistant would load the integration from disk.
    object.__setattr__(
        entry,
        "_integration_for_domain",
        SimpleNamespace(async_get_component=_component),
    )
    return entry


def _hass() -> MagicMock:
    """Home Assistant as far as the migration and the switch touch it."""
    hass = MagicMock()

    def update_entry(entry, *, options=None, minor_version=None):
        # What ConfigEntries.async_update_entry does to the fields used here.
        if options is not None:
            object.__setattr__(entry, "options", MappingProxyType(dict(options)))
        if minor_version is not None:
            object.__setattr__(entry, "minor_version", minor_version)
        return True

    hass.config_entries.async_update_entry = MagicMock(side_effect=update_entry)
    return hass


def _coordinator() -> MagicMock:
    """The switch-state bridge, with the production fallback to the defaults."""
    coordinator = MagicMock()
    states: dict[str, bool] = {}
    coordinator.set_switch_state = MagicMock(side_effect=states.__setitem__)
    coordinator.get_switch_state = MagicMock(
        side_effect=lambda key: states.get(key, SWITCH_DEFAULTS.get(key, False))
    )
    return coordinator


def _start(hass: MagicMock, entry: ConfigEntry) -> LocalShiftSwitch:
    """One Home Assistant start: migrate, then set up the switch."""
    switch = LocalShiftSwitch(_coordinator(), entry, SWITCH_SPIKE_DISCHARGE_ENABLED)
    switch.hass = hass
    return switch


async def _migrate(hass: MagicMock, entry: ConfigEntry) -> None:
    """Home Assistant's migration step, run before every setup."""
    assert await entry.async_migrate(hass) is True


class TestExistingInstall:
    async def test_stored_on_from_an_old_install_comes_up_off(self):
        hass = _hass()
        entry = _entry(
            1, {SPIKE_OPTION: True, DRY_RUN_OPTION: True, CONF_BATTERY_TARGET: 90}
        )

        await _migrate(hass, entry)
        switch = _start(hass, entry)

        assert switch.is_on is False
        assert entry.options[SPIKE_OPTION] is False
        assert entry.minor_version == LocalShiftConfigFlow.MINOR_VERSION == 2
        # Only this switch: every other stored option is left as it was.
        assert entry.options[DRY_RUN_OPTION] is True
        assert entry.options[CONF_BATTERY_TARGET] == 90

    async def test_turning_it_on_after_the_migration_survives_a_restart(self):
        hass = _hass()
        entry = _entry(1, {SPIKE_OPTION: True})
        await _migrate(hass, entry)
        switch = _start(hass, entry)
        assert switch.is_on is False

        with patch.object(LocalShiftSwitch, "async_write_ha_state"):
            await switch.async_turn_on()
        assert switch.is_on is True

        # Restart: Home Assistant stored minor version 2, so it does not
        # migrate again, and the switch comes back as the owner left it.
        await _migrate(hass, entry)
        restarted = _start(hass, entry)

        assert entry.options[SPIKE_OPTION] is True
        assert restarted.is_on is True

    async def test_turned_off_again_stays_off(self):
        hass = _hass()
        entry = _entry(2, {SPIKE_OPTION: True})
        switch = _start(hass, entry)

        with patch.object(LocalShiftSwitch, "async_write_ha_state"):
            await switch.async_turn_off()
        await _migrate(hass, entry)

        assert _start(hass, entry).is_on is False

    async def test_old_install_that_never_stored_the_switch_comes_up_off(self):
        hass = _hass()
        entry = _entry(1, {})

        await _migrate(hass, entry)

        assert _start(hass, entry).is_on is False


class TestNewInstall:
    def test_default_is_off(self):
        assert SWITCH_DEFAULTS[SWITCH_SPIKE_DISCHARGE_ENABLED] is False

    async def test_new_entry_starts_off_and_is_not_migrated(self):
        """A new entry is created at the flow's current version."""
        hass = _hass()
        entry = _entry(LocalShiftConfigFlow.MINOR_VERSION, {})

        await _migrate(hass, entry)

        assert hass.config_entries.async_update_entry.call_count == 0
        assert _start(hass, entry).is_on is False


async def test_entry_from_a_newer_major_version_is_refused():
    """A downgrade from a future major version is not set up."""
    entry = _entry(1, {})
    object.__setattr__(entry, "version", 2)

    assert await entry.async_migrate(_hass()) is False


@pytest.mark.parametrize("stored", [True, False])
async def test_planner_reads_off_after_the_migration(stored):
    """The bridge the switch seeds is what ComputationEngine passes on."""
    from custom_components.localshift.computation_engine import ComputationEngine
    from custom_components.localshift.const import CONF_SPIKE_EXPORT_ENABLED

    hass = _hass()
    entry = _entry(1, {SPIKE_OPTION: stored})
    await _migrate(hass, entry)
    switch = _start(hass, entry)

    engine = ComputationEngine.__new__(ComputationEngine)
    engine.entry = SimpleNamespace(options=dict(entry.options))
    engine._get_switch_state = switch.coordinator.get_switch_state

    assert engine._build_optimizer_config_options()[CONF_SPIKE_EXPORT_ENABLED] is False
