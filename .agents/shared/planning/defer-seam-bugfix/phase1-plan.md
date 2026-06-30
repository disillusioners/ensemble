# Phase 1: Join Key + Shared Idle Predicate + Test Infrastructure

**Closes:** P1, P2, F11, F17  
**Categories:** A + B + D  
**PR:** PR 1 — directly fixes the reported user-facing bugs

## Objective
Fix the two reported P1/P2 bugs: (1) stamp `job_queue_items.metadata.message_id` from the task's `message_id` at admission time so the cross-system guard doesn't self-deadlock, and (2) create a shared "active work in project P" predicate that counts Tasks (not just JobItems) so the defer idle-gate and maintenance idle-check see the full picture. Add default-suite invariant tests that exercise the seam contracts on SQLite.

## Coupling
- **Depends on**: None (root phase)
- **Coupling type**: —
- **Shared files with other phases**: `daemon/repositories/task/repository.py` (readers — also touched by Phase 3 for F6), `daemon/services/job_queue_service.py` (`_select_next_eligible_job` — also touched by Phase 2 for F4/F7)
- **Shared APIs/interfaces**: The shared active-work predicate defined here is consumed by Phase 2 (F1 dedup uses `message_id` stamped by Task 1) and Phase 3 (F8 second defer gate, F5 reconciler)
- **Why this coupling**: Phase 1 establishes the foundational contracts (join key, idle predicate) that later phases build upon. The reader hardening (NULL-safe guards) and predicate definition are the stable interfaces.

## Context
- Previous phase completed: N/A (root)
- Key decisions: Two-table model is kept; the seam is hardened in place. The `task` table is the source of truth for "what work is in flight" post-D13.

---

## Tasks

### Task Group A: Make the join key real (P1, F11)

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Stamp `metadata.message_id` after `enqueue_message` returns | In `JobProcessor._process_next_job`, after `enqueue_message` returns, capture the `message_id` from the return value (`AsyncMessageResult` already carries it — see Implementation Notes). Then call the new `stamp_message_id` repo method (Task 2) to write it to the JobItem's metadata. | `daemon/services/job_processor.py:707-720` |
| 2 | Add `stamp_message_id(job_id, message_id)` repository method | New method on JobQueueRepository: `UPDATE job_queue_items SET metadata = json_set(COALESCE(metadata,'{}'), '$.message_id', :message_id) WHERE job_id = :job_id` (SQLite) / `metadata \|\| jsonb_build_object('message_id', :message_id)` (PostgreSQL). Use dialect-aware helper `_json_set_text_sql` modeled after existing `_json_extract_text_sql`. | `daemon/repositories/job_queue/repository.py` (new method) |
| 3 | Harden `claim_pending_task` cross-system guard for NULL `message_id` | Change the carve-out from `NOT EXISTS (...)` to `AND j.metadata->>'message_id' IS NOT NULL AND NOT EXISTS (...)`. This ensures a JobItem with no `message_id` (orphan ACTIVE, not-yet-dispatched) does not block its own instance's task. | `daemon/repositories/task/repository.py:566-570` |
| 4 | Harden `has_pending_tasks_blocked_by_busy_instance` for NULL `message_id` | Same NULL-safe guard change as Task 3, applied to the F11 consumer. | `daemon/repositories/task/repository.py:1097-1101` |
| 5 | Stamp `message_id` on orphan-recovery paths | The orphan-recovery paths at `job_processor.py:551-555` and `:591-595` also call `enqueue_message` without `message_id` linkage. Apply the same stamping after the `enqueue_message` return. | `daemon/services/job_processor.py:540-595` |

### Task Group B: Shared "active work" predicate + `is_deferred` wiring (P2, F2, F8)

