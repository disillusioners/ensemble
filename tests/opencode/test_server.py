"""Comprehensive unit tests for ``daemon.opencode.server``.

Tests ``external_opencode_send_message()`` — the public dispatcher that
handles every OpenCode action (PING, INIT_SESSION, ABORT_SESSION, PROMPT,
COMMAND, ANSWER, RESUME, LIST_SESSIONS, GET_SESSION, GET_STATUS).

Coverage:
    - Helper functions: ``_normalize_text``, ``_is_special_prompt``,
      ``_extract_target_text``
    - All 10 actions dispatched by ``external_opencode_send_message``
    - Special-prompt detection and ``/start-work`` lock
    - BUSY rejection with special-prompt bypass
    - Agent-lock override on PROMPT/COMMAND
    - ``continue``/``retry`` → RESUME routing
    - Payload validation errors
    - Missing session_id and session-not-found paths
    - Unknown action → error

Every test mocks the registry and manager so no real I/O occurs.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from daemon.opencode.server import (
    OpenCodeRequest,
    OpenCodeResponse,
    _extract_target_text,
    _is_special_prompt,
    _normalize_text,
    external_opencode_send_message,
)
from daemon.opencode.session_manager import Request as ManagerRequest


# ─────────────────────────────────────────────────────────────────────────────
# Helper: _normalize_text
# ─────────────────────────────────────────────────────────────────────────────


class TestNormalizeText:
    """Unit tests for the strip-and-lowercase helper."""

    def test_strips_leading_slash(self):
        assert _normalize_text("/start-work") == "start-work"

    def test_lowercases(self):
        assert _normalize_text("/INIT-SESSION") == "init-session"

    def test_strips_and_lowercases(self):
        assert _normalize_text("/Start-Work") == "start-work"

    def test_no_slash_no_change(self):
        assert _normalize_text("continue") == "continue"

    def test_empty_string(self):
        assert _normalize_text("") == ""


# ─────────────────────────────────────────────────────────────────────────────
# Helper: _is_special_prompt
# ─────────────────────────────────────────────────────────────────────────────


class TestIsSpecialPrompt:
    """Unit tests for the BUSY-bypass detection helper."""

    def test_start_work_is_special(self):
        assert _is_special_prompt("start-work") is True

    def test_continue_is_special(self):
        assert _is_special_prompt("continue") is True

    def test_abort_is_special(self):
        assert _is_special_prompt("abort") is True

    def test_retry_is_special(self):
        assert _is_special_prompt("retry") is True

    def test_status_is_not_special(self):
        assert _is_special_prompt("status") is False

    def test_wait_is_not_special(self):
        assert _is_special_prompt("wait") is False

    def test_arbitrary_prompt_not_special(self):
        assert _is_special_prompt("hello world") is False

    def test_empty_string_not_special(self):
        assert _is_special_prompt("") is False


# ─────────────────────────────────────────────────────────────────────────────
# Helper: _extract_target_text
# ─────────────────────────────────────────────────────────────────────────────


class TestExtractTargetText:
    """Unit tests for extracting the text/command string from action payloads."""

    def test_extracts_text_from_prompt_parts(self):
        payload = {"parts": [{"type": "text", "text": "hello"}]}
        assert _extract_target_text("PROMPT", payload) == "hello"

    def test_extracts_first_part_from_prompt_parts(self):
        payload = {"parts": [{"type": "text", "text": "first"}, {"type": "text", "text": "second"}]}
        assert _extract_target_text("PROMPT", payload) == "first"

    def test_returns_empty_for_missing_parts(self):
        assert _extract_target_text("PROMPT", {}) == ""

    def test_returns_empty_for_empty_parts(self):
        assert _extract_target_text("PROMPT", {"parts": []}) == ""

    def test_returns_empty_for_non_list_parts(self):
        assert _extract_target_text("PROMPT", {"parts": "not a list"}) == ""

    def test_returns_empty_for_missing_text_in_part(self):
        assert _extract_target_text("PROMPT", {"parts": [{"type": "text"}]}) == ""

    def test_extracts_command_from_command_action(self):
        payload = {"command": "/status"}
        assert _extract_target_text("COMMAND", payload) == "/status"

    def test_returns_empty_for_missing_command(self):
        assert _extract_target_text("COMMAND", {}) == ""

    def test_returns_empty_for_non_string_command(self):
        assert _extract_target_text("COMMAND", {"command": 123}) == ""

    def test_returns_empty_for_other_action(self):
        assert _extract_target_text("RESUME", {}) == ""


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_registry() -> AsyncMock:
    """An AsyncMock standing in for ``OpenCodeSessionRegistry``."""
    registry = AsyncMock()
    registry.get_manager = AsyncMock(return_value=None)
    registry.find_by_id = AsyncMock(return_value=None)
    registry.list_sessions = AsyncMock(return_value=[])
    registry.create_new = AsyncMock(return_value="new-session-id")
    registry.abort_session = AsyncMock(return_value={"status": "ok", "message": "Aborted"})
    registry.load_session_into_memory = AsyncMock(return_value=None)
    registry.handle_start_work = AsyncMock(return_value=None)
    return registry


@pytest.fixture
def mock_manager() -> MagicMock:
    """A MagicMock standing in for ``OpenCodeSessionManager``."""
    manager = MagicMock()
    manager.get_snapshot = MagicMock(return_value={"state": "IDLE"})
    manager.sync_state_with_open_code = AsyncMock(return_value={"state": "IDLE"})
    manager.submit_request = MagicMock()
    return manager


# ─────────────────────────────────────────────────────────────────────────────
# Action: PING
# ─────────────────────────────────────────────────────────────────────────────


class TestPing:
    """``PING`` returns a PONG with a UTC timestamp."""

    @pytest.mark.asyncio
    async def test_returns_ok_with_pong_message(self, mock_registry: AsyncMock):
        req = OpenCodeRequest(action="PING")
        resp = await external_opencode_send_message(req, mock_registry)

        assert resp.status == "ok"
        assert resp.message == "PONG"

    @pytest.mark.asyncio
    async def test_returns_start_time_in_data(self, mock_registry: AsyncMock):
        req = OpenCodeRequest(action="PING")
        resp = await external_opencode_send_message(req, mock_registry)

        assert resp.data is not None
        assert "start_time" in resp.data

    @pytest.mark.asyncio
    async def test_does_not_use_registry(self, mock_registry: AsyncMock):
        """PING is a pure local operation and must not touch the registry."""
        req = OpenCodeRequest(action="PING")
        await external_opencode_send_message(req, mock_registry)
        mock_registry.get_manager.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# Action: LIST_SESSIONS
# ─────────────────────────────────────────────────────────────────────────────


class TestListSessions:
    """``LIST_SESSIONS`` delegates to the registry and returns session records."""

    @pytest.mark.asyncio
    async def test_returns_ok_with_sessions(self, mock_registry: AsyncMock):
        sessions = [{"project": "myapp", "session_name": "s1"}]
        mock_registry.list_sessions = AsyncMock(return_value=sessions)

        req = OpenCodeRequest(action="LIST_SESSIONS")
        resp = await external_opencode_send_message(req, mock_registry)

        assert resp.status == "ok"
        assert resp.data == {"sessions": sessions}

    @pytest.mark.asyncio
    async def test_returns_error_when_registry_raises(self, mock_registry: AsyncMock):
        mock_registry.list_sessions = AsyncMock(side_effect=RuntimeError("db error"))

        req = OpenCodeRequest(action="LIST_SESSIONS")
        resp = await external_opencode_send_message(req, mock_registry)

        assert resp.status == "error"
        assert "Failed to list sessions" in resp.message


# ─────────────────────────────────────────────────────────────────────────────
# Action: GET_SESSION
# ─────────────────────────────────────────────────────────────────────────────


class TestGetSession:
    """``GET_SESSION`` looks up a session by project + session_name."""

    @pytest.mark.asyncio
    async def test_returns_not_found_when_missing(self, mock_registry: AsyncMock):
        mock_registry._repository = MagicMock()
        mock_registry._repository.get = MagicMock(return_value=None)

        req = OpenCodeRequest(
            action="GET_SESSION",
            payload={"project": "myapp", "session_name": "not-there"},
        )
        resp = await external_opencode_send_message(req, mock_registry)

        assert resp.status == "error"
        assert resp.message == "not found"

    @pytest.mark.asyncio
    async def test_returns_error_when_project_missing(self, mock_registry: AsyncMock):
        req = OpenCodeRequest(
            action="GET_SESSION",
            payload={"session_name": "s1"},
        )
        resp = await external_opencode_send_message(req, mock_registry)

        assert resp.status == "error"
        assert "project and session_name are required" in resp.message

    @pytest.mark.asyncio
    async def test_returns_error_when_session_name_missing(self, mock_registry: AsyncMock):
        req = OpenCodeRequest(
            action="GET_SESSION",
            payload={"project": "myapp"},
        )
        resp = await external_opencode_send_message(req, mock_registry)

        assert resp.status == "error"
        assert "project and session_name are required" in resp.message

    @pytest.mark.asyncio
    async def test_returns_ok_with_record(self, mock_registry: AsyncMock):
        record = {"project": "myapp", "session_name": "s1", "id": "sid-123"}
        mock_registry._repository = MagicMock()
        mock_registry._repository.get = MagicMock(return_value=record)

        req = OpenCodeRequest(
            action="GET_SESSION",
            payload={"project": "myapp", "session_name": "s1"},
        )
        resp = await external_opencode_send_message(req, mock_registry)

        assert resp.status == "ok"
        assert resp.data == {"session": record}

    @pytest.mark.asyncio
    async def test_loads_into_memory_when_manager_not_in_memory(self, mock_registry: AsyncMock):
        """When a session record exists but manager is not in memory, lazy-load it."""
        record = {"project": "myapp", "session_name": "s1", "id": "sid-123"}
        mock_registry._repository = MagicMock()
        mock_registry._repository.get = MagicMock(return_value=record)
        mock_registry.get_manager = AsyncMock(return_value=None)
        mock_registry.load_session_into_memory = AsyncMock()

        req = OpenCodeRequest(
            action="GET_SESSION",
            payload={"project": "myapp", "session_name": "s1"},
        )
        await external_opencode_send_message(req, mock_registry)

        mock_registry.load_session_into_memory.assert_awaited_once_with("sid-123")


# ─────────────────────────────────────────────────────────────────────────────
# Action: INIT_SESSION
# ─────────────────────────────────────────────────────────────────────────────


class TestInitSession:
    """``INIT_SESSION`` creates a new session via the registry."""

    @pytest.mark.asyncio
    async def test_returns_ok_with_new_session_id(self, mock_registry: AsyncMock):
        mock_registry.create_new = AsyncMock(return_value="new-sid")

        req = OpenCodeRequest(
            action="INIT_SESSION",
            payload={"project": "myapp", "session_name": "feature-x"},
        )
        resp = await external_opencode_send_message(req, mock_registry)

        assert resp.status == "ok"
        assert resp.session_id == "new-sid"
        mock_registry.create_new.assert_awaited_once_with(
            project="myapp",
            session_name="feature-x",
            working_dir="",
        )

    @pytest.mark.asyncio
    async def test_passes_working_dir_when_provided(self, mock_registry: AsyncMock):
        mock_registry.create_new = AsyncMock(return_value="sid")

        req = OpenCodeRequest(
            action="INIT_SESSION",
            payload={"project": "p", "session_name": "s", "working_dir": "/path"},
        )
        await external_opencode_send_message(req, mock_registry)

        mock_registry.create_new.assert_awaited_once_with(
            project="p",
            session_name="s",
            working_dir="/path",
        )

    @pytest.mark.asyncio
    async def test_returns_error_when_project_missing(self, mock_registry: AsyncMock):
        req = OpenCodeRequest(
            action="INIT_SESSION",
            payload={"session_name": "s"},
        )
        resp = await external_opencode_send_message(req, mock_registry)

        assert resp.status == "error"
        assert "project and session_name are required" in resp.message

    @pytest.mark.asyncio
    async def test_returns_error_when_registry_raises(self, mock_registry: AsyncMock):
        mock_registry.create_new = AsyncMock(side_effect=RuntimeError("create failed"))

        req = OpenCodeRequest(
            action="INIT_SESSION",
            payload={"project": "p", "session_name": "s"},
        )
        resp = await external_opencode_send_message(req, mock_registry)

        assert resp.status == "error"
        assert "create failed" in resp.message


# ─────────────────────────────────────────────────────────────────────────────
# Action: ABORT_SESSION
# ─────────────────────────────────────────────────────────────────────────────


class TestAbortSession:
    """``ABORT_SESSION`` delegates to the registry's abort handler."""

    @pytest.mark.asyncio
    async def test_returns_ok_from_registry(self, mock_registry: AsyncMock):
        mock_registry.abort_session = AsyncMock(
            return_value={"status": "ok", "message": "Aborted"}
        )

        req = OpenCodeRequest(
            action="ABORT_SESSION",
            payload={"project": "myapp", "session_name": "s1"},
        )
        resp = await external_opencode_send_message(req, mock_registry)

        assert resp.status == "ok"
        assert resp.message == "Aborted"

    @pytest.mark.asyncio
    async def test_returns_error_from_registry(self, mock_registry: AsyncMock):
        mock_registry.abort_session = AsyncMock(
            return_value={"status": "error", "message": "Session not found"}
        )

        req = OpenCodeRequest(
            action="ABORT_SESSION",
            payload={"project": "myapp", "session_name": "s1"},
        )
        resp = await external_opencode_send_message(req, mock_registry)

        assert resp.status == "error"

    @pytest.mark.asyncio
    async def test_returns_error_when_missing_fields(self, mock_registry: AsyncMock):
        req = OpenCodeRequest(action="ABORT_SESSION", payload={"project": "p"})
        resp = await external_opencode_send_message(req, mock_registry)

        assert resp.status == "error"


