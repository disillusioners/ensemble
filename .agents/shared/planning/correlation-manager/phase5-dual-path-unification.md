# Phase 5: Dual-Path Event Unification (Optional)

## Objective
Unify the WorkerPool and JobQueue message processing paths so both publish to the same shared event topics, reducing the 14 mirroring points to shared event emitters. This is the final cleanup phase that leverages the CorrelationManager foundation from Phases 1-4.

## Coupling
- **Depends on**: Phase 3 (unified cascade delegation to CM)
- **Coupling type**: loose — touches different code (dispatch paths, not cascade logic)
- **Shared files with other phases**: `task_processor.py`, `message_job_handler.py`, `instance_messaging.py`
- **Shared APIs/interfaces**: EventBus event types, shared processing helpers
- **Why this coupling**: Phase 3 unified the cascade decision; Phase 5 unifies the dispatch path side-effects. Only depends on Phase 3's event types being stable.

## Context

### Current Dual-Path State (from Investigation)

| Path | File | Lines | Dispatcher |
|------|------|-------|------------|
| WorkerPool | `task_processor.py` | 620 | `ProcessMessageProcessor` + `TaskProcessor` |
| JobQueue | `message_job_handler.py` | 495 | `MessageJobHandler` |

### 14 Mirroring Points (5 Active Divergences)

| # | Stage | WorkerPool | JobQueue | Status After Phase 0 |
|---|-------|-----------|----------|---------------------|
| 1 | Acquire Execution Gate | `task_processor.py:248` | `message_job_handler.py:214` | ✅ Both |
| 2 | Call `_process_message_with_tracking` | `task_processor.py:235` | `message_job_handler.py:184` | ✅ Both |
| 3 | Mark message COMPLETED | `task_processor.py:330` | `message_job_handler.py:271` | ✅ Both |
| 4 | Resolve `original_source` | `task_processor.py:355-365` | `message_job_handler.py:286-298` | ✅ Both |
| 5 | `dispatch_completed` | `task_processor.py:374-380` | `message_job_handler.py:302-308` | ✅ Both |
| 6 | `_process_child_completion_and_notify_parent` | `task_processor.py:389-396` | `message_job_handler.py:317-319` | ✅ Both |
| 7 | WAITING_CHILDREN deferral | `complete_task` | `skip_complete` + observer | △ Divergent mechanism |
| 8 | `retry_count` | `task.retry_count` | Hardcoded `0` | ✅ Fixed in Phase 0 |
| 9 | Error event in DB | `task_processor.py:417-436` | (missing) | ✅ Fixed in Phase 0 |
| 10 | Lifecycle event publish | `task_processor.py:440-451` | (missing) | ✅ Fixed in Phase 0 |
| 11 | `_send_error_report` | `task_processor.py:456-465` | (missing) | ✅ Fixed in Phase 0 |
| 12 | Error type classification | `_classify_error_type(e)` | (missing) | ✅ Fixed in Phase 0 |
| 13 | Re-queue on `LeaseContention` | `task_processor.py:315-325` | `message_job_handler.py:236-239` | ✅ Both |
| 14 | Re-queue on `LeaseLostError` | `task_processor.py:265-273` | `message_job_handler.py:254-258` | ✅ Both |

After Phase 0, all 5 bugs are fixed. Phase 5 goes further: extracts shared logic into reusable components so the two paths become thin wrappers.

### Duplicated `enqueue_message` Code

| Function | File:Lines | Length | Differences |
|----------|-----------|--------|-------------|
| `enqueue_message` (WorkerPool) | `instance_messaging.py:696-839` | 144 lines | Creates `Task` row, notifies `WorkerPool` |
| `enqueue_message_via_jq` (JobQueue) | `instance_messaging.py:1331-1500` | 170 lines | Creates `JobQueue` item, notifies `JobProcessor` |

~50 lines of the prelude (MessageQueue row + Event + status transition + title generation) are identical.

## Tasks

### Part A: Extract Shared Processing Pipeline

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Create `MessageProcessingPipeline` class | Encapsulates the shared stages: gate acquire, process, mark complete, dispatch, child completion check, error handling | `daemon/services/message_processing_pipeline.py` (new) |
| 2 | Define pipeline interface | `async def execute(self, context: ProcessingContext) -> ProcessingResult` — single entry point for both paths | `daemon/services/message_processing_pipeline.py` |
| 3 | Refactor WorkerPool to use pipeline | `ProcessMessageProcessor.process()` delegates to pipeline | `daemon/services/task_processor.py` |
| 4 | Refactor JobQueue to use pipeline | `MessageJobHandler.handle()` delegates to pipeline | `daemon/services/message_job_handler.py` |
| 5 | Path-specific hooks via strategy pattern | WorkerPool provides `on_complete` (task completion); JobQueue provides `on_complete` (job completion) | Both files |

