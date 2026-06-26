# Architecture Migration Testing — Lessons Learned

## Migration: feature/finish-architecture-migration (D11-D13, dispatch_path removal)
**Date:** 2026-06-26

---

## Key Findings

### 1. handle_correlation_complete Removal (D13)
When CorrelationManager was removed, `JobFeedbackObserver.handle_correlation_complete()` was also removed. Tests calling this method (`test_finalize_job_h15.py`, 11 tests) must be skip-marked or rewritten. **Equivalent coverage exists** in `test_job_feedback_observer.py` (41+ tests).

**Action:** Skip-marked the file, pointing to post-migration equivalent test.

### 2. SQL Dialect Portability — VARCHAR vs INTEGER in NOT IN
PostgreSQL rejects `VARCHAR_col NOT IN (SELECT int_col FROM ...)` with `operator does not exist: character varying = integer`. SQLite silently tolerates this type mismatch.

**Fix:** Always use `CAST(id AS TEXT)` when comparing across type boundaries. This is a recurring pattern — see critical note about `_ensure_postgres_columns()`.

**Affected file:** `daemon/services/dependency_bus.py` (`_sweep_orphan_watchers`)

### 3. SQLModel Default Columns in Raw SQL Tests
SQLModel `Field(default=...)` sets Python-side defaults only — NOT PostgreSQL server defaults. Raw SQL INSERTs in test helpers must supply NOT NULL columns explicitly:
- `created_at`, `updated_at` for instance table
- `retry_count`, `cancel_requested`, `retry_scheduled` for task table

**Affected file:** `tests/postgres/test_06f500af_bug_class_eliminated_pg.py`

### 4. Pre-existing Failure Baseline is Higher Than Documented
The documented baseline of ~102-166 pre-existing failures is outdated. Actual baseline is ~194-205 failures, mostly in:
- `inner_soul` subsystem (66 failures)
- `reasoning_content` edge cases (50 failures)
- `opencode` test collection errors (48 errors)
- `memory` integration tests

These are ALL unrelated to the architecture migration.

### 5. Message Status Endpoint Had No Tests
The `GET /instances/{id}/messages/{msg_id}/status` endpoint (with `running` → `processing` mapping for frontend compat) had zero test coverage before this session. **11 new tests written** covering all status mappings, error propagation, and fallback behavior.

### 6. Observer Correlation/Late Msg Tests Correctly Skipped
26 tests in `test_observer_correlation.py` and `test_observer_late_msg.py` carry `pytestmark = pytest.mark.skip(reason="Phase 5: CorrelationManager removed")`. This is a **positive migration outcome** — the legacy completion authority is gone.

### 7. D13 Simulation Test Design Flaw
`test_d13_single_record_invariant` deliberately inserts a `job_queue_items` row with `job_type='message'` then asserts `job_item_count == 0`. This is contradictory by design. The test's own docstring says it should be replaced with a real `enqueue_message` invocation. The xfail removal was premature.

**Recommendation:** Rewrite this test to call `enqueue_message()` directly and verify 0 JobItems created as a side effect.

### 8. CAST AS TIMESTAMP SQLite Bug (Pre-existing, NOT Migration)
`instance_lifecycle.py:2459` uses `CAST(:now_ts AS TIMESTAMP)`. SQLite converts the ISO string to a numeric value on read-back, breaking `str_to_datetime`. This causes `test_cascade_pause_resume.py` and `test_cold_resume_ttl.py` failures.

**Root cause:** Commit `6759de0c` (Phase 3 resume work), NOT D11-D13 migration.
**Quick fix candidate:** Replace `CAST(:now_ts AS TIMESTAMP)` with `:now_ts`.

---

## Testing Strategy Used
3 parallel sessions worked well:
1. **Full suite** (286s) — broad regression detection
2. **PostgreSQL** (6.5s) — PG-specific invariants
3. **Targeted** (~120s) — 5 specific migration validation areas

All completed within ~12 minutes total wall time (including session overhead).
