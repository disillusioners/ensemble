---
version: 1.0.1
category: execution
auto_load: false
include: [test-pack]
---

# Test Pack Execution

You are the executor. You run test packs directly — you do not hand work off to another agent or spawn another session. The innate `test-pack` skill defines the INVARIANT rules (5-min cap, dual-layer timeout, naming convention, output format). This skill builds on those invariants and contains the EVOLVABLE procedures: how to launch, monitor, fix, and report your pack.

## Pack Existence Gate

1. **Check if test packs exist for this project** (look for `test/packs/` or similar; PACKS.md documents them)
2. **YES** → Proceed to Run Single Test Pack
3. **NO** → Organize tests into packs directly before proceeding (group by `<scope>_<type>_test`, follow the innate test-pack skill). This is a preparation step you own — do not defer it to another agent.

## Pre-Execution Self-Check (Run Before EVERY Pack)

Before executing any test pack, verify ALL of the following. If any check fails, fix the execution plan before starting.

- [ ] **Single pack** — You are targeting exactly ONE pack path (no "run all tests", no bare `go test ./...` / `pytest tests/`)
- [ ] **Scope locked** — You will run ONLY that pack; you will NOT execute any other pack or test
- [ ] **Command-level timeout** — Your run is wrapped with the 5-min cap (`timeout 300 ...` or `subprocess.run(..., timeout=300)`)
- [ ] **Script-internal timeout** — Pack script self-timeouts at ≤ 5 min (dual-layer confirmed)
- [ ] **Time estimate** — Pack estimated < 5 min (if not, split before starting)
- [ ] **PACKS.md valid** — Pack path exists and is registered in PACKS.md
- [ ] **Override documented** — If long-timeout case, overridden config/env is intentional and documented

## Run Single Test Pack — Execution Contract (MANDATORY)

**This is the ONLY acceptable execution shape for running a test pack.** Never free-form "run the tests" — that causes the whole suite to run at once and blow past the timeout.

```
Task: Run Single Test Pack
Pack: [exact path/to/<scope>_<type>_test.sh]   # exactly ONE pack
Estimated runtime: [X min, must be < 5]

CONSTRAINTS (do NOT violate):
- Run ONLY the pack at the path above. Do NOT run any other pack or test.
- Do NOT run broad commands like `go test ./...`, `pytest tests/`, `npm test`, `jest`.
- Wrap the run with a 5-min command-level timeout:
    bash:   `timeout 300 ./path/to/<scope>_<type>_test.sh`
    python: `subprocess.run([..., "<pack>"], timeout=300)`
- The pack script also has its own internal timer — do NOT disable or extend it.
- Do NOT "discover and run" extra tests to "be thorough".

Requirements:
- Execute the single pack with the timeout wrapper above.
- Capture all output.
- Report final status: PASS / FAIL / TIMEOUT  (exit 0 / 1 / 124).
- If FAIL: include file, line, test name, error message for each failure.
- If TIMEOUT (exit 124): report which test/scenario was running when it timed out.
- If a quick fix is possible (< 20 lines, no architecture change): fix, re-run, and report the fix + commit hash.

Return:
- RESULT: PASS|FAIL|TIMEOUT
- Failures (if any): [file:line] test — reason
- Quick fixes applied (if any): what + commit hash
- Actual runtime: [X min]
```

**Long-timeout case variant:** If the pack contains tests that inherently need long waits (retries/sleeps/polls), add an `ENV OVERRIDES` block to the same execution shape so the pack still finishes < 5 min:

```
ENV OVERRIDES (intentional, documented in MOCK_TESTS.md):
- RETRY_COUNT=1            # was 5
- SLEEP_INTERVAL_MS=50     # was 1000
- MOCK_ENDPOINT=http://127.0.0.1:10080   # fast local mock, was real service
```

Do **not** raise the 5-min cap to fit a slow test. Override the config/env instead.

## Split & Parallel Workflow (Full Project Test)

