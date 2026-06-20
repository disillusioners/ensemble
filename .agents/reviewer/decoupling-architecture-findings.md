# Decoupling Architecture Review — Current State Findings
**Project:** agents-ensemble
**Scope:** Job / Task / Message / Correlation decoupling (RESEARCH ONLY)
**Investigator:** deep-codebase research
**Status:** Complete

---

## 1. Current Architecture Overview

### 1.1 Destination Architecture (from `docs/plans/decouple-job-task-message-correlation.md`, v2 single-run delivery plan, 508 lines)

The plan collapses v1's 8 sequential milestones into **4 phases** landed on one branch with **4 feature flags** as safety nets:

| Phase | Merged milestones | Goal | Effort |
|---|---|---|---|
| **Phase A — Authority & visibility** | M1 + M2 (~4 days) | ADR-011 enforced in code; premature-completion bug structurally impossible under `USE_LEGACY_WAITING_FOR_CASCADE=OFF` | ~4 days |
| **Phase B — Close the bug class** | M3 (~1 day) | Route `watch_job` through CM; all 3 repro variants structurally impossible | ~1 day |
| **Phase C — Single dispatcher** | M4+M5+M6 (~2 weeks) | One enqueue function; JobQueue scheduling only; WorkerPool execution only; Execution Gate ~40 lines | ~2 weeks |
| **Phase D — Dependency Bus & cleanup** | M7+M8 (~1.5 weeks) | Dependency Bus as single completion authority; drop `waiting_for`, `children` JSON, `instance_hierarchy` table, `job_type='message'` dispatch | ~1.5 weeks |

**Critical dependencies preserved:**
- Phase A → Phase B (CM must be authoritative before adding `watched_jobs`)
- Phase C-M5 → Phase C-M6 (collapse gate after unifying dispatch)
- Phase D is last (bus must be source of truth before dropping old dispatch)

**4 Feature flags (introduced + removed per phase):**

| Flag | Phase | Default at release |
|---|---|---|
| `USE_LEGACY_WAITING_FOR_CASCADE` | Phase A (M2a) | **OFF** in dev/CI/prod |
| `DEBUG_COMPLETION_INVARIANT` | Phase A (M1c) | ON in dev/CI; OFF prod until release, then ON 2 weeks post-release |
| `USE_LEGACY_JOBQUEUE_DISPATCH` | Phase C (M5a) | OFF immediately after M5f lands |
| `USE_DEPENDENCY_BUS` | Phase D (M7c) | OFF until release day, then **ON** |

**Destination architecture components:**
- **Dependency Bus** (`daemon/services/dependency_bus.py` — new): single source of truth; survives restart; keyed on `source_task_id`
- **WorkerPool-only execution**: JobQueue becomes scheduling vocabulary only
- **`asyncio.Lock` gate**: ~40 lines; no DB lease, no cross-process coordination

### 1.2 Where We Are Now (Pre-Phase A Starting Point)

```
┌─────────────────────────────────────────────────────────────┐
│ 3 COMPLETION AUTHORITIES (can drift)                      │
│  1. Instance.waiting_for counter (decremented atomically)   │
│  2. CorrelationManager._pending dict (per child_id:message_id)│
│  3. child-reports-as-message (Task row to parent's queue)  │
└─────────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────────┐
│ 2 PHYSICAL DISPATCHERS (both call graph.astream)            │
│  - JobQueue path: enqueue_message_via_jq → JobItem         │
│  - WorkerPool path: enqueue_message → Task table           │
└─────────────────────────────────────────────────────────────┘
                  ↓
┌─────────────────────────────────────────────────────────────┐
│ MessageProcessingPipeline (Phase 5 done — 6 shared stages)    │
│ execution_gate.run (DB-backed 707-line gate)               │
│ manager._process_message_with_tracking → graph.astream       │
└─────────────────────────────────────────────────────────────┘
```

**Migration status:** Phase 0 (error handling), Phase 1-4 (partial), Phase 5 (pipeline unification) — all DONE. Phase A/B/C/D NOT STARTED.

---

## 2. Key Source Files — Structure, Responsibilities, Coupling

### 2.1 `daemon/services/correlation_manager.py` — 1051 lines

**Authority #2.** In-memory pending set promoting to sole authority in Phase D.

**Classes:**
- `PendingResponse` (`@dataclass`, 46–54): `parent_id, child_id, message_id, created_at, status`
- `ParentCorrelation` (`@dataclass`, 57–72): `parent_id, pending: dict[str, PendingResponse], had_error: bool` — computed `is_complete`, `pending_count`
- `CorrelationManager` (75–804): singleton with lifecycle + validation

**Data structure — `_pending: dict[str, ParentCorrelation]` (line 119):** in-memory only; Phase B adds `pending_jobs: dict[parent_id, set[child_job_id]` for `watch_job` path.

**Key methods:**

