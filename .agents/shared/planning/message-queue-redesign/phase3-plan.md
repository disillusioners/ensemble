# Phase 3: Migrate Message Flow

## Objective

Switch the message processing pipeline from the current consumer pattern (in-memory queues + persistent consumers) to the new worker pool pattern (DB tasks + stateless workers). This is the **highest-risk phase** — it replaces the core message processing loop.

## Coupling

- **Depends on**: Phase 1 (schema), Phase 2 (worker pool)
- **Coupling type**: tight (directly uses WorkerPool and TaskProcessor from Phase 2)
- **Shared files with other phases**: `daemon/manager.py`, `daemon/worker_pool.py`, `daemon/task_processor.py`
- **Shared APIs/interfaces**: `WorkerPool`, `TaskRepository`, `MessageRepository`
- **Why this coupling**: This phase rewrites the message processing flow in `manager.py` to use the worker pool. It directly imports and calls the code built in Phase 2.

## Context

### What Exists Today (Current Flow)

```
API → enqueue_message() → asyncio.Queue → _instance_consumer() → _process_queue() → LangGraph
         ↓                      ↓                    ↓
    DB + in-memory         in-memory            circuit breaker
```

### New Flow

```
API → enqueue_message() → DB (message + task) → Worker claims task → TaskProcessor → LangGraph
         ↓                        ↓                     ↓                    ↓
    DB only                   DB only              DB claim             DB events
```

### Key Change: Dual-Write During Migration

During the transition, we use a **feature flag** to choose which path:

```python
# In manager.py
if self.use_worker_pool:
    self._enqueue_via_worker_pool(instance_id, content, source)
else:
    self._enqueue_via_consumer(instance_id, content, source)  # old path
```

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Add feature flag | Config option to enable worker pool message flow | `daemon/config.py` (modify) |
| 2 | Implement new enqueue | Atomic: insert message + task + update instance status in single transaction | `daemon/manager.py` (modify) |
| 3 | Implement CHECK_CHILD_COMPLETION | Atomic DB check for child completion, insert completion report | `daemon/manager.py` (modify) |
| 4 | Implement child spawn flow | Atomic: insert instance + update parent children + create task | `daemon/manager.py` (modify) |
| 5 | Implement ProcessMessageProcessor | Full implementation connecting worker to LangGraph execution | `daemon/task_processor.py` (modify) |
| 6 | Implement SendReportProcessor | Handle completion reports to parent instances | `daemon/task_processor.py` (modify) |
| 7 | Remove in-memory queue state | Remove `_instance_queues`, `_consumer_tasks`, `_processing` | `daemon/manager.py` (modify) |
| 8 | Remove old consumer pattern | Remove `_instance_consumer()`, `_process_queue()`, `_ensure_consumer()`, `_start_consumer()` | `daemon/manager.py` (modify) |
| 9 | Update API endpoints | Update message enqueue to use new flow | `daemon/api.py` (modify) |
| 10 | Implement restart recovery | On startup: reset stale tasks/messages, re-create worker pool | `daemon/manager.py` (modify) |
| 11 | Write integration tests | Full message flow tests with worker pool | `tests/message_queue_redesign/test_message_flow.py` (new) |
| 12 | Write child completion tests | Test atomic child completion, duplicate prevention | `tests/message_queue_redesign/test_child_completion.py` (new) |

## Key Files

### Modified Files

| File | Changes |
|------|---------|
| `daemon/config.py` | Add `use_worker_pool` config option |
| `daemon/manager.py` | New enqueue flow, remove old consumer, add child completion, restart recovery |
| `daemon/task_processor.py` | Full ProcessMessageProcessor and SendReportProcessor implementations |
| `daemon/api.py` | Update message endpoints to use new flow |

### New Files

| File | Purpose |
|------|---------|
| `tests/message_queue_redesign/test_message_flow.py` | End-to-end message flow tests |
| `tests/message_queue_redesign/test_child_completion.py` | Child completion atomicity tests |
| `tests/message_queue_redesign/test_restart_recovery.py` | Restart recovery tests |

## Constraints

1. **Additive first**: New code path works alongside old path, controlled by feature flag
2. **No data loss**: All messages persist in DB regardless of which path is used
3. **Backward compatible**: Old path continues to work until feature flag is enabled
4. **Atomic operations**: All state changes in single transactions

## Detailed Design

### New Enqueue Flow

