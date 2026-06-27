# Job as Queue Proxy — Cross-Table Invariants & Phase 0 Audit

> **Status**: Phase 0 deliverable (Audit & Documentation only — no code changes).
> **Scope**: This document is both (a) the §2.1 enforcement spec for the cross-table invariants the
> refactored admission-state model must maintain, and (b) the Phase 9 test spec.
> **Companion plan**: `.agents/shared/planning/job-as-queue-proxy/plan.md`
> **Audit date**: 2026-06-28. All line numbers verified against the live codebase on this date.

---

## 1. Cross-Table Invariants (the core spec)

The "Job as Queue Proxy" refactor collapses execution state onto `Instance`, leaving `JobItem` as a
pure queue ticket carrying an `admission_state` column (`queued` / `active` / `done` / `dead`). The
following cross-table invariants define the *correctness contract* of the new model. Every phase
must preserve them, and Phase 9 must assert them.

> **First-of-kind note**: This refactor introduces the **first** `CONSTRAINT TRIGGER` / `DEFERRABLE`
> usage anywhere in the codebase. There is no precedent — the line `manager.py` reviewers
> previously cited (`DROP CONSTRAINT` on an FK, ~line 1997) is *not* a trigger. The trigger design
> in §8.7 of the plan therefore cannot assume prior operational experience with deferred triggers;
> see the invariants doc `waiting_for` note below for a related precedent on schema additions.

### 1.1 `admission_state='active'` ⇔ a `JobLock` row exists

> `JobLock.instance_id = JobItem.instance_id`

This is the **concurrency-correctness invariant** of the entire system (plan §8.7). Violating it
causes one of two failure modes:

- `active` **without** a lock → the worker pool can double-dispatch the same admission slot.
- A `JobLock` **without** an `active` job → the defer-gate and `count_active_jobs*` miscount,
  silently breaking FIFO priority in mixed-queue projects.

The invariant is *bidirectional* — both directions must hold. A single-direction check (active ⇒
lock) misses the silent "lock exists but job isn't active" failure mode.

### 1.2 `admission_state IN ('queued','active')` ⇔ `deleted_at IS NULL`

Soft-delete (`deleted_at`) is the queue-membership lifecycle marker. A terminal or dead-lettered
job may be soft-deleted; a `queued` or `active` job must never be. The partial-unique idempotency
index `idx_job_idempotency` (`models.py:146-152`) depends on this — its
`WHERE idempotency_key IS NOT NULL AND deleted_at IS NULL` predicate would be unsound otherwise.

### 1.3 `admission_state='done'` ⇒ `instance_id` references a terminal instance

A `done` job's `instance_id` must point at an instance whose `status` is in
`{completed, error, failed, terminated}` (the four terminal `InstanceStatus` values). The instance
is the execution authority; `done` is the admission layer acknowledging that authority has reached
a final state and no retry is pending.

> `instance_id` is a **pointer**, not a 1:1 binding. Each retry overwrites it with the new
> attempt's id (plan §8.5). Historical attempts are not addressable from the job row — see the
> "Intentional limitation" note in plan §8.5.

### 1.4 `admission_state='dead'` ⇒ a `DeadLetterItem` row exists for this `job_id`

Dead-lettering is a *queue* outcome (retries exhausted or manual DLQ), not an execution outcome.
`DeadLetterItem` (`models.py:316-380`) holds a frozen autopsy snapshot
(`error_message` / `retry_count` / `failed_at`) captured at admission. The `dead` admission state
must always be paired with exactly one `DeadLetterItem` row — enforced in part by the
`idx_dead_letter_job_id` unique index (`models.py:324`).

### 1.5 Isolation assumption (unchanged by this plan)

The in-session **bus pending-children `COUNT`** + **terminal `UPDATE` gate** in
`_finalize_job_db_sync` relies on transaction atomicity. The correct operation of the gate must
**not** assume a stronger isolation level than what each database actually provides:

- **PostgreSQL (primary)**: baseline is **READ COMMITTED**. The gate's correctness is preserved
  because the `COUNT` and the terminal `UPDATE` share one transaction and the bus watcher state
  lives in a separate table updated under the same write. Do **not** design the §8.7 Postgres
  triggers to assume REPEATABLE READ or SERIALIZABLE — they run under READ COMMITTED.
- **SQLite (secondary)**: the whole-DB write lock serializes the gate. SQLite cannot host deferred
  cross-table triggers; the §8.7 bidirectional CI sweep is the only runtime enforcement path on
  SQLite, with disclosed detection lag.

> **Recorded so**: the Postgres constraint-trigger design (plan §8.7) does not assume stronger
> isolation than READ COMMITTED. A trigger that re-reads `job_locks` at COMMIT under READ COMMITTED
> sees the committed state of other concurrent transactions, which is sufficient for the
> "active ⇔ lock" check — the check does not need phantom protection.

### 1.6 Vestigial column: `waiting_for`

The `waiting_for` column on `JobItem` is **vestigial** under `USE_DEPENDENCY_BUS=ON` (the current
default). It is always `0` on the bus-active path; multi-agent completion is governed entirely by
the `DependencyBus` and `CorrelationManager`. Pre-bus tests that asserted on `waiting_for > 0` for
premature-completion detection are checking a column that no longer carries meaning — a known E2E
gap (ref: *E2E premature completion detection gap* 2026-06-22). Document it so the admission-state
tests do not perpetuate the assumption.

---

## 2. Current State Audit — COMPLETE inventory

All inventories below are verified against the live codebase (`daemon/`) as of 2026-06-28. They are
the authoritative baseline for Phases 1–7; where the plan's appendix (§6) gave a starting
inventory, this audit either confirms or corrects it.

### 2a. `JobItem` execution-state columns and their write sites

#### 2a.1 Column definitions (`daemon/repositories/job_queue/models.py`)

| Column | Line | Type | Default | Role |
|---|---|---|---|---|
| `status` | 173 | `str` | `JobStatus.PENDING.value` | The 7-value execution-status mirror (the column being collapsed). |
| `started_at` | 177 | `str \| None` | `None` | Execution start time (Instance owns this). |
| `completed_at` | 178 | `str \| None` | `None` | Execution completion time (Instance owns this). |
| `instance_id` | 181 | `str \| None` | `None` | Execution binding — **KEEP as pointer** (plan §2.3). |
| `error_message` | 182 | `str \| None` | `None` | Execution error (Instance/Task owns this). |
| `result_summary` | 183 | `str \| None` | `None` | Execution result (Instance/Task owns this). |
| `cancelled_at` | 192 | `str \| None` | `None` | Cancellation time (derivable from `Instance.status == TERMINATED`). |
| `failed_at` | 204 | `str \| None` | `None` | Failure transition time; DLQ carries its own snapshot. |

`JobStatus` enum (`models.py:21-37`) — 7 values:
`PENDING`, `PROCESSING`, `PAUSED`, `COMPLETED`, `FAILED`, `CANCELLED`, `DEAD_LETTER`.

#### 2a.2 Repository-layer canonical writers (`daemon/repositories/job_queue/repository.py`)

All execution-state column writes ultimately funnel through one of these primitives.

