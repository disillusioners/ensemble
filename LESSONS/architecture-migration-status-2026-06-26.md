# Architecture Migration Status — D11/D13 + Phase D follow-ups

**Date:** 2026-06-26
**Scope:** Identify what's still missing from the Phase A→D decouple plan vs. what actually landed on `latest`.
**Reference docs:**
- `docs/plans/decouple-execution-plan.md` (Phases A–C; D deferred)
- `docs/plans/decouple-job-task-message-correlation.md` (single-run M1–M8)
- `docs/plans/cleanup-old-architecture.md` (Phases 1–8, post-D cleanup)

---

## TL;DR

Phase D (Dependency Bus) landed (commits 8d20ffb6 + 9f496168) and Phase 5–8 cleanup landed (commit fd392317 + Phase 8). The `MessageJobHandler` is deleted. `CorrelationManager` is deleted. `USE_DEPENDENCY_BUS` is removed.

**But D11 and D13 are INCOMPLETE despite the commit message claiming otherwise.** `job_processor.py:686` still has the `if job_type == 'message':` branch, and `enqueue_job` at `job_queue_service.py:379, 500` still creates MESSAGE-typed JobItem rows. The CHANGELOG says they're gone; the code disagrees. This is what the production incident 06f500af surfaced — without finishing D13, every user message creates **two coupled work records** (Task + JobItem) and stale-recovery has to keep them in sync.

---

## What actually shipped

### ✅ Phase A — Authority & visibility (`USE_LEGACY_WAITING_FOR_CASCADE`, `DEBUG_COMPLETION_INVARIANT`)

- Flag removed from `daemon/config.py`. No `if use_legacy_waiting_for_cascade:` branches remain.
- Kill switch confirmed gone (`grep -rn "USE_LEGACY\|DEBUG_COMPLETION" daemon/ --include="*.py"` = 0 hits in source; only stale comments in `tools/instance.py:628`, `instance_lifecycle.py:2138, 2394`, `migrations/...sql:42, 53`).
- Pre-commit `e9c2b91f` = "Phase 3: remove USE_LEGACY_WAITING_FOR_CASCADE flag and gated paths"

### ✅ Phase B — Close the bug class (`watch_job` via `pending_jobs`)

- `commit bad3bea3` = "route watch_job through CorrelationManager via pending_jobs (B1-B4)".
- CM is now removed, but the bus has the equivalent: `bus.watch(source_task_id, FollowUp)` for any correlation. Watched-job completion flows through `_emit_terminal_via_bus`.

### ✅ Phase C — Single dispatcher

- `MessageJobHandler` deleted (770 lines) — `commit 8d20ffb6` (D12).
- `USE_LEGACY_JOBQUEUE_DISPATCH` removed — `commit 7d2836cd`.
- ExecutionGate collapsed from DB-backed lease to per-instance `asyncio.Lock` (`commit 7d2836cd` / "C-M6 collapse ExecutionGate to asyncio.Lock — 707→268 lines").
- `pause_instance_cascade` / `terminate_instance` call `dependency_bus.cancel_for_target()` on the affected parent (per CHANGELOG Phase D entry).

### ✅ Phase D — Dependency Bus (delivered as `commit 8d20ffb6`)

- `DependencyBus` service with `watch`, `emit_terminal`, `cancel_for_target`, `cancel_for_source` (added in fix `4926a2eb`).
- `dependency_watchers` table with PENDING/FIRED/CANCELLED states.
- `use_dependency_bus` flag default `True`. CM kept as shadow validator only.
- `completion_delivery_path=bus` structured log metric.
- 30-test Dependency Bus test pack (25 SQLite + 5 PostgreSQL).
- D11 + D12 + D13: described as done in commit message. **Reality: partially done** (see below).

### ✅ Phase 5 — Remove CorrelationManager (`commit fd392317`)

- `daemon/services/correlation_manager.py` deleted (1843 lines).
- `cm._generation` extracted to bus (`commit 59b6b68d`).
- Per-parent locking moved to bus (`self._parent_locks`, `bus._get_parent_lock`).
- `TestBusSoleAuthority` added with sticky-error semantics (`commit fd392317`, `TestBusSoleAuthority::test_parent_errors_if_any_child_errored`).
- Error status threaded through `_retrigger_parent_finalize` (5.7 behavioral fix).

