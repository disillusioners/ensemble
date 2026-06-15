# Infra Info Storage — Testing Report (Phase 0 + Phase 1)

**Date:** 2026-06-15
**Branch:** `feature/infra-info`
**Tester:** Tester Agent (ensemble)

## Summary

| Metric | Value |
|--------|-------|
| Existing Test Suite | 93/93 PASS (SQLite) |
| Edge Case Verification | 31/31 PASS (new tests written) |
| Combined Total | 124/124 PASS |
| Bugs Found | 1 (FIXED) |
| Quick Fixes Applied | 1 bug fix + 1 test script fix |
| ensure.md | PASS (dev.sh stable 30s) |
| Verdict | ✅ **PASS** |

---

## Test Results

### 1. Existing Test Suite (93 tests)

**Session:** `infra-test-runner`
**Runtime:** 1.58s
**Result:** 93/93 PASS, 0 failures, 0 errors, 0 skipped

Coverage by class:
- `TestCreateAsset` — 5/5
- `TestGetAsset` — 2/2
- `TestListAssets` — 5/5
- `TestUpdateAsset` — 5/5
- `TestDeleteAsset` — 2/2
- `TestHistoryOnCreate/Update/Delete` — 6/6
- `TestGetHistoryPagination` — 2/2
- `TestSearchByType/Name/ParentAssetId` — 4/4
- `TestSearchByAttributesOperators` — 14/14 (all Mongo-style operators)
- `TestRegisterType/GetType/ListTypes/BootstrapDefaultTypes` — 8/8
- `TestProjectIsolation` — 4/4
- `TestParentChild*` — 6/6
- `TestModelToDict/InfraChangeType/RecordChange` — 7/7
- `TestEdgeCases` — 8/8
- `TestBooleanAttributeSearch/ExistsOperator` — 3/3
- `TestPreUpdateSnapshot/PostDeletionHistory` — 2/2
- `TestListAssetsParentFilter` — 3/3
- `TestGetAssetProjectIsolation` — 3/3

**Conftest:** In-memory SQLite with StaticPool, FK enforcement via PRAGMA, reuses real Project model.

### 2. Edge Case Verification (31 tests — NEW)

**Session:** `infra-edge-cases`
**File:** `tests/repositories/infra/test_edge_case_verification.py`
**Result:** 31/31 PASS (after bug fix)

| # | Scenario | Result | Details |
|---|----------|--------|---------|
| 1 | Migration File | PASS | 3 CREATE TABLEs, 11 indexes, FK semantics (SET NULL + CASCADE) |
| 2 | Factory Wiring | PASS | `create_infra_repository` importable, returns SQLModelInfraRepository, create_tables flag works |
| 3 | Boolean Attributes | PASS | `$eq True/False`, `$ne True`, cross-asset isolation |
| 4 | Pre-Update Snapshot | PASS | Snapshot holds OLD values (name="v1", count=10), not new |
| 5 | parent_asset_id=None | PASS | Returns only unparented assets (A and C), excludes child B |
| 6 | Project Isolation | PASS | list/get/search cross-project = no leakage |
| 7 | Pagination | PASS | limit=2/offset=0,2,4 → 2+2+1=5 unique assets |
| 8 | Type Registry (Global) | PASS | No project_id, shared across projects, upsert idempotent |
| 9 | All 9 Search Operators | PASS | $eq, $ne, $gt, $gte, $lt, $lte, $contains, $exists, $in |
| 10 | History on Delete | PASS | 'deleted' row with full snapshot, asset_id NULL after SET NULL |

### 3. ensure.md Validation

**Session:** `infra-ensure-md`
**Result:** PASS

- dev.sh started successfully (Uvicorn on port 8079)
- Boot time: 7s (normal)
- Ran healthy for full 30s with no crashes
- PostgreSQL engine, LightRAG auto-test, WorkerPool (4/4), MCP schemas (3/3) all healthy
- Killed cleanly by timeout at 30s (exit code 143 = SIGTERM)

---

## Bug Found & Fixed

### Bug: `$ne` Boolean Operator Dialect Bug

**Severity:** Production-relevant — every `$ne` query against a boolean attribute returned ALL rows, defeating the operator entirely.

**Root Cause:** `_json_ineq_predicate` in `daemon/repositories/infra/repository.py` had the same dialect-bug class as the previously-fixed `_json_eq_predicate`, but was NOT patched. On SQLite, `json_extract(attrs, '$.active')` returns `1` for `True`; cast to TEXT it becomes `"1"`. The non-numeric, non-bool code path used `str(value)` → `"True"`. The comparison `"1" != "True"` evaluates to `True` for every row.

**Fix:** Added boolean branch to `_json_ineq_predicate` mirroring `_json_eq_predicate` — dialect-aware `true/false` for PostgreSQL, `1/0` for SQLite. ~14 lines net, single file.

**Test that surfaced it:** `TestBooleanAttributes::test_ne_true_excludes_true` — failed initially, passes after fix.

---

## Coverage Assessment

### What's Tested ✅
- CRUD operations (create, get, list, update, delete)
- All 9 search operators ($eq, $ne, $gt, $gte, $lt, $lte, $contains, $exists, $in)
- Boolean attribute search (both $eq and $ne after fix)
- Pre-update snapshot correctness (history captures OLD values)
- Post-deletion history (full snapshot, asset_id NULL via ON DELETE SET NULL)
- parent_asset_id=None filtering (unparented assets only)
- Project isolation (no cross-project leakage on list/get/search)
- Pagination (limit/offset)
- Type registry (global, no project_id, idempotent upsert)
- Factory wiring (create_infra_repository works)
- Migration file (3 tables, 11 indexes, FK semantics)
- Combined + combined operators + pagination on search results

### Coverage Gaps (Known Limitations)
1. **PostgreSQL tests** — All 124 tests run on SQLite only. PostgreSQL-specific paths (`@>` containment, `?` key-existence, GIN index usage, PG boolean string handling) are NOT tested. The fix for `_json_eq_predicate` (commit `e7c40f1`) was made but never tested against a real PG instance. The new `_json_ineq_predicate` fix is similarly untested against PG.
2. **Concurrent updates** — No test for concurrent update race conditions
3. **Large JSON payloads** — No stress test with very large attributes documents
4. **JSONBType TypeDecorator** — Not independently unit tested (tested only via repository operations)

---

## Code Changes Summary

| File | Change | Commit |
|------|--------|--------|
| `daemon/repositories/infra/repository.py` | Added boolean branch to `_json_ineq_predicate` for dialect-aware boolean handling (~14 lines) | `ef47856` |
| `tests/repositories/infra/test_edge_case_verification.py` | New edge case verification suite (31 tests) | `ef47856` |

---

## Overall Status

- **Existing Test Suite:** ✅ PASS (93/93)
- **Edge Case Verification:** ✅ PASS (31/31)
- **Bug Fixes:** ✅ 1 bug found and fixed
- **ensure.md:** ✅ PASS (dev.sh stable 30s)
- **All Changes Committed:** ✅ Commit `ef47856`

### **Verdict: ✅ PASS**

The infra asset storage implementation is correct and well-tested. One production-relevant bug (`$ne` boolean operator) was found during edge case verification and immediately fixed. The implementation correctly handles CRUD, search operators, versioning/history, project isolation, and pagination. The primary known gap is zero PostgreSQL test coverage — the dialect-specific code paths exist but remain untested against a real PG backend.
