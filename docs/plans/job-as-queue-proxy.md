# Plan: Job as Queue Proxy — Collapse Execution State onto Instance

| Field | Value |
|---|---|
| **Status** | DRAFT — design; exploration complete, not started |
| **Mode** | Storage-collapse refactor. `JobItem` keeps only queue/admission concerns; all execution lifecycle (status/timing/result/error) moves to its existing authority — the **Instance** + **`DependencyBus`**. |
| **Builds on** | D11/D13 message decouple (done), **D14 Virtual Job Management Surface** (`docs/plans/virtual-job-management-surface.md` — the read facade `WorkResolverService` / `WorkRecord`), `docs/architecture/completion-authority.md` |
| **Unblocks** | Removal of `JobStatus`, `JobFeedbackObserver` status-write half, the status-drift warning, the `_job_item_to_work_record_shim`, and ~half of `_finalize_job_db_sync` |
| **Primary scope** | `daemon/repositories/job_queue/models.py`, `daemon/repositories/job_queue/repository.py`, `daemon/services/job_queue_service.py`, `daemon/services/job_feedback_observer.py`, `daemon/services/job_processor.py`, `daemon/services/job_state_machine.py`, `daemon/services/instance_lifecycle.py`, `daemon/services/job_retry_engine.py`, `daemon/services/dead_letter_service.py`, `daemon/services/work_resolver.py`, `daemon/services/work_status.py`, `daemon/tools/job_queue.py`, `daemon/routers/{jobs_crud,jobs_management,jobs_streaming,work,messages}.py`, `daemon/routers/schemas.py` |
| **Frontend scope** | `frontend/src/app/models/{job,work}.model.ts`, `frontend/src/app/pages/jobs/`, `frontend/src/app/services/{job,job-sse,work}.service.ts` |
| **Schema** | **PostgreSQL is the primary database; SQLite remains supported.** Dual-path migrations per the established convention (`daemon/migrations/versions/*.sql` for SQLite + runtime `ALTER … IF EXISTS` in `daemon/manager.py::_ensure_postgres_columns()` for adds / a new `_ensure_postgres_drop_*()` helper for drops; the migration runner is a NO-OP on Postgres, `runner.py:464-480`). See Phase 2 / Phase 5. |
| **Agent contract** | No tool rename, no notification format change. The `work_id` handle and canonical status vocabulary from D14 stay verbatim; only their *source* changes (Instance instead of JobItem). |
| **Definition of done** | §10 |

---

## 1. Problem

`JobItem` today carries **two unrelated kinds of state** in one table:

1. **Queue/admission state** — `priority`, `idempotency_key`, `queue_id`, the `JobLock` slot, `deleted_at`, `max_retries`, soft-delete. This is what a queue row *is*.
2. **Execution lifecycle state** — `status` (a 7-value enum), `started_at`, `completed_at`, `result_summary`, `error_message`, `instance_id` binding, `cancelled_at`, `failed_at`.

The execution state on `JobItem` is **a mirror, not an authority**. The real authority already exists and the codebase goes to extreme lengths to prove it:

- Terminal job status is **derived from `Instance.status`**, never decided independently. `_finalize_job_db_sync` (`job_feedback_observer.py:2436-2491`) maps `InstanceStatus.COMPLETED → JobStatus.COMPLETED` and `InstanceStatus.ERROR → JobStatus.FAILED` (`:2209-2212`). The job column is written *second*, from the instance value.
- Multi-agent completion is gated solely by the **`DependencyBus`**, with hard-error enforcement on a missing bus (A8/A9) and the TOCTOU-prone `SELECT COUNT(*)` fallback deliberately removed. See `docs/architecture/completion-authority.md`.
- The **status-drift warning** in `work_resolver.py:692-712` — the resolver *logs when the job column disagrees with the task/instance column* — is the codebase admitting the mirror can desync. That warning exists only because there are two columns where one would do.

The mirror causes real cost:

- Every terminal transition writes **two tables** (`job_queue_items` + `instances`) in one transaction (`_finalize_job_db_sync` Steps 1+2, `_pause_cascade_db_sync` UPDATE 1+2, `_terminate_instance_db_sync` Step 2). Dual-write = the entire H15 / C1 / C2 / W3 fix surface.
- `JobFeedbackObserver._finalize_job` is ~600 lines, half of which exists only to keep the job status column in lockstep with the instance (`_finalize_job_db_sync` Step 1, the `rearm_after_complete` `COMPLETED→PROCESSING` transition `:1135-1140`, the W3 fail-safe `:1454-1461`).
- Two status vocabularies (`processing` vs `running`; `dead_letter` job-only) that `work_status.py:_STATUS_CANONICAL_MAP` has to reconcile at every read.

### The insight (the "proxy" model)

Once a job is **dequeued**, it has done its job: it routes a unit of work to an instance and holds a concurrency slot. From that point the **Instance** owns the entire execution lifecycle — and it already does, by construction. The `JobItem` should become a **queue ticket / proxy**: it carries admission concerns only, and *delegates* all execution reads to the instance it pointed at.

