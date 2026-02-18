
# Mode Switching Delay Analysis

**Created:** 2026-02-18
**Status:** Investigation in progress

This document analyzes potential delay sources and bugs in the mode switching logic for the LocalShift battery automation system.

---

## 1. 🔴 CRITICAL: `elif` Blocks Proactive Export

> **Plain language:** If the forecast says "try to grid charge" but the battery is already full (so there's nothing to import), the code falls through that block — but because of a Python `elif` quirk, the very next check ("should I be exporting?") is silently skipped. The battery does nothing instead of exporting.

### The Problem

In `_compute_active_mode()` (`computation_engine.py`), the `proactive_export` check is chained as an `elif` off the second `if forecast_entry.get("grid_charge")` block:

```python
if forecast_entry.get("grid_charge_boost"):
    if grid_import_kwh > GRID_IMPORT_THRESHOLD:
        data.active_mode = BatteryMode.BOOST_CHARGING
        return
    else:
        _LOGGER.debug("fall through to check grid_charge")

if forecast_entry.get("grid_charge"):          # second "if"
    if grid_import_kwh > GRID_IMPORT_THRESHOLD:
        data.active_mode = BatteryMode.GRID_CHARGING
        return
    else:
        _LOGGER.debug("grid_charge=True but no import")

# FORECAST-DRIVED: Proactive export
elif forecast_entry.get("proactive_export"):   # <-- BUG: elif, not if
    data.active_mode = BatteryMode.PROACTIVE_EXPORT
    ...
```

### Why It Fails

In Python, `elif` only executes when the immediately preceding `if` condition was **False**. The `elif` is chained to `if forecast_entry.get("grid_charge")`.

**Scenario that breaks:**
- Slot has `grid_charge=True` and `grid_import_kwh=0` (no import to do)
- Falls into the `else` block for grid_charge (logs debug, no return)
- Because `grid_charge` was **truthy**, the `elif proactive_export` is **skipped**
- Mode falls through to the "OTHER MODES" block and defaults to SELF_CONSUMPTION

### Impact

Any time a forecast slot has `grid_charge=True` but zero import (legitimate - battery already full), proactive export is completely blocked for that slot, even if it should be exporting.

### Fix

Change `elif` to `if` for the proactive_export block:
```python
# Change this:
elif forecast_entry.get("proactive_export"):
# To this:
if forecast_entry.get("proactive_export"):
```

---

## 2. 🔴 CRITICAL: Debounce Timer Doesn't Reset on Mode Oscillation

> **Plain language:** The system has a 5-minute delay ("debounce") before acting on a new mode, to avoid reacting to fleeting price changes. But the timer never resets when the desired mode temporarily flips away and back again. So if prices wobble back and forth 3–4 times, the debounce clock keeps ticking through those wobbles — and the battery can end up grid-charging almost immediately after a brief price blip, instead of waiting the full 5 minutes of stable signal.

### The Problem

The `_mode_desired_since` dict records when a mode was **first** desired and is never cleared when the mode stops being desired (this was intentional to handle forecast flip-back). However, this causes the debounce to be effectively bypassed when a mode oscillates.

```python
if desired not in self._mode_desired_since:
    # First time this mode is desired — start the timer
    # NOTE: Don't clear other mode timers - they may be needed if forecast flips back
    self._mode_desired_since[desired] = now
```

### Example Scenario

- **t=0**: GRID_CHARGING becomes desired → timer starts (5-min debounce)
- **t=2min**: Forecast updates, mode flips back to SELF_CONSUMPTION → GRID_CHARGING timer stays in dict
- **t=3min**: Price drops again, GRID_CHARGING becomes desired again
- **Timer check**: `if desired not in self._mode_desired_since` → **False** (already in dict from t=0)
- **System thinks 3 minutes have elapsed, debounce needs only 2 more minutes**

The 2 minutes where GRID_CHARGING was NOT desired are counted toward the debounce. If the forecast oscillates 3-4 times, the debounce can be beaten entirely. In the worst case, the battery could be commanded to grid charge almost immediately after a single price fluctuation.

### Impact

Price-driven modes (GRID_CHARGING, BOOST_CHARGING) could be activated prematurely when prices oscillate near the threshold, wasting energy on charging at non-optimal prices.

### Fix

Clear the timer when a mode is no longer desired:
```python
# When desired mode changes away from X, remove X's timer
for mode in list(self._mode_desired_since.keys()):
    if mode != desired:
        self._mode_desired_since.pop(mode, None)
```

---

## 3. 🔴 CRITICAL: Forecast Slot Mismatch (Suspected Root Cause of Delays)

> **Plain language:** The forecast is built starting from the *next* 15-minute boundary (e.g. at 10:05 the forecast starts at 10:15), but when the code looks up "what should I be doing right now?" it looks for the *previous* boundary (e.g. 10:05 → looks for a 10:00 slot). That slot doesn't exist in the forecast, so it falls back — and in a narrow but real window (just before the top of a 15-minute mark) it finds nothing at all and defaults to doing nothing (SELF_CONSUMPTION). This is the most likely cause of the observed mode delays.

### The Problem

There's a **rounding mismatch between how the forecast is generated and how it's looked up**:

1. **Forecast Generation** (`forecast_computer.py`): Rounds UP to next 15-minute boundary
   - At 10:05, forecast starts at **10:15**
   - At 10:16, forecast starts at **10:30**
   - At 10:00 exactly, forecast starts at **10:15** (goes to next boundary)

2. **Forecast Lookup** (`computation_engine.py`): Rounds DOWN to current slot
   - At 10:05, looks for slot at **10:00** → not found
   - At 10:16, looks for slot at **10:15** → not found (forecast starts at 10:30)

### Impact

When current time falls between the previous 15-min slot and the first forecast slot:
- Lookup fails to find exact match
- System falls back to "first available slot" logic (within 15 min)
- If gap > 15 minutes OR first slot is in the past, returns `None`
- **Result: Mode defaults to SELF_CONSUMPTION instead of following forecast**

### Additional Note

The fallback logic also fails silently when the **forecast is stale** (first slot is in the past, `time_diff < 0`). This can happen if the coordinator fires before the forecast has recomputed. In this case `_get_forecast_entry_for_now()` returns `None` and mode defaults to SELF_CONSUMPTION with no warning-level log.

### Code Evidence

**Forecast generation** (`forecast_computer.py`):
```python
# Rounds UP - at 10:00 exactly, goes to 10:15
if minute % 15 == 0 and second == 0 and microsecond == 0:
    base_slot = now_dt.replace(second=0, microsecond=0)
else:
    remainder = minute % 15
    if remainder == 0:
        add_minutes = 15  # On boundary, go to NEXT
    else:
        add_minutes = 15 - remainder
    base_slot = now_dt + timedelta(minutes=add_minutes)
```

**Forecast lookup** (`computation_engine.py`):
```python
# Rounds DOWN
current_hour = now_dt.hour
current_minute = (now_dt.minute // 15) * 15  # rounds down

for entry in data.daily_forecast:
    if entry["hour"] == current_hour and entry["minute"] == current_minute:
        return entry  # Only exact match
```

### Timeline of Impact

At 10:14:59 (just before 10:15):
- Forecast starts at 10:15
- Lookup searches for 10:00 slot → not found
- Gap to first slot = 1 sec → fallback used ✅

At 10:00:01 (just after 10:00):
- Forecast starts at 10:15
- Lookup searches for 10:00 slot → not found
- Gap to first slot = 14:59 → fallback used ✅

At 9:59:59 (just before 10:00):
- Forecast starts at 10:15
- Lookup searches for 9:45 slot → not found (too old)
- Gap to 10:15 is 15+ minutes → **returns None, defaults to SELF_CONSUMPTION** ❌

### Potential Fix

Option A: Change forecast generation to start at current rounded-down slot (consistent with lookup):
```python
# Round DOWN to current 15-min slot
current_minute = (now_dt.minute // 15) * 15
base_slot = now_dt.replace(minute=current_minute, second=0, microsecond=0)
```

Option B: Change lookup to round UP instead of down (consistent with generation).

Option A is preferred - the forecast should always include the *current* slot.

---

## 4. 🟠 HIGH: `is_before_dw` Wrap-Around Bug in Forecast

> **Plain language:** After today's demand window has already started (e.g. it's 4pm and the demand window was at 3pm), the code that decides which forecast slots are eligible for pre-charging accidentally marks the hours from 4pm–11pm as "before the demand window." This can cause the forecast to plan grid charging during the late afternoon/evening when the demand window is already well underway — unnecessary and potentially costly charging.

