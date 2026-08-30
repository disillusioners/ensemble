# Roadmap: Persistent Multi-Instance Agent Daemon

## Vision

Build a **long-running daemon** that hosts conversational agents as independent instances.

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
│  ┌─────────┐    ┌───────────────┐    ┌─────────────────┐   │
│  │ HTTP API│───►│Instance Manager│───►│ Instance Registry│   │
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
│    ┌──────────┐  ┌──────────┐  ┌──────────┐                  │
│    │Instance 1│  │Instance 2│  │Instance N│                  │
│    │(leader) │  │ (developer) │  │(reviewer)│                 │
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
│  agents/leader/         agents/developer/        agents/reviewer│
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
POST   /instances                    # Spawn new instance
GET    /instances                    # List all instances
GET    /instances/:id                # Get instance info
DELETE /instances/:id                # Terminate instance

POST   /instances/:id/messages       # Send message to instance
GET    /instances/:id/messages       # Get message history
GET    /instances/:id/events         # SSE stream of events

GET    /health                      # Daemon health status
```

### Request/Response Examples

**Spawn Instance**
```json
POST /instances
{
  "agent_dir": "agents/leader",
  "instance_id": "leader_001"  // optional, auto-generated if omitted
}

Response 201:
{
  "instance_id": "leader_001",
  "agent_dir": "agents/leader",
  "status": "idle",
  "created_at": "2024-01-15T10:30:00Z"
}
```

**Send Message**
```json
POST /instances/leader_001/messages
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

**List Instances**
```json
GET /instances

Response 200:
{
  "instances": [
    {
      "instance_id": "leader_001",
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
GET /instances/leader_001/events

event: message
data: {"type": "user", "content": "Hello"}

event: message
data: {"type": "assistant", "content": "Hi there!"}

event: tool_call
data: {"tool": "spawn_instance", "args": {...}}

event: status
data: {"status": "waiting"}
```

**Error Response**
```json
Response 400/404/429/500:
{
  "error": {
    "code": "INSTANCE_NOT_FOUND",
    "message": "Instance 'leader_001' does not exist",
    "details": {"instance_id": "leader_001"}
  }
}
```

Error codes: `INVALID_REQUEST`, `INSTANCE_NOT_FOUND`, `INSTANCE_TERMINATED`, `RATE_LIMITED`, `LLM_ERROR`, `INTERNAL_ERROR`

### API Documentation

OpenAPI/Swagger UI available at:
- `GET /docs` - Swagger UI
- `GET /redoc` - ReDoc documentation  
- `GET /openapi.json` - OpenAPI schema

---

## Instance Tools (LLM-callable)

These tools are available to all instances:

```python
# Spawn a new instance
spawn_instance(
    agent_dir: str,      # e.g., "agents/developer"
    instance_id: str      # optional, auto-generated if omitted
) -> str                 # returns instance_id

# Send message to another instance
send_message(
    instance_id: str,
    message: str
) -> str                 # returns response

# Terminate an instance
terminate_instance(
    instance_id: str
) -> bool

# List active instances
list_instances() -> list[dict]

# Get instance info
get_instance_info(
    instance_id: str
) -> dict
```

---

## Database Schema

Single SQLite database with LangGraph checkpoints:

```sql
-- LangGraph checkpoint tables (managed by LangGraph)
-- checkpoints, checkpoint_blobs, checkpoint_writes

-- Instance metadata (daemon-managed)
CREATE TABLE instances (
    instance_id TEXT PRIMARY KEY,
    agent_dir TEXT NOT NULL,
    parent_id TEXT,
    status TEXT DEFAULT 'idle',  -- idle, running, waiting, error, terminated
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    metadata JSON
);

CREATE TABLE instance_hierarchy (
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
  max_children_per_instance: 10
  instance_timeout_minutes: 60
  
persistence:
  db_path: "./data/instances.db"
  checkpoint_interval: 1  # every message
  checkpoint_ttl_hours: 168        # 7 days, then eligible for cleanup
  checkpoint_cleanup_interval: 24  # hours between cleanup runs
  checkpoint_max_count: 1000       # max checkpoints per instance
  
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
│   ├── tools.py            # instance tools (spawn, send, etc.)
│   ├── manager.py          # instance lifecycle management
│   ├── persistence.py      # SQLite + checkpoint setup
│   ├── config.py           # configuration loading
│   └── models.py           # pydantic models
├── agents/
│   ├── leader/
│   │   ├── skill.md
│   │   ├── workflow.md
│   │   ├── rule.md
│   │   └── memory.md
│   ├── developer/
│   │   ├── skill.md
│   │   ├── workflow.md
│   │   ├── rule.md
│   │   └── memory.md
│   └── reviewer/
│       └── ...
├── data/
│   └── instances.db         # SQLite database
├── config.yaml
├── pyproject.toml
├── ROADMAP.md
├── PLAN.md
└── README.md
```

---

## Phased Implementation

See [PLAN.md](./PLAN.md) for detailed implementation tasks.
