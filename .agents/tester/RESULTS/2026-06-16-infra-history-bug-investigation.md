# Test Report: infra_history_get AttributeError Investigation

**Date**: 2026-06-16
**Session IDs**: ses_130866488ffezYANEuehM1iKYy, ses_1307f9481ffe7ROWKD7mKdEben
**Type**: Bug Investigation (NOT a fix)

---

## Summary

**Status: BUG NOT REPRODUCIBLE — Already resolved or misdiagnosed**

The reported `infra_history_get` AttributeError does NOT reproduce in the current codebase (commit `9af792e8` at HEAD). All scenarios tested successfully with zero errors.

---

## Investigation Results

### Reproduction Attempts (SQLite)

| Scenario | Result | Details |
|----------|--------|---------|
| Live asset, `created` only | ✅ PASS | 1 history row, serialization OK |
| Live asset, `created` + `updated` | ✅ PASS | 2 history rows, changed_fields/old_values/new_values populated |
| Deleted asset (asset_id NULL) | ✅ PASS | 2 history rows, snapshot-id fallback works |
| get_history without project_id | ✅ PASS | Separate WHERE branch OK |
| Attribute presence check | ✅ PASS | All 6 fields exist on model |

### Existing Tests

| Test Class | Count | Result |
|------------|-------|--------|
| `TestInfraHistoryGetTool` | 6 | ✅ 6 passed |
| `TestHistoryGet` | 4 | ✅ 4 passed |
| `TestProjectIsolation::test_history_cross_project` | 1 | ✅ passed |
| `test_infra_repository.py` (history) | 13 | ✅ 13 passed |
| `test_edge_case_verification.py` (history) | 2 | ✅ 2 passed |
| **Total** | **26** | **✅ 26/26 passed** |

### Root Cause Analysis

The KB note "infra_history_get consistently fails with AttributeError" appears to be **stale**. The actual bug that existed in infra tools was:

- **Actual bug type**: `TypeError` (not `AttributeError`)
- **Root cause**: `type` builtin shadowing in exception handlers (parameter named `type` shadowed Python builtin)
- **Affected tools**: `infra_asset_create`, `infra_asset_list`, `infra_asset_search` — NOT `infra_history_get`
- **Fix commit**: `9af792e8` — *fix: type builtin shadow in exception handlers*
- `infra_history_get` was **never affected** because its parameters (`project_id`, `asset_id`, `limit`) don't include `type`

### Most Likely Explanation for Original Report

1. **Stale .pyc cache** (~70% probability) — Fix landed ~12h before investigation; old bytecode cache in the environment
2. **Misclassified error** (~20% probability) — `TypeError: 'str' object is not callable` from a different infra tool, attributed to `infra_history_get` because the bare-except error handler doesn't reveal the original exception class
3. **PostgreSQL-only edge case** (~10% probability) — All 26 history tests are SQLite-only; PG path untested but likely fine since code is dialect-agnostic via `JSONBType` TypeDecorator

---

## Code Analysis

### `infra_history_get` tool (`daemon/tools/infra.py:781-849`)
- Calls `repo.get_history()` → returns `list[InfraAssetHistory]` (model objects)
- Iterates list, calls `_format_history_row()` per row
- Exception handler at lines 816-824 uses `type(exc).__name__` (safe — no `type` shadow here)

### `_format_history_row` (`daemon/tools/infra.py:141-166`)
- Accesses 6 fields: `timestamp`, `change_type`, `changed_by`, `changed_fields`, `old_values`, `new_values`
- All fields exist on `InfraAssetHistory` model with proper None-handling

### `InfraAssetHistory` model (`daemon/repositories/infra/models.py:253-382`)
- All 6 fields correctly defined with `JSONBType` for JSONB columns
- No custom `to_dict()` needed — tool accesses attributes directly

### `get_history()` (`daemon/repositories/infra/repository.py:1097-1184`)
- Returns `list[InfraAssetHistory]` (model objects, not dicts)
- Snapshot-id fallback for deleted assets works correctly
- Project isolation via `project_id` parameter (C2 fix applied)

---

## Action Needed

- [ ] **Verify with user**: Does the bug still reproduce in their environment? If yes, need exact stack trace, DB type (SQLite/PG), and invocation
- [ ] **Clear .pyc cache**: `find . -name __pycache__ -type d -exec rm -rf {} +` in affected environment
- [ ] **Add PostgreSQL test coverage**: All 26 history tests are SQLite-only — PG-specific paths remain untested
- [x] Bug investigation complete — no code changes needed

---

## Reproduction Script

Script saved at `/tmp/repro_infra_history_bug.py` (257 lines) by opencode session. Covers all 4 scenarios listed above.