### The Problem

In `compute_forecast()` (`forecast_computer.py`), when computing grid charge eligibility for each slot after the DW has started today (`now_dt.hour >= target_hour`):

```python
if now_dt.hour >= target_hour:
    is_before_dw = slot_hour >= now_dt.hour or slot_hour < target_hour
else:
    is_before_dw = slot_hour < target_hour
```

### Why It Fails

When `now_dt.hour >= target_hour` (e.g., now=16:00, DW=15:00):
- `is_before_dw = slot_hour >= 16 or slot_hour < 15`
- Slots at hours 0-14 are considered "before DW" → correct (next day pre-charging)
- Slots at hours 16-23 are **also** considered "before DW" → **incorrect**

Hours 16-23 are actually **after** today's demand window has started. The system shouldn't be trying to grid charge in these hours because the DW is already active.

### Impact

Could cause forecast to schedule grid charging in post-DW hours (late afternoon/evening), leading to incorrect mode decisions. This would only affect the afternoon portion of the forecast but could trigger charging when it's not needed.

### Fix

```python
# Simplifies to just:
is_before_dw = slot_hour < target_hour
```

---

## 5. 🟠 HIGH: `compute_derived_values` Not Protected by Evaluate Lock

> **Plain language:** When two sensor updates arrive close together (e.g. a price update and a battery SOC update within 100ms of each other), the code that recalculates the system state runs twice simultaneously on the same shared data object — with no protection against them stepping on each other. On top of that, by the time the second queued evaluation actually executes, it's working from a snapshot of the world that was taken *before* the first evaluation made any hardware changes — which can cause it to immediately undo what the first evaluation just did.

