# Test Scenarios

Structured test cases for the Amber Powerwall automation package. Each scenario defines initial conditions, a trigger event, expected behaviour, and what to verify.

> **Format note**: Scenarios are grouped by feature area. Each has a unique ID for reference (e.g. S3.2). The `Verify` field describes what to check in HA Developer Tools or notifications. Future features (solar forecasts, weather sensors, etc.) will be added as new sections when implemented.

---

## Reference: System Modes

| Mode | operation_mode | reserve | Description |
|---|---|---|---|
| Self Consumption | `self_consumption` | `10%` | Normal — battery powers house |
| Hold | `self_consumption` | `100%` | Grid only — battery preserved, house draws from grid |
| Force Charge | `autonomous` | `100%` | Grid import — battery charges from grid |
| Force Discharge | `autonomous` | `10%` | Export — battery discharges to grid (6am-midnight only) |

## Reference: Key Thresholds

| Setting | Default | Description |
|---|---|---|
| `cheap_price_threshold` | $0.05/kWh | Below this = cheap, start charging |
| `cheap_price_deadband` | $0.03/kWh | Buffer zone above threshold |
| `cheap_charge_stop_price` | $0.08/kWh | Computed: threshold + deadband |
| `precharge_battery_threshold` | 50% | Charge if battery below this |
| `forecast_lookahead_hours` | 2h | How far ahead to check for spikes |

---

## 1. Deadband / Hysteresis

### S1.1 — Small price rise during charging: stay charging
- **Start**: Charging (autonomous/100%), price $0.04, threshold $0.05, deadband $0.03
- **Event**: Price rises to $0.049 (still below threshold)
- **Expected**: No change — still charging. A4 conditions fail (price not above threshold)
- **Verify**: Mode stays autonomous, reserve stays 100%

### S1.2 — Price enters deadband: charge to hold
- **Start**: Charging (autonomous/100%), price $0.04, threshold $0.05, deadband $0.03
- **Event**: Price rises to $0.06 (above threshold, below stop price $0.08)
- **Expected**: A4 fires, `choose` selects hold (price < stop price). Mode transitions to hold
- **Verify**: Mode = self_consumption, reserve = 100%, house draws from grid

### S1.3 — Price exits deadband: hold to self consumption
- **Start**: Hold (self_consumption/100%), price $0.07, stop price $0.08
- **Event**: Price rises to $0.09 (above stop price)
- **Expected**: A10 fires, transitions to self consumption
- **Verify**: Mode = self_consumption, reserve = 10%, battery powers house

### S1.4 — Price drops into deadband: self consumption to hold
- **Start**: Self consumption (self_consumption/10%), price $0.10, threshold $0.05, stop price $0.08
- **Event**: Price drops to $0.06 (below stop price, above threshold)
- **Expected**: A11 fires, transitions to hold (preserve battery)
- **Verify**: Mode = self_consumption, reserve = 100%, house draws from grid

### S1.5 — Price drops below threshold while holding: hold to charge
- **Start**: Hold (self_consumption/100%), price $0.06, threshold $0.05
- **Event**: Price drops to $0.04 (below threshold), battery at 30%
- **Expected**: A3 fires (price below threshold, not already charging). Transitions to force charge
- **Verify**: Mode = autonomous, reserve = 100%

### S1.6 — Price jumps past deadband entirely: charge straight to self consumption
- **Start**: Charging (autonomous/100%), price $0.04, threshold $0.05, stop price $0.08
- **Event**: Price jumps to $0.15 (above stop price)
- **Expected**: A4 fires, `choose` default branch (price >= stop price). Skips hold, goes to self consumption
- **Verify**: Mode = self_consumption, reserve = 10%

### S1.7 — Deadband set to zero
- **Start**: Threshold $0.05, deadband $0.00, stop price $0.05
- **Event**: Charging at $0.04, price rises to $0.06
- **Expected**: A4 fires, price > stop price ($0.05), default branch → self consumption directly
- **Verify**: Hold zone effectively doesn't exist, same behaviour as pre-deadband

