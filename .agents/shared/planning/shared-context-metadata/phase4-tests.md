# Phase 4: Tests

## Objective

Create comprehensive tests covering all three layers: storage repository, agent tool, and system prompt injection. Tests must pass on both SQLite (default) and PostgreSQL (via `@pytest.mark.postgres` marker).

## Coupling

- **Depends on**: Phases 1, 2, 3 (all layers must be complete)
- **Coupling type**: tight — tests import and exercise all layers
- **Shared files with other phases**: Tests import from all phase outputs
- **Why this coupling**: Integration tests require the full stack to be functional

## Context

- **Test infrastructure**: Project uses `pytest` with `integration` and `postgres` markers. Default tests run on SQLite. PostgreSQL tests in `tests/postgres/` run serially.
- **Existing test patterns**: See `tests/test_project_metadata*.py` for repository test patterns. See `tests/test_*_tools.py` for tool test patterns.
- **Test DB**: Tests use in-memory or temporary SQLite. PostgreSQL tests use a real PG instance.

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Storage layer tests | Test `SharedContextMetadataRepository`: CRUD operations, batch upsert, batch delete, upsert (insert then update), unique constraint, empty key validation, `get_all_as_dict()`. | `tests/test_shared_context_metadata_repo.py` |
| 2 | Agent tool tests | Test `shared_context_metadata` tool: batch set, batch delete, list, mixed operations, invalid op type, empty operations, JSON parse error, context_key auto-resolution. | `tests/test_shared_context_metadata_tool.py` |
| 3 | Injection tests | Test `append_shared_context_metadata()`: with metadata, without metadata (empty), with repository=None, with DB error (graceful degradation), format verification (section headers, `---` separator, JSON content), position in chain (after Context Key, before Current Time). | `tests/test_shared_context_injection.py` |
| 4 | Integration test | End-to-end: set metadata via tool → spawn child agent → verify metadata appears in child's system prompt. | `tests/test_shared_context_e2e.py` |
| 5 | PostgreSQL tests | Run storage layer tests against PostgreSQL (mark with `@pytest.mark.postgres`). | `tests/postgres/test_shared_context_metadata_pg.py` |

## Key Files

### Test File 1: `tests/test_shared_context_metadata_repo.py`

