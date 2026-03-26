# Agents Ensemble

A **persistent multi-agent daemon** built with LangGraph. Agents are defined by markdown files, not code.

## Features

- **1-node LangGraph** - minimal runtime, all complexity in markdown
- **HTTP API** - RESTful control interface
- **OpenAI-compatible** - works with any OpenAI-compatible endpoint
- **Session hierarchy** - agents can spawn and communicate with other agents
- **Persistent state** - SQLite checkpoints for crash recovery
- **Job Queue** - priority-based job scheduling with per-project locking
- **Database Migrations** - file-based schema versioning with auto-apply

## Quick Start

```bash
# 1. Create virtual environment
python -m venv .venv
source .venv/bin/activate  # or: .venv\Scripts\activate (Windows)

# 2. Install dependencies
pip install langgraph langgraph-checkpoint-sqlite langchain-core langchain-openai
pip install fastapi uvicorn pydantic pydantic-settings pyyaml sse-starlette tiktoken

# 3. Configure
cp .env.example .env
# Edit .env and set OPENAI_API_KEY

# 4. Start server
./start.sh
```

Server runs at `http://localhost:8079`

## API

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Health check |
| POST | `/sessions` | Spawn new session |
| GET | `/sessions` | List all sessions |
| GET | `/sessions/:id` | Get session info |
| DELETE | `/sessions/:id` | Terminate session |
| POST | `/sessions/:id/messages` | Send message |
| GET | `/sessions/:id/messages` | Get message history |
| GET | `/sessions/:id/events` | SSE event stream |

### Job Queue API

For priority-based job scheduling and per-project locking, see the [Job Queue Documentation](./docs/features/job-queue.md).

### Example

```bash
# Spawn a session
curl -X POST http://localhost:8079/sessions \
  -H "Content-Type: application/json" \
  -d '{"agent_dir": "agents/leader"}'

# Send a message
curl -X POST http://localhost:8079/sessions/{session_id}/messages \
  -H "Content-Type: application/json" \
  -d '{"content": "Hello, agent!"}'
```

## Agents

Agents are defined by markdown files in `agents/` directory:

```
agents/
├── leader/
│   ├── skill.md      # Capabilities
│   ├── workflow.md   # Methodology
│   ├── rule.md       # Constraints
│   └── memory.md     # Long-term knowledge
└── coder/
    └── ...
```

### Composition Order

1. `rule.md` - constraints (highest priority)
2. `skill.md` - capabilities
3. `workflow.md` - methodology
4. `memory.md` - knowledge

### Session Tools

Agents can use these tools:

- `spawn_session(agent_dir, session_id)` - spawn new agent
- `send_message(session_id, message)` - send message to another session
- `terminate_session(session_id)` - end a session
- `list_sessions()` - list active sessions
- `get_session_info(session_id)` - get session details

## Configuration

Edit `config.yaml`:

```yaml
llm:
  base_url: "${OPENAI_BASE_URL:-https://api.openai.com/v1}"
  api_key: "${OPENAI_API_KEY}"
  model: "${OPENAI_MODEL:-gpt-4}"
  model_title: "${OPENAI_MODEL_TITLE:-}"  # Optional: cheaper model for title generation
  temperature: 0.7

daemon:
  host: "0.0.0.0"
  port: 8079

limits:
  max_sessions: 100
  max_children_per_session: 10
```

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `OPENAI_API_KEY` | Your OpenAI API key (required) | - |
| `OPENAI_BASE_URL` | API endpoint URL | `https://api.openai.com/v1` |
| `OPENAI_MODEL` | Main model for agent responses | `gpt-4` |
| `OPENAI_MODEL_TITLE` | Model for title generation (optional) | Falls back to `OPENAI_MODEL` |

## Development

```bash
# Run tests
python -m pytest tests/ -v

# Start with auto-reload
./dev.sh
```

## Database Migrations

The project uses a file-based database migration system for schema versioning.

### How It Works

- Migration files are SQL files in `daemon/migrations/versions/`
- Pending migrations auto-apply on server startup
- Each migration has UP (apply) and DOWN (rollback) sections

### Creating a Migration

```bash
# 1. Create migration file with timestamp
touch daemon/migrations/versions/$(date +%Y%m%d_%H%M%S)_add_feature.sql

# 2. Edit the file:
-- Migration: add feature
-- Created: 2026-03-26

-- UP
ALTER TABLE sessions ADD COLUMN feature_flag TEXT;

-- DOWN
-- SQLite does not support DROP COLUMN
```

### Documentation

See [`daemon/migrations/README.md`](./daemon/migrations/README.md) for full documentation.

## Project Structure

```
ensemble/
├── daemon/
│   ├── api.py         # FastAPI routes
│   ├── graph.py       # LangGraph definition
│   ├── manager.py     # Session lifecycle
│   ├── migrations/    # Database migration system
│   │   ├── runner.py  # MigrationRunner class
│   │   └── versions/  # SQL migration files
│   ├── tools.py       # LLM-callable tools
│   ├── loader.py      # Markdown loader
│   ├── persistence.py # SQLite + checkpoints
│   ├── config.py      # Configuration
│   └── models.py      # Pydantic models
├── agents/            # Agent definitions
├── tests/             # Test suite
├── config.yaml        # Configuration
├── start.sh           # Production start
├── dev.sh             # Development start
└── start.py           # Cross-platform start
```

## License

MIT