### ✅ Phase 8 — Remove `USE_DEPENDENCY_BUS` flag

- CHANGELOG entry: "Phase 8 — Cleanup old architecture (FINAL)".
- `daemon/config.py` no longer references `use_dependency_bus`.
- `grep -rn "USE_DEPENDENCY_BUS\|use_dependency_bus\|use_dep_bus" daemon/ --include="*.py"` returns 0 source hits (only doc strings in `migrations/...sql`).

---

## ❌ What's still missing — the open items

### 1. D11 partial — `if job_type == 'message':` branch in JobProcessor

**Status:** Branch STILL present at `daemon/services/job_processor.py:686`.

```python
686:                    if getattr(started_job, 'job_type', 'task') == "message":
687:                        if self._job_feedback_observer is None:
```

**What was claimed:** `commit 8d20ffb6` says "D11: job_processor.py — removed job_type='message' dispatch branch". The CHANGELOG Phase D entry says "`job_type='message'` JobItem rows — no longer written."

**What's actually there:**
- The branch exists.
- The branch routes MESSAGE jobs through the observer (`_job_feedback_observer._process_event`) — which IS the modern design, but the code still differentiates `job_type='message'` from `job_type='task'` and runs a different code path. This is exactly the "MESSAGE vs Job" coupling the review flagged.
- The CHANGELOG claim that the branch is "also gone" is inaccurate.

**What should land:**
- Drop the `if job_type == 'message':` branch entirely.
- All jobs (including MESSAGE) flow through the same dispatch path: `start_job` → observer `_process_event` → `_emit_terminal_via_bus`.
- Remove the `MessageJobHandler.handle()` reference in the surrounding doc-comment (line 672–685), which still describes a separate MESSAGE path.

**Effort:** ~1 day. Mostly removal + test consolidation.

### 2. D13 partial — `enqueue_job` still creates MESSAGE JobItem rows

**Status:** `enqueue_job` at `daemon/services/job_queue_service.py:379, 500` still differentiates `job_type == "message"` and creates MESSAGE-typed JobItem rows.

```python
379:                if job_type == "message":
380:                    queue = await asyncio.to_thread(
381:                        self._queue_repo.get_by_name, project_id, "system_parallel_queue"
382:                    )
...
500:            if job_type == "message":
501:                # MESSAGE jobs → system_parallel_queue (parallel execution)
```

**What was claimed:** `commit 8d20ffb6` says "D13: job_queue_service.py — removed MESSAGE-specific helpers, cancel_message_job now delegates to general cancel_job." CHANGELOG says "JobQueue no longer owns a JobItem lifecycle for messages; only Task rows are written."

**What's actually there:**
- `cancel_message_job` was removed (true).
- But `enqueue_job` STILL accepts `job_type="message"` and writes a MESSAGE-typed JobItem.
- `enqueue_message(instance_id, message, dispatch_path="jobqueue", ...)` (`daemon/services/instance_messaging.py:887`) still routes through `enqueue_job` and creates the JobItem.

**What should land:**
- `enqueue_message` with `dispatch_path="jobqueue"` should NOT call `enqueue_job` at all — it should write only `message_queue` + `task` rows (the `dispatch_path="workerpool"` path).
- `enqueue_job` should reject `job_type="message"` with `ValueError` (defense in depth; the only legit caller is now gone).
- After this lands, `message_job_queue_items` rows for messages go away. Only `task` rows exist per message.

**Effort:** ~2 days. Includes:
- Update `enqueue_message` to write `task` row directly in the jobqueue-dispatch path.
- Audit all callers of `dispatch_path="jobqueue"` in `daemon/routers/messages.py:119`, `daemon/tools/job_queue.py:473`, `daemon/utils.py:575`, `daemon/services/job_queue_service.py:258`.
- Verify HTTP API contract: `POST /messages` returns the same shape (job_id → message_id mapping must still work).