```python
"""Tests for SharedContextMetadataRepository."""
import pytest
from datetime import datetime
from daemon.repositories.shared_context.repository import SharedContextMetadataRepository
from daemon.repositories.shared_context.models import SharedContextMetadataRecord


@pytest.fixture
def repo(tmp_db_engine):
    """Create repository with temporary SQLite engine."""
    from sqlmodel import SQLModel
    SQLModel.metadata.create_all(tmp_db_engine)
    return SharedContextMetadataRepository(tmp_db_engine)


class TestSharedContextMetadataRepository:
    """Test SharedContextMetadataRepository CRUD operations."""

    # ---- Single record operations ----

    def test_upsert_record_insert(self, repo):
        """Test inserting a new record via upsert."""
        record = repo.upsert_record("ctx-1", "key1", "value1")
        assert record is not None
        assert record.context_key == "ctx-1"
        assert record.meta_key == "key1"
        assert record.meta_value == "value1"

    def test_upsert_record_update(self, repo):
        """Test updating an existing record via upsert."""
        repo.upsert_record("ctx-1", "key1", "value1")
        repo.upsert_record("ctx-1", "key1", "value2")
        record = repo.get_record("ctx-1", "key1")
        assert record.meta_value == "value2"

    def test_get_record_not_found(self, repo):
        """Test getting a non-existent record returns None."""
        assert repo.get_record("ctx-1", "nonexistent") is None

    def test_delete_record(self, repo):
        """Test deleting a record."""
        repo.upsert_record("ctx-1", "key1", "value1")
        assert repo.delete_record("ctx-1", "key1") is True
        assert repo.get_record("ctx-1", "key1") is None

    def test_delete_record_not_found(self, repo):
        """Test deleting a non-existent record returns False."""
        assert repo.delete_record("ctx-1", "nonexistent") is False

    def test_upsert_empty_key_raises(self, repo):
        """Test that empty meta_key raises ValueError."""
        with pytest.raises(ValueError):
            repo.upsert_record("ctx-1", "", "value")

    # ---- List operations ----

    def test_list_records_empty(self, repo):
        """Test listing records when none exist."""
        assert repo.list_records("ctx-1") == []

    def test_list_records_multiple(self, repo):
        """Test listing multiple records for a context_key."""
        repo.upsert_record("ctx-1", "key1", "value1")
        repo.upsert_record("ctx-1", "key2", "value2")
        repo.upsert_record("ctx-2", "key3", "value3")  # Different context
        records = repo.list_records("ctx-1")
        assert len(records) == 2
        keys = {r.meta_key for r in records}
        assert keys == {"key1", "key2"}

    def test_get_all_as_dict(self, repo):
        """Test getting all KV pairs as a dict."""
        repo.upsert_record("ctx-1", "key1", "value1")
        repo.upsert_record("ctx-1", "key2", {"nested": "value"})
        result = repo.get_all_as_dict("ctx-1")
        assert result == {"key1": "value1", "key2": {"nested": "value"}}

    # ---- Batch operations ----

    def test_batch_upsert(self, repo):
        """Test batch upsert of multiple KV pairs."""
        items = {"key1": "val1", "key2": "val2", "key3": "val3"}
        repo.batch_upsert("ctx-1", items)
        result = repo.get_all_as_dict("ctx-1")
        assert result == items

    def test_batch_upsert_updates_existing(self, repo):
        """Test batch upsert updates existing keys."""
        repo.upsert_record("ctx-1", "key1", "old_value")
        repo.batch_upsert("ctx-1", {"key1": "new_value", "key2": "val2"})
        result = repo.get_all_as_dict("ctx-1")
        assert result == {"key1": "new_value", "key2": "val2"}

    def test_batch_delete(self, repo):
        """Test batch deletion of multiple keys."""
        repo.batch_upsert("ctx-1", {"key1": "v1", "key2": "v2", "key3": "v3"})
        deleted = repo.batch_delete("ctx-1", ["key1", "key3"])
        assert deleted == 2
        result = repo.get_all_as_dict("ctx-1")
        assert result == {"key2": "v2"}

    def test_batch_delete_nonexistent(self, repo):
        """Test batch deletion with some non-existent keys."""
        repo.batch_upsert("ctx-1", {"key1": "v1"})
        deleted = repo.batch_delete("ctx-1", ["key1", "nonexistent"])
        assert deleted == 1

    # ---- Complex value types ----

    def test_complex_value_types(self, repo):
        """Test storing various JSON value types."""
        repo.upsert_record("ctx-1", "string_val", "hello")
        repo.upsert_record("ctx-1", "int_val", 42)
        repo.upsert_record("ctx-1", "bool_val", True)
        repo.upsert_record("ctx-1", "null_val", None)
        repo.upsert_record("ctx-1", "array_val", [1, 2, 3])
        repo.upsert_record("ctx-1", "object_val", {"nested": {"deep": "value"}})
        result = repo.get_all_as_dict("ctx-1")
        assert result["string_val"] == "hello"
        assert result["int_val"] == 42
        assert result["bool_val"] is True
        assert result["null_val"] is None
        assert result["array_val"] == [1, 2, 3]
        assert result["object_val"] == {"nested": {"deep": "value"}}

    # ---- Context key isolation ----

    def test_context_key_isolation(self, repo):
        """Test that metadata is isolated between different context_keys."""
        repo.upsert_record("ctx-1", "key1", "value1")
        repo.upsert_record("ctx-2", "key1", "value2")
        assert repo.get_all_as_dict("ctx-1") == {"key1": "value1"}
        assert repo.get_all_as_dict("ctx-2") == {"key1": "value2"}
```

### Test File 2: `tests/test_shared_context_metadata_tool.py`