### Part B: Extract Shared Enqueue Logic

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 6 | Create `_prepare_enqueued_message` helper | Extract the 50 shared lines: MessageQueue row + Event + status transition + title generation | `daemon/services/instance_messaging.py` |
| 7 | Refactor `enqueue_message` to use helper | WorkerPool path calls helper, then creates Task + notifies pool | `daemon/services/instance_messaging.py:696-839` |
| 8 | Refactor `enqueue_message_via_jq` to use helper | JobQueue path calls helper, then creates job item + notifies processor | `daemon/services/instance_messaging.py:1331-1500` |

### Part C: Unify Event Emission

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 9 | Create shared event emission helpers | `_emit_processing_started`, `_emit_processing_completed`, `_emit_processing_error` — called by pipeline | `daemon/services/message_processing_pipeline.py` |
| 10 | Both paths emit through shared helpers | Pipeline calls these; no path-specific event code | Both files |

### Part D: Consolidate Status Conditionals

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 11 | Reduce `InstanceStatus` conditional branches | After Phase 4 removes `WAITING_CHILDREN`, ~20 of the 43 references disappear. Audit remaining ~23 and consolidate. | Multiple files |
| 12 | Consolidate `InstanceStatus` definition | Remove duplicate at `daemon/models/instance.py:13`; use only `daemon/repositories/instance/models.py:28` | `daemon/models/instance.py` |
| 13 | Replace raw string status checks with enum | 19 raw-string checks across `job_feedback_observer.py`, `job_queue_service.py`, etc. → use `InstanceStatus` enum | Multiple files |

### Part E: Testing

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 14 | Test pipeline produces identical results for both paths | Process same message through WorkerPool and JobQueue, verify identical side-effects | `tests/test_pipeline_unified.py` (new) |
| 15 | Test enqueue helper creates identical pre-state | Both enqueue variants create same MessageQueue + Event rows | `tests/test_enqueue_shared.py` (new) |
| 16 | Regression: full test suite | All existing tests pass | All tests |

## Shared Pipeline Design

```python
@dataclass
class ProcessingContext:
    """Input for message processing — path-agnostic."""
    instance_id: str
    message_id: str
    message: str
    retry_count: int
    message_source: str | None = None
    silent: bool = False
    images: list[str] | None = None

@dataclass
class ProcessingResult:
    """Output of message processing — path-agnostic."""
    success: bool
    result_content: str | None = None
    error: Exception | None = None
    should_defer: bool = False  # waiting for children (Phase 4: CM check)

class MessageProcessingPipeline:
    """
    Shared message processing pipeline used by both WorkerPool and JobQueue.
    
    Encapsulates: gate acquire → process → mark complete → dispatch → 
    child completion check → error handling → event emission.
    
    Path-specific concerns (task vs job tracking) handled via callbacks.
    """

    def __init__(
        self,
        execution_gate: ExecutionGateService,
        instance_manager: InstanceManager,
        event_bus: EventBus,
        correlation_manager: CorrelationManager,
    ) -> None: ...

    async def execute(
        self,
        context: ProcessingContext,
        holder_id: str,
        holder_kind: str,
        on_success: Callable | None = None,   # path-specific completion
        on_error: Callable | None = None,      # path-specific error
        on_defer: Callable | None = None,      # path-specific deferral
    ) -> ProcessingResult:
        """
        Execute the full message processing pipeline.
        
        1. Acquire execution gate
        2. Call _process_message_with_tracking
        3. Mark message COMPLETED
        4. Resolve original_source + dispatch_completed
        5. Check child completion (via CorrelationManager)
        6. If error: emit error event + lifecycle + report
        7. If defer: call on_defer
        8. If success: call on_success
        """
        ...
```

### WorkerPool Path (After Refactor)

```python
class ProcessMessageProcessor:
    async def process(self, task: Task) -> None:
        context = ProcessingContext(
            instance_id=task.instance_id,
            message_id=task.message_id,
            message=task.message,
            retry_count=task.retry_count,
        )

        async def on_success(result: ProcessingResult):
            self._task_repository.complete_task(task.id)
            # WorkerPool-specific: no job tracking

        async def on_error(result: ProcessingResult):
            # Error handling already done by pipeline
            self._task_repository.complete_task(task.id, status="failed")

        result = await self._pipeline.execute(
            context=context,
            holder_id=f"task:{task.id}",
            holder_kind=LeaseHolderKind.TASK.value,
            on_success=on_success,
            on_error=on_error,
        )
```

### JobQueue Path (After Refactor)

```python
class MessageJobHandler:
    async def handle(self, job: JobItem) -> None:
        context = ProcessingContext(
            instance_id=job.instance_id,
            message_id=job.metadata.get("message_id"),
            message=job.metadata.get("message", "resume"),
            retry_count=job.metadata.get("retry_count", 0),
        )

        async def on_success(result: ProcessingResult):
            await self._job_service.complete_job(
                job.job_id, demand_state=DemandState.COMPLETED,
                result_summary=result.result_content,
            )

        async def on_error(result: ProcessingResult):
            await self._job_service.complete_job(
                job.job_id, demand_state=DemandState.FAILED,
                error=str(result.error),
            )

        async def on_defer():
            # Don't complete job — let observer handle it via correlation events
            await self._job_service.notify_watchers(
                job.job_id, status="in_progress",
            )

        result = await self._pipeline.execute(
            context=context,
            holder_id=f"message_job:{job.job_id}",
            holder_kind=LeaseHolderKind.MESSAGE_JOB.value,
            on_success=on_success,
            on_error=on_error,
            on_defer=on_defer,
        )
```

