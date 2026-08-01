# Follow-up: Turn-Reconciler Migration Bridge Strategy

Date: 2026-08-01
Status: Planned (post point-fix unblock)
Parent plan: [`plan-overview.md`](./plan-overview.md)
Bridge target: [`docs/plans/turn-reconciler-named-transitions.md`](../../../docs/plans/turn-reconciler-named-transitions.md)

---

## 1. Bridge Strategy

The point-fix plan (`fix-pause-report-turn-orphan/`) is the **immediate unblock**: surgical
hardening of the existing guard / carve-out / cascade paths that close 4 root causes
behind the 2026-08-01 production deadlock. The turn-reconciler plan
(`docs/plans/turn-reconciler-named-transitions.md`) is the **structural fix** — a single
reconciliation primitive plus named transitions that make the orphan-mirror bug class
structurally impossible. The two are **not** alternatives; they are sequenced.

**The hybrid bridge strategy is:**

1. **Point-fixes ship first** to unblock production (Phase 1 + Phase 2 of the
   `fix-pause-report-turn-orphan/` plan). They are the minimum needed to recover the
   leader instance and prevent recurrence on the current code paths.
2. **Turn-reconciler migration is a planned follow-up** — a separate effort with its
   own timeline, scope (LARGE; ~6 production files + a new property-test harness),
   and review gate. It is *not* on the critical path for the 2026-08-01 incident.
3. **The point-fixes are NOT throwaway.** They are the regression baseline that the
   reconciler will be measured against. Specifically, they establish:
   - The **`work_id`-keyed correlation axis** that the reconciler uses as its primary
     handle (Phase 1 Step B's `find_resume_root_candidate_by_active_job` already
     returns the terminal backing `PROCESS_MESSAGE` Task carrying `work_id` +
     `message_id` — the exact shape the reconciler's `reconcile_turn_mirror(work_id)`
     consumes).
   - The **`NOT EXISTS` / positive-condition orphan-detection pattern** (the canonical
     SQL predicate in `plan-overview.md §Shared Pattern`) that the reconciler
     subsumes into one routine. Both phases converge on `NOT EXISTS (SELECT 1 FROM
     task t WHERE t.message_id = <correlation_key> AND t.status IN ('pending',
     'running', 'paused'))`; the reconciler's snapshot-and-guard-update is the
     productionized form of this predicate.
   - The **shared predicate helper** (the single SQL helper interpolated at both
     `claim_pending_task` and `has_pending_tasks_blocked_by_busy_instance`, per the
     P1/F11 "MUST agree" invariant) becomes the reconciler's core detection logic.
   - The **test scenarios** — truth tables (active+running, active+paused,
     active+paused-via-message_id), production-state seeds (the exact 2026-08-01 DB
     snapshot from `docs/bugs/pause-during-report-turn-orphans-message-jobitem.md`
     Production Evidence), and the E2E `test_pause_during_report_turn_then_resume` —
     become the reconciler's regression suite (§9.3 "Fuzz seed for the bug" in the
     design doc).

**Why this ordering:** the design doc's own recommendation (§11 Sequencing & risk) is
to ship "Phase 1 + Phase 5 + Phase 9 first as one increment. That alone converts
every existing orphan-mirror bug into a self-healing no-op and gives the test surface
to safely cut the carve-outs (Phase 4)." The point-fix plan delivers Phase 5 +
property-test foundation *now*, in surgical form, on the existing code paths. The
reconciler then layers on top without re-litigating which tests prove safety.

---

## 2. Turn-Reconciler Approach (Brief Summary)

The full design lives in [`docs/plans/turn-reconciler-named-transitions.md`](../../../docs/plans/turn-reconciler-named-transitions.md).
This is the executive read.

**The core concept.** Today, the same logical fact ("this turn is paused / done /
cancelled") is stored in up to four mirror tables (`task`, `message_queue`,
`job_queue_items`, `job_locks`, `dependency_watchers`) whose updates are hand-written
SQL statements — each transition picks a different subset, leaves the rest orphaned,
and the bug surfaces weeks later. The reconciler introduces a single
`reconcile_turn_mirror(work_id)` routine in the repository layer that **owns** the
"every mirror row tied to this turn must match the Task's terminal status" rule. It
is called from claim, resume, finalize, timeout, and a periodic startup sweep.
Within one transaction, it reads `task.status` once, then issues `WHERE`-guarded
UPDATEs on every mirror keyed by `work_id` (or `message_id`); if a concurrent
transition mutates the Task between read and UPDATE, the `WHERE task.status =
:snapshot_status` guard drops rowcount to 0 and the reconciler logs and returns
(same idempotency pattern as `complete_task`). The five phases (1: Reconciler, 2:
Named transitions, 3: Turn handle, 4: Delete carve-outs, 5: PG invariant test
visibility, plus §9 property tests) are independently shippable, but the
recommended ship order is **Phase 1 + Phase 5 + Phase 9 first**.

