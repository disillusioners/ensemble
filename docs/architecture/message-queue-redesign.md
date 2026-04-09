# Message Queue Architecture Redesign

> **Status**: For Design Discussion  
> **Goal**: Eliminate race conditions and concurrency bugs through principled architecture  
> **Principle**: Bugs should be impossible by design, not fixed by patches

---

## Executive Summary

The current architecture has **fundamental flaws** that cannot be fixed with patches:

| Current Problem | Root Cause | Patches Fail Because... |
|-----------------|------------|------------------------|
| Child report race | Queue state checked AFTER dequeue | Timing gap is inherent in check-then-act |
| Duplicate reports | No idempotency on send | Can't fix without changing when we send |
| Consumer deadlocks | In-memory state vs DB state | Two sources of truth conflict |
| Message lost | Frontend optimistic + checkpoint async | Need atomic frontend-backend sync |

**Solution**: Redesign the architecture so these bugs are **impossible by construction**, not fixed by code.

---

## Current Architecture Flaws

### Flaw 1: Two Sources of Truth

The system has conflicting sources of truth:

```
┌─────────────────────────────────────────────────────────────────────┐
│                    CURRENT: TWO SOURCES OF TRUTH                     │
│                                                                     │
│  ┌─────────────────────┐           ┌─────────────────────┐        │
│  │    IN-MEMORY        │           │      DATABASE        │        │
│  │                     │           │                     │        │
│  │  • self.instances   │   ???     │  • Instance table    │        │
│  │  • _processing{}    │           │  • MessageQueue      │        │
│  │  • _instance_queues │           │  • InstanceHierarchy │        │
│  │  • _consumer_tasks  │           │                     │        │
│  └─────────────────────┘           └─────────────────────┘        │
│                                                                     │
│  Problem: They can diverge!                                          │
│  • DB says instance exists, memory says no (restart scenario)       │
│  • Memory says processing, DB says idle (crash scenario)             │
│  • Queue empty in DB, but message in memory queue                   │
└─────────────────────────────────────────────────────────────────────┘
```

**Why this is fatal**: Every operation must keep both in sync. Any inconsistency = bugs.

### Flaw 2: Check-Then-Act Pattern

The current code uses a dangerous pattern:

```python
# DANGEROUS: Check then act with gap in between
msg = dequeue_by_instance(instance_id)  # Act 1: Dequeue
if msg is None:                         # Check: Is queue empty?
    send_completion_report()            # Act 2: Send report
```

**The gap**:
```
Time ──────────────────────────────────────────────────────────────────────▶
     │                │                         │
     │  dequeue()     │                         │
     │  returns msg   │                         │
     │                │                         │
     │                │  NEW MESSAGE            │
     │                │  ARRIVES HERE!          │
     │                │                         │
     │                │                         ▼
     │                │                dequeue() returns NEW msg
     │                │                is_empty() is NEVER called
     │                │                completion_report is NEVER sent
```

### Flaw 3: Implicit State via Queue

The system uses "queue is empty" as a proxy for "child is done":

```python
# Current: Implicit state
if is_queue_empty(instance_id):
    send_completion_report()

# Problem: Queue can become empty for OTHER reasons:
# • Message dequeued but not yet processed
# • Message failed and not re-queued
# • Race condition with new message
```

**Better**: Use **explicit state** via database status field.

### Flaw 4: Consumer Lives Forever

Persistent consumers create state management nightmares:

```python
# Consumer never dies, but:
# • Event loop can close
# • Instance can be terminated
# • State can become stale
# • No clear cleanup on restart

while True:
    await queue.get()  # Forever...
```

### Flaw 5: Mixing Async and Threading

```python
# Thread-based code calling async code:
asyncio.to_thread(self._queue_repository.dequeue)  # DB in thread

# Async code calling thread-based:
await asyncio.to_thread(self._instance_repository.get, instance_id)

# Problem: Timing issues, blocking the event loop
# Solution: Be consistent - either all async or all sync with thread pool
```

---

## New Architecture: Database as Single Source of Truth

### Core Principle

