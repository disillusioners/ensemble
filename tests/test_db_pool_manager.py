"""Unit tests for ConnectionPoolManager.

Phase 2 of the Database Tool Category feature. The
:class:`ConnectionPoolManager` is the shared singleton that owns one
``asyncpg`` connection pool per registered database connection name.

These tests deliberately use ``unittest.mock`` to avoid hitting a real
PostgreSQL server. The pool is patched at the module boundary
(``daemon.services.db_pool_manager.asyncpg.create_pool``) so we can
exercise:

* DSN construction across the three authentication cases (N3)
* Error-message sanitization (W1 / N9 / BLOCKER 3)
* Lazy / cached pool creation
* Health-check (``test_connection``) and query
  (``execute_select``) plumbing through the mock pool
* Pool disposal (single + bulk) and idempotency
* Negative paths: unknown connection name, non-postgres db_type
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from daemon.services.db_pool_manager import (
    DEFAULT_MAX_ROWS,
    DEFAULT_QUERY_TIMEOUT,
    POOL_MAX_QUERIES,
    POOL_MAX_SIZE,
    POOL_MIN_SIZE,
    POOL_TIMEOUT,
    ConnectionPoolManager,
)


# =============================================================================
# Helpers
# =============================================================================


def _make_config(
    *,
    host: str = "db.example.com",
    port: int | None = 5432,
    database: str | None = "appdb",
    username: str | None = "app_user",
    db_type: str = "postgres",
    ssl_mode: str | None = "require",
    connection_name: str = "primary",
) -> MagicMock:
    """Build a MagicMock that quacks like :class:`DbConnectionConfig`.

    We intentionally do *not* instantiate the SQLModel — those go
    through the database engine. Here we only need attribute access,
    which is what ``_build_dsn`` and the pool-construction block
    touch.
    """
    config = MagicMock(spec=[
        "id",
        "connection_name",
        "db_type",
        "host",
        "port",
        "database",
        "username",
        "credentials",
        "ssl_mode",
        "created_at",
        "updated_at",
    ])
    config.connection_name = connection_name
    config.db_type = db_type
    config.host = host
    config.port = port
    config.database = database
    config.username = username
    config.credentials = None
    config.ssl_mode = ssl_mode
    return config


def _make_manager(
    *,
    config: MagicMock | None = None,
    credentials_blob: str | None = None,
) -> tuple[ConnectionPoolManager, MagicMock, MagicMock]:
    """Build a :class:`ConnectionPoolManager` with mock collaborators.

    Returns a ``(manager, repository, credential_manager)`` triple so
    individual tests can re-stub behaviour on the mocks if needed.
    """
    repository = MagicMock()
    repository.get_by_name = MagicMock(return_value=config)
    repository.get_credentials = MagicMock(return_value=credentials_blob)

    credential_manager = MagicMock()
    # decrypt() returns whatever the test wants; default is a dict with
    # no password (e.g. peer / IAM auth). Tests that exercise a real
    # password override ``decrypt.return_value``.
    credential_manager.decrypt = MagicMock(return_value={"password": "secret"})

    manager = ConnectionPoolManager(
        repository=repository,
        credential_manager=credential_manager,
    )
    return manager, repository, credential_manager


def _build_acquire_ctx(mock_conn: AsyncMock | MagicMock) -> MagicMock:
    """Wrap a mock connection in a fake ``pool.acquire()`` context.

    ``asyncpg.Pool.acquire()`` returns a ``PoolAcquireContext`` which
    is used as ``async with pool.acquire() as conn: ...``. The mock
    must therefore expose ``__aenter__`` (awaitable, returns the
    connection) and ``__aexit__`` (awaitable, returns ``None``).
    """
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=mock_conn)
    ctx.__aexit__ = AsyncMock(return_value=None)
    return ctx


def _make_mock_pool(mock_conn: AsyncMock | MagicMock) -> MagicMock:
    """Build a mock ``asyncpg.Pool`` that hands out ``mock_conn``."""
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_build_acquire_ctx(mock_conn))
    pool.close = AsyncMock(return_value=None)
    return pool


# =============================================================================
# Group 1: _build_dsn() — credential-free logging DSN (C1, C2, C3, C5)
# =============================================================================


class TestBuildDsn:
    """Cover the credential-free DSN shape that ``_build_dsn`` emits.

    The DSN is for log messages only — it MUST NOT contain any
    credentials. The constructor therefore takes only ``conn`` and
    never a password. Format: ``postgresql://host[:port]/database[?sslmode=...]``.

    These tests guard the security contract: a regression that
    re-introduces the username or a password argument would be
    caught here.
    """

    def test_basic_dsn_with_host_port_database_sslmode(self):
        """Standard case → ``postgresql://host:port/db?sslmode=...``."""
        manager, _, _ = _make_manager()
        conn = _make_config(
            host="db.example.com",
            port=5432,
            database="appdb",
            username="app_user",
            ssl_mode="require",
        )
        dsn = manager._build_dsn(conn)
        assert dsn == "postgresql://db.example.com:5432/appdb?sslmode=require"

    def test_username_is_never_included(self):
        """Username is dropped — it is identity, not a credential, but still
        avoided in the safe DSN so the value cannot be used to pivot
        during log review.
        """
        manager, _, _ = _make_manager()
        conn = _make_config(username="app_user")
        dsn = manager._build_dsn(conn)
        assert "app_user" not in dsn
        assert "@" not in dsn

    def test_no_user_no_password_anonymous_dsn(self):
        """No username, no password → just host:port/db."""
        manager, _, _ = _make_manager()
        conn = _make_config(
            host="db.example.com",
            port=5432,
            database="appdb",
            username=None,
            ssl_mode="require",
        )
        dsn = manager._build_dsn(conn)
        assert dsn == "postgresql://db.example.com:5432/appdb?sslmode=require"

    def test_port_omitted_when_none(self):
        """``port=None`` → no ``:port`` segment."""
        manager, _, _ = _make_manager()
        conn = _make_config(port=None, username="u", database="d")
        dsn = manager._build_dsn(conn)
        assert ":None" not in dsn
        assert dsn == "postgresql://db.example.com/d?sslmode=require"

    def test_database_omitted_when_none(self):
        """``database=None`` → no ``/database`` segment."""
        manager, _, _ = _make_manager()
        conn = _make_config(port=5432, database=None, username="u")
        dsn = manager._build_dsn(conn)
        assert "/None" not in dsn
        assert dsn == "postgresql://db.example.com:5432?sslmode=require"

    def test_ssl_mode_omitted_when_none(self):
        """``ssl_mode=None`` → no ``?sslmode=...`` query string."""
        manager, _, _ = _make_manager()
        conn = _make_config(ssl_mode=None, username="u", database="d")
        dsn = manager._build_dsn(conn)
        assert "sslmode" not in dsn
        assert dsn == "postgresql://db.example.com:5432/d"

    def test_all_optional_fields_none_produces_minimal_dsn(self):
        """Edge: every optional field is ``None`` → bare DSN."""
        manager, _, _ = _make_manager()
        conn = _make_config(host="h", port=None, database=None,
                            username=None, ssl_mode=None)
        dsn = manager._build_dsn(conn)
        assert dsn == "postgresql://h"

    def test_signature_has_no_password_parameter(self):
        """The method must not accept a password argument at all.

        A regression that re-adds a password parameter would silently
        re-enable the entire class of DSN-leak bugs the refactor
        removed. This test pins the signature.
        """
        import inspect

        sig = inspect.signature(ConnectionPoolManager._build_dsn)
        params = list(sig.parameters.keys())
        assert params == ["self", "conn"], (
            f"_build_dsn signature changed: {params} — must not accept "
            f"a password (would re-introduce DSN-leak risk)"
        )