### The Problem

The coordinator calls `_compute_derived_values()` (which mutates the shared `data` object) BEFORE firing the async state machine task - and does so without any synchronization:

```python
@callback
def _handle_state_change(self, _event):
    self._read_all_external_state()
    self._compute_derived_values()           # mutates shared data
    self.hass.async_create_task(             # queues task reading that data
        self._evaluate_state_machine(),
    )
    self._notify_listeners()
```

Multiple rapid state change events (e.g., a price update and a SOC update arriving 100ms apart) trigger multiple concurrent executions of `_handle_state_change`, which means `compute_derived_values()` can run concurrently on the **same shared `data` object** with no lock.

### Full Race Condition: Stale State Post-Transition

Additionally, when a queued state machine evaluation runs, it operates on `data` that was captured **before** the transition completed. If multiple evaluations queue behind the evaluate lock:

1. t=0: State change fires, reads state, creates task (acquires lock, starts transition)
2. t=10: Price change fires, reads state (still pre-transition), creates task (waits on lock)
3. t=35: First transition completes, releases lock
4. t=35: **Second evaluation runs with state from t=10** (pre-transition hardware state)
5. t=35: May immediately try to revert the transition

### Fix

Call `_read_all_external_state()` and `_compute_derived_values()` **inside** `evaluate_state_machine()` after acquiring the lock.

---

## 6. 🟠 HIGH: Blocking Service Calls (Full Transition Can Take 40+ Seconds)

> **Plain language:** Every command sent to the Tesla battery (set export mode, set backup reserve, set operation mode) waits for the Teslemetry cloud to confirm before moving on. These calls happen one at a time, each taking up to 5 seconds. A full mode switch can require 3–4 of these calls followed by a 20-second verification loop — meaning the system can be frozen mid-transition for up to 40 seconds, and during that entire time it won't react to anything new (like a price spike).

### The Problem

All Teslemetry service calls use `blocking=True` AND are sequential:

```python
await self.hass.services.async_call(
    "select",
    "select_option",
    {"entity_id": entity_id, "option": mode},
    blocking=True,  # Waits for cloud API confirmation
)
```

### Impact

- Each command waits for Teslemetry cloud API response
- Typical latency: 1-5 seconds per command
- Commands are sequential, not parallel
- Full mode transition = 3-4 blocking calls + 20s validation loop

**Worst case timing for `set_force_charge()`:**
1. `_set_export_mode()` → up to 5s
2. `_set_backup_reserve()` → up to 5s
3. `_set_operation_mode()` → up to 5s
4. `validate_transition()` → up to 20s
5. **Total: up to 35-40 seconds**

During this 35-40 seconds, `in_mode_transition=True` blocks all re-evaluations.

### Affected Transitions

- `set_self_consumption()`: 3 calls + 20s validation = up to 35s
- `set_force_charge()`: 4 calls + 20s validation = up to 40s
- `set_boost_charge()`: 3 calls + 20s validation = up to 35s
- `set_force_discharge()`: 3 calls + 20s validation = up to 35s
- `set_proactive_export()`: 3 calls + 20s validation = up to 35s

---

## 7. 🟠 HIGH: Validation Timeout

