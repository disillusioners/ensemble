**STATUS: COMPLETED (2026-06-26) — This is a historical planning document. The migration is complete. See LESSONS/ for final status.**

---

# Plan: Decouple Job / Task / Message / Correlation — Single-Run Delivery

| Field | Value |
|---|---|
| **Status** | REVISED (v2) — single-run delivery for next release |
| **Previous version** | v1 (8 sequential milestones, ~5–6 weeks) — superseded |
| **Mode** | **Aggressive**: all phases M1–M8 land in one release branch. No shadow-mode dwell periods between phases. Feature flags used for safety nets, not for validation campaigns. |
| **Scope** | `daemon/services/correlation_manager.py`, `daemon/services/job_feedback_observer.py`, `daemon/services/child_reports.py`, `daemon/services/message_job_handler.py`, `daemon/services/task_processor.py`, `daemon/services/execution_gate.py`, `daemon/tools/instance.py`, `daemon/services/instance_messaging.py`, `daemon/services/job_processor.py`, `daemon/services/job_queue_service.py`, `daemon/services/instance_lifecycle.py`, `daemon/services/dependency_bus.py` (new) |
| **Bug reference** | `.agents/tester/RESULTS/2026-06-20-job-premature-completion-investigation.md` |
| **Architecture target** | `docs/plans/unified-dispatcher.md` §3–§5 (Dependency Bus + WorkerPool-only execution + asyncio.Lock gate) |
| **Definition of done** | §11 below |

---

## 1. Why single-run

v1 of this plan sequenced M0–M8 over ~5–6 weeks with **explicit shadow-mode dwell periods** between every milestone (M2: 1 week OFF, 1 week ON in dev, 2 weeks ON in prod; M5: 1 week shadow dashboard; M7: 1 sprint dual-running). The dwell periods are valuable but they are calendar time, not engineering time.

For the next release we ship the **entire** M1–M8 scope as one feature branch, with feature flags as safety nets (not as validation campaigns). The release gates move from "metrics show no divergence for N weeks" to **"the test packs in §6 all pass and code review confirms no new coupling points."** The destination architecture is the same; the only thing removed is the dwell time.

**This is the risk the team is taking on:** the three dwell periods (M2, M5, M7) catch real bugs that code review and unit tests miss. Mitigation:

1. The test packs from each phase run on every commit. CI catches regressions a shadow dashboard would catch one week later.
2. Feature flags default OFF in production until release-day flip. If a divergence appears in production post-release, the flag is the rollback (one env-var flip, no rollback deploy needed).
3. The `DEBUG_COMPLETION_INVARIANT` runtime check (from M1c) is ON in production for the first 2 weeks post-release, then turned OFF. It is the safety net that replaces the M2 dwell.

**Out of scope for this revision:** changing the destination architecture, changing the M0 patch (already landed), or changing the test packs.

---

## 2. Phasing within the single run

The 8 milestones from v1 collapse into **4 phases** that are landed in merge order on one branch. Each phase is a single PR. Feature flags are introduced in the phase that needs them and removed by the phase after.

```
Phase A — Authority & visibility      (M1 + M2 in one PR, ~4 days)
    │
Phase B — Close the bug class        (M3, ~1 day; rides Phase A's flag)
    │
Phase C — Single dispatcher           (M4 + M5 + M6 in one PR, ~2 weeks)
    │
Phase D — Dependency Bus & cleanup    (M7 + M8 in one PR, ~1.5 weeks)
```

Total engineering: **~3.5 weeks** (vs. ~5–6 weeks sequential + ~4 weeks dwell in v1).
Total calendar: **~3.5 weeks** (single branch, no dwell).
Risk profile: higher per-PR but bounded by test packs (§6) and feature flags.

### Critical dependencies (preserved from v1)

- **Phase A must precede Phase B.** Adding `watched_jobs` to CM before CM is authoritative re-introduces the three-authority problem.
- **Phase C (especially M6) must follow Phase C-M5.** Collapsing the gate before unifying dispatch re-creates the race that motivated the gate.
- **Phase D must be last.** The Dependency Bus must be the source of truth for completion before we drop the old MESSAGE-job dispatch.

### Feature flags (introduced and removed)

| Flag | Introduced in | Removed in | Purpose | Default at release |
|---|---|---|---|---|
| `USE_LEGACY_WAITING_FOR_CASCADE` | Phase A (M2a) | never (kept as kill switch, removed in v3 cleanup) | Gating the legacy `waiting_for` decrement/cascade | **OFF** in dev/CI/prod |
| `DEBUG_COMPLETION_INVARIANT` | Phase A (M1c) | kept (low-cost runtime check) | Log divergence between CM and `waiting_for` | **ON** in dev/CI, OFF in prod until release day, then **ON** for 2 weeks post-release |
| `USE_LEGACY_JOBQUEUE_DISPATCH` | Phase C (M5a) | Phase C (M5f, same PR) | Legacy JobQueue admission path during shadow | **OFF** immediately after M5f lands |
| `USE_DEPENDENCY_BUS` | Phase D (M7c) | Phase D (M7g, same PR) | Old CM path vs. new bus | **OFF** until release day, then **ON** |

