"""Symbols that are LIVE but that a static scan cannot see being used (#986).

Read by scripts/dead_code_gate.py, which is what CI runs. Also valid vulture
input, so a bare `uv run vulture` picks it up from [tool.vulture] paths.

This is NOT where you silence the gate. An entry is a claim that the symbol
runs in production through dynamic access, and the gate re-checks the claim:

  # file:      where the symbol is defined, relative to the package
  # kind:      what the detector calls it (method, function, variable, class,
               attribute, unread-field). An entry covers that one kind of
               symbol in that one file and nothing else.
  # site:      a file, relative to the repo root, that reaches the symbol
  # evidence:  text that must still be present in that site
               ({name} = the entry's own name). Several site/evidence pairs
               are allowed; every one must hold.
  # why:       one line on the mechanism.

If the evidence disappears (a dispatch-table row is deleted, a getattr goes
away) the gate fails and tells you the symbol is now dead. If the symbol is
deleted, moved, or starts being referenced directly, the gate fails until the
entry is removed. Anything you cannot trace to a site belongs in
dead_code_baseline.txt, not here.

`# framework:` is for what Home Assistant calls by name, and it is narrow on
purpose. It accepts only:
  * `_.member`  - a method or property of a class that derives from a
    homeassistant import. The test suite checks that every class it ends up
    covering really is a subclass of the framework class named, and that the
    framework class defines the member.
  * a bare name - a module-level hook, matched only in the modules HA loads by
    name: __init__.py and the PLATFORMS modules.
Anything else under `# framework:` is an error.

Rules (review findings #1074, #1077):
  * No bare-name matching. `ForecastSlot.estimate` does not hide any other
    `estimate`; a second `StateMachine` in another file is not covered.
  * No broad nouns (mode, state, data, value, name, options, update, ...).
  * The two conventions Home Assistant defines by pattern are applied by the
    gate itself, scoped: `_attr_*` on classes that derive from homeassistant,
    `async_step_*` on config/options flow classes. They are not listed here.
"""

# ===========================================================================
# Home Assistant calls these by name. No site in this repo can be cited; the
# test suite verifies each name against the framework class or module named.
# ===========================================================================

# framework: homeassistant.config_entries
# why: HA imports the integration and each platform module and calls these
#      module-level hooks by name when a config entry is set up, unloaded or,
#      when its stored version is older than the flow's, migrated.
async_setup_entry
async_unload_entry
async_migrate_entry

# framework: homeassistant.helpers.entity.Entity
# why: Entity properties and lifecycle hooks; the entity platform reads and
#      calls them by name on every entity it manages.
_.device_info
_.extra_state_attributes
_.icon
_.async_added_to_hass
_.async_will_remove_from_hass

# framework: homeassistant.components.button.ButtonEntity
# why: button.press service handler.
_.async_press

# framework: homeassistant.components.number.NumberEntity
# why: number state property and number.set_value service handler.
_.native_value
_.async_set_native_value

# framework: homeassistant.components.select.SelectEntity
# why: select.select_option service handler.
_.async_select_option

# framework: homeassistant.components.switch.SwitchEntity
# why: switch state property and switch.turn_on / turn_off service handlers.
_.is_on
_.async_turn_on
_.async_turn_off

# framework: homeassistant.config_entries.ConfigFlow
# why: HA calls this static hook on the flow handler to open the options flow.
_.async_get_options_flow

# ===========================================================================
# Reached through dynamic access inside this repo.
# ===========================================================================

# file: config_flow/__init__.py
# kind: class
# site: custom_components/localshift/config_flow/__init__.py
# evidence: class LocalShiftConfigFlow(ConfigFlow, domain=DOMAIN)
# why: the `domain=` class keyword registers the flow handler with HA
#      (ConfigFlow.__init_subclass__); nothing imports the class by name.
LocalShiftConfigFlow

