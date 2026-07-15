# Phase 5: Tester Agent Updates

## Objective
Transform the tester from a direct executor (running tests with multiple auto_load skills baked in) to a planner + dispatcher (spawn workers with specific skills via meta tags). Reduce auto_load skills from 4 to 1, keeping only `test-strategy` for planning. Update workflow.md with dispatcher patterns.

## Coupling
- **Depends on**: Phase 1 (meta tag mechanism must exist for worker dispatch)
- **Coupling type**: loose (Phase 5 references the Phase 1 interface, but the prompt content is independent code)
- **Shared files with other phases**: `agents/tester/skill-set.md`, `agents/tester/workflow.md` (only this phase)
- **Shared APIs/interfaces**: References `<meta>{"load_skill": "..."}</meta>` from Phase 1
- **Why this coupling**: Phase 5's workflow.md describes HOW the tester uses meta tags to dispatch workers, but the markdown content compiles independently.

## Context
- Current `skill-set.md`: 4 auto_load (test-strategy, test-pack-execution, mock-test, unit-test) + 5 on-demand
- Current `workflow.md`: Tester spawns **opencode sessions** for test execution (not worker instances)
- The worker agent (`agents/worker/`) already has `skill_injection: true`, `dynamic-skill` innate skill, and dense `skill_feedback` enforcement in rule.md
- Worker meta.json: `{"innate_skills": ["dynamic-skill", "todo"], "skill_injection": true, ...}`

## Tasks

### Task 5.1: Update skill-set.md — Reduce Auto_Load to test-strategy Only

**File**: `agents/tester/skill-set.md`

Change 3 skills from `auto_load: true` to `auto_load: false`:

```yaml
# CHANGE these three from auto_load: true → auto_load: false:
  - name: test-pack-execution
    version: "1.0.0"
    auto_load: false         # WAS: true
    category: execution
    description: "Pack lifecycle, TTQA optimization, splitting heuristics, output format"

  - name: mock-test
    version: "1.0.0"
    auto_load: false         # WAS: true
    category: execution
    description: "Mock test design: spec template, port allocation, what-to-mock, 5-phase workflow"

  - name: unit-test
    version: "1.0.0"
    auto_load: false         # WAS: true
    category: execution
    description: "Unit test discovery, delegation, analysis, coverage documentation"
```

**KEEP unchanged:**
```yaml
  - name: test-strategy
    version: "1.0.0"
    auto_load: true          # KEPT — tester uses for planning decisions
    category: testing-strategy
    description: "Blast radius analysis, change-set derivation, test pack planning"
```

**Update the skill-set.md header** to reflect the new distribution:
```markdown
# Tester Agent — Skill Set

The tester agent coordinates testing as a Test Leader + Dispatcher. It keeps `test-strategy` auto-loaded for planning decisions, and dispatches execution to worker instances — each loaded with a single skill via `<meta>{"load_skill": "..."}</meta>` for clean 1:1 attribution. Skill content is evolvable.
```

### Task 5.2: Update skill-set.md — Update Skill Bank Templates

The skill-set.md is the source of truth for the skill bank seeding system. When `auto_load` changes here, the skill bank templates should be updated accordingly. The clone-on-miss mechanism propagates `auto_load` from the bank template.

**Check**: Does the skill bank re-seed on skill-set.md changes? If so, the bank templates will be updated automatically on next startup. If manual update is needed:

```bash
# If skill bank needs manual update (check the seeding mechanism):
# The seed_skill_bank function reads skill-set.md and creates/updates bank templates.
# Changing auto_load: true → false in skill-set.md should propagate on next startup.
```

### Task 5.3: Update workflow.md — Add Dispatcher Patterns

**File**: `agents/tester/workflow.md`

Add a new section for the skill-per-worker dispatch pattern. This doesn't replace the existing opencode-based execution — it adds a parallel pattern for skill-attributed work.

**Add after the "Role: Test Leader" section:**