The legacy `waiting_for` cascade flag is the only one kept past release; it is the documented kill switch for the premature-completion bug class.

---

## 3. Phase A — Authority & visibility (M1 + M2)

**Goal:** ADR-011 is enforced in code. The premature-completion bug class is structurally impossible under `USE_LEGACY_WAITING_FOR_CASCADE=OFF`. Divergence between the three authorities is observable.

**Effort:** ~4 days. **One PR.**

**Scope:** `daemon/services/correlation_manager.py`, `daemon/services/child_reports.py`, `daemon/services/job_feedback_observer.py`, `daemon/services/instance_lifecycle.py`, `daemon/tools/instance.py`, `daemon/config.py`, plus docs and tests.

### Deliverables

**A1 — `docs/architecture/completion-authority.md`** (new, ≤200 lines). Contents from v1 §M1-1a verbatim: three authorities table, invariant statement (bold at top), every call site that mutates each authority with rationale and "should this still be here after Phase A?" column, ADR-011 reference, "future authority changes" list.

**A2 — `daemon/config.py`** — add `USE_LEGACY_WAITING_FOR_CASCADE` (env `WAITING_FOR_CASCADE_LEGACY`, default `False`) and `DEBUG_COMPLETION_INVARIANT` (env `DEBUG_COMPLETION_INVARIANT`, default `False` in prod, `True` in dev/CI). Document in `docs/configuration/`.

**A3 — `daemon/services/correlation_manager.py`** — add the `DEBUG_COMPLETION_INVARIANT` runtime check to `resolve_response` and `register_message_send`. On every CM operation, read current `waiting_for` from the same session; log structured warning `event=CM_WAITING_FOR_DIVERGENCE` with `parent_id, child_id, message_id, cm_pending, waiting_for` on mismatch.

**A4 — `daemon/services/child_reports.py`** — in `_process_child_completion_and_notify_parent`, wrap the `waiting_for` SQL decrement and the `if parent.waiting_for == 0: …` cascade branch in `if config.USE_LEGACY_WAITING_FOR_CASCADE:`. When OFF:
- Still calls `notify_corr_resolve` (authoritative).
- Does not write `waiting_for = waiting_for - 1`.
- Uses `cm.get_pending_count(parent_id)` for the cascade decision.
- Does not write `parent.status = WAITING_CHILDREN` (dead branch).

