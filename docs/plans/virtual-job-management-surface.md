# Plan: Virtual Job — Unified Work Management Surface (the "D14" half of the decouple migration)

| Field | Value |
|---|---|
| **Status** | DRAFT — design agreed; not started |
| **Mode** | Facade migration. Preserves the orchestrator-facing tool interface; changes only the storage backing. |
| **Depends on** | D11 + D13 complete (confirmed done in `LESSONS/architecture-migration-status-2026-06-26.md`). This plan is the missing management half of that migration. |
| **Unblocks** | Defer queue on the message/task layer (separate follow-up plan). |
| **Scope (primary)** | `daemon/repositories/job_queue/watcher_models.py`, `daemon/repositories/job_queue/watcher_repository.py`, `daemon/repositories/task/models.py`, `daemon/repositories/task/repository.py`, `daemon/services/job_queue_service.py`, `daemon/services/job_feedback_observer.py`, `daemon/services/worker_pool.py`, `daemon/services/stale_task_recovery.py`, `daemon/services/instance_messaging.py`, `daemon/tools/job_queue.py`, `daemon/routers/messages.py` |
| **Scope (P4 — frontend)** | `frontend/src/app/pages/jobs/`, `frontend/src/app/components/job-card/`, `frontend/src/app/models/job.model.ts`, `frontend/src/app/services/job.service.ts` / new `work.service.ts` |
| **Scope (schema)** | New migration `20260627_000001_virtual_job_work_id.sql` + `_ensure_postgres_columns()` parity entry |
| **Agent contract** | No tool renames, no notification format change. `agents/_prompt_system/innate-skills/job-orchestration/skill.md` stays valid verbatim. |
| **Definition of done** | §9 below |

---

## 1. Problem

After D13, a message (and any message-equivalent work that drives `graph.astream` on an existing instance) creates a **`task`** row, not a `JobItem`. That is structurally correct — it eliminated the `06f500af`-class dual-record bug.

But the orchestrator agent manages work **exclusively** through the `job_*` tool suite (`daemon/tools/job_queue.py`), and every tool there resolves a handle against `job_queue_items.job_id`:

- `job_get` (`job_queue.py:307`), `job_list` (`:320`), `job_cancel` (`:352`), `job_retry` (`:365`), `watch_job` (`:631`), `watch_jobs` (`:731`) — all `JobItem` lookups.
- The watcher table `job_watchers.job_id` is FK-constrained to `job_queue_items.job_id` (`watcher_models.py:38`).
- `notify_watchers(job_id, ...)` joins `JobItem` for `agent_id` / `result_summary` (`job_queue_service.py:221,245,251`).

The result is a **broken handle**. `InstanceMessagingService.enqueue_message` returns `job_id = str(task.id)` (`instance_messaging.py:994`) purely as an "adapter," and `job_continue` passes it back as `new_job_id` (`job_queue.py:504`). But `task.id` is an integer in the `task` table; the orchestrator's `watch_job(new_job_id)` / `job_get(new_job_id)` then returns "Job not found." **The orchestrator created work it cannot observe, cancel, or watch.**