| Line | Method | Columns written | Type |
|---|---|---|---|
| 588–703 | `atomic_transition` | `status` + `**extra_updates` (any subset of `started_at`, `completed_at`, `cancelled_at`, `instance_id`, `result_summary`, `error_message`) | **canonical atomic primitive** — `WHERE status = :from` guard + version check |
| 705–815 | `atomic_retry` | `status='pending'`, `retry_count+1`, `next_retry_at`, `failed_at=None`, `error_message=None` | raw UPDATE (guarded) |
| 821–875 | `update` | any field via `setattr` — **rejects `status`** (ValueError at L855) | ORM setattr (non-status writes only) |
| 877–962 | `start_job` | `status='processing'`, `started_at`, `instance_id` | raw UPDATE (guarded) |
| 964–981 | `start_job_atomic` | `status='processing'`, `started_at`, `instance_id` | `atomic_transition` wrapper |
| 983–996 | `complete_job` | `status='completed'`, `completed_at`, `result_summary` | `atomic_transition` wrapper |
| 998–1011 | `fail_job` | `status='failed'`, `completed_at`, `error_message` | `atomic_transition` wrapper |
| 1013–1113 | `cancel_job` | `status='cancelled'`, `cancelled_at` | raw UPDATE (guarded) |
| 1115–1128 | `terminate_job` | `status='cancelled'`, `completed_at`, `error_message` | `atomic_transition` wrapper |

#### 2a.3 Service-layer direct writers (raw SQL or `atomic_transition`)

| File:Line | Method | Columns written | Dual-write? |
|---|---|---|---|
| `instance_lifecycle.py:1786–1805` | `_terminate_instance_db_sync` (cancel processing jobs) | `status='cancelled'`, `cancelled_at`, `completed_at`, `error_message`, `result_summary=NULL` | **Yes** — also DELETEs `job_locks`/`message_queue`/`task` in same WriteGuardSession |
| `instance_lifecycle.py:1824–1840` | `_terminate_instance_db_sync` (cancel non-processing) | `status='cancelled'`, `cancelled_at`, `error_message` (COALESCE) | **Yes** — same transaction |
| `instance_lifecycle.py:2150–2165` | `_pause_cascade_db_sync` UPDATE 2 | `status='paused'` | **Yes** — UPDATE 1 at L2117 writes `instances.status='paused'` |
| `instance_lifecycle.py:2421–2436` | `_resume_cascade_db_sync` UPDATE 2 | `status='processing'` | **Yes** — UPDATE 1 at L2389 writes `instances.status='running'` |
| `job_feedback_observer.py:2436–2483` | `_finalize_job_db_sync` Step 1 | `status`, `completed_at`, (`result_summary` OR `error_message`) | **Yes** — Step 2 instance.status at L2518; Step 3 DELETE `job_locks` at L2529 |
| `job_feedback_observer.py:1135–1141` | `_finalize_job` orphan-race re-arm | `status` (completed→processing) | No (single-write) |
| `job_feedback_observer.py:1454–1467` | `_finalize_job` W3 fail-safe | `status`, `completed_at`, `error_message` | No (failure path) |
| `job_recovery_service.py:159–164` | `recover_on_startup` (paused reconcile) | `status` (processing→paused) | No |
| `job_recovery_service.py:218–225` | `_fail_orphaned_job` | `status`, `completed_at`, `error_message` | **Yes** — `release_by_instance` in `finally` (L250–252) |
| `dead_letter_service.py:139–156` | `move_to_dlq` | `status='dead_letter'` | **Yes** — inserts `DeadLetterItem` (L141) |
| `dead_letter_service.py:232–247` | `move_to_dlq_standalone` | `status='dead_letter'` | **Yes** — DLQ insert + commit |
| `dead_letter_service.py:335–348` | `replay_from_dlq` | `status='pending'`, `retry_count=0`, `failed_at=None`, `error_message=None`, `started_at=None`, `completed_at=None`, `instance_id=None` | **Yes** — DELETE DLQ row (L361) |
| `manager.py:2090–2114` | `_migrate_cancel_inflight_message_jobitems` | `status='cancelled'`, `cancelled_at`, `error_message` | Bulk UPDATE — **runs unconditionally on every daemon boot** (called at `manager.py:590`) |

#### 2a.4 Service-layer routing wrappers (delegate, no direct write)

These call into the repository primitives above; they are listed so Phase 4's `_finalize_terminal`
migration grep is exhaustive.