> **Plain language:** After sending commands to the battery, the system polls for up to 20 seconds to confirm the battery's reported state matches what was requested. If Teslemetry is slow (common — cloud propagation can take 15–30 seconds), the full 20-second wait is burned every time. Because this wait happens while the system is "locked" mid-transition, nothing else can happen during those 20 seconds.

### The Problem

After sending commands, `validate_transition()` waits up to 20 seconds for hardware state to match:

```python
for attempt in range(timeout):  # timeout = 20
    await asyncio.sleep(1)
    # Check if state matches...
```

### Impact

- Adds up to 20 seconds delay after each transition
- Only exits early if validation succeeds
- If Teslemetry is slow to update, full 20s wait occurs
- This runs while `in_mode_transition=True`, blocking all state change processing

### Why It Exists

Added to ensure commands actually took effect before considering the transition complete. However, it adds significant latency.

### Potential Fix

- Reduce timeout to 10 seconds
- Or run validation asynchronously in the background (post-transition, non-blocking)

---

## 8. 🟠 HIGH: Manual Override Timeout Triggers Nested Computation

> **Plain language:** When a manual override expires, the code tries to be clever by immediately re-running the full forecast and decision logic *inside* the same function that's already mid-way through evaluating state. This causes the forecast engine to run twice in one cycle — potentially logging the same decision twice and triggering a second unnecessary forecast recompute. It's also a hidden coupling: the state machine shouldn't need to know about the computation engine internals.

### The Problem

When manual override expires inside `evaluate_state_machine()` (which already holds the evaluate lock), a nested call to `compute_derived_values()` is made:

```python
if data.manual_override and self._manual_override_set_at is not None:
    if elapsed >= timedelta(hours=timeout_hours):
        data.manual_override = False
        self._manual_override_set_at = None
        # Re-evaluate now that override is cleared
        computation_engine.compute_derived_values(data)   # <-- nested call
        desired = data.active_mode
```

### Impact

1. **Double forecast computation**: `_forecast_change_tracker` gets called twice in one cycle. The 1-minute age check may trigger a second full forecast recompute within the same evaluation.
2. **Double decision log entry**: `compute_derived_values` updates `_last_decision_log_time` and may append to the decision log. If called twice, the same cycle logs twice.
3. **Hidden dependency**: The state machine now has an internal dependency on `computation_engine` that's not obvious from the signature.

### Fix

Instead of re-running `compute_derived_values`, just clear the override and update `desired` from the now-cleared `data.active_mode`. The next evaluation cycle will recompute naturally.

---

## 9. 🟡 MEDIUM: Proactive Export Has No Discharge Window Guard

> **Plain language:** Price spike discharging has a sensible rule: only discharge after 6am. But proactive export (forecast-driven discharging) has no such rule. If the forecast ever marks an overnight slot as worth exporting (e.g. an unusually high feed-in tariff at 2am), the battery will start discharging in the middle of the night. The forecast computer doesn't check the time of day — it's purely driven by price and SOC.

### The Problem

`_compute_active_mode()` gates SPIKE_DISCHARGE behind a discharge window check:

```python
in_discharge_window = current_hour >= DISCHARGE_EARLIEST_HOUR  # 6am
if data.price_spike and spike_discharge_enabled and in_discharge_window:
    data.active_mode = BatteryMode.SPIKE_DISCHARGE
```

But PROACTIVE_EXPORT is set directly from the forecast with **no time-of-day guard**:

```python
elif forecast_entry.get("proactive_export"):  # no time check
    data.active_mode = BatteryMode.PROACTIVE_EXPORT
```

### Impact

If `_should_proactive_export_at_slot()` in `forecast_computer.py` marks an overnight slot for export (e.g., due to unusually high FIT prices overnight), the battery will attempt to discharge during nighttime hours. The forecast computer has no explicit hour-of-day check — it's purely price/SOC driven.

### Fix

Add a discharge window guard to proactive export:
```python
if forecast_entry.get("proactive_export") and in_discharge_window:
    data.active_mode = BatteryMode.PROACTIVE_EXPORT
```

---

## 10. 🟡 MEDIUM: Concurrent Evaluation - Debounce Timer (5 Minutes)

> **Plain language:** This is intentional behaviour, not a bug. Price-driven mode changes (like starting grid charging) are deliberately delayed 5 minutes to avoid reacting to fleeting price movements. Users may perceive this as sluggishness, but it's a design choice to prevent costly oscillation. Combined with the 1-minute polling tick, the visible "delay" can be up to 6 minutes.

