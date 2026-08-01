# Increment 2 Plan: Carve-Out Deletion (Turn-Reconciler Migration Phase 4)

Date: 2026-08-01
Author: plan-creation worker
Status: **Revised (Council + Approver Review 2026-08-01)** — see "Revision Log" below
Design Doc: `docs/plans/turn-reconciler-named-transitions.md` §7
Predecessor: `increment1-plan.md` (MUST be stable before this lands)
Decisions: `decisions.md` §1.2 (Inc 2), §5 (Risk Register), §6 (Open Questions)

> ## § REVISION NOTE (Council Review — 2026-08-01)
>
> This plan was revised in place after council review. The four deltas:
>
> 1. **B4 (BLOCKER) — WAITING_CHILDREN carve-out is RETAINED, not deleted.** The original plan recommended Option (b) "remove" (see §6.2 in the prior revision); the council has reversed this to Option (a) "retain" because D11 makes `instances` soft-reconciliation only, which means the reconciler CANNOT force-transition an instance out of `waiting_children`. Without the carve-out, the simplified predicate would deadlock on the in-flight `process_message` Task semaphore held by a `waiting_children` instance. See §6 below and the new **D13** decision in `decisions.md`.
> 2. **B5 (BLOCKER) — W4 retry-regression fixture promoted to HARD gate #8.** The original §3.1 listed W4 as part of the property matrix; the council has promoted it to a hard pre-flight gate that must pass against the post-Increment-1 baseline before Increment 2 deletion begins. See §3.1 item 8.
> 3. **C8 (WARNING) — Forward-compatibility test for the simplified predicate.** After Increment 3 ships named transitions, `ClaimTurn.run()` will be the claim path. The simplified predicate must work identically whether the post-UPDATE is hand-written SQL (pre-Increment-3) or `ClaimTurn.run()` (post-Increment-3). Added as `tests/integration/test_simplified_predicate_claimturn_parity.py`. See §8.2.
> 4. **R1 — Stale line citations refreshed.** File:line references in §4 and §10 were off by a few lines against the current `repository.py` HEAD; this revision re-verifies each citation against the current source.
>
> All changes below are tagged `§ REVISION NOTE (Council Review)` near the affected text so the diff is reviewable.

---

## Revision Log

| Rev | Date | Author | Summary |
|-----|------|--------|---------|
| 0 | 2026-08-01 | plan-creation v1 | Initial draft. Recommended Option (b) — remove WAITING_CHILDREN carve-out. |
| 1 | 2026-08-01 | plan-creation v2 (this file) | Council review revisions: B4 (RETAIN WAITING_CHILDREN — add D13), B5 (W4 → hard gate), C8 (forward-compat test), R1 (refresh line citations). |
| 2 | 2026-08-01 | plan-creation v3 (Approver Review) | Issue 4 (BLOCKING): replaced the incoherent partial rollback with exactly two tiers — full git revert restoring all three deleted protections atomically, or no rollback when the reconciler makes the simplified predicate's divergence correct. |

---

## 1. Objective

Delete the cross-system guard's "carve-out pile" — the ~230 lines of hand-tuned `NOT EXISTS` subqueries in `daemon/repositories/task/repository.py` that have accumulated since the 2026-07-03 F1 fix — and replace them with a single ~7-line predicate (see §5 — the `+2` lines vs. the pre-revision estimate is the retained WAITING_CHILDREN carve-out) that is correct *because* `reconcile_turn_mirror(work_id)` (Increment 1) is the single owner of mirror⇄Task consistency. With the reconciler running on every claim/resume/finalize/timeout and via the periodic sweep, an `active`/`queued` JobItem whose backing Task is terminal or absent is *structurally impossible*; the guard no longer needs to prove it via subqueries. Net result: ~225 lines of SQL deleted, the guard becomes self-documenting, and the "add another carve-out" trajectory that produced Bug A (2026-08-01) is broken — the next mirror lifecycle cannot accidentally widen the predicate.

**Note on the WAITING_CHILDREN carve-out (§ REVISION NOTE — B4):** The two `AND (i.status IS NULL OR i.status != :status_waiting_children)` clauses at `repository.py:861` and `:1776` are RETAINED. They are not part of the "carve-out pile" this increment deletes; they are an *instance-lifecycle* carve-out that the reconciler is structurally unable to subsume (see D11 in `decisions.md` and the new D13). Deleting them would re-introduce the Bug-A-class deadlock. The simplified predicate therefore retains the WAITING_CHILDREN clause as its outermost conjunction (see §5).

Outcome: the file shrinks, the cross-system guard's intent is legible, and the Bug A reproduction scenario passes *without* the `_terminal_orphan_active_sql` point-fix.

---

## 2. Scope

### In Scope

