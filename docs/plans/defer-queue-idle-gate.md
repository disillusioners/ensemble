# Plan: Defer-Queue Idle Gate — move "is the project busy" off the ephemeral `task` layer

| Field | Value |
|---|---|
| **Status** | DRAFT — root cause confirmed against production DB; architecture agreed |
| **Goal** | The defer/background idle gate answers "has the non-defer unit of work finished?" on the **durable** lifecycle (job `admission_state` / instance status), not on the **ephemeral** `task` row. |
| **Scope** | MEDIUM — ~6 files, 1 lifecycle change (job/instance), 1 restored predicate, admission hot path |
| **Risk** | Hot path (task claim, job admission, defer/background scheduling). Two failure modes to avoid: **defer starvation** (gate never says idle) and **premature admission** (the current bug). |
| **Incident** | 2026-07-23 — defer job `be336411` admitted at 10:36 (local) while non-defer instance `40f1be39` was still working (`waiting_children` on a long-running child). |

---

## 1. Problem (what happened)

Production, project `83da04de`, server-local time (UTC+7), 2026-07-23:

- `40f1be39` (leader) — **non-defer** work. Original defer job `a447a21d` (defer queue
  `752d4f5d`) completed earlier; the user then sent a new message, which ran on the
  **parallel** queue as message job `1310169d` (`sys-parallel-83da04de…`). That job drove
  a graph turn that spawned children; the parent then processed child reports.
- `be336411` (leader, same project) — a **defer** job (`48103a25`) on the same system
  defer queue `752d4f5d` ("only processes when project is idle", `concurrency_limit=1`).

`be336411`'s defer job was **admitted at 10:36:22** while `40f1be39` was still working.

### Timeline of `40f1be39` task rows (all `is_deferred=false`)

| task | type | completed | note |
|---|---|---|---|
| 9936 | process_message | 09:20:18 | initial message turn (parallel job `1310169d`) |
| 9940 | process_report | 09:21:12 | child report |
| 9941 | process_report | 09:26:34 | child report |
| 9944 | process_report | **10:00:35** | child report ─┐ |
| | | | **~60 min GAP — no active task** (instance `waiting_children`, child still working) |
| 9974 | process_report | **11:00:07** started ─┘ | child report |
| 9976 | process_report | 11:02:46 | child report |

`be336411` was admitted at **10:36:22** — squarely inside the 10:00:35 → 11:00:07 gap.

### Predicate replay at the admission instant

`has_active_non_deferred_work('83da04de')` evaluated at `2026-07-23 10:36:21` returns
**0** — i.e. the gate correctly reported "project idle" *according to its own SQL*, yet
`40f1be39` was genuinely busy. The user paused `be336411` manually.

> Note: `logs/prod_run.log` is stale (last written 2026-07-22 12:36). The live prod process
> (`./ensemble-prod`, PID observed via `lsof`) logs to the terminal only (`/dev/ttys000`),
> so the live admission line is not on disk. All evidence above is from the PostgreSQL DB.

---

## 2. Root cause

The defer idle gate is **task-granular** and blind to instances that are between turns.

`has_active_non_deferred_work` (`daemon/repositories/task/repository.py:1418`) — the single
predicate used by **Gate A** (`daemon/services/job_processor.py:183` `_defer_idle_check`),
**Gate B** (`daemon/services/job_queue_service.py:2358` `_select_next_eligible_job`), and
**maintenance** (`daemon/services/maintenance.py:240` `_is_idle`) — returns "idle" when there
is **no** `task` row with:

```sql
status IN ('pending','running','paused') AND is_deferred = false [AND project_id = ?]
```

It never joins/consults `instances.status`. A `task` is **ephemeral per graph turn**: the
instant a turn ends the instance moves to `waiting_children`
(`daemon/services/message_processing_pipeline.py:743`) and has **no** active task row, even
though it is mid-flight (waiting on a child). During those inter-turn windows the gate sees
"idle" and admits defer work.

### Two coupled defects

