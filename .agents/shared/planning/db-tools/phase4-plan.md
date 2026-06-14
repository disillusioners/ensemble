# Phase 4: Agent Access Configuration + Tests

## Objective
Grant `db` tool access to coder, devops, and tester agents by adding `"db"` to their `tools.allow` arrays, and write a comprehensive test suite covering all DB tools, SELECT guard, encryption, and error handling.

## Coupling
- **Depends on:** Phase 3 (tools must exist and be registered)
- **Coupling type:** loose — only references tool names and imports for testing
- **Shared files with other phases:** None
- **Shared APIs/interfaces:** Tests import Phase 1 + Phase 2 + Phase 3 components
- **Why this coupling:** Meta.json changes are configuration; tests validate the integrated behavior. Both depend on Phase 3 being complete but don't modify Phase 3 files.

## Context
- **Phase 3 delivered:** 5 DB tools registered under `"db"` category, wired into `create_instance_tools()`
- **Tool filtering system:** `_apply_tool_filter()` in `instance.py` expands category names to tool names. Adding `"db"` to `tools.allow` will automatically grant all 5 `db_*` tools.
- **Current agent allow lists:** `["bash", "filesystem", "time", "self", "help", "knowledge", "mcp", "context"]`

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Add `db` to coder meta.json | Add `"db"` to `tools.allow` array. | `agents/coder/meta.json` (MODIFY) |
| 2 | Add `db` to devops meta.json | Add `"db"` to `tools.allow` array. | `agents/devops/meta.json` (MODIFY) |
| 3 | Add `db` to tester meta.json | Add `"db"` to `tools.allow` array. | `agents/tester/meta.json` (MODIFY) |
| 4 | Write tool integration tests | Test all 5 tools via `create_db_tools()` with a mock manager. Test connection add→list→test→delete lifecycle. Test `db_postgres_dml_select` with mocked pool. | `tests/test_db_tools.py` (NEW) |
| 5 | Write SELECT guard tests | Test that valid SELECT and WITH/CTE queries pass. Test that INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, TRUNCATE are rejected. Test edge cases: comments, semicolons, empty queries, leading whitespace. | `tests/test_db_select_guard.py` (NEW) |
| 6 | Write encryption security tests | Test that `db_conn_list` never returns passwords. Test that `db_conn_add` return value doesn't contain password. Test encryption round-trip (write → read → decrypt = original). | `tests/test_db_tools.py` (ADD to) |
| 7 | Write tool-filter integration test | Test that when `"db"` is in allow list, all 5 `db_*` tools are included. Test that when `"db"` is NOT in allow list, no `db_*` tools are included. | `tests/test_db_tools.py` (ADD to) |

## Key Files

### MODIFIED Files
- `agents/coder/meta.json` — Add `"db"` to `tools.allow`
- `agents/devops/meta.json` — Add `"db"` to `tools.allow`
- `agents/tester/meta.json` — Add `"db"` to `tools.allow`

### NEW Files
- `tests/test_db_tools.py` — Integration tests for all DB tools
- `tests/test_db_select_guard.py` — Dedicated SELECT-only guard tests

## Detailed Design

### Meta.json Changes

For each of `agents/coder/meta.json`, `agents/devops/meta.json`, `agents/tester/meta.json`:

```json
{
  "tools": {
    "allow": ["bash", "filesystem", "time", "self", "help", "knowledge", "mcp", "context", "db"]
  }
}
```

Change: append `"db"` to the `allow` array.

### Test Plan

#### `tests/test_db_select_guard.py` — SELECT Guard Tests

