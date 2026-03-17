---
name: doc-first
description: Automatically check relevant documentation before code analysis tasks
compatibility: opencode
metadata:
  triggers:
    - "analyze"
    - "review"
    - "refactor"
    - "explain"
    - "understand"
    - "how does"
    - "what is"
    - "why"
    - "change optimizer"
    - "modify optimizer"
    - "add entity"
    - "remove entity"
    - "update entity"
  actions:
    - determine_context
    - identify_relevant_docs
    - read_docs
    - summarize_constraints
    - proceed_with_context
---

## What I Do

I enforce a documentation-first workflow. When you ask an agent to analyze, review, refactor, or modify code, I automatically:

1. Determine what domain you're working in (optimizer, entities, state machine, etc.)
2. Identify the critical docs you must read first
3. Read those docs and extract key constraints
4. Present a summary before any code analysis begins

This prevents agents from diving into code without understanding the architectural constraints and patterns.

## When to Use Me

My triggers are broad - I activate on almost any code-related question:

- "Analyze this function"
- "Review the optimizer code"
- "Refactor the state machine"
- "Explain how entities work"
- "How does the DP solver work?"
- "What constraints exist for grid charging?"
- "Add a new sensor entity"
- "Change the planning model"

If you're asking about code, I'll make sure docs are consulted first.

## How I Work

### Step 1: Context Determination

I examine the task to identify the domain:
- Keywords: "optimizer", "DP", "planner", "feasible_actions" → optimizer domain
- Keywords: "entity", "sensor", "switch", "binary_sensor" → entity domain
- Keywords: "state", "machine", "transition", "mode" → state machine domain
- Keywords: "forecast", "prediction", "solar", "load" → forecast domain
- Keywords: "config", "flow", "options" → config flow domain

### Step 2: Doc Identification

Based on domain, I consult the documentation index:

| Domain | Must-Read Docs | Also Useful |
|--------|---------------|-------------|
| Optimizer | PLANNING_MODEL.md | OPTIMIZER_DP_ROLLOUT.md, TROUBLESHOOTING.md |
| Entities | ENTITY_REFERENCE.md, AGENTS.md | ARCHITECTURE.md |
| State Machine | ARCHITECTURE.md | TROUBLESHOOTING.md |
| Forecast | ARCHITECTURE.md, FORECAST_DRIVEN_CONTROL.md | LOAD_FORECASTING.md |
| General | ARCHITECTURE.md | DEVELOPER_GUIDE.md |

### Step 3: Extract Key Constraints

I don't just dump the whole doc. I extract specific constraints:

**For optimizer changes:**
- Hard constraints → `feasible_actions()`
- Deadlines → `terminal_cost()`
- Preferences → `stage_cost()` penalties
- Pure/stateless requirement

**For entities:**
- Platform file patterns (sensor.py, switch.py)
- Entity count limits
- Coordinator-read-only pattern
- Update ENTITY_REFERENCE.md requirement

**For state machine:**
- Debounce timer rules
- Validation timeout values
- Health check cooldowns
- Transition safety gates

### Step 4: Present Summary

Before analysis, I'll tell you:
- "This task involves the DP optimizer. You must consult PLANNING_MODEL.md first."
- "Key constraints: [bullet list of critical rules]"
- "Required pattern: [specific implementation pattern]"
- "Anti-patterns to avoid: [list]"

Then the agent proceeds with code analysis INFORMED by the docs.

## Example Workflow

**User:** "Can you refactor the optimizer to simplify stage_cost()?"

**Me:**
```
📚 Documentation Check Required

Domain: DP Optimizer
Must-read: docs/PLANNING_MODEL.md

Key constraints extracted:
• Hard constraints → feasible_actions() only
• stage_cost() is for soft preferences (penalties)
• Keep optimizer pure (deterministic, stateless)
• terminal_cost() encodes deadline requirements

Required pattern:
- Use penalties in stage_cost() to discourage undesirable timing
- Do NOT add hard constraints to stage_cost()
- Maintain pure function (no side effects)

Anti-patterns:
✗ Don't modify feasible_actions() for preferences
✗ Don't introduce state into optimizer
✗ Don't forget to update terminal_cost() if changing stage_cost()

Proceeding with refactoring analysis...
```

**Agent:** Now analyzes with doc constraints in mind.

## Why This Matters

Without this skill, agents:
1. See complex code
2. Start proposing changes based on code smells alone
3. Violate critical constraints because they didn't know them
4. Create PRs that get rejected or introduce bugs

With this skill:
1. Agent reads constraints first
2. Proposals respect architectural boundaries
3. Changes align with system design principles
4. Fewer review cycles, higher quality

## Configuration

The skill uses these file mappings (in `docs/AGENTS.md`):

```yaml
domain_mappings:
  optimizer:
    must_read: [PLANNING_MODEL.md, OPTIMIZER_DP_ROLLOUT.md]
    key_constraints:
      - feasible_actions: hard constraints only
      - stage_cost: soft penalties only
      - terminal_cost: deadline requirements
      - pure: stateless and deterministic
  entities:
    must_read: [ENTITY_REFERENCE.md, AGENTS.md]
    key_constraints:
      - update_reference: always update ENTITY_REFERENCE.md
      - pattern: entities read from coordinator only
      - platforms: create in sensor.py, switch.py, etc.
      - count: aware of entity limits
```

## Integration with SymDex

I use `symdex_search_text()` to quickly find relevant sections in indexed docs. This is fast and precise.

## Integration with OpenViking

For semantic code understanding, I also use OpenViking:

- **When to use:** "explain how X works", "find patterns", "analyze code structure"
- **Start if not running:** `~/.local/bin/ov-start`
- **Commands:**
  - `ov search "query" --uri viking://resources/localshift` - semantic search
  - `ov grep "exact_text" --uri viking://resources/localshift` - keyword search
  - `ov tree viking://resources/localshift -L 2` - explore structure

OpenViking provides AI-generated summaries and is particularly useful for understanding code intent and patterns across the codebase.

## Fallback

If a doc isn't indexed in SymDex yet, I'll use `Read` to fetch it directly. No configuration needed.

## Override

If you explicitly say "skip documentation check" or "I know the constraints", I'll respect that. But you'll need to be explicit.

## See Also

- `docs/AGENTS.md` - Documentation index that maps domains to docs
- `AGENTS.md` (root) - Existing rule that optimizer changes must consult PLANNING_MODEL.md
- `custom_components/localshift/AGENTS.md` - Entity patterns and rules