| File:Line | Method | Underlying primitive |
|---|---|---|
| `job_queue_service.py:894` | `cancel_job` | `repository.cancel_job` |
| `job_queue_service.py:1094, 1122, 1137` | `_try_start_job` | `start_job_atomic` |
| `job_queue_service.py:1160` | `_complete_job` | `complete_job` |
| `job_queue_service.py:1196` | `_fail_job` | `fail_job` |
| `job_queue_service.py:1432` | `start_job` (clear stale `instance_id`) | **`repository.update(instance_id=None)`** — the only audited-column write via `update()` |
| `job_queue_service.py:1508` | `start_job` | `start_job_atomic` |
| `job_queue_service.py:1567, 1573, 1592` | `complete_job` | `complete_job` / `fail_job` / `terminate_job` |
| `job_queue_service.py:1644, 1652, 1673` | `complete_job_sync` | same trio |
| `job_queue_service.py:1806, 1837, 1853` | `trigger_next_job_sync` | `start_job_atomic` / `start_job` |
| `job_retry_engine.py:272` | `maybe_retry` (retry path) | `atomic_retry` |
| `job_retry_engine.py:310` | `maybe_retry` (DLQ path) | `dlq_service.move_to_dlq` |

#### 2a.5 Drift vs. the plan's §6.1 inventory

The plan's §6.1 inventories **`instance.status` write sites**, not `JobItem` column writes — so it
is the *companion* inventory, not a duplicate. All `instance.status` line references in §6.1 are
confirmed accurate against live code. JobItem-side gaps (none of which the plan's appendix
enumerates) that **Phase 4/5 must address**:

1. **`manager.py:_migrate_cancel_inflight_message_jobitems` (L2090–2114)** — bulk raw UPDATE on
   every boot. **Not** covered by the plan's `_finalize_terminal` boundary guarantee. Phase 4 must
   decide: route through `_finalize_terminal`, or treat as a startup-only maintenance write that
   bypasses admission state entirely.
2. **`instance_lifecycle.py:_terminate_instance_db_sync` (L1786–1805, L1824–1840)** — raw UPDATEs
   bypass `atomic_transition` by design (inside a WriteGuardSession that also DELETEs locks/queue/
   task). §8.1 of the plan discusses pause-cascade dual-write but never enumerates the
   terminate-cascade bulk-cancel.
3. **`job_feedback_observer.py:_finalize_job_db_sync` Step 1 (L2436–2483)** — §6.1 cites Step 2
   (instance.status at L2518) but not the Step 1 JobItem write.
4. **`job_recovery_service.py:_fail_orphaned_job`** — the plan's §8.8 references `:189` for
   recovery; the **actual write** is at **L218–225** (line drift in the plan).
5. **`dead_letter_service.py`** — three writers (`move_to_dlq`, `move_to_dlq_standalone`,
   `replay_from_dlq`). `replay_from_dlq` wipes 7 columns including `started_at`, `completed_at`,
   `failed_at`, `instance_id` — a large reset surface.

### 2b. Dual-write patterns

A dual-write is any single transaction that writes **both** `job_queue_items` and another table
(`instances`, `job_locks`, `dead_letter_items`) atomically. These are the migration's highest-risk
sites — the whole point of the refactor is to *eliminate* them.

#### 2b.1 `_finalize_job_db_sync` — the terminal cascade (`job_feedback_observer.py`)

The canonical terminal path. Three steps in one `WriteGuardSession`:

| Step | Line | Write | Target table |
|---|---|---|---|
| 0 / 0b | ~1902, ~2032 | Bus pending-children `COUNT` gate (dual bus check — early defense + in-session authoritative) | `dependency_bus` read |
| 1 | 2436–2483 | `status`, `completed_at`, `result_summary`/`error_message` | `job_queue_items` |
| 2 | 2518 | `instance.status = terminal_status` | `instances` |
| 3 | 2529–2533 | `DELETE FROM job_locks WHERE instance_id` | `job_locks` |

