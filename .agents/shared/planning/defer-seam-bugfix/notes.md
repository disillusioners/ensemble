# Working Notes: Defer Seam Bugfix

## Bug → Phase → Category Mapping

| Bug ID | Pattern | Category | Phase | Severity | Status |
|--------|---------|----------|-------|----------|--------|
| P1 | S1 | A | 1 | Critical | planned |
| P2 | S2 | B | 1 | Critical | planned |
| F1 | S3 | C | 2 | Critical | planned |
| F2 | S2 | B | 3 | Critical | planned |
| F3 | S4 | C | 2 | Critical | planned |
| F4 | S5 | C | 2 | Critical | planned |
| F5 | S5 | C | 3 | High | planned |
| F6 | S5 | C | 3 | High | planned |
| F7 | S5 | C | 2 | High | planned |
| F8 | S2 | B | 3 | High | planned |
| F9 | S5 | — | deferred | High (PG-only) | deferred |
| F10 | S5 | C | 3 | Medium | planned |
| F11 | S1 | A | 1 | Medium | planned |
| F12 | S5 | C | 3 | Medium | planned |
| F13 | S3 | C | 3 | Medium | planned |
| F14 | S5 | C | 3 | Medium | planned |
| F15 | S5 | C | 3 | Medium | planned |
| F16 | S4 | — | deferred | Low | deferred |
| F17 | S5 | D | 1 | Low (infra) | planned |

## Pattern Legend
- **S1**: reader of `metadata.message_id` whose writer never sets it
- **S2**: "active/idle work" predicate blind to one half of the dual tables
- **S3**: cross-table correlation by `instance_id` assuming 1:1
- **S4**: lossy `admission_state`↔`status` vocabulary mapping
- **S5**: recovery / lock / observer paths that don't reconcile both tables

## Key Code Locations (from exploration)

### Phase 1 targets
- `job_processor.py:707-720` — enqueue call (no `is_deferred`, no `message_id` stamp)
- `job_processor.py:406-419` — Gate A defer idle-check (counts JobItems only)
- `job_processor.py:540-595` — orphan recovery paths (same omissions)
- `task/repository.py:516-572` — cross-system guard (NULL `message_id` → self-deadlock)
- `task/repository.py:1052-1101` — `has_pending_tasks_blocked_by_busy_instance` (same NULL issue)
- `task/repository.py:467-489` — Gate B defer idle-check (task-level, needs `is_deferred=true`)
- `instance_messaging.py:971-981` — `enqueue_message` signature (`is_deferred` keyword-only, default False)
- `instance_messaging.py:802-822` — `message_id` generation (always `str(uuid.uuid4())`)
- `instance_messaging.py:877-891` — Task creation with `is_deferred` stamp
- `job_queue_service.py:1722-1771` — `_select_next_eligible_job` (Gate B / F8)
- `job_queue/repository.py:442-474` — `count_active_jobs_in_non_defer_queues` (JobItem-only, superseded by shared predicate)
- `maintenance.py:212-242` — `_is_idle` (only checks `queued` JobItems + request registry)
- ⚠️ `AsyncMessageResult` already carries `message_id` — no return type change needed, just capture it

### Phase 2 targets
- `job_queue_service.py:1420-1435` — `_finalize_terminal` lock release (`release_by_instance` unconditional)
- `lock_repository.py:84-108` — `release_by_job` (exists, ready to use)
- `lock_repository.py:110-128` — `release_by_instance` (current, unscoped)
- `work_resolver.py:945-981` — dedup by `instance_id` (should be `message_id`; HARD dep on Phase 1 stamping)
- `work_resolver.py:339-347` — `_JOB_CANONICAL_TO_ADMISSION` (lossy map)
- `job_queue/models.py:62-67` — `_ADMISSION_TO_LEGACY_STATUS` (lossy: done → completed)
- ⚠️ NULL `terminal_reason` on pre-Phase-7c databases — fallback to `_ADMISSION_TO_LEGACY_STATUS` + backfill migration

