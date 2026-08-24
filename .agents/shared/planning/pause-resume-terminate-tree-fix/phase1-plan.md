# Phase 1: Lineage / Tree-Enumeration Fix (B1 + B4)

Date: 2026-08-24
Author: worker (plan-creation) for planner dispatch — pause-resume-terminate-tree-fix batch
Status: Draft — Ready for architect review (2 pre-flagged decisions, see §Architect Flags)
Branch: `feature/pause-resume-terminate-tree-fix` (worktree; separate worker commits atomically)

## Objective

Make every cascade that must act on an instance tree (pause, terminate, hard-delete snapshot) enumerate the **complete permanent lineage** (`instances.parent_id`) instead of the transient `instance_hierarchy` working set, so that descendants which completed/errored/were-revived during churn are never missed; and close the B4 tail by giving reports-to-dead-parents a **designed terminal path** (canonical dead-letter) plus a data-repair for the observed stranded-row class (`d14cbde5`).

Fixes: **B1** (pause does not cascade DOWN — root pause returns `paused_ids=[root]`, grandchild starts new work 18s later) and **B4** (terminate-root DOWN misses live children — terminate trace `children=0`, live child orphaned, report to dead parent → PENDING-forever work row + perpetual `[GUARD]` livelock every ~3s).

## Research Corrections (verified this session — supersedes research-lineage.md where they conflict)

The research inventory was verified line-by-line; three corrections, all strengthening the fix:

1. **`resume_instance_cascade` also enumerates via `get_tree_ids`** — `instance_lifecycle.py:2300` (`tree_ids = repo.get_tree_ids(root_id)` after re-root at `:2269+`). Missed by the research inventory. A paused child whose hierarchy row is already gone (completed→revived→paused) can never be resumed. Same defect class; needs a scope decision (see table, site R1).
2. **The job-cleanup sites are in `daemon/tools/job_queue.py:1443, :1571, :1726`, not `instance_lifecycle.py:1571/:1726`** (research cited the wrong file). They are the **visibility tools** (`job_messages`, `job_tree`, `job_progress`) — read-only, not mutation sweeps.
3. **The actual B4 DOWN-propagation mechanism is the inline hierarchy child query inside `terminate_instance`** — `instance_lifecycle.py:1385-1393` (`select(InstanceHierarchy.child_id).where(InstanceHierarchy.parent_id == instance_id)` at `:1392`). The `:1930` snapshot in `hard_delete_instance` only feeds the checkpoint sweep + `hard_delete_tree` FK cascade (`repository.py:1317`). Fixing only `:1930` would leave terminate DOWN still broken.
4. **Guard-livelock root cause — strong hypothesis found (research said UNRESOLVED):** the **pause gate** at `task/repository.py:~1315-1334` excludes `instance_id NOT IN (SELECT instance_id FROM instances WHERE status IN (:status_paused, :status_terminated))` for **ALL task types** — its own comment says "restores it uniformly for every task type — user messages and reports alike". The research's claim that "report tasks bypass the pause gate" misread the bypass comment, which applies only to the **cross-system guard** (`:1337-1391`, scoped to `process_message`). A `process_report` task targeting a TERMINATED parent is therefore permanently unclaimable → PENDING forever → the `[GUARD]` diagnostic (`:1473-1479`) loops every poll. The diagnosis task below verifies this by unit repro before we build on it.

## Recommended Approach [ARCHITECT-FLAGGED: lineage duality]

**Option (a) — NEW repository helper `get_tree_ids_permanent()`, call-site-by-call-site migration. RECOMMENDED.**

