# Phase 6 Testing — Lessons Learned

**Date:** 2026-06-23
**Branch:** `feature/cleanup-old-architecture`
**Commit:** `d45cc0ed`

---

## Key Findings

### 1. Zero New Failures — Clean Consolidation
Phase 6 consolidated `enqueue_message_via_jq` into `enqueue_message()` with `dispatch_path` parameter. 
The consolidation introduced **zero new test failures** across:
- 208 dispatch/pipeline/dispatcher unit tests
- 50 PostgreSQL tests
- 7688 broad SQLite regression tests
- 4 E2E workflow tests (real LLM)

### 2. Pre-existing Failures Improved (29 vs ~63 baseline)
Phase 6 actually *improved* the test pass rate by 34 tests as a side effect of code cleanup.
Key improvements:
- RAG failures: 16 → 1 (−15)
- Title generation failures: 4 → 2 (−2)
- Nudge behavior: 3 → 0 (−3)
- Webfetch builtin: 2 → 0 (−2)

### 3. `_has_no_active_message_job` Guard Intact
The guard has 5 references in `child_reports.py` (1 definition + 4 call sites):
- Line 611: Definition
- Line 1216: `_update_parent_on_child_complete` — parent cascade
- Line 1656: Root carve-out in `_process_child_completion_db_sync` (bus=ON branch)
- Line 1781: `_process_child_completion_db_sync` (inline cascade, child count branch)
- Line 2063: Legacy inline cascade (parent_pending branch)

All 4 call sites follow correct pattern: guard hit → skip WAITING_CHILDREN write, preserve current status.

### 4. PostgreSQL Test Skips Are Intentional
33 PostgreSQL tests are skipped via module-level `pytest.mark.skip` because they target CorrelationManager 
which was removed in Phase 5. These are NOT failures — they document the old API surface.

Affected files:
- `test_premature_completion_regression.py` (15 skipped)
- `test_premature_completion_edge_cases.py` (13 skipped)
- `test_inflight_flag_flip.py` (5 skipped)

### 5. Some Test Files Referenced in Earlier Phases Don't Exist
When running Phase 5/6 tests, these files from earlier documentation don't exist on this branch:
- `tests/test_phase5_real_cm_integration.py` (CM class removed in Phase 5)
- `tests/unit/test_dispatch_completed_fix.py` (covered by `test_dispatcher_path_invariants.py`)
- `tests/test_completion_authority_invariant.py` (Phase A decouple, not cleanup branch)
- `tests/verify_phase4.py` (not on this branch)

Always verify file existence before referencing in test tasks.

### 6. Spec Gotcha: Optimistic Locking Filename
The spec referenced `test_concurrent_optimistic_locking.py` but the actual file is `test_optimistic_locking.py`.
Update PACKS.md references accordingly.

### 7. E2E Tests Are Fast (~3 min for all 4)
Contrary to expectations of ~20 minutes, all 4 E2E workflow tests completed in 185.70s (3:05).
This is likely because the LLM responses are fast and the workflow steps are efficient.

### 8. Full Non-Integration Suite with --override-ini Takes Long
Running `pytest tests/ -m "not integration" --override-ini="addopts="` includes PostgreSQL tests and 
takes >10 minutes. This caused session timeouts. The regression session used the correct approach 
(`-m 'not integration and not postgres'`) which completed in ~5 minutes.
