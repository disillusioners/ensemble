# Architectural Decisions: DB Tool Category

## D1: Reuse `CredentialManager` — Encryption in TOOL Layer, Not Repository

**Decision:** Reuse the existing `daemon/sources/credentials.py::CredentialManager` class (Fernet symmetric encryption) for encrypting connection passwords. The encryption/decryption happens in the **tool functions**, NOT in the repository — matching the existing `source_configs` pattern exactly.

**Context — The Actual Codebase Pattern:**
The existing credential handling in this codebase follows a clear separation:
- `SQLModelSourceRepository.__init__(self, engine: Engine)` — **NO `credential_manager` parameter**
- `routers/sources.py:155` — the **ROUTER** encrypts before calling `repository.create_source_config()`
- `routers/sources.py:388` — the **ROUTER** decrypts after calling the repository
- The repository receives and stores **opaque encrypted strings** — it has no knowledge of encryption

We follow this exact pattern, with the tool functions taking the router's role:
- `DbConnectionRepository.__init__(self, engine: Engine)` — **NO `credential_manager` parameter**
- `db_conn_add` tool — encrypts password via `credential_manager.encrypt()` before `repository.create()`
- `db_postgres_dml_select` tool — decrypts password via `credential_manager.decrypt()` before building DSN

**Rationale:**
- No new crypto code to write or audit
- **Consistent with existing pattern** — the repository stays a dumb CRUD layer
- The `CredentialManager` is a service-layer concern, not a persistence concern
- If operators already set `SOURCE_CREDENTIAL_KEY`, DB connection passwords get the same protection automatically

**Alternatives Considered:**
1. **New `DB_CREDENTIAL_KEY` env var** — rejected. Adds configuration burden for operators; they'd need to manage another secret. Reusing `SOURCE_CREDENTIAL_KEY` is simpler.
2. **Raw `cryptography` calls** — rejected. Duplicates existing `CredentialManager` logic.
3. **Hash-based (no decryption)** — rejected. We need to decrypt passwords to build connection DSNs for asyncpg.
4. **Encryption in the repository** — rejected. This is how v2 of the plan proposed it, but it does NOT match the actual codebase pattern. The source repository stores opaque strings; encryption is the caller's responsibility.

**Implications:**
- If `SOURCE_CREDENTIAL_KEY` is unset, passwords are stored as plaintext JSON (with logged warning). This matches existing behavior for source credentials.
- A **single shared `CredentialManager` instance** (D8) is injected from `app.state` and passed to the tool factory closure. The repository never sees it.

---

## D2: Single `db_connections` Table — Encrypted Credentials Column

**Decision:** Store all connection metadata as table columns (name, db_type, host, port, database, username) and store the password as an encrypted JSON string in a single `credentials` column.

**Context:** The `source_configs` table uses this exact pattern — a `credentials: str | None` column containing encrypted JSON.

**Rationale:**
- Consistent with existing patterns (source_configs)
- SQLModel column definitions stay simple
- The encrypted blob is opaque to SQL — no need for per-field encryption
- `to_public_dict()` method cleanly separates what's exposed vs what's secret

**Alternatives Considered:**
1. **Separate `password` column (encrypted)** — rejected. Creates a separate encryption concern. Less consistent with existing patterns.
2. **Multiple credential columns** (password, ssl_key, etc.) — rejected. Over-normalized for current needs. Can extend later by adding fields to the encrypted JSON.

**Schema:**
```
db_connections
├── id (UUID PK)
├── connection_name (unique, indexed)
├── db_type (str)
├── host (str)
├── port (int | None)
├── database (str | None)
├── username (str | None)
├── credentials (encrypted JSON str | None)  ← {"password": "..."}
├── ssl_mode (str, default "prefer")
├── created_at (ISO str)
└── updated_at (ISO str)
```

---

## D3: Connection Pooling via `asyncpg.create_pool`

**Decision:** Use `asyncpg` (already installed) with per-connection-name connection pools managed by a `ConnectionPoolManager` service. Pools are created lazily on first use.

**Context:**
- `asyncpg>=0.29.0` is already in `pyproject.toml`
- `psycopg[binary]>=3.1.0` is also installed but is used for the ensemble's internal sync engine
- asyncpg is purpose-built for fast async PostgreSQL queries with native protocol support