- Add `get_tree_ids_permanent(root_id: str) -> list[str]` to `SQLModelInstanceRepository` (`daemon/repositories/instance/repository.py`, next to `get_tree_ids` at `:313-341`). Python-side BFS over `instances.parent_id`, structurally mirroring the existing `get_tree_ids` BFS but selecting `Instance.instance_id WHERE Instance.parent_id == current_id` per level (same shape as the verified `list_child_ids_permanent` query at `:98-101`). Guarded by `_MAX_TRAVERSAL_DEPTH = 256` (`repository.py:33`) + the existing visited-set pattern. Returns `[root_id] + BFS-order descendants`; `[]` if root not found (matches `get_tree_ids` contract).
- **No recursive CTE.** Rationale: the repo retains SQLite compatibility (project blueprint); `WITH RECURSIVE` would work on both engines but diverges from every existing tree helper in this repository (`get_tree_root_id :291-311`, `get_ancestor_ids :343-361`, `get_tree_ids :313-341` are all Python-side loops) and adds a dialect-testing burden for zero benefit at typical tree sizes (<20 nodes; repro was 3 levels). Each level query is index-backed by `ix_instances_parent_id` (migration `20260402_000001_rename_session_to_instance.sql:225`). [IMPL-DETAIL]
- **Status-filter placement: keep classification-AFTER-enumeration** (confirms research recommendation). The helper applies NO status filter by design; callers classify (pause already does at `instance_lifecycle.py:2094-2102`, skipping PAUSED + TERMINAL_STATUSES with per-node logging into `skipped_ids`). Reasons: (1) terminate/hard-delete need the complete tree regardless of status (checkpoint sweep, FK cascade); (2) moving the filter into SQL would silently change the pause response contract (`skipped_ids`); (3) one enumeration source of truth. [IMPL-DETAIL, confirmed]
- `get_tree_ids()` (transient) is left untouched for consumers that may want working-set semantics (observer; see scope table).

**Option (b) — repoint `get_tree_ids()` internals to `parent_id`. REJECTED.** Blast radius: 9 call sites across 4 modules change behavior in one commit, including read-only visibility tools whose output consumers (job_tree BFS expansion) have unstated expectations, and the observer cleanup path with race-hardening history. Option (a) lets each site migrate with its own tests and its own revert story, and preserves a transient-semantics escape hatch during rollout.

### B1+ Enumeration design — exact signature

```python
def get_tree_ids_permanent(self, root_id: str) -> list[str]:
    """Complete permanent-lineage tree enumeration (root + ALL descendants).

    Walks ``instances.parent_id`` (permanent — survives completion, error,
    terminate, revive) rather than the ``instance_hierarchy`` working set
    (rows deleted at child_reports.py:922 / error_reporting.py:233 /
    child_reports.py:2872 / instance_lifecycle.py:3331). Use for ANY
    cascade that must see the whole tree regardless of churn.

    NO status filter by design — callers classify AFTER enumeration
    (pause skips PAUSED/TERMINAL at instance_lifecycle.py:2094-2102;
    terminate recursion must add the same skip, see T3).
    """
```

## Scope Decision Table — which `get_tree_ids()` / transient-enumeration sites switch in P1

| # | Site (verified) | Function | Switch? | Rationale / same-defect exposure |
|---|---|---|---|---|
| P1 | `instance_lifecycle.py:2056` | `pause_instance_cascade` enumerate (after permanent re-root `:2050-2056`) | **YES — mandatory** | B1. Re-root via `get_tree_root_id` (parent chain) is already permanent and correct; only the BFS source is wrong. |
| T1 | `instance_lifecycle.py:1385-1393` | `terminate_instance` inline `InstanceHierarchy` child query (DOWN recursion) | **YES — mandatory** | B4 primary mechanism. Replace inline query with `list_child_ids_permanent(instance_id)` (`repository.py:82-103`) **plus add terminal-skip classification** (see Risks R1). |
| T2 | `instance_lifecycle.py:1930` | `hard_delete_instance` snapshot (BEFORE terminate) | **YES — mandatory** | B4. Feeds checkpoint sweep + `hard_delete_tree` FK cascade (`repository.py:1317`). Permanent snapshot also makes the ordering requirement trivially safe (no longer depends on hierarchy rows that `_terminate_instance_db_sync:3331` deletes). |
| R1 | `instance_lifecycle.py:2300` | `resume_instance_cascade` enumerate | **YES — recommended, [COORDINATION]** | Discovered site (research missed it). A paused child with a severed hierarchy row (completed→revived→paused) is otherwise permanently unresumable — same root cause. One-line change; B2's handle/watcher fixes (P2 worker) touch the same function but not this line. Dispatcher must de-conflict merge order with the phase-2 worker. |
| M1 | `maintenance.py:831, :836` | protected-instance marking (pinned subtrees protected from TTL purge) | **YES — low risk** | Same root cause, opposite polarity: churned descendants of pinned roots are currently UNprotected → premature purge of rows still needed for lineage display. Switching is strictly more protective; only side effect is delayed TTL cleanup of terminal descendants under pinned roots (matches user pin intent). |
| V1 | `daemon/tools/job_queue.py:1443, :1571, :1726` | `job_messages` / `job_tree` / `job_progress` visibility tools | **DEFER — follow-up ticket** | Read-only; no observed defect. Completeness change alters `job_tree` BFS output that existing consumers/tests may pin. Cheap to switch later once cascade semantics settle; leaving them transient does not corrupt state. |
| O1 | `job_feedback_observer.py:2730` (in `_cleanup_descendants_of`, `:2689`) | observer descendant-cleanup | **DEFER — follow-up ticket** | It IS a cleanup path (missed descendants leave stale job artifacts — will eventually bite), but its behavior on terminal descendants is unverified and observer paths carry race-hardening history (W1 skip, pause filters). Switching needs its own verification pass; bundling it into P1 grows the blast radius past the B1/B4 mandate. Explicit follow-up: "migrate observer `_cleanup_descendants_of` to permanent enumeration + verify terminal-descendant cleanup semantics". |

