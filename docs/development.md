# Development Guide

This guide covers everything you need to know to develop, test, and deploy agents-ensemble.

## Prerequisites

- **Python 3.13+** (required)
- **Node.js 18+** (required for frontend)
- **Git**
- **uv** (optional, but recommended for fast Python package management)
- **SQLite** (usually pre-installed on macOS/Linux)

## Getting Started

### Clone & Setup

```bash
git clone <repo-url>
cd agents-ensemble

# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies with uv (recommended)
make sync

# OR install with pip
pip install -e ".[dev]"
```

### Frontend Setup

```bash
cd frontend
npm install
```

### Environment Configuration

Create a `.env` file in the project root:

```bash
cp .env.example .env
```

Edit `.env` and set your OpenAI API key:

```bash
OPENAI_API_KEY=sk-your-key-here
```

## Running in Development

### Backend (dev.sh)

The `dev.sh` script starts the FastAPI backend with hot-reload:

```bash
./dev.sh
```

**What dev.sh does step by step:**

1. **Sets working directory** — Changes to the script's directory
2. **Detects Python** — Uses `.venv/bin/python` if available, falls back to `python3`
3. **Loads .env** — Sources environment variables from `.env` file (if present)
4. **Validates API key** — Exits with error if `OPENAI_API_KEY` is not set
5. **Sets defaults** — Configures `OPENAI_BASE_URL`, `OPENAI_MODEL`, `DATA_DIR`
6. **Creates dev data directory** — Uses `./data_dev/` (separate from production `./data/`)
7. **Configures dev databases:**
   - `PERSISTENCE_DB_PATH=./data_dev/instances.db`
   - `ENSEMBLE_DATA_DIR=./data_dev` (lifespan resolves the checkpointer
     DB path from `ensemble.json`, which lives here)
8. **Sets dev port** — Always uses port **8079** (avoids conflict with production on 8088)
9. **Starts uvicorn** — With `--reload` flag for auto-reload on code changes

### Frontend

```bash
cd frontend
npm start
```

The Angular dev server runs on **port 4199** and proxies API calls to the backend at `localhost:8079` via `proxy.conf.json`.

### Full Development Workflow

```bash
make dev
```

This runs: `stop` → `sync` → `start`

## Project Architecture

### Directory Overview

```
agents-ensemble/
├── agents/              # Agent definitions (markdown files)
├── config.yaml          # Main configuration file
├── daemon/              # Python backend
├── data/                # Production database files
├── data_dev/            # Development database files
├── data_debug/          # Debug database files
├── dist/                # Built binary output
├── docs/                # Documentation
├── frontend/            # Angular frontend
├── scripts/             # Utility scripts
├── start.sh             # Production startup script
├── dev.sh               # Development startup script
├── Makefile             # Build automation
└── tests/               # Test suite
```

### Backend Structure (daemon/)