**Rationale:**
- asyncpg is faster than psycopg for pure async query workloads (binary protocol, prepared statements)
- Connection pooling avoids per-query connection overhead
- Lazy creation means we don't connect to databases that aren't queried
- asyncpg's `create_pool()` handles reconnection, health checks, and connection lifecycle

**Pool Configuration:**
```python
asyncpg.create_pool(
    dsn=dsn,
    min_size=1,        # Keep at least 1 connection warm
    max_size=5,        # Max 5 concurrent connections per named connection
    max_queries=500,   # Recycle connection after 500 queries
    timeout=30,        # 30s connection timeout
)
```

**Alternatives Considered:**
1. **SQLAlchemy async engine** — rejected. Heavier, adds async session complexity. asyncpg is lighter and faster for pure query execution.
2. **psycopg async** — rejected. While psycopg is installed, it's used for the internal sync engine. Using it for external queries would mix concerns. asyncpg is a better fit for query-only tools.
3. **No pooling (connect per query)** — rejected. High latency, no connection reuse. Unacceptable for tools that may be called frequently.

**Implications:**
- `ConnectionPoolManager` is a **singleton at the `InstanceManager` level** (see D5) — all agent instances share one pool per connection name, avoiding pool proliferation
- `ConnectionPoolManager.dispose_all()` is called as a step in `manager.shutdown()` steps list (C6, N2, N6) — NOT in `cleanup()` (sync) and NOT in `api.py` lifespan (would double-dispose)
- `ConnectionPoolManager.dispose(name)` must be called when a connection is deleted via `db_conn_delete`
- Thread-safe pool creation via `asyncio.Lock` with double-check pattern
- Only PostgreSQL pools for now; other DB types would need different drivers

---

## D4: SELECT-Only Enforcement via Keyword Analysis (No External Dependency)

**Decision:** Implement a lightweight multi-layer SELECT-only guard without adding `sqlparse` or any external dependency.

**Context:** The requirement is that `db_postgres_dml_select` must ONLY allow SELECT statements. We need to prevent INSERT, UPDATE, DELETE, and DDL operations.

**Guard Logic:**
1. **Strip comments** (`--` single-line, `/* */` multi-line)
2. **Strip trailing semicolons and whitespace**
3. **Check first keyword** is `SELECT` or `WITH` (for CTEs)
4. **Strip string literals** — replace single-quoted strings (`'...'`, including `''` escapes) with empty strings `''`, so forbidden keywords inside string literals don't trigger false positives
5. **Scan for forbidden keywords** using regex word boundaries: `INSERT`, `INTO`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `CREATE`, `TRUNCATE`, `GRANT`, `REVOKE`, `MERGE`, `REPLACE`, `CALL`, `EXEC`, `EXECUTE`, `VACUUM`, `REINDEX`, `REFRESH`

> **Note on string literal stripping (N4):** Without stripping string literals, queries like `SELECT * FROM logs WHERE message LIKE '%INTO%'` or `SELECT note FROM tickets WHERE note = 'DROP TABLE'` would be falsely rejected. The regex `'(?:[^']|'')*'` matches SQL string literals including escaped single-quotes (`''`), replacing them with `''` before the keyword scan.

> **Note on `INTO`:** `SELECT ... INTO` creates a table and is therefore a DDL side-effect. Word-boundary regex (`\bINTO\b`) prevents false positives on substrings while still blocking legitimate `SELECT ... INTO new_table FROM ...` statements. Note: `INSERT INTO ...` is already caught by the first-keyword check, but `INTO` in the forbidden set also catches `SELECT ... INTO`.

**Rationale:**
- `sqlparse` is not currently installed and would be a new dependency for a single validation function
- The keyword approach handles 99% of real-world cases
- Word-boundary regex prevents false positives (e.g., column named `updated_at`)
- Documented as **defense-in-depth, not a security boundary** — if true SQL injection prevention is needed, use a dedicated proxy or database roles

**What it catches:**
- `INSERT INTO ...` → rejected (first keyword check)
- `SELECT * INTO new_table FROM x` → rejected (forbidden keyword `INTO`)
- `SELECT * FROM x; DROP TABLE y;` → rejected (forbidden keyword scan)
- `WITH x AS (DELETE ...) SELECT ...` → rejected (forbidden keyword scan)
- `UPDATE users SET ...` → rejected (first keyword check)