Net: 5 site groups switch in P1 (P1, T1, T2, R1, M1); 2 deferred with named follow-ups (V1, O1). `get_tree_ids()` itself is NOT modified.

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | **Add `get_tree_ids_permanent()`** to `SQLModelInstanceRepository` (`daemon/repositories/instance/repository.py`, adjacent to `:313-341`) — Python-side BFS over `instances.parent_id`, `_MAX_TRAVERSAL_DEPTH` guard, no status filter. Add optional kill-switch wrapper `get_cascade_tree_ids(root_id)` reading `ENSEMBLE_CASCADE_LINEAGE` env (default `permanent`; `hierarchy` falls back to legacy `get_tree_ids`) — cascades call the wrapper. Unit tests in `tests/unit/test_tree_traversal.py` style: (a) 3-level tree completeness, (b) completeness after hierarchy rows deleted (churn), (c) after child terminate→revive, (d) root-not-found → `[]`, (e) BFS order root-first, (f) self-parenting cycle guarded by depth cap. | none | New tests green; `get_tree_ids()` behavior unchanged (existing `test_tree_traversal.py` passes untouched). |
| 2 | **Pause cascade switch** — `instance_lifecycle.py:2056`: `tree_ids = repo.get_cascade_tree_ids(root_id)`. Classification block `:2094-2102` UNCHANGED (skip PAUSED + TERMINAL into `skipped_ids`). Unit tests: churned tree → `paused_ids` includes all live descendants, terminal/paused descendants land in `skipped_ids`, graph-task cancel NOT invoked for skipped nodes. | 1 | `tests/unit/test_pause_instance_cascade.py` + `test_tree_aware_pause_resume.py` extended with churned-tree case; all green. |
| 3 | **Terminate recursion switch** — `instance_lifecycle.py:1385-1393`: replace inline `InstanceHierarchy` query with `list_child_ids_permanent(instance_id)`; add terminal-skip classification BEFORE recursing (skip children whose status ∈ TERMINAL_STATUSES — nothing to stop; log skip). Re-entrancy guard at `:1363-1369` unchanged (still short-circuits already-TERMINATED). Unit tests: churned tree terminate → live descendants TERMINATED, terminal children keep their prior status (completed stays completed), trace `children>0`. | 1 | New unit tests green; existing terminate/hard-delete tests pass. |
| 4 | **Hard-delete snapshot switch** — `instance_lifecycle.py:1930`: `tree_ids = instance_repository.get_cascade_tree_ids(instance_id)`. Assert-by-test the ordering invariant: snapshot taken BEFORE `terminate_instance` rewrites/deletes hierarchy rows (already the design; add a regression test that the snapshot is complete even when hierarchy is empty). | 1 | `tests/test_instance_hard_delete.py` extended: churned tree → checkpoint sweep + `hard_delete_tree` receive the complete permanent id set. |
| 5 | **Resume cascade switch [COORDINATION]** — `instance_lifecycle.py:2300`: same one-line swap. | 1 | Resume enumeration unit test (revived-then-paused child now in `tree_ids`); no regression in `tests/unit/test_resume_*`. Merge order de-conflicted with phase-2 worker by dispatcher. |
| 6 | **Maintenance switch** — `maintenance.py:831, :836`: swap to `get_cascade_tree_ids`. Unit test: pinned root with churned descendant → descendant in protected set. | 1 | `tests/test_maintenance.py` extended; green. |
| 7 | **B4-tail diagnosis (timeboxed ≤2h)** — verify the guard-livelock hypothesis: write a failing-repro unit test against `TaskRepository.claim_pending_task` with a PENDING `process_report` task whose `instance_id` targets a TERMINATED instance row (schema per `tests/message_queue_redesign/test_task_repository.py` harness). Expect: claim returns `None` + `[GUARD] … blocked by guard` diagnostic fires — confirming the pause gate (`task/repository.py:~1315-1334`, `status IN (paused, terminated)`, ALL task types) as root cause. If the repro unexpectedly PASSES the gate, instrument the live claim (EXPLAIN/bound params) and stop at the timebox: document findings, open follow-up, proceed with T8's enqueue-time guard anyway (it does not depend on the gate analysis). | none | Root cause confirmed-by-repro and documented in the task's test/commit message, OR timebox hit with documented findings + follow-up ticket. |
| 8 | **Designed terminal path for reports-to-dead-parents** — (a) enqueue-time guard: at the report-task creation seam (report lane; buffer origin `completion_registry.py:141` "Buffered completion (no event yet)"; task-creation seam to be pinned by a 30-min code-read at implementation start — expected in `child_reports.py` report-enqueue path near `:2510` region) check the parent instance's status BEFORE creating the PENDING `process_report` task: if TERMINATED or row-missing → dead-letter immediately — write the terminal state with canonical `terminal_reason='failed'` (from `_STATUS_CANONICAL_MAP`, `work_status.py:60-125`; NEVER `orphaned_no_task`) via the task repository's failure path (`fail_task` → `AbortTurn(reason='failed')`, `task/repository.py:1930-1941`), log one line. (b) reconcile sweep for residual/raced rows: extend `JobRecoveryService.reconcile_drift_states` (`job_recovery_service.py:488`) — or a sibling method in the same service — with a narrowly-scoped predicate: PENDING `process_report` tasks whose target instance is TERMINATED/missing → dead-letter with `reason='failed'`. `dependency_bus.py` remains the SOLE completion authority for JobItems — the sweep finalizes only the orphaned report Task rows, it must not touch JobItem completion. Unit tests for both (a) and (b) incl. canonical-reason assertion. [ARCHITECT: design choice — see flags] | 7 (soft — can start in parallel) | New unit tests green: no PENDING-forever row is created for a dead parent; stranded rows dead-lettered with `failed`; guard loop stops (blocked-count → 0). |
| 9 | **Data-repair for `d14cbde5`-class rows** — the T8(b) sweep's first run IS the repair (idempotent: dead-letters only PENDING report tasks with terminal/missing targets). Run it against the dev environment where the livelock is still live (dev daemon pid per repro §handoff; TERM via pid tree, port 8079 — never 8088), then verify: work row `d14cbde5` terminal with `terminal_reason='failed'`, `[GUARD]` lines stop appearing in `dev-daemon.log`. No production data migration (no schema change; prod exposure unknown — the sweep covers it opportunistically). | 8 | Dev-env verification captured (log excerpt + row state); sweep idempotence asserted by unit test. |
| 10 | **New e2e specs + mandatory gates** — author 2 new e2e tests in `tests/e2e/test_e2e_workflows.py` style (see Test Strategy), then run the full mandatory list one-by-one (no `-x`): the 5 blast-radius packs + release-gate e2e set. | 2,3,4,5,6,8,9 | All listed packs PASS; release-gate e2e 4/4 (happy path, pause-resume, terminate-revive, 3-level cascade) + 2 new specs PASS. |