```python
class TestSelectGuard:
    """Tests for SELECT-only query validation."""

    def test_valid_simple_select(self):
        """SELECT * FROM users should pass."""
        _validate_select_only("SELECT * FROM users")

    def test_valid_select_with_columns(self):
        """SELECT id, name FROM users should pass."""
        _validate_select_only("SELECT id, name FROM users")

    def test_valid_with_cte(self):
        """WITH ... AS (...) SELECT ... should pass."""
        _validate_select_only("WITH active AS (SELECT * FROM users WHERE active = true) SELECT * FROM active")

    def test_valid_select_with_trailing_semicolon(self):
        """SELECT with trailing semicolon should pass."""
        _validate_select_only("SELECT 1;")

    def test_valid_select_with_comments(self):
        """SELECT with comments should pass."""
        _validate_select_only("-- This is a comment\nSELECT 1")

    def test_reject_insert(self):
        """INSERT should be rejected."""
        with pytest.raises(ValueError, match="SELECT"):
            _validate_select_only("INSERT INTO users VALUES (1)")

    def test_reject_update(self):
        """UPDATE should be rejected."""
        with pytest.raises(ValueError, match="UPDATE"):
            _validate_select_only("UPDATE users SET name = 'x'")

    def test_reject_delete(self):
        """DELETE should be rejected."""
        with pytest.raises(ValueError, match="DELETE"):
            _validate_select_only("DELETE FROM users")

    def test_reject_drop(self):
        """DROP should be rejected."""
        with pytest.raises(ValueError, match="DROP"):
            _validate_select_only("DROP TABLE users")

    def test_reject_select_into(self):
        """SELECT ... INTO should be rejected (creates a table)."""
        # C1: INTO is now in _FORBIDDEN_KEYWORDS, so the word-boundary scan catches it
        with pytest.raises(ValueError, match="INTO"):
            _validate_select_only("SELECT * INTO new_table FROM users")

    def test_reject_cte_with_delete(self):
        """CTE containing DELETE should be rejected."""
        with pytest.raises(ValueError, match="DELETE"):
            _validate_select_only("WITH deleted AS (DELETE FROM users RETURNING *) SELECT * FROM deleted")

    def test_reject_empty_query(self):
        """Empty query should be rejected."""
        with pytest.raises(ValueError, match="empty"):
            _validate_select_only("")

    def test_reject_comment_only_query(self):
        """Query with only comments should be rejected."""
        with pytest.raises(ValueError, match="empty"):
            _validate_select_only("-- just a comment")

    def test_does_not_match_column_names_with_keywords(self):
        """Column name 'updated_at' should not trigger UPDATE rejection."""
        # 'updated_at' contains 'UPDATE' as substring but word-boundary check should skip it
        _validate_select_only("SELECT updated_at FROM users")

    def test_keyword_in_string_literal_not_flagged(self):
        """N4: Keywords inside string literals should NOT trigger rejection."""
        # '%INTO%' is inside a string literal — should pass
        _validate_select_only("SELECT * FROM logs WHERE message LIKE '%INTO%'")
        # 'DROP TABLE' is inside a string literal — should pass
        _validate_select_only("SELECT note FROM tickets WHERE note = 'DROP TABLE'")
        # 'DELETE' is inside a string literal — should pass
        _validate_select_only("SELECT * FROM config WHERE key = 'DELETE'")

    def test_string_literal_with_escaped_quotes(self):
        """N4: String literals with escaped single-quotes should be handled."""
        # '' inside a string is an escaped single-quote, not end of string
        _validate_select_only("SELECT * FROM t WHERE v = 'it''s INTO'")
```

#### `tests/test_db_tools.py` — Integration Tests