**Database is the ONLY source of truth. In-memory state is derived from database, never the reverse.**

```
┌─────────────────────────────────────────────────────────────────────┐
│              NEW: DATABASE AS SINGLE SOURCE OF TRUTH                  │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │                         DATABASE                             │    │
│  │                                                             │    │
│  │  ┌─────────────────┐  ┌─────────────────┐                 │    │
│  │  │  Instance        │  │  Message         │                 │    │
│  │  │  ─────────────── │  │  ─────────────── │                 │    │
│  │  │  id              │  │  id              │                 │    │
│  │  │  status          │  │  instance_id     │                 │    │
│  │  │  parent_id       │  │  status          │                 │    │
│  │  │  children[]     │  │  type            │                 │    │
│  │  │  last_activity  │  │  payload         │                 │    │
│  │  │  version (ETag) │  │  created_at      │                 │    │
│  │  └─────────────────┘  └─────────────────┘                 │    │
│  │                                                             │    │
│  │  ┌─────────────────┐  ┌─────────────────┐                  │    │
│  │  │  Task           │  │  Event          │                  │    │
│  │  │  ─────────────── │  │  ─────────────── │                  │    │
│  │  │  id              │  │  id              │                  │    │
│  │  │  instance_id     │  │  instance_id     │                  │    │
│  │  │  status          │  │  type            │                  │    │
│  │  │  created_at      │  │  data            │                  │    │
│  │  │  completed_at    │  │  delivered       │                  │    │
│  │  └─────────────────┘  └─────────────────┘                  │    │
│  │                                                             │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                              │                                        │
│                              │                                        │
│                              ▼                                        │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │                   EVENT HANDLERS                             │    │
│  │  Workers query database, never hold state                    │    │
│  │  All state changes are atomic database transactions          │    │
│  └─────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────┘
```

---

## New Data Model

### Instance Table (Enhanced)

```sql
CREATE TABLE instance (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    agent_dir TEXT NOT NULL,
    parent_id TEXT REFERENCES instance(id),
    status TEXT NOT NULL DEFAULT 'idle',  -- idle | running | waiting_children | completed | failed | terminated
    
    -- Explicit child tracking
    children TEXT[] DEFAULT '{}',  -- Array of child instance IDs
    
    -- Activity tracking (for watchdog)
    last_activity_at TIMESTAMP,
    
    -- Version for optimistic locking
    version INTEGER DEFAULT 1,
    
    -- Metadata
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_instance_parent ON instance(parent_id);
CREATE INDEX idx_instance_status ON instance(status);
```

### Message Table (Enhanced)

```sql
CREATE TABLE message (
    id TEXT PRIMARY KEY,
    instance_id TEXT NOT NULL REFERENCES instance(id),
    
    -- Message type for routing
    type TEXT NOT NULL,  -- human | agent | system | completion_report | error_report
    
    -- Payload
    content TEXT NOT NULL,
    metadata JSONB DEFAULT '{}',  -- child_instance_id, error_type, etc.
    
    -- Status
    status TEXT NOT NULL DEFAULT 'pending',  -- pending | processing | completed | failed
    
    -- Priority
    priority INTEGER DEFAULT 1,  -- 0 = system, 1 = user
    
    -- Source tracking
    source TEXT,  -- "api", "telegram:user:123", "report:{instance_id}"
    root_source TEXT,  -- Original external source for cascade
    
    -- Processing tracking
    processing_task_id TEXT,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    
    -- Retry
    retry_count INTEGER DEFAULT 0,
    last_error TEXT,
    
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_message_instance ON message(instance_id);
CREATE INDEX idx_message_status ON message(status);
CREATE INDEX idx_message_priority ON message(instance_id, status, priority, created_at);
```

### Task Table (New)

```sql
CREATE TABLE task (
    id TEXT PRIMARY KEY,
    
    -- What this task does
    type TEXT NOT NULL,  -- process_message | send_report | cleanup
    
    -- Target
    instance_id TEXT REFERENCES instance(id),
    message_id TEXT REFERENCES message(id),
    
    -- Status
    status TEXT NOT NULL DEFAULT 'pending',  -- pending | running | completed | failed
    
    -- Who is running it
    worker_id TEXT,  -- For debugging
    
    -- Timestamps
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    failed_at TIMESTAMP,
    
    -- Result
    result JSONB,
    error TEXT
);

CREATE INDEX idx_task_status ON task(status);
CREATE INDEX idx_task_instance ON task(instance_id);
```