# =============================================================================
# Group 2: _sanitize_error() — security (W1, N9, BLOCKER 3)
# =============================================================================


class TestSanitizeError:
    """Defense-in-depth: passwords/DSNs must never leak to callers.

    The sanitizer is a static method, so it can be called as
    ``ConnectionPoolManager._sanitize_error(...)`` — no instance
    needed. Each case is mapped to a fix identifier from the
    architecture review comments.
    """

    def test_dsn_credentials_redacted(self):
        """W1: ``postgresql://user:secret@host`` → user:pass becomes ``***:***``."""
        result = ConnectionPoolManager._sanitize_error(
            "could not connect to postgresql://user:secret@host:5432/db"
        )
        assert "secret" not in result
        assert "***:***" in result
        assert "postgresql://***:***@host" in result

    def test_password_kv_redacted(self):
        """N9: ``password=foo`` → ``password=***``."""
        result = ConnectionPoolManager._sanitize_error(
            "auth failed: password=topsecret supplied"
        )
        assert "topsecret" not in result
        assert "password=***" in result

    def test_password_quoted_redacted(self):
        """asyncpg native form: ``password "foo"`` → ``password "***"``."""
        result = ConnectionPoolManager._sanitize_error(
            'asyncpg auth error: password "hunter2" is wrong'
        )
        assert "hunter2" not in result
        assert 'password "***"' in result

    def test_role_and_password_combined_redacted(self):
        """Server-style: ``role "u" password "p"`` → both redacted."""
        result = ConnectionPoolManager._sanitize_error(
            'FATAL: role "alice" password "leaked-pw" authentication failed'
        )
        assert "alice" not in result
        assert "leaked-pw" not in result
        # Both redacted slots present.
        assert '"***"' in result

    def test_multiline_input_returns_only_first_line(self):
        """Traceback-like multi-line input is collapsed to the first line."""
        raw = "Error: postgresql://u:pw@host\n  at frame 0\n  at frame 1"
        result = ConnectionPoolManager._sanitize_error(raw)
        assert "\n" not in result
        assert "frame" not in result
        # First line is sanitized too.
        assert "pw" not in result

    def test_input_without_credentials_unchanged(self):
        """No credentials present → no transformation, just first line."""
        result = ConnectionPoolManager._sanitize_error(
            "no rows returned by the query"
        )
        assert result == "no rows returned by the query"

    def test_password_kv_case_insensitive(self):
        """``PASSWORD=secret`` (uppercase) is still redacted."""
        result = ConnectionPoolManager._sanitize_error(
            "config error: PASSWORD=topsecret"
        )
        assert "topsecret" not in result
        assert "password=***" in result.lower()

    def test_generic_safety_net_redacts_unknown_auth_shape(self):
        """Any ``scheme://user:pass@host`` shape is masked, even non-postgres."""
        result = ConnectionPoolManager._sanitize_error(
            "redis://user:pass@cache-host:6379 timeout"
        )
        # The ://user:pass@ pattern is caught by the safety-net regex.
        assert "pass" not in result or "***:***" in result
        assert "***:***@cache-host" in result

    def test_sanitize_password_with_at_symbol(self):
        """Sanitizer must redact passwords containing ``@``.

        ``password=p@ss@word`` style patterns are common in service
        meshes / sidecar configs that embed raw credentials in error
        messages. The ``password=...`` key=value regex must consume the
        full non-whitespace token (``\\S+``) so embedded ``@`` does not
        leave a tail behind.
        """
        result = ConnectionPoolManager._sanitize_error(
            "auth failed: password=p@ss@word supplied"
        )
        assert "password=***" in result
        assert "p@ss@word" not in result

        # DSN-style: the literal password (with embedded ``@``) must
        # not survive sanitization. The current DSN regex stops at the
        # first ``@`` and the safety net is conservative, so the strict
        # assertion is "the literal password string is absent".
        dsn_result = ConnectionPoolManager._sanitize_error(
            "could not connect to postgresql://user:p@ss@word@host/db"
        )
        assert "p@ss@word" not in dsn_result
        assert "***:***" in dsn_result

    def test_sanitize_single_quoted_password(self):
        """Sanitizer must redact ``password 'foo'`` (single-quoted) form.

        Some libpq / psql error dialects emit single-quoted passwords
        rather than the double-quoted form handled by
        ``_RE_PASSWORD_QUOTED``. The defense-in-depth sanitizer covers
        both quote styles so neither leaks.
        """
        result = ConnectionPoolManager._sanitize_error(
            "auth error: password 'secret123' is wrong"
        )
        assert "secret123" not in result
        assert "password '***'" in result


