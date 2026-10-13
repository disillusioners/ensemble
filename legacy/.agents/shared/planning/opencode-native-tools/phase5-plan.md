# Phase 5: Tests

## Objective
Create comprehensive unit tests for the production code in `daemon/opencode/` and integration tests for the end-to-end workflow.

## Coupling
- **Depends on**: Phase 1 (production code)
- **Coupling type**: independent (tests are separate files, no shared code with production)
- **Shared files with other phases**: None
- **Why this coupling**: Tests verify the implementation; can be written in parallel with Phases 2/3/4.

## Context
- Existing test patterns: `tests/job_queue/test_*_repository.py`
- Test framework: pytest with async support
- Mock HTTP responses needed for client tests
- Test both SQLite and PostgreSQL dialects where relevant

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | State helpers unit tests | `_derive_state_from_finish`, `has_message_error`, `get_message_finish`, `strip_message_bloat` | `tests/opencode/test_state.py` (NEW) |
| 2 | Repository unit tests | CRUD, dialect-aware upsert, index on `id`, error cases | `tests/opencode/test_repository.py` (NEW) |
| 3 | HTTP client unit tests | All 8 endpoints, auth headers, error handling, mocked httpx | `tests/opencode/test_client.py` (NEW) |
| 4 | Session manager unit tests | State transitions, worker pattern, optimistic BUSY, abort, recovery | `tests/opencode/test_session_manager.py` (NEW) |
| 5 | Registry unit tests | create_new, abort_session, recover_from_registry, handle_start_work | `tests/opencode/test_registry.py` (NEW) |
| 6 | Server dispatcher tests | `external_opencode_send_message` with special prompts, BUSY bypass, agent lock | `tests/opencode/test_server.py` (NEW) |
| 7 | Tool factory tests | All 8 tools return correct format, error handling | `tests/opencode/test_tools.py` (NEW) |
| 8 | Integration test | End-to-end workflow: init → send → wait → status → answer → abort | `tests/opencode/test_integration.py` (NEW) |
| 9 | Table creation test | `__table__.create()` idempotency + no ensemble table leakage | `tests/opencode/test_table_creation.py` (NEW) |

## Test File Details

### `tests/opencode/test_state.py`

```python
"""Unit tests for state derivation helpers."""

import pytest
from daemon.opencode.state import (
    SessionState,
    _derive_state_from_finish,
    has_message_error,
    get_message_finish,
    strip_message_bloat,
)


class TestDeriveState:
    def test_waiting_for_input(self):
        assert _derive_state_from_finish("waiting_for_input", False) == SessionState.WAITING_FOR_INPUT
    
    def test_stop(self):
        assert _derive_state_from_finish("stop", False) == SessionState.IDLE
    
    def test_unknown_with_error(self):
        assert _derive_state_from_finish("<unknown>", True) == SessionState.IDLE
    
    def test_unknown_without_error(self):
        assert _derive_state_from_finish("<unknown>", False) == SessionState.BUSY


class TestHasMessageError:
    def test_error_present(self):
        msg = {"info": {"error": "timeout"}}
        assert has_message_error(msg) is True
    
    def test_error_key_present_but_none(self):
        msg = {"info": {"error": None}}
        assert has_message_error(msg) is True  # key presence, not truthiness
    
    def test_no_error_key(self):
        msg = {"info": {"finish": "stop"}}
        assert has_message_error(msg) is False


class TestGetMessageFinish:
    def test_extracts_reason(self):
        msg = {"parts": [{"type": "step-finish", "reason": "stop"}]}
        assert get_message_finish(msg) == ("stop", False)
    
    def test_returns_unknown_when_no_step_finish(self):
        msg = {"parts": [{"type": "text", "text": "hello"}]}
        assert get_message_finish(msg) == ("<unknown>", False)


class TestStripMessageBloat:
    def test_removes_reasoning_text(self):
        msg = {"parts": [{"type": "reasoning", "text": "long internal monologue"}]}
        result = strip_message_bloat(msg)
        # text should be truncated or removed
        assert "long internal monologue" not in str(result) or len(str(result)) < len(str(msg))
```

