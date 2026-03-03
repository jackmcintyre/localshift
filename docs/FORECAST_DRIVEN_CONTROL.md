# Forecast-Driven Control Design

## Problem Statement

The current system has duplicate logic for grid charging decisions:

1. **Forecast simulation** (`forecast_computer.py`): Determines IF/WHEN to grid charge for the 15-minute SOC forecast
2. **Mode control** (`computation_engine.py`): Determines WHEN to set GRID_CHARGING mode based on current state

These are independent and can diverge, causing:
- Forecast shows charging at 12:00 PM, but mode doesn't switch
- Mode switches to GRID_CHARGING, but forecast shows self-consumption
- Difficulty debugging: which one is "correct"?

## Solution: Single Source of Truth

### Architecture

```
┌─────────────────────────────────────────────────────────┐
│         forecast_computer.py                         │
│  ┌───────────────────────────────────────────────┐  │
│  │  Decision Logic (Single Source)               │  │
│  │  • _should_grid_charge_at_slot()             │  │
│  │  • _should_proactive_export_at_slot()         │  │
│  └───────────────────────────────────────────────┘  │
│                      │                              │
│          ┌───────────┴───────────┐               │
│          ▼                       ▼               │
│  ┌───────────────┐     ┌───────────────┐        │
│  │ Forecast      │     │ Forecast      │        │
│  │ Simulation    │     │ Data Structure│        │
│  │ (uses logic)  │     │ (marks plans) │        │
│  └───────────────┘     └───────────────┘        │
└─────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────┐
│         computation_engine.py                          │
│  ┌───────────────────────────────────────────────┐  │
│  │  Control Logic (Follows Plan)                 │  │
│  │  • _get_forecast_entry_for_now()              │  │
│  │  • _compute_active_mode() (reads forecast)      │  │
│  └───────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
```

### Key Principles

1. **Forecast IS the Plan**: The forecast data structure defines what WILL happen
2. **Control Follows Plan**: Control logic just executes what forecast says
3. **Single Decision Point**: Only one place makes charging/exporting decisions
4. **No Divergence**: Impossible for forecast and control to disagree

### Forecast Granularity

The forecast uses **hybrid slot sizes** to balance accuracy and complexity:

| Window | Slot size | Count | Coverage |
|--------|-----------|-------|----------|
| Near-term | 5 minutes | 24 | 2 hours |
| Long-term | 15 minutes | 88 | 22 hours |
| **Total** | | **112** | **24 hours** |

Near-term 5-minute slots match Amber's pricing data granularity, ensuring the
lookup in `_get_forecast_entry_for_now()` always finds an entry that covers the
current moment regardless of when the coordinator fires within a 5-minute window.
This eliminates the rounding mismatch (Issue 3) that previously caused the system
to default to SELF_CONSUMPTION for up to ~14 minutes per cycle.

All energy values scale with `slot_fraction = slot_minutes / 60`:
- Solar: `get_solar_for_5min_slot()` → `period_kwh / 6` for near-term; `get_solar_for_15min_slot()` → `period_kwh / 2` for long-term
- Consumption: `load_kw × slot_fraction`
- Charge / export caps: `CHARGE_RATE_kW × slot_fraction`

## Implementation: Phase 1 - Grid Charging

### Step 1.1: Extract Decision Logic

**Location**: `forecast_computer.py`

**New method**:
```python
def _should_grid_charge_at_slot(
    self,
    slot_start: datetime,
    solar_kwh: float,
    slot_price: float,
    predicted_soc: float,
    target_pct: float,
    effective_cheap_price: float,
    is_before_dw: bool,
    in_demand_window: bool,
    gap_to_target: float,
    is_daylight: bool,
) -> tuple[bool, bool]:
    """Determine if grid charging should happen at this slot.
    
    Single source of truth for grid charging decisions.
    Used by both forecast simulation and mode control.
    
    Args:
        slot_start: Start time of the 15-minute slot
        solar_kwh: Solar forecast for this slot
        slot_price: Buy price for this slot
        predicted_soc: Predicted SOC at start of slot
        target_pct: Target SOC percentage
        effective_cheap_price: Cheap price threshold
        is_before_dw: True if before demand window
        in_demand_window: True if in demand window
        gap_to_target: How many percent to target
        is_daylight: True if solar_kwh > 0.05
        
    Returns:
        (should_charge, should_boost)
    """
    # Never charge during demand window
    if in_demand_window:
        return False, False
    
    # Never charge after demand window
    if not is_before_dw:
        return False, False
    
    # Must have solar (daylight) to charge
    if not is_daylight:
        return False, False
    
    # Already at target - no charging needed
    if gap_to_target <= 0:
        return False, False
    
    # Price-based decisions
    price_is_cheap = slot_price <= effective_cheap_price
    price_is_very_cheap = slot_price <= (effective_cheap_price * 0.8)
    
    # Very cheap: boost charge (stock up!)
    if price_is_very_cheap:
        return True, True
    
    # Cheap: normal charge
    if price_is_cheap:
        return True, False
    
    # Far from target: charge anyway (urgency)
    if gap_to_target > 10:
        return True, False
    
    # Wait for cheaper price
    return False, False
```