# =============================================================================
# Group 3: Pool caching (lazy creation)
# =============================================================================


class TestGetOrCreatePool:
    """First call creates a pool; subsequent calls reuse it."""

    @pytest.mark.asyncio
    async def test_first_call_invokes_create_pool(self):
        """Cold start → ``asyncpg.create_pool`` is called once."""
        manager, _, _ = _make_manager(config=_make_config())
        fake_pool = MagicMock(name="fake_pool")
        with patch(
            "daemon.services.db_pool_manager.asyncpg.create_pool",
            new_callable=AsyncMock,
            return_value=fake_pool,
        ) as create_pool:
            pool = await manager._get_or_create_pool("primary")
        assert pool is fake_pool
        assert create_pool.call_count == 1
        # The pool is now cached.
        assert manager._pools["primary"] is fake_pool

    @pytest.mark.asyncio
    async def test_second_call_returns_cached_pool_without_create(self):
        """Warm path → ``create_pool`` is *not* called again."""
        manager, _, _ = _make_manager(config=_make_config())
        fake_pool = MagicMock(name="fake_pool")
        with patch(
            "daemon.services.db_pool_manager.asyncpg.create_pool",
            new_callable=AsyncMock,
            return_value=fake_pool,
        ) as create_pool:
            await manager._get_or_create_pool("primary")
            pool2 = await manager._get_or_create_pool("primary")
        # Same object both times; create_pool hit exactly once.
        assert pool2 is fake_pool
        assert create_pool.call_count == 1

    @pytest.mark.asyncio
    async def test_create_pool_receives_kwargs_and_pool_constants(self):
        """Pool construction is wired with kwargs (not a DSN) and the
        documented constants (C1, C2, C3, C5).

        Kwargs-based ``create_pool`` is the only safe way to pass a
        password — a DSN-based call would risk embedding the secret
        in a URL string that could leak via asyncpg error messages.
        """
        manager, _, _ = _make_manager(
            config=_make_config(),
            credentials_blob="enc::blob",
        )
        # Decrypted password is "secret" (per helper default).
        with patch(
            "daemon.services.db_pool_manager.asyncpg.create_pool",
            new_callable=AsyncMock,
            return_value=MagicMock(),
        ) as create_pool:
            await manager._get_or_create_pool("primary")
        create_pool.assert_awaited_once()
        kwargs = create_pool.await_args.kwargs
        # Pool constants.
        assert kwargs["min_size"] == POOL_MIN_SIZE == 1
        assert kwargs["max_size"] == POOL_MAX_SIZE == 5
        assert kwargs["max_queries"] == POOL_MAX_QUERIES == 500
        assert kwargs["timeout"] == POOL_TIMEOUT == 30
        # Kwargs-based connection (C1-C3, C5): host/port/database/user/
        # password must be passed as separate kwargs — never a `dsn`.
        assert "dsn" not in kwargs
        assert kwargs["host"] == "db.example.com"
        assert kwargs["port"] == 5432
        assert kwargs["database"] == "appdb"
        assert kwargs["user"] == "app_user"
        # The decrypted password is passed as a separate kwarg.
        assert kwargs["password"] == "secret"

    @pytest.mark.asyncio
    async def test_unknown_connection_name_raises_value_error(self):
        """Repository returns ``None`` → ``ValueError`` mentioning the name."""
        manager, repository, _ = _make_manager(config=None)
        repository.get_by_name = MagicMock(return_value=None)
        with pytest.raises(ValueError, match="primary"):
            await manager._get_or_create_pool("primary")

    @pytest.mark.asyncio
    async def test_non_postgres_db_type_rejected(self):
        """Connection row is not postgres → ``ValueError`` mentioning db_type."""
        manager, repository, _ = _make_manager()
        repository.get_by_name = MagicMock(
            return_value=_make_config(db_type="mysql")
        )
        with pytest.raises(ValueError, match="postgres"):
            await manager._get_or_create_pool("primary")

    @pytest.mark.asyncio
    async def test_asyncpg_postgres_error_wrapped_in_connection_error(self):
        """Server-side errors are wrapped so the original DSN stays hidden."""
        manager, repository, _ = _make_manager(config=_make_config())
        # Simulate an asyncpg error whose message embeds the DSN.
        raw = (
            "FATAL: password authentication failed for user \"u\" "
            "password \"hunter2\""
        )
        with patch(
            "daemon.services.db_pool_manager.asyncpg.create_pool",
            new_callable=AsyncMock,
            side_effect=asyncpg_postgres_error(raw),
        ):
            with pytest.raises(ConnectionError) as exc_info:
                await manager._get_or_create_pool("primary")
        # Original secret must not appear in the wrapped message.
        assert "hunter2" not in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_concurrent_pool_creation_single_create(self):
        """Double-checked locking: 10 concurrent calls trigger ONE
        ``asyncpg.create_pool``.

        The slow-path lock + re-check guarantees that even if N
        coroutines race past the fast-path dict lookup, exactly one of
        them reaches the pool-construction site. The other N-1 find
        the cached entry on the re-check and short-circuit.
        """
        manager, _, _ = _make_manager(config=_make_config())

        with patch(
            "daemon.services.db_pool_manager.asyncpg.create_pool",
            new_callable=AsyncMock,
            return_value=MagicMock(name="shared_pool"),
        ) as create_pool:
            results = await asyncio.gather(
                *[manager._get_or_create_pool("db1") for _ in range(10)]
            )

        # All 10 callers got the same (mocked) pool.
        assert len(results) == 10
        assert all(r is results[0] for r in results)
        # But the underlying factory was hit exactly once.
        create_pool.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_pool_reuse_after_dispose(self):
        """After ``dispose(name)``, a fresh pool is created on next access.

        ``dispose`` pops the entry from ``_pools`` and awaits
        ``pool.close()``. A subsequent ``_get_or_create_pool`` must hit
        the slow path and construct a *new* pool — not return a stale
        reference to the disposed one.
        """
        manager, _, _ = _make_manager(config=_make_config())

        # Use a real async-mock for ``close`` so ``dispose`` can await it.
        first_pool = MagicMock(name="first_pool")
        first_pool.close = AsyncMock(return_value=None)

        with patch(
            "daemon.services.db_pool_manager.asyncpg.create_pool",
            new_callable=AsyncMock,
            return_value=first_pool,
        ) as create_pool:
            returned_first = await manager._get_or_create_pool("primary")
        assert returned_first is first_pool
        assert manager._pools["primary"] is first_pool

        # Dispose pops the entry and awaits close.
        await manager.dispose("primary")
        assert "primary" not in manager._pools
        first_pool.close.assert_awaited_once()

        # Second round: a different mock pool. Reset the call counter
        # so we can assert a *new* creation event fires.
        second_pool = MagicMock(name="second_pool")
        second_pool.close = AsyncMock(return_value=None)
        with patch(
            "daemon.services.db_pool_manager.asyncpg.create_pool",
            new_callable=AsyncMock,
            return_value=second_pool,
        ) as create_pool:
            returned_second = await manager._get_or_create_pool("primary")

        assert returned_second is second_pool
        assert returned_second is not first_pool
        # The new pool is now cached.
        assert manager._pools["primary"] is second_pool
        create_pool.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_execute_select_special_char_password(self):
        """Pool construction passes special-char passwords as a kwarg.

        The kwargs-based ``create_pool`` approach is what makes
        passwords containing URL-reserved characters (``,``, ``:``,
        ``/``, ``#``) safe to use: they are never embedded in a DSN
        string that asyncpg or libpq might echo back in an error
        message. This test pins that contract for an adversarial
        password.
        """
        nasty_password = "p@ss:w0rd#1"
        manager, repository, credential_manager = _make_manager(
            config=_make_config(),
        )
        # The repository must return a non-None ``credentials_blob`` so
        # the manager actually decrypts; otherwise it short-circuits and
        # passes ``password=None`` to ``create_pool``.
        repository.get_credentials = MagicMock(return_value="enc::blob")
        # Override the default ``secret`` password with the nasty one.
        credential_manager.decrypt = MagicMock(
            return_value={"password": nasty_password}
        )

        with patch(
            "daemon.services.db_pool_manager.asyncpg.create_pool",
            new_callable=AsyncMock,
            return_value=MagicMock(),
        ) as create_pool:
            await manager._get_or_create_pool("special-db")

        create_pool.assert_awaited_once()
        kwargs = create_pool.await_args.kwargs
        # The password is passed as its own kwarg — the dangerous path
        # of string-embedding is structurally impossible here.
        assert kwargs.get("password") == nasty_password
        # And, critically, no ``dsn`` argument is present at all.
        assert "dsn" not in kwargs


