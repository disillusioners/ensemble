# Message Queue System: Problems & Architecture Analysis

> **Status**: Draft - For Architecture Discussion  
> **Date**: 2026-04-09  
> **Author**: Code Analysis (Agent Orchestrator)

> **Historical snapshot (2026-04-09).** This problem-analysis doc describes race conditions and defects that have since been resolved by the CorrelationManager migration (6 phases) and the deadlock fix. Retained as a historical record of the investigation. For the current architecture, see [`docs/architecture/message-processing-and-correlation.md`](message-processing-and-correlation.md).

---

## Executive Summary

The current message queue system has **three confirmed bugs** that manifest when running multiple leader instances in parallel. These bugs share a common root cause: **race conditions between event-driven signaling and database polling** in a mixed async/threading architecture.

> **Resolution status:** Most issues documented below are now resolved by the CorrelationManager migration (6 phases) and the unified `MessageProcessingPipeline`. See the canonical doc linked above for current architecture.

### Bugs Experienced

| # | Symptom | Root Cause | Severity |
|---|---------|------------|----------|
| 1 | Child (coder) completes but report doesn't reach leader | Race: queue empty check vs new message arrival | Critical |
| 2 | Human message ignored but triggers child report dequeue | Frontend optimistic update + checkpoint timing | Medium |
| 3 | Planner report received twice by parent | Missing idempotency in completion report | High |

---

## Architecture Overview

### Current System Components

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              FRONTEND (Angular)                              │
│                     SSE Connection + REST API Client                        │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              FASTAPI (8079)                                   │
│  ┌────────────────┐  ┌────────────────┐  ┌──────────────────────────────┐   │
│  │ POST /messages │  │ POST /jobs     │  │ SSE /instances/{id}/events  │   │
│  └───────┬────────┘  └───────┬────────┘  └──────────────┬───────────────┘   │
└──────────┼───────────────────┼──────────────────────────┼───────────────────┘
           │                   │                          │
           ▼                   ▼                          ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                           MESSAGE QUEUE (SQLite)                              │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │ MessageQueue Table                                                   │    │
│  │ ─────────────────────────────────────────────────────────────────── │    │
│  │ message_id      | UUID (PK)                                         │    │
│  │ instance_id     | FK to instance (indexed)                          │    │
│  │ content         | TEXT - message body                               │    │
│  │ source          | TEXT - "api", "telegram:user", "internal_report:{id}"      │    │
│  │ status          | ENUM - READY | PROCESSING | RETRYING | COMPLETED   │    │
│  │ priority        | INT - 0 (system) | 1 (user)                        │    │
│  │ retry_count     | INT                                                │    │
│  │ next_retry_at   | TIMESTAMP (for exponential backoff)               │    │
│  │ message_metadata| JSON - child_instance_id, error_type, etc.        │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │ InstanceHierarchy Table                                              │    │
│  │ ─────────────────────────────────────────────────────────────────── │    │
│  │ parent_id | FK to parent instance                                    │    │
│  │ child_id  | FK to child instance                                     │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                          INSTANCE MANAGER                                     │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │ Persistent Consumer Pattern (per instance)                           │    │
│  │                                                                      │    │
│  │  asyncio.Queue ──▶ _process_queue() ──▶ LangGraph execution        │    │
│  │       ▲                                                              │    │
│  │       │                                                              │    │
│  │  _signal_consumer() adds None to wake consumer                       │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │ In-Memory State                                                      │    │
│  │  • instances{}          - loaded graphs                              │    │
│  │  • _instance_queues{}   - asyncio.Queue per instance                 │    │
│  │  • _consumer_tasks{}    - consumer asyncio.Task per instance         │    │
│  │  • _processing{}        - set of instance_ids currently processing   │    │
│  │  • circuit_breaker{}    - failure tracking                           │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                          LANGGRAPH EXECUTION                                 │
│                                                                              │
│     ┌──────────────────────────────────────────────────────────────────┐    │
│     │                    StateGraph(MessagesState)                       │    │
│     │                                                                   │    │
│     │   START → [agent] ───should_continue()──→ [tools] ──→ [agent]    │    │
│     │                         │                  ▲                       │    │
│     │                         ▼                  │                       │    │
│     │                       [nudge] ─────────────┘                       │    │
│     │                         │                                           │    │
│     │                         ▼                                           │    │
│     │                        END                                           │    │
│     │                                                                   │    │
│     │   Checkpointer: AsyncSqliteSaver (separate checkpoints.db)          │    │
│     └──────────────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────────────────┘
```

### Parent-Child Communication Flow

```
LEADER INSTANCE                          CODER INSTANCE
     │                                        │
     │  1. spawn_instance()                   │
     │ ───────────────────────────────────────▶ Creates child with parent_id
     │                                        │
     │  2. Queue empty                        │
     │                                        │ 3. Processes work
     │                                        │ 4. Queue becomes empty
     │                                        │ 5. _send_completion_report()
     │ ◀──────────────────────────────────────
     │  Report enqueued to leader queue       source="internal_report:{coder_id}"
     │                                        │
     │  6. Leader dequeues report             │
     │  7. Leader processes report             │
