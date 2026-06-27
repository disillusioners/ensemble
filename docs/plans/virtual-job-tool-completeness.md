# Plan: Virtual Job — Tool-Surface Completeness & Root Scoping

| Field | Value |
|---|---|
| **Status** | DRAFT — design proposed; not started |
| **Parent** | `docs/plans/virtual-job-management-surface.md` (the D14 facade). This plan closes the gaps that facade left in the orchestrator-facing tools. |
| **Mode** | Targeted hardening. No new tables, no dispatch-path change. Edits are confined to the `job_*` tool surface + the resolver's `list_work` read filter. |
| **Depends on** | `feature/virtual-job-management-surface` merged (resolver live, flag `USE_VIRTUAL_JOB_RESOLVER` default ON). |
| **Agent contract** | `agents/_prompt_system/innate-skills/job-orchestration/skill.md` stays valid verbatim — every change tightens the existing handle contract, never widens it. |
| **Definition of done** | §9 below |

---

## 1. Problem

The D14 facade shipped the *read* half of the virtual job (resolver → `WorkRecord`), and the
`job_*` tools in `daemon/tools/job_queue.py` were *partially* rewired behind the
`use_virtual_job_resolver` flag. A full audit shows the rewire is **incomplete**, leaving two
classes of broken handle plus a listing-noise bug that together defeat the jober's ability to
manage the work it created.

### 1.1 Audit of every `job_*` tool

| Tool | Resolver-aware? | Line | Verdict |
|---|---|---|---|
| `job_create` | n/a (creates new JobItem) | `:291` | ✅ fine |
| `job_get` | ✅ `service.get_work` | `:343` | ✅ works for task + job |
| `job_list` | ✅ `list_work` UNION | `:397` | ⚠️ **no root scoping** (§1.2) |
| `job_cancel` | ✅ routes task vs job | `:488` | ✅ works |
| `job_retry` | ❌ `retry_job` (JobItem-only) | `:551` | ⚠️ task work_id → generic error (§1.4) |
| `job_delete` | ❌ `soft_delete_job` (JobItem-only) | `:564` | ⚠️ task work_id → generic error (§1.4) |
| `job_restore` | ❌ `restore_job` (JobItem-only) | `:577` | ⚠️ task work_id → generic error (§1.4) |
| **`job_continue`** | ❌ **`get_job` (JobItem-only)** | `:604` | 🔴 **broken handle** (§1.3) |
| `queue_list/create/update` | n/a (queue mgmt) | `:699+` | ✅ fine |
| `dlq_list` | job-only (by design — tasks have no DLQ) | `:780` | ✅ acceptable |
| `dlq_replay` | job-only (by design) | `:804` | ✅ acceptable |
| `watch_job` | ✅ `service.get_work` | `:847` | ✅ works |
| `unwatch_job` | n/a (watcher-row key) | `:906` | ✅ fine |
| `list_watched_jobs` | n/a (lists rows) | `:929` | ✅ fine |
| `watch_jobs` | ✅ `service.get_work` | `:981` | ✅ works |

### 1.2 `job_list` surfaces child-instance noise (the original report)

`WorkResolverService.list_work` (`daemon/services/work_resolver.py:420`,
`_query_tasks`/`_query_jobs`) has **no `parent_id` awareness**. A job the jober created is bound
to one root instance (`job_create` → `agent_id`); that root spawns **child instances** to do
sub-work, and those children emit their own `process_message` (turn) and
`process_report`/`send_report` (report) tasks. Every one of those child rows is returned by
`list_work` as if it were a first-class work unit the jober owns.

The jober manages work it bound to a root. Child turns/reports are internal mechanics of that
root's job — they have **no link back to the originating `job_id`**, so the jober cannot attribute,
cancel, or watch them as part of the job it created. Surfacing them is noise that breaks the
"one virtual job per root" mental model.

### 1.3 `job_continue` is a broken handle for task work_ids

`job_continue` (`daemon/tools/job_queue.py:604`) looks the old job up via
`job_service.get_job(old_job_id)` — a **JobItem-only** lookup. But:

* `job_continue` itself **returns** `new_job_id = result.job_id`, which after D13 is a **task
  `work_id`** (`enqueue_message` writes a Task row, not a JobItem — confirmed at
  `daemon/services/job_processor.py:700`, the job's root instance is fed via
  `enqueue_message`).
* So the jober's natural follow-up — `job_continue(old_job_id=<that task work_id>)` — returns
  **`"Job {id} not found"`**.