```python
class TestDbConnTools:
    """Integration tests for DB connection management tools."""

    @pytest.fixture
    def db_tools(self, tmp_path):
        """Create DB tools with in-memory SQLite engine.
        
        C3: The factory receives shared repository and pool_manager.
        N1: Repository takes NO credential_manager.
        N5: Pool manager receives credential_manager for DSN decryption.
        """
        from sqlalchemy import create_engine
        from daemon.repositories.db_connection.repository import DbConnectionRepository
        from daemon.sources.credentials import CredentialManager
        from daemon.services.db_pool_manager import ConnectionPoolManager
        from daemon.tools.db_tools import create_db_tools
        
        engine = create_engine(f"sqlite:///{tmp_path}/test.db")
        # N5: Single shared CredentialManager
        cred_mgr = CredentialManager()
        # N1: Repository takes engine ONLY — no credential_manager
        repository = DbConnectionRepository(engine)
        # N1/N5: Pool manager gets credential_manager for DSN decryption
        pool_manager = ConnectionPoolManager(repository, cred_mgr)
        
        # C3: Factory receives shared instances, not manager creating its own
        mock_manager = create_mock_manager_with_db_services(repository, pool_manager)
        tools_list = create_db_tools(
            mock_manager, "test-instance",
            repository=repository,
            pool_manager=pool_manager,
        )
        # Return dict of tool_name → tool
        return {getattr(t, 'name', None): t for t in tools_list}

    @pytest.mark.asyncio
    async def test_conn_add_creates_connection(self, db_tools):
        """db_conn_add should create a connection."""
        result = await db_tools["db_conn_add"].ainvoke({
            "connection_name": "test-pg",
            "db_type": "postgres",
            "host": "localhost",
            "port": 5432,
            "database": "testdb",
            "username": "testuser",
            "password": "secret123",
        })
        assert "test-pg" in result
        assert "Created" in result or "registered" in result.lower()
        # Password must NOT be in output
        assert "secret123" not in result

    @pytest.mark.asyncio
    async def test_conn_list_shows_connections(self, db_tools):
        """db_conn_list should list connections without secrets."""
        await db_tools["db_conn_add"].ainvoke({...})
        result = await db_tools["db_conn_list"].ainvoke({})
        assert "test-pg" in result
        assert "secret123" not in result
        assert "password" not in result.lower()

    @pytest.mark.asyncio
    async def test_conn_delete_removes_connection(self, db_tools):
        """db_conn_delete should remove a connection."""
        await db_tools["db_conn_add"].ainvoke({...})
        result = await db_tools["db_conn_delete"].ainvoke({"connection_name": "test-pg"})
        assert "deleted" in result.lower()
        # Verify it's gone
        list_result = await db_tools["db_conn_list"].ainvoke({})
        assert "test-pg" not in list_result

    @pytest.mark.asyncio
    async def test_conn_add_duplicate_name_rejected(self, db_tools):
        """Adding duplicate connection name should fail gracefully."""
        await db_tools["db_conn_add"].ainvoke({...})
        result = await db_tools["db_conn_add"].ainvoke({...})  # Same name
        assert "ERROR" in result or "already exists" in result.lower()

    @pytest.mark.asyncio
    async def test_conn_delete_nonexistent(self, db_tools):
        """Deleting a non-existent connection should return error."""
        result = await db_tools["db_conn_delete"].ainvoke({"connection_name": "nonexistent"})
        assert "ERROR" in result or "not found" in result.lower()


class TestDbPostgresSelect:
    """Tests for db_postgres_dml_select tool."""

    @pytest.mark.asyncio
    async def test_select_rejects_non_select(self, db_tools):
        """Non-SELECT queries should be rejected."""
        result = await db_tools["db_postgres_dml_select"].ainvoke({
            "connection_name": "test-pg",
            "query": "DELETE FROM users",
        })
        assert "ERROR" in result
        assert "SELECT" in result

    @pytest.mark.asyncio
    async def test_select_rejects_select_into(self, db_tools):
        """SELECT ... INTO should be rejected (C1)."""
        result = await db_tools["db_postgres_dml_select"].ainvoke({
            "connection_name": "test-pg",
            "query": "SELECT * INTO new_table FROM users",
        })
        assert "ERROR" in result
        assert "INTO" in result

    @pytest.mark.asyncio
    async def test_select_nonexistent_connection(self, db_tools):
        """Querying non-existent connection should return error."""
        result = await db_tools["db_postgres_dml_select"].ainvoke({
            "connection_name": "nonexistent",
            "query": "SELECT 1",
        })
        assert "ERROR" in result
        assert "not found" in result.lower()


class TestToolFilterIntegration:
    """Test that tool filtering works for the db category."""

    def test_db_category_grants_all_db_tools(self):
        """When 'db' is in allow list, all db_* tools should be included."""
        # This tests the resolve_tool_filter function
        from daemon.tools.instance import resolve_tool_filter
        from daemon.tools._tool_registry import list_tools_by_category
        ...

    def test_db_category_absent_excludes_db_tools(self):
        """When 'db' is NOT in allow list, no db_* tools should be included."""
        ...


class TestPoolErrorSanitization:
    """Tests that error messages never leak DSN/password (W1, BLOCKER 3)."""

    def test_sanitize_dsn_format(self):
        """DSN format (postgresql://user:password@host) must be redacted."""
        from daemon.services.db_pool_manager import ConnectionPoolManager
        result = ConnectionPoolManager._sanitize_error(
            "connection refused: postgresql://admin:s3cr3t@db.example.com:5432/mydb"
        )
        assert "s3cr3t" not in result
        assert "***" in result

    def test_sanitize_password_equals_format(self):
        """password=... format must be redacted."""
        from daemon.services.db_pool_manager import ConnectionPoolManager
        result = ConnectionPoolManager._sanitize_error(
            "auth failed: password=mySecret123 host=localhost"
        )
        assert "mySecret123" not in result
        assert "password=***" in result

    def test_sanitize_pg_native_quoted_format(self):
        """BLOCKER 3: PostgreSQL native quoted format password "..." must be redacted."""
        from daemon.services.db_pool_manager import ConnectionPoolManager
        result = ConnectionPoolManager._sanitize_error(
            'FATAL: password authentication failed for user "admin", password "mySecret"'
        )
        assert "mySecret" not in result
        assert "password \"***\"" in result

    def test_sanitize_role_password_quoted(self):
        """BLOCKER 3: role/user + password quoted format must be redacted."""
        from daemon.services.db_pool_manager import ConnectionPoolManager
        result = ConnectionPoolManager._sanitize_error(
            'role "admin" password "s3cr3t" does not exist'
        )
        assert "s3cr3t" not in result
        assert "***" in result

    @pytest.mark.asyncio
    async def test_connection_error_excludes_password(self):
        """Pool creation error must not include DSN (contains password)."""
        # Mock asyncpg.create_pool to raise an error containing DSN
        # Verify the error message contains connection_name but NOT password/DSN
        ...

    @pytest.mark.asyncio
    async def test_test_connection_error_safe(self):
        """test_connection error message must not leak credentials."""
        ...
```