```
daemon/
├── __main__.py          # Entry point for `python -m daemon`
├── __init__.py          # Package init, exports __version__
├── api.py               # FastAPI app factory, lifespan, middleware
├── config.py            # Configuration loading from YAML + env vars
├── constants.py         # Global constants (SSE timeouts, pool sizes)
├── graph.py             # LangGraph state machine definition
├── loader.py            # Agent prompt loading from markdown files
├── manager.py           # InstanceManager facade (orchestrates all services)
├── models.py            # Pydantic response models
├── persistence.py       # Database connection and checkpointer setup
├── registry.py          # Agent/tool registry
├── utils.py             # Utility functions

├── routers/             # FastAPI route handlers
│   ├── agents.py       # /api/agents - Agent CRUD
│   ├── instances.py    # /api/instances - Instance management
│   ├── messages.py     # /api/instances/{id}/messages - Send messages, SSE events
│   ├── projects.py     # /api/projects - Project context
│   ├── queues.py       # /api/queues - Job queue management
│   ├── jobs.py         # /api/jobs - Job lifecycle
│   ├── jobs_crud.py    # Job CRUD operations
│   ├── jobs_management.py  # Job state transitions
│   ├── jobs_streaming.py   # Job SSE streaming
│   ├── schedules.py    # /api/schedules - Cron schedules
│   ├── sources.py      # /api/sources - Message sources (Telegram, etc.)
│   ├── mappings.py     # /api/sources/{id}/mappings - Source→Instance routing
│   ├── webhooks.py     # /api/webhooks - Webhook triggers
│   ├── dlq.py          # /api/dlq - Dead letter queue
│   ├── mcp_servers.py  # /api/mcp-servers - MCP server management
│   └── notifications.py # /api/notifications - SSE notifications

├── services/            # Business logic services (32 files)
│   ├── worker_pool.py   # Thread pool for task processing
│   ├── task_processor.py # Processes tasks from queue
│   ├── job_queue_service.py # Job queue with lock-first execution
│   ├── job_processor.py # Polls queue, dispatches jobs
│   ├── job_lock_manager.py # DB-based job locking
│   ├── job_retry_engine.py # Retry logic with backoff
│   ├── retry_scheduler.py # Background retry polling
│   ├── dead_letter_service.py # DLQ management
│   ├── instance_lifecycle.py # Instance spawn/pause/resume/terminate
│   ├── instance_messaging.py # Message handling and routing
│   ├── live_event_hub.py # Live-only SSE streaming
│   ├── notification_broadcaster.py # Notification delivery
│   ├── cancellation.py  # Cancellation token support
│   ├── completion_registry.py # Sync invoke-and-wait support
│   ├── mcp_service.py   # MCP tool lifecycle
│   ├── stale_task_recovery.py # Crash recovery
│   └── ... (24 more services)

├── repositories/         # Data access layer
│   ├── instance/        # Instance repository (SQLModel)
│   ├── project/         # Project repository
│   ├── job_queue/       # Job queue, lock, DLQ repositories
│   ├── task/            # Task repository
│   └── event/           # Event repository

├── tools/               # Agent tools (19 files)
│   ├── bash.py          # Execute bash commands
│   ├── filesystem.py    # File operations
│   ├── job_queue.py     # Job queue tools
│   ├── project.py       # Project tools
│   ├── mcp_tools.py     # MCP adapter tools
│   └── ...

├── sources/             # Message source adapters
│   ├── telegram.py     # Telegram integration
│   ├── scheduler.py     # Cron-based scheduling
│   └── ...

├── mcp/                 # MCP (Model Context Protocol) integration
│   ├── kb_server.py     # Knowledge base MCP server
│   ├── warmup_pool.py   # MCP connection pooling
│   ├── builtin_servers/ # Built-in MCP servers
│   └── ...

├── migrations/          # Database migrations
│   ├── runner.py        # Migration runner
│   ├── models.py        # Migration tracking model
│   ├── versions/        # Migration SQL files (31 files)
│   └── README.md        # Migration documentation

└── tests/               # Backend tests
```

### Frontend Structure

```
frontend/
├── src/
│   ├── app/             # Angular application
│   │   ├── pages/       # Route components
│   │   ├── components/  # Shared components
│   │   └── services/    # API services
│   ├── main.ts          # Bootstrap
│   └── styles.scss      # Global styles
├── e2e/                 # Playwright E2E tests
├── proxy.conf.json      # Dev proxy config (→ backend:8079)
├── angular.json         # Angular CLI config
└── package.json         # Dependencies and scripts
```

### Key Architecture Patterns

1. **Facade Pattern**: `InstanceManager` in `manager.py` is the main facade that delegates to specialized services (lifecycle, messaging, job queue, etc.)

2. **Two Processing Paths**:
   - **WorkerPool** — For child instances (spawned by parent agents)
   - **JobQueue** — For root instances (directly triggered by users/sources)

3. **Lock-first Job Execution**: Jobs acquire a DB lock before processing, preventing duplicate execution

4. **Checkpoint-based Crash Recovery**: LangGraph checkpoints state to SQLite, enabling resume after crashes

5. **Event-driven Dispatch**: `DispatchEventBus` for in-process events, SSE for client streaming

6. **Agent Definition by Markdown**: Agents in `agents/<agent_id>/` with `meta.json` + markdown files (soul.md, rule.md, skill.md, etc.)

### LangGraph State Machine

The graph is defined in `daemon/graph.py`:

- **State**: `MessagesState` (messages list with checkpointing)
- **Nodes**: `llm_node` (call LLM), `tool_node` (execute tools)
- **Routing**: Conditional edges based on LLM output (has_tools vs end)
- **Checkpointer**: SQLite-based via `langgraph-checkpoint-sqlite`

## Testing

### Running Tests

```bash
# All tests (verbose)
pytest tests/ -v

# Specific test file
pytest tests/unit/test_utils.py -v

# Single test by name
pytest tests/ -v -k "test_name"

# Short traceback
pytest tests/ -v --tb=short

# Unit tests only
pytest tests/unit/ -v

# Integration tests only
pytest tests/integration/ -v

# Job queue tests only
pytest tests/job_queue/ -v
```

### Test Types

| Type | Location | Description |
|------|----------|-------------|
| Unit | `tests/unit/` | Mocked tests, no external dependencies |
| Integration | `tests/integration/` | May use mock LLM server |
| E2E | `frontend/e2e/` | Full stack with Playwright |
| Job Queue | `tests/job_queue/` | Job queue specific tests |
| Message Queue | `tests/message_queue_redesign/` | Worker pool tests |

### Test Structure

```
tests/
├── conftest.py          # Shared fixtures (mocks langgraph, MCP modules)
├── unit/                # 71 unit test files
├── integration/         # 16 integration test files
├── job_queue/           # Job queue tests
├── e2e/                 # Playwright tests
├── mock_llm_server.py   # Mock LLM server for testing
└── mock_*.py           # Various test mocks
```

### Writing Tests

Key patterns from `tests/conftest.py`:

- **Mock langgraph**: `tests/conftest.py` provides fixtures that mock LangGraph modules
- **Mock MCP**: MCP adapters are mocked to avoid external dependencies
- **Async tests**: Use `pytest-asyncio` with `async def test_`
- **Test databases**: Separate `test_*.db` files, cleaned up after tests

```python
import pytest

@pytest.mark.asyncio
async def test_example():
    # Use mocked fixtures from conftest.py
    manager = InstanceManager()
    result = await manager.create_instance(project="test")
    assert result.id is not None
```

## Database Migrations

### How Migrations Work

Located in `daemon/migrations/`:

- **Migration runner**: `daemon/migrations/runner.py` — `MigrationRunner` class
- **SQL files**: `daemon/migrations/versions/*.sql` — 31 migration files
- **Auto-run**: Executed on startup via `InstanceManager.__init__()`
- **Format**: `-- UP` / `-- DOWN` SQL sections

**Naming convention**: `YYYYMMDD_HHMMSS_description.sql`

Example: `20260514_000001_add_project_id_to_instances.sql`

### Migration Structure

```sql
-- UP
CREATE TABLE IF NOT EXISTS new_table (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL
);

-- DOWN
DROP TABLE IF EXISTS new_table;
```

### Creating a New Migration

1. Create file in `daemon/migrations/versions/`:
   ```bash
   touch daemon/migrations/versions/20260530_000001_add_feature.sql
   ```

2. Add UP and DOWN sections:
   ```sql
   -- UP
   ALTER TABLE instances ADD COLUMN new_column TEXT;

   -- DOWN
   -- SQLite doesn't support DROP COLUMN directly
   -- Migrations must be idempotent or use a workaround
   ```

3. On next startup, migration runs automatically

**Note**: SQLite has limited ALTER TABLE support. Use `CREATE TABLE ... AS SELECT` for complex changes.

## Building & Production

### Build Frontend

```bash
make build
```

Runs: `cd frontend && npm install && npm run build`

Output: `frontend/dist/frontend/browser/`

### Build Binary (PyInstaller)

```bash
make pyinstaller
```

- Clears `build/` and `dist/` directories
- Runs PyInstaller with `ensemble.spec`
- Output: `dist/ensemble-prod`

Requires frontend to be built first (`make build` is called automatically).

### Production Install

```bash
make install
```

This:
1. Builds the binary (`make pyinstaller`)
2. Stops any existing process on port 8088
3. Backs up existing installation
4. Copies binary, agents, config, and frontend to `~/agents-ensemble`
5. Sets production port to 8088

### Start/Stop Production

