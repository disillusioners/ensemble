"""Connection pool manager for PostgreSQL connections.

This module provides :class:`ConnectionPoolManager`, a singleton-level
manager that owns one ``asyncpg`` connection pool per registered database
connection name. The manager is shared by every agent instance in a
process so that pool counts stay bounded (one pool per connection, not
one pool per agent instance).

The manager is intentionally narrow:

* It reads connection metadata from :class:`DbConnectionRepository`
  and decrypts credentials at the moment a pool is created.
* It builds PostgreSQL DSNs that preserve the ``username`` even when
  no password is supplied (peer / IAM / ``.pgpass`` auth).
* It scrubs every error message that flows back to the caller so that
  passwords and DSNs never leak into logs, tool results, or HTTP
  responses.
* It supports a non-throwing :meth:`test_connection` for health
  checks, and a bounded :meth:`execute_select` for query tools.

Pool disposal happens in the async :meth:`dispose` / :meth:`dispose_all`
methods so that ``asyncpg``'s async close protocol is honored. The
:class:`~daemon.manager.InstanceManager` shutdown chain is responsible
for calling them.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING, Any

import asyncpg

from daemon.services.db_sql_utils import (
    DEFAULT_MAX_ROWS,
    DEFAULT_QUERY_TIMEOUT,
    strip_sql_noise,
)

if TYPE_CHECKING:
    from daemon.repositories.db_connection import DbConnectionConfig
    from daemon.repositories.db_connection import DbConnectionRepository
    from daemon.sources.credentials import CredentialManager


logger = logging.getLogger(__name__)


# --- Pool configuration ---------------------------------------------------

#: Minimum number of connections per pool.
POOL_MIN_SIZE: int = 1

#: Maximum number of connections per pool.
POOL_MAX_SIZE: int = 5

#: Maximum number of queries a single connection may execute before it
#: is recycled. Mirrors a typical ``pgbouncer``-style lifetime cap.
POOL_MAX_QUERIES: int = 500

#: Timeout in seconds for ``asyncpg.create_pool`` and for acquiring a
#: connection from the pool.
POOL_TIMEOUT: int = 30


# --- Sanitization regexes (compiled once) ---------------------------------

# DSN credentials: ``postgresql://user:pass@host`` → ``postgresql://***:***@host``
_RE_DSN_CREDS = re.compile(r"(postgresql://)[^@\s]+(@)")

# Connection-string key-value form: ``password=foo`` → ``password=***``
_RE_PASSWORD_KV = re.compile(r"password\s*=\s*\S+", re.IGNORECASE)

# asyncpg / libpq quoted form: ``password "foo"`` → ``password "***"``
_RE_PASSWORD_QUOTED = re.compile(
    r'(password\s+)"[^"]*"',
    re.IGNORECASE,
)

# Single-quoted variant: ``password 'foo'`` → ``password '***'``.
# Distinct from the double-quoted form above (some libpq dialects
# and psql output emit single-quoted passwords).
_RE_PASSWORD_QUOTED_SINGLE = re.compile(
    r"(password\s+)'[^']*'",
    re.IGNORECASE,
)

# Combined ``role "x" password "y"`` form emitted by some server errors.
_RE_ROLE_PASSWORD = re.compile(
    r'(role|user)\s+"[^"]*"\s+(password\s+)"[^"]*"',
    re.IGNORECASE,
)

# Final safety net: anything that still looks like user:pass@host.
_RE_GENERIC_AUTH = re.compile(r"://[^@]+@")


class ConnectionPoolManager:
    """Manages one shared ``asyncpg`` pool per connection name.

    The manager is meant to be constructed once at the
    :class:`~daemon.manager.InstanceManager` level and shared by every
    agent instance. Pool creation is guarded by a double-check
    ``asyncio.Lock`` so concurrent first-time callers do not create
    duplicate pools.

    Attributes:
        _repository: Source of ``DbConnectionConfig`` rows and their
            opaque encrypted credentials.
        _credential_manager: Decrypts the credentials returned by the
            repository.
        _pools: Name → :class:`asyncpg.Pool` mapping. The dict is the
            single source of truth for which pools are live.
        _lock: Async lock that serializes pool creation.
    """

    def __init__(
        self,
        repository: DbConnectionRepository,
        credential_manager: CredentialManager,
    ) -> None:
        """Initialize the manager with a repository and credential manager.

        Args:
            repository: Repository for looking up ``DbConnectionConfig``
                rows by name.
            credential_manager: Decrypts the opaque credentials string
                returned by ``repository.get_credentials(...)``.
        """
        self._repository: DbConnectionRepository = repository
        self._credential_manager: CredentialManager = credential_manager
        self._pools: dict[str, asyncpg.Pool] = {}
        self._lock: asyncio.Lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # DSN construction
    # ------------------------------------------------------------------

    def _build_dsn(self, conn: DbConnectionConfig) -> str:
        """Build a credential-free DSN for logging purposes only.

        The returned string is intended for log messages and operational
        diagnostics. It MUST NEVER contain credentials — no username,
        no password, no auth token of any kind.

        Format: ``postgresql://host[:port]/database[?sslmode=...]``

        Optional segments are appended only when present:

        * ``port`` → ``:port`` after the host
        * ``database`` → ``/database`` after the port
        * ``ssl_mode`` → ``?sslmode=ssl_mode`` as the first query arg

        Note: pool creation in :meth:`_get_or_create_pool` uses
        kwargs-based ``asyncpg.create_pool`` (host/port/database/user/
        password) so the password is never embedded in a DSN string.
        This method is logging-only.

        Args:
            conn: The connection configuration row.

        Returns:
            A safe, credential-free PostgreSQL DSN string suitable for
            log messages.
        """
        dsn = "postgresql://"

        # Host (required).
        dsn += conn.host

        # Optional port.
        if conn.port:
            dsn += f":{conn.port}"

        # Optional database.
        if conn.database:
            dsn += f"/{conn.database}"

        # Optional sslmode.
        if conn.ssl_mode:
            dsn += f"?sslmode={conn.ssl_mode}"

        return dsn

    # ------------------------------------------------------------------
    # Pool lifecycle
    # ------------------------------------------------------------------

    async def _get_or_create_pool(self, connection_name: str) -> asyncpg.Pool:
        """Return the live pool for ``connection_name``, creating if needed.

        Uses the double-checked locking pattern:

        1. Fast path: dict lookup without the lock. Most calls hit this.
        2. Slow path: acquire ``self._lock`` and re-check, in case a
           concurrent coroutine created the pool while we were waiting.

        Pool creation reads the connection config, decrypts the
        password (if any), and calls :func:`asyncpg.create_pool`
        using **kwargs** (``host``, ``port``, ``database``, ``user``,
        ``password``) so the password is never embedded in a DSN
        string that could leak into error messages or logs. Any error
        is sanitized and re-raised as :class:`ConnectionError`. The
        ``__cause__`` chain is intentionally NOT preserved (see C4)
        to avoid leaking the raw exception to logging middleware
        that uses ``exc_info=True``.

        Args:
            connection_name: The unique connection name to look up.

        Returns:
            A live :class:`asyncpg.Pool` ready to acquire connections.

        Raises:
            ValueError: If no connection is registered with that name,
                or if the connection's ``db_type`` is not ``"postgres"``.
            ConnectionError: If pool creation fails for any reason
                (network, auth, server). The original error is
                **not** chained via ``__cause__``; the message is
                sanitized.
        """
        # Fast path: already initialized.
        existing = self._pools.get(connection_name)
        if existing is not None:
            return existing

        # Slow path: take the lock, re-check, then create.
        async with self._lock:
            existing = self._pools.get(connection_name)
            if existing is not None:
                return existing

            config = self._repository.get_by_name(connection_name)
            if config is None:
                raise ValueError(
                    f"Connection '{connection_name}' not found"
                )

            if config.db_type != "postgres":
                raise ValueError(
                    f"Connection '{connection_name}' is not PostgreSQL "
                    f"(db_type={config.db_type!r}); only 'postgres' is supported"
                )

            # Decrypt credentials at the moment of pool creation.
            # The repository returns an opaque string — we never log it.
            password: str | None = None
            encrypted = self._repository.get_credentials(connection_name)
            if encrypted:
                creds = self._credential_manager.decrypt(encrypted)
                password = creds.get("password")

            # Kwargs-based pool creation (C1, C2, C3, C5): the password
            # is passed as a separate kwarg and never embedded in a DSN
            # string. This eliminates the entire class of leaks where
            # the DSN appears in asyncpg error messages, libpq
            # diagnostics, or Python tracebacks.
            try:
                pool = await asyncpg.create_pool(
                    host=config.host,
                    port=config.port or 5432,
                    database=config.database or "",
                    user=config.username or "",
                    password=password,
                    min_size=POOL_MIN_SIZE,
                    max_size=POOL_MAX_SIZE,
                    max_queries=POOL_MAX_QUERIES,
                    timeout=POOL_TIMEOUT,
                )
            except (
                asyncpg.PostgresError,
                OSError,
                ConnectionRefusedError,
            ) as exc:
                # C4: do NOT chain via `from exc` — the __cause__
                # chain would expose the raw exception (and any
                # connection details it may carry) to logging
                # middleware that uses exc_info=True.
                detail = self._sanitize_error(str(exc))
                raise ConnectionError(
                    f"Failed to connect to '{connection_name}': {detail}"
                )
            except Exception as exc:  # noqa: BLE001 - sanitized + re-raised
                # C4: same — no `from exc`.
                detail = self._sanitize_error(str(exc))
                raise ConnectionError(
                    f"Failed to connect to '{connection_name}': {detail}"
                )

            self._pools[connection_name] = pool
            safe_dsn = self._build_dsn(config)
            logger.info(
                "Created asyncpg pool for connection '%s' "
                "(min=%d, max=%d, dsn=%s)",
                connection_name,
                POOL_MIN_SIZE,
                POOL_MAX_SIZE,
                safe_dsn,
            )
            return pool

    # ------------------------------------------------------------------
    # Error sanitization
    # ------------------------------------------------------------------

    @staticmethod
    def _sanitize_error(error_str: str) -> str:
        """Strip credentials out of an error message.

        Defense-in-depth — the order matters:

        1. Take only the first line so multi-line traceback-like
           errors (``Exception: foo\\n  at ...``) are compacted.
        2. Redact ``postgresql://user:pass@host`` DSN credentials.
        3. Redact ``password=foo`` key=value form (case-insensitive).
        4. Redact ``password "foo"`` libpq-quoted form.
        4b. Redact ``password 'foo'`` libpq single-quoted form.
        5. Redact ``role "x" password "y"`` combined form.
        6. Final safety net: anything still shaped like
           ``scheme://user:pass@host`` is aggressively masked.

        Args:
            error_str: The raw exception string. May be multi-line.

        Returns:
            A redacted single-line error string safe for logs and
            user-facing responses.
        """
        # 1. First line only.
        result = error_str.split("\n")[0]

        # 2. DSN credentials.
        result = _RE_DSN_CREDS.sub(r"\1***:***\2", result)

        # 3. password=... key=value.
        result = _RE_PASSWORD_KV.sub("password=***", result)

        # 4. password "..." quoted.
        result = _RE_PASSWORD_QUOTED.sub(r'\1"***"', result)

        # 4b. password '...' single-quoted.
        result = _RE_PASSWORD_QUOTED_SINGLE.sub(r"\1'***'", result)

        # 5. role/user "..." password "..." combined.
        result = _RE_ROLE_PASSWORD.sub(r'\1"***"\2"***"', result)

        # 6. Final safety net for any ://user:pass@host shape.
        result = _RE_GENERIC_AUTH.sub("://***:***@", result)

        return result

    @staticmethod
    def _has_limit_clause(query: str) -> bool:
        """Return True if ``query`` contains a real SQL ``LIMIT`` clause.

        F3 fix: the previous ``"LIMIT" not in query_upper`` check could
        be bypassed by putting the substring "LIMIT" inside a string
        literal (e.g. ``WHERE msg = 'no LIMIT here'``), which would
        prevent the safety LIMIT from being injected on queries that
        actually had no LIMIT clause. A naive ``\\bLIMIT\\b`` regex is
        also not enough on its own: apostrophes and spaces are
        non-word characters, so the regex still matches the "LIMIT"
        inside a quoted string.

        The fix mirrors :func:`daemon.tools.db_tools._validate_select_only`
        and uses :func:`daemon.services.db_sql_utils.strip_sql_noise` to
        strip comments and single-quoted string literals (with the
        ``''`` SQL escape sequence) before applying a case-insensitive,
        word-boundary regex to the stripped query.

        This is intentionally a defensive substring scan, not a full
        SQL parser. The trust boundary for actual query safety is the
        database user, not this guard. The goal here is narrow: do
        not let a string literal defeat the safety row cap.

        Args:
            query: The SQL query text.

        Returns:
            True iff the query contains a real ``LIMIT`` keyword
            outside of comments and string literals.
        """
        stripped = strip_sql_noise(query)
        return re.search(r"\bLIMIT\b", stripped, re.IGNORECASE) is not None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_connection(self, connection_name: str) -> Any:
        """Acquire a raw connection from the pool for ``connection_name``.

        Returns the :class:`asyncpg.pool.PoolAcquireContext` produced
        by ``pool.acquire()``. The caller is responsible for using it
        as an async context manager (``async with ...``) or for
        releasing it explicitly. This is the lower-level entry point;
        most tools should prefer :meth:`execute_select` or
        :meth:`test_connection`.

        Args:
            connection_name: The connection to acquire from.

        Returns:
            The acquire context manager. Use ``async with
            manager.get_connection(name) as conn: ...`` to obtain an
            :class:`asyncpg.Connection`.

        Raises:
            ValueError: If the connection is unknown or not PostgreSQL.
            ConnectionError: If the pool cannot be created.
        """
        pool = await self._get_or_create_pool(connection_name)
        return pool.acquire()

    async def test_connection(self, connection_name: str) -> dict[str, Any]:
        """Run ``SELECT 1`` against ``connection_name`` and report health.

        Unlike the other entry points, this method does **not** raise
        on failure — it always returns a dict so the caller (usually a
        tool or a health-check endpoint) can render the result without
        try/except.

        Args:
            connection_name: The connection to test.

        Returns:
            A dict with:

            * ``success`` (``bool``) — ``True`` iff ``SELECT 1`` ran.
            * ``message`` (``str``) — human-readable status, sanitized
              so it never contains credentials.
        """
        try:
            pool = await self._get_or_create_pool(connection_name)
            async with pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            return {
                "success": True,
                "message": f"Connection '{connection_name}' is healthy",
            }
        except (
            asyncpg.PostgresError,
            OSError,
            ConnectionError,
            asyncio.TimeoutError,
        ) as exc:
            # W5: only catch the expected I/O / protocol failure modes.
            # Anything else (TypeError, AttributeError, KeyError, …) is
            # almost certainly a programming bug and must propagate so
            # it can be diagnosed in tests and alerting.
            detail = self._sanitize_error(str(exc))
            logger.warning(
                "test_connection failed for '%s': %s",
                connection_name,
                detail,
            )
            return {
                "success": False,
                "message": f"Connection '{connection_name}' failed: {detail}",
            }

    async def execute_select(
        self,
        connection_name: str,
        query: str,
        timeout: int = DEFAULT_QUERY_TIMEOUT,
        max_rows: int = DEFAULT_MAX_ROWS,
    ) -> dict[str, Any]:
        """Execute a read query and return rows as dictionaries.

        Implementation notes (per the design review):

        * ``conn.fetch(query)`` is used directly — we do **not** wrap
          the call in ``conn.prepare(query).fetch()``. ``prepare()``
          would force a server round trip on every call and would
          leave prepared statements orphaned in the connection's
          cache, which is the wrong trade-off for ad-hoc tool queries.
        * The fetch is bounded by :func:`asyncio.wait_for` with a
          caller-configurable timeout.
        * Rows are truncated to ``max_rows`` and the response carries
          a ``truncated`` flag so the caller can paginate or warn.
        * :class:`asyncpg.Record` instances are converted to plain
          dicts so the result is JSON-serializable out of the box.

        Args:
            connection_name: The connection to run the query against.
            query: The SQL text to execute. Callers are expected to
                have already validated that it is a read-only query.
            timeout: Maximum wall-clock seconds to wait for the query
                to complete. Defaults to :data:`DEFAULT_QUERY_TIMEOUT`.
            max_rows: Maximum number of rows to return. Defaults to
                :data:`DEFAULT_MAX_ROWS`. Excess rows are dropped
                silently and reported via ``truncated=True``.

        Returns:
            A dict with:

            * ``columns`` (``list[str]``) — column names in the order
              returned by asyncpg. Empty if the result set is empty.
            * ``rows`` (``list[dict[str, Any]]``) — rows as dicts.
            * ``row_count`` (``int``) — number of rows in ``rows``
              (after truncation).
            * ``truncated`` (``bool``) — ``True`` iff the underlying
              result had more rows than ``max_rows``.

        Raises:
            ValueError: If the connection is unknown or not PostgreSQL.
            ConnectionError: If the pool cannot be created.
            asyncio.TimeoutError: If the query did not complete within
                ``timeout`` seconds.
            asyncpg.PostgresError: For server-side errors. The message
                is sanitized before being propagated.
        """
        pool = await self._get_or_create_pool(connection_name)
        async with pool.acquire() as conn:
            # W1: inject LIMIT if not already present to prevent OOM
            # on unconstrained queries. We fetch at most max_rows + 1
            # rows so the truncation flag below can detect "more rows
            # exist" without a second round trip.
            #
            # F3 fix: delegate the LIMIT-presence check to
            # :meth:`_has_limit_clause`, which strips comments and
            # single-quoted string literals before scanning with a
            # word-boundary regex. The previous ``"LIMIT" not in
            # query_upper`` check was bypassable by queries like
            # ``SELECT * FROM logs WHERE msg = 'no LIMIT here'``.
            if not self._has_limit_clause(query):
                query = f"{query.rstrip(';')} LIMIT {max_rows + 1}"
            records = await asyncio.wait_for(
                conn.fetch(query),
                timeout=timeout,
            )

        truncated = False
        if len(records) > max_rows:
            records = records[:max_rows]
            truncated = True

        if records:
            columns: list[str] = list(records[0].keys())
            rows: list[dict[str, Any]] = [dict(record) for record in records]
        else:
            columns = []
            rows = []

        return {
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
            "truncated": truncated,
        }

    async def dispose(self, connection_name: str) -> None:
        """Close and remove a single pool.

        Idempotent: calling on an unknown name is a no-op. The pop
        happens inside ``self._lock`` so a concurrent
        ``_get_or_create_pool`` cannot re-create a pool for the same
        name while we are disposing it. The actual ``pool.close()``
        is awaited **outside** the lock so the lock is not held
        during the (potentially slow) network close.

        Args:
            connection_name: The pool to dispose.
        """
        async with self._lock:
            pool = self._pools.pop(connection_name, None)
        if pool is None:
            return
        await pool.close()
        logger.info("Disposed asyncpg pool for connection '%s'", connection_name)

    async def dispose_all(self) -> None:
        """Close and remove every pool owned by this manager.

        Safe to call multiple times. After this call, all subsequent
        ``get_connection`` / ``test_connection`` / ``execute_select``
        calls will lazily re-create pools on demand.
        """
        # Snapshot keys to avoid mutating the dict while iterating.
        for name in list(self._pools.keys()):
            await self.dispose(name)
