# Phase 5 CM Removal Testing — Lessons Learned

## Date: 2026-06-23

## Key Patterns

### 1. MagicMock Fixture Gotchas After CM Removal
**Problem:** When CorrelationManager was removed, test fixtures using `MagicMock` for the bus broke silently in three ways:
- `bus._get_parent_lock` returns non-awaitable MagicMock → `TypeError: 'MagicMock' object can't be awaited`
- `bus.get_generation` truthy by default → spurious COMPLETED→PROCESSING re-arms
- `observer._config.use_dependency_bus` False → C1 abort path fails

**Fix:** Always use explicit `AsyncMock` for async bus methods, `MagicMock(return_value=0)` for generation counter, and `MagicMock(use_dependency_bus=True)` for config.

**Affected:** `test_finalize_job_h15.py` (commit `3585cea2`)

### 2. emit_terminal Changed from Module Function to Instance Method
**Problem:** Phase 5 changed `emit_terminal` from a module-level function to an instance method on DependencyBus. Test patches like `patch("daemon.services.dependency_bus.emit_terminal")` broke because there's no module-level attribute to patch.

**Fix:** Remove dead patches; wire a real DependencyBus instance via fixtures. When bus=None, the emit path is naturally dormant.

**Affected:** `test_deadlock_fix.py`, `test_child_reports.py` (commit `04811c54`)

### 3. Sync Mock on Async Method
**Problem:** Production code calls `await bus.count_pending_for_target()` but test fixtures mocked only `bus.count_pending_for_target_sync`. After CM removal, the resume path at `daemon/manager.py:2900` uses the async version.

**Fix:** Add `bus_mock.count_pending_for_target = AsyncMock(return_value=0)` alongside the sync mock.

**Affected:** `test_resume_gate.py` (commit `256e58d7`)

### 4. pytestmark Silent Overwrite
**Problem:** Two consecutive `pytestmark = ...` assignments overwrite each other. A skip marker is silently lost when followed by a postgres marker.

**Fix:** Use list syntax: `pytestmark = [pytest.mark.skip(...), pytest.mark.postgres]`

**Affected:** PG test files (commit `3ad7e766`)

### 5. E2E Tests Catch What Unit Tests Miss
**Problem:** Two production bugs survived all unit tests:
- `waiting_for` column reference in `task/repository.py:804` — only triggered by real SQL execution against PostgreSQL
- `children` field not populated from junction table — only visible in API responses

**Root Cause:** Unit tests mock the database layer, so raw SQL errors and missing fields are invisible. Only E2E tests with real daemon + real DB expose these.

**Lesson:** For architecture changes that drop DB columns, ALWAYS run E2E tests. Unit tests cannot catch schema mismatch regressions.

**Affected:** `task/repository.py`, `instance_lifecycle.py` (commit `fc034988`)

### 6. Phase 4 Column Dropouts ≠ Phase 5
**Problem:** ~100 test failures were initially suspected as Phase 5 regressions but are actually Phase 4 work (dropped `waiting_for`/`children` columns from Instance table).

**Pattern:** When testing Phase N, always distinguish between:
- Tests that reference removed Phase N code (Phase N regression)
- Tests that reference removed Phase N-1 code (pre-existing, carried forward)
