# Phase 2: Connection Pool Service

## Objective
Create a `ConnectionPoolManager` service that manages `asyncpg.Pool` instances for named connections. Pools are created lazily on first query, cached by connection name, and disposed when connections are deleted. This service is a **singleton at the `InstanceManager` level** (D5) — all agent instances share one pool per connection name. The service receives the injected `CredentialManager` (N5) for decrypting passwords when building DSNs.

## Coupling
- **Depends on:** Phase 1 (needs `DbConnectionRepository.get_by_name()` and `get_credentials()`)
- **Coupling type:** loose — only depends on Phase 1's public interface, not implementation
- **Shared files with other phases:** None (new file)
- **Shared APIs/interfaces:** `ConnectionPoolManager` class (consumed by Phase 3 tools, created by InstanceManager)
- **Why this coupling:** Pool service reads connection params from repository and decrypts credentials via `CredentialManager` to build pool config. It's a consumer of both interfaces.

## Context
- **Phase 1 delivered:** `DbConnectionConfig` model and `DbConnectionRepository` (get_by_name, get_credentials — returns opaque encrypted string, NO decryption in repo per N1)
- **Key decision D1/N1:** Repository stores opaque encrypted strings. The pool manager decrypts via `CredentialManager` when building DSNs.
- **Key decision D3:** Use `asyncpg` (already installed) — purpose-built for fast PostgreSQL async queries
- **Key decision D5/N5:** Pool manager is a **singleton at the InstanceManager level**. `CredentialManager` is **injected from `app.state`** (N5), not created here.
- **Key decision:** Lazy pool creation — pool created on first `get_connection()` call, cached in `dict[str, asyncpg.Pool]`
- Pool config: `min_size=1, max_size=5, max_queries=500, timeout=30`

### C3/N5: Where the Pool Manager Lives

```
api.py lifespan (REORDERED — BLOCKER 1):
    credential_manager = CredentialManager()              ← moved BEFORE InstanceManager (was at line 232)
    manager = InstanceManager(config, ensemble_config,
                              credential_manager=credential_manager)  ← N5: inject

InstanceManager.__init__ (after engine initialization):
    self._credential_manager = credential_manager          ← injected, NOT created
    self._db_connection_repository = DbConnectionRepository(self._engine)
                                                           ← N1: NO cred_mgr param
    self._db_pool_manager = ConnectionPoolManager(
        self._db_connection_repository,
        self._credential_manager                           ← N5: decrypts when building DSN
    )
            │
            └── @property db_pool_manager → self._db_pool_manager
                    │
                    └── Passed to create_db_tools() via manager.db_pool_manager
```

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Create `ConnectionPoolManager` class | Manages dict of connection_name → `asyncpg.Pool`. Constructor: `__init__(self, repository, credential_manager)` — receives both (N1, N5). Methods: `get_connection(name)`, `test_connection(name)`, `dispose(name)`, `dispose_all()`, `execute_select()`. | `daemon/services/db_pool_manager.py` (NEW) |
| 2 | Implement lazy pool creation | On `get_connection(name)`: check cache dict; if miss, read `DbConnectionConfig` from repo, decrypt credentials via `credential_manager.decrypt(repo.get_credentials(name))`, build DSN (N3 three-case), create `asyncpg.create_pool()`, store in cache. Return `pool.acquire()`. | `daemon/services/db_pool_manager.py` (MODIFY) |
| 3 | Implement `test_connection` | Acquire a connection, run `SELECT 1`, return `{"success": True, "message": "..."}` or `{"success": False, "message": error}`. | `daemon/services/db_pool_manager.py` (MODIFY) |
| 4 | Implement `dispose` / `dispose_all` | `dispose(name)`: pop pool from cache, await `pool.close()`. `dispose_all()`: close all cached pools. Must handle pool already disposed gracefully. | `daemon/services/db_pool_manager.py` (MODIFY) |
| 5 | Wire pool manager into `InstanceManager` (C3, N5, BLOCKER 2) | In `InstanceManager.__init__`: accept `credential_manager` param, store as `self._credential_manager`, create `self._db_connection_repository = DbConnectionRepository(self._engine)` (N1: no cred_mgr), create `self._db_pool_manager = ConnectionPoolManager(repo, cred_mgr)`. Add THREE public properties: `@property db_pool_manager`, `@property db_connection_repository`, AND `@property credential_manager` (BLOCKER 2 — Phase 3 references `manager.credential_manager`). | `daemon/manager.py` (MODIFY) |
| 6 | Reorder + inject CredentialManager in `api.py` (N5, BLOCKER 1) | **Reorder IS needed.** In current code, `InstanceManager` is at `api.py:172` and `CredentialManager()` is at `api.py:232` (60 lines LATER). Move `credential_manager = CredentialManager()` to BEFORE line 172, then pass it as kwarg: `InstanceManager(config, ensemble_config, credential_manager=credential_manager)`. Remove the old `credential_manager = CredentialManager()` at line 232 (or reuse the variable). | `daemon/api.py` (MODIFY) |
| 7 | Add pool disposal to `shutdown()` steps (C6, N2, N6) | Add a new step to the `steps` list in `shutdown()` (manager.py:2867-2878): `("dispose_db_pools", self._db_pool_manager.dispose_all() if hasattr(self, '_db_pool_manager') else asyncio.sleep(0))`. Do NOT modify `cleanup()` (N2). Do NOT add to `api.py` lifespan (N6 — avoid double disposal). | `daemon/manager.py` (MODIFY) |
| 8 | Write unit tests | Test pool caching, `test_connection` with mock pool, `dispose` removes from cache, error handling for non-existent connection. Test DSN building with all three cases (N3). Test that error messages never contain DSN/password (W1, N9). | `tests/test_db_pool_manager.py` (NEW) |