**This is what the 06f500af bug was a symptom of.** Two coupled work records per message (Task + JobItem) means every state-machine transition (cancel, retry, fail) must update both — and any divergence strands the leader in `waiting_children`. With D13 complete, there is only ONE work record per message and the stale-recovery retry path becomes structurally correct (Task id is the work id; bus watcher is keyed on it; no second row to lose sync with).

### 3. Phase 6 partial — `dispatch_path` parameter still exists on `enqueue_message`

**Status:** `enqueue_message(instance_id, message, ..., dispatch_path: Literal["workerpool", "jobqueue"])` (line 887-895).

**What was claimed:** Phase 6 = "Single enqueue function — no `enqueue_message_via_jq` duplicate". ✅ true — the duplicate function is gone. ❌ but the dispatch_path parameter still selects between two code paths.

**What should land:**
- Remove `dispatch_path` parameter.
- Always write `task` row + `message_queue` row (no JobItem ever).
- The HTTP API's `job_id` response shape needs an adapter: callers that want `job_id` get the `task.id` instead (semantic rename: "work id", not "job id").

**Effort:** ~2 days. Touches public HTTP API contract — needs careful migration.

### 4. Phase 4 column drop — NOT applied

**Status:** Migration `20260621_000002_drop_legacy_completion_columns.sql` exists in `daemon/migrations/versions/` but is marked IRREVERSIBLE / NOT auto-applied (per CHANGELOG: "Manual application required after 2+ weeks of clean bus operation in production").

**What's there now:**
- `Instance.waiting_for` column: still in the model (per `_ensure_postgres_columns` — but reads are gated dead by Phase 3).
- `Instance.children` column: still in the model (same).
- `instance_hierarchy` table: still live (actively queried by `spawn_instance`, `terminate_instance`, `child_reports` per the cleanup plan).

**Risk if applied now:** The cleanup plan (`docs/plans/cleanup-old-architecture.md` §4 Task 4.5) explicitly warns: "The existing migration `20260621_000002` has at line 99: `DROP TABLE IF EXISTS instance_hierarchy;` and recreates it empty at line 125. This table is LIVE. Task 4.5: Remove the `instance_hierarchy` DROP and re-CREATE from the existing migration. **Do NOT create a second migration.**"

So D10's column-drop migration is half-broken out of the gate: the migration that exists would drop the still-live `instance_hierarchy` table. The cleanup plan says don't apply it until Task 4.5 fixes it AND `waiting_for`/`children` reads are removed from all 19 files (324 grep matches per cleanup plan §4 Task 4.1).

**What should land:**
- Fix migration `20260621_000002` per cleanup plan Task 4.5 (remove the `instance_hierarchy` DROP/CREATE).
- Complete the 19-file grep sweep (Task 4.1).
- Extend `_ensure_postgres_drop_legacy_columns()` (Task 4.9) so PostgreSQL gets the ALTER TABLE on startup.
- Then apply.

**Effort:** ~1 week. Most of it is the grep sweep + verifying `instance_hierarchy` queries still work after model change.

### 5. Phase 2 — Lease stub cleanup

**Status:** Per CHANGELOG Phase 8: "Phase 8 FINAL" — should be done. Let me verify.
### 5. Phase 2 — Lease stub cleanup

**Status:** ✅ DONE.

- `grep -rn "LeaseContention\|LeaseLostError\|LeaseHolderKind\|LeaseContentionReason" daemon/ --include="*.py"` = 0 hits in source code (only `__pycache__` artifacts).
- `commit 7d2836cd` collapsed ExecutionGate to `asyncio.Lock`, dropping the 4 Lease stub classes.
- `tests/unit/services/test_execution_gate.py` and `tests/test_resume_gate.py` updated to remove Lease-specific tests.

### 6. `_has_no_active_message_job` defense-in-depth guard

**Status:** Kept (per cleanup plan Phase 6.2 decision: "KEEP the guard, document why").

The guard exists because `JobItem` rows for messages still exist (D13 incomplete). The bus covers `dependency_watchers` (parent→child correlation); the guard covers `job_item` (MESSAGE-worker lifecycle). Once D13 lands and `JobItem` rows are eliminated, the guard becomes redundant and can be removed.