**When blast-radius assessment determines the full suite is warranted:**

1. **List every pack** from PACKS.md and estimate each pack's runtime
2. **Split any pack estimated > 5 min** into smaller packs until every pack is < 5 min (unit packs must also be < 2 min)
3. **Group by independence:**
   - Independent packs → execute in **parallel** within your execution context (one execution slot per pack, one execution contract per pack)
   - Dependent packs → run sequentially in the required order
4. **Execute each pack** using the Run Single Test Pack contract, after passing the Pre-Execution Self-Check
5. **Aggregate** PASS/FAIL/TIMEOUT from every pack into one report. One pack's TIMEOUT does not block the others
6. **For any TIMEOUT** → run the TTQA Loop on that single pack (do not re-run the whole project)

```
Example: 6 packs, all independent, ~3 min each
Plan: 6 parallel executions, each gets one "Run Single Test Pack" contract
Expected: ~3 min total (parallel) instead of ~18 min (sequential) or 1 opaque timeout (all-at-once)
```

## TTQA Loop (When Timeout Occurs)

1. **Analyze timeout cause** — Which specific test/scenario timed out? Expected vs actual duration?
2. **Attempt TTQA optimizations** (canonical list):
   - Mock external services
   - Skip tests needing unavailable API keys
   - Override ENV to match conditions sooner (faster mock endpoints, shorter retry intervals)
   - Reduce retry attempts / sleep intervals
   - Disable slow/flaky sub-tests
3. **Re-run test pack** with optimizations
4. **If still timeout** → Proceed to Test Architecture Fix (NOT straight to escalation)

## Test Architecture Fix Workflow

**When:** pack can't fit timeout, or quality issues found. Test-code architecture changes are your job; TTQA patches this run, this workflow is the permanent fix.

**Diagnose:** identify slow pack/test + root cause; estimate before/after with target limit (≤ 5 min; unit ≤ 2 min). Small fix (< 20 lines, test code) → quick-fix path. Larger refactor (≥ 20 lines, test code only) → use the task template below — still immediate.

**Task template:**

```
Task: Test Architecture Fix
Pack: [path/to/<scope>_<type>_test]   # the slow/bloated pack
Problem: [root cause — e.g., calls real DB, sleeps 2s/test, 400 tests in one pack]
Target: pack must finish < [2|5] min after fix.

CONSTRAINTS:
- TEST code only. Don't change production/source behavior.
- Preserve coverage/equivalence — same scenarios, just faster/leaner.
- Each resulting pack keeps dual-layer timeout (`timeout 300` + script-internal).
- Follow test-pack skill for new/split scripts.

Approaches (apply as needed): split into smaller packs; mock slow dependencies; reduce/parameterize sleeps/retries; share/remove redundant setup; isolate order-dependent tests; parallelize within a pack where safe.

Return: what changed (before/after runtime); new PACKS.md entries if split; re-run RESULT (PASS|FAIL|TIMEOUT) with actual runtime; commit hash.
```

**Verify & Document:** Re-run pack(s); confirm under timeout. Update PACKS.md + LESSONS/[descriptive].md (root cause, fix, before/after runtime).

## Escalation

**Only after a Test Architecture Fix has been attempted and verified insufficient**, report back to your dispatcher:

```
TESTER_CANT_OPTIMIZE_TEST_PACK: Test pack [pack_name] exceeded timeout limit of 5 minutes.
Attempted TTQA optimizations:
- [Optimization 1]: [Result]
Attempted Test Architecture Fix:
- [Fix 1]: [before → after runtime, still over limit]
Root cause that resists fixing: [reason]

Test pack cannot meet timeout requirement. Manual intervention required.
```

You stop here. Your dispatcher decides whether to escalate to the user, to spawn a follow-up task with relaxed constraints, or to apply other recovery actions. Do not continue iterating on your own once you have reported TESTER_CANT_OPTIMIZE_TEST_PACK.