> ⚠️ **ATOMICITY REQUIREMENT:** Tasks 6 (predicate), 7 (`is_deferred` wiring), and 9 (Gate A update) **must be committed together in the same PR/commit**. Without Task 7 wiring `is_deferred=(queue.queue_type == "defer")`, ALL tasks have `is_deferred=false` — the predicate in Task 6 would never exclude defer-queue tasks, making the idle-gate ineffective. Task 9 consumes the predicate from Task 6, so they cannot land independently.

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 6 | Define shared `has_active_non_deferred_work(project_id: str \| None = None)` predicate | New repository method on TaskRepository (the task table is the source of truth post-D13). When `project_id` is provided: `SELECT EXISTS(SELECT 1 FROM task t JOIN instances i ON t.instance_id = i.instance_id WHERE i.project_id = :p AND t.status IN ('pending','running') AND t.is_deferred = false)`. When `project_id` is None (system-wide): omit the `WHERE i.project_id = :p` clause to check ALL projects. Returns bool. **Design choice:** overload the predicate to accept `project_id=None` for system-wide checks (Option a) — this avoids a separate method and keeps one code path. | `daemon/repositories/task/repository.py` (new method) |
| 7 | Wire `is_deferred` from queue type to `enqueue_message` | In `_process_next_job`, pass `is_deferred=(queue.queue_type == "defer")` to the `enqueue_message` call. This makes the Task-level defer gate (Gate B) a real backstop and enables the predicate in Task 6 to correctly filter. | `daemon/services/job_processor.py:709-713` |
| 8 | Wire `is_deferred` on orphan-recovery paths | Same `is_deferred` wiring on lines 551-555 and 591-595. | `daemon/services/job_processor.py:540-595` |
| 9 | Update Gate A (`_process_next_job` defer idle-gate) to use shared predicate | Replace `count_active_jobs_in_non_defer_queues` call with `has_active_non_deferred_work(project_id=queue.project_id)`. The JobItem-only count misses virtual jobs. | `daemon/services/job_processor.py:406-419` |
| 10 | Update `_select_next_eligible_job` (Gate B / F8) to use shared predicate | Replace `count_active_jobs_in_non_defer_queues` with `has_active_non_deferred_work(project_id)`. This is the observer admission path (`job_feedback_observer.py:2670`). | `daemon/services/job_queue_service.py:1750-1758` |
| 11 | Update `maintenance._is_idle` to consult active work | Use the system-wide variant: `has_active_non_deferred_work(project_id=None)`. Also add a check for `admission_state IN ('queued', 'active')` JobItems (not just `queued`). The predicate handles all Task rows; the JobItem check is supplementary for queue-policy state. | `daemon/services/maintenance.py:212-242` |

### Task Group D: Test the invariant on SQLite (F17, regression for A–C)

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 12 | Test: defer job Task gets `is_deferred=true` | Seed a defer queue + enqueue a job → verify the spawned Task has `is_deferred=true`. | `tests/job_queue/test_seam_invariants.py` (new file) |
| 13 | Test: `message_id` stamped on JobItem after admission | Seed a job → admit it → verify `job_queue_items.metadata->>'message_id'` matches the Task's `message_id`. | `tests/job_queue/test_seam_invariants.py` (new file) |
| 14 | Test: NULL `message_id` guard in cross-system guard | Seed an active JobItem with NULL `message_id` + a pending Task for the same instance → verify `claim_pending_task` can still claim the task (not self-deadlocked). | `tests/job_queue/test_seam_invariants.py` (new file) |
| 15 | Test: P2 invariant — defer job not admitted during active virtual work | Seed a project with an active non-deferred Task (no JobItem) + a defer-queue JobItem → verify the defer job stays `queued` (not admitted). | `tests/job_queue/test_seam_invariants.py` (new file) |
| 16 | Test: P1 invariant — defer job completes after idle | Seed defer job → project becomes idle → verify the defer Task is claimed and reaches a terminal state (not stuck "processing"). | `tests/job_queue/test_seam_invariants.py` (new file) |
| 17 | Test: `_is_idle` returns False during active work | Seed active JobItem or running Task → verify `_is_idle` returns False. | `tests/test_maintenance.py` (append to existing) |
| 18 | Test: F4/F7 lock invariant on SQLite | Seed instance with JobA (active, lock held) + JobB (queued) → `cancel_job(JobB)` → verify JobA's lock is NOT released. | `tests/job_queue/test_seam_invariants.py` (new file) |

