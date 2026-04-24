# Duplicate add_watch() Bug Found During Phase 3 Testing

**Date**: 2026-04-24
**Severity**: Low (benign, no functional impact)
**Files**: `daemon/tools/job_queue.py`

## Bug Description

Both `watch_job` and `watch_jobs` tools have duplicate `add_watch()` calls in the terminal-state branch.

### Location 1: `watch_job` tool (lines 516-518)
```python
if job.status in TERMINAL_STATES:
    watcher_repo.add_watch(job_id, current_instance_id, events)  # line 516
    # Register watch first, then notify (notify_watchers sends + cleans up)
    watcher_repo.add_watch(job_id, current_instance_id, events)  # line 518 (DUPLICATE)
    await job_service.notify_watchers(job_id, job.status, job.error_message)
```

### Location 2: `watch_jobs` tool (lines 605-607)
```python
if job.status in TERMINAL_STATES:
    watcher_repo.add_watch(jid, current_instance_id, events)  # line 605
    # Register watch first, then notify (notify_watchers sends + cleans up)
    watcher_repo.add_watch(jid, current_instance_id, events)  # line 607 (DUPLICATE)
    await job_service.notify_watchers(jid, job.status, job.error_message)
```

## Why It's Benign
`add_watch()` handles duplicates gracefully — it checks for existing (job_id, instance_id) pairs and updates the events if found. The second call simply re-updates the same watch.

## Fix
Remove the duplicate line (either line 516 or 518 for watch_job, and either 605 or 607 for watch_jobs). Keep only one `add_watch()` call before `notify_watchers()`.