**How it eliminates the orphan class.** Once the reconciler runs on every lifecycle
event, the "cascade forgot table X" class becomes structurally dead — new
transitions only have to call the reconciler, not enumerate mirrors. The carve-out
pile in `claim_pending_task` and `has_pending_tasks_blocked_by_busy_instance`
(Phase 4 of the design doc) can then be **deleted**, not widened. The routing
inference in `find_paused_or_running_by_instance` (Phase 3) can be replaced by a
turn handle (`suspension_reason` + `resume_target_turn_id`) so resume targets
*that* row instead of inferring root-vs-child from task statuses.

**Why this is a follow-up rather than the immediate fix.** Three reasons:

1. **Scope.** LARGE — spans `daemon/repositories/task/`, `daemon/services/instance_lifecycle.py`,
   `daemon/services/job_feedback_observer.py`, `daemon/services/child_reports.py`,
   `daemon/manager.py`, plus a new property-test harness. It touches hot paths
   (claim, resume, finalize) and requires a new column on `task`.
2. **Risk.** The hot-path refactor is medium-risk even with the additive-first
   strategy (Phase 1 runs alongside existing guards). Property tests are the
   safety net; we need the test surface first. The point-fix plan delivers that
   test surface (truth tables, production-state seeds, E2E
   `test_pause_during_report_turn_then_resume`) on the existing code paths so the
   reconciler's Phase 1 can land with confidence.
3. **Timeline.** The reconciler's Phase 1 alone is an additive new method plus
   reconciler calls at six call sites — a multi-week effort with its own review
   cycle. Production is unblocked sooner with the point-fixes.

**Council decision:** ship the point-fixes NOW (Phase 1 + Phase 2 of
`fix-pause-report-turn-orphan/`), and track the reconciler as a planned follow-up
with its own plan file (`docs/plans/turn-reconciler-named-transitions.md`).

---

## 3. Point-Fix → Reconciler Mapping

Each point-fix in `fix-pause-report-turn-orphan/` is a **defensive** or
**structural** hardening on a specific code path. The reconciler **subsumes** each
one — the point-fix becomes redundant once the reconciler's corresponding phase
ships. This table is the bridge: it lets reviewers verify that nothing in the
point-fix plan is wasted work, and it tells the reconciler team exactly which tests
must continue passing as they cut the carve-outs.

