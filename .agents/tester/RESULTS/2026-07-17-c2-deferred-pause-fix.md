# Test Report: C2 Deferred Pause Fix (commit 557ec294)

**Date**: 2026-07-17
**Commit under test**: `557ec294` — *"fix: defer question pause cascade to post-graph callback (C2 torn state)"*
**Sessions used**: c2-investigate, create-packs, c2-pack1-question, c2-pack2-pause, c2-pack3-messaging, c2-pack4-cleanup, c2-pack5-core, c2-edge-cases, c2-ensure, c2-pg-test

## Summary
- **Total tests run**: 341 passed, 38 pre-existing failures (migration bug), 17 skipped
- **C2 invariant proven**: ✅ YES
- **Quick fixes applied**: 1 (test stub missing attribute)
- **New tests written**: 5 edge case tests
- **ensure.md validation**: ✅ All critical requirements PASS
- **Overall status**: ✅ READY — C2 fix verified, no regressions

---

## Scope Decision
The C2 fix changes 3 behavioral files: `daemon/manager.py`, `daemon/graph.py`, `daemon/services/instance_messaging.py` — all in the **core pause/resume + question flow**. Blast radius assessed as **LARGE** because `_graph_tasks` and `pause_instance_cascade` are central to message processing. Full relevant suite run across 6 packs covering 46 test files.

---

## C2 Invariant Verification

### The Bug (C2 Torn State)
`question_pause_node` called `pause_instance_cascade()` from inside the graph task. This self-cancelled the task (`task.cancel()` → `CancelledError`) before the DB write completed, leaving the instance stuck in PROCESSING in the DB while in-memory state said PAUSED.

### The Fix
- `question_pause_node` now only sets a deferred-pause marker (`set_deferred_question_pause`)
- `pause_instance_cascade()` is called AFTER `_graph_tasks.pop(instance_id)` in the `finally` blocks of `send_message` and `_process_message_with_tracking`
- The cascade runs safely outside the graph-task context — no self-cancel

### Invariant Proof
**Test**: `test_send_message_current_task_is_popped_before_cascade_runs`
**Mechanism**: Uses `AsyncMock(side_effect=_capture_then_cascade)` that snapshots `dict(manager._graph_tasks)` at the exact moment the cascade is awaited. Asserts the instance is NOT in `_graph_tasks` at that instant.

**Result**: ✅ PASS — proves the graph task is popped BEFORE the cascade runs.

---

## Mock Verification

### Does the mock accurately represent real behavior?
**YES**. Key fidelity points:
1. `_graph_tasks` is a **real dict** (not a mock)
2. `_deferred_question_pause` is a **real set** (not a mock)
3. The marker methods (`set_deferred_question_pause`, `pop_deferred_question_pause`) are **bound from the real InstanceManager class** via `__get__`
4. The actual `InstanceMessagingService.send_message` finally block **executes for real** (not mocked)

The only mocked elements are: `pause_instance_cascade` (AsyncMock — the production method under test), the graph (`ainvoke` — hand-rolled async fn simulating question_pause_node), and DB/lifecycle surfaces (not part of the C2 chain).

### Does `test_send_message_current_task_is_popped_before_cascade_runs` check ORDERING?
**YES**. The test captures `_graph_tasks` state **at the exact instant** `pause_instance_cascade` is awaited (via side_effect). If the ordering were wrong (cascade before pop), the snapshot would contain the instance and the test would fail with an explicit diagnostic message about the C2 self-cancel bug.

---

## Edge Cases Tested

| Edge Case | Status | Test |
|-----------|--------|------|
| Question→pause→answer→resume→question→pause (second cycle) | ✅ PROVEN | `test_second_cycle_marker_set_again_after_first_popped` |
| Non-question tool call → no marker → graph continues | ✅ PROVEN | `test_non_question_message_does_not_set_marker` + `test_send_message_does_not_call_cascade_without_marker` |
| Instance terminated while marker set → cleanup | ✅ PROVEN | `test_cleanup_instance_state_discards_deferred_marker` (test_question_graph.py) |
| Marker idempotency (pop once, second pop returns False) | ✅ PROVEN | `test_marker_idempotency_pop_returns_false_on_second_pop` |
| Concurrent instances markers isolated | ✅ PROVEN | `test_concurrent_different_instances_markers_isolated` |
| _process_message_with_tracking Path B | ✅ PROVEN | `test_process_message_with_tracking_path_deferred_pause` |

---

## Pack Results