### S1.8 — Price oscillates around threshold (jitter)
- **Start**: Price oscillates $0.048 → $0.052 → $0.049 every 2 minutes
- **Expected**: 5-minute `for:` debounce on numeric_state triggers prevents rapid switching. Time pattern backup checks conditions but state guards prevent re-firing
- **Verify**: No mode changes during rapid oscillation

---

## 2. Manual Override Protection

### S2.1 — Manual force charge not interrupted by A4
- **Start**: Self consumption, price $0.15 (well above threshold)
- **Action**: Press Force Charge tile on dashboard
- **Expected**: `manual_override_active` set ON, force charge starts. A4 time_pattern fires but conditions fail (override is ON)
- **Verify**: Charging continues indefinitely until manually toggled off

### S2.2 — Manual force charge toggled off
- **Start**: Manual charge active (override ON), charging
- **Action**: Press Force Charge tile again
- **Expected**: Toggle logic fires, calls self_consumption, which clears `manual_override_active`
- **Verify**: Returns to self consumption, override OFF, automations resume

### S2.3 — Self Consumption button clears manual override
- **Start**: Manual discharge active (override ON)
- **Action**: Press Self Consumption button
- **Expected**: `battery_set_self_consumption` runs, clears `manual_override_active`
- **Verify**: Override OFF, mode = self_consumption/10%

### S2.4 — Spike overrides manual action
- **Start**: Manual charge active (override ON), charging
- **Event**: Spike detected at 14:00
- **Expected**: A1/A5 fire (no override check). Charge aborted, discharge starts. When spike clears, A2 calls self_consumption which clears override
- **Verify**: Spike discharge happens despite manual override. Override cleared after spike

### S2.5 — Manual hold not interrupted by A10
- **Start**: Self consumption, price $0.20 (well above stop price)
- **Action**: Press Hold Battery tile on dashboard
- **Expected**: `manual_override_active` set ON, hold mode starts. A10 sees override ON, doesn't interfere
- **Verify**: Hold continues, house draws from grid

---

## 3. Price Spike Discharge

### S3.1 — Spike detected during allowed hours
- **Start**: Self consumption, battery 60%, time 14:00
- **Event**: `binary_sensor.100h_price_spike` turns ON
- **Expected**: A1 fires, transitions to force discharge
- **Verify**: Mode = autonomous, reserve = 10%, notification received with feed-in price

### S3.2 — Spike detected during blocked hours (midnight-6am)
- **Start**: Self consumption, battery 80%, time 02:00
- **Event**: Spike detected
- **Expected**: A1 does NOT fire (time condition: after 06:00 before 00:00 fails)
- **Verify**: Mode stays self_consumption

### S3.3 — Spike clears: return to self consumption
- **Start**: Discharging (autonomous/10%), spike active, battery 40%
- **Event**: Spike clears
- **Expected**: A2 fires, transitions to self consumption
- **Verify**: Mode = self_consumption, reserve = 10%

### S3.4 — Spike clears while in deadband zone
- **Start**: Discharging due to spike, price $0.06 (in deadband: above $0.05 threshold, below $0.08 stop price)
- **Event**: Spike clears
- **Expected**: A2 fires → self consumption. Within 1 minute, A11 time_pattern fires → transitions to hold
- **Verify**: Ends up in hold (self_consumption/100%) within ~1 minute

### S3.5 — Spike fires while charging (A5 race condition)
- **Start**: Charging (autonomous/100%), time 10:00
- **Event**: Spike detected
- **Expected**: A5 fires (not A1, because in charge state). Stops charge script, waits 2s, starts discharge
- **Verify**: Mode = autonomous, reserve = 10%, notification mentions override

### S3.6 — Spike discharge disabled via toggle
- **Start**: Self consumption, `spike_discharge_enabled` = OFF
- **Event**: Spike detected
- **Expected**: A1 blocked (spike_discharge_enabled condition fails). No discharge
- **Verify**: Mode stays self_consumption. When spike clears, A2 has no work to do

