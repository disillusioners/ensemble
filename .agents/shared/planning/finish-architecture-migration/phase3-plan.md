# Phase 3: D11 — Collapse job_processor MESSAGE Branch

## Objective

Remove the `if job_type == 'message':` branch from `job_processor.py`. After D13 (Phase 2), no MESSAGE-typed JobItems are ever created, so this branch is dead code. Also remove the dead `dispatch_path=jobqueue_local` log metric. This phase completes the dispatch-path unification.

## Coupling

- **Depends on**: Phase 2 (D13) + Phase 2.5 (consumption-site rewrites) — **tight coupling**
- **Coupling type**: tight — Phase 2 eliminates MESSAGE JobItem creation; Phase 2.5 rewrites all consumers; Phase 3 removes the branch that created them
- **Shared files with other phases**: `job_processor.py` (only Phase 3 touches this), `api.py` (dead log removal)
- **Shared APIs/interfaces**: the `observer._admit_via_worker_pool` method becomes dead for MESSAGE jobs
- **Why this coupling**: The branch at `job_processor.py:687` routes MESSAGE JobItems to `observer._admit_via_worker_pool`. Once D13 lands (Phase 2) + consumption sites are rewritten (Phase 2.5), no MESSAGE JobItems exist and all consumers work without them. Phase 3 removes the now-dead branch.

## Context

### Current MESSAGE Branch (lines 687-761)

```python
687:  if getattr(started_job, 'job_type', 'task') == "message":
688:      if self._job_feedback_observer is None:
            # ... error handling, mark FAILED ...
715:          continue
716:      try:
717:          await self._job_feedback_observer._admit_via_worker_pool(started_job)
            # ... error handling ...
761:      continue  # skip the TASK-only spawn + enqueue path below
762:  # <<< END MESSAGE dispatch >>>
```

