# Plan Overview: Finish Architecture Migration

## Objective

Complete the remaining architecture migration items: close the 06f500af orphan-watcher bug class, collapse the MESSAGE-vs-Job dual-record coupling (D11+D13), and finalize documentation/test cleanup. The goal is a **single work record per user message** with the DependencyBus as sole completion authority.

## Scope Assessment

**LARGE** — Multiple coupled subsystems touched across ~18 source files and ~20+ test files. The core refactoring (D11+D13) changes how user messages flow through the system (eliminating the JobItem lifecycle for messages) and touches the HTTP API contract, the observer finalization chain, and the pause/resume checkpoint path. However, codebase exploration revealed that **several items the LESSONS docs described as "incomplete" are already partially or fully done**.

### Key Discovery: Actual Code State vs LESSONS Docs

| Item | LESSONS Doc Says | Actual Code State (Verified) |
|------|-----------------|------------------------------|
| `cancel_for_source` method | "needs to be added" | ✅ **EXISTS** — `dependency_bus.py:885-997`, already wired into retry paths |
| Bus notification on retry | "never notifies the bus" | ✅ **ALREADY WIRED** — `_notify_bus_of_cancel_and_retry` at `stale_task_recovery.py:490-543`, called after all retry-scheduled paths |
| Worker pool bus cancel | "never notifies the bus" | ✅ **ALREADY WIRED** — `_cancel_bus_watchers_for_task` at `worker_pool.py:463-496`, calls `bus.cancel_for_source` |
| Migration DROP TABLE instance_hierarchy | "broken, would drop live table" | ✅ **ALREADY FIXED** — migration `20260621_000002` has NO DROP TABLE; only drops `waiting_for` + `children` columns |
| `_ensure_postgres_drop_legacy_columns` | "currently a NO-OP" | ✅ **ALREADY EXTENDED** — `manager.py:1917` has the real `ALTER TABLE DROP COLUMN` statements |
| `waiting_for`/`children` column reads | "324 matches across 19 files" | ✅ **MOSTLY CLEANED** — `waiting_for` has only 2 hits (the ALTER TABLE + log msg); `.children` has 0 active reads (4 comment-only hits) |
| Dead test files | "still exist" | ✅ **ALREADY DELETED** — all 3 test files confirmed absent |
| "bus is default/CM fallback" framing | "still in docs" | ✅ **ALREADY REMOVED** from source code (0 hits in daemon/) |
| `_has_no_active_message_job` guard | "kept, review for removal after D13" | Still present — review deferred to D13 landing |
| `enqueue_message` dispatch_path | "still exists" | ❌ **STILL EXISTS** — `instance_messaging.py:887-1045` has both paths |
| D11 branch (`if job_type == 'message'`) | "still at line 686" | ❌ **STILL EXISTS** — `job_processor.py:687-761` |
| D13 (`enqueue_job` creates MESSAGE JobItems) | ❌ **STILL EXISTS** — `job_queue_service.py:379, 500, 1256` (THIRD branch found by reviewer) + **3 BLOCKING consumption sites** (approver: `resume_processing_job`, observer finalize chain, `job_continue` gate) |  |
| Startup sweep for orphan watchers | "not implemented" | ❌ **NOT IMPLEMENTED** — `start()` only warms cache + recovers FIRED rows |

### Revised Scope

Based on the actual code state, the scope is smaller than the LESSONS docs suggest for Items 1, 3, 4 — but the D11+D13 coupling elimination (Item 2) is **larger than initially planned** because the approver identified three BLOCKING consumption-site issues that depend on MESSAGE JobItems existing:

- **Item 1 (06f500af bug)**: Mostly done — `cancel_for_source` exists and is wired. **Remaining**: (a) verify permanent-fail paths call bus.emit_terminal, (b) add startup sweep for orphan PENDING watchers as defense-in-depth.
- **Item 2 (D11+D13)**: **The core remaining work — now with 3 blocking consumption-site rewrites.** Phase 2 eliminates JobItem creation; **Phase 2.5** rewrites all consumption sites that look up MESSAGE JobItems (observer finalization chain, resume routing, concurrency gate). These must land together — eliminating creation without rewriting consumption breaks the system.
- **Item 3 (Phase 4 column drop)**: **Largely done.** Migration fixed, columns dropped, references cleaned. **Remaining**: InstanceInfo `children` field review (semantic — populated from junction table, not dropped column), grep sweep for any residual references.
- **Item 4 (Docs/test cleanup)**: **Largely done.** Dead tests deleted, bus-as-optional framing removed from source. **Remaining**: verify historical plan docs don't cause confusion, update LESSONS to reflect actual state.

