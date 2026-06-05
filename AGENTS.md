# Agent Guidelines for Ensemble

Multi-agent orchestration daemon using LangGraph with agents defined by markdown files. Manages persistent conversations with crash recovery, job queuing, and pluggable message sources.

## Tech Stack

- **Backend**: Python 3.13+, FastAPI, LangGraph 0.3+, SQLModel, aiosqlite
- **Frontend**: Angular 21 (TypeScript)
- **Package Manager**: `uv` (Python), `npm` (frontend)

---

## Build / Test Commands

### Python Backend

```bash
# Install dependencies
make sync                    # uv sync
make install                 # Full production install

# Development server (auto-reload on port 8079)
./dev.sh
# Or: python -m uvicorn daemon.api:app --reload --port 8079

# Run tests
pytest tests/ -v                         # All tests verbose
pytest tests/ -v -k "test_name"          # Single test by name
pytest tests/ -v --tb=short               # Short traceback
pytest tests/unit/ -v                     # Unit tests only
pytest tests/integration/ -v              # Integration tests only
pytest tests/job_queue/ -v               # Job queue tests only
ptw tests/ -v                            # Watch mode (if pytest-watch installed)
```

### Angular Frontend

```bash
cd frontend && npm install
npm start          # Dev server port 4200
npm test           # Karma/Jasmine tests
npm run build      # Production build
```

### Process Management

**CRITICAL**: Never use `pkill -f "uvicorn daemon.api"` — forbidden.

**NEVER kill port 8088** — this is the production backend port.

```bash
# Find/kill by port (dev: 8079, NEVER 8088)
lsof -ti:8079 | xargs kill   # or: fuser -k 8079/tcp

# Find by process name
ps aux | grep uvicorn
kill <PID>
```

---

## Code Style Guidelines

### Python Conventions

| Convention | Rule | Example |
|------------|------|---------|
| Type Hints | Required on all signatures | `def foo(x: str) -> dict[str, Any]:` |
| Logging | Module-level logger | `logger = logging.getLogger(__name__)` |
| Docstrings | Required on public methods | Google-style with Args/Returns |
| Async I/O | Use `async`/`await` for all DB/network | `async with aiosqlite.connect(...)` |
| Imports | Relative within daemon package | `from .models import Instance` |
| Error Handling | Pydantic validation + explicit try/except | Log warning, raise HTTPException |

### Naming Conventions

| Type | Convention | Example |
|------|------------|---------|
| Variables/Functions | snake_case | `instance_id`, `fetch_instance()` |
| Classes | PascalCase | `InstanceManager` |
| Constants | SCREAMING_SNAKE | `MAX_RETRIES` |
| Private | _prefix | `_internal_state` |
| Type aliases | PascalCase | `MessageHandler` |

### Pydantic Models

Use Pydantic v2 with `Field` for documentation:

```python
from pydantic import BaseModel, Field

class InstanceCreate(BaseModel):
    project: str = Field(..., description="Project name")
    agent_id: str = Field(default="leader", description="Agent ID")

class InstanceResponse(BaseModel):
    id: str
    project: str
    status: InstanceStatus
```

---

## File Organization

```
daemon/
├── __main__.py          # Entry point
├── api.py               # FastAPI routes
├── graph.py             # LangGraph definition
├── manager.py           # Instance orchestration
├── loader.py            # Agent/markdown loader
├── models.py            # Pydantic models
├── config.py            # Config loading
├── tools/               # Agent tools (bash, filesystem, instance)
└── sources/             # Message adapters (telegram, scheduler)
```

---

## Agent Definition Structure

Agents live in `agents/<agent_id>/` with files:

| File | Purpose |
|------|---------|
| `meta.json` | Agent metadata |
| `soul.md` | Identity/personality |
| `rule.md` | Constraints (highest priority) |
| `skill.md` | Single capability (optional) |
| `skills/<skill>/skill.md` | Multiple skills |
| `tools.md` | Tool documentation |
| `workflow.md` | Methodology |
| `memory.md` / `memories/` | Long-term knowledge |

**Prompt Order**: soul → rule → skill → skills → tools → workflow → memory

---

## Configuration

### config.yaml

```yaml
llm:
  model: gpt-4o
  temperature: 0.7
  max_tokens: 4096

daemon:
  host: 0.0.0.0
  port: 8079

limits:
  max_concurrent_instances: 10
  instance_timeout_minutes: 60

persistence:
  database: data/ensemble.db
  checkpoint_database: data/checkpoints.db
```

### Environment (.env)

```
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...   # Optional
LOG_LEVEL=INFO
LOG_LEVEL_DAEMON=DEBUG
```

---

## Testing Guidelines

- **Mock External APIs**: Fixtures in `tests/conftest.py` mock langgraph
- **Async Tests**: Use `pytest-asyncio` with `async def test_`
- **Mock LLM Server**: `tests/mock_llm_server.py` for real graph testing
- **Test DBs**: Use separate `test_*.db` files, clean up after tests

```python
import pytest

@pytest.mark.asyncio
async def test_example():
    manager = InstanceManager()
    result = await manager.create_instance(project="test")
    assert result.id is not None
```

---

## Important Notes

1. **No Linting Config** — No ruff/black/mypy. Maintain consistency manually.
2. **Frontend**: Print width 100, single quotes, Angular HTML parser.
3. **Databases**: Two SQLite DBs (persistence + checkpoints) using aiosqlite.
4. **SSE**: Real-time streaming via SSE. Don't wait for network idle.