### Step 1.2: Use in Forecast Simulation

**Location**: `forecast_computer.py` in `compute_forecast()` loop

**Replace inline logic** (lines 220-280) with:
```python
# Determine grid charging using single source of truth
should_grid_charge, should_boost = self._should_grid_charge_at_slot(
    slot_start=slot_start,
    solar_kwh=solar_kwh,
    slot_price=_slot_price,
    predicted_soc=predicted_soc,
    target_pct=target_pct,
    effective_cheap_price=data.effective_cheap_price,
    is_before_dw=is_before_dw,
    in_demand_window=in_demand_window,
    gap_to_target=gap_to_target,
    is_daylight=is_daylight,
)
```

### Step 1.3: Mark Forecast Entries

**Location**: `forecast_computer.py` in `compute_forecast()` loop

**Add to forecast entry**:
```python
daily_forecast.append({
    # ... existing fields ...
    "grid_charge": should_grid_charge,
    "grid_charge_boost": should_boost,
})
```

### Step 1.4: Implement Forecast-Driven Control ✅ IMPLEMENTED

**Location**: `computation_engine.py`

**Lookup helper** — granularity-agnostic scan (replaces old exact-match):
```python
def _get_forecast_entry_for_now(
    self, data: CoordinatorData, now_dt: datetime
) -> dict | None:
    """Find the most-recent forecast entry whose timestamp ≤ now.

    Because compute_forecast() starts from the rounded-down 5-minute boundary
    there is always an entry whose start time ≤ now — no fallback gap logic
    is required.  Works for 5-min near-term and 15-min long-term slots alike.
    """
    if not data.daily_forecast:
        return None

    now_local = dt_util.as_local(now_dt)
    best_entry = None
    for entry in data.daily_forecast:
        slot_local = dt_util.as_local(datetime.fromisoformat(entry["timestamp"]))
        if slot_local <= now_local:
            best_entry = entry
        else:
            break  # list is chronological
    return best_entry
```

**`_compute_active_mode()` — forecast-driven section (as implemented)**:
```python
# Grid charging (boost takes priority)
if forecast_entry.get("grid_charge_boost") and grid_import_kwh > threshold:
    data.active_mode = BatteryMode.BOOST_CHARGING
    return

if forecast_entry.get("grid_charge") and grid_import_kwh > threshold:
    data.active_mode = BatteryMode.GRID_CHARGING
    return

# Proactive export — `if`, NOT `elif`.
# Independent of grid_charge so that a slot with grid_charge=True but
# zero import (battery full) still triggers export if marked for it.
if forecast_entry.get("proactive_export"):
    data.active_mode = BatteryMode.PROACTIVE_EXPORT
    return
```

### Step 1.5: Test and Validate

**Validation checklist**:
- [ ] Forecast shows `grid_charge=True` at expected times
- [ ] Mode switches to GRID_CHARGING when forecast says so
- [ ] Mode switches to BOOST_CHARGING when forecast says so
- [ ] Behavior matches pre-refactoring system
- [ ] No regressions in other modes

**Debug logging**:
```python
_LOGGER.info(
    "Forecast: %02d:%02d grid_charge=%s boost=%s soc=%.1f price=%.2f",
    slot_hour, slot_minute, should_grid_charge, should_boost,
    predicted_soc, slot_price
)
```

## Implementation: Phase 2 - Proactive Export

### Step 2.1: Add Export Decision Logic

**Location**: `forecast_computer.py`

