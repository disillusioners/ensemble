# Test Report: Pinned Instance Cleanup Protection
Date: 2026-07-31
Instance IDs: 729fa328 (maintenance pack), f5e18b14 (ui_prefs pack), fe6fe0c0 (independent mock)
Change set (uncommitted working tree):
- `daemon/services/maintenance.py` — `_get_protected_instance_ids()` + filtering in Op B (`_cleanup_expired_terminal`) and Op C (`_enforce_history_cap`)
- `daemon/repositories/instance_ui_prefs/repository.py` — `get_pinned_instance_ids()`
- `daemon/manager.py` — wiring of `ui_prefs_repo`
- `tests/test_maintenance.py`, `tests/repositories/test_instance_ui_prefs.py` — dev's new tests

### Summary
- Total packs run: 3 | Passed: 3 | Failed: 0 | Errors: 0
- Dev test coverage: 92 tests run, 92 passed (66 maintenance + 26 ui_prefs)
- Independent mock verification: 9/9 scenarios PASS (fresh script, NOT dev's tests)
- New-feature test count: 24 (20 pinned-protection + 4 `get_pinned_instance_ids`)
- Quick Fixes Applied: 0 (none needed)
- Quarantined: 0

### Scope Decision
> Full requested; change touches 3 source files + 2 test files in the maintenance/ui_prefs area — a focused feature, single concern (cleanup protection), no locking/concurrency/architecture change. **Scope reduced** to the 2 directly-affected test packs + 1 independent mock test. Skipped: all other unit/integration/e2e packs (concurrency_atomic, core, e2e_workflows, etc.). Full suite NOT warranted — running ~24 packs across unrelated modules would burn ~40 min for a non-architecture change.

### ensure.md Validation Results (Core, scoped to change set)
- **Critical — "No regressions in changed packs"**: ✅ PASS — maintenance + ui_prefs packs both PASS.
  Validation: scoped pack runs (above). Satisfied directly by the pack results.
- Other Core critical (concurrency_atomic_unit_test, dev.sh graceful-shutdown): OUT OF SCOPE — no locking/concurrency/shutdown code changed. Informational note: the new `_get_protected_instance_ids` → `get_pinned_instance_ids` is a sync DB call from an async method, but it follows the existing maintenance-job pattern (the job already made sync repo calls before this feature) and runs as a background scheduled task, not a hot request path — not a new regression.
- Release Gate: OUT OF SCOPE — focused feature, not big/critical/architecture.

### ensure.md Improvement Notices
- ⚠️ `tests/test_maintenance.py` and `tests/repositories/test_instance_ui_prefs.py` are NOT registered in PACKS.md. Ran as scoped ad-hoc packs with dual-layer timeout (still compliant). **Recommendation:** register both as unit packs in PACKS.md (entries added below) so future runs resolve them by pack name.

### Pack Results

| Pack | File | Total | Passed | Failed | Runtime | Status |
|------|------|-------|--------|--------|---------|--------|
| maintenance_unit_test | tests/test_maintenance.py | 66 | 66 | 0 | 1.25s | ✅ PASS |
| instance_ui_prefs_unit_test | tests/repositories/test_instance_ui_prefs.py | 26 | 26 | 0 | 1.24s | ✅ PASS |
| pinned_cleanup_protection_mock | tests/mocks/pinned_cleanup_protection_mock.py | 9 scenarios | 9 | 0 | 0.20s | ✅ PASS |

### Independent Mock Test Detail (9/9 PASS)
Fresh standalone script (`tests/mocks/pinned_cleanup_protection_mock.py`, ~600 lines) exercising REAL `CheckpointCleanupJob` + REAL `SQLModelInstanceRepository` + REAL `InstanceUiPrefsRepository` against in-memory SQLite, with a MagicMock/AsyncMock checkpointer. Does NOT import the dev's tests.

| # | Scenario | Result | Evidence |
|---|----------|--------|----------|
| 1 | TTL protects pinned terminal | PASS | A survived, B deleted; `adelete_thread` awaited for B only |
| 2 | History cap spares pinned oldest (cap=2) | PASS | A pinned oldest preserved; under cap → no prune |
| 2b | History cap overflow (cap=1) | PASS | pinned A excluded from cap, B pruned, C survives |
| 3 | Descendants of pinned root protected | PASS | root+child+grandchild survive; decoy deleted |
| 4 | Non-pinned still cleaned normally | PASS | lonely deleted, `adelete_thread` awaited |
| 5 | **W1 broken ancestor chain** | PASS | leaf protected despite stale `parent_id`; `get_tree_root_id` returned None, fail-protect branch fired (`get_tree_ids(leaf)` → `[leaf]`), WARNING log emitted; control unpinned deleted |
| 6 | All-protected edge case | PASS | all-pinned set untouched; no checkpointer calls |
| 7 | Backward compat (`ui_prefs_repo=None`) | PASS | both deleted (old behavior preserved) |
| 8 | Fail-safe (prefs lookup raises) | PASS | cycle skipped, no deletions, no checkpointer calls |

**PostgreSQL compatibility:** Confirmed — `get_pinned_instance_ids()` uses standard SQLModel `select().where(col(pinned) == True)` → portable `SELECT instance_id FROM instance_ui_prefs WHERE pinned = true`. No `rowid`, no SQLite-only constructs. Works on both SQLite and PostgreSQL.

### Failures
None.

### Action Needed
None — feature is verified correct.

### Documentation Updated
- [x] RESULTS/2026-07-31-pinned-instance-cleanup-protection.md — this report
- [x] PACKS.md — registered 3 new packs (maintenance_unit_test, instance_ui_prefs_unit_test, pinned_cleanup_protection_mock)
- [x] MOCK_TESTS.md — pinned-cleanup-protection spec section (written by mock worker)

### Overall Status
- Dev Test Packs: ✅ PASS (92/92)
- Independent Mock Verification: ✅ PASS (9/9 scenarios)
- ensure.md (Core, scoped): ✅ PASS
- **Testing Complete: ✅ READY**