**InstanceStatus → JobStatus mapping** (`job_feedback_observer.py:2209–2212`):

```python
if terminal_status == InstanceStatus.COMPLETED.value:
    to_status = JobStatus.COMPLETED.value
elif terminal_status == InstanceStatus.ERROR.value:
    to_status = JobStatus.FAILED.value
```

This is the redundant mirror the refactor removes (plan §4). After Phase 4, Step 1 writes only
`admission_state`; the mapping table is deleted (DoD item 3).

**TOCTOU protection**: the dual bus gate (early defense outside WriteGuardSession +
authoritative COUNT inside) plus the `gate_deferred` / `instance_was_terminal` / `atomic_transition`
idempotency layers. The whole cascade runs via `asyncio.to_thread` to avoid event-loop deadlock.

#### 2b.2 `_pause_cascade_db_sync` / `_resume_cascade_db_sync` (`instance_lifecycle.py`)

Pause is the **highest-risk** dual-write (plan §8.1). Today pause writes instance→`PAUSED` **and**
job→`PAUSED` atomically so the JobProcessor dequeue and worker-pool claim gate agree. Resume is
symmetric.

| Operation | UPDATE 1 (instance) | UPDATE 2 (job) |
|---|---|---|
| Pause | `instance_lifecycle.py:2117–2136` — `instances.status='paused'` | `instance_lifecycle.py:2150–2165` — `job_queue_items.status='paused'` (WHERE `status='processing'`) |
| Resume | `instance_lifecycle.py:2389–2405` — `instances.status='running'` | `instance_lifecycle.py:2421–2436` — `job_queue_items.status='processing'` (WHERE `status='paused'`) |

**After Phase 4**: UPDATE 2 is deleted on both paths. Pause becomes instance-only; the job stays
`active` with its lock held. Correctness then depends entirely on instance-status checks at three
verified gates (plan §8.1):
- `_process_next_job` pre-check — `job_processor.py:634–646`
- `start_job` second-line defense — `job_queue_service.py:1435–1439`
- `claim_pending_task` SQL guard — `task/repository.py:552–577` (via `status_paused` bind param)

#### 2b.3 `_terminate_instance_db_sync` (`instance_lifecycle.py:1624–1827`)

Step 2 (L1744–1827) cancels associated jobs in the same transaction as the instance termination.
Two UPDATE statements (processing vs non-processing jobs) at L1786–1805 and L1824–1840. After
Phase 4, these set `admission_state='done'` (single value — no longer splits processing/pending).

#### 2b.4 `_fail_orphaned_job` (`job_recovery_service.py:218–225`)

Startup recovery path for `active` jobs whose instance is dead/missing. Writes `status`,
`completed_at`, `error_message` via `atomic_transition`, then releases the lock in a `finally`
block (L250–252). Dual-write because lock release is in the same logical operation.

> **CRITICAL PAUSED-INSTANCE NOTE** (plan Phase 3): `recover_on_startup` must **NOT** treat jobs
> whose instance is `PAUSED` as orphaned — a paused job stays `active` with its lock held. The
> paused-reconcile logic at `job_recovery_service.py:140–165` distinguishes PAUSED instances
> (leave alone — resume will handle them) from dead/missing instances (recover as orphaned).

### 2c. Instance model status system

#### 2c.1 `InstanceStatus` enum (`daemon/repositories/instance/models.py:20–35`)

10 values:

| Value | String | Classification |
|---|---|---|
| `IDLE` | `"idle"` | non-terminal (idle) |
| `RUNNING` | `"running"` | non-terminal (active) |
| `WAITING` | `"waiting"` | non-terminal (active, awaiting input) |
| `PAUSED` | `"paused"` | non-terminal (suspendable/resumable) |
| `QUEUED` | `"queued"` | non-terminal (idle with pending work) |
| `WAITING_CHILDREN` | `"waiting_children"` | non-terminal (parent awaiting children) |
| `COMPLETED` | `"completed"` | **terminal** |
| `ERROR` | `"error"` | **terminal** |
| `TERMINATED` | `"terminated"` | **terminal** |
| `FAILED` | `"failed"` | **terminal** (task-level failure) |