# ─────────────────────────────────────────────────────────────────────────────
# Action: GET_STATUS
# ─────────────────────────────────────────────────────────────────────────────


class TestGetStatus:
    """``GET_STATUS`` syncs state with OpenCode and returns a snapshot."""

    @pytest.mark.asyncio
    async def test_returns_ok_with_snapshot(self, mock_registry: AsyncMock, mock_manager: MagicMock):
        mock_registry.get_manager = AsyncMock(return_value=mock_manager)
        mock_manager.sync_state_with_open_code = AsyncMock(
            return_value={"state": "BUSY", "session_id": "sid-1"}
        )

        req = OpenCodeRequest(action="GET_STATUS", session_id="sid-1")
        resp = await external_opencode_send_message(req, mock_registry)

        assert resp.status == "ok"
        assert resp.data == {"state": "BUSY", "session_id": "sid-1"}

    @pytest.mark.asyncio
    async def test_returns_error_when_session_not_found(self, mock_registry: AsyncMock):
        mock_registry.get_manager = AsyncMock(return_value=None)

        req = OpenCodeRequest(action="GET_STATUS", session_id="missing")
        resp = await external_opencode_send_message(req, mock_registry)

        assert resp.status == "error"
        assert "Session not found" in resp.message

    @pytest.mark.asyncio
    async def test_returns_error_when_session_id_missing(self, mock_registry: AsyncMock):
        req = OpenCodeRequest(action="GET_STATUS")
        resp = await external_opencode_send_message(req, mock_registry)

        assert resp.status == "error"
        assert "session_id is required" in resp.message

    @pytest.mark.asyncio
    async def test_calls_sync_state_with_open_code_first(self, mock_registry: AsyncMock, mock_manager: MagicMock):
        """The implementation must call sync_state_with_open_code before returning."""
        mock_registry.get_manager = AsyncMock(return_value=mock_manager)
        mock_manager.sync_state_with_open_code = AsyncMock(return_value={})

        req = OpenCodeRequest(action="GET_STATUS", session_id="sid-1")
        await external_opencode_send_message(req, mock_registry)

        mock_manager.sync_state_with_open_code.assert_awaited_once()


