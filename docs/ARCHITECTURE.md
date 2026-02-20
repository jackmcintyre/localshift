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
   - Simulates 24-hour battery behavior with hybrid granularity
   - Near-term (2 h): 24 × 5-minute slots for accurate current-period decisions
   - Long-term (22 h): 88 × 15-minute slots for planning further ahead
   - Models solar, consumption, grid charging, and proactive exports
   - Provides `daily_forecast` with 112 entries; each entry carries `slot_interval_minutes`

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

The `daily_forecast` list contains 112 entries: 24 × 5-minute near-term slots followed by 88 × 15-minute long-term slots. The `slot_interval_minutes` field identifies the granularity of each entry.

### Hybrid Timescale Architecture

The forecast system uses a **hybrid timescale** approach that balances accuracy and efficiency:

**Design Rationale:**
- **Near-term (0-2h):** 24 × 5-minute slots for high-accuracy decisions
  - Matches Amber 5-minute pricing granularity
  - Ensures "now" is always covered in forecast
  - Critical for grid charging and export decisions
- **Long-term (2-24h):** 88 × 15-minute slots for efficient planning
  - Sufficient granularity for forward planning
  - Reduces computational complexity
  - Matches original 15-minute design for consistency

**Total Coverage:** 24×5min + 88×15min = 120min + 1320min = 1440min = 24 hours (112 slots)

**Critical Bug Fix:**
Prior to this implementation, helper functions (`_find_battery_fill_point`, `_calculate_solar_energy_between_slots`) assumed uniform 15-minute slots throughout the entire 24-hour forecast. This caused SOC accumulation to be calculated **3× too fast** in the near-term window (0-2h), leading to:
- Battery fill point predicted 2-3 hours too early
- Grid charging decisions incorrectly delayed
- System predicting rapid charging while battery actually draining

**Solution Implemented:**
All helper functions now use the same hybrid timescale as the main forecast loop:
- `_find_battery_fill_point()` - Returns elapsed minutes using hybrid loops
- `_calculate_solar_energy_between_slots()` - Uses elapsed minutes parameters
- `_should_proactive_export_at_slot()` - Accepts `current_elapsed_minutes` and `fill_point_elapsed_minutes`

**Solar Retrieval:**
- Near-term (5-min): `get_solar_for_5min_slot()` returns 1/6 of 30-min Solcast period
- Long-term (15-min): `get_solar_for_15min_slot()` returns 1/2 of 30-min Solcast period

**Time-Based Comparisons:**
Helper functions return elapsed minutes rather than slot offsets, enabling clean time-based comparisons that are agnostic to slot duration:

```python
# Fill point calculation returns minutes
fill_point_elapsed_minutes = self._find_battery_fill_point(...)

# Main loop calculates elapsed time
elapsed_minutes = (slot_start - base_slot).total_seconds() / 60

# Comparison is duration-based, not slot-based
if elapsed_minutes >= fill_point_elapsed_minutes:
    # Block export - battery would fill before we can use more solar
```

This architecture ensures accurate near-term decisions while maintaining computational efficiency for long-term planning.

```python
daily_forecast = [
    # Near-term entry (5-min slot, first 2 h)
    {
        "hour": 10,
        "minute": 0,
        "timestamp": "2026-02-16T10:00:00+11:00",
        "slot_interval_minutes": 5,          # 5 for near-term, 15 for long-term
        "predicted_soc": 85.5,
        "solar_kwh": 0.125,                  # 1/6 of 30-min Solcast period
        "consumption_kwh": 0.042,            # load_kw × (5/60)
        "net_kwh": 0.083,
        "grid_import_kwh": 0.000,
        "grid_export_kwh": 0.000,
        "grid_charge": True,
        "grid_charge_boost": False,
        "proactive_export": False,
        "export_amount_kwh": 0.0,
    },
    # ... 23 more 5-min entries ...
    # Long-term entry (15-min slot, remaining 22 h)
    {
        "hour": 12,
        "minute": 0,
        "timestamp": "2026-02-16T12:00:00+11:00",
        "slot_interval_minutes": 15,
        "predicted_soc": 92.0,
        "solar_kwh": 0.750,                  # 1/2 of 30-min Solcast period
        "consumption_kwh": 0.125,            # load_kw × (15/60)
        "net_kwh": 0.625,
        "grid_import_kwh": 0.000,
        "grid_export_kwh": 0.000,
        "grid_charge": False,
        "grid_charge_boost": False,
        "proactive_export": False,
        "export_amount_kwh": 0.0,
    },
    # ... 87 more 15-min entries
]
```

### Mode Decision Flow