```

---

## Identified Problems

### Problem 1: Child Report Race Condition (CRITICAL)

**File**: `daemon/manager.py:943-1150`

**Symptom**: Child instance (coder) completes work but report never reaches parent (leader). User has to manually send "check coder result" message.

#### Root Cause

The completion report logic has a **race condition** between dequeuing messages and checking if the queue is empty:

```python
# Lines 943-947: Dequeue loop
while True:
    msg = await asyncio.to_thread(self._queue_repository.dequeue_by_instance, instance_id)
    if msg is None:
        logger.debug(f"No more messages for instance..., exiting loop")
        break  # ← Exits loop when queue is empty

    # ... process message (takes time) ...

# Lines 1149-1154: Check queue empty AFTER loop
if await asyncio.to_thread(self._queue_repository.is_empty, instance_id):
    meta = await asyncio.to_thread(self._instance_repository.get, instance_id)
    if meta and meta.parent_id:
        await self._send_completion_report(instance_id)
```

**The Race**:

```
Timeline:
─────────────────────────────────────────────────────────────────────────────────
│ CHILD │  dequeue(msg3)  │ process(msg3) │  dequeue(NULL) │ is_empty=TRUE │
│       │                 │               │                │ send_report() │
─────────────────────────────────────────────────────────────────────────────────
                          │
                          │ NEW MESSAGE ARRIVES
                          │ (parent sends "check coder result")
                          ▼
─────────────────────────────────────────────────────────────────────────────────
│ CHILD │  dequeue(msg3)  │ process(msg3) │  dequeue(msg4) │ is_empty=FALSE│
│       │                 │               │   ← NEW MSG!   │ NO report!    │
─────────────────────────────────────────────────────────────────────────────────
```

**What Happens**:
1. Child dequeues and processes its last message
2. Before `is_empty` check runs, parent sends "check coder result"
3. Child's `dequeue_by_instance` returns the NEW message (not NULL)
4. Loop continues, `is_empty` check is never reached
5. Child is now busy processing parent's message
6. Child never sends completion report because it's working

**Impact**: Parent never knows child is done. Child appears "stuck" from parent's perspective.

#### Fix Options

**Option A: Atomic Check-and-Report** (Recommended)

```python
while True:
    msg = await asyncio.to_thread(self._queue_repository.dequeue_by_instance, instance_id)
    if msg is None:
        # Queue empty - send report BEFORE releasing any lock
        meta = await asyncio.to_thread(self._instance_repository.get, instance_id)
        if meta and meta.parent_id:
            await self._send_completion_report(instance_id)
        break
    # ... process message ...
```

**Option B: Use Instance Status Instead of Queue State**

```python
# Before: check if queue is empty
# After: check if instance status is "completed"
meta = await asyncio.to_thread(self._instance_repository.get, instance_id)
if meta and meta.parent_id and meta.status == 'running':
    await self._send_completion_report(instance_id)
    await asyncio.to_thread(self._instance_repository.update_status, instance_id, 'completed')
