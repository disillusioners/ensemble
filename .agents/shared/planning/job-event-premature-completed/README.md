# job-event-premature-completed — mission-live guard

## What this branch fixes

Premature `completed ✓` job events for task-type LEADER jobs whose
per-turn task row settles while the mission is still live
(`child_reports` defers the JobItem finalize behind running children).
Two emitter sites, both now guarded:

1. **Drift reconciler Pattern f2** (`job_recovery_service.reconcile_drift_states`):
   the "Task COMPLETED but JobItem never transitioned" predicate had no
   mission-live guard. Live repro 2026-09-22: events 79328 (lag 183s) and
   79349 (lag 232s) — both deferred leader jobs. The recent result-arm +
   F10 notify arm (e57c806b→b024bb09, b4e11583) made the false finalize
   deliver a false terminal AND CAS-delete the mission watcher row.
2. **Boot sweep** (`job_queue_service.reconcile_terminal_watches`):
   fired terminal for mission-keyed (`mission_terminal`) watches on
   settled work rows at restart with no liveness consult.

## Shape

- Shared helper: `daemon/services/mission_live_guard.py`
  (`evaluate_mission_live`) — used by BOTH sites.
- LIVE legs: (a) bus pending watchers, (b) any non-terminal descendant,
  (c) non-terminal root. Non-terminal = NOT IN
  `TERMINAL_INSTANCE_STATUSES`; **idle counts LIVE** (mission resolver
  canonicalizes IDLE → `processing`). Tree walk uses
  `get_tree_ids_permanent` (`instances.parent_id` permanent record —
  resolver reference semantics).
- Zombie backstop: work `completed_at` older than
  `MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS` (6h, module constant, no env
  flag per repo fix/flag policy) falls through to finalize —
  at-least-once terminal delivery preserved, starvation impossible.
- Fail-open: any guard error → finalize + notify proceed (a missing
  terminal is worse than an extra premature one).
- Skips are observable: drift detail `orphan_active_skipped_mission_live`;
  INFO log naming which leg was live.

## File overlap

**Zero file overlap with `feature/job-answer-tool`** (which owns
`daemon/tools/job_queue.py`). This branch touches:

- `daemon/services/mission_live_guard.py` (new)
- `daemon/services/job_recovery_service.py` (guard inserted after the
  existing f2 gates 1–3; existing gate skip-detail names unchanged)
- `daemon/services/job_queue_service.py` (guard in the resolver-based
  `reconcile_terminal_watches` path only; legacy no-resolver path and
  receipt-kind watches UNCHANGED)
- tests: `tests/job_queue/test_mission_live_guard.py` (new, matrix (i)–(vi)),
  fixture doctrine updates in `test_orphan_active_job_recovery.py` +
  `test_f1_killswitch_tz_matrix.py` (f2-finalize-expected seeds now use a
  TERMINAL root — the "mission ended, JobItem side lagged" shape; a
  RUNNING root is the mid-mission shape the guard holds ACTIVE).

## Dependency

Depends on the **b024bb09 result-arm base** (result_summary +
notify arm on force-complete paths). Base for this branch:
`latest@0062e6fc`.
