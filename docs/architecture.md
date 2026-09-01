# Agents Ensemble Architecture

> **Note (2026-06-24):** Post-cleanup architecture. The Dependency Bus is the **sole** parent-waits-for-children mechanism; the CorrelationManager is deleted. The `USE_LEGACY_WAITING_FOR_CASCADE`, `USE_LEGACY_JOBQUEUE_DISPATCH`, and `DEBUG_COMPLETION_INVARIANT` flags are all removed. `MessageJobHandler` is deleted. The JobQueue is scheduling vocabulary only. Message dispatch is unified into a single `enqueue_message()` function with a `dispatch_path` parameter. See the [Completion Architecture](#completion-architecture) summary below, and [`docs/architecture/message-processing-and-correlation.md`](architecture/message-processing-and-correlation.md) for the current authoritative reference.
>
> **Update (2026-09-01):** since JAFP (2026-07), the Job is the **single public work
> primitive** — every public entry point creates a JobItem; only internal paths bypass
> the queue. "Scheduling vocabulary only" below refers to internal message dispatch.
> See [`docs/job-task-system.md`](job-task-system.md) for the canonical job-task model.

## Core Design Philosophy

**System orchestrates, agents execute.**

The agent framework manages lifecycle, scheduling, and persistence. Agents are pure: receive messages, produce responses, spawn children. No blocking, no tracking, no complex state machines.

---

## Architecture Decisions

### 1. Fire-and-Forget Messaging (vs OpenClaw's Blocking Wait)

| Aspect | OpenClaw | Ensemble |
|--------|----------|----------|
| Pattern | `agent.wait` blocks parent | Message queue, parent continues |
| Resources | Blocked threads holding memory | Threads freed, work deferred |
| Crash handling | Parent crash = child orphaned | System recovers, child completes |
| Complexity | Agent manages child lifecycle | System manages lifecycle |

**Decision**: Fire-and-forget is correct for long-running children.

**Rationale**: 
- Parent spawns child, continues other work
- Hours between spawn and completion = fine
- Parent crash = child still completes independently
- System (not agent) manages timeout, retry, recovery

**Don't add**: `wait_for_instance()` — reintroduces blocking, defeats separation of concerns.

---

### 2. SQLite-Backed Persistence (vs In-Memory Sessions)

| Aspect | OpenClaw | Ensemble |
|--------|----------|----------|
| Crash recovery | Session lost | LangGraph checkpointer survives |
| Message durability | In-memory, lost on crash | SQLite persists |
| Worker fault tolerance | N/A (single process) | Workers crash, DB ensures recovery |
| Long idle periods | Process must stay alive | Message persists, child idle |

**Decision**: All state to SQLite.

**Rationale**:
- MessageQueue: pending work survives restarts
- Task: worker claims atomically, can crash mid-work
- Instance metadata: hierarchy, status, parent-child links
- LangGraph checkpointer: conversation state persisted per thread_id

---

### 3. Worker Pool (vs Single-Process Event Loop)

| Aspect | OpenClaw | Ensemble |
|--------|----------|----------|
| Concurrency | Async/await (single thread) | Thread pool (4 workers) |
| Memory isolation | Shared process | Separate threads per task |
| Long-running tasks | Blocks event loop | Background execution |
| CPU contention | Shared | Configurable concurrency limit |

**Decision**: Stateless worker threads with DB-backed coordination.

**Rationale**:
- Workers are disposable — crash and another picks up
- No shared state = no race conditions
- Long tasks run in background, don't block
- Scale by adding workers, not threads

---

### 4. Markdown-Based Agents (vs Code-Based Plugins)

| Aspect | OpenClaw | Ensemble |
|--------|----------|----------|
| Agent creation | TypeScript plugin SDK | Write markdown files |
| Non-dev access | Requires coding | Anyone can create agents |
| Flexibility | Typed interfaces | Natural language prompts |
| Iteration speed | Compile + deploy | Edit file + reload |

**Decision**: Markdown-defined agents.

**Rationale**:
- Prompts are the API — no typing, no schemas
- Agents defined by files: soul.md, rule.md, tools.md, etc.
- Loader composes system prompt from files
- Easy to version control, branch, test

---

## Agent-to-Agent Messaging Architecture

> **Current model (2026-06-24):** The agents-ensemble uses a single-dispatcher, DB-backed completion model. The Dependency Bus (`daemon/services/dependency_bus.py`) is the **sole** parent-waits-for-children mechanism. The CorrelationManager, `waiting_for`, `children`, and `instance_hierarchy`-as-control-flow are all gone; `waiting_for` and `children` columns have been dropped from the SQLModel. The unified `MessageProcessingPipeline` and the per-instance `asyncio.Lock` `ExecutionGate` are unchanged. See [`docs/architecture/message-processing-and-correlation.md`](architecture/message-processing-and-correlation.md) for the full reference.

### Flow Overview

```
┌────────────────────────────────────────────────────────────────────┐
│                        AGENT-TO-AGENT FLOW                         │
├────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  1. Parent spawns child                                             │
│     spawn_instance("developer") → returns instance_id                  │
│                                                                     │
│  2. Parent sends task                                               │
│     send_message(child_id, "implement login")                       │
│     └─> MessageQueue DB row created (status=READY)                 │
│     └─> Task DB row created (status=PENDING)                       │
│     └─> DependencyBus.watch() writes dependency_watchers row      │
│                                                                     │
│  3. Worker pool picks up task                                       │
│     claim_task() → atomic UPDATE-RETURNING                         │
│     └─> Worker executes via TaskProcessor                          │
│     └─> graph.astream() runs child LangGraph                       │
│                                                                     │
│  4. Child completes                                                 │
│     Child LangGraph returns → completion event                    │
│     └─> dependency_bus.emit_terminal() (sole completion authority) │
│     └─> Watcher atomically transitions PENDING → FIRED            │
│     └─> FollowUp task enqueued onto parent via Task row            │
│                                                                     │
│  5. Parent receives report                                          │
│     Worker processes parent's task                                  │
│     Parent receives COMPLETION_REPORT as message                    │
│                                                                     │
└────────────────────────────────────────────────────────────────────┘
```

### Key Design Principles

1. **Atomic claiming**: `UPDATE-RETURNING` prevents worker race conditions
2. **Idempotent completion**: Won't send duplicate COMPLETION_REPORT
3. **Sole completion authority**: The DependencyBus (DB-backed `dependency_watchers` table) is the only mechanism that answers "is the parent done?". The CorrelationManager is deleted.
4. **Fire-and-forget**: Parent doesn't block, system handles timing
5. **Crash-safe**: Workers can die, tasks retried, state preserved; bus watcher state is durable in the `dependency_watchers` table

### Message Types

| Type | Purpose | Source |
|------|---------|--------|
| `HUMAN` | User input | API, Telegram, etc. |
| `AGENT` | Agent-to-agent | `send_message()` tool |
| `SYSTEM` | System events | Internal |
| `COMPLETION_REPORT` | Child finished | `_check_child_completion()` |

### Instance Hierarchy

```python
class Instance:
    instance_id: str      # Unique per instance
    agent_id: str         # Which agent type
    parent_id: str | None # Direct parent
    status: str           # idle | running | waiting_children | paused | completed | error | terminated
    # NOTE: `waiting_for` and `children` columns dropped from the SQLModel.
    # Parent-child correlation lives in:
    #   - DependencyBus: dependency_watchers table (in-flight FollowUps)
    #   - instance_hierarchy junction table: canonical parent-child working set
```

> **Parent-child correlation** is now authoritative in the **DependencyBus** (`daemon/services/dependency_bus.py`). The `waiting_for` and `children` columns have been removed from the SQLModel; the canonical parent-child working set is the `instance_hierarchy` junction table. See [`docs/architecture/message-processing-and-correlation.md`](architecture/message-processing-and-correlation.md) and [`docs/architecture/completion-authority.md`](architecture/completion-authority.md).

---

## Comparison: Ensemble vs OpenClaw

### OpenClaw: Gateway-Centric

```
Channels → Gateway (WebSocket) → Plugin System → Agent
```

- Single Node.js process
- In-process children
- `agent.wait` for blocking
- Session keys for isolation
- 110+ TypeScript extensions
- Multi-platform (20+ channels)

### Ensemble: Agent-Centric

```
Message → Queue → Worker Pool → LangGraph → Tools → Child Agents
```

- Python + LangGraph
- Out-of-process children (threads)
- Fire-and-forget messaging
- SQLite checkpointer
- Markdown-defined agents
- Text-only (currently)

### When to Choose Each

| Use Case | Ensemble | OpenClaw |
|----------|----------|----------|
| Long-running children (hours) | ✅ | ❌ |
| Crash recovery critical | ✅ | ❌ |
| Multi-channel messaging | ❌ | ✅ |
| Non-dev agent creation | ✅ | ❌ |
| Real-time streaming | Moderate | ✅ |
| Production messaging bot | ❌ | ✅ |
| Research/prototype | ✅ | ✅ |

---

## Potential Enhancements

### Priority 1: Structured Completion Reports

**Current**: COMPLETION_REPORT is plain text.

**Proposed**: Structured metadata.

```python
{
    "type": "completion_report",
    "source_instance_id": "child-abc",
    "parent_instance_id": "parent-xyz",
    "task_description": "Implement login",
    "status": "completed",  # or "error", "cancelled"
    "content": "...result...",
    "error": null,
    "metadata": {
        "duration_seconds": 45,
        "tool_calls": [...],
        "compaction_occurred": false,
        "tokens_used": 1234
    }
}
```

**Benefit**: Agent can parse report programmatically, not just display.

---

### Priority 2: Progress Checkpoints

**Use case**: Child runs for 2 hours, parent wants status.

```python
# Child tool
@tool
def checkpoint_progress(
    phase: str,
    progress: float,  # 0.0 - 1.0
    details: str
) -> str:
    """Save progress for parent visibility."""
    # Stores in Instance metadata or separate table
    # Parent can query: get_child_progress(child_id)
```

**Benefit**: Observable long-running tasks.

---

### Priority 3: Heartbeat / Liveness

**Use case**: Detect stuck children.

```python
# System monitors:
# 1. Task.last_activity_at updated during graph.astream()
# 2. TimeoutMonitor kills stalled tasks
# 3. Event: "child_heartbeat" every N minutes for long tasks

class ChildLiveness:
    instance_id: str
    last_heartbeat: datetime
    expected_interval_seconds: int = 300
```

**Benefit**: Know if child is alive vs stuck vs crashed.

---

### Priority 4: Pause / Resume

**Use case**: Parent needs to provide input mid-task.

```python
@tool
def pause_child(child_id: str, reason: str) -> str:
    """Pause child, waiting for input."""

@tool  
def resume_child(child_id: str, input: str) -> str:
    """Resume paused child with input."""

# Child receives PAUSE message, saves checkpoint, stops
# Parent provides input via resume
# Child resumes from checkpoint
```

**Benefit**: Interactive multi-agent workflows.

---

### Priority 5: Hierarchical Timeout

**Use case**: "If coding > 1hr, stop and report progress."

```python
# Config per instance or per spawn
spawn_instance(
    agent_id="developer",
    timeout_seconds=3600,  # 1 hour
    timeout_action="report_progress"  # or "cancel", "continue"
)

# Phases
timeout_config = {
    "coding": 3600,
    "testing": 1800,
    "deployment": 900
}
```

**Benefit**: Control runaway tasks.

---

### Priority 6: Cancellation Propagation

**Current**: `cancel_instance()` only affects direct child.

**Proposed**: Cascade to grandchildren.

```python
# Current: cancels only instance_id
cancel_instance("child-123")

# Proposed: cancels tree
cancel_instance("child-123", cascade=True)
# → cancels child-123
# → cancels child-123's children
# → emits cancellation events
```

**Benefit**: Clean shutdown of subtrees.

---

### Priority 7: Non-Blocking Collect

**Use case**: "Check if any of these children completed."

```python
@tool
def collect_completion_reports(
    child_ids: list[str],
    timeout_seconds: int = 300
) -> list[CompletionReport]:
    """Non-blocking check for completed children.
    
    Returns reports for completed children only.
    Returns empty if none done yet.
    """

# Agent can poll without blocking
reports = collect_completion_reports(["child-1", "child-2", "child-3"])
if reports:
    for report in reports:
        handle(report)
else:
    # Continue with other work
    pass
```

**Benefit**: Agent can check status without waiting.

---

## Design Trade-offs

### Chosen: Fire-and-forget

**Pros**:
- System manages timing, not agent
- Resources freed immediately
- Scales to N concurrent children
- Parent crash doesn't affect children

**Cons**:
- Agent must track children implicitly (memory/tool calls)
- No synchronous result available
- Harder to debug ("where's my result?")

**Verdict**: Correct for long-running systems.

---

### Chosen: SQLite Checkpointing

**Pros**:
- Crash recovery
- Persists conversation history
- Cheap to implement

**Cons**:
- Slower than in-memory
- Not horizontal scalable (single file)
- Compaction complexity

**Verdict**: Correct for single-node deployment.

---

### Chosen: Markdown Agents

**Pros**:
- Non-dev friendly
- Version controlled
- Fast iteration

**Cons**:
- No type safety
- Hard to test systematically
- Prompt engineering is fragile

**Verdict**: Correct for flexibility over rigor.

---

## Future Considerations

### Horizontal Scaling

Current: Single node, SQLite.

Options:
1. **PostgreSQL** instead of SQLite — minimal code change
2. **Redis** for queues, PostgreSQL for persistence
3. **Celery + workers** — separate queue process
4. **LangGraph Cloud** — managed execution

### Multi-Node Workers

Current: Threads in single process.

Options:
1. **Ray** — distributed actors
2. **Celery** — distributed tasks
3. **Dask** — parallel execution
4. **ASGI workers** — multiple uvicorn processes

### External Runtimes

OpenClaw has ACP mode for Claude CLI, Codex.

Ensemble could add:
1. **subprocess** tool — spawn Claude CLI, capture output
2. **docker** tool — sandboxed code execution
3. **WASM** runtime — lightweight isolation

---

## Completion Architecture

The agents-ensemble uses a single-dispatcher, DB-backed completion model:

1. **Dispatcher**: WorkerPool (4 threads) is the sole execution path for all work (messages, tasks). The JobQueue is scheduling vocabulary only (priority, queue management, project scoping) — for *internal* dispatch; public work enters as JobItems (JAFP, see [`docs/job-task-system.md`](job-task-system.md)).

2. **Sole Completion Authority**: The Dependency Bus (`daemon/services/dependency_bus.py`) is the **sole** parent-waits-for-children mechanism. When a parent sends a message to a child, a `dependency_watchers` row is written. When the child's task reaches a terminal event, the bus atomically transitions the watcher `PENDING → FIRED` and enqueues a FollowUp back onto the parent. The bus is DB-backed — watcher state survives restart.

3. **No Rollback Path**: The CorrelationManager (in-memory `_pending` dict + generation counter) is **deleted**. There is no kill-switch flag — `USE_LEGACY_WAITING_FOR_CASCADE`, `USE_LEGACY_JOBQUEUE_DISPATCH`, and `DEBUG_COMPLETION_INVARIANT` have all been removed.

4. **No Legacy Columns**: The `waiting_for` and `children` columns have been dropped from the SQLModel. A migration exists to drop them on existing DBs (`20260621_000002_drop_legacy_completion_columns.sql`); it is operator-applied (manual).

5. **No Lease Stubs**: The `ExecutionGate` is a pure per-instance `asyncio.Lock`. The `LeaseContention`, `LeaseLostError`, `LeaseHolderKind`, and `LeaseContentionReason` classes are deleted. The `instance_execution_leases` table is retained (created at startup as part of released history) but no code writes to it at runtime — the `asyncio.Lock` is the gate, and since the lock provides no contention-return path there is no need for holders/leases to be tracked in the DB.

6. **Unified Message Dispatch**: Message dispatch is consolidated into a single `enqueue_message()` function with a `dispatch_path` parameter (`"workerpool"` or `"jobqueue"`). The legacy `enqueue_message_via_jq` is gone.

---

## Summary

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Messaging | Fire-and-forget | System orchestrates, agents simple |
| Persistence | SQLite | Crash recovery, long idle periods |
| Concurrency | Worker pool | Isolation, crash-safe, background |
| Agents | Markdown | Non-dev friendly, fast iteration |
| State | LangGraph checkpointer | Conversation history persisted |

**Core principle**: System manages lifecycle. Agents are pure functions: input → output.

---

## Appendix: Key Files

### Core orchestration
| Component | File | Purpose |
|-----------|------|---------|
| InstanceManager | `daemon/manager.py` | Core orchestration |
| Instance tools | `daemon/tools/instance.py` | spawn_instance, send_message |
| Worker pool | `daemon/services/worker_pool.py` | Stateless workers |
| Task processor | `daemon/services/task_processor.py` | Route to processors |
| Message queue | `daemon/repositories/message_queue/` | DB persistence |
| Graph | `daemon/graph.py` | LangGraph definition |
| Config | `config.yaml` | All configuration |

### Message processing & correlation (current architecture)
| Component | File | Purpose |
|-----------|------|---------|
| MessageProcessingPipeline | `daemon/services/message_processing_pipeline.py` | 6-stage shared pipeline (gate → process → mark → dispatch → child-check via DependencyBus → error-handle) |
| **DependencyBus** | `daemon/services/dependency_bus.py` | **Sole parent-waits-for-children mechanism. DB-backed `dependency_watchers` table, `watch` / `emit_terminal` / `cancel_for_target` API. Watcher state survives restart by construction.** |
| ExecutionGate | `daemon/services/execution_gate.py` | Per-instance `asyncio.Lock` serializing `graph.astream`; required on ALL paths including resume (Race #5 fix). No Lease stubs. |
| Message processing errors | `daemon/services/message_processing_errors.py` | Shared error side-effects (event write, lifecycle event, parent report, job FAILED) |