## Exact Files / Functions Changed

| File | Function / site | Change |
|---|---|---|
| `daemon/repositories/instance/repository.py` | new `get_tree_ids_permanent` + optional `get_cascade_tree_ids` (kill-switch wrapper), placed in TREE TRAVERSAL section `:289-361` | additive |
| `daemon/services/instance_lifecycle.py` | `pause_instance_cascade :2056`; `terminate_instance :1385-1393`; `hard_delete_instance :1930`; `resume_instance_cascade :2300` | enumeration-source swap + terminal-skip classification in terminate recursion only |
| `daemon/services/maintenance.py` | protected-marking `:831, :836` | enumeration-source swap |
| `daemon/services/job_recovery_service.py` | `reconcile_drift_states :488` or sibling | + narrow dead-letter predicate for PENDING report→dead-parent tasks |
| report lane (`daemon/services/child_reports.py` enqueue seam; `daemon/services/completion_registry.py:141` buffer) | report-task creation | + parent-status guard dead-lettering at enqueue (exact creation seam pinned at implementation start) |
| tests | `tests/unit/test_tree_traversal.py`, `test_pause_instance_cascade.py`, `test_tree_aware_pause_resume.py`, `tests/test_instance_hard_delete.py`, `tests/test_maintenance.py`, `tests/message_queue_redesign/test_task_repository.py`, new e2e specs | extended + new |

