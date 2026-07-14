# Phase 1: Skill Content Authoring

## Objective

Author the 9 evolvable skill templates extracted from the tester agent's current prompt-embedded knowledge, plus the `skill-set.md` manifest and `skills-template/` directory structure.

## Coupling

- **Depends on**: None (root phase)
- **Coupling type**: independent
- **Shared files with other phases**: Phase 3 reads the files this phase creates
- **Shared APIs/interfaces**: skill-set.md format (parsed by Phase 3 seeding)
- **Why this coupling**: Phase 3's seeding mechanism must read template content and skill-set.md metadata to seed the skill_bank table

## Context

The tester's workflow.md (1088 lines) contains distinct testing methodologies. Each must be extracted into a standalone, self-contained skill template. The key is that each skill template should be **actionable on its own** — an agent reading it should know exactly what to do without needing the full workflow.md context.

## New Files

```
agents/tester/
├── skill-set.md                          ← NEW: manifest of 9 skills
└── skills-template/                      ← NEW: template directory
    ├── test-strategy.md                  ← NEW
    ├── test-pack-execution.md            ← NEW
    ├── mock-test.md                      ← NEW
    ├── unit-test.md                      ← NEW
    ├── integration-test.md               ← NEW (gap fill)
    ├── e2e-test.md                       ← NEW (gap fill)
    ├── ensure-validation.md              ← NEW
    ├── flaky-test-management.md          ← NEW
    └── quick-fix.md                      ← NEW
```

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Create `skills-template/` directory | `mkdir -p agents/tester/skills-template/` | agents/tester/skills-template/ |
| 2 | Write `skill-set.md` manifest | See format spec below | agents/tester/skill-set.md |
| 3 | Author `test-strategy.md` | From workflow.md "Planning Phase" + "Blast Radius Control" + "Decision Points" sections (lines 45-130, 989-1020). auto_load=true. | agents/tester/skills-template/test-strategy.md |
| 4 | Author `test-pack-execution.md` | From innate test-pack (TTQA, splitting, 5-min cap, dual-layer timeout) + workflow.md "Test Pack Execution Workflow" (lines 306-494). auto_load=true. | agents/tester/skills-template/test-pack-execution.md |
| 5 | Author `mock-test.md` | From workflow.md "Mock Test Workflow" + "Mock Test Specification Template" (lines 529-642). auto_load=true. | agents/tester/skills-template/mock-test.md |
| 6 | Author `unit-test.md` | From workflow.md "Unit Test Workflow" (lines 259-305). auto_load=true. | agents/tester/skills-template/unit-test.md |
| 7 | Author `integration-test.md` | NEW content — gap fill. Cover cross-component testing, API boundary testing, service interaction testing, contract testing. auto_load=false. | agents/tester/skills-template/integration-test.md |
| 8 | Author `e2e-test.md` | NEW content — gap fill. Cover full-flow testing, browser automation, user journey testing, environment setup. auto_load=false. | agents/tester/skills-template/e2e-test.md |
| 9 | Author `ensure-validation.md` | From workflow.md "ensure.md Validation Workflow" (lines 144-258). auto_load=false. | agents/tester/skills-template/ensure-validation.md |
| 10 | Author `flaky-test-management.md` | From workflow.md "Flaky Test & Quarantine Workflow" (lines 496-527). auto_load=false. | agents/tester/skills-template/flaky-test-management.md |
| 11 | Author `quick-fix.md` | From workflow.md "Quick Fix Workflow" (lines 666-717). auto_load=false. | agents/tester/skills-template/quick-fix.md |

## skill-set.md Format: YAML Frontmatter (W1)