## Constraints
- Meta.json changes must be backward-compatible (only adding to array)
- Tests must NOT require a live PostgreSQL instance (mock the pool manager or use SQLite for registry tests)
- SELECT guard tests must cover both positive (valid) and negative (invalid) cases
- **C1 — SELECT ... INTO must be tested as rejected:** The INTO keyword in `_FORBIDDEN_KEYWORDS` must be verified by a dedicated test.
- **N4 — String literal false-positive tests:** Tests must verify that keywords inside string literals (`'%INTO%'`, `'DROP TABLE'`, `'DELETE'`) do NOT trigger rejection. Must also test escaped single-quotes (`''`).
- **N3 — DSN building tests:** Test `_build_dsn()` with all three cases: (1) user+password, (2) user without password, (3) truly anonymous.
- Security tests must assert that passwords NEVER appear in output strings
- **W1/N9/BLOCKER 3 — Error sanitization tests:** Tests must verify that pool connection errors do NOT include the DSN/password. Test `_sanitize_error()` redacts ALL three formats: (1) DSN `postgresql://user:password@host`, (2) `password=...`, (3) PostgreSQL native quoted `password "..."` (BLOCKER 3).
- Tests should follow the existing test pattern (`await tool.ainvoke({...})`)
- **C3/N1 — Test fixture:** The `db_tools` fixture creates a real `DbConnectionRepository(engine)` (NO credential_manager — N1) + `ConnectionPoolManager(repository, cred_mgr)` and passes them to `create_db_tools()` — matching the production pattern.
- All tests must pass with `pytest` (no special markers needed)
- **W3/W4/W6 — Known limitations documented:** SELECT guard is defense-in-depth only. Tests document what it catches but don't claim it's a security boundary. True security is the DB-level read-only role.

## Deliverables
- [ ] `"db"` added to `tools.allow` in `agents/coder/meta.json`
- [ ] `"db"` added to `tools.allow` in `agents/devops/meta.json`
- [ ] `"db"` added to `tools.allow` in `agents/tester/meta.json`
- [ ] `tests/test_db_select_guard.py` with comprehensive SELECT guard tests (incl. INTO rejection — C1, string-literal false-positive tests — N4)
- [ ] `tests/test_db_tools.py` with integration tests for all 5 tools
- [ ] `tests/test_db_pool_manager.py` with DSN building tests (N3 three-case) and error sanitization tests covering ALL three formats: DSN, password=, PG native quoted (W1/N9/BLOCKER 3)
- [ ] Test fixture creates `DbConnectionRepository(engine)` without credential_manager (N1) + `ConnectionPoolManager(repository, cred_mgr)` (N5)
- [ ] SELECT ... INTO rejection tested (C1)
- [ ] String literal false-positive cases tested (N4)
- [ ] Security tests confirm no password leakage
- [ ] Error sanitization tests confirm redaction of DSN format, password= format, AND PostgreSQL native quoted `password "..."` format (W1/BLOCKER 3)
- [ ] Tool filter tests confirm category-based access control
- [ ] All tests pass with `pytest tests/test_db_*.py`
