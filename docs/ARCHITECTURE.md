# LocalShift Integration - System Architecture

## Overview

The LocalShift integration optimizes battery charging/discharging based on:
- Amber Electric spot prices (5-minute intervals)
- Solcast solar forecasts (30-minute intervals)
- Tesla Powerwall state (via Teslemetry)
- Household consumption patterns

## System Design Goals

The architecture was designed to solve several problems from the original YAML-based automation:

1. **Eliminate "stuck state" bugs** — The YAML automations had edge cases where the battery could get stuck in a state. A state machine evaluates on every change.

2. **Single source of truth** — All mode decisions flow through one priority chain, not spread across 18 independent automations.

3. **Testable** — Python code is far easier to test than YAML automations.

4. **Configurable** — No more editing YAML for threshold changes. All options available via UI.

5. **Observable** — Extensive sensors and logging for debugging.

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        HOME ASSISTANT CORE                                   │
│                                                                              │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │                    LocalShift Integration                        │  │
│  │                                                                       │  │
│  │  ┌─────────────┐    ┌─────────────┐    ┌─────────────────────────┐  │  │
│  │  │  Config    │    │   Entity   │    │   Coordinator            │  │  │
│  │  │  Flow      │───▶│   Platform │───▶│   (AmberPowerwall       │  │  │
│  │  │            │    │   (sensor, │    │    Coordinator)          │  │  │
│  │  │            │    │    binary,  │    │                         │  │  │
│  │  │            │    │    switch, │    │   - Subscribes to      │  │  │
 │            │    │    number,│  │  │    │     external entities   │  │  │
│  │  │            │    │    button) │    │   - 1-min periodic     │  │  │
│  │  └─────────────┘    └─────────────┘    │   - Coordinates       │  │  │
│  │                                          │     all modules        │  │  │
│  │                                          └───────────┬───────────┘  │  │
│  │                                                      │              │  │
│  │          ┌───────────────────────────────────────────┼──────────────┤  │
│  │          │                                           │              │  │
│  │          ▼                                           ▼              │  │
│  │  ┌─────────────────────────────────────────────────────────────────┐│  │
│  │  │                      Internal Modules                          ││  │
│  │  │                                                                 ││  │
│  │  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  ││  │
│  │  │  │  State      │  │ Computation │  │    State Machine     │  ││  │
│  │  │  │  Reader     │─▶│   Engine    │─▶│    (evaluates        │  ││  │
│  │  │  │             │  │             │  │     desired mode)     │  ││  │
│  │  │  │  Reads      │  │  Computes   │  │                      │  ││  │
│  │  │  │  external   │  │  derived    │  │  ┌──────────────────┴┐ ││  │
│  │  │  │  entities   │  │  values     │  │  │                   │ ││  │
│  │  │  │             │  │             │  │  ▼                   │ ││  │
│  │  │  └──────────────┘  └──────┬───────┘  │  Battery Controller │ ││  │
│  │  │                            │          │  (executes commands)│ ││  │
│  │  │                            │          └──────────────────────┘  ││  │
│  │  │                            │                                   ││  │
│  │  │                            ▼                                   ││  │
│  │  │              ┌─────────────────────────┐                       ││  │
│  │  │              │  Forecast Computer      │                       ││  │
│  │  │              │  (15-min simulation)   │                       ││  │
│  │  │              └─────────────────────────┘                       ││  │
│  │  └─────────────────────────────────────────────────────────────────┘│  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                                                                              │
│  External Integrations (read):                                               │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐                        │
│  │  Teslemetry │  │   Amber     │  │   Solcast  │                        │
│  │             │  │   Electric  │  │            │                        │
│  │  Powerwall  │◀─│   Pricing  │◀─│   Solar    │                        │
│  │   control   │  │   forecasts │  │  forecasts │                        │
│  └──────┬──────┘  └──────┬──────┘  └─────┬──────┘                        │
│         │                 │                  │                               │
│         ▼                 ▼                  ▼                               │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                     TESLA POWERWALL HARDWARE                        │    │
│  │                                                                     │    │
│  │   ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌───────────────┐    │    │
│  │   │   Solar  │  │   Grid   │  │ Battery  │  │    Home      │    │    │
│  │   │  Panels  │  │  Import/ │  │  (13.5  │  │   Load       │    │    │
│  │   │          │  │  Export  │  │   kWh)   │  │              │    │    │
│  │   └──────────┘  └──────────┘  └──────────┘  └───────────────┘    │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Current Architecture

### Component Responsibilities

