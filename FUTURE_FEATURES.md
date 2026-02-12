# Future Features

Backlog of planned enhancements for the Amber Powerwall automation package. The core system (spike discharge, cheap charging, demand window block, dry run) should be stable before tackling these.

---

## 1. Solcast-Aware Daytime Solar Gap Monitor ✅ IMPLEMENTED

**Status:** Implemented. A3 now uses Solcast `detailedForecast` (pv_estimate10 pessimistic) to calculate whether solar alone can fill the battery to the target SOC by 3pm. If not, it triggers grid charging — either gentle (3.3kW backup) or boost (5kW autonomous+reserve=100%) depending on time remaining. A `battery_target_reached_today` flag prevents charge-drain-recharge cycles.

Key sensors: `sensor.solar_battery_forecast`, `binary_sensor.solar_can_reach_target`, `binary_sensor.boost_charge_needed`

---

## 1b. Smart Pre-charge: Dynamic Price Ceiling ✅ IMPLEMENTED

**Status:** Implemented as urgency-based dynamic pricing. `sensor.effective_cheap_price` automatically raises the acceptable grid charging threshold when `solar_can_reach_target` is OFF (solar gap detected) and before the demand window. The threshold interpolates between `cheap_price_threshold` (base) and `max_precharge_price` (ceiling) based on urgency — how close the demand window is. A forecast floor prevents overpaying when cheaper prices are coming soon (scans `sensor.100h_general_forecast` for minimum upcoming price + 2c margin).

Key entities: `sensor.effective_cheap_price` (with `urgency`, `is_elevated`, `min_forecast_price` attributes), `input_number.max_precharge_price` (default 20c). All automations (A3, A4, A11) and `cheap_charge_stop_price` now reference the effective price instead of the static threshold.

---

## 2. Configurable Demand Window Times ✅ IMPLEMENTED

**Status:** Implemented. `input_datetime.demand_window_start` and `input_datetime.demand_window_end` replace all hardcoded 3pm/9pm references. The `demand_window_active` binary sensor, A8 trigger, solar forecast sensor (`target_hour`), notifications, and dashboard all use the configurable times. A14 sets defaults (15:00/21:00) on first HA startup.

---

## 3. Notification Tuning

**Problem:** Every state change sends a notification. During volatile pricing this could be noisy.

**Goal:** Allow the user to control notification verbosity.

**Approach:**
- Add `input_boolean.quiet_notifications` — when on, only send notifications for spikes and demand window overrides (the important stuff). Suppress routine charging start/stop and safety net messages.
- Consider notification throttling — don't send more than N notifications per hour
- Group related notifications (e.g. "charging started" + "charging stopped" within a short window)

**Complexity:** Low-medium.

---

## 4. Export Revenue Tracking ✅ IMPLEMENTED (as part of Visibility Suite)

**Status:** Implemented as part of a broader cost tracking and visibility suite. Uses cost-rate integration ($/hr → $ via Riemann sum) for accurate time-correlated cost tracking, rather than multiplying accumulated kWh by average price.

**Sensors added:**
- Cost rate sensors: `grid_import_cost_rate`, `grid_export_revenue_rate`, `battery_savings_rate`, `battery_charge_cost_rate` (all $/hr)
- Directional power: `grid_import_power`, `grid_export_power` (kW)
- Integration (cumulative): `grid_import_cost`, `grid_export_revenue`, `battery_savings`, `battery_charge_cost` ($), `grid_import_energy`, `grid_export_energy`, `solar_production_energy` (kWh)
- Utility meters: daily + monthly resets for all cost/energy sensors
- `net_electricity_cost_today` — convenience sensor with cost breakdown attributes
- `battery_automation_decision_log` — records mode changes with human-readable reasoning

**Dashboard:** New "Insights" section with cost summary table, price+mode timeline overlay, and latest decision card.

**Automation:** A15 daily summary notification fires at demand window end with cost/energy/solar totals.

---

## 5. Battery Health Awareness

**Problem:** Draining the battery to 10% reserve during every spike event maximises export revenue but increases wear on the battery cells.

**Goal:** Configurable minimum SOC floor during export events, separate from the backup reserve.

**Approach:**
- New `input_number.min_export_soc` (e.g. default 20%)
- During spike discharge, monitor SOC and switch back to self consumption if it drops below this floor
- Trade-off: less revenue per spike, but better battery longevity

**Complexity:** Low-medium.

---

## 6. Seasonal Profiles

**Problem:** Summer and winter have very different solar production, pricing patterns, and demand window importance.

**Goal:** Pre-set threshold profiles that can be switched with a single toggle.

