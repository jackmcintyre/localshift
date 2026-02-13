# Amber Powerwall Integration - Bug Backlog

**Last Updated:** 2026-02-13  
**Review Date:** Complete code review performed covering all components

This backlog documents all bugs and improvement opportunities identified through comprehensive code review of the Amber Powerwall integration. Issues are prioritized by severity and impact.

## Summary Statistics
- **Critical Bugs:** 6 total, **5 resolved** (affect core functionality)
- **High Priority Improvements:** 7 (reliability & robustness)
- **Medium Priority Improvements:** 5 (code quality & maintainability)
- **Low Priority Improvements:** 4 (cosmetic & nice-to-have)
- **Already Resolved:** 2 (correctly implemented features)

**Total Issues:** 24

---

## ✅ Recently Completed (2026-02-13)

The following critical bugs have been resolved:
1. **Switch State Not Persisted** - Switch settings now survive HA restarts
2. **Number Entity Re-evaluation** - Threshold changes take effect immediately
3. **Historical Load Cache Unit Bug** - Fixed 1000x incorrect values in solar projections
4. **Hardcoded Sun Entity** - Sun entity is now configurable
5. **Manual Override Persistence** - Added configurable auto-clear timeout (default 4 hours)

## Critical Bugs

### 1. Force Charge Detection Logic Bug
**File:** `coordinator.py` (lines ~440-447)

**Issue:** The force charge detection logic is incorrect:
```python
d.force_charge_active = d.operation_mode == "backup" or (
    d.operation_mode == "autonomous" and d.backup_reserve > 99
)
```
The OR operator makes this always true when in "backup" mode. The logic is meant to detect:
- "backup" mode (3.3kW force charge)
- "autonomous" mode with reserve > 99 (5kW boost charge)

However, the OR structure doesn't properly distinguish between these two states.

**Fix:** Change to:
```python
d.force_charge_active = (
    d.operation_mode == "backup" or 
    (d.operation_mode == "autonomous" and d.backup_reserve > 99)
)
```

---

### 2. Switch State Not Persisted on HA Restart ✅ RESOLVED
**File:** `switch.py`, `const.py`

**Issue:** Switches default to `SWITCH_DEFAULTS` on every restart, ignoring user changes. The switch reads from coordinator's internal dict (`_switch_states`) which resets on restart.

**Fix Applied:** 
- Added switch state persistence to config entry options
- Switches now load initial state from options in `__init__` method
- Switches save state to options when toggled
- Added `SWITCH_STATE_PREFIX` constant for option key naming
- Switch settings now survive HA restarts

---

### 3. Number Entity Doesn't Trigger Immediate Re-evaluation ✅ RESOLVED
**File:** `number.py` (async_set_native_value method)

**Issue:** When a user changes a threshold via the slider, only the config entry is updated. The critical methods `_compute_derived_values()` and `async_evaluate_state_machine()` are NEVER called - change only takes effect on the next periodic tick (up to 1 minute delay).

**Fix Applied:** After updating options, the code now calls:
```python
self.coordinator._compute_derived_values()
self.coordinator._notify_listeners()
await self.coordinator.async_evaluate_state_machine()
```
- Threshold changes now take effect instantly instead of waiting up to 1 minute
- UI updates immediately when thresholds are changed
- State machine re-evaluates immediately after threshold changes

---

### 4. Historical Load Cache Unit Conversion Bug ✅ RESOLVED
**File:** `coordinator.py` (_get_historical_hourly_averages method, line ~658)

**Issue:** The code assumes load power is in watts and converts to kW:
```python
avg_kw = avg_watts / 1000.0
```
However, Teslemetry load power is ALREADY in kW (positive = import). This results in.average being incorrectly divided by 1000, producing values 1000x too small (e.g., 0.0005 kW instead of 0.5 kW). This causes incorrect solar projection calculations.