## Key Files

### NEW Files
- `daemon/services/db_pool_manager.py` — Connection pool management service
- `tests/test_db_pool_manager.py` — Unit tests (with mocked asyncpg)

### MODIFIED Files
- `daemon/manager.py` — Add `credential_manager` constructor param; create shared `_db_connection_repository` + `_db_pool_manager`; add properties; add pool disposal step to `shutdown()` (C3, C6, N5, N2)
- `daemon/api.py` — Pass `credential_manager=credential_manager` to `InstanceManager(...)` (N5)

## Detailed Design

### `ConnectionPoolManager`

```python
import asyncio
import logging
from typing import TYPE_CHECKING, Any

import asyncpg

if TYPE_CHECKING:
    from daemon.repositories.db_connection.repository import DbConnectionRepository
    from daemon.repositories.db_connection.models import DbConnectionConfig
    from daemon.sources.credentials import CredentialManager

logger = logging.getLogger(__name__)

# Pool configuration defaults
POOL_MIN_SIZE = 1
POOL_MAX_SIZE = 5
POOL_MAX_QUERIES = 500
POOL_TIMEOUT = 30  # seconds

# Query execution limits
DEFAULT_QUERY_TIMEOUT = 30  # seconds
DEFAULT_MAX_ROWS = 1000


class ConnectionPoolManager:
    """Manages asyncpg connection pools for named database connections.
    
    Pools are created lazily on first use and cached by connection name.
    This is a SINGLETON at the InstanceManager level — all agent instances
    share the same pool manager, ensuring one pool per connection name.
    
    Call dispose(name) when a connection is deleted to clean up its pool.
    Call dispose_all() during daemon shutdown to clean up all pools.
    """

    def __init__(
        self,
        repository: "DbConnectionRepository",
        credential_manager: "CredentialManager",
    ):
        """Initialize with repository and credential_manager.
        
        Args:
            repository: DbConnectionRepository for reading connection metadata.
            credential_manager: CredentialManager for decrypting passwords
                when building DSNs (N1: repo returns opaque encrypted strings).
        """
        self._repository = repository
        self._credential_manager = credential_manager
        self._pools: dict[str, asyncpg.Pool] = {}
        self._lock = asyncio.Lock()  # Protects _pools dict during lazy creation

    def _build_dsn(self, conn: "DbConnectionConfig", password: str | None) -> str:
        """Build PostgreSQL DSN from connection config.
        
        Handles three authentication cases (N3):
        - user + password: postgresql://user:password@host:port/database
        - user, no password: postgresql://user@host:port/database (.pgpass/peer/IAM auth)
        - no user, no password: postgresql://host:port/database (anonymous)
        
        Args:
            conn: Connection config model with host, port, database, username.
            password: Decrypted password string, or None for passwordless auth.
        
        Returns:
            PostgreSQL connection DSN string.
        """
        # N3: Three-case DSN building — don't drop username when password is None
        if password and conn.username:
            auth = f"{conn.username}:{password}@{conn.host}"
        elif conn.username:
            auth = f"{conn.username}@{conn.host}"   # username without password
        else:
            auth = conn.host                          # truly anonymous
        
        # Append port if specified
        if conn.port:
            auth += f":{conn.port}"
        
        dsn = f"postgresql://{auth}"
        if conn.database:
            dsn += f"/{conn.database}"
        if conn.ssl_mode:
            dsn += f"?sslmode={conn.ssl_mode}"
        return dsn

    async def _get_or_create_pool(self, connection_name: str) -> asyncpg.Pool:
        """Get cached pool or create new one. Thread-safe via lock."""
        if connection_name in self._pools:
            return self._pools[connection_name]
        
        async with self._lock:
            # Double-check after acquiring lock
            if connection_name in self._pools:
                return self._pools[connection_name]
            
            conn = self._repository.get_by_name(connection_name)
            if conn is None:
                raise ValueError(f"Connection '{connection_name}' not found")
            
            if conn.db_type != "postgres":
                raise ValueError(
                    f"Connection '{connection_name}' is type '{conn.db_type}', "
                    f"only 'postgres' is currently supported"
                )
            
            # N1: Decrypt credentials HERE (not in repository).
            # Repository returns opaque encrypted string via get_credentials().
            password = None
            encrypted_creds = self._repository.get_credentials(connection_name)
            if encrypted_creds:
                creds_dict = self._credential_manager.decrypt(encrypted_creds)
                password = creds_dict.get("password")
            
            dsn = self._build_dsn(conn, password)
            
            try:
                pool = await asyncpg.create_pool(
                    dsn=dsn,
                    min_size=POOL_MIN_SIZE,
                    max_size=POOL_MAX_SIZE,
                    max_queries=POOL_MAX_QUERIES,
                    timeout=POOL_TIMEOUT,
                )
            except (asyncpg.PostgresError, OSError, ConnectionRefusedError) as e:
                # W1 + N9: Sanitize error — NEVER include the DSN (contains password)
                # Extract only the error detail from the exception
                error_detail = self._sanitize_error(str(e))
                raise ConnectionError(
                    f"Failed to connect to '{connection_name}': {error_detail}"
                ) from e
            except Exception as e:
                error_detail = self._sanitize_error(str(e))
                raise ConnectionError(
                    f"Failed to create connection pool for '{connection_name}': {error_detail}"
                ) from e
            
            self._pools[connection_name] = pool
            logger.info(f"Created connection pool for '{connection_name}'")
            return pool

    @staticmethod
    def _sanitize_error(error_str: str) -> str:
        """Sanitize error message to remove any DSN/password leakage (W1, N9).
        
        Defense-in-depth approach (BLOCKER 3):
        1. Take first line only
        2. Redact known credential-bearing patterns via deny-list regex
        3. Final safety net: scan for the actual password value and redact if present
        
        Handles both DSN format and PostgreSQL native quoted format:
        - DSN:         postgresql://user:password@host
        - Conn string: password=mySecret
        - PG native:   password "mySecret"  (quoted syntax)
        """
        import re
        
        # Step 1: First line only
        sanitized = error_str.split('\n')[0]
        
        # Step 2: Redact known credential patterns
        # 2a: DSN format — postgresql://user:password@host
        sanitized = re.sub(
            r'(postgresql://)[^@\s]+(@)',
            r'\1***:***\2',
            sanitized,
        )
        # 2b: password=... format (key=value connection string)
        sanitized = re.sub(
            r'password\s*=\s*\S+',
            'password=***',
            sanitized,
            flags=re.IGNORECASE,
        )
        # 2c: BLOCKER 3 — password "..." format (PostgreSQL native quoted syntax)
        sanitized = re.sub(
            r'(password\s+)"[^"]*"',
            r'\1"***"',
            sanitized,
            flags=re.IGNORECASE,
        )
        # 2d: Also handle user "..." with embedded password (some PG error formats)
        sanitized = re.sub(
            r'(role|user)\s+"[^"]*"\s+(password\s+)"[^"]*"',
            r'\1"***"\2"***"',
            sanitized,
            flags=re.IGNORECASE,
        )
        
        # Step 3: Final safety net — if the sanitized string still contains 
        # anything that looks like a credential (colon-separated user:pass or 
        # bare password=), truncate to first colon to be safe.
        # This catches any format we didn't anticipate.
        if re.search(r'://[^@\s]+:[^@\s]+@', sanitized):
            # Still has user:password@host — aggressive redaction
            sanitized = re.sub(r'://[^@]+@', '://***:***@', sanitized)
        
        return sanitized

    async def get_connection(self, connection_name: str):
        """Acquire a connection from the pool (context manager).
        
        Usage:
            async with await pool_manager.get_connection("my-db") as conn:
                rows = await conn.fetch("SELECT 1")
        """
        pool = await self._get_or_create_pool(connection_name)
        return pool.acquire()

    async def test_connection(self, connection_name: str) -> dict[str, Any]:
        """Test that a connection works. Returns {"success": bool, "message": str}."""
        try:
            async with await self.get_connection(connection_name) as conn:
                result = await conn.fetchval("SELECT 1")
                if result == 1:
                    return {"success": True, "message": f"Connection '{connection_name}' is healthy"}
                return {"success": False, "message": "Unexpected response from database"}
        except (asyncpg.PostgresError, OSError, ConnectionError) as e:
            error_detail = self._sanitize_error(str(e))
            return {"success": False, "message": error_detail}
        except Exception as e:
            error_detail = self._sanitize_error(str(e))
            return {"success": False, "message": error_detail}

    async def execute_select(
        self,
        connection_name: str,
        query: str,
        timeout: int = DEFAULT_QUERY_TIMEOUT,
        max_rows: int = DEFAULT_MAX_ROWS,
    ) -> dict[str, Any]:
        """Execute a SELECT query and return formatted results.
        
        Args:
            connection_name: Name of the registered connection.
            query: SELECT SQL query (must be validated SELECT-only by caller).
            timeout: Query execution timeout in seconds.
            max_rows: Maximum number of rows to return.
        """
        async with await self.get_connection(connection_name) as conn:
            # N7: Use conn.fetch(query) directly instead of conn.prepare(query).fetch()
            # to avoid server-side prepared statement cache churn for ad-hoc queries
            rows = await asyncio.wait_for(conn.fetch(query), timeout=timeout)
            # Truncate if exceeds max_rows
            truncated = len(rows) > max_rows
            rows = rows[:max_rows]
            # Convert Record objects to dicts
            columns = [col for col in rows[0].keys()] if rows else []
            data = [dict(row) for row in rows]
            return {
                "columns": columns,
                "rows": data,
                "row_count": len(data),
                "truncated": truncated,
            }

    async def dispose(self, connection_name: str) -> None:
        """Dispose pool for a connection. Safe to call if no pool exists."""
        pool = self._pools.pop(connection_name, None)
        if pool:
            await pool.close()
            logger.info(f"Disposed connection pool for '{connection_name}'")

    async def dispose_all(self) -> None:
        """Dispose all cached pools. Called during daemon shutdown (C6, N2)."""
        for name in list(self._pools.keys()):
            await self.dispose(name)
```

