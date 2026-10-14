# LocalShift Troubleshooting Guide

This guide covers common error scenarios and solutions for the LocalShift integration.

## Integration Status Sensors

LocalShift provides two sensors for monitoring integration health:

### Integration Status Sensor (`sensor.localshift_integration_status`)

Displays the overall health status:

| Status | Meaning |
|--------|---------|
| `ok` | All required entities are healthy and available |
| `degraded` | Some optional entities are missing, but core functionality works |
| `error` | Required entities are missing; automation may not function correctly |

**Attributes:**
- `message` - Human-readable status message
- `error_count` - Number of current errors
- `warning_count` - Number of current warnings
- `required_entities_healthy` - Boolean indicating if all required entities are available
- `errors` - List of current error messages
- `warnings` - List of current warning messages

### Entity Health Sensor (`sensor.localshift_entity_health`)

Shows detailed health status for each tracked entity.

**Attributes:**
- `entities` - Dictionary mapping entity config keys to health status
- `errors` - Current error messages
- `warnings` - Current warning messages

---

## Common Error Scenarios

### 1. Missing Required Entities

**Symptoms:**
- Integration status shows `error`
- Automation is disabled or not functioning
- Errors in logs like "Entity 'sensor.xxx' does not exist"

**Causes:**
- Entity was renamed or deleted
- Integration providing the entity was removed
- Typo in configuration

**Solutions:**
1. Check which entity is missing from the `Entity Health` sensor attributes
2. Verify the entity exists in Home Assistant Developer Tools → States
3. Reconfigure the integration with the correct entity ID:
   - Go to Settings → Devices & Services → LocalShift → Configure

### 2. Unavailable Entities

**Symptoms:**
- Integration status shows `degraded` or `error`
- Entities show state `unavailable` in Home Assistant

**Causes:**
- External integration (Teslemetry, Amber, Solcast) is not running
- Network connectivity issues
- External service outage

**Solutions:**
1. Check the external integration's status
2. Verify network connectivity to external services
3. Wait for service restoration if it's an outage
4. Check Home Assistant logs for related errors

### 3. Solcast Forecast Not Available

**Symptoms:**
- Integration starts but solar forecast shows 0 kWh
- Warning in logs: "Solcast data not available after 3 retries"

**Causes:**
- Solcast integration hasn't initialized yet
- Solcast API rate limit exceeded
- Solcast integration misconfigured

**Solutions:**
1. Wait 2-3 minutes for Solcast to initialize
2. Check Solcast integration configuration
3. Verify Solcast API key is valid
4. Check if rate limit was exceeded (Solcast free tier has limits)

### 4. Prices Showing Zero or Invalid Values

**Symptoms:**
- `general_price` or `feed_in_price` showing 0
- Automation making incorrect decisions

**Causes:**
- Amber Electric integration issues
- Price entity misconfigured
- API rate limiting

**Solutions:**
1. Verify Amber Electric integration is running
2. Check the price entity in Developer Tools
3. Ensure the correct price entities are configured
4. Check Amber Electric API status

### 5. Battery Commands Not Working

**Symptoms:**
- Mode changes not reflected in Powerwall
- Validation errors in logs

**Causes:**
- Teslemetry integration issues
- Tesla API rate limiting
- Tesla service outage
- Storm Watch or Grid Event active

**Solutions:**
1. Check Teslemetry integration status
2. Verify Tesla account is accessible in Tesla app
3. Check for Tesla service outages
4. If Storm Watch is active, wait for it to end (automation will pause)

---

## Entity Categories

The integration categorizes entities by importance:

### Required Entities
These MUST be available for automation to function:
- Battery operation mode (Teslemetry)
- Backup reserve percentage (Teslemetry)
- Battery state of charge (Teslemetry)
- Current buy price (Amber)
- Current sell price (Amber)

### Recommended Entities
Missing these will cause degraded functionality:
- Grid power, battery power, solar power, load power
- Price forecasts (general and feed-in)
- Solcast solar forecasts

