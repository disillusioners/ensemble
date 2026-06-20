# Plan: Decouple Job / Task / Message / Correlation — Phased Architecture Improvement

| Field | Value |
|---|---|
| **Status** | DRAFT (v1) — addresses the premature-completion bug class and the deeper two-dispatcher coupling |
| **Scope** | `daemon/services/correlation_manager.py`, `daemon/services/job_feedback_observer.py`, `daemon/services/child_reports.py`, `daemon/services/message_job_handler.py`, `daemon/services/task_processor.py`, `daemon/services/execution_gate.py`, `daemon/tools/instance.py`, `daemon/services/instance_messaging.py` |
| **Estimated effort** | ~5–6 weeks sequential for one engineer (M0–M8) |
| **Bug reference** | `.agents/tester/RESULTS/2026-06-20-job-premature-completion-investigation.md` |
| **Related docs** | `docs/architecture/message-processing-and-correlation.md`, `docs/architecture/job-task-pause-resume.md`, `docs/queue-architecture-review.md`, `docs/plans/unified-dispatcher.md`, `docs/bugs/unresolved/symmetric-cross-system-race-messagejobhandler-ignores-running-tasks.md` |
| **Destination** | This plan reaches the architecture described in `docs/plans/unified-dispatcher.md` §3–§5 incrementally, milestone by milestone |

---

## 1. Why this plan exists

A production investigation on 2026-06-20 (`.agents/tester/RESULTS/2026-06-20-job-premature-completion-investigation.md`) confirmed that parent **JOBs** transition to `completed` while their child **instances** are still running. The investigation concluded — and the working-tree patch on `bugfix/job-premature-complete` confirms in code — that this is a class of bug caused by **two decoupled completion tracks**:

1. **Instance completion track** (`child_reports._process_child_completion_and_notify_parent`) — decrements a `waiting_for` counter on the parent `Instance` row; defers instance terminal when `waiting_for > 0`. Works correctly.
2. **Job finalization track** (`correlation_manager` → `job_feedback_observer`) — tracks `(child_id, message_id)` triples in an in-memory dict; fires `handle_correlation_complete` when its pending set reaches zero; the observer finalizes the JOB (and via it, releases locks, calls `_trigger_next_job`). **Not gated on instance `waiting_for` or `pending_count`.**

A second, related defect is structural: two physical dispatchers (`JobQueue` + `WorkerPool`) converge on the same `graph.astream` call, currently held together by the DB-backed Execution Gate as a "mutex between two unrelated dispatchers." This is already documented in `docs/plans/unified-dispatcher.md` as the long-term destination.

The current uncommitted patch (a `waiting_for` re-read inside `WriteGuardSession` + a parent-revive in `send_message`) is a **defensive band-aid**. It closes the TOCTOU window for **one of three** repro paths (Variant A — multi-wave spawn). It does not address Variant B (`watch_job`) or Variant C (queued messages), it does not address the legacy `waiting_for` cascade in `child_reports`, it does not address the CM restart hole, and it does not move the system toward the unified-dispatcher destination.

**This plan delivers "easy first, archive good result" by sequencing small, individually-reversible milestones that each:**

- leave the system in a working state,
- ship a test pack that fails on `main` and passes on the branch,
- strictly improve the architecture (never add a new coupling point, never add a new band-aid site).

The plan's central principle: **the premature-completion bug class is closed at Milestone 3 (week 1)**. The remaining milestones are the architecture cleanup that prevents the next bug class from being a variant of the same thing.

---

## 2. Diagnosis (one paragraph, restated)

The codebase has three overlapping, partially-decoupled completion authorities with direct DB state coupling and no enforced invariants between them:

| Authority | Location | Storage | Authoritative per ADR-011? |
|---|---|---|---|
| `waiting_for` counter | `Instance.waiting_for` (DB column) | SQLite/PG column, written by SQL text in `instance.py:570+` and `child_reports.py:512+` | **No — rebuild cache only** |
| `CorrelationManager` pending set | `_pending[parent_id]` | In-memory dict, single source of truth for *correlation* | **Yes** for correlation; but does not see all event sources |
| `pending_count` | `MessageQueue.status` count query | DB read at three different sites with different gating semantics | **No — derived view only** |

There is **no code anywhere** that asserts these three are equal at any point in time. ADR-011 names CM authoritative, but the legacy `waiting_for` cascade still runs in `child_reports._process_child_completion_and_notify_parent` and still drives the `WAITING_CHILDREN` status. The two systems can — and do — disagree.

The structural defect: two physical dispatchers (JobQueue + WorkerPool) hold no shared state machine, no shared lock, no shared pre-flight. The Execution Gate is a 700-line DB-backed mutex specifically to keep them from corrupting langgraph checkpoints. The cost of this duplication is enumerated in `docs/plans/unified-dispatcher.md` §12.

---

## 3. Guiding principles for every milestone

1. **No new band-aid sites.** The working-tree patch adds a `waiting_for` re-read inside `WriteGuardSession`. Every milestone in this plan must **remove** a coupling point, never add a re-read of the same column as a new one.
2. **No big-bang rewrites.** Each milestone is reversible in a single PR. The Execution Gate stays as the safety belt through every milestone until Milestone 6.
3. **Each milestone ships a test pack** that fails on `main` and passes on the branch (matches `.agents/tester/PACKS.md` culture).
4. **All three repro variants** (A: multi-wave, B: `watch_job`, C: queued messages) must be regression-tested in the milestone that closes them.
5. **Move toward ADR-011 compliance, not away from it.** ADR-011 says `waiting_for` is a rebuild cache, not a control-flow value. Milestones must end with strictly fewer `waiting_for`-for-control-flow reads than they started with.
6. **Single dispatcher is the destination.** All dispatch-side milestones assume `docs/plans/unified-dispatcher.md` §3–§5 is the right end state. Open question for the user is captured in §11.

---

## 4. Milestone sequencing