> "Job-as-queue-proxy" = `JobItem.admission_state` has ~3 values (`queued` / `active` / terminal). Every rich execution status (`processing` / `completed` / `failed` / `paused` / `cancelled`) is read from `Instance.status`, which is already authoritative. `dead_letter` stays on the job (it is a *queue* outcome, not an execution outcome).

---

## 2. What is genuinely removable vs. what must stay

This is the crux. Not all of `JobStatus` is redundant. The research split is decisive.

### 2.1 DROP — execution state Instance already owns

| Column / concept | Line | Why redundant |
|---|---|---|
| `JobStatus` enum values: `processing`, `completed`, `failed`, `paused`, `cancelled` | `models.py:21-37` | Instance owns `RUNNING/COMPLETED/ERROR/PAUSED/TERMINATED/FAILED`. Job terminal status is *derived* at `_finalize_job_db_sync:2209-2212`. |
| `JobItem.started_at` / `completed_at` | `models.py:177-178` | Instance timing (`last_activity_at`, `created_at`, terminal transition time) already exists. |
| `JobItem.result_summary` | `models.py:183` | Lives on the terminal Instance / the driving Task (`task.result`). `WorkRecord.result_summary` already reads Task for turn-kind (`work_resolver.py:763`). |
| `JobItem.error_message` | `models.py:182` | Instance error / Task error. `WorkRecord.error` already reads both (`:764, :793`). |
| `JobItem.cancelled_at` | `models.py:192` | Derivable from `Instance.status == TERMINATED`. |
| `JobItem.failed_at` | `models.py:204` | Instance `ERROR`/`FAILED` transition time; DLQ carries its own snapshot (`DeadLetterItem.failed_at`). |
| Indexes `idx_job_queue_status`, `idx_job_queue_instance`, `idx_job_queue_items_project_status_deleted`, `idx_job_queue_items_status_type_instance` | `models.py:122-152` | Replaced by an `admission_state` index. |

### 2.2 KEEP — genuine queue/admission concerns (the proxy's job)

| Column / concept | Line | Why it stays |
|---|---|---|
| `job_id` (= `work_id`), routing (`agent_id`, `agent_dir`, `message`, `source`, `project_id`, `queue_id`, `job_type`, `job_metadata`) | `models.py:156-198` | Queue payload + routing. Instance carries a subset; the queue row is the durable ticket across retry attempts (each retry mints a *new* instance — `instance_id = uuid4()` in `start_job`). |
| `priority` + ordering (`list_pending_by_queue` `priority DESC, created_at ASC`) | `models.py:172` | Dequeue order. Instance has no concept of priority. |
| `idempotency_key` + `idx_job_idempotency` partial unique | `models.py:203, 146-152` | Enqueue dedup at the DB layer. |
| **`admission_state`** (new — replaces `status`) | — | See §3. Minimal 3–4 value state. |
| `retry_count` (current attempt counter) | `models.py:201` | **Must** live on the durable ticket: each retry mints a fresh instance, so no single instance can own the cumulative counter. |
| `max_retries` | `models.py:202` | Admission retry policy. |
| `next_retry_at` | `models.py:205` | Deferred re-admission scheduler key (`find_retryable_jobs`). |
| `deleted_at` + every `WHERE deleted_at IS NULL` filter + `soft_delete`/`restore` | `models.py:195` | Queue-membership lifecycle; the idempotency index depends on it. |
| `version` (`version_id_col`) | `models.py:219` | Optimistic-lock for ORM writes. |
| **Entire `JobLock` table + `uq_job_locks_slot`** | `models.py:269-313` | Cross-process concurrency primitive. *Lock presence is the real "active" marker* — the `status='processing'` column is partly redundant with it already. |
| **Entire `DeadLetterItem` table** | `models.py:316-380` | A DLQ is a *second-chance queue*. Its `error_message`/`retry_count`/`failed_at` are a frozen autopsy snapshot, not live execution state. |
| `JobQueue` table (100%) | `models.py:57-111` | Pure queue topology (`queue_type`, `concurrency_limit`, `is_paused`, `default_max_retries`). |

### 2.3 The `instance_id` column — KEEP as a pointer

`JobItem.instance_id` (`:181`) is technically "execution binding" but it is also **the proxy's delegation handle**: "the instance that currently owns this ticket." It must stay so a read can `JOIN instances` for status/timing/result. Each retry overwrites it with the new attempt's id. Classify: **KEEP as a pointer, DROP only the status/timing/result that used to sit beside it.**

---

## 3. The admission state machine (replaces `JobStatus`)

A minimal enum, **queue vocabulary only**:

