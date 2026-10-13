# Phase 3: Manager Wiring (Separate Engine)

## Objective
Wire the `OpenCodeSessionRegistry` into `daemon/manager.py` with a **dedicated SQLite engine** at `{data_dir}/opencode_sessions.db` (separate file from the main ensemble DB). Run `recover_from_registry()` on startup.

## Coupling
- **Depends on**: Phase 1 (production code), Phase 2 (tools reference `manager._opencode_registry`)
- **Coupling type**: tight
- **Shared files with other phases**:
  - `daemon/manager.py` (MODIFY)
- **Why this coupling**: Tools (Phase 2) need `manager._opencode_registry` available; this phase creates that attribute.

## Context
- **CRITICAL**: The user's Critical Note says: "OpenCode session registry uses a SEPARATE SQLite DB file"
- The main ensemble engine handles instances, projects, message queues, etc.
- A separate engine means a separate connection pool, separate SQLite file, and independent persistence layer
- Path convention: `{data_dir}/opencode_sessions.db`
- **Blocker 1 fix**: Table created via `OpenCodeSessionRecord.__table__.create()` — NOT `SQLModel.metadata.create_all()`
- **Blocker 3 fix**: Recovery in `initialize()` (line 972), cleanup in `shutdown()` (line 2563)

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Add `create_opencode_engine` factory function | Creates dedicated SQLite engine + table via `__table__.create()` | `daemon/opencode/repository.py` (already exists) or inline in manager |
| 2 | Initialize `_opencode_engine` in manager `__init__` | After main engine creation (~line 600) | `daemon/manager.py` (MODIFY) |
| 3 | Initialize `OpenCodeSessionRegistry` | With the repository and dedicated engine | `daemon/manager.py` (MODIFY) |
| 4 | Add `opencode_registry` property | Public read-only access | `daemon/manager.py` (MODIFY) |
| 5 | Call `recover_from_registry()` in `initialize()` | After existing initialization (~line 1034) | `daemon/manager.py` (MODIFY) |
| 6 | Add shutdown cleanup in `shutdown()` | Call `registry.shutdown()` in shutdown steps | `daemon/manager.py` (MODIFY) |

## Key Files

### MODIFY: `daemon/manager.py`

#### Step 1: In `__init__`, after line 600 (after `_project_repository`):

```python
# ── OpenCode session integration (separate engine) ──────────────────
from daemon.opencode.repository import create_opencode_session_repository
from daemon.opencode.registry import OpenCodeSessionRegistry
from daemon.repositories.factory import create_engine_from_config
from daemon.config import DatabaseConfig

# Dedicated engine for opencode sessions — separate file at
# {data_dir}/opencode_sessions.db (per Critical Note: separate persistence).
# Uses create_engine_from_config (Blocker 2 Rev 4: consistent with manager.py:510-511
# pattern, which handles SQLite pragmas like WAL mode, busy_timeout,
# foreign_keys=ON, check_same_thread=False automatically).
# Table created via __table__.create() internally — creates ONLY opencode_sessions
# table, NOT all 22+ ensemble tables (Blocker 1 fix).
opencode_db_path = self.data_dir / "opencode_sessions.db"
self._opencode_engine = create_engine_from_config(
    DatabaseConfig.sqlite(db_path=str(opencode_db_path))
)
self._opencode_session_repository = create_opencode_session_repository(self._opencode_engine)
self._opencode_registry = OpenCodeSessionRegistry(
    repository=self._opencode_session_repository,
)

logger.info(f"OpenCode session registry initialized at {opencode_db_path}")
```

**Key points**:
- `create_engine_from_config(DatabaseConfig.sqlite(...))` handles all SQLite pragmas (WAL, busy_timeout, foreign_keys=ON, check_same_thread) consistent with the main ensemble engine at `manager.py:510-511`.
- `create_opencode_session_repository()` calls `OpenCodeSessionRecord.__table__.create(engine, checkfirst=True)` — creates ONLY the `opencode_sessions` table + index. Does NOT call `SQLModel.metadata.create_all()` which would create all 22+ tables.
- **No raw `sqlalchemy.create_engine` or manual `@event.listens_for` pragmas** — those are redundant when `create_engine_from_config` is used.

#### Step 2: Add public property (after `data_dir` property, ~line 920):

```python
@property
def opencode_registry(self) -> "OpenCodeSessionRegistry":
    """Public read-only access to the opencode session registry.
    
    Used by daemon/tools/external_opencode.py to access session state
    from agent tool calls.
    """
    return self._opencode_registry
```