| Method | Lines | Responsibility |
|---|---|---|
| `_get_lock(parent_id) → asyncio.Lock` | 148–162 | Lazy per-parent lock; N3 constraint (main event loop only) |
| `register_message_send(parent_id, child_id, message_id)` | 181–214 | Add entry; mirrors `waiting_for++` |
| `resolve_response(parent_id, child_id, message_id, status) → bool` | 216–369 | Remove entry; H7/H15 fixes; W1 callback deferred past lock release |
| `get_pending_count(parent_id) → int` | 371–382 | Read-only |
| `is_complete(parent_id) → bool` | 384–397 | True if not tracked OR empty |
| `rearm_parent(parent_id) → bool` | 425–487 | C2-PartA: recreate empty slot for wave-2 scenarios |
| `rebuild_from_db()` | 493–584 | Rebuild from DB; logs CM↔DB mismatch warnings |
| `_validate_shadow_mode(parent_id)` | 586–619 | Rate-limited CM↔DB divergence logging |

**Module-level fire-and-forget hooks (lines 850–968):** `notify_corr_register`, `notify_corr_resolve`, `notify_corr_rearm` — no control flow impact on failure.

**External collaborators:** `instance_repository`, `message_queue_repository`, `EventBus` (5000-queue buffer, N1 fix), `JobFeedbackObserver.handle_correlation_complete` (completion callback).

**Coupling contribution:** Wires into `send_message` (increment), `child_reports` (decrement/resolve), `job_feedback_observer` (finalize). Phase D replaces `_pending` with `dependency_watchers` table.

---

### 2.2 `daemon/services/job_feedback_observer.py` — 1626 lines

**The job-track terminal-transition authority.** Translates instance lifecycle → JobItem terminal state.

**Classes:**
- `_InstanceFinalizeResult(NamedTuple)` (103–118): `skip, parent_id, agent_id`
- `_FinalizeJobResult(NamedTuple)` (121–163): 11-field outbox-style result for post-commit side effects
- `JobFeedbackObserver` (166–1626): EventBus subscriber → job completion authority

**Key methods:**

| Method | Lines | Responsibility |
|---|---|---|
| `handle_correlation_complete(parent_id, terminal_status)` | 406–444 | Sole terminal-transition path when CM wired; N4 constraint: MUST NOT re-enter CM |
| `_process_event(event)` | 446–567 | Two-path dispatch: CM-active → in_progress OR shared terminal; CM-none → legacy `waiting_for` fallback |
| `_finalize_job(job, instance_id, terminal_status, error)` | 602–819 | Single WriteGuardSession cascade (H15 fix); gate_deferred → `notify_corr_rearm` via `asyncio.create_task` |
| `_finalize_job_db_sync(...) → _FinalizeJobResult` | 1113–1506 | **The critical 394-line helper.** Contains C1 TOCTOU gate (100+ lines), bounded defer counter, all 3 DB writes in one transaction |
| `_trigger_next_job(job)` | 1508–1626 | M10 fix: rollback orphaned instance if `enqueue_message` fails post-spawn |

**The M0 `WriteGuardSession` gate (lines 1226–1386):** Inside `_finalize_job_db_sync`, this is the single largest coupling point — checks `waiting_for` BEFORE job transition, with dialect-aware `SELECT ... FOR UPDATE` (PG/MySQL/MariaDB only), bounded defer counter `_MAX_GATE_DEFERS=5` (F1 escape valve), rollback-on-exception. Phase A replaces this entire block.

**External collaborators:** `EventBus`, `JobQueueService`, `JobRepository`, `LockRepository`, `CorrelationManager` (get only), `InstanceManager`.

**Coupling contribution:** Wires CM `_pending`, `waiting_for` DB column, JobItem state machine, LockRepository, SSE, CompletionRegistry, EventBus, and `_trigger_next_job` — the single hottest coupling point in the job-track. Phase B/D removes ~7 of these connections.

---

### 2.3 `daemon/services/child_reports.py` — 1676 lines

**Authority #3 + cascade logic.** The densest coupling cluster.

**Outcome strings** (7 types, lines 34–78): `instance_not_found`, `deferred_waiting_children`, `root_waiting_children`, `root_completed`, `idempotency_skip`, `tool_invocation_completed`, `regular_child_completed`

**Key methods:**

| Method | Lines | Responsibility |
|---|---|---|
| `_process_child_completion_and_notify_parent(instance_id, completed_message_id)` | 826–873 | Entry point; fetch content BEFORE transaction (Fix C3) |
| `_process_child_completion_db_sync(...) → _ChildCompletionDbResult` | 875–1434 | 560-line sync helper; 4 dispatch branches; Phase A gates `waiting_for` cascade |
| `_update_parent_on_child_complete(session, instance, completed_message_id)` | 462–725 | Inline cascade in the `_ChildCompletionDbResult` producing path |
| `_dispatch_post_commit_side_effects(result, last_content, completed_message_id)` | 1436–1676 | SSE / CompletionRegistry / lifecycle / CM resolve hook based on outcome |

**Inline cascade `_update_parent_on_child_complete` (462–725):** Inside `_process_child_completion_db_sync`, this is the most tightly coupled method: decrements `waiting_for`, calls `notify_corr_resolve`, queries own-queue messages, sets COMPLETED or WAITING_CHILDREN. Phase A gates all of it.