| `AdmissionState` | Meaning | Replaces | Instance status while in this state |
|---|---|---|---|
| `queued` | In the queue, awaiting dequeue. May be a fresh ticket or a retried one (`retry_count > 0`). | `pending` | none / previous attempt terminal |
| `active` | Dequeued, lock held, instance spawned. Execution state lives entirely on the instance. | `processing` **and** `paused` | `running` / `waiting` / `waiting_children` / `paused` / `idle` / `queued` |
| `done` | Terminal. Instance reached a final state and no retry is pending. | `completed`, `failed` (when not retried), `cancelled` | `completed` / `error` / `failed` / `terminated` |
| `dead` | Dead-lettered (retries exhausted or manual DLQ). | `dead_letter` | (terminal; the autopsy is in `DeadLetterItem`) |

### 3.1 Transitions

```
                  enqueue
            ─────────────────────►  queued
                                      │
                          start_job   │  (acquire lock, spawn instance)
                          ─────────►  active
                                      │
              finalize(decision)      │
            ┌─────────────────────────┼──────────────────────┐
            ▼                         ▼                      ▼
          done                  queued (retry)              dead
       (success/cancel/         (retry_count+1,        (move_to_dlq:
        fail-no-retry)           next_retry_at set)     max_retries exhausted)
                                      │
                                      └─► active (re-dequeued)
```

### 3.2 Key design decisions encoded in this state machine

- **`paused` is gone from the job.** Pause is an *Instance* concern. A paused job stays `active` with its lock held; the instance is `PAUSED`. The admission layer already gates correctly off instance status — `claim_pending_task` skips paused instances (`task/repository.py:555,575`), and `_process_next_job` already checks `instance.status == PAUSED` before `start_job` (`job_processor.py:634-646`). The pause cascade stops writing `job_queue_items.status='paused'` (`instance_lifecycle.py:2138-2165`) and writes only the instance. **Risk item — see §8.1.**
- **`failed` is gone as a *resting* state.** The retry decision is made **synchronously at finalize** (it largely already is — `complete_job` FAILED branch calls `maybe_retry` inline, `job_queue_service.py:1579`). So finalize atomically does one of: `active → done` (no retry), `active → queued` (retry), or `active → dead` (DLQ). There is no window where a job sits `failed` with no living instance.
- **A single terminal-write boundary with a required decision.** Every finalize path funnels through one entry point — `_finalize_terminal(instance_id, decision: Decision)` — where `Decision` is a closed, non-defaulted enum: `NO_RETRY` / `RETRY` / `DEAD_LETTER`. The admission transition (`done` / `queued` / `dead`) and the `maybe_retry` call are computed *inside* this boundary; callers cannot finalize an instance without stating the decision. This converts the §8.2 audit from a checklist into a structural guarantee — a future finalize path that forgets retry fails at instantiation, not in production. All of `_finalize_job`, `complete_job`, `complete_job_sync`, `JobRecoveryService._fail_orphaned_job`, and `cancel_job`'s terminal branch route through it. (Review §2.2.)
- **Terminal classification (`completed` vs `failed` vs `cancelled`) moves to the read side**, read off `Instance.status` via the `WorkRecord` join. The job only knows `done`.

---

## 4. How completion works in the proxy model (write side)

The flow today already routes through the instance; this plan just *stops writing the redundant half*.

```
instance reaches terminal (COMPLETED / ERROR / TERMINATED)
   │
   ▼
JobFeedbackObserver._process_event  (job_feedback_observer.py:641)
   │  bus.count_pending_for_target(instance_id)  →  the ONLY completion gate
   │     > 0  → wait (no write)
   │     == 0 → _finalize_job
   ▼
_finalize_job_db_sync  (atomic, one WriteGuardSession, via _finalize_terminal)
   ├─ Step 0/0b: bus pending-children gate (in-session COUNT)   ← UNCHANGED
   ├─ Step 1 (NEW): UPDATE job_queue_items
   │                 SET admission_state = decision→(done | queued | dead)
   │                 WHERE job_id AND admission_state='active'
   │              (was: SET status=completed/failed — the redundant mirror)
   ├─ Step 2: UPDATE instances SET status=terminal  ← UNCHANGED (this is the authority)
   ├─ Step 3: DELETE job_locks WHERE instance_id    ← UNCHANGED
   └─ COMMIT
```

What disappears:
- The `InstanceStatus → JobStatus` mapping (`:2209-2212`) — no longer needed; the job no longer has a status to map *to*.
- The `rearm_after_complete` `COMPLETED → PROCESSING` transition (`:1135-1140`) becomes `done → active` on the admission column (same orphan-race logic, smaller vocabulary). The bus generation guard stays.
- The W3 fail-safe `PROCESSING → FAILED` (`:1454-1461`) becomes `active → done`.
- The status-drift warning (`work_resolver.py:692-712`) is deleted outright — there is no second status column to drift against.

---

## 5. Phased rollout

Strategy: **the read landing zone (`WorkRecord`) already exists from D14.** We extend it to be the *only* read path, then cut over writers, then drop columns. Every phase is independently shippable and behind the existing `use_virtual_job_resolver` flag where helpful.