```

---

### Problem 2: Human Message Not Appearing in UI (MEDIUM)

**File**: `frontend/src/app/pages/chat/chat.component.ts:417-453`

**Symptom**: User types message, sees it in UI briefly, but after refresh or navigation, the message is gone. However, the message DID trigger the agent to process the child report.

#### Root Cause

The frontend uses **optimistic updates** without proper persistence:

```typescript
// chat.component.ts:417-432
protected onSendMessage(content: string): void {
    // 1. Add to UI immediately (optimistic)
    const userMessage: Message = {
      message_id: `temp-${Date.now()}`,  // ← Temporary ID
      role: 'user',
      content,
    };
    this.messages.update(prev => [...prev, userMessage]);
    
    // 2. Send to backend
    this.api.sendMessage(instance.instance_id, content).subscribe({...});
}
```

**Problem Flow**:

```
┌──────────────────┐     ┌──────────────────┐     ┌──────────────────┐
│    FRONTEND      │     │     BACKEND      │     │   CHECKPOINT    │
│                  │     │                  │     │                  │
│ 1. Add temp msg  │────▶│ 2. Enqueue msg   │────▶│ 3. Graph runs    │
│    (in memory)   │     │    (DB)          │     │ 4. Checkpoint    │
│                  │     │                  │     │    written       │
│ 5. SSE completed │◀────│                  │     │                  │
│    event comes   │     │                  │     │                  │
│                  │     │                  │     │                  │
│ 6. Add assistant │     │                  │     │                  │
│    response      │     │                  │     │                  │
│                  │     │                  │     │                  │
│ ❌ User's temp   │     │                  │     │                  │
│    msg still has │     │                  │     │                  │
│    temp ID       │     │                  │     │                  │
└──────────────────┘     └──────────────────┘     └──────────────────┘
```

**The Issue**:

1. Frontend adds message with `message_id: 'temp-{timestamp}'`
2. Backend stores actual message with proper UUID
3. Backend checkpoints the graph state
4. Frontend receives SSE `completed` event with assistant response
5. **Frontend's temp message is NOT updated** - it still has the temp ID
6. If user refreshes/navigates, `loadMessages()` fetches from checkpoint
7. Checkpoint has the correct `HumanMessage` from the graph
8. But there's a **timing issue**: the message may not be checkpointed before the first token is streamed

#### Fix Options

**Option A: Frontend Sync on SSE `message_queued`**

```typescript
// In SSE service, listen for message_queued events
eventSource.addEventListener('message_queued', (e: MessageEvent) => {
    const data = JSON.parse(e.data);
    // Update temp message with real message_id
    this.messages.update(prev => 
        prev.map(m => 
            m.message_id.startsWith('temp-') && m.content === data.content
                ? { ...m, message_id: data.message_id }
                : m
        )
    );
});
```

**Option B: Load Messages After Send**

```typescript
protected onSendMessage(content: string): void {
    // ... existing code ...
    
    this.api.sendMessage(instance.instance_id, content).subscribe({
        next: () => {
            // Immediately reload messages to get checkpointed state
            this.loadMessages(instance.instance_id);
        }
    });
}
```

**Option C: Don't Use Temp IDs (Recommended)**

```typescript
protected onSendMessage(content: string): void {
    this.isSending.set(true);
    
    this.api.sendMessage(instance.instance_id, content).subscribe({
        next: (response) => {
            // Show pending state instead of temp message
            this.pendingMessage.set({ content, role: 'user' });
        }
    });
}