| Point-Fix (This Plan) | Root Cause | Reconciler Successor | Relationship |
|---|---|---|---|
| **Phase 1 Step A** — guard carve-out broadened in `claim_pending_task` (`:742-763`) + `has_pending_tasks_blocked_by_busy_instance` (`:1465-1500`) to admit `active` JobItems whose backing Task is terminal, via shared `NOT EXISTS` predicate | **RC2** — Guard gap (cross-system guard blocks fresh `process_message` when instance has `active` JobItem; existing carve-outs don't fire for `(active JobItem, completed Task)`) | **Design doc Phase 4** — Delete the carve-out pile; the broadened exclusion is replaced by the reconciler's "is the JobItem still active?" query (which returns `False` because the reconciler has already transitioned the orphan to `done`). **Backed by Phase 1** (the reconciler routine that does the transition). | **Subsumed.** Once Phase 1 runs on every claim/resume/finalize/timeout and Phase 4 deletes the `NOT EXISTS` subquery, the broadened carve-out is no longer required. The point-fix code is removed in Phase 4, not before. |
| **Phase 1 Step B** — new repository primitive `find_resume_root_candidate_by_active_job` returning the terminal `PROCESS_MESSAGE` Task (with `work_id` + `message_id`), used by `resume_processing_job` as a fallback routing signal when `find_paused_or_running_by_instance` returns `None` | **RC1** — Routing gap (resume misroutes to child branch because `find_paused_or_running_by_instance` filters on `process_message` only and misses the report-turn-pause state) | **Design doc Phase 3** — Turn suspension handle (`suspension_reason` enum + `resume_target_turn_id` column on `task`); new resume flow `find_suspended_turn_for_answer(answer.message_id)` replaces status-inference primitives. **Backed by Phase 1** (the reconciler cleans the stale `active` JobItem once the correct branch is selected). | **Subsumed.** Phase 3 turns "infer root-vs-child from task statuses" into "target the turn by handle"; the inference primitive `find_paused_or_running_by_instance` is *deleted* per §6.4. The point-fix's fallback primitive is removed when Phase 3 lands. |
| **Phase 2 Task 1** — UPDATE 4 added to `_resume_cascade_db_sync` (`instance_lifecycle.py:3519-3527`): `NOT EXISTS` subquery against `task.message_id` reconciles orphaned `processing`/`retrying` rows to `completed` inside the existing `WriteGuardSession` | **RC3** — Cascade gap (`_pause_cascade_db_sync` and `_resume_cascade_db_sync` update only `instances` + `task` (+ `job_queue_items` on resume); never touch `message_queue` → `processing` rows become permanent orphans) | **Design doc Phase 1** — `reconcile_turn_mirror(work_id)` owns the `message_queue.status` reconciliation rule (`If Task ∈ terminal → status='completed', processing_task_id=NULL, completed_at=now`); called from `_resume_cascade_db_sync` and `_pause_cascade_db_sync` after UPDATE 2. | **Subsumed.** The reconciler's `message_queue` rule is the productionized form of UPDATE 4's `NOT EXISTS` predicate — but applied uniformly across all callers, not just the resume cascade. The point-fix's UPDATE 4 is removed when the reconciler's `_resume_cascade_db_sync` integration lands. |
| **Phase 2 Tasks 4–6** — guard hardening at 3 sites (`child_reports.py:1459-1519` Site 1B root completion, `:862-922` legacy `_update_parent_on_child_complete`, `error_reporting.py:269-324`): broadened `pending_count` guard excludes `processing`/`retrying` rows whose backing Task is terminal | **RC4** — Guard gap (root-completion `pending_count` guard counts `processing`/`retrying` rows with no join to `task` → cannot distinguish in-flight from orphaned → instance stuck at `WAITING_CHILDREN` forever) | **Design doc Phase 1** + **Phase 5** — Phase 1: reconciler pre-empts the guard by marking orphan rows `completed` (the guard never sees them). Phase 5: the `active ⇔ JobLock` invariant becomes test-visible on SQLite (Python-side check in `reconcile_turn_mirror`); the orphan-detection pattern's positive condition is enforced at runtime, not just at read-time. | **Subsumed (defense-in-depth).** Once Phase 1 lands, the guard rarely encounters orphans because they are reconciled upstream. The broadened guard remains as a second line of defense (the design doc explicitly preserves it as conservative semantics — "no-Task rows are still counted"). |

**Cross-cutting shared artifact — `work_id` correlation axis.** Phase 1 Step B's
decision (D-A-3, [`decisions.md`](./decisions.md)) returns `Task.work_id` from the
new primitive because "the existing root path in `resume_processing_job` consumes
`Task.work_id` (passed as `old_job_id` to `_resume_processing_background` →
`_process_resume_finalize`)." This decision is **load-bearing** for the reconciler:
the reconciler's `reconcile_turn_mirror(work_id)` takes the same handle. The
point-fix plan therefore establishes the API the reconciler will consume, without
waiting for the reconciler's Phase 3 handle columns.

**Cross-cutting shared artifact — shared predicate helper (claim + busy-probe).**
Phase 1 Step A's "single shared SQL helper interpolated at both
`claim_pending_task` and `has_pending_tasks_blocked_by_busy_instance`" (per the
P1/F11 invariant in the risk table) is the structural ancestor of the reconciler's
core detection logic. Phase 4 of the design doc deletes the helper; Phase 1 of the
design doc subsumes its semantic into `reconcile_turn_mirror`. The point-fix
preserves the *invariant* (both sites agree); the reconciler preserves the
*invariant* while removing the *need* for the helper.

**Cross-cutting shared artifact — test scenarios.** The point-fix plan introduces
four classes of tests that the reconciler's property-test harness (§9) consumes
verbatim:

| Test class (point-fix) | Reconciler use (§9 design doc) |
|---|---|
| Truth-table tests — `active+running`, `active+paused`, `active+paused-via-message_id` negative cases pinning F1 bifurcation | §9.2 Invariant #2 ("No orphan mirrors: for every `task` row in terminal status, every mirror row tied to its `work_id` / `message_id` is also terminal or absent") and §9.2 Invariant #3 ("No permanent deadlock") |
| Production-state seeds — the 2026-08-01 DB snapshot from `docs/bugs/pause-during-report-turn-orphans-message-jobitem.md` Production Evidence | §9.3 Fuzz seed for the bug ("pause fires during a `process_report` turn → resume → answer arrives") |
| E2E `test_pause_during_report_turn_then_resume` — the missing scenario test the bug doc calls out | §6.5 Phase 3 acceptance ("New E2E `pause_during_report_turn_then_resume`") |
| Defense-in-depth negative test (Phase 2 Task 13) — monkeypatch UPDATE 4 to no-op and assert the broadened guard alone resolves the orphan | §4.5 Phase 1 acceptance ("reconciler unit tests: idempotent on re-run; no-op when mirrors already agree") |