### S3.7 — Safety net catches stuck discharge (A6)
- **Start**: Discharging (autonomous/10%), no spike active, no manual override
- **Expected**: A6 fires within 5 minutes. Waits 30s, re-checks conditions, returns to self consumption
- **Verify**: Mode = self_consumption within ~5.5 minutes

---

## 4. Cheap Grid Charging

### S4.1 — Price drops below threshold, battery low
- **Start**: Self consumption, battery 30%, price $0.06, threshold $0.05
- **Event**: Price drops to $0.04 and stays for 5+ minutes
- **Expected**: A3 fires (battery < precharge threshold 50%)
- **Verify**: Mode = autonomous, reserve = 100%, grid charges battery

### S4.2 — Price cheap but battery sufficient, no expensive forecast
- **Start**: Self consumption, battery 70%, price $0.04, threshold $0.05, precharge 50%
- **Expected**: A3 does NOT fire (battery above precharge threshold, no expensive forecast)
- **Verify**: Mode stays self_consumption

### S4.3 — Price cheap, battery sufficient, but expensive forecast coming
- **Start**: Self consumption, battery 70%, price $0.04, forecast shows spike in 1.5 hours
- **Expected**: A3 fires (expensive forecast overrides SOC check)
- **Verify**: Mode = autonomous, reserve = 100%

### S4.4 — Price cheap during demand window: blocked
- **Start**: Price $0.03, time 16:00, demand window enabled, battery 30%
- **Expected**: A3 condition fails (`demand_window_active` = ON). No charging
- **Verify**: Mode stays self_consumption

---

## 5. Demand Window

### S5.1 — Demand window starts at 3pm
- **Start**: Charging (autonomous/100%), time 14:59, demand window enabled
- **Event**: Time reaches 15:00
- **Expected**: A8 fires, forces self consumption
- **Verify**: Mode = self_consumption, notification received

### S5.2 — Charging attempted during demand window (enforcement)
- **Start**: Time 16:00, demand window active, self consumption
- **Event**: Something sets mode to autonomous + reserve 100%
- **Expected**: A9 catches it within 5 minutes, reverts to self consumption
- **Verify**: Mode forced back to self_consumption

### S5.3 — Demand window ends at 9pm
- **Start**: Time 20:59, demand window active, self consumption
- **Event**: Time reaches 21:00
- **Expected**: `demand_window_active` → OFF. If price < threshold, A3 can now fire
- **Verify**: Demand window sensor turns off, charging resumes if conditions met

### S5.4 — Demand window disabled: no blocking
- **Start**: Time 16:00, `demand_window_block_enabled` = OFF, price $0.03, battery 30%
- **Expected**: A3 fires normally (demand window not active)
- **Verify**: Charging proceeds during 3pm-9pm

---

## 6. Time Boundaries

### S6.1 — Discharge blocked before 6am
- **Start**: Battery 100%, time 05:55, spike detected
- **Expected**: A1 blocked (time condition fails before 06:00)
- **Verify**: No discharge

### S6.2 — Discharge allowed at exactly 6am
- **Start**: Spike active since 05:55, time reaches 06:00
- **Expected**: A1 time_pattern trigger picks up spike within 1 minute
- **Verify**: Discharge starts between 06:00-06:01

### S6.3 — Midnight boundary
- **Start**: Discharging, time 23:59, spike active
- **Event**: Time crosses midnight
- **Expected**: Existing discharge continues (not interrupted). But A1 won't start NEW discharge after midnight
- **Verify**: No new discharge initiated in midnight-6am window

---

## 7. System Controls

### S7.1 — Master toggle off (A7)
- **Start**: Any active mode (charging, discharging, holding)
- **Action**: Set `battery_automation_enabled` to OFF
- **Expected**: A7 fires unconditionally, returns to self consumption
- **Verify**: Mode = self_consumption/10%, all automations suppressed

### S7.2 — Master toggle on: automations resume
- **Start**: Self consumption, automation disabled, price $0.03, battery 30%
- **Action**: Set `battery_automation_enabled` to ON
- **Expected**: Next time_pattern cycle (within 1 minute) evaluates conditions, A3 fires if met
- **Verify**: Automations resume within 1 minute

