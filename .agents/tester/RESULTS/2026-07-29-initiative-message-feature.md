# Test Report: initiative_message Feature

Date: 2026-07-29
Branch: `feature/initiative-message` @ `a0fa7c1e`
Feature commit: `a0fa7c1e feat: add initiative_message — durable first user message for instance search`
Integration test commit: `77b451f8 test: add initiative_message end-to-end API integration tests`

## Summary
- **Total tests: 129 | Passed: 129 | Failed: 0 | Skipped: 0**
- Unit Tests: 41 (SQLite initiative_message) + 16 (PostgreSQL initiative_message) = 57
- Regression: 20 (SQLite search) + 17 (API search) + 22 (PG search) = 59
- Integration Tests: 13 (NEW — created during this validation)
- ensure.md: ✅ All in-scope requirements PASS
- Quick Fixes Applied: 0 (feature code) + 1 environment fix (PG schema grant)
- Quarantined: 0

### Scope Decision
> Full test suite was NOT run. The initiative_message feature is a small, additive change touching 5 files in a single area (instance messaging + search). Blast radius: JSONB metadata capture hook + search condition extension + API field exposure. No cross-module architecture impact. Scoped to the directly-affected test packs (initiative_message SQLite/PG + instance search SQLite/API/PG regression). Release Gate NOT warranted — small additive feature.

## Test Results

### 1. initiative_message SQLite Tests
- **Pack**: `tests/test_initiative_message.py` (829 lines)
- **Result**: ✅ PASS — 41/41 in 1.76s
- **Coverage**: Group 1 (Capture: 17 tests), Group 2 (Hook fires: 3 tests), Group 3 (Search: ~10 tests), Group 4 (Escaping: 4 tests), Group 5 (API + Edge cases: ~7 tests)

### 2. initiative_message PostgreSQL Tests
- **Pack**: `tests/postgres/test_initiative_message_pg.py` (310 lines)
- **Result**: ✅ PASS — 16/16 in 2.16s
- **Coverage**: PG-specific JSONB dialect-aware extraction for initiative_message search
- **PG Environment**: All tests passed. A pre-existing schema privilege issue (missing CREATE on public schema) was identified and fixed by the PG regression worker (see LESSONS).

### 3. Instance Search Regression — SQLite + API
- **Packs**: `tests/test_instance_search.py` + `tests/test_instance_search_api.py`
- **Result**: ✅ PASS — 37/37 (20 + 17) in ~2.5s total
- **Regression status**: NO REGRESSION. The initiative_message extension to `_build_search_condition` preserves existing search behavior.

### 4. Instance Search Regression — PostgreSQL
- **Pack**: `tests/postgres/test_instance_search_pg.py`
- **Result**: ✅ PASS — 22/22 in 2.75s
- **Regression status**: NO REGRESSION.

### 5. Full API Integration Tests (NEW)
- **Pack**: `tests/integration/test_initiative_message_integration.py` (433 lines, NEW)
- **Result**: ✅ PASS — 13/13 in 1.36s
- **Commit**: `77b451f8`
- **Coverage**: End-to-end API capture flow (real `_maybe_store_initiative_message` → repo → API reflection), API-level idempotency, API-level edge cases (truncation >1000, special chars %/_/\, unicode/emoji, multiline, empty, whitespace, None), multi-instance isolation, API search after real capture

## ensure.md Validation Results

### Core — Critical Requirements
- ✅ **No regressions in changed packs** — PASS. All 129 tests in the change set passed (0 failures).
- ✅ **dev.sh includes --timeout-graceful-shutdown 10** — PASS. Static check confirmed (2 occurrences in dev.sh).
- ✅ **No sync DB calls on the asyncio event loop** — PASS (scoped). The capture hook `_maybe_store_initiative_message` wraps all DB reads/writes in `asyncio.to_thread()` (lines 782, 796, 828 in instance_messaging.py).
- ⏭️ **Deadlock / concurrency integrity** — SCOPED OUT. Blast radius does not touch concurrency/atomic-lock code. Not relevant to this additive JSONB feature.

### Core — Important Requirements
- ⏭️ **Async function callers properly await** — SCOPED OUT. Not relevant (no async function signatures changed in this feature).

### Release Gate
- NOT RUN — small additive feature, no cross-module architecture change. Release Gate not warranted.

## ensure.md Improvement Notices
None — no contradictions found between ensure.md requirements and testing rules.

## PG Environment Issue (Resolved)

The developer reported a pre-existing "missing public schema" issue with PostgreSQL tests. Root cause identified during the PG regression run:

**Root cause**: The `ensemble` database role lacked `CREATE` privilege on the `public` schema of the `ensemble_test` database. The `pg_engine` fixture calls `SQLModel.metadata.create_all(engine)`, which requires CREATE on the target schema.

**Fix applied** (environment, not code):
```sql
GRANT CREATE, USAGE ON SCHEMA public TO ensemble;
```

This resolved the issue for ALL PG test packs. Both `ensemble_dev` and `ensemble_test` databases now have correct privileges. See `LESSONS/2026-07-29-pg-schema-privilege-fix.md` for details.

## Test Pack Scripts Created

Created pack scripts for the packs that were referenced in PACKS.md but had no corresponding script on disk:
- `test/packs/initiative_message_unit_test.sh`
- `test/packs/initiative_message_pg_test.sh`
- `test/packs/initiative_message_integration_test.sh`

## Documentation Updated
- [x] RESULTS/2026-07-29-initiative-message-feature.md — this report
- [x] PACKS.md — added 3 new pack entries (initiative_message unit/pg/integration)
- [x] LESSONS/2026-07-29-pg-schema-privilege-fix.md — PG schema grant root cause + fix
- [ ] rules/ensure.md — no changes (user-maintained)
- [ ] MOCK_TESTS.md — no changes
- [ ] QUARANTINE.md — no changes (no flaky tests)

---

### Overall Status
- Unit Tests: ✅ PASS (57/57)
- Regression: ✅ PASS (59/59, no regressions)
- Integration Tests: ✅ PASS (13/13, NEW)
- ensure.md: ✅ PASS (all in-scope critical requirements met)
- **Testing Complete**: ✅ READY — initiative_message feature validated end-to-end
