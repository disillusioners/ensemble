# Job-as-Front-Primitive POC Test Findings (2026-07-03)

## Key Findings

### 1. POC Introduces Zero Regressions
- All 4 POC tests pass
- All 26 regression tests pass
- All 160 active supporting infrastructure tests pass
- All 89 active PostgreSQL tests pass (1 pre-existing failure confirmed NOT caused by POC)

### 2. PostgreSQL Test Helper Schema Drift Found and Fixed
**Critical discovery**: 35 PG tests were failing due to schema drift from the Phase 5
`status → admission_state` migration. The `JobItem` schema dropped the legacy `status`
column in favor of `admission_state` (queued/active/done/dead), but raw-SQL test helpers
in 4 files continued to reference the dropped column.

**Root cause**: Test helpers used raw SQL strings (not ORM) so they weren't caught by
schema migrations. The ORM-based tests all passed; only raw-SQL helpers drifted.

**Fix committed**: `86b45f0f` — Updated INSERT/UPDATE SQL and value constants in:
- `tests/postgres/test_concurrent_enqueue.py`
- `tests/postgres/test_concurrent_status_transitions.py`
- `tests/postgres/test_optimistic_locking.py`
- `tests/postgres/test_jq_proxy_phase2_constraints.py`
- `tests/postgres/test_concurrent_lock_claims.py` (trigger-drop fixture)

**Lesson**: When schema columns are renamed/dropped, grep ALL raw SQL in test files,
not just ORM model references. Raw-SQL test helpers are invisible to migration tooling.

### 3. Pre-Existing Failure: test_pg_restart_survival
`test_dependency_bus_pg.py::test_pg_restart_survival` fails with `assert 0 == 1`.
**Confirmed pre-existing**: Verified by checking out parent commit `3151010f` —
same failure. The watcher doesn't fire after bus restart on the same engine.
Unrelated to POC. Needs separate investigation.

### 4. Skipped Tests Are CM-Removal Related, NOT PG-Related
29 tests skipped (`test_observer_correlation.py`, `test_observer_late_msg.py`,
`test_observer_race1.py`) are skipped due to `CorrelationManager` class removal in
Phase 5 (replaced by `DependencyBus`). They are NOT PG-only tests. Importing
`CorrelationManager` raises `ImportError`. Porting requires multi-day effort.

### 5. POC Test Coverage Gaps
The POC test file covers 2 of 4 success criteria directly:
- ✅ Flag ON normal flow (creation + linkage)
- ✅ Flag OFF regression

2 criteria are implemented but lack direct POC tests:
- ❌ Stuck queued JobItem finalize fallback (covered by 47/47 observer tests)
- ❌ Poll loop filter exclusion (implemented at `repository.py:746`, covered by 28/28 idempotent enqueue tests)

**Recommendation**: Add 2 direct tests (~50-80 lines each, exceeds quick-fix scope).