This is the gap that motivated "make the message API create a job." The right fix is not to push messages back into jobs (that reopens D13's bug class) — it is to **push the management surface down onto tasks** behind a facade that keeps the existing interface.

---

## 2. Design: a virtual job (read facade, not a mirror table)

A **`work_id`** resolves to *either* a `job_queue_items` row *or* a `task` row at read time. The virtual job is a **read facade**: status is always read from the live source row, never copied. There is no second record, so the dual-record divergence that caused `06f500af` is structurally impossible.

```
orchestrator → job_get(W) / watch_job(W) / job_cancel(W)
                    │
                    ▼
            resolve_work(W)
              ├─ task_repo.get_by_work_id(W)   → hit? → normalize Task
              └─ job_repo.get(W)               → else → normalize JobItem
```

### 2.1 `work_id` minting (chosen: option 1 — UUID on the task row)

| Option | Mechanism | Verdict |
|---|---|---|
| **(1) Mint a `work_id` UUID on the `task` row** | `ALTER TABLE task ADD work_id`; set at enqueue | **CHOSEN** — shape-identical to `job_id` (UUID), no namespace collision, resolvable both directions |
| (2) Prefixed derived id `task:{id}` | parse prefix | Leaks kind into id; resolver must branch on prefix |
| (3) Reuse `message_id` | tasks already carry it | Not 1:1 (retry chains: `get_retry_chain`); loses the "one work record per turn" handle |

`task.work_id` is minted in `_prepare_enqueued_message` in the same transaction that writes `message_queue` + `task`. It then flows out as `AsyncMessageResult.job_id` — **the current lie becomes true.**

### 2.2 Status canonicalization

One virtual status enum, expressed in job vocabulary (the existing tool/notification contract):

| Canonical | From `JobItem.status` | From `Task.status` |
|---|---|---|
| `pending` | `pending` | `pending` |
| `processing` | `processing` | `running` |
| `paused` | — (n/a) | `paused` |
| `completed` | `completed` | `completed` |
| `failed` | `failed` | `failed` |
| `cancelled` | `cancelled` | `cancelled` |
| `dead_letter` | `dead_letter` | — (tasks fail, no DLQ) |

The mapping already exists half-formed in `daemon/routers/messages.py:57` (`_STATUS_MAP`). Promote it to a shared util (e.g. `daemon/services/work_status.py`) used by the resolver, the HTTP route, and `notify_watchers`.

---

## 3. Concrete change list (layer by layer)

### 3.1 Schema & models

- **`task` table** (`daemon/repositories/task/models.py`): add
  `work_id: str = Field(default_factory=lambda: str(uuid.uuid4()), index=True, unique=True)`.
  Update `to_dict()` to include it.
- **`job_watchers` table** (`daemon/repositories/job_queue/watcher_models.py`):
  - Rename column concept `job_id` → `work_id` (keep the DB column name `job_id` to avoid a wide rename churn, OR rename — see §8 decision). **Recommended: keep column name `job_id`, treat it as `work_id` semantically.**
  - **Drop the FK** `foreign_key="job_queue_items.job_id"` (line 38) — it now references either table. Replace with a plain indexed string column. Keep `UniqueConstraint` (rename `uq_job_watchers_job_instance` → keep as-is or alias) and both indexes.
- **Migration** `daemon/migrations/versions/20260627_000001_virtual_job_work_id.sql`:
  - `ALTER TABLE task ADD COLUMN work_id TEXT;` + `CREATE UNIQUE INDEX idx_task_work_id ON task(work_id);`
  - Backfill existing `task` rows: `UPDATE task SET work_id = lower(hex(randomblob(16))) WHERE work_id IS NULL;` (SQLite) / `gen_random_uuid()` (PG).
  - Make `work_id NOT NULL` after backfill.
  - Recreate `job_watchers` constraints without the FK (SQLite cannot drop a column FK in place → rebuild table; PG: `ALTER TABLE job_watchers DROP CONSTRAINT ...`). See §8 for the SQLite rebuild note.
- **Postgres parity** (`daemon/manager.py:_ensure_postgres_columns`, line 1653): add the `ALTER TABLE task ADD COLUMN IF NOT EXISTS work_id TEXT` + `CREATE UNIQUE INDEX IF NOT EXISTS idx_task_work_id ...` statements. Migration runner is SQLite-only (`runner.py:480`), so PG gets schema evolution here.

### 3.2 Task repository (`daemon/repositories/task/repository.py`)

- Add `get_by_work_id(work_id: str) -> Task | None` (mirror of `get_by_message` at `:100`).
- The read/cancel primitives already exist — no new ones needed:
  `get` (`:71`), `cancel_task` (`:1272`), `complete_task` (`:704`), `fail_task` (`:759`), `find_running_by_instance` (`:113`).

### 3.3 Resolver service (new, thin)

- New `daemon/services/work_resolver.py` exposing `resolve_work(work_id) -> WorkRecord | None` and `list_work(...)`.
- `WorkRecord` is a normalized dataclass: `work_id, kind ("job"|"task"), status (canonical), instance_id, project_id, agent_id, result_summary, error, created_at`.
  - For a `task`: `agent_id` resolved via the instance row (task has no `agent_id`); `result_summary` parsed from `task.result` JSON (same logic as `messages.py:251-263`); `project_id` via the instance.
  - For a `JobItem`: direct field mapping.
- `list_work(...)` is a UNION read (status/instance/project filters) so `job_list` finally returns **all** work, not just job-spawned.

### 3.4 Watcher repository (`daemon/repositories/job_queue/watcher_repository.py`)

- Internally the column is `work_id`; keep method names (`add_watch`, `get_watchers_for_job`, `remove_all_watches_for_job`, etc.) but treat their `job_id` param as `work_id`. No public-API rename (minimizes call-site churn).
- `get_watched_processing_job_ids` (`:217`) currently JOINs `JobItem` (`:256`) to filter PROCESSING. After the facade, this becomes "watched work that is still in-flight" — rewrite the JOIN to resolve through the work resolver (or UNION task running + job processing). This method feeds restart-rebuild; verify semantics in §6 tests.

### 3.5 Notification (`daemon/services/job_queue_service.py:notify_watchers`, line 194)

- Generalize: resolve `work_id` via the resolver instead of `self._repository.get(job_id)` (`:221`). Build the notification from `WorkRecord` (`agent_id`, `result_summary`, `error`) instead of `job.*`.
- **Notification format is unchanged** (`[JOB_EVENT] Job {work_id}... {status}`, `Agent:`, `Result:`/`Error:`) — the orchestrator's parsing contract (`job-orchestration/skill.md` §Notification Format) stays valid.
- `notify_watchers` becomes kind-agnostic: same function serves job-terminal and task-terminal.

### 3.6 Terminal-emit wiring (the watch half for tasks)

Fire `notify_watchers(work_id, canonical_status, error)` from the **task** terminal sites:
- `worker_pool.py:565` (`complete_task` → `completed`)
- `worker_pool.py:613` (`fail_task` → `failed`)
- `worker_pool.py:631` (`cancel_task` → `cancelled`)
- `stale_task_recovery.py:251/307/396/456` (`fail_task` → `failed`)

Job-terminal wiring already exists at `job_feedback_observer.py:879,1277,1287` — those keep firing `notify_watchers(work_id, ...)` unchanged (the work_id *is* the job_id for job-spawned work).

To avoid double-notify: a task terminal that is *also* tracked by an observer finalization (rare post-D13; messages no longer have a JobItem) must fire exactly once. The guard is: `notify_watchers` already removes watches on terminal states (`job_queue_service.py:268-274`), so the second call finds zero watchers and no-ops. Document this as the dedup mechanism.

### 3.7 Tools (`daemon/tools/job_queue.py`) — interface preserved

- `job_get(W)` → resolver → `WorkRecord.to_dict()`. Works for jobs and tasks.
- `job_list(...)` → `list_work(...)` (UNION). Orchestrator now sees in-flight message/report tasks too.
- `job_cancel(W)` → resolver routes to `cancel_job` (job) or `cancel_task` (task). Note: task cancel is cooperative (`cancel_requested`); docstring reflects "cancel requested" vs "instantly gone."
- `watch_job(W)` / `watch_jobs([W])` → register on `work_id` (kind-agnostic). Works for both.
- `job_continue` → returns `result.job_id` which is now the **real** `task.work_id` (the lie is fixed at the source, `instance_messaging.py:994`, no change needed here).
- `job_retry` / `dlq_replay` → resolver returns "not applicable for task-type work"; stay job-only. No regression (tasks already have their own retry path via `next_retry_at`).

### 3.8 HTTP API (`daemon/routers/messages.py`)

- `send_message` already returns `message_id` (stable). The `job_id` field on `AsyncMessageResult` now carries `task.work_id` (real). No contract change.
- `get_message_status` (`:195`) already queries the `task` table post-D13. Unchanged.

### 3.9 Agent docs

- `agents/_prompt_system/innate-skills/job-orchestration/skill.md` — **no change required**. The notification format, parsing rules, and terminal-state table all hold for virtual jobs. Optional: add one line noting that `job_continue`/`watch_job` handles also work over continued-instance work.
- No agent `soul.md`/`tools.md` edits (the whole point of the facade).

### 3.10 Frontend — virtual queue UI (P4)

The "virtual queue" is the human-visible layer of the same facade. The backend `list_work` UNION (§3.3) already emits normalized `WorkRecord`s; the frontend becomes a thin consumer. **Scoped deliverable: Option A (flat unified board). Options B and the defer-lane are follow-ups.**

**Backend (small):**
- Expose `GET /work` (or widen `GET /jobs` to return the UNION from `list_work`). One endpoint returning normalized `WorkRecord` rows with filters (`status`, `project_id`, `instance_id`, `kind`).
- `WorkRecord` JSON shape maps 1:1 onto the existing frontend `Job` interface (`frontend/src/app/models/job.model.ts:7`) — add a `kind: 'job' | 'turn' | 'report'` field and reuse everything else.

**Frontend (small):**
- `JobCardComponent` (`components/job-card/job-card.component.html`) already renders `agent_id`, `status`, `message`, `instance_id`, `source`, timestamps, cancel/retry. `WorkRecord` populates it with zero structural change.
- Add a `kind` chip next to the status chip: `job` (real queued job), `turn` (message turn on an instance), `report` (child-completion report).
- Jobs page (`pages/jobs/jobs.component.ts`) gains a unified list mode backed by the `/work` endpoint; the queue sidebar (`QueueListComponent`) still filters real queues. Task-backed work shows no queue badge (it has no `queue_id`) — surfaced only via the `kind` chip.
- SSE: reuse `job-sse.service` against `work_id` so task status flips (running→processing, paused, terminal) stream live into the board.

**Why not fake task "queues":** tasks have no queue membership and no FIFO/concurrency semantics — `claim_pending_task` claims globally with only a "1 RUNNING task per instance" guard. Pretending they sit in a FIFO lane (synthetic `job_queues` rows) would advertise queueing behavior that doesn't exist. The defer lane (§7) is the one place a task-type virtual queue will earn real sidebar placement, because it actually gates admission.

**Follow-ups (out of P4 scope):**
- **Option B — per-instance grouping:** group task work under a synthetic lane per running instance ("Instance abc123 → 1 running turn"). The most honest visualization for conversation turns; medium effort (instance-aware grouping + new sidebar section).
- **Defer-lane-as-real-queue:** when the defer queue (§7) lands, its tasks carry a lane with real idle-gating semantics → that lane is a genuine virtual queue that belongs in `QueueListComponent`.

---

## 4. Phasing (one branch, 4 PRs)

```
P1 — Schema + models + resolver (no behavior change yet)
   task.work_id column, migration, PG parity, WorkRecord, resolve_work/list_work.
   enqueue_message mints work_id; AsyncMessageResult.job_id = work_id (truthy).
   All tools still resolve job-only; tasks are resolvable but nothing routes to them yet.
   │
P2 — Watcher rewire + task-terminal notify (the observability half)
   job_watchers FK dropped; notify_watchers goes through the resolver;
   task terminal sites fire notify_work_watchers(work_id, ...).
   watch_job/job_get/job_cancel route through resolver (tasks now visible/manageable).
   │
P3 — Cleanup + docs + test consolidation
   job_list UNION; remove the str(task.id) adapter comment; skill doc note;
   regression tests for the "one terminal notification" invariant.
   │
P4 — Virtual queue UI (the visible half for humans)
   Backend GET /work (or widen GET /jobs to the list_work UNION);
   frontend Work model + kind chip; jobs page unified list mode;
   SSE live updates keyed on work_id.
```

Engineering estimate: **~5–6 days** (P1 ~1.5, P2 ~1.5, P3 ~0.5, P4 ~1.5–2). Lower risk than D11/D13 because it is additive (a facade), not a dispatch-path replacement.

---

## 5. Feature flag

Single flag, default **ON** from day one (the facade is the safer path, not a risk):

| Flag | Purpose | Default | Removed |
|---|---|---|---|
| `USE_VIRTUAL_JOB_RESOLVER` | Route `job_get`/`watch_job`/`job_cancel`/`job_list` through the resolver vs. the old job-only path | **ON** | P3 (same release) |

The kill switch rolls the tools back to job-only resolution if a resolver bug appears; tasks become invisible again (regressing to today's behavior), not corrupting.

---

## 6. Test plan

New pack `tests/unit/services/test_work_resolver.py` + extensions to existing packs:

1. **`test_resolve_work_job` / `test_resolve_work_task`** — each kind resolves; status normalization correct (`running`→`processing`, `paused` preserved).
2. **`test_resolve_work_not_found`** — unknown work_id → None → tool returns "not found."
3. **`test_watch_task_and_notify_on_complete`** — `watch_job(task.work_id)`, drive task to COMPLETED via worker_pool, assert exactly one `[JOB_EVENT] ... completed ✓` message delivered to watcher instance, watch row removed.
4. **`test_watch_task_already_terminal`** — watch a COMPLETED task → immediate notification (parity with `watch_job` job behavior, `job_queue.py:655`).
5. **`test_cancel_task_via_job_cancel`** — `job_cancel(task.work_id)` sets `cancel_requested`; worker observes and the task reaches CANCELLED; watcher notified.
6. **`test_no_double_notify`** — a work_id that is both observer-finalized and task-terminal fires `notify_watchers` once (second call finds zero watchers).
7. **`test_job_list_union`** — `job_list` returns both a pending JobItem and a running Task, normalized to canonical status.
8. **`test_get_watched_processing_job_ids_restart_rebuild`** — restart-rebuild sees a watched *task* in-flight (not just jobs); parent stays non-complete until it resolves.
9. **`test_job_continue_returns_real_work_id`** — `new_job_id` is resolvable by `watch_job`/`job_get` (the regression this whole plan fixes).
10. **Migration/backfill tests** — existing `task` rows get a `work_id`; `job_watchers` FK dropped without losing rows (SQLite table-rebuild path covered by `tests/migrations/`).

Existing job-orchestration E2E tests (patterns 1–6 in the skill doc) must pass unchanged — they are the contract-preservation proof.

### P4 — UI tests (`frontend/src/app/pages/jobs/`)

11. **`jobs-page unified list renders both kinds`** — a pending JobItem and a running Task both appear as cards with correct `kind` chip; task card shows no queue badge.
12. **`task status flips stream via SSE`** — task terminal → card transitions to terminal status without a manual refresh (SSE keyed on `work_id`).
13. **`work endpoint filters`** — `GET /work?kind=turn` and `?status=processing` return the normalized subset.

---

## 7. Defer queue (follow-up — out of scope here, but enabled)

Once the facade lands, the defer queue is a `claim_pending_task` gate change, not a new management paradigm:

- Add `is_deferred: bool` (or reuse `next_retry_at` for time-based) to `task`.
- `claim_pending_task` (`task/repository.py:387`) skips deferred tasks unless the instance/project is idle (port of `job_queue_service.py:1036-1080`).
- The deferred message's `work_id` is already watchable by the orchestrator. **No new management surface needed.**

This is why D14 (this plan) is a prerequisite, not a competitor, to the defer queue.

---

## 8. Decisions (RESOLVED 2026-06-27)

1. **`job_watchers.job_id` column rename?** ✅ **Keep the name** `job_id`, treat as `work_id` semantically. No rename churn; the facade provides the abstraction, the column name doesn't matter.
2. **SQLite FK drop on `job_watchers`.** ✅ Accept the SQLite table-rebuild path (`CREATE NEW ... ; INSERT ... ; DROP OLD ; ALTER RENAME`); PG uses `DROP CONSTRAINT`. Migration handles both; covered by `tests/migrations/` harness.
3. **`paused` exposure.** ✅ **Surface it** — `paused ⏸` becomes a first-class virtual status. Add a `paused ⏸` row to the `job-orchestration/skill.md` status table in P3. A paused instance is actionable info the orchestrator should see.

---

## 9. Definition of done

- [ ] `task.work_id` minted for every new task; existing rows backfilled.
- [ ] `job_watchers` has no FK to `job_queue_items`; watch/cancel/get/list work over jobs **and** tasks.
- [ ] `notify_watchers` fires on task terminal (complete/fail/cancel) with exactly one notification.
- [ ] `AsyncMessageResult.job_id` / `job_continue`'s `new_job_id` is a real, resolvable `work_id`.
- [ ] The 10-test pack in §6 passes; existing job-orchestration E2E patterns pass unchanged.
- [ ] `job-orchestration/skill.md` notification contract still parses identically.
- [ ] `_ensure_postgres_columns()` parity entry present; PG startup applies `work_id`.
- [ ] No new dual-record coupling introduced (single source of truth per work_id; resolver is read-only).
- [ ] (P4) `GET /work` returns the UNION; jobs page shows jobs + task turns with a `kind` chip; task status flips stream live via SSE.

---

## 10. Risks

- **`get_watched_processing_job_ids` restart-rebuild** (`watcher_repository.py:217`) is the one place that JOINs `JobItem` for restart semantics. Mis-generalizing it can prematurely complete a parent on restart. Mitigated by test #8.
- **Cooperative vs. immediate cancel** — orchestrators expecting `job_cancel` to be instantaneous will see task cancel as "requested." Documented; behavior is already correct, just newly observable.
- **Notification dedup** relies on watch-row removal on terminal. If any task-terminal path forgets to call `notify_watchers`, the watch leaks (watcher waits forever). Mitigated by centralizing the call (single helper) + test #6.
- **(P4) Misleading "queue" framing** — the unified board must NOT present task turns as if they sit in a FIFO queue (they don't). The `kind` chip is the guardrail: only real queues show a queue badge; task turns show `turn`/`report`. Avoiding synthetic `job_queues` rows for tasks keeps the queue metaphor honest. The defer lane (§7) is the sole future exception.