**Fix Applied:** Check unit of measurement of source entity:
```python
state = self.hass.states.get(entity_id)
unit = state.attributes.get("unit_of_measurement", "")
if unit == "W":
    avg_kw = avg_value / 1000.0
elif unit == "kW":
    avg_kw = avg_value
else:
    _LOGGER.debug("Unknown unit %s for %s, assuming kW", unit, entity_id)
    avg_kw = avg_value
```
- Added unit of measurement checking from source entity
- Properly handles both watts (W) and kilowatts (kW) units
- Only divides by 1000 when source is in watts
- Added debug logging for unknown units
- Fixes 1000x incorrect values that were breaking solar projections

---

### 5. Hardcoded Sun Entity Reference ✅ RESOLVED
**File:** `coordinator.py`, `const.py`, `config_flow.py`

**Issue:** Uses hardcoded `"sun.sun"` entity:
```python
sun_state = self.hass.states.get("sun.sun")
sun_up = sun_state is not None and sun_state.state == "above_horizon"
```
If user has renamed, removed, or never set up sun integration, `sun_up` will always be False, causing solar export hold to never activate even when it should.

**Fix Applied:**
- Added `CONF_SUN_ENTITY` configuration constant
- Added sun entity to config flow as a selectable entity
- Updated coordinator to use configurable sun entity instead of hardcoded "sun.sun"
- Added fallback logging when sun entity is unavailable
- Solar export hold now works correctly even if user has renamed or doesn't use the default sun entity

---

### 6. Manual Override Persistence Issue ✅ RESOLVED
**File:** `coordinator.py`, `const.py`, `config_flow.py`, `button.py`

**Issue:** The `manual_override` flag is only cleared when:
- "Return to Self Consumption" button is pressed, OR
- HA restarts

It persists indefinitely otherwise, potentially leaving automation disabled for hours after a manual action. There's no automatic clearing mechanism (e.g., after demand window ends, after X hours, or on mode change from automation).

**Fix Applied:**
- Added `CONF_MANUAL_OVERRIDE_TIMEOUT` configuration option (default: 4 hours)
- Added timeout tracking variable `_manual_override_set_at`
- Added timeout check in state machine evaluation that clears override after configured timeout
- All manual override buttons now set timestamp when activated
- Manual override automatically clears after configurable timeout (0 = no auto-clear)
- Added to config flow for user configuration

## High Priority Improvements

### 7. Add Entity Validation on Config Flow
**File:** `config_flow.py`

**Issue:** No validation that selected entities exist or are from correct integrations. Could fail silently with runtime errors.

**Fix:** Add validation step that checks entity domains and availability before proceeding. Use async_validate_step or custom validators.

---

### 8. Missing Tomorrow's Forecast Integration
**File:** `coordinator.py` - `_compute_derived_values` (Step 4: solar_battery_forecast)

**Issue:** The `solcast_tomorrow` data is read and stored but NEVER used in solar_battery_forecast calculations. Only today's forecast is considered, potentially missing important overnight solar contributions for early morning demand windows.

**Fix:** Modify `_sum_solar_before_target` to include tomorrow's forecast when target_hour is earlier than the current hour (e.g., target at 15:00, currently 20:00).

---

### 9. No Error Handling for Powerwall API Calls
**File:** `coordinator.py` - `_set_export_mode`, `_set_operation_mode`, `_set_backup_reserve` methods

**Issue:** These methods make async service calls with no try/except blocks. If Teslemetry API is down or slow, exceptions propagate and may:
- Crash the coordinator
- Leave battery in undefined state
- Cause no notifications of failure

**Fix:** Wrap all service calls in try/except with logging:
```python
try:
    await self.hass.services.async_call(...)
    _LOGGER.debug("Command %s successful", command)
except Exception as e:
    _LOGGER.error("Failed to execute %s: %s", command, e)
    # Optionally retry or notify user
```

---

### 10. Notification Service Not Validated
**File:** `config_flow.py` (async_step_solcast)

**Issue:** `CONF_NOTIFY_SERVICE` accepts any text input without validating the notify service exists. Invalid service will cause notification failures at runtime with no warning during setup.