```python
"""Tests for shared_context_metadata agent tool."""
import json
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from daemon.tools.shared_context_tools import create_shared_context_tools


@pytest.fixture
def mock_manager():
    """Create a mock manager with shared_context_repository."""
    manager = MagicMock()
    # Mock instance repository for context_key resolution
    instance_repo = MagicMock()
    instance_repo.get_tree_root_id.return_value = "root-123"
    manager.get_instance_repository.return_value = instance_repo
    # Mock shared context repository
    sc_repo = MagicMock()
    sc_repo.get_all_as_dict.return_value = {}
    sc_repo.upsert_record = MagicMock()
    sc_repo.delete_record = MagicMock(return_value=True)
    sc_repo.list_records.return_value = []
    manager.get_shared_context_repository.return_value = sc_repo
    return manager


@pytest.fixture
def tool_list(mock_manager):
    """Create shared context tools with mock manager."""
    return create_shared_context_tools(mock_manager, "instance-456")


class TestSharedContextMetadataTool:

    @pytest.mark.asyncio
    async def test_set_operation(self, tool_list, mock_manager):
        """Test set operation creates/upserts a KV pair."""
        sc_tool = tool_list[0]
        operations = json.dumps([{"op": "set", "key": "scope", "value": "BIG"}])
        result = await sc_tool.ainvoke({"operations": operations})
        data = json.loads(result)
        assert data["context_key"] == "root-123"
        assert data["results"][0]["status"] == "ok"
        mock_manager.get_shared_context_repository().upsert_record.assert_called_once_with(
            "root-123", "scope", "BIG"
        )

    @pytest.mark.asyncio
    async def test_delete_operation(self, tool_list, mock_manager):
        """Test delete operation removes a KV pair."""
        sc_tool = tool_list[0]
        operations = json.dumps([{"op": "delete", "key": "scope"}])
        result = await sc_tool.ainvoke({"operations": operations})
        data = json.loads(result)
        assert data["results"][0]["deleted"] is True

    @pytest.mark.asyncio
    async def test_list_operation(self, tool_list, mock_manager):
        """Test list operation returns all KV pairs."""
        sc_repo = mock_manager.get_shared_context_repository()
        sc_repo.list_records.return_value = []
        sc_repo.get_all_as_dict.return_value = {"scope": "BIG"}
        # The tool calls list_records for list op
        from daemon.repositories.shared_context.models import SharedContextMetadataRecord
        sc_repo.list_records.return_value = [
            SharedContextMetadataRecord(context_key="root-123", meta_key="scope", meta_value="BIG")
        ]
        sc_tool = tool_list[0]
        operations = json.dumps([{"op": "list"}])
        result = await sc_tool.ainvoke({"operations": operations})
        data = json.loads(result)
        assert data["results"][0]["op"] == "list"
        assert data["results"][0]["count"] == 1

    @pytest.mark.asyncio
    async def test_batch_operations(self, tool_list, mock_manager):
        """Test multiple operations in one call."""
        sc_tool = tool_list[0]
        operations = json.dumps([
            {"op": "set", "key": "k1", "value": "v1"},
            {"op": "set", "key": "k2", "value": 42},
            {"op": "delete", "key": "old_key"},
            {"op": "list"},
        ])
        result = await sc_tool.ainvoke({"operations": operations})
        data = json.loads(result)
        assert len(data["results"]) == 4

    @pytest.mark.asyncio
    async def test_invalid_json(self, tool_list):
        """Test invalid JSON returns error."""
        sc_tool = tool_list[0]
        result = await sc_tool.ainvoke({"operations": "not valid json{"})
        assert "Error" in result

    @pytest.mark.asyncio
    async def test_empty_operations(self, tool_list):
        """Test empty operations array returns error."""
        sc_tool = tool_list[0]
        result = await sc_tool.ainvoke({"operations": "[]"})
        assert "Error" in result

    @pytest.mark.asyncio
    async def test_unknown_op_type(self, tool_list):
        """Test unknown operation type returns error in results."""
        sc_tool = tool_list[0]
        operations = json.dumps([{"op": "foobar", "key": "x"}])
        result = await sc_tool.ainvoke({"operations": operations})
        data = json.loads(result)
        assert "error" in data["results"][0]

    @pytest.mark.asyncio
    async def test_context_key_auto_resolution(self, tool_list, mock_manager):
        """Test that context_key is auto-resolved from instance tree root."""
        sc_tool = tool_list[0]
        operations = json.dumps([{"op": "set", "key": "k", "value": "v"}])
        await sc_tool.ainvoke({"operations": operations})
        # Verify get_tree_root_id was called with current_instance_id
        mock_manager.get_instance_repository().get_tree_root_id.assert_called_with("instance-456")

    @pytest.mark.asyncio
    async def test_context_key_fallback_to_self(self, mock_manager):
        """Test context_key falls back to instance_id when root not found."""
        mock_manager.get_instance_repository().get_tree_root_id.return_value = None
        tool_list = create_shared_context_tools(mock_manager, "instance-789")
        sc_tool = tool_list[0]
        operations = json.dumps([{"op": "set", "key": "k", "value": "v"}])
        result = await sc_tool.ainvoke({"operations": operations})
        data = json.loads(result)
        assert data["context_key"] == "instance-789"
```

### Test File 3: `tests/test_shared_context_injection.py`