# ─────────────────────────────────────────────────────────────────────────────
# PROMPT / COMMAND / ANSWER / RESUME: Missing session_id
# ─────────────────────────────────────────────────────────────────────────────


class TestSessionActionsRequireSessionId:
    """All session-bound actions reject requests without a session_id."""

    @pytest.mark.parametrize("action", ["PROMPT", "COMMAND", "ANSWER", "RESUME"])
    async def test_returns_error_when_session_id_missing(
        self, action: str, mock_registry: AsyncMock
    ):
        req = OpenCodeRequest(action=action)
        resp = await external_opencode_send_message(req, mock_registry)

        assert resp.status == "error"
        assert "session_id is required" in resp.message


# ─────────────────────────────────────────────────────────────────────────────
# PROMPT / COMMAND / ANSWER / RESUME: Session not found
# ─────────────────────────────────────────────────────────────────────────────


class TestSessionActionsSessionNotFound:
    """Actions return an error when the session cannot be found or loaded."""

    @pytest.mark.parametrize("action", ["PROMPT", "COMMAND", "ANSWER", "RESUME"])
    async def test_returns_error_when_session_not_in_registry_or_repository(
        self, action: str, mock_registry: AsyncMock
    ):
        mock_registry.get_manager = AsyncMock(return_value=None)
        mock_registry.load_session_into_memory = AsyncMock(return_value=None)

        req = OpenCodeRequest(action=action, session_id="missing")
        resp = await external_opencode_send_message(req, mock_registry)

        assert resp.status == "error"
        assert "Session not found" in resp.message


