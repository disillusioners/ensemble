# Implementation Plan: Persistent Multi-Session Agent Daemon

## Dependencies

```
langgraph>=0.3.0
langchain-core>=0.3.14
langchain-openai>=0.2.14
fastapi>=0.115.6
uvicorn[standard]>=0.34.0
pydantic>=2.10.0
pydantic-settings>=2.7.0
sqlite3  # stdlib
watchdog>=6.0.0  # hot reload (phase 4)
sse-starlette>=2.2.0  # for SSE support
tiktoken>=0.8.0  # token counting
```

---

## Phase 1: Core Runtime

### 1.1 Project Setup
- [ ] Create `pyproject.toml` with dependencies
- [ ] Create directory structure
- [ ] Create `config.yaml` template
- [ ] Create `.env.example`

### 1.2 Configuration (`daemon/config.py`)
- [ ] Load config from YAML
- [ ] Environment variable substitution
- [ ] Pydantic settings model
- [ ] Validate LLM config

### 1.3 Pydantic Models (`daemon/models.py`)
- [ ] `SessionCreate` request
- [ ] `SessionInfo` response
- [ ] `MessageCreate` request
- [ ] `MessageResponse` response
- [ ] `SessionStatus` enum
- [ ] `ErrorResponse` with `code`, `message`, `details` fields
- [ ] `ErrorCodes` enum (SESSION_NOT_FOUND, RATE_LIMITED, etc.)

### 1.4 Markdown Loader (`daemon/loader.py`)
- [ ] `load_agent_prompts(agent_dir: Path) -> dict[str, str]`
- [ ] Load all 4 files (skill, workflow, rule, memory)
- [ ] Compose into single system prompt with separators
- [ ] Token count estimation (tiktoken)
- [ ] Prompt caching (in-memory)

```
Composition order:
1. rule.md (constraints - highest priority)
2. skill.md (capabilities)
3. workflow.md (methodology)
4. memory.md (knowledge)

Separator: "\n\n---\n\n"
```

### 1.5 Persistence Setup (`daemon/persistence.py`)
- [ ] `init_database(db_path: Path) -> sqlite3.Connection`
- [ ] Create sessions table
- [ ] Create session_hierarchy table
- [ ] Return LangGraph `SqliteSaver` checkpointer
- [ ] `save_session_metadata(conn, session_id, agent_dir, parent_id)`
- [ ] `update_session_status(conn, session_id, status)`
- [ ] `get_session_metadata(conn, session_id) -> dict`
- [ ] `list_all_sessions(conn) -> list[dict]`

### 1.6 Session Tools (`daemon/tools.py`)
- [ ] `spawn_session` tool definition
- [ ] `send_message` tool definition
- [ ] `terminate_session` tool definition
- [ ] `list_sessions` tool definition
- [ ] `get_session_info` tool definition
- [ ] Tool executor that routes to session manager

### 1.7 Graph Definition (`daemon/graph.py`)
- [ ] `build_session_graph(tools, checkpointer) -> CompiledGraph`
- [ ] Single node: `agent_node`
- [ ] State: `MessagesState`
- [ ] Tool loop integration
- [ ] Interrupt capability

```python
def agent_node(state: MessagesState, config: RunnableConfig) -> MessagesState:
    # 1. Get session_id from config
    # 2. Load system prompt from cache
    # 3. Call LLM with messages + tools
    # 4. Return updated state
    
def build_session_graph(tools, checkpointer):
    graph = StateGraph(MessagesState)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", ToolNode(tools))
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", should_continue, {
        "tools": "tools",
        "end": END
    })
    graph.add_edge("tools", "agent")
    return graph.compile(checkpointer=checkpointer)
```

### 1.8 Session Manager (`daemon/manager.py`)
- [ ] `SessionManager` class
- [ ] `spawn_session(agent_dir, session_id, parent_id) -> str`
- [ ] `send_message(session_id, message) -> str`
- [ ] `terminate_session(session_id)`
- [ ] `get_session(session_id) -> CompiledGraph`
- [ ] `list_sessions() -> list[dict]`
- [ ] Track parent-child relationships
- [ ] Enforce max sessions limit

### 1.9 HTTP API (`daemon/api.py`)
- [ ] FastAPI app setup
- [ ] `POST /sessions` - spawn
- [ ] `GET /sessions` - list
- [ ] `GET /sessions/:id` - get info
- [ ] `DELETE /sessions/:id` - terminate
- [ ] `POST /sessions/:id/messages` - send message
- [ ] `GET /sessions/:id/messages` - get history
- [ ] `GET /sessions/:id/events` - SSE stream
- [ ] `GET /health` - returns {"status": "healthy", "uptime_seconds": N}
- [ ] `GET /docs` - serve Swagger UI (FastAPI built-in)
- [ ] `GET /redoc` - serve ReDoc (FastAPI built-in)
- [ ] Global exception handler returning `ErrorResponse` schema