1. **The non-defer job terminates too early.** Message job `1310169d` is already
   `admission_state='done'` (flipped after the initial `process_message` turn, ~09:20),
   while the instance keeps working (children) until 11:02. So *neither* a task-based gate
   *nor* a naïve job-based gate would block during the gaps.
2. **The gate is task/job-granular and blind to `waiting_children`.** While a child works,
   the parent has no `pending/running/paused` task **and** its job is `done` → the gate
   reports "project idle".

### Why it "worked before" / surfaced now

The predicate has been task-based since the defer-seam refactor (2026-06-30); the blind spot
is **latent** and triggers whenever a parent waits on a long-running child. The recent
**report-injection** feature (`e858aa94`, `e513f2e9`, `6f3b7339`) likely widened the idle
windows: when a child report is injected into a *live* parent turn, the `PROCESS_REPORT`
task is skipped ("already delivered via report-injection — skipping PROCESS_REPORT graph
turn"). Fewer/shorter non-deferred tasks → wider `waiting_children` gaps → the race
triggered. A child that took ~1 hour (09:59 → 11:00) did the rest.

---

## 3. Architecture smell (the real issue)

The gate conflates two different questions and answers both with the wrong source of truth:

| Question | Correct source of truth | Current source |
|---|---|---|
| "Can this deferred task be *claimed* right now without racing?" | `task` (atomic with the claim) | `task` ✅ |
| "Is the project *idle* — has the non-defer unit of work **finished**?" | `job.admission_state` / `instance.status` (**durable** lifecycle) | `task` ❌ |

`task` is per-turn; `job.admission_state`/`instance.status` span the whole unit of work
(message + spawned children). Keying "is the project busy" on a per-turn row guarantees
false-idle windows whenever an instance pauses between turns.

### Why it moved off jobs onto tasks (history)

This was deliberate, not accidental:

- **D13** (`89333a47` "eliminate MESSAGE JobItem creation") made messages job-less; the code
  comment said *"task table = source of truth post-D13."*
- **Phase 1 defer-seam** (`b79ddc87`) replaced the job-based
  `count_active_jobs_in_non_deferred_work` with the task-based
  `has_active_non_deferred_work` to: (a) close the **TOCTOU** between the Python admission
  probe and the SQL atomic claim in `claim_pending_task`, (b) see job-less/"virtual" work,
  (c) fix a self-deadlock (P1) and "defer admitted during active virtual work" (P2). The
  predicate was **inlined into `claim_pending_task`'s atomic SQL** so claim + admission
  could not disagree.
- `91859bde` consciously put "defer queue on the task layer".

**The justification has since eroded.** Messages create `JobItem`s again (`1310169d` is a
`message` job). The durable lifecycle the gate abandoned now exists; using a per-turn task
row to define "is the project busy" is both blind (the incident) and no longer necessary for
the idle-semantics question. The atomic-claim argument still holds — but only for the
**claim-guard**, not for the idle definition.

> Existing carve-out to respect: `claim_pending_task` excludes `waiting_children` at
> `daemon/repositories/task/repository.py:653` so one `waiting_children` instance does **not**
> block a *different* instance's FIFO task. That carve-out is about FIFO **claiming** and is
> orthogonal — treating `waiting_children` as "busy" for the **defer gate** does not conflict
> with it.

---

## 4. Target architecture: separate the two concerns

- **Idle semantics** live on the **durable unit of work**, keyed by `admission_state` +
  instance terminality. A non-defer job is considered "in flight" while its instance is
  non-terminal (`running` / `waiting_children` / `paused`), not just while it has an active
  task. This is essentially the **legacy job-based predicate that was deleted**, restored
  with a corrected job lifecycle.
- **Claim-guard atomicity** keeps the task predicate **inlined in `claim_pending_task`**
  (`daemon/repositories/task/repository.py`) — but strictly as a belt-and-suspenders race
  guard, **not** as the definition of "idle".

Net: jobs/queues own *when* the project is busy (lifecycle); tasks own *whether a specific
claim races* (atomicity).

### 4.1 Background queue — same defect, wider blast radius

The **background** queue has the identical task-granular blind spot, and is the more
dangerous case because its scope is **system-wide**, not project-scoped.

- Its gate is `has_active_non_background_work` (`daemon/repositories/task/repository.py:1517`),
  the sister predicate to the defer gate. Same SQL shape (`status IN (pending, running, paused)`
  AND `is_deferred=false` AND `is_background=false`), same per-turn blindness to
  `waiting_children` instances — but it ignores `project_id` entirely (the parameter is
  `del`'d; Phase 3 background-seam, `eb1da642`).
- It is consulted by **Gate A** (`daemon/services/job_processor.py:250`
  `_background_idle_check`, which passes `None`), **Gate B**
  (`daemon/services/job_queue_service.py:2461`), and **maintenance** (`_is_idle`).
- Consequence: a background job can be admitted whenever **every** instance in **every**
  project happens to be between turns at the same instant. With dozens of long-lived parents
  in `waiting_children` across projects, this is the same false-idle race, just
  system-global — so it fires more often and is harder to reason about than the defer case.

**Scope asymmetry must be preserved** (documented Phase 3 background-seam, 2026-07-14):

| Gate | Idle question | Scope |
|---|---|---|
| DEFER | non-deferred work finished in **this project**? | project-scoped |
| BACKGROUND | non-deferred, non-background work finished **anywhere**? | system-wide |

Both move to the job/admission-state lifecycle, but the new background job predicate keeps
the system-wide scope (no `project_id` filter). The Phase 1 job-lifecycle fix already
covers background: a non-defer message job staying `active` until `idle` is "non-background
work" by definition, so it blocks both gates from the same lifecycle correction.

---

## 5. Phases

### Phase 1 — Ship-first: fix the job lifecycle (unblocks existing gates)

The cheapest correct fix: keep the non-defer message job `admission_state='active'` until
its instance leaves `running/waiting_children` back to `idle`. Once the job spans the full
message + children lifecycle, the existing gates block correctly even without touching the
predicate.

- `daemon/services/message_processing_pipeline.py` / `child_reports.py` / job-finalize path:
  do not flip a message job to `done` while its instance is `running` or `waiting_children`
  (i.e. while `dependency_bus` has pending children, or the instance is non-terminal and
  non-idle). Finalize on the **idle** transition, not on the first-turn completion.
- Verify `admission_state` transitions still release the queue lock only when truly idle
  (mirror the existing "paused holds the lock" semantics, `32ac51b7`).

**Acceptance:** a parent on the parallel queue that spawns a long-running child keeps its
job `active`; the defer queue does not admit until the parent reaches `idle`.

### Phase 2 — Restore lifecycle-based idle predicates for Gate A / B / maintenance

Two predicates (project-scoped defer + system-wide background), both job/admission-state
aware:

- Add `JobQueueRepository.has_active_non_deferred_work(project_id)` — the modern successor
  to the deleted `count_active_jobs_in_non_defer_queues`
  (`daemon/repositories/job_queue/repository.py:549`). Counts non-defer jobs with
  `admission_state IN ('queued','active')` whose instance is non-terminal
  (`running`/`waiting_children`/`paused`), scoped by `project_id`.
- Add `JobQueueRepository.has_active_non_background_work()` — the **system-wide** sister
  predicate (no `project_id` filter), counting jobs with `admission_state IN ('queued','active')`
  whose queue is non-defer **and** non-background and whose instance is non-terminal. This is
  the job-level replacement for `TaskRepository.has_active_non_background_work`.
- Repoint **Gate A** (`_defer_idle_check` + `_background_idle_check`), **Gate B**
  (`_select_next_eligible_job`, both the defer and background branches), and **maintenance**
  (`_is_idle`) at the new predicates. Gate B keeps its three-way (normal/defer/background)
  decision shape; preserve the project-vs-system scope asymmetry from §4.1.
- Keep `TaskRepository.has_active_non_deferred_work` /
  `has_active_non_background_work` **only inside `claim_pending_task`** as the atomic race
  guards.

**Acceptance:** same scenario as Phase 1 blocks the defer queue *even if* the job lifecycle
regresses, because the gate now reads the durable lifecycle directly. Additionally: a
background job is not admitted while any non-defer/non-background job's instance is
`waiting_children` in **any** project.

### Phase 3 — Cleanup, tests, docs

- Drop now-dead task-based idle reads from the admission path; keep doc comments accurate.
- Invariant tests in `tests/job_queue/test_seam_invariants.py`: "defer never admits while a
  non-defer job's instance is `waiting_children`", plus the paused/terminated matrix.
- PG + SQLite parity for the new predicate (Python-boolean dual-driver pattern, see
  `has_active_non_deferred_work` dialect notes).
- Update `docs/job-queue.md` (defer/background idle semantics) and the predicate docstrings.

---

## 6. Risks & guardrails

- **Defer starvation (over-blocking).** This project has ~145 non-terminal instances at rest
  (always-on `experiencer`/`kb-importer`/`explorer`, plus many `waiting_children` parents).
  The new predicate must count a job as "in flight" **only when its own instance is
  non-terminal**, not blanket-count all non-terminal instances — otherwise the defer queue
  never runs. Scope the predicate to the job→instance lineage.
- **Background starvation (worse — system-wide).** The background gate is system-wide, so
  the same always-on/`waiting_children` population across **all** projects would make it say
  "busy" forever. The same job→instance-lineage scoping applies, but validate empirically
  that a healthy system still reaches "all non-background work idle" often enough for
  background jobs to make progress — otherwise background effectively dies. Consider whether
  always-on agents (`experiencer`, `kb-importer`, `explorer`) should be excluded from the
  busy-signal (e.g. by agent type / a "daemon instance" flag) since they are never truly idle
  by design.
- **Self-deadlock.** A defer job's own `waiting_children` instance must not block *its own*
  admission (already admitted; `concurrency_limit=1`). Guard against a defer job observing
  itself as busy.
- **Lock retention.** Holding `admission_state='active'` longer keeps the queue lock longer;
  verify it does not stall the parallel/fifo lanes or the `job_queue_items_active_lock_guard`
  trigger.
- **Carve-out parity.** The FIFO `waiting_children` carve-out (`task/repository.py:653`)
  stays FIFO-only; the defer gate intentionally treats `waiting_children` as busy. Document
  the asymmetry so a future reader does not "unify" them.

---

## 7. References

- Predicate (task-based, to be demoted to claim-guard): `daemon/repositories/task/repository.py:1418`
- Background sister predicate: `daemon/repositories/task/repository.py:1517`
- Gate A: `daemon/services/job_processor.py:183` (`_defer_idle_check`) and
  `daemon/services/job_processor.py:250` (`_background_idle_check`, system-wide)
- Gate B: `daemon/services/job_queue_service.py:2358` (`_select_next_eligible_job`, decision
  2489–2503; defer branch ~2437, background branch ~2461)
- Maintenance idle: `daemon/services/maintenance.py:240` (`_is_idle`)
- Legacy job-based predicate (deleted from admission path): `daemon/repositories/job_queue/repository.py:549`
- FIFO `waiting_children` carve-out (orthogonal): `daemon/repositories/task/repository.py:653`
- `waiting_children` transition: `daemon/services/message_processing_pipeline.py:743`
- History: `89333a47` (D13), `b79ddc87` (defer-seam Phase 1), `91859bde` (defer on task layer),
  `7ecf09e2` (paused = non-idle), `32ac51b7` (claim-gate alignment), `eb1da642` (background seam)
- Incident artifacts: defer queue `752d4f5d` (system_defer_queue); jobs `a447a21d`/`1310169d`
  (`40f1be39`), `48103a25` (`be336411`); project `83da04de`
