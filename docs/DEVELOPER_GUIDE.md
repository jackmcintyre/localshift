# Developer Guide

This guide covers the technical architecture of the LocalShift integration for developers who want to understand, extend, or contribute to the codebase.

## Project Structure

```
custom_components/localshift/
├── __init__.py               # Integration entry point, creates coordinator
├── const.py                  # Constants, enums, entity keys, defaults
├── computation_engine.py     # Orchestrates forecast + optimizer execution
│
├── coordinator/              # Data coordination
│   ├── coordinator.py        # LocalShiftCoordinator: tiered ticks, event handling
│   └── data.py              # CoordinatorData, OptimizerResult dataclasses
│
├── engine/                  # DP Optimization engine
│   ├── optimizer_dp.py      # Core DP solver (feasible_actions, stage_cost, terminal_cost)
│   ├── optimizer_facade.py  # Facade: slot building, corrections, planner, runtime mode
│   ├── optimizer_runner.py   # Helpers around a DP optimizer run (library)
│   ├── constraints.py       # Hard constraint functions (feasible_actions)
│   ├── cost.py              # Cost function components (stage_cost, terminal_cost)
│   ├── types.py             # Type definitions (SlotContext, OptimizerConfig, etc.)
│   ├── core.py              # Core optimizer logic
│   ├── parameters.py       # Adaptive parameter management (Thompson sampling)
│   ├── outcomes.py          # Decision outcome tracking
│   ├── pattern_analyzer.py   # Bias detection (weekly patterns)
│   ├── counterfactual.py    # TOU baseline scoring
│   ├── optimization_controller.py # Real-time contextual adjustments
│   ├── slots.py             # Slot building (SlotBuilder, SlotBuildMetadata)
│   ├── slot_schedule.py     # Hybrid slot schedule (5-min + 15-min)
│   ├── price_calculator.py  # Price calculations
│   ├── price_signal_engine.py # Price signal orchestration
│   ├── excess_solar.py      # Excess solar detection
│   ├── excess_solar_signals.py # Load shift signals
│   ├── soc_simulator.py     # SOC simulation
│   ├── spike_analyzer.py    # Price spike detection
│   ├── dp_math.py           # DP math utilities
│   ├── weather_diagnostics.py # Weather diagnostics
│   └── utils.py             # Engine utilities
│
├── forecast/                # Forecasting modules
│   ├── pipeline.py          # Forecast orchestration
│   ├── load.py              # Load forecasting
│   ├── solar.py             # Solar calculations
│   ├── accuracy.py          # Forecast accuracy engine
│   ├── solar_accuracy.py    # Solar accuracy tracking
│   ├── history.py           # Historical data fetching
│   ├── history_store.py     # Forecast history storage
│   ├── bootstrapper.py      # Forecast initialization
│   ├── load_deviation.py    # Real-time load deviation detection
│   └── solar_events.py      # Mid-day solar re-optimization detection
│
├── integration/             # External integrations
│   ├── controller.py        # Battery controller (Teslemetry)
│   └── client.py            # Powerwall service client
│
├── learning/                # Adaptive learning system
│   ├── orchestrator.py      # Learning system coordinator
│   ├── correlation.py       # Weather correlation regression + storage facade
│   ├── temperature.py       # Weather forecast fetching/parsing/caching
│   └── anomaly.py           # Weather anomaly detection
│
├── services/                # Core services
│   ├── evaluation_dispatcher.py # Decides when to trigger re-evaluation
│   ├── notification_service.py  # Notification dispatch
│   └── subscription_manager.py # Entity subscriptions and timers
│
├── state/                   # State machine
│   ├── machine.py           # StateMachine: state evaluation and transitions
│   ├── mode_configs.py      # Per-mode configuration and executor mapping
│   ├── reader.py            # External entity reader
│   └── validator.py         # Transition validator
│
├── sensors/                 # Sensor implementations (organized by domain)
│   ├── base.py              # Base sensor class
│   ├── pricing.py           # Price-related sensors (3 sensors)
│   ├── forecast.py          # Forecast/optimizer sensors (9 sensors)
│   ├── status.py            # Status/health sensors (7 sensors)
│   ├── learning.py          # Learning-related sensors (4 sensors)
│   ├── optimizer.py         # Optimizer-specific sensors (3 sensors)
│   ├── misc.py              # Miscellaneous sensors (2 sensors)
│   ├── load_deviation.py    # Load deviation sensor (1 sensor)
│   └── cloud_event.py       # Cloud event sensor (1 sensor)
│
├── utils/                   # Shared utilities
│   ├── validation.py        # Entity validation
│   ├── costs.py             # Cost tracking
│   └── entity_configs.py    # Entity configuration helpers
│
├── config_flow/             # HA configuration flow
│   ├── __init__.py          # Config flow entry point
│   ├── schemas.py           # Config schemas
│   └── validators.py        # Config validators
│
├── *.py (HA entity platforms - root level per HA convention)
│   ├── sensor.py            # 30 sensor entities (delegates to sensors/ package)
│   ├── binary_sensor.py     # 10 binary sensor entities
│   ├── switch.py            # 8 switch entities
│   ├── number.py            # 4 number entities
│   ├── select.py            # 2 select entities
│   └── button.py            # 2 button entities
│
├── manifest.json            # HA manifest
├── strings.json             # Localization strings
├── dashboard.yaml           # Dashboard configuration
└── AGENTS.md                # Agent rules for this integration
```