### S7.3 — Dry run mode
- **Start**: Any state, `battery_automation_dry_run` = ON
- **Event**: Any automation triggers
- **Expected**: Scripts send "[DRY RUN]" notifications instead of actual mode changes
- **Verify**: No actual API calls, notifications received with [DRY RUN] prefix

---

## 8. Sensor Verification

### S8.1 — cheap_charge_stop_price computation
- **Setup**: threshold = $0.05, deadband = $0.03
- **Expected**: `sensor.cheap_charge_stop_price` = $0.08
- **Verify**: Check Developer Tools > States

### S8.2 — Diagnostic mode sensor accuracy
Test each mode and verify `sensor.battery_automation_active_mode` shows:
- `manual` — automation disabled
- `demand_block` — during demand window
- `spike_discharge` — spike active + discharge enabled
- `grid_charging` — price below threshold + conditions met
- `holding` — price in deadband zone
- `holding_for_spike` — forecast spike within lookahead
- `self_consumption` — all else

### S8.3 — Binary sensor state detection
- `battery_force_discharge_active`: ON when autonomous + reserve < 11
- `battery_force_charge_active`: ON when autonomous + reserve > 99
- `battery_hold_active`: ON when self_consumption + reserve > 99
- `demand_window_active`: ON when 15:00-21:00 + enabled

---

## 9. End-to-End Flows

### S9.1 — Full charge cycle with deadband
1. Self consumption, battery 30%, price $0.10
2. Price drops to $0.04 → A3 fires → force charge
3. Price rises to $0.06 (deadband) → A4 fires → hold
4. Price rises to $0.09 (above stop) → A10 fires → self consumption
5. Price drops to $0.07 (deadband) → A11 fires → hold
6. Price drops to $0.04 → A3 fires → charge resumes

### S9.2 — Spike interrupts charge, recovers to deadband hold
1. Charging at $0.04, battery 60%
2. Spike detected → A5 fires → discharge
3. Spike clears, price = $0.06 (in deadband)
4. A2 fires → self consumption
5. A11 fires within 1 min → hold
6. Price drops to $0.03 → A3 fires → charge resumes

### S9.3 — Demand window day
1. 09:00: Price $0.03, battery 40% → A3 charges
2. 14:55: Battery 95%, still charging
3. 15:00: A8 fires → self consumption (demand window starts)
4. 16:00: Price still cheap, A9 blocks any charging
5. 21:00: Demand window ends, price $0.03 → A3 resumes charging

### S9.4 — Manual override survives price changes
1. Self consumption, price $0.20
2. User presses Force Charge → manual override ON
3. Price changes to $0.25, $0.30 → A4 blocked by override
4. User presses Force Charge again → toggles off, override cleared
5. A4 now free to act if conditions met

---

## 10. Future Feature Placeholders

> These scenarios will be fleshed out as features from FUTURE_FEATURES.md are implemented.

### F1 — Solar forecast aware pre-charging
- Cloudy day forecast, battery 30%, price moderate ($0.10)
- System should weigh solar forecast against current price to decide whether to grid-charge

### F2 — Configurable demand window times
- Custom demand window (e.g. 5pm-8pm instead of 3pm-9pm)
- All time-based scenarios should be re-tested with custom times

### F3 — Notification tuning
- Quiet mode: suppress routine notifications (charge start/stop, hold transitions)
- Only send spike and demand window alerts

### F4 — Export revenue tracking
- Track kWh exported during spike discharge, multiply by feed-in price
- Daily/monthly revenue summaries

### F5 — Battery health awareness
- Configurable minimum export SOC (e.g. don't discharge below 25%)
- Spike discharge stops at health threshold instead of 10%

### F6 — Seasonal profiles
- Summer vs winter default thresholds
- Quick-switch via dashboard

### F7 — Demand window spike discharge protection
- Allow partial spike discharge during demand window
- Stop before battery too low to cover remaining evening load
