# Load Shifting Guide: Using Excess Solar Sensors

This guide explains how to create Home Assistant automations that use the LocalShift integration's excess solar sensors to optimize energy consumption.

## Overview

The LocalShift integration provides sensors that forecast excess solar production - energy that would otherwise be exported to the grid, potentially at negative feed-in tariff (FIT) prices. By consuming this excess energy with discretionary loads (AC, pool pumps, EV chargers, etc.), you can:

1. **Avoid paying to export** during negative FIT periods
2. **Pre-condition your home** (cooling/heating) using free solar energy
3. **Maximize self-consumption** of your solar production
4. **Reduce grid dependency** by using solar for flexible loads

---

## Available Sensors

### 1. `sensor.localshift_excess_solar_kwh`

**Purpose:** Forecasted excess solar energy available for discretionary loads.

| Attribute | Type | Description |
|-----------|------|-------------|
| `excess_current_hour_kwh` | float | Excess available in the current hour |
| `excess_next_2h_kwh` | float | Excess available in the next 2 hours |
| `excess_next_4h_kwh` | float | Excess available in the next 4 hours |
| `excess_until_battery_full_kwh` | float | Excess until battery reaches 100% |
| `excess_until_negative_fit_kwh` | float | Excess before negative FIT window starts |
| `time_until_battery_full_minutes` | int | Minutes until battery is full |
| `negative_fit_window_start` | datetime | When negative FIT begins (or null) |
| `negative_fit_window_duration_minutes` | int | Duration of negative FIT period |
| `can_add_load_now` | bool | **Critical** - Is it safe to add load? |
| `safe_additional_load_kw` | float | Max kW that can be safely added |
| `forecast_confidence` | string | low/medium/high |

### 2. `sensor.localshift_load_shift_signal`

**Purpose:** Simple actionable signal for automations.

| State Value | Meaning | Action |
|-------------|---------|--------|
| `INCREASE_LOAD` | Excess solar available | Turn on discretionary loads |
| `MAINTAIN_LOAD` | Current balance is good | No changes needed |
| `REDUCE_LOAD` | Risk of grid charging | Turn off discretionary loads |
| `HOLD` | Demand window or uncertain | Don't change anything |

| Attribute | Type | Description |
|-----------|------|-------------|
| `recommended_additional_kw` | float | Suggested load change (+ or -) |
| `recommended_duration_minutes` | int | How long to maintain the change |
| `signal_reason` | string | Human-readable explanation |
| `signal_confidence` | string | low/medium/high |
| `current_excess_rate_kw` | float | Current excess generation rate |
| `grid_charge_risk` | bool | Would adding load trigger grid charging? |
| `time_until_signal_change_minutes` | int | When signal might change |

### 3. `binary_sensor.localshift_excess_solar_available`

**Purpose:** Simple ON/OFF trigger for basic automations.

- **ON:** Excess solar is available and it's safe to add load
- **OFF:** No excess available or not safe to add load

---

## Critical Concept: Avoiding Grid Charging

The most important aspect of load shifting is ensuring that adding discretionary load **does not cause the system to grid charge later**. 

### The Problem

If you turn on your AC because there's excess solar *right now*, but the forecast shows you'll need that energy later:
1. Battery doesn't fully charge from solar
2. System needs to grid charge before demand window
3. You pay for electricity you could have stored for free

### The Solution

Always check `can_add_load_now` before adding load:

```yaml
condition:
  - condition: state
    entity_id: sensor.localshift_excess_solar_kwh
    attribute: can_add_load_now
    state: true
```

The integration calculates this by simulating the forecast with and without additional load to ensure battery targets are still met.

---

## Air Conditioning Examples

### Example 1: Simple Pre-cooling

Turn on AC when excess solar is available, turn off when it's not.