```python
"""Tests for append_shared_context_metadata injection function."""
import pytest
from unittest.mock import MagicMock
from daemon.services.instance_lifecycle import append_shared_context_metadata


class TestAppendSharedContextMetadata:

    def test_with_metadata(self):
        """Test injection when metadata exists."""
        repo = MagicMock()
        repo.get_all_as_dict.return_value = {"scope": "BIG", "decision": "OAuth2"}
        prompt = "## Context Key\n\nCONTEXT_KEY: root-123\n"
        result = append_shared_context_metadata(prompt, "root-123", repo)
        assert "# Shared Context" in result
        assert "context_key: root-123" in result
        assert "## Metadata KV" in result
        assert '"scope": "BIG"' in result
        assert '"decision": "OAuth2"' in result
        assert "---" in result

    def test_without_metadata(self):
        """Test that no metadata means no injection."""
        repo = MagicMock()
        repo.get_all_as_dict.return_value = {}
        prompt = "## Context Key\n\nCONTEXT_KEY: root-123\n"
        result = append_shared_context_metadata(prompt, "root-123", repo)
        assert result == prompt  # Unchanged

    def test_repository_none(self):
        """Test that None repository means no injection."""
        prompt = "## Context Key\n\nCONTEXT_KEY: root-123\n"
        result = append_shared_context_metadata(prompt, "root-123", None)
        assert result == prompt  # Unchanged

    def test_repository_error_graceful(self):
        """Test that DB error doesn't crash — graceful degradation."""
        repo = MagicMock()
        repo.get_all_as_dict.side_effect = Exception("DB connection failed")
        prompt = "## Context Key\n\nCONTEXT_KEY: root-123\n"
        result = append_shared_context_metadata(prompt, "root-123", repo)
        assert result == prompt  # Unchanged — error swallowed

    def test_separator_position(self):
        """Test that --- separator comes AFTER metadata content."""
        repo = MagicMock()
        repo.get_all_as_dict.return_value = {"key": "value"}
        prompt = "BASE"
        result = append_shared_context_metadata(prompt, "ctx", repo)
        # The separator should be after the JSON content
        metadata_pos = result.find('"key"')
        separator_pos = result.find("---")
        assert metadata_pos < separator_pos
        assert separator_pos > 0

    def test_json_format(self):
        """Test that metadata is formatted as valid JSON."""
        repo = MagicMock()
        repo.get_all_as_dict.return_value = {"key": "value", "num": 42}
        prompt = "BASE"
        result = append_shared_context_metadata(prompt, "ctx", repo)
        # Extract JSON between "## Metadata KV" and "---"
        start = result.find("## Metadata KV")
        end = result.find("---")
        json_section = result[start:end].strip()
        # Remove the header line
        json_lines = json_section.split("\n", 1)[1].strip()
        import json
        parsed = json.loads(json_lines)
        assert parsed == {"key": "value", "num": 42}

    def test_complex_values(self):
        """Test that complex JSON values are properly serialized."""
        repo = MagicMock()
        repo.get_all_as_dict.return_value = {
            "list_val": [1, 2, 3],
            "obj_val": {"nested": True},
            "null_val": None,
        }
        prompt = "BASE"
        result = append_shared_context_metadata(prompt, "ctx", repo)
        import json
        # Verify the JSON is valid and contains all values
        start = result.find("{")
        end = result.rfind("}") + 1
        parsed = json.loads(result[start:end])
        assert parsed["list_val"] == [1, 2, 3]
        assert parsed["obj_val"] == {"nested": True}
        assert parsed["null_val"] is None

    def test_section_after_context_key(self):
        """Test that the Shared Context section comes after Context Key section."""
        repo = MagicMock()
        repo.get_all_as_dict.return_value = {"key": "value"}
        prompt = "## Context Key\n\nCONTEXT_KEY: root-123\n"
        result = append_shared_context_metadata(prompt, "root-123", repo)
        ctx_key_pos = result.find("## Context Key")
        shared_ctx_pos = result.find("# Shared Context")
        assert ctx_key_pos < shared_ctx_pos
```

### Test File 4: `tests/test_shared_context_e2e.py` (integration)