### Phase 0 — Audit & decision (no code)

- Confirm §3 admission-state vocabulary with a second reviewer.
- Inventory every `count_active_jobs*` / `list_pending*` / defer-gate query that filters `status IN (...)` (§6.2) — these are the migration's load-bearing query sites.
- Decide: derive `active` from lock presence vs. denormalize `admission_state`. **Recommend denormalize** (cheaper reads, explicit); note lock-presence as the invariant `admission_state='active'` must satisfy.
- **Write `docs/architecture/job-as-queue-proxy-invariants.md`** stating the cross-table invariants the new model maintains (this is both the §2.1 enforcement spec and the Phase 9 test spec):
  - `admission_state='active'` ⇔ a `JobLock` row exists with `instance_id = JobItem.instance_id`.
  - `admission_state IN ('queued','active')` ⇔ `deleted_at IS NULL`.
  - `admission_state='done'` ⇒ `instance_id` references a terminal instance.
  - `admission_state='dead'` ⇒ a `DeadLetterItem` row exists for this `job_id`.
  - **Isolation assumption (unchanged by this plan):** the in-session `bus pending-children COUNT` + terminal UPDATE gate in `_finalize_job_db_sync` relies on transaction atomicity. On **PostgreSQL (primary)** the baseline is READ COMMITTED; the gate's correctness is preserved because the COUNT and UPDATE share one transaction and the bus watcher state lives in a separate table updated under the same write. On SQLite the whole-DB write lock serializes it. Record this so the Postgres trigger design (§8.7) does not assume a stronger isolation level than READ COMMITTED.

### Phase 1 — Read authority: route all job reads through Instance/WorkRecord

Goal: no consumer reads execution state off `JobItem` directly.

- `jobs_crud._job_to_response` / `JobResponse` schema (`routers/schemas.py:53-100`): **reuse `WorkResolverService` to build the response — do not write a second independent instance-join.** Keep returning the legacy field names for API compatibility, but source them from the same resolver path D14 already proves is byte-identical across Task/Job/Instance sources (`test_jobs_streaming_resolver.py` asserts "byte-identical wire format"). A parallel hand-rolled join is exactly the divergence bug a separate review flagged (§12 R2.3) — we avoid it by not reimplementing. No `JobResponseV2` / schema-version field: D14's canonicalization already neutralizes the semantic-drift risk the review worried about (instance + job terminal are written in *one* transaction today, so latency is identical; after the refactor there's one write, not two).
- `jobs_management` retry/cancel/restore terminal checks (`jobs_management.py:163,238,304,349`): switch from `job.status` to `WorkResolverService.get_work` (already instance-aware) or an instance-status join.
- `jobs_streaming` legacy `_ResolvedWork.from_job` (`:52-62`): delete; route everything through `from_work_record`. The endpoint already dual-paths on `use_virtual_job_resolver`.
- MCP `_job_item_to_work_record_shim` (`tools/job_queue.py:1139-1174`): delete; `job_get`/`job_list`/`watch_job` resolver-ON path already works.
- `work_resolver._job_to_record` (`:768-795`): read status/timing/result from the joined instance, not `job.status`. The `_STATUS_CANONICAL_MAP` JobItem-only entries (`processing`, `dead_letter`) reduce to mapping `admission_state` → canonical status.
- `messages.py:_STATUS_MAP` (`:275`): already Task-driven; keep, this is correct.

Exit criterion: every execution-state read of a job resolves through the instance/work layer. `job.status` is read only by the *internal* state-machine/repository code still being migrated.

### Phase 2 — Introduce `admission_state` (additive, dual-write)

- Migration: `ALTER TABLE job_queue_items ADD COLUMN admission_state TEXT NOT NULL DEFAULT 'queued'`; backfill from `status` (`pending→queued`, `processing→active`, `completed/failed/cancelled→done`, `dead_letter→dead`).
- **Dual-path, Postgres-primary** (per the repo convention):
  - **SQLite**: migration SQL file `daemon/migrations/versions/<date>_job_admission_state.sql` (auto-applied by the runner).
  - **PostgreSQL**: the runner is a NO-OP (`runner.py:464-480`); the `ADD COLUMN` + backfill + index run idempotently at startup in `daemon/manager.py::_ensure_postgres_columns()`. **Add the `admission_state` index with `CREATE INDEX IF NOT EXISTS … CONCURRENTLY`** (Postgres) to avoid an `ACCESS EXCLUSIVE` lock on a large `job_queue_items` table — note `CONCURRENTLY` cannot run inside a transaction, so it must be issued on a raw connection outside the `engine.begin()` block, not via SQLAlchemy ORM.
- Every existing status write site (§6.1) adds a *paired* `admission_state` write in the same UPDATE. No behavior change — both columns move together.
- Add `AdmissionState` enum in `models.py`; add `admission_state` index replacing the status indexes (keep old indexes for now).

