# Plan Overview: Option A — Full D13 Reversal (One Path for Messages)

## Objective

Route all public/external messages through the **standard job queue path** — `JobQueueService.enqueue()` → `start_job_atomic_with_lock` (slot locking) → `JobProcessor` → `spawn_instance_with_mcp` — so messages become first-class jobs with **enforced queue-level concurrency**, eliminating the "mirror" bypass and the dead-letter concurrency config that misleads users via the queue selector UI.

## Scope Assessment

**Scope: LARGE** — Architectural reversal of the D13 "Job-as-Front-Primitive" design.

### Justification
- **15+ distinct code touch points** across 7 files (`job_queue_service.py`, `instance_messaging.py`, `job_processor.py`, `instance_lifecycle.py`, `repository.py`, `manager.py`, `worker_pool.py`)
- **2 mandatory new code branches** (load-existing-instance in spawn path; message-aware dispatch in JobProcessor)
- **1 PostgreSQL trigger** must be rewritten (`trg_job_queue_items_active_lock_guard`)
- **~20+ existing tests** assert the D13 invariant and will break (these tests *must* break — they assert the very behavior we are reversing)
- **System is broken in any mid-state** — the migration must be atomic or carefully ordered to avoid double-dispatch / duplicate-instance scenarios
- **API response contract change** — `message_id` may no longer be immediately available (job may be queued, not yet admitted)

## Context