```python
async def _enqueue_via_worker_pool(self, instance_id: str, content: str, 
                                    source: str, metadata: dict = None):
    """New message enqueue: atomic message + task + instance update."""
    
    message_id = str(uuid4())
    task_id = str(uuid4())
    
    with SQLModelSession(self._engine) as session:
        # 1. Insert message
        message = MessageQueue(
            message_id=message_id,
            instance_id=instance_id,
            content=content,
            source=source,
            message_metadata=metadata or {},
            status=MessageStatus.READY.value,
            type=MessageType.HUMAN.value,
            priority=1,
            enqueued_at=datetime.now(timezone.utc)
        )
        session.add(message)
        
        # 2. Create task
        task = Task(
            id=task_id,
            type=TaskType.PROCESS_MESSAGE,
            instance_id=instance_id,
            message_id=message_id,
            status=TaskStatus.PENDING.value,
            created_at=datetime.now(timezone.utc)
        )
        session.add(task)
        
        # 3. Update instance status
        instance = session.get(Instance, instance_id)
        if instance.status == InstanceStatus.IDLE.value:
            instance.status = InstanceStatus.RUNNING.value
            instance.last_activity_at = datetime.now(timezone.utc)
        
        # 4. Create event
        event = Event(
            instance_id=instance_id,
            message_id=message_id,
            type=EventType.MESSAGE_RECEIVED,
            data={"source": source},
            created_at=datetime.now(timezone.utc)
        )
        session.add(event)
        
        session.commit()
    
    return message_id
```

<!-- FIX: C3 — fetch content BEFORE transaction to avoid orphaned COMPLETED state -->
### CHECK_CHILD_COMPLETION (Atomic)

**Critical**: `_get_last_assistant_message()` reads from LangGraph's async checkpointer and can fail. If we set `instance.status = COMPLETED` inside the transaction and then the content fetch fails, we're left with a completed instance but no report. The fix: fetch content **before** the transaction.

```python
async def check_child_completion(self, instance_id: str):
    """Atomic check if child instance is done and should send completion report.
    
    IMPORTANT: Content is fetched BEFORE the transaction to avoid leaving the 
    instance in COMPLETED state without a report if the fetch fails.
    """
    
    # --- OUTSIDE TRANSACTION: Fetch content first ---
    last_content = await self._get_last_assistant_message(instance_id)
    if last_content is None:
        logger.warning(f"No content found for instance {instance_id}, skipping completion report")
        return  # Don't proceed without content
    
    # --- INSIDE TRANSACTION: All DB mutations ---
    with SQLModelSession(self._engine) as session:
        instance = session.get(Instance, instance_id)
        
        # Not a child? Nothing to do
        if instance.parent_id is None:
            return
        
        # Check for pending/processing messages
        pending = session.exec(
            select(func.count()).select_from(MessageQueue)
            .where(MessageQueue.instance_id == instance_id)
            .where(MessageQueue.status.in_([
                MessageStatus.READY.value,
                MessageStatus.PROCESSING.value
            ]))
        ).one()
        
        if pending > 0:
            return  # Still has work
        
        # Check if already sent completion report (idempotency)
        existing = session.exec(
            select(MessageQueue)
            .where(MessageQueue.instance_id == instance.parent_id)
            .where(MessageQueue.source == f"internal_report:{instance_id}")
            .where(MessageQueue.status.in_([
                MessageStatus.READY.value,
                MessageStatus.PROCESSING.value,
                MessageStatus.COMPLETED.value
            ]))
        ).first()
        
        if existing is not None:
            return  # Already sent
        
        # ATOMIC: Complete instance + create report + create task
        instance.status = InstanceStatus.COMPLETED.value
        instance.updated_at = datetime.now(timezone.utc)
        
        report_message_id = str(uuid4())
        report_message = MessageQueue(
            message_id=report_message_id,
            instance_id=instance.parent_id,
            content=last_content,  # Already fetched above
            type=MessageType.COMPLETION_REPORT.value,
            source=f"internal_report:{instance_id}",
            status=MessageStatus.READY.value,
            priority=0,  # System priority
            enqueued_at=datetime.now(timezone.utc)
        )
        session.add(report_message)
        
        report_task = Task(
            id=str(uuid4()),
            type=TaskType.PROCESS_MESSAGE,
            instance_id=instance.parent_id,
            message_id=report_message_id,
            status=TaskStatus.PENDING.value
        )
        session.add(report_task)
        
        # Decrement parent's waiting_for counter
        parent = session.get(Instance, instance.parent_id)
        parent.waiting_for = max(0, (parent.waiting_for or 0) - 1)
        
        # Update parent's children denormalized cache
        # NOTE: instance_hierarchy junction table is canonical source; this is a cache update <!-- FIX: W6 -->
        if instance_id in (parent.children or []):
            parent.children = [c for c in parent.children if c != instance_id]
        
        # Check if parent is now complete
        if parent.waiting_for == 0 and parent.status == InstanceStatus.WAITING_CHILDREN.value:
            parent_pending = session.exec(
                select(func.count()).select_from(MessageQueue)
                .where(MessageQueue.instance_id == parent.instance_id)
                .where(MessageQueue.status.in_([
                    MessageStatus.READY.value,
                    MessageStatus.PROCESSING.value
                ]))
            ).one()
            
            if parent_pending == 0:
                parent.status = InstanceStatus.COMPLETED.value
        
        session.commit()
```