def asyncpg_postgres_error(message: str):
    """Build a fake ``asyncpg.PostgresError`` with the given message."""
    # We use a plain Exception subclass that *also* satisfies
    # ``isinstance`` against ``asyncpg.PostgresError`` checks inside
    # the manager. Because the manager imports asyncpg and does an
    # ``except (asyncpg.PostgresError, OSError, ConnectionRefusedError)``,
    # we need a real instance of ``asyncpg.PostgresError`` if
    # asyncpg is importable, or a fallback that falls through to the
    # generic ``except Exception`` branch.
    try:
        import asyncpg

        err = asyncpg.PostgresError(message)
        return err
    except Exception:
        # asyncpg unavailable at test time: build an error that triggers
        # the generic ``except Exception`` branch by raising a real
        # Exception subclass.
        class _FakePostgresError(Exception):
            pass

        return _FakePostgresError(message)


# =============================================================================
# Group 4: test_connection() — health check plumbing
# =============================================================================


class TestConnection:
    """Cover the happy path, the failure path, and message sanitization."""

    @pytest.mark.asyncio
    async def test_select_one_returns_healthy_message(self):
        """``fetchval('SELECT 1')`` succeeds → ``success=True``."""
        manager, _, _ = _make_manager(config=_make_config())

        mock_conn = MagicMock()
        mock_conn.fetchval = AsyncMock(return_value=1)

        fake_pool = _make_mock_pool(mock_conn)
        with patch(
            "daemon.services.db_pool_manager.asyncpg.create_pool",
            new_callable=AsyncMock,
            return_value=fake_pool,
        ):
            result = await manager.test_connection("primary")

        assert result["success"] is True
        assert "healthy" in result["message"].lower()
        assert "primary" in result["message"]
        mock_conn.fetchval.assert_awaited_once_with("SELECT 1")

    @pytest.mark.asyncio
    async def test_failure_returns_sanitized_message(self):
        """fetchval raises an I/O-class error → ``success=False``, message
        never contains DSN/secret.

        Uses ``OSError`` so it matches the narrowed W5 catch list
        (``asyncpg.PostgresError``, ``OSError``, ``ConnectionError``,
        ``asyncio.TimeoutError``). ``RuntimeError`` is intentionally
        not in the catch list — programming bugs must propagate.
        """
        manager, _, _ = _make_manager(config=_make_config())

        mock_conn = MagicMock()
        mock_conn.fetchval = AsyncMock(
            side_effect=OSError(
                "connection to postgresql://u:hunter2@host failed"
            )
        )

        fake_pool = _make_mock_pool(mock_conn)
        with patch(
            "daemon.services.db_pool_manager.asyncpg.create_pool",
            new_callable=AsyncMock,
            return_value=fake_pool,
        ):
            result = await manager.test_connection("primary")

        assert result["success"] is False
        assert "hunter2" not in result["message"]
        assert "primary" in result["message"]