- **Project**: `agents-ensemble`
- **Working Directory**: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble`
- **Branch target**: feature branch (suggest `feature/queue-dispatch-option-a`)
- **DB requirement**: PostgreSQL is the PRIMARY test DB (see 🟡 critical constraint `v0.5.2+`). All tests must pass against PostgreSQL, not just SQLite.

## Background — The D13 Pattern Being Reversed

D13 ("Job-as-Front-Primitive") established that **Task IS the dispatch primitive; JobItem is a derived mirror**. This created two paths:

1. **Standard path (TASK jobs)**: `enqueue_job` → `start_job_atomic_with_lock` (slot locking, concurrency enforced) → `JobProcessor` poll → `spawn_instance` → `enqueue_message`
2. **Message path (mirror JobItems)**: `enqueue_message_job` → `job_repo.create` (bypasses `enqueue_job` via D13 guard) → `worker_pool.notify_work()` → Task-driven dispatch (NO slot locking, NO concurrency enforcement)

**The bug this fixes**: The queue selector UI exposes `concurrency_limit` (e.g., FIFO with concurrency_limit=1) as if enforced for messages — but it is **dead-letter** for messages by design. Messages only enforce **per-instance** serialization via `ExecutionGate` (a per-`instance_id` `asyncio.Lock`), not queue-level concurrency.

## Touch Point Inventory (15+ touch points across 7 files)

This is the COMPLETE inventory discovered via deep code exploration. Every item below MUST be addressed — the system is broken if any is missed.

| # | File:Line(s) | Current Behavior | Required Change |
|---|---|---|---|
| 1 | `job_queue_service.py:603-607` | D13 guard rejects `job_type='message'` with ValueError | **Remove** the guard |
| 2 | `job_queue_service.py:631-634` | Queue resolution assumes "only TASK jobs reach this point" | **Generalize** — resolve `queue_id` for messages (use selected `queue_id` or fall back) |
| 3 | `job_queue_service.py:2748` (`start_job`) | Unconditionally mints `instance_id = str(uuid.uuid4())` | **Preserve** existing `instance_id` for messages (restore the removed message-specific branch) |
| 4 | `job_queue_service.py:2192` (`_try_start_job`) | Also mints fresh UUID (parallel to `start_job`) | **Preserve** existing `instance_id` for messages here too |
| 5 | `instance_messaging.py:1275-1677` (`enqueue_message_job`) | Creates Task first, then mirror JobItem via `job_repo.create`, eager `queued→active` flip, stamps `message_id` | **Rewrite** — create authoritative QUEUED JobItem via `enqueue()`, NO eager activation, NO inline Task creation |
| 6 | `instance_messaging.py:1629-1635` | Eager `queued→active` flip on mirror | **Remove** — standard path transitions via `start_job_atomic_with_lock` |
| 7 | `instance_messaging.py:1683-1684` | Calls `worker_pool.notify_work()` (Condition channel) | **Replace** with `dispatch_bus.notify_new_job()` (asyncio.Event channel that JobProcessor poll listens to) |
| 8 | `instance_lifecycle.py:3162-3280` (`_spawn_instance_db_sync`) | Always `session.add(new_instance)` — pure INSERT, no load-existing | **Add** load-existing-instance branch: SELECT existing row, reuse if present |
| 9 | `repository.py:921` (`list_pending_by_project`) | `.where(JobItem.job_type != "message")` | **Remove** filter |
| 10 | `repository.py:945` (`list_all_pending`) | `.where(JobItem.job_type != "message")` | **Remove** filter |
| 11 | `repository.py:1022` (`list_pending_by_queue`) | `.where(JobItem.job_type != "message")` | **Remove** filter |
| 12 | `repository.py:2315` (`cancel_all_queued_jobs`) | `.where(JobItem.job_type != "message")` | **Remove** filter |
| 13 | `repository.py:2355` (`find_active_jobs`) | `.where(JobItem.job_type != "message")` | **Remove** filter |
| 14 | `manager.py:3355` | PG trigger skips message-type via `AND NEW.job_type != 'message'` | **Remove** the `job_type != 'message'` conjunct so messages require a lock row |
| 15 | `manager.py:620-636` + `3848-3932` | Startup unconditionally cancels ALL in-flight message JobItems | **Remove** or **version-gate** (would destroy legitimate message jobs on restart) |
| 16 | `job_processor.py:1007-1050` | Assumes every job gets a fresh instance, calls `spawn_instance_with_mcp` always | **Add** message-aware branch: if `instance_id` already exists & valid, reuse it (do NOT spawn duplicate) |
| 17 | `worker_pool.py:267-292, 363+` | Post-claim mirror activation (`_activate_message_jobitem_async`) | **Remove** (standard path activates via `start_job_atomic_with_lock`) |

### Secondary touch points (review only, likely no change)

| # | File:Line(s) | Notes |
|---|---|---|
| S1 | `instance_lifecycle.py:1790` | `if remaining_job.job_type == "message": continue` in instance cleanup — review whether skip is still correct |
| S2 | `instance_lifecycle.py:3732-3768` | `job_type='message'` scoped UPDATE for message cleanup — review |
| S3 | `child_reports.py:900-905, 1413-1418` | Removed `_has_no_active_message_job` guards — review under real active message jobs |
| S4 | `task/repository.py:630-660, 1390-1410` | Comments referencing removed `j.job_type = 'message'` predicates — verify still correct |
| S5 | `repository.py:1590-1628` (`stamp_message_id`) | Keep — still needed; JobProcessor already stamps after Task creation |
| S6 | API response contract | `enqueue_message_job` returns `message_id` immediately today — under standard path, `message_id` may not exist until admission |

## Phase Index

This migration is decomposed into **5 phases** at module level. The ordering is **strictly sequential** — the system is broken in any mid-state, so phases must merge as a coherent unit.

| Phase | Name | Objective | Dependencies | Coupling | Est. Time |
|-------|------|-----------|-------------|----------|-----------|
| 1 | Foundation: Enable `enqueue_job` for messages | Remove D13 guard + generalize queue resolution + preserve `instance_id` in `start_job`/`_try_start_job` | None | — (root) | 4-6h |
| 2 | Receptor: Load-existing-instance in spawn + JobProcessor dispatch branch | Add load-existing branch in `_spawn_instance_db_sync` + message-aware dispatch in JobProcessor | Phase 1 | **tight** | 4-6h |
| 3 | Producer: Rewrite `enqueue_message_job` to use `enqueue()` | Switch from `job_repo.create` + eager flip to authoritative `enqueue()` + `dispatch_bus.notify_new_job()` | Phase 1+2 | **tight** | 4-6h |
| 4 | Filters & Safety: Remove all `job_type != "message"` filters + trigger + startup cancel | Repository filters (×5), PG trigger conjunct, startup migration, WorkerPool activation | Phase 3 | **tight** | 3-4h |
| 5 | Test & Contract: Update ~20+ D13 tests + API response audit | Fix tests asserting D13 invariant, add new Option-A tests, audit `message_id` availability | Phase 1-4 | **tight** | 6-8h |

**Total estimated effort**: 21-30 hours (3-4 focused days)

### Coupling Assessment

| Phase Pair | Coupling | Rationale |
|------------|----------|-----------|
| 1 → 2 | **tight** | Phase 2's load-existing branch depends on Phase 1 preserving `instance_id` through `start_job` |
| 2 → 3 | **tight** | Phase 3's rewritten producer must produce JobItems that Phase 2's JobProcessor branch can correctly dispatch |
| 3 → 4 | **tight** | Removing filters (Phase 4) before the new producer exists (Phase 3) would double-dispatch via both Task path AND JobProcessor |
| 4 → 5 | **tight** | Tests assert the invariants; must be updated after behavior changes land |

**Conclusion: All 5 phases must merge as ONE atomic unit.** There is no safe partial deployment. Feature-flag the entire change if a staged rollout is needed.

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| **Duplicate dispatch** (Task path + JobProcessor both fire) | 🔴 Critical — double message execution | Phase ordering: producer rewrite (Phase 3) lands BEFORE filters removed (Phase 4). Verify no inline Task creation remains. |
| **Duplicate instances** (fresh UUID overwrites existing target) | 🔴 Critical — messages create new conversations | Phase 2 load-existing branch must SELECT-then-decide before INSERT. Phase 1 must preserve `instance_id`. |
| **Startup data loss** (migration cancels legit message jobs) | 🔴 Critical — post-restart data loss | Phase 4 removes/gates `manager.py:620-636`. Must land in same release as producer. |
| **PG trigger violation** (active message without lock row) | 🟠 High — transaction aborts | Phase 4 removes the `job_type != 'message'` conjunct so messages require a lock. Ensure `start_job_atomic_with_lock` runs for messages (it does — no `job_type` filter inside it). |
| **API response contract break** (`message_id` not immediate) | 🟠 High — HTTP/scheduler/tool callers break | Phase 5 audits all 5 callers of `enqueue_message_job`. Either (a) block until admission, or (b) return `job_id` with `message_id` filled lazily. |
| **Recursion hazard** (JobProcessor calls `enqueue_message` to create Task) | 🟡 Medium — infinite loop | Phase 3 keeps internal `enqueue_message` (Task-only, no JobItem) for JobProcessor's own Task creation. The recursion is only if internal messages ALSO route through the queue — they must NOT. |
| **~20+ test breakage** (D13 invariant tests) | 🟡 Medium — test churn | Phase 5 updates tests. These tests *should* break — they assert the behavior we're reversing. Convert them to assert the NEW invariant. |
| **ExecutionGate vs queue concurrency** (two serialization mechanisms) | 🟡 Medium — over-serialization or conflict | ExecutionGate (per-instance) + slot locking (per-queue) are complementary. Same-instance messages still serialize via gate; cross-instance messages now respect queue concurrency. No conflict. |
| **Removed child-report guards** (`_has_no_active_message_job`) | 🟡 Medium — incorrect parent status | Phase 5/S3: review `child_reports.py:900-905, 1413-1418` under real active message jobs. Add behavioral tests. |
| **DB migration rollback** (PG trigger is `CREATE OR REPLACE`) | 🟡 Medium — no auto-rollback | Provide a `down` migration that restores the `job_type != 'message'` conjunct. Document rollback procedure. |

## Success Criteria

- [ ] `POST /instances/{id}/messages` with a FIFO queue (`concurrency_limit=1`) **serializes** N messages — only 1 runs at a time across ALL instances in that queue (not just per-instance)
- [ ] Sending a message to an EXISTING instance reuses it — does NOT create a duplicate Instance row
- [ ] No inline Task creation in `enqueue_message_job` — JobItem is the authoritative dispatch primitive
- [ ] `start_job_atomic_with_lock` runs for message jobs (slot acquired, `job_locks` row written)
- [ ] PG trigger fires for message jobs (no `job_type` exemption)
- [ ] Startup does NOT cancel legitimate in-flight message jobs
- [ ] All existing tests pass after Phase 5 updates (no regressions outside the D13-specific tests)
- [ ] New tests assert Option-A behavior: concurrency enforcement, instance reuse, no duplicate dispatch

## Test Strategy

### Tests That MUST Break (Phase 5 — update to assert NEW invariant)

| Test File | Current Assertion | New Assertion |
|-----------|-------------------|---------------|
| `tests/test_dispatcher_path_equivalence.py:445-490` | `enqueue(job_type='message')` raises ValueError | `enqueue(job_type='message')` succeeds; creates authoritative JobItem |
| `tests/postgres/test_06f500af_bug_class_eliminated_pg.py:40-695` | 0 `job_type='message'` JobItem rows; enqueue rejects messages | Message JobItems now exist; created via standard path |
| `tests/job_queue/test_job_repository_phase1.py` | Filter behavior for `job_type='message'` | Update to reflect filters removed |
| `tests/job_queue/test_job_feedback_observer.py:1513-1732` | Observers exclude message JobItems | Observers handle message JobItems normally |

### New Tests to Add

1. **Concurrency enforcement test**: N messages to different instances in a `concurrency_limit=1` queue → only 1 runs at a time
2. **Instance reuse test**: message to existing instance → no duplicate Instance row
3. **No-double-dispatch test**: message → exactly 1 Task created (via JobProcessor), not 2
4. **Startup safety test**: restart with in-flight message jobs → jobs survive
5. **PG trigger test**: message job admission → `job_locks` row required (no exemption)
6. **Recursion safety test**: JobProcessor processing a job → its internal `enqueue_message` does NOT re-enter the queue

### Test DB Requirement

🟡 **CRITICAL**: Run ALL tests against PostgreSQL, not just SQLite. The PG trigger (`trg_job_queue_items_active_lock_guard`) is PostgreSQL-specific and cannot be validated on SQLite. See critical constraint `v0.5.2+`.

## Tracking

- **Created**: 2026-07-25
- **Last Updated**: 2026-07-25
- **Status**: draft
- **Owner**: planner (this plan) → developer (execution)

## References

- Phase files: `phase1-plan.md` through `phase5-plan.md` (in this directory)
- Critical notes (D13 references): see project critical notes `Job-as-Front-Primitive merged to latest (2026-07-07)` and related
- Pre-loaded RAG context: `job-path-migration-feasibility-message-dispatch-jobqueue-gat_*.md`