**`_should_send_completion_report` idempotency (312–405):** Two checks: no pending messages + no existing report keyed by `internal_report:{instance_id}:{completed_message_id}`. Phase D replaces completion-report mechanism entirely.

**Coupling contribution:** Imports and calls 6 repositories + 4 services + LangGraph LLM for summarization. Phase D keeps `_ChildCompletionDbResult` outcome strings but removes all CM/waiting_for/instance_hierarchy coupling.

---

### 2.4 `daemon/tools/instance.py` — 1028 lines

**Focus: `send_message` tool (lines 523–727).**

**`send_message` (523 lines):**
```
validate instance → check terminated → check queue stats →
manager.enqueue_message(...) → message_id →
if sender is parent:
    [M0 parent-revive UPDATE]  ← band-aid Phase A gates
    [atomic waiting_for = waiting_for + 1] ← Phase A gates
    [notify_corr_register before session.commit]  ← C3 ordering fix
    [session.commit]
```

**M0 parent-revive UPDATE (lines 583–670):** Transitions `COMPLETED` → `RUNNING` when sending to prematurely-completed parent. W1 defense: requires active PROCESSING/PENDING job. Phase A gates this entirely (A5).

**C3 ordering fix (lines 684–719):** Registers CM correlation BEFORE `session.commit` so rollback is possible. Phase A routes through `notify_corr_register` instead.

**`spawn_instance` tool (lines 462–521):** Calls `manager.spawn_instance`. Does NOT touch `waiting_for` (only `send_message` does).

**External collaborators:** `InstanceManager`, `InstanceStatus`, `WriteGuardSession`, `get_correlation_manager`, `Session`.

**Coupling contribution:** Single tool couples 4 systems: MessageQueue + Task + waiting_for + CM.

---

### 2.5 `daemon/repositories/task/repository.py` — 1197+ lines

**WorkerPool dispatch table.**

**`Task` model (44–113 of `task/models.py`):** `id` (PK), `instance_id` (indexed), `message_id` (indexed), `task_type`, `status`, `worker_id`, `retry_count`, `cancel_requested`, `result/error` (JSON/text), timestamps, `version` (optimistic lock), `last_heartbeat_at` (liveness signal). No parent_id FK.

**Critical `claim_pending_task` SQL (lines 214–269):** UPDATE with 2 subqueries excluding instance_ids that: (a) have a RUNNING task, (b) have an actively PROCESSING MESSAGE job with `waiting_for=0` AND status≠WAITING_CHILDREN. **Phase 4 marks the `waiting_for` read as DEPRECATED.**

**Key methods:**

| Method | Lines | Purpose |
|---|---|---|
| `claim_pending_task(worker_id) → Task\|None` | 153–285 | Atomic claim; 2 subquery exclusion blocks |
| `requeue_task_with_backoff(task_id, 0.5–2.0s)` | 287–357 | Jittered backoff; prevents CPU spin on hot instance |
| `complete_task / fail_task / cancel_task / force_cancel_and_schedule_retry` | 461–1172 | All atomic UPDATE with status guards |
| `schedule_retry(task_id, ...) → Task\|None` | 791–910 | Atomic parent UPDATE + child INSERT; GIL-safe |

**Coupling contribution:** `claim_pending_task` subqueries join `task`↔`job_queue_items`↔`instances` — the only cross-system SQL guard. Phase C makes this subquery unnecessary.

---

### 2.6 `daemon/repositories/job_queue/repository.py` — 1253+ lines

**JobQueue scheduling layer.**

**`JobItem` model (110–249 of `job_queue/models.py`):** `job_id` PK, `instance_id`, `job_type` (free-form string, `"task"` or `"message"`), `status`, `job_metadata` (JSONB → `metadata`), `version` (optimistic lock), `idempotency_key`, `priority`, timestamps, retry fields.

**Indexes:** 8 total; key ones: `idx_job_queue_status`, `idx_job_queue_instance`, `idx_job_queue_items_status_type_instance` (status + job_type + instance_id), `idx_job_idempotency` (partial UNIQUE WHERE idempotency_key IS NOT NULL AND deleted_at IS NULL).

**Key methods:**

| Method | Lines | Purpose |
|---|---|---|
| `create(...)` | 68–120 | Basic INSERT |
| `create_or_get_by_idempotency_key(...)` | 122–259 | Atomic INSERT ON CONFLICT DO NOTHING; idempotency key partial unique index |
| `get_by_instance(instance_id)` | 278–298 | `ORDER BY created_at DESC, job_id` — terminate→revive ordering guarantee |
| `get_active_by_instance(instance_id)` | 300–324 | PENDING\|PROCESSING only |
| `atomic_transition(job_id, from_status, to_status, **extra) → JobItem` | 604–719 | Single guarded UPDATE; EvalPlanQual recheck; raises `InvalidTransitionError` |
| `find_processing_message_jobs_by_instance(instance_id)` | 502–516 | Indexed lookup; used by JQ pre-flight and lifecycle terminate |
| `start_job / complete_job / fail_job / cancel_job / terminate_job` | 893–1134 | Wrappers over `atomic_transition` |
| `soft_delete / restore / hard_delete` | 1140–1232 | Soft delete pattern; idempotent |

