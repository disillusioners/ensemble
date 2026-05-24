# Test Report: Project Metadata Table Separation
Date: 2026-05-25
Branch: feature/metadata-table
Commits: de4ad4f, d897fc8, 0e59e21 (+ quick fix 9861040)

## Summary
- **New Tests**: 42/42 PASS
- **Regression Tests**: 2593 passed, 0 failed, 27 skipped
- **ensure.md**: ✅ PASS (dev.sh stable 30s+)
- **Quick Fixes**: 1 (test_models.py enum casing)
- **Regressions**: 0

## New Tests: tests/unit/test_project_metadata_table.py

| Category | Tests | Status |
|----------|-------|--------|
| TestSetMetadataRecord (Low-level CRUD) | 10 | ✅ PASS |
| TestSetMetadata (High-level) | 3 | ✅ PASS |
| TestDeleteMetadata (High-level) | 3 | ✅ PASS |
| TestUpdateWithMetadata | 5 | ✅ PASS |
| TestCreateWithMetadata | 3 | ✅ PASS |
| TestDeleteProject | 1 | ✅ PASS |
| TestEnrichment | 5 | ✅ PASS |
| TestAtomicUpsert | 2 | ✅ PASS |
| TestValueTypes | 6 | ✅ PASS |
| TestMigration | 4 | ✅ PASS |
| **Total** | **42** | **✅ All Passed** |

### Coverage Highlights
- ✅ Repository CRUD: set/get/delete/list metadata records
- ✅ Empty key validation (ValueError)
- ✅ Atomic upsert (INSERT ON CONFLICT)
- ✅ High-level set_metadata / delete_metadata
- ✅ update() with project_metadata routing to new table
- ✅ update() with empty dict clears all metadata
- ✅ create() with metadata stores in new table
- ✅ delete() project cleans up metadata records
- ✅ Enrichment: _enrich_project and _enrich_projects load from new table
- ✅ API responses return metadata as dict (backward compatible)
- ✅ All JSON value types: string, number, bool, list, dict, null
- ✅ Migration file: exists, valid SQL, unique constraint, CASCADE

## Regression Tests

| Test Pack | Status | Passed | Failed | Skipped |
|-----------|--------|--------|--------|---------|
| Core Unit Tests | ✅ PASS | 658 | 0 | 0 |
| API Unit Tests | ✅ PASS | 201 | 0 | 8 |
| Job Queue Tests | ✅ PASS | 1073 | 0 | 19 |
| Frontend Tests | ✅ PASS | 661 | 0 | 0 |
| **Total** | | **2593** | **0** | **27** |

## ensure.md Validation
- ✅ dev.sh runs stable for 30+ seconds
- ✅ No crashes detected
- ✅ All services initialized properly

## Quick Fixes Applied
1. **tests/test_models.py** — Enum casing mismatch (`.running` → `.RUNNING`, 14 occurrences)
   - Root cause: Previous branch changed enum naming to UPPER_CASE, test file not updated
   - Fix: Updated all InstanceStatus references to uppercase
   - Commit: 9861040

## Overall Status: ✅ READY
- 42 new metadata tests pass
- 2593 regression tests pass, 0 failures
- dev.sh stable
- No regressions from metadata table separation