**Approach:**
- `input_select.season_profile` with options like "summer", "winter", "shoulder"
- Automation that sets all `input_number` thresholds to profile defaults when the profile changes
- Summer: lower cheap threshold (more solar available), wider demand window
- Winter: higher cheap threshold (less solar, need to charge more from grid), possibly shorter demand window

**Complexity:** Low — mostly UI convenience, the underlying automations don't change.

---

## 7. Demand Window Spike Discharge Protection

**Problem:** Spike discharge is currently allowed during the demand window (3pm-9pm). This is intentional — selling at spike prices is profitable. However, if the spike drains the battery and then clears, the house will pull from the grid for the remainder of the demand window, which is exactly what the demand block is designed to prevent.

**Goal:** Allow spike discharge during the demand window but stop exporting before the battery is too low to cover remaining household demand until 9pm.

**Approach:**
- Estimate remaining household consumption from now until demand window end (e.g. average load * hours remaining)
- Set a dynamic minimum SOC floor during demand window spike discharge: only export down to the level needed to cover the house until 9pm
- Could use `sensor.my_home_load_power` averaged over recent history to estimate consumption rate
- Falls under Battery Health Awareness (Feature 5) but is specifically demand-window-aware

**Complexity:** Medium — needs load estimation and dynamic reserve calculation.

---

## 8. Change of approach - data driven vs time driven ⚡ PARTIALLY ADDRESSED (B9)

**Problem:** Currently, a lot of weight goes towards being charged to a SOC by 3pm. Today, this goal was achieved by charging during a cloudy morning. At 3pm, the clouds cleared, and power began exporting from solar due to the battery being full. This late sun was accurately reflected in the solar forecast.

**Goal:** Look at the data we have available (and other sources, if required.) Rather than being full by 3pm, the goal should be to reach 100% prioritising solar over grid. For example, a better outcome would have been 80% charge at 3pm, then the battery getting the final 20% from the sun.