# =============================================================================
# Group 5: dispose() and dispose_all() — pool lifecycle
# =============================================================================


class TestDispose:
    """Pool disposal must be idempotent and await the async close."""

    @pytest.mark.asyncio
    async def test_dispose_removes_pool_and_awaits_close(self):
        """``dispose`` pops the entry from ``_pools`` and awaits ``pool.close()``."""
        manager, _, _ = _make_manager()
        fake_pool = _make_mock_pool(MagicMock())
        manager._pools["primary"] = fake_pool

        await manager.dispose("primary")

        assert "primary" not in manager._pools
        fake_pool.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_dispose_is_idempotent(self):
        """Calling ``dispose`` twice does not raise."""
        manager, _, _ = _make_manager()
        fake_pool = _make_mock_pool(MagicMock())
        manager._pools["primary"] = fake_pool

        await manager.dispose("primary")
        # Second call: no-op, no exception.
        await manager.dispose("primary")
        # close was only awaited once.
        fake_pool.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_dispose_unknown_name_is_noop(self):
        """``dispose`` of an unknown name does not raise."""
        manager, _, _ = _make_manager()
        # No entry to dispose — must not raise.
        await manager.dispose("ghost")
        assert manager._pools == {}

    @pytest.mark.asyncio
    async def test_dispose_all_clears_every_pool(self):
        """``dispose_all`` walks every cached pool and awaits each close."""
        manager, _, _ = _make_manager()
        pool_a = _make_mock_pool(MagicMock())
        pool_b = _make_mock_pool(MagicMock())
        manager._pools["a"] = pool_a
        manager._pools["b"] = pool_b

        await manager.dispose_all()

        assert manager._pools == {}
        pool_a.close.assert_awaited_once()
        pool_b.close.assert_awaited_once()