**Coupling contribution:** `job_type` column is the fundamental axis of dispatch divergence. Phase D removes the `"message"` value; Phase C-M5 adds a Task row for local message work so JobQueue admission goes through WorkerPool.

---

## 3. Coupling Points — Where Jobs, Tasks, Messages Tightly Coupled

### 3.1 Two enqueue functions

| | `enqueue_message` | `enqueue_message_via_jq` |
|---|---|---|
| **Definition** | `instance_messaging.py:887` | `instance_messaging.py:1486` |
| **Execution path** | WorkerPool (Task table) | JobQueue (JobItem table) |
| **`create_task_row`** | `True` | `False` |
| **Task row** | Written | Not written | Not written |
| **JobItem row** | Not written | Written with `job_type="message"`, `instance_id` in column | Written with `job_type="message"`, `instance_id` in column |
| **Notifies** | `_worker_pool.notify_work()` | `_dispatch_event_bus` |
| **Key callers** | `send_message`, `_trigger_next_job`, `_job_processor`, `sources/registry`, `resume_processing_job` | `routers/messages` (HTTP), `tools/job_queue` (job_create tool) |

**Shared prelude (`_prepare_enqueued_message`, lines 727–885):** Both paths share: shutdown guard, msg_type resolution, MessageQueue INSERT, instance status transition (IDLE/WAITING_CHILDREN/COMPLETED→RUNNING), MESSAGE_RECEIVED event, commit.

**Phase C-M4 alias:** `enqueue_message_via_jq` becomes thin wrapper of `enqueue_message` with metadata tag. Phase C-M5 routes JobQueue admission through observer. Phase D removes JQ path entirely.

### 3.2 Three completion authorities — mutation sites

**Authority #1: `Instance.waiting_for` column** (220 matches across daemon/)

| Site | File | Mutation |
|---|---|---|
| Increment | `tools/instance.py:672` | `UPDATE instances SET waiting_for = COALESCE(waiting_for,0)+1 WHERE instance_id=:pid RETURNING waiting_for` |
| Decrement (child_reports inline) | `child_reports.py:509` | Atomic SQL UPDATE with CASE clamp; `RETURNING` new value |
| Decrement (child_reports sync helper) | `child_reports.py:1254` | Same SQL, duplicated in `_process_child_completion_db_sync` |
| Reset (pause/resume) | `instance_lifecycle.py:~926` | Reset on resume if parent has pending children |
| In-session SELECT FOR UPDATE gate | `job_feedback_observer.py:1284` | 100-line dialect-aware block in `_finalize_job_db_sync` |
| FIFO SQL subquery | `task/repository.py:261` | `COALESCE(waiting_for,0)=0` in `claim_pending_task` exclusion subquery |

**Authority #2: `CorrelationManager._pending` dict**

| Site | File | Operation |
|---|---|---|
| Register | `tools/instance.py:703` | Before `session.commit` (C3 fix) |
| Register (error path) | `error_reporting.py:529` | `notify_corr_resolve` |
| Resolve | `child_reports.py:567` | `notify_corr_resolve` |
| Resolve (dispatch side) | `child_reports.py:1582` | `notify_corr_resolve` |
| Rearm | `job_feedback_observer.py:720` | `asyncio.create_task(notify_corr_rearm)` |
| Count read | `job_feedback_observer.py:509`, `child_reports.py:936`, `message_job_handler.py:500` | `cm.get_pending_count()` |
| Complete check | `child_reports.py:624`, `job_feedback_observer.py:1205` | `cm.is_complete()` |

**Authority #3: child-reports-as-message**

| Site | File | Mechanism |
|---|---|---|
| Report creation | `child_reports.py:438` | `MessageQueue` + `Task` rows to parent |
| Delivery | Via WorkerPool claim → graph.astream | Standard task dispatch |
| Idempotency | `child_reports.py:386` | `source = f"internal_report:{instance}:{msg}"` |
| Cascade (CM-disabled) | `child_reports.py:672` | Inline `SELECT COUNT(*) FROM message_queue WHERE instance_id=parent AND status IN (READY,PROCESSING,RETRYING)` |

### 3.3 `MessageJobHandler` vs `ProcessMessageProcessor` — Phase 5 unified

**Phase 5 result:** `MessageProcessingPipeline` (783 lines) is the single source of truth for 6 shared stages. Path-specific behavior in `PipelineCallbacks`:

| Callback | JobQueue path | WorkerPool path |
|---|---|---|
| `on_success` | CM-aware skip-complete; emits `in_progress` if pending children | Marks Task COMPLETED |
| `on_error` | No-op (helper handles FAILED) | No-op (worker pool marks FAILED) |
| `on_contention` | Jittered requeue + dispatch-bus notify | Throttled logging + `requeue_task_with_backoff` |
| `on_cancel` | None (outer discriminates pause/terminate) | None (outer re-raises) |