### Optional Entities
Nice to have, missing won't affect core functionality:
- Price spike indicator
- Tomorrow's solar forecast
- Weather entity

---

## Diagnostic Tools

### Home Assistant Diagnostics

Download diagnostics from:
Settings → Devices & Services → LocalShift → ⋮ → Download Diagnostics

The diagnostics include:
- Integration status
- Entity health details
- Current sensor values
- Recent errors and warnings
- Configuration summary

### Log Analysis

Search logs for LocalShift errors:
```
grep "localshift" home-assistant.log | grep -i error
```

Common log patterns:

| Pattern | Meaning |
|---------|---------|
| `Entity health error` | An entity is missing or unavailable |
| `Solcast data not available` | Solcast initialization failed |
| `Automation blocked` | Required entity unavailable, automation paused |
| `Validation failed` | Hardware state doesn't match expected |

---

## Recovery Procedures

### Full Integration Reset

If problems persist:

1. **Backup configuration:**
   - Note all configured entity IDs
   
2. **Remove integration:**
   - Settings → Devices & Services → LocalShift → Delete
   
3. **Restart Home Assistant**
   
4. **Re-add integration:**
   - Settings → Devices & Services → Add Integration → LocalShift
   - Configure all required entities

### Partial Recovery

For specific entity issues:

1. **Identify the problematic entity** from the Entity Health sensor
2. **Fix the underlying issue** (rename, restore integration, etc.)
3. **Reload the integration:**
   - Settings → Devices & Services → LocalShift → ⋮ → Reload

---

## Decision Telemetry

The parameter-learning layer was retired in September 2026 — it produced no
measurable gain over six months, and its contextual adjustments compounded into
an unintended standing inflation of the grid-charge price gate. The
troubleshooting entries that lived here (learning stuck on "observing",
decision quality not improving, parameters at their bounds) no longer describe
anything the integration does.

What remains is telemetry: `sensor.localshift_learning_decision_history` records
each mode decision and its measured outcome, and
`button.localshift_reset_decision_telemetry` discards that record set. Nothing reads the
records back to change behaviour, so there is no learning state that can get
stuck or need resetting.

If decisions look wrong, the cause is in the optimizer or the state machine, not
in learning: see the DP Optimizer section below. For the full account of the
retired layer, see [LEARNING_SYSTEM.md](LEARNING_SYSTEM.md).

## DP Optimizer Issues (Issue #403)

The DP optimizer is the active planner — there is no separate legacy planner or shadow mode to compare against (see [LEARNING_SYSTEM.md](LEARNING_SYSTEM.md) and the Phase 5 migration note in [ENTITY_REFERENCE.md](ENTITY_REFERENCE.md) for the history). Issues here typically affect observability rather than control.

### Optimizer Shows "disabled"

**Symptoms:**
- `sensor.localshift_optimizer_plan_detailed` state is "disabled"
- `sensor.localshift_optimizer_summary` state is "disabled"
- No plan data available

**Causes:**
- No optimizer cycle has completed yet, so there is no summary to show (normal for the first moments after a restart or reload)