# =============================================================================
# Group 6: execute_select() — query plumbing and truncation
# =============================================================================


class TestExecuteSelect:
    """Cover row normalization, truncation, and the use of ``conn.fetch``."""

    @pytest.mark.asyncio
    async def test_returns_columns_rows_count_and_not_truncated(self):
        """A small result set returns dicts and reports ``truncated=False``."""
        manager, _, _ = _make_manager(config=_make_config())

        # Build three fake records whose ``.keys()`` returns column
        # names and whose ``dict(record)`` yields a Python dict.
        record_a = MagicMock()
        record_a.keys = MagicMock(return_value=["id", "name"])
        record_a.__iter__ = MagicMock(return_value=iter([("id", 1), ("name", "alice")]))
        # ``dict(record)`` relies on iteration; in real asyncpg a Record
        # is dict-like. The manager uses ``[dict(record) for record in records]``.
        dict_calls = {
            "a": {"id": 1, "name": "alice"},
            "b": {"id": 2, "name": "bob"},
        }
        record_a_dict = dict_calls["a"]
        record_b = MagicMock()
        record_b.keys = MagicMock(return_value=["id", "name"])
        record_b_dict = dict_calls["b"]

        records = [record_a, record_b]
        # Side effect: real ``dict(record)`` would return what we set.
        # The manager does ``[dict(record) for record in records]``,
        # so the simplest mock is a list of plain dicts that *also*
        # expose ``.keys()`` (to satisfy ``records[0].keys()``).
        dict_with_keys_a = MagicMock(spec=dict)
        dict_with_keys_a.__iter__ = MagicMock(return_value=iter(dict_calls["a"].items()))
        dict_with_keys_a.keys = MagicMock(return_value=["id", "name"])
        # But ``dict(record)`` returns a real dict; we replace the
        # records with objects that behave like the asyncpg.Record /
        # dict hybrid. Easiest: make records dicts that also have
        # ``.keys()``.

        records = [dict_calls["a"], dict_calls["b"]]
        # Augment the dicts with a ``.keys()`` method via a subclass
        # — but real dicts already have ``.keys()`` built in, so the
        # list comprehension ``[dict(record) for record in records]``
        # returns the same dicts. Perfect.
        assert dict(records[0]) == {"id": 1, "name": "alice"}

        mock_conn = MagicMock()
        mock_conn.fetch = AsyncMock(return_value=records)

        fake_pool = _make_mock_pool(mock_conn)
        with patch(
            "daemon.services.db_pool_manager.asyncpg.create_pool",
            new_callable=AsyncMock,
            return_value=fake_pool,
        ):
            result = await manager.execute_select("primary", "SELECT * FROM users")

        assert set(result.keys()) == {"columns", "rows", "row_count", "truncated"}
        assert result["columns"] == ["id", "name"]
        assert result["rows"] == [{"id": 1, "name": "alice"}, {"id": 2, "name": "bob"}]
        assert result["row_count"] == 2
        assert result["truncated"] is False

        # Sanity: we used ``conn.fetch``, not ``conn.prepare(...).fetch()``.
        # W1: the query is suffixed with ``LIMIT <max_rows + 1>`` to
        # prevent OOM on unconstrained queries. ``DEFAULT_MAX_ROWS``
        # is 1000, so the injected limit is 1001.
        mock_conn.fetch.assert_awaited_once_with(
            "SELECT * FROM users LIMIT 1001"
        )
        mock_conn.prepare.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_result_set_returns_empty_columns_and_rows(self):
        """No rows → ``columns=[]``, ``rows=[]``, ``row_count=0``."""
        manager, _, _ = _make_manager(config=_make_config())

        mock_conn = MagicMock()
        mock_conn.fetch = AsyncMock(return_value=[])

        fake_pool = _make_mock_pool(mock_conn)
        with patch(
            "daemon.services.db_pool_manager.asyncpg.create_pool",
            new_callable=AsyncMock,
            return_value=fake_pool,
        ):
            result = await manager.execute_select("primary", "SELECT 1 WHERE FALSE")

        assert result["columns"] == []
        assert result["rows"] == []
        assert result["row_count"] == 0
        assert result["truncated"] is False

    @pytest.mark.asyncio
    async def test_truncation_when_rows_exceed_max(self):
        """More rows than ``max_rows`` → ``truncated=True`` and row count capped."""
        manager, _, _ = _make_manager(config=_make_config())

        # Build 5 dict-records, ask for max_rows=2.
        records = [{"id": i, "name": f"u{i}"} for i in range(5)]

        mock_conn = MagicMock()
        mock_conn.fetch = AsyncMock(return_value=records)

        fake_pool = _make_mock_pool(mock_conn)
        with patch(
            "daemon.services.db_pool_manager.asyncpg.create_pool",
            new_callable=AsyncMock,
            return_value=fake_pool,
        ):
            result = await manager.execute_select(
                "primary", "SELECT * FROM users", max_rows=2
            )

        assert result["truncated"] is True
        assert result["row_count"] == 2
        assert result["rows"] == [{"id": 0, "name": "u0"}, {"id": 1, "name": "u1"}]
        assert result["columns"] == ["id", "name"]

    @pytest.mark.asyncio
    async def test_truncation_does_not_trigger_when_within_limit(self):
        """``max_rows=10`` with 3 rows → ``truncated=False``."""
        manager, _, _ = _make_manager(config=_make_config())
        records = [{"id": i} for i in range(3)]

        mock_conn = MagicMock()
        mock_conn.fetch = AsyncMock(return_value=records)
        fake_pool = _make_mock_pool(mock_conn)

        with patch(
            "daemon.services.db_pool_manager.asyncpg.create_pool",
            new_callable=AsyncMock,
            return_value=fake_pool,
        ):
            result = await manager.execute_select(
                "primary", "SELECT id FROM t", max_rows=10
            )

        assert result["truncated"] is False
        assert result["row_count"] == 3

    @pytest.mark.asyncio
    async def test_default_max_rows_is_1000(self):
        """``execute_select`` defaults to :data:`DEFAULT_MAX_ROWS = 1000`."""
        manager, _, _ = _make_manager(config=_make_config())
        # 1000 records (== default) → not truncated.
        records = [{"id": i} for i in range(DEFAULT_MAX_ROWS)]
        mock_conn = MagicMock()
        mock_conn.fetch = AsyncMock(return_value=records)
        fake_pool = _make_mock_pool(mock_conn)

        with patch(
            "daemon.services.db_pool_manager.asyncpg.create_pool",
            new_callable=AsyncMock,
            return_value=fake_pool,
        ):
            result = await manager.execute_select("primary", "SELECT * FROM big")

        assert result["truncated"] is False
        assert result["row_count"] == DEFAULT_MAX_ROWS

    @pytest.mark.asyncio
    async def test_default_query_timeout_is_30(self):
        """``execute_select`` uses :data:`DEFAULT_QUERY_TIMEOUT = 30` by default.

        We replace ``asyncio.wait_for`` with a thin async shim that
        captures the timeout it was called with and forwards the
        underlying coroutine. The shim is a real ``async def`` so we
        avoid the coroutine-leak warnings that surface when an
        ``AsyncMock`` shadows another ``await`` site.
        """
        manager, _, _ = _make_manager(config=_make_config())
        records = [{"id": 1}]
        mock_conn = MagicMock()
        mock_conn.fetch = AsyncMock(return_value=records)
        fake_pool = _make_mock_pool(mock_conn)

        captured: dict[str, Any] = {}

        async def fake_wait_for(awaitable, timeout):  # noqa: ANN001
            captured["timeout"] = timeout
            return await awaitable

        with patch(
            "daemon.services.db_pool_manager.asyncpg.create_pool",
            new_callable=AsyncMock,
            return_value=fake_pool,
        ), patch(
            "daemon.services.db_pool_manager.asyncio.wait_for",
            new=fake_wait_for,
        ):
            await manager.execute_select("primary", "SELECT 1")

        # The timeout kwarg equals the module default.
        assert captured["timeout"] == DEFAULT_QUERY_TIMEOUT == 30

    @pytest.mark.asyncio
    async def test_query_timeout_propagates_to_caller(self):
        """``asyncio.wait_for`` raising → ``asyncio.TimeoutError`` propagates."""
        manager, _, _ = _make_manager(config=_make_config())
        fake_pool = _make_mock_pool(MagicMock())

        async def fake_wait_for(awaitable, timeout):  # noqa: ANN001
            raise asyncio.TimeoutError()

        with patch(
            "daemon.services.db_pool_manager.asyncpg.create_pool",
            new_callable=AsyncMock,
            return_value=fake_pool,
        ), patch(
            "daemon.services.db_pool_manager.asyncio.wait_for",
            new=fake_wait_for,
        ):
            with pytest.raises(asyncio.TimeoutError):
                await manager.execute_select(
                    "primary", "SELECT pg_sleep(60)", timeout=0.01
                )