After D13, no JobItem will have `job_type="message"`. The `getattr(started_job, 'job_type', 'task')` will always return `"task"` (or the job won't exist at all).

### TASK Path (lines 764-797)

```python
764:  # Spawn instance for this job
766:  instance_id = await self._instance_manager.spawn_instance_with_mcp(...)
779:  # Send the job message to the instance
781:  await self._instance_manager.enqueue_message(
782:      instance_id=instance_id,
783:      message=job.message,
784:      source=job.source,
785:  )  # default workerpool path
```

This path already works correctly for TASK jobs and will be the only remaining path after the branch removal.

### Dead Log Metric

`daemon/api.py:393` contains: `logger.info("JobFeedbackObserver wired into JobProcessor (dispatch_path=jobqueue_local)")`. The `dispatch_path=jobqueue_local` metric is dead — after D13+D11, no job uses that path. Remove the parenthetical or update to reflect the unified path.

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| **3.1** | Remove MESSAGE branch from `_process_next_job` | Delete lines 673-761 in `job_processor.py` (the `if job_type == 'message':` branch + surrounding doc-comments). The TASK path (lines 764+) becomes the only dispatch path. | `daemon/services/job_processor.py:673-761` |
| **3.2** | Clean up doc-comments | Remove the doc-comment block at lines 673-686 that describes the separate MESSAGE dispatch path. Update any surrounding comments to reflect the unified dispatch. | `daemon/services/job_processor.py:672-686` |
| **3.3** | Remove dead `dispatch_path=jobqueue_local` log metric | At `daemon/api.py:393`, the log message `dispatch_path=jobqueue_local` is dead. Either remove the parenthetical or update to `dispatch_path=unified`. | `daemon/api.py:393` |
| **3.4** | Review `observer._admit_via_worker_pool` for dead code | After D11, `_admit_via_worker_pool` (lines 590-709 in `job_feedback_observer.py`) is no longer called from the job processor. Check if it has any other callers. If none, mark as dead code (do NOT delete yet — it may be called from tests or have utility). | `daemon/services/job_feedback_observer.py:590-709` |
| **3.5** | Verify `observer._process_event` still handles Task-driven lifecycle | The observer's `_process_event` (lines 711-852) subscribes to `instance_lifecycle` events. After D11, job completion events come from Task lifecycle, not JobItem lifecycle. Verify the observer still correctly handles the bus gate and terminal transition. **NOTE**: The observer may need adjustment if it was relying on the JobItem lifecycle for MESSAGE jobs. However, since D13 already routes messages through the workerpool path (which creates a Task + notifies the worker directly), the observer's role for MESSAGE jobs was already obsolete. | `daemon/services/job_feedback_observer.py:711-852` |
| **3.6** | Rewrite `test_job_processor.py` MESSAGE-branch tests | **W3**: The MESSAGE-branch tests in `test_job_processor.py` need a full **rewrite** (not just update) since the branch no longer exists. Remove tests that set up MESSAGE JobItems in the job processor. Rewrite them to verify the unified dispatch path handles all jobs correctly. | `tests/job_queue/test_job_processor.py` |
| **3.7** | Update dispatcher path equivalence + enqueue shared tests | **W3**: `test_dispatcher_path_equivalence.py` (entire file tests jobqueue vs workerpool) and `test_enqueue_shared.py` (15+ `dispatch_path="jobqueue"` test cases) need substantial rewrites. After D13+D11, both paths are identical — the equivalence tests become trivially true or need to be replaced with single-path tests. | `tests/test_dispatcher_path_equivalence.py`, `tests/test_enqueue_shared.py` |
| **3.8** | Update remaining test files with `dispatch_path` / `job_type` references | **W3**: Update `test_dispatcher_path_invariants.py` (guard message), `test_manager.py:1757,1812` (dispatch_path calls), `test_pause_terminate_matrix.py:92` (job_type setup), `test_report_lane_phase2.py:146` (job_type default). | `tests/test_dispatcher_path_invariants.py`, `tests/test_manager.py`, `tests/test_pause_terminate_matrix.py`, `tests/test_report_lane_phase2.py` |
| **3.9** | Run full test suite | **W3**: After all test updates, run `pytest tests/ -x` on PostgreSQL. Fix ALL breakage before proceeding to Phase 4. | — |

## Key Files

- `daemon/services/job_processor.py` — `_process_next_job` method (lines 670-797), MESSAGE branch (687-761)
- `daemon/api.py` — dead log metric (line 393)
- `daemon/services/job_feedback_observer.py` — `_admit_via_worker_pool` (590-709), `_process_event` (711-852)
- `tests/job_queue/test_job_processor.py` — **rewrite** MESSAGE-branch tests
- `tests/test_dispatcher_path_equivalence.py` — **rewrite** (entire file)
- `tests/test_enqueue_shared.py` — **rewrite** (15+ test cases)
- `tests/test_dispatcher_path_invariants.py` — update guard message
- `tests/test_manager.py` — update dispatch_path calls
- `tests/test_pause_terminate_matrix.py` — update job_type setup
- `tests/test_report_lane_phase2.py` — update job_type default

## Constraints

- **Must complete after D13 (Phase 2) AND Phase 2.5 (consumption-site rewrites)**: The branch removal is safe ONLY when (a) no MESSAGE JobItems are created (D13) AND (b) all consumers work without JobItems (Phase 2.5).
- **W3 — Full test rewrite for `test_job_processor.py`**: The MESSAGE-branch tests need a full rewrite, not just updates. The branch no longer exists — the tests must verify the unified dispatch path.
- **Observer behavior**: The observer's `_process_event` was the terminal-transition authority for MESSAGE jobs via the JobItem lifecycle. After D13+D11, message completion flows entirely through the WorkerPool → Task lifecycle → `_emit_terminal_via_bus` path (same as internal messages). Verify no regression in completion handling.
- **`_admit_via_worker_pool`**: Do NOT delete yet — it may be referenced by tests. Mark for cleanup in Phase 6.
- **Full test suite must pass before proceeding to Phase 4** (W3).

## Deliverables

- [ ] `if job_type == 'message':` branch removed from `job_processor.py`
- [ ] Dead `dispatch_path=jobqueue_local` log metric removed (api.py:393)
- [ ] No remaining code differentiates MESSAGE from TASK jobs in the processor
- [ ] All jobs flow through the same dispatch path: spawn → enqueue_message → worker
- [ ] `_admit_via_worker_pool` reviewed for dead code (marked, not deleted)
- [ ] 7 test files updated/rewritten (test_job_processor, test_dispatcher_path_equivalence, test_enqueue_shared, test_dispatcher_path_invariants, test_manager, test_pause_terminate_matrix, test_report_lane_phase2)
- [ ] Full test suite passes on PostgreSQL