### The Problem

Price-driven mode transitions have a 5-minute debounce:

```python
def get_debounce_for_transition(self, from_mode, to_mode):
    # Immediate for high-priority modes...
    # All other (price-driven): 5 minutes
    return timedelta(minutes=5)
```

### Impact

- When price drops below threshold, system waits 5 minutes before acting
- This is by design to prevent oscillation
- But can feel like a "delay" to users
- Combined with the 1-minute periodic tick, transition can take up to 6 minutes

### Not Affected (Immediate)

- Any mode → SPIKE_DISCHARGE
- Any mode → PROACTIVE_EXPORT
- Any mode → DEMAND_BLOCK
- Any mode → MANUAL

---

## 11. 🟡 MEDIUM: 1-Minute Periodic Evaluation

> **Plain language:** Even if no sensor events are received, the system re-evaluates its state once per minute as a safety net. This means if an event is somehow missed, the worst-case catch-up time is 1 minute. Not a bug, but it adds to the maximum possible delay when combined with the debounce.

### The Problem

The coordinator only evaluates state on:
1. Entity state changes (real-time)
2. 1-minute periodic tick

```python
PERIODIC_INTERVAL = timedelta(minutes=1)
```

### Impact

- If an entity changes but the event is missed, worst case is 1-minute delay
- Combined with 5-minute debounce, worst case total: 6 minutes before any action

---

## 12. 🟡 MEDIUM: State Change Skip During Transition

> **Plain language:** While the system is mid-transition (locked for up to 40 seconds), it completely ignores any incoming state changes — including high-priority ones like a sudden price spike. If a spike arrives during a 40-second transition window, it will be missed entirely, and by the time the system unlocks and re-evaluates, the spike may already be over.

### The Problem

When `in_mode_transition=True`, all state changes are ignored:

```python
if self._state_machine.in_mode_transition:
    _LOGGER.debug("Skipping re-evaluation during mode transition")
    return
```

### Impact

- Prevents feedback loops when we change entities
- But also blocks legitimate state changes during transition (e.g., price spike)
- If transition takes 35+ seconds, a price spike that occurs during transition is missed entirely
- The spike may be gone by the time the next evaluation runs

---

## 13. 🟡 MEDIUM: Forecast Change Detection

> **Plain language:** The forecast only recomputes when something meaningful changes: any price movement, or battery SOC changing by 1% or more. Tiny SOC fluctuations (< 1%) are ignored. If nothing significant changes, the forecast can be up to 1 minute stale before the age-based fallback forces a recompute. This is mostly fine, but means mode decisions could lag slightly behind reality.

### The Problem

Forecast only recomputes when significant changes are detected:

```python
# Price changes (ANY change = recalc)
if price != self._last_price:
    return True, reason

# SOC change (1% threshold)
if soc_change >= 1.0:
    return True, reason
```

### Impact

- Small SOC changes (< 1%) don't trigger recompute
- If forecast becomes stale, mode decisions may be outdated
- Maximum staleness: 1 minute (age-based backup check)

---

## 14. 🟡 MEDIUM: `_get_forecast_at_demand_window` Returns Today's (Stale) Entry Post-DW

> **Plain language:** After today's demand window has already started, the code that looks up "what does the forecast say about the demand window?" returns the demand window slot from *today* — which is now in the past. That past slot contains a predicted battery level that was calculated before the DW actually happened, not what reality turned out to be. This stale prediction is used to decide whether to boost-charge and to drive dashboard displays. Tomorrow's DW slot would be more useful, but may not always exist in the forecast window.

### The Problem

`_get_forecast_at_demand_window()` searches for the first entry matching `hour == target_hour and minute == 0`:

```python
for entry in data.daily_forecast:
    if entry["hour"] == target_hour and entry["minute"] == 0:
        return entry  # First match - always today
```

### Impact

After today's DW has already started (e.g., it's 17:00, DW was at 15:00), this returns the **15:00 slot from the current forecast** — which is a past slot. The `predicted_soc` in that slot reflects a forecast made before the DW started, not what actually happened. This stale SOC is used to:
1. Set `data.solar_can_reach_target`
2. Set `data.boost_charge_needed`
3. Drive the "solar battery forecast" dashboard sensor

For next day, the forecast wraps around and the 15:00 slot the next day is about 22+ slots from the end of the 96-slot forecast — it's only present if the DW is within 24 hours of the forecast start. If not, `None` is returned and the fallback uses current SOC which is more accurate.