### Manager-Level Wiring (C3, N1, N5)

In `daemon/manager.py`:

```python
# N10: Model import at MODULE LEVEL (top of file), NOT inline in __init__:
from .repositories.db_connection.models import DbConnectionConfig  # ← top of manager.py

class InstanceManager:
    def __init__(
        self,
        config: Config,
        ensemble_config: EnsembleConfig | None = None,
        credential_manager: "CredentialManager | None" = None,  # N5: injected from app.state
    ):
        # ... existing init ...
        
        # N5: Use injected CredentialManager (from app.state.credential_manager)
        # Fall back to creating one only if not provided (backward compat for tests)
        from ..sources.credentials import CredentialManager
        if credential_manager is None:
            credential_manager = CredentialManager()
        self._credential_manager = credential_manager
        
        # N1: Repository takes engine ONLY — no credential_manager
        from ..repositories.db_connection.repository import DbConnectionRepository
        self._db_connection_repository = DbConnectionRepository(self._engine)
        
        # Pool manager gets credential_manager for DSN decryption
        from ..services.db_pool_manager import ConnectionPoolManager
        self._db_pool_manager = ConnectionPoolManager(
            self._db_connection_repository,
            self._credential_manager
        )
        
        # ... (SQLModel.metadata.create_all(self._engine) already happens below) ...

    # Public read-only properties:
    @property
    def db_connection_repository(self):
        return self._db_connection_repository

    @property
    def db_pool_manager(self):
        return self._db_pool_manager

    @property
    def credential_manager(self):  # BLOCKER 2: Phase 3 tools reference manager.credential_manager
        return self._credential_manager
```