```yaml
automation:
  - alias: "Pre-cool with excess solar - ON"
    description: "Start pre-cooling when excess solar available"
    trigger:
      - platform: state
        entity_id: binary_sensor.localshift_excess_solar_available
        to: "on"
    condition:
      - condition: numeric_state
        entity_id: sensor.indoor_temperature
        above: 23  # Only if it's warm
    action:
      - service: climate.set_hvac_mode
        target:
          entity_id: climate.living_room
        data:
          hvac_mode: cool
      - service: climate.set_temperature
        target:
          entity_id: climate.living_room
        data:
          temperature: 22

  - alias: "Pre-cool with excess solar - OFF"
    description: "Stop pre-cooling when excess drops"
    trigger:
      - platform: state
        entity_id: binary_sensor.localshift_excess_solar_available
        to: "off"
        for:
          minutes: 5  # Debounce
    action:
      - service: climate.set_hvac_mode
        target:
          entity_id: climate.living_room
        data:
          hvac_mode: "off"
```

### Example 2: Smart Pre-cooling Before Negative FIT

Pre-cool aggressively before a negative FIT window, accounting for thermal mass.

```yaml
automation:
  - alias: "Aggressive pre-cool before negative FIT"
    description: "Use excess to pre-cool before negative FIT window"
    trigger:
      - platform: state
        entity_id: sensor.localshift_load_shift_signal
        to: "INCREASE_LOAD"
    condition:
      # Negative FIT coming within 4 hours
      - condition: template
        value_template: >
          {{ state_attr('sensor.localshift_excess_solar_kwh', 'negative_fit_window_start') is not none }}
      # At least 5kWh excess available
      - condition: numeric_state
        entity_id: sensor.localshift_excess_solar_kwh
        attribute: excess_until_negative_fit_kwh
        above: 5
      # Safe to add load
      - condition: state
        entity_id: sensor.localshift_excess_solar_kwh
        attribute: can_add_load_now
        state: true
      # AC capacity check
      - condition: numeric_state
        entity_id: sensor.localshift_excess_solar_kwh
        attribute: safe_additional_load_kw
        above: 2.0
    action:
      - service: climate.set_temperature
        target:
          entity_id: climate.living_room
        data:
          temperature: 20  # Aggressive pre-cool
      - service: notify.mobile_app
        data:
          message: >
            Pre-cooling started. {{ state_attr('sensor.localshift_excess_solar_kwh', 
            'excess_until_negative_fit_kwh') | round(1) }}kWh excess before negative FIT at 
            {{ state_attr('sensor.localshift_excess_solar_kwh', 'negative_fit_window_start') }}
```

### Example 3: Dynamic Temperature Based on Excess

Adjust AC setpoint based on how much excess is available.

```yaml
automation:
  - alias: "Dynamic pre-cool temperature"
    description: "Adjust cooling based on excess solar amount"
    trigger:
      - platform: state
        entity_id: sensor.localshift_excess_solar_kwh
    condition:
      - condition: state
        entity_id: sensor.localshift_load_shift_signal
        state: "INCREASE_LOAD"
    action:
      - variables:
          excess: "{{ state_attr('sensor.localshift_excess_solar_kwh', 'excess_next_2h_kwh') | float }}"
          # More excess = more aggressive cooling
          target_temp: >
            {% if excess > 8 %}
              19
            {% elif excess > 5 %}
              20
            {% elif excess > 3 %}
              21
            {% else %}
              22
            {% endif %}
      - service: climate.set_temperature
        target:
          entity_id: climate.living_room
        data:
          temperature: "{{ target_temp }}"
```

### Example 4: Respond to REDUCE_LOAD Signal

When the system detects grid charging risk, reduce AC load.

```yaml
automation:
  - alias: "Reduce AC on grid charge risk"
    trigger:
      - platform: state
        entity_id: sensor.localshift_load_shift_signal
        to: "REDUCE_LOAD"
    condition:
      - condition: state
        entity_id: climate.living_room
        attribute: hvac_action
        state: "cooling"
    action:
      - service: climate.set_temperature
        target:
          entity_id: climate.living_room
        data:
          temperature: 25  # Reduce cooling load
      - service: notify.mobile_app
        data:
          message: >
            AC setpoint raised to reduce load. 
            Reason: {{ state_attr('sensor.localshift_load_shift_signal', 'signal_reason') }}
```