// When SSE completes, replace pending with real message
effect(() => {
    const completed = this.sseService.latestCompletedMessage();
    if (completed?.role === 'assistant') {
        this.pendingMessage.set(null);
        // Add assistant message
    }
});
```

---

### Problem 3: Duplicate Completion Report (HIGH)

**File**: `daemon/manager.py:1640-1698`

**Symptom**: Parent receives the same completion report from a child agent twice.

#### Root Cause

`_send_completion_report()` lacks **idempotency protection**, unlike `_send_error_report()` which has it:

```python
# _send_error_report HAS idempotency (lines 1722-1736):
async def _send_error_report(self, instance_id: str, ...) -> None:
    # Check for existing error report in parent's queue
    existing = await asyncio.to_thread(
        self._queue_repository.list,
        instance_id=parent_id, 
        status="ready", 
        limit=10
    )
    for existing_msg in existing:
        if existing_msg.source == f"internal_error_report:{instance_id}":
            logger.debug(f"Error report already queued..., skipping duplicate")
            return  # ← Early return prevents duplicate

# _send_completion_report MISSING this check:
async def _send_completion_report(self, instance_id: str, ...) -> None:
    # NO check! Just sends directly.
    await asyncio.to_thread(
        self._queue_repository.enqueue,
        instance_id=parent_id,
        source=f"internal_report:{instance_id}",  # ← Source is set but never checked
        ...
    )
```

**How Duplicates Occur**:

```
Scenario A: Consumer Re-trigger
────────────────────────────────────────────────────────────────────
│ Consumer │ await queue.get() │ process_queue() │ queue.get() │
│          │                    │ send_report()   │ send_report()│
│          │                    │                 │   ← DUPE!   │
└────────────────────────────────────────────────────────────────────

Scenario B: Status Not Updated
────────────────────────────────────────────────────────────────────
│ Child │ complete() │ send_report() │ terminate() │ send_report() │
│       │            │                │             │   ← DUPE!    │
└────────────────────────────────────────────────────────────────────
```

**Impact**: Parent processes child result twice, leading to duplicate tool calls or corrupted state.

#### Fix

Add idempotency check and instance status tracking:

```python
async def _send_completion_report(self, instance_id: str, ...) -> None:
    # 1. Check instance status
    meta = await asyncio.to_thread(self._instance_repository.get, instance_id)
    if not meta:
        return
    
    # 2. NEW: Prevent duplicate reports
    if meta.status == 'completed':
        logger.debug(f"Instance {instance_id[:8]}... already completed, skipping")
        return
    
    # 3. NEW: Mark as completed atomically
    await asyncio.to_thread(
        self._instance_repository.update_status,
        instance_id,
        'completed'
    )
    
    # 4. Now send report (safe to do)
    # ...
```

---

## Architectural Issues

### Issue 1: Mixed Polling + Event-Driven Pattern

**Problem**: The system mixes two paradigms in a way that creates race conditions:

```
Current Pattern:
┌─────────────────────────────────────────────────────┐
│ Consumer Loop (asyncio)                             │
│  ┌─────────────────────────────────────────────┐    │
│  │ await queue.get()  ← Event-driven wakeup   │    │
│  │ _process_queue()   ← DB polling            │    │
│  │   └─ dequeues from DB                       │    │
│  │   └─ processes messages                     │    │
│  │   └─ may send completion report             │    │
│  └─────────────────────────────────────────────┘    │
│         │                                          │
│         │ Loop continues...                         │
│         ▼                                          │
└─────────────────────────────────────────────────────┘
```

**Issues**:
1. Consumer loops infinitely, even when idle
2. `_signal_consumer()` adds `None` sentinel to wake consumer
3. But consumer may wake up DURING processing
4. No clear state transitions

### Issue 2: In-Memory State Not Durable

**Problem**: Critical state is in-memory only:

```python
# daemon/manager.py
self._processing: set[str]           # In-memory only!
self._instance_queues: dict[str, asyncio.Queue]  # In-memory only!
self._consumer_tasks: dict[str, asyncio.Task]   # In-memory only!
```

**What happens on restart**:
- `_processing` set is empty
- `_instance_queues` are gone
- Consumer tasks are dead
- **But instances still exist in DB!**
- `get_instance()` checks `if instance_id not in self.instances` → fails
- `_ensure_consumer()` checks `if instance_id not in self.instances` → returns early
- **Parent consumer never starts!**

### Issue 3: Checkpoint Timing Unclear

**Problem**: When is the input message checkpointed?

```python
# daemon/manager.py:1257
graph_input = {"messages": [message]}  # message is a plain string