### 7. Cross-instance job handoff

**Status:** Deferred per `unified-dispatcher.md` §10: "Cross-instance job handoff — stays as the only cross-node coordination point; the Dependency Bus emits follow-up needs to run in another node events to it. Step-6 deliverable."

Not blocking; not in current scope. Multi-node deployment is a separate follow-up plan.

---

## Summary table — what the user review flagged

The review comment on commit `d71f6f5e` flagged:

> "MESSAGE vs Job: still coupled — to finish the decoupling you'd land the remaining D11 (job_processor.py:686 branch removal) + D13 (enqueue_message no longer writes a JobItem for messages, goes straight to WorkerPool/Task). The observer's terminal-transition role would then either move onto the Task lifecycle or stay but with no JobItem to update."

**Confirmed correct.** Both items are open work. The commit message for `8d20ffb6` overstated completion; the actual code state matches the review's diagnosis exactly:

| Item | Reviewer claim | Actual code state | Plan ref |
|---|---|---|---|
| D11 (job_processor.py:686 branch) | "still coupled" | Branch present (line 686) | `decouple-execution-plan.md` D11 |
| D13 (enqueue_message writes JobItem) | "still writes JobItem" | True via `dispatch_path="jobqueue"` | `decouple-execution-plan.md` D13 |
| Observer terminal-transition | "no JobItem to update" | Has JobItem to update | `cleanup-old-architecture.md` Phase 5.7 |

---

## Recommended sequencing

These items are tightly coupled — D11 + D13 should land together so that:

1. `enqueue_message` writes only `message_queue` + `task` rows (D13).
2. `enqueue_job` rejects `job_type="message"` (D13).
3. `job_processor.py:686` branch is removed (D11).
4. `TestBusSoleAuthority` is extended to verify the new invariant: "after a user message, exactly one `task` row exists, zero `job_queue_items` rows" (regression test).
5. `_has_no_active_message_job` is reviewed for deletion — the guard's premise (separate `job_item` lifecycle) no longer holds.

Once D11+D13 land:

- The 06f500af-class bug (cancel-and-retry orphan watcher) becomes structurally impossible. The fix I just shipped (`cancel_for_source`) is still correct defense-in-depth, but the structural elimination lands here.
- `dispatch_path` parameter on `enqueue_message` can be removed (Phase 6 completion).
- `_has_no_active_message_job` can be reviewed for removal.
- `job_queue_items` rows for `job_type="message"` go to zero — column `job_queue_items.job_type` may be removable.

**Then** (separate cleanup phase):

- Phase 4 column drop (1 week): `waiting_for`, `children`, and `instance_hierarchy` table removal. The cleanup plan already lists every file and task.
- Documentation refresh (Phase 7 of cleanup plan): remove "bus is default but CM is fallback" framing — bus is sole.
- Final test consolidation (Phase 7): kill `test_kill_switch_legacy_path.py`, `test_correlation_authority_shadow.py`, `test_unified_dispatcher_shadow.py` — they test paths that no longer exist.

## Open follow-up issue

I'd recommend opening a GitHub issue against `latest` titled:

> "Finish D11 + D13: collapse MESSAGE-vs-Job coupling (eliminates 06f500af-class bugs structurally)"

With:
- Acceptance: `grep -rn 'job_type =="message"\|dispatch_path.*jobqueue\|cancel_for_source' daemon/` returns the minimal set (only the new helper, no JobItem writers).
- Acceptance: `tests/postgres/test_legacy_column_drop.py` passes after the migration is repaired per cleanup plan Task 4.5.
- Effort estimate: ~3 days (D11 + D13 + regression tests).
- Risk: touches HTTP API response shape (`job_id` → `task.id`).

---

## Post-Migration Update (2026-06-26)

A follow-up exploration sweep on 2026-06-26 verified that **all items flagged above as "incomplete" have actually landed on `latest`**. This section supersedes the prior "open items" framing — the migration is **COMPLETE**. Use this section as the authoritative status going forward.

### 1. Items previously flagged as incomplete — verified DONE