### Event Table (New)

```sql
CREATE TABLE event (
    id SERIAL PRIMARY KEY,
    
    instance_id TEXT NOT NULL REFERENCES instance(id),
    message_id TEXT REFERENCES message(id),
    
    type TEXT NOT NULL,  -- message_received | processing_started | processing_completed | 
                         -- child_completed | instance_completed | error
    
    data JSONB DEFAULT '{}',
    
    -- Delivery tracking for SSE
    delivered BOOLEAN DEFAULT FALSE,
    delivered_at TIMESTAMP,
    
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_event_instance ON event(instance_id);
CREATE INDEX idx_event_undelivered ON event(delivered) WHERE delivered = FALSE;
```

---

## New Architecture: Worker Pool Pattern

### Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           WORKER POOL ARCHITECTURE                           │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                         DATABASE (Source of Truth)                    │   │
│  │                                                                      │   │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐           │   │
│  │  │ Instance │  │ Message  │  │   Task   │  │  Event   │           │   │
│  │  └──────────┘  └──────────┘  └──────────┘  └──────────┘           │   │
│  │                                                                      │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                    │                                        │
│         ┌──────────────────────────┼──────────────────────────┐           │
│         │                          │                          │           │
│         ▼                          ▼                          ▼           │
│  ┌─────────────┐           ┌─────────────┐           ┌─────────────┐     │
│  │  WORKER 1   │           │  WORKER 2   │           │  WORKER N   │     │
│  │              │           │              │           │              │     │
│  │ Poll: 0.5s   │           │ Poll: 0.5s   │           │ Poll: 0.5s   │     │
│  │              │           │              │           │              │     │
│  │ SELECT ...   │           │ SELECT ...   │           │ SELECT ...   │     │
│  │ FOR UPDATE   │           │ FOR UPDATE   │           │ FOR UPDATE   │     │
│  │ SKIP LOCKED  │           │ SKIP LOCKED  │           │ SKIP LOCKED  │     │
│  └─────────────┘           └─────────────┘           └─────────────┘     │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                      SSE STREAMING (Read Events)                     │   │
│  │   SELECT * FROM event WHERE instance_id = ? AND delivered = FALSE   │   │
│  │   Stream to client, mark as delivered                                │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Key Design Decisions

1. **Workers poll database** - No persistent consumers, no event loop state
2. **FOR UPDATE SKIP LOCKED** - Database handles concurrency atomically
3. **Tasks are explicit** - Not implicit via queue state
4. **Events are stored** - SSE reads from DB, not in-memory
5. **Instance status is explicit** - Not inferred from queue state

---

## Message Processing Flow (New)

### Step 1: Enqueue Message

```
┌─────────────────────────────────────────────────────────────────────┐
│ POST /instances/{id}/messages                                       │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│                        ENQUEUE_MESSAGE                                │
│                                                                      │
│  BEGIN TRANSACTION                                                   │
│    1. INSERT INTO message (...) VALUES (...)                         │
│    2. INSERT INTO task (type='process_message', message_id=?)      │
│    3. UPDATE instance SET status='running', last_activity=NOW()     │
│  COMMIT                                                              │
│                                                                      │
│  RETURN message_id                                                   │
└─────────────────────────────────────────────────────────────────────┘
```

**Key**: Message and Task are inserted atomically. No race conditions.

### Step 2: Worker Picks Up Task