### `tests/opencode/test_repository.py`

```python
"""Unit tests for OpenCodeSessionRepository."""

import pytest
from sqlalchemy import create_engine
from daemon.opencode.repository import OpenCodeSessionRepository, OpenCodeSessionRecord


@pytest.fixture
def engine():
    """In-memory engine with ONLY opencode_sessions table.
    Uses __table__.create() — NOT SQLModel.metadata.create_all().
    """
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    OpenCodeSessionRecord.__table__.create(engine, checkfirst=True)
    return engine


@pytest.fixture
def repo(engine):
    return OpenCodeSessionRepository(engine)


class TestCreate:
    def test_create_new_session(self, repo):
        repo.create("myapp", "feature-x", "session-uuid-123", "/path")
        record = repo.get("myapp", "feature-x")
        assert record is not None
        assert record.id == "session-uuid-123"
    
    def test_create_duplicate_raises(self, repo):
        repo.create("myapp", "feature-x", "id1", "/path")
        with pytest.raises(ValueError):
            repo.create("myapp", "feature-x", "id2", "/path")


class TestFindById:
    def test_find_by_id_uses_index(self, repo):
        repo.create("myapp", "feature-x", "session-uuid-123", "/path")
        record = repo.find_by_id("session-uuid-123")
        assert record is not None
        assert record.project == "myapp"


class TestUpdateAgentState:
    def test_locks_agent(self, repo):
        repo.create("myapp", "feature-x", "id1", "/path")
        repo.update_agent_state("myapp", "feature-x", "atlas", True)
        record = repo.get("myapp", "feature-x")
        assert record.is_agent_locked is True
        assert record.last_agent == "atlas"
```

### `tests/opencode/test_client.py`

```python
"""Unit tests for OpenCodeClient (with mocked httpx)."""

import pytest
from unittest.mock import AsyncMock, patch
from daemon.opencode.client import OpenCodeClient, PromptRequest, ModelDetails


@pytest.fixture
def client():
    return OpenCodeClient(
        base_url="http://127.0.0.1:4095",
        working_dir="/test/path",
        api_user="opencode",
        api_key="opencode",
    )


class TestHeaders:
    def test_headers_include_auth(self, client):
        headers = client._build_headers()
        assert "Authorization" in headers
        assert headers["Authorization"].startswith("Basic ")
    
    def test_headers_include_directory(self, client):
        headers = client._build_headers()
        assert headers["x-opencode-directory"] == "/test/path"


class TestCreateSession:
    @pytest.mark.asyncio
    async def test_create_session_success(self, client):
        # Issue 6: Production code uses self._client.request() internally,
        # NOT httpx.AsyncClient.post(). Patch the actual method used.
        with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"id": "session-uuid", "title": "test"}
            result = await client.create_session("test")
            assert result == "session-uuid"
            # Verify it called _request with POST
            mock_req.assert_awaited_once()
```

### `tests/opencode/test_session_manager.py`

```python
"""Unit tests for OpenCodeSessionManager state machine."""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from daemon.opencode.session_manager import (
    OpenCodeSessionManager,
    SessionState,
    Request,
)


@pytest.fixture
def mock_client():
    return AsyncMock()


@pytest.fixture
def manager(mock_client):
    return OpenCodeSessionManager(
        session_id="remote-session-uuid",
        working_dir="/test/path",
        client=mock_client,
    )


class TestInitialState:
    def test_starts_idle(self, manager):
        assert manager._state == SessionState.IDLE


class TestSubmitRequest:
    @pytest.mark.asyncio
    async def test_optimistic_busy(self, manager):
        req = Request(type_="PROMPT", payload=None)
        manager.submit_request(req)
        await asyncio.sleep(0.01)  # let the task run
        assert manager._state == SessionState.BUSY
        assert manager._is_worker_busy is True
    
    @pytest.mark.asyncio
    async def test_callback_outside_lock(self, manager):
        """Verify that _persist_state is called after lock is released."""
        # This is a concurrency test — hard to assert precisely, but we can
        # check that the lock is not held during the callback.
        ...


class TestAbort:
    @pytest.mark.asyncio
    async def test_abort_sets_idle(self, manager):
        await manager.abort_task()
        assert manager._state == SessionState.IDLE
        assert manager._aborted is True
```

