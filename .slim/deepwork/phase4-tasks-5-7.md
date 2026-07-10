# Phase 4 Skill Evolution — Integration Tasks 5-7

## Goal
Wire the Tier 0 metrics recorder + Tier 1 trigger engine into the
job-queue completion hook, the manager startup flow, and the periodic
maintenance scan. Implement the `skill_feedback` tool backend.

## Files we own (write/edit)
- `daemon/services/job_queue_service.py` — add hook + helper in `_finalize_terminal`.
- `daemon/manager.py` — initialize services; seed triggers; register scan job.
- `daemon/tools/skill_tools.py` — replace stub body of `skill_feedback`.
- `tests/job_queue/test_phase4_metrics_hook.py` (new) — hook + helper tests.
- `tests/manager/test_skill_metrics_trigger_init.py` (new) — manager init tests.
- `tests/tools/test_skill_feedback_tool.py` (new) — feedback tool tests.
- `tests/services/test_skill_metric_scan.py` (new) — scan handler tests.

## Files we read-only
- `daemon/services/skill_metrics_service.py` (exists, Task 1)
- `daemon/services/skill_trigger_engine.py` (exists, Task 3)
- `daemon/services/skill_trigger_seed.py` (exists, Task 4)
- `daemon/repositories/skill/repository.py` (counter helpers, Task 2)

## Constraints (from spec)
1. Hook at `_finalize_terminal` — single chokepoint.
2. Job enqueues MUST use `system_parallel_queue` (resolve via `queue_repo.get_by_name`).
3. Config via `self._config.skill_evolution` (NOT `EnsembleConfig`).
4. Metrics recording must fail gracefully — wrap in try/except.
5. `feedback_applied` defaults to NULL in DB; repository may coerce.
6. Do NOT implement evolution itself (Tier 2/3) — only enqueue.
7. Spec says `self._manager` but the actual attribute on `JobQueueService` is `self._instance_manager` — use the real one.

## Plan
- T5: Add `_get_task_details(self, job_id)` helper that reads JobItem + duration + message count. Add hook after `finalize_active_to_done()` in NO_RETRY branch.
- T6: In `InstanceManager.initialize`, build the 3 missing skill repos, build the metrics service + trigger engine, call `seed_default_triggers` for global set (`project_id=None`).
- T7a: Add `_run_skill_metric_scan()` on InstanceManager. Register with `MaintenanceService` at `min_interval_hours=24`. The method calls `evaluate_all(project_id=None)`, then for each flagged skill enqueues a job on `system_parallel_queue`.
- T7b: Replace stub body of `skill_feedback` to call `record_feedback` with project_id + agent_id from instance context.

## Validation
- New unit tests per task; run ONLY the new packs (`pytest tests/job_queue/test_phase4_metrics_hook.py tests/manager/test_skill_metrics_trigger_init.py tests/tools/test_skill_feedback_tool.py tests/services/test_skill_metric_scan.py -v --tb=short`).