Only helper on the enum: `is_valid` (classmethod, L33–35). **No** `is_terminal`/`is_running`/
transition validators on the enum itself.

#### 2c.2 How `InstanceStatus` relates to `JobStatus`

The relationship is **derived, not stored**: terminal job status is *computed* from the instance at
finalize time via the §2b.1 mapping. The canonical reconciliation layer that consumers see is
`_STATUS_CANONICAL_MAP` in `work_status.py` (§2d.5 below).

| `InstanceStatus` | Maps to `JobStatus` via | Then to canonical via |
|---|---|---|
| `RUNNING` / `WAITING` / `QUEUED` / `IDLE` | (no direct job mapping — job stays `processing`) | `processing` → `processing` |
| `WAITING_CHILDREN` | (no direct job mapping — job stays `processing`) | `processing` → `processing` |
| `PAUSED` | `JobStatus.PAUSED` (via pause cascade UPDATE 2) | `paused` → `paused` |
| `COMPLETED` | `JobStatus.COMPLETED` (via `_finalize_job_db_sync:2209`) | `completed` → `completed` |
| `ERROR` | `JobStatus.FAILED` (via `_finalize_job_db_sync:2211`) | `failed` → `failed` |
| `TERMINATED` | `JobStatus.CANCELLED` (via terminate cascade) | `cancelled` → `cancelled` |
| `FAILED` | (no dedicated job mapping — surfaces as `failed`) | `failed` → `failed` |

After the refactor, this entire derivation chain collapses: terminal classification is read
directly off `Instance.status` via the `WorkRecord` join; the `JobStatus` column is gone.

### 2d. Dependencies and interaction points (read sites)

Phase 1 rewires every execution-state **read** off `JobItem` to resolve through
`Instance`/`WorkRecord`. This section is the complete inventory of current read sites.

#### 2d.1 `jobs_crud._job_to_response` (`daemon/routers/jobs_crud.py:46–79`)

**Bypasses the resolver entirely.** Every field comes straight from the `JobItem` ORM row — the
single biggest concentration of direct JobItem reads.

```python
job_id=job.job_id, status=job.status, priority=job.priority,
instance_id=job.instance_id, started_at=job.started_at,
completed_at=job.completed_at, result_summary=job.result_summary,
error_message=job.error_message, cancelled_at=job.cancelled_at,
```

Three call sites in the same file feed it: `create_job` (L180), `get_job` (L237), `list_jobs`
(L351). Adjacent status reads in this file: L169, L220, L230, L334, L344 (position/DLQ gates).

#### 2d.2 `jobs_management` terminal checks (`daemon/routers/jobs_management.py`)

| Line | Function | Read | Comparison |
|---|---|---|---|
| 85 | `delete_job` | `job.status` | `in TERMINAL_STATUSES` |
| 163 | `cancel_job_endpoint` | `job.status` | `in TERMINAL_STATUSES` (reject 400) |
| 238 | `restore_job_endpoint` | `job.status` | `in TERMINAL_STATUSES` (reject 400) |
| 304 | `retry_job` | `job.status` | `== JobStatus.DEAD_LETTER.value` |
| 349 | `retry_job` | `job.status` | `!= JobStatus.FAILED.value` |

`TERMINAL_STATUSES` (defined at `jobs_crud.py:33–38`, imported here) is the JobItem vocabulary
`{COMPLETED, FAILED, CANCELLED, DEAD_LETTER}`. None route through `WorkResolverService`.

#### 2d.3 `jobs_streaming._ResolvedWork` (`daemon/routers/jobs_streaming.py`)

Two branches, gated by `_use_resolver(request)` (reads `config.job_system.use_virtual_job_resolver`,
default `False`):