# ─────────────────────────────────────────────────────────────────────────────
# Special prompt: /start-work locks agent to "atlas"
# ─────────────────────────────────────────────────────────────────────────────


class TestStartWorkLocksAgent:
    """``/start-work`` dispatches to ``registry.handle_start_work`` with agent=atlas."""

    @pytest.mark.asyncio
    async def test_start_work_calls_handle_start_work(
        self, mock_registry: AsyncMock, mock_manager: MagicMock
    ):
        mock_registry.get_manager = AsyncMock(return_value=mock_manager)
        mock_registry.find_by_id = AsyncMock(
            return_value={"project": "myapp", "session_name": "s1"}
        )

        req = OpenCodeRequest(
            action="PROMPT",
            session_id="sid-1",
            payload={"parts": [{"type": "text", "text": "/start-work"}]},
        )
        await external_opencode_send_message(req, mock_registry)

        mock_registry.handle_start_work.assert_awaited_once_with(
            project="myapp",
            session_name="s1",
            agent="atlas",
        )

    @pytest.mark.asyncio
    async def test_start_work_is_case_insensitive(self, mock_registry: AsyncMock, mock_manager: MagicMock):
        """The leading slash is stripped and text is lowercased before matching."""
        mock_registry.get_manager = AsyncMock(return_value=mock_manager)
        mock_registry.find_by_id = AsyncMock(
            return_value={"project": "myapp", "session_name": "s1"}
        )

        req = OpenCodeRequest(
            action="PROMPT",
            session_id="sid-1",
            payload={"parts": [{"type": "text", "text": "/START-WORK"}]},
        )
        await external_opencode_send_message(req, mock_registry)

        mock_registry.handle_start_work.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_start_work_without_slash_still_calls_handle_start_work(
        self, mock_registry: AsyncMock, mock_manager: MagicMock
    ):
        """"start-work" (no slash) should also trigger the handler."""
        mock_registry.get_manager = AsyncMock(return_value=mock_manager)
        mock_registry.find_by_id = AsyncMock(
            return_value={"project": "myapp", "session_name": "s1"}
        )

        req = OpenCodeRequest(
            action="PROMPT",
            session_id="sid-1",
            payload={"parts": [{"type": "text", "text": "start-work"}]},
        )
        await external_opencode_send_message(req, mock_registry)

        mock_registry.handle_start_work.assert_awaited_once()