Not touched: `get_tree_ids()` itself, `_terminate_instance_db_sync` raw DELETEs (`:3331` — known Phase 4b/4c deferred debt; P1 only relies on snapshot-before-terminate ordering, does not migrate it), `daemon/tools/job_queue.py` visibility tools, observer.

## Coupling

- **Tight with Phase 2 (B2/B3, parallel worker):** `resume_instance_cascade` (`:2269+`, task 5) — P2 owns the resume-handle/watcher fixes in the same function; one shared line (`:2300`). Sequencing P1→P2 with dispatcher-arbitrated merge order. Also shared seam: the terminate path (`_terminate_instance_db_sync`, compaction fire-vs-cancel in B3) — P1 does not modify it, only depends on its ordering.
- **Tight with Phase 3 (B5/B6/SSE):** none directly; B5 (`/stop` wrong-target) is router-level, B6 (detail 404) is rehydration — different seams.
- **Loose:** guard/dead-letter work (T7-T9) touches `task/repository.py` read paths (claim) only via tests; no change to the claim SQL itself.

## Risks

| # | Risk | Impact | Mitigation |
|---|------|--------|------------|
| R1 | **Revive semantics / classification ordering** — permanent enumeration now exposes TERMINATED/COMPLETED children to cascades. Pause already classifies (`:2094-2102`); terminate recursion does NOT today (hierarchy working set made it unnecessary) and would re-stamp terminal children. | High | T3 adds terminal-skip classification BEFORE recursion (classify → skip-or-act), mirroring the pause pattern; unit test asserts completed child keeps `completed` after parent soft-terminate. Order: enumerate (complete) → classify per node → act. |
| R2 | **Hard-delete snapshot ordering** — snapshot must precede hierarchy rewrite. | Medium | Already the design (`:1925-1930`); permanent source removes the dependency on pre-delete hierarchy rows entirely; regression test in T4. |
| R3 | **Guard-livelock fix masks other latent blocks** — a broad sweep could hide unrelated starvation. | Medium | Predicate scoped to `process_report`-type PENDING tasks with TERMINATED/missing target ONLY; unit test pins the predicate; [GUARD] diagnostic left intact for anything else. |
| R4 | **Performance on deep trees** — BFS issues one indexed query per level (`ix_instances_parent_id`); depth-capped at 256. Repro was 3 levels; production trees deeper but small. | Low | Pause/terminate/maintenance are admin/periodic frequency, not per-message hot paths. Recursive CTE documented as deferred alternative. Observer (highest-frequency consumer) NOT switched in P1. |
| R5 | **Phase-2 interplay** — same-file edits as B2/B3 worker; `_terminate_instance_db_sync` raw DELETEs bypass named transitions (existing 4b/4c debt). | Medium | P1 explicitly does not migrate or worsen it; dispatcher arbitrates merge order; coordination flag on task 5. |
| R6 | **Maintenance protection grows** — terminal descendants of pinned roots stop being TTL-purged. | Low | Matches pin intent; flagged in scope table (M1). |
| R7 | **Dead-letter design choice unverified by architect** — enqueue-time guard vs claim-time gate carve-out. | Medium | Recommendation recorded with rationale; [ARCHITECT] flag; enqueue-time chosen to keep the pause-gate invariant intact (it protects reports during PAUSE — resume re-enables delivery; TERMINATED is permanent so enqueue-time skip + sweep is fail-closed). |
| R8 | **Report-creation seam citation unverified** — the exact line where the orphaned `process_report` task row is created was not pinned this session (buffer origin verified at `completion_registry.py:141`). | Low | 30-min pinned code-read as the first step of T8; acceptance depends on tests, not the citation. |