```
┌─────────────────────────────────────────────────────────────────────┐
│                        WORKER POLL LOOP                               │
│                                                                      │
│  WHILE running:                                                      │
│    BEGIN TRANSACTION                                                 │
│      SELECT * FROM task                                              │
│        WHERE status = 'pending'                                      │
│        ORDER BY created_at ASC                                        │
│        LIMIT 1                                                       │
│        FOR UPDATE SKIP LOCKED                                        │
│                                                                      │
│      IF task found:                                                  │
│        UPDATE task SET status='running', worker_id=? WHERE id=?     │
│      ELSE:                                                           │
│        COMMIT (release lock)                                         │
│        SLEEP 0.5s                                                    │
│        CONTINUE                                                      │
│    COMMIT                                                            │
│                                                                      │
│    IF task found:                                                    │
│      PROCESS_TASK(task)                                              │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

**Key**: `FOR UPDATE SKIP LOCKED` ensures only ONE worker picks up a task.

### Step 3: Process Message

```
┌─────────────────────────────────────────────────────────────────────┐
│                      PROCESS_MESSAGE_TASK                            │
│                                                                      │
│  BEGIN TRANSACTION                                                   │
│    1. SELECT * FROM message WHERE id = task.message_id               │
│    2. UPDATE message SET status='processing', started_at=NOW()     │
│    3. UPDATE instance SET last_activity=NOW()                      │
│  COMMIT                                                              │
│                                                                      │
│  -- Now run the graph (outside transaction, can be slow)            │
│  result = await run_langgraph(message.instance_id, message.content) │
│                                                                      │
│  BEGIN TRANSACTION                                                   │
│    4. UPDATE message SET status='completed', completed_at=NOW()    │
│    5. INSERT INTO event (type='processing_completed', ...)        │
│    6. UPDATE task SET status='completed'                           │
│  COMMIT                                                              │
│                                                                      │
│  -- Check if instance should send completion report                 │
│  CHECK_CHILD_COMPLETION(message.instance_id)                        │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

### Step 4: Check Child Completion (NEW LOGIC)

```
┌─────────────────────────────────────────────────────────────────────┐
│                     CHECK_CHILD_COMPLETION                           │
│                                                                      │
│  BEGIN TRANSACTION                                                   │
│    1. SELECT * FROM instance WHERE id = instance_id                 │
│                                                                      │
│    -- Is this a child instance?                                     │
│    IF instance.parent_id IS NULL:                                    │
│      -- Root instance, we're done                                   │
│      COMMIT                                                         │
│      RETURN                                                          │
│                                                                      │
│    -- Is this instance completed successfully?                      │
│    IF message.status != 'completed':                                 │
│      -- Failed or still processing                                  │
│      COMMIT                                                         │
│      RETURN                                                          │
│                                                                      │
│    -- Are there pending messages for this instance?                  │
│    SELECT COUNT(*) FROM message                                      │
│      WHERE instance_id = instance_id                                 │
│        AND status IN ('pending', 'processing')                      │
│                                                                      │
│    IF count > 0:                                                    │
│      -- Still has work                                              │
│      COMMIT                                                         │
│      RETURN                                                          │
│                                                                      │
│    -- Queue is empty - instance is truly done                       │
│    -- But check if we already sent completion report                │
│    SELECT * FROM message                                            │
│      WHERE instance_id = instance.parent_id                          │
│        AND source = 'report:{instance_id}'                           │
│        AND status IN ('pending', 'completed')                       │
│                                                                      │
│    IF exists:                                                        │
│      -- Already sent report!                                         │
│      COMMIT                                                         │
│      RETURN                                                          │
│                                                                      │
│    -- NEW: Update instance status to 'completed' atomically        │
│    UPDATE instance SET status='completed' WHERE id=instance_id     │
│                                                                      │
│    -- Create completion report message                               │
│    INSERT INTO message (type='completion_report', source=?)         │
│                                                                      │
│    -- Create task to send the report                                 │
│    INSERT INTO task (type='deliver_message', message_id=?)          │
│                                                                      │
│  COMMIT                                                              │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

**Key**: Completion check is atomic with status update. No race.

---

## Child Instance Lifecycle (New)

### Spawning a Child

```
┌─────────────────────────────────────────────────────────────────────┐
│                    SPAWN_CHILD_INSTANCE                              │
│                                                                      │
│  BEGIN TRANSACTION                                                   │
│    1. INSERT INTO instance (parent_id=parent, status='idle')       │
│    2. UPDATE parent SET children = array_append(children, child_id) │
│    3. INSERT INTO event (type='child_spawned', ...)                │
│  COMMIT                                                              │
│                                                                      │
│  RETURN child_instance_id                                           │
└─────────────────────────────────────────────────────────────────────┘
```

### Parent Waiting for Children

```
┌─────────────────────────────────────────────────────────────────────┐
│                   WAITING_FOR_CHILDREN STATE                          │
│                                                                      │
│  When parent processes a message that spawns children:              │
│                                                                      │
│  BEGIN TRANSACTION                                                   │
│    1. UPDATE instance SET status='waiting_children'                 │
│    2. INSERT INTO event (type='waiting_children', ...)             │
│  COMMIT                                                              │
│                                                                      │
│  Parent's queue continues processing messages                        │
│  When children complete, they send reports to parent                │
│                                                                      │
│  Parent receives completion_report → processes it                   │
│  When all children done AND parent queue empty:                      │
│    UPDATE instance SET status='completed'                            │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