# ... graph runs ...

# Checkpoint written AFTER streaming completes
messages = final_result.values.get("messages", [])
```

**Questions**:
1. Is the input checkpointed BEFORE the first token?
2. What happens if the process crashes mid-streaming?
3. Is the checkpoint transactional with the queue completion?

### Issue 4: Circuit Breaker on Wrong Granularity

**Problem**: Circuit breaker is per-instance, but should be per-message:

```python
# daemon/manager.py:926
if not self.circuit_breaker.can_execute(instance_id):
    # Circuit breaker blocks ALL messages for this instance
    # But we want to block only failing messages
```

**Impact**: If one message fails 5 times, ALL subsequent messages are blocked.

### Issue 5: No Clear Message Lifecycle

**Problem**: Message states are confusing:

```
Message lifecycle (current):
┌──────────┐    ┌─────────────┐    ┌──────────────┐    ┌───────────┐
│  READY   │───▶│ PROCESSING  │───▶│  COMPLETED   │    │  FAILED   │
└──────────┘    └─────────────┘    └──────────────┘    └───────────┘
                      │                   ▲
                      │                   │
                      └───────────────────┘
                           (retry)

Instance lifecycle:
┌──────────┐    ┌─────────────┐    ┌──────────────┐
│ starting │───▶│   running   │───▶│  completed   │
└──────────┘    └─────────────┘    └──────────────┘
                      │
                      ▼
               ┌──────────────┐
               │   error      │
               └──────────────┘
```

**Issues**:
1. Completion report doesn't update instance status
2. Instance status is never checked before sending reports
3. No clear termination state

---

## Proposed Architecture Redesign

### Guiding Principles

1. **Single Source of Truth**: Database is the source of truth, not in-memory state
2. **Atomic Operations**: Check-and-act should be atomic
3. **Explicit State**: Use explicit status flags, not implicit (queue empty)
4. **Idempotency**: All operations must be idempotent
5. **Graceful Degradation**: Handle restarts without losing work

### Architecture Option A: Task-Based Model

**Core Idea**: Replace persistent consumers with task-based processing

```
┌─────────────────────────────────────────────────────────────────┐
│                    Task-Based Architecture                       │
│                                                                 │
│  ┌──────────────┐                                               │
│  │ REST API     │                                               │
│  └──────┬───────┘                                               │
│         │                                                        │
│         ▼                                                        │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │                      MESSAGE ENQUEUE                       │   │
│  │  1. Insert message into DB (status=READY)                │   │
│  │  2. Create processing task                               │   │
│  │  3. Return immediately (fire-and-forget)                  │   │
│  └──────────────────────────────────────────────────────────┘   │
│                            │                                     │
│                            ▼                                     │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │                   TASK EXECUTION                          │   │
│  │                                                          │   │
│  │  async def process_message(instance_id, message_id):     │   │
│  │      async with processing_lock:                         │   │
│  │          if already_processing(instance_id):              │   │
│  │              return  # Another task is handling this       │   │
│  │          mark_processing(instance_id)                     │   │
│  │      try:                                                 │   │
│  │          result = await run_graph(instance_id)            │   │
│  │          mark_completed(message_id, result)               │   │
│  │          if is_child_instance(instance_id):              │   │
│  │              send_completion_report(instance_id)          │   │
│  │      except Exception as e:                               │   │
│  │          handle_error(message_id, e)                      │   │
│  │      finally:                                             │   │
│  │          clear_processing_lock(instance_id)               │   │
│  └──────────────────────────────────────────────────────────┘   │
│                            │                                     │
│                            ▼                                     │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │              SSE NOTIFICATION (Fire-and-Forget)           │   │
│  │  Broadcast events but don't wait for consumers            │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