**Pre-flight differences (still in `MessageJobHandler.handle`):** Sibling MESSAGE job check, cross-dispatcher running task check, CTS creation, pre-pickup status transition → RUNNING.

**Post-Phase 5 gap CLOSED:** The historical gap (JQ path missing `dispatch_completed`) is resolved. `_dispatch_completed` is called at `message_processing_pipeline.py:457` for both paths.

### 3.4 `MessageQueue` table as audit trail AND correlation key

**Triple role:**

1. **Audit trail** — `status` field (READY/PROCESSING/COMPLETED/FAILED) is per-message lifecycle; `Event` rows reference `message_id`
2. **Idempotency** — `source` field: `f"internal_report:{instance}:{message_id}"` pattern; Phase 0 deduplication query at `child_reports.py:386`
3. **CM correlation key** — `message_id` originates in `_prepare_enqueued_message` (instance_messaging.py:801) and seeds CM's correlation key `f"{child_id}:{message_id}"` (correlation_manager.py:196)

**Phase D removes role 3** — bus uses `source_task_id` from Task table. Roles 1–2 stay (Event table + idempotency key).

### 3.5 `instance_execution_leases` table + 707-line ExecutionGate

**Migration:** `daemon/migrations/versions/20260614_000002_create_instance_execution_leases.sql` (44 lines):
```sql
CREATE TABLE instance_execution_leases (
    instance_id TEXT PRIMARY KEY,
    holder_id TEXT NOT NULL,
    holder_kind TEXT NOT NULL CHECK (holder_kind IN ('message_job','task','resume')),
    acquired_at TIMESTAMP NOT NULL,
    heartbeat_at TIMESTAMP NOT NULL,
    process_id INTEGER
);
CREATE INDEX idx_lease_holder_id ON instance_execution_leases(holder_id);
CREATE INDEX idx_lease_holder_kind ON instance_execution_leases(holder_kind);
```

**ExecutionGateService (707 lines):**

| Component | Lines | Purpose |
|---|---|---|
| `_local_holders` dict + lock | 278–330 | In-process fast path O(1) holder check |
| `_running_tasks` dict | 288–291 | Per-instance asyncio.Task for cancel_instance_execution |
| `run(instance_id, holder_id, holder_kind, work_fn)` | 336–445 | Acquire lease + run + release |
| `_execute_under_lease` | 447–544 | Run work_fn with heartbeat + cancellation |
| `_lease_heartbeat_loop` | 546–615 | Refresh heartbeat_at; 5-error escalation to LeaseLostError |
| `is_held_locally / _local_holder_id / _mark_local / _unmark_local` | 300–330 | Fast-path optimization |
| `is_held / is_held_by` | 621–632 | Read-only DB queries |
| `cancel_instance_execution(instance_id) → bool` | 634–651 | Interrupt running task |
| `recover_stale_leases(max_age_seconds) → int` | 657–686 | Startup recovery |
| `heartbeat(instance_id, holder_id) → bool` | 692–707 | Refresh heartbeat_at |

**Phase C-M13/M14/M15 (deletion):** Entire class + repo + migration + indexes removed. Phase C-M12 replaces with ~40 lines of `asyncio.Lock`.

### 3.6 `MessageJobHandler.handle` missing `dispatch_completed` — **GAP CLOSED**

**Status: FALSE.** `MessageProcessingPipeline._dispatch_completed` (lines 581–661) is called for BOTH paths at line 457. The gap documented in pre-loaded context is historical (pre-Phase 5). JQ-specific guard (skip internal sources) applied at lines 633–643.

---

## 4. Pain Points

### 4.1 Race conditions (premature-completion bug class)

**Root cause: 3 independent completion authorities that can disagree.**

**Variant A — Wave race:**
1. Wave 1 children complete → CM fires `handle_correlation_complete` → JobFeedbackObserver transitions job to COMPLETED
2. Wave 2 children spawned under terminal job
3. **Result:** Job COMPLETED while children still running

**Variant B — `job_continue`/`watch_job` fire-and-forget:**
- `send_message` increments `waiting_for`; `watch_job` does NOT → CM `_pending` has zero entries but `waiting_for>0` → observer defers job finalization → job stuck in PROCESSING indefinitely

**Variant C — TOCTOU on gate read:**
- `waiting_for` read (non-locking SELECT) → concurrent `send_message` increments `waiting_for` before `SELECT FOR UPDATE` re-check → premature completion