# ─────────────────────────────────────────────────────────────────────────────
# BUSY rejection: normal prompts are rejected when session is BUSY
# ─────────────────────────────────────────────────────────────────────────────


class TestBusyRejection:
    """Normal prompts return an error when the session is BUSY."""

    @pytest.mark.asyncio
    async def test_normal_prompt_rejected_when_busy(self, mock_registry: AsyncMock, mock_manager: MagicMock):
        mock_registry.get_manager = AsyncMock(return_value=mock_manager)
        mock_manager.get_snapshot = MagicMock(return_value={"state": "BUSY"})

        req = OpenCodeRequest(
            action="PROMPT",
            session_id="sid-1",
            payload={
                "parts": [{"type": "text", "text": "hello"}],
                "agent": "orchestrator",
            },
        )
        resp = await external_opencode_send_message(req, mock_registry)

        assert resp.status == "error"
        assert "busy" in resp.message.lower()
        mock_manager.submit_request.assert_not_called()

    @pytest.mark.asyncio
    async def test_busy_check_happens_before_agent_lock_override(
        self, mock_registry: AsyncMock, mock_manager: MagicMock
    ):
        """Even if agent is locked, a normal BUSY prompt is rejected before override."""
        mock_registry.get_manager = AsyncMock(return_value=mock_manager)
        mock_manager.get_snapshot = MagicMock(return_value={"state": "BUSY"})
        mock_registry.find_by_id = AsyncMock(
            return_value={"is_agent_locked": True, "last_agent": "atlas"}
        )

        req = OpenCodeRequest(
            action="PROMPT",
            session_id="sid-1",
            payload={"parts": [{"type": "text", "text": "hello"}]},
        )
        resp = await external_opencode_send_message(req, mock_registry)

        assert resp.status == "error"
        mock_manager.submit_request.assert_not_called()

    @pytest.mark.asyncio
    async def test_wait_command_rejected_when_busy(self, mock_registry: AsyncMock, mock_manager: MagicMock):
        """COMMAND actions are not subject to the BUSY check."""
        mock_registry.get_manager = AsyncMock(return_value=mock_manager)
        mock_manager.get_snapshot = MagicMock(return_value={"state": "BUSY"})

        req = OpenCodeRequest(
            action="COMMAND",
            session_id="sid-1",
            payload={"command": "/wait"},
        )
        # COMMAND bypasses the BUSY check (it only runs on PROMPT)
        resp = await external_opencode_send_message(req, mock_registry)

        assert resp.status == "ok"


