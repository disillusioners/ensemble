# Phase 3: DB Tools Implementation

## Objective
Create the 5 `db_*` tool functions and wire them into the tool assembly pipeline. This phase connects Phase 1 (repository) and Phase 2 (pool manager) into agent-usable tools registered under the `"db"` category.

## Coupling
- **Depends on:** Phase 1 (DbConnectionRepository) + Phase 2 (ConnectionPoolManager)
- **Coupling type:** tight — directly imports and instantiates both Phase 1 and Phase 2 components
- **Shared files with other phases:** None (new tool module file)
- **Shared APIs/interfaces:** Tool functions consumed by agents via `create_instance_tools()`
- **Why this coupling:** Tools are the integration point — they call repository CRUD methods and pool manager query methods. They must compile against actual implementations.

## Context
- **Phase 1 delivered:** `DbConnectionRepository` (CRUD, NO credential_manager per N1 — stores opaque encrypted strings) and `create_db_connection_repository()`
- **Phase 2 delivered:** `ConnectionPoolManager` (singleton at manager level — lazy pools, execute_select, test_connection, dispose, three-case DSN). Created in `InstanceManager.__init__` with injected `CredentialManager`, accessed via `manager.db_pool_manager`.
- **Key decision D1/N1:** Encryption/decryption happens in the TOOL functions, NOT the repository. `db_conn_add` encrypts before `repository.create()`, pool manager decrypts when building DSN. Repository is a dumb CRUD layer for opaque strings.
- **Key decision D5:** Tools created by `create_db_tools(manager, instance_id, repository, pool_manager)` — the factory RECEIVES the shared repository and pool manager from the manager (NOT creating its own). This prevents pool proliferation.
- **Key decision D8/N5:** `CredentialManager` is injected from `app.state` into `InstanceManager` — the pool manager already holds it. The factory does NOT need a separate CredentialManager reference (it uses the pool manager's).
- **Key decision D6:** Category `"db"`, names: `db_conn_add`, `db_conn_delete`, `db_conn_list`, `db_conn_test`, `db_postgres_dml_select`
- **Key decision D4/N4:** SELECT-only enforcement via keyword analysis (no external dependency), with string-literal stripping to prevent false positives

### How Tools Are Currently Assembled (for wiring reference)

In `daemon/tools/instance.py::create_instance_tools()`:
1. Static tools (bash, filesystem, time, etc.) are imported at module level
2. Manager-dependent tools are created via factory functions: `create_help_tool()`, `create_mother_tools()`, etc.
3. All tools are collected in a `tools: list` 
4. `scan_tools_for_full_docs(tools)` populates metadata
5. `_apply_tool_filter()` filters by agent's `tools.allow`/`tools.deny`
6. Returns filtered tool list

**We add `create_db_tools(manager, instance_id, repository, pool_manager)` following the same pattern** (N8: factory takes 4 args, not 2) and call it in `create_instance_tools()`.

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Create tool module skeleton | `daemon/tools/db_tools.py`. Define `CATEGORY_NAME`, `CATEGORY_DOC`, and `create_db_tools(manager, instance_id, repository, pool_manager)` factory function (N8: 4 args). The factory RECEIVES the shared `repository` and `pool_manager` from the manager — it does NOT create its own `CredentialManager` or `ConnectionPoolManager` (D5, D8, C3). | `daemon/tools/db_tools.py` (NEW) |
| 2 | Implement `db_conn_add` | Tool to register a named connection. Args: `connection_name`, `db_type`, `host`, `port`, `database`, `username`, `password`, `ssl_mode`. **N1: Encrypt password HERE in the tool** via `credential_manager.encrypt({"password": password})` before calling `repository.create(credentials=encrypted_str)`. Returns confirmation (without password). | `daemon/tools/db_tools.py` (MODIFY) |
| 3 | Implement `db_conn_delete` | Tool to remove a connection by name. Calls `repository.delete()` then `pool_manager.dispose(name)` (cleanup). Returns confirmation. | `daemon/tools/db_tools.py` (MODIFY) |
| 4 | Implement `db_conn_list` | Tool to list all connections. Calls `repository.list_public()`. Returns formatted table without secrets. | `daemon/tools/db_tools.py` (MODIFY) |
| 5 | Implement `db_conn_test` | Tool to test a connection. Calls `pool_manager.test_connection(name)`. Returns formatted success/failure message. | `daemon/tools/db_tools.py` (MODIFY) |
| 6 | Implement `db_postgres_dml_select` | Tool to run SELECT. Args: `connection_name`, `query`, `timeout` (optional), `max_rows` (optional). Validates SELECT-only (task 7), then calls `pool_manager.execute_select()`. Returns formatted results. | `daemon/tools/db_tools.py` (MODIFY) |
| 7 | Implement SELECT-only guard | Function `_validate_select_only(query: str) -> None` that: (1) strips comments (`--`, `/* */`), (2) checks first keyword is SELECT or WITH, (3) **strips string literals** (N4: replace `'...'` with `''` before keyword scan to avoid false positives on keywords inside string values), (4) scans for forbidden keywords including `INTO`, INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, TRUNCATE, GRANT, REVOKE via word-boundary regex. Raises `ValueError` with clear message if invalid. | `daemon/tools/db_tools.py` (MODIFY) |
| 8 | Register in CATEGORY_MODULES | Add `"db": "daemon.tools.db_tools"` to `CATEGORY_MODULES` dict in `_tool_registry.py`. | `daemon/tools/_tool_registry.py` (MODIFY) |
| 9 | Wire into `create_instance_tools` | Import `create_db_tools` and call it in `create_instance_tools()` in `instance.py`. Pass `manager.db_connection_repository` and `manager.db_pool_manager` (shared instances). Add resulting tools to the `tools` list BEFORE `scan_tools_for_full_docs()`. | `daemon/tools/instance.py` (MODIFY) |

## Key Files

### NEW Files
- `daemon/tools/db_tools.py` — All 5 DB tools + factory function + SELECT guard

### MODIFIED Files
- `daemon/tools/_tool_registry.py` — Add `"db"` to `CATEGORY_MODULES`
- `daemon/tools/instance.py` — Import and call `create_db_tools()` in `create_instance_tools()`

## Detailed Design

### Tool Module Structure

```python
# daemon/tools/db_tools.py
"""Database tools for external database connection management and querying."""

import logging
from typing import TYPE_CHECKING, Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from ._tool_registry import register_tool_category

if TYPE_CHECKING:
    from daemon.manager import InstanceManager
    from daemon.repositories.db_connection.repository import DbConnectionRepository
    from daemon.services.db_pool_manager import ConnectionPoolManager

logger = logging.getLogger(__name__)

CATEGORY_NAME = "Database"
CATEGORY_DOC = """\
Manage external database connections and run queries.

**Connection Management:**
- `db_conn_add` — Register a named database connection
- `db_conn_delete` — Remove a connection
- `db_conn_list` — List all saved connections
- `db_conn_test` — Test that a connection works

**Query Execution:**
- `db_postgres_dml_select` — Run a SELECT query (read-only)
"""

# Constants
DEFAULT_QUERY_TIMEOUT = 30
DEFAULT_MAX_ROWS = 1000

# Forbidden SQL keywords (defense-in-depth, not a security boundary)
# INTO is included to block SELECT ... INTO (creates a table = DDL)
_FORBIDDEN_KEYWORDS = frozenset({
    "INSERT", "INTO", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE",
    "TRUNCATE", "GRANT", "REVOKE", "MERGE", "REPLACE", "CALL",
    "EXEC", "EXECUTE", "VACUUM", "REINDEX", "REFRESH",
})
```

### SELECT-Only Guard

```python
def _validate_select_only(query: str) -> None:
    """Validate that a SQL query is SELECT-only.
    
    Defense-in-depth guard. Not a complete SQL parser.
    Raises ValueError if the query contains non-SELECT statements.
    
    Checks:
    1. Strip comments and whitespace
    2. First keyword must be SELECT or WITH (CTE)
    3. Strip string literals (N4) — replace '...' with '' before keyword scan
    4. Scan for forbidden DML/DDL keywords
    
    Args:
        query: SQL query string.
    
    Raises:
        ValueError: If query is not a SELECT-only statement.
    """
    import re
    
    if not query or not query.strip():
        raise ValueError("Query cannot be empty")
    
    # Strip SQL comments (-- single line, /* multi-line */)
    cleaned = re.sub(r'--[^\n]*', '', query)
    cleaned = re.sub(r'/\*.*?\*/', '', cleaned, flags=re.DOTALL)
    cleaned = cleaned.strip().rstrip(';').strip()
    
    if not cleaned:
        raise ValueError("Query is empty after stripping comments")
    
    # Get first word (should be SELECT or WITH)
    first_word = cleaned.split()[0].upper()
    if first_word not in ("SELECT", "WITH"):
        raise ValueError(
            f"Only SELECT queries are allowed. "
            f"Query starts with '{first_word}'."
        )
    
    # N4: Strip string literals before keyword scan.
    # Replace single-quoted strings (including '' escapes) with empty strings.
    # This prevents false positives on keywords inside string values:
    #   SELECT * FROM logs WHERE msg LIKE '%INTO%'  → no false positive
    #   SELECT note FROM t WHERE note = 'DROP TABLE' → no false positive
    cleaned_no_strings = re.sub(r"'(?:[^']|'')*'", "''", cleaned)
    
    # Scan for forbidden keywords as standalone words on the string-stripped version
    # Use word boundaries to avoid false positives (e.g., a column named "update_time")
    for keyword in _FORBIDDEN_KEYWORDS:
        if re.search(rf'\b{keyword}\b', cleaned_no_strings, re.IGNORECASE):
            raise ValueError(
                f"Forbidden keyword '{keyword}' detected. "
                f"Only SELECT queries are allowed."
            )
```

### Tool Function Examples

```python
@register_tool_category("db")
@tool
async def db_conn_add(
    connection_name: str,
    db_type: str,
    host: str,
    port: int | None = None,
    database: str | None = None,
    username: str | None = None,
    password: str | None = None,
    ssl_mode: str = "prefer",
) -> str:
    """Register a named database connection. Passwords are encrypted.
    Use tool_help("db_conn_add") for details.
    """
    # N1: Encrypt password HERE in the tool, then pass opaque string to repository
    credentials = None
    if password:
        credentials = credential_manager.encrypt({"password": password})
    
    repository.create(
        connection_name=connection_name,
        db_type=db_type,
        host=host,
        port=port,
        database=database,
        username=username,
        credentials=credentials,  # opaque encrypted string
        ssl_mode=ssl_mode,
    )
    # Return confirmation WITHOUT password
```

```python
@register_tool_category("db")
@tool
async def db_postgres_dml_select(
    connection_name: str,
    query: str,
    timeout: int = 30,
    max_rows: int = 1000,
) -> str:
    """Run a SELECT query against a named PostgreSQL connection.
    Use tool_help("db_postgres_dml_select") for details.
    """
    # 1. Validate SELECT-only
    _validate_select_only(query)
    # 2. Execute via pool manager
    result = await pool_manager.execute_select(connection_name, query, timeout, max_rows)
    # 3. Format results as table
    # ... format columns + rows as markdown-style table
    # 4. Return formatted string
```

### Factory Function

```python
def create_db_tools(
    manager: "InstanceManager",
    current_instance_id: str,
    repository: "DbConnectionRepository",      # ← SHARED from manager.db_connection_repository
    pool_manager: "ConnectionPoolManager",     # ← SHARED from manager.db_pool_manager
) -> list[Any]:
    """Create database tools with injected shared services.
    
    The repository and pool_manager are SHARED SINGLETONS from the
    InstanceManager (D5). They are NOT created here — this prevents
    pool proliferation across instances.
    
    Args:
        manager: The InstanceManager instance.
        current_instance_id: The current instance ID.
        repository: Shared DbConnectionRepository (from manager.db_connection_repository).
        pool_manager: Shared ConnectionPoolManager (from manager.db_pool_manager).
    
    Returns:
        List of db tool functions.
    """
    # N1: The factory needs credential_manager for encrypt/decrypt in tools.
    # The pool_manager already holds a reference (injected from app.state — N5/D8).
    # Access it via the manager for the db_conn_add tool's encryption:
    credential_manager = manager.credential_manager
    
    # Define tools here (closure over shared repository, pool_manager, credential_manager)
    # NO CredentialManager() or ConnectionPoolManager() creation here —
    # they're already on the manager and passed in.
    
    @register_tool_category("db")
    @tool
    async def db_conn_add(...):
        # N1: Encrypt password HERE using credential_manager from closure
        credentials = credential_manager.encrypt({"password": password}) if password else None
        repository.create(..., credentials=credentials)
        ...
    
    @register_tool_category("db")
    @tool
    async def db_postgres_dml_select(...):
        _validate_select_only(query)  # N4: guard strips string literals
        result = await pool_manager.execute_select(...)  # pool mgr decrypts internally
        ...
    
    # ... other tools ...
    tools = [db_conn_add, db_conn_delete, db_conn_list, db_conn_test, db_postgres_dml_select]
    
    # Set full docs
    db_conn_add._full_doc_ = """..."""
    db_conn_delete._full_doc_ = """..."""
    # etc.
    
    return tools
```

### Wiring in instance.py

```python
# In create_instance_tools(), after other tool factory calls:
# ── Database tools ──
# C3: Pass shared repository and pool_manager from manager (not creating new ones)
db_tools = create_db_tools(
    manager,
    current_instance_id,
    repository=manager.db_connection_repository,   # shared singleton
    pool_manager=manager.db_pool_manager,           # shared singleton
)
tools.extend(db_tools)
```

### Result Formatting for `db_postgres_dml_select`

```
Query executed successfully on 'production-db'.

| id | name        | email              |
|----|-------------|--------------------|
| 1  | Alice       | alice@example.com  |
| 2  | Bob         | bob@example.com    |

Rows: 2 (of 2)
```

If truncated:
```
Query executed successfully on 'production-db'.

| id | name        | email              |
|----|-------------|--------------------|
| ... (1000 rows shown) ... |

Rows: 1000 (TRUNCATED — 5432 total rows. Use max_rows parameter to increase limit.)
```

## Constraints
- ALL tool functions must return strings (consistent with existing tools)
- Passwords must NEVER appear in any tool return value or log message
- `db_conn_delete` MUST dispose the connection pool after deleting from repository
- SELECT guard must reject non-SELECT queries BEFORE attempting execution
- **C1 — SELECT guard must block `SELECT ... INTO`:** The keyword `INTO` is in `_FORBIDDEN_KEYWORDS` and checked via word-boundary regex.
- **N4 — Strip string literals before keyword scan:** The guard must strip single-quoted string literals (`'...'` including `''` escapes) before scanning for forbidden keywords. This prevents false positives like `SELECT * FROM logs WHERE msg LIKE '%INTO%'`.
- **N1 — Encryption in TOOL layer, not repository:** `db_conn_add` encrypts password via `credential_manager.encrypt()` before calling `repository.create()`. The repository receives opaque encrypted strings only.
- **N8 — Factory takes 4 args:** `create_db_tools(manager, current_instance_id, repository, pool_manager)` — NOT 2 args.
- **N9 — Error sanitization in tool output:** Tool functions must catch exceptions from pool manager and sanitize error messages (the pool manager's `_sanitize_error()` handles this, but tools should also wrap in try/except and return error strings).
- Error messages must be clear and actionable (show what went wrong + suggested fix)
- Tools must be async (pool manager uses asyncpg)
- Tool functions created inside `create_db_tools` closure (capture shared repository + pool_manager + credential_manager)
- **C3 — Factory receives shared instances:** `create_db_tools()` must NOT create `CredentialManager()`, `DbConnectionRepository()`, or `ConnectionPoolManager()`. These are created once at the `InstanceManager` level and passed as parameters.
- **D7 — Sync repo calls in async context:** Connection CRUD tools (`db_conn_add`, `db_conn_delete`, `db_conn_list`) call sync `Session(engine)` methods inside async functions. This is acceptable (low-frequency admin operations). See D7 in decisions.md.
- Must follow `@register_tool_category("db")` + `@tool` decorator pattern

## Deliverables
- [ ] `daemon/tools/db_tools.py` with 5 tool functions + SELECT guard + factory
- [ ] `_FORBIDDEN_KEYWORDS` includes `INTO` (C1)
- [ ] SELECT guard strips string literals before keyword scan (N4)
- [ ] `db_conn_add` encrypts password in the tool via `credential_manager.encrypt()` (N1)
- [ ] `"db"` added to `CATEGORY_MODULES` in `_tool_registry.py`
- [ ] `create_db_tools(manager, instance_id, repository, pool_manager)` takes 4 args (N8)
- [ ] `create_db_tools()` called in `create_instance_tools()` in `instance.py` with shared repository + pool_manager (C3)
- [ ] `create_db_tools()` does NOT create its own CredentialManager/ConnectionPoolManager (C5, D8)
- [ ] SELECT-only guard rejects INSERT/UPDATE/DELETE/DROP/SELECT-INTO/etc.
- [ ] All tools return formatted strings, never raise exceptions (return error messages)
- [ ] No secrets in any tool output
