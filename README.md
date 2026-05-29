# Agents Ensemble

**Persistent Multi-Agent AI Daemon** — A LangGraph-powered system where agents are defined by markdown files, not code.

---

## Introduction

Agents Ensemble is a persistent multi-agent daemon built on LangGraph that revolutionizes how AI agent systems are designed and deployed. Instead of hardcoding agent behaviors in Python, you define agents through markdown files — their personality, skills, constraints, and workflows live alongside your code as plain text documents. This makes agents infinitely more portable, version-controllable, and accessible to non-developers.

The system provides a robust HTTP REST API with 60+ endpoints and a modern Angular web dashboard for monitoring and control. Each agent runs as an **instance** — a stateful, checkpointed execution that survives crashes and disconnections. Instances can spawn child agents and communicate via a hierarchical message system, enabling sophisticated multi-agent workflows.

Built on SQLite with LangGraph's checkpointing, every conversation is persisted. Combined with a priority-based job queue featuring per-project locking and dead-letter queue handling, Agents Ensemble is production-ready for sustained, mission-critical workloads. It works with any OpenAI-compatible LLM API, giving you flexibility in model choice.

---

## Key Features

| Feature | Description |
|---------|-------------|
| **1-node LangGraph Runtime** | Minimal core, all agent complexity in markdown |
| **OpenAI-Compatible LLM** | Works with any OpenAI-compatible API endpoint |
| **HTTP REST API** | 60+ endpoints for full programmatic control |
| **Angular Web UI** | Modern dashboard for monitoring and interaction |
| **Instance Hierarchy** | Parent-child agent spawning and messaging |
| **SQLite Persistence** | Crash recovery via LangGraph checkpoints |
| **Job Queue** | Priority scheduling, per-project locking, DLQ, retry |
| **Context Compaction** | Auto-summarizes long conversations |
| **MCP Server Support** | Model Context Protocol integration |
| **Pluggable Sources** | Webhooks, schedules — extendable message adapters |
| **Database Migrations** | File-based schema versioning with auto-apply |

---

## Quick Start

Get up and running in 5 minutes:

```bash
# Clone the repository
git clone <repository-url>
cd agents-ensemble

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # Linux/macOS
# .venv\Scripts\activate   # Windows

# Install dependencies
uv sync

# Configure
cp .env.example .env
# Edit .env and set your OPENAI_API_KEY

# Start development server
./dev.sh
```

Verify the server is running:

```bash
curl http://localhost:8079/api/health
```

Expected response:
```json
{"status":"ok","version":"0.3.6"}
```

---

## First Steps

Once the server is running, here are the basic operations via curl:

```bash
# List available agents
curl http://localhost:8079/api/agents

# Spawn a new instance with the leader agent
curl -X POST http://localhost:8079/api/instances \
  -H "Content-Type: application/json" \
  -d '{"agent_dir": "agents/leader"}'

# Send a message to your instance
curl -X POST http://localhost:8079/api/instances/{instance_id}/messages \
  -H "Content-Type: application/json" \
  -d '{"content": "Hello, what can you help me with?"}'

# Get message history
curl http://localhost:8079/api/instances/{instance_id}/messages

# Watch real-time events (SSE stream)
curl http://localhost:8079/api/instances/{instance_id}/events
```

---

## Agents

### Markdown-Based Agent Definitions

Agents in this project are defined entirely through markdown files — no code changes required to create new agents. Each agent lives in its own directory under `agents/` and consists of optional markdown files that compose into the agent's complete personality and capabilities.

```
agents/
├── leader/
│   ├── meta.json       # Agent metadata
│   ├── soul.md         # Identity and personality
│   ├── rule.md         # Hard constraints (highest priority)
│   ├── skill.md        # Single capability definition
│   ├── skills/         # Multiple skills directory
│   │   └── planning/
│   │       └── skill.md
│   ├── tools.md        # Tool documentation for the agent
│   ├── workflow.md     # Methodology and processes
│   ├── memory.md       # Long-term knowledge
│   └── memories/       # Additional knowledge files
└── coder/
    └── ...
```

### Composition Order

When an agent is loaded, files are composed in this priority order:

1. `rule.md` — Constraints and guardrails (highest priority)
2. `skill.md` / `skills/*/skill.md` — Capabilities and instructions
3. `workflow.md` — Methodology and processes
4. `memory.md` / `memories/` — Long-term knowledge
5. `soul.md` — Core identity and personality

### Built-In Agents