### Fix

After DW start, look for **tomorrow's** DW entry, not today's. The timestamp on the entry can distinguish them.

---

## 15. 🟡 MEDIUM: Health Check Triggers Command Spam Without Cooldown

> **Plain language:** Every minute, when the system is in a stable mode, it double-checks that the battery hardware actually reflects the commanded mode. If there's a mismatch, it immediately re-sends all the transition commands. The problem: Teslemetry can take 15–30 seconds to reflect a change in its reported state, so in the normal window right after a mode switch, the health check sees a "mismatch" and re-fires all commands — and then does it again a minute later — causing a continuous loop of redundant commands until Teslemetry catches up.

### The Problem

`_perform_health_check()` runs **every minute** when mode is stable, and immediately re-issues all transition commands if there's any mismatch:

```python
if not is_valid:
    # Attempt to correct the drift
    await self._execute_mode_transition(data, self._commanded_mode)
```

There is no "last corrected at" tracking or minimum time between corrections.

### Impact

If Teslemetry cloud lags in reflecting the state after a legitimate transition (common — can take 15-30 seconds for state to propagate), the health check will:
1. See stale state, detect a "mismatch"
2. Re-issue all 3-4 transition commands
3. 1 minute later, check again
4. Still stale → re-issue again

This can cause **continuous command spam** every minute while Teslemetry is slow. Each re-correction also re-validates (up to 20 seconds), potentially causing cascading delays.

Additionally, health checks only run when `desired == commanded_mode`. During debounce periods (mode is desired but not yet actioned), health checks are skipped — hardware can drift for up to 5+ minutes before being corrected.

### Fix

Add a cooldown to `_perform_health_check`:
```python
_MIN_CORRECTION_INTERVAL = timedelta(minutes=5)
if (
    self._last_health_correction is None or
    now - self._last_health_correction >= _MIN_CORRECTION_INTERVAL
):
    await self._execute_mode_transition(data, self._commanded_mode)
    self._last_health_correction = now
```

---

## 16. ⚪ LOW: Startup Grace Period

> **Plain language:** For the first 30 seconds after Home Assistant starts (or the integration reloads), the system holds its position and makes no mode changes. This prevents it from acting on incomplete or stale state that hasn't fully loaded yet. It only happens at startup and isn't a concern during normal operation.

### The Problem

30-second grace period at startup prevents any mode changes:

```python
self._startup_grace_until = dt_util.now() + timedelta(seconds=30)
```

### Impact

- Only affects initial startup
- Not a runtime delay
- But during this window, mode stays as last hardware state

---

## 17. ⚪ LOW: Missing Log Entries at Mode Fallthrough Paths

> **Plain language:** When the code runs through all the mode-selection logic and ends up defaulting to "do nothing special" (SELF_CONSUMPTION), it does so silently — no log entry is written to explain *why* none of the other modes were chosen. Every other mode that *is* selected does log a reason. This makes it very hard to debug situations where the battery seems to be ignoring the forecast, because there's no trace of what was evaluated and rejected.

### The Problem

In `_compute_active_mode()`, when mode falls through to the "OTHER MODES" section and defaults to SELF_CONSUMPTION, no log entry is emitted:

```python
else:
    data.active_mode = BatteryMode.SELF_CONSUMPTION  # ← no log
```

Other paths that trigger modes DO log:
```python
data.active_mode = BatteryMode.BOOST_CHARGING
_LOGGER.info("Forecast-driven: BOOST_CHARGING ...")  # ← logged ✅
```

### Impact

When diagnosing why a mode is SELF_CONSUMPTION, there's no log trace to show why each other mode was rejected. Makes troubleshooting slow, especially for the `grid_charge=True but grid_import=0` fallthrough which now silently results in SELF_CONSUMPTION.

---

## Summary Table

