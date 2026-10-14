# P1 Pause/Resume/Terminate Tree-Fix (T7-T9) — Lessons & Findings

Date: 2026-08-24
Worker: coder (this session)
Branch: feature/pause-resume-terminate-tree-fix
Commits: 594547e6..cbc21c09 (7 commits, all on this branch)

## Mission

Implement Phase 1 (P1), tasks T7-T9 of the approved
pause/resume/terminate tree-propagation fix — the B4-tail
dead-letter subsystem. T1-T6 (enumeration subsystem) had already
landed in commit `3824e881` by coder-A.

## Tasks Completed vs Plan T-numbers

### T7 — B4-tail diagnosis (✅ confirmed)

Hypothesis confirmed by unit test:
`task/repository.py:claim_pending_task` blocks PENDING process_report
Tasks whose instance_id targets a TERMINATED Instance row, and emits
the `[GUARD] … blocked by guard` DEBUG diagnostic.

The fix is **not** in the pause gate itself (it is the canonical
"every task type" invariant per plan §C); it is the T8 dead-letter
mechanism that gives the row a terminal path.

Test cases:
- `TestB4TailDiagnosis::test_terminated_instance_blocks_report_task_claim`
- `TestB4TailDiagnosis::test_terminated_instance_emit_guard_debug_log`
- `TestB4TailDiagnosis::test_paused_instance_blocks_report_task_claim`
- `TestB4TailDiagnosis::test_running_instance_unblocks_report_task_claim`

### T8(a) — Enqueue seam dead-parent guard (✅ accepted)

`daemon/services/child_reports.py:_process_child_completion_db_sync` —
extended the existing PAUSED-only skip to a three-way skip:

  * `marker_paused` (question() pause marker)
  * `db_paused` (parent.status==PAUSED)
  * `db_dead_parent` (parent is None or TERMINATED)

When dead-parent: marks message `MessageStatus.FAILED`, skips both
Task + ReportInjection INSERTs, returns `_ChildCompletionDbResult(
outcome='dead_parent_skip')`. The downstream async dispatcher
handles the new outcome as a side-effect-free return.

### T8(c) — DeadLetterTurn named transition (✅ accepted)

`daemon/services/turn_transitions.py` — new named transition
`PENDING → FAILED` with canonical `terminal_reason='failed'`
(leader D3), `MIRROR_SET=ALL_8_MIRRORS`, atomic `status='pending'`
guard, registered in `TRANSITIONS` tuple.