```yaml
---
# Tester Skill Set

This manifest defines the tester agent's evolvable skills. Each entry
is seeded into the Skill Bank at startup and cloned into project-scoped
skills on first use.

## Skills

### test-strategy
- **version**: 1.0.0
- **auto_load**: true
- **category**: planning
- **description**: Blast radius assessment, test scope planning, parallelism strategy

### test-pack-execution
- **version**: 1.0.0
- **auto_load**: true
- **category**: execution
- **description**: Test pack creation, execution, TTQA optimization, timeout handling

### mock-test
- **version**: 1.0.0
- **auto_load**: true
- **category**: execution
- **description**: Mock service testing with 5-phase workflow and specification templates

### unit-test
- **version**: 1.0.0
- **auto_load**: true
- **category**: execution
- **description**: Unit test discovery, delegation, analysis, and fix workflow

### integration-test
- **version**: 1.0.0
- **auto_load**: false
- **category**: execution
- **description**: Cross-component testing, API boundary validation, contract testing

### e2e-test
- **version**: 1.0.0
- **auto_load**: false
- **category**: execution
- **description**: End-to-end full-flow testing, browser automation, user journeys

### ensure-validation
- **version**: 1.0.0
- **auto_load**: false
- **category**: validation
- **description**: ensure.md requirement validation across 4 phases

### flaky-test-management
- **version**: 1.0.0
- **auto_load**: false
- **category**: maintenance
- **description**: Flaky test detection, quarantine, auto-skip, and un-quarantine

### quick-fix
- **version**: 1.0.0
- **auto_load**: false
- **category**: maintenance
- **description**: Quick fix workflow for failures under 20 lines, with templates
```

**NOTE**: The canonical YAML frontmatter format is defined in `phase3-plan.md §3.1`. The parser (`parse_skill_set_file()`) reads YAML frontmatter with a `skills:` list. Use that format as the source of truth.

### Parsing Contract (for Phase 3)

The `skill-set.md` parser must extract:
- Skill name (from `### {name}` headings under `## Skills`)
- `version` (semver string)
- `auto_load` (boolean)
- `category` (free-form string)
- `description` (single-line string)
- Template content (from `skills-template/{name}.md` — NOT embedded in skill-set.md)

### Category Convention (W2)

All seeded skill_bank items use category `{agent_id}-skill-set` (e.g. `tester-skill-set`) to distinguish from user-created bank items.

## Skill Template Content Guidelines

Each template must be:
1. **Self-contained** — no "see workflow.md section X" references
2. **Action-oriented** — describe WHAT to do, not WHY it exists
3. **Concise** — 1-3 pages max. These are injected into prompts; token budget matters
4. **Version-tagged** — start at `1.0.0` in frontmatter
5. **Include invariant rules** where applicable (e.g., test-pack-execution MUST include the 5-min cap + dual-layer timeout from the innate test-pack)

### Template Frontmatter Format

```markdown
---
version: 1.0.0
category: execution
auto_load: true
---

# Test Pack Execution

[skill content...]
```

## Constraints

- Templates must NOT duplicate the innate test-pack invariant rules verbatim — instead, they should REFERENCE them ("The innate test-pack skill defines the 5-minute cap and dual-layer timeout as invariants — this skill builds on that foundation")
- auto_load=true skills should be the most concise (they're always in the prompt)
- auto_load=false skills can be more detailed (loaded on-demand only)
- integration-test.md and e2e-test.md are NEW content — author from scratch based on testing best practices

## Deliverables

- [ ] `agents/tester/skill-set.md` with all 9 skills defined
- [ ] `agents/tester/skills-template/test-strategy.md`
- [ ] `agents/tester/skills-template/test-pack-execution.md`
- [ ] `agents/tester/skills-template/mock-test.md`
- [ ] `agents/tester/skills-template/unit-test.md`
- [ ] `agents/tester/skills-template/integration-test.md`
- [ ] `agents/tester/skills-template/e2e-test.md`
- [ ] `agents/tester/skills-template/ensure-validation.md`
- [ ] `agents/tester/skills-template/flaky-test-management.md`
- [ ] `agents/tester/skills-template/quick-fix.md`
