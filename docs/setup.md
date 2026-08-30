# Setup Guide

## agents-ensemble v0.3.6

A comprehensive installation and configuration guide for the agents-ensemble multi-agent orchestration system.

---

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Quick Start](#quick-start)
3. [Installation Methods](#installation-methods)
4. [Configuration](#configuration)
5. [Database](#database)
6. [Frontend Setup](#frontend-setup)
7. [First Run](#first-run)
8. [Troubleshooting](#troubleshooting)
9. [Next Steps](#next-steps)

---

## Prerequisites

| Prerequisite | Version | Required | Notes |
|-------------|---------|----------|-------|
| Python | 3.11+ | Yes | Core runtime for the backend |
| Node.js | 18+ | Yes | Required for frontend build |
| uv | Latest | Recommended | Fast Python package manager |
| pip | Latest | Optional | Alternative to uv |
| OpenAI-compatible API | — | Yes | API key required for LLM access |
| Docker | Latest | Optional | Required only for LightRAG |
| Git | Any | Yes | For cloning the repository |

### Verifying Python Version

```bash
python --version
# Must output Python 3.13.x or higher
```

### Verifying Node.js Version

```bash
node --version
# Must output v18.x.x or higher
```

### Installing uv (Recommended)

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Or via pip
pip install uv
```

---

## Quick Start

Get agents-ensemble running in under 5 minutes:

```bash
# 1. Clone the repository
git clone <repository-url>
cd agents-ensemble

# 2. Create virtual environment and install dependencies
python -m venv .venv
source .venv/bin/activate
uv sync

# 3. Configure environment
cp .env.example .env
# Edit .env and set OPENAI_API_KEY=your-api-key-here

# 4. Start development server
./dev.sh
```

That's it! The server starts on port **8079**. Access the web UI at `http://localhost:8079`.

---

## Installation Methods

### Method 1: Development Setup (dev.sh)

Recommended for local development with hot-reload support.

#### Step 1: Clone the Repository

```bash
git clone <repository-url>
cd agents-ensemble
```

#### Step 2: Create and Activate Virtual Environment

```bash
python -m venv .venv
source .venv/bin/activate
```

> **Note:** On Windows, use `.venv\Scripts\activate` instead.

#### Step 3: Install Dependencies

```bash
uv sync
```

> **Alternative:** If not using uv, run `pip install -e .`

#### Step 4: Configure Environment Variables

```bash
cp .env.example .env
```

Edit `.env` with your preferred text editor:

```bash
nano .env  # or vim, code, etc.
```

**Required setting:**

```env
OPENAI_API_KEY=sk-your-actual-api-key
```

#### Step 5: Start Development Server

```bash
./dev.sh
```

The `dev.sh` script:

- Activates `.venv` if available
- Loads environment variables from `.env`
- Creates `./data_dev/` directory for development data
- Sets `PORT=8079`
- Runs uvicorn with `--reload` for hot-reload

#### Step 6: Verify Server is Running

```bash
curl http://localhost:8079/api/health
```

Expected response:

```json
{"status": "ok", "version": "0.3.6"}
```

---

### Method 2: Production Install (make install)

Complete production installation to `~/agents-ensemble/`.

#### Step 1: Clone and Prepare

```bash
git clone <repository-url>
cd agents-ensemble
```

#### Step 2: Run Production Install

```bash
make install
```

This command performs the following steps in order:

1. **Builds frontend** — Runs `make build` (npm install + Angular build)
2. **Builds PyInstaller binary** — Creates standalone executable
3. **Creates installation directory** — `~/agents-ensemble/`
4. **Copies required files:**
   - `agents/` — Agent definitions
   - `config.yaml` — Production configuration
   - `.env` — Environment variables (if exists)
   - `frontend/dist/` — Built frontend assets

#### Step 3: Configure for Production

```bash
cd ~/agents-ensemble
cp .env.example .env
nano .env
```

Set your production values:

```env
OPENAI_API_KEY=sk-your-api-key
PORT=8088
```

#### Step 4: Run Production Server

```bash
cd ~/agents-ensemble
./ensemble-prod
```

The production binary runs on port **8088** by default.

> **Note:** Ensure no other process is using port 8088 before starting.

---

### Method 3: PyInstaller Binary

Build a standalone executable without full installation.

#### Step 1: Build the Binary

```bash
make pyinstaller
```

Output: `dist/ensemble-prod`

#### Step 2: Prepare Runtime Directory

The binary requires these files/directories in its working directory:

```
working-dir/
├── config.yaml      # Configuration file
├── .env            # Environment variables
├── agents/         # Agent definitions
└── frontend/dist/  # Built frontend
```

#### Step 3: Run the Binary

```bash
./dist/ensemble-prod
```

---

### Method 4: Local Production Test (start.sh)

Test production-like setup locally without full installation.

```bash
./start.sh
```

This script:

- Loads `.env.prod` (falls back to `.env`)
- Uses port from environment (default 8079)
- Runs without hot-reload
- Uses `./data/` instead of `./data_dev/`

---

## Configuration

### Environment Variables

Environment variables are loaded from `.env` file and override config.yaml values.

| Variable | Type | Required | Default | Description |
|----------|------|----------|---------|-------------|
| `OPENAI_API_KEY` | string | **Yes** | — | Your OpenAI API key for LLM access |
| `OPENAI_BASE_URL` | string | No | `https://api.openai.com/v1` | API endpoint base URL (for proxies/custom endpoints) |
| `OPENAI_MODEL` | string | No | `gpt-4` | Default model for agent conversations |
| `OPENAI_MODEL_TITLE` | string | No | — | Cheaper model for generating conversation titles |
| `OPENAI_MODEL_VISION` | string | No | — | Vision-capable model for image analysis |
| `HOST` | string | No | `0.0.0.0` | Server bind address |
| `PORT` | integer | No | `8079` (dev) / `8088` (prod) | Server port |
| `LIGHTRAG_HOST` | string | No | `http://lightrag.lightrag.svc.cluster.local:9621` | LightRAG service host |
| `LIGHTRAG_API_KEY` | string | No | — | LightRAG authentication key |
| `LIGHTRAG_WORKSPACE` | string | No | — | LightRAG workspace identifier (optional, not in .env.example) |
| `LIGHTRAG_TIMEOUT` | integer | No | — | LightRAG request timeout in seconds (optional, not in .env.example) |

#### Creating .env File

```bash
cp .env.example .env
```

**Minimal .env for development:**

```env
OPENAI_API_KEY=sk-your-api-key-here
```

**Custom endpoint example:**

```env
OPENAI_API_KEY=your-api-key
OPENAI_BASE_URL=https://api.my-proxy.com/v1
OPENAI_MODEL=gpt-4o
```

---

### config.yaml Reference

The `config.yaml` file contains all application configuration. Environment variables can be referenced using `${VAR_NAME:-default}` syntax.

#### LLM Configuration

```yaml
llm:
  base_url: ${OPENAI_BASE_URL:-https://api.openai.com/v1}
  api_key: ${OPENAI_API_KEY}
  model: ${OPENAI_MODEL:-gpt-4}
  model_title: ${OPENAI_MODEL_TITLE:-}
  model_vision: ${OPENAI_MODEL_VISION:-}
  temperature: 0.7
  request_timeout: 610
```

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `llm.base_url` | string | `https://api.openai.com/v1` | Base URL for LLM API requests |
| `llm.api_key` | string | (from env) | API key for authentication |
| `llm.model` | string | `gpt-4` | Default model for agent conversations |
| `llm.model_title` | string | — | Model used for generating conversation titles (optional, can use cheaper model) |
| `llm.model_vision` | string | — | Vision-capable model for image analysis tasks |
| `llm.temperature` | float | `0.7` | Default temperature for LLM requests (0.0-2.0) |
| `llm.request_timeout` | integer | `610` | Timeout for LLM API requests in seconds |

#### Daemon Configuration

```yaml
daemon:
  host: ${HOST:-0.0.0.0}
  port: ${PORT:-8088}
```

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `daemon.host` | string | `0.0.0.0` | Network interface to bind to (`0.0.0.0` = all interfaces) |
| `daemon.port` | integer | `8088` | Port for the API server |

#### Limits Configuration

```yaml
limits:
  max_children_per_instance: 50
  instance_timeout_minutes: 60
  graph_recursion_limit: 200
  llm_concurrency: 10
```

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `limits.max_children_per_instance` | integer | `50` | Maximum child instances per parent instance |
| `limits.instance_timeout_minutes` | integer | `60` | Auto-shutdown timeout for idle instances (minutes) |
| `limits.graph_recursion_limit` | integer | `200` | Maximum recursion depth for LangGraph execution (global default) |
| `limits.llm_concurrency` | integer | `10` | Maximum concurrent LLM API requests |
| `limits.governor_recursion_guard_enabled` | boolean | `true` | Master kill-switch for the governor recursive-spawn guard. Override via `LIMITS_GOVERNOR_RECURSION_GUARD_ENABLED` (truthy values enable; `0` disables). Restart required — cached at boot. |
| `limits.max_governor_ancestors` | integer | `1` | Max governor count allowed in the parent ∪ ancestors chain (K). When the parent-inclusive count ≥ K, governor spawns are refused with a corrective HINT. Override via `LIMITS_MAX_GOVERNOR_ANCESTORS`. `0` disables the guard entirely. Restart required. |
| `ENSEMBLE_WC_WAKE_ENQUEUE` (env var — **no config.yaml key**) | env var (string) | unset → OFF | **WC-wake enqueue routing pivot** (wc-wake-report-integrity, `feature/wc-wake-report-integrity`). **Env-only:** there is NO `messaging.wc_wake_enqueue_enabled` (or any other) config.yaml key for this flag — a yaml flip would be a silent no-op. When **OFF (default)**, `WAITING_CHILDREN` targets keep the legacy RAM-FIFO `set_injection` route (HTTP 202-injected, agent-tool injection, `job_inject` injected). When **ON**, WC targets route through `enqueue_message` — a durable `MessageQueue` + `Task` row + real wake turn + `MessageResponse{message_id, job_id, queued}` HTTP 200. Truthy spellings: `1`/`true`/`yes`/`on`; falsy: `0`/`false`/`no`/`off`; blank/unset/unknown → OFF (blanking the env is the instant-revert path). Read once at boot and cached — restart required to flip. Soak/flip policy: ≤2-week OFF soak on first deploy, operator flips ON; immediate flip to OFF on any silent-death incident (per `decisions.md` C1-Q2 + C2-D2.5-FLIP). D1/D2/R1/T6b are flag-independent (always active). |

> **Per-agent override:** the global `graph_recursion_limit` is a *base* — individual
> agents can exceed it via `recursion_limit_multiplier` (e.g. `5` = 5× the base) or an
> absolute `recursion_limit`, set in the agent's `meta.json`. `worker` and `coder`
> default to `5x` so long-running working agents get a larger step budget. See
> [Agent System Guide – meta.json Fields](AGENTS.md#metajson-fields).

#### Persistence Configuration

```yaml
persistence:
  db_path: ./data/instances.db
  checkpoint_interval: 1
  checkpoint_ttl_hours: 168
  checkpoint_cleanup_interval: 24
  checkpoint_max_count: 1000
```

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `persistence.db_path` | string | `./data/instances.db` | Path to main SQLite database |
| `persistence.checkpoint_interval` | integer | `1` | Create checkpoint every N state updates |
| `persistence.checkpoint_ttl_hours` | integer | `168` | Delete checkpoints older than this (1 week) |
| `persistence.checkpoint_cleanup_interval` | integer | `24` | Run cleanup every N hours |
| `persistence.checkpoint_max_count` | integer | `1000` | Maximum checkpoints per instance before cleanup |

> **Note:** In development mode (`dev.sh`), data paths use `./data_dev/` instead of `./data/`.

#### Agents Configuration

```yaml
agents:
  directory: ./agents
```

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `agents.directory` | string | `./agents` | Directory containing agent definitions |

#### Queue Configuration

```yaml
queue:
  discard_on_startup: false
  llm_retry_transient_attempts: 10
  llm_retry_timeout_attempts: 3
```

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `queue.discard_on_startup` | boolean | `false` | Clear pending jobs on server restart |
| `queue.llm_retry_transient_attempts` | integer | `10` | Retry attempts for transient LLM errors |
| `queue.llm_retry_timeout_attempts` | integer | `3` | Retry attempts for timeout errors |

#### Compaction Configuration

```yaml
compaction:
  enabled: true
  threshold: 0.80
  recent_message_window: 10
  min_recent_window: 3
  # Per-model context window overrides. Substring match; longest key wins.
  # Useful when the main model and a vision/specialized sub-model have
  # different context windows. Falls back to context_window_default, then
  # the built-in MODEL_CONTEXT_LIMITS registry, then 180k.
  context_window_default: 0
  context_window_overrides: {}
  target_ratio: 0.40
  summarization_model: ""
  min_messages_before_compaction: 10
  summarization_chunk_threshold: 0.60
```

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `compaction.enabled` | boolean | `true` | Enable automatic conversation compaction |
| `compaction.threshold` | float | `0.80` | Compact when context reaches 80% of window |
| `compaction.recent_message_window` | integer | `10` | Keep this many recent messages separate |
| `compaction.min_recent_window` | integer | `3` | Minimum recent messages to preserve |
| `compaction.context_window_default` | integer | `0` | Fallback context window when neither overrides nor the registry match. `0` = use built-in default (180k) |
| `compaction.context_window_overrides` | object | `{}` | Per-model context windows (`model_substring → tokens`). Substring match; longest key wins. Takes priority over the built-in registry |
| `compaction.target_ratio` | float | `0.40` | Target ratio after compaction (40%) |
| `compaction.summarization_model` | string | — | Model for summarization (empty = use default) |
| `compaction.min_messages_before_compaction` | integer | `10` | Minimum messages before first compaction |
| `compaction.summarization_chunk_threshold` | float | `0.60` | Chunk long histories at 60% of window |

Example: cap a smaller vision sub-model independently of the main model.

```yaml
compaction:
  context_window_default: 128000
  context_window_overrides:
    "gpt-4o-mini-vision": 32768   # tighter window for the vision model
    "claude-3-5-haiku": 50000     # haiku has a smaller effective window
```

#### Services Configuration

```yaml
services:
  task_timeout_minutes: 60
  max_task_retries: 3
  task_retry_backoff_base: 60
  task_retry_backoff_max: 3600
  stale_task_cancel_grace_seconds: 10
  graph_timeout_minutes: 55
```

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `services.task_timeout_minutes` | integer | `60` | Default timeout for service tasks |
| `services.max_task_retries` | integer | `3` | Maximum retry attempts for failed tasks |
| `services.task_retry_backoff_base` | integer | `60` | Base backoff time for retries (seconds) |
| `services.task_retry_backoff_max` | integer | `3600` | Maximum backoff time (seconds) |
| `services.stale_task_cancel_grace_seconds` | integer | `10` | Grace period before cancelling stale tasks |
| `services.graph_timeout_minutes` | integer | `55` | Timeout for LangGraph execution |

#### Job System Configuration

```yaml
job_system:
  job_retry_scheduler_enabled: null
```

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `job_system.job_retry_scheduler_enabled` | boolean/null | `null` | Enable scheduled job retry (null = auto) |

---

## Database

### Overview

agents-ensemble uses two SQLite databases:

| Database | Purpose | Default Path |
|----------|---------|--------------|
| `instances.db` | Application data | `./data/` or `./data_dev/` |
| `checkpoints.db` | LangGraph state snapshots | `./data/` or `./data_dev/` |

### Database Files

#### instances.db

Stores persistent application state:

- Agent instances
- Job queue entries
- Projects
- Message history
- Configuration snapshots

#### checkpoints.db

Stores LangGraph state for crash recovery:

- Conversation states
- Execution checkpoints
- Transaction logs

### Database Creation

Databases are automatically created on first startup:

```bash
./dev.sh  # Creates ./data_dev/ directory and databases
```

Generated structure:

```
data_dev/
├── instances.db
└── checkpoints.db
```

### Migrations

Database schemas are automatically migrated on startup using Alembic-style migrations.

Migration files location: `daemon/migrations/versions/`

> **Note:** Migrations run automatically. Do not modify database files manually.

### Development vs Production Paths

| Mode | Database Path |
|------|---------------|
| Development (`dev.sh`) | `./data_dev/` |
| Production (`ensemble-prod`) | `./data/` |

---

## Frontend Setup

### Development Mode

#### Step 1: Install Dependencies

```bash
cd frontend
npm install
```

#### Step 2: Start Dev Server

```bash
npm start
```

This starts:

- Angular dev server on **port 4199**
- API proxy to backend at **localhost:8079**

#### Step 3: Access the UI

Open browser: `http://localhost:4199`

### Production Build

```bash
make build
```

This command:

1. Runs `npm install` in frontend directory
2. Runs Angular build (`ng build`)
3. Outputs to `frontend/dist/frontend/browser/`

### Proxy Configuration

During development, Angular proxies API calls:

```json
{
  "/api/*": {
    "target": "http://localhost:8079",
    "secure": false
  },
  "/ws/*": {
    "target": "ws://localhost:8079",
    "secure": false
  }
}
```

This allows the frontend dev server to communicate with the backend without CORS issues.

---

## First Run

### Verify Backend Health

```bash
curl http://localhost:8079/api/health
```

Expected response:

```json
{
  "status": "ok",
  "version": "0.3.6"
}
```

### Access the Web UI

| Environment | URL |
|-------------|-----|
| Development | `http://localhost:8079` |
| Production | `http://localhost:8088` |

### API Documentation

Interactive API docs (Swagger UI):

```
http://localhost:8079/docs
```

### Test LLM Connection

Create an agent instance and send a test message:

```bash
# Create instance
curl -X POST http://localhost:8079/api/instances \
  -H "Content-Type: application/json" \
  -d '{"project": "test", "agent_id": "leader"}'

# Send message
curl -X POST http://localhost:8079/api/instances/<instance-id>/messages \
  -H "Content-Type: application/json" \
  -d '{"content": "Hello, say hello back"}'
```

---

## Troubleshooting

### Common Issues

| Issue | Cause | Solution |
|-------|-------|----------|
| `OPENAI_API_KEY not set` | Missing `.env` file | Create `.env` with `OPENAI_API_KEY=your-key` |
| `Address already in use` | Port occupied | Kill existing process: `lsof -ti:8079 \| xargs kill` |
| `Module not found` | Dependencies missing | Run `uv sync` or `pip install -e .` |
| `Frontend not built` | Missing build | Run `make build` |
| Database locked | Multiple instances | Only run one instance per database |
| LightRAG connection failed | Service unavailable | RAG tools disabled gracefully (non-critical) |

### Port Conflicts

Find what's using a port:

```bash
# macOS
lsof -i :8079

# Linux
ss -tlnp | grep 8079
```

Kill the process:

```bash
# By PID
kill <PID>

# Or by port
lsof -ti:8079 | xargs kill
```

### Virtual Environment Issues

```bash
# Recreate venv
rm -rf .venv
python -m venv .venv
source .venv/bin/activate
uv sync
```

### Clean Rebuild

```bash
# Remove build artifacts
make clean

# Remove databases
rm -rf data/ data_dev/

# Fresh install
make sync
./dev.sh
```

### Checking Logs

The server outputs logs to stdout. In production, redirect to a file:

```bash
./ensemble-prod > app.log 2>&1
```

### Verify Configuration

Check loaded environment variables:

```bash
source .venv/bin/activate
python -c "from daemon.config import Config; c = Config(); print(c.model_dump())"
```

---

## Next Steps

Once you have agents-ensemble running, explore these topics:

- **[Agents Architecture](architecture.md)** — Understanding how agents work
- **[API Reference](/docs)** — Interactive API documentation (Swagger UI)
- **[Creating Custom Agents](agents.md)** — Define your own agents
- **[Production Install](#method-2-production-install-make-install)** — Full production installation
- **[Configuration](#configuration)** — Environment variables and config.yaml reference
- **[Troubleshooting](#troubleshooting)** — Common issues and solutions

---

## Quick Reference

### Essential Commands

```bash
# Development
./dev.sh                           # Start dev server
uv sync                            # Install dependencies

# Production
make install                       # Full installation
./ensemble-prod                    # Run production

# Frontend
cd frontend && npm start           # Dev server
make build                         # Production build

# Utilities
make stop                          # Stop prod server
make clean                         # Clean build artifacts
```

### File Locations

| File/Directory | Purpose |
|---------------|---------|
| `config.yaml` | Application configuration |
| `.env` | Environment variables (API keys) |
| `agents/` | Agent definitions |
| `daemon/` | Backend source code |
| `frontend/` | Frontend source code |
| `data/` | Production database |
| `data_dev/` | Development database |

### Ports

| Port | Purpose |
|------|---------|
| 8079 | Development server |
| 8088 | Production server |
| 4199 | Frontend dev server |

---

*Last updated: Version 0.3.6*
