# Test Pack Skill

Create self-contained test scripts with subprocess-based timeout enforcement that actually interrupts hung tests.

---

## Principles

### 5-Minute Hard Cap (No Exception)
Every pack must finish under 5 minutes. Unit packs target 2 min. If a pack cannot fit, **split it into smaller packs** — never raise the cap. Tests that inherently need long waits (retries/sleeps/polls) must use **overridden config/env** (fewer retries, shorter sleeps, fast mock endpoints) to stay under the cap.

### Dual-Layer Timeout (Both Required)
- **Layer 1 — command-level (outer guard):** Wrap the whole run with `timeout 300 <cmd>` (bash) or `subprocess.run(..., timeout=300)` (Python). This caps damage even if the caller mis-scopes the run.
- **Layer 2 — script-internal (inner guard):** The script self-timeouts at 5 min (or per-type limit, whichever lower) to interrupt hung tests.
One layer alone is not enough — a hung process can defeat a single timer.

### Subprocess-Based Timeout
Use `timeout` command (bash) or `subprocess.run(..., timeout=N)` (Python). Post-hoc checks are broken — they can't interrupt hung processes.

### Explicit Output
Report one of: `PASS`, `FAIL`, `TIMEOUT` (exit 124)

### Predictable Timing
Estimate execution time before writing. If > limit, redesign:
- Split into smaller packs
- Mock slow dependencies
- Reduce unnecessary waits

---

## Timeout Pattern

```
# Subprocess-based: timeout check MUST interrupt hung tests
START = now()
RUN tests as subprocess with TIMEOUT limit

if subprocess.times_out:
    cleanup()
    print "RESULT: TIMEOUT"
    exit 124
elif subprocess.passed:
    print "RESULT: PASS"
    exit 0
else:
    print "RESULT: FAIL"
    exit 1
```

Choose language (bash, python, go, etc.) based on project context.

---

## Naming Convention

`<scope>_<type>_test` (e.g., `core_unit_test`, `auth_integration_test`)

---

## Output Format

```
=== Test Pack: <name> ===
[optional test output]
RESULT: PASS|FAIL|TIMEOUT
```

Exit codes:
- `0`: PASS
- `124`: TIMEOUT (via `timeout` command or subprocess)
- `1`: FAIL

### Partial Pass Handling

- **All pass** → `RESULT: PASS`
- **Any fail** → `RESULT: FAIL` (include count if available: `FAIL (5/7 passed)`)
- **Any timeout** → `RESULT: TIMEOUT` (exit 124)

---

## Timeout Limits

| Type | Limit |
|------|-------|
| Unit | 2 min |
| Integration | 5 min |
| Feature | 5 min |
| E2E | 5 min |
| Mock | Per spec (≤ 5 min) |

**Absolute hard cap: 5 minutes for every pack — no exception.** If a pack can't fit, split it or override config/env. Never raise the limit.

---

## TTQA

When timeout occurs, apply TTQA optimizations per rule.md.

---

## Skill System Integration

This innate skill defines the **INVARIANT rules** for test packs. These rules NEVER change.

The following **evolvable skills** build on this foundation and contain project-specific procedures:
- `test-strategy` (auto_load) — How to decide WHAT to test (blast radius, planning)
- `test-pack-execution` (auto_load) — How to optimize and split packs for this project
- `unit-test` (auto_load) — Unit test patterns for this codebase
- `mock-test` (auto_load) — Mock test design for this project's stack
- `integration-test` (on-demand) — Cross-component integration testing
- `e2e-test` (on-demand) — End-to-end test procedures
- `quick-fix` (on-demand) — Quick fix patterns and eligibility
- `ensure-validation` (on-demand) — Quality gate validation
- `flaky-test-management` (on-demand) — Flaky test quarantine lifecycle

**auto_load** skills are always loaded into your context by the system.
**on-demand** skills are injected when your task context matches, or you can use `skill_search` to find them manually.

If any skill is missing for this project, the system auto-loads it from the template bank.