```
┌─────────────────────────────────────────────────────────────┐
│                    coordinator.py                            │
│  - Subscribes to entity state changes                      │
│  - Coordinates all modules                                  │
│  - 1-minute periodic tick                                   │
└──────────────────┬──────────────────────────────────────────┘
                   │
    ┌──────────────┼──────────────┬──────────────┐
    │              │              │              │
    ▼              ▼              ▼              ▼
┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐
│  state  │  │ compute │  │ battery │  │  state  │
│  reader │  │  engine │  │controller│  │ machine │
└─────────┘  └─────────┘  └─────────┘  └─────────┘
                                    │
                            ┌───────┴───────┐
                            │  forecast    │
                            │  computer    │
                            └───────────────┘
```

### Data Flow

1. **State Reader** (`state_reader.py`)
   - Reads Teslemetry entities (SOC, operation mode, grid/battery/solar/load power)
   - Reads Amber entities (prices, forecasts, spike status)
   - Reads Solcast entities (solar forecasts)
   - Populates `CoordinatorData`

2. **Computation Engine** (`computation_engine.py`)
   - Computes derived values (directional power, mode detection, forecasts)
   - Delegates to `ForecastComputer` for 15-minute SOC simulation
   - Determines `active_mode` based on all conditions

3. **State Machine** (`state_machine.py`)
   - Compares `active_mode` with `commanded_mode`
   - Applies debounce timers (2-5 minutes depending on transition)
   - Executes mode transitions via `BatteryController`

4. **Battery Controller** (`battery_controller.py`)
   - Issues commands to Teslemetry (operation mode, backup reserve, export mode)
   - Validates transitions completed successfully

5. **Forecast Computer** (`forecast_computer.py`)
   - Simulates 24-hour battery behavior with 15-minute granularity
   - Models solar, consumption, grid charging, and exports
   - Provides `daily_forecast` with 96 entries (one per 15-min slot)

## Current Architecture Issues

### Issue 1: Duplicate Grid Charging Logic

**Location 1: `forecast_computer.py` (lines 200-320)**
- Simulates grid charging for forecast
- Sets `should_grid_charge` and `should_boost` flags

**Location 2: `computation_engine.py` (lines 680-730)**
- Decides WHEN to grid charge (sets `active_mode`)
- Uses different logic (current state vs forecast)

**Problem:** Independent logic can diverge

### Issue 2: No Change Detection for Forecasts

**Current behavior:**
- Forecast recomputed on EVERY state change
- Forecast recomputed on EVERY 1-minute tick
- Most changes don't materially affect forecast

**Problem:** Unnecessary computation (~70% waste)

### Issue 3: No Proactive Export Logic

**Current behavior:**
- Battery only exports when full + excess solar
- No logic to export before negative prices

**Problem:** Missed revenue, paying to export

## Target Architecture

### Single Source of Truth

```
Decision Logic (single source)
    └─> forecast_computer.py
        ├─> _should_grid_charge_at_slot()
        └─> _should_proactive_export_at_slot()

Forecast uses decision logic
    └─> Simulates behavior for 96 slots
    └─> Marks planned actions in forecast data

Control follows forecast plan
    └─> Checks forecast for current time slot
    └─> Executes mode transitions as planned
```

### Key Principles

1. **Forecast as Plan**: The forecast IS the plan for battery behavior
2. **Control Follows Plan**: Control logic just follows what forecast says
3. **Single Decision Point**: Only one place makes charging/exporting decisions
4. **Change Detection**: Only recompute forecast when inputs change significantly
5. **Extensibility**: New features follow the same pattern
6. **Spot Price Priority**: Current spot prices preferred over forecasts for decisions

### Price Decision Logic

Both grid charging (buy) and proactive export (sell) decisions use a **spot price first** approach:

- **Grid Charging** (`_should_grid_charge_at_slot()`): Uses current spot buy price (`general_price`) as primary signal, falls back to forecast price when spot unavailable
- **Proactive Export** (`_should_proactive_export_at_slot()`): Uses current spot feed-in price (`feed_in_price`) as primary signal, falls back to forecast when spot unavailable

This ensures the system captures real-time price opportunities rather than relying solely on forecasts which may be outdated or inaccurate.

### Benefits

| Aspect | Current | Target |
|---------|---------|---------|
| Grid charging logic | 2 places, independent | 1 place, shared |
| Forecast updates | Every change (wasteful) | On significant changes |
| Control decisions | Current state only | Forecast-driven |
| Proactive exports | Not implemented | Follows same pattern |
| Maintainability | High risk of divergence | Single source of truth |
| Debugging | Hard to correlate | Forecast = plan |

## Migration Strategy

### Phase 1: Architecture Refactoring (Grid Charging)
1. Extract grid charging logic to `_should_grid_charge_at_slot()`
2. Add `grid_charge` and `grid_charge_boost` flags to forecast entries
3. Implement forecast-driven control in `_compute_active_mode()`
4. Test: behavior matches current system

### Phase 2: Change Detection
1. Add `ForecastChangeTracker` class
2. Implement `_should_recompute_forecast()` with thresholds:
   - Buy price: ANY change
   - Feed-in price: ANY change
   - SOC: ≥1% change
   - Age: >1 minute (backup)
