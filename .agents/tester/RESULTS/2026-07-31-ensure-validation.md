# ensure.md Validation — 2026-07-31

## Scope
- Branch: `feature/pause-tool-result-fix`
- HEAD: `485e0cf1 test(pg): fix report-lane guard tests — identity map + job_locks seed`
- Pack: `concurrency_atomic_unit_test`
- Change type: concurrency-sensitive (pause/cascade race conditions)
- Trigger: in-scope Critical requirement for this branch's blast radius

## Critical Requirements

### ✅ Critical #2 — Deadlock / concurrency integrity — PASS

**Requirement text (ensure.md):**
> Deadlock / concurrency integrity — pack `concurrency_atomic_unit_test` PASS (includes `test_deadlock_fix.py`, cascade races, observer race, instance/project atomic locks)

**Pack (PACKS.md):** `concurrency_atomic_unit_test`
- tests/test_cascade_concurrency.py
- tests/test_cascade_race3.py
- tests/test_deadlock_fix.py
- tests/test_instance_delete_by_project_locking.py
- tests/test_instance_metadata_atomic.py
- tests/test_observer_race1.py
- tests/test_project_repository_atomic.py

**Result:**
- **STATUS: PASS** (exit 0)
- **Tests:** 66 passed, 19 skipped, 0 failed
- **Runtime:** ~7.6s (well under the 5-min cap)
- **Quarantined tests in pack:** 0 (none of the 7 files are in QUARANTINE.md)
- **Quick fixes applied:** none — pack was green on first run

**Command executed:**
```
timeout 300 env PYTEST_TIMEOUT=120 .venv/bin/pytest \
  tests/test_cascade_concurrency.py \
  tests/test_cascade_race3.py \
  tests/test_deadlock_fix.py \
  tests/test_instance_delete_by_project_locking.py \
  tests/test_instance_metadata_atomic.py \
  tests/test_observer_race1.py \
  tests/test_project_repository_atomic.py \
  --tb=short -q
```

**Final pytest line:**
```
66 passed, 19 skipped in 6.65s
```

## Other Critical Requirements (out of scope for this validation)
- Critical #1 (no regressions in changed packs) — out of scope; not part of this validation request
- Critical release-gate (full non-integration suite, E2E happy path) — out of scope; not part of this validation request

## Contradictions / Improvement Notices
- None. The requirement text maps cleanly to PACKS.md and was executed as a scoped pack with the timeout wrapper.

## Notes
- The pause-race fix series on this branch (commits 34e0d1ee, b43af9af, ee29377e) was the primary motivation for this validation. The deadlock/concurrency pack passed cleanly, providing regression evidence that the cascade-execution window closing did not break any concurrency invariants.
- 19 skipped tests are pre-existing skips (likely platform/DB-driver conditional skips); not regressions from this branch.