```
M0 (defensive patch + repro tests)             [0.5 day]
   ↓
M1 (document invariant + expose divergence)    [1 day]
   ↓
M2 (CM is sole authority, shadow mode)         [3 days]
   ↓ ──→ M3 (watch_job → CM)                   [1 day]
            ↓
M4 (alias enqueue, don't fork)                 [3 days]
   ↓
M5 (route JobQueue admission through observer) [1 week]
   ↓
M6 (collapse gate to asyncio.Lock)             [2 days]
   ↓
M7 (Dependency Bus, behind feature flag)        [1 week]
   ↓
M8 (drop MESSAGE job_type, final docs)         [1 day]
```

Total: **~5–6 weeks sequential for one engineer**. The bug class is closed at M3 (week 1). Milestones M4–M8 are the architecture cleanup.

### Critical dependencies

- **M2 must precede M3.** Adding `watched_jobs` to CM before CM is authoritative is a third concurrent authority. Same bug class, different code path.
- **M5 must precede M6.** Collapsing the gate before unifying dispatch re-creates the exact race that motivated the gate (commit `46cf524`, `docs/bugs/unresolved/symmetric-cross-system-race-...`).
- **M7 must precede M8.** The Dependency Bus must be the source of truth for completion before we drop the old MESSAGE-job dispatch (otherwise we lose the cross-instance handoff that the bus is replacing).

---

## 5. Milestones — detailed

### Milestone 0 — Stabilize and capture the bug

**Goal:** stop production bleeding, prove the work ahead.

**Effort:** 0.5 day.

**Scope:**
- The current uncommitted patch in working tree:
  - `daemon/services/job_feedback_observer.py` — adds a `waiting_for > 0` re-read inside `WriteGuardSession` to abort terminal transition.
  - `daemon/tools/instance.py` — adds a "revive prematurely-COMPLETED parent" `UPDATE` in `send_message`.
- New tests for all three repro variants.

**Deliverables:**

- **0a. Land the current uncommitted patch as a *known-incomplete* defensive commit** on a `bugfix/job-premature-complete` branch. Do **not** describe it as a fix in the commit message; describe it as a "Variant A backstop, Variants B/C open." Link to the investigation report in the commit body.

- **0b. Add `tests/test_premature_completion.py`** with three regression tests, one per variant from the investigation:
  - `test_variant_a_multiwave_spawn` — parent spawns 2 children, first wave acks, parent spawns 2 more (wave 2) before wave 1's last response was fully processed. Asserts: parent JOB is not finalized before all 4 children complete. Currently fails on `main` (variant A fires). After M2 this test must pass **and** be redundant (M2 closes it structurally).
  - `test_variant_b_watch_job` — parent calls `job_continue` + `watch_job` for a long-running child job, then produces a final text response. Asserts: parent JOB stays in `processing` until the watched child job completes. Currently fails on `main` and **also fails on the M0 patch** (patch does not cover Variant B).
  - `test_variant_c_queued_message` — two human messages arrive for a root instance in quick succession; first processes, second queues; first message processes `waiting_for=0` + `pending_count=1`. Asserts: parent JOB stays in `processing` until both messages are delivered. Currently fails on `main`; fails on the M0 patch.

- **0c. Register the test pack in `.agents/tester/PACKS.md`** as `premature_completion_regression_test` (Unit, 1 min timeout, 3 tests). Add a tracker entry showing 3 fails on `main`, 1 passes on M0, all 3 pass on M3.

- **0d. Update `.agents/tester/RESULTS/2026-06-20-job-premature-completion-investigation.md`** with an "M0 status" section recording what the patch covers and what it does not.

**Acceptance criteria:**
- Production has a backstop for Variant A.
- Test pack exists and runs in CI.
- Three failing tests are visible to the team as "the work ahead."

**Out of scope:** fixing Variant B or Variant C. That is M3.

---

### Milestone 1 — Document the invariant and expose the divergence

**Goal:** divergence between the three completion authorities is detectable.

**Effort:** 1 day.

**Scope:** documentation + observability. **No code logic changes.**

**Deliverables:**

- **1a. Create `docs/architecture/completion-authority.md`** (≤200 lines). Contents:
  - Statement of the three authorities and their roles.
  - The invariant (in bold at the top, so it cannot be missed):
    > *For any instance, exactly one of the three completion authorities drives terminal transitions; the other two are derived caches and must be kept consistent on every transition that affects them. The CorrelationManager is authoritative for completion decisions. The `waiting_for` column is a rebuild cache only. The `pending_count` query is a derived view only.*
  - A table of every call site that currently mutates each authority, with the rationale (and a "should this still be here after M2?" column).
  - Reference to ADR-011.
  - List of "future authority changes" — anything that introduces a new completion source must add itself here and the invariant check below must be extended.

- **1b. Add `tests/test_completion_authority_invariant.py`** (Unit, 2 min timeout, ~10 tests). The pack:
  - For every call site that mutates `waiting_for` (5 sites: `instance.py:570+`, `child_reports.py:512+`, `instance_lifecycle.py` pause/resume, `error_reporting.py:528`, `correlation_manager.py` rebuild), assert that the matching CM `register`/`resolve` call exists at the same call site, OR that the site is documented as "cache-only mutation, no CM call by design."
  - For every call site that reads `waiting_for` for control flow, assert that the call is gated by `USE_LEGACY_WAITING_FOR_CASCADE=1` (the M2 flag) **OR** that the read is documented as a rebuild-cache read.
  - For every call site that reads `pending_count`, assert that the gating logic is consistent with the CM `is_complete()` check at the same call site.
  - On `main`, this pack will fail in many places. **That is the audit, not a regression.** A new test entry in `PACKS.md` records the expected failure count.

- **1c. Add a runtime invariant check** in `correlation_manager.py:resolve_response` and `register_message_send`. Gated by `DEBUG_COMPLETION_INVARIANT=1` (env var). When ON, every CM operation reads the current `waiting_for` value from the same session and logs a structured warning (`event=CM_WAITING_FOR_DIVERGENCE`, with parent_id, child_id, message_id, cm_pending, waiting_for) on mismatch. Off in production by default; on in dev and CI.

- **1d. Update `docs/architecture/message-processing-and-correlation.md`** Section 5 ("CorrelationManager in Depth") to point to the new authority doc.