### api.py Change (N5, BLOCKER 1)

```python
# BLOCKER 1: Reorder IS needed.
# Current code: InstanceManager at line 172, CredentialManager() at line 232.
# CredentialManager must be constructed BEFORE InstanceManager.

# BEFORE (current code — WRONG ORDER):
#   line 172: manager = InstanceManager(config, ensemble_config)
#   ...
#   line 232: credential_manager = CredentialManager()

# AFTER (corrected — move CredentialManager up):
# Step 1: Construct CredentialManager FIRST (move from line 232 to before line 172)
credential_manager = CredentialManager()

# Step 2: Construct InstanceManager with credential_manager injected
manager = InstanceManager(config, ensemble_config, credential_manager=credential_manager)
await manager.initialize()

# Step 3: Remove the old CredentialManager() at line 232 (it's now constructed above)
# The existing app.state.credential_manager = credential_manager assignment at line 378 stays.

# Step 4: Verify app.state still gets it (unchanged):
#   app.state.credential_manager = credential_manager  ← already at line 378, keep as-is
```

### Lifecycle Cleanup — shutdown() Steps ONLY (C6, N2, N6)

In `daemon/manager.py` `shutdown()` method (manager.py:2867-2878):

```python
# Add to the existing `steps` list in shutdown():
steps = [
    ("stop_sources", self.stop_sources(timeout=grace_period)),
    ("cancel_active_requests", self._cancel_all_active_requests()),
    ("wait_inflight", self._wait_for_inflight(grace_period)),
    ("shutdown_worker_pool", asyncio.to_thread(self.shutdown_worker_pool)),
    ("shutdown_event_bus", self._event_bus.shutdown()),
    ("shutdown_maintenance_service", self._maintenance_service.stop() if self._maintenance_service else asyncio.sleep(0)),
    ("dispose_db_pools", self._db_pool_manager.dispose_all() if hasattr(self, '_db_pool_manager') else asyncio.sleep(0)),  # NEW
    ("close_checkpointer", self.close_checkpointer()),
    ("drain_mcp_pool", self._drain_warmup_pool()),
    ("shutdown_mcp_service", self._mcp_service.close_all_connections()),
    ("shutdown_opencode_registry", self._shutdown_opencode_registry()),
]
```