### Child Sends Completion Report

```
┌─────────────────────────────────────────────────────────────────────┐
│                 CHILD_COMPLETE → SEND_REPORT                          │
│                                                                      │
│  Triggered by: CHECK_CHILD_COMPLETION when child's queue empties    │
│                                                                      │
│  BEGIN TRANSACTION                                                   │
│    1. UPDATE instance SET status='completed'                        │
│                                                                      │
│    2. -- Get last assistant message from child's graph              │
│    last_msg = get_last_message(child_instance_id)                   │
│                                                                      │
│    3. INSERT INTO message (                                           │
│        instance_id = parent_id,                                      │
│        type = 'completion_report',                                   │
│        source = 'report:{child_instance_id}',                       │
│        content = last_msg.content                                    │
│      )                                                               │
│                                                                      │
│    4. -- CRITICAL: Update parent's expected children count           │
│    SELECT children FROM instance WHERE id = parent_id                │
│    -- Remove this child from parent's waiting list                   │
│    UPDATE instance SET                                              │
│      children = array_remove(children, child_id),                   │
│      waiting_for = waiting_for - 1                                   │
│      WHERE id = parent_id                                            │
│                                                                      │
│    5. -- If parent has no more waiting children:                    │
│    SELECT waiting_for FROM instance WHERE id = parent_id             │
│    IF waiting_for = 0:                                               │
│      -- Check if parent's queue is also empty                        │
│      SELECT COUNT(*) FROM message WHERE instance_id = parent_id      │
│        AND status IN ('pending', 'processing')                        │
│      IF count = 0:                                                   │
│        UPDATE instance SET status = 'completed'                      │
│        INSERT INTO event (type='instance_completed', ...)            │
│                                                                      │
│  COMMIT                                                              │
│                                                                      │
│  -- Create task to deliver report to parent                         │
│  INSERT INTO task (type='deliver_message', message_id=?)            │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

---

## SSE Event Streaming (New)

### Event Delivery

```
┌─────────────────────────────────────────────────────────────────────┐
│                      SSE EVENT DELIVERY                               │
│                                                                      │
│  GET /instances/{id}/events                                          │
│  │                                                                    │
│  │  Start Long Poll                                                 │
│  │                                                                    │
│  ▼                                                                    │
│  ┌─────────────────────────────────────────────────────────────────┐ │
│  │                     LONG POLL LOOP                               │ │
│  │                                                                  │ │
│  │  WHILE client_connected:                                         │ │
│  │    BEGIN TRANSACTION (READ ONLY)                                 │ │
│  │      SELECT * FROM event                                         │ │
│  │        WHERE instance_id = ?                                     │ │
│  │          AND delivered = FALSE                                   │ │
│  │        ORDER BY created_at ASC                                    │ │
│  │        LIMIT 10                                                  │ │
│  │    COMMIT                                                        │ │
│  │                                                                  │ │
│  │    IF events:                                                    │ │
│  │      FOR event IN events:                                        │ │
│  │        yield f"event: {event.type}\ndata: {json.dumps(event)}\n\n" │
│  │                                                                  │ │
│  │      BEGIN TRANSACTION                                           │ │
│  │        UPDATE event SET delivered = TRUE, delivered_at = NOW()   │ │
│  │          WHERE id IN (event ids)                                  │ │
│  │      COMMIT                                                      │ │
│  │                                                                  │ │
│  │    ELSE:                                                         │ │
│  │      -- No events, wait with polling                             │ │
│  │      SLEEP 1s                                                     │ │
│  │                                                                  │ │
│  └─────────────────────────────────────────────────────────────────┘ │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