**New method**:
```python
def _should_proactive_export_at_slot(
    self,
    slot_start: datetime,
    solar_kwh: float,
    slot_fit: float,
    predicted_soc: float,
    target_pct: float,
    is_before_dw: bool,
    in_demand_window: bool,
    forecasted_excess_kwh: float,
) -> tuple[bool, float]:
    """Determine if proactive export should happen at this slot.
    
    Exports battery to grid before feed-in price goes negative.
    
    Args:
        slot_start: Start time of the 15-minute slot
        solar_kwh: Solar forecast for this slot
        slot_fit: Feed-in price for this slot
        predicted_soc: Predicted SOC at start of slot
        target_pct: Target SOC percentage
        is_before_dw: True if before demand window
        in_demand_window: True if in demand window
        forecasted_excess_kwh: Total excess forecast before DW
        
    Returns:
        (should_export, export_amount_kwh)
    """
    # Never export during demand window
    if in_demand_window:
        return False, 0.0
    
    # Never export after demand window
    if not is_before_dw:
        return False, 0.0
    
    # Must have daylight (solar to recharge)
    if solar_kwh <= 0.05:
        return False, 0.0
    
    # Must have positive FIT
    if slot_fit <= 0:
        return False, 0.0
    
    # Check if solar can reach target (already computed)
    # Use data.solar_can_reach_target from computation_engine
    
    # Check forecasted excess against 10% buffer
    target_kwh = target_pct / 100 * BATTERY_CAPACITY_KWH
    excess_threshold = target_kwh * 0.1  # 10% buffer
    
    if forecasted_excess_kwh < excess_threshold:
        return False, 0.0
    
    # Calculate export amount (excess - buffer)
    export_amount = forecasted_excess_kwh - excess_threshold
    
    # Limit to 15-min slot capacity (3.3kW * 0.25h = 0.825kWh)
    max_export_per_slot = CHARGE_RATE_BACKUP_KW / 4
    export_amount = min(export_amount, max_export_per_slot)
    
    return True, export_amount
```

### Step 2.2: Calculate Forecasted Excess

**Location**: `forecast_computer.py` in `compute_forecast()`

**Before main loop**:
```python
# Calculate total excess forecast before demand window
total_solar_before_dw = 0.0
total_consumption_before_dw = 0.0
target_dt = now_dt.replace(hour=target_hour, minute=0, second=0, microsecond=0)

for period in all_solcast:
    period_start = parse_forecast_dt(period.get("period_start"))
    if period_start is None:
        continue
    ps_local = dt_util.as_local(period_start)
    
    if ps_local >= target_dt:
        continue
    
    # Sum solar
    if ps_local >= now_dt:
        total_solar_before_dw += float(period.get("pv_estimate10", 0))
    
    # Estimate consumption (using weighted averages)
    slot_hour = ps_local.hour
    load_kw, _ = self._estimate_hourly_consumption_kw(
        historical_avg_kw, slot_hour, data.load_power_kw, recent_load_kw
    )
    total_consumption_before_dw += load_kw * 0.5  # 30-min periods

# Calculate excess after charging to target
current_kwh = current_soc / 100 * BATTERY_CAPACITY_KWH
gap_to_target_kwh = max(target_kwh - current_kwh, 0)
forecasted_excess = max(
    total_solar_before_dw - total_consumption_before_dw - gap_to_target_kwh,
    0.0
)
```

### Step 2.3: Use in Forecast Simulation

**Location**: `forecast_computer.py` in `compute_forecast()` loop

**After grid charging logic**:
```python
# Determine proactive export
should_proactive_export, export_amount_kwh = self._should_proactive_export_at_slot(
    slot_start=slot_start,
    solar_kwh=solar_kwh,
    slot_fit=get_price_for_slot(data.feed_in_forecast, slot_start),
    predicted_soc=predicted_soc,
    target_pct=target_pct,
    is_before_dw=is_before_dw,
    in_demand_window=in_demand_window,
    forecasted_excess_kwh=forecasted_excess,
)
```

**Modify battery delta calculation**:
```python
if should_proactive_export:
    # Proactive export: discharge battery to grid
    battery_delta_kwh = -export_amount_kwh / 0.95
    grid_export_kwh = export_amount_kwh
```

### Step 2.4: Mark Forecast Entries

**Location**: `forecast_computer.py` in `compute_forecast()` loop

**Add to forecast entry**:
```python
daily_forecast.append({
    # ... existing fields ...
    "grid_charge": should_grid_charge,
    "grid_charge_boost": should_boost,
    "proactive_export": should_proactive_export,
    "export_amount_kwh": export_amount_kwh,
})
```

### Step 2.5: Implement Forecast-Driven Export Control

**Location**: `computation_engine.py` in `_compute_active_mode()`

**Add after grid charging logic**:
```python
# Proactive export (follow forecast plan)
if forecast_entry.get("proactive_export"):
    export_amount = forecast_entry.get("export_amount_kwh", 0.0)
    
    # Calculate backup_reserve to control discharge rate
    # Export amount = current SOC - target reserve
    current_kwh = data.soc / 100 * BATTERY_CAPACITY_KWH
    target_reserve_pct = max(
        10,
        int((current_kwh - export_amount) / BATTERY_CAPACITY_KWH * 100)
    )
    
    data.active_mode = BatteryMode.SPIKE_DISCHARGE
    _LOGGER.info(
        "Forecast-driven: PROACTIVE_EXPORT at %s, amount=%.2f kWh, reserve=%d%%",
        now_dt.strftime("%H:%M"),
        export_amount,
        target_reserve_pct
    )
    return
```