```python
# In computation_engine.py _compute_active_mode()

# 1. Find the most-recent forecast entry whose timestamp ≤ now.
#    Granularity-agnostic: works for 5-min and 15-min slots alike.
forecast_entry = _get_forecast_entry_for_now(data, now_dt)

if forecast_entry:
    # Grid charging (boost takes priority over normal)
    if forecast_entry.get("grid_charge_boost") and grid_import_kwh > threshold:
        active_mode = BatteryMode.BOOST_CHARGING
        return
    if forecast_entry.get("grid_charge") and grid_import_kwh > threshold:
        active_mode = BatteryMode.GRID_CHARGING
        return

    # Proactive export (note: `if`, not `elif` — evaluated independently
    # even when grid_charge=True but no import is available)
    if forecast_entry.get("proactive_export"):
        active_mode = BatteryMode.PROACTIVE_EXPORT
        return

# 2. Fallback to spike discharge, demand block, or self-consumption
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

## Coordinator Event Loop

### State Change Handling

On every external entity state change, the coordinator:

1. Reads raw entity state immediately (`_read_all_external_state`) so sensor entities reflect the new value without waiting for the async task.
2. Notifies HA listeners synchronously for fast UI updates.
3. Queues an async evaluate task — does NOT compute derived values here.

Inside the async evaluate task (`evaluate_state_machine`), while holding the `_evaluate_lock`:

4. Re-reads raw state to get the latest post-transition hardware values.
5. Runs `compute_derived_values()` (including forecast recompute if needed).
6. Notifies HA listeners again with fully-derived values.
7. Applies debounce + executes transition if needed.
8. `try/finally` ensures listener notification always fires regardless of which code path returns.

This design eliminates the race condition where a queued evaluation used pre-transition stale state and could immediately revert a transition.

### Periodic Tick Handling

Every minute:
1. Reads raw state.
2. Runs cost accumulation synchronously (needs raw state, no lock needed).
3. Queues an async evaluate task (same lock-protected flow as above).

## State Machine Reliability

### Debounce Timer Behaviour

Debounce timers for price-driven modes are reset whenever the desired mode changes away from a mode. This prevents oscillating prices from accumulating time toward the debounce without the mode being continuously desired:

- Timer for `GRID_CHARGING` starts when it first becomes desired.
- If the mode flip-flops to `SELF_CONSUMPTION` and back, the timer resets.
- Full 5-minute debounce is always served from a continuous period of stable desire.

### Health Check Cooldown

The health check runs every minute to detect hardware state drift. If a mismatch is found, correction commands are only re-issued if at least 5 minutes have elapsed since the last correction (`_MIN_CORRECTION_INTERVAL`). This prevents command spam during the 15–30 second window when Teslemetry's cloud state lags behind a legitimate transition.

### Validation Timeout

After issuing transition commands, the system polls for hardware confirmation for up to **10 seconds** (reduced from 20 seconds). The "operation_mode matches → success" early-exit logic ensures fast confirmation when Teslemetry responds promptly. The reduction lowers the maximum worst-case `in_mode_transition` lock time from ~40 s to ~25 s per transition.

## Risks and Mitigations

| Risk | Mitigation |
|------|-------------|
| Forecast cache becomes stale | 1-minute backup timer |
| Forecast unavailable at startup | Always recompute if `_last_soc < 0` |
| Mode transition during forecast recompute | Skip recompute if `in_mode_transition` |
| Forecast and control diverge | Single source of truth design |
| Solar forecast changes undetected | 1-minute timer catches all changes |

## Future Enhancements

1. **Dynamic thresholds**: Auto-tune change detection thresholds
2. **Multi-battery support**: Extend for multiple Powerwalls
3. **Cost optimization**: Goal-seeking algorithm for maximum savings

## Weather Correlation (Issue #61)

The integration includes a weather-aware consumption prediction system using a degree-day model that learns the correlation between temperature and household load.

### Component: WeatherCorrelation (`weather_correlation.py`)

The `WeatherCorrelation` class manages:
- Loading/saving learned coefficients to HA storage
- Learning from temperature/load observations
- Predicting load adjustments based on temperature forecasts

### Degree-Day Model

The model learns separate coefficients for each hour of the day:

| Coefficient | Description |
|-------------|-------------|
| **Base load** | Minimum load at mild temperatures (18-24°C band) |
| **Cooling coefficient** | Additional kW per °C above cooling threshold (default 24°C) |
| **Heating coefficient** | Additional kW per °C below heating threshold (default 18°C) |

### How It Works

1. **Learning Phase**: The system observes temperature and load pairs, updating hourly coefficients using a moving average approach
2. **Prediction Phase**: When forecasting consumption, the system applies learned coefficients based on forecasted temperatures
3. **Confidence Levels**: Based on sample count:
   - Low: < 7 samples
   - Medium: 7-30 samples
   - High: 30+ samples

### Configuration

Weather correlation is configured via the integration options:
- **Weather Entity**: Home Assistant weather entity providing temperature forecasts
- **Cooling Threshold**: Temperature above which cooling load increases (default 24°C)
- **Heating Threshold**: Temperature below which heating load increases (default 18°C)

### Benefits

- More accurate consumption predictions during temperature extremes
- Better battery SOC planning during hot/cold days
- Reduced risk of unexpected grid imports during demand windows

## Day-of-Week Aware Consumption Profiles (Issue #60)

The integration supports separate weekday and weekend consumption profiles for improved forecast accuracy in households with different daily patterns.

### How It Works

1. **Sample Separation**: Historical load samples are separated by day type (weekday: Mon-Fri, weekend: Sat-Sun)
2. **Profile Calculation**: Separate hourly averages are calculated for each profile
3. **Profile Selection**: When forecasting consumption, the appropriate profile is selected based on the target day's day-of-week
4. **Fallback**: If insufficient samples exist for day-specific profiles, the system falls back to combined averages

### Requirements for Day-Specific Profiles

- Minimum 12 hours with 3+ samples each in both weekday and weekend profiles
- If requirements not met, falls back to combined profile

### Diagnostic Fields

| Field | Description |
|-------|-------------|
| `consumption_profile_type` | "weekday_weekend" or "combined_fallback" |
| `weekday_sample_counts` | Sample counts per hour for weekdays |
| `weekend_sample_counts` | Sample counts per hour for weekends |
| `weekday_hourly_profile_kw` | Weekday hourly averages |
| `weekend_hourly_profile_kw` | Weekend hourly averages |

## References

- `FORECAST_DRIVEN_CONTROL.md` - Detailed design for forecast-driven control
- `CHANGE_DETECTION.md` - Change detection system design
- `README.md` - User-facing documentation
- `TEST_SCENARIOS.md` - Test scenarios and validation