# file: state/machine.py
# kind: method
# site: custom_components/localshift/state/mode_configs.py
# evidence: "{name}"
# site: custom_components/localshift/state/machine.py
# evidence: getattr(self, builder_name)(data)
# why: BATTERY WRITE PATH. MODE_CONFIG_BUILDERS maps each BatteryMode to a
#      method name as a string; StateMachine._get_mode_config resolves it with
#      getattr.
StateMachine._build_self_consumption_config
StateMachine._build_grid_charging_config
StateMachine._build_boost_charging_config
StateMachine._build_spike_discharge_config
StateMachine._build_proactive_export_config
StateMachine._build_hold_config

# file: state/machine.py
# kind: method
# site: custom_components/localshift/state/mode_configs.py
# evidence: "{name}"
# site: custom_components/localshift/state/machine.py
# evidence: getattr(self, executor_name)(data, config, dry_run)
# why: BATTERY WRITE PATH. MODE_EXECUTORS maps each BatteryMode to a method
#      name as a string; StateMachine._dispatch_mode_transition resolves it
#      with getattr.
StateMachine._execute_self_consumption_transition
StateMachine._execute_grid_charging_transition
StateMachine._execute_boost_charging_transition
StateMachine._execute_spike_discharge_transition
StateMachine._execute_proactive_export_transition
StateMachine._execute_hold_transition

# file: pricing/types.py
# kind: variable
# site: custom_components/localshift/pricing/types.py
# evidence: return getattr(self, key, default)
# site: custom_components/localshift/engine/slots.py
# evidence: entry.get("estimate")
# why: ForecastSlot.get() is a dict-style accessor over getattr. SlotBuilder
#      reads the settlement-estimate flag through it on the feed-in forecast
#      (data.feed_in_forecast is list[ForecastSlot]) to defer starting an
#      export on an unconfirmed price.
ForecastSlot.estimate

# file: engine/core.py
# kind: function
# site: tests/engine/test_next_window_tail.py
# evidence: core._reset_tail_failure_log()
# why: deliberate test hook. It clears the module-level once-a-day log budget
#      in engine/core.py so tests of that budget do not depend on run order.
#      Nothing in production calls it, by design.
_reset_tail_failure_log

# file: coordinator/data.py
# kind: unread-field
# site: scripts/replay_no_dw.py
# evidence: data.optimizer_result
# site: tests/test_scenarios.py
# evidence: result = data.optimizer_result
# why: CoordinatorData field with no reader inside the integration, kept on
#      purpose: the replay scripts read the plan's cost off it and the scenario
#      tripwire asserts it is not None, which is what catches the inline
#      optimizer being silently disabled behind the facade's broad except.
CoordinatorData.optimizer_result

# file: config_flow/__init__.py
# kind: variable
# site: custom_components/localshift/config_flow/__init__.py
# evidence: class LocalShiftConfigFlow(ConfigFlow, domain=DOMAIN):
# site: custom_components/localshift/__init__.py
# evidence: async def async_migrate_entry(
# why: Home Assistant reads ConfigFlow.MINOR_VERSION when it loads an entry
#      and calls async_migrate_entry when the stored minor version is older
#      (issue #1120: 1.1 -> 1.2 turns spike export off once). New entries are
#      created at this version. The reader is inside Home Assistant.
LocalShiftConfigFlow.MINOR_VERSION

# file: learning/correlation.py
# kind: attribute
# site: custom_components/localshift/learning/correlation.py
# evidence: from homeassistant.helpers.storage import Store
# evidence: self._store = Store[
# evidence: self._store._async_migrate_func = self._async_migrate_store_data
# why: Home Assistant's Store calls self._async_migrate_func when the stored
#      version is older; assigning it on the instance is how WeatherCorrelation
#      installs its migration. The reader is inside Home Assistant, so the
#      evidence here is that _store is an HA Store; the test suite checks Store
#      still has the hook.
self._store._async_migrate_func