**What it allows:**
- `SELECT * FROM users` → passes
- `WITH active AS (SELECT * FROM users WHERE active = true) SELECT * FROM active` → passes
- `SELECT updated_at, deleted_at FROM logs` → passes (word-boundary regex)
- `SELECT * FROM logs WHERE message LIKE '%INTO%'` → passes (string literal stripped before keyword scan — N4)
- `SELECT note FROM tickets WHERE note = 'DROP TABLE'` → passes (string literal stripped — N4)

> **Known limitation (W3/W4/W6):** This guard is **defense-in-depth, not a security boundary**. Sophisticated SQL could theoretically bypass keyword scanning. The true security boundary is a **database-level read-only role** — operators should configure PostgreSQL connections with a user that has only `SELECT` privileges. The tool guard catches the obvious cases; the DB role catches the rest.

**Alternatives Considered:**
1. **`sqlparse` library** — rejected. Adds a dependency for a single function. The keyword approach is sufficient for defense-in-depth.
2. **Database-level read-only role** — recommended as defense-in-depth at the DB level, but not something the tool can enforce. Documented as a best practice and noted as the **true security boundary** (see known limitation above).
3. **PostgreSQL `SET TRANSACTION READ ONLY`** — considered but adds complexity. The keyword check is simpler and catches the common cases.

---

## D5: Shared Services at Manager Level + Factory-Closure Tools

**Decision:** DB tools are created by a `create_db_tools(manager, instance_id, repository, pool_manager)` factory function. The `DbConnectionRepository` and `ConnectionPoolManager` are **created once at the `InstanceManager` level** and passed into the factory. The `CredentialManager` is **injected from `app.state`** (N5) — it already exists there and is reused.

**Context:** All manager-dependent tools in the codebase use the factory-closure pattern:
- `create_instance_tools(manager, instance_id, agent_id)` — instance management tools
- `create_mother_tools(manager, instance_id)` — agent mother tools
- `create_help_tool(tools, agent_id, mcp_tool_names)` — help tool
- etc.

The key insight: `create_instance_tools()` runs **per-instance** (once for every agent instance). If `ConnectionPoolManager` is created inside this factory, you get N instances × M connections = pool proliferation. The pool manager must be a **singleton at the manager level**.

**Service Lifecycle:**
```
api.py lifespan (REORDERED — BLOCKER 1):
    credential_manager = CredentialManager()              ← moved BEFORE InstanceManager (was at line 232)
    manager = InstanceManager(config, ensemble_config,    ← N5: pass credential_manager via constructor
                              credential_manager=credential_manager)
    
InstanceManager.__init__():
    self._credential_manager = credential_manager          ← injected, NOT created (N5)
    self._db_connection_repository = DbConnectionRepository(self._engine)
                                                           ← N1: NO credential_manager param
    self._db_pool_manager = ConnectionPoolManager(
        self._db_connection_repository,
        self._credential_manager                           ← pool manager uses cred mgr for DSN building
    )
            │
            ├── @property db_connection_repository → self._db_connection_repository
            └── @property db_pool_manager → self._db_pool_manager

create_instance_tools(manager, instance_id, agent_id)
    │
    └── create_db_tools(manager, instance_id,
            repository=manager.db_connection_repository,   ← shared from manager
            pool_manager=manager.db_pool_manager           ← shared from manager
        )
```

**Why this matters (C3 fix):**
- **Without this fix:** 10 agent instances × 3 PostgreSQL connections = 30 connection pools, each maintaining 1-5 TCP connections = up to 150 connections to the same databases. This causes resource exhaustion and connection-limit violations.
- **With this fix:** 10 agent instances share 1 pool manager → 3 connections × max 5 pool size = at most 15 TCP connections total. Resources are bounded and predictable.

**Rationale:**
- Tools access the repository and pool manager via closure variables
- Consistent with every other tool in the system
- `manager.engine` is a public read-only property (`daemon/manager.py:983`)
- **Single shared pool per connection name across ALL instances** — avoids pool proliferation
- **Existing `CredentialManager` reused** — the one at `app.state.credential_manager` (N5) is injected into `InstanceManager`, not duplicated

