# Phase 4 Column Drop — Drop `waiting_for` + `children` DB Columns

## Date: 2026-06-23
## Branch: feature/cleanup-old-architecture

## Key Patterns

### 1. API Field Removal Cascade (CRITICAL FINDING)
**Problem:** When dropping a DB column, you must also check if the API model (Pydantic) field and router constructors were separately removed in prior cleanup commits. The `children` field was dropped from the DB (Phase 4), but Phase 5's cleanup (commit `fc034988`) ALSO removed it from the `InstanceInfo` Pydantic model. The production code was populating `children` correctly from the junction table, but the model never exposed it.

**Symptom:** `GET /api/instances/{id}` returned JSON without a `children` key. E2E tests polling for child IDs timed out because `data.get("children")` returned `None`.

**Fix:** Restored `children: list[str] | None` on `InstanceInfo`, passed it in all 3 router handlers, populated via `list_child_ids()`.

**Lesson:** When removing a DB column that feeds an API response field, trace the full path: DB column → repository → service → model field → router constructor. Prior cleanup commits may have removed intermediate fields.

**Affected:** `daemon/models/instance.py`, `daemon/routers/instances.py`, `daemon/services/instance_lifecycle.py` (commit `3cc8da05`)

### 2. Test Files Testing Removed Columns Must Be Explicitly Handled
**Problem:** `test_waiting_for_atomic.py` had 8 tests directly testing `waiting_for` column increment/decrement atomicity. After Phase 4 dropped the column, these tests errored at the SQL level (column doesn't exist).

**Fix:** Added `@pytest.mark.skip(reason="Phase 4 dropped waiting_for column")` — these tests are testing dead functionality.

**Lesson:** When dropping a column, grep for ALL test files that reference it. Tests that directly test the column's behavior need to be skipped, not left to error.

**Affected:** `tests/message_queue_redesign/test_waiting_for_atomic.py` (commit `06e8c4e3`)

### 3. Test Model Assertions Need Updating When Fields Are Restored
**Problem:** A test asserted `children` NOT in model fields (from Phase 5's CM removal). When Phase 4 testing restored the `children` field, the test broke.

**Fix:** Updated assertion to reflect the new state: `children` field now exists (populated from junction table).

**Lesson:** When fixing one phase's regression, check if prior phases had tests asserting the OPPOSITE state.

**Affected:** `tests/test_models.py` (commit `fa347c46`)

### 4. Phase 5 "111 Failures" Were Mostly Pre-Existing
**Problem:** Phase 5 tester noted ~111 failures as "Phase 4 column dropouts." Actual investigation showed only 3 were truly Phase 4 related.

**Root Cause:** The ~111 count included all pre-existing failures (RAG server missing, config drift, mock fixture issues) that predate the cleanup branch. The Phase 5 tester correctly flagged them as "not Phase 5" but incorrectly categorized all as "Phase 4."

**Lesson:** When attributing failures to a specific phase, verify each failure individually. The baseline pre-existing failure count on this branch is ~60+.

### 5. Large Test Suite Strategy
**Problem:** The full SQLite suite (~8000 tests) takes ~9 minutes to run. Opencode sessions time out at 10 minutes, making a single run barely fit.

**Solution:** Run in batches by directory or use `--tb=no` for the final count run. Or split into parallel sessions by module.

**Lesson:** For 8000+ test suites, either use pytest-xdist for parallelism, or split into module-level batches.