### Phase 3 targets
- `job_recovery_service.py:97-193` — `recover_on_startup` (startup-only, no periodic)
- `stale_task_recovery.py:615-635` — `notify_work_watchers` fire-and-forget
- `stale_task_recovery.py` — needs new `force_complete_task(task_id, reason)` method (does not exist yet)
- `task/repository.py:1294-1318` — `schedule_retry` (fresh `work_id` is CORRECT — watcher migration, not work_id reuse)
- `job_retry_engine.py:318-336` — `atomic_retry` (cancel stale PENDING BEFORE start_job)
- `job_feedback_observer.py:620-630` — `get_active_by_instance` (freshest, not exact)
- `job_feedback_observer.py:2258, 2387` — bus gate (dependency_watchers only)
- `job_feedback_observer.py:741-776, 1636-1795` — `_deferred_finalize_check` (TOCTOU)
- ⚠️ Reconciler must NOT use MaintenanceService._loop (15-min interval + `_is_idle` gated); use StaleTaskRecovery's loop or own asyncio task
- ⚠️ F6 approach: watcher migration (`UPDATE job_watchers SET job_id = :child_work_id WHERE job_id = :parent_work_id`) inside retry transaction; `notify_work_watchers` uses exact match, no prefix

## Test Infrastructure Notes

### Existing test fixtures
- **SQLite**: `tests/job_queue/conftest.py` — `:memory:` + `StaticPool`, session-scoped engine, function-scoped repos, autouse `_truncate_tables`
- **PostgreSQL**: `tests/postgres/conftest.py` — opt-in via `PG_TEST_*` env vars, auto-skip if unavailable, `TRUNCATE ... RESTART IDENTITY CASCADE` between tests
- **E2E**: `tests/e2e/conftest.py` — swaps mocked MCP for real SDK, requires live daemon + LLM

### New test file
`tests/job_queue/test_seam_invariants.py` — uses the existing SQLite conftest. Tests:
1. `test_defer_job_task_is_deferred_true` — Task gets `is_deferred=true` for defer queue
2. `test_message_id_stamped_on_jobitem` — metadata.message_id matches Task.message_id
3. `test_null_message_id_guard_no_deadlock` — cross-system guard handles NULL gracefully
4. `test_defer_job_not_admitted_during_virtual_work` — P2 invariant
5. `test_defer_job_completes_after_idle` — P1 invariant (not stuck processing)
6. `test_is_idle_false_during_active_work` — F2 invariant
7. `test_cancel_queued_sibling_no_lock_release` — F4/F7 invariant
8. `test_reconciler_catches_p1_deadlock` — F5 (active JobItem + pending Task)
9. `test_reconciler_force_completes_zombie_task` — F10 (done JobItem + running Task)
10. `test_watcher_survives_retry_migration` — F6 (exact-match watcher migration)
11. `test_stale_pending_task_cancelled_before_readmission` — F12 (ordering)
12. `test_observer_path_respects_defer_idle_gate` — F8

### Existing defer tests to run (must not break)
- `tests/job_queue/test_defer_queue.py` (779 lines, 19 tests)
- `tests/job_queue/test_defer_deadlock.py` (392 lines, 8 tests)
- `tests/job_queue/test_deferred_finalize_check.py` (293 lines, 4 tests)

### Test command
```bash
# Default SQLite suite
.venv/bin/pytest tests/job_queue/test_seam_invariants.py -v

# PostgreSQL suite
.venv/bin/pytest tests/postgres/ --override-ini="addopts=" -m postgres -v

# Full regression
.venv/bin/pytest tests/ -x
```

## Model Notes

### Task model fields (`daemon/repositories/task/models.py`)
| Field | Type | Notes |
|-------|------|-------|
| `work_id` | str | UUID4, `unique=True, index=True`, `default_factory=lambda: str(uuid.uuid4())` |
| `message_id` | str \| None | indexed |
| `is_deferred` | bool | indexed, `server_default=text("false")` — Phase 3 Part B1 |
| `task_type` | str | `PROCESS_MESSAGE`, `PROCESS_REPORT`, `SEND_REPORT`, `CLEANUP` |
| `status` | str | `PENDING`, `RUNNING`, `PAUSED`, `COMPLETED`, `FAILED`, `CANCELLED` |
| `last_heartbeat_at` | datetime \| None | indexed — Phase 3 liveness signal |

### PostgreSQL lock-guard triggers (`daemon/manager.py:2135-2140`)
- `trg_job_queue_items_active_lock_guard` — forward guard: every active JobItem must have a JobLock row
- `trg_job_locks_active_guard` — reverse guard: every JobLock row must point at an active JobItem
- Both are `DEFERRABLE INITIALLY DEFERRED` — allows acquire-then-set-active ordering within a transaction
- Any code that sets `admission_state='active'` without a `job_locks` insert will trigger `integrity_constraint_violation` on PostgreSQL