**Key**: Events are stored in database, SSE just reads and marks delivered. No lost events.

---

## Recovery & Restart Handling

### On Application Restart

```
┌─────────────────────────────────────────────────────────────────────┐
│                       RESTART RECOVERY                               │
│                                                                      │
│  1. Load all instances with status IN ('running', 'waiting_children') │
│                                                                      │
│  2. For each instance:                                              │
│     a. Check for orphaned processing tasks                           │
│        SELECT * FROM task WHERE status='running' AND worker_id IS NULL│ │
│        -- This shouldn't happen, but handle it                       │
│                                                                      │
│     b. Check for orphaned messages                                   │
│        SELECT * FROM message WHERE status='processing'               │
│        UPDATE message SET status='pending' WHERE ...                 │
│        -- Allow them to be re-processed                              │
│                                                                      │
│     c. Re-create worker pool (workers start polling)                  │
│                                                                      │
│  3. Clients reconnect via SSE, start receiving events              │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

**Key**: Database state survives restart. Workers can resume from where they left off.

### On Worker Crash

```
┌─────────────────────────────────────────────────────────────────────┐
│                        WORKER CRASH RECOVERY                         │
│                                                                      │
│  Worker dies while processing:                                       │
│                                                                      │
│  1. Worker process dies → task left in 'running' state               │
│                                                                      │
│  2. Other workers keep polling, skip this task (worker_id mismatch) │
│                                                                      │
│  3. Recovery task runs periodically:                                │
│     SELECT * FROM task WHERE status='running'                         │
│       AND started_at < NOW() - INTERVAL '5 minutes'                 │
│                                                                      │
│     UPDATE task SET status='pending', worker_id=NULL                 │
│     UPDATE message SET status='pending' WHERE id = task.message_id  │
│                                                                      │
│  4. Task becomes available for other workers                         │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

---

## How New Architecture Eliminates Bugs

### Bug 1: Child Report Race Condition

**OLD**: Check queue empty AFTER dequeue, gap in between

```python
# OLD - RACE!
msg = dequeue()
if msg is None:
    send_report()  # But what if new message arrived?
```

**NEW**: Completion is explicit, atomic with status update

```python
# NEW - IMPOSSIBLE TO RACE!
UPDATE instance SET status='completed' WHERE id=? AND status='running'
# If this returns 0 rows, instance was already completed
# No race possible
```

### Bug 2: Duplicate Completion Report

**OLD**: No idempotency check, can send twice

```python
# OLD - CAN SEND TWICE!
send_completion_report()  # No check
send_completion_report()  # Called again = duplicate
```

**NEW**: Instance status prevents duplicates

```python
# NEW - IMPOSSIBLE TO DUPLICATE!
UPDATE instance SET status='completed' WHERE id=? AND status != 'completed'
-- Only one succeeds, others return 0 rows
```

### Bug 3: Consumer Deadlock

**OLD**: Consumer in memory, dies with app, orphaned state

```python
# OLD - STATE LOST!
self._consumer_tasks[id] = asyncio.create_task(...)  # Lost on restart!
```

**NEW**: Workers are stateless, poll from database

```python
# NEW - STATE SURVIVES!
# Worker is just a process, no state
# Database is the truth
# On restart, workers just start polling again
```

### Bug 4: Message Lost

**OLD**: Frontend optimistic update, backend async

```typescript
// OLD - CAN LOSE MESSAGE!
messages.update([...tempMessage])  // In memory only
api.sendMessage(...)                // If this fails before checkpoint...
// User's message is lost
```