**Note**: Need to extend `BatteryController.set_force_discharge()` to accept dynamic `backup_reserve` parameter.

### Step 2.6: Extend Battery Controller

**Location**: `battery_controller.py`

**Modify method signature**:
```python
async def set_force_discharge(
    self, data: CoordinatorData, dry_run: bool = False,
    backup_reserve: int | None = None
) -> bool:
    """Set battery to force discharge mode (autonomous, reserve=10).
    
    Args:
        data: Coordinator data
        dry_run: If True, don't issue commands
        backup_reserve: Optional override for backup reserve percentage
                     (default: 10)
    """
```

**Modify implementation**:
```python
reserve = backup_reserve if backup_reserve is not None else 10

# ... rest of method with dynamic reserve ...

if not await self._set_backup_reserve(reserve):
    _LOGGER.error("Aborting force discharge mode: Failed to set backup reserve")
    return False
```

## Testing Strategy

### Test Scenario 1: Grid Charging

**Setup**:
- Current time: 10:00 AM
- Current SOC: 60%
- Battery target: 100%
- Buy price: $0.15
- Cheap threshold: $0.20
- Solar: Sufficient to reach target

**Expected**:
1. Forecast shows `grid_charge=True` at 10:00-12:00
2. Mode switches to GRID_CHARGING at 10:00 AM
3. Battery reaches 100% by 12:00 PM
4. No grid imports after 12:00 PM

### Test Scenario 2: Proactive Export

**Setup**:
- Current time: 11:00 AM
- Current SOC: 80%
- Battery target: 100%
- Solar forecast: 25 kWh remaining
- Consumption: 8 kWh remaining
- FIT forecast: $0.08 → $0.05 → -$0.02 (negative at 2 PM)

**Expected**:
1. Forecast shows `proactive_export=True` at 11:30 AM-1:30 PM
2. Mode switches to SPIKE_DISCHARGE at 11:30 AM
3. Battery exports ~6 kWh before negative prices
4. Solar recharges battery to ~95%
5. Minimal exports at negative prices

### Test Scenario 3: Fallback

**Setup**:
- Forecast unavailable (empty `daily_forecast`)
- Current price: $0.15 (cheap)
- SOC: 50%
- Target: 100%

**Expected**:
1. `_get_forecast_entry_for_now()` returns `None`
2. Fallback to existing price-based logic
3. Mode switches to GRID_CHARGING (same as current behavior)

## Benefits

| Aspect | Before | After |
|---------|--------|-------|
| Grid charging logic | 2 places, divergent | 1 place, consistent |
| Forecast vs control | Can disagree | Always agree |
| Debugging | Which is correct? | Forecast = truth |
| Maintenance | Update 2 places | Update 1 place |
| Proactive export | Not possible | Follows same pattern |
| Test coverage | Complex | Simpler |

## Legacy Planner vs Optimizer Ownership

The DP optimizer (Issue #403) runs alongside the legacy planner. This section clarifies ownership boundaries.

### Control Modes

| Mode | Legacy Planner | Optimizer | Battery Control |
|------|---------------|-----------|-----------------|
| `shadow` | Authoritative | Runs for comparison | Legacy |
| `assist` | Authoritative | Provides recommendations | Legacy |
| `active` | Fallback | Primary decision-maker | Optimizer |

### Decision Flow

```
Coordinator Cycle:
1. DP Optimizer (DPPlanner) → produces optimizer_result with decisions
2. Safety Gate → validates optimizer can control
3. Apply Plan → maps optimizer actions to battery modes
4. [fallback] SELF_CONSUMPTION → used if optimizer fails any check
```

### Optimizer Control Path

The DP optimizer is the sole control path:

1. **Safety gate validates** all prerequisites before applying
2. **Fallback to SELF_CONSUMPTION** occurs if:
   - Last solve failed
   - Slot alignment is invalid
   - No decisions available
   - In cooldown after repeated failures
   - Forecast data is stale

3. **Manual overrides** bypass optimizer:
   - User-initiated mode changes
   - Button handlers (force charge, etc.)
   - Error recovery

### Error Recovery

- **Optimizer failure**: Falls back to SELF_CONSUMPTION
- **Repeated failures**: Cooldown period prevents rapid re-attempts
- **Manual override**: User can always control via HA UI

## Risks and Mitigations

| Risk | Mitigation |
|------|-------------|
| Forecast unavailable at startup | Fallback to existing logic |
| Forecast entry not found for current time | Fallback to existing logic |
| Mode transition during forecast change | Skip recompute if in transition |
| Battery controller doesn't support dynamic reserve | Extend method signature |
| Export timing conflicts with grid charging | Order checks in `_compute_active_mode()` |

## References

- `ARCHITECTURE.md` - Overall system architecture
- `CHANGE_DETECTION.md` - Change detection system design