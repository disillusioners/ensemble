"""Comprehensive unit tests for ``OpenCodeSessionRegistry``.

``daemon.opencode.registry.OpenCodeSessionRegistry`` is the top-level
coordinator that owns:
    - ``self._repository`` — the SQLite-backed CRUD
    - ``self._managers``  — the in-memory map ``session_id → SessionManager``

This suite covers every public method plus the crash-recovery path:

- ``__init__`` — repository + callback + empty managers map
- ``get_manager`` — in-memory accessor (None when absent, returns stored)
- ``list_sessions`` / ``find_by_id`` / ``get_session_record`` — delegates
- ``handle_start_work`` — locks agent to ``"atlas"`` via the repository
- ``create_new`` — abort-old + delete + create + load-into-memory
- ``abort_session`` — remote abort + 3-second settle + local reset
- ``recover_from_registry`` — DB-backed startup recovery
- ``load_session_into_memory`` — lazy manager creation + dedup

Mocking strategy:
    - The repository is a ``MagicMock`` (its methods are synchronous).
    - ``OpenCodeClient`` is patched at
      ``daemon.opencode.registry.OpenCodeClient`` so the registry builds
      ``AsyncMock`` instances instead of real ``httpx`` clients.
    - ``_load_manager_into_memory`` is replaced with an ``AsyncMock`` for
      ``create_new`` / ``recover_from_registry`` tests so no real
      background event-loop task is spawned.
    - ``asyncio.sleep`` is patched to assert the 3-second settle without
      a real delay.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from daemon.opencode.constants import ABORT_REMOTE_SETTLE_S
from daemon.opencode.registry import OpenCodeSessionRegistry


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_repository() -> MagicMock:
    """A ``MagicMock`` standing in for ``OpenCodeSessionRepository``.

    Synchronous methods (``get``, ``list``, ``create``, ``delete``, …) are
    plain ``MagicMock`` calls; the registry never awaits them.
    """
    repo = MagicMock()
    repo.get.return_value = None
    repo.list.return_value = []
    repo.find_by_id.return_value = None
    return repo


@pytest.fixture
def registry(mock_repository: MagicMock) -> OpenCodeSessionRegistry:
    """``OpenCodeSessionRegistry`` wired to the mock repository."""
    return OpenCodeSessionRegistry(mock_repository)


@pytest.fixture
def patched_client():
    """Patch ``OpenCodeClient`` in the registry module.

    The patched constructor returns a single ``AsyncMock`` instance for
    every ``OpenCodeClient(...)`` call, so both the abort-client and the
    create-client in ``create_new`` share the same mock. Tests configure
    ``create_session.return_value`` and ``abort_session.side_effect`` on
    the yielded object.
    """
    mock_client = AsyncMock()
    mock_client.create_session.return_value = "new-session-id"
    with patch(
        "daemon.opencode.registry.OpenCodeClient",
        MagicMock(return_value=mock_client),
    ):
        yield mock_client


# =============================================================================
# Construction
# =============================================================================


class TestInit:
    """``OpenCodeSessionRegistry.__init__`` wiring."""

    def test_stores_repository_reference(self, mock_repository: MagicMock) -> None:
        registry = OpenCodeSessionRegistry(mock_repository)
        assert registry._repository is mock_repository

    def test_starts_with_empty_managers_map(self, mock_repository: MagicMock) -> None:
        registry = OpenCodeSessionRegistry(mock_repository)
        assert registry._managers == {}

    def test_on_state_change_defaults_to_none(self, mock_repository: MagicMock) -> None:
        registry = OpenCodeSessionRegistry(mock_repository)
        assert registry._on_state_change is None

    def test_stores_on_state_change_callback(self, mock_repository: MagicMock) -> None:
        callback = AsyncMock()
        registry = OpenCodeSessionRegistry(mock_repository, on_state_change=callback)
        assert registry._on_state_change is callback


# =============================================================================
# get_manager — in-memory accessor
# =============================================================================


class TestGetManager:
    """``get_manager(session_id)`` — lock-protected map lookup."""

    @pytest.mark.asyncio
    async def test_returns_none_when_no_manager_registered(
        self, registry: OpenCodeSessionRegistry,
    ) -> None:
        assert await registry.get_manager("missing-id") is None

    @pytest.mark.asyncio
    async def test_returns_registered_manager(
        self, registry: OpenCodeSessionRegistry,
    ) -> None:
        manager = MagicMock(name="the-manager")
        registry._managers["sess-1"] = manager

        assert await registry.get_manager("sess-1") is manager

    @pytest.mark.asyncio
    async def test_returns_none_for_unregistered_id_even_when_others_exist(
        self, registry: OpenCodeSessionRegistry,
    ) -> None:
        registry._managers["sess-1"] = MagicMock()
        assert await registry.get_manager("sess-other") is None


# =============================================================================
# get_session_record — delegate for repository.get()
# =============================================================================


class TestGetSessionRecord:
    """``get_session_record(project, session_name)`` — public delegate."""

    @pytest.mark.asyncio
    async def test_returns_record_for_existing_session(
        self, registry: OpenCodeSessionRegistry, mock_repository: MagicMock,
    ) -> None:
        record = {"id": "sess-1", "state": "IDLE"}
        mock_repository.get.return_value = record

        result = await registry.get_session_record("myapp", "feature-1")

        assert result == record
        mock_repository.get.assert_called_once_with("myapp", "feature-1")

    @pytest.mark.asyncio
    async def test_returns_none_for_missing_session(
        self, registry: OpenCodeSessionRegistry, mock_repository: MagicMock,
    ) -> None:
        mock_repository.get.return_value = None

        result = await registry.get_session_record("nope", "missing")

        assert result is None

    @pytest.mark.asyncio
    async def test_passes_composite_key_to_repository(
        self, registry: OpenCodeSessionRegistry, mock_repository: MagicMock,
    ) -> None:
        await registry.get_session_record("project-a", "session-b")

        mock_repository.get.assert_called_once_with("project-a", "session-b")


# =============================================================================
# list_sessions — delegate for repository.list()
# =============================================================================


class TestListSessions:
    """``list_sessions()`` — delegate for ``repository.list()``."""

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_sessions(
        self, registry: OpenCodeSessionRegistry, mock_repository: MagicMock,
    ) -> None:
        mock_repository.list.return_value = []

        result = await registry.list_sessions()

        assert result == []

    @pytest.mark.asyncio
    async def test_returns_all_sessions_from_repository(
        self, registry: OpenCodeSessionRegistry, mock_repository: MagicMock,
    ) -> None:
        sessions = [
            {"project": "p1", "session_name": "s1", "id": "id-1"},
            {"project": "p2", "session_name": "s2", "id": "id-2"},
        ]
        mock_repository.list.return_value = sessions

        result = await registry.list_sessions()

        assert result == sessions

    @pytest.mark.asyncio
    async def test_delegates_directly_to_repository_list(
        self, registry: OpenCodeSessionRegistry, mock_repository: MagicMock,
    ) -> None:
        await registry.list_sessions()

        mock_repository.list.assert_called_once_with()


# =============================================================================
# find_by_id — delegate for repository.find_by_id()
# =============================================================================


class TestFindById:
    """``find_by_id(session_id)`` — delegate for ``repository.find_by_id()``."""

    @pytest.mark.asyncio
    async def test_returns_record_for_known_id(
        self, registry: OpenCodeSessionRegistry, mock_repository: MagicMock,
    ) -> None:
        record = {"project": "p", "session_name": "s", "id": "sess-1"}
        mock_repository.find_by_id.return_value = record

        result = await registry.find_by_id("sess-1")

        assert result == record
        mock_repository.find_by_id.assert_called_once_with("sess-1")

    @pytest.mark.asyncio
    async def test_returns_none_for_unknown_id(
        self, registry: OpenCodeSessionRegistry, mock_repository: MagicMock,
    ) -> None:
        mock_repository.find_by_id.return_value = None

        assert await registry.find_by_id("nope") is None


# =============================================================================
# handle_start_work — /start-work lock
# =============================================================================


class TestHandleStartWork:
    """``handle_start_work(project, session_name, agent="atlas")``.

    Ports ``server.go`` lines 436-444: locks the session's agent.
    """

    @pytest.mark.asyncio
    async def test_locks_agent_to_atlas_by_default(
        self, registry: OpenCodeSessionRegistry, mock_repository: MagicMock,
    ) -> None:
        await registry.handle_start_work("myapp", "feature-1")

        mock_repository.update_agent_state.assert_called_once_with(
            project="myapp",
            session_name="feature-1",
            last_agent="atlas",
            is_locked=True,
        )

    @pytest.mark.asyncio
    async def test_locks_to_custom_agent_when_specified(
        self, registry: OpenCodeSessionRegistry, mock_repository: MagicMock,
    ) -> None:
        await registry.handle_start_work("myapp", "feature-1", agent="custom-agent")

        mock_repository.update_agent_state.assert_called_once_with(
            project="myapp",
            session_name="feature-1",
            last_agent="custom-agent",
            is_locked=True,
        )

    @pytest.mark.asyncio
    async def test_sets_is_locked_true(
        self, registry: OpenCodeSessionRegistry, mock_repository: MagicMock,
    ) -> None:
        await registry.handle_start_work("p", "s")

        _, kwargs = mock_repository.update_agent_state.call_args
        assert kwargs["is_locked"] is True

    @pytest.mark.asyncio
    async def test_does_not_raise_when_session_missing(
        self, registry: OpenCodeSessionRegistry, mock_repository: MagicMock,
    ) -> None:
        """A missing row surfaces as ``KeyError``; the handler must swallow it."""
        mock_repository.update_agent_state.side_effect = KeyError("not found")

        # Should NOT raise — the KeyError is caught and logged.
        await registry.handle_start_work("nope", "nope")


# =============================================================================
# create_new — INIT_SESSION handler
# =============================================================================


class TestCreateNew:
    """``create_new(project, session_name, working_dir)``.

    Ports the ``INIT_SESSION`` block in ``server.go`` lines 298-336.

    Sequence when a session already exists:
        1. best-effort abort the old remote session
        2. delete the old row from the repository
        3. create a new remote session via the OpenCode HTTP API
        4. persist the new ``(project, session_name, id, working_dir)``
        5. load a new ``OpenCodeSessionManager`` into memory
    """

    @pytest.mark.asyncio
    async def test_creates_session_when_none_exists(
        self,
        registry: OpenCodeSessionRegistry,
        mock_repository: MagicMock,
        patched_client: AsyncMock,
    ) -> None:
        mock_repository.get.return_value = None
        registry._load_manager_into_memory = AsyncMock()

        result = await registry.create_new("myapp", "feature-1", "/work/dir")

        assert result == "new-session-id"
        patched_client.create_session.assert_awaited_once_with("feature-1")
        mock_repository.create.assert_called_once_with(
            "myapp", "feature-1", "new-session-id", "/work/dir",
        )

    @pytest.mark.asyncio
    async def test_returns_new_session_id(
        self,
        registry: OpenCodeSessionRegistry,
        mock_repository: MagicMock,
        patched_client: AsyncMock,
    ) -> None:
        mock_repository.get.return_value = None
        registry._load_manager_into_memory = AsyncMock()
        patched_client.create_session.return_value = "fresh-id-42"

        result = await registry.create_new("p", "s", "/d")

        assert result == "fresh-id-42"

    @pytest.mark.asyncio
    async def test_aborts_old_session_when_exists(
        self,
        registry: OpenCodeSessionRegistry,
        mock_repository: MagicMock,
        patched_client: AsyncMock,
    ) -> None:
        mock_repository.get.return_value = {
            "id": "old-id",
            "working_dir": "/old/dir",
        }
        registry._load_manager_into_memory = AsyncMock()

        await registry.create_new("myapp", "feature-1", "/new/dir")

        patched_client.abort_session.assert_awaited_once_with("old-id")

    @pytest.mark.asyncio
    async def test_deletes_old_record_when_exists(
        self,
        registry: OpenCodeSessionRegistry,
        mock_repository: MagicMock,
        patched_client: AsyncMock,
    ) -> None:
        mock_repository.get.return_value = {
            "id": "old-id",
            "working_dir": "/old/dir",
        }
        registry._load_manager_into_memory = AsyncMock()

        await registry.create_new("myapp", "feature-1", "/new/dir")

        mock_repository.delete.assert_called_once_with("myapp", "feature-1")

    @pytest.mark.asyncio
    async def test_creates_new_session_after_cleanup(
        self,
        registry: OpenCodeSessionRegistry,
        mock_repository: MagicMock,
        patched_client: AsyncMock,
    ) -> None:
        mock_repository.get.return_value = {
            "id": "old-id",
            "working_dir": "/old/dir",
        }
        registry._load_manager_into_memory = AsyncMock()

        await registry.create_new("myapp", "feature-1", "/new/dir")

        patched_client.create_session.assert_awaited_once_with("feature-1")
        mock_repository.create.assert_called_once_with(
            "myapp", "feature-1", "new-session-id", "/new/dir",
        )

    @pytest.mark.asyncio
    async def test_tolerates_abort_failure_and_still_creates(
        self,
        registry: OpenCodeSessionRegistry,
        mock_repository: MagicMock,
        patched_client: AsyncMock,
    ) -> None:
        """A failing abort is best-effort: creation must still proceed."""
        mock_repository.get.return_value = {
            "id": "old-id",
            "working_dir": "/old/dir",
        }
        patched_client.abort_session.side_effect = RuntimeError("server down")
        registry._load_manager_into_memory = AsyncMock()

        result = await registry.create_new("myapp", "feature-1", "/new/dir")

        assert result == "new-session-id"
        mock_repository.delete.assert_called_once()
        patched_client.create_session.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_deletes_old_record_even_without_id_or_working_dir(
        self,
        registry: OpenCodeSessionRegistry,
        mock_repository: MagicMock,
        patched_client: AsyncMock,
    ) -> None:
        """When the old record lacks id/working_dir, abort is skipped but
        the delete still runs (so the stale row is purged)."""
        mock_repository.get.return_value = {"id": None, "working_dir": None}
        registry._load_manager_into_memory = AsyncMock()

        await registry.create_new("myapp", "feature-1", "/work/dir")

        patched_client.abort_session.assert_not_awaited()
        mock_repository.delete.assert_called_once_with("myapp", "feature-1")

    @pytest.mark.asyncio
    async def test_closes_abort_client_in_finally_block(
        self,
        registry: OpenCodeSessionRegistry,
        mock_repository: MagicMock,
        patched_client: AsyncMock,
    ) -> None:
        mock_repository.get.return_value = {
            "id": "old-id",
            "working_dir": "/old/dir",
        }
        registry._load_manager_into_memory = AsyncMock()

        await registry.create_new("myapp", "feature-1", "/new/dir")

        # aclose is called for both the abort client and the create client
        assert patched_client.aclose.await_count >= 2

    @pytest.mark.asyncio
    async def test_raises_runtime_error_when_create_session_fails(
        self,
        registry: OpenCodeSessionRegistry,
        mock_repository: MagicMock,
        patched_client: AsyncMock,
    ) -> None:
        mock_repository.get.return_value = None
        patched_client.create_session.side_effect = RuntimeError("network error")
        registry._load_manager_into_memory = AsyncMock()

        with pytest.raises(RuntimeError, match="Failed to create session"):
            await registry.create_new("myapp", "feature-1", "/work/dir")

        # The create client is closed even on failure
        patched_client.aclose.assert_awaited()
        # And the row is never persisted
        mock_repository.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_loads_new_manager_into_memory(
        self,
        registry: OpenCodeSessionRegistry,
        mock_repository: MagicMock,
        patched_client: AsyncMock,
    ) -> None:
        mock_repository.get.return_value = None
        registry._load_manager_into_memory = AsyncMock()

        await registry.create_new("myapp", "feature-1", "/work/dir")

        registry._load_manager_into_memory.assert_awaited_once()
        kwargs = registry._load_manager_into_memory.call_args.kwargs
        assert kwargs["project"] == "myapp"
        assert kwargs["session_name"] == "feature-1"
        assert kwargs["session_id"] == "new-session-id"
        assert kwargs["working_dir"] == "/work/dir"

    @pytest.mark.asyncio
    async def test_does_not_load_manager_when_persist_fails(
        self,
        registry: OpenCodeSessionRegistry,
        mock_repository: MagicMock,
        patched_client: AsyncMock,
    ) -> None:
        mock_repository.get.return_value = None
        mock_repository.create.side_effect = RuntimeError("db locked")
        registry._load_manager_into_memory = AsyncMock()

        with pytest.raises(RuntimeError, match="Failed to save session"):
            await registry.create_new("myapp", "feature-1", "/work/dir")

        registry._load_manager_into_memory.assert_not_awaited()


# =============================================================================
# abort_session — 3-second settle delay
# =============================================================================


class TestAbortSessionSettleDelay:
    """The 3-second ``ABORT_REMOTE_SETTLE_S`` settle after a remote abort.

    Ports ``server.go`` line 359: ``time.Sleep(3 * time.Second)``. The
    delay only fires when the remote abort succeeds — on failure, the
    local reset proceeds immediately.
    """

    def test_abort_settle_constant_is_three_seconds(self) -> None:
        assert ABORT_REMOTE_SETTLE_S == 3.0

    @pytest.mark.asyncio
    async def test_sleeps_settle_seconds_after_successful_remote_abort(
        self,
        registry: OpenCodeSessionRegistry,
        mock_repository: MagicMock,
        patched_client: AsyncMock,
    ) -> None:
        mock_repository.get.return_value = {
            "id": "sess-1",
            "working_dir": "/dir",
        }

        with patch(
            "daemon.opencode.registry.asyncio.sleep", new_callable=AsyncMock,
        ) as mock_sleep:
            await registry.abort_session("myapp", "feature-1")

        mock_sleep.assert_awaited_once_with(ABORT_REMOTE_SETTLE_S)

    @pytest.mark.asyncio
    async def test_does_not_sleep_when_remote_abort_fails(
        self,
        registry: OpenCodeSessionRegistry,
        mock_repository: MagicMock,
        patched_client: AsyncMock,
    ) -> None:
        mock_repository.get.return_value = {
            "id": "sess-1",
            "working_dir": "/dir",
        }
        patched_client.abort_session.side_effect = RuntimeError("unreachable")

        with patch(
            "daemon.opencode.registry.asyncio.sleep", new_callable=AsyncMock,
        ) as mock_sleep:
            await registry.abort_session("myapp", "feature-1")

        mock_sleep.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_sleeps_settle_when_working_dir_missing(
        self,
        registry: OpenCodeSessionRegistry,
        mock_repository: MagicMock,
        patched_client: AsyncMock,
    ) -> None:
        """No working_dir → no remote abort attempt, but ``abort_err``
        stays ``None`` (the abort block was never entered), so the
        settle delay still fires before the local reset."""
        mock_repository.get.return_value = {
            "id": "sess-1",
            "working_dir": None,
        }

        with patch(
            "daemon.opencode.registry.asyncio.sleep", new_callable=AsyncMock,
        ) as mock_sleep:
            await registry.abort_session("myapp", "feature-1")

        # The remote abort is skipped (no working_dir)…
        patched_client.abort_session.assert_not_awaited()
        # …but the settle delay still runs because no abort error was raised.
        mock_sleep.assert_awaited_once_with(ABORT_REMOTE_SETTLE_S)


# =============================================================================
# abort_session — full behavior
# =============================================================================


class TestAbortSession:
    """``abort_session(project, session_name)`` — ABORT_SESSION handler.

    Ports ``server.go`` lines 338-374.
    """

    @pytest.mark.asyncio
    async def test_returns_error_when_session_not_found(
        self, registry: OpenCodeSessionRegistry, mock_repository: MagicMock,
    ) -> None:
        mock_repository.get.return_value = None

        result = await registry.abort_session("myapp", "missing")

        assert result["status"] == "error"
        assert "not found" in result["message"]

    @pytest.mark.asyncio
    async def test_returns_error_when_session_has_no_id(
        self, registry: OpenCodeSessionRegistry, mock_repository: MagicMock,
    ) -> None:
        mock_repository.get.return_value = {"id": None, "working_dir": "/dir"}

        result = await registry.abort_session("myapp", "feature-1")

        assert result["status"] == "error"
        assert "no id" in result["message"]

    @pytest.mark.asyncio
    async def test_calls_remote_abort_with_session_id(
        self,
        registry: OpenCodeSessionRegistry,
        mock_repository: MagicMock,
        patched_client: AsyncMock,
    ) -> None:
        mock_repository.get.return_value = {
            "id": "sess-42",
            "working_dir": "/dir",
        }

        with patch("daemon.opencode.registry.asyncio.sleep", new_callable=AsyncMock):
            await registry.abort_session("myapp", "feature-1")

        patched_client.abort_session.assert_awaited_once_with("sess-42")

    @pytest.mark.asyncio
    async def test_closes_client_after_abort(
        self,
        registry: OpenCodeSessionRegistry,
        mock_repository: MagicMock,
        patched_client: AsyncMock,
    ) -> None:
        mock_repository.get.return_value = {
            "id": "sess-1",
            "working_dir": "/dir",
        }

        with patch("daemon.opencode.registry.asyncio.sleep", new_callable=AsyncMock):
            await registry.abort_session("myapp", "feature-1")

        patched_client.aclose.assert_awaited()

    @pytest.mark.asyncio
    async def test_resets_local_manager_state(
        self,
        registry: OpenCodeSessionRegistry,
        mock_repository: MagicMock,
        patched_client: AsyncMock,
    ) -> None:
        mock_repository.get.return_value = {
            "id": "sess-1",
            "working_dir": "/dir",
        }
        mock_manager = AsyncMock()
        registry._managers["sess-1"] = mock_manager

        with patch("daemon.opencode.registry.asyncio.sleep", new_callable=AsyncMock):
            await registry.abort_session("myapp", "feature-1")

        mock_manager.abort_task.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_returns_ok_when_remote_abort_succeeds(
        self,
        registry: OpenCodeSessionRegistry,
        mock_repository: MagicMock,
        patched_client: AsyncMock,
    ) -> None:
        mock_repository.get.return_value = {
            "id": "sess-1",
            "working_dir": "/dir",
        }

        with patch("daemon.opencode.registry.asyncio.sleep", new_callable=AsyncMock):
            result = await registry.abort_session("myapp", "feature-1")

        assert result["status"] == "ok"
        assert "ready for new input" in result["message"]

    @pytest.mark.asyncio
    async def test_returns_partial_ok_when_remote_abort_fails(
        self,
        registry: OpenCodeSessionRegistry,
        mock_repository: MagicMock,
        patched_client: AsyncMock,
    ) -> None:
        mock_repository.get.return_value = {
            "id": "sess-1",
            "working_dir": "/dir",
        }
        patched_client.abort_session.side_effect = RuntimeError("server gone")

        with patch("daemon.opencode.registry.asyncio.sleep", new_callable=AsyncMock):
            result = await registry.abort_session("myapp", "feature-1")

        assert result["status"] == "ok"
        assert "remote abort failed" in result["message"]

    @pytest.mark.asyncio
    async def test_skips_remote_abort_when_no_working_dir(
        self,
        registry: OpenCodeSessionRegistry,
        mock_repository: MagicMock,
        patched_client: AsyncMock,
    ) -> None:
        mock_repository.get.return_value = {
            "id": "sess-1",
            "working_dir": None,
        }

        with patch("daemon.opencode.registry.asyncio.sleep", new_callable=AsyncMock):
            result = await registry.abort_session("myapp", "feature-1")

        patched_client.abort_session.assert_not_awaited()
        # No working_dir means abort_err stays None → ok message
        assert result["status"] == "ok"


# =============================================================================
# recover_from_registry — crash recovery
# =============================================================================


class TestRecoverFromRegistry:
    """``recover_from_registry()`` — loads every persisted session on startup.

    Ports ``Server.Start`` lines 115-144.
    """

    @pytest.mark.asyncio
    async def test_returns_zero_when_no_sessions(
        self, registry: OpenCodeSessionRegistry, mock_repository: MagicMock,
    ) -> None:
        mock_repository.list.return_value = []
        registry._load_manager_into_memory = AsyncMock()

        count = await registry.recover_from_registry()

        assert count == 0
        registry._load_manager_into_memory.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_loads_all_persisted_sessions(
        self, registry: OpenCodeSessionRegistry, mock_repository: MagicMock,
    ) -> None:
        mock_repository.list.return_value = [
            {"project": "p1", "session_name": "s1", "id": "id-1", "working_dir": "/a"},
            {"project": "p2", "session_name": "s2", "id": "id-2", "working_dir": "/b"},
        ]
        registry._load_manager_into_memory = AsyncMock()

        count = await registry.recover_from_registry()

        assert count == 2
        assert registry._load_manager_into_memory.await_count == 2

    @pytest.mark.asyncio
    async def test_passes_record_fields_to_load_manager(
        self, registry: OpenCodeSessionRegistry, mock_repository: MagicMock,
    ) -> None:
        mock_repository.list.return_value = [
            {
                "project": "p1",
                "session_name": "s1",
                "id": "id-1",
                "working_dir": "/work/a",
            },
        ]
        registry._load_manager_into_memory = AsyncMock()

        await registry.recover_from_registry()

        kwargs = registry._load_manager_into_memory.call_args.kwargs
        assert kwargs == {
            "project": "p1",
            "session_name": "s1",
            "session_id": "id-1",
            "working_dir": "/work/a",
        }

    @pytest.mark.asyncio
    async def test_skips_records_without_id(
        self, registry: OpenCodeSessionRegistry, mock_repository: MagicMock,
    ) -> None:
        mock_repository.list.return_value = [
            {"project": "p1", "session_name": "s1", "id": None, "working_dir": "/a"},
            {"project": "p2", "session_name": "s2", "id": "id-2", "working_dir": "/b"},
        ]
        registry._load_manager_into_memory = AsyncMock()

        count = await registry.recover_from_registry()

        # The return value is the total record count (matches the Go
        # ``Recovered N session(s)`` log line), even though one was skipped.
        assert count == 2
        # But only one manager was actually loaded.
        assert registry._load_manager_into_memory.await_count == 1
        kwargs = registry._load_manager_into_memory.call_args.kwargs
        assert kwargs["session_id"] == "id-2"

    @pytest.mark.asyncio
    async def test_continues_when_one_session_fails_to_load(
        self, registry: OpenCodeSessionRegistry, mock_repository: MagicMock,
    ) -> None:
        """A single load failure must not abort recovery of the rest."""
        mock_repository.list.return_value = [
            {"project": "p1", "session_name": "s1", "id": "id-1", "working_dir": "/a"},
            {"project": "p2", "session_name": "s2", "id": "id-2", "working_dir": "/b"},
        ]
        registry._load_manager_into_memory = AsyncMock(
            side_effect=[RuntimeError("boom"), None],
        )

        # Must not raise.
        count = await registry.recover_from_registry()

        assert count == 2
        assert registry._load_manager_into_memory.await_count == 2


# =============================================================================
# load_session_into_memory — lazy creation + dedup
# =============================================================================


class TestLoadSessionIntoMemory:
    """``load_session_into_memory(session_id)`` — lazy manager creation.

    Ports the body of the ``GET_SESSION`` handler (``server.go`` 384-408).
    """

    @pytest.mark.asyncio
    async def test_returns_none_for_unknown_session_id(
        self, registry: OpenCodeSessionRegistry, mock_repository: MagicMock,
    ) -> None:
        mock_repository.find_by_id.return_value = None

        result = await registry.load_session_into_memory("unknown-id")

        assert result is None

    @pytest.mark.asyncio
    async def test_loads_manager_for_known_session_id(
        self, registry: OpenCodeSessionRegistry, mock_repository: MagicMock,
    ) -> None:
        mock_repository.find_by_id.return_value = {
            "project": "p1",
            "session_name": "s1",
            "id": "id-1",
            "working_dir": "/work/a",
        }
        mock_manager = MagicMock()
        with patch(
            "daemon.opencode.registry.OpenCodeSessionManager",
            return_value=mock_manager,
        ):
            result = await registry.load_session_into_memory("id-1")

        assert result is mock_manager
        # The manager's background loop is started.
        mock_manager.start.assert_called_once()
        # And it is registered in the in-memory map.
        stored = await registry.get_manager("id-1")
        assert stored is mock_manager

    @pytest.mark.asyncio
    async def test_does_not_replace_existing_manager_on_reload(
        self, registry: OpenCodeSessionRegistry, mock_repository: MagicMock,
    ) -> None:
        """The ``if session_id not in self._managers`` guard keeps the
        first-registered manager even when ``load_session_into_memory``
        is called a second time for the same id."""
        mock_repository.find_by_id.return_value = {
            "project": "p1",
            "session_name": "s1",
            "id": "id-1",
            "working_dir": "/work/a",
        }
        manager_first = MagicMock(name="first")
        manager_second = MagicMock(name="second")
        with patch(
            "daemon.opencode.registry.OpenCodeSessionManager",
            side_effect=[manager_first, manager_second],
        ):
            await registry.load_session_into_memory("id-1")
            await registry.load_session_into_memory("id-1")

        # The registry keeps the FIRST manager — the dedup guard prevents
        # the second load from overwriting it.
        stored = await registry.get_manager("id-1")
        assert stored is manager_first


# =============================================================================
# Resource-leak fix: abort_session evicts in-memory manager but keeps DB row
# =============================================================================


class TestAbortSessionResourceLeak:
    """Resource-leak fixes for ``abort_session``.

    The fix splits the responsibility cleanly:
    - The in-memory manager is **popped** from ``_managers`` and ``stop()`` is
      awaited (so the background loop and any in-flight HTTP client are torn
      down). This prevents the "ghost manager" leak where a stopped manager
      lingers in the dict indefinitely.
    - The repository row is **left intact** so the session can be reloaded
      on demand via ``load_session_into_memory``. Abort is "reset to IDLE,
      ready for new input", not "destroy completely".
    """

    @pytest.mark.asyncio
    async def test_abort_session_removes_manager_from_memory(
        self,
        registry: OpenCodeSessionRegistry,
        mock_repository: MagicMock,
        patched_client: AsyncMock,
    ) -> None:
        """After ``abort_session``, the manager is no longer in ``_managers``."""
        mock_repository.get.return_value = {
            "id": "sess-1",
            "working_dir": "/dir",
        }
        mock_manager = AsyncMock()
        registry._managers["sess-1"] = mock_manager

        with patch("daemon.opencode.registry.asyncio.sleep", new_callable=AsyncMock):
            await registry.abort_session("myapp", "feature-1")

        assert "sess-1" not in registry._managers
        # get_manager must now return None for the evicted session.
        assert await registry.get_manager("sess-1") is None

    @pytest.mark.asyncio
    async def test_abort_session_keeps_db_row(
        self,
        registry: OpenCodeSessionRegistry,
        mock_repository: MagicMock,
        patched_client: AsyncMock,
    ) -> None:
        """After ``abort_session``, the repository row is NOT deleted.

        This is the contract that lets the session be reloaded on demand
        via ``load_session_into_memory`` once the caller references it
        again. The DB row is the source of truth for "session exists";
        the in-memory manager is just a hot cache.
        """
        mock_repository.get.return_value = {
            "id": "sess-1",
            "working_dir": "/dir",
        }

        with patch("daemon.opencode.registry.asyncio.sleep", new_callable=AsyncMock):
            await registry.abort_session("myapp", "feature-1")

        # Abort must NOT touch the repository — the row survives.
        mock_repository.delete.assert_not_called()


# =============================================================================
# Resource-leak fix: create_new stops the old in-memory manager
# =============================================================================


class TestCreateNewStopsOldManager:
    """Resource-leak fix for ``create_new``.

    When a session is being replaced (same ``(project, session_name)`` already
    exists), the registry now pops and ``stop()``s the old in-memory manager
    BEFORE aborting the remote session. This prevents concurrent PROMPT
    handlers from picking up a half-torn-down manager reference.
    """

    @pytest.mark.asyncio
    async def test_create_new_stops_old_manager(
        self,
        registry: OpenCodeSessionRegistry,
        mock_repository: MagicMock,
        patched_client: AsyncMock,
    ) -> None:
        """``create_new`` awaits ``old_manager.stop()`` when replacing a session."""
        mock_repository.get.return_value = {
            "id": "old-id",
            "working_dir": "/old/dir",
        }
        old_manager = AsyncMock()
        registry._managers["old-id"] = old_manager
        # Default _load_manager_into_memory is the AsyncMock registered on
        # the test fixture; override it for this test to keep things simple.
        registry._load_manager_into_memory = AsyncMock()

        await registry.create_new("myapp", "feature-1", "/new/dir")

        old_manager.stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_create_new_pops_old_manager_from_memory(
        self,
        registry: OpenCodeSessionRegistry,
        mock_repository: MagicMock,
        patched_client: AsyncMock,
    ) -> None:
        """The old manager is removed from ``_managers`` after ``stop()``."""
        mock_repository.get.return_value = {
            "id": "old-id",
            "working_dir": "/old/dir",
        }
        old_manager = AsyncMock()
        registry._managers["old-id"] = old_manager
        registry._load_manager_into_memory = AsyncMock()

        await registry.create_new("myapp", "feature-1", "/new/dir")

        # The old manager must be gone from the in-memory map (popped under
        # the lock so concurrent callers can't grab the dying reference).
        assert "old-id" not in registry._managers

    @pytest.mark.asyncio
    async def test_create_new_tolerates_old_manager_stop_failure(
        self,
        registry: OpenCodeSessionRegistry,
        mock_repository: MagicMock,
        patched_client: AsyncMock,
    ) -> None:
        """A failing ``old_manager.stop()`` is logged but does NOT block creation."""
        mock_repository.get.return_value = {
            "id": "old-id",
            "working_dir": "/old/dir",
        }
        old_manager = AsyncMock()
        old_manager.stop.side_effect = RuntimeError("loop wedged")
        registry._managers["old-id"] = old_manager
        registry._load_manager_into_memory = AsyncMock()

        # Must not raise — the new session is created successfully.
        result = await registry.create_new("myapp", "feature-1", "/new/dir")

        assert result == "new-session-id"
        mock_repository.create.assert_called_once()


# =============================================================================
# Resource-leak fix: evict_idle_sessions — TTL eviction
# =============================================================================


class TestEvictIdleSessions:
    """``evict_idle_sessions(ttl_seconds)`` — TTL-based cleanup.

    The fix: ``evict_idle_sessions`` now uses the public ``last_activity``
    property and properly calls ``manager.stop()`` BEFORE popping the entry
    from ``_managers``. Tests below verify both branches of the
    ``(now - last).total_seconds() > ttl_seconds`` decision.
    """

    @pytest.mark.asyncio
    async def test_evict_idle_sessions_removes_expired(
        self, registry: OpenCodeSessionRegistry,
    ) -> None:
        """Managers idle > TTL are evicted from ``_managers`` and ``stop()``-ed."""
        from datetime import datetime, timedelta, timezone

        mock_manager = AsyncMock()
        # last_activity 2 hours ago — well past the default 1h TTL.
        mock_manager.last_activity = datetime.now(timezone.utc) - timedelta(hours=2)
        registry._managers["sess-1"] = mock_manager

        evicted = await registry.evict_idle_sessions(ttl_seconds=3600)

        assert evicted == 1
        assert "sess-1" not in registry._managers
        mock_manager.stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_evict_idle_sessions_keeps_active(
        self, registry: OpenCodeSessionRegistry,
    ) -> None:
        """Managers with recent activity are NOT evicted."""
        from datetime import datetime, timezone

        mock_manager = AsyncMock()
        # last_activity "now" — far below the 1h TTL threshold.
        mock_manager.last_activity = datetime.now(timezone.utc)
        registry._managers["sess-1"] = mock_manager

        evicted = await registry.evict_idle_sessions(ttl_seconds=3600)

        assert evicted == 0
        assert "sess-1" in registry._managers
        mock_manager.stop.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_evict_idle_sessions_tolerates_stop_failure(
        self, registry: OpenCodeSessionRegistry,
    ) -> None:
        """A failing ``manager.stop()`` is logged but the manager is still evicted.

        The eviction is best-effort: one bad manager must not block cleanup
        of the rest. The manager is removed from ``_managers`` even when
        ``stop()`` raises.
        """
        from datetime import datetime, timedelta, timezone

        mock_manager = AsyncMock()
        mock_manager.last_activity = datetime.now(timezone.utc) - timedelta(hours=2)
        mock_manager.stop.side_effect = RuntimeError("loop wedged")
        registry._managers["sess-1"] = mock_manager

        # Must NOT raise — the stop() error is logged and eviction proceeds.
        evicted = await registry.evict_idle_sessions(ttl_seconds=3600)

        assert evicted == 1
        assert "sess-1" not in registry._managers