# ─────────────────────────────────────────────────────────────────────────────
# BUSY bypass: special prompts (start-work, continue, abort, retry) pass through
# ─────────────────────────────────────────────────────────────────────────────


class TestBusyBypass:
    """Special prompts bypass the BUSY rejection so agents can intervene."""

    @pytest.mark.parametrize("text", ["/start-work", "/continue", "/abort", "/retry"])
    async def test_special_prompt_bypasses_busy(
        self, text: str, mock_registry: AsyncMock, mock_manager: MagicMock
    ):
        mock_registry.get_manager = AsyncMock(return_value=mock_manager)
        mock_manager.get_snapshot = MagicMock(return_value={"state": "BUSY"})
        mock_registry.find_by_id = AsyncMock(return_value=None)

        req = OpenCodeRequest(
            action="PROMPT",
            session_id="sid-1",
            payload={"parts": [{"type": "text", "text": text}]},
        )
        resp = await external_opencode_send_message(req, mock_registry)

        assert resp.status == "ok"
        mock_manager.submit_request.assert_called_once()

    @pytest.mark.asyncio
    async def test_start_work_bypasses_busy(self, mock_registry: AsyncMock, mock_manager: MagicMock):
        """Even when the worker is busy, /start-work can be sent."""
        mock_registry.get_manager = AsyncMock(return_value=mock_manager)
        mock_manager.get_snapshot = MagicMock(return_value={"state": "BUSY"})
        mock_registry.find_by_id = AsyncMock(
            return_value={"project": "myapp", "session_name": "s1"}
        )

        req = OpenCodeRequest(
            action="PROMPT",
            session_id="sid-1",
            payload={"parts": [{"type": "text", "text": "/start-work"}]},
        )
        resp = await external_opencode_send_message(req, mock_registry)

        assert resp.status == "ok"

    @pytest.mark.asyncio
    async def test_abort_bypasses_busy(self, mock_registry: AsyncMock, mock_manager: MagicMock):
        """The /abort command can interrupt a busy worker."""
        mock_registry.get_manager = AsyncMock(return_value=mock_manager)
        mock_manager.get_snapshot = MagicMock(return_value={"state": "BUSY"})

        req = OpenCodeRequest(
            action="PROMPT",
            session_id="sid-1",
            payload={"parts": [{"type": "text", "text": "/abort"}]},
        )
        resp = await external_opencode_send_message(req, mock_registry)

        assert resp.status == "ok"


# ─────────────────────────────────────────────────────────────────────────────
# Agent lock override
# ─────────────────────────────────────────────────────────────────────────────


class TestAgentLockOverride:
    """When a session is locked to an agent, the payload's agent field is overridden."""

    @pytest.mark.asyncio
    async def test_agent_field_is_overridden_for_prompt(
        self, mock_registry: AsyncMock, mock_manager: MagicMock
    ):
        mock_registry.get_manager = AsyncMock(return_value=mock_manager)
        mock_manager.get_snapshot = MagicMock(return_value={"state": "IDLE"})
        mock_registry.find_by_id = AsyncMock(
            return_value={"is_agent_locked": True, "last_agent": "atlas"}
        )

        req = OpenCodeRequest(
            action="PROMPT",
            session_id="sid-1",
            payload={
                "parts": [{"type": "text", "text": "hello"}],
                "agent": "orchestrator",
            },
        )
        await external_opencode_send_message(req, mock_registry)

        call_args = mock_manager.submit_request.call_args
        manager_req: ManagerRequest = call_args.args[0]
        assert manager_req.payload.agent == "atlas"

    @pytest.mark.asyncio
    async def test_agent_field_is_overridden_for_command(
        self, mock_registry: AsyncMock, mock_manager: MagicMock
    ):
        mock_registry.get_manager = AsyncMock(return_value=mock_manager)
        mock_registry.find_by_id = AsyncMock(
            return_value={"is_agent_locked": True, "last_agent": "atlas"}
        )

        req = OpenCodeRequest(
            action="COMMAND",
            session_id="sid-1",
            payload={"command": "/status", "agent": "orchestrator"},
        )
        await external_opencode_send_message(req, mock_registry)

        call_args = mock_manager.submit_request.call_args
        manager_req: ManagerRequest = call_args.args[0]
        assert manager_req.payload.agent == "atlas"

    @pytest.mark.asyncio
    async def test_no_override_when_not_locked(
        self, mock_registry: AsyncMock, mock_manager: MagicMock
    ):
        mock_registry.get_manager = AsyncMock(return_value=mock_manager)
        mock_manager.get_snapshot = MagicMock(return_value={"state": "IDLE"})
        mock_registry.find_by_id = AsyncMock(return_value={"is_agent_locked": False})

        req = OpenCodeRequest(
            action="PROMPT",
            session_id="sid-1",
            payload={
                "parts": [{"type": "text", "text": "hello"}],
                "agent": "orchestrator",
            },
        )
        await external_opencode_send_message(req, mock_registry)

        call_args = mock_manager.submit_request.call_args
        manager_req: ManagerRequest = call_args.args[0]
        assert manager_req.payload.agent == "orchestrator"


