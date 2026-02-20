# Excess Solar Load Shifting Sensors

**ID:** backlog-high-017  
**Priority:** HIGH  
**Status:** COMPLETED  
**Created:** 2026-02-17  
**Updated:** 2026-02-20

---

## Summary

Expose sensors to enable external automations to consume excess solar production before it would be exported at negative/low feed-in tariff prices.

---

## Description

When solar production exceeds consumption + battery capacity, the system currently:
1. Fills the battery to 100%
2. Exports excess to the grid (potentially at negative FIT prices)

Instead of wasting this energy or paying to export it, we want to signal external automations (like AC, pool pumps, EV chargers) to consume the excess **before** it would be exported at poor prices.

### Key Challenge

**We must not trigger grid charging to accommodate extra load.** If an automation adds load (e.g., turns on AC), and this causes the battery to need grid charging later, we've defeated the purpose. The sensors must provide:
1. How much excess is available (forecast-based)
2. Whether it's safe to add load (won't cause grid charging)
3. Load management signals (increase/reduce/hold)

---

## Affected Files

- `custom_components/localshift/coordinator_data.py` - Add excess solar fields
- `custom_components/localshift/computation_engine.py` - Calculate excess solar availability
- `custom_components/localshift/computation_engine_lib/forecast_computer.py` - Extend excess calculations
- `custom_components/localshift/sensor.py` - Add new sensors
- `custom_components/localshift/binary_sensor.py` - Add excess_solar_available binary sensor
- `docs/LOAD_SHIFTING_GUIDE.md` - Documentation for external automations

---

## Proposed Solution

### New Sensors

#### 1. `sensor.amber_powerwall_excess_solar_kwh` (Primary Forecast)

**Purpose:** Forecasted excess solar energy available for discretionary loads.

**State Value:** Total excess kWh available until battery would fill AND before negative FIT window.

**Attributes:**
```python
{
    "excess_current_hour_kwh": 1.2,       # Available excess in current hour
    "excess_next_2h_kwh": 3.5,            # Available excess in next 2 hours
    "excess_next_4h_kwh": 6.8,            # Available excess in next 4 hours
    "excess_until_battery_full_kwh": 4.2, # Excess until battery reaches 100%
    "excess_until_negative_fit_kwh": 8.1, # Excess before negative FIT window
    "time_until_battery_full_minutes": 45, # Estimated time until 100% SOC
    "negative_fit_window_start": "2026-02-17T11:30:00+11:00",  # When negative FIT begins (or null)
    "negative_fit_window_duration_minutes": 120,
    "can_add_load_now": true,             # Boolean - safe to add discretionary load
    "safe_additional_load_kw": 2.5,       # Max kW that can be added safely
    "forecast_confidence": "high",        # low/medium/high based on Solcast reliability
}
```

#### 2. `sensor.amber_powerwall_load_shift_signal` (Control Signal)

**Purpose:** Actionable signal for automations indicating what to do with discretionary loads.

**State Value:** One of: `INCREASE_LOAD`, `MAINTAIN_LOAD`, `REDUCE_LOAD`, `HOLD`

**Signal Logic:**
- `INCREASE_LOAD`: Excess solar available, battery approaching full, FIT is low/negative coming
- `MAINTAIN_LOAD`: Current balance is good, no action needed
- `REDUCE_LOAD`: Would trigger grid charging, or approaching demand window
- `HOLD`: During demand window, forecast uncertain, or system in manual override

**Attributes:**
```python
{
    "recommended_additional_kw": 2.5,     # Suggested load increase (positive) or decrease (negative)
    "recommended_duration_minutes": 120,  # How long to maintain the load change
    "signal_reason": "Excess solar: 4.2kWh available before battery full at 11:30",
    "signal_confidence": "high",
    "current_excess_rate_kw": 1.8,        # Current excess generation rate
    "grid_charge_risk": false,            # True if adding load might trigger grid charging
    "time_until_signal_change_minutes": 45, # Estimated time until signal might change
}
```

#### 3. `binary_sensor.amber_powerwall_excess_solar_available`

**Purpose:** Simple ON/OFF trigger for basic automations.

**State:** ON when:
- Solar production > household load + battery charge rate
- Battery SOC > 80% OR battery charging at max rate
- Not in demand window
- `can_add_load_now` is true

**Attributes:**
```python
{
    "current_excess_kw": 2.1,
    "battery_soc": 85,
    "battery_charging": true,
}
```

---

### Implementation Details

#### CoordinatorData Additions

```python
# Excess solar load shifting
excess_solar_available: bool = False
excess_solar_current_kw: float = 0.0
excess_solar_current_hour_kwh: float = 0.0
excess_solar_next_2h_kwh: float = 0.0
excess_solar_next_4h_kwh: float = 0.0
excess_until_battery_full_kwh: float = 0.0
excess_until_negative_fit_kwh: float = 0.0
time_until_battery_full_minutes: int = 0
negative_fit_window_start: datetime | None = None
negative_fit_window_duration_minutes: int = 0
can_add_load_now: bool = False
safe_additional_load_kw: float = 0.0
load_shift_signal: str = "HOLD"  # INCREASE_LOAD, MAINTAIN_LOAD, REDUCE_LOAD, HOLD
load_shift_recommended_kw: float = 0.0
load_shift_recommended_duration_minutes: int = 0
load_shift_reason: str = ""
load_shift_confidence: str = "low"
grid_charge_risk: bool = False
```

