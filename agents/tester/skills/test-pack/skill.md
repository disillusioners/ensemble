# Test Pack Skill

Create self-contained test scripts with subprocess-based timeout enforcement that actually interrupts hung tests.

---

## Principles

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
| Mock | Per spec |

---

## TTQA

When timeout occurs, apply TTQA optimizations per rule.md.