**Fix:** Use entity selector for notify services or validate against available notify services:
```python
from homeassistant.components import notify
available_services = notify.async_get_services(hass)
```

---

### 11. Arbitrary Sleep Delays Between Commands
**File:** `coordinator.py` (async_set_self_consumption, async_set_hold, etc.)

**Issue:** Uses hardcoded `await asyncio.sleep(5)` between each command. This is arbitrary and:
- May be too long (causing unnecessary delays in mode transitions)
- May be too short (race conditions if Powerwall hasn't processed previous command)
- No verification that command actually succeeded

**Fix:** 
- Make delays configurable via options (default 5s)
- OR implement proper state verification (wait for mode change confirmation via _read_state)
- OR reduce to 2s and add retry logic if mode doesn't change

---

### 12. Demand Window Block Logic Priority Issue
**File:** `coordinator.py` (Step 14: active_mode calculation, line ~475)

**Issue:** 
```python
elif d.demand_window_active:
    d.active_mode = BatteryMode.DEMAND_BLOCK
```
This sets DEMAND_BLOCK whenever active, even if price_spike is detected. However, for financial optimization, spike discharge should take priority over demand block - exporting during a spike is more valuable than avoiding imports.

**Fix:** Reorder priority check so price_spike is evaluated BEFORE demand_window_active:
```python
elif d.price_spike and spike_discharge_enabled and in_discharge_window:
    d.active_mode = BatteryMode.SPIKE_DISCHARGE
elif d.demand_window_active:
    d.active_mode = BatteryMode.DEMAND_BLOCK
```
(Note: Looking at current code, spike IS checked first - this issue may already be resolved)

---

### 13. Inconsistent State Machine Priority Order
**File:** `coordinator.py` (Step 14)

**Issue:** The priority chain order doesn't match documentation:
- Documentation says: "Automation disabled" should be FIRST priority
- Code checks: demand_window_active BEFORE automation_enabled

This means automation disabled users still get demand block enforcement, which may be unexpected.

**Fix:** Reorder to match documented priority:
1. Automation disabled → MANUAL
2. Price spike → SPIKE_DISCHARGE
3. Manual override → MANUAL
4. Demand window → DEMAND_BLOCK
5. ... rest of chain

---

## Medium Priority Improvements

### 14. No Test Coverage
**File:** `tests/` directory

**Issue:** The `tests/` directory is empty. No unit tests for critical logic:
- State machine transitions
- Price calculations (percentile, effective cheap price)
- Mode detection from Teslemetry state
- Solar projection calculations

**Fix:** Add comprehensive test suite using pytest:
- Test coordinator.py with mock HA entities
- Verify all 9 battery mode transitions
- Test edge cases (boundary times, price thresholds)
- Test error handling paths

---

### 15. Time Precision Inconsistency
**File:** `coordinator.py` (line ~415)

**Issue:** Uses `now_t = now_dt.time()` to compare against demand window times, which are parsed from strings like "15:00:00". The comparison `now_t >= dw_start_time` compares time objects with different precision (now_t includes microseconds, dw_start_time may or may not).

**Impact:** At the exact boundary (e.g., 14:59:59.999), the comparison may behave unexpectedly.

**Fix:** Ensure consistent time handling:
```python
# Strip microseconds from now for comparison
now_t = now_dt.replace(microsecond=0).time()
```

---

### 16. Decision Log Limited to 50 Entries
**File:** `coordinator.py` (line ~975)

**Issue:** Decision log is capped at 50 entries, discarding older history:
```python
if len(d.decision_log) > 50:
    d.decision_log = d.decision_log[-50:]
```
For debugging issues that occurred hours ago, this may be insufficient. Users can't see what happened earlier in the day.

**Fix:** 
- Increase limit to 100-200 entries, OR
- Implement time-based retention (keep last 24 hours), OR
- Make it configurable via options

---

### 17. Missing Cleanup for Historical Load Cache
**File:** `coordinator.py` (async_stop method)

**Issue:** The historical load cache is cleared at midnight but never explicitly cleaned up on coordinator shutdown. While not critical (memory will be freed on process exit), it's good practice for clean shutdown.

**Fix:** Add cache cleanup in `async_stop`:
```python
self._historical_load_cache.clear()
self._historical_load_cache_date = ""
```

---

### 18. Unused Config Option - ALLOW_EXPORT
**File:** `config_flow.py` (async_step_user)

**Issue:** `CONF_TESLEMETRY_ALLOW_EXPORT` is collected in config flow but:
- There's NO way to reconfigure it after setup
- It's hardcoded with default in DEFAULT_ENTITY_IDS
- The integration changes it programmatically (so user shouldn't be setting it)

**Fix:** Either:
- Remove from config flow (integration manages it), OR
- Add to config flow as optional entity that can be reconfigured

---

## Low Priority Improvements

### 19. Dashboard Setup Complexity
**File:** README.md

**Issue:** Dashboard YAML is provided but requires:
1. Manual integration into Lovelace
2. Additional YAML helpers (Riemann sum, utility meters) that are NOT created by the component
3. User must manually create these sensors in configuration.yaml

**Fix:** Consider:
- Creating helper sensors automatically via integration
- Providing clearer step-by-step setup instructions
- Including automation templates for the required sensors
- OR removing dashboard from integration and documenting as separate optional addon

---

### 20. Hardcoded Personal Notification Service
**File:** `const.py` (DEFAULT_ENTITY_IDS)

**Issue:** `notify.mobile_app_jacks_iphone` is hardcoded with personal user's device ID. Every user who installs this will see this default, which won't work for them.

**Fix:** 
- Use more generic default like "notify.mobile_app" or make it required without default, OR
- Detect available notify services and use first one as default

---

### 21. Version Inconsistency
**Files:** `manifest.json` vs README

**Issue:** 
- manifest.json shows version "0.1.0" 
- README mentions "Home Assistant 2025.6+ compatibility"

The version number doesn't follow semantic versioning properly or align with HA version compatibility.

**Fix:** Update version to proper semantic versioning (e.g., "1.0.0" for stable release) and document HA compatibility in manifest.

---

### 22. Missing Type Hints for Internal Methods
**File:** `coordinator.py`

**Issue:** Many internal helper methods lack complete type hints, making code harder to:
- Maintain
- Understand
- Use IDE auto-completion
- Catch type errors early

Examples:
- `_get_expected_load_kw` - no return type
- `_get_historical_hourly_averages` - no parameter types
- `_sum_solar_before_target` - incomplete types

**Fix:** Add proper type annotations for all method parameters and return values:
```python
def _get_expected_load_kw(self, hours_to_target: float) -> float:
```

---

## Minor / Cosmetic Issues

### 23. No Cleanup on Coordinator Shutdown
**File:** `coordinator.py`

**Issue:** The `_unsub` callback lists are set to None in `async_stop`, but the actual callback objects are not properly cleared if they exist.

**Fix:** Verify all unsubscribes are properly called before setting to None.

---

### 24. Inconsistent Logging Levels
**File:** Multiple files

**Issue:** Some informational messages use `_LOGGER.info` when they should use `_LOGGER.debug` (e.g., mode transitions during normal operation). This clutters logs.

**Fix:** Review all log statements and adjust levels:
- ERROR: Failed operations
- WARNING: Recoverable issues
- INFO: Important state changes (mode transitions, startup)
- DEBUG: Routine operations, detailed state

---

## Already Resolved / Not Actual Issues

### Self-Consumption Button Manual Override
**Status:** CORRECTLY IMPLEMENTED
**File:** `button.py` (SelfConsumptionButton class)

The "Return to Self Consumption" button DOES clear `manual_override` flag. The `async_set_self_consumption()` method in coordinator.py sets `d.manual_override = False`. This is working as intended.

---

### Demand Window vs Spike Priority
**Status:** ALREADY CORRECT
**File:** `coordinator.py` (Step 14)

The code already checks price_spike BEFORE demand_window_active, so spike discharge takes priority. No fix needed.
