# daemon/

## Responsibility
The daemon is a **persistent multi-session agent orchestration server** that manages LangGraph-based agent sessions with real-time event streaming, message queuing, and hierarchical session support. It exposes a FastAPI REST API for session lifecycle management, message exchange via queue-based async processing, and Server-Sent Events (SSE) for real-time updates.

## Design Patterns

### Core Patterns
- **Event-Driven Architecture**: `EventBroadcaster` implements Observer pattern for pub/sub across sessions; `ResponseDispatcher` routes agent responses back to external sources
- **Message Queue Pattern**: `InputMessageQueue` provides SQLite-backed FIFO queue with priority, retry, and circuit breaker
- **Circuit Breaker**: `SessionCircuitBreaker` prevents cascading failures per session (closed → half_open → open states)
- **Checkpoint/Recovery**: LangGraph `SqliteSaver` persists conversation state; on retry, session resumes from checkpoint instead of re-executing
- **Lazy Loading**: Sessions restored from DB on-demand via `_restore_session()`

### Agent Execution
- **LangGraph StateGraph**: Agent nodes (`agent` → conditional `tools` → `agent` loop)
- **Tool Binding**: LLM bound with tools before graph compilation; retry wrapper via `RunnableRetry`
- **Prompt Composition**: `compose_system_prompt()` aggregates soul.md, rule.md, skills/, tools.md, workflow.md, memory.md

### API Layer
- **FastAPI Lifespan**: `SessionManager` initialized on startup; broadcaster main loop set for thread-safe async
- **Pydantic Validation**: All request/response models in `models.py`

## Data & Control Flow

### Session Lifecycle
```
POST /sessions → spawn_session()
    → load_and_cache_prompt() → create_session_tools()
    → build_session_graph(tools, checkpointer, llm_config, system_prompt, retry_config)
    → save_session_metadata() → sessions[session_id] = (graph, agent_dir)
```

### Message Flow (Async Queue)
```
POST /sessions/{id}/messages → enqueue_message()
    → InputMessageQueue.enqueue() → broadcasts "message_queued"
    → _process_queue() triggered (fire-and-forget Task)
    
Process Loop:
    1. SessionCircuitBreaker.can_execute() check
    2. dequeue() atomically claims next ready message
    3. Broadcasts "status_changed" (processing)
    4. _process_message_with_tracking():
        - ActivityCallbackHandler updates last_activity_at periodically
        - On retry: checkpoint exists? → graph.invoke(None) to resume
        - Otherwise: graph.invoke({"messages": [message]})
    5. On success: ack() → broadcast "completed" → circuit_breaker.record_success()
    6. On failure: record_failure() → if retries left: schedule_retry() → broadcast "status_changed" (retrying)
       else: fail() → broadcast "error"
```

### Event Streaming
```
GET /sessions/{id}/events → EventSourceResponse(event_generator())
    → broadcaster.get_queue(session_id) → yields events via queue.get()
    → Supports Last-Event-ID for reconnection replay
    → Keepalive every 30s
```

### Hierarchical Sessions
- Parent session can spawn child sessions (`parent_id` in `spawn_session()`)
- When child queue empties → `_send_completion_report()` → enqueues report to parent

## Integration Points

### Dependencies
- **FastAPI** + **Uvicorn**: HTTP server
- **LangGraph**: StateGraph orchestration, `SqliteSaver` checkpointer
- **LangChain OpenAI**: LLM client (`ThinkingChatOpenAI` for reasoning extraction)
- **SQLite3**: Session metadata, message queue, source configs
- **Pydantic**: Config and API models
- **sse-starlette**: Server-Sent Events

### Database Tables (`persistence.py`)
- `sessions`: session_id, agent_dir, agent_name, parent_id, status, metadata
- `session_hierarchy`: parent_id → child_id
- `message_queue`: priority FIFO with status lifecycle (ready → processing → completed/failed)
- `source_configs`: Pluggable adapter configs (for external message sources)
- `session_mappings`: external_user_id → agent_session_id mapping

### API Endpoints
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | Liveness probe |
| GET | `/agents` | List available agents (scans `agents/` directory) |
| POST | `/agents` | Create agent from template |
| DELETE | `/agents/{agent_id}` | Soft-delete (move to `_trash`) |
| POST | `/sessions` | Spawn new session |
| GET | `/sessions` | List all sessions |
| GET | `/sessions/{session_id}` | Get session info |
| DELETE | `/sessions/{session_id}` | Terminate session |
| POST | `/sessions/{session_id}/messages` | Enqueue message (async) |
| GET | `/sessions/{session_id}/messages` | Get message history (from checkpoints) |
| GET | `/sessions/{session_id}/messages/{message_id}` | Get queue stats |
| GET | `/sessions/{session_id}/events` | SSE event stream |
| GET | `/` | Serve frontend (SPA fallback) |

### Event Types (SSE)
- `message_queued`: Message enqueued
- `status_changed`: Processing/retrying/completion report
- `content_chunk`: (future) streaming response
- `tool_call`: Tool invocation
- `completed`: Message processed successfully
- `error`: Processing failed

### External Integrations (Sources System)
- `SourceRegistry`: Loads adapters from `source_configs` table
- `ResponseDispatcher`: Subscribes to all broadcast events, routes responses based on `source` field
- `SourceCleanup`: Periodic removal of old mappings and processed messages

## Key Files

- **`manager.py`**: Central orchestrator—`SessionManager` class manages session lifecycle, queue processing, LangGraph invocation, and sources system
- **`api.py`**: FastAPI app with lifespan, CORS, all REST endpoints, and SSE streaming generator
- **`queue.py`**: `InputMessageQueue` (SQLite-backed FIFO), `SessionCircuitBreaker`, `SessionWatchdog` (background stuck-message recovery)
- **`persistence.py`**: SQLite schema, `SqliteSaver` checkpointer, session/message CRUD
- **`graph.py`**: `build_session_graph()` factory—creates compiled LangGraph with agent/tools nodes and retry wrapper
- **`loader.py`**: Prompt composition from markdown files, `PromptCache` with mtime invalidation
- **`events.py`**: `EventBroadcaster` with per-session queues, history for reconnection, global subscriber support
- **`config.py`**: YAML config loading with `${VAR:-default}` substitution, Pydantic validation for all sections
