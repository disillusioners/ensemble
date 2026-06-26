# Phase 4: Phase 6 — Remove `dispatch_path` Parameter

## Objective

Remove the `dispatch_path` parameter from `enqueue_message` entirely. After D13 (Phase 2) and D11 (Phase 3), the parameter selects between two code paths that are now identical. Always write `task` row + `message_queue` row and notify the WorkerPool — no JobItem ever.

## Coupling

- **Depends on**: Phase 2 (D13) + Phase 2.5 (consumption-site rewrites) + Phase 3 (D11) — **tight coupling**
- **Coupling type**: tight — Phase 2 eliminates the jobqueue path's distinct behavior; Phase 3 removes the branch that consumes it; Phase 4 removes the now-meaningless parameter
- **Shared files with other phases**: `instance_messaging.py` (Phase 2 modified the body; Phase 4 cleans the signature)
- **Shared APIs/interfaces**: `enqueue_message` public API signature changes
- **Why this coupling**: The parameter is only safe to remove when both code paths produce identical behavior, which requires D13 + D11 to be complete.

## Context

After Phase 2, the `enqueue_message` function looks approximately like:

```python
async def enqueue_message(
    self,
    instance_id: str,
    message: str,
    source: str = "api",
    priority: int = 1,
    images: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    dispatch_path: Literal["workerpool", "jobqueue"] = "workerpool",  # ← to remove
) -> "AsyncMessageResult":
    # _prepare_enqueued_message now always creates Task row
    ctx = await asyncio.to_thread(self._prepare_enqueued_message, ...)

    job_id: str | None = None
    if dispatch_path == "jobqueue":
        # D13: this branch now just sets job_id = str(ctx.task_id)
        # (no longer calls enqueue_job)
        job_id = str(ctx.task_id) if ctx.task_id else None
    else:
        # workerpool path
        if self._manager._worker_pool is not None:
            self._manager._worker_pool.notify_work()

    return AsyncMessageResult(message_id=..., job_id=job_id, ...)
```

After this phase, the function should look like:

```python
async def enqueue_message(
    self,
    instance_id: str,
    message: str,
    source: str = "api",
    priority: int = 1,
    images: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> "AsyncMessageResult":
    # Always creates Task row + MessageQueue row
    ctx = await asyncio.to_thread(self._prepare_enqueued_message, ...)
    # Always notify WorkerPool
    if self._manager._worker_pool is not None:
        self._manager._worker_pool.notify_work()
    return AsyncMessageResult(message_id=..., job_id=str(ctx.task_id), ...)
```

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| **4.1** | Remove `dispatch_path` parameter from signature | Remove `dispatch_path: Literal["workerpool", "jobqueue"] = "workerpool"` from `enqueue_message` signature. Remove the `Literal` import if no longer used. | `daemon/services/instance_messaging.py:887-896` |
| **4.2** | Collapse the dispatch branch | Remove the `if dispatch_path == "jobqueue":` / `else:` branch. Always call `worker_pool.notify_work()`. The `job_id` return value is always `str(ctx.task_id)`. | `daemon/services/instance_messaging.py:989-1038` |
| **4.3** | Update all callers to remove `dispatch_path` argument | Remove `dispatch_path="jobqueue"` from: (a) `daemon/routers/messages.py:124`, (b) `daemon/tools/job_queue.py:477`. These are the only two callers that pass it explicitly. **Note**: The `job_continue` concurrency gate at `tools/job_queue.py:466-470` was already rewritten in Phase 2.5 Task 2.5.8 — this task only removes the `dispatch_path` argument from the `enqueue_message` call at line 477. | `daemon/routers/messages.py:124`, `daemon/tools/job_queue.py:477` |
| **4.4** | Remove `create_task_row` flag from `_prepare_enqueued_message` | The `create_task_row` parameter (line 963) is now always `True`. Remove the flag and the conditional logic. The function always creates a Task row. | `daemon/services/instance_messaging.py:963` (and `_prepare_enqueued_message` implementation) |
| **4.5** | Update tests | Remove any test that passes `dispatch_path` as an argument. Verify all tests still pass with the unified function. **W3**: Run full test suite after this change. | `tests/unit/test_instance_messaging.py`, full suite |
| **4.6** | Grep verification | Run `grep -rn 'dispatch_path' daemon/` — should return 0 hits. If any remain in comments/docstrings, clean them up. **Minor note**: The `waiting_for` grep will hit `daemon/opencode/state.py` with ~3 false positives (`waiting_for_input` state reason) — these are expected and should be documented as known false positives, NOT removed. | — |

## Key Files

- `daemon/services/instance_messaging.py` — `enqueue_message` signature (887-896), dispatch branch (989-1038), `_prepare_enqueued_message` (963-975)
- `daemon/routers/messages.py` — HTTP send_message (119-125, remove `dispatch_path="jobqueue"`)
- `daemon/tools/job_queue.py` — `job_continue` tool (473-487, remove `dispatch_path="jobqueue"`)
- `tests/unit/test_instance_messaging.py` — dispatch_path tests

## Constraints

- **API contract**: `AsyncMessageResult.job_id` must still be populated (now always from `task.id`). The HTTP route and `job_continue` tool depend on it.
- **Backward compatibility**: Any code that passes `dispatch_path="jobqueue"` will break with a `TypeError` (unexpected keyword argument). This is the desired behavior — it surfaces any missed callers during testing.
- **`_prepare_enqueued_message`**: The `create_task_row` flag removal must not change the transaction semantics. The function should still create MessageQueue + Event + Task rows in a single transaction.
- **Minor note — grep false positives**: `waiting_for` grep will hit `opencode/state.py` (~3 false positives: `waiting_for_input` state reason). Document as expected — do NOT remove. `.children` grep returns ~4 hits — all in explanatory comments, not active reads. Document as expected.

## Deliverables

- [ ] `enqueue_message` has no `dispatch_path` parameter
- [ ] No `dispatch_path` references in `daemon/` (0 grep hits, except known false positives documented)
- [ ] All callers updated to call `enqueue_message` without `dispatch_path`
- [ ] `_prepare_enqueued_message` always creates Task row (no `create_task_row` flag)
- [ ] Full test suite passes on PostgreSQL
