# Agent Guidelines for Ensemble

This is a **multi-agent orchestration daemon** using LangGraph where agents are defined by markdown files rather than code. The system manages persistent conversations with crash recovery, job queuing, and pluggable message sources.

## Tech Stack

- **Backend**: Python 3.11+, FastAPI, LangGraph, SQLModel, aiosqlite
- **Frontend**: Angular 21 (TypeScript)
- **Database**: SQLite (persistence + checkpointing)
- **Package Manager**: `uv` (Python), `npm` (frontend)

---

## Build / Test Commands

### Python Backend

```bash
# Install dependencies
make sync                    # Uses uv sync
make install                 # Full production install

# Development
./dev.sh                     # Auto-reload server on port 8079 (recommended)
python -m uvicorn daemon.api:app --reload --port 8079

# Run tests
pytest tests/ -v             # All tests with verbose output
pytest tests/ -v -k "test_name"           # Single test by name
pytest tests/ -v --tb=short                # With short traceback
pytest tests/unit/ -v                        # Unit tests only
pytest tests/integration/ -v                # Integration tests only

# Watch mode (if pytest-watch installed)
ptw tests/ -v
```

### Angular Frontend

```bash
cd frontend
npm install
npm start                    # Dev server on port 4200
npm test                     # Karma/Jasmine tests
npm run build                # Production build
```

### Process Management

**CRITICAL**: Never use `pkill -f "uvicorn daemon.api"` — it is forbidden in this project.

To find and kill processes:
```bash
# Find PID by port
lsof -ti:8079 | xargs kill   # Kill process on port 8079
# Or use:
fuser -k 8079/tcp            # Alternative method

# Kill by PID (get PID first)
ps aux | grep uvicorn
kill <PID>
```

---

## Code Style Guidelines

### Python Conventions

1. **Type Hints**: Required for all function signatures
   def process_message(message: str, instance_id: str) -> dict[str, Any]:

2. **Logging**: Use module-level logger
   logger = logging.getLogger(__name__)

3. **Docstrings**: Required for public methods and classes
   async def fetch_instance(instance_id: str) -> Instance | None:
       """Fetch an instance by ID or return None if not found.
       
       Args:
           instance_id: Unique instance identifier
       
       Returns:
           Instance object or None
       """

4. **Async/Await**: Use for all I/O operations
    async with aiosqlite.connect(db_path) as db:
        result = await db.execute("SELECT * FROM instances")

5. **Imports**: Relative imports within daemon package
   from .models import Instance
   from ..graph import build_instance_graph

6. **Error Handling**: Use Pydantic validation + explicit exception handling
   try:
       result = await risky_operation()
   except ValueError as e:
       logger.warning(f"Invalid input: {e}")
       raise HTTPException(status_code=400, detail=str(e))

### Naming Conventions

| Type | Convention | Example |
|------|------------|---------|
| Variables | snake_case | `instance_id`, `message_count` |
| Functions | snake_case | `fetch_instance()`, `build_graph()` |
| Classes | PascalCase | `InstanceManager`, `ThinkingChatOpenAI` |
| Constants | SCREAMING_SNAKE | `MAX_RETRIES`, `DEFAULT_TIMEOUT` |
| Private | _prefix | `_internal_state`, `_cache` |
| Type aliases | PascalCase | `MessageHandler`, `ToolResult` |

### Pydantic Models

All API request/response models should use Pydantic v2:

```python
from pydantic import BaseModel, Field
from typing import Optional

class InstanceCreate(BaseModel):
    project: str = Field(..., description="Project name")
    agent_id: str = Field(default="leader", description="Agent to use")
    
class InstanceResponse(BaseModel):
    id: str
    project: str
    created_at: datetime
    status: InstanceStatus
```

### File Organization

```raw
daemon/
├── __main__.py          # Entry point
├── api.py               # FastAPI routes (1500+ lines)
├── graph.py             # LangGraph definition
├── manager.py           # Instance orchestration
├── loader.py            # Agent/markdown loader
├── models.py            # Pydantic models
├── config.py            # Configuration loading
├── tools/               # Agent tools
│   ├── bash.py
│   ├── filesystem.py
│   └── instance.py
└── sources/             # Message source adapters
    ├── telegram.py
    └── scheduler.py
```

---

## Agent Definition Structure

Agents are defined in markdown files under `agents/<agent_id>/`:

```raw
agents/
├── <agent_id>/
│   ├── meta.json          # Agent metadata
│   ├── soul.md            # Identity/personality
│   ├── rule.md            # Constraints (highest priority)
│   ├── skill.md           # Capabilities (single, optional)
│   ├── skills/            # Multiple skills directory
│   │   └── <skill>/
│   │       └── skill.md
│   ├── tools.md           # Tool documentation
│   ├── workflow.md        # Methodology
│   ├── memory.md          # Long-term knowledge
│   └── memories/          # Persistent memories
```

**Prompt Composition Order**: soul → rule → skill → skills → tools → workflow → memory

---

## Configuration

### config.yaml (Main Configuration)

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

### Environment Variables (.env)

```raw
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...   # Optional
LOG_LEVEL=INFO
```

---

## Testing Guidelines

1. **Mock External APIs**: Use `tests/conftest.py` fixtures that mock langgraph modules
2. **Unit Tests**: Place in `tests/unit/`
3. **Integration Tests**: Place in `tests/integration/`
4. **Async Tests**: Use `pytest-asyncio` with `async def test_`
5. **Mock LLM Server**: Use `tests/mock_llm_server.py` for testing with real graph execution

```python
# Example test
import pytest
from daemon.manager import InstanceManager

@pytest.mark.asyncio
async def test_instance_creation():
    manager = InstanceManager()
    instance = await manager.create_instance(project="test")
    assert instance.id is not None
    assert instance.project == "test"
```

---

## Important Notes

1. **No Linting Config**: Project lacks ruff/black/mypy configuration. Maintain clean, consistent code manually.

2. **Frontend Prettier Config** (`frontend/package.json`):
   - Print width: 100
   - Single quotes: true
   - Angular HTML files use angular parser
3. **Database**: Two SQLite databases — one for persistence, one for LangGraph checkpoints. Both use aiosqlite for async access.

4. **LangGraph Version**: Uses LangGraph 0.3+ with checkpoint-sqlite for state persistence.

5. **SSE (Server-Sent Events)**: This project uses SSE for real-time streaming. When performing browser automation, do not wait for the network to become idle — it will never happen due to the persistent SSE connection.