## Context

- **Project**: agents-ensemble
- **Working Directory**: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble`
- **Branch**: `feature/finish-architecture-migration` (already created from `latest`)

## Phase Index

| Phase | Name | Objective | Dependencies | Coupling | Est. Time |
|-------|------|-----------|-------------|----------|-----------|
| 0 | Acceptance Test (Red) | Write `test_06f500af_bug_class_eliminated` E2E test FIRST — will fail until later phases land | None | — | 0.5 day |
| 1 | Orphan Watcher Defense-in-Depth | Add `fetch_all_pending()` to watcher repo; add atomic startup sweep for orphan PENDING watchers; verify permanent-fail bus coverage; regression tests | Phase 0 (test goes green) | — | 1 day |
| 2 | D13 — Eliminate MESSAGE JobItem Creation | Route `enqueue_message` through WorkerPool path only; make `enqueue_job` reject MESSAGE jobs; rewrite `get_message_status` endpoint; clean up ALL `job_type="message"` sites; data migration for in-flight MESSAGE JobItems | None (independent of Phase 1) | independent | 2–2.5 days |
| 2.5 | Observer + Resume Consumption-Site Rewrite | Rewrite `_get_processing_job_for_instance`, `_process_resume_finalize`, `_finalize_job`, `resume_processing_job` routing, and `job_continue` concurrency gate to work with Task rows instead of MESSAGE JobItems | Phase 2 (tight — D13 eliminates the JobItems this phase replaces lookups for) | tight | 2–3 days |
| 3 | D11 — Collapse job_processor MESSAGE Branch | Remove `if job_type == 'message':` branch; remove dead `dispatch_path=jobqueue_local` log; all jobs through single dispatch path | Phase 2 + Phase 2.5 (tight — D13 + consumption-site rewrites must land first) | tight | 1 day |
| 4 | Phase 6 — Remove `dispatch_path` Parameter | Remove `dispatch_path` from `enqueue_message` signature; always write Task + MessageQueue rows | Phase 2 + Phase 3 | tight | 0.5–1 day |
| 5 | Guard Removal + Regression Invariants | Review/remove `_has_no_active_message_job` + its tests; extend `TestBusSoleAuthority` with single-record invariant; HTTP API adapter verification; full test suite green | Phase 2 + Phase 3 + Phase 4 | tight | 1–1.5 days |
| 6 | Column/Docs/Test Final Cleanup | Verify InstanceInfo `children` field semantics; clean residual doc references; update LESSONS to reflect actual state | None (independent) | independent | 0.5–1 day |

### Coupling Assessment

| Phase pair | Coupling type | Justification | Can parallel? |
|---|---|---|---|
| 0 → 1 | loose | Phase 0 writes the test; Phase 1 implements the sweep that makes it pass | **No** (test-first) |
| 0 → 2 | loose | Phase 0's E2E also validates the D13 structural fix (parent doesn't strand after child crash + restart) | **Yes** (can write tests in parallel) |
| 1 ↔ 2 | independent | Different files (bus/startup sweep vs enqueue_message/job_queue_service) | **Yes** |
| 2 → 2.5 | tight | Phase 2.5 rewrites all consumption sites that look up MESSAGE JobItems. Phase 2 (D13) eliminates the creation of those JobItems. The lookups must be rewritten BEFORE or TOGETHER WITH the elimination — otherwise every consumer returns None and the system breaks. | **No** |
| 2.5 → 3 | tight | Phase 3 removes the job_processor branch. Phase 2.5 must land first so the observer finalization chain works without JobItems. | **No** |
| 2 → 3 | tight | Phase 3 removes the job_processor branch that Phase 2 makes dead. D13 must land first so no MESSAGE JobItems are created when the branch is removed. | **No** |
| 3 → 4 | tight | Phase 4 removes the `dispatch_path` parameter. Must happen after D13 (Phase 2) eliminates the jobqueue path and D11 (Phase 3) removes the branch that reads it. | **No** |
| 4 → 5 | tight | Phase 5 reviews the guard that only becomes redundant after dispatch_path is removed (Phase 4). | **No** |
| 1 ↔ 6 | independent | Different areas entirely | **Yes** |
| 2 ↔ 6 | independent | Different areas entirely | **Yes** |

### Parallelization Strategy

```
Phase 0 (acceptance test, red) ──► Phase 1 (orphan sweep, test→green) ──┐
                                  Phase 2 (D13) ──► Phase 2.5 (consumption sites) ──► Phase 3 (D11) ──► Phase 4 (dispatch_path) ──► Phase 5 (guard+invariants, full suite green)
