# Test Pack Skill

Create self-contained test scripts with built-in timeout enforcement.

---

## Principles

### Self-Enforcement
Test packs must enforce their own timeout. Do not rely on external agents or callers to enforce limits.

### Explicit Output
Report one of: `PASS`, `FAIL`, `TIMEOUT`

### Predictable Timing
Before writing tests, estimate execution time. If > limit, redesign:
- Split into smaller packs
- Mock slow dependencies
- Reduce unnecessary waits

---

## Timeout Pattern

```
START = now()
TIMEOUT = [120/300] seconds

do_tests()

if now() - START > TIMEOUT:
    cleanup()
    print "TIMEOUT"
    exit 124
else:
    print "PASS"
```

Choose language (bash, python, go, etc.) based on project context.

---

## Timeout Limits

| Type | Limit |
|------|-------|
| Unit | 2 min |
| Integration | 5 min |
| Feature | 5 min |
| Mock | Per spec |

---

## Naming Convention

`<category>_<type>_test.sh` or `.py` or `.go`

Examples:
- `core_unit_test.sh`
- `api_integration_test.sh`
- `feature_auth_test.sh`

---

## TTQA Triggers

When timeout occurs, attempt optimizations:
- Mock external services for faster response
- Skip tests requiring unavailable API keys
- Override ENV to match conditions sooner
- Reduce retry attempts / sleep intervals
- Increase timeout threshold if justified

If unfixable → `TESTER_CANT_OPTIMIZE_TEST_PACK_UNDER_FIVE_MIN`