| Agent | Purpose |
|-------|---------|
| `leader` | Orchestrates other agents, coordinates workflows |
| `coder` | Writes and modifies code |
| `reviewer` | Reviews code and provides feedback |
| `tester` | Creates and runs tests |
| `planner` | Creates project plans and roadmaps |
| `explorer` | Searches and analyzes codebases |
| `experiencer` | Records and retrieves knowledge |
| `giter` | Git operations and repository management |
| `jober` | Job queue operations and scheduling |
| `approver` | Reviews and approves changes |
| `tidier` | Code cleanup and refactoring |
| `kb-importer` | Imports knowledge into the system |
| `gaia` | General AI assistant capabilities |

> **Note:** Internal system agents (prefixed with `_`) handle agent lifecycle and are not listed here. They include `_mother` (agent management), `_baby_template` (agent creation template), and `_inner_soul`.

### Instance Tools

Agents can use these tools to interact with the system:

| Tool | Description |
|------|-------------|
| `spawn_instance` | Create a new child agent instance |
| `send_message` | Send a message to another instance |
| `terminate_instance` | End an instance gracefully |
| `list_instances` | List all active instances |
| `get_instance_info` | Get details about a specific instance |

---

## Project Structure

```
agents-ensemble/
├── daemon/                    # Backend core
│   ├── __main__.py            # Entry point
│   ├── api.py                 # FastAPI application + routes
│   ├── graph.py               # LangGraph agent loop
│   ├── manager.py             # Instance lifecycle management
│   ├── loader.py               # Markdown agent loader
│   ├── tools/                  # Agent-callable tools directory
│   ├── config.py               # Configuration management
│   ├── models.py               # Pydantic/SQLModel models
│   ├── sources/                # Message adapters (webhooks, schedules)
│   └── migrations/             # Database migration system
├── agents/                     # Agent definitions (markdown)
├── frontend/                   # Angular 21 web UI
├── tests/                      # Test suite
├── docs/                       # Documentation
├── config.yaml                 # Daemon configuration
├── dev.sh                      # Development server (port 8079, auto-reload)
├── start.sh                    # Local production test script
├── Makefile                    # Build & install targets
├── pyproject.toml              # Python project metadata
└── .env.example                # Environment template
```

---

## Configuration Overview

The daemon is configured via `config.yaml`. Key sections:

| Section | Purpose |
|---------|---------|
| `llm` | Model selection, API key, temperature, base URL |
| `daemon` | Host and port settings |
| `limits` | Max instances, children per agent, timeout |
| `persistence` | SQLite database paths |
| `agents` | Agent directory paths and defaults |
| `queue` | Job queue settings |
| `compaction` | Context summarization thresholds |
| `services` | External service configurations |
| `job_system` | Job queue operational settings |

For full configuration details, see [docs/setup.md](docs/setup.md).

---

## API Overview

### Main Endpoint Groups

| Group | Base Path | Description |
|-------|-----------|-------------|
| Health | `/api/health` | Server health and version |
| Agents | `/api/agents` | List and manage agents |
| Instances | `/api/instances` | Agent instance lifecycle |
| Jobs | `/api/jobs` | Job queue operations |
| Projects | `/api/projects` | Project management |
| Sources | `/api/sources` | Message source adapters |
| Schedules | `/api/schedules` | Scheduled task management |
| MCP Servers | `/api/mcp-servers` | MCP server management |

**Interactive API docs:** http://localhost:8079/docs (Swagger UI)

---

## Documentation

| Guide | Description |
|-------|-------------|
| [Setup Guide](docs/setup.md) | Complete installation and configuration |
| [Architecture](docs/architecture.md) | System design and internals |
| [Job Queue](docs/features/job-queue.md) | Priority scheduling and dead-letter queue |
| [Configuration](docs/configuration/rag-configuration.md) | Configuration reference |

Full documentation is available in the [`docs/`](docs/) directory.

---

## Development

### Running the Dev Server

```bash
# Development server with auto-reload (port 8079)
./dev.sh
```

### Running Tests

```bash
# All tests
python -m pytest tests/ -v

# Specific test file
python -m pytest tests/unit/test_manager.py -v

# With short traceback
python -m pytest tests/ -v --tb=short

# Watch mode (requires pytest-watch)
ptw tests/ -v
```

### Frontend Development

```bash
cd frontend
npm install
npm start
```

The Angular dev server runs on port 4199 with proxy to the backend API.

---

## Production Deployment

### Standard Installation

```bash
make install
```

This builds the frontend and installs the complete application to `~/agents-ensemble/`.

### Standalone Binary

```bash
make pyinstaller
```

Creates a standalone executable with bundled frontend.

### Data Directories

| Environment | Data Directory |
|-------------|----------------|
| Development | `./data_dev/` |
| Production | `./data/` |

For complete deployment instructions, see [docs/setup.md](docs/setup.md).

---

## License

MIT