| # | File | Lines (current) | Action |
|---|------|-----------------|--------|
| 1 | `daemon/repositories/task/repository.py` | 1030–1179 | **DELETE** `_admitted_task_carve_out_sql` method (~150 lines) |
| 2 | `daemon/repositories/task/repository.py` | 1181–1253 | **DELETE** `_terminal_orphan_active_sql` method (~72 lines) |
| 3 | `daemon/repositories/task/repository.py` | 881 | **DELETE** `AND {self._admitted_task_carve_out_sql("j")}` interpolation in `claim_pending_task` |
| 4 | `daemon/repositories/task/repository.py` | 882–912 | **DELETE** `AND NOT ({self._terminal_orphan_active_sql("j")})` block in `claim_pending_task` (the post-`_admitted_task_carve_out_sql` Bug A exclusion block — the `AND NOT (...)` wrapper at 908-910 plus the surrounding comment block 882-907) |
| 5 | `daemon/repositories/task/repository.py` | 934–940 | **DELETE** the queued-orphan `NOT EXISTS` `AND NOT (...)` clause |
| 6 | `daemon/repositories/task/repository.py` | 1795, 1812 | **DELETE** the same two interpolations in `has_pending_tasks_blocked_by_busy_instance` (mirror of P1) — the `_terminal_orphan_active_sql` call at 1812 is wrapped in `AND NOT (...)` (1811-1813) |
| 7 | `daemon/repositories/task/repository.py` | (new) | **ADD** a single private helper, e.g. `_active_jobitem_with_inflight_task_sql(job_alias)`, returning the simplified ~5-line predicate (see §5) |
| 8 | `daemon/repositories/task/repository.py` | 881, 1795 | **REPLACE** both call sites' interpolations to call the new helper with their respective `job_alias` (`"j"`, `"j_running"`) — preserves P1/F11 shared-predicate invariant (§7) |
| 9 | `daemon/repositories/task/repository.py` | 1030–1253 (removed region) | Tighten the inner-SQL `claim_pending_task` block: remove the `status_queued_admission`, `status_active_admission` binds that are now dead (kept only those the new helper needs) — but **KEEP** `status_waiting_children` (line 954) because the retained carve-out references it (see §6) |
| 10 | `daemon/repositories/task/repository.py` | 1748–1845 (claim block in busy-probe; SQL 1748-1818, execute 1819-1845) | Mirror tighten of the busy-instance probe's execute params — also **KEEP** `status_waiting_children` (line 1835) |
| 11 | `tests/test_terminal_orphan_matrix.py` | entire file | **REWRITE** — the existing tests target the post-Revision 2 carved-out predicate; new tests must validate the simplified predicate against the same matrix. Keep the W4 retry-regression (parent CANCELLED + retry child PENDING, same `message_id` different `work_id`) and the claim↔busy-probe parity assertions (§8) |
| 12 | `tests/property/test_turn_state_machine.py` | §7 / §9 of increment1-plan | **EXTEND** — add explicit assertions that the simplified guard's orphan-exclusion behavior is correct (the increment 1 invariants already cover most of this, but the property matrix for the cross-system guard is the canonical Increment 2 validation) |
| 13 | `tests/unit/test_pause_resume_root.py` (W4 fixture at line 864), `test_resume_flow_redesign.py`, `test_cascade_pause_resume.py`, `services/test_execution_gate.py`, `test_work_resolver.py`, `integration/test_pause_race_*` | various | **AUDIT + UPDATE** — any test that asserts the OLD carve-out's `NOT EXISTS` shape or the `_admitted_task_carve_out_sql`/`_terminal_orphan_active_sql` symbols must be rewritten to assert the simplified predicate's behavior |
| 14 | `tests/e2e/test_pause_during_report_turn_then_resume.py` | (increment 1) | **VERIFY** the existing directed E2E test passes against the simplified guard (it should — that's the point) |
| 15 | `docs/bugs/pause-during-report-turn-orphans-message-jobitem.md` | (Bug A doc) | **APPEND** a "Resolved by Increment 2" footer pointing at this plan and the test that validates it |
| 16 | `.agents/shared/planning/turn-reconciler-migration/decisions.md` | §2 | **APPEND** the Increment 2 final-state decision: D-INC2-1 "carve-outs deleted; single predicate covers all 8 tables"; **AMEND** D9 to reflect Option (a) RETAIN; **ADD** D13 (see §6.5) |
| 17 | `tests/integration/test_simplified_predicate_claimturn_parity.py` | NEW | **ADD** — C8 forward-compatibility test (see §8.2) |

### Out of Scope

- **Deleting `_admitted_task_carve_out_sql` is conditional on Increment 1 being live** — if the reconciler is not yet running at all six call sites (claim, resume, finalize, timeout, periodic sweep, pause-after-Update-2), this increment is *unsafe* and the plan must be deferred. See §3.
- **Migrating the per-instance "one RUNNING task per instance" guard** — that guard operates on `task.status`, not `job_queue_items.admission_state`; it is unaffected by the carve-out removal.
- **Migrating the `process_message`-type bypass** — the `task_type != :process_message_type OR ...` wrapper that scopes the guard to message tasks remains. It is *not* a carve-out; it is a deliberate type-bypass.
- **Removing the per-instance "PAUSED/TERMINATED instance" guard** — that is an instance-status check, not a JobItem correlation; independent of this work.
- **The `report-lane-decoupling` plan's report bypass** — `PROCESS_REPORT` tasks continue to bypass the cross-system guard by `task_type`. The reconciler (Increment 1) handles their mirror lifecycle uniformly; the guard's simplification does not affect report routing.
- **FIFO vs parallel queue semantics** — the simplified predicate is queue-agnostic; the existing `active_admission_states_sql()` (shared with `lock_repository.py`) supplies the `admission_state` set, which is unchanged.
- **PostgreSQL trigger definitions** — the `trg_job_queue_items_active_lock_guard` and the active-admission/JobLock invariant trigger remain. They are defense-in-depth.
- **§ REVISION NOTE (B4) — The WAITING_CHILDREN carve-out at `repository.py:861` and `:1776`** — RETAINED. This is now a non-question: the council's 2026-08-01 review resolved D9 in favor of Option (a) "keep" (see §6). The two clauses STAY in the simplified predicate. See D11 and the new D13 in `decisions.md` for the architectural reason.
- **Schema changes** — no new columns. If implementation unexpectedly requires a column, that is a separate migration.
- **Increment 3 (named transitions)** — that ships after Increment 2; the named-transition routines may further simplify the predicate, but they are not part of this plan.

---

## 3. Dependency on Increment 1 — Readiness Gate

**Increment 1 MUST be live and stable before Increment 2 is safe to ship.** Removing the carve-out pile re-opens the orphan-admission bugs that the carve-outs (and the Bug A point-fix) were hiding. The reconciler is what closes those bugs structurally.

### 3.1 Readiness Checklist

| # | Readiness criterion | How to verify | Required for Increment 2 start? |
|---|---------------------|---------------|--------------------------------|
| 1 | `TaskRepository.reconcile_turn_mirror(work_id)` is implemented and integrated at all six call sites | Source inspection of `repository.py`, `instance_lifecycle.py` (×2), `job_feedback_observer.py`, `stale_task_recovery.py`, `job_recovery_service.py`, `claim_pending_task` | **YES — hard gate** |
| 2 | The reconciler covers all 8 mirror tables (`task`, `job_queue_items`, `message_queue`, `job_locks`, `dependency_watchers`, `report_injections`, `instances`, `job_watchers`) | Source inspection + `MIRROR_TABLES = 8` registry assertion in `tests/property/test_turn_state_machine.py` | **YES — hard gate** |
| 3 | Property tests in `tests/property/test_turn_state_machine.py` pass on PostgreSQL for ≥1000 generated transitions including the directed pause/report/resume sequence | `pytest tests/property/test_turn_state_machine.py --hypothesis-seed=...` against PostgreSQL | **YES — hard gate** |
| 4 | E2E test at `tests/e2e/test_pause_during_report_turn_then_resume.py` passes on PostgreSQL (the Increment 1 directed scenario) | `pytest tests/e2e/test_pause_during_report_turn_then_resume.py` against PostgreSQL | **YES — hard gate** |
| 5 | Full 404-test baseline passes with the reconciler live | `pytest tests/ --tb=short` against PostgreSQL | **YES — hard gate** |
| 6 | The periodic sweep at `JobRecoveryService.reconcile_drift_states` is active in production (or in a canary environment) for at least 7 days with no P1/P2 orphan-admission incidents | Production monitoring / incident log | **RECOMMENDED — soft gate** (if a canary shows no incidents, this is a strong signal; but the call sites are sufficient for correctness on the immediate path) |
| 7 | Increment 1's own success criteria (1–10 in `increment1-plan.md` §9) are all met | Increment 1 sign-off | **YES — hard gate** |
| 8 | **§ REVISION NOTE (B5) — W4 retry-regression fixture passes against the post-Increment-1 baseline.** Specifically, the fixture at `tests/unit/test_pause_resume_root.py:864` ("Retry scenario (W4 case 1, KEY regression)") must pass: a parent Task in `CANCELLED` state with a retry child Task in `PENDING` state, sharing the same `message_id` but with different `work_id`. This validates that the simplified predicate correctly distinguishes "parent-cancelled-but-retry-scheduled" from "genuinely-orphaned." Without this gate, a regression in the simplified predicate's `EXISTS(task WHERE work_id=job_id AND status IN (pending,running,paused))` would silently pass local tests but break the W4 case in production. | `pytest tests/unit/test_pause_resume_root.py::test_retry_scenario_w4_case_1 -v` against PostgreSQL with the post-Increment-1 reconciler live | **YES — hard gate (promoted from soft by council review 2026-08-01)** |

### 3.2 If a readiness check fails

- If any hard-gate check fails (items 1–5, 7, **8**), **defer Increment 2**. Do not partially apply; the carve-out deletion is all-or-nothing (see §10 rollback).
- If the soft-gate (production 7-day soak, item 6) is not met but all hard gates pass, ship to a canary environment and monitor for 48 hours before promoting to production. Document the canary period in the PR.

### 3.3 Verification commands (executed in the Increment 2 worktree, after Increment 1 is merged)

```bash
# Verify the reconciler exists and is wired
grep -n "reconcile_turn_mirror" daemon/repositories/task/repository.py \
    daemon/services/instance_lifecycle.py \
    daemon/services/job_feedback_observer.py \
    daemon/services/stale_task_recovery.py \
    daemon/services/job_recovery_service.py
# Expected: ≥6 occurrences, ≥1 per file

# Property tests
pytest tests/property/test_turn_state_machine.py --hypothesis-seed=20260801 -v

# E2E
pytest tests/e2e/test_pause_during_report_turn_then_resume.py -v

# § REVISION NOTE (B5) — W4 retry-regression hard-gate fixture
pytest tests/unit/test_pause_resume_root.py::test_retry_scenario_w4_case_1 -v

# Full baseline
pytest tests/ --tb=short -q 2>&1 | tail -50
# Expected: 404 + (Increment 1 additions) tests pass
```

---

## 4. Deletion Plan — Order, Rationale, Risk Per Carve-Out

The deletions are interdependent; the order matters. We delete from the outside in: first the *callers' interpolations*, then the *helper methods* themselves. This keeps the codebase compilable at every step (each commit is bisect-safe).

### 4.1 Step-by-step deletion

| Step | Action | File:Line | Rationale | Risk | Mitigation |
|------|--------|-----------|-----------|------|------------|
| 1 | Replace `claim_pending_task` (`:881`) and `has_pending_tasks_blocked_by_busy_instance` (`:1795`) interpolations to call a new helper that returns the SIMPLIFIED predicate (no `message_id` JSON extraction, no bifurcated branches). At this step the helper is a NEW method that returns a temporary SQL string *functionally equivalent to* the current carve-out pile. **§ REVISION NOTE (B4)** The helper MUST include the WAITING_CHILDREN carve-out clause `AND (i.status IS NULL OR i.status != :status_waiting_children)` as its outermost conjunction (see §5). | `:881`, `:1795` | Establishes the simplified-predicate shape on disk before any logic changes. The new helper still passes the existing 404-test baseline (it preserves the orphan-exclusion semantics, INCLUDING the WAITING_CHILDREN carve-out). | None (mechanical refactor, behavior preserved) | Run full test suite after this step |
| 2 | DELETE `_admitted_task_carve_out_sql` (`:1030-1179`) and `_terminal_orphan_active_sql` (`:1181-1253`) methods. The new helper from step 1 is the only source of the predicate. | `:1030-1253` | The methods are now dead code; the new helper holds the truth. | Low (no callers reference them) | grep for `_admitted_task_carve_out_sql` and `_terminal_orphan_active_sql` in the repo to confirm zero remaining references before deletion |
| 3 | DELETE the queued-orphan `AND NOT (...)` clause (`:934-940`). This is the F1 carve-out — orphan exclusion for queued JobItems with NO matching Task. The reconciler's `transition_to_done_by_work_id` covers this case. | `:934-940` | The F1 case (queued mirror with no backing Task) is now handled by the reconciler's periodic sweep + the new step-1 helper's simplified predicate. The cross-system guard no longer needs to pre-empt the F1 case at claim time. | **MEDIUM** — the F1 deadlock (2026-07-03) was a real production incident. Verify the reconciler transitions orphaned-no-Task JobItems to `done` with `terminal_reason='orphaned_no_task'`. | Add a focused test: queue a `PROCESS_MESSAGE` task, never create the backing Task, run reconciler, assert the JobItem transitions to `done` and a fresh claim is admissible |
| 4 | DELETE the Bug A point-fix: the `_terminal_orphan_active_sql` interpolation at `:882-912` (claim path, including the comment block 882-907 and the `AND NOT (...)` wrapper at 908-910) and at `:1811-1813` (busy-probe path: `AND NOT ({self._terminal_orphan_active_sql("j_running")})`). With the simplified predicate, an `active` JobItem with all-terminal backing Tasks is admitted because the reconciler has already transitioned the JobItem to `done` (so the `EXISTS` check returns false). | `:882-912`, `:1811-1813` | This is the **structural** Bug A fix. The point-fix handled the symptom; the simplified predicate + reconciler handles the cause. | **MEDIUM-HIGH** — Bug A is the most recent production incident. The exact reproduction scenario must be validated (see §8). | The directed E2E test from Increment 1 already exercises this scenario; ensure it still passes. Add an additional fixture in `tests/test_terminal_orphan_matrix.py` that asserts: orphaned `active` JobItem + terminal backing Task → fresh claim is admissible |
| 5 | Tighten execute params: remove `status_queued_admission`, `status_active_admission` binds that the simplified predicate no longer needs (keep only `status_pending`, `status_running`, `status_paused`, and `status_waiting_children`). **§ REVISION NOTE (B4)** `status_waiting_children` MUST be retained because the WAITING_CHILDREN carve-out clause is preserved (see §6). | `:948-987`, `:1819-1845` | The simplified predicate uses `admission_state IN {active_admission_states_sql()}` (one bind via the helper), a single `IN (:status_pending, :status_running, :status_paused)` for the backing Task check, and `:status_waiting_children` for the retained carve-out. | Low (mechanical, dead binds) | Run a SQL trace to confirm the new SQL references only the kept binds |
| 6 | Delete obsolete test assertions that hard-code the old `NOT EXISTS` shape. | (multiple test files) | The old tests assert the *implementation* of the guard; the new tests assert its *behavior* (admissibility matrix). | Low | New matrix-based tests at `tests/test_terminal_orphan_matrix.py` cover all the old fixtures |
| 7 | Update docstrings: remove the multi-paragraph rationale about bifurcated branches, the F1/F1-revision-2/Bug-A historical commentary, and the `MUST stay in sync` P1/F11 cross-reference (the latter is replaced by literal shared-helper-invocation, which is provably equivalent) | Various | Reduce docstring noise; the helper's docstring explains the *post-Increment-2* shape | None | Docstring review by the lead developer |

### 4.2 Per-carve-out rationale (why each is redundant)

| Carve-out | Why it's redundant after Increment 1 |
|-----------|--------------------------------------|
| `_admitted_task_carve_out_sql` Branch 1 (queued, ANY matching Task releases) | The reconciler transitions orphaned-no-Task `queued` JobItems to `done` (`terminal_reason='orphaned_no_task'`). A `queued` JobItem that survives reconciliation has a matching non-terminal backing Task — so the simple `EXISTS(task WHERE work_id=job_id AND status IN (pending,running,paused))` correctly admits a fresh claim. |
| `_admitted_task_carve_out_sql` Branch 2 (active, message_id-keyed PENDING/RUNNING) | The reconciler transitions orphaned-`active` JobItems (terminal backing Task, or no backing Task) to `done`. An `active` JobItem that survives reconciliation has an in-flight backing Task — so the simple `EXISTS` check is the *correct* and *complete* orphan-exclusion. The `message_id` JSON extraction was a workaround for the absence of a reconciler; the work_id-keyed simple `EXISTS` is now the structurally correct form. |
| The queued-orphan `AND NOT (...)` clause (F1, 2026-07-03) | Same as Branch 1 — the reconciler's `transition_to_done_by_work_id` covers it. The reconciler is called from the claim path (Increment 1) and from the periodic sweep, so by the time the cross-system guard's claim is evaluated, no orphaned-queued JobItem can exist. |
| `_terminal_orphan_active_sql` (Bug A, 2026-08-01) | The point-fix handled the *symptom* (an `active` JobItem with all-terminal backing Task rows blocking a fresh claim). The reconciler handles the *cause*: the moment the original `process_message` Task transitions to terminal in `reconcile_turn_mirror`, the correlated `active` JobItem transitions to `done` in the same transaction. The fresh claim then sees no `active` JobItem at all. |
| **§ REVISION NOTE (B4) — WAITING_CHILDREN carve-out at `:861` and `:1776`** | **NOT REDUNDANT — RETAINED.** See §6 for the full architectural rationale. D11 (soft reconciliation of `instances`) means the reconciler CANNOT force-transition an instance out of `waiting_children`; the JobItem must stay `active` as a semaphore for the child-completion report path. The reconciler's `job_queue_items` rule (per D13) explicitly does NOT transition a `waiting_children` instance's active JobItem to `done` even if the Task is terminal — the JobItem is an intentional semaphore. Removing the WAITING_CHILDREN carve-out would cause the simplified predicate to deadlock on the parent's in-flight `process_message` Task. |

---

## 5. Replacement Guard — Target Shape

The simplified predicate (~7 lines of meaningful SQL, with whitespace — see the § REVISION NOTE at §1 about the "+2" vs. the pre-revision estimate):

```sql
EXISTS (
    SELECT 1 FROM task t
    WHERE t.work_id = :job_alias.job_id
      AND t.status IN (:status_pending, :status_running, :status_paused)
)
```

**§ REVISION NOTE (B4) — the WAITING_CHILDREN carve-out clause is RETAINED as the outermost conjunction of the guard.** When inlined into the cross-system guard's existing structure (preserving the `task_type != :process_message_type OR instance_id NOT IN (...)` wrapper and the `LEFT JOIN instances i ON j.instance_id = i.instance_id`), the guard becomes:

```python
def _active_jobitem_with_inflight_task_sql(self, job_alias: str) -> str:
    """Single-source-of-truth simplified cross-system predicate.

    Post-Increment 2 (Phase 4 of turn-reconciler migration). With
    reconcile_turn_mirror(work_id) running on every claim/resume/
    finalize/timeout and the periodic sweep, an active JobItem whose
    backing Task is terminal or absent is structurally impossible.
    The guard therefore reduces to: "block only if there is an
    active JobItem whose backing Task is in-flight." Orphan
    reconciliation is the reconciler's job, not the guard's.

    The WAITING_CHILDREN carve-out (D9 RETAINED, D13 in
    decisions.md) is RETAINED at the outermost conjunction:
    the JobItem of a WAITING_CHILDREN instance is an intentional
    semaphore for the child-completion report path. The reconciler
    CANNOT subsume it (D11: instances are soft-reconciled, not
    force-updated). Removing this clause reproduces the Bug-A
    deadlock class.

    P1/F11 MUST-stay-in-sync invariant: this method is the single
    source of truth for BOTH claim_pending_task and
    has_pending_tasks_blocked_by_busy_instance. Both call sites
    invoke this helper with their respective job_alias
    ("j" / "j_running"), guaranteeing the two gates cannot
    disagree.
    """
    return (
        f"EXISTS (\n"
        f"    SELECT 1 FROM task t\n"
        f"    WHERE t.work_id = {job_alias}.job_id\n"
        f"      AND t.status IN (:status_pending, :status_running, :status_paused)\n"
        f")\n"
        # WAITING_CHILDREN carve-out (D9 RETAINED, D13). The
        # surrounding query's ``LEFT JOIN instances i ON
        # j.instance_id = i.instance_id`` (and its mirror
        # ``j_running``) supplies ``i``; the ``status_waiting_children``
        # bind is the WAITING_CHILDREN status value.
        f"AND (i.status IS NULL OR i.status != :status_waiting_children)"
    )
```

**Usage at `claim_pending_task` (P1, around `:881`):**

```python
-- Was: AND {self._admitted_task_carve_out_sql("j")}
--      AND NOT ({self._terminal_orphan_active_sql("j")})
--      AND NOT (j.admission_state = :status_queued_admission AND NOT EXISTS (...))
--      AND (i.status IS NULL OR i.status != :status_waiting_children)  -- KEPT
-- Now:
AND {self._active_jobitem_with_inflight_task_sql("j")}
-- The helper now includes the WAITING_CHILDREN clause (D9 RETAINED, D13).
```

**Usage at `has_pending_tasks_blocked_by_busy_instance` (F11, around `:1795` and `:1812`):**

```python
-- Was: AND {self._admitted_task_carve_out_sql("j_running")}
--      AND NOT ({self._terminal_orphan_active_sql("j_running")})
--      AND (i.status IS NULL OR i.status != :status_waiting_children)  -- KEPT
-- Now:
AND {self._active_jobitem_with_inflight_task_sql("j_running")}
```

### 5.1 Why this shape is correct

- **`work_id = job_id` correlation axis** — direct column-to-column join, no JSON extraction. Works identically on SQLite and PostgreSQL. The `message_id` JSON extraction was a workaround for the absence of a reconciler; with the reconciler, the work_id-keyed correlation is the structurally correct form.
- **No bifurcated branches** — the F1 (queued) / F1-R2 (active) / Bug A (active+terminal) split collapses into a single `EXISTS` because the reconciler handles all three cases uniformly: any orphan is transitioned to `done` before the claim SQL is evaluated.
- **No `admission_state` filter inside the predicate** — the surrounding SQL already filters on `j.admission_state IN {active_admission_states_sql()}` (the `queued`/`active` set). The `EXISTS` just checks for an in-flight Task. This matches the pre-D13 simplification that removed the `job_type = 'message'` filter.
- **The `status_paused` Task status is kept** — a paused Task still holds the instance's lease (per the Increment 1 reconciler and the D2 transition). The guard correctly blocks a fresh claim while a Task is paused.
- **§ REVISION NOTE (B4) — `waiting_children` reference inside this predicate IS RETAINED.** The WAITING_CHILDREN carve-out is the outermost conjunction of the guard. The `LEFT JOIN instances i ON j.instance_id = i.instance_id` stays; `status_waiting_children` stays bound. See §6 and D13.

---

## 6. WAITING_CHILDREN Carve-Out — ACCEPTED DECISION (RETAIN)

> ### § REVISION NOTE (Council Review — B4)
>
> The pre-revision version of this section was an "OPEN DECISION" with a recommendation of Option (b) "remove." The council has reviewed and **rejected** that recommendation. The accepted decision is **Option (a) — RETAIN** the WAITING_CHILDREN carve-out. The architectural reason is D11 (soft reconciliation of `instances`): the reconciler CANNOT force-update `instances.status`, so a `waiting_children` instance's active JobItem must stay `active` as a semaphore for the child-completion report path. The new decision D13 (added to `decisions.md`) codifies this. The remainder of this section reflects the accepted decision.

The `WAITING_CHILDREN` carve-out is **RETAINED** as the outermost conjunction of the simplified cross-system guard. It is not a mirror-lifecycle concern; it is an *instance-lifecycle* concern that the reconciler is structurally unable to subsume.

### 6.1 What the carve-out says

```sql
AND (i.status IS NULL OR i.status != :status_waiting_children)
```

- **What it says:** "A `WAITING_CHILDREN` instance's JobItem is just a FIFO placeholder waiting for the instance lifecycle to resolve, not holding the langgraph thread. A child-completion report task is not actually blocked."
- **Reasoning:** `WAITING_CHILDREN` is a semantic state — the parent has finished its user-message turn and is awaiting child-completion reports. The JobItem in this state is intentionally inert. Treating it as "actively blocking" would deadlock the child-completion report path.

### 6.2 Why "remove" was rejected (council's reasoning, captured for posterity)

The pre-revision version of this plan recommended Option (b) "remove" with the argument that the reconciler's `admission_state=active` rule would subsume the WAITING_CHILDREN semantic: a `WAITING_CHILDREN` instance whose JobItem is `active` but Task is `completed` (or absent) would have its JobItem transitioned to `done` by the reconciler, and the guard's simplified `EXISTS` would then correctly identify that no in-flight Task exists.

This argument is **valid in isolation but fails against D11**. D11 (accepted, see `decisions.md` §D11) makes `instances` soft-reconciliation only — the reconciler *verifies* instance↔Task consistency but does NOT *force-update* `instances.status`. A `waiting_children` instance cannot be transitioned to a different status by the reconciler. The reconciler's `job_queue_items` rule, per D13 (new), explicitly does NOT transition a `waiting_children` instance's active JobItem to `done` even if the Task is terminal — the JobItem is an intentional semaphore.

Concretely: a `waiting_children` instance's JobItem may be `active` while its original `process_message` Task is `completed` (it finished its turn and is awaiting children). If the WAITING_CHILDREN carve-out is removed, the simplified `EXISTS(task WHERE work_id=job_id AND status IN (pending,running,paused))` predicate sees the parent's in-flight Task and BLOCKS the child-completion report Task from being claimed — reproducing the exact deadlock the carve-out was designed to prevent.

### 6.3 The architectural coupling that makes RETAIN mandatory

The interaction between D11 and the cross-system guard is subtle and was missed in the v1 draft of this plan:

| Decision | Effect on instances | Effect on JobItems (via reconciler) |
|----------|--------------------|-------------------------------------|
| **D11 (accepted)** | Soft reconciliation: verify-and-flag, NOT force-update | Reconciler does NOT transition `waiting_children` instance's JobItems |
| **D13 (new — REVISION NOTE B4)** | (inherits D11) | Reconciler's `job_queue_items` rule: when `i.status='waiting_children'`, do NOT transition the active JobItem to `done` even if Task is terminal |
| **Cross-system guard** | Must NOT treat a `waiting_children` instance's active JobItem as blocking | The WAITING_CHILDREN carve-out is the only mechanism to express this — the reconciler is structurally unable to subsume it |

The simplified predicate (D9 Option a) and the reconciler (D11 + D13) form a coupled invariant: *the reconciler guarantees that no orphaned `active` JobItem exists UNLESS that JobItem is the intentional semaphore for a `waiting_children` instance, in which case the guard's carve-out correctly identifies it as inert.* Removing the carve-out breaks the invariant.

### 6.4 Implementation

The two `AND (i.status IS NULL OR i.status != :status_waiting_children)` clauses at `repository.py:861` and `:1776` STAY. They are folded into the new `_active_jobitem_with_inflight_task_sql` helper as its outermost conjunction (see §5). The `LEFT JOIN instances i ON j.instance_id = i.instance_id` joins stay. The `status_waiting_children` bind stays. **No deletion of these clauses is in scope for Increment 2.**

### 6.5 New decision D13 (added to `decisions.md` §2)

> ### D13 — `WAITING_CHILDREN` is an instance-lifecycle semantic state, not a mirror-consistency state. The reconciler cannot subsume it.
>
> **Status:** Accepted (2026-08-01, council review of Increment 2).
>
> **Context:** D11 makes `instances` soft-reconciliation only. The reconciler cannot force-transition a `waiting_children` instance's `instances.status`. Consequently, the reconciler also cannot transition the correlated `active` JobItem to `done` — the JobItem is an intentional semaphore for the child-completion report path.
>
> **Decision:** The cross-system guard's WAITING_CHILDREN carve-out at `repository.py:861` and `:1776` is RETAINED. The reconciler's `job_queue_items` rule (Increment 1) explicitly does NOT transition a `waiting_children` instance's active JobItem to `done` even if the Task is terminal. Increment 2's simplified predicate includes the WAITING_CHILDREN clause as its outermost conjunction (see `increment2-plan.md` §5, §6.4).
>
> **Rationale:** Removing the carve-out would cause the simplified `EXISTS` predicate to deadlock on the parent's in-flight `process_message` Task when the instance is in `waiting_children` state — reproducing the exact deadlock class the original carve-out was designed to prevent. D11 + the guard's carve-out form a coupled invariant that the simplified predicate preserves by retaining the WAITING_CHILDREN clause.
>
> **Consequences:**
> - Increment 2 ships with the WAITING_CHILDREN carve-out intact.
> - The simplified predicate is ~7 lines (vs. the pre-revision estimate of ~5 lines) — the "+2" is the retained WAITING_CHILDREN clause.
> - A future increment MAY consider subsuming this carve-out if and only if D11 is revisited and `instances` becomes hard-reconciled. Until then, RETAIN.
>
> **Related Increment:** Increment 2; D11 (prerequisite); D9 (reclassified to "accepted: RETAIN").

---

## 7. Shared Predicate Invariant (P1/F11 "MUST agree")

`claim_pending_task` and `has_pending_tasks_blocked_by_busy_instance` MUST evaluate the *same* predicate against `job_queue_items`. If they disagree, the worker pool makes inconsistent idle/busy decisions: spurious wakeups, or worse, workers sleeping through admissible work.

### 7.1 How the invariant is preserved

Both call sites invoke the same private method `_active_jobitem_with_inflight_task_sql(job_alias)`, parameterized only by the SQL alias (`"j"` for claim, `"j_running"` for busy-probe). The two interpolations are *literally* the same code path — there is no possibility of drift.

```python
# claim_pending_task (P1, line ~881)
AND {self._active_jobitem_with_inflight_task_sql("j")}

# has_pending_tasks_blocked_by_busy_instance (F11, line ~1795)
AND {self._active_jobitem_with_inflight_task_sql("j_running")}
```

### 7.2 Property-test enforcement

The Hypothesis state machine in `tests/property/test_turn_state_machine.py` already asserts the P1/F11 parity at the matrix level (per the Increment 1 invariants). Increment 2 EXTENDS this with an explicit cross-system assertion:

```python
@given(matrix_fixture())  # JobItem.admission_state × Task.status × instance.status
def test_claim_and_busy_probe_agree(fixture):
    setup_db(fixture)
    claim_blocks = not _claim_pending_task(...).matches  # whether claim returns a row
    busy_blocks = _has_pending_tasks_blocked_by_busy_instance()
    assert claim_blocks == busy_blocks, (
        f"P1/F11 invariant violated: "
        f"JobItem={fixture['admission_state']}, "
        f"Task={fixture['task_status']}, "
        f"Instance={fixture['instance_status']}"
    )
```

This test catches any future drift between the two call sites — if someone later adds a "fix" to one but not the other, the property test fails.

### 7.3 Static check (defense-in-depth)

Add a `tests/unit/test_shared_predicate_invariant.py` that imports the SQL strings via a helper and asserts string equality modulo the alias:

```python
def test_p1_f11_predicates_agree():
    claim = TaskRepository._active_jobitem_with_inflight_task_sql("j")
    busy = TaskRepository._active_jobitem_with_inflight_task_sql("j_running")
    assert claim.replace('"j"', '<<ALIAS>>') == busy.replace('"j_running"', '<<ALIAS>>')
```

This is a cheap belt-and-suspenders check alongside the property test.

---

## 8. Test Strategy

### 8.1 Baseline protection

- Run the full 404-test suite before and after each logical step. Document the exact command and DB backend in the PR description.
- Run against PostgreSQL as the primary environment (per the project's known limitation: "PostgreSQL is the PRIMARY dev/test DB").
- Run the focused SQLite suite to validate portable SQL and Python-side invariant visibility (no SQLite-only syntax; `work_id` correlation is a direct column-to-column join, so it works on both).
- **§ REVISION NOTE (B5)** — The W4 retry-regression fixture (`tests/unit/test_pause_resume_root.py:864`) is a hard pre-flight gate (§3.1 item 8). It must pass BEFORE any Increment 2 deletion step begins.

### 8.2 New focused coverage (Increment 2)

| Test file | Purpose | Key cases |
|-----------|---------|-----------|
| `tests/test_terminal_orphan_matrix.py` (REWRITE) | Validates the simplified predicate against the full guard matrix: `JobItem.admission_state` × `Task.status` × `instance.status` × `Task.work_id = job_id` correlation | All combinations; **§ REVISION NOTE (B5) — the W4 retry regression (parent CANCELLED + retry child PENDING, same `message_id` different `work_id`)** as a hard gate (also see `tests/unit/test_pause_resume_root.py:864`); multi-JobItem-per-instance (W4 case 3) |
| `tests/property/test_turn_state_machine.py` (EXTEND) | Adds the P1/F11 parity assertion (§7.2) and the `MIRROR_TABLES = 8` orphan-coverage registry check is tightened to require the simplified predicate's behavior | ≥1000 generated transitions; directed pause/report/resume sequence |
| `tests/unit/test_shared_predicate_invariant.py` (NEW) | Static string-equality check on the two call sites' predicates (§7.3) | The two SQL fragments differ only in the `job_alias` |
| `tests/e2e/test_pause_during_report_turn_then_resume.py` (VERIFY from Increment 1) | The directed Bug A scenario: pause-during-`process_report` → resume → answer. Must pass against the simplified guard. | The exact reproduction from the 2026-08-01 incident |
| `tests/unit/test_queued_orphan_reconciler.py` (NEW) | Validates the F1 case: a `queued` JobItem with NO backing Task is transitioned to `done` by the reconciler; the cross-system guard admits a fresh claim. | Fixture: queue a `PROCESS_MESSAGE` task without a Task row, run reconciler, assert JobItem is `done` and `claim_pending_task` returns the new task |
| `tests/integration/test_carve_out_deletion_smoke.py` (NEW) | End-to-end smoke test: simulates the Bug A scenario with the *simplified* guard, asserting the answer is delivered without manual DB intervention. | Replay the leader-pause-mid-report scenario |
| `tests/unit/test_pause_resume_root.py` (VERIFY) | The W4 fixture at line 864 ("Retry scenario (W4 case 1, KEY regression)") must pass against the post-Increment-1 baseline (B5 hard gate) and continue to pass post-Increment-2. | parent Task `CANCELLED` + retry child Task `PENDING`, same `message_id`, different `work_id` |
| `tests/integration/test_simplified_predicate_claimturn_parity.py` (NEW — § REVISION NOTE C8) | Forward-compatibility regression: asserts the simplified `_active_jobitem_with_inflight_task_sql` predicate produces *identical* admissibility results whether the claim path uses hand-written SQL (current state, pre-Increment-3) OR `ClaimTurn.run()` (post-Increment-3 named-transition architecture). The test uses a polymorphic `ClaimPath` interface (parameterized: `hand_written_sql` vs `claim_turn_run`) and asserts: for every matrix fixture in `tests/test_terminal_orphan_matrix.py`, the two paths return the same `admissible: bool`. This test is a forward-compatibility guarantee that Increment 2's deletion does not silently break when Increment 3's `ClaimTurn.run()` lands. | All `JobItem.admission_state` × `Task.status` × `instance.status` matrix fixtures run through both claim paths; results asserted equal |

### 8.3 Bug A reproduction scenario — explicit assertion

The canonical Bug A scenario (from `docs/bugs/pause-during-report-turn-orphans-message-jobitem.md`):

1. Root instance creates a `process_message` Task (status: `running`).
2. The Task completes naturally (status: `completed`); instance transitions to `WAITING_CHILDREN`.
3. A child-completion `process_report` Task is created (status: `running`).
4. A user `ask_questions` pause fires mid-report.
5. Pause cascade cancels the `process_report` Task.
6. The original `process_message`'s JobItem is left `active` (orphaned active mirror).
7. The user answers the question; resume cascade fires.
8. A fresh `process_message` Task is enqueued (the answer).
9. **Pre-Increment 1:** the cross-system guard blocks the new Task forever (Bug A).
10. **Post-Increment 1 + pre-Increment 2:** the reconciler at the claim path transitions the orphaned `active` JobItem to `done`; the simplified guard admits the fresh Task.
11. **Post-Increment 1 + Increment 2:** the simplified guard is in place (with the WAITING_CHILDREN carve-out RETAINED per § REVISION NOTE B4); the claim is admissible; the answer is delivered.

The Increment 2 assertion is that step 10 and 11 produce the same outcome — the only difference is the *shape* of the SQL, not the *behavior*. The E2E test (`tests/e2e/test_pause_during_report_turn_then_resume.py`) is the canonical validator.

### 8.4 Property tests (from Increment 1 §9, extended)

The property tests in `tests/property/test_turn_state_machine.py` already validate the eight-table orphan invariant. Increment 2 adds:

- The P1/F11 parity assertion (§7.2) — runs every transition and asserts claim and busy-probe agree.
- The `MIRROR_TABLES = 8` coverage registry is tightened to require the simplified predicate's *behavior* (not just that the reconciler covers the 8 tables). The fixture matrix must include "active JobItem + terminal Task" and "queued JobItem + no Task" cases.
- The directed pause/report/resume sequence (already in Increment 1) is re-run with the simplified guard and must pass.

### 8.5 Migration tests (for existing tests that hard-code the old shape)

Several existing tests in `tests/unit/test_pause_resume_root.py`, `tests/unit/test_resume_flow_redesign.py`, `tests/unit/test_cascade_pause_resume.py`, `tests/unit/services/test_execution_gate.py`, `tests/unit/services/test_work_resolver.py`, and `tests/integration/test_pause_race_*` may assert the OLD `NOT EXISTS` shape or reference `_admitted_task_carve_out_sql` / `_terminal_orphan_active_sql` by name. Audit each file:

- If the test asserts a specific SQL string, rewrite to assert the *behavior* (claim admissible / not admissible under fixture X).
- If the test imports the helper methods, update the import (the methods are gone).
- If the test asserts a docstring or comment referencing the helpers, update or remove.

### 8.6 § REVISION NOTE (C8) — Forward-compatibility test design

The C8 regression test (`tests/integration/test_simplified_predicate_claimturn_parity.py`) addresses a specific failure mode: Increment 3 will replace the hand-written SQL claim path with a `ClaimTurn.run()` named-transition architecture. The simplified predicate introduced in Increment 2 must continue to work correctly under both paths. The test design:

```python
@pytest.mark.parametrize("claim_path", ["hand_written_sql", "claim_turn_run"])
def test_simplified_predicate_claim_path_parity(matrix_fixture, claim_path):
    """§ REVISION NOTE (C8) — Forward-compatibility regression.

    Asserts the simplified _active_jobitem_with_inflight_task_sql
    predicate produces identical admissibility results whether the
    claim path uses hand-written SQL (current state, pre-Increment-3)
    or ClaimTurn.run() (post-Increment-3 named transitions).
    """
    setup_db(matrix_fixture)
    if claim_path == "hand_written_sql":
        result = TaskRepository.claim_pending_task(worker_id="w1")
    else:
        # Post-Increment-3: routes through ClaimTurn.run() which
        # internally calls the same reconciler + simplified predicate.
        result = ClaimTurn.run(worker_id="w1")
    # The two paths must return the same Task (or both return None).
    assert_handled_identically(result, claim_path)
```

This test is a **forward-compatibility contract** — it fails immediately if a future Increment 3 implementation diverges from the simplified predicate's behavior, even though the predicate is the same SQL fragment in both paths.

### 8.7 Verification commands (executed in the Increment 2 worktree)

```bash
# § REVISION NOTE (B5) — W4 hard gate (must pass BEFORE deletions begin)
pytest tests/unit/test_pause_resume_root.py::test_retry_scenario_w4_case_1 -v

# Focused repository matrix
pytest tests/test_terminal_orphan_matrix.py -v --tb=short

# Property tests
pytest tests/property/test_turn_state_machine.py --hypothesis-seed=20260801 -v

# Shared-predicate invariant
pytest tests/unit/test_shared_predicate_invariant.py -v

# E2E
pytest tests/e2e/test_pause_during_report_turn_then_resume.py -v

# Migration smoke
pytest tests/integration/test_carve_out_deletion_smoke.py -v

# § REVISION NOTE (C8) — Forward-compatibility test
pytest tests/integration/test_simplified_predicate_claimturn_parity.py -v

# Full baseline
pytest tests/ --tb=short -q 2>&1 | tail -100
# Expected: 404 (pre-Increment 1) + (Increment 1 additions) + (Increment 2 additions) tests pass
# Both PostgreSQL and SQLite backends
```

---

## 9. Success Criteria

| # | Criterion | Measurement | Threshold |
|---|-----------|-------------|-----------|
| 1 | `_admitted_task_carve_out_sql` and `_terminal_orphan_active_sql` are deleted | `grep` returns zero matches across the repo (excluding the deleted lines in git history) | 0 occurrences |
| 2 | The F1 queued-orphan `AND NOT (...)` clause at `repository.py:934-940` is deleted | `git diff` shows the block removed | 0 lines |
| 3 | The simplified `_active_jobitem_with_inflight_task_sql` helper is the single source of the cross-system predicate | Both `claim_pending_task` and `has_pending_tasks_blocked_by_busy_instance` interpolate the same helper call | 2 call sites, identical SQL modulo alias |
| 4 | Net line reduction in `daemon/repositories/task/repository.py` | `git diff --stat` on the file | ≥195 lines deleted, ≤30 lines added (net ≥165 reduction) — *the pre-revision estimate of "≥200 deleted" is reduced by ~5 lines because the WAITING_CHILDREN clause is RETAINED (B4)* |
| 5 | All 404 existing tests pass on PostgreSQL | `pytest tests/ --tb=short` | 0 failures, 0 errors |
| 6 | The directed Bug A E2E test passes on PostgreSQL | `pytest tests/e2e/test_pause_during_report_turn_then_resume.py` | 0 failures |
| 7 | The P1/F11 parity property test passes for ≥1000 generated transitions | `pytest tests/property/test_turn_state_machine.py --hypothesis-seed=20260801` | 0 failures, no `P1/F11 invariant violated` errors |
| 8 | The static P1/F11 string-equality test passes | `pytest tests/unit/test_shared_predicate_invariant.py` | 0 failures |
| 9 | The F1 queued-orphan reconciler test passes | `pytest tests/unit/test_queued_orphan_reconciler.py` | 0 failures |
| 10 | The carve-out deletion smoke test passes | `pytest tests/integration/test_carve_out_deletion_smoke.py` | 0 failures |
| 11 | Both PostgreSQL and SQLite produce identical claim behavior on the matrix fixtures | Run `tests/test_terminal_orphan_matrix.py` on both backends | Same outcomes for every fixture |
| 12 | No SQLite-only SQL is introduced | Code review + grep for `rowid`, SQLite-specific timestamp/locking syntax | 0 occurrences |
| 13 | `work_id` correlation axis is preserved everywhere in the guard | Code review | All Task↔JobItem joins use `task.work_id = job_queue_items.job_id` |
| 14 | The Bug A doc is annotated with "Resolved by Increment 2" footer | `docs/bugs/pause-during-report-turn-orphans-message-jobitem.md` | Footer present, points at this plan and the validating test |
| 15 | `decisions.md` §2 appends D-INC2-1 documenting the final-state decision | `decisions.md` | Entry present with reference to this plan |
| 16 | **§ REVISION NOTE (B4)** — The WAITING_CHILDREN carve-out is RETAINED. The two `AND (i.status IS NULL OR i.status != :status_waiting_children)` clauses at `repository.py:861` and `:1776` STAY, are folded into the new helper, and the `status_waiting_children` bind remains. `decisions.md` §2 D9 is reclassified from "OPEN — recommend REMOVE" to "ACCEPTED — RETAIN (Option a)" and the new D13 is added. | `git diff` shows the two clauses preserved; `grep -n "status_waiting_children" daemon/repositories/task/repository.py` returns ≥2 matches (one in each call site, inside the new helper); `decisions.md` §2 contains D9 (ACCEPTED) and D13 (ACCEPTED) | All three conditions met |
| 17 | **(If soft gate is not met)** Canary deployment runs for ≥48 hours with no P1/P2 orphan-admission incidents | Production monitoring | 0 incidents |
| 18 | **§ REVISION NOTE (B5) — W4 retry-regression hard gate** passes against the post-Increment-1 baseline (pre-flight) AND continues to pass post-Increment-2 | `pytest tests/unit/test_pause_resume_root.py::test_retry_scenario_w4_case_1 -v` | 0 failures, both pre-flight and post-Increment-2 runs |
| 19 | **§ REVISION NOTE (C8) — Forward-compatibility regression** test passes for both `hand_written_sql` and `claim_turn_run` claim paths on all matrix fixtures | `pytest tests/integration/test_simplified_predicate_claimturn_parity.py -v` | 0 failures, identical results across both claim-path parameterizations |

---

## 10. Rollback Plan

> ### § REVISION NOTE v3 (Approver Review)
>
> The v2 rollback was incoherent (recommended partial revert of one protection while deleting three). v3 defines exactly two tiers: full git revert (restores all protections atomically) or no rollback (divergence is correct behavior).

Increment 2 is a pure deletion + replacement with a single helper (with the WAITING_CHILDREN carve-out preserved per § REVISION NOTE B4). It makes no schema or data changes. Rollback is deliberately all-or-nothing: use Tier 1 only for a production orphan-admission incident that shows the reconciler is not keeping the mirrors safe; use Tier 2 when the observed divergence is the intended consequence of the reconciler having normalized the mirrors. There is no partial rollback.

### 10.1 Tier 1 — Full rollback (git revert)

**Trigger:** Production sees orphan-admission incidents (P1/P2) after Increment 2 ships, and the reconciler is not correcting orphans fast enough.

**Procedure:**

1. `git revert <increment-2-merge-commit>` — restores **ALL THREE deleted protections**: `_admitted_task_carve_out_sql`, `_terminal_orphan_active_sql`, and the queued-orphan `AND NOT EXISTS` clause at `repository.py:934-940`.
2. The new `_active_jobitem_with_inflight_task_sql` helper is removed.
3. Both `claim_pending_task` and `has_pending_tasks_blocked_by_busy_instance` revert to their pre-Increment-2 interpolations.
4. Run the full 404-test baseline + Increment 1 additions on PostgreSQL to confirm restoration.
5. Treat this as **ONE atomic commit** — no partial state and no selective restoration of only one protection.

**Why this is safe:** The full revert restores the pre-Increment-2 state, which was the validated baseline. The reconciler (Increment 1) continues to run at all 6 call sites — it is purely additive and unaffected by the revert. The carve-outs return as defense-in-depth alongside the reconciler.

### 10.2 Tier 2 — No rollback needed (accept divergence)

**Trigger:** The simplified predicate is **MORE permissive** than the original (it admits claims the original would block). If production sees legitimate claims being admitted that the old carve-outs would have blocked, this is **correct behavior** — the reconciler has already normalized the mirrors.

**Procedure:**

1. Investigate the claim and verify that the reconciler transitioned the JobItem to `done` (check reconciler logs and the JobItem's terminal state/reason where available).
2. If the reconciler ran correctly, the admitted claim is legitimate — take no rollback action.
3. If the reconciler did **not** run (a missing or faulty call site), that is an Increment 1 bug, not an Increment 2 bug. Fix the reconciler call site; do not roll back Increment 2.

### 10.3 Failure → Detection → Recovery Tier

| Failure | Detection | Recovery Tier |
|---------|-----------|---------------|
| P1/P2 orphan-admission incident after Increment 2; reconciler is not correcting the orphan fast enough | Production monitoring shows an orphaned `active`/`queued` JobItem being admitted or the reconciler failing to normalize it | **Tier 1 — Full rollback:** execute the atomic git revert and PostgreSQL verification in §10.1 |
| A legitimate claim is admitted even though the retired carve-outs would have blocked it | Reconciler logs and JobItem state show the mirror was transitioned to `done` before the claim | **Tier 2 — No rollback:** accept the divergence; the claim is correct |
| A claim is admitted without the reconciler transitioning the corresponding orphaned JobItem | Reconciler logs show a missing, failed, or skipped Increment 1 call site | **Tier 2 — No rollback:** fix the Increment 1 call site; retain Increment 2 |
| The full revert does not restore the validated test baseline | The full 404-test baseline + Increment 1 additions fail on PostgreSQL after the revert | **Tier 1 — Full rollback:** keep the single revert deployed and investigate the restoration/test failure; do not create a partial rollback |

---

## Appendix A — File-by-File Diff Sketch

### A.1 `daemon/repositories/task/repository.py`

**Before** (simplified, current state):

```python
# :861
AND (i.status IS NULL OR i.status != :status_waiting_children)  # KEPT (B4)
# :881
AND {self._admitted_task_carve_out_sql("j")}
# :882-912
AND NOT (
    {self._terminal_orphan_active_sql("j")}
)
# :934-940
AND NOT (
    j.admission_state = :status_queued_admission
    AND NOT EXISTS (
        SELECT 1 FROM task _orphan_check
        WHERE _orphan_check.message_id = {orphan_json_extract}
    )
)
# :1030-1179 — def _admitted_task_carve_out_sql(...): ... (deleted)
# :1181-1253 — def _terminal_orphan_active_sql(...): ... (deleted)
# :1776
AND (i.status IS NULL OR i.status != :status_waiting_children)  # KEPT (B4)
# :1795
AND {self._admitted_task_carve_out_sql("j_running")}
# :1812
AND NOT (
    {self._terminal_orphan_active_sql("j_running")}
)
```

**After** (§ REVISION NOTE B4 — the WAITING_CHILDREN clauses are folded into the new helper):

```python
# :861 (B4 RETAINED) — the WAITING_CHILDREN clause is now inside the helper invocation
AND {self._active_jobitem_with_inflight_task_sql("j")}
# (lines 882-940 deleted; the surrounding OR/AND structure is preserved)
# (lines 1030-1253 deleted; the new helper replaces them)
# :1795 (B4 RETAINED) — the WAITING_CHILDREN clause is now inside the helper invocation
AND {self._active_jobitem_with_inflight_task_sql("j_running")}
# (line 1812 deleted)
```

### A.2 New helper (§ REVISION NOTE B4 — the WAITING_CHILDREN clause is RETAINED as the outermost conjunction)

```python
def _active_jobitem_with_inflight_task_sql(self, job_alias: str) -> str:
    """... (see §5 for the full docstring) ...

    The WAITING_CHILDREN carve-out (D9 RETAINED, D13) is the outermost
    conjunction: the surrounding query's ``LEFT JOIN instances i ON
    j.instance_id = i.instance_id`` (and ``j_running``) supplies ``i``;
    the ``status_waiting_children`` bind is the WAITING_CHILDREN status
    value. Removing this clause reproduces the Bug-A-class deadlock.
    """
    return (
        f"EXISTS (\n"
        f"    SELECT 1 FROM task t\n"
        f"    WHERE t.work_id = {job_alias}.job_id\n"
        f"      AND t.status IN (:status_pending, :status_running, :status_paused)\n"
        f")\n"
        f"AND (i.status IS NULL OR i.status != :status_waiting_children)"
    )
```

### A.3 Execute-param tighten

```python
# :948-987 (claim path execute params) — REMOVE:
"status_queued_admission": AdmissionState.QUEUED.value,
"status_active_admission": AdmissionState.ACTIVE.value,
# (status_paused and status_pending remain — used by the new predicate)
# § REVISION NOTE (B4) — KEEP:
"status_waiting_children": InstanceStatus.WAITING_CHILDREN.value,

# :1819-1845 (busy-probe path execute params) — same REMOVE / KEEP
```

---

## Appendix B — Cross-References

- **Predecessor plan:** `.agents/shared/planning/turn-reconciler-migration/increment1-plan.md` (especially §5 call sites, §7–9 property tests and success criteria, §10 rollback)
- **Decisions log:** `.agents/shared/planning/turn-reconciler-migration/decisions.md` (§1.2 migration overview, §2 D1–**D13**, §3 sequencing, §5 risk register, §6 open questions)
- **Design doc:** `docs/plans/turn-reconciler-named-transitions.md` (§7 Phase 4 carve-out removal, §10 Phase 4 review decisions)
- **Bug A origin:** `docs/bugs/pause-during-report-turn-orphans-message-jobitem.md` (especially the "Routing Gap" and "Guard Gap" sections)
- **Test surface:** `tests/property/test_turn_state_machine.py` (Increment 1), `tests/e2e/test_pause_during_report_turn_then_resume.py` (Increment 1), `tests/test_terminal_orphan_matrix.py` (Phase 1 Revision 2; rewritten in Increment 2), `tests/integration/test_simplified_predicate_claimturn_parity.py` (NEW — § REVISION NOTE C8), `tests/unit/test_pause_resume_root.py:864` (W4 hard gate — § REVISION NOTE B5)
- **Critical notes:** "PostgreSQL is PRIMARY dev/test DB" (project-wide constraint), "work_id is the authoritative correlation axis" (cross-system guard constraint)