Exit criterion: `admission_state` is correct and dual-written on both dialects; nothing reads it yet.

### Phase 3 — Cut over gating/count queries

The load-bearing internal queries that today filter on `status`:

- `list_pending_by_queue` / `list_pending_by_project` / `list_all_pending` (`repository.py:451-540`): `WHERE admission_state='queued'`.
- `count_active_jobs_by_project`, `count_active_jobs_in_non_defer_queues` (`repository.py:361-390`): `WHERE admission_state='active'`. *(Invariant: `admission_state='active'` ⇔ a `JobLock` row exists for this job — see §8.7.)*
- `find_processing_jobs`, `find_jobs_by_instance` (`:487-520`): `WHERE admission_state='active'`.
- `find_retryable_jobs` (`:1228-1258`): `WHERE admission_state='queued' AND next_retry_at <= now` (retried jobs are back in `queued`).
- Defer idle-gate (`job_processor.py:399-418`): counts non-defer `active` jobs.
- `JobRecoveryService.recover_on_startup` (`job_recovery_service.py:97-187`): scans `active` jobs with dead/missing/paused instances → `active → done`/`queued`.
- `_ACTIVE_JOB_IDS_SUBQUERY` for stale-lock sweep (`lock_repository.py:23-27`): `WHERE admission_state IN ('queued','active')`.

Exit criterion: all admission decisions use `admission_state`; `status` is write-only-mirror.

### Phase 4 — Flip writers to instance-authoritative

- **Introduce `_finalize_terminal(instance_id, decision)`** (§3.2) as the single terminal-write boundary with a required `Decision` enum. Route `_finalize_job`, `complete_job`, `complete_job_sync`, `JobRecoveryService._fail_orphaned_job`, and `cancel_job`'s terminal branch through it. `maybe_retry` is called *inside* it (§8.2 structural guarantee).
- `_finalize_job_db_sync` Step 1: write `admission_state` only (per §4); stop deriving/writing `status`.
- `_pause_cascade_db_sync` / resume cascade (`instance_lifecycle.py:2138-2165, 2407-2436`): **delete the `job_queue_items` status UPDATE**. Job stays `active`; only instance flips. (Verify §8.1.)
- `_terminate_instance_db_sync` Step 2 (`:1786-1840`): cancel cascade sets `admission_state='done'` (single value, no longer splits processing/pending).
- `JobRetryEngine.maybe_retry` (`job_retry_engine.py:173-336`): `active → queued` (retry) or `active → dead` (DLQ) — no intermediate `failed`.
- `DeadLetterService.replay_from_dlq` (`:335-348`): `dead → queued`.
- `cancel_job` (`job_queue_service.py:822-898`): `queued|active → done`.
- **`work_status._STATUS_CANONICAL_MAP`**: delete the entries that mapped `JobStatus.*` (`processing`, `paused`, `cancelled`, `failed`). Add `admission_state='dead' → dead_letter`. All other canonical statuses now resolve from `Instance.status`. *(A stale entry firing on a removed enum value would raise on every job read — must land in this phase, not Phase 5.)*

Exit criterion: `status` column is no longer written by any production path (only the dual-write shim from Phase 2, which we now remove).

### Phase 5 — Drop the redundant columns

- Migration: drop `status`, `started_at`, `completed_at`, `result_summary`, `error_message`, `cancelled_at`, `failed_at` from `job_queue_items`; drop the status indexes.
- **Dual-path, Postgres-primary, irreversible** — follow the established `20260621_000002_drop_legacy_completion_columns.sql` pattern exactly:
  - **SQLite**: migration SQL file marked `MANUAL: TRUE`, with the same irreversibility/observation-window warnings (apply only after Phase 4 has run clean in production for ≥2 weeks, post-backup, operator on-call). The `DOWN` section recreates empty columns — data is permanently lost.
  - **PostgreSQL**: a new `daemon/manager.py::_ensure_postgres_drop_admission_legacy()` helper (mirroring `_ensure_postgres_drop_legacy_columns()` at `manager.py:2022`) runs `ALTER TABLE job_queue_items DROP COLUMN IF EXISTS …` idempotently at startup. Drop the now-unused indexes first (`DROP INDEX IF EXISTS`) to avoid dependency errors; note `DROP COLUMN` takes a brief `ACCESS EXCLUSIVE` lock on Postgres.
  - **Dialect note**: `move_to_dlq` already snapshots `error_message`/`retry_count`/`failed_at` into `DeadLetterItem` (Phase 4 must complete first — §8.6), so no data is lost on the drop; the columns are write-only-dead by Phase 4.
- Delete `JobStatus` enum, `job_state_machine.py` (or reduce to a 4-transition admission machine), the drift warning, the shim, the dead `stream_status_change(job_status=...)` parameter.
- `WorkRecord` / `work_status.py`: confirm `_STATUS_CANONICAL_MAP` is fully consistent post-drop (Phase 4 already deleted the `JobStatus.*` entries; this phase verifies no residual references).

