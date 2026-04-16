# Phase 2 Implementation — Task↔Job Feedback Loop

## Key Learnings

### 1. Parallel Execution Success
Three tasks ran in parallel successfully:
- Task 1: Instance lifecycle events + dead code removal (manager.py + event/models.py)
- Task 2: JobFeedbackObserver (new file) + api.py wiring
- Task 3: JobRecoveryService (new file) + cancel_job() cascade + api.py wiring

Both Task 2 and Task 3 modified `daemon/api.py` but the changes were in different sections (Task 3 added recovery BEFORE processor, Task 2 added observer AFTER processor). The review caught one missing `set_instance_manager()` call.

### 2. api.py Conflict Pattern
When multiple parallel tasks modify the same file (api.py lifespan), the review session MUST verify:
- Correct startup ordering (recovery → observer → processor)
- No duplicate imports
- All wiring calls present
- Shutdown ordering correct

### 3. Event Bus Event Structure
EventBus events use `event_type` field, NOT `kind`. The observer must filter on `event["event_type"] == "instance_lifecycle"`. This was documented as ADR-012 and correctly implemented.

### 4. Observer Error Handling
The observer loop MUST have try/except with continue around each event. Without this, a single bad event could crash the entire observer, preventing all future job completions. The reviewer specifically flagged this.

### 5. Race Condition: terminate_instance Always Wins
`terminate_instance()` is `async def` but calls `complete_job_sync()` synchronously within the coroutine step — before yielding control. The observer processes events asynchronously from a queue, so it can't act until the current coroutine step completes. `atomic_transition()` with rowcount=0 handles the race gracefully.

### Architecture
- `INSTANCE_LIFECYCLE` event kind with `status` field (completed/terminated/error)
- `_publish_instance_lifecycle_event()` hooks into manager completion paths
- Observer subscribes to EventBus via `subscribe_all()` 
- Recovery runs once at startup before observer/processor
- Cancellation: terminate_instance() → FAILED → CANCELLED double transition

### Test Coverage
- 47 new tests (26 for observer, 21 for recovery)
- Total: 767 passed, 14 skipped