### `tests/opencode/test_registry.py`

```python
"""Unit tests for OpenCodeSessionRegistry."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from daemon.opencode.registry import OpenCodeSessionRegistry


@pytest.fixture
def mock_repo():
    return MagicMock()


@pytest.fixture
def registry(mock_repo):
    return OpenCodeSessionRegistry(repository=mock_repo)


class TestCreateNew:
    @pytest.mark.asyncio
    async def test_abort_old_then_delete(self, registry, mock_repo):
        """Verify INIT_SESSION conflict resolution sequence."""
        mock_repo.get.return_value = {"id": "old-id", "working_dir": "/old"}
        with patch("daemon.opencode.registry.OpenCodeClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.create_session.return_value = "new-id"
            mock_client_cls.return_value = mock_client
            
            await registry.create_new("myapp", "feature-x", "/new")
            
            # Verify abort was called on old session
            mock_client.abort_session.assert_awaited_once_with("old-id")
            # Verify delete was called
            mock_repo.delete.assert_called_once_with("myapp", "feature-x")


class TestHandleStartWork:
    @pytest.mark.asyncio
    async def test_locks_agent_to_atlas(self, registry, mock_repo):
        await registry.handle_start_work("myapp", "feature-x", agent="atlas")
        mock_repo.update_agent_state.assert_called_once_with(
            project="myapp",
            session_name="feature-x",
            last_agent="atlas",
            is_locked=True,
        )
```

### `tests/opencode/test_server.py`

```python
"""Unit tests for external_opencode_send_message dispatcher."""

import pytest
from unittest.mock import AsyncMock, MagicMock
from daemon.opencode.server import (
    external_opencode_send_message,
    OpenCodeRequest,
)


@pytest.fixture
def mock_registry():
    registry = MagicMock()
    registry.get_manager = AsyncMock(return_value=MagicMock())
    registry.find_by_id = AsyncMock(return_value={"project": "myapp", "session_name": "x", "is_agent_locked": True, "last_agent": "atlas"})
    return registry


class TestSpecialPrompts:
    @pytest.mark.asyncio
    async def test_start_work_locks_agent(self, mock_registry):
        req = OpenCodeRequest(action="PROMPT", session_id="sid", payload={"parts": [{"type": "text", "text": "/start-work"}]})
        # Verify handle_start_work is called before submit
        ...


class TestBusyBypass:
    @pytest.mark.asyncio
    async def test_special_prompt_bypasses_busy(self, mock_registry):
        # When state is BUSY and prompt is special, should not return error
        ...
```

### `tests/opencode/test_tools.py`

```python
"""Unit tests for the 8 opencode tool functions."""

import pytest
from unittest.mock import AsyncMock, MagicMock
from daemon.tools.external_opencode import create_opencode_tools


@pytest.fixture
def mock_manager():
    manager = MagicMock()
    manager._opencode_registry = AsyncMock()
    return manager


@pytest.fixture
def tools(mock_manager):
    return create_opencode_tools(mock_manager, "instance-123")


class TestInitSession:
    @pytest.mark.asyncio
    async def test_returns_success_format(self, tools, mock_manager):
        result = await tools[0]("myapp", "feature-x", "/path")
        assert "[SUCCESS]" in result or "[ERROR]" in result
```

### `tests/opencode/test_integration.py`

```python
"""Integration test for end-to-end opencode workflow.

Requires a running OpenCode instance at 127.0.0.1:4095.
"""

import pytest
import asyncio


@pytest.mark.integration
@pytest.mark.asyncio
class TestEndToEndWorkflow:
    async def test_full_lifecycle(self):
        """init → send → wait → status → cleanup."""
        ...
    
    async def test_parallel_sessions(self):
        """3 sessions in parallel via wait_any."""
        ...
    
    async def test_persistence_across_restart(self):
        """Session survives manager restart."""
        ...
```

