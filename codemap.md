# Repository Atlas: auto-code (Ensemble)

## Project Responsibility
A **persistent multi-agent AI daemon** built with LangGraph where agents are defined by markdown files, not code. Features session hierarchy, crash recovery via SQLite checkpoints, and real-time streaming via SSE.

## System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        FRONTEND (Angular 21)                     │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────┐ │
│  │  Home    │  │  Chat    │  │ Services │  │    Components    │ │
│  │  Page    │  │  Page    │  │ API/SSE  │  │ 6 UI Components  │ │
│  └──────────┘  └──────────┘  └──────────┘  └──────────────────┘ │
└───────────────────────────────┬─────────────────────────────────┘
                                │ HTTP/SSE
┌───────────────────────────────▼─────────────────────────────────┐
│                      DAEMON (FastAPI + LangGraph)                │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────┐ │
│  │   API    │  │ Manager  │  │  Queue   │  │    Persistence   │ │
│  │ Routes   │  │ Sessions │  │ Messages │  │  SQLite + Check  │ │
│  └──────────┘  └──────────┘  └──────────┘  └──────────────────┘ │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────┐ │
│  │  Graph   │  │  Tools   │  │  Events  │  │     Sources      │ │
│  │LangGraph │  │ 7 Tools  │  │  SSE     │  │ LLM Providers    │ │
│  └──────────┘  └──────────┘  └──────────┘  └──────────────────┘ │
└───────────────────────────────┬─────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────┐
│                       AGENTS (Markdown)                          │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────┐ │
│  │  Leader  │  │  Coder   │  │  _mother │  │   _inner_soul    │ │
│  │Orchestr. │  │ Executor │  │  Factory │  │  Self-Evolution  │ │
│  └──────────┘  └──────────┘  └──────────┘  └──────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

## System Entry Points
| File | Purpose |
|------|---------|
| `start.py` | Cross-platform daemon launcher (loads .env, starts uvicorn) |
| `start.sh` | Bash launcher for Unix systems |
| `daemon/__main__.py` | Python module entry point (`python -m daemon`) |
| `daemon/api.py:app` | FastAPI application (14 REST endpoints + SSE) |
| `frontend/src/main.ts` | Angular bootstrap entry point |

## Directory Map (Aggregated)

### Backend (Python)

| Directory | Responsibility | Detailed Map |
|-----------|----------------|--------------|
| `daemon/` | Persistent multi-session orchestration with LangGraph, FastAPI, message queuing, SSE | [View Map](daemon/codemap.md) |
| `daemon/sources/` | Multi-source message abstraction (Telegram, webhooks, WhatsApp) with rate limiting, circuit breaker | [View Map](daemon/sources/codemap.md) |
| `daemon/tools/` | 7 LangGraph-compatible tools (filesystem, bash, session, inner_soul, agent_mother, time) | [View Map](daemon/tools/codemap.md) |

### Agent Definitions (Markdown)

| Directory | Responsibility | Detailed Map |
|-----------|----------------|--------------|
| `agents/` | Agent configuration system - markdown-based behavior definitions | [View Map](agents/codemap.md) |
| `agents/leader/` | Orchestrator agent - delegates to specialists via fire-and-forget | [View Map](agents/leader/codemap.md) |
| `agents/coder/` | Code generation agent - interfaces with opencode_skill CLI | [View Map](agents/coder/codemap.md) |
| `agents/_mother/` | Agent factory - creates, modifies, lists, deletes agents | [View Map](agents/_mother/codemap.md) |
| `agents/_inner_soul/` | Self-evolution engine - semantic classification for agent growth | [View Map](agents/_inner_soul/codemap.md) |
| `agents/_baby_template/` | Spawnable blank agent template for new agent creation | [View Map](agents/_baby_template/codemap.md) |

### Frontend (Angular 21)