---

## Key Files
- `daemon/services/job_processor.py` — `_process_next_job` (defer gate + enqueue call), orphan recovery paths
- `daemon/services/instance_messaging.py` — `enqueue_message` return value (`AsyncMessageResult` already carries `message_id`)
- `daemon/repositories/task/repository.py` — cross-system guard (P1), `has_pending_tasks_blocked_by_busy_instance` (F11), new `has_active_non_deferred_work` predicate
- `daemon/repositories/job_queue/repository.py` — new `stamp_message_id` method
- `daemon/services/job_queue_service.py` — `_select_next_eligible_job` (F8)
- `daemon/services/maintenance.py` — `_is_idle` (F2)
- `tests/job_queue/test_seam_invariants.py` — new test file for all seam contracts

## Constraints
- All SQL must work on both SQLite and PostgreSQL (use `_json_extract_text_sql` pattern for JSON extraction; for JSON writes, use dialect-aware helpers)
- `is_deferred` is keyword-only on `enqueue_message` (the `*` separator) — callers must use keyword syntax
- Test timeout is 30s per test (pyproject.toml) — seam tests must be fast (no real LLM, no daemon)
- SQLite in-memory engine via `tests/job_queue/conftest.py` — use existing fixtures, don't create new engine setups
- Tasks 6, 7, 9 must be committed atomically — the predicate, wiring, and gate update are interdependent

## Deliverables
- [ ] `job_queue_items.metadata.message_id` is stamped at admission time
- [ ] Cross-system guard is NULL-safe (P1 deadlock fixed)
- [ ] `is_deferred` is wired from `queue.queue_type == "defer"` to `enqueue_message`
- [ ] Shared `has_active_non_deferred_work(project_id=None)` predicate exists and is used by both defer idle-gates + maintenance
- [ ] `maintenance._is_idle` sees active jobs and tasks (not just queued JobItems)
- [ ] `tests/job_queue/test_seam_invariants.py` exists and passes on SQLite
- [ ] All existing tests pass (8000+ SQLite unit tests)
- [ ] PostgreSQL test suite passes (`tests/postgres/`)

## Implementation Notes

### `message_id` stamping approach
**Post-enqueue stamp.** After `enqueue_message` returns, capture `message_id` from the `AsyncMessageResult` return value (it already carries `message_id` from `_PreparedEnqueuedContext` — no changes to the return type needed). Then call `repo.stamp_message_id(job_id, message_id)`. 

The NULL-safe reader guard (Tasks 3–4) is the real fix — it prevents self-deadlock regardless of whether the stamp succeeds. The stamp is defense-in-depth for the carve-out to work optimally. A crash between enqueue and stamp leaves NULL `message_id`, but the NULL-safe guard handles this gracefully.

### Shared predicate — `project_id=None` overload
The `has_active_non_deferred_work` method accepts an optional `project_id`:
- `project_id="abc123"` — scoped to one project (used by both defer idle-gates)
- `project_id=None` — system-wide (used by `maintenance._is_idle`)

When `project_id` is None, the SQL omits the `WHERE i.project_id = :p` clause. This avoids a separate method while keeping one code path. The alternative of inlining SQL in `_is_idle` was rejected to avoid duplicating the predicate logic.

### JSON write dialect handling
SQLite: `json_set(COALESCE(metadata, '{}'), '$.message_id', :message_id)`  
PostgreSQL: `metadata \|\| jsonb_build_object('message_id', :message_id)` or `jsonb_set(COALESCE(metadata, '{}'::jsonb), '{message_id}', to_jsonb(:message_id))`

Use a helper like `_json_set_text_sql(column, key, param)` modeled after the existing `_json_extract_text_sql`.
