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

## Learning System Issues

### Learning Status Stuck on "observing"

**Symptoms:**
- `sensor.localshift_learning_status` shows `observing` for days
- Parameters never change from defaults

**Causes:**
- Not enough decisions recorded (needs 50+)
- `switch.localshift_enable_learning` is OFF
- State machine not making mode transitions

**Solutions:**
1. Check `sensor.localshift_decision_history` count — needs to be 50+
2. Enable `switch.localshift_enable_learning`
3. Verify state machine is running — check `select.localshift_battery_mode` for mode changes
4. Wait 2-3 days for warm-up period to complete

**Expected Timeline:**
- Days 1-3: Observing phase (collecting data)
- Day 3+: Tuning phase (parameter adjustments begin)
- Week 2+: Optimizing phase (full optimization active)

---

### How Do I Reset Learning Data?

**When to Reset:**
- After significant household changes (new appliances, solar panels, etc.)
- If learned parameters seem sub-optimal
- To start fresh after testing

**How to Reset:**
1. Go to Settings → Devices & Services → LocalShift
2. Find `button.localshift_reset_learning`
3. Press the button

**What Gets Reset:**
- Decision records cleared
- Weather regression statistics cleared (temperature history retained)
- Recent decision log cleared
- Performance metrics reset
- Learning status returns to "observing"

**Note:** Parameter optimizer and pattern analysis data are not reset by this button yet.

**Note:** The system will need another 2-3 days of observation before parameter optimization resumes.

---

### Can I Disable the Learning System?

**Yes.** Use `switch.localshift_enable_learning`:

- **ON**: Learning system can adjust parameters
- **OFF**: Learning system observes only, parameters stay at defaults

**When Disabled:**
- Decisions are still tracked for observability
- All parameters remain at default (zero-offset) values
- No behavioral changes occur

**When to Disable:**
- If you prefer manual control over all parameters
- During testing or troubleshooting
- If learned behavior is not desired

---

### Decision Quality Score Not Improving

**Symptoms:**
- `sensor.localshift_decision_quality` shows low or declining scores
- `cost_trend` attribute shows "degrading"

**Causes:**
- Unusual price patterns (volatility)
- Solar forecast inaccuracies
- Consumption pattern changes
- Learning parameters not yet optimal

**Solutions:**
1. Check `sensor.localshift_decision_history` for low-scoring decisions
2. Review `sensor.localshift_learning_status` parameters — are they sensible?
3. If parameters are extreme, reset learning and start fresh
4. Wait for more data — learning improves over time

**What to Monitor:**
- `avg_score_7d` should trend upward over weeks
- `grid_charge_efficiency` should improve (closer to 1.0)
- `export_loss_ratio` should decrease (closer to 0.0)

---

### Learning System Not Persisting Across Restarts

**Symptoms:**
- Learning status resets to "observing" after Home Assistant restart
- Decision history is empty after restart

**Causes:**
- Storage initialization failure
- Corrupted storage data

**Solutions:**
1. Check Home Assistant logs for storage errors
2. Verify `.storage/` directory is writable
3. Look for errors containing "localshift" and "storage"

**Storage Keys Used:**
- `localshift.decision_outcomes.{entry_id}`
- `localshift.param_optimizer.{entry_id}`
- `localshift.pattern_analysis.{entry_id}`
- `localshift.opt_controller.{entry_id}`

---

### Parameter Values Seem Incorrect

**Symptoms:**
- Learned parameters are at extreme values
- Battery behavior doesn't match expectations

**Causes:**
- Insufficient data during learning
- Unusual operating conditions
- Bias in training data

**Solutions:**
1. Review parameters in `sensor.localshift_learning_status`
2. Compare current values to defaults (all defaults are 0.0)
3. If values are at min/max bounds, consider resetting
4. Check if the learning switch was enabled prematurely

**Parameter Ranges:**

| Parameter | Range | Default |
|-----------|-------|---------|
| `cheap_price_bias` | -5.0 to +5.0 c/kWh | 0.0 |
| `solar_confidence_factor` | 0.5 to 1.5 | 1.0 |
| `overnight_drain_safety_margin` | -5.0 to +10.0 % | 0.0 |
| `grid_charge_soc_headroom` | -5.0 to +10.0 % | 0.0 |
| `export_threshold_adjustment` | -3.0 to +3.0 c/kWh | 0.0 |
| `consumption_forecast_bias` | -0.5 to +0.5 kW | 0.0 |

---

## DP Optimizer Issues (Issue #403)

The DP optimizer runs in shadow mode alongside the legacy planner. Issues here typically affect observability rather than control.

### Optimizer Shows "disabled"

**Symptoms:**
- `sensor.localshift_optimizer_shadow_plan` state is "disabled"
- `sensor.localshift_optimizer_shadow_summary` state is "disabled"
- No comparison data available

**Causes:**
- Optimizer not enabled in configuration
- Integration option `optimizer_enabled` is `False`

**Solutions:**
1. Go to **Settings → Devices & Services → LocalShift → Configure**
2. Enable the optimizer option
3. Reload the integration

### Optimizer Shows "error"

**Symptoms:**
- `sensor.localshift_optimizer_shadow_plan` state is "error"
- `sensor.localshift_optimizer_shadow_summary` state is "failed"
- `error_message` attribute contains error details

**Causes:**
- No forecast slots available
- Invalid initial SOC (e.g., entity unavailable returning 0)
- DP solver internal error

