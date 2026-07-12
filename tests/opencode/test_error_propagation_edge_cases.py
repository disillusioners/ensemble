"""Edge case tests for OpenCode error propagation.

Covers scenarios NOT addressed by the baseline 12 tests in
``test_tools.py`` / ``test_session_manager.py``:

1. **Error → Success recovery** — a worker error followed by a new
   successful request must clear the error and return ``[COMPLETED]``.
2. **Concurrent wait_for_result** — two simultaneous callers both get
   ``[ERROR]`` when the worker has failed (no race lets one silently
   succeed).
3. **Resume surfaces worker error** — a RESUME that triggers a worker
   HTTP 500 must surface the failure on the next ``wait_for_result``.
4. **get_status recovery** — ``get_status`` reflects ``[ERROR]`` while
   the error is present, then drops the marker after recovery.
5. **Error message has no leaked success tokens** — an error result
   must not contain ``[COMPLETED]`` or ``[RESUMED]`` substrings.

These tests follow the same fixture / patching conventions used by
``tests/opencode/test_tools.py`` (see ``TestWaitForResultExecution``
and ``TestGetStatusExecution`` for reference).
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from daemon.opencode.server import OpenCodeResponse
from daemon.tools.external_opencode import create_opencode_tools


ERROR_PAYLOAD_TEXT = "API Error 500: Unexpected server error"


# =============================================================================
# Local fixtures
#
# ``mock_manager`` / ``mock_registry`` / ``_stub_preload_context`` are
# defined inline in ``tests/opencode/test_tools.py`` (not in ``conftest.py``)
# so this file reproduces them verbatim. Pattern parity is intentional —
# keeping the same fixture names ensures the test code reads identically
# to the existing opencode tool tests.
# =============================================================================


@pytest.fixture
def mock_registry() -> AsyncMock:
    """Return an AsyncMock that mimics ``OpenCodeSessionRegistry``.

    The factory calls ``get_session_record`` and ``get_manager`` on the
    registry, so we stub both. ``get_manager`` defaults to ``None`` so
    the wait loop falls back to the legacy 30s sleep path.
    """
    registry = AsyncMock()
    registry.get_session_record = AsyncMock(return_value=None)
    registry.get_manager = AsyncMock(return_value=None)
    return registry


@pytest.fixture
def mock_manager(mock_registry: AsyncMock) -> MagicMock:
    """Return a MagicMock InstanceManager with an ``opencode_registry``."""
    manager = MagicMock()
    manager.opencode_registry = mock_registry
    # Stub the instance repository so the preload-context helper resolves a
    # context key without touching the real filesystem / context dir.
    repo = MagicMock()
    repo.get_tree_root_id = MagicMock(return_value=None)
    manager._instance_repository = repo
    return manager


@pytest.fixture(autouse=True)
def _stub_preload_context() -> Any:
    """Default-patch ``get_shared_context`` to return empty for all tests."""
    with patch(
        "daemon.tools.external_opencode.get_shared_context",
        return_value="",
    ):
        yield


def _make_session_record(session_id: str = "session-edge") -> dict:
    """Build a minimal session record matching the test_tools.py style."""
    return {
        "id": session_id,
        "state": "IDLE",
        "last_activity": "2026-07-12T00:00:00Z",
    }


def _error_response(session_id: str = "session-edge") -> OpenCodeResponse:
    """GET_STATUS payload carrying a worker HTTP 500 error."""
    return OpenCodeResponse(
        status="ok",
        data={
            "state": "IDLE",
            "latest_response": {"error": ERROR_PAYLOAD_TEXT},
        },
    )


def _success_response(
    session_id: str = "session-edge",
    result_text: str = "recovered",
) -> OpenCodeResponse:
    """GET_STATUS payload carrying a successful worker result."""
    return OpenCodeResponse(
        status="ok",
        data={
            "state": "IDLE",
            "latest_response": {"result": {"text": result_text}},
        },
    )


# =============================================================================
# 1. Error → Success recovery
# =============================================================================


class TestErrorThenSuccessRecovery:
    """After a worker error, a NEW request must clear the error state."""

    @pytest.mark.asyncio
    async def test_error_then_success_recovery(
        self,
        mock_manager: MagicMock,
        mock_registry: AsyncMock,
    ) -> None:
        """First wait_for_result surfaces the error, the next one returns [COMPLETED].

        Models the recovery scenario: a worker fails with HTTP 500, the
        agent then submits a fresh prompt, and the second
        ``wait_for_result`` should NOT carry forward the previous error
        — the new response is fresh and successful.
        """
        mock_registry.get_session_record = AsyncMock(
            return_value=_make_session_record("session-recover")
        )

        error_resp = _error_response("session-recover")
        success_resp = _success_response("session-recover")

        # First call returns the error, second returns the success.
        with patch(
            "daemon.tools.external_opencode._server_send_message",
            new_callable=AsyncMock,
            side_effect=[error_resp, success_resp],
        ), patch(
            "daemon.tools.external_opencode.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            tools = create_opencode_tools(mock_manager, "test-id")
            wait_tool = next(
                t for t in tools if t.name == "external_opencode_wait_for_result"
            )

            first_result = await wait_tool.ainvoke({
                "project": "myapp",
                "session_name": "feature-recover",
            })
            second_result = await wait_tool.ainvoke({
                "project": "myapp",
                "session_name": "feature-recover",
            })

        assert isinstance(first_result, str)
        assert first_result.startswith("[ERROR]")
        assert ERROR_PAYLOAD_TEXT in first_result

        assert isinstance(second_result, str)
        assert second_result.startswith("[COMPLETED]")
        assert "[ERROR]" not in second_result


# =============================================================================
# 2. Concurrent wait_for_result calls
# =============================================================================


class TestConcurrentWaitForResult:
    """Concurrent callers must all receive the [ERROR] signal."""

    @pytest.mark.asyncio
    async def test_concurrent_wait_for_result_both_get_error(
        self,
        mock_manager: MagicMock,
        mock_registry: AsyncMock,
    ) -> None:
        """Two simultaneous wait_for_result calls both return [ERROR].

        Regression guard: with the async fire-and-forget pattern, a
        racing pair of waiters could otherwise observe one stale
        "completed" snapshot and one error — the test ensures that
        when the worker has truly failed, every waiter sees the error
        rather than a misleading success.
        """
        mock_registry.get_session_record = AsyncMock(
            return_value=_make_session_record("session-concurrent")
        )

        # Always serve the same error (no successful response).
        error_resp = _error_response("session-concurrent")

        with patch(
            "daemon.tools.external_opencode._server_send_message",
            new_callable=AsyncMock,
            return_value=error_resp,
        ), patch(
            "daemon.tools.external_opencode.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            tools = create_opencode_tools(mock_manager, "test-id")
            wait_tool = next(
                t for t in tools if t.name == "external_opencode_wait_for_result"
            )

            call_args = {
                "project": "myapp",
                "session_name": "feature-concurrent",
            }
            results = await asyncio.gather(
                wait_tool.ainvoke(call_args),
                wait_tool.ainvoke(call_args),
            )

        assert len(results) == 2
        for result in results:
            assert isinstance(result, str)
            assert result.startswith("[ERROR]")
            assert ERROR_PAYLOAD_TEXT in result
            assert "[COMPLETED]" not in result


# =============================================================================
# 3. Resume session surfaces worker error
# =============================================================================


class TestResumeSessionSurfacesError:
    """A RESUME that triggers an HTTP 500 must be surfaced via wait_for_result."""

    @pytest.mark.asyncio
    async def test_resume_session_surfaces_error(
        self,
        mock_manager: MagicMock,
        mock_registry: AsyncMock,
    ) -> None:
        """Calling resume then wait_for_result reports [ERROR], not [COMPLETED].

        RESUME itself is fire-and-forget (returns ``[RESUMED]``
        immediately). The bug is the worker failing AFTER the RESUME
        was accepted: the next ``wait_for_result`` call must surface
        the failure instead of pretending the session succeeded.
        """
        mock_registry.get_session_record = AsyncMock(
            return_value=_make_session_record("session-resume")
        )

        resume_resp = OpenCodeResponse(
            status="ok",
            message="resumed",
        )
        error_resp = _error_response("session-resume")

        with patch(
            "daemon.tools.external_opencode._server_send_message",
            new_callable=AsyncMock,
            side_effect=[resume_resp, error_resp],
        ), patch(
            "daemon.tools.external_opencode.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            tools = create_opencode_tools(mock_manager, "test-id")
            resume_tool = next(
                t for t in tools if t.name == "external_opencode_resume_session"
            )
            wait_tool = next(
                t for t in tools if t.name == "external_opencode_wait_for_result"
            )

            resume_result = await resume_tool.ainvoke({
                "project": "myapp",
                "session_name": "feature-resume",
            })
            assert resume_result.startswith("[RESUMED]")

            wait_result = await wait_tool.ainvoke({
                "project": "myapp",
                "session_name": "feature-resume",
            })

        assert isinstance(wait_result, str)
        assert wait_result.startswith("[ERROR]")
        assert ERROR_PAYLOAD_TEXT in wait_result
        assert "[COMPLETED]" not in wait_result


# =============================================================================
# 4. get_status error → recovery
# =============================================================================


class TestGetStatusErrorThenRecovery:
    """get_status tracks both directions of the error lifecycle."""

    @pytest.mark.asyncio
    async def test_get_status_after_error_then_recovery(
        self,
        mock_manager: MagicMock,
        mock_registry: AsyncMock,
    ) -> None:
        """First status shows [ERROR], second shows normal status.

        While the worker error is present the get_status tool reports
        an ``[ERROR]`` block; after a successful follow-up request
        the next call drops the marker and renders the success shape.
        """
        mock_registry.get_session_record = AsyncMock(
            return_value=_make_session_record("session-status-recover")
        )

        error_resp = _error_response("session-status-recover")
        success_resp = _success_response(
            "session-status-recover", result_text="all good now"
        )

        with patch(
            "daemon.tools.external_opencode._server_send_message",
            new_callable=AsyncMock,
            side_effect=[error_resp, success_resp],
        ):
            tools = create_opencode_tools(mock_manager, "test-id")
            status_tool = next(
                t for t in tools if t.name == "external_opencode_get_status"
            )

            args = {
                "project": "myapp",
                "session_name": "feature-status",
            }
            error_status = await status_tool.ainvoke(args)
            recovered_status = await status_tool.ainvoke(args)

        assert isinstance(error_status, str)
        assert "[ERROR]" in error_status
        assert ERROR_PAYLOAD_TEXT in error_status

        assert isinstance(recovered_status, str)
        assert "[ERROR]" not in recovered_status
        # Recovered status should render the success payload verbatim.
        assert "all good now" in recovered_status


# =============================================================================
# 5. Error result has no leaked success tokens
# =============================================================================


class TestErrorResultHasNoSuccessTokens:
    """An error result must not contain [COMPLETED] or [RESUMED] markers."""

    @pytest.mark.asyncio
    async def test_wait_for_result_error_does_not_contain_completed(
        self,
        mock_manager: MagicMock,
        mock_registry: AsyncMock,
    ) -> None:
        """Explicit guard: error result excludes [COMPLETED] and [RESUMED].

        Belt-and-braces assertion covering the bug class: even if a
        future refactor accidentally concatenates the success footer
        onto an error result, the explicit substring check fails
        loudly. Mirrors the existing
        ``test_wait_for_result_returns_error_on_worker_http_500``
        assertion but adds the ``[RESUMED]`` negative check too.
        """
        mock_registry.get_session_record = AsyncMock(
            return_value=_make_session_record("session-no-leak")
        )

        error_resp = _error_response("session-no-leak")

        with patch(
            "daemon.tools.external_opencode._server_send_message",
            new_callable=AsyncMock,
            return_value=error_resp,
        ), patch(
            "daemon.tools.external_opencode.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            tools = create_opencode_tools(mock_manager, "test-id")
            wait_tool = next(
                t for t in tools if t.name == "external_opencode_wait_for_result"
            )

            result = await wait_tool.ainvoke({
                "project": "myapp",
                "session_name": "feature-no-leak",
            })

        assert isinstance(result, str)
        assert result.startswith("[ERROR]")
        assert ERROR_PAYLOAD_TEXT in result
        assert "[COMPLETED]" not in result
        assert "[RESUMED]" not in result


# =============================================================================
# 6. _format_timeout surfaces error dicts (F8 gap)
#
# Round 2 added the error-detection branch in ``_format_timeout``
# (daemon/tools/external_opencode.py, lines ~199-204): when
# ``latest_response`` is ``{"error": "..."}`` the rendered timeout
# message contains ``[ERROR] Worker request failed: <msg>`` rather
# than the raw result text. ``_format_timeout`` is a closure-local
# nested function and is NOT directly importable, so these tests
# exercise it through the only reachable surface — the
# ``external_opencode_wait_for_result`` tool — by forcing the
# poll loop to exhaust ``WAIT_TIMEOUT_S`` while the mocked dispatcher
# keeps returning BUSY with the error payload.
# =============================================================================


class TestFormatTimeoutErrorDetection:
    """_format_timeout must surface worker HTTP 500 errors on TIMEOUT path."""

    @pytest.mark.asyncio
    async def test_format_timeout_surfaces_error_dict(
        self,
        mock_manager: MagicMock,
        mock_registry: AsyncMock,
    ) -> None:
        """Timeout while ``latest_response={"error": "..."}`` renders [ERROR].

        The poll loop never observes ``IDLE``/``WAITING_FOR_INPUT``
        (mocked dispatcher always returns BUSY), ``asyncio.sleep`` is a
        no-op, and ``WAIT_TIMEOUT_S`` is patched to 0.05s so the loop
        exhausts the deadline quickly. ``_format_timeout`` is then
        called with the BUSY response whose ``latest_response`` carries
        the error dict, and the rendered message must wrap the error
        inside a ``[ERROR] Worker request failed:`` block instead of
        the normal result text.
        """
        mock_registry.get_session_record = AsyncMock(
            return_value=_make_session_record("session-timeout-error")
        )

        busy_error_resp = OpenCodeResponse(
            status="ok",
            data={
                "state": "BUSY",
                "latest_response": {"error": "API Error 500: server down"},
            },
        )

        with patch(
            "daemon.tools.external_opencode._server_send_message",
            new_callable=AsyncMock,
            return_value=busy_error_resp,
        ), patch(
            "daemon.tools.external_opencode.asyncio.sleep",
            new_callable=AsyncMock,
        ), patch(
            "daemon.tools.external_opencode.WAIT_TIMEOUT_S", 0.05,
        ):
            tools = create_opencode_tools(mock_manager, "test-id")
            wait_tool = next(
                t for t in tools if t.name == "external_opencode_wait_for_result"
            )

            result = await wait_tool.ainvoke({
                "project": "myapp",
                "session_name": "feature-timeout-error",
            })

        assert isinstance(result, str)
        # Timeout wrapper is preserved.
        assert result.startswith("[TIMEOUT]")
        # _format_timeout surfaced the worker error inside the timeout block.
        assert "[ERROR]" in result
        assert "API Error 500" in result
        assert "server down" in result
        # Full prefix from the error-detection branch.
        assert "[ERROR] Worker request failed: API Error 500: server down" in result
        # No false-positive success markers.
        assert "[COMPLETED]" not in result

    @pytest.mark.asyncio
    async def test_format_timeout_with_error_none_uses_fallback(
        self,
        mock_manager: MagicMock,
        mock_registry: AsyncMock,
    ) -> None:
        """``latest_response={"error": None}`` renders the ``unknown error`` fallback.

        Guards the ``latest.get("error") or "unknown error"`` short-circuit:
        a worker that crashes with no message attached should still produce
        a useful marker for the agent rather than leaking a Python ``None``
        string into the rendered output.
        """
        mock_registry.get_session_record = AsyncMock(
            return_value=_make_session_record("session-timeout-none")
        )

        busy_none_resp = OpenCodeResponse(
            status="ok",
            data={
                "state": "BUSY",
                "latest_response": {"error": None},
            },
        )

        with patch(
            "daemon.tools.external_opencode._server_send_message",
            new_callable=AsyncMock,
            return_value=busy_none_resp,
        ), patch(
            "daemon.tools.external_opencode.asyncio.sleep",
            new_callable=AsyncMock,
        ), patch(
            "daemon.tools.external_opencode.WAIT_TIMEOUT_S", 0.05,
        ):
            tools = create_opencode_tools(mock_manager, "test-id")
            wait_tool = next(
                t for t in tools if t.name == "external_opencode_wait_for_result"
            )

            result = await wait_tool.ainvoke({
                "project": "myapp",
                "session_name": "feature-timeout-none",
            })

        assert isinstance(result, str)
        # Timeout wrapper is preserved.
        assert result.startswith("[TIMEOUT]")
        # Fallback string replaces the literal None.
        assert "[ERROR]" in result
        assert "unknown error" in result
        assert "[ERROR] Worker request failed: unknown error" in result
        # The literal Python None must not leak into the rendered message.
        assert "None" not in result