#### Calculation Logic in ForecastComputer

The system already calculates `forecasted_excess_kwh`. Extend this to provide:

1. **Time-windowed excess calculations:**
   ```python
   def _calculate_excess_by_window(
       self,
       forecast: list[dict],
       current_soc: float,
       windows: list[int],  # e.g., [1, 2, 4] hours
   ) -> dict[int, float]:
       """Calculate excess solar for each time window."""
   ```

2. **Battery fill time estimation:**
   ```python
   def _estimate_time_to_battery_full(
       self,
       current_soc: float,
       forecast: list[dict],
   ) -> tuple[int, float]:  # (minutes, excess_until_then)
       """Estimate when battery will reach 100% and excess available until then."""
   ```

3. **Safe load calculation:**
   ```python
   def _calculate_safe_additional_load(
       self,
       current_soc: float,
       target_soc: float,
       current_solar_kw: float,
       current_load_kw: float,
       forecast_slots: list[dict],
   ) -> float:
       """Calculate max additional load that won't trigger grid charging."""
   ```

#### Load Shift Signal Logic

```python
def _compute_load_shift_signal(self, data: CoordinatorData) -> str:
    """Determine the load shift signal based on current state and forecast."""
    
    # HOLD conditions (highest priority)
    if data.demand_window_active:
        return "HOLD", "Demand window active - maintain current loads"
    if data.manual_override:
        return "HOLD", "Manual override active"
    if not data.solcast_today:
        return "HOLD", "No solar forecast available"
    
    # REDUCE_LOAD conditions
    if data.grid_charge_risk:
        return "REDUCE_LOAD", "Current load may trigger grid charging"
    if data.soc < data.battery_target - 10:
        return "REDUCE_LOAD", "Battery below target - reduce discretionary load"
    
    # INCREASE_LOAD conditions
    if data.excess_solar_available and data.can_add_load_now:
        if data.excess_until_negative_fit_kwh > 2.0:
            return "INCREASE_LOAD", f"Excess solar: {data.excess_until_negative_fit_kwh:.1f}kWh before negative FIT"
        if data.excess_until_battery_full_kwh > 1.0:
            return "INCREASE_LOAD", f"Excess solar: {data.excess_until_battery_full_kwh:.1f}kWh before battery full"
    
    # Default
    return "MAINTAIN_LOAD", "Current balance is optimal"
```

---

## Documentation: External Automation Guide

See `docs/LOAD_SHIFTING_GUIDE.md` for complete documentation.

### Quick Start: Air Conditioning Example

```yaml
# Home Assistant automation example
automation:
  - alias: "Pre-cool house with excess solar"
    description: "Use excess solar to pre-cool before negative FIT window"
    trigger:
      - platform: state
        entity_id: sensor.amber_powerwall_load_shift_signal
        to: "INCREASE_LOAD"
    condition:
      - condition: numeric_state
        entity_id: sensor.amber_powerwall_excess_solar_kwh
        attribute: excess_next_2h_kwh
        above: 3  # At least 3kWh excess available
      - condition: numeric_state
        entity_id: sensor.amber_powerwall_excess_solar_kwh
        attribute: safe_additional_load_kw
        above: 1.5  # AC needs ~1.5-2kW
    action:
      - service: climate.set_temperature
        target:
          entity_id: climate.living_room
        data:
          temperature: 22  # Pre-cool target
      - delay:
          minutes: "{{ state_attr('sensor.amber_powerwall_load_shift_signal', 'recommended_duration_minutes') | int }}"
      - service: climate.set_temperature
        target:
          entity_id: climate.living_room
        data:
          temperature: 24  # Return to normal

  - alias: "Reduce AC when solar excess drops"
    trigger:
      - platform: state
        entity_id: sensor.amber_powerwall_load_shift_signal
        to: "REDUCE_LOAD"
    action:
      - service: climate.set_temperature
        target:
          entity_id: climate.living_room
        data:
          temperature: 26  # Reduce cooling
```

---

## Notes

- This feature is **read-only** - it provides signals but does not control external devices
- External automations are responsible for acting on the signals appropriately
- The `can_add_load_now` check is critical to prevent grid charging scenarios
- Consider adding hysteresis to prevent rapid signal changes
- The `grid_charge_risk` flag uses the existing forecast simulation to predict if adding load would require grid charging

---

## Related Items

- backlog-high-009: Solar Curtailment for Negative FIT (related - both deal with negative FIT optimization)
- backlog-high-008: Proactive Export Not Using Peak FIT Prices (completed - related FIT optimization)

---

## Acceptance Criteria

1. [ ] `sensor.amber_powerwall_excess_solar_kwh` exposes all specified attributes
2. [ ] `sensor.amber_powerwall_load_shift_signal` provides actionable signals
3. [ ] `binary_sensor.amber_powerwall_excess_solar_available` triggers correctly
4. [ ] `can_add_load_now` correctly prevents false positives that would cause grid charging
5. [ ] Documentation provides working automation examples
6. [ ] Unit tests cover edge cases (no solar, already full battery, demand window, etc.)