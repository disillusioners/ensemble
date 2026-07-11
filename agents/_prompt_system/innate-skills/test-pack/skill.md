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
