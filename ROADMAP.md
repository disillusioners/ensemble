# Roadmap: Persistent Multi-Session Agent Daemon

## Vision

Build a **long-running daemon** that hosts conversational agents as independent sessions.

- **1 LangGraph node** - minimal runtime, all complexity in markdown
- **HTTP API** - RESTful control interface
- **OpenAI-compatible LLM** - works with any OpenAI-compatible endpoint
- **Single SQLite DB** - all checkpoints in one database

The daemon is infrastructure. Intelligence lives in `agents/` directories.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                         DAEMON                              │
│                                                             │
│  ┌─────────┐    ┌──────────────┐    ┌─────────────────┐   │
│  │ HTTP API│───►│Session Manager│───►│ Session Registry│   │
│  └─────────┘    └──────┬───────┘    └─────────────────┘   │
│                        │                                    │
│                        ▼                                    │
│              ┌─────────────────┐                           │
│              │  Graph Factory  │                           │
│              │  (1-node each)  │                           │
│              └────────┬────────┘                           │
│                       │                                     │
│         ┌─────────────┼─────────────┐                      │
│         ▼             ▼             ▼                      │
│    ┌─────────┐  ┌─────────┐  ┌─────────┐                  │
│    │Session 1│  │Session 2│  │Session N│                  │
│    │(leader) │  │ (coder) │  │(reviewer)│                 │
│    └────┬────┘  └────┬────┘  └────┬────┘                  │
│         │            │            │                        │
│         └────────────┼────────────┘                        │
│                      ▼                                     │
│              ┌──────────────┐                              │
│              │   SQLite     │                              │
│              │ (checkpoints)│                              │
│              └──────────────┘                              │
│                                                             │
│              ┌──────────────┐                              │
│              │ OpenAI API   │                              │
│              │ (compatible) │                              │
│              └──────────────┘                              │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│                      AGENTS (disk)                          │
│                                                             │
│  agents/leader/         agents/coder/        agents/reviewer│
│  ├── skill.md           ├── skill.md         ├── skill.md  │
│  ├── workflow.md        ├── workflow.md      ├── workflow.md│
│  ├── rule.md            ├── rule.md          ├── rule.md   │
│  └── memory.md          └── memory.md        └── memory.md │
└─────────────────────────────────────────────────────────────┘
```

---

## HTTP API Design

### Endpoints

```
POST   /sessions                    # Spawn new session
GET    /sessions                    # List all sessions
GET    /sessions/:id                # Get session info
DELETE /sessions/:id                # Terminate session

POST   /sessions/:id/messages       # Send message to session
GET    /sessions/:id/messages       # Get message history
GET    /sessions/:id/events         # SSE stream of events

GET    /health                      # Daemon health status
```

### Request/Response Examples

**Spawn Session**
```json
POST /sessions
{
  "agent_dir": "agents/leader",
  "session_id": "leader_001"  // optional, auto-generated if omitted
}

Response 201:
{
  "session_id": "leader_001",
  "agent_dir": "agents/leader",
  "status": "idle",
  "created_at": "2024-01-15T10:30:00Z"
}
```

**Send Message**
```json
POST /sessions/leader_001/messages
{
  "content": "Review the authentication module"
}

Response 200:
{
  "message_id": "msg_abc123",
  "response": {
    "role": "assistant",
    "content": "I'll review the auth module...",
    "tool_calls": [...]  // if any
  }
}
```

**List Sessions**
```json
GET /sessions

Response 200:
{
  "sessions": [
    {
      "session_id": "leader_001",
      "agent_dir": "agents/leader",
      "status": "running",
      "parent_id": null,
      "children": ["task_001", "task_002"],
      "created_at": "2024-01-15T10:30:00Z"
    }
  ]
}
```

**SSE Events Stream**
```
GET /sessions/leader_001/events

event: message
data: {"type": "user", "content": "Hello"}

event: message
data: {"type": "assistant", "content": "Hi there!"}