---

## 4. Migration Path

The point-fix plan is the **bridge**: it does not block the reconciler, and it does
not become throwaway. The migration has four observable transitions.

### 4.1 The `work_id`-keyed correlation becomes the reconciler's primary axis

- **Point-fix establishes:** Phase 1 Step B's `find_resume_root_candidate_by_active_job`
  returns the terminal `PROCESS_MESSAGE` Task with `work_id` + `message_id`. Phase 2
  Task 1's UPDATE 4 correlates `message_queue.message_id` against `task.message_id`
  via `NOT EXISTS`. The point-fix code already uses `work_id` as the cross-table
  correlation key.
- **Reconciler adopts:** `reconcile_turn_mirror(work_id)` (§4.1 design doc) is keyed
  on `work_id`; all four mirror UPDATEs join on `work_id` (or `message_id` for
  `message_queue`). The point-fix's correlation pattern is the *contract* the
  reconciler inherits.
- **What happens at migration:** no code change required to the point-fix code —
  the API surface is preserved. The reconciler's call sites consume the same
  `work_id`.

### 4.2 The shared predicate helper becomes the reconciler's core detection logic

- **Point-fix establishes:** a single SQL helper interpolates the `NOT EXISTS`
  orphan-exclusion predicate at both `claim_pending_task` and
  `has_pending_tasks_blocked_by_busy_instance` (the P1/F11 "MUST agree" invariant).
  The predicate is `NOT EXISTS (SELECT 1 FROM task t WHERE t.message_id =
  <correlation_key> AND t.status IN ('pending', 'running', 'paused'))`.
- **Reconciler adopts:** the same predicate becomes the *where-clause* of every
  mirror UPDATE in `reconcile_turn_mirror`. The reconciler reads `task.status` once
  per transaction, then issues `WHERE`-guarded UPDATEs that *are* the predicate's
  productionized form. The "single shared helper" becomes "single routine owned by
  the reconciler."
- **What happens at migration (Phase 4 of design doc):** the shared helper is
  *deleted*. The predicate's truth table — `PENDING/RUNNING/PAUSED` is in-flight,
  anything else is orphan — moves from inline SQL strings to the reconciler's
  transaction body. The P1/F11 invariant ("both methods agree") becomes trivially
  true because both methods are now thin wrappers around the reconciler's call.

### 4.3 The test scenarios become the reconciler's regression suite

- **Point-fix introduces:** truth-table tests, production-state seeds, the
  `test_pause_during_report_turn_then_resume` E2E, and the defense-in-depth
  negative test (Phase 2 Task 13). All run on both SQLite and PostgreSQL.
- **Reconciler adopts:** the design doc's §9 property-test harness explicitly
  names these as the fuzz seed for the bug (§9.3) and the invariants they prove
  (§9.2 Invariants #2 and #3). The property test asserts "for any reachable state,
  after any sequence of pause/resume/abort events, claim_pending_task must ... leave
  zero orphan mirrors" — the truth tables are the enumeration of reachable states.
- **What happens at migration:** the point-fix tests become the *seed corpus* for
  Hypothesis-style fuzzing. They are not deleted; they are wrapped by the property
  test and continue to run as deterministic regression tests. The production-state
  seed (the exact 2026-08-01 DB snapshot) becomes a *fixture factory* reused by
  both the point-fix suite and the reconciler suite.

### 4.4 The point-fix code is removed when the reconciler takes over — not before

- **Sequencing constraint:** the point-fix code MUST remain in production until the
  reconciler's corresponding phase has shipped AND its property tests pass against
  the point-fix tests' truth tables. Specifically:
  - **Phase 1 Step A's broadened carve-out** stays until design doc Phase 4
    (delete the carve-out pile) lands. Phase 4 depends on Phase 1 (the reconciler
    routine) being stable.
  - **Phase 1 Step B's fallback primitive** stays until design doc Phase 3 (turn
    handle) lands. Phase 3 depends on Phase 2 (named transitions) which depends on
    Phase 1.
  - **Phase 2 Task 1's UPDATE 4** stays until design doc Phase 1 (reconciler in
    `_resume_cascade_db_sync`) lands. The reconciler's call is a superset of
    UPDATE 4 (it also handles pause cascade, finalize, timeout, startup sweep).
  - **Phase 2 Tasks 4–6's broadened guards** stay as defense-in-depth. The design
    doc preserves the conservative semantics ("no-Task rows are still counted");
    removing the guard broadening is *not* a goal of the reconciler.