#### Step 3: In `initialize()`, after maintenance service start (~line 1034):

```python
# ── Recover opencode sessions on startup ───────────────────────────
# Loads all persisted sessions from the dedicated opencode DB and starts
# their background state-machine loops. Must happen after engine is ready
# but before agents can use the tools.
# NOTE: Uses initialize() NOT start() (Blocker 3 fix).
try:
    recovered = await self._opencode_registry.recover_from_registry()
    logger.info(f"Recovered {recovered} opencode session(s) from registry")
except Exception as exc:
    logger.warning(f"Failed to recover opencode sessions: {exc}")
```

#### Step 4: In `shutdown()`, add to the shutdown steps list (~line 2605):

```python
steps = [
    ...  # existing steps
    ("shutdown_opencode_registry", self._shutdown_opencode_registry()),
]
```

And add the method:

```python
async def _shutdown_opencode_registry(self) -> None:
    """Shutdown the opencode session registry during daemon shutdown.
    
    NOTE: Uses shutdown() NOT stop() (Blocker 3 fix).
    """
    if hasattr(self, '_opencode_registry') and self._opencode_registry:
        try:
            await self._opencode_registry.shutdown()
        except Exception as exc:
            logger.warning(f"Error during opencode registry shutdown: {exc}")
```

## Engine Architecture

```
┌──────────────────────────────────────────────────────┐
│  InstanceManager                                     │
│  ┌────────────────────────────────────────────────┐  │
│  │  self._engine  (main ensemble DB)              │  │
│  │  → data/instances.db (SQLite)                  │  │
│  │  → projects, instances, message_queues, etc.   │  │
│  │  → ALL 22+ ensemble tables                     │  │
│  └────────────────────────────────────────────────┘  │
│  ┌────────────────────────────────────────────────┐  │
│  │  self._opencode_engine  (dedicated)            │  │
│  │  → data/opencode_sessions.db (SQLite)          │  │
│  │  → opencode_sessions table ONLY                │  │
│  │  → Created via __table__.create()              │  │
│  │  → Has its own connection pool                 │  │
│  └────────────────────────────────────────────────┘  │
│  ┌────────────────────────────────────────────────┐  │
│  │  self._opencode_registry                       │  │
│  │  → {session_id: OpenCodeSessionManager}        │  │
│  │  → in-memory state machine map                 │  │
│  │  → on_state_change writes to repository        │  │
│  └────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────┘
```

**Why `__table__.create()` and not `SQLModel.metadata.create_all()`**:

`SQLModel.metadata` is a global registry containing every SQLModel in the entire project. Calling `create_all(opencode_engine)` would create all 22+ tables (`instances`, `projects`, `job_queues`, `message_queue`, `tasks`, `events`, `mcp_servers`, `critical_notes`, etc.) in the dedicated `opencode_sessions.db` file — completely defeating the purpose of a separate persistence layer.

`OpenCodeSessionRecord.__table__.create(engine, checkfirst=True)` creates ONLY the `opencode_sessions` table and its `ix_opencode_sessions_id` index.

## Constraints
- Must use a **separate SQLite file** (NOT the main ensemble DB)
- Table created via `__table__.create()` (Blocker 1 fix)
- No migration file needed (Blocker 2 fix — deleted)
- `data_dir` from `manager.data_dir` (Path) — production `./data/`, dev `./data_dev/`
- Engine must use `create_engine_from_config` pattern for proper SQLite pragmas
- Recovery in `initialize()` (Blocker 3 fix — NOT `start()`)
- Shutdown in `shutdown()` (Blocker 3 fix — NOT `stop()`)
- `OpenCodeSessionRegistry` instance is a singleton on the manager

## Deliverables
- [ ] `self._opencode_engine` initialized in `InstanceManager.__init__` with separate SQLite file
- [ ] `self._opencode_session_repository` created via `create_opencode_session_repository()` (uses `__table__.create()`)
- [ ] `self._opencode_registry` initialized
- [ ] `opencode_registry` public property
- [ ] `recover_from_registry()` called in `initialize()` (not `start()`)
- [ ] `registry.shutdown()` called in `shutdown()` (not `stop()`)
- [ ] `data/opencode_sessions.db` (or `data_dev/opencode_sessions.db`) created on first run
- [ ] Only `opencode_sessions` table in the dedicated DB (NOT all 22+ tables)
- [ ] Daemon starts cleanly with `[INFO] OpenCode session registry initialized at {path}`