There is no option that enables or disables the optimizer; the `optimizer_enabled` setting was removed (Issue #448).

**Solutions:**
1. Wait for one coordinator cycle
2. If it stays "disabled", check the Home Assistant log for LocalShift errors and whether the price and solar forecast inputs are available (`sensor.localshift_automation_ready`)

### Optimizer Shows "error"

**Symptoms:**
- `sensor.localshift_optimizer_plan_detailed` state is "error"
- `sensor.localshift_optimizer_summary` state is "failed"
- `error_message` attribute contains error details

**Causes:**
- No forecast slots available
- Invalid initial SOC (e.g., entity unavailable returning 0)
- DP solver internal error

**Solutions:**
1. Check `error_message` attribute on the summary sensor
2. Check Home Assistant logs for the `cycle_id` mentioned in the error
3. Verify `sensor.localshift_forecast_battery` has valid data
4. Verify SOC entity is returning valid values (> 0)

### Optimizer Not Controlling the Battery

**Symptoms:**
- Optimizer plan looks reasonable but the battery isn't following it

**Explanation:**
The optimizer's plan is applied only after a safety gate passes (see Safety Gate below). If a check fails, that cycle's plan is not applied and the battery is put in self-consumption instead.

**Solutions:**
1. Ensure the optimizer has been producing valid plans (no `error` state)
2. Check the safety gate block reasons below
3. Check that automation is on (`switch.localshift_automation_enabled`) and that no manual mode is selected

**Safety First:**
The safety gate:
- Defaults to self-consumption immediately if any check fails
- Is re-evaluated every cycle; there is no fallback counter or cooldown, so control returns on the first cycle that passes

### Parity Completeness Low

**Symptoms:**
- `parity_completeness_pct` < 95%
- Warning in `sensor.localshift_optimizer_summary` about defaulted fields

**Causes:**
- Forecast slots missing expected fields
- Input data quality issues

**Solutions:**
1. Check `parity_defaulted_fields` on `sensor.localshift_optimizer_summary`
2. Verify forecast sensors have all expected attributes
3. Check `sensor.localshift_forecast_battery` for complete data

### Alignment Issues

**Symptoms:**
- `alignment_valid` is `False`
- `alignment_issues` list contains warnings

**Causes:**
- Slot count mismatch between the forecast slots and the optimizer's slot contexts
- Timestamp/interval inconsistencies

**Solutions:**
1. Check `alignment_issues` for specific problems
2. Verify forecast is generating slots correctly
3. Restart integration if issues persist

### Diagnostics Missing Optimizer Section

**Symptoms:**
- Downloaded diagnostics don't include `optimizer` key
- Optimizer section shows `status: not_loaded`

**Causes:**
- Coordinator not available
- Optimizer never run

**Solutions:**
1. Verify integration is loaded
2. Wait for at least one coordinator cycle after a restart or reload

### Safety Gate

The optimizer always controls the battery; the shadow and assist control modes and the "Enable Optimizer" option were removed (Issue #448). What remains from that rollout is the safety gate, which checks each cycle's plan before it is applied.

#### Safety Gate Fallback

**Symptoms:**
- The Home Assistant log shows `DP optimizer safety gate blocked: <reason> — defaulting to SELF_CONSUMPTION`
- `sensor.localshift_forecast_status` attribute `debug_mode_source` reads `fallback` instead of `optimizer`
- The battery is in self-consumption although the plan wanted something else

**Causes:**
- A safety gate check failed this cycle

**Solutions:**
1. Find the block reason in the log line above (it is not published as a sensor attribute)
2. Look the reason up in the table below

#### Safety Gate Block Reasons

| Block Reason | Meaning | Action |
|--------------|---------|--------|
| `optimizer_result_none` | The planner returned no result | Check the log for the optimizer error that preceded it |
| `optimizer_solve_failed` | The DP solve failed | Check `error_message` on `sensor.localshift_optimizer_summary` |
| `forecast_stale` | The plan is more than 30 minutes old | Check that the price and solar forecast inputs are updating |
| `no_decisions_available` | The solve succeeded but produced no decisions | Check that forecast slots are available |

#### Stopping LocalShift from Controlling the Battery

There is no shadow or assist mode to roll back to. To stop LocalShift driving the battery:

1. Turn off `switch.localshift_automation_enabled`, or
2. Pick a mode on `select.localshift_battery_mode`, which turns automation off and applies that mode; select "automatic" to hand control back

---

## Getting Help

If issues persist after troubleshooting:

1. **Download diagnostics** from Home Assistant
2. **Check existing issues:** https://github.com/jackmcintyre/ha-solar-battery-automation/issues
3. **Open a new issue** with:
   - Diagnostics file (redact any sensitive info)
   - Description of the problem
   - Steps already tried
   - Home Assistant version
   - Integration version