### 1.10 Entry Point (`daemon/__main__.py`)
- [ ] Load config
- [ ] Initialize database
- [ ] Start FastAPI with uvicorn
- [ ] Graceful shutdown

---

## Phase 2: Persistence & Recovery

### 2.1 Crash Recovery
- [ ] On startup: load all non-terminated sessions from DB
- [ ] Resume each session's graph from last checkpoint
- [ ] Handle corrupted checkpoints (log and skip)

### 2.2 Session Manifest
- [ ] Track "desired state" in sessions table
- [ ] `status` field: idle, running, waiting, error, terminated
- [ ] Recovery respects last known status

### 2.3 Checkpoint Management
- [ ] Configure checkpoint retention policy
- [ ] `thread_id` = session_id for checkpoint isolation
- [ ] Background cleanup task (runs every `checkpoint_cleanup_interval`)
- [ ] Delete checkpoints older than `checkpoint_ttl_hours`
- [ ] Enforce `checkpoint_max_count` per session (keep most recent)
- [ ] Log cleanup statistics (deleted count, freed space)

---

## Phase 3: Essential Guards

### 3.1 Rate Limiting
- [ ] Per-session message rate limiter (token bucket)
- [ ] Global LLM request queue (semaphore)
- [ ] Return 429 when rate limited

### 3.2 Resource Limits
- [ ] Max concurrent sessions check
- [ ] Max children per session check (in spawn_session tool)
- [ ] Session timeout tracking (background task)

### 3.3 Failure Handling
- [ ] LLM API retry with exponential backoff
- [ ] Max retries before marking session as error
- [ ] Invalid tool call → error message in conversation
- [ ] Child death notification to parent

---

## Phase 4: Developer Experience

### 4.1 Logging
- [ ] Structured JSON logging
- [ ] Correlation ID per session
- [ ] Request/response logging

### 4.2 Metrics
- [ ] Active sessions counter
- [ ] Messages processed counter
- [ ] LLM latency histogram
- [ ] Checkpoint count gauge
- [ ] Checkpoint cleanup metrics (deleted, errors)
- [ ] Prometheus endpoint (optional)

### 4.3 Hot Reload
- [ ] Watch agents/ directory for changes
- [ ] Invalidate prompt cache on change
- [ ] Log reload events

---

## Testing Strategy

### Unit Tests
- [ ] `test_loader.py` - markdown composition
- [ ] `test_persistence.py` - database operations
- [ ] `test_tools.py` - tool definitions

### Integration Tests
- [ ] `test_session_lifecycle.py` - spawn, message, terminate
- [ ] `test_multi_session.py` - leader spawns worker
- [ ] `test_persistence.py` - crash recovery

### Manual Testing
- [ ] Spawn leader session
- [ ] Leader spawns coder session
- [ ] Leader sends task to coder
- [ ] Coder responds
- [ ] Kill daemon, restart, verify recovery

---

## Estimated Effort

| Phase | Tasks | LOC | Time |
|-------|-------|-----|------|
| 1.1-1.3 Setup & Config | 8 | 100 | 2h |
| 1.4 Loader | 5 | 80 | 1.5h |
| 1.5 Persistence | 8 | 100 | 2h |
| 1.6 Tools | 6 | 80 | 1.5h |
| 1.7 Graph | 5 | 60 | 1.5h |
| 1.8 Manager | 7 | 120 | 2h |
| 1.9 API | 9 | 150 | 3h |
| 1.10 Entry | 4 | 40 | 1h |
| **Phase 1 Total** | **52** | **730** | **14.5h** |
| Phase 2 | 10 | 100 | 3h |
| Phase 3 | 12 | 150 | 4h |
| Phase 4 | 8 | 100 | 2h |
| **Total** | **82** | **1080** | **23.5h** |

---

## Execution Order

```
Day 1: 1.1 → 1.2 → 1.3 → 1.4 → 1.5 → 1.6
Day 2: 1.7 → 1.8 → 1.9 → 1.10
Day 3: Phase 2 + Phase 3
Day 4: Phase 4 + Testing + Polish
```

---

## Ready to Start?

Run this to begin Phase 1:

```bash
# I'll create the project structure and start with 1.1-1.3
```