## Key Design Decisions

### 1. Pipeline with Strategy Callbacks (Not Inheritance)
**Decision**: `MessageProcessingPipeline.execute()` takes `on_success`, `on_error`, `on_defer` callbacks.
**Rationale**:
- WorkerPool and JobQueue are fundamentally different dispatch mechanisms
- Inheritance would create a fragile base class with many conditionals
- Callbacks/strategy is the established pattern in the codebase (see ExecutionGate's `work_fn`)
- Each path customizes only its unique concern (task vs job tracking)

### 2. Pipeline Owns All Event Emission
**Decision**: The pipeline emits error events, lifecycle events, and error reports — not the callers.
**Rationale**:
- Centralizes event emission (fixes the "missing error reporting" class of bugs permanently)
- Callers only handle their dispatch-specific side-effects
- Any new path (e.g., future "batch processor") automatically gets correct event emission

### 3. Enqueue Helper Extraction (Not Full Consolidation)
**Decision**: Extract the ~50 shared prelude lines, keep the two `enqueue_message` variants separate.
**Rationale**:
- The dispatch mechanisms are genuinely different (Task table vs JobQueue table)
- Full consolidation would require unifying the dispatch layer (huge scope)
- Helper extraction eliminates duplication without forcing architectural convergence
- The remaining ~90 lines in each variant are path-specific dispatch code

### 4. `InstanceStatus` Consolidation
**Decision**: Remove duplicate enum at `daemon/models/instance.py:13`; use only canonical at `daemon/repositories/instance/models.py:28`.
**Rationale**:
- Two definitions is a maintenance hazard (drift risk)
- All imports should use the canonical repository model
- This is a code quality fix that's safe to do once Phase 4 removes `WAITING_CHILDREN`

## Key Files

| File | Purpose |
|------|---------|
| `daemon/services/message_processing_pipeline.py` (new) | Shared processing pipeline |
| `daemon/services/task_processor.py` | WorkerPool path → thin wrapper |
| `daemon/services/message_job_handler.py` | JobQueue path → thin wrapper |
| `daemon/services/instance_messaging.py:696-839, 1331-1500` | Enqueue helpers |
| `daemon/models/instance.py` | Duplicate InstanceStatus — remove |
| `daemon/repositories/instance/models.py` | Canonical InstanceStatus — keep |

## Constraints
- Must not change the external API contract (both paths produce identical observable behavior)
- WorkerPool and JobQueue must remain independently deployable (can't force one to depend on the other)
- Pipeline must not introduce a new bottleneck (both paths can still process concurrently for different instances)
- Callbacks must be async (all side-effects involve DB/event I/O)
- `_classify_error_type` must be importable by the pipeline module

## Verification Strategy

1. **Parity test**: Process identical message through both paths, verify identical: error events, lifecycle events, child reports, message completion, status transitions
2. **Pipeline unit test**: Mock gate, instance_manager, event_bus; verify pipeline calls all stages in correct order
3. **Callback test**: Verify `on_success`, `on_error`, `on_defer` are called at the right times
4. **Enqueue helper test**: Verify both enqueue variants create identical MessageQueue + Event rows (only dispatch differs)
5. **Regression**: Full test suite passes
6. **Code metric**: Count mirroring points after refactor — target ≤5 (down from 14)

## Rollback Plan

1. Restore `task_processor.py` and `message_job_handler.py` from git
2. Remove `message_processing_pipeline.py`
3. Restore enqueue functions from git (remove helper extraction)
4. Restore `daemon/models/instance.py` InstanceStatus

The rollback is **safe** because:
- Pipeline is additive — removing it doesn't affect DB or events
- No schema changes
- The thin wrappers just re-expand to their original form
- CorrelationManager (from Phases 1-4) is unaffected

## Deliverables
- [ ] `MessageProcessingPipeline` class implemented
- [ ] WorkerPool path refactored to use pipeline
- [ ] JobQueue path refactored to use pipeline
- [ ] Shared enqueue helper extracted
- [ ] Event emission centralized in pipeline
- [ ] `InstanceStatus` duplicate removed
- [ ] Raw string status checks replaced with enum
- [ ] Mirroring points reduced from 14 to ≤5
- [ ] Parity test: both paths produce identical side-effects
- [ ] Full test suite passes

## Optional: Future Considerations (Beyond Phase 5)

If the team decides to fully converge the dual paths:
- **Unified dispatcher**: Single dispatcher that routes to either WorkerPool or JobQueue based on config/feature flag
- **Single enqueue function**: One `enqueue_message` that creates both Task and JobQueue items, dispatching to the configured backend
- **Remove one path entirely**: If one path proves superior, deprecate the other

These are NOT part of this plan — they're architectural decisions that require separate investigation.
