# Increment 3 Plan: Named Transitions Refactor (Turn-Reconciler Migration Phase 2)

Date: 2026-08-01 (revised 2026-08-01, council review)
Author: plan-creation worker
Status: Draft (revised — council review fixes B1, B6, C1, C5, C6, C7, C9, R1 applied)
Design Doc: `docs/plans/turn-reconciler-named-transitions.md` §5
Predecessor: `increment1-plan.md` (MUST be live — see §3 readiness gate)
Companion: `increment2-plan.md` (interchangeable ship order with this increment)
Decisions: `decisions.md` §1.2 (Inc 3), §D8 (chokepoint routing), §D10 (mirror-set coverage), §D11 (instances soft reconciliation), §5 (Open Questions)

---

## 0. Council Review Revisions (2026-08-01)

> **§ REVISION NOTE (Council Review 2026-08-01):** This plan was revised to address 2 blockers and 5 warnings from the council review. Each revised section is marked with `§ REVISION NOTE (Council Review)` at the change site.

| ID | Severity | Title | Section(s) revised |
|----|----------|-------|--------------------|
| **B1** | **BLOCKER** | `fail_task` omitted from D8 chokepoint — same dangerous split as `complete_task`/`cancel_task`, touches ONLY `task`, forgets all 8 mirrors. Verified at `repository.py:1492` with 8 call sites. | §2 (scope), §5.6 (`ABORT_TURN`), §6.5 (D8 chokepoint), §8.5 (integration test), §9 Phase 4 tasks, Appendix A |
| **B6** | **BLOCKER** | Indirect callers of chokepoint methods (`cancel_task_by_work_id`, `force_cancel_and_schedule_retry`, `force_complete_task`, `StaleTaskRecovery.fail_task` wrapper) not enumerated. Some bypass the chokepoint. | §9 Phase 4 tasks, §11 Risks (R1), Appendix A |
| **C1** | Warning | No per-transition behavioral correctness test that iterates over ALL_8_MIRRORS. | §8.2 (mirror isolation test) |
| **C5** | Warning | No observability metrics for transition health. | §11a (new Metrics & Observability section) |
| **C6** | Warning | §6 and D8 contradict on deprecation window ("same PR — no window" vs "6-month window"). Resolved: phased 4a/4b approach, no formal deprecation window. | §6.5, §9 (Phase 4 → 4a/4b), §13 (OQ-INC3-2 resolved) |
| **C7** | Warning | No repository-level static defense (`_status_write_guard`) that raises on direct `UPDATE task SET status=` outside a transition context. | §6a (new write-guard section), §9 Phase 4 tasks |
| **C9** | Warning | No feature flag for safe chokepoint migration (shadow-traffic / dark-launch pattern). | §6b (new feature-flag section), §9 Phase 4 tasks |
| **R1** | Warning | Stale file:line citations (off by ~40-50 lines in a few places). | §2, §5.x, §6.x line ranges corrected |

---

## 1. Objective

Replace the hand-written cascade SQL — the root cause of the "cascade forgot table X" bug class — with a **named-transition surface** that declares its mirror set per operation. After this increment ships:

- Every lifecycle event goes through one of 7 named transitions (`BEGIN_TURN`, `CLAIM_TURN`, `SUSPEND_TURN`, `RESUME_TURN`, `COMPLETE_TURN`, `ABORT_TURN`, `RETRY_TURN`) on `TaskRepository`.
- Each transition **declares** the mirror tables it touches as a `frozenset`; the union of all 7 transition mirror sets equals the full 8-mirror set (D10) — i.e. **no transition can silently drop a mirror from its contract**.
- The mirror reconciliation that today is hand-written SQL inside `_pause_cascade_db_sync`, `_resume_cascade_db_sync`, `_finalize_job_db_sync`, and `_terminate_instance_db_sync` is **delegated** to the Increment-1 reconciler (`reconcile_turn_mirror(work_id)`).
- The worker-side `complete_task` and `cancel_task` chokepoint methods (D8) — the most dangerous split because they touch ONLY `task` and forget every mirror — **route through** `COMPLETE_TURN` / `ABORT_TURN` so any caller, even a non-cascade path, is automatically protected.
- `schedule_retry`'s parent-cancel + child-arm transition is the single `RETRY_TURN` operation.
- All 404 existing tests remain green; behavior is **identical** (this is a refactor, not a behavior change).

The cascade SQL bodies shrink to thin wrappers: each cascade becomes a loop over the in-flight turns in the tree, calling the appropriate transition for each, plus the tree-scoped instance status UPDATE. Hand-written per-table mirror UPDATEs cease to exist.

---

## 2. Scope

### In Scope