## Key Concepts

### Battery Modes

The system operates in one of these modes (defined in `const.py`):

| Mode | Operation Mode | Reserve | Description |
|------|---------------|---------|-------------|
| `SELF_CONSUMPTION` | self_consumption | 10% | Default — battery powers house |
| `GRID_CHARGING` | backup | 10% | Grid charging at 3.3kW |
| `BOOST_CHARGING` | autonomous | 100% | Fast charge at 5kW |
| `SPIKE_DISCHARGE` | autonomous | 10% | Export during price spikes |
| `PROACTIVE_EXPORT` | autonomous | dynamic | Export before negative FIT |
| `DEMAND_BLOCK` | self_consumption | 10% | Self-consumption enforced |
| `MANUAL` | — | — | Manual control (automation disabled) |

Mode selection is driven by the DP optimizer with safety gates. See [PLANNING_MODEL.md](PLANNING_MODEL.md) for details.

### Optimization Modes

| Mode | Description |
|------|-------------|
| `auto` | Full automation with DP optimizer |
| `eco` | Economic optimization (cost-focused) |
| `self_consumption` | Maximize self-consumption only |

### Entity Architecture

The integration creates 56 entities:

| Platform | Count | Notes |
|----------|-------|-------|
| Sensors | 30 | Implementation in `sensors/` package |
| Binary Sensors | 10 | In `binary_sensor.py` |
| Switches | 8 | In `switch.py` |
| Numbers | 4 | In `number.py` |
| Selects | 2 | Battery mode, optimization mode |
| Buttons | 2 | Update forecast, reset learning |

Sensors are organized by domain in the `sensors/` package for maintainability.

## Extension Points

### Adding a New Sensor

1. Create sensor class in appropriate `sensors/*.py` file
2. Register in `sensor.py` platform `async_setup_entry`
3. Add entity key to `const.py` ENTITY_KEYS
4. Update [ENTITY_REFERENCE.md](ENTITY_REFERENCE.md)

Example for a pricing sensor:
```python
# sensors/pricing.py
class EffectiveCheapPriceSensor(LocalShiftSensor):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:currency-usd"
    _attr_state_class = SensorStateClass.MEASUREMENT
    
    @property
    def native_value(self) -> float | None:
        return self.coordinator.data.get("price_cheap_effective")
```

### Modifying the Optimizer

Any optimizer changes MUST consult [PLANNING_MODEL.md](PLANNING_MODEL.md) first:

| Question | Answer → |
|----------|----------|
| Impossible/forbidden? | Add to `feasible_actions()` |
| Required by deadline? | Add to `terminal_cost()` |
| Discouraged/preferred? | Add penalty to `stage_cost()` |

Key files:
- `engine/optimizer_dp.py` — DP solver
- `engine/constraints.py` — Hard constraints
- `engine/cost.py` — Cost functions
- `engine/types.py` — Type definitions

### Proving a change is behaviour-neutral

For dead-code removal, refactors and renames: changes that are meant to leave
every plan and every command exactly as they were. Passing tests are not
enough evidence here, for two reasons this repo has already paid for.

- **The plan and command paths catch broadly and carry on.** A removed
  constructor argument once made the inline optimizer raise on every cycle;
  `OptimizerFacade.run_inline` logged a warning and the suite stayed green.