## Test Strategy

**Unit (new/extended):**
- Enumeration completeness: 3-level tree; delete hierarchy rows (churn); terminate→revive a child; assert `get_tree_ids_permanent` still returns the full id set (T1).
- Classification skip: pause cascade skips PAUSED/TERMINAL (existing behavior preserved, now exercised against permanent enumeration) (T2); terminate recursion terminal-skip (T3).
- Terminate snapshot-before-delete ordering + complete id set to `hard_delete_tree`/checkpoint sweep (T4).
- Guard repro: PENDING `process_report` + TERMINATED target → claim returns None + blocked-diagnostic fires (T7 — documents root cause).
- Dead-letter: no PENDING row created for dead parent; sweep dead-letters stranded rows with canonical `failed` (never `orphaned_no_task`); idempotence (T8).

**Mandatory packs (blast radius: task/queue system touched via dead-letter + claim-guard tests; per `.agents/tester/rules/ensure.md` critical note):**
- `bash test/packs/claim_guard_locks_unit_test.sh`
- `bash test/packs/turn_transitions_reconciler_unit_test.sh`
- `bash test/packs/job_queue_unit_test.sh`
- `bash test/packs/concurrency_atomic_unit_test.sh` (Core always-on)
- plus changed-file packs ad-hoc: tree traversal / pause cascade / hard delete / maintenance suites above.

**Release-gate e2e (exact commands per `ensure.md:47-53`; `PYTEST_TIMEOUT=280`, one-by-one, no `-x`, queue cleaned before each):**
- happy path: `PYTEST_TIMEOUT=280 timeout 300 .venv/bin/pytest tests/e2e/test_e2e_workflows.py --override-ini="addopts=" --override-ini="timeout=280" -m integration -k "test_parent_child_workflow_happy_path" --tb=short -q`
- `-k "test_pause_after_spawn_then_resume"` (same pattern)
- `-k "test_terminate_after_spawn_then_revive"` (same pattern)
- `-k "test_three_level_cascade_reports"` (`timeout 320` variant)

**New e2e specs (T10):**
1. `test_pause_root_churned_tree_no_new_work` — build leader→child→grandchild; let one child complete (hierarchy row severed); re-delegate so a grandchild is mid-`sleep`; `POST /api/instances/{root}/pause`; assert: `paused_ids` ⊇ {root, live child, live grandchild}; NO new tool-call work starts under the paused root within a 60s observation window (message count frozen); drift reconciler reports consistent state.
2. `test_terminate_root_prechurn_live_child_not_orphaned` — root→child completes (churn)→child revived and mid-`sleep`; `DELETE /api/instances/{root}`; assert: revived child reaches TERMINATED (not left running/orphaned); no PENDING-forever work row for its completion report; zero `[GUARD] … blocked by guard` lines in the daemon log window; final audit: instances + work rows terminal-coherent.

## Rollback Story