Phase 6 (cleanup/docs) ─────────────────────────────────────────────────┘
```

- **Launch Phase 0 FIRST** (write the acceptance test that will fail until Phases 1+2 land).
- **Launch Phase 1, Phase 2, and Phase 6 in parallel** after Phase 0 (independent areas).
- **Phases 2→2.5→3→4→5** are sequential (tight coupling through shared files and dependent changes).
- **Critical path**: Phase 0 → Phase 2 → Phase 2.5 → Phase 3 → Phase 4 → Phase 5 (~7.5–8.5 days).

## Test Budget (W3)

**20+ test files** need changes across Phases 2.5+3+4+5. Run the **full test suite** after each phase, not just targeted unit tests.

| Phase | Test File | Change Type |
|-------|-----------|-------------|
| 0 | `tests/e2e/test_06f500af_bug_class_eliminated.py` (new) | **Create** — E2E: spawn parent+child, crash child, restart daemon, assert parent exits WAITING_CHILDREN |
| 1 | `tests/unit/test_dependency_bus.py` | **Add** — `_sweep_orphan_watchers` tests (paused exempt, orphan cancelled, permanent-fail) |
| 2 | `tests/unit/test_instance_messaging.py` | **Add** — D13 invariant: no JobItem created |
| 2 | `tests/unit/test_job_queue_service.py` | **Add** — `enqueue(job_type="message")` raises ValueError |
| 2.5 | `tests/unit/test_pause_flow_redesign.py` (or E2E) | **Add** — pause/resume E2E for root instance (checkpoint resume via Task) |
| 2.5 | `tests/unit/` (new) | **Add** — `job_continue` concurrency gate (Task-based) |
| 2.5 | `tests/unit/services/test_job_feedback_observer.py` (or new) | **Add** — observer finalize without JobItem (`job_id=None` path) |
| 2 | `tests/unit/test_api.py` | **Update** — `dispatch_path="jobqueue"` at line 816 |
| 2 | `tests/unit/test_messages_api.py` (or `test_api.py`) | **Update** — `get_message_status` queries task, not JobItem |
| 3 | `tests/job_queue/test_job_processor.py` | **Rewrite** — MESSAGE-branch tests need full rewrite (branch removed) |
| 3 | `tests/test_dispatcher_path_equivalence.py` | **Rewrite** — entire file tests jobqueue vs workerpool paths; both must be collapsed |
| 3 | `tests/test_enqueue_shared.py` | **Rewrite** — 15+ `dispatch_path="jobqueue"` test cases |
| 3 | `tests/test_dispatcher_path_invariants.py` | **Update** — `enqueue_message_via_jq` guard message references `dispatch_path='jobqueue'` |
| 3 | `tests/test_manager.py` | **Update** — `dispatch_path="jobqueue"` at lines 1757, 1812 |
| 3 | `tests/test_pause_terminate_matrix.py:92` | **Update** — `job.job_type = "message"` setup |
| 3 | `tests/test_report_lane_phase2.py:146` | **Update** — `job_type: str = "message"` default |
| 5 | `tests/unit/services/test_child_reports.py` | **Delete** — `_has_no_active_message_job` tests (if they exist; search for method name references) |
| 5 | `tests/unit/test_dependency_bus.py` | **Add** — `TestBusSoleAuthority` single-record invariant |
| 5 | `tests/test_watch_job_integration.py` | **Update** — `TestMixedMessageAndJob` class may need adjustment |
| 5 | `tests/test_jq_error_reporting.py` | **Update** — verify MESSAGE job error reporting still works without JobItem |

**Testing rule**: After each phase lands, run `pytest tests/ -x` on PostgreSQL. Fix any breakage before proceeding to the next phase.

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| **B1 — `resume_processing_job` routing breaks (root instances lose checkpoint resume)** | **CRITICAL** | **Phase 2.5**: Rewrite `resume_processing_job` to route via Task rows (`has_inflight_task` or new `find_paused_or_running_by_instance`) instead of `find_processing_message_jobs_by_instance`. Root = has paused/running PROCESS_MESSAGE task; child = no such task. |
| **B2 — Observer finalization chain dead after D13 (`_process_resume_finalize`, `_process_event` return early)** | **CRITICAL** | **Phase 2.5**: Rewrite `_get_processing_job_for_instance` to return Task-based context or add alternative finalize path. Rewrite `_finalize_job_db_sync` to transition the Task (not JobItem) — Step 1 becomes a no-op (no JobItem), Step 2 (instance status) stays, Step 3 (lock release) may be moot. |
| **B3 — `job_continue` concurrency gate disabled (race condition)** | **CRITICAL** | **Phase 2.5**: Replace `find_processing_message_jobs_by_instance` gate with `task_repo.has_inflight_task(instance_id)` or `find_running_by_instance(instance_id)`. |
| In-flight MESSAGE JobItems orphaned after D13 (no processor) | **high** | Phase 2 Task 2.8: data migration `UPDATE job_queue_items SET status='cancelled' WHERE job_type='message' AND status IN ('pending','processing')`. Must work on SQLite + PostgreSQL. |
| D13 changes break HTTP API contract (`job_id` response shape) | high | Phase 2 Task 2.5: adapter returns `task.id` as `job_id`; verify `job_continue` tool still works |
| `get_message_status` endpoint breaks (queries MESSAGE JobItems) | high | Phase 2 Task 2.7: rewrite to query `task` rows instead |
| Removing `_has_no_active_message_job` guard causes parent to strand | high | Phase 5: ONLY remove after TestBusSoleAuthority invariant passes; keep guard as no-op initially |
| `job_continue` tool breaks (only remaining consumer of `job_id` for messages) | medium | Phase 2: adapter mapping `task.id` → `new_job_id` in response |
| Startup sweep cancels watchers for PAUSED tasks (false positive) | high | Phase 1: atomic sweep `WHERE state='pending' AND source_task_id NOT IN (SELECT id FROM tasks WHERE status IN ('running','pending','paused'))` |
| Permanent-fail paths don't call bus.emit_terminal | medium | Phase 1: verify manager._send_error_report routes to bus; add explicit cancel_for_source if missing |
| `cancel_for_source` race with retry watcher registration | low | Already handled — retry creates its own Task id; cancel_for_source only touches the old task's watchers |
| Missed `job_type="message"` cleanup sites cause dead code | medium | Phase 2 Task 2.6: comprehensive grep `grep -rn 'job_type.*message\|JobItem\.job_type.*message' daemon/` before declaring complete |
| 17+ test files break across phases — cascading failures | medium | Run full suite after each phase (W3); fix all breakage before proceeding |
| Observer orphan-race generation counter depends on JobItem re-arm (COMPLETED→PROCESSING) | medium | Phase 2.5: the re-arm path transitions the JobItem back to PROCESSING. After D13, there is no JobItem to re-arm — the generation counter mechanism needs rethinking for the Task-based world. May need to re-arm the Task instead. |

## Success Criteria

- [ ] `test_06f500af_bug_class_eliminated` E2E test passes (C4)
- [ ] **B1 — `resume_processing_job` routes root instances via Task rows (checkpoint resume preserved)**
- [ ] **B2 — Observer finalization chain works without JobItems (instance reaches terminal after resume)**
- [ ] **B3 — `job_continue` concurrency gate uses Task-based check (no race condition)**
- [ ] No user message creates a `job_queue_items` row with `job_type="message"` (D13 invariant)
- [ ] After a user message, exactly one `task` row exists, zero `job_queue_items` rows for messages
- [ ] `grep -rn 'job_type.*==.*"message"\|dispatch_path.*jobqueue\|JobItem\.job_type.*message' daemon/` returns 0 hits in source
- [ ] `enqueue_message` has no `dispatch_path` parameter
- [ ] `enqueue_job` raises `ValueError` on `job_type="message"`
- [ ] `get_message_status` endpoint queries task rows, not JobItems
- [ ] DependencyBus `start()` sweeps orphan PENDING watchers on startup (atomic UPDATE)
- [ ] Paused tasks' watchers are NOT cancelled by the startup sweep
- [ ] All existing tests pass on PostgreSQL (full suite, all modified files)
- [ ] `job_continue` tool returns a valid `new_job_id` (mapped from task.id)
- [ ] HTTP `POST /messages` returns same response shape (message_id-based)
- [ ] No in-flight MESSAGE JobItems left orphaned (data migration applied)
- [ ] **Pause/resume feature works end-to-end for root instances (checkpoint resume → terminal transition)**

## Tracking

- **Created**: 2026-06-26
- **Last Updated**: 2026-06-26 (approver blocking issues B1–B3 incorporated — Phase 2.5 added)
- **Status**: approved (pending approver re-review of Phase 2.5)