- **Removal criterion (per code path):** the reconciler's corresponding phase has
  shipped in production for one minor release cycle AND the property tests prove
  the same invariant as the point-fix tests with no behavior change in production
  telemetry.

---

## 5. References

### Plans and bug report

- **Point-fix plan (this directory's primary plan):**
  [`fix-pause-report-turn-orphan/plan-overview.md`](./plan-overview.md)
  - Phase 1 detail: [`phase1-plan.md`](./phase1-plan.md)
  - Phase 2 detail: [`phase2-plan.md`](./phase2-plan.md)
  - Decisions log: [`decisions.md`](./decisions.md)
- **Turn-reconciler design (the follow-up target):**
  [`docs/plans/turn-reconciler-named-transitions.md`](../../../docs/plans/turn-reconciler-named-transitions.md)
- **Bug report (the incident that triggered both):**
  [`docs/bugs/pause-during-report-turn-orphans-message-jobitem.md`](../../../docs/bugs/pause-during-report-turn-orphans-message-jobitem.md)

### Prior work referenced by both plans

- Report-Lane Decoupling (2026-06-24) — `PROCESS_REPORT` deliberately bypasses
  cross-system guards. Preserved by both plans.
- Job-as-Front-Primitive (2026-07-07) — every entry point onto one
  `enqueue_message_job` primitive. Preserved by both plans.
- F1 bifurcation fix (2026-07-06) — split carve-out branches to prevent
  `completed` Task from releasing `active` JobItem prematurely. The point-fix
  expands the carve-out; the reconciler deletes it.
- FIFO concurrency fix (2026-07-26) — `queued`-orphan exclusion. Phase 1 of the
  reconciler subsumes it.
- Unified Dispatcher (`docs/plans/unified-dispatcher.md`) — admission carve-out
  shared between `claim_pending_task` and `has_pending_tasks_blocked_by_busy_instance`
  (the P1/F11 "MUST agree" invariant, the W8-style shared predicate helper).
- Decouple Job-Task-Message Correlation
  (`docs/plans/decouple-job-task-message-correlation.md`) — D13 message-JobItem
  elimination, the basis for the reconciler's `job_queue_items` reconciliation
  rule.
- Defer Queue-and-Job-Task Seam Bugs (`defer-queue-and-job-task-seam-bugs.md`) —
  §1 canonical rejection of the table-merge alternative. The reconciler preserves
  this judgement (one authority, derived mirrors, not a merge).

### Critical notes (project context)

- 🟢 **[pattern]** LoopDetector + LoopRepairer in agent_node —
  `daemon/services/instance_lifecycle.py`. The reconciler's `_pause_cascade_db_sync`
  call site is in this file (per design doc §12 Phase 1 file list).
- 🟢 **[pattern]** `get_resolved()` = BASE meta, `get_version(id, tag)` = versioned —
  all meta lookups via `get_version()` with fallback to `get_resolved()`. Affects
  the reconciler's tool allow / team_members lookups when it adds the
  `turn_transitions.py` module.
- 🟢 **[pattern]** `_derive_legacy_status()` is the single source for legacy status
  derivation. The reconciler's `task.status` snapshot must respect this convention.
- 🔴 **[constraint]** Phase D `enqueued_at` column bug — use
  `_ensure_postgres_columns()` for ALL new columns on existing tables. The
  reconciler's Phase 3 introduces `suspension_reason` + `resume_target_turn_id` on
  `task`; both MUST go through `_ensure_postgres_columns()`.
- 🔴 **[constraint]** PostgreSQL is the PRIMARY dev/test DB. Run tests against
  PostgreSQL, not just SQLite. The reconciler's Phase 5 (PG invariant
  test-visibility) and all point-fix tests must pass on both engines.

---

## 6. Tracking

- **Created:** 2026-08-01
- **Status:** Planned (post point-fix unblock)
- **Owner:** planner[v2]
- **Bridge artifact:** this document (`follow-up-turn-reconciler.md`)
- **Next actions:**
  1. Confirm point-fix Phase 1 ships before reconciler Phase 1 begins.
  2. Open a tracker issue referencing this document and
     `docs/plans/turn-reconciler-named-transitions.md` with sequenced Phase
     milestones.
  3. At each reconciler phase, run the point-fix truth tables + production-state
     seeds + E2E as the go/no-go gate before deleting the corresponding point-fix
     code per §4.4.