3. Update computation flow with caching
4. Test: forecast regeneration is efficient

### Phase 3: Proactive Export ✅ IMPLEMENTED
1. ✅ Add `_should_proactive_export_at_slot()` decision logic
2. ✅ Add `proactive_export` and `export_amount_kwh` to forecast
3. ✅ Implement forecast-driven export switching
4. ✅ Use PROACTIVE_EXPORT mode with **dynamic throttling reserve**
5. ✅ Test: exports before negative prices

#### Proactive Export Safety Features

**Overnight Drain Simulation:**
- `_simulate_overnight_drain_after_export()` simulates battery drain from export slot until solar production starts
- Blocks exports that would cause overnight minimum SOC to drop below `export_min_soc_pct`
- Returns `solar_found_in_forecast` flag to detect late forecast slots without solar visibility

**Late Forecast Slot Protection:**
- Exports blocked when solar cannot be found in remaining forecast data
- This prevents exports in last 6-8 hours of 24h forecast window where overnight simulation is unreliable

**Dynamic Throttling:**
- Reserve set to `max(4, SOC - 5)` instead of fixed minimum
- Limits each export session to ~5% of battery capacity (~0.675 kWh)
- Creates "trickle export" behavior instead of full 8kW discharge
- Forecast incorporates throttling to show realistic export amounts

## Component Details

### Forecast Data Structure

```python
daily_forecast = [
    {
        "hour": 10,
        "minute": 0,
        "timestamp": "2026-02-16T10:00:00+11:00",
        "predicted_soc": 85.5,
        "solar_kwh": 0.750,
        "consumption_kwh": 0.125,
        "net_kwh": 0.625,
        "grid_import_kwh": 0.000,
        "grid_export_kwh": 0.000,
        
        # NEW: Grid charging flags
        "grid_charge": True,
        "grid_charge_boost": False,
        
        # NEW: Proactive export flags
        "proactive_export": False,
        "export_amount_kwh": 0.0,
    },
    # ... 95 more entries
]
```

### Mode Decision Flow

```python
# In computation_engine.py _compute_active_mode()

# 1. Check forecast for planned actions at current time
forecast_entry = _get_forecast_entry_for_now(data, now_dt)

if forecast_entry:
    # Grid charging
    if forecast_entry.get("grid_charge_boost"):
        active_mode = BatteryMode.BOOST_CHARGING
        return
    elif forecast_entry.get("grid_charge"):
        active_mode = BatteryMode.GRID_CHARGING
        return
    
    # Proactive export
    if forecast_entry.get("proactive_export"):
        active_mode = BatteryMode.SPIKE_DISCHARGE
        # Use controlled backup_reserve for rate limiting
        export_amount = forecast_entry.get("export_amount_kwh")
        return

# 2. Fallback: Use existing logic if forecast unavailable
# (current price-based decisions, etc.)
```

### Change Detection Flow

```python
# In computation_engine.py

class ForecastChangeTracker:
    def _should_recompute_forecast(
        data: CoordinatorData,
        now_dt: datetime
    ) -> tuple[bool, str]:
        """Check if forecast should recompute."""
        
        # Price changes (ANY change)
        if data.general_price != self._last_price:
            return True, f"price_change_{data.general_price:.2f}"
        
        if data.feed_in_price != self._last_feed_in:
            return True, f"fit_change_{data.feed_in_price:.2f}"
        
        # SOC change (1% threshold)
        soc_change = abs(data.soc - self._last_soc)
        if soc_change >= 1.0:
            return True, f"soc_change_{soc_change:.1f}%"
        
        # Age check (1-minute backup)
        if now_dt - self._last_forecast_time > timedelta(minutes=1):
            return True, "age_1min"
        
        return False, "no_change"
```

## Risks and Mitigations

| Risk | Mitigation |
|------|-------------|
| Forecast cache becomes stale | 1-minute backup timer |
| Forecast unavailable at startup | Always recompute if `_last_soc < 0` |
| Mode transition during forecast recompute | Skip recompute if `in_mode_transition` |
| Forecast and control diverge | Single source of truth design |
| Solar forecast changes undetected | 1-minute timer catches all changes |

## Future Enhancements

1. **Machine learning**: Improve consumption forecasting accuracy
2. **Weather integration**: Adjust for cloud cover predictions
3. **Dynamic thresholds**: Auto-tune change detection thresholds
4. **Multi-battery support**: Extend for multiple Powerwalls
5. **Cost optimization**: Goal-seeking algorithm for maximum savings

## References

- `FORECAST_DRIVEN_CONTROL.md` - Detailed design for forecast-driven control
- `CHANGE_DETECTION.md` - Change detection system design
- `README.md` - User-facing documentation
- `TEST_SCENARIOS.md` - Test scenarios and validation