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