**Implementation:**
```python
# BLOCKER 1 / N5: In api.py, MOVE CredentialManager() construction BEFORE InstanceManager.
# Current code has InstanceManager at line 172 and CredentialManager at line 232.
# CredentialManager must be constructed FIRST so it can be injected:
credential_manager = CredentialManager()
manager = InstanceManager(config, ensemble_config, credential_manager=credential_manager)

# In InstanceManager.__init__ (after engine initialization):
# N10: Model import at MODULE LEVEL (top of manager.py), NOT inline here.
#   from ..repositories.db_connection.models import DbConnectionConfig  ← top of file, not here
from ..repositories.db_connection.repository import DbConnectionRepository
from ..services.db_pool_manager import ConnectionPoolManager

# N1: Repository takes NO credential_manager — stores opaque encrypted strings
self._db_connection_repository = DbConnectionRepository(self._engine)

# N1+N5: Pool manager gets credential_manager for DSN decryption
self._db_pool_manager = ConnectionPoolManager(
    self._db_connection_repository,
    credential_manager  # ← injected from app.state, not created here
)

# Public properties:
@property
def db_connection_repository(self) -> DbConnectionRepository:
    return self._db_connection_repository

@property
def db_pool_manager(self) -> ConnectionPoolManager:
    return self._db_pool_manager

@property
def credential_manager(self):  # BLOCKER 2: Phase 3 tools reference manager.credential_manager
    return self._credential_manager


# In create_db_tools() — receives shared instances as parameters:
def create_db_tools(
    manager: "InstanceManager",
    current_instance_id: str,
    repository: "DbConnectionRepository",      # ← shared, from manager
    pool_manager: "ConnectionPoolManager",     # ← shared, from manager
) -> list[Any]:
    @register_tool_category("db")
    @tool
    async def db_conn_add(...):
        # N1: Encrypt HERE in the tool, then pass opaque string to repository
        ...
    
    # ... other tools ...
    return [db_conn_add, db_conn_delete, db_conn_list, db_conn_test, db_postgres_dml_select]
```

---

## D6: Tool Naming — `db` Category with `db_*` Prefix

**Decision:** Register all DB tools under category `"db"`. Tool names follow the pattern `db_{operation}` for connection management and `db_{db_type}_{operation}` for queries.

**Tool Names:**
| Tool Name | Category | Purpose |
|-----------|----------|---------|
| `db_conn_add` | db | Register a named connection |
| `db_conn_delete` | db | Remove a connection |
| `db_conn_list` | db | List all connections (no secrets) |
| `db_conn_test` | db | Test connection health |
| `db_postgres_dml_select` | db | Run SELECT on PostgreSQL |

**Rationale:**
- Category `"db"` is added to `CATEGORY_MODULES` in `_tool_registry.py`
- Adding `"db"` to `tools.allow` grants ALL db tools via category expansion
- Future DB types (MySQL, etc.) add `db_mysql_*` tools under the same `"db"` category
- The `db_conn_*` prefix groups connection management tools together
- The `db_postgres_*` prefix groups PostgreSQL-specific tools together

**Future Extensibility:**
```
db_conn_add / db_conn_delete / db_conn_list / db_conn_test  (connection management)
db_postgres_dml_select                                         (PostgreSQL SELECT)
db_postgres_dml_insert   (future)
db_postgres_ddl_create   (future)
db_mysql_dml_select      (future)
db_mysql_dml_insert      (future)
```

All under category `"db"` — one `tools.allow` entry grants them all.

---

## D7: Sync Repository Calls Inside Async Tool Functions (C2)

**Decision:** Connection CRUD operations (`db_conn_add`, `db_conn_delete`, `db_conn_list`) use sync SQLAlchemy `Session(engine)` calls inside async tool functions. This is an intentional, documented trade-off.

**Context:** The `DbConnectionRepository` uses the existing sync SQLAlchemy pattern (`with Session(self.engine) as session: ...`) — same as every other repository in the codebase (`source/repository.py`, `instance/repository.py`, etc.). The DB tools that call these repository methods are async functions (because `db_conn_test` and `db_postgres_dml_select` use asyncpg).

**Note on encryption boundary (N1):** Since encryption happens in the tool functions (not the repository — see D1), the tool calls `credential_manager.encrypt()` / `.decrypt()` directly, then passes opaque encrypted strings to the sync repository. The `CredentialManager` calls themselves are trivially fast (Fernet is symmetric, in-memory).

