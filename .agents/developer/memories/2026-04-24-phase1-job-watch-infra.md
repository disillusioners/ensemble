# Phase 1: Job Watch Infrastructure — Implementation Notes

## Date: 2026-04-24

## Summary
Implemented Phase 1 of the Job Watch Infrastructure for the jober agent feature. This adds the ability for agents to subscribe to job lifecycle events and receive notifications as messages.

## Commits (4 total)
1. `0d4dc50` — `feat(job-watcher): add JobWatcher model and repository`
2. `0108ea2` — `feat(job-watcher): add notify_watchers() and hook into all 7 terminal paths`
3. `8a936c4` — `feat(job-watcher): add watch tools and watch parameter on job_create`
4. (fix commit) — `fix(job-watcher): terminal notification race and instance_manager guard`
5. (test commit) — `test(job-watcher): update tool count test for 4 new watch tools`

## Files Created
- `daemon/repositories/job_queue/watcher_models.py` — JobWatcher SQLModel (JSON watch_events column)
- `daemon/repositories/job_queue/watcher_repository.py` — CRUD + reconciliation queries

## Files Modified
- `daemon/repositories/job_queue/__init__.py` — Export JobWatcher + JobWatcherRepository
- `daemon/services/job_queue_service.py` — notify_watchers(), set_watcher_repo(), reconcile_terminal_watches(), hooks in cancel_job, complete_job, complete_job_sync
- `daemon/services/job_feedback_observer.py` — Path 1 hook + demand_state fix
- `daemon/services/dead_letter_service.py` — Path 5 hook (run_coroutine_threadsafe after commit)
- `daemon/services/job_retry_engine.py` — Path 6 hook (run_coroutine_threadsafe after commit)
- `daemon/services/job_recovery_service.py` — Path 7 hook (direct await, async context)
- `daemon/services/instance_lifecycle.py` — Auto-cleanup watches on terminate
- `daemon/tools/job_queue.py` — 4 new tools (watch_job, unwatch_job, list_watched_jobs, watch_jobs) + watch param on job_create
- `daemon/tools/instance.py` — Pass watcher_repo through to create_job_tools
- `daemon/api.py` — Bootstrap wiring (watcher_repo creation, ordering, reconciliation)
- `daemon/services/job_processor.py` — demand_state fix (7 call sites)
- `tests/test_job_queue_tools.py` — Tool count 12→16

## Key Architecture Decisions
- JSON column for watch_events (not comma-separated)
- All 7 terminal paths call single notify_watchers() in JobQueueService
- sync→async bridge uses asyncio.run_coroutine_threadsafe() for Paths 5,6
- Bootstrap ordering: watcher_repo → recovery → reconciliation → dead_letter → observer
- Backward compatible: watcher_repo=None means everything works as before
- Max 50 watches per instance

## Bugs Found & Fixed
- Terminal notification race: watch_job() called notify_watchers() before registering watch
- _instance_manager None guard missing in notify_watchers()
- count_watches_for_instance() was O(n) — fixed to O(1) with COUNT(*)
- complete_job(success=) callers updated to demand_state= (2 sites in observer, 7 in processor)