- **`from_job` (L52–62)** — legacy JobItem path. Reads `job_id, status, instance_id, queue_id,
  result_summary, error_message`.
- **`from_work_record` (L64–86)** — resolver-ON path. Reads `WorkRecord` fields. Field-name
  divergences: `record.error` ↔ `job.error_message`; `queue_id` forced to `None`.

`_resolve()` (L128–145) is the branch picker. **Default is the JobItem branch** in production today.

#### 2d.4 MCP `_job_item_to_work_record_shim` (`daemon/tools/job_queue.py:1139–1174`)

Sits inside the `else` branch of `use_virtual_job_resolver`. Two callers: `watch_job` (L976) and
`watch_jobs` (L1104). Reads 8 JobItem fields via `getattr` and projects them onto a `WorkRecord` so
the post-resolver terminal check (`is_terminal(record.status)`) stays uniform. `created_at=None` —
the shim does not parse the ISO string.

Also in this file: `job_continue` legacy kill-switch branch (L734, L737) reads
`old_job.status not in TERMINAL_STATES`.

#### 2d.5 `work_resolver._job_to_record` & `work_status._STATUS_CANONICAL_MAP`

**`_job_to_record`** (`daemon/services/work_resolver.py:768–795`) — the canonical resolver-side
read. Builds a `WorkRecord` from a `JobItem`:

```python
status=canonicalize_status(job.status),   # JobItem status passes through the map
instance_id=job.instance_id,
result_summary=job.result_summary,
error=job.error_message,
```

**`agent_id` and `project_id` come straight off the JobItem row** — unlike `_task_to_record`
(L725–766), this branch performs **no Instance join** because `job_queue_items` stores both columns
natively.

**`_STATUS_CANONICAL_MAP`** (`daemon/services/work_status.py:62–74`) — the reconciliation layer:

| Source value | Canonical value | Origin | Phase 4 fate |
|---|---|---|---|
| `"pending"` | `"pending"` | Task-side / shared | Keep (shared with Task) |
| `"running"` | `"processing"` | Task-side | Keep (Task-only source) |
| `"paused"` | `"paused"` | shared | Keep (shared with Task) |
| `"completed"` | `"completed"` | shared | Keep (shared with Task) |
| `"failed"` | `"failed"` | shared | Keep (shared with Task) |
| `"cancelled"` | `"cancelled"` | shared | Keep (shared with Task) |
| `"processing"` | `"processing"` | **JobItem-only** | **DELETE in Phase 4** |
| `"dead_letter"` | `"dead_letter"` | **JobItem-only** | **DELETE in Phase 4** (replaced by `admission_state='dead' → dead_letter`) |

Companion sets/helpers in the same file: `_TERMINAL_STATUSES` frozenset (L84–86) =
`{completed, failed, cancelled, dead_letter}`; `canonicalize_status` (L89);
`is_terminal` (L119).

**Status-drift warning** (`daemon/services/work_resolver.py:692–709`) — the codebase's own
admission that the mirror desyncs. Compares `r.status` (Task turn) against `job_status` (JobItem)
as **raw strings** in the dedup loop, logging a warning on mismatch. *Note: `running` vs
`processing` fires the warning even though both map to canonical `processing`.* This warning is
deleted outright in Phase 4 (DoD item 5) — there is no second status column to drift against.

#### 2d.6 `_STATUS_MAP` in `messages.py` (`daemon/routers/messages.py:57–64`)

Keyed on `TaskStatus.*.value` (not `InstanceStatus` or `JobStatus`). Already Task-driven —
**correct, no change needed** (plan Phase 1 explicitly keeps this).

#### 2d.7 `JobResponse` schema (`daemon/routers/schemas.py:53–100`)