**Why this is acceptable:**
1. **Connection CRUD is NOT a hot path.** Agents register/delete connections rarely — it's an admin operation. The frequency is negligible (single-digit calls per session at most).
2. **The hot path IS properly async.** The query execution path (`db_postgres_dml_select` → `pool_manager.execute_select()` → `asyncpg`) is fully async with connection pooling. This is where latency matters.
3. **The sync calls are fast.** Repository operations hit the local ensemble DB (SQLite or PostgreSQL on localhost), not remote databases. Typical latency: <1ms.
4. **Consistency with codebase.** Every other repository uses sync sessions. Introducing async sessions would create an inconsistency and require a separate async engine.

**Mitigation if this becomes a bottleneck:**
If sync calls ever block the event loop measurably (unlikely given the low frequency), wrap the repository call in `asyncio.to_thread()`:
```python
result = await asyncio.to_thread(repository.create, connection_name, ...)
```

This is a one-line change per tool function and does not require refactoring the repository itself.

**Alternatives Considered:**
1. **Async SQLAlchemy sessions from the start** — rejected. Would require a separate async engine, async session factory, and async repository variants. Significant complexity for negligible benefit given the low CRUD frequency.
2. **`asyncio.to_thread()` now** — deferred. Premature optimization. The CRUD path is cold. Document the escape hatch for future use.

---

## D8: Inject Existing `CredentialManager` from `app.state` (N5)

**Decision:** The DB tool system does NOT create a new `CredentialManager`. It **injects the existing one** from `app.state.credential_manager` (currently created at `api.py:232`, but must be **moved before** `InstanceManager` construction at `api.py:172` — see BLOCKER 1).

**Context:** `CredentialManager` reads the Fernet encryption key from the `SOURCE_CREDENTIAL_KEY` environment variable at construction time. There is already exactly ONE instance in the application — created in `api.py` lifespan and stored as `app.state.credential_manager`. Creating a second one in `InstanceManager.__init__` would be a duplicate with the same key, which is wasteful and confusing. The existing instance must be **reordered** in `api.py` to be constructed before `InstanceManager` (BLOCKER 1).

**Injection Flow:**
```
api.py lifespan (REORDERED):
    credential_manager = CredentialManager()           ← MUST be constructed FIRST (moved up from line 232)
    manager = InstanceManager(
        config, ensemble_config,
        credential_manager=credential_manager           ← N5: pass via constructor
    )

InstanceManager.__init__(..., credential_manager):
    self._credential_manager = credential_manager       ← stored, NOT created
    self._db_pool_manager = ConnectionPoolManager(
        self._db_connection_repository,
        self._credential_manager                        ← passed to pool manager
    )
```

**What changed from v2:**
- v2: `self._credential_manager = CredentialManager()` in `InstanceManager.__init__` (duplicate)
- v3: `self._credential_manager = credential_manager` from constructor param (inject existing)

**Required `api.py` change:**
Reorder IS needed. In the current code, `InstanceManager` is constructed at `api.py:172` and `CredentialManager()` at `api.py:232` — 60 lines LATER. We must **move `CredentialManager()` construction to BEFORE line 172** so it can be passed to `InstanceManager()`:
```python
# CORRECTED ordering in api.py lifespan:
# Step 1: Construct CredentialManager FIRST (moved up from line 232)
credential_manager = CredentialManager()

# Step 2: Construct InstanceManager, passing credential_manager
manager = InstanceManager(config, ensemble_config, credential_manager=credential_manager)
await manager.initialize()

# ... (credential_manager is already constructed — the old line 232 
#      assignment should be removed or the variable reused) ...

# Step 3: Set on app.state as before (was api.py:378)
app.state.credential_manager = credential_manager
```

**Rationale:**
- Single source of truth — one `CredentialManager`, one Fernet key
- No risk of key inconsistency
- Matches the dependency-injection pattern used throughout the codebase
- The `CredentialManager` is stateless after construction (it caches the `Fernet` object), so sharing is safe

**Alternatives Considered:**
1. **Create new `CredentialManager()` in `InstanceManager.__init__`** — rejected (v2 approach). Duplicates `app.state.credential_manager`. Both read the same env var and construct the same `Fernet`, but it's wasteful and creates two instances where one suffices.
2. **Module-level singleton** — rejected. Harder to test (can't inject mock), and doesn't match the codebase's dependency-injection pattern.