**Status:** Partially addressed by B9 (solar-first daytime charging). The `battery_low` threshold no longer overrides the Solcast solar gap monitor during daytime — if solar can reach the target, the system trusts the forecast and waits for free solar instead of grid charging. Remaining edge case: the system doesn't yet optimise *how much* grid to use vs solar — it's binary (charge or don't). A more sophisticated approach would calculate the optimal grid charge amount to complement solar.

**Complexity:** Remaining work is medium-high (optimal charge amount calculation).

## 9. Exporting to grid directly from Solar ✅ IMPLEMENTED

**Problem:** Consider a day with full sun. The battery will rapidly charge, and reach 100% before the demand window. This may result in us paying to export solar (negative FIT), or exporting at a lower price than possible. For example, if the battery is 30% full at 10am and charging, then we may be missing a chance to export with a higher FIT than later in the day. Ideally we would do this without discharging the battery, purely using excess solar.

**Goal:** Improve earnings/reduce cost

**Status:** Implemented as "Solar Export Hold". When current FIT exceeds the solar-weighted average remaining FIT before the demand window AND solar surplus is confident (≥1.5× deficit to enter, ≥1.0× to stay via hysteresis), the battery holds at current SOC. Excess solar exports at the good FIT price instead of charging the battery. The hold releases when FIT drops below average or surplus becomes tight, allowing the battery to resume charging from solar.

**Key entities:**
- `binary_sensor.solar_export_hold_justified` — ON when all conditions met (sun up, before DW, surplus confident, FIT above average)
- `sensor.solar_weighted_avg_fit` — solar-production-weighted average FIT for remaining daylight hours (combines Solcast pv_estimate10 with Amber FIT forecast)
- `input_boolean.solar_export_hold_active` — tracks whether current hold is for solar export
- A17 (enter) / A18 (exit) — automations with 2-minute delay to prevent flapping

**Conservative design:** Full battery by demand window always takes priority. Solar export hold only activates with confident surplus and is blocked during charging, spikes, and demand window. Safety net (A6) and A10 updated to respect the new hold type.

**Complexity:** Implemented

---

## Bugs

### B1: Backup charging doesn't auto-upgrade to boost ✅ FIXED

**Problem:** When the battery is already in "backup" (gentle 3.3kW) charging mode, the system doesn't automatically switch to "boost" (5kW) charging even though `binary_sensor.boost_charge_needed` is ON. A3 won't re-trigger because it checks `not in any charging state` — since backup mode is already active, the condition blocks the upgrade.

**Fix:** A3's "not already charging" condition now allows re-triggering when in backup mode AND `boost_charge_needed` is ON (upgrade case). It still blocks if already in boost or already in the correct gentle mode. This means A3 can upgrade from gentle→boost on the next `time_pattern` tick.

### B2: Charge mode flip-flopping between boost and gentle ✅ FIXED

**Problem:** The automation flip-flops between `boost_charging` and `grid_charging` modes. Caused by SOC rising during charging → `boost_needed` flips to OFF → system drops to gentle → SOC growth slows → `boost_needed` flips back ON.

**Fix:** Added hysteresis latch to the `boost_needed` attribute in `sensor.solar_battery_forecast`. Once in boost mode (`autonomous + reserve > 99`), `boost_needed` stays `true` until the battery reaches the target SOC or the demand window starts. This prevents the calculation from flip-flopping based on incremental SOC changes during charging.

### B3: Force charge remains enabled after mode change to self_consumption ✅ FIXED (via B1/B2)

**Problem:** When the operation mode changes from `backup` to `self_consumption`, the force charge state appears to remain enabled.

**Root cause:** This was a symptom of B1 and B2. A3 was re-firing on the next `time_pattern` tick (within 1 minute) and re-engaging backup mode because the "not already charging" condition passed once the mode was `self_consumption`. Combined with B2's flip-flopping, this made it appear like force charge never turned off. The B1 fix (allowing A3 to upgrade from backup→boost but not re-fire when already in the correct mode) and B2 fix (boost hysteresis latch) should resolve this. Monitor to confirm.

### B4: Spike discharge allowed during demand window ✅ FIXED

**Problem:** A1 (spike discharge) and A5 (spike override charging) had no condition checking `demand_window_active`. During the demand window, a price spike would trigger force discharge, drain the battery, and the house would import from the grid for the rest of the window — exactly what the demand block is designed to prevent. The comment on A1 said "Blocked during demand window" but no condition existed.

**Fix:** Added `demand_window_active = off` condition to both A1 and A5. Spike discharge is now fully blocked during the demand window. Feature 7 (future) will add smart spike discharge with a dynamic SOC floor to allow profitable spikes while protecting demand window coverage.

### B5: Race condition between charge scripts and demand window start ✅ FIXED

**Problem:** The boost charge script has a 5-second delay between setting reserve=100 and setting mode=autonomous. If A8 (demand window start) or A9 (demand window enforcement) fired during this delay, they would call `battery_set_self_consumption`, but the still-running boost script would overwrite the mode to autonomous after its delay — resulting in autonomous+reserve=10 (discharge state) during the demand window.

**Fix:** A8 and A9 now call `script.turn_off` on all charge/discharge scripts before setting self_consumption, with a 2-second delay. This kills any mid-sequence scripts and prevents the overwrite race condition.

### B6: Safety net only catches stuck discharge ✅ FIXED

**Problem:** A6 only detected stuck autonomous discharge (reserve<11, no spike). If the system got stuck in backup (charging), boost (autonomous+reserve>99), or hold mode when conditions no longer justified it, there was no safety net.

**Fix:** A6 now uses a template condition to detect any unjustified state: discharge without spike, charging when price is above stop threshold or during demand window, hold when price is above stop threshold. Uses a 30-second re-check delay to avoid fighting with other automations. Notification message now reports which specific stuck state was detected.

### B7: Charging doesn't stop when justification changes (price still cheap) ✅ FIXED

**Problem:** When A3 starts grid charging because price is cheap AND a justification exists (battery low, expensive coming, or solar gap), and the justification later changes (e.g. Solcast updates so solar CAN now reach target, or battery fills past the precharge threshold, or target_reached flips on), the system stays in backup mode indefinitely. No automation detects the change: A3 blocks because already in backup (B1 fix), A4 doesn't fire because price is still below threshold, and A6 only checked price-based conditions.

**Symptom:** `select.my_home_operation_mode` stays `backup` while `sensor.battery_automation_active_mode` correctly shows `self_consumption`.

**Fix:** Added a justification-mismatch clause to A6's unjustified-state template. If the system is in a charging state (backup or boost) but `sensor.battery_automation_active_mode` evaluates to `self_consumption`, A6 detects the mismatch and returns the system to self consumption. This leverages the existing `active_mode` sensor which already mirrors A3's exact justification logic, avoiding code duplication.

### B8: Hold mode activates without justification (e.g. overnight) ✅ FIXED

**Problem:** A11 (Self Consumption → Hold) and A4's hold branch enter hold mode whenever the price is in the deadband zone, regardless of whether holding actually saves money. At 10pm with 40% SOC and price at 17c (deadband between 15c effective and 18c stop), the battery was held idle while the house drew from the grid — paying for imports when the battery could have powered the house. Hold only makes sense when there's a reason to preserve battery charge.

**Fix:** Added `binary_sensor.hold_justified` which checks two conditions within the forecast lookahead window:
1. Meaningful solar forecast (≥0.5 kWh pessimistic from Solcast, checking both today + tomorrow forecasts for overnight coverage)
2. Cheaper grid prices coming (Amber forecast shows any price below `effective_cheap_price`)

If neither is true, hold is unjustified — self consumption is better. The sensor gates A11, A4's hold branch, and A6's safety net now catches holds that become unjustified over time. The `active_mode` sensor's holding branch also reflects justification, feeding into B7's mismatch detection.

### B10: Solar forecast shows 0kWh when solar is actively producing ✅ FIXED

**Problem:** At 2:30pm with 96% SOC and 5kW+ solar production, the Solar Plan card showed "Solar forecast: 0.0kWh before 15:00", "Boost needed", and the system was treating the battery as unable to reach target from solar. The battery was 30 minutes from DW start and clearly on track to hit 100% from solar alone.

**Root cause:** `_sum_solar_before_target()` used `ps_local >= now_dt and ps_local.hour < target_hour` to filter Solcast 30-minute periods. At 14:31 with target_hour=15: the 14:30 period (`ps_local=14:30`) fails the `>= 14:31` check, and the 15:00 period fails the `15 < 15` hour check — resulting in zero periods matched and 0.0 kWh forecast. Additionally, periods already in progress weren't prorated.

**Fix:** Replaced hour-only comparison with full datetime comparison (`ps_local < target_dt`), and added prorating for the in-progress period. A period where `period_start < now < period_end` now contributes a fractional amount based on remaining time in the period.

### B11: Force discharge doesn't export to grid (Powerwall 3) ✅ FIXED

**Problem:** During price spikes, the component set `autonomous` + `reserve=10` to force discharge, but the Powerwall 3 started pulling FROM the grid instead of exporting. This affected both the YAML package and the custom component.

**Root cause:** The code assumed Powerwall 2 with a "dummy tariff" trick ($2/kWh fake sell price). The Powerwall 3 handles export differently — it requires the `allow_export` setting (via Teslemetry `select.my_home_allow_export`) to be set to `battery_ok` for the battery to export to grid. With the default `pv_only` or unset value, only solar excess exports; the battery never discharges to grid regardless of operation mode.

Additionally, all `async_set_*` methods were missing the 5-second delays between Teslemetry API calls that the YAML package had, causing race conditions where the Powerwall gateway processed commands out of order.

**Fix:**
1. Added `CONF_TESLEMETRY_ALLOW_EXPORT` as a configurable entity in config flow
2. `async_set_force_discharge()` now sets `allow_export=battery_ok` before `autonomous+reserve=10`
3. All other modes (`self_consumption`, `hold`, `force_charge`, `boost_charge`) set `allow_export=pv_only`
4. Added 5-second `asyncio.sleep()` delays between all Teslemetry service calls
5. Refactored service calls into `_set_export_mode()`, `_set_operation_mode()`, `_set_backup_reserve()` helpers

---

### B9: Grid charging when solar is on track (battery_low override) ✅ FIXED

**Problem:** At 8:24am with 12% SOC and 34 kWh net solar forecast (3x the 12 kWh needed), A3 grid charged at 3.3kW because `battery_low` (12% < 50% precharge threshold) was a standalone daytime justification. The Solar Plan card correctly showed "✅ On track" but the charging logic ignored this. Wasted money on grid imports when free solar would comfortably fill the battery.

**Fix:** Removed `battery_low` as a standalone daytime charging justification. The daytime logic changed from `battery_low OR expensive_coming OR solar_gap` to `solar_gap OR expensive_coming`. When `solar_can_reach_target` is ON, the system trusts the Solcast forecast (which already accounts for current SOC and consumption) and waits for solar. Grid charging during daytime now only triggers when there's a genuine solar gap or when cheap arbitrage before a spike is justified. Overnight logic unchanged (still requires `battery_low AND expensive_coming`). Updated `active_mode` sensor to mirror the new logic.

### B12: Daily summary timer doesn't update when options change

**Problem:** The daily summary notification timer is set once at coordinator startup. If the user changes the demand window end time in the integration options, the coordinator needs to be restarted to pick up the new time.

**Fix:** Not yet implemented. Would require listening for config entry option changes and rescheduling the timer.

### B13: target_reached_today flag not persisted across restarts

**Problem:** The `target_reached_today` flag is stored in memory. If Home Assistant restarts mid-day after the battery target was already reached, the flag resets and the system might unnecessarily try to charge the battery again.

**Fix:** Not yet implemented. Would require persisting to config entry options or using HA's storage mechanism.