**NEW**: Message is in database immediately

```typescript
// NEW - MESSAGE PERSISTS FIRST!
const response = await api.sendMessage(...)
// Response confirms message is in database
// Frontend shows "sending..." state
// SSE event confirms receipt
```

### Bug 5: Human Message Not Appearing

**OLD**: Temp ID, async checkpoint

```typescript
// OLD - ID MISMATCH!
message_id: `temp-${Date.now()}`  // Temp ID
// Later: API returns message with real UUID
// Frontend doesn't know they're the same
```

**NEW**: Real ID returned immediately

```typescript
// NEW - CONSISTENT ID!
const response = await api.sendMessage(...)
// Response contains the real message_id from database
// Frontend uses real ID from the start
```

---

## Implementation Phases

### Phase 1: Database Schema (Low Risk)

```sql
-- Add new tables alongside existing
-- No changes to existing code
-- Just add: task, event tables
-- Add columns: instance.waiting_for, instance.status='waiting_children'
```

### Phase 2: Worker Pool (Medium Risk)

```python
# Keep existing consumer pattern
# Add parallel worker pool that handles new tasks
# Gradually migrate message processing to workers
```

### Phase 3: Migrate Message Flow (Medium Risk)

```python
# New enqueue creates task
# Workers pick up tasks
# Gradually deprecate consumer pattern
```

### Phase 4: Migrate SSE (Low Risk)

```python
# Keep existing SSE working
# Add new event-based SSE as alternative
# Switch clients to new SSE
```

### Phase 5: Remove Old Code (Low Risk)

```python
# Remove consumer pattern
# Remove in-memory state (self._processing, etc.)
# Simplify manager to only handle graph execution
```

---

## Comparison: Old vs New

| Aspect | Old Architecture | New Architecture |
|--------|------------------|------------------|
| **State Source** | In-memory + DB (conflict) | Database only |
| **Concurrency** | asyncio.Queue + locks | Database transactions |
| **Consumer** | Persistent loop | Stateless workers |
| **Completion** | Implicit (queue empty) | Explicit (status field) |
| **Idempotency** | Manual checks | Atomic UPDATE |
| **Recovery** | Lost state | Database persists |
| **Events** | In-memory broadcaster | Database + SSE |
| **Race Conditions** | Multiple | None |

---

## Open Questions for Discussion

1. **Polling Interval**: Workers poll every 0.5s. Too slow? Too fast?
   - Option: Use PostgreSQL LISTEN/NOTIFY for push instead of poll

2. **Worker Count**: How many workers per application instance?
   - Option: Auto-scale based on pending task count

3. **Task Priority**: Should some tasks skip the queue?
   - Option: Priority tiers (system > user > background)

4. **Child Count Limits**: Should we track pending children?
   - Current: Just array of child IDs
   - Option: `waiting_for` counter (increments on spawn, decrements on complete)

5. **Checkpoint Strategy**: Keep LangGraph checkpoints or move to our own?
   - Current: AsyncSqliteSaver
   - Option: Store graph state in message.metadata

6. **Rollback Strategy**: What if task fails partway?
   - Current: Retry with backoff
   - Option: Compensating transactions

---

## Files to Change

| File | Changes |
|------|---------|
| `daemon/repositories/instance/models.py` | Add status field, waiting_for, version |
| `daemon/repositories/message_queue/models.py` | Rename to message, add type, task tracking |
| `daemon/repositories/task/` | New task repository |
| `daemon/repositories/event/` | New event repository |
| `daemon/manager.py` | Replace consumer with worker pool |
| `daemon/worker.py` | New worker implementation |
| `daemon/api.py` | Update to use new task-based enqueue |
| `daemon/sse.py` | Update to read from event table |

---

## Success Metrics

After implementation:

- ✅ No race conditions between child completion and parent notification
- ✅ No duplicate completion reports
- ✅ No lost messages on restart
- ✅ No consumer deadlocks
- ✅ Human messages appear in UI immediately
- ✅ SSE events are never dropped
- ✅ Restart recovery completes in < 5 seconds