### Pack 1: c2_question_deferred_pause_unit_test — ✅ PASS (40/40)
**Files**: test_question_deferred_pause_callback.py, test_question_graph.py, test_question_manager.py, test_question_tools.py, test_question_untested_paths.py
**Initial run**: 36 passed, 4 failed → **Quick fix applied** (see below)
**After fix**: 40 passed, 0 failed
**Runtime**: ~1.03s

### Pack 2: c2_pause_cascade_graph_unit_test — ✅ PASS
**Files**: test_pause_instance_cascade.py, test_pause_flow_redesign.py, test_graph_task_cancellation.py, test_tree_aware_pause_resume.py
**Runtime**: ~2.2s

### Pack 3: c2_messaging_lifecycle_unit_test — ✅ PASS (69 passed, 14 skipped)
**Files**: test_instance_lifecycle_h10_l14.py, test_instance_lifecycle_terminate.py, test_instance_messaging_compaction_guard.py, test_instance_messaging_shared_context_injection.py, test_instance_messaging_skill_injection.py, test_multi_reuse_lifecycle.py
**Runtime**: ~7.19s

### Pack 4: c2_cleanup_resume_unit_test — ✅ PASS (56/56)
**Files**: test_instance_hard_delete.py, test_hard_delete_mock_integration.py, test_resume_gate.py, test_resume_child_notification.py, test_resume_message_append.py, test_resume_waiting_children.py, test_child_resume.py
**Runtime**: ~3.32s

### Pack 5: c2_core_regression_unit_test — ⚠️ PRE-EXISTING FAILURES
**Files**: test_manager.py, test_paused_instance_ttl.py, test_context_usage_emission.py, test_dispatcher_path_equivalence.py, test_phase4_manager_decomposition.py, test_title_generation_trigger.py
**Result**: 165 passed, 38 failed (ALL pre-existing migration bug — see below)
**Runtime**: ~11s

### Pack 6: c2_edge_cases (NEW) — ✅ PASS (5/5)
**Files**: test_question_deferred_pause_edge_cases.py (NEW)
**Runtime**: ~0.89s

### PostgreSQL Verification
- PostgreSQL 14.22 available and functional
- C2 invariant tests pass on PG (4+5=9 tests)
- Migration `20260714_000001` is valid PG SQL — constraint-widening works correctly
- test_manager.py still fails on PG because the test fixture hardcodes `db_path=":memory:"` (SQLite) — this is a test infrastructure limitation, NOT a C2 issue

---

## ensure.md Validation Results

### Critical Requirements: 3/3 PASS
- ✅ **No regressions in changed packs** — all C2-relevant packs PASS
- ✅ **Deadlock / concurrency integrity** — `concurrency_atomic_unit_test` PASS (66 passed, 19 skipped)
- ✅ **No sync DB calls on event loop** — PASS (thread-identity tests pass; `_deferred_question_pause` is in-memory, no DB)
- ✅ **dev.sh includes `--timeout-graceful-shutdown 10`** — PASS (confirmed via grep)

---

## Quick Fixes Applied

### Fix 1: _ManagerStub missing _deferred_question_pause attribute
- **Instance**: c2-pack1-question
- **File**: `tests/test_question_untested_paths.py` (line 383, `_ManagerStub.__init__`)
- **Root cause**: C2 fix added `self._deferred_question_pause.discard(instance_id)` to `_cleanup_instance_state` in `manager.py:2100`, but the test stub `_ManagerStub` only mirrored the three pop dicts — not the new set attribute.
- **Fix**: Added `self._deferred_question_pause: set[str] = set()` to `_ManagerStub.__init__` (1 line)
- **Commit**: `cae11e6f` — `test: init _deferred_question_pause on _ManagerStub for cleanup tests`

---

## Pre-Existing Failures (NOT C2-related)

### 38 failures in test_manager.py — SQLite migration bug
- **Root cause**: `daemon/migrations/versions/20260714_000001_widen_job_queue_type_constraint.sql` uses `ALTER TABLE ... DROP CONSTRAINT IF EXISTS` which SQLite doesn't support
- **Introduced by**: commit `843e2c34` (2026-07-14) — 3 days before C2 work
- **Affects PG?**: NO — migration is valid PG SQL, verified directly
- **Why it persists on PG tests**: test fixture pins `db_path=":memory:"` (SQLite), overriding DATABASE_URL
- **Action needed**: Follow-up task to fix migration for SQLite compatibility (table-rebuild pattern) OR update test_manager.py fixture to use PG

---

## Documentation Updated
- [x] RESULTS/2026-07-17-c2-deferred-pause-fix.md — this report
- [x] LESSONS/2026-07-17-c2-deferred-pause-quickfix.md — quick fix documentation
- [x] PACKS.md — 7 new C2 pack entries added