Exit criterion: `JobItem` is a pure queue ticket.

### Phase 6 — Frontend

- Jobs page already synthesizes `Job` from `Work` (`jobs.component.ts:230-238`) — make `Work` the only source.
- Either extend `Work` with the instance-sourced `started_at`/`completed_at`/`retry_count` for the detail drawer, or accept their loss on the job card (Instance detail view already shows them).
- `job.model.ts` `JobStatus` → derive from `Work.status` (canonical). `job-sse.service.ts` already speaks the canonical vocabulary.

### Phase 7 — Cleanup

Remove `use_virtual_job_resolver` flag (now always-on), legacy branches in `tools/job_queue.py`, dead SSE param, and the dual-write shim. Update tests (§9).

- **MCP schema-equivalence verification:** the legacy/resolver-OFF branches in `tools/job_queue.py` (`job_get`/`job_list`/`watch_job`/etc., ~100–200 lines of dual-path code with non-trivial result/error/retry-hint fields) must be shown to produce **byte-identical** result shapes to the resolver-ON path for a sample of inputs before deletion. External agents consume these tool results and may not survive a shape change. `test_jobs_streaming_resolver.py` already asserts this for the SSE endpoint; add an equivalent assertion for the MCP tool result layer.

---

## 6. Appendix: the write/read site inventory (from exploration)

### 6.1 Status write sites that become admission-state writes

Every site in `JobRepository` (`atomic_transition`, `start_job_atomic`, `complete_job`, `fail_job`, `cancel_job`, `terminate_job`, `atomic_retry`) and the inlined UPDATEs in `_finalize_job_db_sync` (Step 1), `_terminate_instance_db_sync` (Step 2), `_pause_cascade_db_sync` (UPDATE 2), resume cascade (UPDATE 2), `DeadLetterService.move_to_dlq`/`replay_from_dlq`, `JobRecoveryService._fail_orphaned_job`, and the D13 migration bulk cancel. Full map in exploration notes; all reduce to the §3 transitions.

### 6.2 Status read sites (consumers)

- **Routers** (`jobs_crud`, `jobs_management`, `jobs_streaming`): pure JobItem today, no Instance join — Phase 1 rewires these.
- **MCP tools** (`tools/job_queue.py`): already branch on `use_virtual_job_resolver`; resolver path is instance-aware. Phase 1 + Phase 7 finish this.
- **`work_resolver` / `work_status`**: self-sufficient on Task; the Job branch is the part being simplified.
- **SSE `status_change`**: already instance-status-driven; the `job_status` param is dead code (no caller passes it).
- **Frontend**: `Job`/`JobStatus` model is the contract; Phase 6 migrates to `Work`.
- **Tests**: large seed surface in `tests/unit/services/test_work_resolver.py`, `test_work_router.py`, `test_jobs_streaming_resolver.py`, `test_cascade_pause_resume.py`, `test_job_queue_tools.py` — see §9.

---

## 7. What this buys

- **One authority.** No dual-write, no drift warning, no `InstanceStatus→JobStatus` mapping table. Terminal state is read once, from the instance.
- **`_finalize_job_db_sync` roughly halves.** Step 1 becomes a one-line `admission_state` write; the mapping logic and the `processing/completed/failed` branchery go away.
- **Smaller vocabulary, fewer races.** A 4-value admission enum vs. a 7-value execution enum. `paused` and `failed` (as resting states) disappear, eliminating the pause-cascade dual-write and the failed-with-no-instance window.
- **D14 pays off.** The virtual-job surface was built for exactly this — a read facade that already hides whether state lives on Task or Job. This plan collapses the Job side onto Instance, and the facade becomes thin.
- **Clearer mental model** for future contributors: *Job = queue ticket, Instance = execution, Task = turn.*

---

## 8. Risks & open questions

### 8.1 Pause semantics (highest-risk item)

Today pause is **dual-written atomically**: instance→`PAUSED` **and** job→`PAUSED` in one transaction (`instance_lifecycle.py:2138-2165`), specifically so the JobProcessor's dequeue and the worker-pool's claim gate agree. Dropping the job-side write means pause correctness depends entirely on **instance** status being checked at every gate. Verified gates:

- `_process_next_job` pre-check skips `instance.status == PAUSED` before `start_job` (`job_processor.py:634-646`). ✅
- `start_job` second-line defense rechecks `instance.status == PAUSED` (`job_queue_service.py:1435-1439`). ✅
- `claim_pending_task` SQL guards on instance status (`task/repository.py:555,575`). ✅

**Mitigation:** in Phase 4, add an explicit integration test (`test_pause_resume_root`-style) that pauses a project while jobs are `active` and asserts (a) no new work is claimed, (b) resume re-enables claiming, (c) the job stays `active` throughout (lock held). Only proceed to Phase 5 after green.