**Acceptance criteria:**
- A developer looking for "who decides when a parent is done?" has a single, authoritative reference.
- A developer introducing a new completion source gets a CI failure if they don't update the invariant check.
- The team has a runtime signal of divergence in dev/CI.

**Out of scope:** removing the legacy `waiting_for` reads. That is M2.

---

### Milestone 2 — Promote CM to the sole completion authority, in shadow

**Goal:** the premature-completion bug class for Variants A and C is **structurally impossible** under a feature flag. ADR-011 becomes enforced in code, not just in docstring.

**Effort:** 3 days.

**Scope:**
- `daemon/services/correlation_manager.py`
- `daemon/services/child_reports.py`
- `daemon/services/job_feedback_observer.py` — **remove** the M0 `WriteGuardSession` re-read (no longer needed).
- `daemon/services/instance_lifecycle.py` (pause/resume `waiting_for` reset — gated by the new flag).
- `daemon/tools/instance.py` (the M0 parent-revive patch — gated by the new flag, becomes redundant when M2 ships).

**Deliverables:**

- **2a. Add the `USE_LEGACY_WAITING_FOR_CASCADE` feature flag** to `daemon/config.py` (default `OFF` in dev, `OFF` in CI, `ON` in production initially for the transition). Reads from `WAITING_FOR_CASCADE_LEGACY` env var. Documented in `docs/configuration/`.

- **2b. In `child_reports._process_child_completion_and_notify_parent`**, wrap the `waiting_for` SQL decrement in a `if config.USE_LEGACY_WAITING_FOR_CASCADE:` block. When the flag is OFF, the function:
  - Still calls `notify_corr_resolve` (this is the authoritative path).
  - Does **not** write `waiting_for = waiting_for - 1`.
  - Does **not** read `waiting_for` for the cascade decision (`if parent.waiting_for == 0: …`); uses `cm.get_pending_count(parent_id)` instead.
  - Does **not** write `parent.status = WAITING_CHILDREN`; that branch is dead code when the flag is OFF (CM owns the deferral decision).