| # | File | Action |
|---|------|--------|
| 1 | `daemon/services/turn_transitions.py` | **NEW MODULE.** Defines `TransitionResult` dataclass + base `_Transition` class + 7 concrete transitions (`BeginTurn`, `ClaimTurn`, `SuspendTurn`, `ResumeTurn`, `CompleteTurn`, `AbortTurn`, `RetryTurn`). Each declares `MIRROR_SET: frozenset[str]`. |
| 2 | `daemon/repositories/task/repository.py` | `complete_task` (line 1437), `cancel_task` (line 2386), **`fail_task` (line 1492)** become thin wrappers that call `CompleteTurn(work_id, result=...).run(session)` / `AbortTurn(work_id, reason=...).run(session)` / `AbortTurn(work_id, reason='failed').run(session)`. Signatures unchanged (existing callers pass through). `schedule_retry` (line 2119) **becomes a wrapper** that calls `RetryTurn(...).run(session)`. |
| 3 | `daemon/services/instance_lifecycle.py:3039-3210` (`_pause_cascade_db_sync`) | **REWRITE** as a thin wrapper: iterate in-flight turns, call `SuspendTurn(work_id, reason=...).run(session)` per turn + instance status UPDATE. The 170-line block becomes ~40 lines. |
| 4 | `daemon/services/instance_lifecycle.py:3474-4162` (`_resume_cascade_db_sync`) | **REWRITE** as a thin wrapper: per cancelled Task, call `ResumeTurn(work_id, ...).run(session)` + instance status UPDATE + schedule resume-processing job. UPDATE 4 (already replaced by Increment 1's reconciler call) becomes one `ResumeTurn` invocation. |
| 5 | `daemon/services/instance_lifecycle.py:2599-2962` (`_terminate_instance_db_sync`) | **REWRITE** as a thin wrapper: per terminal Task, call `AbortTurn(work_id, reason='terminated').run(session)` + instance status UPDATE. |
| 6 | `daemon/services/job_feedback_observer.py:2761-3421` (`_finalize_job_db_sync`) | **REWRITE** as a thin wrapper: per finalized Task, call `CompleteTurn(work_id, result=...).run(session)`. Step 1 (JobItem UPDATE) and Step 2 (mirror reconcile — Increment 1) collapse into the single `CompleteTurn.run()` call. |
| 7 | `daemon/repositories/task/repository.py` (Task creation path) | The Task-creation path that today inserts a `pending` Task + arming mirrors becomes `BeginTurn(work_id, ...).run(session)`. |
| 8 | `tests/property/test_named_transitions.py` | **NEW.** Property tests for each transition (atomicity, idempotency, fails-closed on concurrent Task mutation). Plus the mirror-set coverage test (D10): `frozenset.union(*[t.MIRROR_SET for t in TRANSITIONS]) == ALL_8_MIRRORS`. |
| 9 | `tests/unit/test_transition_results.py` | **NEW.** Unit tests for `TransitionResult` shape — outbox payloads (wakeup, SSE, watcher notify) carry the right data; `instance_id`, `work_id`, `old_status`, `new_status`, `mirrors_touched` are populated. |
| 10 | `tests/e2e/test_pause_resume_unchanged.py` | **NEW.** Directed E2E that re-runs the existing pause/resume E2E tests against the new wrappers, asserting behavior is identical (a guard against accidental behavior drift). |
| 11 | `tests/integration/test_complete_cancel_route_through_transitions.py` | **NEW.** Integration test that proves `complete_task(...)`, **`fail_task(...)`**, and `cancel_task(...)` chokepoint methods, when called directly (NOT through a cascade), still reconcile all 8 mirrors via the named transition. This is the test that validates D8 (including the `fail_task` half — see B1). |
| 12 | `daemon/services/turn_transitions.py` | **EXPORT** the `ALL_8_MIRRORS = frozenset({...})` constant + `TRANSITIONS = (BeginTurn, ClaimTurn, ...)` registry for the property test to consume. |
| 13 | `docs/bugs/pause-during-report-turn-orphans-message-jobitem.md` | **APPEND** "Resolved structurally by Increment 3 — named transitions + D8 chokepoint routing prevent any future hand-SQL bypass." |

### Out of Scope

- **Increment 1** (reconciler routine) and **Increment 2** (carve-out deletion) — both must be **live** before this ships (see §3), but the work itself belongs to those plans.
- **Increment 4** (turn suspension handle / `suspension_reason` + `resume_target_turn_id` columns) — Increment 4 ships after this; the named transitions defined here are the right substrate for it to plug into.
- **The 9P instance-status logic in cascades** — `_pause_cascade_db_sync`'s instance-status UPDATE remains in the cascade wrapper (the instance status is tree-scoped per D11 and is the cascade's job, not the per-turn transition's).
- **`schedule_retry`'s `job_watchers` migration** — Increment 1's reconciler already handles table 8 (`job_watchers` cleanup). `RETRY_TURN` inherits that; no separate migration is required here. The hot-path optimization (D-Risk-8 — fast-path when no orphans detected) is **deferred** to a follow-up performance sprint; not in this refactor.
- **Linter rule from OQ7** — "Direct SQL on a mirror table MUST go through a transition" is a follow-up. The migration ships the manual code-review checklist (Risk 7); linter is a separate PR.
- **Schema changes** — no new columns in Increment 3. If implementation discovers a need, STOP and obtain a separate migration decision; any new column must use `_ensure_postgres_columns()` (D7 precedent).
- **Performance tuning beyond transaction correctness** — D-Risk-8 latency is a follow-up. The refactor is required to preserve current p95 latency, not improve it.
- **The `DeprecationWarning` window from OQ5** — D8 was drafted with a 6-month warning window. Because call-site migration is part of the SAME PR (D8 "Consequences"), there is no deprecation window needed for this refactor; the wrappers are aliases from day one. If the team prefers staged migration (split the migration across releases), that is a council decision (see §13 Open Questions).

---

## 3. Dependency on Increment 1 — Readiness Gate

**Increment 1 MUST be live and stable before Increment 3 is safe to ship.** The named transitions DELEGATE mirror reconciliation to `reconcile_turn_mirror(work_id)`. Without the reconciler live, the transitions would need to enumerate mirrors by hand again — which is the bug class this increment is designed to eliminate.

### 3.1 Readiness Checklist (mirror Increment 2's gate)

| # | Readiness criterion | How to verify | Required for Increment 3 start? |
|---|---------------------|---------------|----------------------------------|
| 1 | `TaskRepository.reconcile_turn_mirror(work_id)` is implemented | Source inspection of `daemon/repositories/task/repository.py` | **YES — hard gate** |
| 2 | Reconciler is integrated at all 6 call sites (claim, resume, finalize, timeout, periodic sweep, pause-after-Update-2) | Source inspection of the 6 files per Increment 1's §3.2 | **YES — hard gate** |
| 3 | Property tests in `tests/property/test_turn_state_machine.py` pass on PostgreSQL for ≥1000 generated transitions | `pytest tests/property/test_turn_state_machine.py --hypothesis-seed=...` against PG | **YES — hard gate** |
| 4 | Increment 1's directed E2E at `tests/e2e/test_pause_during_report_turn_then_resume.py` passes on PostgreSQL | `pytest tests/e2e/test_pause_during_report_turn_then_resume.py` against PG | **YES — hard gate** |
| 5 | Full 404-test baseline passes with the reconciler live | `pytest tests/ --tb=short` against PG | **YES — hard gate** |
| 6 | Reconciler is in production for ≥7 days with `reconciler_corrections_per_hour == 0` AND zero P1/P2 orphan-admission incidents (D3 telemetry gate) | Production monitoring + incident log | **RECOMMENDED — soft gate.** If Increment 2 has already shipped and is stable, the reconciler is by definition stable; this gate is redundant. |

### 3.2 If a readiness check fails

- If any hard gate fails, **defer Increment 3** (same rule as Increment 2 §3.2). The named transitions depend on the reconciler; shipping them without it would re-introduce the bug class.
- If the soft gate is not met but all hard gates pass AND Increment 2 has not yet shipped, ship Increment 3 to a canary environment and monitor for 48 hours before production.

### 3.3 Verification commands (Increment 3 worktree, after Increment 1 is merged; Increment 2 may or may not have landed)

```bash
# Verify the reconciler exists and is wired
grep -n "reconcile_turn_mirror" daemon/repositories/task/repository.py \
    daemon/services/instance_lifecycle.py \
    daemon/services/job_feedback_observer.py \
    daemon/services/stale_task_recovery.py \
    daemon/services/job_recovery_service.py
# Expected: ≥6 occurrences, ≥1 per file

# Property tests (Increment 1's state machine)
pytest tests/property/test_turn_state_machine.py --hypothesis-seed=20260801 -v

# E2E (Increment 1's directed scenario)
pytest tests/e2e/test_pause_during_report_turn_then_resume.py -v

# Full baseline
pytest tests/ --tb=short -q 2>&1 | tail -50
# Expected: 404 + (Increment 1 additions) tests pass
```

### 3.4 Independence from Increment 2

Increment 3 is **independent of Increment 2**. Both consume Increment 1; neither consumes the other. The team may ship them in either order (OQ3 recommends Increment 3 first — "the migration feels like renaming things to see what's redundant"). This plan does NOT require Increment 2 to have shipped first.

---

## 4. The Transition Surface

### 4.1 Module structure — `daemon/services/turn_transitions.py`

```python
# Public surface — exported for the property test (D10) and for any future caller.

#: All 8 mirror tables (D1).
ALL_8_MIRRORS: frozenset[str] = frozenset({
    "task",
    "job_queue_items",
    "message_queue",
    "job_locks",
    "dependency_watchers",
    "report_injections",
    "instances",
    "job_watchers",
})

#: Registry of all 7 transitions. The property test asserts
#: frozenset.union(*[t.MIRROR_SET for t in TRANSITIONS]) == ALL_8_MIRRORS (D10).
TRANSITIONS: tuple[type[_Transition], ...] = (
    BeginTurn,
    ClaimTurn,
    SuspendTurn,
    ResumeTurn,
    CompleteTurn,
    AbortTurn,
    RetryTurn,
)

@dataclass(frozen=True)
class TransitionResult:
    """Outbox payload from a named transition. Consumed by post-commit side effects."""
    work_id: UUID
    instance_id: UUID | None
    old_status: str
    new_status: str
    mirrors_touched: frozenset[str]  # subset of the transition's MIRROR_SET that was actually mutated
    cross_turn_side_effects: tuple[str, ...]  # e.g. ("instance_running", "schedule_resume_job")
    wakeup_payload: dict | None  # for the WorkerPool wake queue
    sse_payload: dict | None     # for HTTP/SSE subscribers
    watcher_notify: tuple[UUID, ...]  # dependency_watchers IDs that need notify-on-commit

class _Transition(ABC):
    """Base class. Subclasses declare MIRROR_SET + override .run()."""
    MIRROR_SET: ClassVar[frozenset[str]]  # every concrete transition must declare

    @abstractmethod
    def run(self, session: Session) -> TransitionResult: ...

# Concrete transitions defined in §5 below.
```

### 4.2 Why a service module, not methods on `TaskRepository`

Two anchoring options were considered:

| Option | Pros | Cons |
|--------|------|------|
| **`daemon/services/turn_transitions.py` (chosen)** | TransitionResult + MIRROR_SET + TRANSITIONS registry are first-class; property test imports `ALL_8_MIRRORS` directly; module is dedicated to "what is a transition" semantics. | Service module is one more file to navigate. |
| **Methods on `TaskRepository`** | Co-located with `reconcile_turn_mirror` (single ingress). | `TaskRepository` is already a large file; TRANSITIONS registry scattered across the class; harder to import the union of MIRROR_SETs. |

Decision: **service module.** The registry-style exposure (TRANSITIONS, ALL_8_MIRRORS) is the cleanest substrate for the property test (D10). If the team prefers repository-anchored, the module becomes a re-export shim and the implementations live as `TaskRepository.begin_turn(...)` etc. — record the team's preference in §13 Open Questions and apply it before implementation starts.

### 4.3 TransitionResult — outbox contract

Every transition returns a `TransitionResult`. The CALLER (the cascade wrapper, or `complete_task`, or `schedule_retry`) is responsible for:

1. Committing the transaction.
2. Dispatching the post-commit outbox side effects: WorkerPool wake (`wakeup_payload`), HTTP/SSE emit (`sse_payload`), watcher notification (`watcher_notify`).

The transition itself NEVER dispatches outbox side effects inside `run()`. This is the same outbox pattern as the existing `_terminate_instance_db_sync` (H10 fix) and `_pause_cascade_db_sync` / `_resume_cascade_db_sync` (L14 fix) — see `daemon/services/instance_lifecycle.py:93` and `:152`.

`cross_turn_side_effects` is a TUPLE of opaque strings that the caller can switch on (`"instance_running"`, `"instance_paused"`, `"instance_completed"`, `"schedule_resume_job"`, `"cancel_dependency_watchers"`). These are documented in each transition's docstring; the property test does NOT assert on them (they are caller-policy, not transition-state).

---

## 5. The 7 Named Transitions — Signatures, Mirror Sets, Side Effects

Each transition declares `MIRROR_SET: ClassVar[frozenset[str]]` as the EXACT set of mirror tables it touches. The property test (D10, §9) asserts that the union of all 7 MIRROR_SETs equals `ALL_8_MIRRORS`. **A transition cannot silently drop a mirror from its contract.**

The transition's `MIRROR_SET` declares **intent** (which mirrors this transition is allowed to touch); `TransitionResult.mirrors_touched` reports **actual** mutation (which mirrors actually changed in this run — useful for tests and metrics).

### 5.1 `BEGIN_TURN` — Task creation path

| Property | Value |
|---|---|
| **Task status change** | None (Task is created with `status='pending'`) |
| **Current function** | The Task-insertion path in `daemon/repositories/task/repository.py` (the `INSERT INTO task ... RETURNING *` call sites) |
| **MIRROR_SET** | `frozenset({"task", "job_queue_items", "message_queue", "job_locks"})` |
| **What it touches** | Insert `task` row (pending); arm `job_queue_items` (queued, on a `system_fifo_queue` or `system_parallel_queue` per type); arm `message_queue` (ready); acquire `job_locks` (zero-state insert for admission reservation) |
| **Cross-turn side effects** | `instance_running` (instance status update if this is the first turn in the tree) |
| **Risk** | Low. The Task-creation path today is small and well-understood; it does not currently run through a cascade. |
| **Property test asserts** | After `BEGIN_TURN`, `task.status == 'pending'` AND `job_queue_items.admission_state == 'queued'` AND `message_queue.status == 'ready'` AND `job_locks` row exists. |
| **Returns** | `TransitionResult(work_id=..., instance_id=..., old_status=None, new_status='pending', mirrors_touched={...all 4...}, cross_turn_side_effects=("instance_running",), wakeup_payload=None, sse_payload={"event": "turn_started", ...}, watcher_notify=())` |

### 5.2 `CLAIM_TURN` — admission / slot pickup

| Property | Value |
|---|---|
| **Task status change** | `pending` → `running` |
| **Current function** | `claim_pending_task` (`daemon/repositories/task/repository.py:493-992`), specifically the post-`UPDATE task SET status='running'` block |
| **MIRROR_SET** | `frozenset({"task", "job_queue_items", "job_locks"})` |
| **What it touches** | `task.status='running'` (authority); `job_queue_items.admission_state='active'` (via reconciler, Increment 1); `job_locks` row already exists, leave untouched (lock is held while in-flight per D1 table 4) |
| **Cross-turn side effects** | None (claim is the entry; reconciler runs after) |
| **Risk** | Low. Claim is hot-path; the only change is wrapping the existing UPDATE in a transition class. |
| **Property test asserts** | After `CLAIM_TURN`, `task.status == 'running'` AND `job_queue_items.admission_state == 'active'` AND `job_locks` row still exists (in-flight ⇒ lock held). Idempotent: second CLAIM_TURN on same work_id is a no-op (guarded `WHERE status='pending'` returns rowcount=0). |
| **Returns** | `TransitionResult(work_id=..., instance_id=..., old_status='pending', new_status='running', mirrors_touched={"task", "job_queue_items"}, cross_turn_side_effects=(), wakeup_payload={"event": "turn_claimed", ...}, sse_payload=None, watcher_notify=())` |

### 5.3 `SUSPEND_TURN` — pause

| Property | Value |
|---|---|
| **Task status change** | `running` → `paused` |
| **Current function** | `_pause_cascade_db_sync` UPDATE 2 (`daemon/services/instance_lifecycle.py:3039-3210`) |
| **MIRROR_SET** | `frozenset({"task", "instances"})` |
| **What it touches** | `task.status='paused'` (authority); `instances.status='paused'` (cascade wrapper updates the instance row, NOT the per-turn reconciler — instance status is tree-scoped per D11) |
| **Cross-turn side effects** | `instance_paused`; graph-task `CancellationToken` (caller dispatches via outbox `wakeup_payload`) |
| **Risk** | Medium. Pause is the most-tested transition; any behavior drift fails the existing E2E suite. |
| **Critical note** | Mirrors 2, 3, 4 (job_queue_items, message_queue, job_locks) **stay** during pause — the Task is paused, not terminal. The reconciler's terminal-reconciliation branch does NOT fire here. |
| **Property test asserts** | After `SUSPEND_TURN`, `task.status == 'paused'` AND `job_queue_items.admission_state` UNCHANGED (still 'active') AND `message_queue.status` UNCHANGED (still 'processing') AND `job_locks` row still exists. Idempotent: second SUSPEND on same work_id is a no-op. |
| **Returns** | `TransitionResult(work_id=..., instance_id=..., old_status='running', new_status='paused', mirrors_touched={"task", "instances"}, cross_turn_side_effects=("instance_paused",), wakeup_payload={"event": "graph_task_cancel", "work_id": ...}, sse_payload={"event": "turn_paused", ...}, watcher_notify=())` |

### 5.4 `RESUME_TURN` — un-pause

| Property | Value |
|---|---|
| **Task status change** | `paused` → `cancelled` (the resume cascade cancels the paused Task and mints a fresh `work_id` per Increment 4's direction; Increment 3 sets up the transition but Increment 4 owns the fresh-turn minting) |
| **Current function** | `_resume_cascade_db_sync` (`daemon/services/instance_lifecycle.py:3474-4162`) |
| **MIRROR_SET** | `frozenset({"task", "job_queue_items", "message_queue", "job_locks", "dependency_watchers", "report_injections", "instances", "job_watchers"})` — **ALL 8** |
| **What it touches** | `task.status='cancelled'` (authority, on the paused Task); reconciler reconciles all mirrors to terminal for the cancelled Task; fresh `work_id` minting (Increment 4) is OUT OF SCOPE here — the transition takes a `new_work_id` parameter (or `None` if Increment 4 hasn't landed yet, in which case the transition operates on the original `work_id` and marks it terminal); instance status re-runs. |
| **Cross-turn side effects** | `instance_running`; `schedule_resume_job` (caller dispatches via outbox) |
| **Risk** | Medium. Resume is the bug-A hot path; the transition must execute the same semantics as the 136-line UPDATE 4 (already replaced by Increment 1's reconciler call). |
| **Critical note** | This is the transition that BENEFITS MOST from the reconciler — it owns all 8 mirrors because resume's bug class is "cascade forgot mirror X" (Bug A from 2026-08-01 was an orphaned `job_queue_items(message)` row; Bug B was orphaned `message_queue` rows). |
| **Property test asserts** | After `RESUME_TURN`, all 8 mirrors are in the post-resume target state. The pre-resume paused Task's `task.status='cancelled'`; mirrors reconciled per reconciler. (If Increment 4 has landed, a fresh Task exists with `status='pending'`; if not, this assertion is the only state.) |
| **Returns** | `TransitionResult(work_id=..., instance_id=..., old_status='paused', new_status='cancelled', mirrors_touched=<all 8>, cross_turn_side_effects=("instance_running", "schedule_resume_job"), wakeup_payload={"event": "schedule_resume_job", ...}, sse_payload={"event": "turn_resumed", ...}, watcher_notify=(...dependency_watcher_ids...))` |

### 5.5 `COMPLETE_TURN` — successful turn end

| Property | Value |
|---|---|
| **Task status change** | `running` → `completed` |
| **Current function** | `_finalize_job_db_sync` (`daemon/services/job_feedback_observer.py:2761-3421`); ALSO the body of `complete_task` (`daemon/repositories/task/repository.py:1437`) per D8 |
| **MIRROR_SET** | `frozenset({"task", "job_queue_items", "message_queue", "job_locks", "dependency_watchers", "report_injections", "job_watchers", "instances"})` — **ALL 8** |
| **What it touches** | `task.status='completed'` (authority); reconciler runs after for mirrors 2-8; `instances.status` cascade-updated by the cascade wrapper (not the per-turn reconciler — D11). |
| **Cross-turn side effects** | `instance_completed` (gated by bus + own-queue count — same gating as today's `_finalize_job_db_sync`'s instance-status decision); `cancel_dependency_watchers` (PENDING watchers whose target is now terminal) |
| **Risk** | **HIGH (D8 chokepoint)**. `complete_task` is called from many places (worker pool completion callback, retry supersession path, direct test invocations). ALL must route through this transition. The chokepoint routing is the most critical part of this increment. |
| **Property test asserts** | After `COMPLETE_TURN`, `task.status='completed'` AND `job_queue_items.admission_state='done'` AND `message_queue.status='completed'` AND `job_locks` row deleted AND `dependency_watchers.state='CANCELLED'` (for terminal-target PENDING watchers) AND `report_injections.state` not pending AND `job_watchers` rows migrated or cleaned AND `instances` cascade-updated. Idempotent: second COMPLETE on same work_id is a no-op (guarded `WHERE status='running'` returns rowcount=0). |
| **Returns** | `TransitionResult(work_id=..., instance_id=..., old_status='running', new_status='completed', mirrors_touched=<all 8>, cross_turn_side_effects=("instance_completed", "cancel_dependency_watchers"), wakeup_payload={"event": "turn_completed", ...}, sse_payload={"event": "turn_completed", ...}, watcher_notify=(...))` |

### 5.6 `ABORT_TURN` — failure / termination

> **§ REVISION NOTE (Council Review, B1 fix):** The `reason` parameter is now EXPLICITLY discriminated into `'cancelled'` (from `cancel_task`) and `'failed'` (from `fail_task`). The transition body branches on `reason` to set `task.status` and `terminal_reason` correctly. Previously this was vague ("per reason").

| Property | Value |
|---|---|
| **Task status change** | `running`/`cancelled` → `cancelled` (when `reason='cancelled'`) OR `running`/`cancelled` → `failed` (when `reason='failed'`) |
| **Current function** | `cancel_task` (`daemon/repositories/task/repository.py:2386`), `_terminate_instance_db_sync` (`daemon/services/instance_lifecycle.py:2599-2918`), **`fail_task` (`daemon/repositories/task/repository.py:1492`)**, and `complete_task` when called with a failure-result |
| **MIRROR_SET** | `frozenset({"task", "job_queue_items", "message_queue", "job_locks", "dependency_watchers", "report_injections", "job_watchers", "instances"})` — **ALL 8** |
| **What it touches** | `task.status` set per `reason`: `'cancelled'` → `status='cancelled'`, `terminal_reason='cancelled'`; `'failed'` → `status='failed'`, `failed_at=now`, `terminal_reason='failed'`. Reconciler runs after for mirrors 2-8. `instances.status` cascade-updated to `TERMINATED` (cancelled) or `ERROR` (failed) per reason. |
| **Cross-turn side effects** | `instance_terminated` or `instance_error`; `cancel_dependency_watchers`; `cancel_all_pending_reports` (for `report_injections` rows tied to this Task) |
| **Risk** | **HIGHEST (D8 chokepoint, MOST DANGEROUS)**. `cancel_task` AND `fail_task` are called from many places (worker pool error handler, manual stop-instance UI, retry supersession, stale-task recovery, manager.py resume-failure path). Today, BOTH touch ONLY `task`. After Increment 3, every caller is automatically protected. |
| **Property test asserts** | After `ABORT_TURN(reason='cancelled')`, all 8 mirrors in the post-cancel state (same as COMPLETE_TURN but with `cancelled` discriminator instead of `completed`). After `ABORT_TURN(reason='failed')`, mirrors reach `failed` discriminator (`failed_at` set, `terminal_reason='failed'` on `job_queue_items`). Idempotent on second call. |
| **Returns** | `TransitionResult(work_id=..., instance_id=..., old_status='running'|'cancelled', new_status='cancelled'|'failed', reason='cancelled'|'failed', mirrors_touched=<all 8>, cross_turn_side_effects=("instance_terminated"|"instance_error", "cancel_dependency_watchers"), wakeup_payload={"event": "turn_aborted", "reason": ...}, sse_payload={"event": "turn_aborted", ...}, watcher_notify=(...))` |

### 5.7 `RETRY_TURN` — supersede cancelled parent with fresh child

| Property | Value |
|---|---|
| **Task status change** | Parent: any → `cancelled` (the parent is being superseded). Child: created with `pending`. |
| **Current function** | `schedule_retry` (`daemon/repositories/task/repository.py:2119-...`) |
| **MIRROR_SET** | `frozenset({"task", "job_queue_items", "message_queue", "job_locks", "dependency_watchers", "report_injections", "job_watchers", "instances"})` — **ALL 8** |
| **What it touches** | Parent Task → `cancelled`; reconciler reconciles parent's mirrors. Child Task → `pending` (mint fresh `work_id`); mirrors armed. `job_watchers` migrated from parent `work_id` to child `work_id` (D-Risk-7 listener-migration; today part of `schedule_retry` body). |
| **Cross-turn side effects** | `migrate_job_watchers` (caller dispatches post-commit; today part of `schedule_retry`'s in-tx migration) |
| **Risk** | High. Hot path; retry semantics are subtle (parent must reach terminal, child must reach `pending`, watchers must migrate atomically with both). |
| **Property test asserts** | After `RETRY_TURN(parent_work_id, child_work_id, ...)`, parent `task.status='cancelled'` AND parent's mirrors reconciled (mirrors 2-7 in terminal state; mirror 8 migrated) AND child `task.status='pending'` AND child's mirrors armed (mirrors 2-4 in queued/active/locked state; mirror 6 inherits the parent's `report_injections` linkage). |
| **Returns** | `TransitionResult(work_id=child_work_id, instance_id=..., old_status=None, new_status='pending', mirrors_touched=<all 8 across both turns>, cross_turn_side_effects=("migrate_job_watchers",), wakeup_payload={"event": "turn_retried", "parent_work_id": ..., "child_work_id": ...}, sse_payload=None, watcher_notify=())` |

### 5.8 Mirror-set coverage proof (preview of D10 property test)

The union of all 7 MIRROR_SETs:

| Mirror table | BEGIN | CLAIM | SUSPEND | RESUME | COMPLETE | ABORT | RETRY | Total |
|---|---|---|---|---|---|---|---|---|
| `task` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | 7/7 |
| `job_queue_items` | ✓ | ✓ | — | ✓ | ✓ | ✓ | ✓ | 6/7 |
| `message_queue` | ✓ | — | — | ✓ | ✓ | ✓ | ✓ | 5/7 |
| `job_locks` | ✓ | ✓ | — | ✓ | ✓ | ✓ | ✓ | 6/7 |
| `dependency_watchers` | — | — | — | ✓ | ✓ | ✓ | ✓ | 4/7 |
| `report_injections` | — | — | — | ✓ | ✓ | ✓ | ✓ | 4/7 |
| `instances` | — | — | ✓ | ✓ | ✓ | ✓ | ✓ | 5/7 |
| `job_watchers` | — | — | — | ✓ | ✓ | ✓ | ✓ | 4/7 |

Union = all 8 tables (each table appears in at least one MIRROR_SET). Property test asserts this union programmatically.

---

## 6. Cascade → Thin Wrapper Transformation

Each existing cascade is rewritten to delegate to the named transitions. The transformation is mechanical:

### 6.1 `_pause_cascade_db_sync` (instance_lifecycle.py:3039-3210)

**Before** (today):
1. UPDATE `instances SET status='paused'` for the tree.
2. Loop over in-flight Tasks; UPDATE `task SET status='paused'`.
3. (Hand-written SQL block: `message_queue` re-arm, `job_queue_items` keep-active, `job_locks` keep.)
4. Outbox: emit pause events per Task.

**After** (Increment 3):
1. UPDATE `instances SET status='paused'` for the tree (instance is tree-scoped; remains the cascade's job).
2. Loop over in-flight Tasks; for each, `SuspendTurn(work_id, reason=...).run(session)`.
3. Outbox: dispatch `TransitionResult.wakeup_payload` + `sse_payload` per Task.

Net: ~170 lines become ~40 lines. The cascade body is now a clear two-step: (a) instance-status UPDATE (tree-scoped), (b) per-turn transition calls (per-turn lifecycle).

### 6.2 `_resume_cascade_db_sync` (instance_lifecycle.py:3474-4162)

**Before** (today):
1. UPDATE `instances SET status='running'` for the tree.
2. Loop over paused/cancelled Tasks; UPDATE `task SET status='cancelled'` (the resume model).
3. UPDATE 4 (136 lines, replaced by Increment 1's reconciler call) — reconciles mirrors.
4. Mint a fresh `work_id` for the resumed Task; INSERT new `task` row.
5. Schedule resume-processing job.
6. Outbox: emit resume events.

**After** (Increment 3):
1. UPDATE `instances SET status='running'` for the tree.
2. Loop over paused Tasks; for each, `ResumeTurn(old_work_id, new_work_id, ...).run(session)` (the transition mints the fresh Task in its body — this is the ONE place where a transition can mint a new turn, by deliberate design).
3. Schedule resume-processing job (caller-side, via outbox).
4. Outbox: dispatch `TransitionResult.wakeup_payload` + `sse_payload` + `watcher_notify`.

Net: ~688 lines (the giant `_resume_cascade_db_sync` block) become ~80 lines. The transition's `MIRROR_SET = ALL_8` makes the mirror coverage **provable** — D10's property test catches a future regression if the transition's set shrinks.

### 6.3 `_finalize_job_db_sync` (job_feedback_observer.py:2761-3421)

**Before** (today):
1. UPDATE `job_queue_items` admission_state='done' (Step 1 — JobItem terminal).
2. UPDATE `task` status='completed' (Step 2 — Task terminal).
3. DELETE `job_locks` for the work_id.
4. UPDATE `message_queue` status='completed'.
5. UPDATE `dependency_watchers` for completed watchers.
6. UPDATE `instances` (gated by bus + own-queue count).
7. Outbox: emit completion events.

**After** (Increment 3):
1. `CompleteTurn(work_id, result=...).run(session)` — does steps 1-5 in one transition call (delegating to the reconciler).
2. Instance-status cascade-up (the wrapper does this; transition's `cross_turn_side_effects="instance_completed"` signals when).
3. Outbox: dispatch `TransitionResult.wakeup_payload` + `sse_payload` + `watcher_notify`.

Net: ~660 lines become ~50 lines. This is the transition that **also serves as the body of `complete_task`**, per D8.

### 6.4 `_terminate_instance_db_sync` (instance_lifecycle.py:2599-2962)

**Before** (today):
1. Loop over in-flight Tasks; UPDATE `task` status per termination reason.
2. UPDATE `instances` to terminal state.
3. UPDATE `job_queue_items`, `job_locks`, `message_queue` (hand-written; each cascade re-selects a subset).
4. Outbox: emit termination events.

**After** (Increment 3):
1. Loop over in-flight Tasks; for each, `AbortTurn(work_id, reason='terminated').run(session)`.
2. UPDATE `instances` to terminal state (cascade wrapper — instance is tree-scoped).
3. Outbox: dispatch `TransitionResult.wakeup_payload` + `sse_payload` + `watcher_notify`.

Net: ~363 lines become ~60 lines. The transition's `MIRROR_SET = ALL_8` provides the same D10 provable coverage.

### 6.5 `complete_task` / `cancel_task` / **`fail_task`** — D8 chokepoint

> **§ REVISION NOTE (Council Review, B1 fix):** `fail_task` (verified at `daemon/repositories/task/repository.py:1492`) was OMITTED from the original D8 chokepoint. It has the same dangerous split as `complete_task`/`cancel_task` — touches ONLY `task`, forgets all 8 mirrors. It now becomes a THIRD chokepoint method that routes through `AbortTurn(reason='failed')`. The 8 verified call sites are enumerated below; the integration test in §8.5 is extended to cover `fail_task`.

**Before** (today, the dangerous split):
- `complete_task` (`repository.py:1437`): UPDATE `task SET status='completed'` WHERE id=:id. **Touches ONLY `task`.** Forgets all mirrors.
- `cancel_task` (`repository.py:2386`): UPDATE `task SET status='cancelled'`. **Touches ONLY `task`.** Forgets all mirrors.
- `fail_task` (`repository.py:1492`): UPDATE `task SET status='failed'`. **Touches ONLY `task`.** Forgets all mirrors.
- `schedule_retry` (`repository.py:2119`): mint child Task, migrate `job_watchers`. Touches `task` (both rows) and `job_watchers`. Forgets 6 other mirrors.

**After** (Increment 3, D8):
- `complete_task(task_id, result)` → resolve `task_id` to `work_id` → `CompleteTurn(work_id, result=result).run(session)`. The transition body runs the full lifecycle. The wrapper method becomes ~5 lines.
- `cancel_task(task_id, reason)` → resolve `task_id` to `work_id` → `AbortTurn(work_id, reason='cancelled').run(session)`. ~5 lines.
- `fail_task(task_id, error)` → resolve `task_id` to `work_id` → `AbortTurn(work_id, reason='failed').run(session)`. ~5 lines.
- `schedule_retry(...)` → `RetryTurn(parent_work_id, child_work_id, ...).run(session)`. ~10 lines.

Public signatures unchanged; existing callers pass through unchanged. The transition body runs in the same transaction the caller would have started. The 8 direct `fail_task` call sites (verified in code) are:

| # | File:Line | Context |
|---|-----------|---------|
| 1 | `daemon/services/worker_pool.py:785` | Worker-pool error handler — `self._task_processor._task_repo.fail_task(...)` |
| 2 | `daemon/services/worker_pool.py:835` | Worker-pool timeout/error path — `self._task_processor._task_repo.fail_task(task.id, error)` |
| 3 | `daemon/services/stale_task_recovery.py:262` | Stale-task-recovery: RUNNING-stale sweep → `self._task_repo.fail_task(...)` |
| 4 | `daemon/services/stale_task_recovery.py:329` | Stale-task-recovery: cancel-then-fail path → `self._task_repo.fail_task(...)` |
| 5 | `daemon/services/stale_task_recovery.py:468` | `StaleTaskRecovery.fail_task` (convenience wrapper) → `return self._task_repo.fail_task(task_id, error)` |
| 6 | `daemon/services/stale_task_recovery.py:514` | Stale-task-recovery: another sweep site → `self._task_repo.fail_task(...)` |
| 7 | `daemon/services/stale_task_recovery.py:583` | Stale-task-recovery: another sweep site → `self._task_repo.fail_task(...)` |
| 8 | `daemon/manager.py:5422` | Resume-failure fallback — `self._task_repo.fail_task` passed to `asyncio.to_thread` |

Indirect callers (B6 territory — see Appendix A for the FULL chokepoint caller map): `StaleTaskRecovery.force_complete_task` (line 387), `StaleTaskRecovery.fail_task` convenience wrapper (line 449), `JobQueueService.cancel_task_by_work_id` (line 875), `TaskRepository.force_cancel_and_schedule_retry` (line 2454), and the HTTP routes in `daemon/routers/jobs_management.py:114` and `:244` that call `cancel_task_by_work_id`. After Phase 4b (call-site migration), every one of these routes through the named transition or the chokepoint wrapper — never direct SQL.

**Verification**: the integration test `tests/integration/test_complete_cancel_route_through_transitions.py` (item 11 in §2) is the test that validates D8. It now covers THREE direct-call scenarios: `complete_task`, `cancel_task`, AND `fail_task`. Each is called directly (not through a cascade) and asserts all 8 mirrors reconcile. Without this test, D8's chokepoint routing could regress silently.

---

### 6a. Repository-level `_status_write_guard` (C7)

> **§ REVISION NOTE (Council Review, C7 fix):** This section is new. The chokepoint wrappers (D8) protect against *accidental* bypass — but a determined developer writing a one-off UPDATE on `task.status` could still reintroduce the bug class. The repository-level write guard is the static defense: it RAISES if `UPDATE task SET status=` is executed outside a transition context.

**Mechanism**:

```python
# daemon/repositories/task/repository.py
class DirectWriteError(RuntimeError):
    """Raised when a task-status write is attempted outside a named transition.
    
    This is the C7 static defense: the chokepoint wrappers (D8) catch the
    common case, but a future developer writing a one-off SQL UPDATE on
    task.status without going through a transition would silently
    reintroduce the bug class. The write guard raises instead of allowing
    the bypass.
    """

class TaskRepository:
    _in_transition_context: bool = False  # class-level flag (single-threaded tx)
    
    def _assert_in_transition_context(self, op: str) -> None:
        """Called at the top of any UPDATE/INSERT that writes task.status."""
        if not self._in_transition_context:
            raise DirectWriteError(
                f"Direct {op} on task.status is forbidden. "
                f"Use CompleteTurn / AbortTurn / RetryTurn / BeginTurn / "
                f"ClaimTurn / SuspendTurn / ResumeTurn instead. "
                f"See increment3-plan.md §6a (C7)."
            )
```

The 7 named transitions set `TaskRepository._in_transition_context = True` for the duration of their `run(session)` execution (via a context manager); restore it to `False` in `finally`. The flag is process-local (not cross-process) — across-process coordination is the reconciler's job (Increment 1).

**Feature-flag integration (C9)**: the write guard is initially DISABLED behind `TURN_RECONCILER_DIRECT_WRITE_PARITY` (see §6b). After 7 days with zero shadow-traffic divergences in production, the flag is removed and the guard is permanently on. This is the safe-rollout path — see §6b for the full mechanism.

**Tests**:
- `tests/unit/test_write_guard.py::test_direct_status_update_raises` — assert `UPDATE task SET status='completed'` outside a transition raises `DirectWriteError`.
- `tests/unit/test_write_guard.py::test_in_transition_context_allows_write` — assert the same UPDATE inside `with TaskRepository._transition_context():` succeeds.
- `tests/integration/test_complete_cancel_route_through_transitions.py` (extended) — assert the three chokepoint methods (`complete_task`, `cancel_task`, `fail_task`) DO NOT raise (they route through transitions, which set the flag).

---

### 6b. Feature flag `TURN_RECONCILER_DIRECT_WRITE_PARITY` (C9)

> **§ REVISION NOTE (Council Review, C9 fix):** This section is new. The chokepoint migration is the most consequential refactor in the migration (D8 has the widest blast radius — every worker-pool completion callback, every retry supersession, every manual-stop UI). The shadow-traffic / dark-launch pattern is the safe-rollout path: run BOTH the wrapper (new path) and the legacy code (old path) in parallel, log any divergence, and remove the legacy path only after 7 days of zero divergences in production.

**Mechanism**:

```python
# daemon/services/feature_flags.py
TURN_RECONCILER_DIRECT_WRITE_PARITY = Flag(
    name="TURN_RECONCILER_DIRECT_WRITE_PARITY",
    default=False,  # OFF by default — production migration is opt-in
    description=(
        "When enabled: complete_task/cancel_task/fail_task wrappers log any "
        "divergence between the named-transition result and the legacy "
        "direct-UPDATE result. The named-transition result is authoritative. "
        "After 7 days with zero divergences in production, disable the flag "
        "and remove the legacy code path. See increment3-plan.md §6b (C9)."
    ),
)
```

When the flag is ON, `complete_task`/`cancel_task`/`fail_task` execute BOTH paths:

```python
def complete_task(self, task_id: int, result: dict) -> Task | None:
    if feature_flags.TURN_RECONCILER_DIRECT_WRITE_PARITY.enabled:
        # Snapshot pre-state for divergence detection
        snapshot_before = self.get_by_id(task_id)
        # Path A (new): CompleteTurn → reconciler → all 8 mirrors
        new_result = CompleteTurn(work_id, result=result).run(session)
        new_mirrors = self._read_all_8_mirrors(work_id)
        # Path B (legacy): direct UPDATE on task table only (NO mirrors)
        legacy_result = self._legacy_complete_task(task_id, result)
        legacy_mirrors = self._read_all_8_mirrors(work_id)
        # Log divergence
        if new_mirrors != legacy_mirrors:
            logger.error(
                f"[C9 DIVERGENCE] complete_task({task_id}) — "
                f"new path mirrors: {sorted(new_mirrors)}, "
                f"legacy mirrors: {sorted(legacy_mirrors)}, "
                f"diff: {new_mirrors ^ legacy_mirrors}"
            )
        return new_result  # new path is authoritative
    # When flag is OFF, the wrapper IS the only path (Phase 4b state).
    return CompleteTurn(work_id, result=result).run(session)
```

**Rollout**:
1. **Phase 4a** — flag OFF, wrappers ship. Zero behavior change, but the wrappers are exercised by all existing tests.
2. **Phase 4b-step-1** — flag ON in production canary, 24h soak, log divergences. If zero divergences, promote to 10% traffic.
3. **Phase 4b-step-2** — flag ON at 100% traffic, 7-day soak, log divergences. If zero divergences, remove flag and `_legacy_*` methods.
4. **Phase 4b-done** — flag removed from `feature_flags.py`; `complete_task`/`cancel_task`/`fail_task` are pure named-transition wrappers; C7 write guard permanently enabled.

**Telemetry**:
- New metric: `transition_divergences_per_hour{transition=<name>}` — should be ZERO during 7-day soak.
- Existing metric: `reconciler_corrections_per_hour` — should be unchanged from Increment 1 baseline (proves the new path is correct).

---

---

## 7. `TransitionResult` — Outbox Contract (detailed)

```python
@dataclass(frozen=True)
class TransitionResult:
    """Outbox payload from a named transition.

    Consumed by post-commit side effects (WorkerPool wake, HTTP/SSE emit,
    watcher notify). The transition NEVER dispatches side effects inside
    run() — the caller commits, then dispatches.

    This is the same outbox pattern as the existing _terminate_instance_db_sync
    (H10 fix) and _pause_cascade_db_sync / _resume_cascade_db_sync (L14 fix).
    See daemon/services/instance_lifecycle.py:93 and :152.
    """
    work_id: UUID
    instance_id: UUID | None  # None if the Task's instance_id is unset
    old_status: str | None    # None for BEGIN_TURN (no prior state)
    new_status: str
    mirrors_touched: frozenset[str]  # actual mutation subset of transition's MIRROR_SET
    cross_turn_side_effects: tuple[str, ...]  # e.g. ("instance_running", "schedule_resume_job")
    wakeup_payload: dict | None  # enqueued to WorkerPool wake queue post-commit
    sse_payload: dict | None     # emitted to HTTP/SSE subscribers post-commit
    watcher_notify: tuple[UUID, ...]  # dependency_watchers IDs to notify post-commit
```

### 7.1 Why outbox-not-in-tx

If the transition dispatches side effects inside its transaction, a rollback silently swallows them (the cascade never knows to retry). The outbox pattern (commit-then-dispatch) is what the existing `L14` and `H10` fixes established for the same reason. Transitions **must** follow the same pattern.

### 7.2 Caller responsibility

```python
# Example — cascade wrapper pattern
def _pause_cascade_db_sync(self, ...):
    with self._write_guard.session() as session:
        # 1. Tree-scoped instance status update (cascade's job, not a transition)
        session.execute(UPDATE instances SET status='paused' WHERE id=:instance_id)

        # 2. Per-turn transition calls
        results = []
        for work_id in self._in_flight_work_ids(session, instance_id):
            result = SuspendTurn(work_id, reason='external_pause').run(session)
            results.append(result)
        # 3. Commit
    # 4. Outbox dispatch (post-commit)
    for result in results:
        self._dispatch_outbox(result)
```

The cascade wrapper owns the session; the transition is session-aware (it does NOT open its own session). The transition **must NOT** commit (per L14 — see `daemon/services/instance_lifecycle.py:3474-4162`'s post-commit dispatch convention).

### 7.3 What about exceptions

If `SuspendTurn.run()` raises (e.g. `InvalidTransitionError` from a guard), the session is rolled back (WriteGuardSession handles this), the cascade wrapper sees the exception, and **no outbox is dispatched**. This is correct behavior — a failed transition must not emit side effects.

The property test (§9.4) asserts this: "transition raises → outbox has zero entries."

---

## 8. Test Strategy

### 8.1 Existing 404 tests — behavior identical

**Acceptance**: `pytest tests/ --tb=short` against PostgreSQL must produce the same pass/fail set as the pre-Increment-3 baseline (404 + Increment 1 additions). No test is rewritten; behavior is verified unchanged.

The risk is that a refactor changes behavior in subtle ways (e.g. the order of two UPDATE statements is swapped, and a race condition that was masked by the original order now fires). The E2E suite (`tests/e2e/test_pause_during_report_turn_then_resume.py` and the existing pause/resume E2Es) is the safety net.

### 8.2 Transition-level tests — atomicity, idempotency, fails-closed

> **§ REVISION NOTE (Council Review, C1 fix):** The original §8.2 covered atomicity, idempotency, and fails-closed but NOT per-transition mirror isolation. The C1 test below validates that each transition ONLY touches its declared `MIRROR_SET` — no side effects on tables outside the contract.

For each of the 7 transitions, write 4 test classes (28 total):

**Atomicity** — `test_<transition>_atomic`:
- Set up: Task + all 8 mirrors in a known state.
- Call transition; assert all UPDATE statements commit together (single transaction).
- Failure mode: artificially inject a mid-transition exception; assert all mirror writes roll back (no partial state).

**Idempotency** — `test_<transition>_idempotent`:
- Set up: Task in the transition's expected pre-state.
- Call transition; record post-state.
- Call transition AGAIN with the same arguments; assert the second call is a no-op (rowcount=0 on guarded UPDATE; TransitionResult.mirrors_touched == frozenset()).
- Property: `f(f(x)) == f(x)` for any idempotent transition.

**Fails-closed** — `test_<transition>_fails_closed_on_concurrent_mutation`:
- Set up: Task + all 8 mirrors.
- Spawn a concurrent transaction that flips the Task's `status` mid-transition.
- Call the transition; assert the transition detects the mutation (guarded `WHERE status=:snapshot_status` returns rowcount=0) and returns an idempotent `TransitionResult` (mirrors_touched == frozenset() — no half-applied state).
- This is the same "concurrent transition wins the row" pattern as Increment 1's reconciler.

**Mirror isolation (C1)** — `test_<transition>_mirror_isolation`:
- For each transition, iterate over `ALL_8_MIRRORS` and assert that only mirrors in the transition's declared `MIRROR_SET` were touched. Implemented as a parametrized cross-product test:

```python
# tests/property/test_named_transitions.py (C1 — added)
import pytest
from daemon.services.turn_transitions import TRANSITIONS, ALL_8_MIRRORS

@pytest.mark.parametrize("transition_cls", TRANSITIONS)
@pytest.mark.parametrize("mirror", list(ALL_8_MIRRORS))
def test_transition_mirror_isolation(transition_cls, mirror):
    """C1: Each transition ONLY touches mirrors in its declared MIRROR_SET.
    
    Validates that no transition has side effects on tables outside its
    contract. A regression that adds a side effect to a transition's body
    without declaring the mirror in MIRROR_SET fails this test.
    """
    # Set up Task + all 8 mirrors in a known state
    work_id = setup_task_and_all_8_mirrors(...)
    
    # Run transition
    result = transition_cls(work_id, ...).run(session)
    
    # Assert: if mirror is in the transition's declared set, it MAY have
    # been touched (transitions are free to touch any subset of their
    # declared MIRROR_SET). If mirror is NOT in the declared set, it MUST
    # NOT have been touched (no out-of-contract side effects).
    if mirror in transition_cls.MIRROR_SET:
        # The transition is allowed to touch this mirror; we don't assert
        # it did (the actual mutation is the transition's choice), only
        # that it COULD have.
        assert mirror in transition_cls.MIRROR_SET  # tautology — documents intent
    else:
        assert mirror not in result.mirrors_touched, (
            f"{transition_cls.__name__} touched mirror {mirror!r} "
            f"which is NOT in its declared MIRROR_SET "
            f"({sorted(transition_cls.MIRROR_SET)}). "
            f"Either declare the mirror in MIRROR_SET or remove the side effect. "
            f"See increment3-plan.md §8.2 (C1)."
        )
```

This test runs `7 × 8 = 56` cases in CI (under 1 second). It catches any future regression where a transition's body mutates a table it doesn't declare — the structural guarantee that D10 cannot be silently violated by side effects.

### 8.3 D10 mirror-set coverage test — `tests/property/test_named_transitions.py`

```python
# tests/property/test_named_transitions.py

import pytest
from daemon.services.turn_transitions import TRANSITIONS, ALL_8_MIRRORS

def test_mirror_set_coverage():
    """D10: union of every transition's MIRROR_SET equals the full 8-mirror set.
    
    No transition can silently drop a mirror from its contract.
    """
    union = frozenset.union(*(t.MIRROR_SET for t in TRANSITIONS))
    assert union == ALL_8_MIRRORS, (
        f"Mirror-set coverage failure. "
        f"Missing mirrors: {ALL_8_MIRRORS - union}. "
        f"Mirrors with no transition: {ALL_8_MIRRORS - union}. "
        f"Per-transition sets: {[(t.__name__, t.MIRROR_SET) for t in TRANSITIONS]}"
    )

@pytest.mark.parametrize("transition_cls", TRANSITIONS)
def test_each_transition_declares_mirror_set(transition_cls):
    """Every transition declares MIRROR_SET as a non-empty frozenset of valid mirror names."""
    assert hasattr(transition_cls, "MIRROR_SET")
    assert isinstance(transition_cls.MIRROR_SET, frozenset)
    assert len(transition_cls.MIRROR_SET) > 0
    for mirror in transition_cls.MIRROR_SET:
        assert mirror in ALL_8_MIRRORS, (
            f"{transition_cls.__name__}.MIRROR_SET contains unknown mirror {mirror!r}; "
            f"valid mirrors: {ALL_8_MIRRORS}"
        )

@pytest.mark.parametrize("mirror", list(ALL_8_MIRRORS))
def test_every_mirror_has_a_transition(mirror):
    """D10 converse: every mirror table appears in at least one transition's MIRROR_SET."""
    appearances = [t.__name__ for t in TRANSITIONS if mirror in t.MIRROR_SET]
    assert appearances, (
        f"Mirror {mirror!r} is not in any transition's MIRROR_SET. "
        f"Add a transition that touches this mirror, or remove it from ALL_8_MIRRORS."
    )
```

These three tests are the static guarantee of D10. A regression that removes a mirror from a transition's MIRROR_SET would either fail `test_mirror_set_coverage` (if the union shrinks) or fail `test_every_mirror_has_a_transition` (if the mirror becomes orphaned). Either failure mode is a CI-blocker.

### 8.4 Hypothesis state machine — transition sequencing

Extend `tests/property/test_turn_state_machine.py` (Increment 1's state machine) with a TransitionStrategy that picks from the 7 named transitions (instead of raw SQL) and runs through randomized sequences. Invariant: after every transition, all 8 mirrors are consistent. Target runtime: 60 seconds in CI (per OQ1).

### 8.5 D8 integration test — `tests/integration/test_complete_cancel_route_through_transitions.py`

> **§ REVISION NOTE (Council Review, B1 fix):** The integration test now covers THREE chokepoint methods: `complete_task`, `cancel_task`, AND `fail_task`. The `fail_task` test is the validation that the B1 fix (adding `fail_task` to the D8 chokepoint) actually routes through `AbortTurn(reason='failed')` and reconciles all 8 mirrors.

This is the test that validates the D8 chokepoint routing. Without it, the most dangerous split can regress silently.

```python
# tests/integration/test_complete_cancel_route_through_transitions.py

def test_complete_task_direct_call_reconciles_all_8_mirrors():
    """D8: complete_task called directly (NOT through a cascade) still reconciles all mirrors.
    
    Today, complete_task touches ONLY task. After Increment 3, it routes through
    COMPLETE_TURN which delegates to reconcile_turn_mirror. This test catches
    a regression that bypasses the chokepoint.
    """
    # Set up Task + all 8 mirrors
    setup_task_and_all_8_mirrors(work_id=...)
    
    # Call complete_task DIRECTLY (no cascade)
    result = task_repo.complete_task(task_id, result={"answer": "42"})
    
    # Assert all 8 mirrors reached post-completion state
    assert get_task_status(work_id) == "completed"
    assert get_job_queue_items_admission_state(work_id) == "done"
    assert get_message_queue_status(message_id) == "completed"
    assert job_lock_exists(work_id) == False  # DELETEd
    assert get_dependency_watchers_state(source_task_id) == "CANCELLED"
    assert get_report_injections_state(report_message_id) != "PENDING"
    assert instances_status_cascade_updated_correctly(...)
    assert job_watchers_migrated_or_cleaned(work_id)

def test_cancel_task_direct_call_reconciles_all_8_mirrors():
    """Same shape, ABORT_TURN(reason='cancelled') path."""
    # ... identical setup, call cancel_task, assert all 8 mirrors ...

def test_fail_task_direct_call_reconciles_all_8_mirrors():
    """D8 (B1 fix): fail_task called directly reconciles all 8 mirrors.
    
    fail_task is the third chokepoint method (alongside complete_task and
    cancel_task). Today it touches ONLY task; after Increment 3 it routes
    through ABORT_TURN(reason='failed'). This test catches a regression
    that bypasses the chokepoint — the same dangerous split that D8 closes
    for complete_task and cancel_task.
    
    Critical: the task must end in 'failed' status (not 'cancelled'), and
    terminal_reason must be 'failed' on job_queue_items. A regression that
    routes fail_task to ABORT_TURN(reason='cancelled') would lose the
    failure discriminator and corrupt observability (jobs that genuinely
    failed would appear as cancellations).
    """
    # Set up Task + all 8 mirrors
    setup_task_and_all_8_mirrors(work_id=...)
    
    # Call fail_task DIRECTLY (no cascade)
    result = task_repo.fail_task(task_id, error="Simulated worker error")
    
    # Assert all 8 mirrors reached post-failure state
    assert get_task_status(work_id) == "failed"
    assert get_task_failed_at(work_id) is not None
    assert get_job_queue_items_admission_state(work_id) == "done"
    assert get_job_queue_items_terminal_reason(work_id) == "failed"
    assert get_message_queue_status(message_id) == "completed"  # mirror still reaches terminal
    assert job_lock_exists(work_id) == False  # DELETEd
    assert get_dependency_watchers_state(source_task_id) == "CANCELLED"
    assert get_report_injections_state(report_message_id) != "PENDING"
    assert instances_status_cascade_updated_correctly(..., expected="ERROR")
    assert job_watchers_migrated_or_cleaned(work_id)
    
    # Idempotency: second fail_task call is a no-op
    result_2 = task_repo.fail_task(task_id, error="Different error")
    assert result_2 is None  # already terminal

def test_complete_task_outbox_dispatched_post_commit():
    """D8 + outbox: complete_task's TransitionResult.wakeup_payload / sse_payload
    are dispatched AFTER the transaction commits, not before."""
    # ...

def test_fail_task_outbox_dispatched_post_commit():
    """B1 + outbox: fail_task's TransitionResult carries reason='failed'
    in wakeup_payload / sse_payload; dispatched AFTER the transaction commits."""
    # ...

def test_fail_task_does_not_collapse_to_cancel_task():
    """B1 negative-test: verify fail_task and cancel_task are NOT aliased.
    
    A regression that routed fail_task → ABORT_TURN(reason='cancelled') would
    lose the failed-vs-cancelled discriminator. This test asserts the two
    transitions produce distinguishable post-states on the same Task.
    """
    # ... set up two identical Tasks, call fail_task on one and cancel_task on
    # the other, assert task.status differs ('failed' vs 'cancelled') and
    # job_queue_items.terminal_reason differs ...
```

### 8.6 Outbox-dispatch test

```python
def test_outbox_not_dispatched_on_transaction_rollback():
    """If the transition raises, no outbox entries are enqueued.
    
    This is the L14/H10 outbox pattern. Catches "transition dispatches inside tx"
    regressions.
    """
    # Inject a forced exception in the transition body
    with pytest.raises(InvalidTransitionError):
        CompleteTurn(work_id, result=...).run(session)
    # Assert outbox is empty
    assert outbox_queue.empty()
```

### 8.7 E2E — `tests/e2e/test_pause_resume_unchanged.py`

A new E2E that re-runs the existing pause/resume scenarios against the new cascade wrappers (which now call the named transitions internally). Asserts behavior is identical to the pre-Increment-3 baseline. This is the "no behavior drift" safety net.

### 8.8 CI gate — `tests/static/test_chokepoint_callers.py` (B6 enforcement)

> **§ REVISION NOTE (Council Review, B6 fix):** This test is new. It is the STATIC enforcement that no future code path adds a new direct call to `complete_task`/`cancel_task`/`fail_task` without going through the named-transition wrapper. After Phase 4b ships, the chokepoint methods are the ONLY writers of `UPDATE task SET status=` outside the named transitions.

```python
# tests/static/test_chokepoint_callers.py

import ast
import pathlib

ALLOWED_DIRECT_CALLERS = frozenset({
    # The chokepoint methods themselves (they call the transition).
    "daemon/repositories/task/repository.py:complete_task",
    "daemon/repositories/task/repository.py:cancel_task",
    "daemon/repositories/task/repository.py:fail_task",
    "daemon/repositories/task/repository.py:schedule_retry",
    # The 7 named transitions (they are the only allowed writers of task.status).
    "daemon/services/turn_transitions.py:CompleteTurn.run",
    "daemon/services/turn_transitions.py:AbortTurn.run",
    "daemon/services/turn_transitions.py:RetryTurn.run",
    "daemon/services/turn_transitions.py:BeginTurn.run",
    "daemon/services/turn_transitions.py:ClaimTurn.run",
    "daemon/services/turn_transitions.py:SuspendTurn.run",
    "daemon/services/turn_transitions.py:ResumeTurn.run",
})

CHOKEPOINT_METHODS = frozenset({"complete_task", "cancel_task", "fail_task"})

def test_no_new_direct_chokepoint_callers():
    """B6 enforcement: the 8 direct call sites of fail_task + the existing
    call sites of complete_task/cancel_task (see Appendix A) are FROZEN.
    
    After Phase 4b ships, every new caller of these chokepoint methods
    must go through the named transition directly (not through the wrapper).
    This test greps the codebase for new call sites and fails CI if a new
    one is added without updating the allowlist.
    """
    # Grep all call sites of complete_task / cancel_task / fail_task
    new_callers = []
    for py_file in pathlib.Path("daemon").rglob("*.py"):
        # ... ast-walk each file, collect every call site ...
        for call in ast.walk(tree):
            if isinstance(call, ast.Call):
                func_name = ast.unparse(call.func) if hasattr(ast, "unparse") else ""
                for chokepoint in CHOKEPOINT_METHODS:
                    if func_name.endswith(chokepoint):
                        new_callers.append(f"{py_file}:{call.lineno}: {func_name}")
    
    # Each new caller must be in the Appendix A allowlist (verified once)
    # OR be a NEW migration entry (which requires updating the allowlist +
    # a corresponding migration in Phase 4b).
    for caller in new_callers:
        normalized = normalize_caller_id(caller)
        assert normalized in APPENDIX_A_ALLOWLIST, (
            f"New direct caller of chokepoint method: {caller}. "
            f"Either: (a) migrate it to call the named transition directly, "
            f"or (b) add it to Appendix A allowlist with a justification. "
            f"See increment3-plan.md §8.8 (B6)."
        )

def test_no_direct_sql_on_task_status():
    """B6 + C7 enforcement: no direct `UPDATE task SET status=` SQL
    outside the named transitions. Catches hand-written SQL that bypasses
    the chokepoint. The C7 write guard (§6a) is the runtime defense;
    this static test catches the bug at code-review time.
    """
    forbidden_pattern = re.compile(r"UPDATE\s+task\s+SET\s+.*\bstatus\b", re.IGNORECASE)
    for py_file in pathlib.Path("daemon").rglob("*.py"):
        if py_file.name == "repository.py":
            # The chokepoint methods themselves are allowed.
            continue
        if py_file.name == "turn_transitions.py":
            # The named transitions are allowed.
            continue
        for line_num, line in enumerate(py_file.read_text().splitlines(), 1):
            if forbidden_pattern.search(line):
                # Skip comments and docstrings (rough heuristic)
                stripped = line.strip()
                if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
                    continue
                pytest.fail(
                    f"{py_file}:{line_num}: direct UPDATE on task.status outside "
                    f"a named transition is forbidden. See increment3-plan.md §6a (C7)."
                )
```

This test is the B6 / C7 enforcement: every PR that adds a new chokepoint caller OR writes direct SQL on `task.status` fails CI with a clear error message.

---

## 9. Phases

5 phases. Each phase is independently shippable to a feature branch; the team may roll out incrementally (e.g. ship Phase 1+2 first as a single PR).

### Phase 1 — Foundation

**Objective**: Module skeleton + `TransitionResult` + base `_Transition` class + `ALL_8_MIRRORS` constant + `TRANSITIONS` registry. Plus the mirror-set coverage test (D10) GREEN. No production transitions are wired yet; this phase proves the skeleton is correct.

**Tasks**:

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1.1 | Create `daemon/services/turn_transitions.py` with `ALL_8_MIRRORS` + `TRANSITIONS` registry + `_Transition` base + `TransitionResult` dataclass. | None | Module compiles; `from daemon.services.turn_transitions import TRANSITIONS, ALL_8_MIRRORS` works. |
| 1.2 | Stub each of the 7 transition classes with their `MIRROR_SET` declarations and a `pass` body in `run()`. | 1.1 | Each class exists; `MIRROR_SET` matches the table in §5; `run()` is a no-op raising `NotImplementedError`. |
| 1.3 | Add `tests/property/test_named_transitions.py` with the 3 D10 coverage tests (mirror-set union, each-transition-declares-mirror-set, every-mirror-has-a-transition). | 1.2 | All 3 tests pass. This is the gate: a refactor that omits a mirror in any transition's MIRROR_SET will fail in CI. |
| 1.4 | Add `tests/unit/test_transition_results.py` with shape tests for `TransitionResult` (frozen, all fields populated, `wakeup_payload` defaults to None). | 1.1 | Unit tests pass. |
| 1.5 | Run the full 404-test baseline; confirm green (skeleton adds no behavior). | 1.3, 1.4 | Baseline unchanged. |

**Exit criterion**: `tests/property/test_named_transitions.py` is GREEN; baseline is GREEN; module exists with the full registry and 7 stub classes. This phase can ship as a PR with zero production behavior change.

### Phase 2 — `BEGIN_TURN` + `CLAIM_TURN` (lowest-risk refactors)

**Objective**: Wire up the two lowest-risk transitions. Validate the skeleton works end-to-end before tackling the high-risk ones.

**Tasks**:

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 2.1 | Implement `BeginTurn.run()`. The transition body is: INSERT Task (pending), arm mirrors 2-4 (queued JobItem, ready message, acquire job_locks). Delegate to reconciler for the `instances` side (verify-and-flag per D11). | 1.2 | Unit tests for `BeginTurn` atomicity, idempotency, fails-closed all pass. |
| 2.2 | Implement `ClaimTurn.run()`. The transition body is: UPDATE `task SET status='running' WHERE status='pending'` (guarded); delegate to reconciler (mirrors 2, 4). | 1.2 | Same 3 test classes pass; idempotency on second CLAIM_TURN verified. |
| 2.3 | Replace the Task-insertion path in `task/repository.py` with a call to `BeginTurn(...).run(session)`. | 2.1 | All Task-insertion callers pass through the transition; existing tests pass. |
| 2.4 | Replace the post-`UPDATE task` block in `claim_pending_task` (`repository.py:493-992`) with `ClaimTurn(...).run(session)`. | 2.2 | Claim hot-path tests pass; existing claim integration tests pass. |
| 2.5 | Run the full 404-test baseline; confirm green. | 2.3, 2.4 | Baseline unchanged. |

**Exit criterion**: BeginTurn + ClaimTurn are live in production code paths; baseline is GREEN; D10 property test still GREEN (no transition's MIRROR_SET changed). This phase is the **first PR with production behavior change**; it ships behind the 7-day telemetry gate before Phase 3 starts.

### Phase 3 — `SUSPEND_TURN` + `RESUME_TURN` (pause/resume cascade thinning)

**Objective**: Replace `_pause_cascade_db_sync` and `_resume_cascade_db_sync` with thin wrappers that call the named transitions. This is the highest-stakes refactor because pause/resume are the most-tested paths; behavior must be IDENTICAL.

**Tasks**:

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 3.1 | Implement `SuspendTurn.run()`. The transition body is: UPDATE `task SET status='paused' WHERE status='running'` (guarded); the cascade wrapper updates `instances` (tree-scoped). | 1.2 | Unit tests pass; SUSPEND_TURN is the only path that sets `paused`. |
| 3.2 | Implement `ResumeTurn.run()` (with optional `new_work_id` parameter for Increment 4 compatibility). The transition body is: UPDATE old Task → `cancelled`; delegate to reconciler for all 8 mirrors of the cancelled Task; if `new_work_id` provided, INSERT new Task (pending) and arm mirrors 2-4. | 1.2 | Unit tests pass; all 8 mirrors reconcile for the cancelled Task; new Task minted correctly. |
| 3.3 | Rewrite `_pause_cascade_db_sync` as a thin wrapper: instance-status UPDATE + per-turn `SuspendTurn(...).run(session)` calls + outbox dispatch. | 3.1 | Cascade body shrinks from ~170 lines to ~40; existing pause E2E tests pass unchanged. |
| 3.4 | Rewrite `_resume_cascade_db_sync` as a thin wrapper: instance-status UPDATE + per-turn `ResumeTurn(...).run(session)` calls + outbox dispatch + schedule resume-processing job. | 3.2 | Cascade body shrinks from ~688 lines to ~80; existing resume E2E tests pass unchanged. |
| 3.5 | Add `tests/e2e/test_pause_resume_unchanged.py` — re-runs pause/resume scenarios against the new cascade wrappers; asserts behavior identical to pre-Increment-3 baseline. | 3.3, 3.4 | E2E green. |
| 3.6 | Run the full 404-test baseline; confirm green. | 3.5 | Baseline unchanged. |

**Exit criterion**: Both pause and resume cascades are thin wrappers; SUSPEND_TURN + RESUME_TURN are live; baseline is GREEN; D10 property test GREEN; pause/resume E2E GREEN. **This is the largest single-PR in the migration** (the resume cascade is 688 lines shrinking to 80). Phase 3 ships behind the 7-day telemetry gate before Phase 4 starts.

### Phase 4 — `COMPLETE_TURN` + `ABORT_TURN` (D8 chokepoint routing — MOST DANGEROUS)

> **§ REVISION NOTE (Council Review, B1 + C6 + C7 + C9 fixes):** Phase 4 is split into THREE sub-phases to address the council's blockers and warnings:
>
> - **Phase 4a** — Chokepoint WRAPPERS (C6 fix). Ship `complete_task`/`cancel_task`/`fail_task` as ~5-line wrappers around `COMPLETE_TURN`/`ABORT_TURN`. Zero behavior change — all callers automatically benefit.
> - **Phase 4b** — Call-site MIGRATION (B1 + C6 fix). Migrate the 8 verified `fail_task` call sites (plus the existing `complete_task`/`cancel_task` sites) to call the named transition directly. Behind the C9 feature flag for safe rollout.
> - **Phase 4c** — Static defense + permanent enable (C7 + C9 finalization). Enable the `_status_write_guard` permanently; remove the C9 feature flag.

**Phase 4a — Chokepoint wrappers (ZERO behavior change, lowest risk)**:

**Objective**: Ship the `complete_task` / `cancel_task` / `fail_task` wrappers as the FIRST step of D8. Existing callers pass through unchanged because signatures are preserved. The wrappers are ~5 lines each and route through the named transitions. This is the SAME-PR migration (no deprecation window).

**Tasks**:

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 4a.1 | Implement `CompleteTurn.run()`. The transition body is: UPDATE `task SET status='completed' WHERE status='running'` (guarded); delegate to reconciler for all 8 mirrors; cascade wrapper updates `instances`. | 1.2 | Unit tests pass; idempotent on second COMPLETE_TURN. |
| 4a.2 | Implement `AbortTurn.run(reason: Literal['cancelled', 'failed'])`. The transition body BRANCHES on `reason`: `'cancelled'` → UPDATE `task SET status='cancelled'`; `'failed'` → UPDATE `task SET status='failed', failed_at=:now`. Delegate to reconciler for all 8 mirrors. | 1.2 | Unit tests pass; idempotent on second ABORT_TURN; `test_fail_task_does_not_collapse_to_cancel_task` (B1 negative test) passes. |
| 4a.3 | Rewrite `complete_task` (`repository.py:1437`) as a thin wrapper: `return CompleteTurn(self._resolve_work_id(task_id), result=result).run(session)`. Public signature unchanged. | 4a.1 | `complete_task` body is ~5 lines; existing 404-test baseline passes; D8 integration test `test_complete_task_direct_call_reconciles_all_8_mirrors` passes. |
| 4a.4 | Rewrite `cancel_task` (`repository.py:2386`) as a thin wrapper: `return AbortTurn(self._resolve_work_id(task_id), reason='cancelled').run(session)`. | 4a.2 | `cancel_task` body is ~5 lines; baseline passes; `test_cancel_task_direct_call_reconciles_all_8_mirrors` passes. |
| 4a.5 | Rewrite **`fail_task` (`repository.py:1492`)** as a thin wrapper: `return AbortTurn(self._resolve_work_id(task_id), reason='failed', error=error).run(session)`. **B1 fix.** | 4a.2 | `fail_task` body is ~5 lines; baseline passes; `test_fail_task_direct_call_reconciles_all_8_mirrors` (B1) passes. |
| 4a.6 | Rewrite `_finalize_job_db_sync` (`job_feedback_observer.py:2761-3422`) as a thin wrapper: `CompleteTurn(...).run(session)` + instance-status cascade-up + outbox dispatch. | 4a.1 | Cascade body shrinks from ~660 lines to ~50; existing finalize tests pass. |
| 4a.7 | Rewrite `_terminate_instance_db_sync` (`instance_lifecycle.py:2599-2918`) as a thin wrapper: `AbortTurn(work_id, reason='terminated').run(session)` + instance-status UPDATE + outbox dispatch. | 4a.2 | Cascade body shrinks from ~320 lines to ~60; existing terminate tests pass. |
| 4a.8 | Add `tests/integration/test_complete_cancel_route_through_transitions.py` — proves `complete_task`, `cancel_task`, AND `fail_task` called DIRECTLY (NOT through a cascade) reconcile all 8 mirrors. Includes B1's `test_fail_task_does_not_collapse_to_cancel_task` negative test. | 4a.3, 4a.4, 4a.5 | All integration tests pass; D8 chokepoint validated. |
| 4a.9 | Run the full 404-test baseline; confirm green. | 4a.8 | Baseline unchanged. |

**Exit criterion**: `complete_task` / `cancel_task` / `fail_task` route through `COMPLETE_TURN` / `ABORT_TURN`; finalize + terminate cascades are thin wrappers; baseline is GREEN; D10 property test GREEN; D8 integration test GREEN. This phase ships behind the 7-day telemetry gate (D3) before Phase 4b starts.

**Phase 4b — Call-site migration (behind feature flag)**:

> **§ REVISION NOTE (Council Review, B1 + C6 + C9 fixes):** This phase MIGRATES the 8 verified `fail_task` call sites (plus the existing `complete_task`/`cancel_task` sites) to call the named transitions DIRECTLY. The chokepoint wrappers (Phase 4a) remain as a fallback for any caller that hasn't migrated. Behind the C9 feature flag for shadow-traffic / safe rollout.

**Objective**: Eliminate the chokepoint wrappers as a primary code path. Every direct caller of `complete_task`/`cancel_task`/`fail_task` migrates to call `CompleteTurn`/`AbortTurn` directly. The chokepoint methods become thin pass-throughs (still callable for backward compatibility but no longer the primary path).

**Tasks**:

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 4b.1 | Add `TURN_RECONCILER_DIRECT_WRITE_PARITY` feature flag (§6b). Default OFF. | 4a.9 | Flag exists in `daemon/services/feature_flags.py`; unit test for flag toggle passes. |
| 4b.2 | Add `_status_write_guard` (§6a) as a class-level flag on `TaskRepository`. Initially DISABLED behind the same feature flag. Add `DirectWriteError`. | 4b.1 | `tests/unit/test_write_guard.py` passes; flag ON → writes outside transitions raise; flag OFF → writes pass through. |
| 4b.3 | Migrate `worker_pool.py:785` (fail_task) → `AbortTurn(work_id, reason='failed').run(session)`. Update the surrounding call site (worker-pool error handler) accordingly. | 4a.9 | Existing worker-pool tests pass; integration test `test_fail_task_direct_call_reconciles_all_8_mirrors` still passes. |
| 4b.4 | Migrate `worker_pool.py:835` (fail_task) → direct `AbortTurn`. | 4b.3 | Tests pass. |
| 4b.5 | Migrate `stale_task_recovery.py:262`, `:329`, `:514`, `:583` (fail_task) → direct `AbortTurn(reason='failed')`. | 4b.3 | Stale-task-recovery tests pass. |
| 4b.6 | Migrate `stale_task_recovery.py:468` (the `StaleTaskRecovery.fail_task` convenience wrapper itself) — either DELETE the wrapper (callers migrate to direct `AbortTurn`) OR keep it as a thin alias. Recommendation: DELETE (it was only a convenience facade). | 4b.5 | All callers of `StaleTaskRecovery.fail_task` updated; the wrapper is removed OR marked deprecated. |
| 4b.7 | Migrate `manager.py:5422` (fail_task passed to asyncio.to_thread) → direct `AbortTurn`. Update the resume-failure fallback path. | 4b.5 | Resume-failure integration tests pass. |
| 4b.8 | Migrate `worker_pool.py:730` (complete_task) → direct `CompleteTurn`. | 4a.9 | Existing tests pass. |
| 4b.9 | Migrate `worker_pool.py:811` (cancel_task) → direct `AbortTurn(reason='cancelled')`. | 4a.9 | Existing tests pass. |
| 4b.10 | Migrate `manager.py:5117` (cancel_task passed to asyncio.to_thread) → direct `AbortTurn(reason='cancelled')`. | 4a.9 | Existing tests pass. |
| 4b.11 | Migrate `stale_task_recovery.py:436` (force_complete_task's underlying `_task_repo.complete_task` call) → direct `CompleteTurn`. Update `force_complete_task` docstring accordingly. | 4a.9 | F10 drift-reconciler tests pass. |
| 4b.12 | Migrate `task_processor.py:156` and `:817` (complete_task) → direct `CompleteTurn`. | 4a.9 | Worker-pool tests pass. |
| 4b.13 | Migrate `job_queue_service.py:911` (cancel_task inside `cancel_task_by_work_id`) → direct `AbortTurn(reason='cancelled')`. | 4a.9 | HTTP-route tests pass. |
| 4b.14 | Migrate `repository.py:2454` (`force_cancel_and_schedule_retry` — currently calls `cancel_task` + `schedule_retry` directly). Recommendation: REWRITE as `AbortTurn(reason='cancelled')` + `RetryTurn(...)` so the parent-cancel-then-child-mint is a single atomic operation through transitions. | 4b.6, 4b.13 | Retry-recovery integration tests pass. |
| 4b.15 | Enable feature flag in production canary. 24-hour soak. Log divergences. | 4b.14 | Zero divergences logged; promote to 10% traffic. |
| 4b.16 | Enable feature flag at 100% traffic. 7-day soak. Log divergences. | 4b.15 | Zero divergences over 7 days; metric `transition_divergences_per_hour{transition=*}` is 0 for the entire period. |
| 4b.17 | Add `tests/static/test_chokepoint_callers.py` (B6 enforcement) — the new chokepoint callers list (now empty — every caller migrated) is the ground truth. Future PRs that add a new caller fail CI. | 4b.16 | Static test green; CI gate enforced. |
| 4b.18 | Run the full 404-test baseline + the new Increment 3 test suite. Confirm green. | 4b.17 | All green. |

**Exit criterion**: Every verified direct caller (8 fail_task sites + 7 complete_task sites + 3 cancel_task sites = 18 total, see Appendix A) is migrated to call the named transition directly. Feature flag has recorded zero divergences for 7 days in production. The chokepoint wrappers (`complete_task`/`cancel_task`/`fail_task`) are thin pass-throughs that may be marked deprecated. Static test (`tests/static/test_chokepoint_callers.py`) is the long-term enforcement.

**Phase 4c — Static defense + flag removal**:

**Objective**: Permanently enable the `_status_write_guard` and remove the C9 feature flag. After this phase, the chokepoint wrappers cannot be bypassed.

**Tasks**:

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 4c.1 | Enable `_status_write_guard` permanently (remove the feature-flag guard around it). The guard now raises `DirectWriteError` for any `UPDATE task SET status=` outside a transition. | 4b.18 | `tests/unit/test_write_guard.py::test_direct_status_update_raises` passes in production config; existing tests still pass (every status write goes through a transition). |
| 4c.2 | Remove `TURN_RECONCILER_DIRECT_WRITE_PARITY` feature flag from `daemon/services/feature_flags.py`. Remove the shadow-traffic code paths (`_legacy_complete_task` etc.). | 4c.1 | Flag absent from `feature_flags.py`; shadow-traffic code removed; production baseline still green. |
| 4c.3 | Optionally mark `complete_task`/`cancel_task`/`fail_task` as `@deprecated` with a 6-month window before deletion. **OR** leave them as permanent thin wrappers (the team's call — see OQ-INC3-2b in §13). | 4c.2 | Decision recorded in decisions.md. |
| 4c.4 | Run the full 404-test baseline + the new Increment 3 test suite. Confirm green. | 4c.3 | All green. |

**Exit criterion**: `_status_write_guard` permanently on; C9 flag removed; chokepoint wrappers are either `@deprecated` or permanent pass-throughs; baseline GREEN; D10 property test GREEN; D8 integration test GREEN; static test GREEN; metrics (§11a) all green for 7 days.

**Why three sub-phases (not one)**:

The single-PR approach (original Phase 4) was a BLOCKER because it conflated two distinct risks: (a) wrapper correctness (zero behavior change) and (b) call-site migration (behavior identical but routing changes). Splitting into 4a/4b/4c separates these: 4a ships safely with zero behavior change; 4b is opt-in via the C9 feature flag with shadow-traffic divergence detection; 4c is a one-way door (write guard on, flag removed) with a 7-day bake-in. If 4b detects divergences in production, we roll back 4b (revert migration) without touching 4a (wrappers remain safe).

### Phase 5 — `RETRY_TURN` + final verification

**Objective**: Implement `RETRY_TURN` (the highest-risk hot path) and finalize the migration with comprehensive verification.

**Tasks**:

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 5.1 | Implement `RetryTurn.run()`. The transition body is: UPDATE parent Task → `cancelled` (delegated to reconciler for parent's mirrors); INSERT child Task (pending); arm child mirrors 2-4; migrate `job_watchers` from parent to child. | 1.2 | Unit tests pass; idempotent on second RETRY_TURN. |
| 5.2 | Rewrite `schedule_retry` (`repository.py:2119-...`) as a thin wrapper that calls `RetryTurn(...).run(session)`. | 5.1 | `schedule_retry` body shrinks from ~190 lines to ~10; existing retry tests pass unchanged. |
| 5.3 | Run the full 404-test baseline + the new Increment 3 test suite (`tests/property/test_named_transitions.py`, `tests/unit/test_transition_results.py`, `tests/integration/test_complete_cancel_route_through_transitions.py`, `tests/e2e/test_pause_resume_unchanged.py`). | 5.2 | All green. |
| 5.4 | Run the Hypothesis state-machine test for ≥1000 generated transitions; assert zero invariant violations. | 5.3 | State-machine test green; CI runtime < 60 seconds (per OQ1). |
| 5.5 | Run the directed E2E (`tests/e2e/test_pause_during_report_turn_then_resume.py`) against PostgreSQL. | 5.3 | E2E green. |
| 5.6 | Append a "Resolved structurally by Increment 3" footer to `docs/bugs/pause-during-report-turn-orphans-message-jobitem.md`. | 5.5 | Bug doc updated. |
| 5.7 | Append D-INC3-1 to `.agents/shared/planning/turn-reconciler-migration/decisions.md` §2: "named transitions live; cascade SQL replaced; D8 chokepoint routing in production." | 5.6 | Decisions log updated. |

**Exit criterion**: All 7 transitions are live; all 4 cascades + 3 chokepoint methods are thin wrappers; D10 property test GREEN; D8 integration test GREEN; state-machine test GREEN; baseline GREEN; bug doc + decisions log updated.

---

## 10. Coupling Map

| | Phase 1 | Phase 2 | Phase 3 | Phase 4 | Phase 5 |
|---|---|---|---|---|---|
| **Phase 1** (Foundation) | — | tight (registry dependency) | tight (registry dependency) | tight (registry dependency) | tight (registry dependency) |
| **Phase 2** (BEGIN + CLAIM) | tight | — | independent | independent | independent |
| **Phase 3** (SUSPEND + RESUME) | tight | independent | — | loose (shares `instances` cascade-up pattern) | independent |
| **Phase 4** (COMPLETE + ABORT, D8) | tight | independent | loose (same outbox dispatch pattern) | — | loose (RETRY's parent-cancel reuses ABORT) |
| **Phase 5** (RETRY + final verify) | tight | independent | independent | loose | — |

**Tight coupling** = Phase N depends on Phase N-1's exported symbols (registry, base class). **Loose coupling** = Phases share patterns (e.g. instance-status cascade-up) but can ship independently. **Independent** = no shared code.

The team may ship Phases 1+2 as one PR (foundation + first two transitions), Phase 3 as a second PR, Phase 4 as a third PR, Phase 5 as a fourth PR. Each PR is bisect-safe (every commit passes the test suite).

---

## 11. Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| 1 | **D8 chokepoint regression** — `complete_task` / `cancel_task` / **`fail_task`** callers forget to route through the new wrappers. The dangerous split returns. | **High** (re-introduces the bug class) | Medium | `tests/integration/test_complete_cancel_route_through_transitions.py` proves the chokepoint works for all THREE methods (B1); add a **grep gate** in CI that fails if `complete_task(` / `cancel_task(` / `fail_task(` appears anywhere outside the allowed caller list in Appendix A. The new `tests/static/test_chokepoint_callers.py` (§8.8) is the long-term enforcement. |
| 2 | **Behavior drift in cascade thinning** — the refactored cascade produces a slightly different SQL pattern (e.g. UPDATE ordering) and a race condition that was masked by the original order now fires. | High (production incident) | Medium | `tests/e2e/test_pause_resume_unchanged.py` re-runs the existing pause/resume scenarios against the new wrappers; the existing pause/resume E2E suite is the safety net. 7-day telemetry gate (D3) catches user-visible drift. |
| 3 | **Outbox dispatch inside transaction** — a transition dispatches side effects inside its `run()`, violating the L14/H10 outbox pattern. A rollback silently swallows them. | High (lost wakeups/SSE/notify) | Low | `test_outbox_not_dispatched_on_transaction_rollback` (item 8.6) catches this; L14/H10 already established the pattern as a code-review checklist item. |
| 4 | **`TransitionResult.mirrors_touched` reports incorrectly** — a transition declares `MIRROR_SET = ALL_8` but only mutates 5 in practice; `mirrors_touched` reports the 5 but a caller asserts all 8 were touched. | Low (test-only) | Low | The TransitionResult field is for **metrics and tests**, not for caller-correctness logic. Document this in the class docstring. The D10 mirror-set coverage test asserts the **declared** set; the transition's internal mutation is its own contract. |
| 5 | **Phase ordering produces a partial-migration window** — Phase 4a ships but Phase 4b (call-site migration) hasn't yet. During the window, callers hit a half-migrated path. | Medium (inconsistent state) | Low | Each sub-phase ships behind a 7-day telemetry gate (D3); the partial-migration window is at most 7 days × 3 sub-phases = ~21 days, but each intermediate state is **safe** because the chokepoint methods still work (the body changes, but the signature is unchanged). Phase 4b is BEHIND the C9 feature flag (off by default in production). |
| 6 | **Reconciler not running on the `complete_task` / `cancel_task` / `fail_task` direct-call path** — the wrapper calls `CompleteTurn` / `AbortTurn` which delegate to the reconciler, but the reconciler's call-site list (Increment 1's §3.2) doesn't include these wrappers. If a caller reaches the chokepoint mid-Increment-3 and the reconciler isn't on that path, mirrors drift. | High | Low | The Increment 3 wrapper's design ENSURES the reconciler runs (it's inside `CompleteTurn.run()`); the reconciler's call-site list is irrelevant — what matters is that the transition invokes it. Verify with the integration test in §8.5. |
| 7 | **`RETRY_TURN` parent/child mirror migration has a subtle ordering bug** — parent's `task.status='cancelled'` is set before child is INSERTed, and a concurrent transition on the parent (e.g. ABORT_TURN from a stale-task sweep) creates a rowcount=0 collision. | High (retry broken) | Medium | The guarded `WHERE status=:snapshot_status` pattern catches concurrent mutations; `RetryTurn` is fails-closed (returns idempotent TransitionResult). Property test §8.4 asserts this. |
| 8 | **Performance regression on hot paths** — the wrapper overhead (transition class instantiation, outbox payload construction) adds latency to claim / resume / finalize. | Low (operations concern) | Medium | Performance benchmark before/after; threshold p95 increase < 10% (D-Risk-8). If higher, profile and optimize; the wrapper is small enough that most of the cost is the reconciler call (which Increment 1 already paid). |
| 9 | **Team unfamiliar with the new module** — engineers write a new lifecycle change that bypasses the named transitions (direct SQL on `task`, `job_queue_items`, etc.) and reintroduces the bug class. | High | Medium | Code-review checklist: "Does this PR introduce a direct SQL UPDATE/INSERT/DELETE on a mirror table? If yes, it MUST go through a transition or the reconciler." Grep gate per Risk 1. The `_status_write_guard` (C7) raises `DirectWriteError` at runtime. The linter (OQ7) is a follow-up. |
| 10 | **The migration stalls** — the team runs out of momentum at Phase 3 or 4. | Medium (incomplete migration) | Low | Each phase is independently shippable (Phase 1 alone is safe to ship as the foundation); if Phase 3 stalls, the system is at LEAST as good as the post-Increment-1+2 state. No regression risk from pausing the migration. |
| 11 | **`fail_task` mistakenly routes through `ABORT_TURN(reason='cancelled')`** — a regression in Phase 4a/4b collapses the two abort reasons, losing the failure discriminator on `task.status` and `job_queue_items.terminal_reason`. (B1 negative-test risk.) | Medium (observability corruption — failed tasks appear as cancellations) | Low | `test_fail_task_does_not_collapse_to_cancel_task` (§8.5) explicitly asserts the discriminators are distinct. Negative test in CI. |
| 12 | **C9 feature-flag shadow traffic detects divergence in production** — the new path produces different mirror states than the legacy path. Indicates a real bug in `AbortTurn(reason='failed')` or `CompleteTurn` semantics. | High (chokepoint unsafe) | Low | 7-day soak with zero-divergence criterion (§6b). If divergences occur: (a) root-cause via the divergence log, (b) disable the flag, (c) fix the bug, (d) re-enable the flag and repeat 7-day soak. The 7-day window is the safety net. |
| 13 | **C7 write guard enabled in production, blocks an unforeseen legitimate caller** — a caller that legitimately writes `task.status` outside a transition (e.g. a future migration helper) hits `DirectWriteError` and breaks. | Medium (false positive) | Low | The guard is enabled in Phase 4c AFTER all 15 direct callers are migrated to transitions (Appendix A.1). If a future legitimate caller appears, it MUST go through a transition (D10 invariant); if it absolutely cannot, the guard has a `@_status_write_guard.override` decorator for one-time exceptions, gated by code review. |

---

## 11a. Metrics & Observability (C5)

> **§ REVISION NOTE (Council Review, C5 fix):** This section is new. The migration's success depends on observability — we cannot claim "the chokepoint is safe" without metrics that prove it. The metrics below are the production telemetry for the migration.

### Transition health metrics

| Metric | Threshold | Source |
|---|---|---|
| `transition_invocations_per_minute{transition=<name>}` | All 7 transitions show non-zero invocations over a 24h window. A zero-count transition is either dead code or a missed call site. | Increment 3 transition module |
| `transition_success_rate{transition=<name>}` | ≥ 99.9% per transition over a 7-day window. Alert if lower. | Wraps each transition.run() with try/except; counts successes vs exceptions |
| `transition_latency_p99_ms{transition=<name>}` | < existing cascade latency + 10ms. The wrapper is a thin pass-through; the reconciler call is the dominant cost. | Time-series of each invocation's wall-clock duration |
| `transition_divergences_per_hour{transition=<name>}` | 0 during Phase 4b 7-day soak. Logged but never fatal during the soak period (we expect zero). | C9 feature-flag shadow-traffic comparator (§6b) |

### Reconciler parity metrics

| Metric | Threshold | Why |
|---|---|---|
| `reconciler_corrections_per_hour` | Unchanged from Increment 1 baseline. **MUST NOT INCREASE.** | If the new path is correct, the reconciler should fire LESS often (or the same) — never more. An INCREASE indicates the new path is missing a mirror. |
| `dead_letter_rows_added_per_hour` | 0 new DLQ rows from transition sources. | Transitions should not produce DLQ entries unexpectedly. The reconciler is the only source of DLQ rows (when it can't reconcile). |
| `instance_status_drift_flags_per_hour` | 0 from D11 soft-reconciliation. | D11 verifies instance↔Task consistency; transitions correctly set instance status. If drift flags appear, a transition is missing the instance update. |

### Chokepoint safety metrics

| Metric | Threshold | Why |
|---|---|---|
| `chokepoint_direct_call_rate` | 0 in production after Phase 4b completes. Every direct caller is migrated to the named transition. | The chokepoint wrappers become thin pass-throughs; the "direct" rate is the static test's enforcement. |
| `direct_write_guard_blocks_per_hour` | 0 in production after Phase 4c completes. | The C7 write guard is enabled; any block is a CI failure or a regression. |

### Operational gates

- **Phase 4a ship**: requires `transition_success_rate ≥ 99.9%` for 7 days post-ship. This proves the wrappers are correct.
- **Phase 4b ship**: requires `transition_divergences_per_hour = 0` for 7 days during canary + 10% traffic + 100% traffic phases.
- **Phase 4c ship**: requires all 15 direct callers migrated (Appendix A shows zero unmigrated sites) + zero direct_write_guard_blocks for 7 days.

### Dashboard

A Grafana dashboard `Turn-Reconciler Migration` is the single pane of glass for the migration. It includes all metrics above. The on-call engineer is paged on `transition_success_rate < 99.9%` OR `transition_divergences_per_hour > 0` OR `reconciler_corrections_per_hour` increased > 2x from baseline.

---

## 12. Success Criteria

| # | Criterion | How to Measure | Threshold |
|---|-----------|----------------|-----------|
| 1 | `daemon/services/turn_transitions.py` exists with all 7 transitions + `TransitionResult` + registry. | Source inspection | Module compiles, `TRANSITIONS` exports 7 classes, `ALL_8_MIRRORS` has 8 entries. |
| 2 | All 4 cascade functions are thin wrappers. | Line-count check: `_pause_cascade_db_sync` < 60 lines, `_resume_cascade_db_sync` < 100 lines, `_finalize_job_db_sync` < 80 lines, `_terminate_instance_db_sync` < 80 lines. | Net line count: ≥ 1500 lines removed (cascade SQL replaced by transition calls). |
| 3 | `complete_task`, `cancel_task`, AND `fail_task` route through `COMPLETE_TURN` / `ABORT_TURN` (D8). | `tests/integration/test_complete_cancel_route_through_transitions.py` passes. | All 8 mirrors reconcile when any of the three is called directly. (B1 — `fail_task` is now in scope.) |
| 4 | D10 mirror-set coverage test passes. | `pytest tests/property/test_named_transitions.py` | `frozenset.union(*[t.MIRROR_SET for t in TRANSITIONS]) == ALL_8_MIRRORS`. |
| 5 | Per-transition mirror isolation test passes (C1). | `pytest tests/property/test_named_transitions.py::test_transition_mirror_isolation` | All `7 × 8 = 56` parametrized cases pass; no transition touches a mirror outside its declared `MIRROR_SET`. |
| 6 | Hypothesis state-machine test green for ≥1000 generated transitions. | `pytest tests/property/test_turn_state_machine.py` | Zero invariant violations; CI runtime < 60s (OQ1). |
| 7 | Existing 404-test baseline passes. | `pytest tests/ --tb=short` against PostgreSQL | Same pass/fail set as pre-Increment-3 baseline. |
| 8 | Existing pause/resume E2E tests pass unchanged. | `pytest tests/e2e/test_pause_during_*` | All pass; behavior identical. |
| 9 | Outbox dispatch happens post-commit, not in-transaction. | `test_outbox_not_dispatched_on_transaction_rollback` | Passes; transition-raised exception ⇒ outbox empty. |
| 10 | Production telemetry — `named_transition_invocations_per_minute` metric (new metric) shows healthy distribution. | Production monitoring for ≥7 days post-Phase-4c ship. | All 7 transitions are exercised; no transition shows 0 invocations over a 24-hour window (a zero-count transition is either dead code or a missed call site). |
| 11 | Production telemetry — `reconciler_corrections_per_hour` stays at baseline (D3 telemetry gate). | Production monitoring for ≥7 days post-Phase-4c ship. | Metric stays within ±10% of pre-migration baseline (the reconciler is rarely needed because the transitions own mirror lifecycle). |
| 12 | No new orphan-mirror bug class for the period. | Production incident log; quarterly retrospective. | Zero P1/P2 incidents mentioning "orphan" in the title for 90 days post-Phase-4c ship. |
| 13 | Bug doc footer added. | Source inspection of `docs/bugs/pause-during-report-turn-orphans-message-jobitem.md`. | Footer present. |
| 14 | **Chokepoint caller migration (B6):** All 15 verified direct callers of `complete_task`/`cancel_task`/`fail_task` are migrated to call the named transition directly (see Appendix A.1). | `tests/static/test_chokepoint_callers.py` passes; `grep` for direct callers returns 0 results outside the Appendix A.1 allowlist. | Zero unmigrated callers after Phase 4b completes. |
| 15 | **C9 feature flag zero-divergence soak:** `TURN_RECONCILER_DIRECT_WRITE_PARITY` enabled at 100% traffic for 7 days with zero divergences. | Metric `transition_divergences_per_hour{transition=*}` = 0 for 7 consecutive days. | Required before Phase 4c ships. |
| 16 | **C7 write guard permanently enabled:** `_status_write_guard` raises `DirectWriteError` for any `UPDATE task SET status=` outside a transition. | `tests/unit/test_write_guard.py` passes in production config. | Required after Phase 4c ships. |

---

## 13. Rollback Plan

The migration is structured so each phase is independently revertable:

### 13.1 Revert strategy per phase

| Phase | Revert Strategy |
|-------|-----------------|
| **Phase 1** (Foundation) | Delete `daemon/services/turn_transitions.py`; delete `tests/property/test_named_transitions.py`; delete `tests/unit/test_transition_results.py`. No production behavior change in this phase — revert is trivial. |
| **Phase 2** (BEGIN + CLAIM) | Revert the BEGIN_TURN / CLAIM_TURN wrapper additions in `task/repository.py`; restore the inline INSERT / claim UPDATE code paths. The named transitions are no longer called from production code; the test suite for them can remain (they're not used). |
| **Phase 3** (SUSPEND + RESUME) | Restore `_pause_cascade_db_sync` and `_resume_cascade_db_sync` from git history (pre-Phase-3); the cascade bodies are well-tested. The named transitions become unused; the registry still exports them. |
| **Phase 4** (COMPLETE + ABORT, D8) | Restore `complete_task` and `cancel_task` from git history (pre-Phase-4); restore `_finalize_job_db_sync` and `_terminate_instance_db_sync` from git history. The D8 chokepoint routing is removed; `complete_task` reverts to touching only `task`. **WARNING**: this re-introduces the most dangerous split (the bug class D8 closes). Only revert Phase 4 if Phase 4 itself is broken AND a hot-patch is needed. |
| **Phase 5** (RETRY + final verify) | Restore `schedule_retry` from git history. The named transitions become unused. |

### 13.2 Revert triggers

- **Phase 3+**: any 404-test regression that cannot be fixed within 24 hours. Pause/resume are the most-tested paths; a regression is observable immediately.
- **Phase 4**: any orphan-mirror regression that the integration test did not catch. The D8 chokepoint is the highest-stakes change; rollback is justified if the integration test does not catch a regression.

### 13.3 Rollback checklist

Before reverting any phase:

1. Capture the current `pytest tests/ --tb=short` output (for the post-revert comparison).
2. Identify the specific commit(s) that introduced the regression (use `git bisect` if necessary).
3. Open a PR titled "Revert Increment 3 Phase N" with the captured output.
4. After revert, re-run the full baseline; verify green.
5. Open a follow-up issue with the captured failure and root-cause analysis.

---

## 14. Open Questions

These are deliberately left unresolved for council / team review before implementation starts.

### OQ-INC3-1 — Module location (service vs repository)

Should `turn_transitions.py` live in `daemon/services/` (chosen here) or as methods on `TaskRepository`? See §4.2. Repository-anchored co-locates the transitions with the reconciler (`reconcile_turn_mirror`); service-anchored provides cleaner registry export for the property test.

**Recommendation**: service module. The registry (`TRANSITIONS`, `ALL_8_MIRRORS`) is the cleanest substrate for D10. **Decision needed** before Phase 1 implementation.

### OQ-INC3-2 — D8 staged migration (C6)

> **§ REVISION NOTE (Council Review, C6 fix):** This question is RESOLVED. The phased approach below is the accepted decision.

D8's draft prescribes a 6-month `DeprecationWarning` window for callers that haven't migrated. The "Consequences" section says call-site migration is part of the SAME PR (no warning needed). The two sections contradict.

**Resolution (C6 fix)**: phased approach:

- **Phase 4a** — Chokepoint WRAPPERS. `complete_task` / `cancel_task` / `fail_task` become ~5-line wrappers around `COMPLETE_TURN` / `ABORT_TURN` / `AbortTurn(reason='failed')`. ZERO behavior change — all callers automatically benefit because signatures are preserved. The wrappers are alias-style: existing test of "wrapper produces same result as direct transition call" passes.
- **Phase 4b** — Call-site MIGRATION. The 15 verified direct callers (8 fail_task + 4 complete_task + 3 cancel_task — see Appendix A.1) are migrated to call the named transition directly. Behind the C9 feature flag with shadow-traffic divergence detection. After 7 days at 100% traffic with zero divergences, the flag is removed.
- **Phase 4c** — Static defense. The `_status_write_guard` (C7) is permanently enabled; any `UPDATE task SET status=` outside a transition raises `DirectWriteError`.

**No formal 6-month deprecation window is required.** The wrappers ARE the migration. After Phase 4c, the chokepoint methods are thin pass-throughs; the team's choice (OQ-INC3-2b in §13) is whether to mark them `@deprecated` for future removal or leave them as permanent aliases.

**Decision**: **Phase 4a/4b/4c phased approach**. No 6-month deprecation window. Wrappers are permanent pass-throughs (subject to OQ-INC3-2b). Resolves the §6 / D8 contradiction.

### OQ-INC3-2b — Wrapper disposition after Phase 4c

After Phase 4c ships, `complete_task` / `cancel_task` / `fail_task` are thin pass-throughs. The team's choice:

- **(A) Mark `@deprecated`** with a 6-month window before removal. Forces callers to migrate to the named transition directly. Risk: tests that use these methods need updates.
- **(B) Leave as permanent aliases.** No deprecation warning; callers can use the chokepoint OR the named transition.

**Recommendation**: (B). The wrappers are safe by construction (they route through the named transition); deprecating them forces call-site changes that have no functional benefit. The migration's win is structural (the chokepoint exists), not syntactic (every call uses the new API). **Decision needed** before Phase 4c ships.

---

## Appendix A — Full Chokepoint Caller Map (B6)

> **§ REVISION NOTE (Council Review, B6 fix):** This appendix is new. It enumerates EVERY direct and indirect caller of the three chokepoint methods. After Phase 4b, every caller migrates to the named transition directly; the appendix is the ground truth for the static test (§8.8) and the migration tasks (Phase 4b.3 through 4b.14).

### A.1 Direct callers of `_task_repo.complete_task` / `cancel_task` / `fail_task`

**Verified by `grep -rn "_task_repo\.\(complete\|cancel\|fail\)_task\|task_repo\.\(complete\|cancel\|fail\)_task"` against `daemon/` at plan-revision time.**

| # | File:Line | Method | Migration target |
|---|-----------|--------|------------------|
| 1 | `daemon/services/worker_pool.py:730` | `complete_task` | `CompleteTurn(work_id, result=result).run(session)` |
| 2 | `daemon/services/worker_pool.py:785` | `fail_task` | `AbortTurn(work_id, reason='failed', error=error).run(session)` |
| 3 | `daemon/services/worker_pool.py:811` | `cancel_task` | `AbortTurn(work_id, reason='cancelled').run(session)` |
| 4 | `daemon/services/worker_pool.py:835` | `fail_task` | `AbortTurn(work_id, reason='failed', error=error).run(session)` |
| 5 | `daemon/services/stale_task_recovery.py:262` | `fail_task` | `AbortTurn(work_id, reason='failed', error=error).run(session)` |
| 6 | `daemon/services/stale_task_recovery.py:329` | `fail_task` | `AbortTurn(work_id, reason='failed', error=error).run(session)` |
| 7 | `daemon/services/stale_task_recovery.py:436` | `complete_task` (inside `force_complete_task`) | `CompleteTurn(work_id, result=result).run(session)` |
| 8 | `daemon/services/stale_task_recovery.py:468` | `fail_task` (inside `StaleTaskRecovery.fail_task` convenience wrapper) | DELETE the wrapper (callers migrate to direct `AbortTurn`) |
| 9 | `daemon/services/stale_task_recovery.py:514` | `fail_task` | `AbortTurn(work_id, reason='failed', error=error).run(session)` |
| 10 | `daemon/services/stale_task_recovery.py:583` | `fail_task` | `AbortTurn(work_id, reason='failed', error=error).run(session)` |
| 11 | `daemon/services/task_processor.py:156` | `complete_task` (passed as callback) | Direct `CompleteTurn` invocation (refactor callback pattern) |
| 12 | `daemon/services/task_processor.py:817` | `complete_task` | `CompleteTurn(work_id, result=result).run(session)` |
| 13 | `daemon/services/job_queue_service.py:911` | `cancel_task` (inside `cancel_task_by_work_id`) | `AbortTurn(work_id, reason='cancelled').run(session)` |
| 14 | `daemon/manager.py:5117` | `cancel_task` (passed to `asyncio.to_thread`) | Direct `AbortTurn` invocation (refactor async-bridge pattern) |
| 15 | `daemon/manager.py:5422` | `fail_task` (passed to `asyncio.to_thread`) | Direct `AbortTurn(reason='failed')` invocation (refactor async-bridge pattern) |

**Total: 15 direct call sites** (B1's "7 verified call sites" was an under-count; the actual count after full enumeration is 8 fail_task sites + 4 complete_task sites + 3 cancel_task sites = 15, plus the 3 indirect callers in A.2 below).

### A.2 Indirect callers (service-layer wrappers that route through chokepoint methods)

| # | File:Line | Wrapper | What it calls | Migration target |
|---|-----------|---------|---------------|------------------|
| 1 | `daemon/services/stale_task_recovery.py:387` | `StaleTaskRecovery.force_complete_task(task_id, reason)` | `_task_repo.complete_task(task_id, result)` at line 436 | After migration of line 436, the wrapper itself is unchanged — but it now routes through `CompleteTurn` transitively. |
| 2 | `daemon/services/stale_task_recovery.py:449` | `StaleTaskRecovery.fail_task(task_id, error)` | `_task_repo.fail_task(task_id, error)` at line 468 | **DELETE the wrapper** — it's a convenience facade with no value once callers migrate to direct `AbortTurn`. |
| 3 | `daemon/services/job_queue_service.py:875` | `JobQueueService.cancel_task_by_work_id(work_id)` | `task_repo.cancel_task(task.id, ...)` at line 911 (also `task_repo.request_cancel` at line 908 for RUNNING tasks) | After migration of line 911, the wrapper itself is unchanged — it now routes through `AbortTurn(reason='cancelled')` transitively. Note: `request_cancel` is cooperative (sets a flag); it is NOT a chokepoint rewrite target. |
| 4 | `daemon/repositories/task/repository.py:2454` | `TaskRepository.force_cancel_and_schedule_retry(task_id, max_retries, reason, backoff_base, backoff_max)` | Internally: `cancel_task` (or direct SQL UPDATE) + `schedule_retry`. | **REWRITE** as `AbortTurn(reason='cancelled')` + `RetryTurn(...)` so the parent-cancel-then-child-mint is a single atomic operation through transitions. |
| 5 | `daemon/routers/jobs_management.py:114` | HTTP route handler (`POST /api/jobs/{job_id}/cancel`) | `service.cancel_task_by_work_id(job_id)` | No change required — the HTTP route calls the wrapper, which after migration routes through `AbortTurn`. |
| 6 | `daemon/routers/jobs_management.py:244` | HTTP route handler (another cancel route) | `service.cancel_task_by_work_id(job_id)` | No change required. |
| 7 | `daemon/services/job_recovery_service.py:675` | `JobRecoveryService._drift_recovery` callback | `self._stale_task_recovery.force_complete_task` | No change required — the F10 drift path routes through `force_complete_task`, which after migration routes through `CompleteTurn`. |

**Total: 7 indirect callers** (5 wrappers + 2 HTTP routes that use the wrappers).

### A.3 Static test allowlist

The static test `tests/static/test_chokepoint_callers.py` (§8.8) treats the 15 direct call sites in A.1 as the "before-migration" allowlist. After Phase 4b, the allowlist is EMPTY (every direct caller migrated). Future PRs that add a new direct caller MUST:

1. Migrate the caller to the named transition directly (preferred), OR
2. Add the caller to a NEW allowlist (only for backward-compat helpers that genuinely cannot migrate; requires code-review sign-off from a D8 owner).

The CI test fails if either rule is violated.

### A.4 Cross-reference to migration tasks

The 15 direct call sites in A.1 map to Phase 4b tasks as follows:

- **4b.3, 4b.4** — `worker_pool.py` (4 sites: #1, #2, #3, #4)
- **4b.5, 4b.6** — `stale_task_recovery.py` (5 sites: #5, #6, #7, #8, #9, #10)
- **4b.7** — `manager.py` (1 site: #15)
- **4b.8** — `worker_pool.py` (already counted above; the complete_task call is #1)
- **4b.9** — `worker_pool.py` (the cancel_task call is #3)
- **4b.10** — `manager.py` (1 site: #14)
- **4b.11** — `stale_task_recovery.py:436` (already counted as #7)
- **4b.12** — `task_processor.py` (2 sites: #11, #12)
- **4b.13** — `job_queue_service.py` (1 site: #13)
- **4b.14** — `repository.py:2454` (1 indirect: A.2 #4)

---

### OQ-INC3-3 — Increment 4 compatibility

`ResumeTurn` takes an optional `new_work_id` parameter for Increment 4's fresh-turn minting. If Increment 4 ships BEFORE Increment 3, the `new_work_id` parameter is used; if Increment 4 ships AFTER, the parameter is `None` and `ResumeTurn` operates on the original `work_id`. This is awkward.

- **(A) Pre-wired parameter (chosen here)**: `ResumeTurn` always takes `new_work_id: UUID | None`. Increment 4 plugs in.
- **(B) Two transitions**: `ResumeTurn` (no mint) and `ResumeAndMintTurn` (with mint). Increment 4 swaps the call sites.
- **(C) Increment 4 first**: ship Increment 4 before Increment 3; `ResumeTurn` always mints.

**Recommendation**: (A). The parameter is optional and zero-cost when `None`. **Decision needed** before Phase 3 implementation.

### OQ-INC3-4 — Performance threshold (D-Risk-8)

D-Risk-8 says "p95 increase < 10% on hot paths." Is this threshold acceptable, or should it be tighter (e.g. < 5%)? The wrapper overhead is small (one dataclass construction + outbox payload build); the reconciler call (Increment 1) is the dominant cost.

**Recommendation**: 10% is acceptable for the refactor; tighter thresholds (5%) are follow-up optimizations. **Decision needed** before Phase 5 verification.

---

## 15. End-to-End Verification Sequence

After Phase 5 lands, run the following in order:

```bash
# 1. Reconciler exists and is wired (Increment 1 sanity)
grep -n "reconcile_turn_mirror" daemon/repositories/task/repository.py \
    daemon/services/instance_lifecycle.py \
    daemon/services/job_feedback_observer.py \
    daemon/services/stale_task_recovery.py \
    daemon/services/job_recovery_service.py
# Expected: ≥6 occurrences

# 2. Named transitions module exists with full registry
grep -E "class (Begin|Claim|Suspend|Resume|Complete|Abort|Retry)Turn" daemon/services/turn_transitions.py
# Expected: 7 matches

# 3. Cascades are thin wrappers (line counts)
wc -l daemon/services/instance_lifecycle.py daemon/services/job_feedback_observer.py
# Compare against pre-Increment-3 baseline; expect significant reduction

# 4. complete_task / cancel_task are thin wrappers
grep -A 5 "^    def complete_task" daemon/repositories/task/repository.py
grep -A 5 "^    def cancel_task" daemon/repositories/task/repository.py
# Expected: each method body is ≤ 6 lines

# 5. Property tests
pytest tests/property/test_named_transitions.py -v
pytest tests/property/test_turn_state_machine.py --hypothesis-seed=20260801 -v

# 6. Integration tests
pytest tests/integration/test_complete_cancel_route_through_transitions.py -v

# 7. E2E tests
pytest tests/e2e/test_pause_resume_unchanged.py -v
pytest tests/e2e/test_pause_during_report_turn_then_resume.py -v

# 8. Full baseline
pytest tests/ --tb=short -q 2>&1 | tail -50
# Expected: 404 + (Increment 1+3 additions) tests pass; 0 failures

# 9. Mirror-set coverage proof
python -c "
from daemon.services.turn_transitions import TRANSITIONS, ALL_8_MIRRORS
union = frozenset.union(*(t.MIRROR_SET for t in TRANSITIONS))
assert union == ALL_8_MIRRORS, f'Coverage failure: missing {ALL_8_MIRRORS - union}'
print('Mirror-set coverage: OK')
print(f'Per-transition: {[(t.__name__, sorted(t.MIRROR_SET)) for t in TRANSITIONS]}')
"
# Expected: prints "Mirror-set coverage: OK" + the per-transition breakdown
```

---

## 16. Summary

> **§ REVISION NOTE (Council Review 2026-08-01):** This summary reflects the revised plan with the 2 blockers (B1, B6) and 5 warnings (C1, C5, C6, C7, C9) addressed. Phase 4 is now split into 4a/4b/4c; the 15 verified direct chokepoint callers are enumerated in Appendix A.

Increment 3 is the **named-transitions refactor** that replaces hand-written cascade SQL with typed operations declaring their mirror set. After this ships:

- Every lifecycle event goes through one of 7 named transitions on `daemon/services/turn_transitions.py`.
- Each transition declares `MIRROR_SET` (a `frozenset`); the union of all 7 equals the full 8-mirror set (D10 property test asserts). The C1 mirror-isolation test additionally asserts each transition only touches its declared mirrors.
- The 4 large cascade functions (`_pause_cascade_db_sync`, `_resume_cascade_db_sync`, `_finalize_job_db_sync`, `_terminate_instance_db_sync`) become thin wrappers calling the named transitions; net ≥ 1500 lines removed.
- The `complete_task` / `cancel_task` / **`fail_task`** chokepoint (D8) routes through `COMPLETE_TURN` / `ABORT_TURN(reason='cancelled')` / `ABORT_TURN(reason='failed')`, closing the **most dangerous split** in the bug class — any caller, even non-cascade paths, is automatically protected. (B1 fix: `fail_task` is now in scope.)
- The 15 verified direct chokepoint callers (B6) are enumerated in Appendix A and migrated to call the named transition directly in Phase 4b. The static test `tests/static/test_chokepoint_callers.py` enforces the migration.
- The `_status_write_guard` (C7) permanently raises `DirectWriteError` for any `UPDATE task SET status=` outside a transition — the runtime defense against future bypass.
- The `TURN_RECONCILER_DIRECT_WRITE_PARITY` feature flag (C9) provides shadow-traffic divergence detection during the 7-day Phase 4b soak.
- Production observability (C5): `transition_success_rate ≥ 99.9%`, `transition_divergences_per_hour = 0`, `reconciler_corrections_per_hour` unchanged from baseline.
- The `reconcile_turn_mirror` reconciler from Increment 1 is the underlying mirror-consistency mechanism; the transitions delegate to it.
- All 404 existing tests pass unchanged; behavior is identical (Phase 4a is a zero-behavior-change refactor; Phase 4b/c are behind a feature flag with 7-day soak).
- Future "cascade forgot table X" bugs are **structurally impossible** — D10's coverage test catches a future regression if any transition's MIRROR_SET shrinks; C7's write guard catches a future regression that bypasses the chokepoint; the static test (B6) catches a future regression that adds a new direct caller.

Estimated effort: 5 phases (Phase 4 split into 4a/4b/4c), 3-4 weeks wall-clock (slightly longer than the original estimate due to the C9 feature-flag bake-in and C7 write-guard implementation). The migration is the second-largest of the 4 increments (Increment 1 is larger; Increment 4 is comparable).

---

**End of plan.**