**Open question:** does anything enumerate "paused jobs" for UI that currently relies on `job.status='paused'`? If yes, Phase 1 must add an instance-join for that view before the column drops.

### 8.2 Retry-without-instance window

Phase 4 makes the retry decision **synchronous at finalize** (`active → queued` or `active → dead` in the same transaction as the instance terminal write). Risk: any code path that finalizes the instance *without* consulting the retry engine would leave a job stranded. **Mitigation is structural, not an audit:** the single `_finalize_terminal(instance_id, decision)` boundary (§3.2) takes a required `Decision` enum and calls `maybe_retry` internally — a new finalize path cannot be written without stating the decision, so it cannot silently skip retry. All existing callers (`_finalize_job`, `complete_job`, `complete_job_sync`, `_fail_orphaned_job`, `cancel_job`) route through it. The audit becomes "confirm every caller was migrated," which is a grep, not a judgment call.

### 8.3 Root own-queue gate (unaffected, noted for completeness)

Root completion requires *both* no pending children (bus) **and** no own-queue `MessageQueue` rows (`child_reports.py:1319-1329`), because external sources (HTTP/scheduler) aren't bus-tracked. This is **not** a job-status concern and is unchanged by this plan — but it is the reason "Instance+Bus is the completion authority" needs the asterisk "plus MessageQueue for roots." Document it; don't try to fold it into admission state.

### 8.4 `rearm_after_complete` orphan race

Stays, but moves from `COMPLETED → PROCESSING` to `done → active` on the admission column. The bus generation-counter guard (`job_feedback_observer.py:1103-1172`) is unchanged in structure. No new risk; smaller vocabulary.

### 8.5 `instance_id` as a 1:N pointer across retries

A job retried N times points to N terminal instances over its life; only the *current* attempt's `instance_id` is on the row. Reading "this job's history" requires joining by `(project_id, agent_id, message)` or a future attempts table — out of scope here, but flag it: today's single `instance_id` already has this limitation, so this plan makes it no worse.

**Intentional limitation (documented):** successful-retry history is **not retained**. Only the current attempt's instance is addressable from the job row; failed attempts are addressable via `DeadLetterItem` (the DLQ autopsy); successful intermediate attempts leave no trace in any audit-friendly table. This is a pre-existing limitation (today's single `instance_id` has it too). If/when a job-history view is needed, it is a separate plan and a separate table — not bolted onto this refactor.

### 8.6 DLQ snapshot integrity

`DeadLetterItem` keeps its own `error_message`/`retry_count`/`failed_at` (frozen at admission). After dropping those columns from `JobItem`, ensure `move_to_dlq` snapshots them *before* the job row loses them — i.e., Phase 5 drop must run after Phase 4's `move_to_dlq` captures the snapshot. Sequencing in the phases already enforces this.

### 8.7 The `active ⇔ lock-held` invariant (concurrency correctness)

`admission_state='active'` ⇔ a `JobLock` row exists for the job. This is the concurrency-correctness invariant of the whole system: if `active` without a lock, the worker pool can double-dispatch; if a lock without `active`, the defer-gate and `count_active_jobs*` miscount.

**Postgres-primary changes the enforcement menu.** A separate review (§12 R2.1) asked for a per-write `_assert_active_invariant` helper. That is self-defeating: it runs *inside the same code path that already does the acquire/release*, so it cannot catch the actual failure mode — a *missing* release path. But because Postgres is now the primary database, a **DB-enforced** mechanism is available that the helper never was:

- **PostgreSQL (primary) — deferred CONSTRAINT TRIGGER.** A `CREATE CONSTRAINT TRIGGER … DEFERRABLE INITIALLY DEFERRED` on `job_queue_items` that, at commit, raises if `admission_state='active'` has no matching `job_locks` row (and a symmetric trigger on `job_locks` for the other direction). This is **DB-enforced at commit**, independent of which application code path ran — so it *does* catch a missing release path the application helper cannot. Deferred (not immediate) so the `start_job` acquire-then-set-active ordering inside one transaction doesn't false-fire. This is the recommended primary enforcement on Postgres.
- **SQLite (secondary) — bidirectional CI sweep.** SQLite lacks deferred cross-table triggers; fall back to the bidirectional sweep (both directions, since single-direction misses the silent "lock exists but job isn't active" case):
  - daily: sample random `active` jobs → assert each has a `JobLock` row;
  - nightly: sample random `JobLock` rows → assert each has an `active` job.
- **Cheap hot-path assertion (both dialects):** in `start_job_atomic`, after lock acquisition and before commit, assert the row is (or is being set to) `active`. This is the single point where the invariant is *established*.

Net: Postgres gets commit-time DB enforcement (strongest), SQLite gets the sweep, both get the hot-path check. The bidirectional sweep stays as defense-in-depth on Postgres too. This resolves the review's §2.1/§3.2 tension: the rejection of the *application* helper stands, but the Postgres trigger is adopted as the superior replacement that only became viable with Postgres-primary.