```markdown
## Skill-Per-Worker Dispatch Pattern

**For tasks requiring a specific testing skill (unit-test, mock-test, etc.), dispatch to a worker instance with the skill loaded via meta tag.**

### Why Dispatch to Workers?
- Clean 1:1 attribution: each worker has exactly ONE skill
- Reliable metrics: workers have dense `skill_feedback` enforcement
- Parallel execution: multiple workers with different skills run concurrently
- Skill evolution: clean data feeds A/B testing and triggers

### Dispatch Pattern

```
Task: Run unit tests on auth module
Skill needed: unit-test

→ spawn_instance(agent="worker")
→ send_message(
    instance_id=worker_id,
    message="Run unit tests on the auth module. Execute the test packs and report results.\n<meta>{\"load_skill\": \"unit-test\"}</meta>"
  )
```

### Skill Selection Guide

| Task Type | Skill to Load | Meta Tag |
|-----------|---------------|----------|
| Unit testing | unit-test | `<meta>{"load_skill": "unit-test"}</meta>` |
| Mock testing | mock-test | `<meta>{"load_skill": "mock-test"}</meta>` |
| Pack execution | test-pack-execution | `<meta>{"load_skill": "test-pack-execution"}</meta>` |
| Integration testing | integration-test | `<meta>{"load_skill": "integration-test"}</meta>` |
| E2E testing | e2e-test | `<meta>{"load_skill": "e2e-test"}</meta>` |
| Validation | ensure-validation | `<meta>{"load_skill": "ensure-validation"}</meta>` |
| Flaky test mgmt | flaky-test-management | `<meta>{"load_skill": "flaky-test-management"}</meta>` |
| Quick fix | quick-fix | `<meta>{"load_skill": "quick-fix"}</meta>` |

### When to Use Workers vs Opencode Sessions

| Use Workers (skill-per-worker) | Use Opencode Sessions |
|-------------------------------|----------------------|
| Task needs a specific evolvable skill | Generic test execution (no skill needed) |
| Want clean skill metrics attribution | Quick one-off test runs |
| Skill evolution data collection | Infrastructure setup / teardown |
| Parallel skill-specific testing | Test script creation |

**Default**: Use workers for skill-specific tasks. Use opencode for infrastructure and non-skill tasks.
```

### Task 5.4: Update workflow.md Decision Points

**File**: `agents/tester/workflow.md`

Add decision point entries:

```markdown
- **Need to run unit tests with skill attribution?** → Spawn worker with `<meta>{"load_skill": "unit-test"}</meta>`
- **Need to run mock tests with skill attribution?** → Spawn worker with `<meta>{"load_skill": "mock-test"}</meta>`
- **Need skill-specific test execution for evolution data?** → Always use worker dispatch (not opencode) for clean 1:1 attribution
```

## Key Files

| File | Change Type | Purpose |
|------|------------|---------|
| `agents/tester/skill-set.md` | MODIFY | 3 skills: auto_load true→false; update header |
| `agents/tester/workflow.md` | MODIFY | Add skill-per-worker dispatch patterns + decision points |

## Constraints
- Keep `test-strategy` as auto_load — it's used for planning decisions
- Don't remove skills — they're still available, just loaded differently (on-demand via worker)
- The meta tag format must match Phase 1's parsing: `<meta>{"load_skill": "skill-name"}</meta>`
- Skill bank templates must reflect the auto_load change (check seeding mechanism)

## Deliverables
- [ ] `skill-set.md`: 3 skills changed from `auto_load: true` to `auto_load: false`
- [ ] `skill-set.md`: Header updated to reflect dispatcher role
- [ ] `workflow.md`: Skill-per-worker dispatch pattern section added
- [ ] `workflow.md`: Skill selection guide table added
- [ ] `workflow.md`: Decision points updated
- [ ] Skill bank templates updated (auto_load propagated)
- [ ] Manual test: tester spawns worker with meta-tag skill and receives report