| Issue | Severity | Type | Delay / Impact |
|-------|----------|------|----------------|
| `elif` blocks proactive export | 🔴 CRITICAL | Bug | Proactive export skipped when grid_charge=True |
| Debounce timer doesn't reset on oscillation | 🔴 CRITICAL | Bug | Debounce bypassed, premature charging |
| Forecast slot mismatch | 🔴 CRITICAL | Bug | Mode defaults to SELF_CONSUMPTION |
| `is_before_dw` wrap-around | 🟠 HIGH | Bug | Spurious grid charging in post-DW hours |
| `compute_derived_values` outside lock | 🟠 HIGH | Race condition | Concurrent mutations + stale state post-transition |
| Blocking sequential calls | 🟠 HIGH | Latency | 3-40 sec per transition |
| Validation timeout | 🟠 HIGH | Latency | 0-20 sec per transition |
| Manual override double computation | 🟠 HIGH | Bug | Double logging, nested forecast recompute |
| Proactive export no discharge guard | 🟡 MEDIUM | Bug | Possible overnight discharge |
| `_get_forecast_at_demand_window` stale entry | 🟡 MEDIUM | Bug | Stale predicted SOC after DW starts |
| Health check command spam | 🟡 MEDIUM | Design flaw | Continuous command spam on Teslemetry lag |
| Debounce timer 5-min wait | 🟡 MEDIUM | By design | 0-5 min before acting (intended) |
| Periodic evaluation | 🟡 MEDIUM | By design | 0-1 min worst case |
| Transition state skip | 🟡 MEDIUM | By design | Price spikes missed during transitions |
| Forecast change detection | 🟡 MEDIUM | By design | 0-1 min staleness |
| Startup grace period | ⚪ LOW | By design | 30 sec at startup only |
| Missing log at fallthrough | ⚪ LOW | Observability | Hard to diagnose SELF_CONSUMPTION cause |

---

## Recommended Fix Order

1. **`elif` → `if` for proactive_export** — 1-line fix, high impact
2. **Debounce timer oscillation reset** — clear stale timers when desired mode changes
3. **Forecast slot mismatch** — Change base_slot to round down (align with lookup)
4. **`is_before_dw` wrap-around** — Simplify to `slot_hour < target_hour`
5. **`compute_derived_values` outside lock** — move state read + compute inside the evaluate lock
6. **Manual override double computation** — remove nested `compute_derived_values` call
7. **Proactive export discharge guard** — add `in_discharge_window` check
8. **Health check cooldown** — add minimum correction interval
9. **Validation timeout** — reduce from 20s to 10s or make async

---

## Debug Logging Recommendations

Add these logs to diagnose delays in production:

```python
# In _get_forecast_entry_for_now():
_LOGGER.warning(
    "FORECAST LOOKUP: now=%s, looking for %02d:%02d, first_slot=%s, gap=%.0fs, result=%s",
    now_dt.strftime("%H:%M:%S"),
    current_hour, current_minute,
    data.debug_first_forecast_slot_time,
    data.debug_time_gap_seconds,
    "found" if data.debug_forecast_slot_found else "NONE",
)

# In _execute_mode_transition():
import time as _time
start_time = _time.monotonic()
# ... commands ...
_LOGGER.info(
    "TRANSITION TIMING: %s completed in %.1fs",
    target.value,
    _time.monotonic() - start_time,
)

# In _compute_active_mode() fallthrough path:
_LOGGER.debug(
    "Mode fallthrough to SELF_CONSUMPTION: grid_charge=%s grid_boost=%s proactive=%s spike=%s dw=%s manual=%s",
    forecast_entry.get("grid_charge"),
    forecast_entry.get("grid_charge_boost"),
    forecast_entry.get("proactive_export"),
    data.price_spike,
    data.demand_window_active,
    data.manual_override,
)
```

---

## Action Log