- **Primary: straight revert of the single atomic commit** (separate committer). The change is additive at repository level + mechanical call-site swaps + a narrow sweep; no schema migration; no data transformation that a revert must undo.
- **Secondary (hot-path insurance, [IMPL-DETAIL]):** the `ENSEMBLE_CASCADE_LINEAGE` env kill-switch (`get_cascade_tree_ids` wrapper) — set `hierarchy` + restart to fall back to legacy enumeration without a code revert. Default `permanent`. Cost ~15 lines; droppable if architect deems it noise.
- **Data implications:** none for the enumeration switch (read-path change). The dead-letter writes terminal states with canonical `failed` — after a revert those rows stay terminal (they were unrecoverable garbage by definition); the sweep is idempotent, so re-deploying re-runs it safely. Reverting re-exposes B1/B4 (missed cascades) but corrupts nothing.
- **Dev-env note:** the live livelock on the repro daemon is cleared by T9 (or daemon TERM per repro §handoff).

## Architect Flags

| ID | Decision | Level | Question |
|---|---|---|---|
| AF1 | Lineage duality | **[ARCHITECT]** (pre-flagged) | Should `instance_hierarchy` be deprecated for cascades entirely, or does any consumer NEED transient (live-working-set) semantics? After P1 the remaining transient consumers are the 3 visibility tools (V1) and observer cleanup (O1). If none need transience, a fast-follow can repoint/deprecate `get_tree_ids()`; if observer needs working-set semantics, the duality is real and should be documented as such in the blueprint. |
| AF2 | Reports-to-dead-parents terminal path | **[ARCHITECT]** | Enqueue-time guard + reconcile sweep (recommended — keeps pause-gate invariant intact, fail-closed) vs claim-time carve-out (let `process_report` bypass the pause gate for TERMINATED targets and dead-letter inside the report lane — one fewer sweep, but weakens a guard shared by all task types and still needs in-lane dead-parent handling). |
| AF3 | Resume-site migration (task 5) | **[COORDINATION]** | `instance_lifecycle.py:2300` is inside phase-2's function. Include the one-line swap in P1 (recommended — enumeration helper is P1's deliverable) or hand the line to the P2 worker? Dispatcher arbitrates merge order either way. |
| AF4 | Observer deferral (O1) | **[ARCHITECT]-lite** | Confirm deferral of `_cleanup_descendants_of` migration to a named follow-up vs pulling it into P1. |
| AF5 | BFS vs recursive CTE | **[IMPL-DETAIL]** | Python-side BFS chosen (repo convention, SQLite compat, depth-guarded). Recursive CTE documented as deferred alternative for very deep trees. |
| AF6 | Status-filter placement | **[IMPL-DETAIL]** | Classification-after-enumeration preserved (confirmed sound; see Approach). |

## Success Criteria

| # | Criterion | How to Measure | Threshold |
|---|-----------|----------------|-----------|
| 1 | Pause root → ENTIRE non-terminal tree pauses, incl. churned/revived descendants | New e2e spec 1 + unit churn tests | `paused_ids` = all live nodes; zero new tool calls post-pause in 60s window |
| 2 | Terminate root → live descendants (incl. revived, hierarchy-severed) terminate | New e2e spec 2 + unit tests | child status `terminated`; no orphan-running instance |
| 3 | No report-to-dead-parent PENDING-forever rows; no `[GUARD]` livelock | e2e spec 2 log scan + dev-env repair (T9) | zero blocked-by-guard lines; `d14cbde5`-class rows terminal with `terminal_reason='failed'` |
| 4 | Terminal children untouched by cascades (revive semantics preserved) | Unit tests T2/T3 | completed/failed children keep status; only `skipped_ids`/log lines |
| 5 | No regression in mandatory surfaces | 4 packs + release-gate e2e | all PASS, one-by-one, no `-x` |
| 6 | `get_tree_ids()` untouched (transient consumers unaffected until follow-ups) | existing `test_tree_traversal.py` | green without modification |

## Exit Criterion

All 10 tasks accepted; mandatory packs + release-gate e2e (incl. the 2 new specs) green; dev-env livelock cleared with the stranded row repaired; AF1/AF2 (and AF3/AF4) answered by architect; plan for V1/O1 follow-ups filed. Phase 2 (B2/B3) and Phase 3 (B5/B6/B7/SSE) can then proceed on a tree subsystem whose cascades see the whole permanent lineage.