| Directory | Responsibility | Detailed Map |
|-----------|----------------|--------------|
| `frontend/` | Angular 21 SPA configuration, build setup, proxy config | [View Map](frontend/codemap.md) |
| `frontend/src/` | App bootstrap with standalone components | [View Map](frontend/src/codemap.md) |
| `frontend/src/app/` | Core app architecture - routing, models, service layer | [View Map](frontend/src/app/codemap.md) |
| `frontend/src/app/services/` | HTTP API client + SSE streaming service | [View Map](frontend/src/app/services/codemap.md) |
| `frontend/src/app/components/` | 6 shared UI components (chat, session list, agent selector) | [View Map](frontend/src/app/components/codemap.md) |
| `frontend/src/app/pages/` | Route targets - home (agent selection) and chat views | [View Map](frontend/src/app/pages/codemap.md) |
| `frontend/src/app/models/` | TypeScript interfaces aligned with backend Pydantic schemas | [View Map](frontend/src/app/models/codemap.md) |

## Key Design Patterns

| Pattern | Location | Purpose |
|---------|----------|---------|
| **Event-Driven** | `daemon/events.py` | SSE pub/sub for real-time client updates |
| **Message Queue** | `daemon/queue.py` | SQLite-backed FIFO with priority ordering |
| **Circuit Breaker** | `daemon/sources/circuit_breaker.py` | Fault tolerance for external LLM calls |
| **Factory** | `daemon/tools/*.py` | Context-dependent tool creation |
| **Checkpoint/Recovery** | `daemon/persistence.py` | LangGraph SqliteSaver for crash recovery |
| **Agent Factory** | `agents/_mother/` | Runtime agent creation from templates |
| **Self-Modification** | `agents/_inner_soul/` | Agents can evolve their own prompts |

## Data Flow

```
User Message → API (/sessions/:id/messages)
                    ↓
            MessageQueue.enqueue()
                    ↓
            SessionManager._process_queue()
                    ↓
            LangGraph.invoke() → LLM (via Sources)
                    ↓
            Tool Calls (if any) → filesystem/bash/session
                    ↓
            EventBroadcaster → SSE → Frontend
```

## Agent Hierarchy

```
leader (orchestrator)
├── spawn_session → coder (code generation)
├── spawn_session → reviewer (code review)
└── spawn_session → [custom agents via _mother]

_mother (factory, immutable)
└── agent_create → copies _baby_template → new agent

_inner_soul (self-evolution, immutable)
└── modify_agent → updates soul.md/workflow.md/memory.md
```

## API Endpoints

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/health` | Health check |
| GET | `/agents` | List available agents |
| POST | `/sessions` | Spawn new session |
| GET | `/sessions` | List all sessions |
| GET | `/sessions/:id` | Get session info |
| DELETE | `/sessions/:id` | Terminate session |
| POST | `/sessions/:id/messages` | Send message to agent |
| GET | `/sessions/:id/messages` | Get message history |
| GET | `/sessions/:id/events` | SSE event stream |
| GET | `/sessions/:id/completion-report` | Get completion status |

## Configuration

| File | Purpose |
|------|---------|
| `.env` | Environment variables (OPENAI_API_KEY, MODEL, BASE_URL) |
| `pyproject.toml` | Python dependencies, entry points |
| `daemon/config.py` | Pydantic Settings for daemon configuration |

## Quick Start

```bash
# 1. Setup
python -m venv .venv && source .venv/bin/activate
pip install -e .

# 2. Configure
cp .env.example .env
# Edit .env, set OPENAI_API_KEY

# 3. Start daemon
./start.sh

# 4. Start frontend (optional)
cd frontend && npm install && npm start
```

## Tech Stack

**Backend**: Python 3.11+, FastAPI, LangGraph, LangChain, SQLite, Pydantic, SSE-Starlette

**Frontend**: Angular 21, TypeScript 5.9, Angular Material, SCSS, RxJS

**LLM**: OpenAI-compatible endpoints (OpenAI, Azure, local models)