| Date | Item | Action | File | Notes |
|------|------|--------|------|-------|
| 2026-02-18 | 1 | Fixed `elif` → `if` for `proactive_export` block | `computation_engine.py` | One-line change in `_compute_active_mode()`. Proactive export now evaluates independently of `grid_charge` block outcome. |
| 2026-02-18 | 2 | Clear stale debounce timers when desired mode changes | `state_machine.py` | Added loop before `if desired not in self._mode_desired_since` to pop all timer entries for modes other than the current desired mode. Ensures debounce is always served from a continuous period of stable desire, not accumulated across oscillations. |
| 2026-02-18 | 3 | Hybrid 5-min/15-min forecast + granularity-agnostic lookup | `forecast_computer.py`, `computation_engine.py`, `solar_utils.py` | **`base_slot`** now rounds DOWN to current 5-min boundary (was round-up to next 15-min). Main loop replaced with 24 × 5-min near-term slots + 88 × 15-min long-term slots (24 h total). `_get_forecast_entry_for_now()` replaced exact-match + fallback with "most-recent slot ≤ now" scan — granularity-agnostic, fallback gap logic removed. New helper `get_solar_for_5min_slot()` added to `solar_utils.py`. Resolves forecast slot mismatch that caused SELF_CONSUMPTION fallback for up to ~14 min per cycle. |
| 2026-02-18 | 4 | Fix `is_before_dw` wrap-around bug | `forecast_computer.py` | Simplified `is_before_dw = slot_hour >= now_dt.hour or slot_hour < target_hour` → `is_before_dw = slot_hour < target_hour`. The 24-slot forecast is always chronological so the DW hour threshold correctly partitions today's post-DW slots and tomorrow's pre-DW slots without a date check. Done alongside Item 3 as confirmed. |
| 2026-02-18 | 5 | Move `_read_all_external_state` + `_compute_derived_values` inside evaluate lock | `coordinator.py`, `state_machine.py` | **`_handle_state_change()`**: removed `_compute_derived_values()` call; now only reads raw entity state + notifies listeners for fast UI feedback, then queues the task. **`_handle_periodic_tick()`**: same — removed compute; cost accumulation still uses the just-read raw state. **`_evaluate_state_machine()`** / `evaluate_state_machine()`: `read_state_func` and `notify_func` added as optional callables; at the top of the evaluate lock, state is re-read and derived values recomputed. `try/finally` around all evaluation logic ensures `notify_func()` is always called after `compute_derived_values()` regardless of which code path returns. Eliminates the race where a queued evaluation operated on pre-transition stale state and could immediately revert a transition. |
| 2026-02-18 | 6 | Remove nested `compute_derived_values()` from manual override timeout | `state_machine.py` | Removed the nested `compute_derived_values(data)` call in the override-expiry block. With Item 5's fix a full recompute already ran at the top of the lock; a second call would double-fire the forecast tracker age-check and potentially log a duplicate decision entry. Instead, `_forecast_change_tracker._last_forecast_time` is set to `None` to ensure the next periodic tick forces a fresh recompute with `manual_override = False`. `desired` remains MANUAL for the current cycle; the correct mode is applied at most 1 minute later. |
| 2026-02-18 | 9 (Item 7 in doc) | Proactive export discharge guard — NOT added | `computation_engine.py` | Decided **not** to add `and in_discharge_window` to the proactive export check. Unlike `SPIKE_DISCHARGE` (reactive, no forward planning), proactive export is forecast-driven: the forecast computer tracks predicted SOC across all 112 slots and only marks a slot for export if remaining SOC is sufficient to cover overnight load until solar returns. A time-of-day guard would incorrectly block legitimate overnight export at high feed-in prices. |
| 2026-02-18 | 8 (Item 15 in doc) | Health check correction cooldown | `state_machine.py` | Added `_last_health_correction: datetime \| None` and `_MIN_CORRECTION_INTERVAL = timedelta(minutes=5)` to `StateMachine.__init__`. `_perform_health_check()` now only re-issues transition commands if `now - _last_health_correction >= 5 min` (or never corrected). Prevents continuous command spam every minute when Teslemetry cloud lags in reflecting a legitimate transition (can take 15–30 s for state to propagate). |
| 2026-02-18 | 9 (Item 7 fix order) | Validation timeout reduced 20 s → 10 s | `battery_controller.py` | Changed `timeout=20` → `timeout=10` at all 5 call sites (`set_self_consumption`, `set_force_charge`, `set_boost_charge`, `set_force_discharge`, `set_proactive_export`) and updated the `validate_transition()` default parameter from 20 to 10. Reduces worst-case `in_mode_transition` lock time from ~40 s to ~25 s per transition. The "operation_mode matches → success" early-exit logic already handles Teslemetry lag gracefully within the 10 s window. |
| 2026-02-18 | 14 | Fix `_get_forecast_at_demand_window` stale entry post-DW | `computation_engine.py` | Replaced "first match" logic with a chronological scan that returns the first DW-hour entry whose timestamp ≥ now. After the DW has already started (e.g. it's 17:00 and DW=15:00) today's 15:00 slot is skipped and tomorrow's is returned instead. Includes tz-aware comparison guard (same pattern as `_get_forecast_entry_for_now`). Ensures `data.solar_can_reach_target`, `data.boost_charge_needed`, and the solar battery forecast dashboard sensor all reflect the *upcoming* DW rather than a stale past slot. |
| 2026-02-18 | 17 | Add debug log at SELF_CONSUMPTION fallthrough | `computation_engine.py` | Added `_LOGGER.debug(...)` at the `else:` branch in `_compute_active_mode()` logging the current time plus all the forecast/state flags that were evaluated. Makes it straightforward to diagnose why the battery is in SELF_CONSUMPTION when the forecast says it should be doing something else. |