### `tests/opencode/test_table_creation.py`

```python
"""Test __table__.create() idempotency for opencode_sessions table.
Replaces the deleted migration test (Rev 3 Blocker 2 — no migration file).
"""

import pytest
from sqlalchemy import create_engine, inspect
from daemon.opencode.repository import (
    OpenCodeSessionRecord,
    create_opencode_session_repository,
)


class TestTableCreation:
    def test_create_table_on_empty_db(self):
        """Table created on a fresh engine."""
        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        repo = create_opencode_session_repository(engine)
        assert repo is not None
        inspector = inspect(engine)
        assert "opencode_sessions" in inspector.get_table_names()

    def test_create_idempotent(self):
        """Calling create_opencode_session_repository twice doesn't fail (checkfirst=True)."""
        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        repo1 = create_opencode_session_repository(engine)
        repo2 = create_opencode_session_repository(engine)  # should NOT raise
        assert repo1 is not None
        assert repo2 is not None

    def test_only_opencode_table_created(self):
        """Only opencode_sessions table exists — no ensemble tables leaked."""
        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        create_opencode_session_repository(engine)
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        assert "opencode_sessions" in tables
        # Verify NO ensemble tables leaked into this engine
        assert "instances" not in tables
        assert "projects" not in tables
        assert "message_queue" not in tables

    def test_index_exists(self):
        """ix_opencode_sessions_id index exists after creation."""
        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        create_opencode_session_repository(engine)
        inspector = inspect(engine)
        indexes = [idx["name"] for idx in inspector.get_indexes("opencode_sessions")]
        assert "ix_opencode_sessions_id" in indexes
```

## Test Infrastructure

```python
# tests/opencode/__init__.py
"""Tests for native opencode tools."""

# tests/opencode/conftest.py
"""Shared fixtures for opencode tests."""

import pytest
from sqlalchemy import create_engine


@pytest.fixture
def sqlite_engine():
    """In-memory SQLite engine with ONLY opencode_sessions table.
    Uses __table__.create() (Blocker 1 fix) — NOT SQLModel.metadata.create_all()
    which would create all 22+ ensemble tables in the test engine.
    """
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    from daemon.opencode.repository import OpenCodeSessionRecord
    OpenCodeSessionRecord.__table__.create(engine, checkfirst=True)
    return engine


@pytest.fixture
def mock_opencode_api():
    """Mock httpx responses for OpenCode API."""
    ...
```

## Constraints
- All async tests use `@pytest.mark.asyncio`
- **Issue 6**: Tests must patch `client._request()` (the actual internal method used by production code), NOT `httpx.AsyncClient.post()`. Production code uses a unified `_request()` method internally; patching `.post()` would silently bypass mocks and hit real network.
- HTTP client tests must mock `client._request()` (no real network calls in unit tests)
- Integration tests are marked `@pytest.mark.integration` and skipped in CI unless OpenCode is running
- Repository tests use in-memory SQLite
- State manager tests verify exact state transitions
- Test file paths follow existing convention: `tests/opencode/test_*.py`

## Deliverables
- [ ] `tests/opencode/__init__.py` and `conftest.py` created
- [ ] `tests/opencode/test_state.py` — state derivation helpers
- [ ] `tests/opencode/test_repository.py` — CRUD + index
- [ ] `tests/opencode/test_client.py` — all 8 HTTP methods with mocks
- [ ] `tests/opencode/test_session_manager.py` — state machine + worker + abort
- [ ] `tests/opencode/test_registry.py` — create_new, abort, recovery, start-work
- [ ] `tests/opencode/test_server.py` — dispatcher with special prompts
- [ ] `tests/opencode/test_tools.py` — all 8 tools with output format validation
- [ ] `tests/opencode/test_integration.py` — end-to-end workflow (marked integration)
- [ ] `tests/opencode/test_table_creation.py` — `__table__.create()` idempotency + no table leakage (replaces deleted migration test)
- [ ] All unit tests pass with `pytest tests/opencode/`
- [ ] Test coverage > 85% for all new modules
