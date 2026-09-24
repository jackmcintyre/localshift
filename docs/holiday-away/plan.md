# LocalShift — away-aware (holiday mode, slice 5)

The spec for the LocalShift half of the house's holiday mode. The source copy lives in the
`homeassistant-100h` repo at `docs/holiday/slice-5-localshift.md`. The run copy is
`docs/holiday-away/plan.md` in the LocalShift worktree. Decided by Jack on 24 Sep 2026.

## Why

LocalShift forecasts load from the mean of 28 days (`HISTORY_WINDOW_DAYS` in `const.py`) of
`sensor.my_home_load_power` statistics, per hour and per day of week. That's about four samples
per weekday hour (`forecast/history.py`). Live load only blends in for about three hours ahead,
with a decay factor of 0.8 per hour (`forecast/load.py`). Weather correlation uses a 30-day
sliding window (`learning/correlation.py`, `SLIDING_WINDOW_DAYS`). Nothing knows the house is
empty, which causes two problems:

- **During a trip it plans for the family.** The weekday profile sums to about 25.6 kWh a day,
  against roughly 10 kWh for an empty house (a 0.35–0.5 kW floor: network, servers, cameras,
  fridge). It still holds 95% for the demand window, and pre-charges from the grid under
  $0.20/kWh to get there.
- **After a trip it plans for too little.** Each day away becomes one of the four samples for
  its weekday. A week away drops every weekday's forecast by about 15% for four weeks, and two
  weeks by about 30%. The battery then enters the demand window short, which means grid import
  inside it.

The house also wants a bigger backup reserve while away, so an outage at night doesn't take the
cameras, network and HA down with it. LocalShift already owns the reserve: it steers the
Powerwall through `number.my_home_backup_reserve` (`integration/controller.py`). Self-consumption
sets 10% (or `preserve_soc`), and force charge sets the target. So the away reserve must come
from LocalShift too; a second writer would fight it.

## What to build

1. **An "away entity" option.** A config-flow/options entry naming any on/off entity as away.
   The house points it at `input_boolean.holiday_mode`. It stays generic, so LocalShift has no
   dependency on the house's package. Unset means today's behaviour, exactly.
2. **Empty-house forecast while away.** While the away entity is on, the historical profile is
   replaced by the house's own away profile: hourly means over past away hours, once there are
   enough of them. Until then it falls back to the overnight floor, the mean of 00:00–03:00 from
   the at-home profile, flat across the day. Keep the live three-hour blend and the weather
   adjustment on top.
3. **Away hours kept out of learning.** Hours the away entity was on are masked out of the
   28-day consumption statistics and the 30-day weather correlation. Read the entity's on/off
   history from the recorder, which the house keeps for 90 days. The house's return then
   doesn't inherit a quiet month.
4. **Away reserve.**
   - A new number setting, "Away reserve", default 30%, range 10–80%. The Tesla firmware
     clamps 81–99%, so respect `BACKUP_RESERVE_MAX_VALID`.
   - While away, the self-consumption reserve is `max(away reserve, preserve_soc or 10)`, and
     the planner never plans discharge below it.
   - When away turns off, both return to normal on the next cycle.
   - This touches the planner's floor: consult `docs/PLANNING_MODEL.md` first. A floor is a
     feasibility rule, so it goes in `feasible_actions()`.
5. **Visibility.**
   - The existing forecast diagnostics sensor reports `away_active`, the profile source used
     (`away_profile` or `away_floor`), and how many hours were masked out of learning.
   - Update `docs/ENTITY_REFERENCE.md` for the new option and entity.

## Rules (from AGENTS.md)

- TDD: a failing test first, then the implementation.
- Coverage stays at 95% or above.
- Never commit to `main` or `test`.
- Type hints on everything.
- Don't deploy. Deploying is attended, through the `deploy-and-validate` skill.

## Acceptance

- With no away entity configured, every existing test passes unchanged.
- Tests show:
  - the forecast switching to the away profile or floor while away;
  - away hours excluded from both learning windows;
  - the self-consumption reserve and the planner floor honouring the away reserve, and
    releasing when away turns off.
- Live check (attended, after deploy): a holiday-on weekend.
  - The forecast drops to the empty house within the hour.
  - The reserve holds at the away value.
  - The following week's weekday profiles match the weeks before the trip.