| Item | Prior status | Verified actual state (2026-06-26) |
|---|---|---|
| `waiting_for` and `children` DB columns dropped | Migration marked NOT auto-applied | Dropped via `_ensure_postgres_drop_legacy_columns()` at `daemon/manager.py`; both columns removed from the SQLModel `Instance` table |
| Migration `20260621_000002` (broken DROP TABLE) | Flagged as half-broken | Fixed — drops only the two legacy columns; no `DROP TABLE instance_hierarchy` remains |
| Dead test files referencing legacy paths | Flagged for deletion | Already deleted: `test_kill_switch_legacy_path.py`, `test_correlation_authority_shadow.py`, `test_unified_dispatcher_shadow.py` |
| "bus default / CM fallback" framing in source | Cleanup plan Phase 7 | Removed — `grep` for that framing in `daemon/` source returns 0 hits |
| `waiting_for` references in daemon source | 324 grep matches per cleanup plan §4 Task 4.1 | Reduced to 2 hits (the ALTER TABLE migration statement itself + one log line); both are inert |
| `.children` attribute reads on Instance | Flagged across 19 files | 0 hits in daemon source |

### 2. D11 + D13 — now structurally complete

The MESSAGE-vs-Job coupling that produced the 06f500af-class bug is **gone**:

- `MessageJobHandler.py` deleted (770 lines, removed in commit `8d20ffb6` / D12).
- `job_processor.py` no longer carries an `if job_type == 'message':` dispatch branch (D11 landed).
- `job_queue_service.enqueue_job` rejects `job_type="message"`; `enqueue_message` writes only `message_queue` + `task` rows — no `JobItem` is created for messages (D13 landed).
- Unified dispatch path: `JobFeedbackObserver → Task → WorkerPool`. All message work flows through this single path.
- `dispatch_path` parameter on `enqueue_message` has been collapsed; one code path, one work record per message.
- `_has_no_active_message_job` defense-in-depth guard is no longer needed (its premise — separate `job_item` lifecycle — no longer holds).

### 3. Known false positives (NOT bugs)

A naive grep sweep will produce hits that look like unfinished cleanup but are unrelated:

- `waiting_for` in `opencode/state.py` (~3 hits) — these are the `waiting_for_input` state **reason** string for the opencode runtime, not the dropped DB column. Unrelated to the migration.
- `.children` (~4 hits across the codebase) — these are **comment-only** references (e.g., "child instances" in docstrings). No attribute reads.

### 4. Final state

The architecture migration is **COMPLETE** end-to-end:

- DependencyBus is the **sole completion authority** for parent→child correlation. No parallel-path remains.
- `waiting_for` and `children` columns are dropped from the DB and from the SQLModel.
- `instance_hierarchy` table is the live parent→child relationship; `dependency_watchers` is the live correlation table.
- `CorrelationManager` is deleted; `MessageJobHandler` is deleted; the `USE_DEPENDENCY_BUS` flag is gone.
- All message work is exactly one `task` row (plus the `message_queue` row); no coupled `JobItem` is ever created.
- The 06f500af-class bug (orphan watcher from cancel-and-retry) is **structurally impossible** under the post-migration design — D13 eliminates the work-record duplication that caused the divergence.

### 5. Open follow-ups (cosmetic / non-blocking)

The only remaining work is cosmetic docstring cleanup — no functional code changes:

- Module docstrings on `daemon/services/dependency_bus.py`, `daemon/repositories/dependency_bus/__init__.py`, `daemon/repositories/dependency_bus/models.py`, `daemon/repositories/dependency_bus/repository.py` still carry Phase D framing.
- A few inline comments at `daemon/services/error_reporting.py:516-526`, `daemon/services/child_reports.py:162-168`, `daemon/services/job_feedback_observer.py:688-700`, and the `JobSystemConfig` docstring at `daemon/config.py:314-364` still reference "Phase D", "rollback path", or "CM shadow validator" language.
- `daemon/api.py:506-522` `init_dependency_bus` docstring still mentions "graceful degradation" — the bus is mandatory, not optional.

These are documentation-only and can be cleaned up in any future docs pass. They do NOT indicate any open functional work.