This is the exact broken-handle class the whole D14 plan (`virtual-job-management-surface.md` §1)
was created to eliminate. The plan §3.7 *claimed* `job_continue` was handled ("the lie is fixed at
the source"), but only the **return** side was fixed; the **lookup** side was never made
resolver-aware. D14's own test #9 (`test_job_continue_returns_real_work_id`) was never enforced
against the *continue-from-a-task-work_id* path — it only checked the return value, not a
subsequent `job_continue`/`job_get` round-trip.

### 1.4 One logical job → 2+ work rows

Because a dispatched job spawns its root instance and then `enqueue_message`s it
(`job_processor.py:685` then `:700`), **one logical job produces two work records**:

1. the `JobItem` (`kind=job`) — the dispatch-queue row the jober created and holds.
2. the root instance's `process_message` Task (`kind=turn`) — the row that actually drives
   `graph.astream`.

…plus N child turns/reports (§1.2). So even after excluding child instances, `job_list` shows
**2 rows per job**. The jober created one handle (the `job_id`) but sees two; and
`job_cancel`/`watch_job` behave differently depending on whether the caller passed the `job_id`
or the root-turn `work_id`.

### 1.5 `job_retry` / `job_delete` / `job_restore` are silently job-only

These three call `JobQueueService.retry_job` / `soft_delete_job` / `restore_job` directly with no
resolver resolution. A task `work_id` flows straight through to a JobItem lookup that returns
`None`, producing a generic `"Job may not exist / not be retryable"` message. D14 §3.7 stated the
intent ("resolver returns 'not applicable for task-type work'") but that branch was never written.
Result: the jober cannot distinguish "this task work_id isn't retryable by design" from "this job
doesn't exist" — a misleading error that degrades orchestration reasoning.

---

## 2. Design

Three independent hardening tracks. They compose; none blocks another except that P-A's
root-scoping decision (§2.1) informs how P-C (§2.3) collapses the 2-rows-per-job problem.

### 2.1 P-A — Root-instance scoping in `list_work`

Add an optional `root_only: bool` parameter to `WorkResolverService.list_work` (and the
`GET /api/work` router + `job_list` tool), default **`True`**. When on, drop any work whose
backing instance has a non-null/non-empty `parent_id` (`daemon/repositories/instance/models.py:56`
— `parent_id` is already indexed).

Implementation posture:

* **Task side** (`_task_to_record`, `work_resolver.py:573`): the resolver already does
  `_lookup_instance(task.instance_id)` per row (`:644`). Add a guard there —
  `if root_only and instance is not None and instance.parent_id: skip`. This is post-fetch but
  cheap (the lookup is already paid for; `parent_id` is on the row).
* **JobItem side** (`_job_to_record` / `_query_jobs`, `work_resolver.py:599`/`:712`):
  `JobItem` carries `instance_id` directly. To filter at SQL level we'd need a JOIN to
  `instances`; instead do a single batched lookup: collect the distinct `instance_id`s, fetch
  their `parent_id`s in one `SELECT ... WHERE instance_id IN (...)`, and drop child rows
  post-fetch. Keeps the read consistent with the Task-side approach and avoids a cross-repo JOIN.
* **Frontend / router**: `GET /api/work` (`daemon/routers/work.py:106`) gains a
  `root_only` query param (default `true`). The jobs page "All Work" view keeps the default; a
  future debug toggle can set `root_only=false` to see everything.

**Decision (RESOLVED for now):** exclude child work, do **not** re-attribute it to the root. The
defer-lane / per-instance-grouping follow-ups (parent plan §3.10 Option B) are where child work
earns a deliberate surface; until then it stays out of the jober's management view.

### 2.2 P-B — Make `job_continue` resolver-aware

Rewrite the lookup half of `job_continue` (`job_queue.py:604`) to resolve `old_job_id` through
`service.get_work` when the flag is on:

1. `record = await job_service.get_work(old_job_id)`. If `None` → existing `"Job not found"`.
2. Read `instance_id` from `record.instance_id` (present on both Task and JobItem
   `WorkRecord`s).
3. Terminal check: `work_status.is_terminal(record.status)` instead of the JobItem-status set
   check (`TERMINAL_STATES`). The canonical status covers both sides.
4. The soft-deleted guard (`:609`) is **JobItem-only** — `WorkRecord` has no `deleted_at`. For a
   task work_id this guard is a no-op (tasks have no soft-delete); for a job work_id, fall through
   to a `get_job` lookup **only** to read `deleted_at`, OR accept the minor regression that a
   soft-deleted job can be continued (low risk: `job_delete` is rare and `enqueue_message` would
   still re-drive a live instance). **Recommended:** keep correctness — when `record.kind == "job"`,
   do a cheap `get_job` for the `deleted_at` check; skip it for task-kind work.
5. The in-flight task pre-check (`has_inflight_task`, `:671`) and `enqueue_message` call stay
   exactly as-is — they already key on `instance_id`, which the resolver supplies.

The kill-switch path (`use_virtual_job_resolver == False`) keeps today's `get_job` behavior
verbatim.

### 2.3 P-C — Collapse the 1-job → 2-rows duplication

Two sub-options; **recommended: P-C(ii)** because it points the jober at the *real* unit of work.

* **(i) Dedupe at read time.** In `list_work` (root-scoped, after P-A), when a root turn
  (`kind=turn`) shares its instance with a non-terminal `JobItem`, fold the turn into the job row
  (or hide it). Risk: confusing `job_get`/`job_cancel` semantics when two work_ids collapse to one
  visible row.
* **(ii) Watch/cancel the root turn, not the JobItem.** The JobItem is the *dispatch* handle; the
  root `process_message` Task is the *execution* handle. Make `job_create` (when it watches)
  register the watch on the **root turn `work_id`** once the dispatcher creates it, so the jober's
  `watch_job`/`job_cancel`/`job_get` act on the row that actually reflects graph progress. The
  JobItem row remains the create-time return handle (stable, exists before dispatch) but the
  *lifecycle* handle flips to the turn.

  This is the larger change; it touches `JobProcessor` (it must surface the turn `work_id` back)
  and the create→watch wiring. **Defer the full P-C(ii) to a follow-up** and document it; for
  this plan, ship P-A + P-B which already remove the noise and the broken handle.

### 2.4 P-D — Honest job-only tools

In `job_retry` / `job_delete` / `job_restore`, resolve first:

```
record = await job_service.get_work(job_id)   # only on the flag-ON path
if record is not None and record.kind != "job":
    return f"Operation not applicable: {job_id[:8]}... is task-type work ({record.kind}), " \
           f"which has no retry/delete/restore path."
```

Only fall through to the JobItem mutation when `record is None` (legacy id, flag off, or genuinely
a job). Task-kind work gets a precise, actionable message instead of "not found".

---

## 3. Concrete change list

### 3.1 `daemon/services/work_resolver.py`
* `list_work(...)` gains `root_only: bool = True`.
* `_task_to_record` / the Task loop in `list_work` (`:551-556`): skip rows where the looked-up
  instance has `parent_id`.
* `list_work` JobItem branch (`:558-560`): batch-resolve `parent_id` for the page's
  `instance_id`s, drop child rows.
* Docstring §`root_only` + the rationale (matches §2.1).

### 3.2 `daemon/routers/work.py`
* `GET /work` (`:106`) gains `root_only: bool = Query(default=True)`; forwarded to
  `resolver.list_work(..., root_only=root_only)`.

### 3.3 `daemon/tools/job_queue.py`
* `job_list` resolver branch (`:419-427`): pass `root_only=True` (the tool surface should mirror
  the management default; an explicit `include_children` param is a follow-up).
* `job_continue` (`:599-695`): rewrite the lookup per §2.2.
* `job_retry` (`:551`), `job_delete` (`:564`), `job_restore` (`:577`): add the resolve-then-classify
  guard per §2.4.

### 3.4 `daemon/services/work_status.py`
* No change — `is_terminal` / `canonicalize_status` already exist and cover both sides.

### 3.5 Docs
* `agents/_prompt_system/innate-skills/job-orchestration/skill.md`: add a one-line note that
  `job_continue` / `job_get` handles work over continued-instance work (task work_ids), and that
  `job_list` shows root-instance work by default. No format/notification change.

---

## 4. Phasing (one branch, 2 PRs)

```
PR1 — Root scoping + honest errors (P-A + P-D)
   list_work(root_only=True); /work?root_only; job_list; job_retry/delete/restore guard.
   Zero handle-semantics change — only noise reduction + better errors.
   Safe to ship first; unblocks the jober's day-to-day list immediately.
   │
PR2 — job_continue resolver-awareness (P-B)
   Rewrite the continue lookup; add the round-trip contract test (§6 #9b).
   Closes the D14 test #9 gap that was never enforced against task work_ids.

P-C (collapse / watch-the-turn) — follow-up plan, out of scope here.
```

Engineering estimate: **~2 days** (PR1 ~1, PR2 ~1). Low risk: all edits are additive guards or
read-path filters; the flag kill-switch is preserved on every touched tool.

---

## 5. Feature flag

No new flag. `USE_VIRTUAL_JOB_RESOLVER` (`daemon/config.py:419`, default ON) continues to gate
every touched tool: each new branch sits behind `job_service.use_virtual_job_resolver`, and the
legacy JobItem-only path is the clean revert.

`root_only` is **not** a kill switch — it is a normal filter with a sensible default (`true`). It
needs no env flag; it's a `list_work` / `/work` / `job_list` parameter.

---

## 6. Test plan

New cases in `tests/unit/services/test_work_resolver.py` and `tests/unit/routers/test_work_router.py`,
plus extensions to `tests/test_job_queue_tools.py`:

1. **`test_list_work_root_only_excludes_children`** — seed a root instance + a child instance, one
   Task each; with `root_only=True` (default) only the root's Task is returned; with
   `root_only=False` both are.
2. **`test_list_work_root_only_keeps_jobs`** — a `JobItem` bound to a root instance is returned
   under `root_only=True`; a `JobItem` whose instance is a child is excluded.
3. **`test_work_endpoint_root_only_param`** — `GET /work?root_only=false` returns children; default
   omits them (router unit test).
4. **`test_job_list_resolver_excludes_children`** — tool-level: `job_list` with the flag ON returns
   no child-instance rows.
5. **`test_job_retry_task_kind_message`** — `job_retry` on a task work_id returns the precise
   "not applicable for task-type work" message (not a generic error). Same for `job_delete` /
   `job_restore`.
6. **`test_job_continue_from_task_work_id`** *(the D14 test #9 gap)* — drive a job to terminal,
   take the returned `new_job_id` (a task work_id), call `job_continue` on it; assert it resolves
   the instance and enqueues a new message (no "Job not found").
7. **`test_job_continue_from_job_work_id`** — the legacy JobItem path still works (regression).
8. **`test_job_continue_soft_deleted_job_rejected`** — `kind=job` + `deleted_at` set → rejected;
   `kind=turn` → no `deleted_at` check (tasks aren't deletable).

Existing job-orchestration E2E patterns must pass unchanged (contract-preservation proof).

---

## 7. Out of scope (explicit follow-ups)

* **P-C — collapse / watch-the-turn** (§2.3). The 1-job → 2-rows problem (the root turn + the
  JobItem) is acknowledged but deliberately deferred: it requires dispatcher changes to surface
  the turn `work_id` and is higher-risk. P-A hides the child noise; P-B fixes the broken continue
  handle; together they restore a usable surface without the collapse.
* **Defer-lane as a real virtual queue** — parent plan §7; depends on the defer queue, not here.
* **`root_only` as a UI toggle** — the default-on filter is enough for the management surface; a
  debug "show children" toggle in the jobs page is cosmetic.

---

## 8. Decisions

1. **Exclude vs re-attribute child work?** ✅ **Exclude** for now (§2.1). Re-attribution to the
   root via `get_tree_root_id` (`instance_lifecycle.py:182`) is the P-C territory; it muddies
   `job_get`/`job_cancel` semantics (which instance do they act on?) and is deferred.
2. **`root_only` default?** ✅ **`True`**. The jober (and the "All Work" board) want root-scoped
   views; `false` is the debug escape hatch.
3. **`job_continue` `deleted_at` guard for task-kind?** ✅ Apply the JobItem-only guard only when
   `kind == "job"` (cheap `get_job` for the column); task-kind has no soft-delete, so skip.
4. **Collapse the 2-rows-per-job now?** ✅ **No — defer to P-C follow-up.** Ship P-A + P-B first.

---

## 9. Definition of done

- [ ] `list_work(root_only=True)` (default) drops child-instance work; Task and JobItem sides both
      filtered. `root_only=False` restores the union.
- [ ] `GET /api/work` honors `root_only` (default `true`).
- [ ] `job_list` (resolver path) returns a root-scoped list.
- [ ] `job_continue` resolves a task `work_id`; the test #9 round-trip (`new_job_id` →
      `job_continue`) passes (the gap D14 left open).
- [ ] `job_retry` / `job_delete` / `job_restore` return a precise "not applicable for task-type
      work" message for task work_ids; job work_ids behave unchanged.
- [ ] Every touched tool keeps a clean kill-switch branch (`use_virtual_job_resolver == False`
      → today's JobItem-only behavior).
- [ ] `job-orchestration/skill.md` notification/parsing contract unchanged; one-line note added.
- [ ] All new tests in §6 pass; existing job-orchestration E2E patterns pass unchanged.

---

## 10. Risks

- **Root scoping hides a still-running child turn** — if a root is reported `completed` while a
  child turn is still in flight, `root_only=True` hides the in-flight child and the jober may
  reason the job is done. Mitigation: this is a *read* view; the JobItem's own terminal status
  (the handle the jober watches) is unaffected. The child turn never carried a link back to the
  job anyway, so today's behavior is no worse — the child was already unmanageable noise.
- **`job_continue` from a turn work_id re-uses a root instance that has since spawned children** —
  `enqueue_message` to that instance is already correct (messages are per-instance); no new
  hazard introduced by resolving via the turn.
- **Honest-error messages change the string contract** — `job_retry`/`job_delete`/`job_restore`
  return strings; if any agent parses these verbatim they'd see a new message. Mitigation: the
  skill doc only documents terminal-state notification parsing, not tool return-string parsing;
  the new messages are strictly more informative. Covered by the "E2E patterns unchanged" gate.
