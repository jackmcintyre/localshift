# Notifications Guide

Complete reference for the LocalShift notification system.

## Overview

LocalShift sends notifications to keep you informed about battery automation activities. Notifications are delivered via a Home Assistant notify service (e.g., mobile app notifications, email, etc.) configured during setup.

---

## Configuration

### Initial Setup

During the config flow, you select a notify service:

1. Go to **Settings → Devices & Services → Add Integration**
2. Select **LocalShift**
3. Complete the entity selection steps
4. In the **Solcast & Notifications** step, choose your notify service from the dropdown

### Available Notify Services

Common options include:
- `notify.mobile_app_your_phone` — Push notifications to your phone
- `notify.notify` — Default notification group
- Custom notify services you've configured

---

## Notification Types

### 1. Mode Transition Notifications

Sent whenever the battery mode changes. These help you understand what the automation is doing and why.

#### Price Spike (Spike Discharge)
```
Title: LocalShift: Price Spike!
Message: Price spike detected. Feed-in: $X.XX/kWh. Battery at XX%. Switching to force discharge (export).
```

**When:** Feed-in price exceeds the price spike threshold (from Amber binary sensor)

#### Proactive Export
```
Title: LocalShift: Proactive Export
Message: Forecast predicts low/negative FIT. Feed-in: $X.XX/kWh. Battery at XX%. Exporting with XX% reserve.
```

**When:** Feed-in tariff is forecast to drop significantly in the near future

#### Demand Window Active
```
Title: LocalShift: Demand Window Active
Message: Demand window started (HH:MM–HH:MM). Grid imports blocked. Battery at XX%.
```

**When:** Current time enters the configured demand window (default 15:00-21:00)

#### Cheap Grid Charging
```
Title: LocalShift: Cheap Grid Charging
Message: Grid price is $X.XX/kWh (below threshold $X.XX/kWh). Battery at XX%. Charging from grid at ~3.3kW.
```

**When:** Grid price falls below the effective cheap price threshold

#### Boost Charging
```
Title: LocalShift: Boost Charging (5kW)
Message: Grid price is $X.XX/kWh (below threshold $X.XX/kWh). Battery at XX%, target XX%. Solar forecast insufficient — boost charging at ~5kW.
```

**When:** Time to demand window is too short for normal (3.3kW) charging to reach target

#### Self Consumption (Return to Normal)
```
Title: LocalShift: [Situation Ended]
Message: Various messages depending on what triggered the return to self consumption:
  - Price spike has cleared
  - FIT has improved
  - Grid price rose above threshold
  - Demand window ended
  - Normal operation
```

**When:** Special conditions end and automation returns to normal self-consumption mode

#### Manual Override
```
Title: LocalShift: Manual Override
Message: Automation disabled or manual override active.
```

**When:** Automation is disabled via switch, or manual control is active

---

### 2. Daily Summary Notification

Sent once per day at the end of the demand window.

```
Title: LocalShift: Daily Summary
Message:
  Today so far:

  Solar: X.X kWh
  Grid import: X.X kWh ($X.XX)
  Grid export: X.X kWh ($X.XX revenue)
  Net cost: $X.XX

  Battery savings: $X.XX
  Battery charge cost: $X.XX
  SOC: XX%
```

**When:** At demand window end time (default 21:00)

**Data Sources:**
- Solar, grid import/export energy: From Home Assistant utility meter sensors
- Costs: From LocalShift cost accumulator sensors

---

### 3. Manual Button Notifications

Sent when you press any of the manual control buttons.

#### Force Charge Button
```
Title: LocalShift: Manual Force Charge
Message: Manual force charge started. Battery at XX%.
```

#### Force Discharge Button
```
Title: LocalShift: Manual Force Discharge
Message: Manual force discharge started. Battery at XX%.
```

#### Boost Charge Button
```
Title: LocalShift: Manual Boost Charge
Message: Manual boost charge (5kW) started. Battery at XX%.
```

#### Self Consumption Button
```
Title: LocalShift: Manual Self Consumption
Message: Returned to self consumption. Battery at XX%.
```

#### Update Forecast Button
```
Title: LocalShift: Forecast Update
Message: Historical load cache cleared. Forecast will regenerate.
```

---

## Notification Flow

```
User Action or State Change
         │
         ▼
   State Machine Evaluation
         │
         ├─► Mode Transition → send_transition_notification()
         │
         ├─► Button Press → async_send_notification()
         │
         └─► Daily Timer → _handle_daily_summary() → send_daily_summary()
                            │
                            ▼
                    NotificationService
                            │
                            ▼
               Home Assistant Notify Service
                            │
                            ▼
                    Mobile App / Email / etc.
```

---

## Troubleshooting

### Not Receiving Notifications

1. **Check notify service exists**
   - Go to Developer Tools → Services
   - Search for your notify service (e.g., `notify.mobile_app`)
   - Try sending a test notification

2. **Check notification settings on your phone**
   - iOS: Settings → Notifications → Home Assistant
   - Android: Settings → Apps → Home Assistant → Notifications

3. **Check automation is enabled**
   - Verify `switch.localshift_automation_enabled` is ON
   - If OFF, mode stays as "manual" and no transition notifications are sent

4. **Check dry run mode**
   - If `switch.localshift_dry_run` is ON, notifications still work
   - But no actual mode changes occur

### Too Many Notifications

The notification frequency depends on:
- **Mode transitions**: Depends on price volatility and your thresholds
  - Low thresholds → more frequent grid charging → more notifications
  - Price spikes → additional spike discharge notifications
- **Daily summary**: Once per day at DW end

To reduce notifications:
- Increase `cheap_price_percentile` (fewer cheap periods detected)
- Increase `cheap_price_deadband` (less oscillation around thresholds)
- Adjust `forecast_lookahead_hours` to reduce sensitivity

### Notification Service Errors

Check Home Assistant logs:
- Go to **Settings → System → Logs**
- Search for "notify" or your notify service name
- Common errors: service not found, authentication issues

---

## Notification Preferences

The integration provides 4 switches to control which notifications are sent:

### Switches

| Switch | Default | Description |
|--------|---------|-------------|
| `switch.localshift_notify_transitions` | ON | Send notifications when battery mode changes |
| `switch.localshift_notify_daily_summary` | ON | Send daily energy/cost summary at demand window end |
| `switch.localshift_notify_manual_actions` | ON | Send notifications when manual control buttons are pressed |
| `switch.localshift_notify_alerts` | ON | Send alert notifications (automation disabled, health check failures) |

### Usage

- Turn off specific notification types if you find them too noisy
- All switches default to ON for new installations
- Settings persist across Home Assistant restarts

### Examples

**Quiet mode (only important alerts):**
- Turn OFF `notify_transitions`
- Turn OFF `notify_manual_actions`
- Keep ON `notify_daily_summary`
- Keep ON `notify_alerts`

**Minimal notifications:**
- Turn OFF all except `notify_alerts`

---

## Related Documentation

- [Entity Reference](./ENTITY_REFERENCE.md) — All sensors, switches, and buttons
- [Architecture](./ARCHITECTURE.md) — System design overview
- [Developer Guide](./DEVELOPER_GUIDE.md) — Contributing and customization
</parameter>
<task_progress>- [x] Review current notification implementation
- [x] Identify any gaps or issues
- [x] Create plan for improvements
- [x] Document how notifications work
- [ ] Add backlog item for notification improvements</task_progress>
</invoke>
</minimax:tool_call>