# ─────────────────────────────────────────────────────────────────────────────
# continue / retry → RESUME routing
# ─────────────────────────────────────────────────────────────────────────────


class TestContinueRetryRouting:
    """``continue`` and ``retry`` prompts are converted to the RESUME action."""

    @pytest.mark.parametrize("text", ["/continue", "continue", "/retry", "retry"])
    async def test_normalized_continue_or_retry_routes_as_resume(
        self, text: str, mock_registry: AsyncMock, mock_manager: MagicMock
    ):
        mock_registry.get_manager = AsyncMock(return_value=mock_manager)
        mock_manager.get_snapshot = MagicMock(return_value={"state": "IDLE"})

        req = OpenCodeRequest(
            action="PROMPT",
            session_id="sid-1",
            payload={"parts": [{"type": "text", "text": text}]},
        )
        await external_opencode_send_message(req, mock_registry)

        call_args = mock_manager.submit_request.call_args
        manager_req: ManagerRequest = call_args.args[0]
        assert manager_req.type == "RESUME"


# ─────────────────────────────────────────────────────────────────────────────
# Payload validation
# ─────────────────────────────────────────────────────────────────────────────


class TestPayloadValidation:
    """Invalid payloads for PROMPT / COMMAND / ANSWER return validation errors."""

    @pytest.mark.asyncio
    async def test_prompt_with_invalid_part_returns_error(
        self, mock_registry: AsyncMock, mock_manager: MagicMock
    ):
        mock_registry.get_manager = AsyncMock(return_value=mock_manager)
        mock_manager.get_snapshot = MagicMock(return_value={"state": "IDLE"})
        mock_registry.find_by_id = AsyncMock(return_value=None)

        # Part requires a "type" field; omitting it must trigger validation
        req = OpenCodeRequest(
            action="PROMPT",
            session_id="sid-1",
            payload={"parts": [{"text": "missing type field"}]},
        )
        resp = await external_opencode_send_message(req, mock_registry)

        assert resp.status == "error"
        assert "invalid PROMPT payload" in resp.message

    @pytest.mark.asyncio
    async def test_command_with_invalid_payload_returns_error(
        self, mock_registry: AsyncMock, mock_manager: MagicMock
    ):
        mock_registry.get_manager = AsyncMock(return_value=mock_manager)
        mock_registry.find_by_id = AsyncMock(return_value=None)

        req = OpenCodeRequest(
            action="COMMAND",
            session_id="sid-1",
            payload={"command": 123},  # command must be string
        )
        resp = await external_opencode_send_message(req, mock_registry)

        assert resp.status == "error"
        assert "invalid COMMAND payload" in resp.message

    @pytest.mark.asyncio
    async def test_answer_with_invalid_payload_returns_error(
        self, mock_registry: AsyncMock, mock_manager: MagicMock
    ):
        mock_registry.get_manager = AsyncMock(return_value=mock_manager)

        req = OpenCodeRequest(
            action="ANSWER",
            session_id="sid-1",
            payload={},  # missing request_id and answers
        )
        resp = await external_opencode_send_message(req, mock_registry)

        assert resp.status == "error"
        assert "invalid ANSWER payload" in resp.message


# ─────────────────────────────────────────────────────────────────────────────
# Successful submission
# ─────────────────────────────────────────────────────────────────────────────