> **N2:** Do NOT modify `cleanup()` — it is a SYNC method (`def cleanup(self) -> None:` at manager.py:2786) called without await from `shutdown()` at line 2889. Pool disposal is async, so it goes in the `steps` list (which are all coroutines awaited in shutdown).
>
> **N6:** Pool disposal goes in `shutdown()` steps ONLY — NOT also in `api.py` lifespan. This avoids double-disposal. The `shutdown()` method is already called during lifespan shutdown.

## Constraints
- **C3 — Singleton at manager level:** The `ConnectionPoolManager` must be created ONCE in `InstanceManager.__init__`, NOT per-instance.
- **N1 — Repository has no credential_manager:** The pool manager decrypts credentials via `CredentialManager` when building DSNs. The repository only returns opaque encrypted strings.
- **N5 — Inject existing CredentialManager:** The `CredentialManager` is injected from `app.state` via `InstanceManager.__init__` constructor param. Do NOT create a new `CredentialManager()` in the manager.
- Pools MUST be created lazily (not at startup) to avoid connecting to databases that may not be used
- Pool creation MUST be thread-safe (use `asyncio.Lock` with double-check pattern)
- `dispose()` MUST be idempotent (safe to call multiple times)
- DSN construction must NOT log the password (it's in a local variable only)
- **N3 — Three-case DSN building:** `_build_dsn()` must handle: (1) user+password, (2) user without password (preserves username for .pgpass/peer/IAM auth), (3) truly anonymous. Never drop the username when password is None.
- **W1 + N9 — Sanitize error messages:** Error messages MUST NOT include the DSN (which contains password). Use `_sanitize_error()` which: (1) takes first line only, (2) redacts DSN format (`postgresql://user:password@host`), (3) redacts `password=...` format, (4) redacts PostgreSQL native quoted format `password "..."` (BLOCKER 3), (5) final safety-net scan for residual `user:password@host` patterns.
- **W2 — Query timeout:** `execute_select()` MUST use `asyncio.wait_for(conn.fetch(query), timeout=timeout)`.
- **N7 — Direct fetch, no prepare:** Use `conn.fetch(query)` instead of `conn.prepare(query).fetch()` to avoid server-side prepared statement cache churn for ad-hoc queries.
- **N2 — Do NOT modify `cleanup()`:** `cleanup()` is sync and called without await. Pool disposal (async) goes in the `shutdown()` `steps` list.
- **N6 — Disposal in `shutdown()` ONLY:** Do NOT also add disposal to `api.py` lifespan. Avoids double-disposal.
- **N10 — Model import at module level:** `DbConnectionConfig` import is at the top of `manager.py`, not inline in `__init__`.
- Query timeout default: 30 seconds
- Row limit default: 1000 rows

## Deliverables
- [ ] `daemon/services/db_pool_manager.py` with `ConnectionPoolManager`
- [ ] `_build_dsn()` handles three cases: user+password, user-only, anonymous (N3)
- [ ] `_sanitize_error()` redacts DSN/password patterns including PostgreSQL native quoted format (W1, N9, BLOCKER 3)
- [ ] Lazy pool creation with double-check locking
- [ ] `execute_select()` uses `conn.fetch(query)` directly (N7) with `asyncio.wait_for()` timeout (W2)
- [ ] `get_connection()`, `test_connection()`, `execute_select()`, `dispose()`, `dispose_all()`
- [ ] Pool manager wired into `InstanceManager` with injected `CredentialManager` (C3, N5)
- [ ] Repository has NO credential_manager — pool manager decrypts (N1)
- [ ] `credential_manager=credential_manager` passed to `InstanceManager` in `api.py` (N5)
- [ ] Pool disposal step added to `shutdown()` steps list (C6, N2, N6)
- [ ] `cleanup()` NOT modified (N2)
- [ ] `tests/test_db_pool_manager.py` passing (with mocked asyncpg)