### Manager State Removal

**Remove these in-memory structures:**
```python
# DELETE these from InstanceManager:
self._instance_queues: dict[str, asyncio.Queue]     # No longer needed
self._consumer_tasks: dict[str, asyncio.Task]       # No longer needed  
self._processing: set[str]                          # Replaced by DB status
```

**Remove these methods:**
```python
# DELETE these from InstanceManager:
_ensure_consumer()    # Workers handle this
_start_consumer()     # Workers handle this
_instance_consumer()  # Workers handle this
_process_queue()      # TaskProcessor handles this
_signal_consumer()    # Workers poll DB
```

**Keep but adapt:**
```python
# KEEP these (adapted):
_process_message_with_tracking()  # Called by ProcessMessageProcessor
spawn_instance()                  # Enhanced to use new schema
terminate_instance()              # Enhanced to use new schema
shutdown()                        # Adapted for worker pool shutdown
```

## Migration Sequence

### Step 1: Dual-Write Phase (Safe)
- Feature flag `use_worker_pool = false`
- Old consumer path still active
- New enqueue path available but unused
- **All existing tests still pass**

### Step 2: Enable Worker Pool (Cutover)
- Feature flag `use_worker_pool = true`
- New messages go through worker pool
- Old consumers stop receiving new messages
- Monitor for issues

### Step 3: Drain Old Consumers <!-- FIX: W3 -->
```python
# Explicit draining logic — wait for in-flight work to complete
logger.info("Draining old consumers...")
while self._consumer_tasks and any(not t.done() for t in self._consumer_tasks.values()):
    await asyncio.sleep(1)
logger.info("All old consumers drained")
# Now safe to remove old consumer code
```
- Wait for old consumers to finish current work
- No messages lost — all in-flight work completes
- Remove old consumer code

### Step 4: Remove Feature Flag
- Worker pool is now the only path
- Remove the feature flag and old code path

## Testing Strategy

### Integration Tests

| Test | Scenario |
|------|----------|
| `test_enqueue_creates_task` | Enqueueing a message creates both message and task |
| `test_worker_processes_message` | Worker picks up task and processes the message |
| `test_child_completion_atomic` | Child completion is atomic, no race |
| `test_duplicate_report_prevention` | Can't send completion report twice |
| `test_restart_recovery` | After restart, stale tasks are recovered |
| `test_parent_completion_cascade` | Parent completes when all children done |
| `test_error_report_flow` | Child failure sends error report to parent |
| `test_feature_flag_toggle` | Can switch between old and new path |

### Regression Tests

| Test | Scenario |
|------|----------|
| `test_existing_tests_pass` | All existing test suite passes with new code |
| `test_message_not_lost` | No messages lost during cutover |
| `test_instance_lifecycle` | Full instance lifecycle works with new flow |

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| LangGraph execution doesn't work from worker thread | High | Use `asyncio.run_coroutine_threadsafe()` to bridge to main event loop; same pattern as manager.py:349 <!-- FIX: C1 --> |
| Long-running tasks block other work | Medium | Workers don't share state; one slow task doesn't block others |
| Transaction deadlocks on SQLite | Medium | busy_timeout=30s; keep transactions short; retry on SQLITE_BUSY |
| Instance state incorrect after crash | High | Recovery task resets stale tasks; instance status is source of truth |
| Content fetch fails in child completion | Medium | Fetch content BEFORE transaction; if None, skip report safely <!-- FIX: C3 --> |

## Deliverables

- [ ] Feature flag for worker pool toggle
- [ ] New enqueue method (atomic message + task + instance update)
- [ ] CHECK_CHILD_COMPLETION atomic implementation
- [ ] ProcessMessageProcessor fully implemented
- [ ] SendReportProcessor fully implemented
- [ ] In-memory queue state removed
- [ ] Old consumer pattern removed
- [ ] Restart recovery implemented
- [ ] Integration tests pass
- [ ] All existing tests pass (no regression)
- [ ] Feature flag tested (both paths work)