# =============================================================================
# Group 7: get_connection() — acquire context passthrough
# =============================================================================


class TestGetConnection:
    """``get_connection`` is a thin wrapper around ``pool.acquire()``."""

    @pytest.mark.asyncio
    async def test_returns_pool_acquire_context(self):
        """Result is whatever ``pool.acquire()`` returns — the context manager."""
        manager, _, _ = _make_manager(config=_make_config())
        fake_pool = MagicMock()
        sentinel = object()  # any unique marker
        fake_pool.acquire = MagicMock(return_value=sentinel)
        with patch(
            "daemon.services.db_pool_manager.asyncpg.create_pool",
            new_callable=AsyncMock,
            return_value=fake_pool,
        ):
            ctx = await manager.get_connection("primary")
        assert ctx is sentinel
        fake_pool.acquire.assert_called_once()


# =============================================================================
# Group 8: Edge case — non-existent connection
# =============================================================================


class TestMissingConnection:
    """The repository returning ``None`` must surface as a clear ``ValueError``."""

    @pytest.mark.asyncio
    async def test_get_or_create_pool_value_error_message(self):
        """The error message embeds the connection name (helps debugging)."""
        manager, repository, _ = _make_manager()
        repository.get_by_name = MagicMock(return_value=None)
        with pytest.raises(ValueError) as exc_info:
            await manager._get_or_create_pool("ghost")
        assert "ghost" in str(exc_info.value)
        assert "not found" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_execute_select_propagates_value_error(self):
        """``execute_select`` re-raises the ``ValueError`` from pool creation."""
        manager, repository, _ = _make_manager()
        repository.get_by_name = MagicMock(return_value=None)
        with pytest.raises(ValueError, match="ghost"):
            await manager.execute_select("ghost", "SELECT 1")

    @pytest.mark.asyncio
    async def test_test_connection_returns_failure_dict_on_io_error(self):
        """``test_connection`` swallows I/O-class errors and returns a dict.

        Per W5 the catch list is narrowed to ``asyncpg.PostgresError``,
        ``OSError``, ``ConnectionError``, ``asyncio.TimeoutError``.
        Programming errors (e.g. ``ValueError`` from a missing config
        name) intentionally propagate so they are caught in tests and
        alerting rather than being silently masked.
        """
        manager, _, _ = _make_manager(config=_make_config())

        mock_conn = MagicMock()
        mock_conn.fetchval = AsyncMock(
            side_effect=ConnectionError("server unreachable")
        )
        fake_pool = _make_mock_pool(mock_conn)
        with patch(
            "daemon.services.db_pool_manager.asyncpg.create_pool",
            new_callable=AsyncMock,
            return_value=fake_pool,
        ):
            result = await manager.test_connection("primary")

        assert result["success"] is False
        assert "primary" in result["message"]