**Fixes in place (C1/C2/C3/C5/W1/W3/H7/S3/H15/F1/F2/F8:**
- C1: `SELECT ... FOR UPDATE` (PG/MySQL/MariaDB only; SQLite global write lock)
- C2-PartA: `rearm_parent()` recreates empty `_pending[parent_id]` slot
- C2-PartB: `send_message` is ONLY increment site for `waiting_for`
- C3: CM register BEFORE session commit with rollback on failure
- C5: Atomic job queue lock acquisition
- W1: CM callback deferred past lock release (no deadlock)
- W3: Fail-safe job FAILED transition on finalization error
- H7: CM callback exception restores `_pending[parent_id]`
- S3: `_locks` dict pruned on correlation complete
- H15: Single WriteGuardSession for all 3 finalization DB writes
- F1: Bounded defer counter `_MAX_GATE_DEFERS=5` escape valve
- F2: Dialect detection for row-level locking
- F8: Active MESSAGE job defense before WAITING_CHILDREN write

### 4.2 M0 band-aid patches

| Band-aid | File:lines | What it does | Phase A action |
|---|---|---|---|
| Parent-revive UPDATE | `tools/instance.py:583–670` | Transitions COMPLETED→RUNNING when send_message targets prematurely-completed parent | Gates behind `USE_LEGACY_WAITING_FOR_CASCADE`; checks active job (W1 defense) |
| WriteGuardSession re-read of `waiting_for` | `job_feedback_observer.py:1226–1324` | SELECT FOR UPDATE + bounded counter | REPLACED with single `cm.is_complete()` call |
| CM rearm_parent scheduling | `job_feedback_observer.py:720` | `asyncio.create_task(notify_corr_rearm)` | Already Phase A compatible |
| notify_corr_register before commit | `tools/instance.py:684–715` | Register BEFORE session commit; rollback on failure | Already Phase A compatible |
| notify_corr_resolve fire-and-forget | `child_reports.py:566–581` | Swallows all exceptions | Phase A keeps this pattern |
| `instance_hierarchy` junction cleanup | `child_reports.py:601–606` | `DELETE FROM instance_hierarchy WHERE child_id=:child` | Phase A keeps; Phase D drops table |

### 4.3 Code duplication between two dispatch paths

**Pre-Phase 5 (RESOLVED):** Near-identical copies of 6 stages inlined in both `MessageJobHandler.handle` and `ProcessMessageProcessor.process`. **Phase 5 unified** via `MessageProcessingPipeline` + `PipelineCallbacks`. Callback pattern (strategy) is the abstraction.

**Remaining divergence in callbacks (5 active divergences):**
1. `on_success` — JQ: CM-aware skip-complete; WP: direct task completion
2. `on_error` — JQ: no-op; WP: no-op (both delegate to helper)
3. `on_contention` — JQ: atomic_transition + dispatch-bus notify; WP: throttled logging + requeue_task_with_backoff
4. `on_cancel` — JQ: outer try/catch discriminates; WP: outer try/catch re-raises
5. Pre-flight checks — JQ: sibling MESSAGE job + running task + CTS creation + pre-pickup status transition; WP: none

Phase C removes divergence 1–5 by collapsing to single dispatcher.

### 4.4 ~700-line ExecutionGate with DB-backed lease

**Problem:** 707 lines + 2 files (`execution_gate.py` + `execution_lease/repository.py` + `execution_lease/models.py`) + migration + 2 indexes + N constraints for what amounts to a per-instance mutex.

**Cross-process assumptions:**
- `process_id` captured but NOT used for recovery (heartbeat staleness is canonical)
- `_local_holders` dict assumes single-process deployment
- Module docstring acknowledges: "If you deploy the daemon across multiple processes/nodes, this gate WILL NOT prevent concurrent execution"
- Phase C-M15 docstring makes this explicit

### 4.5 `instance_hierarchy` table proliferation

**Created by:** `instance_lifecycle.py:1539–1544` (INSERT on spawn), `child_reports.py:601–606` (DELETE on child completion), `instance_lifecycle.py:1531` (DELETE on terminate)
**Read by:** `instance_lifecycle.py:566` (terminate cascade), `child_reports.py:467` (parent lookup)
**Migration tracked by:** `20260402_000001_rename_session_to_instance.sql` (rename from `session_hierarchy`)
**Phase D action:** DROP TABLE. Replaced by `dependency_watchers` table keyed on `source_task_id`.

### 4.6 Additional maintainability issues

- **`waiting_for` reads in 8+ files** (220 grep matches): Every read site needs audit for CM-replacement. Phase A test pack enforces invariant.
- **`job_type='message'` string literals scattered:** Phase D removes the value.
- **`MessageQueue.source` pattern `internal_report:{id}:{id}` fragile:** No schema enforcement. Phase D replaces with `dependency_watchers.workflow.md`-driven table.
- **`_gate_defer_counts` module-level dict** (job_feedback_observer.py:99): Process-global counter with instance_id keys. Not reset on job completion — accumulation over long-lived daemon. Phase A gates it behind flag.
- **Dual `JobItem.instance_id` column semantics:** For MESSAGE jobs it holds the target instance_id; for TASK jobs it's unset. Phase D removes MESSAGE jobs entirely.

---

## 5. Current Database Models

### 5.1 `instances` table (`daemon/repositories/instance/models.py`, lines 47–113)

**Table:** `instances` (SQLModel, table=True)

| Field | Type | Default | Notes |
|---|---|---|---|
| `instance_id` | `str` | — | PK |
| `project_id` | `str\|None` | None | FK target |
| `agent_id` | `str` | — | Indexed |
| `agent_dir` | `str` | — | Indexed |
| `agent_name` | `str\|None` | None | Indexed |
| `parent_id` | `str\|None` | None | Indexed |
| `status` | `str` | `"idle"` | Indexed; values from `InstanceStatus` enum |
| `instance_metadata` | `dict` | `{}` | JSONBType → `metadata` column |
| `children` | `str` | `"[]"` | **DEPRECATED** JSON cache; junction table is canonical |
| `waiting_for` | `int` | `0` | **REBUILD-ONLY CACHE** per ADR-011 |
| `version` | `int` | `1` | Optimistic locking |
| `last_activity_at` | `datetime\|None` | None | Watchdog |
| `created_at` | `str` | `datetime.now().isoformat()` | — |
| `updated_at` | `str` | `datetime.now().isoformat()` | — |
| `paused_at` | `str\|None` | None | Indexed |

**InstanceStatus values:** `IDLE`, `RUNNING`, `WAITING`, `PAUSED`, `COMPLETED`, `ERROR`, `TERMINATED`, `QUEUED`, `WAITING_CHILDREN`, `FAILED`

**InstanceHierarchy table** (`__tablename__ = "instance_hierarchy"`, lines 38–44):
- `parent_id: str` (PK), `child_id: str` (PK), `created_at: str`

### 5.2 `job_queue_items` table (`daemon/repositories/job_queue/models.py`, lines 110–249)

**Table:** `job_queue_items` (SQLModel, table=True)

| Field | Type | Default | Notes |
|---|---|---|---|
| `job_id` | `str` | uuid | PK |
| `agent_id` | `str` | — | — |
| `agent_dir` | `str` | — | — |
| `message` | `str` | — | — |
| `source` | `str` | `"api"` | e.g. `"api"`, `"telegram"`, `"scheduler"` |
| `project_id` | `str\|None` | None | FK target |
| `queue_id` | `str\|None` | None | FK target `job_queues.queue_id` |
| `priority` | `int` | `5` | 1–10 |
| `status` | `str` | `"pending"` | JobStatus enum |
| `created_at` | `str` | `datetime.now().isoformat()` | — |
| `started_at` | `str\|None` | None | — |
| `completed_at` | `str\|None` | None | — |
| `instance_id` | `str\|None` | None | Indexed; set for MESSAGE jobs |
| `error_message` | `str\|None` | None | — |
| `result_summary` | `str\|None` | None | — |
| `job_metadata` | `dict` | `{}` | JSONB → `metadata` column |
| `cancelled_at` | `str\|None` | None | — |
| `deleted_at` | `str\|None` | None | Soft delete |
| `job_type` | `str` | `"task"` | **Critical axis: `"task"`\|`"message"` |
| `retry_count` | `int` | `0` | — |
| `max_retries` | `int\|None` | None | — |
| `idempotency_key` | `str\|None` | None | Partial unique index |
| `failed_at` | `str\|None` | None | — |
| `next_retry_at` | `str\|None` | None | — |
| `version` | `int` | `0` | SQLAlchemy `version_id_col` |

**Indexes:** 8 total. Key: `idx_job_idempotency` (partial UNIQUE WHERE key IS NOT NULL AND deleted_at IS NULL), `idx_job_queue_instance` (instance_id), `idx_job_queue_items_status_type_instance` (status + job_type + instance_id).

### 5.3 `task` table (`daemon/repositories/task/models.py`, lines 44–113)

**Table:** `task` (SQLModel, table=True)

| Field | Type | Default | Notes |
|---|---|---|---|
| `id` | `int\|None` | None | PK autoincrement |
| `task_type` | `str` | `"process_message"` | TaskType enum |
| `instance_id` | `str` | — | Indexed |
| `message_id` | `str\|None` | None | Indexed; correlation key |
| `status` | `str` | `"pending"` | TaskStatus enum |
| `worker_id` | `str\|None` | None | Indexed |
| `retry_count` | `int` | `0` | — |
| `next_retry_at` | `str\|None` | None | — |
| `cancel_requested` | `bool` | False | — |
| `cancel_requested_at` | `str\|None` | None | — |
| `retry_scheduled` | `bool` | False | — |
| `result` | `str\|None` | None | JSON text |
| `error` | `str\|None` | None | — |
| `created_at` | `datetime` | `datetime.now(timezone.utc)` | — |
| `started_at` | `datetime\|None` | None | — |
| `completed_at` | `datetime\|None` | None | — |
| `last_heartbeat_at` | `datetime\|None` | None | Indexed; liveness signal |
| `version` | `int` | `0` | Optimistic lock |

**Indexes:** 1: `idx_task_status_created` (status + created_at).

### 5.4 `message_queue` table (`daemon/repositories/message_queue/models.py`, lines 42–102)

**Table:** `message_queue` (SQLModel, table=True)

| Field | Type | Default | Notes |
|---|---|---|---|
| `message_id` | `str` | uuid | PK |
| `instance_id` | `str` | — | Indexed |
| `content` | `str` | — | — |
| `type` | `str` | `"agent"` | MessageType enum |
| `source` | `str\|None` | None | **Correlation idempotency key** |
| `root_source` | `str\|None` | None | — |
| `status` | `str` | `"ready"` | Indexed |
| `priority` | `int` | `1` | — |
| `retry_count` | `int` | `0` | — |
| `max_retries` | `int` | `5` | — |
| `error_message` | `str\|None` | None | — |
| `last_error` | `str\|None` | None | — |
| `message_metadata` | `dict` | `{}` | JSONB → `metadata` |
| `enqueued_at` | `datetime` | datetime.now(timezone.utc) | — |
| `processing_started_at` | `datetime\|None` | None | — |
| `last_activity_at` | `datetime\|None` | None | — |
| `completed_at` | `datetime\|None` | None | — |
| `next_retry_at` | `datetime\|None` | None | — |
| `processing_task_id` | `str\|None` | None | Indexed FK to task.id |
| `images` | `list[str]\|None` | None | JSONB list of base64 URIs |

**MessageType values:** `HUMAN`, `AGENT`, `SYSTEM`, `COMPLETION_REPORT`, `ERROR_REPORT`
**MessageStatus values:** `PENDING`, `READY`, `PROCESSING`, `RETRYING`, `COMPLETED`, `FAILED`

### 5.5 `job_queues` table (`daemon/repositories/job_queue/models.py`, lines 53–107)

**Table:** `job_queues` (SQLModel, table=True)

| Field | Type | Default | Notes |
|---|---|---|---|
| `queue_id` | `str` | uuid | PK |
| `project_id` | `str` | — | FK target |
| `queue_name` | `str` | `"default"` | max_length=100 |
| `queue_name_lower` | `str` | `"default"` | Case-insensitive unique |
| `queue_type` | `str` | `"fifo"` | FIFO\|PARALLEL\|DEFER; constraint enforced |
| `concurrency_limit` | `int` | `1` | 1–20; DEFER queues must =1 |
| `is_system` | `bool` | False | — |
| `is_paused` | `bool` | False | — |
| `description` | `str\|None` | None | — |
| `default_max_retries` | `int\|None` | None | — |
| `created_at` | `str` | datetime.now().isoformat() | — |
| `updated_at` | `str` | datetime.now().isoformat() | — |

**Constraints:** CHECK queue_type IN values; CHECK DEFER queues concurrency=1; UNIQUE(project_id, queue_name_lower); INDEX project_id

### 5.6 `job_watchers` table (`daemon/repositories/job_queue/watcher_models.py`, lines 19–50)

**Table:** `job_watchers` (SQLModel, table=True)

| Field | Type | Default | Notes |
|---|---|---|---|
| `watch_id` | `str` | uuid | PK |
| `job_id` | `str` | — | FK job_queue_items.job_id |
| `instance_id` | `str` | — | FK instances.instance_id |
| `watch_events` | `list[str]` | ALL_WATCHABLE_EVENTS | JSONB; terminal + in_progress |
| `created_at` | `datetime` | datetime.now(timezone.utc) | — |

**Indexes:** UNIQUE(job_id, instance_id); INDEX job_id; INDEX instance_id

### 5.7 `instance_execution_leases` table

**Migration:** `daemon/migrations/versions/20260614_000002_create_instance_execution_leases.sql` (44 lines)

**Current schema:**

| Field | Type | Notes |
|---|---|---|
| `instance_id` | TEXT PK | LangGraph thread_id == instance_id |
| `holder_id` | TEXT NOT NULL, indexed | Format: `{kind}:{entity_id}` |
| `holder_kind` | TEXT NOT NULL, indexed | CHECK IN ('message_job', 'task', 'resume') |
| `acquired_at` | TIMESTAMP NOT NULL | Wallclock acquisition |
| `heartbeat_at` | TIMESTAMP NOT NULL | Liveness signal; recovery predicate |
| `process_id` | INTEGER | OS PID; diagnostic only |

**Phase C-M13 drops this table** entirely. Phase C-M12 replaces with `asyncio.Lock` dict.

---

## Summary — Key Coupling Map

```
Phase A gates (USE_LEGACY_WAITING_FOR_CASCADE flag):
  ├─ child_reports._process_child_completion_and_notify_parent cascade branches
  ├─ tools/instance.send_message waiting_for increment + parent-revive UPDATE
  ├─ instance_lifecycle pause/resume waiting_for reset
  └─ job_feedback_observer._finalize_job_db_sync WriteGuardSession gate (REPLACED by cm.is_complete)
     └─ job_feedback_observer.handle_correlation_complete ← SINGLE terminal path (Phase A keeps this)

Phase D removes:
  ├─ instance.waiting_for column
  ├─ instance.children JSON cache
  ├─ instance_hierarchy junction table
  ├─ job_queue_items.job_type column (drops 'message' value)
  ├─ execution_gate.py + instance_execution_leases table
  ├─ correlation_manager.py (replaced by dependency_bus.py)
  └─ message_job_handler.py (WorkerPool-only)

Phase C collapses:
  ├─ enqueue_message_via_jq → thin wrapper
  ├─ JobQueue dispatch → scheduling only
  ├─ WorkerPool → execution only
  └─ ExecutionGate → asyncio.Lock (~40 lines)
```