Exposes 23 fields. 20 are pure JobItem projections; 3 (`dlq_reason`, `retry_count`,
`moved_to_dlq_at`) are joined from `DeadLetterItem`. Phase 1 keeps the legacy field names for API
compatibility but sources them from the resolver path (plan §11 rejects `JobResponseV2` — D14's
canonicalization already neutralizes semantic drift).

#### 2d.8 Additional read sites flagged (not in plan §6.2)

1. **`daemon/routers/dlq.py:497–498`** — DLQ replay response reads `job.job_id` and `job.status`
   directly. Phase 1 scope.
2. **`daemon/services/job_queue_service.py`** — 12 internal read sites (L358, 359, 464, 466, 639,
   642, 853, 860, 936, 1378, 1384, 1792). These are write-side reads (read-then-write under the
   same transaction), out of scope for the router migration but Phase 4 should confirm policy.
3. **`daemon/services/instance_lifecycle.py:935, 938`** — reads `remaining_job.status` against
   `{completed, cancelled, dead_letter}` / `processing` to decide cancel cascades. Natural adjacent
   change if the canonical `is_terminal()` helper is adopted uniformly.

---

## 3. Known dead-code `parent.status` write sites (6 sites)

There are exactly **6** `parent.status =` write sites in `daemon/`, all in unreachable `bus is None`
branches. They are **not** reachable in production (the `DependencyBus` singleton is initialized at
startup in `daemon/api.py:411` via `init_dependency_bus()`, and an A8/A9 hard `RuntimeError` fires
before any of these sites if the bus is ever `None`).

> **Note on `USE_DEPENDENCY_BUS`**: this string is **not a Python symbol** — it appears only once,
> as a comment inside `daemon/migrations/versions/20260621_000002_drop_legacy_completion_columns.sql:54`.
> The runtime guard is `bus is None` backed by the A8/A9 hard error.

These are documented here so a Phase 4 grep for `parent.status =` does not surprise anyone. They are
safe to remove in any phase; they do not affect the admission-state migration.

| # | File:Line | Status value written | Dead-code guard |
|---|---|---|---|
| 1 | `error_reporting.py:287` | `InstanceStatus.COMPLETED.value` | `else:` branch at L264 — preceded by A9 `RuntimeError` at L228–232 |
| 2 | `error_reporting.py:317` | `InstanceStatus.WAITING_CHILDREN.value` | same `else:` branch at L264 |
| 3 | `child_reports.py:842` | `InstanceStatus.COMPLETED.value` | dead code after early `return` at L825 — preceded by A8 `RuntimeError` at L791–796 |
| 4 | `child_reports.py:875` | `InstanceStatus.WAITING_CHILDREN.value` | same dead code after L825 return |
| 5 | `child_reports.py:1606` | `InstanceStatus.COMPLETED.value` | `else:` branch at L1591 — preceded by A8 `RuntimeError` at L1566–1572 |
| 6 | `child_reports.py:1628` | `InstanceStatus.WAITING_CHILDREN.value` | same `else:` branch at L1591 |

**Broader sweep result**: exactly 6 `parent.status =` writes exist in `daemon/`. No
`parent_instance.status =`, `parent_obj.status =`, or aliased `parent_status =` variants exist.
The 8 other `instance.status = InstanceStatus.X.value` writes (on the `instance` variable, not
`parent`) are separate concerns covered by plan §6.1.

---

## 4. References

- **Plan**: `.agents/shared/planning/job-as-queue-proxy/plan.md` (§2.1 invariants, §8.7 trigger
  design, §6 write/read inventory, Phase 0 deliverable at L201–211)
- **Completion authority**: `docs/architecture/completion-authority.md` (three-authority model)
- **D14 read facade**: `docs/plans/virtual-job-management-surface.md` (`WorkResolverService` /
  `WorkRecord`)
- **Status-drift warning deletion**: plan DoD item 5; code at `work_resolver.py:692–709`
- **DLQ snapshot integrity**: plan §8.6 (Phase 5 drop must run after Phase 4's `move_to_dlq`
  captures the snapshot)