**Pros**:
- No persistent consumers
- Each message gets its own task
- Clear processing state in database
- Handles restarts gracefully

**Cons**:
- Need to handle concurrent messages for same instance
- Task cleanup on shutdown
- More complex error handling

### Architecture Option B: Actor Model with Explicit State

**Core Idea**: Each instance is an actor with explicit state machine

```
┌─────────────────────────────────────────────────────────────────┐
│                    Actor-Based Architecture                      │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │                    INSTANCE ACTOR                         │   │
│  │                                                           │   │
│  │  States: IDLE → PROCESSING → WAITING_CHILDREN → COMPLETE │   │
│  │                                                           │   │
│  │  ┌─────────────────────────────────────────────────────┐  │   │
│  │  │ IDLE                                                │  │   │
│  │  │   on message → PROCESSING                          │  │   │
│  │  │   on completion_report → send to parent            │  │   │
│  │  └─────────────────────────────────────────────────────┘  │   │
│  │                         │                                 │   │
│  │                         ▼                                 │   │
│  │  ┌─────────────────────────────────────────────────────┐  │   │
│  │  │ PROCESSING                                          │  │   │
│  │  │   on child_spawned → track child                   │  │   │
│  │  │   on child_complete → check all children           │  │   │
│  │  │   on queue_empty + no_children → IDLE               │  │   │
│  │  └─────────────────────────────────────────────────────┘  │   │
│  │                                                           │   │
│  └───────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

**Pros**:
- Explicit state transitions
- Natural handling of child completion
- Clear termination conditions

**Cons**:
- More complex implementation
- State persistence challenges
- Still need to handle concurrent messages

### Architecture Option C: Queue-Based with Choreography

**Core Idea**: Use database queues for everything, no in-memory consumers

```
┌─────────────────────────────────────────────────────────────────┐
│                 Queue-Based Architecture                         │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │                  MESSAGE QUEUE                             │   │
│  │  SELECT * FROM messages WHERE status='READY'             │   │
│  │    AND instance_id = ?                                    │   │
│  │    ORDER BY priority, enqueued_at                         │   │
│  │    LIMIT 1 FOR UPDATE SKIP LOCKED                        │   │
│  └──────────────────────────────────────────────────────────┘   │
│                            │                                     │
│                            ▼                                     │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │                  WORKER POOL                              │   │
│  │                                                          │   │
│  │  Multiple workers compete for messages                    │   │
│  │  "FOR UPDATE SKIP LOCKED" ensures only one gets it       │   │
│  │                                                          │   │
│  │  Worker 1: picks up msg_1 ──▶ processes ──▶ complete    │   │
│  │  Worker 2: picks up msg_2 ──▶ processes ──▶ complete    │   │
│  │  Worker 3: idle...                                        │   │
│  │                                                          │   │
│  └──────────────────────────────────────────────────────────┘   │
│                            │                                     │
│                            ▼                                     │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │              COMPLETION HANDLER                            │   │
│  │  After message completes:                                 │   │
│  │    1. Check if more messages in queue                     │   │
│  │    2. If queue empty AND is child:                        │   │
│  │       - Mark instance status = 'completed'                │   │
│  │       - Send completion report to parent                  │   │
│  │    3. Broadcast SSE event                                 │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

**Pros**:
- Simple, proven pattern
- Database handles all concurrency
- No in-memory state to lose
- Easy to scale workers

**Cons**:
- Database as queue (may not scale to millions)
- Longer poll intervals
- Less real-time than push

---

## Recommended Immediate Fixes

Before redesigning the architecture, these fixes address the critical bugs:

### Fix 1: Atomic Completion Report (High Priority)

```python
# daemon/manager.py - _process_queue() around line 1149

# BEFORE (race condition):
if await asyncio.to_thread(self._queue_repository.is_empty, instance_id):
    meta = await asyncio.to_thread(self._instance_repository.get, instance_id)
    if meta and meta.parent_id:
        await self._send_completion_report(instance_id)

# AFTER (atomic):
msg = await asyncio.to_thread(self._queue_repository.dequeue_by_instance, instance_id)
if msg is None:
    # Queue empty - send report IMMEDIATELY
    meta = await asyncio.to_thread(self._instance_repository.get, instance_id)
    if meta and meta.parent_id:
        await self._send_completion_report(instance_id)
    break
```