Replaces Rev-1's `fail_task → AbortTurn(reason='failed)` no-op.

### T8(d) — Drift sweep Pattern (e) (✅ accepted)

`daemon/services/job_recovery_service.py` — new
`_pattern_e_dead_letter_dead_parent_process_reports` method
called from `reconcile_drift_states` after patterns (a)-(d).

Predicate is scope-strict per plan §R3 (process_report only).
Action: atomic UPDATE with parent-status EXISTS in WHERE;
companion `report_injections` row DELETE; `DeadLetterTurn`
named transition for the canonical mirror reconcile.

### T8(e) — Secondary seam dead-parent guard (✅ accepted)

`daemon/manager.py:_reconcile_deferred_report` +
`_create_subshape_a_artifacts` — added `db_dead_parent` check
shared across sub-shapes a + b (message-only and task-only). When
dead-parent: message row marked FAILED, no Task INSERT, injection
row's `state` flipped to dead-letter sentinel `state='failed'`,
returns `{"shape": "dead_parent_skip", ...}`.

### T9 — Data-repair + MANDATORY DAEMON RESTART (✅ accepted)

The `d14cbde5-cf2f-4ee2-86f1-8241bd890980` work_id was already
cleared by the existing pattern (d) (drift reconciler cancelled
task 357 at 03:53:35 — see /tmp/pause-repro-20260824/dev-daemon.log).

My new pattern (e) sweep ran cleanly post-restart with no errors
(`reconciled=0, details=0` — no remaining stranded rows).

Mandatory restart executed per W7 (macOS convention):
  * PRE: dev.sh=12534, uvicorn=12539, port=8079 bound
  * TERM uvicorn pid tree (graceful shutdown, no orphan children)
  * POST: dev.sh=34507, uvicorn=34513, port=8079 still bound
  * Cascade lineage boot-log INFO fired at 04:14:19 (immediately
    post-restart) — coder-A's C4 INFO line preserved
  * No `[GUARD]` log lines in the post-restart log file (truncated
    on restart, then observed fresh)

## Files Changed

```
daemon/manager.py                                    (T8(e) secondary seam)
daemon/services/child_reports.py                     (T8(a) enqueue seam)
daemon/services/job_recovery_service.py              (T8(d) drift sweep Pattern (e))
daemon/services/turn_transitions.py                  (T8(c) DeadLetterTurn)
tests/unit/test_pause_resume_terminate_tree_fix_p1.py (T7+T8 tests — 33 cases)
```

No edits to coder-A's seams:
  * `daemon/repositories/instance/repository.py` — untouched
  * `daemon/services/instance_lifecycle.py` — untouched
  * `daemon/services/maintenance.py` — untouched
  * `daemon/manager.py:__init__` — `emit_cascade_lineage_boot_log()`
    at line 706 preserved (C4 boot log)

## Test Results

- T7+T8 unit suite (new): 33 / 33 passed
- Whole-tree pause/terminate/maintenance suite: 221 / 221 passed
- 42 skipped (PostgreSQL-only / legacy fixtures)
- 1 pre-existing failure in `tests/test_terminal_orphan_matrix.py`
  (`test_jobitem_task_status_matrix[pending-True-active]`) — verified
  pre-existing via `git stash` (fails without my changes too). Not
  introduced by this patch.

## Commit SHA List

| SHA       | Message                                                           |
|-----------|-------------------------------------------------------------------|
| 594547e6  | feat: P1 T7+T8(c) — DeadLetterTurn + B4-tail diagnosis tests        |
| 37f6402b  | feat: P1 T8(a) — enqueue seam dead-parent guard                    |
| a506691e  | feat: P1 T8(e) — secondary seam dead-parent guard                  |
| 6d4ee6dc  | test: P1 T8(e) — secondary seam predicate lockstep tests           |
| b571a7eb  | feat: P1 T8(d) — drift sweep Pattern (e) production code          |
| 370055c6  | fix: P1 T8(d) — resolve engine via task_repository                 |
| cbc21c09  | fix: P1 T8(d) — import sqlalchemy.text module-level                |

## T9 Evidence (Captured)

### Pre-restart (21:11:27 UTC)
- d14cbde5 row already absent from `task` table (cleared earlier by
  pattern (d) at 03:53:35)
- `[GUARD]` log lines stopped at 04:01:40 (no new entries since)
- Daemon pids: dev.sh=12534, uvicorn=12539, port=8079
- Pattern (e) sweep had runtime errors (engine attr + text import)
  — fixed in commits 370055c6 and cbc21c09

### Restart (W7, 04:14:08-04:14:17 UTC)
- TERM 12539 → graceful exit (uvicorn --timeout-graceful-shutdown 10)
- All 3 pids gone (dev.sh 12534, uvicorn 12539, worker 34295)
- Port 8079 freed, then re-bound by new daemon
- Launched via `nohup ./dev.sh &` per macOS convention

### Post-restart (21:14:54 UTC, 21:17:37 UTC)
- Cascade lineage INFO fired at 04:14:19 (C4 preserved)
- `Application startup complete` logged
- New pids: dev.sh=34507, uvicorn=34513, worker=34515
- Port 8079 still bound (correct)
- Pattern (e) sweep runs cleanly (`reconciled=0, details=0`)
- Zero `[GUARD]` log lines in post-restart log
- `/readyz` → `{"status":"ready",...}`, `/livez` → `{"status":"alive",...}`

## Plan Deviations

1. **Pattern (e) sweep count is 0, not 1** — the d14cbde5 row was
   already cleared by the existing pattern (d) before my code
   loaded (drift reconciler at 03:53:35 cancelled task 357). The
   sweep was still run (it's idempotent), but no rows required
   dead-letter. This is the expected idempotent behavior per plan §T9.

2. **Daemon restart was prompted by both W7 + my own code reloads.**
   The uvicorn --reload mechanism reloaded my changes 4-5 times
   during implementation (visible in the log as repeated
   `Cascade lineage` boot lines + `Application startup complete`
   pairs at 04:00:15 / 04:01:20 / 04:01:45 / 04:02:09 / 04:03:16 /
   04:09:04 / 04:10:53). The W7 mandatory restart at 04:14:19 was
   the load-bearing deterministic-gate step.

3. **T8 (b) sweep DELETE was scoped to single-row-per-Task** (not
   bulk) per the plan's "single transaction" requirement — keeps
   the sweep atomic and the companion DELETE honest about
   non-delivery.

## Discoveries Affecting Tester's T10 Gate / P2 / P3

1. **Pattern (e) sweep is purely additive** — no existing pattern
   (a)-(d) behavior changes. P2/P3 can extend `reconcile_drift_states`
   without conflict (the pattern (e) call is wrapped in its own
   try/except, so a future pattern (f) failure won't cascade).

2. **`d14cbde5` work_id is cleared from dev DB**, so the e2e spec 2
   in T10 (`test_terminate_root_prechurn_live_child_not_orphaned`)
   can be run on the post-restart daemon without the livelock
   interference. The P1 acceptance criteria §3 (no report-to-dead-
   parent PENDING-forever rows, no `[GUARD]` livelock, companion
   injection rows disposed) is met.

3. **The `report_injections` table does not exist in the current
   dev DB schema** — `data_dev/instances.db` has 24 tables but
   `report_injections` is not among them (the dev DB was
   initialized before the table was added). This does not affect
   pattern (e)'s correctness (the DELETE is a no-op if the table
   is absent), but it means T10's e2e tests should ensure the
   `report_injections` table is present in the test fixture
   schema before exercising the companion-DELETE path.

4. **Pre-existing test failure** in
   `tests/test_terminal_orphan_matrix.py::
   test_jobitem_task_status_matrix[pending-True-active]` — verified
   pre-existing (fails without my changes too via `git stash`).
   The test expects `busy==True and claim==None` when an active
   JobItem has a PENDING backing task, but the live reconcile
   behavior shows a "Turn mirror invariant failed" warning + the
   claim succeeds. Not introduced by this patch — P2/P3 worker
   should investigate if needed.

5. **`emit_cascade_lineage_boot_log()` call site is in
   `InstanceManager.__init__` (line 706)** — coder-A's C4 INFO log
   is preserved through all my changes. The boot log fires every
   time the daemon restarts; the FT-004 removal ticket (~+30
   days post-soak) will need to remove both the call site and the
   wrapper helper.

## Plan Refs

- Phase 1 plan: `.agents/shared/planning/pause-resume-terminate-tree-fix/phase1-plan.md` (Rev 2.1, binding)
- Decisions: `.agents/shared/planning/pause-resume-terminate-tree-fix/decisions.md` (D3 = canonical `'failed'`, D4/FT-005 already filed)
- Architect: `.agents/shared/planning/pause-resume-terminate-tree-fix/architecture-recommendation.md` (AF1/AF2 resolved at Rev 2; AF2 C1/C3/C5/C6 applied here)