**A5 — `daemon/tools/instance.py`** — in `send_message`, wrap the `waiting_for` SQL increment and the M0 parent-revive `UPDATE` in the same flag. When OFF: only calls `notify_corr_register`; does not write `waiting_for`; does not perform the revive (it's the M0 band-aid).

**A6 — `daemon/services/instance_lifecycle.py`** — wrap the `waiting_for` reset in `pause_instance_cascade` and `resume_instance_cascade` in the same flag. When OFF: leave `waiting_for` alone; CM re-registers in-flight correlations on resume via existing `rebuild_from_db()`.

**A7 — `daemon/services/job_feedback_observer.py`** — **remove** the M0 `WriteGuardSession` re-read of `waiting_for`. Replace with a single `cm.is_complete(instance_id)` call in `_finalize_job`. The re-read was defensive belt-and-braces for the legacy path; under flag-OFF, `cm_pending == waiting_for` always.

**A8 — `docs/architecture/message-processing-and-correlation.md`** — pointer to `completion-authority.md` from §5 ("CorrelationManager in Depth").

**A9 — Test packs:**
- `tests/test_completion_authority_invariant.py` (new, ~10 tests, 2 min). From v1 M1-1b: every `waiting_for` mutation site has matching CM call OR is documented cache-only; every `waiting_for` control-flow read is gated by flag OR is documented cache-only; every `pending_count` read is consistent with CM `is_complete()` at the same site.
- `tests/test_correlation_authority_shadow.py` (new, ~20 tests, 2 min). From v1 M2-2f: full M0 suite under flag OFF (Variants A and C pass); CM `is_complete` returns True iff `waiting_for == 0` and `pending_count == 0` (50 random parent state fixtures); pause/resume with flag OFF preserves CM pending set; `waiting_for` consistent with CM at end of every test (M1 invariant check).

**A10 — `tests/test_premature_completion.py` Variant A and C tests** — assert they pass under `USE_LEGACY_WAITING_FOR_CASCADE=OFF`.

### Acceptance criteria

- The premature-completion bug class is structurally impossible under `USE_LEGACY_WAITING_FOR_CASCADE=OFF`.
- Legacy path preserved as a kill switch.
- M0's band-aid (`WriteGuardSession` re-read, parent-revive UPDATE) is gated and the re-read is removed.
- A developer introducing a new completion source gets a CI failure (A9 invariant pack).
- `DEBUG_COMPLETION_INVARIANT` log lines appear in dev/CI on any divergence; absent means invariant holds.

### Risks

- Hidden `waiting_for` reads outside the flag break silently. Mitigation: M1 invariant pack is the audit; DEBUG env var catches in dev/CI.
- `rebuild_from_db()` must reconstruct CM pending set on resume. Already true per ADR-011; A9 test pack is the proof.

---

## 4. Phase B — Close the bug class (M3)

**Goal:** the `watch_job` path is also routed through CM. All three repro variants from the 2026-06-20 investigation are structurally impossible. **The bug class is closed.**

**Effort:** ~1 day. **One PR.** Builds on Phase A's flag (no new flag needed).

**Scope:** `daemon/services/correlation_manager.py`, `daemon/services/job_feedback_observer.py`, `daemon/tools/job.py` (or wherever `job_continue`/`watch_job` lives — locate via grep).

### Deliverables

**B1 — `daemon/services/correlation_manager.py`** — add `pending_jobs: dict[parent_id, set[child_job_id]]` to `ParentCorrelation` storage. `is_complete(parent_id)` returns True only when both `pending` and `pending_jobs` are empty. `handle_correlation_complete` fires only when both reach zero.

**B2 — `daemon/services/correlation_manager.py`** — add `notify_corr_register_job(parent_id, child_job_id)` and `notify_corr_resolve_job(parent_id, child_job_id)` helpers (CM-API-level, not lifecycle).

**B3 — `daemon/tools/job.py`** (locate `job_continue` + `watch_job` code path) — wrap the `watch_job` follow-up call in `notify_corr_register_job(parent_id=current_instance_id, child_job_id=child_job.job_id)`.

**B4 — `daemon/services/job_feedback_observer.py`** — when the existing terminal-event emission for a watched job (`processing → completed` / `processing → failed`) fires, add `notify_corr_resolve_job(parent_id=parent_instance_id, child_job_id=job.job_id)` call. CM checks `is_complete` and fires callback.

**B5 — `tests/test_watch_job_integration.py`** (new, ~10 tests, 2 min). From v1 M3-3d: Variant B regression test from M0; multiple `watch_job` calls from same parent (only fires when all resolved); watched job that fails → parent's terminal is `error`; watched job cancelled → parent's terminal is `cancelled` (document policy choice).

### Acceptance criteria

- All three repro variants from the 2026-06-20 investigation are structurally impossible.
- `tests/test_premature_completion.py` is fully green.
- The premature-completion bug class is **closed**.

### Risks

- `watch_job` may not have a clear "this is the parent instance" context. Audit call site; confirm `current_instance_id` is the parent. If unclear, file a follow-up rather than guess.

---

## 5. Phase C — Single dispatcher (M4 + M5 + M6)

**Goal:** one enqueue function. JobQueue is scheduling only. WorkerPool is the only execution path. Execution Gate is 40 lines.

**Effort:** ~2 weeks. **One PR** (large).

**Scope:** `daemon/services/instance_messaging.py`, `daemon/services/job_queue_service.py`, `daemon/services/job_feedback_observer.py`, `daemon/services/job_processor.py`, `daemon/services/message_job_handler.py`, `daemon/services/execution_gate.py`, `daemon/repositories/execution_lease/` (delete), migration `20260614_000002_create_instance_execution_leases.sql` (drop), `daemon/services/message_processing_pipeline.py` (or post-M5 `MessageTaskProcessor`).

### Deliverables

#### C-M4: alias, don't fork

**C1 — `daemon/services/instance_messaging.py`** — `enqueue_message_via_jq` becomes a thin wrapper of `enqueue_message` with `metadata={"dispatch_path": "legacy_jq"}` tag. Add `DeprecationWarning` log on the wrapper, gated on `LOG_LEVEL >= INFO`. No behavior change.

**C2 — `tests/test_dispatcher_path_equivalence.py`** (new, ~10 tests, 2 min). Runs the same scenario 100 times through each entry point (HTTP, agent tool, child completion report, error report, source, scheduler) and asserts identical observable behavior: identical DB rows, identical SSE events, identical final instance/job status, identical `MessageQueue` row, identical `Task` row, identical `metadata["dispatch_path"]` tag.

**C3 — `tests/test_dispatcher_path_invariants.py`** (new, 1 test, 30s). Greps entire `daemon/` tree for `enqueue_message_via_jq(`; asserts the only call site is the wrapper itself in `daemon/services/job_queue_service.py`. Fails build on any new direct call.

**C4 — `docs/architecture/message-processing-and-correlation.md`** Section 4 ("How a Message Flows") updated to reflect unified entry point. Mark previous "Two physical dispatchers" text as historical. Link to `docs/plans/unified-dispatcher.md` §5.2.

#### C-M5: route JobQueue admission through observer

**C5 — `daemon/config.py`** — add `USE_LEGACY_JOBQUEUE_DISPATCH` (default `False`). The flag exists for the duration of M5 only; removed in C11.

**C6 — `daemon/services/job_feedback_observer.py`** — extend `JobFeedbackObserver` to handle the **local-admission path** (today only handles cross-instance handoff). When `JobProcessor` admits a `JobItem` of `job_type='message'`:
- Observer writes a `Task` row pointing at the same `message_id`.
- Observer calls `worker_pool.notify_work()`.
- `JobItem` is marked `PROCESSING` (status only — execution is in the Task table).
- This is the only path that writes a `Task` row for message work.

**C7 — `daemon/services/job_processor.py`** — under `USE_LEGACY_JOBQUEUE_DISPATCH=ON`: keep current behavior (calls `MessageJobHandler.handle` for local work). Under `OFF`: call observer (C6).

**C8 — `daemon/services/message_job_handler.py`** — demote `handle` to no-op for the local path. File not deleted yet (that's Phase D-M8). Remains a thin adapter: delegates to observer for local work, handles cross-instance handoff for remote work.

**C9 — Structured-log metric `dispatch_path`** on every relevant log line:
- `dispatch_path=jobqueue_local` for work admitted by `JobProcessor` through the observer.
- `dispatch_path=jobqueue_cross_node` for work bounced from another node.
- `dispatch_path=workerpool_direct` for work via `enqueue_message` (sources, scheduler) without a `JobItem` row.

**C10 — `tests/test_unified_dispatcher_shadow.py`** (new, ~15 tests, 5 min). With `USE_LEGACY_JOBQUEUE_DISPATCH=OFF`, asserts observer's path produces the same result for 50 randomized scenarios; `JobItem` rows for `job_type='message'` transition PROCESSING → COMPLETED with Task table as source of truth; cross-instance handoff unaffected (runs both flag states, cross-instance path must work in both).

**C11 — After C10 passes, flip the flag permanently:** `MessageJobHandler` becomes purely cross-instance handoff; `JobProcessor` no longer calls `MessageJobHandler.handle` for local work; `USE_LEGACY_JOBQUEUE_DISPATCH` flag is removed from `daemon/config.py`.

#### C-M6: collapse gate to asyncio.Lock

**C12 — `daemon/services/execution_gate.py`** — replace DB-backed lease with the in-process `asyncio.Lock` implementation from `docs/plans/unified-dispatcher.md` §5.4:
```python
class ExecutionGateService:
    def __init__(self):
        self._locks: dict[str, asyncio.Lock] = {}
        self._locks_guard = threading.Lock()

    def _lock_for(self, instance_id: str) -> asyncio.Lock:
        with self._locks_guard:
            lock = self._locks.get(instance_id)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[instance_id] = lock
            return lock

    async def run(self, instance_id, holder_id, holder_kind, work_fn):
        lock = self._lock_for(instance_id)
        async with lock:
            return await work_fn()
```
Note: signature keeps `holder_id` and `holder_kind` for now (call sites don't need to change); they're ignored in the body. Removed in a later cleanup if desired.

**C13 — Delete:**
- `recover_stale_leases` startup call
- `LeaseContention` exception
- `LeaseLostError` exception
- `_lease_heartbeat_loop` background task
- heartbeat escalation logic
- `LeaseHolderKind` enum
- `instance_execution_leases` table migration (`20260614_000002_create_instance_execution_leases.sql`)
- `daemon/repositories/execution_lease/` directory

**C14 — `daemon/services/message_processing_pipeline.py` (or `MessageTaskProcessor` post-C7)** — update the one surviving call site to `async with self._gate._lock_for(instance_id): await work_fn()`.

**C15 — `daemon/services/execution_gate.py` module docstring** (verbatim from v1 M6-6e):
> *This gate serializes `_process_message_with_tracking` per instance within a single process. It is an in-process `asyncio.Lock`; there is no cross-process coordination. If you deploy the daemon across multiple processes/nodes, this gate WILL NOT prevent the same instance from being driven concurrently. To re-enable cross-process safety, wrap this class in a `MultiProcessExecutionGate` strategy that adds a DB-backed lease (see commit history for the prior implementation; the migration `20260614_000002_create_instance_execution_leases.sql` was dropped and would need to be re-applied).*

**C16 — `docs/architecture/message-processing-and-correlation.md` Section 6** ("ExecutionGate in Depth") updated to reflect new implementation.

**C17 — Run full `concurrency_atomic_unit_test` pack (86 tests, per `PACKS.md`) and `test_cross_dispatcher_*` race tests from `docs/bugs/`.** All must pass. If any fail, the unification isn't actually single-dispatcher yet; do not ship.

### Acceptance criteria

- One enqueue function in the codebase (C3 grep test enforces).
- JobQueue is scheduling layer, not execution layer.
- WorkerPool is the only execution path.
- ~660 lines removed from execution_gate + repos.
- `concurrency_atomic_unit_test` 86/86 pass.
- `cross_dispatcher_*` race tests pass.

### Risks

- C-M5 hot loop on the observer (cross-instance handoff was designed for rare events). Mitigation: profile; add per-instance admission throttling if hot.
- C-M6 re-creates the original race if C-M5 is incomplete. **C17 is the gate.** Do not merge C-M6 until C-M5f (C11) is done.
- `MessageJobHandler`'s pause/terminate discrimination may differ from `ProcessMessageProcessor`'s. C2's equivalence test + `correlation_atomic_unit_test` (86 tests) are the gating tests. Add a pause/terminate matrix test before C-M5 ships.

---

## 6. Phase D — Dependency Bus & cleanup (M7 + M8)

**Goal:** "parent is waiting for N children" is expressed as watcher relationships on a bus. The bus survives restart. Job system is scheduling vocabulary only. Architecture matches the unified-dispatcher destination.

**Effort:** ~1.5 weeks. **One PR.**

**Scope:** new `daemon/services/dependency_bus.py`, new `dependency_watchers` table, deprecation of CM `(child_id, message_id)` correlation set, drop `Instance.waiting_for` column, drop `Instance.children` denormalized JSON cache, drop `instance_hierarchy` table, remove `job_type='message'` dispatch.

### Deliverables

#### D-M7: Dependency Bus

**D1 — `daemon/services/dependency_bus.py`** (new) — API from `docs/plans/unified-dispatcher.md` §5.6:
```python
class DependencyBus:
    async def watch(self, source_task_id: str, follow_up: FollowUp) -> None: ...
    async def emit_terminal(self, task_id: str, outcome: Outcome) -> None: ...
    async def pending_watchers(self, source_task_id: str) -> list[FollowUp]: ...
```
- `watch` called from `send_message` (parent registers itself as watcher of child's task).
- `emit_terminal` called from `MessageTaskProcessor.process` when task reaches terminal event.
- `pending_watchers` returns FollowUps to enqueue when source task completes.

**D2 — Migration `20260620_000001_create_dependency_watchers.sql`** (new). Columns: `watch_id`, `source_task_id`, `target_instance_id`, `follow_up_payload` (JSON), `metadata` (JSON: `kind`, `child_id`, etc.), `created_at`, `fired_at` (nullable), `state` (PENDING, FIRED, CANCELLED).

**D3 — `daemon/config.py`** — add `USE_DEPENDENCY_BUS` (default `False`).

**D4 — Build the parent-waits-for-children flow on the new bus, behind `USE_DEPENDENCY_BUS=1`.** The old CM path keeps running in parallel only while the flag is being validated by D9; once D9 passes, the flag is flipped permanently.

**D5 — `daemon/tools/instance.py:send_message`** under flag ON:
- Writes the `Task` row for the child as today.
- Writes a `dependency_watchers` row (FollowUp) with `source_task_id=child_task.id`, `target_instance_id=parent_id`, pre-built message content.
- Does **not** call `notify_corr_register` (bus replaces it).

**D6 — `daemon/services/task_processor.py` (`MessageTaskProcessor` post-Phase C)** under flag ON:
- On terminal event, calls `bus.emit_terminal(task_id, outcome)`.
- Bus fires all pending watchers, enqueuing their FollowUps as new Tasks.
- Does **not** call `notify_corr_resolve` (bus replaces it).

**D7 — Structured-log metric `completion_delivery_path=cm|bus`** on every relevant log line. Both paths must agree on the answer for every test scenario (D9 enforces this in tests; in production there is no shadow dwell, so this is a CI-only check).

**D8 — After D9 passes, flip `USE_DEPENDENCY_BUS=ON` permanently and remove the CM `register_message_send` / `resolve_response` calls.** Keep CM class for one more release as a shadow validator (verifies bus behavior matches CM's would-have-been behavior); removal is deferred to a follow-up cleanup.

**D9 — `tests/test_dependency_bus.py`** (new, ~30 tests, 5 min):
- Bus watcher semantics: 1 parent, 3 children, all complete → parent's follow-up enqueued exactly once, even with duplicate child completions.
- The `waiting_for` double-decrement bug class is gone (bus has no counter).
- Bus survives restart: write a watcher, simulate crash (in-memory state cleared, DB state preserved), restart, emit terminal — watcher fires correctly.
- Bus cancellation: terminate a parent whose children have pending watchers; bus marks watchers CANCELLED, does not enqueue FollowUps.
- Bus backpressure: 10,000 watchers on a single task → bus emits one at a time (no thundering herd).
- **Shadow-equivalence tests:** for every fixture in `tests/test_correlation_manager_unit_test.py` (40 tests, per `PACKS.md`), assert that running with `USE_DEPENDENCY_BUS=ON` produces identical observable behavior (same DB rows, same SSE events, same final instance/job status) as running with `USE_DEPENDENCY_BUS=OFF`. This replaces the v1 "1 sprint of dual-running" with a CI gate.

**D10 — Migration `20260620_000002_drop_legacy_completion_columns.sql`** (new, reversible). Drops `Instance.waiting_for`, `Instance.children` denormalized JSON cache, `instance_hierarchy` table. Reversible (drops columns, recreates as NULL, no data loss).

#### D-M8: drop MESSAGE dispatch and finalize docs

**D11 — `daemon/services/job_processor.py`** — remove `job_type='message'` branch. Job system no longer dispatches message work; only schedules it. `JobItem` rows for message work are no longer written; only `Task` rows are.

**D12 — `daemon/services/message_job_handler.py`** — delete (already shrunk to cross-instance handoff in Phase C; if Phase C left any cross-instance handoff here, move it to `job_feedback_observer.py` and delete the file).

**D13 — `daemon/services/job_queue_service.py`** — remove MESSAGE-specific helpers.

**D14 — `docs/architecture/message-processing-and-correlation.md` and `docs/architecture/job-task-pause-resume.md`** — final updates reflecting the new shape.

**D15 — `docs/architecture.md`** — add one-page summary at the top (verbatim from v1 M8-8d):
> *The daemon has a unified dispatcher (the WorkerPool) and a scheduling layer (the JobQueue). All "I want `graph.astream` to run for instance X" requests go through the same code path: `manager.enqueue_message(...)` → `MessageQueue` row + `Task` row → WorkerPool claim → gate → `_process_message_with_tracking`. The JobQueue owns the scheduling vocabulary (queues, priorities, concurrency, dead-letter, retries). Completion authority is the Dependency Bus. The pre-Milestone 0 architecture (two dispatchers, three completion authorities, DB-backed gate) is described in `docs/plans/unified-dispatcher.md` and `docs/bugs/`.*

**D16 — `CHANGELOG.md`** — add entry: "Premature-completion bug class closed at Phase B (2026-…); architecture cleanup completed at Phase D (2026-…); single-run delivery consolidated M1–M8."

### Acceptance criteria

- "Parent is waiting for N children" is one mechanism (the bus), not three (counter + completion-report-as-message + CM dict).
- Bus survives restart (D9 restart test).
- All `tests/test_premature_completion.py` tests pass.
- Job system has a single role (scheduling).
- WorkerPool has a single role (execution).
- Dependency Bus has a single role (completion authority).
- Docs match code.
- CHANGELOG updated.

### Risks

- D-M7 is the largest behavioral change in the plan. D9's shadow-equivalence tests against `correlation_manager_unit_test` are the safety net that replaces v1's "1 sprint of dual-running." If D9 fails on any of the 40 fixtures, do not flip the flag.
- Bus persistence layer must support high-concurrency inserts and reads. Use existing `WriteGuardSession` pattern.
- `Task` table grows unboundedly with the new "every admission writes a Task" pattern. Existing `StaleTaskRecovery` and `DeadLetterService` cover this.

---

## 7. Test pack summary (consolidated)

Test packs from v1 land as part of their phase; here is the consolidated table for CI:

| Pack | Phase | Type | Tests (approx) | Timeout | Last-failing-on-main? |
|---|---|---|---|---|---|
| `premature_completion_regression_test` | M0 (already done) | Unit | 3 | 1 min | Yes (3/3 on main, 1/3 on M0) |
| `completion_authority_invariant_test` | A (M1) | Unit | 10 | 2 min | Yes (audit on main) |
| `correlation_authority_shadow_test` | A (M2) | Unit | 20 | 2 min | After Phase A lands, no |
| `watch_job_integration_test` | B (M3) | Unit | 10 | 2 min | After Phase B, no |
| `dispatcher_path_equivalence_test` | C (M4) | Unit | 10 | 2 min | No (wrapper) |
| `dispatcher_path_invariants_test` | C (M4) | Unit | 1 | 30s | No (grep) |
| `unified_dispatcher_shadow_test` | C (M5) | Unit | 15 | 5 min | After Phase C-M5, no |
| `dependency_bus_test` | D (M7) | Unit | 30 | 5 min | After Phase D-M7, no |
| `concurrency_atomic_unit_test` | C (M6) | Unit | 86 | — | Gate against C-M6 |
| `correlation_manager_unit_test` | every | Unit | 40 | — | Regress-check at every phase |
| `correlation_shadow_integration_test` | A | Integration | 8 | — | Shadow-mode coverage |

All packs registered in `.agents/tester/PACKS.md` in their phase's PR.

---

## 8. Release & rollback plan

### Release order

1. Merge all four phases (A, B, C, D) to `feature/single-run-decouple` branch in order. CI is green on each.
2. Open one release-tracking issue listing the four PRs and the four feature flags (`USE_LEGACY_WAITING_FOR_CASCADE`, `DEBUG_COMPLETION_INVARIANT`, `USE_LEGACY_JOBQUEUE_DISPATCH` (removed pre-release), `USE_DEPENDENCY_BUS`).
3. Cut a release branch `release/<version>`. Deploy to staging.
4. Staging runs for 48 hours with `DEBUG_COMPLETION_INVARIANT=ON`. Any divergence is a release blocker.
5. Promote to production with:
   - `USE_LEGACY_WAITING_FOR_CASCADE=OFF`
   - `DEBUG_COMPLETION_INVARIANT=ON` for the first 2 weeks
   - `USE_DEPENDENCY_BUS=OFF` (this flag flips in D8, which happens during the phase D PR; if PR is staged, flip happens at deploy time)
6. 2 weeks post-release: turn off `DEBUG_COMPLETION_INVARIANT` if no divergence logs appeared.

### Rollback

Each phase is independently reversible. Feature flags are the **first** rollback tool:

| Symptom | Flag flip | Effect |
|---|---|---|
| Premature-completion regressions | `USE_LEGACY_WAITING_FOR_CASCADE=ON` | Reverts to M0 band-aid path (still better than v0) |
| `watch_job`-driven regressions | `USE_LEGACY_WAITING_FOR_CASCADE=ON` (re-enables legacy `_process_child_completion_and_notify_parent` which still runs `WAITING_CHILDREN` cascade) | Loses M2 invariant but restores pre-Phase-A behavior |
| Dependency Bus divergence | revert Phase D PR | Bus is the only mechanism in Phase D; revert restores CM + `waiting_for` |
| Execution gate race | revert Phase C-M6 PR | Restores DB-backed lease; `execution_gate.py` and `instance_execution_leases` table are restored by revert |

If a feature flag is not enough, revert the corresponding PR. The four-PR structure means a single revert restores one phase without touching the others.

### What's intentionally not in the release

- v1's dwell periods (1 week shadow on M2, 1 week on M5, 1 sprint on M7).
- The follow-up cleanup of the `USE_LEGACY_WAITING_FOR_CASCADE` kill switch (kept for one release; removed in v3).
- The CM class as a "shadow validator for one more release" (D8).

---

## 9. Risks (consolidated from v1, scoped to single-run)

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **Hidden divergence surfaces in production** that v1's dwell periods would have caught | Medium | High | `DEBUG_COMPLETION_INVARIANT=ON` for 2 weeks post-release is the safety net. Phase A's invariant test pack is the audit. Phase C's C17 (`concurrency_atomic_unit_test` 86/86) is the gate. |
| **Phase C-M6 re-creates the gate race** if M5 is incomplete | Medium | High | C17 gate. Do not merge C-M6 until C-M5 (C11) is done. |
| **Phase D-M7 CM↔Bus divergence** that v1's 1-sprint dual-running would have caught | Medium | High | D9's shadow-equivalence tests against the 40 `correlation_manager_unit_test` fixtures replace the dwell period. CI gate, not calendar gate. |
| **`MessageJobHandler` pause/terminate discrimination** lost in merge | Medium | Medium | C2 equivalence test + `correlation_atomic_unit_test` are the gating tests. Add pause/terminate matrix test before C-M5 ships. |
| **Feature flag rollout mistakes** (wrong default, forgotten env var) | Low | High | Pre-release staging run with all flags explicitly set; release-tracking issue lists every flag and its expected value. |
| **Other callers of `enqueue_message`** outside the agent path silently get a different code path | Low | Low | C3 grep test catches new direct callers. Signature is unchanged. |
| **`Task` table grows unboundedly** with new "every admission writes a Task" | Low | Low | Existing `StaleTaskRecovery` and `DeadLetterService` cover this. |
| **Phase D bus persistence layer** doesn't support high concurrency | Low | Medium | Use existing `WriteGuardSession` pattern. Load test in D9's backpressure test. |
| **Cross-process deployment** uses the in-process lock and corrupts state | Low now, High later | High | C15 module docstring explicitly documents limitation. Multi-node deployment is a follow-up plan; this release does not enable it. |

---

## 10. What this plan is *not* (unchanged from v1)

- Not the unified-dispatcher plan restated. This is the step-by-step path that gets there in one run.
- Not a rewrite of `_process_message_with_tracking`, the SSE pipeline, the langgraph core, or the LLM streaming path.
- Not a multi-node deployment plan. The DB-backed lease is *downgraded*, not removed as a concept.
- Does not fix every bug in `docs/bugs/`. It fixes the premature-completion class and the two-dispatcher coupling.
- Not a "delete CM" plan. CM is removed from the hot path in Phase D-M7 but the class is kept as a shadow validator (per D8) until a follow-up cleanup.

---

## 11. Definition of done

This plan is done when:

1. The premature-completion bug class is **structurally impossible** (closed at Phase B, reinforced at Phase D-M7).
2. The codebase has **one dispatcher** (WorkerPool, closed at Phase C-M5), **one scheduling layer** (JobQueue, scheduling vocabulary only at Phase D-M8), and **one completion authority** (Dependency Bus, closed at Phase D-M7).
3. The Execution Gate is **~40 lines**, not ~700 (closed at Phase C-M6).
4. Three documented repro variants from the 2026-06-20 investigation are regression-tested in `tests/test_premature_completion.py` and registered in `PACKS.md`.
5. ADR-011 is enforced in code: `waiting_for` is no longer a control-flow value (closed at Phase A-M2).
6. All test packs in §7 are green on the release branch.
7. Docs match code: `docs/architecture/message-processing-and-correlation.md`, `docs/architecture/job-task-pause-resume.md`, and `docs/architecture.md` reflect the final architecture.
8. CHANGELOG entry added.
9. Release-tracking issue lists all feature flags and their expected values at deploy.

---

## 12. Appendix — file-level change list (consolidated)

### Phase A (M1 + M2)
- `docs/architecture/completion-authority.md` — new.
- `daemon/services/correlation_manager.py` — add `DEBUG_COMPLETION_INVARIANT` check.
- `daemon/config.py` — add `USE_LEGACY_WAITING_FOR_CASCADE`, `DEBUG_COMPLETION_INVARIANT`.
- `daemon/services/child_reports.py` — gate `waiting_for` decrement + cascade decision.
- `daemon/tools/instance.py` — gate M0's parent-revive and `waiting_for` increment.
- `daemon/services/instance_lifecycle.py` — gate `waiting_for` reset in pause/resume.
- `daemon/services/job_feedback_observer.py` — remove M0 `WriteGuardSession` re-read.
- `docs/architecture/message-processing-and-correlation.md` — pointer to authority doc.
- `tests/test_completion_authority_invariant.py` — new.
- `tests/test_correlation_authority_shadow.py` — new.
- `tests/test_premature_completion.py` — Variant A and C updated for flag.

### Phase B (M3)
- `daemon/services/correlation_manager.py` — `pending_jobs` dict; `is_complete` checks both.
- `daemon/services/correlation_manager.py` — `notify_corr_register_job`, `notify_corr_resolve_job`.
- `daemon/services/job_feedback_observer.py` — call `notify_corr_resolve_job` on terminal event.
- `daemon/tools/job.py` — call `notify_corr_register_job` on `watch_job`.
- `tests/test_watch_job_integration.py` — new.

### Phase C (M4 + M5 + M6)
- `daemon/services/instance_messaging.py` — `enqueue_message_via_jq` → thin wrapper.
- `daemon/config.py` — add (then remove) `USE_LEGACY_JOBQUEUE_DISPATCH`.
- `daemon/services/job_feedback_observer.py` — add local-admission path.
- `daemon/services/job_processor.py` — call observer on local admission.
- `daemon/services/message_job_handler.py` — demote to cross-instance handoff only.
- `tests/test_dispatcher_path_equivalence.py` — new.
- `tests/test_dispatcher_path_invariants.py` — new.
- `tests/test_unified_dispatcher_shadow.py` — new.
- `daemon/services/execution_gate.py` — collapse to in-process `asyncio.Lock` (~40 lines) + module docstring.
- `daemon/repositories/execution_lease/` — delete.
- `20260614_000002_create_instance_execution_leases.sql` — drop.
- `daemon/services/message_processing_pipeline.py` (or `MessageTaskProcessor`) — update call site.
- `docs/architecture/message-processing-and-correlation.md` — update §4 and §6.

### Phase D (M7 + M8)
- `daemon/services/dependency_bus.py` — new.
- `20260620_000001_create_dependency_watchers.sql` — new.
- `daemon/services/task_processor.py` (`MessageTaskProcessor`) — call `bus.emit_terminal` on terminal.
- `daemon/tools/instance.py` — under `USE_DEPENDENCY_BUS`, write `dependency_watchers` row.
- `daemon/config.py` — add (then keep) `USE_DEPENDENCY_BUS`.
- `20260620_000002_drop_legacy_completion_columns.sql` — drop `waiting_for`, `children`, `instance_hierarchy`.
- `tests/test_dependency_bus.py` — new.
- `daemon/services/job_processor.py` — remove `job_type='message'` branch.
- `daemon/services/message_job_handler.py` — delete.
- `daemon/services/job_queue_service.py` — remove MESSAGE-specific helpers.
- `docs/architecture/message-processing-and-correlation.md` — final update.
- `docs/architecture/job-task-pause-resume.md` — final update.
- `docs/architecture.md` — add one-page summary.
- `CHANGELOG.md` — add entry.

---

## 13. Mapping to v1 (for reviewers cross-checking)

| v1 milestone | v2 phase | Notes |
|---|---|---|
| M0 (done) | — | Already landed. |
| M1 | Phase A | Same deliverables, combined with M2. |
| M2 | Phase A | Same deliverables, shadow dwell replaced by `DEBUG_COMPLETION_INVARIANT` for 2 weeks post-release. |
| M3 | Phase B | Unchanged. |
| M4 | Phase C | Unchanged. |
| M5 | Phase C | Shadow dwell replaced by CI shadow-equivalence tests. Flag is introduced and removed in the same PR. |
| M6 | Phase C | Unchanged. |
| M7 | Phase D | 1-sprint dual-running replaced by D9 shadow-equivalence tests against `correlation_manager_unit_test`. |
| M8 | Phase D | Unchanged. |
| §11 open question (unified-dispatcher confirmation) | resolved | Confirmed by user in plan revision. |