event: tool_call
data: {"tool": "spawn_session", "args": {...}}

event: status
data: {"status": "waiting"}
```

**Error Response**
```json
Response 400/404/429/500:
{
  "error": {
    "code": "SESSION_NOT_FOUND",
    "message": "Session 'leader_001' does not exist",
    "details": {"session_id": "leader_001"}
  }
}
```

Error codes: `INVALID_REQUEST`, `SESSION_NOT_FOUND`, `SESSION_TERMINATED`, 
`RATE_LIMITED`, `MAX_SESSIONS_EXCEEDED`, `LLM_ERROR`, `INTERNAL_ERROR`

### API Documentation

OpenAPI/Swagger UI available at:
- `GET /docs` - Swagger UI
- `GET /redoc` - ReDoc documentation  
- `GET /openapi.json` - OpenAPI schema

---

## Session Tools (LLM-callable)

These tools are available to all sessions:

```python
# Spawn a new session
spawn_session(
    agent_dir: str,      # e.g., "agents/coder"
    session_id: str      # optional, auto-generated if omitted
) -> str                 # returns session_id

# Send message to another session
send_message(
    session_id: str,
    message: str
) -> str                 # returns response

# Terminate a session
terminate_session(
    session_id: str
) -> bool

# List active sessions
list_sessions() -> list[dict]

# Get session info
get_session_info(
    session_id: str
) -> dict
```

---

## Database Schema

Single SQLite database with LangGraph checkpoints:

```sql
-- LangGraph checkpoint tables (managed by LangGraph)
-- checkpoints, checkpoint_blobs, checkpoint_writes

-- Session metadata (daemon-managed)
CREATE TABLE sessions (
    session_id TEXT PRIMARY KEY,
    agent_dir TEXT NOT NULL,
    parent_id TEXT,
    status TEXT DEFAULT 'idle',  -- idle, running, waiting, error, terminated
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    metadata JSON
);

CREATE TABLE session_hierarchy (
    parent_id TEXT,
    child_id TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (parent_id, child_id)
);
```

---

## Configuration

```yaml
# config.yaml
llm:
  base_url: "https://api.openai.com/v1"  # or your compatible endpoint
  api_key: "${OPENAI_API_KEY}"
  model: "gpt-4"
  temperature: 0.7
  
daemon:
  host: "0.0.0.0"
  port: 8079
  
limits:
  max_sessions: 100
  max_children_per_session: 10
  session_timeout_minutes: 60
  message_rate_limit: 60  # per minute per session
  
persistence:
  db_path: "./data/sessions.db"
  checkpoint_interval: 1  # every message
  checkpoint_ttl_hours: 168        # 7 days, then eligible for cleanup
  checkpoint_cleanup_interval: 24  # hours between cleanup runs
  checkpoint_max_count: 1000       # max checkpoints per session
  
agents:
  directory: "./agents"
```

---

## File Structure

```
ensemble/
├── daemon/
│   ├── __init__.py
│   ├── api.py              # FastAPI routes
│   ├── graph.py            # 1-node LangGraph definition
│   ├── loader.py           # markdown → system prompt
│   ├── tools.py            # session tools (spawn, send, etc.)
│   ├── manager.py          # session lifecycle management
│   ├── persistence.py      # SQLite + checkpoint setup
│   ├── config.py           # configuration loading
│   └── models.py           # pydantic models
├── agents/
│   ├── leader/
│   │   ├── skill.md
│   │   ├── workflow.md
│   │   ├── rule.md
│   │   └── memory.md
│   ├── coder/
│   │   ├── skill.md
│   │   ├── workflow.md
│   │   ├── rule.md
│   │   └── memory.md
│   └── reviewer/
│       └── ...
├── data/
│   └── sessions.db         # SQLite database
├── config.yaml
├── pyproject.toml
├── ROADMAP.md
├── PLAN.md
└── README.md
```

---

## Phased Implementation

See [PLAN.md](./PLAN.md) for detailed implementation tasks.
