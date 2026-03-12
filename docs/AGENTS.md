# LocalShift Documentation - Index

## Overview

Documentation for LocalShift Home Assistant integration. 14 documents covering architecture, development, and usage.

## Document Index

| Document | Purpose | Lines |
|----------|---------|-------|
| `ARCHITECTURE.md` | System architecture and design | ~938 |
| `DEVELOPER_GUIDE.md` | Development guide and patterns | ~600 |
| `PLANNING_MODEL.md` | DP optimizer extension guide | ~322 |
| `ENTITY_REFERENCE.md` | Entity definitions and attributes | ~400 |
| `COVERAGE.md` | Coverage policy and tracking | ~200 |
| `API.md` | Public API reference | ~300 |
| `MIGRATION.md` | Migration guides | ~250 |
| `TESTING.md` | Testing patterns and examples | ~400 |
| `DEPLOYMENT.md` | Deployment procedures | ~180 |
| `CHANGELOG.md` | Version history | ~500 |
| `CONFIGURATION.md` | Configuration options | ~350 |
| `TROUBLESHOOTING.md` | Common issues and solutions | ~280 |
| `PERFORMANCE.md` | Performance considerations | ~200 |
| `CONTRIBUTING.md` | Contribution guidelines | ~150 |

## Critical Documents

### For Optimizer Changes (MUST READ)

**`PLANNING_MODEL.md`** - Decision guide for modifying DP optimizer:

| Question | Action |
|----------|--------|
| Is it impossible/forbidden? | Add to `feasible_actions()` |
| Is it a requirement by deadline? | Add to `terminal_cost()` |
| Is it discouraged/preferred? | Add penalty to `stage_cost()` |

### For New Entities

**`ENTITY_REFERENCE.md`** - MUST update when adding/removing entities:
- Entity ID format
- State attributes
- Available modes
- Default values

### For Architecture Changes

**`ARCHITECTURE.md`** - System overview:
- Component diagram
- Data flow
- State machine
- Integration points

## Where to Look

| Task | Document |
|------|----------|
| Understand system | `ARCHITECTURE.md` |
| Modify optimizer | `PLANNING_MODEL.md` |
| Add entity | `ENTITY_REFERENCE.md` |
| Write tests | `TESTING.md`, `../tests/AGENTS.md` |
| Deploy changes | `DEPLOYMENT.md` |
| Debug issues | `TROUBLESHOOTING.md` |
| Configure options | `CONFIGURATION.md` |
| Contribute | `CONTRIBUTING.md` |

## Documentation Updates

**When adding/removing entities, update:**
1. `ENTITY_REFERENCE.md` - Entity definitions
2. `ARCHITECTURE.md` - If system architecture changes
3. `DEVELOPER_GUIDE.md` - If development patterns change
4. `CHANGELOG.md` - Document the change

## Conventions

- **Markdown format** with tables for structured data
- **Code blocks** with syntax highlighting (python, yaml, bash)
- **Cross-references** using relative links
- **Keep updated** - Docs should match code

## See Also

- `../AGENTS.md` - Root project guidelines
- `../custom_components/localshift/AGENTS.md` - Integration guide
- `../tests/AGENTS.md` - Testing guide
