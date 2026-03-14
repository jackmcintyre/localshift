# Design: Refactor Battery Mode Select to Have "Automatic" Option

## Problem Statement

The current battery mode select has confusing behaviors:
- **UI doesn't reflect manual selection**: When a user picks a mode (e.g., "grid_charging") while automation is enabled, the UI briefly shows the selection but then reverts because `active_mode` overrides the stored manual choice.
- **Manual mode state is unclear**: The automation switch is separate from the select, making it unclear how to enter or leave manual control. There's also a `manual_override` flag that is never set.

**Success criteria:**
- Manual selection persists in UI until changed or automatic is chosen.
- Obvious mechanism to return to automatic control (select "Automatic").
- No changes to state machine or hardware control logic.

---

## Proposed Solution

Replace the two-control pattern with a **single select** that includes an `"automatic"` option:
- `"automatic"` → optimizer controls (automation enabled)
- Other modes → manual override (automation disabled)

### 2.1 `const.py`

```python
SELECT_OPTIONS = {
    SELECT_BATTERY_MODE: [
        "automatic",
        "self_consumption",
        "grid_charging",
        "boost_charging",
        "spike_discharge",
        "proactive_export",
    ],
}
```

### 2.2 `select.py – BatteryModeSelect`

#### New Attributes
- `_manual_mode: str` – stores manual selection when automation is off

#### `__init__`
```python
self._manual_mode = entry.options.get("manual_battery_mode", "self_consumption")
self._previous_mode = self._manual_mode
```

#### `current_option`
```python
@property
def current_option(self) -> str | None:
    if not self.coordinator.get_switch_state(SWITCH_AUTOMATION_ENABLED):
        return self._manual_mode
    mode = self.coordinator.data.active_mode
    if mode == BatteryMode.DEMAND_BLOCK:
        return "self_consumption"
    if mode in (BatteryMode.SELF_CONSUMPTION, BatteryMode.GRID_CHARGING,
                BatteryMode.BOOST_CHARGING, BatteryMode.SPIKE_DISCHARGE,
                BatteryMode.PROACTIVE_EXPORT):
        return mode.value
    return self._manual_mode
```

#### `async_select_option`
```python
async def async_select_option(self, option: str) -> None:
    _LOGGER.info("Battery mode select changed to: %s", option)
    if option not in self._attr_options:
        _LOGGER.error("Invalid battery mode selected: %s", option)
        return

    old_mode = self.current_option

    if option == "automatic":
        self.coordinator.set_switch_state(SWITCH_AUTOMATION_ENABLED, True)
        new_options = {**self._entry.options, "switch_state_automation_enabled": True}
        self.coordinator.data.manual_override = False
        self.hass.config_entries.async_update_entry(self._entry, options=new_options)
        await self.coordinator.async_recompute_and_evaluate()
        self.async_write_ha_state()
        return

    # Manual mode
    self.coordinator.set_switch_state(SWITCH_AUTOMATION_ENABLED, False)
    new_options = {**self._entry.options, "switch_state_automation_enabled": False, "manual_battery_mode": option}
    self.hass.config_entries.async_update_entry(self._entry, options=new_options)
    self.coordinator.data.manual_override = True

    try:
        target_mode = BatteryMode(option)
    except ValueError:
        _LOGGER.error("Invalid battery mode selected: %s", option)
        return

    success = await self.coordinator.async_set_battery_mode(target_mode)
    if not success:
        _LOGGER.warning("Failed to apply battery mode %s, reverting select to %s", option, old_mode)
        self.async_write_ha_state()
        return

    self._manual_mode = option
    self._previous_mode = option
    if self.coordinator._notification_service is not None:
        await self.coordinator._notification_service.send_manual_action_notification(
            f"Manual {target_mode.display_name}", self.coordinator.data
        )
    self.async_write_ha_state()
```

#### Startup sync
In `async_added_to_hass`, ensure `data.manual_override` matches automation switch state:
```python
async def async_added_to_hass(self) -> None:
    self.async_on_remove(self.coordinator.async_add_listener(self._handle_coordinator_update))
    # Sync manual_override flag with automation switch state
    if not self.coordinator.get_switch_state(SWITCH_AUTOMATION_ENABLED):
        self.coordinator.data.manual_override = True
    else:
        self.coordinator.data.manual_override = False
```

---

## 3. Data Flow

User selects option → `async_select_option` → update switch state, persist selection, set `manual_override` → call `async_set_battery_mode` for hardware → `async_write_ha_state` updates UI.

---

## 4. Edge Cases

- **Manual fails**: log warning, revert `_manual_mode` unchanged, keep automation off, keep manual_override=True so optimizer doesn't interfere.
- **Automatic while automatic**: no-op, safe.
- **Manual while manual**: updates selection and re-sends hardware command.
- **Startup before coordinator ready**: `current_option` may return `None` (existing behavior).
- **Manual override timeout**: State machine later clears `data.manual_override`; this effectively returns control to optimizer (automatic mode). That's intended.

---

## 5. Backward Compatibility

- Option "automatic" appears first; existing users see it on restart.
- Separate automation switch entity remains synchronized (can be hidden via customization later).
- No migration; old manual mode not persisted (was in memory only) – acceptable.

---

## 6. Testing Plan (tests/test_select.py)

1. `test_current_option_returns_manual_when_automation_off`
2. `test_current_option_returns_active_mode_when_automation_on`
3. `test_select_automatic_enables_automation_and_clears_manual_override`
4. `test_select_manual_mode_disables_automation_sets_manual_override_and_persists`
5. `test_startup_with_automation_off_shows_persisted_manual_mode`
6. `test_select_invalid_option_returns_error`
7. `test_failure_reverts_state`

Coverage ≥95% of `select.py`.

---

## 7. Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| `manual_override` not set correctly on startup | `async_added_to_hass` syncs it with automation switch state |
| Separate automation switch confuses users | Document that it's synced; can be hidden in UI |
| Race condition | Existing state machine evaluation lock prevents concurrency issues |

---

## 8. Implementation Checklist

- [ ] Update `const.py` (add "automatic")
- [ ] Modify `BatteryModeSelect` in `select.py` (attributes, `current_option`, `async_select_option`, `async_added_to_hass` adjustments)
- [ ] Add tests in `tests/test_select.py`
- [ ] Run tests, validate coverage
- [ ] Update `docs/ENTITY_REFERENCE.md` if necessary

---

## 9. Open Questions

- Should we also hide the separate automation switch entity from the UI? That's a separate change and not in scope.
- Do we need to clear `manual_override` when user switches to automatic? Yes.
- Should `manual_battery_mode` persist across restarts? Yes, stored in config entry options.

---

## 10. Success Metrics

- Manual selection appears in UI until user changes it.
- Selecting "Automatic" immediately returns UI to optimizer's active mode.
- No regression in existing tests.
- Test coverage ≥95% for `select.py`.