---

## Pool Pump Examples

### Example 5: Run Pool Pump on Excess Solar

```yaml
automation:
  - alias: "Pool pump on excess solar"
    trigger:
      - platform: state
        entity_id: sensor.localshift_load_shift_signal
        to: "INCREASE_LOAD"
    condition:
      # Pool pump needs ~1.5kW
      - condition: numeric_state
        entity_id: sensor.localshift_excess_solar_kwh
        attribute: safe_additional_load_kw
        above: 1.5
      # At least 2 hours of excess (4kWh for 2h @ 2kW)
      - condition: numeric_state
        entity_id: sensor.localshift_excess_solar_kwh
        attribute: excess_next_4h_kwh
        above: 4
      # Hasn't run today
      - condition: state
        entity_id: input_boolean.pool_pump_ran_today
        state: "off"
    action:
      - service: switch.turn_on
        target:
          entity_id: switch.pool_pump
      - delay:
          hours: 2
      - service: switch.turn_off
        target:
          entity_id: switch.pool_pump
      - service: input_boolean.turn_on
        target:
          entity_id: input_boolean.pool_pump_ran_today
```

---

## EV Charging Examples

### Example 6: Opportunistic EV Charging

```yaml
automation:
  - alias: "EV charge on excess solar"
    trigger:
      - platform: state
        entity_id: binary_sensor.localshift_excess_solar_available
        to: "on"
    condition:
      - condition: state
        entity_id: binary_sensor.ev_connected
        state: "on"
      - condition: numeric_state
        entity_id: sensor.ev_battery_level
        below: 80
      # EV charger needs at least 1.4kW (6A @ 240V)
      - condition: numeric_state
        entity_id: sensor.localshift_excess_solar_kwh
        attribute: safe_additional_load_kw
        above: 1.4
    action:
      - service: number.set_value
        target:
          entity_id: number.ev_charge_current
        data:
          # Set current based on available excess (240V assumption)
          value: >
            {% set excess_kw = state_attr('sensor.localshift_excess_solar_kwh', 'safe_additional_load_kw') %}
            {% set max_amps = (excess_kw * 1000 / 240) | int %}
            {{ [max_amps, 32] | min }}
      - service: switch.turn_on
        target:
          entity_id: switch.ev_charger
```

---

## Hot Water Examples

### Example 7: Heat Water with Excess Solar

```yaml
automation:
  - alias: "Heat water with excess solar"
    trigger:
      - platform: state
        entity_id: sensor.localshift_load_shift_signal
        to: "INCREASE_LOAD"
    condition:
      # Hot water heater is typically 3-4kW
      - condition: numeric_state
        entity_id: sensor.localshift_excess_solar_kwh
        attribute: safe_additional_load_kw
        above: 3.5
      # Water isn't already hot
      - condition: numeric_state
        entity_id: sensor.hot_water_temperature
        below: 55
    action:
      - service: switch.turn_on
        target:
          entity_id: switch.hot_water_boost
      - wait_for_trigger:
          - platform: state
            entity_id: sensor.localshift_load_shift_signal
            to: "REDUCE_LOAD"
          - platform: numeric_state
            entity_id: sensor.hot_water_temperature
            above: 65
        timeout:
          hours: 2
      - service: switch.turn_off
        target:
          entity_id: switch.hot_water_boost
```

---

## Advanced: Multi-Load Prioritization

When you have multiple discretionary loads, prioritize them based on available excess.

### Example 8: Load Priority Queue