### Fix 2: Add Idempotency to Completion Reports

```python
# daemon/manager.py - _send_completion_report() around line 1640

async def _send_completion_report(self, instance_id: str, ...) -> None:
    meta = await asyncio.to_thread(self._instance_repository.get, instance_id)
    if not meta:
        return
    
    # NEW: Prevent duplicates
    if meta.status == 'completed':
        logger.debug(f"Instance already completed, skipping")
        return
    
    # NEW: Mark completed atomically
    await asyncio.to_thread(
        self._instance_repository.update_status,
        instance_id,
        'completed'
    )
    
    # ... rest of function ...
```

### Fix 3: Update Instance Status on Termination

```python
# daemon/manager.py - terminate_instance() around line 2100

async def terminate_instance(self, instance_id: str, ...):
    # ... existing cleanup code ...
    
    # NEW: Update status to terminated
    await asyncio.to_thread(
        self._instance_repository.update_status,
        instance_id,
        'terminated'
    )
```

### Fix 4: Fix Consumer Re-trigger

```python
# daemon/manager.py - _instance_consumer() around line 881

async def _instance_consumer(self, instance_id: str) -> None:
    queue = self._instance_queues.get(instance_id)
    if not queue:
        return
    
    while True:
        try:
            await queue.get()
            queue.task_done()
            
            # Check if already processing before starting
            async with self._processing_lock:
                if instance_id in self._processing:
                    continue  # Skip, another task is handling
            
            await self._process_queue(instance_id)
```

### Fix 5: Lazy Load Instances on Access

```python
# daemon/manager.py - get_instance() around line 500

def get_instance(self, instance_id: str) -> tuple[CompiledStateGraph, str]:
    if instance_id in self.instances:
        return self.instances[instance_id]
    
    # NEW: Lazy load from database
    meta = self._instance_repository.get(instance_id)
    if meta:
        graph, agent_dir = self._load_graph_for_instance(instance_id)
        self.instances[instance_id] = (graph, agent_dir)
        return (graph, agent_dir)
    
    raise KeyError(f"Instance {instance_id} not found")
```

---

## Discussion Points

1. **Consumer Pattern**: Should we keep the persistent consumer pattern or move to task-based?

2. **State Management**: Should in-memory state be the source of truth, or should everything be in the database?

3. **Checkpoint Strategy**: When should messages be checkpointed? Before processing, after, or continuously?

4. **Child Completion**: What's the right way to detect when a child instance is "done"?

5. **Concurrency Limits**: How should we handle multiple parallel leader instances?

6. **Recovery**: How should the system recover from crashes mid-processing?

---

## Files Reference

| Component | File | Key Lines |
|-----------|------|-----------|
| Instance Manager | `daemon/manager.py` | 254-2566 |
| Message Queue | `daemon/queue.py` | 67-455 |
| Message Repository | `daemon/repositories/message_queue/repository.py` | 1-612 |
| Instance Repository | `daemon/repositories/instance/repository.py` | 1-300 |
| Graph Build | `daemon/graph.py` | 351-418 |
| API Routes | `daemon/api.py` | 712-933 |
| Frontend Chat | `frontend/src/app/pages/chat/chat.component.ts` | 417-453 |

---

## Appendix: Message Source Types

| Source | Meaning | Routed To |
|--------|---------|-----------|
| `api` | Human message from REST API | Instance queue |
| `telegram:user:{id}` | Human message from Telegram | Instance queue |
| `internal_agent:{id}` | Message from another agent | Instance queue |
| `internal_report:{instance_id}` | Completion report from child | Parent queue |
| `internal_error_report:{instance_id}` | Error report from child | Parent queue |
| `system` | System-generated message | Instance queue |
