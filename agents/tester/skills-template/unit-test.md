---
version: 1.0.0
category: execution
auto_load: true
---

# Unit Test

Coordinate unit test execution across packs. You plan and delegate; opencode runs. Scope to relevant packs per blast radius control (see `test-strategy` skill).

## Step 1: Discover & Plan

1. Read `.agents/tester/README.md` for project context
2. Read `.agents/tester/rules/ensure.md` for quality requirements
3. **Derive the change set** (blast radius) — scope to relevant unit test packs (see `test-strategy` skill)
4. **List the unit test packs to run** (one per module/scope, named `<module>_unit_test`). Estimate each pack's runtime. **Split any pack > 2 min (unit hard limit) into smaller packs before launching.**
5. **Plan sessions**: one opencode session per pack; independent packs in parallel. Materialize as a todo graph: `todo_graph_create(nodes=<packs>, edges=<dependencies>)`
6. Prepare the strict message (Step 2) per pack — never a single "run unit tests" message

## Step 2: Delegate Execution (Per Pack)

Use the **Run Single Test Pack** strict message template (see `test-pack-execution` skill). Send one message per pack, one session per pack. Never send a bare "run unit tests" / `go test ./...` / `pytest tests/` message.

Key fields to fill per pack:

```
Task: Run Single Test Pack
Pack: [exact path/to/<module>_unit_test.sh]   # exactly ONE pack
Estimated runtime: [X min, must be < 2 for unit]
... (rest of the strict template — see test-pack-execution skill)
```

- **Independent unit packs** → launch in parallel (one session each)
- **Run the Pre-Send Self-Check** before each send (see `test-pack-execution` skill for checklist)
- **Always grant Quick Fix Authorization** when criteria are met (< 20 lines, no architecture change, obvious root cause)

## Step 3: Analyze & Document

1. Receive results from all opencode sessions
2. Aggregate per-pack PASS/FAIL/TIMEOUT; analyze failures and patterns
3. Note which issues were quick-fixed by sessions (record commit hashes)
4. Update `.agents/tester/COVERAGE.md` with findings:
   - Modules exercised
   - Gaps discovered (untested code paths, missing edge cases)
   - New test files added
5. Update `.agents/tester/LESSONS/` with issues found and fixes applied (e.g., `unit-test-fix-[issue].md`)

## Step 4: Fix Failures (If Needed)

**If unit tests are still broken after quick fixes:**

1. Assess remaining failures: are they quick-fixable?
2. **If yes** → Reuse same opencode session, send follow-up task (still single-pack scoped). Quick fix criteria: < 20 lines, single file/module, no PRODUCTION architecture change, obvious root cause, instance has context
3. **If no** → Spawn new opencode session for full fix workflow
4. Monitor and verify fixes
5. Document in `.agents/tester/LESSONS/` (e.g., `unit-test-failures-[date].md`)

**For test-code architecture issues** (bloated pack, slow setup, order-dependent tests) → use the Test Architecture Fix workflow from `test-pack-execution` skill. Test-code refactors are NOT blocked by the production "no architecture change" rule.

## Step 5: Validate ensure.md (After Unit Tests Pass)

1. If unit tests pass, proceed to ensure.md validation
2. Follow the ensure-validation skill workflow
3. Document results in `.agents/tester/RESULTS/[date]-ensure-validation.md`

## Unit Pack Sizing Heuristics

| Pack characteristic | Action |
|---|---|
| Estimated runtime < 2 min | OK to launch as-is |
| Estimated runtime ≥ 2 min | **Split before launch** — group by feature/module into smaller packs |
| Tests hitting real DB/network | Mock the boundary or split into a separate integration-style pack |
| Tests sharing heavy setup | Extract shared fixture; ensure cleanup runs (state leaks cause flakes) |
| Tests with > 5s sleeps each | Override config/env to reduce waits; never raise the cap |
| Tests with order dependencies | Isolate them; order-coupled tests create flakes |

## Coverage Documentation Pattern

When updating `.agents/tester/COVERAGE.md` after a unit test run, structure entries as:

```markdown
## [Date] — [Change/Feature]

**Change set**: [files/modules touched]
**Packs run**: [list]
**Result**: [PASS/FAIL — counts]

### Coverage added
- [module/path]: [what scenarios now covered]

### Gaps discovered
- [module/path:line]: [missing edge case] — recommend adding test in [pack]

### Quick fixes applied
- [commit-hash]: [fix description] in [file:line]
```

This format makes coverage trends visible across runs and surfaces gaps that need follow-up.