- **2c. In `instance.py:send_message`**, wrap the `waiting_for` SQL increment and the M0 parent-revive `UPDATE` in the same flag. When the flag is OFF, the function only calls `notify_corr_register`; it does not write `waiting_for` and does not perform the revive (CM owns the count; the revive is a symptom of M0's band-aid, no longer needed).

- **2d. In `instance_lifecycle.pause_instance_cascade` and `resume_instance_cascade`**, wrap the `waiting_for` reset logic in the flag. When the flag is OFF, these methods leave `waiting_for` alone; CM re-registers all in-flight correlations on resume via the existing `rebuild_from_db()` path.

- **2e. In `job_feedback_observer._finalize_job`**, **remove** the M0 `WriteGuardSession` re-read of `waiting_for`. Replace it with a single `cm.is_complete(instance_id)` call (the CM callback already does this; the M0 re-read was defensive belt-and-braces that is now redundant). The re-read was useful only because the legacy path could let `cm_pending == 0` and `waiting_for > 0` simultaneously; under the flag-OFF path, the two are always equal.

- **2f. Add `tests/test_correlation_authority_shadow.py`** (Unit, 2 min timeout, ~20 tests). The pack:
  - Runs the full `test_premature_completion.py` suite (from M0) with `USE_LEGACY_WAITING_FOR_CASCADE=OFF` and asserts Variants A and C pass.
  - Adds new tests for: CM `is_complete` returns `True` exactly when `waiting_for == 0` and `pending_count == 0` (for 50 random parent state fixtures).
  - Adds tests for: pause/resume with the flag OFF preserves the CM pending set across pause boundaries (no re-registration needed).
  - Adds tests for: after the flag is flipped, the `waiting_for` column is consistent with CM at the end of every test (read with the M1 invariant check).

- **2g. Update `tests/test_premature_completion.py` Variant A and C tests** to assert that they pass under the flag. The tests remain in the pack as "flag-OFF invariant" — they must pass even after M2's flag is fully ON in production.

**Acceptance criteria:**
- The premature-completion bug class is **structurally impossible** under `USE_LEGACY_WAITING_FOR_CASCADE=OFF`.
- The legacy path is preserved as a kill switch (no destructive removal yet).
- M0's band-aid patch is gated and redundant; the re-read in `job_feedback_observer` is removed.

**Risks:**
- Any code path that reads `waiting_for` for control flow outside the flag will break silently. The M1 invariant check (1c) catches them; the M2f test pack is the audit.
- The rebuild from `message_queue` in `rebuild_from_db()` must be sufficient to reconstruct the CM pending set on resume. This is already the case per ADR-011; M2's test pack is the proof.

**Migration:**
1. Land the flag (default OFF in dev).
2. Run the M2f test pack in dev for one week.
3. Flip the flag ON in dev. Monitor `DEBUG_COMPLETION_INVARIANT` warnings.
4. Flip the flag ON in production. Monitor for 2 weeks.
5. Flip the flag OFF in production. Keep the legacy path as a kill switch for one more month, then remove in M7 (which supersedes the column anyway).

---

### Milestone 3 — Fix the Variant B path (`watch_job`)

**Goal:** the `watch_job` path is also routed through the CM, not through any of the three completion authorities' legacy checks.

**Effort:** 1 day.

**Scope:** `daemon/services/correlation_manager.py`, `daemon/tools/instance.py` (the `job_continue` + `watch_job` code path), `daemon/services/job_feedback_observer.py` (already emits terminal events for watched jobs).

**Deliverables:**

- **3a. Add `notify_corr_register_job` and `notify_corr_resolve_job`** to `daemon/services/correlation_manager.py`. These are CM-API-level helpers (not lifecycle APIs) that register/resolve a `(parent_id, child_job_id)` correlation. The internal storage adds a new `ParentCorrelation.pending_jobs` dict alongside the existing `pending` dict (message correlations).
  - `is_complete(parent_id)` returns `True` when both `pending` and `pending_jobs` are empty.
  - `handle_correlation_complete` fires when both reach zero.

- **3b. In the `job_continue` tool** (LangChain tool, lives in `daemon/tools/job.py` or wherever it is — find via `grep`), wrap the `watch_job` follow-up call in a `notify_corr_register_job(parent_id=current_instance_id, child_job_id=child_job.job_id)`.

- **3c. In `JobFeedbackObserver`**, the existing terminal-event emission when a watched job completes (`processing → completed` / `processing → failed`) gains a `notify_corr_resolve_job(parent_id=parent_instance_id, child_job_id=job.job_id)` call. The CM then checks `is_complete` and fires the callback as usual.

- **3d. Add `tests/test_watch_job_integration.py`** (Unit, 2 min timeout, ~10 tests). The pack:
  - Variant B regression test from M0: parent calls `job_continue` + `watch_job`; assert parent JOB does not finalize until child job completes.
  - Multiple `watch_job` calls from the same parent: assert `is_complete` only fires when all are resolved.
  - Watched job that fails: assert parent's terminal status is `error`, not `completed`.
  - Watched job cancelled: assert parent's terminal status is `cancelled` (or `completed`, depending on policy — document the choice).

**Acceptance criteria:**
- All three repro variants from the investigation are now structurally impossible.
- The `tests/test_premature_completion.py` pack is fully green.
- The premature-completion bug class is **closed**.

**Risks:**
- The `watch_job` tool's existing logic may not have a clear "this is the parent instance" context. Need to audit the call site and confirm `current_instance_id` is the parent.

---

### Milestone 4 — Single dispatcher: alias, don't fork

**Goal:** the codebase has one enqueue function. No behavior change.

**Effort:** 3 days.

**Scope:** `daemon/services/instance_messaging.py`, `daemon/services/job_queue_service.py`, every caller of `enqueue_message_via_jq` and `enqueue_message`.

**Deliverables:**

- **4a. Pick the winner.** Per `docs/plans/unified-dispatcher.md` §5.1, `enqueue_message` (WorkerPool-flavored) is the winner. It is the path `child_reports` and `error_reporting` already chose, and it is simpler (no `JobLockManager`, no per-queue concurrency, no `DemandState`).

- **4b. Make `enqueue_message_via_jq` a thin wrapper** that calls `enqueue_message` with the same arguments plus a `metadata={"dispatch_path": "legacy_jq"}` tag (for log greppability during the transition). Add a `DeprecationWarning` log on the wrapper. No behavior change.

- **4c. Add `tests/test_dispatcher_path_equivalence.py`** (Unit, 2 min timeout, ~10 tests). The pack:
  - Runs the same scenario 100 times through each entry point (HTTP, agent tool, child completion report, error report, source, scheduler) and asserts identical observable behavior: identical DB rows, identical SSE events, identical final instance status, identical final job status.
  - Asserts that every public enqueue path produces the same `MessageQueue` row, the same `Task` row, and the same downstream behavior.
  - Asserts that the `metadata["dispatch_path"]` tag is set correctly.

- **4d. Update `docs/architecture/message-processing-and-correlation.md` Section 4** ("How a Message Flows") to reflect the unified entry point. Mark the previous "Two physical dispatchers" text as historical. Add a link to `docs/plans/unified-dispatcher.md` §5.2 for the full unification plan.

- **4e. Add a CI grep test** in `tests/test_dispatcher_path_invariants.py` (Unit, 30s timeout, 1 test). The test:
  - Greps the entire `daemon/` tree for `enqueue_message_via_jq(` call sites.
  - Asserts the only call site is the wrapper itself in `daemon/services/job_queue_service.py`.
  - Fails the build on any new direct call.

- **4f. Update `.agents/tester/PACKS.md`** with the new test packs (4c, 4e). Tag both as "Milestone 4 — dispatcher alias."

**Acceptance criteria:**
- One enqueue function in the codebase. No behavior change observable in production.
- The CI grep test prevents the two paths from diverging again.

**Risks:** minimal — the change is purely a wrapper. The risk is the deprecation log line being too noisy; gate it on `LOG_LEVEL >= INFO` and add a one-week monitoring step.

---

### Milestone 5 — Single dispatcher: route all JobQueue admission through `JobFeedbackObserver`

**Goal:** the JobQueue is a scheduling layer; the WorkerPool is the only execution path.

**Effort:** 1 week.

**Scope:** `daemon/services/job_feedback_observer.py`, `daemon/services/job_processor.py`, `daemon/services/message_job_handler.py`.

**Deliverables:**

- **5a. Extend `JobFeedbackObserver`** to handle the **local-admission path** (today it only handles the cross-instance handoff at lines 372, 425, 465, 555). When `JobProcessor` admits a `JobItem` of `job_type='message'`:
  - The observer writes a `Task` row pointing at the same `message_id`.
  - The observer calls `worker_pool.notify_work()`.
  - The `JobItem` is marked `PROCESSING` (status only — the actual execution is in the Task table).
  - This is the only path that writes a `Task` row for message work.

- **5b. Demote `MessageJobHandler.handle`** to a no-op for the local path. The file is not deleted yet (that's M8); it remains a thin adapter that delegates to the observer for local work and to the cross-instance handoff for remote work.

- **5c. Add a metric `dispatch_path`** to every relevant log line:
  - `dispatch_path=jobqueue_local` for work admitted by `JobProcessor`.
  - `dispatch_path=jobqueue_cross_node` for work bounced from another node.
  - `dispatch_path=workerpool_direct` for work that came in via `enqueue_message` (sources, scheduler, etc.) without a `JobItem` row.
  - Emit the metric in a structured-log format (JSON) so a dashboard can be built.

- **5d. Run in shadow for one week** (both paths active, the observer's path observed and the legacy path running in parallel). Verify in the dashboard that:
  - Both paths produce identical observable behavior for the same input.
  - No new `LeaseContention` events appear.
  - The cross-instance handoff path is unchanged.

- **5e. Add `tests/test_unified_dispatcher_shadow.py`** (Unit, 5 min timeout, ~15 tests). The pack:
  - Pauses the legacy path via `USE_LEGACY_JOBQUEUE_DISPATCH=OFF` and asserts the observer's path produces the same result for 50 randomized scenarios.
  - Asserts `JobItem` rows for `job_type='message'` correctly transition through PROCESSING → COMPLETED with the Task table as the source of truth for execution.
  - Asserts the cross-instance handoff path is unaffected (runs both with the flag ON and OFF; the cross-instance path must work in both).

- **5f. After one week of clean dashboards**, remove the legacy path:
  - `MessageJobHandler` becomes purely a cross-instance handoff.
  - `JobProcessor` no longer calls `MessageJobHandler.handle` for local work.
  - The `USE_LEGACY_JOBQUEUE_DISPATCH` flag is removed.

**Acceptance criteria:**
- The JobQueue is now a scheduling layer, not an execution layer.
- Every new entry point (sources, scheduler, future MCP integrations) gets correct pause/resume/revival semantics for free.
- The unified-dispatcher destination is one step away.

**Risks:**
- The JobProcessor polling loop interacts with the dispatch bus; M5d's metrics must verify the dispatch bus wake-up works for the new path.
- The 30 s polling interval in `JobProcessor._process_loop` may need to be event-driven for low-latency admission. This is documented in `docs/bugs/terminate-pause-latency.md` RC2; M5 may need to also address that. If so, file a follow-up plan and don't let M5 scope-creep.

---

### Milestone 6 — Collapse the Execution Gate to an in-process `asyncio.Lock`

**Goal:** ~700 lines of `execution_gate.py` collapse to ~40. The cross-process safety belt is removed because it is no longer needed.

**Effort:** 2 days.

**Scope:** `daemon/services/execution_gate.py`, `daemon/services/message_processing_pipeline.py` (gate call site), `daemon/repositories/execution_lease/`, the `instance_execution_leases` table.

**Deliverables:**

- **6a. Replace the DB-backed lease** with the in-process `asyncio.Lock` implementation described in `docs/plans/unified-dispatcher.md` §5.4:
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

- **6b. Delete** the following:
  - `recover_stale_leases` startup call
  - `LeaseContention` exception
  - `LeaseLostError` exception
  - `_lease_heartbeat_loop` background task
  - heartbeat escalation logic
  - `LeaseHolderKind` enum (no longer needed; the lock is identity-less)
  - The `instance_execution_leases` table migration (`20260614_000002_create_instance_execution_leases.sql`)
  - The `daemon/repositories/execution_lease/` directory

- **6c. Update the one surviving call site** in the (post-M5) unified `MessageTaskProcessor` (renamed from `ProcessMessageProcessor` in M5f). The call is now `async with self._gate._lock_for(instance_id): await work_fn()`. The `holder_id` and `holder_kind` parameters are no longer needed.

- **6d. Update `docs/architecture/message-processing-and-correlation.md` Section 6** ("ExecutionGate in Depth") to reflect the new implementation. Document the cross-process limitation: a future multi-node deployment will need a `MultiProcessExecutionGate` subclass; the module docstring of `execution_gate.py` points to this as a follow-up.

- **6e. Add a module docstring** to `execution_gate.py` that reads:
  > *This gate serializes `_process_message_with_tracking` per instance within a single process. It is an in-process `asyncio.Lock`; there is no cross-process coordination. If you deploy the daemon across multiple processes/nodes, this gate WILL NOT prevent the same instance from being driven concurrently. To re-enable cross-process safety, wrap this class in a `MultiProcessExecutionGate` strategy that adds a DB-backed lease (see commit history for the prior implementation; the migration `20260614_000002_create_instance_execution_leases.sql` was dropped and would need to be re-applied).*

- **6f. Run the full `concurrency_atomic_unit_test` pack** (86 tests, per `PACKS.md`) and the `test_cross_dispatcher_*` race tests from `docs/bugs/`. They must all pass. If any fail, the unification isn't actually single-dispatcher yet; do not ship this milestone.

**Acceptance criteria:**
- ~660 lines removed.
- All race tests pass.
- The gate's interface is unchanged (`gate.run(instance_id, ...)`); only the implementation collapses.

**Risks:**
- If M5 is incomplete (e.g. the cross-instance handoff still goes through `MessageJobHandler.handle` for some path), M6 will re-create the original race. **M6 must not ship until M5f is done.**

---

### Milestone 7 — Dependency Bus, behind a feature flag

**Goal:** the "parent is waiting for N children" model is expressed as watcher relationships on a bus, not as a counter on a row. The bus survives restart. There is one mechanism, not three.

**Effort:** 1 week.

**Scope:** new `daemon/services/dependency_bus.py`, new `dependency_watchers` table, deprecation of the CM `(child_id, message_id)` correlation set (in favor of the new bus), eventually-drop of the `waiting_for` column.

**Deliverables:**

- **7a. Create `daemon/services/dependency_bus.py`** with the API described in `docs/plans/unified-dispatcher.md` §5.6:
  ```python
  class DependencyBus:
      async def watch(self, source_task_id: str, follow_up: FollowUp) -> None: ...
      async def emit_terminal(self, task_id: str, outcome: Outcome) -> None: ...
      async def pending_watchers(self, source_task_id: str) -> list[FollowUp]: ...
  ```
  - `watch` is called from `send_message` (parent registers itself as a watcher of the child's task).
  - `emit_terminal` is called from `MessageTaskProcessor.process` when a task reaches its terminal event.
  - `pending_watchers` returns the FollowUps that should be enqueued when the source task completes.

- **7b. Add a new table `dependency_watchers`** with columns: `watch_id`, `source_task_id`, `target_instance_id`, `follow_up_payload` (JSON, pre-built message content), `metadata` (JSON: `kind`, `child_id`, etc.), `created_at`, `fired_at` (nullable), `state` (PENDING, FIRED, CANCELLED). Migration: `20260620_000001_create_dependency_watchers.sql`.

- **7c. Build the parent-waits-for-children flow on the new bus, behind `USE_DEPENDENCY_BUS=1`** (default OFF initially). The old CM path keeps running in parallel. Both paths are exercised by the test pack; metrics count which one delivered each completion report.

- **7d. The `instance.py:send_message` flow** under the flag ON:
  - Writes the `Task` row for the child as today.
  - Writes a `dependency_watchers` row (FollowUp) with `source_task_id=child_task.id`, `target_instance_id=parent_id`, pre-built message content.
  - Does **not** call `notify_corr_register` (the bus replaces it).

- **7e. The `MessageTaskProcessor.process` flow** under the flag ON:
  - On terminal event, calls `bus.emit_terminal(task_id, outcome)`.
  - The bus fires all pending watchers, enqueuing their FollowUps as new Tasks.
  - The processor does **not** call `notify_corr_resolve` (the bus replaces it).

- **7f. Run in shadow for one sprint** (both paths active). Add a metric `completion_delivery_path=cm|bus|both` to every relevant log line. Dashboard it. **Both paths must agree on the answer for every test scenario.**

- **7g. After one sprint of clean metrics**, flip the flag ON in dev. After another sprint, ON in production. After one more sprint, delete the old CM `register_message_send` / `resolve_response` calls. Keep the CM class for one more month as a shadow validator (verifies that the bus's behavior matches the CM's would-have-been behavior for every test), then remove.

- **7h. Add `tests/test_dependency_bus.py`** (Unit, 5 min timeout, ~30 tests). The pack:
  - Bus watcher semantics: 1 parent, 3 children, all complete → parent's follow-up enqueued exactly once, even with duplicate child completions.
  - The `waiting_for` double-decrement bug class (the one already fixed with the `CASE` clamp in `_update_parent_on_child_complete`) is gone in the new model: bus does not have a counter, so it cannot double-decrement.
  - Bus survives restart: write a watcher, simulate a crash (in-memory state cleared, DB state preserved), restart, emit terminal — watcher fires correctly.
  - Bus cancellation: terminate a parent whose children have pending watchers; the bus marks the watchers CANCELLED and does not enqueue FollowUps.
  - Bus backpressure: 10,000 watchers on a single task → bus emits one at a time (no thundering herd).

- **7i. Drop `Instance.waiting_for`** column, `Instance.children` denormalized JSON cache, and `instance_hierarchy` table. Migration: `20260620_000002_drop_legacy_completion_columns.sql`. The migration is reversible (drops columns, recreates them as NULL, no data loss).

**Acceptance criteria:**
- The "parent is waiting for N children" model is one mechanism (the bus), not three (counter + completion-report-as-message + CM dict).
- The bus survives restart.
- All `tests/test_premature_completion.py` tests pass.

**Risks:**
- M7 is the largest behavioral change in the plan. It needs a full sprint of dual-running metrics before the old path can be turned off.
- The bus's persistence layer (the `dependency_watchers` table) must support high-concurrency inserts and reads. Use the existing `WriteGuardSession` pattern.

---

### Milestone 8 — Drop the `JobItem` MESSAGE dispatch and finalize docs

**Goal:** the Job system is purely a scheduling vocabulary. The two table types have a single, well-defined role each. The architecture matches the unified-dispatcher destination.

**Effort:** 1 day.

**Scope:** `daemon/services/job_processor.py`, `daemon/services/job_queue_service.py`, `daemon/services/message_job_handler.py` (delete if M5 has not already done so), documentation.

**Deliverables:**

- **8a. Remove the `job_type='message'` branch** from `JobProcessor`. The Job system no longer dispatches message work; it only schedules it. `JobItem` rows for message work are no longer written; only `Task` rows are.

- **8b. Delete `daemon/services/message_job_handler.py`** if not already done in M5. If M5 left the cross-instance handoff path in this file, move it to `job_feedback_observer.py` and delete the file.

- **8c. Run the full integration test suite for 2 weeks.** If no message-job log lines appear (`grep "job_type='message'"` is empty in production logs), delete the dispatch constant and the now-dead code paths in `JobProcessor` and `job_queue_service.py`.

- **8d. Update `docs/architecture/message-processing-and-correlation.md` and `docs/architecture/job-task-pause-resume.md`** to reflect the final shape. Add a one-page summary at the top of `docs/architecture.md`:
  > *The daemon has a unified dispatcher (the WorkerPool) and a scheduling layer (the JobQueue). All "I want `graph.astream` to run for instance X" requests go through the same code path: `manager.enqueue_message(...)` → `MessageQueue` row + `Task` row → WorkerPool claim → gate → `_process_message_with_tracking`. The JobQueue owns the scheduling vocabulary (queues, priorities, concurrency, dead-letter, retries). Completion authority is the Dependency Bus. The pre-Milestone 0 architecture (two dispatchers, three completion authorities, DB-backed gate) is described in `docs/plans/unified-dispatcher.md` and `docs/bugs/`.*

- **8e. Add a CHANGELOG entry** noting: "Premature-completion bug class closed at M3 (2026-…); architecture cleanup completed at M8 (2026-…)."

**Acceptance criteria:**
- The Job system has a single role (scheduling).
- The WorkerPool has a single role (execution).
- The Dependency Bus has a single role (completion authority).
- Docs match code.

**Risks:** minimal. M8 is the cleanup milestone.

---

## 6. Test pack summary

Each milestone ships a test pack registered in `.agents/tester/PACKS.md`:

| Pack | Milestone | Type | Tests (approx) | Timeout | Last-failing-on-main? |
|---|---|---|---|---|---|
| `premature_completion_regression_test` | M0 | Unit | 3 | 1 min | Yes (3/3) |
| `completion_authority_invariant_test` | M1 | Unit | 10 | 2 min | Yes (audit) |
| `correlation_authority_shadow_test` | M2 | Unit | 20 | 2 min | After M2 ON, no |
| `watch_job_integration_test` | M3 | Unit | 10 | 2 min | After M3, no |
| `dispatcher_path_equivalence_test` | M4 | Unit | 10 | 2 min | No (wrapper) |
| `dispatcher_path_invariants_test` | M4 | Unit | 1 | 30s | No (grep) |
| `unified_dispatcher_shadow_test` | M5 | Unit | 15 | 5 min | After M5f, no |
| `dependency_bus_test` | M7 | Unit | 30 | 5 min | After M7, no |

Plus reuse of existing packs: `concurrency_atomic_unit_test` (86 tests, gate against M6); `correlation_manager_unit_test` (40 tests, regress-check at every milestone); `correlation_shadow_integration_test` (8 tests, shadow-mode coverage).

---

## 7. Effort & value summary

| Milestone | Effort | Resulting benefit | Bug class closed? |
|---|---|---|---|
| M0 — Defensive patch + repros | 0.5 day | Production backstop; 3 regression tests proving the work ahead | Variant A only |
| M1 — Document invariant | 1 day | Divergence is now observable in dev/CI | n/a |
| M2 — CM authoritative | 3 days | Repro A and Repro C structurally impossible | A, C (under flag) |
| M3 — watch_job → CM | 1 day | Repro B structurally impossible | A, B, C (all) |
| M4 — Alias enqueue | 3 days | One entry point, no behavior change | n/a |
| M5 — Single admission path | 1 week | Pause/resume/revival correct for every entry point | n/a |
| M6 — Collapse gate | 2 days | -660 lines; cross-process safety belt removed (no longer needed) | n/a |
| M7 — Dependency Bus | 1 week | Counter + completion-report-as-message collapses to one primitive | n/a |
| M8 — Drop MESSAGE job_type | 1 day | Final cleanup; docs match code | n/a |
| **Total** | **~5–6 weeks** | | |

---

## 8. Migration runbook (per-milestone go/no-go criteria)

### M0 → M1
- [ ] Patch landed on `bugfix/job-premature-complete`.
- [ ] Three repro tests fail on `main`, pass on the branch (Variant A only).
- [ ] Production has been running the patch for 48 hours with no regression.

### M1 → M2
- [ ] `completion-authority.md` reviewed by a second engineer.
- [ ] `DEBUG_COMPLETION_INVARIANT` log warnings collected for one week; mismatch rate is documented.
- [ ] `test_completion_authority_invariant.py` has been run; the failure list is the M2 audit checklist.

### M2 → M3
- [ ] Flag OFF in dev for one week with no `DEBUG_COMPLETION_INVARIANT` warnings in CI.
- [ ] Flag ON in dev for one week; warnings checked.
- [ ] Flag ON in production for two weeks; warnings checked.

### M3 → M4
- [ ] All three repro variants green in production.
- [ ] No premature-completion incidents for 2 weeks.

### M4 → M5
- [ ] One enqueue function in the codebase.
- [ ] CI grep test passes.
- [ ] Path equivalence test passes 100/100.

### M5 → M6
- [ ] `dispatch_path=jobqueue_local` metric in production for one week with no `LeaseContention` events.
- [ ] Cross-instance handoff unaffected (dashboard).

### M6 → M7
- [ ] `concurrency_atomic_unit_test` 86/86 pass.
- [ ] `cross_dispatcher_*` race tests pass.
- [ ] Gate interface unchanged at all call sites.

### M7 → M8
- [ ] `completion_delivery_path=cm` count is 0 in production for one sprint.
- [ ] `completion_delivery_path=bus` count matches expected traffic.
- [ ] `waiting_for` column and `instance_hierarchy` table dropped in dev, no regressions.

### M8 → Done
- [ ] `JobItem` MESSAGE rows: 0 in production for 2 weeks.
- [ ] Docs match code.
- [ ] CHANGELOG updated.

---

## 9. Risks & mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **M0 patch masks the bug** so the team loses urgency to do M2–M3 | Medium | High | The M0 commit message and the M0b regression tests explicitly document the three repros; two of them still fail on M0. The investigation report is linked in every PR body through M2. |
| **M2 flag OFF in dev has hidden `waiting_for` reads** that only fire in production | Medium | High | M1's runtime invariant check (`DEBUG_COMPLETION_INVARIANT`) catches every divergence. M2f test pack exercises the flag for every documented read site. |
| **M5 hot loop on the observer** — the cross-instance handoff path was designed for rare events; under load it may bottleneck | Medium | Medium | Profile first. The observer's polling is event-driven (dispatch bus), not interval-driven. If hot, add per-instance admission throttling. |
| **M6 loses the cross-process safety net** before multi-node is off the table | Low now, High later | High | The new `ExecutionGateService` keeps the same interface. The module docstring explicitly documents how to re-enable cross-process safety. Multi-node deployment is a follow-up plan; this plan does not enable it. |
| **M7 duplicates or races with the legacy CM path during the transition** | Medium | Medium | Feature-flag the bus. Keep both paths running for at least one full sprint. Dashboard `completion_delivery_path`; both must agree. |
| **`MessageJobHandler`'s pause/terminate discrimination** is subtly different from `ProcessMessageProcessor`'s; merging them may lose a case | Medium | Medium | M4's `dispatcher_path_equivalence_test` and the existing `correlation_atomic_unit_test` (86 tests) are the gating tests. Add a pause/terminate matrix test before M5 ships. |
| **Other callers of `enqueue_message`** outside the agent path (scheduler, sources, `invoke_agent_and_wait`) silently get a different code path | Low | Low | M4's grep test catches new direct callers. The signature is unchanged; the implementation change is internal. |
| **`Task` table grows unboundedly** with the new "every admission writes a Task" pattern | Low | Low | Existing `StaleTaskRecovery` and `DeadLetterService` cover this. |
| **Milestone takes longer than estimated** | High | Low | Each milestone is independently-reversible. If M5 slips, M2+M3 are still valuable. The plan has no "all or nothing" commit points. |

---

## 10. What this plan is *not*

- It is **not** the unified-dispatcher plan restated. That plan is the destination; this is the step-by-step path that gets there with "easy first, archive good result."
- It is **not** a rewrite of `_process_message_with_tracking`, the SSE pipeline, the langgraph core, or the LLM streaming path. None of that is touched.
- It is **not** a multi-node deployment plan. The DB-backed lease is *downgraded*, not removed as a concept; M6 documents where it goes back.
- It does **not** fix every bug in `docs/bugs/`. It fixes the premature-completion class, which is the user's stated concern, and leaves the others for their own plans.
- It is **not** a "delete CM" plan. CM is the authority for the duration of M2–M6, then becomes a shadow validator during M7's transition, then is removed in M7g. The CM API surface is preserved through M3.

---

## 11. Open question for the user

The plan above assumes the unified-dispatcher destination in `docs/plans/unified-dispatcher.md` is the right one. If the team prefers a different destination (e.g. queue-only with no CM, or event-sourcing instead of dependency bus), M7 changes shape. **Confirm or revise before M5 ships** — M4 is reversible, M5 is not.

---

## 12. Appendix — file-level change list per milestone

### M0 (current uncommitted patch, known-incomplete)
- `daemon/services/job_feedback_observer.py` — add `WriteGuardSession` re-read of `waiting_for` (M0 only; removed in M2e).
- `daemon/tools/instance.py` — add parent-revive `UPDATE` in `send_message` (M0 only; gated by flag in M2c).
- `tests/test_premature_completion.py` — new file.
- `.agents/tester/PACKS.md` — add `premature_completion_regression_test`.

### M1
- `docs/architecture/completion-authority.md` — new file.
- `tests/test_completion_authority_invariant.py` — new file.
- `daemon/services/correlation_manager.py` — add `DEBUG_COMPLETION_INVARIANT` env-gated check.
- `daemon/config.py` — add `WAITING_FOR_CASCADE_LEGACY` and `DEBUG_COMPLETION_INVARIANT` env vars.
- `docs/architecture/message-processing-and-correlation.md` — pointer to authority doc.

### M2
- `daemon/services/child_reports.py` — gate `waiting_for` decrement and cascade decision under `USE_LEGACY_WAITING_FOR_CASCADE`.
- `daemon/services/instance_lifecycle.py` — gate `waiting_for` reset in pause/resume.
- `daemon/services/job_feedback_observer.py` — **remove** M0's `WriteGuardSession` re-read.
- `daemon/tools/instance.py` — gate M0's parent-revive and the `waiting_for` increment under the flag.
- `tests/test_correlation_authority_shadow.py` — new file.
- `daemon/config.py` — add `USE_LEGACY_WAITING_FOR_CASCADE` config (default OFF in dev/CI, ON in prod initially).

### M3
- `daemon/services/correlation_manager.py` — add `pending_jobs` dict; `is_complete` checks both.
- `daemon/services/correlation_manager.py` — add `notify_corr_register_job`, `notify_corr_resolve_job` helpers.
- `daemon/services/job_feedback_observer.py` — call `notify_corr_resolve_job` on terminal event.
- `daemon/tools/job.py` (or wherever `job_continue` is) — call `notify_corr_register_job` on `watch_job`.
- `tests/test_watch_job_integration.py` — new file.

### M4
- `daemon/services/instance_messaging.py` — `enqueue_message_via_jq` becomes a thin wrapper of `enqueue_message` with `metadata["dispatch_path"]="legacy_jq"`.
- `tests/test_dispatcher_path_equivalence.py` — new file.
- `tests/test_dispatcher_path_invariants.py` — new file (grep test).
- `docs/architecture/message-processing-and-correlation.md` — update Section 4.

### M5
- `daemon/services/job_feedback_observer.py` — add local-admission path.
- `daemon/services/job_processor.py` — call observer on local admission; no longer call `MessageJobHandler.handle` for local.
- `daemon/services/message_job_handler.py` — demoted to cross-instance handoff only; no behavior change yet.
- `tests/test_unified_dispatcher_shadow.py` — new file.
- `daemon/config.py` — add `USE_LEGACY_JOBQUEUE_DISPATCH` config.

### M6
- `daemon/services/execution_gate.py` — collapse to in-process `asyncio.Lock` (~40 lines).
- `daemon/repositories/execution_lease/` — delete.
- Migration `20260614_000002_create_instance_execution_leases.sql` — drop.
- `daemon/services/message_processing_pipeline.py` (or `MessageTaskProcessor`) — update call site.
- `docs/architecture/message-processing-and-correlation.md` — update Section 6.

### M7
- `daemon/services/dependency_bus.py` — new file.
- Migration `20260620_000001_create_dependency_watchers.sql` — new.
- `daemon/services/task_processor.py` (`MessageTaskProcessor` post-M5) — call `bus.emit_terminal` on terminal event.
- `daemon/tools/instance.py` — under `USE_DEPENDENCY_BUS`, write `dependency_watchers` row instead of calling CM.
- Migration `20260620_000002_drop_legacy_completion_columns.sql` — drop `waiting_for`, `children`, `instance_hierarchy`.
- `tests/test_dependency_bus.py` — new file.
- `daemon/config.py` — add `USE_DEPENDENCY_BUS` config.

### M8
- `daemon/services/job_processor.py` — remove `job_type='message'` branch.
- `daemon/services/message_job_handler.py` — delete (or shrink to a one-line `JobFeedbackObserver.cross_instance_handoff` shim if needed).
- `daemon/services/job_queue_service.py` — remove MESSAGE-specific helpers.
- `docs/architecture/message-processing-and-correlation.md` — final update.
- `docs/architecture/job-task-pause-resume.md` — final update.
- `docs/architecture.md` — add one-page summary.
- `CHANGELOG.md` — add entry.

---

## 13. Definition of done

This plan is done when:

1. The premature-completion bug class is **structurally impossible** (closed at M3, reinforced at M7).
2. The codebase has **one dispatcher** (WorkerPool, closed at M5f), **one scheduling layer** (JobQueue, scheduling vocabulary only at M8), and **one completion authority** (Dependency Bus, closed at M7).
3. The Execution Gate is **40 lines**, not 700 (closed at M6).
4. Three documented repro variants from the 2026-06-20 investigation are regression-tested in `tests/test_premature_completion.py` and registered in `PACKS.md`.
5. ADR-011 is enforced in code: `waiting_for` is no longer a control-flow value (closed at M2).
6. Docs match code: `docs/architecture/message-processing-and-correlation.md`, `docs/architecture/job-task-pause-resume.md`, and `docs/architecture.md` reflect the final architecture (closed at M8).