```python
"""End-to-end integration test for shared context metadata.
Tests the full flow: set metadata via tool → spawn child → verify injection.
"""
import pytest
from daemon.repositories.shared_context.repository import SharedContextMetadataRepository
from daemon.services.instance_lifecycle import append_shared_context_metadata


@pytest.mark.integration
class TestSharedContextE2E:

    def test_full_flow_set_then_inject(self, tmp_db_engine):
        """Test: set metadata → fetch in injection → verify format."""
        from sqlmodel import SQLModel
        SQLModel.metadata.create_all(tmp_db_engine)

        # 1. Set metadata via repository (simulates tool call)
        repo = SharedContextMetadataRepository(tmp_db_engine)
        repo.batch_upsert("ctx-e2e", {
            "project_change_scope": "BIG",
            "decision": "use OAuth2",
        })

        # 2. Simulate injection
        base_prompt = "## Context Key\n\nCONTEXT_KEY: ctx-e2e\n"
        result = append_shared_context_metadata(base_prompt, "ctx-e2e", repo)

        # 3. Verify injection
        assert "# Shared Context" in result
        assert "context_key: ctx-e2e" in result
        assert "## Metadata KV" in result
        assert '"project_change_scope": "BIG"' in result
        assert '"decision": "use OAuth2"' in result
        assert "---" in result

    def test_full_flow_delete_then_inject_empty(self, tmp_db_engine):
        """Test: delete all metadata → inject → verify no section."""
        from sqlmodel import SQLModel
        SQLModel.metadata.create_all(tmp_db_engine)

        repo = SharedContextMetadataRepository(tmp_db_engine)
        repo.batch_upsert("ctx-e2e", {"key1": "val1"})
        repo.batch_delete("ctx-e2e", ["key1"])

        base_prompt = "## Context Key\n\nCONTEXT_KEY: ctx-e2e\n"
        result = append_shared_context_metadata(base_prompt, "ctx-e2e", repo)
        assert "# Shared Context" not in result
```

### Test File 5: `tests/postgres/test_shared_context_metadata_pg.py`

```python
"""PostgreSQL tests for SharedContextMetadataRepository.
Run with: pytest tests/postgres/test_shared_context_metadata_pg.py -m postgres
"""
import pytest
from daemon.repositories.shared_context.repository import SharedContextMetadataRepository


@pytest.mark.postgres
class TestSharedContextMetadataPostgres:

    def test_pg_upsert_and_list(self, pg_engine):
        """Test upsert and list on PostgreSQL."""
        repo = SharedContextMetadataRepository(pg_engine)
        repo.upsert_record("pg-ctx", "key1", {"nested": "value"})
        result = repo.get_all_as_dict("pg-ctx")
        assert result == {"key1": {"nested": "value"}}

    def test_pg_batch_operations(self, pg_engine):
        """Test batch operations on PostgreSQL."""
        repo = SharedContextMetadataRepository(pg_engine)
        repo.batch_upsert("pg-ctx", {"k1": "v1", "k2": "v2", "k3": "v3"})
        assert len(repo.list_records("pg-ctx")) == 3
        deleted = repo.batch_delete("pg-ctx", ["k1", "k2"])
        assert deleted == 2
        assert len(repo.list_records("pg-ctx")) == 1

    def test_pg_upsert_on_conflict(self, pg_engine):
        """Test that upsert properly updates on conflict (PostgreSQL ON CONFLICT)."""
        repo = SharedContextMetadataRepository(pg_engine)
        repo.upsert_record("pg-ctx", "key1", "original")
        repo.upsert_record("pg-ctx", "key1", "updated")
        record = repo.get_record("pg-ctx", "key1")
        assert record.meta_value == "updated"
        # Should NOT have created a duplicate
        records = repo.list_records("pg-ctx")
        assert len(records) == 1
```

## Test Execution

```bash
# Run all shared context tests (SQLite default)
pytest tests/test_shared_context_metadata_repo.py tests/test_shared_context_metadata_tool.py tests/test_shared_context_injection.py tests/test_shared_context_e2e.py -v

# Run PostgreSQL tests
pytest tests/postgres/test_shared_context_metadata_pg.py -m postgres -v

# Run full test suite to check for regressions
pytest -v
```

## Constraints

- Tests must pass on both SQLite (default) and PostgreSQL (via `@pytest.mark.postgres`).
- Tool tests use mock manager/repositories — no real DB needed.
- Injection tests use mock repositories — no real DB needed.
- Repository and E2E tests use temporary SQLite engines (`tmp_db_engine` fixture).
- PostgreSQL tests must run serially (schema conflicts).
- Follow existing test naming conventions (`test_*.py` files, `Test*` classes).
- Use `@pytest.mark.asyncio` for async tool tests.

## Deliverables

- [ ] `tests/test_shared_context_metadata_repo.py` — repository CRUD + batch tests
- [ ] `tests/test_shared_context_metadata_tool.py` — tool operation tests
- [ ] `tests/test_shared_context_injection.py` — injection format + edge case tests
- [ ] `tests/test_shared_context_e2e.py` — end-to-end integration test
- [ ] `tests/postgres/test_shared_context_metadata_pg.py` — PostgreSQL-specific tests
- [ ] All tests pass on SQLite
- [ ] PostgreSQL tests pass when run with `-m postgres`
- [ ] No regressions in existing test suite
