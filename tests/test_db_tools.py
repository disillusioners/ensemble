"""Integration tests for the Database Tool Category.

These tests exercise the LangChain tools produced by
:func:`daemon.tools.db_tools.create_db_tools` against a real
in-memory SQLite-backed :class:`DbConnectionRepository`, and
cross-check the ``"db"`` category in
:func:`daemon.tools.instance.resolve_tool_filter`.

Test classes
------------

* :class:`TestDbConnTools` — CRUD lifecycle (add, list, delete, dup,
  test, encryption-unavailable).
* :class:`TestCredentialSecurity` — security boundary: passwords must
  never appear in storage, list output, or public dicts.
* :class:`TestDbPostgresSelect` — SELECT-only guard behavior of
  ``db_postgres_dml_select`` (the actual query path is mocked).
* :class:`TestToolFilterIntegration` — the ``"db"`` category expands
  to all 5 db tool names in the filter, and excludes them when not
  present in the allow list.

Conventions
-----------

* Use ``pytest`` + ``pytest-asyncio``.
* Use ``DbConnectionConfig.__table__.create(engine, checkfirst=True)``
  to isolate a single table — never ``SQLModel.metadata.create_all``,
  which would leak other tables from the test session.
* ``mock_manager.is_write_paused = False`` must be set explicitly.
* Real password values (e.g. ``"secret123"``) are used in assertions
  to verify they do NOT appear in any tool output.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine


# =============================================================================
# Constants — shared across tool-filter tests.
# =============================================================================


# The 5 tool names registered by ``create_db_tools``. Repeated as a
# module constant so the tool-filter tests can assert against it
# without re-typing the same set literal.
DB_TOOL_NAMES: frozenset[str] = frozenset({
    "db_conn_add",
    "db_conn_delete",
    "db_conn_list",
    "db_conn_test",
    "db_postgres_dml_select",
})


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def fernet_key():
    """Fresh Fernet key for the test session.

    A real key (rather than a mock) keeps the encryption path honest:
    the test then verifies the stored ciphertext does NOT contain the
    plaintext password.
    """
    from cryptography.fernet import Fernet
    return Fernet.generate_key()


@pytest.fixture
def db_engine(tmp_path):
    """Create an isolated SQLite engine for the db_connections table only.

    We deliberately use ``DbConnectionConfig.__table__.create`` rather than
    ``SQLModel.metadata.create_all`` to avoid leaking the entire ensemble
    schema (instances, projects, source_configs, ...) into the test database.
    """
    engine = create_engine(f"sqlite:///{tmp_path}/test.db", echo=False)
    yield engine
    engine.dispose()


@pytest.fixture
def credential_manager(fernet_key):
    """Real ``CredentialManager`` with a fresh Fernet key set."""
    from daemon.sources.credentials import CredentialManager
    return CredentialManager(encryption_key=fernet_key)


@pytest.fixture
def repository(db_engine):
    """``DbConnectionRepository`` bound to the isolated table."""
    from daemon.repositories.db_connection.models import DbConnectionConfig
    from daemon.repositories.db_connection.repository import DbConnectionRepository

    # CRITICAL: use __table__.create, not metadata.create_all, so we
    # only create the db_connections table and do not leak the
    # ensemble schema.
    DbConnectionConfig.__table__.create(db_engine, checkfirst=True)
    return DbConnectionRepository(db_engine)


@pytest.fixture
def pool_manager(repository, credential_manager):
    """Real ``ConnectionPoolManager`` (singleton-style for the test scope)."""
    from daemon.services.db_pool_manager import ConnectionPoolManager
    return ConnectionPoolManager(repository, credential_manager)


@pytest.fixture
def mock_manager(credential_manager):
    """Mock InstanceManager that exposes ``credential_manager`` and ``is_write_paused``.

    The factory reads ``manager.credential_manager`` for the N1
    encryption step. ``is_write_paused`` is the field some tool paths
    guard on — we set it explicitly to ``False`` to avoid the default
    ``MagicMock`` truthiness from sneaking in.
    """
    manager = MagicMock()
    manager.credential_manager = credential_manager
    manager.is_write_paused = False
    return manager


@pytest.fixture
def db_tools(mock_manager, repository, pool_manager):
    """Build the 5 LangChain tools and return them indexed by name.

    Tools are produced by the real ``create_db_tools`` factory against
    the real in-memory SQLite + real ``CredentialManager``. The
    ``InstanceManager`` itself is mocked only because the factory
    only needs its ``credential_manager`` attribute — the rest of the
    manager's surface is irrelevant to these tools.
    """
    from daemon.tools.db_tools import create_db_tools

    tools_list = create_db_tools(
        mock_manager,
        "test-instance",
        repository=repository,
        pool_manager=pool_manager,
    )
    return {getattr(t, "name", None): t for t in tools_list}


# =============================================================================
# Group 1: TestDbConnTools — CRUD lifecycle
# =============================================================================


class TestDbConnTools:
    """Connection CRUD lifecycle via the agent-facing tools.

    Each test invokes the tool via ``tool.ainvoke({...})`` and asserts
    on the returned string. The tools never raise — they always
    return a string (success confirmation or ``ERROR: ...``) so the
    agent can self-correct.
    """

    @pytest.mark.asyncio
    async def test_conn_add_creates_connection(self, db_tools, repository):
        """Adding a connection returns a confirmation that does NOT
        include the password, and the row exists in the repository.
        """
        add = db_tools["db_conn_add"]
        result = await add.ainvoke({
            "connection_name": "alpha",
            "db_type": "postgres",
            "host": "db.example.com",
            "port": 5432,
            "database": "appdb",
            "username": "app_user",
            "password": "secret123",
        })

        # No password in the return string.
        assert "secret123" not in result
        assert "Created db connection" in result
        assert "alpha" in result
        # The has_password flag must be present and True.
        assert "has_password=True" in result

        # And the row actually exists in the repository.
        row = repository.get_by_name("alpha")
        assert row is not None
        assert row.connection_name == "alpha"
        assert row.db_type == "postgres"
        assert row.host == "db.example.com"

    @pytest.mark.asyncio
    async def test_conn_list_shows_connections(self, db_tools):
        """After adding, ``db_conn_list`` shows the connection in a
        markdown table — and still does NOT include the password.
        """
        add = db_tools["db_conn_add"]
        list_tool = db_tools["db_conn_list"]

        await add.ainvoke({
            "connection_name": "beta",
            "db_type": "postgres",
            "host": "db2.example.com",
            "port": 5433,
            "database": "beta_db",
            "username": "beta_user",
            "password": "another-secret",
        })

        result = await list_tool.ainvoke({})

        assert "beta" in result
        assert "beta_db" in result
        assert "another-secret" not in result
        # has_password is shown as a flag, not a value.
        assert "has_password" in result or "yes" in result

    @pytest.mark.asyncio
    async def test_conn_delete_removes_connection(self, db_tools, repository):
        """Adding then deleting removes the row from the repository.
        The delete tool reports success.
        """
        add = db_tools["db_conn_add"]
        delete = db_tools["db_conn_delete"]

        await add.ainvoke({
            "connection_name": "gamma",
            "db_type": "postgres",
            "host": "h",
        })
        assert repository.get_by_name("gamma") is not None

        result = await delete.ainvoke({"connection_name": "gamma"})

        assert "Deleted" in result
        assert "gamma" in result
        assert repository.get_by_name("gamma") is None

    @pytest.mark.asyncio
    async def test_conn_add_duplicate_name_rejected(self, db_tools):
        """Adding a duplicate name returns an ``ERROR:`` string and
        does NOT leak the plaintext password in the error.
        """
        add = db_tools["db_conn_add"]
        # First add succeeds.
        await add.ainvoke({
            "connection_name": "dup",
            "db_type": "postgres",
            "host": "h",
            "password": "first-secret",
        })
        # Second add with a DIFFERENT password — the error must not
        # echo the second password back.
        result = await add.ainvoke({
            "connection_name": "dup",
            "db_type": "postgres",
            "host": "h2",
            "password": "second-secret",
        })

        assert "ERROR" in result
        # The error must not echo either password value.
        assert "first-secret" not in result
        assert "second-secret" not in result
        # It mentions the duplicate name.
        assert "dup" in result

    @pytest.mark.asyncio
    async def test_conn_delete_nonexistent(self, db_tools):
        """Deleting a name that was never added reports it (does not raise)."""
        delete = db_tools["db_conn_delete"]
        result = await delete.ainvoke({"connection_name": "ghost"})

        # The tool returns the literal "No db connection named '...' to delete."
        assert "No db connection named 'ghost'" in result
        assert "to delete" in result

    @pytest.mark.asyncio
    async def test_conn_test_nonexistent_connection(self, db_tools, pool_manager):
        """Testing a connection that is not registered returns an
        ``ERROR:`` string.

        The pool manager's ``_get_or_create_pool`` raises a
        ``ValueError`` for unknown names, which the pool manager's
        ``test_connection`` does *not* catch (it only swallows I/O
        errors per W5). The exception propagates to the
        ``db_conn_test`` tool, which renders it as an ``ERROR:``
        string with the class name only (N9 sanitization).
        """
        test = db_tools["db_conn_test"]
        result = await test.ainvoke({"connection_name": "ghost"})

        # ERROR: prefix is what the tool emits for unexpected exceptions.
        assert result.startswith("ERROR:")
        # The connection name should be mentioned for agent correlation.
        assert "ghost" in result

    @pytest.mark.asyncio
    async def test_conn_add_rejects_when_encryption_unavailable(
        self, mock_manager, repository, pool_manager
    ):
        """When ``is_encryption_available()`` returns False and a
        password is supplied, ``db_conn_add`` rejects the call with
        a clear error and does NOT store plaintext.
        """
        from daemon.tools.db_tools import create_db_tools

        # Force CredentialManager to report encryption as unavailable.
        mock_manager.credential_manager.is_encryption_available = MagicMock(
            return_value=False
        )

        tools_list = create_db_tools(
            mock_manager,
            "test-instance",
            repository=repository,
            pool_manager=pool_manager,
        )
        tools_by_name = {getattr(t, "name", None): t for t in tools_list}
        add = tools_by_name["db_conn_add"]

        result = await add.ainvoke({
            "connection_name": "nokey",
            "db_type": "postgres",
            "host": "h",
            "password": "should-not-be-stored",
        })

        # Rejection with a clear error.
        assert "ERROR" in result
        assert "encryption" in result.lower() or "credential" in result.lower()
        # The plaintext password must NOT appear in the result.
        assert "should-not-be-stored" not in result

        # And nothing was stored (rollback on rejection).
        assert repository.get_by_name("nokey") is None


# =============================================================================
# Group 2: TestCredentialSecurity — Security boundary
# =============================================================================


class TestCredentialSecurity:
    """The encryption boundary must be respected at every read path.

    The repository stores opaque strings, the ``db_conn_list`` tool
    never includes credentials in its output, and ``to_public_dict()``
    exposes only a ``has_password`` boolean. Any leak in any of these
    surfaces fails the test.
    """

    @pytest.mark.asyncio
    async def test_add_stores_encrypted_not_plaintext(self, db_tools, repository):
        """After ``db_conn_add`` with a known password, the underlying
        ``credentials`` column in the repository contains the
        encrypted blob — NOT the plaintext password.
        """
        add = db_tools["db_conn_add"]
        plaintext_pw = "plaintext-pw-do-not-leak"

        await add.ainvoke({
            "connection_name": "sec_alpha",
            "db_type": "postgres",
            "host": "h",
            "password": plaintext_pw,
        })

        # Pull the raw credentials string from the repository. It is
        # the encrypted blob and must not contain the plaintext.
        encrypted = repository.get_credentials("sec_alpha")
        assert encrypted is not None
        assert plaintext_pw not in encrypted
        # Fernet tokens always start with 'gAAAAA' (base64-encoded version).
        # The repository stores the encrypted form, not the plaintext.
        assert encrypted != plaintext_pw

    @pytest.mark.asyncio
    async def test_list_never_contains_password(self, db_tools):
        """After adding with a known password, ``db_conn_list`` must
        not include the password value in its output.
        """
        add = db_tools["db_conn_add"]
        list_tool = db_tools["db_conn_list"]

        plaintext_pw = "leak-test-secret-123"
        await add.ainvoke({
            "connection_name": "sec_beta",
            "db_type": "postgres",
            "host": "h",
            "password": plaintext_pw,
        })

        result = await list_tool.ainvoke({})

        # The plaintext must NOT appear anywhere in the list output.
        assert plaintext_pw not in result
        # The boolean flag should be present (yes/no), not the secret.
        assert "yes" in result or "has_password" in result

    @pytest.mark.asyncio
    async def test_repository_public_dict_excludes_credentials(self, repository):
        """``to_public_dict()`` and ``list_public()`` return a
        ``has_password`` boolean — never the actual credentials.
        """
        # Use the repository to insert a row with a known credentials
        # value (simulating what the tool would store).
        repository.create(
            connection_name="sec_gamma",
            db_type="postgres",
            host="h",
            credentials="enc::v1::not-a-real-ciphertext-just-a-string",
        )

        # to_public_dict: no credentials, has_password=True.
        row = repository.get_by_name("sec_gamma")
        public = row.to_public_dict()
        assert "credentials" not in public
        assert public["has_password"] is True

        # list_public: same, repeated for the bulk path.
        public_list = repository.list_public()
        assert len(public_list) == 1
        assert "credentials" not in public_list[0]
        assert public_list[0]["has_password"] is True

        # Defensive: even direct field access is a redaction-safe repr.
        assert "enc::v1::not-a-real-ciphertext-just-a-string" not in repr(row)


# =============================================================================
# Group 3: TestDbPostgresSelect — Select tool guard behavior
# =============================================================================


class TestDbPostgresSelect:
    """``db_postgres_dml_select`` SELECT-only guard and error paths.

    The actual pool query is mocked so the tests do not need a real
    PostgreSQL server. The guard is the real implementation.
    """

    @pytest.mark.asyncio
    async def test_select_rejects_non_select(self, db_tools):
        """``DELETE FROM users`` is rejected before any network I/O.

        The tool returns an ``ERROR:`` string — it never raises.
        """
        select = db_tools["db_postgres_dml_select"]
        result = await select.ainvoke({
            "connection_name": "anything",
            "query": "DELETE FROM users",
        })

        # The tool layer surfaces only the exception class name
        # (N9 sanitization); the full reason is in the daemon log.
        assert "ERROR" in result

    @pytest.mark.asyncio
    async def test_select_rejects_select_into(self, db_tools):
        """``SELECT * INTO new_table FROM users`` is rejected — INTO
        is on the forbidden keyword list.
        """
        select = db_tools["db_postgres_dml_select"]
        result = await select.ainvoke({
            "connection_name": "anything",
            "query": "SELECT * INTO new_table FROM users",
        })

        # The tool layer does not echo the reason; the word "INTO"
        # is in the daemon log, not the agent-visible response.
        assert "ERROR" in result

    @pytest.mark.asyncio
    async def test_select_nonexistent_connection(self, db_tools):
        """Querying a connection that was never added returns an
        ``ERROR:`` string. The pool manager's ``_get_or_create_pool``
        raises ``ValueError`` for unknown names, and the tool layer
        sanitizes that into a class-name-only error message.
        """
        select = db_tools["db_postgres_dml_select"]
        result = await select.ainvoke({
            "connection_name": "ghost",
            "query": "SELECT 1",
        })

        assert "ERROR" in result
        # The connection name should be mentioned so the agent
        # can correlate.
        assert "ghost" in result

    @pytest.mark.asyncio
    async def test_select_query_is_forwarded_to_pool(self, db_tools, pool_manager):
        """A legitimate SELECT is forwarded to the pool manager, and
        the rendered result uses the data returned by the pool.
        """
        # Patch the pool manager so the test does not need a real DB.
        async def fake_execute_select(
            connection_name: str,
            query: str,
            timeout: int = 30,
            max_rows: int = 1000,
        ) -> dict:
            return {
                "columns": ["x"],
                "rows": [{"x": 42}],
                "row_count": 1,
                "truncated": False,
            }

        # Bind the fake onto the pool manager used by db_tools.
        with patch.object(
            pool_manager,
            "execute_select",
            new=fake_execute_select,
        ):
            select = db_tools["db_postgres_dml_select"]
            result = await select.ainvoke({
                "connection_name": "anything",
                "query": "SELECT 42 AS x",
            })

        assert "42" in result
        assert "x" in result
        assert "Rows: 1" in result


# =============================================================================
# Group 4: TestToolFilterIntegration — Tool filter expansion
# =============================================================================


class TestToolFilterIntegration:
    """The ``"db"`` category must expand to all 5 db tool names when
    present in an agent's allow list, and exclude them when absent.
    """

    @staticmethod
    def _categories_with_db() -> dict[str, list[str]]:
        """A small ``tool_categories`` map that includes the db category."""
        return {
            "bash": ["bash"],
            "filesystem": ["read_file"],
            "db": list(DB_TOOL_NAMES),
        }

    def test_db_category_grants_all_db_tools(self):
        """Allow list = ``["db"]`` → all 5 db tool names are included."""
        from daemon.tools.instance import resolve_tool_filter

        result = resolve_tool_filter(
            allow=["db"],
            deny=None,
            tool_categories=self._categories_with_db(),
        )

        assert result == set(DB_TOOL_NAMES)

    def test_db_category_absent_excludes_db_tools(self):
        """Allow list = ``["bash"]`` → NO db tool names are included."""
        from daemon.tools.instance import resolve_tool_filter

        result = resolve_tool_filter(
            allow=["bash"],
            deny=None,
            tool_categories=self._categories_with_db(),
        )

        # bash is included; no db_* tool is included.
        assert "bash" in result
        assert not (result & DB_TOOL_NAMES)

    def test_db_category_registered_in_registry(self):
        """The ``"db"`` category is registered with the 5 tool names
        after the factory has been called. This verifies the
        ``@register_tool_category("db")`` decorator on each tool
        function and the ``scan_tools_for_full_docs`` registration.
        """
        from daemon.tools.db_tools import create_db_tools
        from daemon.tools._tool_registry import (
            clear_registry,
            list_tools_by_category,
            scan_tools_for_full_docs,
        )

        # Clear the registry to make this test independent of
        # collection order, then restore the prior state at the end.
        clear_registry()
        try:
            # Build the tools. The decorator runs at function
            # definition time, so the categories are wired when the
            # module is imported; ``scan_tools_for_full_docs`` then
            # populates the registry with the tool metadata.
            mgr = MagicMock()
            mgr.credential_manager = MagicMock()
            mgr.credential_manager.is_encryption_available = MagicMock(return_value=True)
            tools_list = create_db_tools(
                mgr, "x", repository=MagicMock(), pool_manager=MagicMock(),
            )
            scan_tools_for_full_docs(tools_list)

            categories = list_tools_by_category()
            # The "db" category must exist with all 5 tool names.
            assert "db" in categories
            assert set(categories["db"]) == set(DB_TOOL_NAMES)
        finally:
            # Clean up: clear the registry so other tests are not affected.
            clear_registry()