```bash
make start   # Stop existing + run start.sh
make stop    # Kill process on port 8088
```

## Makefile Reference

| Target | Description |
|--------|-------------|
| `make help` | Show all targets |
| `make sync` | Install Python deps with uv |
| `make dev` | Full dev workflow: stop + sync + start |
| `make start` | Stop existing + start daemon |
| `make stop` | Stop daemon on port 8088 |
| `make build` | Build frontend (`npm install && npm run build`) |
| `make pyinstaller` | Build production binary to `dist/ensemble-prod` |
| `make install` | Build and install to `~/agents-ensemble` |
| `make install-deps` | Install Python deps in production directory |
| `make clean` | Remove build artifacts |
| `make pyinstaller-clean` | Remove PyInstaller files |
| `make uninstall` | Remove production installation |

## Environment Variables

### Required

| Variable | Description |
|----------|-------------|
| `OPENAI_API_KEY` | OpenAI API key (required) |

### Optional

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | API base URL (for proxies/custom endpoints) |
| `OPENAI_MODEL` | `gpt-4` | Model to use |
| `OPENAI_MODEL_TITLE` | (none) | Separate model for title generation |
| `OPENAI_MODEL_VISION` | (none) | Vision-capable model for image processing |
| `HOST` | `0.0.0.0` | Server bind host |
| `PORT` | `8079` (dev) / `8088` (prod) | Server port |
| `DATA_DIR` | `./data_dev` (dev) | Database directory |
| `PERSISTENCE_DB_PATH` | `$DATA_DIR/instances.db` | Instance database path |
| `ENSEMBLE_DATA_DIR` | `$DATA_DIR` (dev) | Directory holding `ensemble.json` (which records the checkpointer DB path) |
| `LOG_LEVEL` | `info` | Logging level for third-party libs (debug, info, warning, error) |
| `LOG_LEVEL_DAEMON` | `info` | Logging level for daemon modules (debug, info, warning, error) |
| `USE_WORKER_POOL` | `true` | Enable/disable worker pool |

### MCP Configuration

| Variable | Description |
|----------|-------------|
| `MCP_DISABLE_BUILT_IN_<NAME>` | Disable built-in MCP server by name |
| `LIGHTRAG_HOST` | LightRAG service host |
| `LIGHTRAG_API_KEY` | LightRAG API key |

## Common Development Tasks

### Running with Debug Logging

```bash
LOG_LEVEL=info LOG_LEVEL_DAEMON=debug ./dev.sh
```

### Checking API Documentation

Start the server, then visit: http://localhost:8079/docs

### Resetting Development Database

```bash
rm -rf data_dev/
./dev.sh  # Recreates on startup
```

### Running a Single Test File

```bash
pytest tests/unit/test_utils.py -v
```

### Running Tests with Coverage

```bash
pytest tests/unit/ -v --cov=daemon --cov-report=term-missing
```

### Adding a New Tool

1. Create tool file in `daemon/tools/` (e.g., `my_tool.py`)
2. Register in `daemon/tools/__init__.py`
3. Add tests in `tests/unit/tools/`

### Adding a New Migration

1. Create SQL file in `daemon/migrations/versions/`
2. Follow naming: `YYYYMMDD_HHMMSS_description.sql`
3. Include `-- UP` and `-- DOWN` sections
4. Migration runs automatically on next startup

## Troubleshooting

### Port Already in Use

```bash
# Find process on dev port
lsof -ti:8079

# Kill it
lsof -ti:8079 | xargs kill
```

### Database Lock Issues

SQLite can have lock issues with multiple processes. Ensure only one instance runs per database.

### MCP Server Connection Issues

Check logs for MCP warm-up pool errors. May need to adjust `mcp_pool` settings in `config.yaml`.

### Frontend Not Proxying Correctly

Verify `frontend/proxy.conf.json` points to `localhost:8079` and backend is running.

## Further Reading

- [AGENTS.md](./AGENTS.md) — Agent system overview
- [README.md](./README.md) — Project overview
- [Quick-Start.md](./Quick-Start.md) — Quick start guide
- [daemon/migrations/README.md](./daemon/migrations/README.md) — Migration system details