class TestSuccessfulSubmission:
    """Valid PROMPT/COMMAND/ANSWER requests are submitted to the manager."""

    @pytest.mark.asyncio
    async def test_prompt_is_submitted_with_correct_type(
        self, mock_registry: AsyncMock, mock_manager: MagicMock
    ):
        mock_registry.get_manager = AsyncMock(return_value=mock_manager)
        mock_manager.get_snapshot = MagicMock(return_value={"state": "IDLE"})

        req = OpenCodeRequest(
            action="PROMPT",
            session_id="sid-1",
            payload={
                "parts": [{"type": "text", "text": "hello"}],
                "agent": "orchestrator",
            },
        )
        resp = await external_opencode_send_message(req, mock_registry)

        assert resp.status == "ok"
        assert resp.message == "Request submitted"
        call_args = mock_manager.submit_request.call_args
        manager_req: ManagerRequest = call_args.args[0]
        assert manager_req.type == "PROMPT"
        assert manager_req.payload is not None

    @pytest.mark.asyncio
    async def test_command_is_submitted_with_correct_type(
        self, mock_registry: AsyncMock, mock_manager: MagicMock
    ):
        mock_registry.get_manager = AsyncMock(return_value=mock_manager)

        req = OpenCodeRequest(
            action="COMMAND",
            session_id="sid-1",
            payload={"command": "/wait", "agent": "orchestrator"},
        )
        resp = await external_opencode_send_message(req, mock_registry)

        assert resp.status == "ok"
        call_args = mock_manager.submit_request.call_args
        manager_req: ManagerRequest = call_args.args[0]
        assert manager_req.type == "COMMAND"

    @pytest.mark.asyncio
    async def test_answer_is_submitted_with_correct_type(
        self, mock_registry: AsyncMock, mock_manager: MagicMock
    ):
        mock_registry.get_manager = AsyncMock(return_value=mock_manager)

        req = OpenCodeRequest(
            action="ANSWER",
            session_id="sid-1",
            payload={"requestID": "req-1", "answers": [["A"]]},
        )
        resp = await external_opencode_send_message(req, mock_registry)

        assert resp.status == "ok"
        call_args = mock_manager.submit_request.call_args
        manager_req: ManagerRequest = call_args.args[0]
        assert manager_req.type == "ANSWER"

    @pytest.mark.asyncio
    async def test_resume_is_submitted_with_no_payload(
        self, mock_registry: AsyncMock, mock_manager: MagicMock
    ):
        mock_registry.get_manager = AsyncMock(return_value=mock_manager)
        mock_manager.get_snapshot = MagicMock(return_value={"state": "IDLE"})

        req = OpenCodeRequest(
            action="RESUME",
            session_id="sid-1",
            payload={},
        )
        resp = await external_opencode_send_message(req, mock_registry)

        assert resp.status == "ok"
        call_args = mock_manager.submit_request.call_args
        manager_req: ManagerRequest = call_args.args[0]
        assert manager_req.type == "RESUME"
        # RESUME has no payload conversion — payload is None
        assert manager_req.payload is None


# ─────────────────────────────────────────────────────────────────────────────
# Unknown action
# ─────────────────────────────────────────────────────────────────────────────


class TestUnknownAction:
    """An unknown action returns an error with "Unknown action"."""

    @pytest.mark.asyncio
    async def test_unknown_action_returns_error(self, mock_registry: AsyncMock):
        req = OpenCodeRequest(action="DO_SOMETHING_ELSE")
        resp = await external_opencode_send_message(req, mock_registry)

        assert resp.status == "error"
        assert resp.message == "Unknown action"


# ─────────────────────────────────────────────────────────────────────────────
# Extra field tolerance (model_config = {"extra": "ignore"})
# ─────────────────────────────────────────────────────────────────────────────


class TestExtraFieldTolerance:
    """Unknown fields in the request payload are silently ignored."""

    @pytest.mark.asyncio
    async def test_extra_fields_in_payload_ignored(self, mock_registry: AsyncMock, mock_manager: MagicMock):
        mock_registry.get_manager = AsyncMock(return_value=mock_manager)
        mock_manager.get_snapshot = MagicMock(return_value={"state": "IDLE"})

        req = OpenCodeRequest(
            action="PROMPT",
            session_id="sid-1",
            payload={
                "parts": [{"type": "text", "text": "hello"}],
                "unknown_field": "should be ignored",
            },
        )
        resp = await external_opencode_send_message(req, mock_registry)

        assert resp.status == "ok"
