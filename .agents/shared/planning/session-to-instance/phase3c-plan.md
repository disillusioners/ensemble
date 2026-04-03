# Phase 3c: Services — Job Processor, Lock Manager, Queue Service

## Objective
Rename all session references in the job processing services. These files import `SessionManager` (renamed to `InstanceManager` in Phase 3a) and use `session_id` as the identifier that flows from job creation through to agent spawning.

## Context
- **Phase 3a completed**: `InstanceManager` class renamed, `spawn_instance()` method renamed
- **Key insight**: The `session_id` in the job system IS the same value as the agent instance ID. The job queue generates `session_id = str(uuid.uuid4())`, stores it in `JobItem.session_id`, and passes it to `spawn_session()`. Since they are the same concept, ALL of these must be renamed to `instance_id`.
- This phase can run in parallel with Phases 3b, 4, and 5

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | **Rename daemon/services/job_processor.py** (~157 lines) | Update import: `from daemon.manager import SessionManager` → `from daemon.manager import InstanceManager`. Update type hints: `self._session_manager` → `self._instance_manager`. Update `spawn_session()` call → `spawn_instance()`. Update `session_id = self._session_manager.spawn_session(...)` → `instance_id = self._instance_manager.spawn_instance(...)`. Update `enqueue_message(session_id=...)` → `enqueue_message(instance_id=...)`. Update `started_job.session_id` → `started_job.instance_id` (field from DB model, renamed in Phase 1). | `daemon/services/job_processor.py` (~157 lines) |
| 2 | **Rename daemon/services/job_lock_manager.py** (~400 lines) | Rename fields: `LockInfo.session_id` → `LockInfo.instance_id`. Rename method params: `acquire(project_id, job_id, session_id)` → `acquire(project_id, job_id, instance_id)`. Rename methods: `release_by_session(session_id)` → `release_by_instance(instance_id)`, `release_by_session_sync(session_id)` → `release_by_instance_sync(instance_id)`. Update internal tracking: `_locks_by_session` → `_locks_by_instance` or similar dict keys. | `daemon/services/job_lock_manager.py` (~400 lines) |
| 3 | **Rename daemon/services/job_queue_service.py** (~467 lines) | Rename local variables: `session_id = str(uuid.uuid4())` → `instance_id = str(uuid.uuid4())`. Update all references: `self._repository.start_job(job.job_id, session_id)` → `self._repository.start_job(job.job_id, instance_id)`. Update lock calls: `self._lock_manager.acquire(..., session_id=session_id)` → `self._lock_manager.acquire(..., instance_id=instance_id)`. Rename method: `release_lock_by_session(session_id)` → `release_lock_by_instance(instance_id)`. Update all `job.session_id` → `job.instance_id` references (DB model field renamed in Phase 1). | `daemon/services/job_queue_service.py` (~467 lines) |
| 4 | **Update daemon/services/__init__.py** | Verify exports: `JobLockManager`, `LockInfo`, `JobQueueService` — check if any session-related names are exported and rename them. | `daemon/services/__init__.py` |

## Key Files
- `daemon/services/job_processor.py` — ~157 lines, imports InstanceManager
- `daemon/services/job_lock_manager.py` — ~400 lines, tracks locks by instance_id
- `daemon/services/job_queue_service.py` — ~467 lines, generates UUIDs, calls lock manager
- `daemon/services/__init__.py` — service exports

## The session_id Flow (Why It Must All Be Renamed)

```
job_queue_service.py:
    instance_id = str(uuid.uuid4())          ← UUID generated
    self._repository.start_job(job_id, instance_id)   ← stored in JobItem.instance_id
    self._lock_manager.acquire(job_id, instance_id)    ← lock tracked

job_processor.py:
    started_job.instance_id                  ← read from DB (Phase 1 renamed field)
    instance_id = self._instance_manager.spawn_instance(
        session_id=started_job.instance_id   ← ← ← IMPORTANT: this param name
    )                                         is renamed in manager.py to instance_id

job_lock_manager.py:
    release_by_instance(instance_id)         ← releases locks
```

**Note on manager.py spawn_instance() parameter**: In Phase 3a, `spawn_session(session_id=...)` became `spawn_instance(instance_id=...)`. So job_processor.py must pass `instance_id=started_job.instance_id`.

## Constraints
- `job_processor.py` imports `SessionManager` from `daemon.manager` — must use new import `InstanceManager`
- The `session_id` parameter name in `spawn_instance()` is renamed to `instance_id` in Phase 3a — the call site here must match
- `job.session_id` → `job.instance_id` depends on DB model field renamed in Phase 1 (JobItem model)
- `LockInfo.session_id` → `LockInfo.instance_id` depends on Phase 1 (job_queue models)

## Verification
```bash
# 1. No old names in services
grep -rn "SessionManager\|session_id\|spawn_session\|release_by_session\|release_lock_by_session" daemon/services/ | grep -v "db_session"

# 2. New names present
grep -c "InstanceManager\|instance_id\|spawn_instance\|release_by_instance\|release_lock_by_instance" daemon/services/job_processor.py daemon/services/job_lock_manager.py daemon/services/job_queue_service.py

# 3. Import check
python -c "from daemon.services import JobLockManager, JobQueueService; print('OK')"
```

## Deliverables
- [ ] `daemon/services/job_processor.py` — InstanceManager import, spawn_instance call, instance_id vars
- [ ] `daemon/services/job_lock_manager.py` — LockInfo.instance_id, release_by_instance methods
- [ ] `daemon/services/job_queue_service.py` — instance_id UUIDs, release_lock_by_instance
- [ ] `daemon/services/__init__.py` — exports verified
- [ ] Method call sites match manager.py (Phase 3a) renamed methods
- [ ] Grep shows 0 old session names in services/ (excluding exclusions)