- **Name-based "is it used?" analysis is unsound.** `SlotBuilder` reaches the
  facade as an injected class reference, and the state machine dispatches
  through string tables (`MODE_CONFIG_BUILDERS`, `MODE_EXECUTORS`) and
  `getattr`. A `grep` for the call site finds nothing and proves nothing.

Two gates cover it, and both run in CI (the second as its own workflow,
`Replay baseline`).

**1. The swallowed-failure tripwire** (`tests/swallow_tripwire.py`, wired in
`tests/conftest.py`). Any test during which a guarded `except` block catches
and logs a failure fails, naming the block and what was swallowed. It needs
nothing from you unless a test is *about* that failure path, in which case it
says so:

```python
@pytest.mark.allow_swallowed_failure("inline_optimizer")
def test_run_inline_logs_and_swallows_unexpected_exception(): ...
```

An opt-in that the test never reaches fails too. If the tripwire fires on a
test you did not expect, do not add the marker to make it pass: either the
change broke something the production code hid, or the test's fixture was
already broken and nobody could tell. The guarded blocks are listed in
`TRIPWIRES`; add one when you add a swallow-and-continue `except` on the plan
or command path.

**2. The planner replay baseline** (`scripts/replay_plan_baseline.py`,
baseline in `simulations/plan-baseline/`). Every tracked capture under
`simulations/` is replayed through `ComputationEngine.compute_derived_values`
under three arms, and the plan (every slot's action, SOC and inputs, the
commanded mode, the published summary) is compared with the checked-in one.

```bash
uv run scripts/replay_plan_baseline.py           # compare; exit 1 and a diff on any change (about 90 s)
uv run scripts/replay_plan_baseline.py --write   # regenerate the baseline
```

What each arm covers:

| Arm | Inputs | What it reaches that the others do not |
|---|---|---|
| `captured` | The capture, with the configuration recorded in it. | The planner as each capture's own settings drive it, including under-target demand-window entry. |
| `strict` | The capture, with strict demand-window entry and the live tuning of 2026-09-08. | The hard target floor, the urgency window, the pre-charge backstops, the next-window tail. |
| `live_like` | The `strict` arm, plus the learned inputs the live planner always has and a bare capture lacks: a seeded `SolarAccuracyTracker`, load history, a Solcast analysis for today and tomorrow, and the day's weather. | Solar bias correction (`OptimizerFacade._apply_bias_correction_to_slots`, `SolarAccuracyTracker.apply_bias_correction` and its context ladder), the accuracy discount (`engine/solar.get_forecast_accuracy` and its readers), the accuracy confidence ceiling and the over-forecast cap, the slot builder's "correction is ready" branch, per-day and weekday/weekend load profiles (`LoadForecaster._resolve_daily_profile`, every rung), the load-forecast ceiling, and one evaluation with no decision grant (`no_grant`). |

The first two arms have no tracker and no load history, so before the third
arm existed a deletion anywhere in that last cell left `plans identical`
untouched. The live system has 800-odd solar samples with correction active,
so it runs that code on every cycle.

The `live_like` inputs come from fixed tables at the top of the script and
are dated from each capture's own instant; nothing is random and no clock is
read. Its record carries a `coverage` section (how many slots bias correction
moved, the measured accuracy, the confidence ceiling stamped, how many load
slots each profile rung fed) and the script adds those up across the run,
prints them, and **fails a full run that falls below `COVERAGE_FLOORS`**. An
arm whose plans still match but which has stopped exercising the code it is
there for is a failure, not a pass. If you change the seed tables, run
`--write` and expect every `live_like` record to move; the first two arms
must not.

What is still **not** proven by `plans identical`, on any arm:

- **The state machine and the command path.** The replay ends at the mode the
  facade commits. `state/machine.py`, the coordinator's handlers, the mode
  executors and anything that talks to the Powerwall are not run. The
  `no_grant` evaluation covers the facade's side of the decision token only
  (the mode is held, the plan's mode is published as pending); the state
  machine's side, which decides when a grant is raised and consumes it, is
  not.
- **Anything that needs more than one cycle of real time.** Each replay is one
  instant, evaluated two or three times. Dwell timers, the backstop hold
  across cycles, the daily latch reset, the tracker's own record/backfill
  loop and the recorder fetch that builds the load profiles are seeded or
  skipped, not exercised.
- **Solar confidence where the capture has no spread.** Most replay captures
  carry a P10 equal to the median, so the confidence ceiling is stamped and
  recorded but has nothing to blend toward. It moves plans only on the
  captures that have a spread (the hand-written scenarios, mainly).
- **Weather-driven load, away mode, cloud events.** No weather correlation is
  attached, no away profile is injected, and no cloud-event scale factor is
  set.
- **Sensors, services, config flow, storage.** Not on this path at all.

For a deletion in one of those areas, the replay passing is necessary and
not sufficient: say in the PR what else shows the code was unreachable.

How a deletion PR uses it:

1. Before touching anything, run the comparison on a clean checkout of
   `main`. It must report `plans identical`. If it does not, stop: the
   baseline is stale and that is somebody else's change to explain.
2. Make the deletion. Run the comparison again. It must still report
   `plans identical`, with no baseline file modified. Quote that line, and the
   `live_like coverage` block under it, in the PR.
3. If it reports a difference, the deletion was not dead code. Do not
   regenerate the baseline to make it pass.

A PR that *means* to change plans runs `--write` and commits the result; the
baseline diff is then the reviewable statement of what changed, slot by slot.
CI runs the full comparison on every PR (`.github/workflows/replay-baseline.yml`),
so all 84 captures are enforced, on Linux, not only on the machine that wrote
the baseline. `tests/test_replay_plan_baseline.py` also runs four captures of
it, under all three arms, inside the test suite (about five seconds), so a
moved plan shows up in an ordinary local `pytest` run too. The same file
checks that the `live_like` arm is sensitive to its seeds (halving the seeded
solar bias, swapping the weekday and weekend load shapes, or never attaching
the tracker each change a plan) and that the script names nothing on the
dead-code list.

The replay pins every clock, Home Assistant's default time zone and the
process's own time zone (all to Australia/Sydney, where the live system runs),
and is byte-identical from run to run on the same code. Add a capture to one
of the tracked directories and run `--write` to bring it under the gate.

**What "plans identical" does not prove.** It is evidence about the path the
replay drives, and that path has gaps:

- **Hour-of-day load profiles are not exercised.** The replay empties the
  load history, so every forecast falls back to the recent-load figure. A
  deletion in `forecast/load.py`, `forecast/history.py` or the weekday,
  weekend and per-day profile code would leave every plan untouched and still
  be wrong. Those need their own tests.
- **The decision-token gate is not covered.** Each replayed cycle is handed a
  decision grant (`mode_decision_allowed=True`), so the commanded mode is what
  the facade commits when it is allowed to decide. Whether the state machine
  grants a decision at the right moments, and what is held while it does not,
  is outside the replay.
- **Nothing downstream of the plan is covered**: the state machine's
  transitions, debounce, the commands sent to the battery, sensors and
  services. The tripwire's `mode_transition` path and the state tests are the
  cover there.
- **It is 84 instants.** A code path none of them reaches is not held still
  by any of this. The swallowed-failure column narrows that (a path that
  newly fails is seen even if the plan survives), but does not close it.

### Changing Evaluation Triggers

Evaluation triggers are defined in `services/evaluation_dispatcher.py`:
- State change triggers
- Stale price detection
- Load deviation detection (>1kW for 10min, >3kW for 5min)
- Solar event detection

### Config Flow Changes

Config flow is in `config_flow/__init__.py`:
- Initial setup: 3 steps (user, entity mapping, settings)
- Options flow: 2 steps (entity_mappings, settings)

## Key Sensors

Important sensors for debugging:

| Sensor | Purpose |
|--------|---------|
| `sensor.localshift_optimizer_plan` | Current optimization plan |
| `sensor.localshift_optimizer_plan_grid` | Grid view of plan |
| `sensor.localshift_optimizer_plan_detailed` | Detailed per-slot decisions |
| `sensor.localshift_forecast_battery` | Battery SOC forecast |
| `sensor.localshift_cost_electricity_net` | Net electricity cost |
| `sensor.localshift_decision_log` | Recent optimizer decisions |
| `sensor.localshift_learning_decision_history` | Recorded decisions and outcomes (telemetry) |
| `sensor.localshift_automation_ready` | System ready for automation |

## Code Style

The project follows these conventions:

- **Python**: 3.13+ with type hints
- **Type hints**: All functions have type annotations
- **Docstrings**: Google-style docstrings for public methods
- **Logging**: Use `_LOGGER.info/debug/warning/error`. INFO is for a state
  change or something actionable (a pre-charge decision, a backstop firing,
  a mode transition); DEBUG is for per-cycle or intermediate state that
  fires every optimizer tick regardless of whether anything changed.
  Issue-tagged `ISSUE_<n>` diagnostics added while chasing a bug must never
  ship at INFO/WARNING — `tests/test_logging_patterns.py` statically blocks
  this, and demoting a message means updating any test that asserts on it
  at the old level (issue #976).
- **Constants**: All magic numbers in `const.py`
- **Async**: Use `async/await` throughout, no blocking calls

## Prerequisites for Development

1. **Home Assistant**: A running HA instance (dev or production)
2. **Python**: 3.13+ (required for type hints)
3. **Tools**: `uv` for package management

### Local Development

```bash
# Clone the repository
git clone https://github.com/jackmcintyre/localshift.git

# Set up development environment
cd localshift
uv sync

# Run tests
uv run pytest

# Lint
uv run ruff check custom_components/localshift
```

### Deployment

When running locally with Home Assistant:

```bash
# Deploy to HA custom_components
./deploy.sh --reserve && ./deploy.sh

# Dry run
./deploy.sh --dry-run
```

The `deploy.sh` script copies files to HA and reloads the integration.

## Testing

Tests are in `tests/` directory:

```bash
# Run all tests with coverage
uv run pytest --cov=custom_components/localshift --cov-report=term-missing

# Run specific test file
uv run pytest tests/test_optimizer_dp_solve.py -v

# Run with verbose output
uv run pytest -vvs
```

See [tests/AGENTS.md](tests/AGENTS.md) for test patterns.

## Dead-code ratchet

CI blocks on `uv run python scripts/dead_code_gate.py` (Lint workflow, issue
#986). It is a ratchet: the amount of unused code is allowed to go down and
never up.

### How it works

Two detectors run over `custom_components/localshift`:

- **vulture** at confidence 60 (`[tool.vulture]` in `pyproject.toml`).
- **A reader census for `CoordinatorData`**: a field that no production code
  reads is reported as `unread-field`. Tests and scripts do not count as
  readers, because a test that asserts a field's value mirrors the field
  rather than consuming it (issue #981).

Every finding has to be in exactly one of two checked-in files:

| File | Meaning | Grows? |
|------|---------|--------|
| `vulture_whitelist.py` | Live, but reached dynamically. Each entry cites the site that reaches it. | Only with evidence |
| `dead_code_baseline.txt` | Unused and not yet deleted, wired up, or shown to be live. Debt. | Never |

The gate fails in both directions:

- **New dead code**: a finding that is in neither file.
- **Stale baseline**: a baseline entry that is no longer found. Whoever
  deletes dead code shrinks the baseline in the same PR, so it cannot quietly
  regrow later.

It also fails when a whitelist entry stops being true (below). Findings are
keyed by kind, file and qualified name, not line number, so editing a file
does not disturb its entries.

### Why a whitelist is needed at all

A static scan cannot see a call made by name, and this integration makes its
most important calls that way. `StateMachine._build_*_config` and
`_execute_*_transition`, the methods that write to the battery, are reached
through the string tables in `state/mode_configs.py` and `getattr`. Home
Assistant calls `async_setup_entry`, `extra_state_attributes` and the like by
name. A scan reports all of them as unused.

So the whitelist is evidence, not a mute button. Each entry carries `# file:`
and `# kind:` (what it is and where it is defined), a `# site:` (a file that
reaches the symbol), an `# evidence:` (text that must still be in that file)
and a `# why:`. The gate re-checks the evidence on
every run: delete the dispatch-table row for a builder and the gate fails,
telling you the method is now dead. Home Assistant names are checked against
Home Assistant itself by `tests/test_dead_code_gate.py`.

An entry covers one kind of symbol in one file (`# file:`, `# kind:`), matched
by qualified name. `ForecastSlot.estimate` does not hide any other `estimate`,
and a second `StateMachine` in another file is not covered.

`# framework:` blocks are for what Home Assistant calls by name, and they
accept exactly two shapes:

- `_.member`: a method or property of a class that derives from a
  `homeassistant` import. The test suite checks that every class it covers is
  a real subclass of the framework class named and that the class defines the
  member.
- a bare name: a module-level hook such as `async_setup_entry`, matched only
  in the modules Home Assistant loads by name (`__init__.py` and the
  `PLATFORMS` modules).

Anything else under `# framework:` is an error. It is not a way around the
site and evidence rules.

Two conventions Home Assistant defines by pattern are applied by the gate
itself, scoped the same way: `_attr_*` attributes on classes that derive from
Home Assistant, and `async_step_*` methods on config and options flow classes.
A new entity that sets `_attr_translation_key` needs no whitelist edit; an
`_attr_foo` on a class that is not an entity is reported.

Two findings that share a key (several write sites of one dead attribute, two
dead functions of the same name) are listed as `name`, `name@2`, `name@3`, so
adding another one is seen.

### What the gate does not do

- **It is a red check, not a merge block.** `main` has no branch protection,
  so a failing gate does not stop a merge. Somebody has to look at it.
- **It reports unused names, nothing finer.** A parameter, or a class only
  ever built through an injected reference (`self._slot_builder_cls(...)` in
  `engine/optimizer_facade.py`), is not something it can vouch for. Removing
  "unused" pieces of such a class is how the inline optimizer was once
  silently disabled. The gate keeps a deletion honest about what was removed;
  it does not prove the deletion was safe. Run the scenario tests.
- **A container field that is only written to counts as read.** The census
  treats any load of `data.<field>` as a read, and `data.log.append(x)` loads
  `data.log`.
- **`dataclasses.asdict` / `replace` over `CoordinatorData` are not
  modelled.** Nothing uses them today. If something starts to, the fields it
  serialises would be reported unread and the gate would fail loudly rather
  than pass quietly; teach the census the new form then.
- **It cannot force the removal of a computation that feeds a write-only
  field.** It reports the field and its write sites. The method that works the
  value out is still called, so it is not reported
  (`_update_hybrid_accuracy` feeding `hybrid_solar_accuracy` was the
  example until #981 removed both). Deleting the field means deciding about that method too.
- **If `coordinator/data.py` or `CoordinatorData` is renamed** the census
  finds no fields and the gate fails, rather than passing with the census
  switched off. Update the defaults at the top of the script.

### What to do when it fails

**"NEW DEAD CODE"** names a symbol you added or orphaned. Pick one:

1. It is dead: delete it.
2. It was meant to be called: wire it up. A fix that was written but never
   connected looks exactly like this (issues #959, #968).
3. It is live through dynamic access: add it to `vulture_whitelist.py` with
   its file, kind, site, evidence and reason.

Do not add it to the baseline. `--update` refuses to.

**"BASELINE IS STALE"** means you removed or wired up something that was
listed as dead. Run the update and commit the result:

```bash
uv run python scripts/dead_code_gate.py --update
```

**"WHITELIST PROBLEMS"** means an entry no longer matches a symbol, or the
dynamic access it cites has gone. Remove the entry, and delete the symbol if
nothing reaches it any more.

### A PR that deletes dead code

1. Pick entries from `dead_code_baseline.txt`. Lines with a trailing `# note`
   were flagged as possibly unwired fixes rather than dead code: decide those
   before deleting them.
2. Delete the symbol, its writers, and the tests that only exercised it.
   Check how it is reached first: `grep` for the name as a string, not just as
   an identifier.
3. `uv run python scripts/dead_code_gate.py --update` and commit the smaller
   baseline with the deletion.
4. Run the full test suite.

### Commands

```bash
uv run python scripts/dead_code_gate.py              # the CI check
uv run python scripts/dead_code_gate.py --summary    # counts by kind
uv run python scripts/dead_code_gate.py --update     # shrink the baseline
uv run vulture                                       # raw view; red by design, not the pass/fail
```

`--update --allow-additions` adds new findings to the baseline. It exists for
one case: refreshing the baseline after merging a branch that legitimately
changed what is reported. It is not the way to get a PR green.

## Architecture Overview

See [ARCHITECTURE.md](ARCHITECTURE.md) for detailed system architecture.

## Related Documentation

- [PLANNING_MODEL.md](PLANNING_MODEL.md) - Optimizer design (MUST READ for engine changes)
- [ENTITY_REFERENCE.md](ENTITY_REFERENCE.md) - Complete entity catalog
- [ARCHITECTURE.md](ARCHITECTURE.md) - System architecture diagrams
- [INDEX.md](INDEX.md) - Documentation index
