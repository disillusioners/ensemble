# Phase 4: Bad State Visibility + Enhanced Cleanup

## Objective

Make the "bad state" condition — a `Task` in `paused`/`pending` whose linked `JobItem` is already terminal (`done`/`dead`) — observable and fixable through the existing System Cleanup flow. The plan adds (1) backend per-queue bad-state counts, (2) a fourth cleanup bucket that batch-reconciles bad-state Tasks, (3) a frontend queue badge using `$accent-rose`, and (4) an animated red-glow + tooltip on the System Cleanup button whenever bad-state rows exist anywhere in the system. The cleanup endpoint's existing `validate_total_processed` invariant is preserved by treating the new bucket the same way `orphaned_reaped` is treated (excluded from `total_processed`).

The bad-state definition used here is **identical** to Phase 1 (`reconcile_terminal_task`) and Phase 2 (NOT EXISTS defensive predicate): `task.status IN ('paused','pending')` AND `job_queue_items.admission_state IN ('done','dead')` AND `job_queue_items.deleted_at IS NULL`. No JobItem row is mutated — only the orphan Task rows are reconciled, because the JobItem is already terminal.

---

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | Add `TaskRepository.count_bad_state_tasks(self, queue_id: str \| None = None, project_id: str \| None = None) -> int` in `daemon/repositories/task/repository.py` (add near the existing idle-gate predicates, line 1955–2125) | none | Single SQL query returns the count; per-queue variant adds `AND jqi.queue_id = :queue_id`; dual-driver (SQLite + PostgreSQL); reuses the same `WHERE EXISTS` shape as the Phase 2 NOT EXISTS pattern |
| 2 | Add `TaskRepository.batch_reconcile_bad_state_tasks(self, queue_id: str \| None = None, project_id: str \| None = None) -> int` in `daemon/repositories/task/repository.py` (adjacent to `reconcile_terminal_task` from Phase 1) | none | Single UPDATE statement transitions bad-state tasks to `CANCELLED` with `updated_at = CURRENT_TIMESTAMP`; returns rowcount; same `WHERE status IN ('paused','pending')` guard as Phase 1's `reconcile_terminal_task`; idempotent |
| 3 | Enhance `JobQueueMgmtService.get_queue_with_counts` (`daemon/services/job_queue_mgmt_service.py:260-290`) to call `count_bad_state_tasks(queue_id=queue.queue_id)` and add `bad_state_jobs` to the returned dict | Task 1 | Return dict now carries `bad_state_jobs: int`; same `asyncio.to_thread` threading used by the existing `count_jobs_by_status` call |
| 4 | Enhance `JobQueueMgmtService.list_queues` (`daemon/services/job_queue_mgmt_service.py:314-341`) to populate `bad_state_jobs` per queue using the same per-queue call | Task 3 | The returned list of dicts each carry `bad_state_jobs`; same loop shape preserved (acceptable single extra query per queue per the conventions note) |
| 5 | Add a new `count_bad_state_tasks` overload (or wrapper) for the **system-wide** counter used by the frontend `(badStateCount)` signal on the Jobs page: `count_bad_state_tasks(project_id=None)` with no queue filter | Task 1 | Returns total across all queues/projects; single SQL call (no fan-out) |
| 6 | Expose a system-wide bad-state count to the jobs page via a new lightweight endpoint OR reuse the existing list endpoint aggregation — **decision: add a single-featured endpoint `/api/jobs/cleanup/preflight`** (see Detailed Implementation Guidance § 6) | Task 5 | Endpoint returns `{bad_state_count: int}`. **Reviewer correction W1 (2026-08-11): NO `is_write_paused` guard** — this is a read-only COUNT query that MUST work even during write pause (database migration), because that's precisely when bad-state items are most likely to accumulate (writes are paused, so reconciliation can't run, but the preflight must still surface the stale rows). Used by the frontend to render the red-glow + tooltip. |
| 7 | Update `JobQueueResponse` Pydantic schema in `daemon/routers/schemas.py:272-306`: add `bad_state_jobs: int = Field(default=0, ge=0, description="Number of bad-state tasks (paused/pending) whose linked JobItem is terminal")` | Task 3 | Field present with default `0` so existing callers without the new count are unaffected; `model_config` example updated |
| 8 | Update `_queue_to_response` helper in `daemon/routers/queues.py:87-141` to map `bad_state_jobs` from the service dict (both the dict branch and the model branch) | Task 7 | Both code paths populate `bad_state_jobs`. **Reviewer correction W3 (2026-08-11): the model branch (`else:` block) currently hardcodes `active_jobs=0, pending_jobs=0, bad_state_jobs=0` defaults because the `_queue_to_response` helper is called from paths that pass a raw `JobQueue` model object (not the enriched dict from `get_queue_with_counts`).** Recommended fix: **(a) extend the model branch to query `count_bad_state_tasks(queue.queue_id)`** when building the response (consistent with how `active_jobs` and `pending_jobs` are populated by `get_queue_with_counts` on the dict path), so the badge is consistent across ALL router paths. The alternative (b) — leaving the model branch as `bad_state_jobs=0` and documenting which paths use which branch — is acceptable as a fallback but the badge would not appear on those paths. **Approach (a) is recommended.** |
| 9 | Enhance `JobQueueService.cleanup_non_terminal_jobs` (`daemon/services/job_queue_service.py:1137-1243`) with a fourth bucket: `reconciled_bad_state` calling `batch_reconcile_bad_state_tasks` | Task 2 | Return dict adds `reconciled_bad_state: int`; `total_processed` is **unchanged** (`orphaned_reaped` + `reconciled_bad_state` excluded); the new bucket is best-effort with the same `try/except Exception as exc: logger.warning(...)` pattern as `orphaned_reaped` |
| 10 | Update `JobCleanupResponse` Pydantic schema in `daemon/routers/schemas.py:182-259`: add `reconciled_bad_state: int = Field(default=0, ge=0, ...)`. Add to the docstring's counter breakdown. **Do NOT change `validate_total_processed`** — the new field is excluded from the invariant, mirroring `orphaned_reaped`. Update `model_config.json_schema_extra` example. | Task 9 | Field ships with default `0`; docstring explains exclusion; existing two-bucket invariant remains intact |
| 11 | Update `cleanup_jobs` router docstring in `daemon/routers/jobs_management.py:562-635` to document the fourth bucket (`bad-state tasks`); document the dual-driver invariant exclusion | Task 9 | Router docstring lists all four buckets; response example block updated to include `reconciled_bad_state` |
| 12 | Update `JobQueue` TS model in `frontend/src/app/models/job-queue.model.ts:5-18`: add `bad_state_jobs: number` | Task 7 | New field with default `0` in any initializer that constructs `JobQueue` |
| 13 | Update `JobCleanupResult` interface in `frontend/src/app/services/job.service.ts:17-22`: add `reconciled_bad_state?: number` | Task 10 | Optional field; matches backend's `default=0` shape |
| 14 | Add `.count-badge.bad-state` style in `frontend/src/app/components/queue-list/queue-list.component.scss:325-340`: use `rgba($accent-rose, 0.15)` background and `$accent-rose` color, mirroring the existing `.active`/`.pending` modifier shape | none | New CSS modifier follows the existing pattern; reuses the existing `$accent-rose` palette variable |
| 15 | Add the bad-state badge to the queue list template `frontend/src/app/components/queue-list/queue-list.component.html:137-146`: new `@if (queue.bad_state_jobs > 0)` block after the `pending` badge | Tasks 12, 14 | Badge label rendered as `{{ queue.bad_state_jobs }} bad-state`; tooltip via `matTooltip` on the badge explaining "Tasks whose linked JobItem is already terminal" |
| 16 | Add `@keyframes pulse-glow` + `.cleanup-btn.has-bad-state` class in `frontend/src/app/pages/jobs/jobs.component.scss` (near the existing `.spinning` rule at line 460-471): red `box-shadow` pulse animation using `$accent-rose` | none | Animation is visible only when the class is applied; respects `prefers-reduced-motion` (skip animation when set) |
| 17 | Add `hasBadState` computed signal in `frontend/src/app/pages/jobs/jobs.component.ts`: derived from a new `badStateCount` signal that is populated by polling/fetching the preflight endpoint (Task 6); apply the `.has-bad-state` class to the System Cleanup button via `[class.has-bad-state]`; update the `matTooltip` to `"⚠ N bad-state items detected. Click to fix."` when count > 0 | Tasks 6, 13, 16 | Tooltip changes when bad-state exists; pulse animation runs; button remains enabled and functional (no behavioural change to click handler) |
| 18 | Update `onSystemCleanup` snackbar in `frontend/src/app/pages/jobs/jobs.component.ts:942-983` to include `reconciled_bad_state` in the success message | Task 13 | Snackbar text: `"Cancelled N queued, M active, R orphaned, B bad-state"` (letter abbreviations chosen to match the codebase's existing log/payload style; full words preferred if the row gets crowded) |
| 19 | (Optional) Enhance `SystemCleanupConfirmDialogComponent` (`frontend/src/app/components/system-cleanup-confirm-dialog/system-cleanup-confirm-dialog.component.ts`): extend `SystemCleanupConfirmData` to carry `bad_state_count: number`; render an extra warning line in the dialog when `bad_state_count > 0` | Task 17 | Dialog shows `"⚠ N bad-state tasks will be reconciled."` above the existing copy; matches the established inline-confirmation pattern from `SwitchConfirmDialog` (`migration.component.ts`) |
| 20 | Add unit tests for `count_bad_state_tasks` and `batch_reconcile_bad_state_tasks` on both SQLite and PostgreSQL | Tasks 1-2 | Tests cover: per-queue variant, system-wide variant, project-scoped variant, idempotency (re-running returns 0), exclusion of `running`/`completed`/`failed`/`cancelled` tasks, exclusion of queued/active JobItems |
| 21 | Add integration test: `cleanup_jobs` endpoint returns `reconciled_bad_state` populated; `total_processed` invariant still holds | Task 9 | Request hits endpoint; assert `reconciled_bad_state` count matches expected rows; assert `total_processed == cancelled_queued + cancelled_active` |
| 22 | Add frontend unit test for the new badge rendering (queue-list component) and the `hasBadState` signal (jobs component) | Tasks 15, 17 | Tests cover: badge appears when `bad_state_jobs > 0`, tooltip text changes when `hasBadState()` is true, snackbar includes `reconciled_bad_state` |
| 23 | Update `daemon/services/job_queue_service.py:cleanup_non_terminal_jobs` log line to include `reconciled_bad_state`: `logger.info("cleanup_non_terminal_jobs: cancelled_queued=%d cancelled_active=%d orphaned_reaped=%d reconciled_bad_state=%d total=%d", ...)` | Task 9 | Log line surfaced at INFO with all four buckets + total |

---

## Coupling

- **Tight with:** Phase 1 (Reconciliation Code Fix). Phase 4's `batch_reconcile_bad_state_tasks` is the batch sibling of Phase 1's single-task `reconcile_terminal_task`. Both share the same `WHERE status IN ('paused','pending')` guard and the same `CANCELLED` target. The schema for the new repository method should mirror Phase 1's single-side method signatures (return `int` rowcount, log via `logger.info`).
- **Tight with:** Phase 2 (Defensive Idle-Gate). The bad-state condition is the same predicate Phase 2's NOT EXISTS subquery excludes. Phase 4 REVERSES the join direction: instead of `task LEFT JOIN job_queue_items` to exclude, Phase 4 uses `task JOIN job_queue_items` to count and reconcile. The two endpoints of the same truth.
- **Loose with:** Phase 3 (Data Migration). Phase 3 ships a one-shot UPDATE migration; Phase 4 ships runtime code that handles the same condition via the API. They target the same end-state but at different layers (DDL vs API). If Phase 4 ships first, Phase 3 is largely redundant; if Phase 3 ships first, Phase 4 is the durable runtime guarantee.
- **Tight with:** `JobCleanupResponse` schema. The `validate_total_processed` invariant pins `total_processed == cancelled_queued + cancelled_active`. Adding `reconciled_bad_state` MUST follow the `orphaned_reaped` pattern (excluded) — changing the invariant would break the existing contract for callers that depend on it (the frontend snackbar `total_processed` summary, monitoring/alerting, etc.).
- **Loose with:** `JobQueueMgmtService` vs `JobQueueService` service split. Bad-state COUNT belongs on `JobQueueMgmtService` (queue CRUD/path); bad-state CLEANUP belongs on `JobQueueService` (job lifecycle). This matches the existing convention noted in the task context.
- **Independent of:** `JobQueue`/`Task` model definitions. No new fields, no new tables, no new migrations.

---

## Detailed Implementation Guidance

### Task 1: `count_bad_state_tasks`

File: `daemon/repositories/task/repository.py` (add near line 2125, alongside the existing idle-gate predicates)

```python
def count_bad_state_tasks(
    self,
    queue_id: str | None = None,
    project_id: str | None = None,
) -> int:
    """Count tasks whose linked JobItem is already terminal (bad-state).

    SYNC method (reviewer correction C1/W2, 2026-08-11):
        ``TaskRepository.__init__(engine: Engine)`` — all repository
        methods use ``with self.engine.begin() as conn:`` (sync).
        The service-layer caller in :meth:`JobQueueMgmtService.get_queue_with_counts`
        (Task 3) wraps this in ``asyncio.to_thread`` to bridge
        sync → async. Matches the rest of the repository's style
        (``has_active_non_deferred_work``, ``claim_pending_task``, etc.).

    Defines a "bad-state" task as:

      * task.status IN ('paused', 'pending')
      * linked job_queue_items.admission_state IN ('done', 'dead')
      * job_queue_items.deleted_at IS NULL

    This is the same predicate as Phase 1's `reconcile_terminal_task`
    and Phase 2's NOT EXISTS idle-gate exclusion — Phase 4 makes the
    count queryable. Per docs/plans/task-job-reconciliation/phase4-plan.md.

    Args:
        queue_id: Optional queue filter. None = system-wide.
        project_id: Optional project filter. None = system-wide.
            When project_id is set, the query joins ``instances`` so
            only tasks whose instance belongs to that project count
            (consistent with the existing idle-gate predicates).

    Returns:
        Integer count of bad-state tasks matching the filters.

    Dialect notes:
        * Uses ANSI ``WHERE EXISTS`` so the same SQL works on both
          SQLite and PostgreSQL (matches the Phase 2 pattern).
        * The ``JOIN`` on ``job_queue_items`` is INNER, so a task with
          no matching JobItem row is excluded (its status is not bad-state
          — it is direct-queue work, which is fine).
        * When neither queue_id nor project_id is provided, the query
          becomes a single table scan with a JOIN — acceptable for the
          cleanup-button preflight call (called once per user interaction).
    """
    with self.engine.begin() as conn:
        params: dict[str, Any] = {
            "status_paused": TaskStatus.PAUSED.value,
            "status_pending": TaskStatus.PENDING.value,
            "admission_done": AdmissionState.DONE.value,
            "admission_dead": AdmissionState.DEAD.value,
        }
        sql = """
            SELECT COUNT(*) AS bad_state_count
            FROM task t
            JOIN job_queue_items jqi ON jqi.job_id = t.work_id
            WHERE t.status IN (:status_paused, :status_pending)
              AND jqi.admission_state IN (:admission_done, :admission_dead)
              AND jqi.deleted_at IS NULL
        """
        if queue_id is not None:
            sql += " AND jqi.queue_id = :queue_id"
            params["queue_id"] = queue_id
        if project_id is not None:
            sql += " AND EXISTS (SELECT 1 FROM instances i WHERE i.instance_id = t.instance_id AND i.project_id = :project_id)"
            params["project_id"] = project_id
        row = conn.execute(text(sql), params).fetchone()
        return int(row[0])
```

**Why ANSI `WHERE EXISTS` (not LEFT JOIN with NULL check)?** Matches the precedent in `JobRepository.has_active_non_background_work:801-820` (FIX 2B 2026-08-10) and the Phase 2 NOT EXISTS predicates. The codebase convention is `.text("""...""")` with named parameters — follow it.

### Task 2: `batch_reconcile_bad_state_tasks`

File: `daemon/repositories/task/repository.py` (add adjacent to `reconcile_terminal_task` from Phase 1)

```python
def batch_reconcile_bad_state_tasks(
    self,
    queue_id: str | None = None,
    project_id: str | None = None,
) -> int:
    """Batch-transition every bad-state task to 'cancelled'.

    Per docs/plans/task-job-reconciliation/phase4-plan.md. Sister of
    `reconcile_terminal_task` (Phase 1) — same guard, same target,
    batch shape. The cleanup endpoint's fourth bucket.

    Idempotent: the WHERE clause only matches bad-state rows, so
    re-running the UPDATE after a successful sweep returns 0.

    SYNC method (reviewer correction C1/W2, 2026-08-11):
        `TaskRepository.__init__(engine: Engine)` — all repository
        methods use `with self.engine.begin() as conn:` (sync). The
        original draft used `async def` + `await self.session.execute(stmt)`
        which would crash because `TaskRepository` has no `session`
        attribute. The service-layer caller in
        `cleanup_non_terminal_jobs` (Task 9) wraps this call in
        `asyncio.to_thread` to bridge sync → async.

    Args:
        queue_id: Optional queue filter. None = system-wide.
        project_id: Optional project filter. None = system-wide.

    Returns:
        Rowcount of the UPDATE statement (0 in the steady state).
    """
    with self.engine.begin() as conn:
        params: dict[str, Any] = {
            "status_paused": TaskStatus.PAUSED.value,
            "status_pending": TaskStatus.PENDING.value,
            "status_cancelled": TaskStatus.CANCELLED.value,
            "admission_done": AdmissionState.DONE.value,
            "admission_dead": AdmissionState.DEAD.value,
        }
        sql = """
            UPDATE task
            SET status = :status_cancelled,
                updated_at = CURRENT_TIMESTAMP
            WHERE status IN (:status_paused, :status_pending)
              AND EXISTS (
                  SELECT 1 FROM job_queue_items jqi
                  WHERE jqi.job_id = task.work_id
                    AND jqi.admission_state IN (:admission_done, :admission_dead)
                    AND jqi.deleted_at IS NULL
              )
        """
        if queue_id is not None:
            sql += " AND EXISTS (SELECT 1 FROM job_queue_items jqi2 WHERE jqi2.job_id = task.work_id AND jqi2.queue_id = :queue_id)"
            params["queue_id"] = queue_id
        if project_id is not None:
            sql += " AND EXISTS (SELECT 1 FROM instances i WHERE i.instance_id = task.instance_id AND i.project_id = :project_id)"
            params["project_id"] = project_id

        result = conn.execute(text(sql), params)
        count = result.rowcount
        if count > 0:
            logger.info(
                "task.reconciled_bad_state_batch",
                count=count,
                queue_id=queue_id,
                project_id=project_id,
            )
        return count
```

**Why raw-SQL (not SQLAlchemy ORM `update()` with `exists()`)?** Reviewer correction C1/W2 (2026-08-11) — the original draft offered two alternatives: (1) `async def` + `await self.session.execute(stmt)` using `update(TaskModel).where(exists()...)`, and (2) raw `text("UPDATE ...")` using `with self.engine.begin() as conn:`. The first alternative **does not compile** against this repository: `TaskRepository.__init__` takes `engine: Engine`, not a session, and the rest of the file uses the sync `with self.engine.begin() as conn:` pattern. The raw-SQL form matches (a) the existing repository style, (b) the dual-driver validation already performed on similar queries in Phases 2-3, and (c) the sync/async bridge via `asyncio.to_thread` in the service caller. **Use the raw-SQL form** shown above.
2. **Raw `text("UPDATE task ... WHERE EXISTS (...)")`** — matches the rest of the repository's pattern (see `has_active_non_deferred_work` at line 2045). **Recommendation: use raw SQL** for consistency with the existing repository style and the dual-driver validation already performed on similar queries in Phases 2-3.

### Task 3: Enhance `get_queue_with_counts`

File: `daemon/services/job_queue_mgmt_service.py:260-290`

```python
async def get_queue_with_counts(
    self,
    project_id: str,
    queue_id: str,
) -> dict[str, Any | None]:
    """Get a queue by ID with actual job counts, including bad-state tasks."""
    queue = await asyncio.to_thread(self._queue_repo.get, queue_id)
    if queue is None or queue.project_id != project_id:
        return None

    counts = await asyncio.to_thread(
        self._queue_repo.count_jobs_by_status,
        queue.queue_id,
    )

    # NEW: separate query for bad-state tasks (requires task↔job_queue_items JOIN).
    bad_state = await asyncio.to_thread(
        self._task_repo.count_bad_state_tasks,
        queue_id=queue.queue_id,
    )

    queue_dict = queue.to_dict()
    queue_dict["active_jobs"] = counts.get(AdmissionState.ACTIVE.value, 0)
    queue_dict["pending_jobs"] = counts.get(AdmissionState.QUEUED.value, 0)
    queue_dict["bad_state_jobs"] = bad_state  # NEW

    return queue_dict
```

**Service injection note:** `JobQueueMgmtService` currently only has `_queue_repo`. The Phase 4 Tasks 3-5 add a `_task_repo: TaskRepository` constructor dependency. Follow the existing pattern in `JobQueueService` (which presumably already has `TaskRepository` for `reconcile_terminal_task` from Phase 1). If `JobQueueMgmtService` is constructed elsewhere, add the dependency there.

### Task 4: Enhance `list_queues`

File: `daemon/services/job_queue_mgmt_service.py:314-341`

```python
async def list_queues(self, project_id: str) -> list[dict[str, Any]]:
    """List all queues for a project with job counts, including bad-state tasks."""
    queues = await asyncio.to_thread(
        self._queue_repo.list_by_project,
        project_id,
    )

    result = []
    for queue in queues:
        counts = await asyncio.to_thread(
            self._queue_repo.count_jobs_by_status,
            queue.queue_id,
        )
        bad_state = await asyncio.to_thread(
            self._task_repo.count_bad_state_tasks,
            queue_id=queue.queue_id,
        )

        queue_dict = queue.to_dict()
        queue_dict["active_jobs"] = counts.get(AdmissionState.ACTIVE.value, 0)
        queue_dict["pending_jobs"] = counts.get(AdmissionState.QUEUED.value, 0)
        queue_dict["bad_state_jobs"] = bad_state
        result.append(queue_dict)

    return result
```

**Performance note:** Per the conventions note, this adds one extra query per queue per `list_queues` call. For typical projects (a few queues) this is negligible. If a future hot-path optimization is needed, add a bulk method that joins `task` once for all queues in the project — but that is out of scope for Phase 4.

### Task 5: System-wide `count_bad_state_tasks`

Same repository method as Task 1, called with `queue_id=None, project_id=None`:

```python
system_bad_state = await asyncio.to_thread(
    self._task_repo.count_bad_state_tasks
)
```

### Task 6: Preflight endpoint for the Jobs page

**Decision:** Add a new lightweight endpoint `GET /api/jobs/cleanup/preflight` that returns the system-wide bad-state count. The frontend polls this endpoint (or refreshes it on `onRefresh()` + before `onSystemCleanup()`) to drive the `hasBadState` signal.

**Why a new endpoint and not extending the existing list endpoint?**
- Polling frequency is decoupled from queue list refresh.
- The payload is `{bad_state_count: int}` — minimal.
- The cleanup-button UX only needs the *presence* of bad-state rows, not the per-queue breakdown.

File: `daemon/routers/jobs_management.py` (add near the existing `cleanup_jobs` endpoint at line 562)

```python
@router.get(
    "/cleanup/preflight",
    response_model=CleanupPreflightResponse,
    responses={
        200: {"description": "Bad-state count for the System Cleanup button UX"},
    },
)
async def cleanup_jobs_preflight(
    request: Request,
    service: JobQueueService = Depends(get_job_queue_service),
) -> CleanupPreflightResponse:
    """Lightweight preflight for the System Cleanup button.

    Returns the system-wide bad-state task count used by the
    frontend to drive the red-glow + tooltip. The preflight is
    strictly read-only — it does NOT mutate state.

    Per docs/plans/task-job-reconciliation/phase4-plan.md.

    Reviewer correction W1 (2026-08-11): this endpoint intentionally
    has NO ``is_write_paused`` guard. It is a read-only COUNT query,
    and it must work even during write pause (database migration)
    because that is precisely when bad-state items are most likely
    to accumulate (writes are paused, so reconciliation cannot run,
    but the preflight must still surface the stale rows so the
    operator can see them via the red-glow + tooltip and decide
    whether to wait for writes to resume or take a different action).
    The endpoint never calls a mutating service, so the lack of a
    guard is safe — it cannot race with the migration.
    """
    manager = _get_manager(request)
    # ``manager._task_repo`` is the singleton TaskRepository constructed
    # in ``daemon/manager.py:4979-4986`` (exposed on the manager so
    # cross-dispatcher handlers can read the repo without reaching into
    # a private local). Wrapping the sync call in ``asyncio.to_thread``
    # matches the rest of the service-layer callers (see Task 3).
    task_repo = manager._task_repo
    count = await asyncio.to_thread(task_repo.count_bad_state_tasks)
    return CleanupPreflightResponse(bad_state_count=count)
```

And add `CleanupPreflightResponse` to `daemon/routers/schemas.py`:

```python
class CleanupPreflightResponse(BaseModel):
    """Response for GET /api/jobs/cleanup/preflight."""
    bad_state_count: int = Field(
        default=0,
        ge=0,
        description="Number of bad-state tasks (paused/pending) whose linked JobItem is terminal",
    )
```

**Alternative:** If the team prefers to avoid a new endpoint, the FE can derive `bad_state_count` from the sum of `bad_state_jobs` across all queues in `list_queues`. This is acceptable as a fallback but loses the per-click freshness guarantee (the queue list is cached on the page).

### Task 7: Update `JobQueueResponse` schema

File: `daemon/routers/schemas.py:272-306`

```python
class JobQueueResponse(BaseModel):
    """Response for a single job queue."""

    queue_id: str = Field(..., description="Unique queue identifier")
    project_id: str = Field(..., description="Project ID this queue belongs to")
    queue_name: str = Field(..., description="Queue name")
    queue_type: str = Field(..., description="Queue type: 'fifo' or 'parallel'")
    concurrency_limit: int = Field(..., description="Maximum concurrent jobs")
    is_system: bool = Field(..., description="Whether this is a system queue")
    is_paused: bool = Field(..., description="Whether the queue is paused")
    description: str | None = Field(default=None, description="Queue description")
    created_at: str = Field(..., description="Queue creation timestamp")
    updated_at: str = Field(..., description="Queue last update timestamp")
    active_jobs: int = Field(default=0, description="Number of currently active jobs")
    pending_jobs: int = Field(default=0, description="Number of pending jobs")
    bad_state_jobs: int = Field(
        default=0,
        ge=0,
        description=(
            "Number of bad-state tasks (paused/pending) whose linked JobItem "
            "is terminal (done/dead). Reconciled by the System Cleanup button."
        ),
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "queue_id": "queue-uuid",
                "project_id": "project-uuid",
                "queue_name": "default",
                "queue_type": "fifo",
                "concurrency_limit": 1,
                "is_system": False,
                "is_paused": False,
                "description": None,
                "created_at": "2026-08-11T05:30:27.309654Z",
                "updated_at": "2026-08-11T05:30:27.309654Z",
                "active_jobs": 5,
                "pending_jobs": 12,
                "bad_state_jobs": 2,  # NEW
            }
        }
    }
```

### Task 8: Update `_queue_to_response`

File: `daemon/routers/queues.py:87-141`

```python
def _queue_to_response(queue_data) -> JobQueueResponse:
    """Convert queue data to JobQueueResponse."""
    if isinstance(queue_data, dict):
        return JobQueueResponse(
            queue_id=queue_data["queue_id"],
            project_id=queue_data["project_id"],
            queue_name=queue_data["queue_name"],
            queue_type=queue_data["queue_type"],
            concurrency_limit=queue_data["concurrency_limit"],
            is_system=queue_data["is_system"],
            is_paused=queue_data["is_paused"],
            description=queue_data.get("description"),
            created_at=queue_data["created_at"],
            updated_at=queue_data["updated_at"],
            active_jobs=queue_data.get("active_jobs", 0),
            pending_jobs=queue_data.get("pending_jobs", 0),
            bad_state_jobs=queue_data.get("bad_state_jobs", 0),  # NEW
        )
    else:
        # JobQueue model object — path used by router endpoints that do
        # NOT go through get_queue_with_counts. Inventory of call sites
        # in `daemon/routers/queues.py:87-141`:
        #
        #   * line 159 (`list_queues`)            — uses the DICT branch
        #   * line 256 (`create_queue`)           — fresh queue, no jobs yet
        #   * line 302 (likely `get_queue`)       — MODEL branch
        #   * lines 367, 476, 526 (read paths)    — use the DICT branch via
        #                                            `get_queue_with_counts`
        #
        # The MODEL branch therefore matters for `create_queue` (where
        # `bad_state_jobs` is meaningless — the queue has no jobs) and
        # the read path at line 302 (where the badge would otherwise be
        # hidden).
        #
        # Reviewer correction W3 (2026-08-11): the model branch was
        # originally going to hardcode ``bad_state_jobs=0`` here. That
        # would mean the badge never shows on the read path at line 302
        # even when there are real bad-state rows. Recommended fix
        # (approach a — preferred): query the count here so the badge
        # is consistent across ALL router paths, matching how
        # ``active_jobs`` and ``pending_jobs`` are populated by
        # ``get_queue_with_counts`` on the dict path. The query is a
        # single COUNT with WHERE EXISTS on the (work_id, queue_id,
        # admission_state, deleted_at) index — cheap enough for the
        # single-queue endpoint.
        #
        # For `create_queue` (line 256), the count will always be 0
        # because the queue was just created with no JobItems — the
        # badge naturally won't render, but the code path is still
        # exercised. Approach (a) is unconditional.
        from daemon.repositories.task.repository import TaskRepository  # NEW (W3)
        manager = _get_manager(request)  # NEW (W3) — needs the request in scope
        bad_state = await asyncio.to_thread(
            TaskRepository(manager._engine).count_bad_state_tasks,  # NEW (W3)
            queue_id=queue_data.queue_id,  # NEW (W3)
        )
        return JobQueueResponse(
            queue_id=queue_data.queue_id,
            project_id=queue_data.project_id,
            queue_name=queue_data.queue_name,
            queue_type=queue_data.queue_type,
            concurrency_limit=queue_data.concurrency_limit,
            is_system=queue_data.is_system,
            is_paused=queue_data.is_paused,
            description=queue_data.description,
            created_at=queue_data.created_at,
            updated_at=queue_data.updated_at,
            active_jobs=0,  # kept as 0 — single-queue path doesn't compute
            pending_jobs=0,  # kept as 0 — single-queue path doesn't compute
            bad_state_jobs=bad_state,  # NEW (W3) — was hardcoded 0 before
        )
```

**W3 follow-up tasks (Phase 4 Task 8 extension):**

1. **Inventory which router paths use the model branch vs the dict branch.** Currently identified: `GET /api/queues/{queue_id}` (per-queue endpoint) uses the model branch. `GET /api/queues` (list endpoint) uses the dict branch via `list_queues`. Any new router path that constructs a `JobQueue` model object directly must also use the model-branch enrichment shown above, OR explicitly opt out of the badge and document that the badge is hidden on that path.

2. **Alternative (b) — fallback if the developer prefers to avoid the per-call count:** Keep the model branch returning `bad_state_jobs=0` and document in the docstring that the badge only appears on the dict branch (i.e. on the queue list). This is acceptable as a v1 trade-off but the operator experience is degraded on the per-queue endpoint. **Approach (a) above is recommended.**

### Task 9: Fourth bucket in `cleanup_non_terminal_jobs`

File: `daemon/services/job_queue_service.py:1137-1243`

Insert after the existing `orphaned_reaped` block (line 1228), before the `total = cancelled_queued + cancelled_active` line at 1234:

```python
# 4) Bad-state reconciler — Tasks whose linked JobItem is already
# terminal but the Task itself is stuck in 'paused'/'pending'. These
# block idle-gates but are invisible to the queue counters (the
# JobItem is terminal). Per docs/plans/task-job-reconciliation/phase4-plan.md.
# Sister of Phase 1's reconcile_terminal_task (single-task) and
# Phase 3's data migration (one-shot). Excluded from total_processed
# to preserve the existing two-bucket invariant contract.
reconciled_bad_state = 0
try:
    # Reviewer correction C1/W2 (2026-08-11): `batch_reconcile_bad_state_tasks`
    # is a SYNC method (uses `with self.engine.begin() as conn:` —
    # `TaskRepository` does not have a `session` attribute, only an
    # `engine`). The service-layer caller wraps it in `asyncio.to_thread`
    # to bridge sync → async. `self._engine` is the canonical engine
    # stored on the service (same engine used by `_finalize_job_db_sync`
    # and the other sync methods in this module).
    task_repo = TaskRepository(engine=self._engine)
    reconciled_bad_state = await asyncio.to_thread(
        task_repo.batch_reconcile_bad_state_tasks
    )
    if reconciled_bad_state > 0:
        logger.info(
            "cleanup_non_terminal_jobs: reconciled_bad_state=%d",
            reconciled_bad_state,
        )
except Exception as exc:  # noqa: BLE001 — best-effort cleanup
    logger.warning(
        "cleanup_non_terminal_jobs: batch_reconcile_bad_state_tasks failed: %s",
        exc,
    )

total = cancelled_queued + cancelled_active  # UNCHANGED — invariant preserved
logger.info(
    "cleanup_non_terminal_jobs: cancelled_queued=%d cancelled_active=%d "
    "orphaned_reaped=%d reconciled_bad_state=%d total=%d",
    cancelled_queued, cancelled_active, orphaned_reaped,
    reconciled_bad_state, total,
)
return {
    "cancelled_queued": cancelled_queued,
    "cancelled_active": cancelled_active,
    "orphaned_reaped": orphaned_reaped,
    "reconciled_bad_state": reconciled_bad_state,  # NEW
    "total_processed": total,
}
```

### Task 10: Update `JobCleanupResponse` schema

File: `daemon/routers/schemas.py:182-259`

Add the new field to the existing class. **Do NOT modify `validate_total_processed`** — the new bucket is excluded from the invariant, mirroring `orphaned_reaped`.

```python
class JobCleanupResponse(BaseModel):
    """Response for the ``POST /api/jobs/cleanup`` "system reset" endpoint.

    Counter breakdown:
      * ``cancelled_queued`` — number of PENDING (queued) jobs that
        were batch-updated to ``admission_state='done'`` /
        ``terminal_reason='cancelled'`` in a single SQL UPDATE.
      * ``cancelled_active`` — number of PROCESSING (active) jobs
        whose per-row ``cancel_job`` cascade returned ``True`` (lock
        released + instance terminated).
      * ``orphaned_reaped`` — number of *ghost* active jobs whose
        underlying instance is already terminal (or missing), so the
        cancel cascade above has nothing to terminate. These jobs
        slipped through the natural finalize path (e.g. observer
        feedback dropped because the worker process died mid-ack) and
        had to be force-finalized via the orphan reaper. Excluded from
        ``total_processed`` so the contract for the existing two
        counters is preserved.
      * ``reconciled_bad_state`` — number of Tasks whose linked JobItem
        is already terminal (``done``/``dead``) but the Task itself is
        stuck in ``paused``/``pending``. These invisible orphans block
        idle-gates; the cleanup transition moves them to ``cancelled``
        via the single batch UPDATE. Excluded from ``total_processed``
        for the same reason as ``orphaned_reaped`` — the invariant
        contract remains a two-bucket sum. Per
        docs/plans/task-job-reconciliation/phase4-plan.md.
      * ``total_processed`` — sum of ``cancelled_queued`` +
        ``cancelled_active``.
    """

    cancelled_queued: int = Field(
        ...,
        ge=0,
        description="Number of queued (PENDING) jobs that were cancelled",
    )
    cancelled_active: int = Field(
        ...,
        ge=0,
        description=(
            "Number of active (PROCESSING) jobs whose cancel cascade completed"
        ),
    )
    orphaned_reaped: int = Field(
        default=0,
        ge=0,
        description=(
            "Number of orphan active jobs (instance terminal or missing) "
            "that were force-finalized to clear the ghost active counter"
        ),
    )
    reconciled_bad_state: int = Field(  # NEW
        default=0,
        ge=0,
        description=(
            "Number of bad-state tasks (paused/pending with terminal JobItem) "
            "reconciled to cancelled. Excluded from total_processed."
        ),
    )
    total_processed: int = Field(
        ...,
        ge=0,
        description="Sum of cancelled_queued + cancelled_active",
    )

    @model_validator(mode="after")
    def validate_total_processed(self) -> "JobCleanupResponse":
        """Enforce ``total_processed == cancelled_queued + cancelled_active``.

        The cleanup endpoint builds ``total_processed`` as the sum of the
        two per-bucket counts; pinning the invariant here means a future
        refactor of the service layer that drops a count (or double-
        counts a row) cannot silently produce a misleading
        ``total_processed`` in the response body.

        NOTE: ``orphaned_reaped`` and ``reconciled_bad_state`` are
        best-effort cleanup buckets whose row counts are NOT added to
        ``total_processed``. Their inclusion would break the existing
        two-bucket invariant and silently break callers that depend
        on the contract (e.g. the frontend cleanup snackbar).
        """
        if self.total_processed != self.cancelled_queued + self.cancelled_active:
            raise ValueError(
                f"total_processed ({self.total_processed}) must equal "
                f"cancelled_queued + cancelled_active "
                f"({self.cancelled_queued} + {self.cancelled_active} "
                f"= {self.cancelled_queued + self.cancelled_active})"
            )
        return self

    model_config = {
        "json_schema_extra": {
            "example": {
                "cancelled_queued": 12,
                "cancelled_active": 3,
                "orphaned_reaped": 1,
                "reconciled_bad_state": 2,  # NEW
                "total_processed": 15,
            }
        }
    }
```

### Task 11: Update `cleanup_jobs` router docstring

File: `daemon/routers/jobs_management.py:562-635`

Update the docstring's bucket list to include the fourth bucket:

```python
async def cleanup_jobs(...):
    """Cancel ALL non-terminal jobs ("system reset" for the job board).

    Splits the work into FOUR buckets so each side uses the right
    cancellation tool:

    * **queued (PENDING)** -- batch UPDATE ...
    * **active (PROCESSING)** -- iterate and call :meth:`cancel_job` ...
    * **orphan active** -- rows whose underlying ``instances`` row is
      already terminal ...
    * **bad-state tasks** -- Tasks whose linked JobItem is already
      terminal but the Task itself is stuck in ``paused``/``pending``.
      The batch UPDATE transitions them to ``cancelled`` so the
      idle-gate no longer blocks. Reported as ``reconciled_bad_state``
      (separate from the two main counters and ``orphaned_reaped``) so
      existing callers that only read ``total_processed`` see no
      behavioural change. Per docs/plans/task-job-reconciliation/phase4-plan.md.

    ...

    Returns:
        200 with :class:`JobCleanupResponse`:

        .. code-block:: json

            {"cancelled_queued": N, "cancelled_active": N,
             "orphaned_reaped": N, "reconciled_bad_state": N,
             "total_processed": N}
    """
```

### Task 12: Update `JobQueue` TS model

File: `frontend/src/app/models/job-queue.model.ts:5-18`

```typescript
export interface JobQueue {
  // ... existing fields ...
  active_jobs: number;      // line 16
  pending_jobs: number;     // line 17
  bad_state_jobs: number;   // NEW
}
```

### Task 13: Update `JobCleanupResult` interface

File: `frontend/src/app/services/job.service.ts:17-22`

```typescript
export interface JobCleanupResult {
  cancelled_queued: number;
  cancelled_active: number;
  orphaned_reaped?: number;
  reconciled_bad_state?: number;  // NEW
  total_processed: number;
}
```

Note: `total_processed` is unchanged on the API side — it still sums the two main buckets. The frontend uses `reconciled_bad_state` only to render the snackbar message.

### Task 14: Add `.count-badge.bad-state` style

File: `frontend/src/app/components/queue-list/queue-list.component.scss` (insert after line 339, after the existing `.pending` block)

```scss
.count-badge {
  font-size: 10px; font-weight: 500; padding: 2px 6px; border-radius: 4px;

  &.active { background: rgba($accent-emerald, 0.15); color: $accent-emerald; }
  &.pending { background: rgba($accent-amber, 0.15); color: $accent-amber; }

  &.bad-state {  // NEW
    background: rgba($accent-rose, 0.15);
    color: $accent-rose;
  }
}
```

### Task 15: Add the bad-state badge to the queue list template

File: `frontend/src/app/components/queue-list/queue-list.component.html` (modify the existing `.queue-counts` block at lines 137-148)

```html
<div class="queue-counts">
  @if (queue.active_jobs > 0) {
    <span class="count-badge active">
      {{ queue.active_jobs }} active
    </span>
  }
  @if (queue.pending_jobs > 0) {
    <span class="count-badge pending">
      {{ queue.pending_jobs }} pending
    </span>
  }
  @if (queue.bad_state_jobs > 0) {  <!-- NEW -->
    <span class="count-badge bad-state"
          matTooltip="Tasks whose linked JobItem is already terminal. Click System Cleanup to fix.">
      {{ queue.bad_state_jobs }} bad-state
    </span>
  }
</div>
```

### Task 16: Red-glow pulse animation

File: `frontend/src/app/pages/jobs/jobs.component.scss` (add near the existing `.spinning` animation at line 460-471)

```scss
@keyframes pulse-glow {
  0%, 100% {
    box-shadow: 0 0 0 0 rgba($accent-rose, 0.7);
  }
  50% {
    box-shadow: 0 0 0 8px rgba($accent-rose, 0);
  }
}

.cleanup-btn.has-bad-state {
  animation: pulse-glow 1.5s ease-in-out infinite;
  border-color: $accent-rose !important;
  color: $accent-rose !important;
}

@media (prefers-reduced-motion: reduce) {
  .cleanup-btn.has-bad-state { animation: none; }
}
```

### Task 17: Wire `hasBadState` signal + tooltip + class

File: `frontend/src/app/pages/jobs/jobs.component.ts`

Add a new signal at the top of the component class:

```typescript
readonly badStateCount = signal<number>(0);
readonly hasBadState = computed(() => this.badStateCount() > 0);

private async refreshBadStateCount(): Promise<void> {
  try {
    const result = await firstValueFrom(
      this.http.get<{ bad_state_count: number }>('/api/jobs/cleanup/preflight')
    );
    this.badStateCount.set(result.bad_state_count);
  } catch {
    // Fail silently — the badge is UX-only and the cleanup itself
    // does not depend on it. The next refresh will retry.
  }
}
```

Modify the System Cleanup button template at `jobs.component.html:71-80`:

```html
<button
  mat-stroked-button
  color="warn"
  (click)="onSystemCleanup()"
  [disabled]="cleanupInProgress()"
  class="cleanup-btn"
  [class.has-bad-state]="hasBadState()"
  [matTooltip]="hasBadState()
    ? '⚠ ' + badStateCount() + ' bad-state items detected. Click to fix.'
    : 'Cancel ALL pending and running jobs across ALL projects'">
  <mat-icon [class.spinning]="cleanupInProgress()">cleaning_services</mat-icon>
  System Cleanup
</button>
```

Call `this.refreshBadStateCount()` from:
- `ngOnInit` (initial paint)
- `onRefresh()` (when the user clicks Refresh)
- After `cleanupAllJobs()` resolves (post-cleanup state)

### Task 18: Update `onSystemCleanup` snackbar

File: `frontend/src/app/pages/jobs/jobs.component.ts:942-983`

```typescript
async onSystemCleanup(): Promise<void> {
  const dialogRef = this.dialog.open(SystemCleanupConfirmDialogComponent, {
    width: '400px',
    data: { bad_state_count: this.badStateCount() },  // NEW — see Task 19
  });
  const confirmed = await firstValueFrom(dialogRef.afterClosed());
  if (!confirmed) return;

  this.cleanupInProgress.set(true);
  try {
    const result = await firstValueFrom(this.jobService.cleanupAllJobs());
    const parts = [
      `cancelled ${result.cancelled_queued} queued`,
      `${result.cancelled_active} active`,
    ];
    if (result.orphaned_reaped) {
      parts.push(`${result.orphaned_reaped} orphaned`);
    }
    if (result.reconciled_bad_state) {  // NEW
      parts.push(`${result.reconciled_bad_state} bad-state`);
    }
    this.snackBar.open(`Cleanup complete: ${parts.join(', ')}`, 'Close', {
      duration: 5000,
    });
    this.refreshBadStateCount();  // refresh the count after cleanup
  } catch (err) {
    this.snackBar.open(`Cleanup failed: ${err}`, 'Close', { duration: 5000 });
  } finally {
    this.cleanupInProgress.set(false);
  }
}
```

### Task 19: Optional enhancement to confirm dialog

File: `frontend/src/app/components/system-cleanup-confirm-dialog/system-cleanup-confirm-dialog.component.ts`

```typescript
export interface SystemCleanupConfirmData {
  bad_state_count?: number;  // NEW
}

@Component({
  selector: 'app-system-cleanup-confirm-dialog',
  standalone: true,
  imports: [MatDialogModule, MatButtonModule],
  template: `
    <h2 mat-dialog-title>System Cleanup</h2>
    <div mat-dialog-content>
      <p>This will cancel ALL pending and running jobs across ALL projects. This action cannot be undone. Continue?</p>
      @if (data.bad_state_count && data.bad_state_count > 0) {
        <p class="bad-state-warning">
          ⚠ {{ data.bad_state_count }} bad-state tasks will be reconciled.
        </p>
      }
    </div>
    <div mat-dialog-actions align="end">
      <button mat-button (click)="dialogRef.close(false)">Cancel</button>
      <button mat-raised-button color="warn" (click)="dialogRef.close(true)">Cleanup</button>
    </div>
    <style>
      .bad-state-warning {
        color: #f43f5e; /* $accent-rose */
        margin-top: 12px;
        font-weight: 500;
      }
    </style>
  `,
})
```

### Task 20: Repository unit tests

File: `daemon/tests/repositories/task/test_repository_bad_state.py` (new file or extend existing)

```python
import pytest
from daemon.repositories.task.repository import TaskRepository
from daemon.repositories.task.models import TaskStatus
from daemon.repositories.job_queue.models import AdmissionState

@pytest.mark.parametrize("driver", ["sqlite", "postgres"])
async def test_count_bad_state_tasks_includes_stuck_only(driver, populated_db):
    """paused task + done JobItem counts; running/completed/failed tasks do not."""
    ...
    repo = TaskRepository(populated_db)
    # Seed: 2 paused tasks with done JobItems, 1 running task with done JobItem
    count = repo.count_bad_state_tasks()
    assert count == 2

@pytest.mark.parametrize("driver", ["sqlite", "postgres"])
async def test_count_bad_state_tasks_per_queue(driver, populated_db):
    """queue_id filter restricts the count to that queue only."""
    ...

@pytest.mark.parametrize("driver", ["sqlite", "postgres"])
async def test_batch_reconcile_bad_state_tasks_is_idempotent(driver, populated_db):
    """Second call returns 0."""
    repo = TaskRepository(populated_db)
    first = repo.batch_reconcile_bad_state_tasks()
    assert first > 0
    second = repo.batch_reconcile_bad_state_tasks()
    assert second == 0

@pytest.mark.parametrize("driver", ["sqlite", "postgres"])
async def test_batch_reconcile_bad_state_tasks_excludes_running(driver, populated_db):
    """running tasks must NOT be reconciled."""
    ...
```

### Task 21: Integration test for the cleanup endpoint

File: `daemon/tests/integration/test_cleanup_bad_state.py` (new file or extend existing)

```python
@pytest.mark.parametrize("driver", ["sqlite", "postgres"])
async def test_cleanup_jobs_includes_reconciled_bad_state(driver, http_client, populated_db):
    """POST /api/jobs/cleanup returns reconciled_bad_state populated; invariant preserved."""
    # Seed: 2 paused tasks with done JobItems
    response = await http_client.post("/api/jobs/cleanup")
    assert response.status_code == 200
    payload = response.json()
    assert payload["reconciled_bad_state"] == 2
    # Invariant: total_processed == cancelled_queued + cancelled_active
    assert payload["total_processed"] == payload["cancelled_queued"] + payload["cancelled_active"]
```

### Task 22: Frontend unit tests

File: `frontend/src/app/pages/jobs/jobs.component.spec.ts` (add)

```typescript
it('renders the bad-state badge when queue.bad_state_jobs > 0', () => {
  // Mount component with mock queue data
  component.queues.set([{ ...queueWithBadState, bad_state_jobs: 3 }]);
  fixture.detectChanges();
  expect(debugEl.query(By.css('.count-badge.bad-state')).nativeElement.textContent).toContain('3 bad-state');
});

it('applies the has-bad-state class and tooltip when hasBadState() is true', () => {
  component.badStateCount.set(5);
  fixture.detectChanges();
  const btn = debugEl.query(By.css('.cleanup-btn')).nativeElement;
  expect(btn.classList).toContain('has-bad-state');
  expect(btn.getAttribute('ng-reflect-message')).toContain('5 bad-state items');
});

it('includes reconciled_bad_state in the snackbar after cleanup', async () => {
  // Mock jobService.cleanupAllJobs to return { ..., reconciled_bad_state: 7 }
  // Trigger onSystemCleanup()
  // Assert snackBar.open was called with text containing '7 bad-state'
});
```

### Task 23: Backend log line

Already included in the Task 9 implementation guidance — the new `logger.info(...)` line covers this.

---

## Why the Bad-State Bucket Is Excluded from `total_processed`

The `validate_total_processed` pin (`total_processed == cancelled_queued + cancelled_active`) is a contract for the two primary cleanup buckets — the same two buckets that existed before Phase 4. Adding `reconciled_bad_state` to `total_processed` would:

1. **Break the existing two-bucket invariant.** A future refactor that changes bucket definitions would silently break the contract.
2. **Mislead the frontend's "total" display.** The current `total_processed` is the count of JobItems cancelled. The new bucket is about Tasks (not JobItems). Mixing them inflates the count.
3. **Diverge from the `orphaned_reaped` precedent.** The orphan reaper was added the same way (informative side-bucket, excluded from `total_processed`) — the bad-state reconciler follows the same pattern.

The frontend renders the four buckets separately in the snackbar (Task 18), so the operator gets a complete picture without conflating the counts.

---

## Why a New Endpoint Instead of Extending the Queue List

The `list_queues` endpoint returns per-queue counts; the System Cleanup button needs the **system-wide** total. Options:

| Option | Pros | Cons |
|--------|------|------|
| **A. New `/api/jobs/cleanup/preflight` endpoint** | Decoupled polling; payload minimal; clear contract | One more endpoint to maintain |
| **B. Sum `bad_state_jobs` across `list_queues` on the FE** | No new endpoint | Coupled to queue list refresh; inaccurate if the page is cached; stale after cleanup |
| **C. Extend `list_queues` to include a system-wide `bad_state_total` field** | One endpoint per refresh | Couples the queue-list payload to a global summary; breaks the per-project scoping of `list_queues` |

**Decision: Option A** — it is the cleanest decoupling and matches the existing pattern of dedicated preflight endpoints elsewhere in the system (e.g. write-pause checks).

---

## Rollback Plan

| Step | Action |
|------|--------|
| 1 | Revert the schema additions (`bad_state_jobs` field in `JobQueueResponse`, `reconciled_bad_state` field in `JobCleanupResponse`, `CleanupPreflightResponse`). All fields have `default=0` so reverting is non-breaking for older clients. |
| 2 | Revert the service-layer changes (`get_queue_with_counts`, `list_queues`, `cleanup_non_terminal_jobs` fourth bucket). The fourth bucket is best-effort — removal does not affect the existing two buckets. |
| 3 | Revert the frontend changes (badge, animation, snackbar text). The `[class.has-bad-state]` is a no-op when the new endpoint returns 404 or no count is provided. |
| 4 | Remove the new `count_bad_state_tasks` and `batch_reconcile_bad_state_tasks` repository methods. Phase 1's `reconcile_terminal_task` (single-task) is unaffected. |
| 5 | Revert the preflight endpoint or mark it as deprecated. |

**Net effect of rollback:** no data loss (the JSONB `reconciled_bad_state` is a response field, not persisted). The bad-state condition becomes invisible again, but Phases 1-3 still fix the underlying deadlock at finalization time and migration time.

**Per-phase feature flag (optional):** If a faster kill-switch is desired, gate the new CSS class and the preflight call behind a `BAD_STATE_VISIBILITY_ENABLED` env var (default `true`). The backend changes can stay unflagged because the new fields default to `0` (no behavioural change to existing callers).

---

## Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| 1 | `validate_total_processed` invariant accidentally extended to include `reconciled_bad_state`, breaking the two-bucket contract | High | Low | Explicit guidance in Task 10 to NOT modify the invariant; docstring pinned to the existing two-bucket language; CI test (Task 21) asserts `total_processed == cancelled_queued + cancelled_active` |
| 2 | `count_bad_state_tasks` per-queue query is slow on busy systems | Medium | Low | Single SQL with the existing `task.work_id` and `job_queue_items.job_id` indexes; the JOIN is filtered by `admission_state IN ('done','dead')` which is normally a small set; benchmark included in Task 6 if needed |
| 3 | `batch_reconcile_bad_state_tasks` UPDATE locks the `task` table for too long on PostgreSQL | Medium | Low | Same row-level lock semantics as Phase 3's migration; `task.status` is indexed; if a project has >100K stuck rows, batch in a follow-up |
| 4 | Frontend polls `/api/jobs/cleanup/preflight` too aggressively, creating DB load | Low | Medium | Endpoint is cheap (single COUNT with WHERE EXISTS); recommend throttling to once per page lifetime + on `onRefresh()` + post-cleanup; do not poll on a timer in v1 |
| 5 | The red-glow animation triggers accessibility complaints (motion-sensitivity) | Low | Medium | `@media (prefers-reduced-motion: reduce)` block disables the animation (Task 16); pulse-glow is subtle (8px box-shadow), not a full flicker |
| 6 | Operators mistake the "bad-state" badge for a fresh failure (alarm fatigue) | Medium | Low | Add a `matTooltip` on the badge (Task 15) explaining the meaning; the badge label is "bad-state" not "error" to avoid panic copy |
| 7 | `list_queues` extra query per queue inflates the list endpoint latency for large projects | Low | Low | Per the conventions note, this is acceptable; if a project has 50+ queues, switch to a bulk SELECT GROUP BY jqi.queue_id in a follow-up (out of scope here) |
| 8 | Phase 4 ships before Phase 3 — the system-wide count could return >0 for legacy bad-state rows from before Phase 3 ever ran | Low | High | Phase 4's reconciliation removes them via the cleanup button; the count drops to 0 after one click. Document this in the release notes. |
| 9 | `reconciled_bad_state` is included in the JSON response but the FE forgets to show it in the snackbar | Low | Low | Frontend test (Task 22) asserts snackbar text contains the count; backend test (Task 21) asserts the field is in the response payload |
| 10 | Preflight endpoint leaks data across projects (system-wide count exposes per-project bad-state) | Low | Low | The count is system-wide — by intent (the cleanup button operates system-wide). If a per-project preflight is needed later, add a `project_id` query param. |
| 11 | Cursor position races during cleanup: while cleanup runs, a new bad-state row forms (e.g. a new JobItem finalizes with a still-paused Task) | Low | Medium | Document the race but acknowledge it: the caller can click Cleanup again, or Phase 1's finalization-time reconciliation can fire on the next transition. No infinite-loop risk. |
| 12 | The pulse-glow animation interferes with the `:focus` outline of the button (keyboard accessibility) | Low | Low | The animation only modulates `box-shadow`; keep the default Material `outline` intact; verify with a keyboard tab test in code review |
| 13 | `batch_reconcile_bad_state_tasks` accidentally constructed as `async def` with `await self.session.execute(stmt)` (reviewer correction C1/W2, 2026-08-11) | High | Medium | Explicit guidance in Tasks 1, 2, 9 to use SYNC `with self.engine.begin() as conn:` and wrap service-layer callers in `asyncio.to_thread`. `TaskRepository.__init__(engine: Engine)` does not have a `session` attribute — the original ORM-async pattern would crash at runtime. The corrected plan removes the SQLAlchemy ORM `update()` example in favour of the raw-SQL variant, matching the rest of the repository. |
| 14 | Preflight endpoint (`GET /api/jobs/cleanup/preflight`) accidentally has an `is_write_paused` guard (reviewer correction W1, 2026-08-11) | Medium | High | Explicit guidance in Task 6 to NOT add a `is_write_paused` guard. The preflight is a read-only COUNT query — it must work even during write pause (database migration) because that is precisely when bad-state items are most likely to accumulate. The endpoint never calls a mutating service, so the lack of a guard is safe. The 503 response is removed from the OpenAPI schema; the pre-merge verification checklist asserts the endpoint returns 200 during write pause. |
| 15 | `_queue_to_response` model branch hardcodes `bad_state_jobs=0`, hiding the badge on the per-queue endpoint (reviewer correction W3, 2026-08-11) | Low | Medium | Recommended approach (a) in Task 8: extend the model branch to query `count_bad_state_tasks(queue.queue_id)` via `asyncio.to_thread` so the badge is consistent across all router paths. The alternative (b) — keeping the model branch at 0 and documenting which paths use which branch — is documented as a v1 trade-off but approach (a) is preferred. |

---

## Exit Criterion

All 23 tasks complete. Tests pass on both SQLite and PostgreSQL.

**Functional verification:**
- `GET /api/jobs/cleanup/preflight` returns `{bad_state_count: N}` accurately.
- Queue list shows the `.bad-state` badge whenever `bad_state_jobs > 0`.
- `POST /api/jobs/cleanup` returns `reconciled_bad_state` populated; `total_processed == cancelled_queued + cancelled_active` (invariant preserved).
- After cleanup, the badge count drops to 0 and the red-glow stops on the next refresh.
- The existing three-bucket cleanup behaviour is unchanged for callers that ignore `reconciled_bad_state`.

**Non-functional verification:**
- No new test failures on either DB backend.
- `prefers-reduced-motion: reduce` disables the animation.
- The badge is reachable via keyboard navigation; tooltip is announced via screen reader (verify with `aria-label` if needed).
- The preflight endpoint p95 < 50ms on a 10K-row `task` table.

**Code-review checklist:**
- [ ] All new schema fields have `default=0`.
- [ ] `validate_total_processed` is unchanged.
- [ ] `count_bad_state_tasks` is dual-driver (no banned operators).
- [ ] `batch_reconcile_bad_state_tasks` is idempotent.
- [ ] Bad-state definition matches Phase 1 (`paused`/`pending` + terminal JobItem).
- [ ] Frontend badge uses `$accent-rose` (consistent with the palette).
- [ ] Red-glow animation respects `prefers-reduced-motion`.
- [ ] Snackbar includes all four buckets.
- [ ] Both `list_queues` paths (dict + model) populate `bad_state_jobs` in `_queue_to_response` (Task 8) — W3 approach (a): model branch queries `count_bad_state_tasks` via `asyncio.to_thread` so the badge is consistent across all router paths.

**Pre-merge verification checklist:**
- [ ] `pytest` passes on SQLite.
- [ ] `pytest` passes on PostgreSQL.
- [ ] `npm test` (or `ng test`) passes for the jobs + queue-list components.
- [ ] Manual smoke test: create a stuck bad-state row, observe badge + glow, click System Cleanup, confirm badge disappears on refresh.
- [ ] Confirm `GET /api/jobs/cleanup/preflight` works correctly during write pause (no `is_write_paused` guard; returns 200 with the current count, not 503 — see Task 6 / W1).