```yaml
automation:
  - alias: "Manage discretionary loads by priority"
    trigger:
      - platform: state
        entity_id: sensor.localshift_excess_solar_kwh
    action:
      - variables:
          safe_kw: "{{ state_attr('sensor.localshift_excess_solar_kwh', 'safe_additional_load_kw') | float }}"
          signal: "{{ states('sensor.localshift_load_shift_signal') }}"
      - choose:
          # Priority 1: Hot water (3.5kW) - if we have lots of excess
          - conditions:
              - "{{ signal == 'INCREASE_LOAD' }}"
              - "{{ safe_kw >= 3.5 }}"
              - "{{ states('sensor.hot_water_temperature') | float < 55 }}"
            sequence:
              - service: switch.turn_on
                target:
                  entity_id: switch.hot_water_boost

          # Priority 2: Pool pump (1.5kW) - medium excess
          - conditions:
              - "{{ signal == 'INCREASE_LOAD' }}"
              - "{{ safe_kw >= 1.5 }}"
              - "{{ is_state('input_boolean.pool_pump_ran_today', 'off') }}"
            sequence:
              - service: switch.turn_on
                target:
                  entity_id: switch.pool_pump

          # Priority 3: AC pre-cool (2kW) - any excess
          - conditions:
              - "{{ signal == 'INCREASE_LOAD' }}"
              - "{{ safe_kw >= 2.0 }}"
              - "{{ states('sensor.indoor_temperature') | float > 23 }}"
            sequence:
              - service: climate.set_temperature
                target:
                  entity_id: climate.living_room
                data:
                  temperature: 22

          # REDUCE_LOAD: Turn off in reverse priority
          - conditions:
              - "{{ signal == 'REDUCE_LOAD' }}"
            sequence:
              - service: climate.turn_off
                target:
                  entity_id: climate.living_room
              - service: switch.turn_off
                target:
                  entity_id: switch.pool_pump
              # Keep hot water if it's almost done
              - condition: numeric_state
                entity_id: sensor.hot_water_temperature
                below: 50
              - service: switch.turn_off
                target:
                  entity_id: switch.hot_water_boost
```

---

## Troubleshooting

### Signal Changes Too Frequently

Add debouncing with `for:` in triggers:

```yaml
trigger:
  - platform: state
    entity_id: sensor.localshift_load_shift_signal
    to: "INCREASE_LOAD"
    for:
      minutes: 5
```

### AC Cycling On/Off

Use hysteresis in conditions:

```yaml
condition:
  - condition: numeric_state
    entity_id: sensor.localshift_excess_solar_kwh
    attribute: excess_next_2h_kwh
    above: 4  # Higher threshold to start
```

And a lower threshold to stop:

```yaml
trigger:
  - platform: numeric_state
    entity_id: sensor.localshift_excess_solar_kwh
    attribute: excess_next_2h_kwh
    below: 1  # Lower threshold to stop
```

### Load Started But Grid Charging Occurred

This shouldn't happen if you check `can_add_load_now`, but if it does:

1. Check if forecast changed significantly after load started
2. Consider adding `grid_charge_risk` monitoring
3. Add automation to respond to REDUCE_LOAD signal

---

## Best Practices

1. **Always check `can_add_load_now`** before adding significant loads
2. **Use `safe_additional_load_kw`** to ensure you don't add more load than available excess
3. **Respond to `REDUCE_LOAD`** signals promptly
4. **Add debouncing** to prevent rapid cycling
5. **Log your automations** to understand behavior patterns
6. **Start conservative** with thresholds, then adjust based on observation
7. **Consider thermal mass** - pre-cool/heat before you need it, not during

---

## Sensor Update Frequency

The excess solar sensors update every time the coordinator refreshes (typically every 30-60 seconds). The forecast recalculates every 15 minutes when new Solcast data arrives.

Plan automations with these timing considerations:
- Real-time reactions: Use binary sensor or signal changes
- Scheduled actions: Use forecast attributes (excess_next_2h, etc.)
- Long-running loads: Check signal periodically and respond to REDUCE_LOAD