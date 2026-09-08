"""Vulture whitelist for Home Assistant framework entry points and entity properties.

LocalShift is a Home Assistant custom integration. Many of these names are invoked
by the HA framework via reflection or string dispatch, so they appear to be unused
to static analysis. This whitelist suppresses false positives for:

1. Config entry / integration lifecycle: async_setup, async_setup_entry, etc.
2. Entity lifecycle callbacks: async_added_to_hass, async_will_remove_from_hass, etc.
3. Entity command handlers: async_turn_on, async_turn_off, async_press, etc.
4. Entity properties: device_info, extra_state_attributes, icon, etc. (read reflectively)
5. Entity class attributes: _attr_* prefixes (entity descriptors, private to HA)
6. Named framework classes: config-flow handlers registered by domain string.

Prefix families dispatched by name (async_step_*, _build_*_config,
_execute_*_transition) are suppressed via `ignore_names` in
[tool.vulture] (pyproject.toml), not enumerated here — see that section
for why each prefix is a structural fact rather than a case-by-case
suppression.

Deliberate-but-currently-test-only-referenced names live in
vulture_allowlist.py instead of this file, each with a reason and an
issue number (Issue #986) — keep the two files separate so genuine HA
framework false positives never blur together with tracked debt.

See: https://developers.home-assistant.io/docs/creating_platform_integration/
     https://developers.home-assistant.io/docs/config_entries_index/
"""

# Config flow class (registered by domain string in the flow handler registry)
LocalShiftConfigFlow

# Integration lifecycle (invoked by HA startup/config)
async_setup
async_setup_entry
async_unload_entry
async_migrate_entry
async_reload_entry
async_remove_entry
async_migrate_config_entry
async_remove_config_entry_device

# Config flow lifecycle
async_get_options_flow
async_supports_options_flow

# Entity lifecycle callbacks
async_added_to_hass
async_will_remove_from_hass
async_update
update

# Entity command handlers (read reflectively by dispatcher)
async_turn_on
async_turn_off
async_toggle
async_press
press
async_select_option
select_option
async_set_native_value
set_native_value

# Coordinator
_async_update_data
_async_setup

# HA Store migration hook (assigned to store._async_migrate_func and invoked
# by the Store class itself when the on-disk schema version is stale;
# see homeassistant.helpers.storage.Store)
_async_migrate_func

# Entity properties (read reflectively by HA's entity model)
device_info
unique_id
name
icon
entity_picture
entity_category
extra_state_attributes
available
enabled
should_poll
entity_registry_enabled_default
entity_registry_visible_default
native_value
native_min_value
native_max_value
native_step
native_unit_of_measurement
device_class
state_class
suggested_display_precision
mode
options
current_option
is_on
state
assumed_state
capability_attributes

# Entity class attributes (_attr_* descriptors, private to HA)
_attr_unique_id
_attr_name
_attr_icon
_attr_native_min_value
_attr_native_max_value
_attr_native_step
_attr_native_unit_of_measurement
_attr_mode
_attr_entity_category
_attr_assumed_state
_attr_should_poll
_attr_available
_attr_enabled
_attr_entity_registry_enabled_default
_attr_entity_registry_visible_default
_attr_device_class
_attr_state_class
_attr_suggested_display_precision
_attr_entity_picture
_attr_capability_attributes

# Config-flow step handlers (async_step_user, async_step_init,
# async_step_options_pricing_source, async_step_advanced, ...) are matched
# by the "async_step_*" glob in [tool.vulture] ignore_names (pyproject.toml),
# not enumerated here.
