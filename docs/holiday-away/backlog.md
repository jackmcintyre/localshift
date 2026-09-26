# Holiday away — backlog

Build in order on one stacked branch; each task builds on the one before. The spec is
`docs/holiday-away/plan.md`, and the rules are in `AGENTS.md`: TDD, coverage at 95% or above,
type hints. For the planner-floor change, consult `docs/PLANNING_MODEL.md`. Never deploy.

- [ ] 1. Add an optional "away entity" option to LocalShift's config and options flow, as specified in docs/holiday-away/plan.md "What to build" item 1.
  - It names any on/off entity; the house will set `input_boolean.holiday_mode`.
  - Add the constant in `const.py`, the schema in `config_flow/schemas.py` and validation in `config_flow/validators.py`, plus a small helper that reports whether away is active (the entity is on).
  - Unset means no behaviour change.
  - Write the failing tests first.
  - Acceptance: every existing test passes unchanged, new tests cover set, unset and a missing entity, and coverage stays at 95% or above.

- [ ] 2. Keep away hours out of learning, as specified in docs/holiday-away/plan.md "What to build" item 3.
  - When the away entity is configured, read its on/off history from the recorder for the learning windows.
  - Leave the hours it was on out of the 28-day consumption statistics in `forecast/history.py`.
  - Leave the same hours out of the 30-day weather correlation in `learning/correlation.py`.
  - Tests first.
  - Acceptance: tests show away hours excluded from both windows and profiles unchanged when no away entity is set, with coverage at 95% or above.

- [ ] 3. Forecast the empty house while away, as specified in docs/holiday-away/plan.md "What to build" item 2.
  - While away is active, the historical profile used by `forecast/load.py` becomes the away profile (hourly means over past away hours, once there are enough) or, until then, the overnight floor: the 00:00–03:00 mean of the at-home profile, flat across the day.
  - Keep the live three-hour blend and the weather adjustment.
  - Report `away_active`, the profile source and the masked-hour count on the forecast diagnostics sensor.
  - Tests first.
  - Acceptance: tests cover both profile sources and the switch back when away ends, with coverage at 95% or above.

- [ ] 4. Add the away reserve, as specified in docs/holiday-away/plan.md "What to build" items 4–5.
  - Add a new number entity, "Away reserve": default 30%, range 10–80%.
  - While away is active, the self-consumption reserve in `integration/controller.py` is `max(away reserve, preserve_soc or 10)`, and the planner never plans discharge below it. Add that as a feasibility rule in `feasible_actions()` per `docs/PLANNING_MODEL.md`.
  - Release both when away ends.
  - Update `docs/ENTITY_REFERENCE.md`.
  - Tests first.
  - Acceptance: tests cover the reserve, the planner floor and the release, respect `BACKUP_RESERVE_MAX_VALID`, and keep coverage at 95% or above.