---

## 9. Test impact

Large but mechanical. Major files:

- `tests/unit/services/test_work_resolver.py` — reseed from Instance/Task instead of `JobStatus` values; drop drift-warning assertions.
- `tests/unit/routers/test_work_router.py`, `test_jobs_streaming_resolver.py` — reseed.
- `tests/unit/test_cascade_pause_resume.py`, `test_pause_resume_root.py` — assert on **instance** pause status, not job; add the §8.1 integration test.
- `tests/test_job_queue_tools.py` — drop the resolver-OFF / shim branches after Phase 7.
- `tests/message_queue_redesign/test_task_repository.py` — assert `admission_state` transitions.
- `tests/test_finalize_job_h15.py` — Step 1 now writes `admission_state`; update the atomic-commit assertions.
- New: `tests/unit/test_admission_state_machine.py` — the 4-value transition table + the "`active` ⇔ lock held" invariant.

---

## 10. Definition of done

1. `JobItem` has no `status`, `started_at`, `completed_at`, `result_summary`, `error_message`, `cancelled_at`, or `failed_at` column.
2. `JobStatus` enum and `job_state_machine.py` (rich version) are deleted; replaced by a 4-value `AdmissionState` and its transition table.
3. `_finalize_job_db_sync` Step 1 writes only `admission_state`; the `InstanceStatus→JobStatus` mapping is gone.
4. The pause cascade writes only the instance; no job-status pause write remains. §8.1 integration test is green.
5. The status-drift warning, `_job_item_to_work_record_shim`, and the dead `stream_status_change(job_status=...)` parameter are deleted.
6. Every job execution-state read resolves through `Instance`/`WorkRecord`; `use_virtual_job_resolver` flag is removed (always-on). `JobResponse` is built by `WorkResolver`, not a hand-rolled join.
7. `count_active_jobs*`, `list_pending*`, defer-gate, recovery, and stale-lock-sweep queries filter on `admission_state` (and lock presence).
8. The `active ⇔ lock-held` invariant is enforced: on **PostgreSQL** via a deferred CONSTRAINT TRIGGER at commit, on **SQLite** via a bidirectional CI sweep, plus a hot-path assertion in `start_job_atomic` on both (§8.7).
9. Frontend Jobs page renders from `Work` exclusively; `JobStatus` derives from canonical status.
10. All existing tests green after reseed; new admission-state tests green.
11. Every terminal write routes through `_finalize_terminal(instance_id, decision)` with a required `Decision`; no finalize path bypasses it (grep-verifiable).
12. `docs/architecture/job-as-queue-proxy-invariants.md` exists (Phase 0) and the invariants it lists are enforced/checked per §8.7.
13. Phase 7 MCP tool result shapes are byte-identical to the legacy path for the sample inputs.

---

## 11. Out of scope

- Unifying the **Task** layer with Instance (Task = turn; orthogonal).
- Adding a job-attempts/history table (§8.5) — including retention of successful intermediate retry attempts.
- Defer-queue on the message/task layer (separate plan, noted in D14).
- Changing the `work_id` handle or the agent tool contract.
- A versioned `JobResponseV2` / schema-version field — explicitly rejected (§12 R2.3): D14's byte-identical canonicalization already neutralizes the semantic-drift risk.

---

## 12. Review disposition (2026-06-28)

A strategic review (`job-as-queue-proxy.review.md`) approved the destination and phasing with five "required" edits. Disposition, recorded here so the reasoning is auditable:

| Review item | Disposition | Where |
|---|---|---|
| R2.1 enforce `active ⇔ lock-held` at write boundary | **Redirected + upgraded** — the application per-write helper is rejected as self-defeating; instead **PostgreSQL commit-time deferred CONSTRAINT TRIGGER** (newly viable under Postgres-primary) is the primary enforcement, with the SQLite bidirectional CI sweep as the secondary-dialect fallback and a hot-path assertion on both | §8.7, DoD 8 |
| R2.2 single `_finalize_terminal` with required `Decision` | **Adopted fully** — structural guarantee over an audit | §3.2, Phase 4, DoD 11 |
| R2.3 `JobResponseV2` / frontend in Phase 2 | **Rejected** — over-engineering; D14 already guarantees byte-identical output. Adopted only the kernel: reuse `WorkResolver`, don't hand-roll a join | Phase 1, DoD 6, §11 |
| R2.4 assign `_STATUS_CANONICAL_MAP` cleanup to a phase | **Adopted** — explicit Phase 4 bullet | Phase 4 |
| R2.5 document successful-retry history loss | **Adopted** | §8.5, §11 |
| R3.1 written invariants doc | **Adopted** | Phase 0, DoD 12 |
| R3.2 bidirectional CI sweep | **Adopted** (folded into §8.7) | §8.7 |
| R3.3 MCP schema-equivalence verification in Phase 7 | **Adopted** | Phase 7, DoD 13 |