**Solutions:**
1. Check `error_message` attribute in the shadow summary sensor
2. Check Home Assistant logs for the `cycle_id` mentioned in the error
3. Verify `sensor.localshift_forecast_battery` has valid data
4. Verify SOC entity is returning valid values (> 0)

### High Mismatch Count

**Symptoms:**
- `sensor.localshift_optimizer_comparison` shows high mismatch count (> 10)
- Plans differ significantly between legacy and optimizer

**Causes:**
- Different planning assumptions between systems
- SOC discretization effects
- Edge case handling differences

**Solutions:**
1. Check `mismatch_by_type` attribute to see which mismatch types dominate
2. Review `top_mismatches` for specific slot-level differences
3. Check `parity_completeness_pct` — if low, input data may be incomplete
4. Compare `net_cost_delta` — if negative, optimizer may actually be better

**Understanding Mismatch Types:**

| Type | Meaning | Action |
|------|---------|--------|
| `ACTION_MISMATCH` | Different action types (charge vs hold) | Review if optimizer's action is reasonable |
| `IMPORT_QUANTITY_MISMATCH` | Same action, different charge amount | Minor difference, usually OK |
| `EXPORT_QUANTITY_MISMATCH` | Same action, different export amount | Minor difference, usually OK |
| `TARGET_ATTAINMENT_MISMATCH` | DW target met by only one plan | Review if optimizer target strategy is better |
| `PROFITABILITY_MISMATCH` | Action differs due to cost optimization | Check `net_cost_delta` for actual impact |

### Optimizer Cheaper But Not Used

**Symptoms:**
- `net_cost_delta` is negative (optimizer cheaper)
- Legacy planner still controls battery

**Explanation:**
This is expected behavior in shadow/assist mode. The optimizer runs for comparison only and does NOT control the battery. This allows you to:

1. Observe optimizer behavior over time
2. Build trust in optimizer decisions
3. Compare projected costs
4. Identify when optimizer would make different choices

**When Will Optimizer Control?**
Active control mode is now available (Phase F). To enable:
1. Ensure optimizer has been running in shadow mode successfully
2. Go to **Settings → Devices & Services → LocalShift → Configure**
3. Set **Optimizer Control Mode** to "active"
4. The optimizer will control the battery with safety gates

**Safety First:**
Active mode includes strict safety gates:
- Falls back to legacy control immediately if any check fails
- Tracks fallback count and applies cooldown after repeated failures
- Can be disabled at any time

### Comparison Sensor Shows -1

**Symptoms:**
- `sensor.localshift_optimizer_comparison` state is `-1`

**Meaning:**
The comparison computation failed, not the optimizer itself.

**Solutions:**
1. Check `error_message` attribute for details
2. Verify both legacy and optimizer plans have data
3. Check diagnostics for comparison error details

### Parity Completeness Low

**Symptoms:**
- `parity_completeness_pct` < 95%
- Warning in shadow summary about defaulted fields

**Causes:**
- Legacy forecast slots missing expected fields
- Input data quality issues

**Solutions:**
1. Check `parity_defaulted_fields` in shadow summary
2. Verify forecast sensors have all expected attributes
3. Check `sensor.localshift_forecast_battery` for complete data

### Alignment Issues

**Symptoms:**
- `alignment_valid` is `False`
- `alignment_issues` list contains warnings

**Causes:**
- Slot count mismatch between legacy and optimizer
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
2. Check that optimizer is enabled in configuration
3. Wait for at least one coordinator cycle after enabling

### Active Mode Issues

When using active mode (optimizer controls battery), additional monitoring is available.

#### Active Mode Fallback

**Symptoms:**
- `sensor.localshift_optimizer_shadow_summary` shows `block_reason` in attributes
- Battery is being controlled by legacy planner even though active mode is enabled

**Causes:**
- Safety gate check failed
- One or more admission criteria not met

**Solutions:**
1. Check `block_reason` in `optimizer_shadow_summary` attributes
2. Review the safety gate checks below

#### Safety Gate Block Reasons

| Block Reason | Meaning | Action |
|--------------|---------|--------|
| `optimizer_not_enabled` | Optimizer disabled in config | Enable optimizer in configuration |
| `control_mode_not_active` | Control mode not set to "active" | Set control mode to "active" |
| `solve_not_successful` | Last DP solve failed | Check optimizer error messages |
| `slot_alignment_invalid` | Slot mismatch between legacy and optimizer | Check forecast generation |
| `no_decisions` | No optimizer decisions available | Check optimizer is producing results |
| `cooldown_active` | In fallback cooldown after repeated failures | Wait for cooldown to complete |

#### Active Mode Apply Status

**Symptoms:**
- Want to verify optimizer decisions are being applied

**Solutions:**
1. Check `sensor.localshift_optimizer_shadow_summary` attributes:
   - `last_apply_status`: "success" or "failed"
   - `last_apply_timestamp`: ISO timestamp of last successful apply
   - `fallback_count`: Number of consecutive fallback cycles
2. Check diagnostics for detailed apply path information

#### Rolling Back from Active Mode

**Symptoms:**
- Want to revert to shadow/assist mode or disable optimizer entirely

**Solutions:**
1. Go to **Settings → Devices & Services → LocalShift → Configure**
2. Change **Optimizer Control Mode** from "active" to "shadow" or "assist"
3. Or disable the optimizer entirely by toggling off **Enable Optimizer**
4. Changes take effect on next coordinator cycle

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
