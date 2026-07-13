"""Tests for the OpenCode native tool factory (daemon.tools.external_opencode).

Tests ``create_opencode_tools()`` — the factory that produces the 8 native
LangChain tools that replace the Go-based ``opencode_skill`` daemon.

Coverage:
    1. Tool count — factory returns exactly 8 tools
    2. Tool names — all 8 expected names with ``external_opencode_`` prefix
    3. Tool docstrings — every tool has a non-empty description
    4. ``_full_doc_`` attribute — every tool has extended documentation
    5. Category registration — every tool belongs to ``external_opencode``
    6. Tool execution — at least one tool runs end-to-end against mocks
"""

from __future__ import annotations

import asyncio
import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typing import Any

from daemon.opencode.server import OpenCodeRequest, OpenCodeResponse
from daemon.tools.external_opencode import (
    CATEGORY_DOC,
    CATEGORY_NAME,
    create_opencode_tools,
)


# =============================================================================
# Expected tool manifest
# =============================================================================


EXPECTED_TOOL_NAMES = {
    "external_opencode_init_session",
    "external_opencode_send_message",
    "external_opencode_get_status",
    "external_opencode_wait_for_result",
    "external_opencode_wait_any",
    "external_opencode_answer_question",
    "external_opencode_resume_session",
    "external_opencode_abort_session",
}


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_registry() -> AsyncMock:
    """Return an AsyncMock that mimics ``OpenCodeSessionRegistry``.

    The factory calls ``get_session_record`` and ``get_manager`` on the
    registry, so we stub both. ``get_manager`` defaults to ``None`` so
    the wait loop falls back to the legacy 30s sleep path — the
    event-based path is exercised in dedicated tests (e.g. tests that
    patch a real ``OpenCodeSessionManager``).
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
    """Default-patch ``get_shared_context`` to return empty for all tests.

    Individual tests that need a specific preload response override this
    with their own ``patch(..., return_value=...)`` context manager.
    """
    with patch(
        "daemon.tools.external_opencode.get_shared_context",
        return_value="",
    ):
        yield


@pytest.fixture
def tools(mock_manager: MagicMock) -> list:
    """Return the list of 8 tools produced by the factory."""
    return create_opencode_tools(mock_manager, "test-instance-id")


@pytest.fixture
def tools_by_name(tools: list) -> dict:
    """Return ``{tool_name: tool}`` for convenient lookup in tests."""
    return {t.name: t for t in tools}


# =============================================================================
# Factory structure tests
# =============================================================================


class TestFactoryStructure:
    """Tests for the shape of the factory's return value."""

    def test_factory_returns_exactly_eight_tools(self, tools: list) -> None:
        """``create_opencode_tools()`` returns a list of 8 tools."""
        assert len(tools) == 8

    def test_factory_returns_a_list(self, mock_manager: MagicMock) -> None:
        """Factory's return type is a list (not a tuple/iterator)."""
        result = create_opencode_tools(mock_manager, "test-instance-id")
        assert isinstance(result, list)

    def test_factory_accepts_current_instance_id(self, mock_manager: MagicMock) -> None:
        """Factory accepts the current instance id without raising."""
        # Should not raise even when given any string
        tools = create_opencode_tools(mock_manager, "any-instance-id-12345")
        assert len(tools) == 8

    def test_all_tools_have_unique_names(self, tools: list) -> None:
        """No two tools share a name (prevents LangGraph dispatch ambiguity)."""
        names = [t.name for t in tools]
        assert len(names) == len(set(names)), f"Duplicate tool names: {names}"


# =============================================================================
# Tool name tests
# =============================================================================


class TestToolNames:
    """Tests that the factory produces tools with the expected names."""

    def test_all_eight_expected_names_present(self, tools: list) -> None:
        """The set of tool names matches the expected 8 names exactly."""
        actual_names = {t.name for t in tools}
        assert actual_names == EXPECTED_TOOL_NAMES

    def test_all_names_use_external_opencode_prefix(self, tools: list) -> None:
        """Every tool name starts with the ``external_opencode_`` prefix."""
        for tool in tools:
            assert tool.name.startswith("external_opencode_"), (
                f"Tool {tool.name!r} does not start with 'external_opencode_'"
            )

    @pytest.mark.parametrize(
        "tool_name",
        sorted(EXPECTED_TOOL_NAMES),
    )
    def test_individual_tool_present(self, tools_by_name: dict, tool_name: str) -> None:
        """Each expected tool is present in the factory output."""
        assert tool_name in tools_by_name, f"Missing tool: {tool_name}"


# =============================================================================
# Docstring / description tests
# =============================================================================


class TestToolDocstrings:
    """Tests that every tool exposes a non-empty description for the LLM."""

    def test_every_tool_has_a_description_attribute(self, tools: list) -> None:
        """Every tool has a ``description`` attribute."""
        for tool in tools:
            assert hasattr(tool, "description"), f"{tool.name} missing description"
            assert tool.description is not None, f"{tool.name} description is None"

    def test_every_tool_description_is_non_empty(self, tools: list) -> None:
        """Every tool's description is a non-empty string."""
        for tool in tools:
            assert isinstance(tool.description, str), (
                f"{tool.name} description is not a string: {type(tool.description)}"
            )
            assert tool.description.strip(), (
                f"{tool.name} description is empty or whitespace"
            )

    def test_descriptions_mention_opencode_concept(self, tools: list) -> None:
        """Every description references opencode or session (semantic check)."""
        for tool in tools:
            lowered = tool.description.lower()
            assert "opencode" in lowered or "session" in lowered, (
                f"{tool.name} description does not mention opencode/session: "
                f"{tool.description!r}"
            )


# =============================================================================
# _full_doc_ attribute tests
# =============================================================================


class TestFullDocAttribute:
    """Tests that every tool has the ``_full_doc_`` extended documentation."""

    def test_every_tool_has_full_doc_attribute(self, tools: list) -> None:
        """Each tool exposes a ``_full_doc_`` attribute."""
        for tool in tools:
            assert hasattr(tool, "_full_doc_"), (
                f"Tool {tool.name} missing _full_doc_ attribute"
            )

    def test_every_full_doc_is_a_string(self, tools: list) -> None:
        """Each ``_full_doc_`` is a string instance."""
        for tool in tools:
            assert isinstance(tool._full_doc_, str), (
                f"{tool.name}._full_doc_ is not a string: {type(tool._full_doc_)}"
            )

    def test_every_full_doc_is_non_empty(self, tools: list) -> None:
        """Each ``_full_doc_`` is a non-empty (whitespace-trimmed) string."""
        for tool in tools:
            assert tool._full_doc_.strip(), (
                f"{tool.name}._full_doc_ is empty or whitespace"
            )

    def test_full_doc_is_at_least_as_long_as_description(self, tools: list) -> None:
        """``_full_doc_`` carries at least as much text as the short description.

        LangChain's ``@tool`` decorator appends a generic suffix to the
        description, so we don't assert a strict multiplier — we only check
        the full doc is never shorter than the description.
        """
        for tool in tools:
            full_len = len(tool._full_doc_.strip())
            desc_len = len(tool.description.strip())
            assert full_len >= desc_len, (
                f"{tool.name}: _full_doc_ length {full_len} is shorter than "
                f"description length {desc_len}"
            )

    def test_full_doc_is_substantially_longer_than_first_line(self, tools: list) -> None:
        """``_full_doc_`` carries substantially more text than the docstring's first line."""
        for tool in tools:
            # The "first line" of the docstring is the actual short doc —
            # much shorter than the auto-augmented LangChain description.
            first_line = tool.description.split("\n")[0].strip()
            full_len = len(tool._full_doc_.strip())
            assert full_len >= len(first_line) * 2, (
                f"{tool.name}: _full_doc_ length {full_len} is not >= 2x "
                f"first-line length {len(first_line)}"
            )

    def test_full_doc_lists_tool_parameters(self, tools: list) -> None:
        """``_full_doc_`` includes an ``Args:`` section for parameter documentation."""
        for tool in tools:
            assert "Args:" in tool._full_doc_, (
                f"{tool.name}._full_doc_ missing 'Args:' section"
            )

    def test_full_doc_lists_return_value(self, tools: list) -> None:
        """``_full_doc_`` includes a ``Returns:`` section for return documentation."""
        for tool in tools:
            assert "Returns:" in tool._full_doc_, (
                f"{tool.name}._full_doc_ missing 'Returns:' section"
            )

    def test_full_doc_is_picked_up_by_registry(self, tools: list) -> None:
        """When scanned, the tool's full doc is registered under the tool's name."""
        from daemon.tools._tool_registry import (
            get_full_doc,
            scan_tools_for_full_docs,
        )

        scan_tools_for_full_docs(tools)
        for tool in tools:
            registered = get_full_doc(tool.name)
            assert registered == tool._full_doc_, (
                f"Registry doc for {tool.name} does not match _full_doc_"
            )


# =============================================================================
# Category registration tests
# =============================================================================


class TestCategoryRegistration:
    """Tests that every tool is registered under the ``external_opencode`` category."""

    def test_every_tool_has_tool_category_attribute(self, tools: list) -> None:
        """Each tool exposes a ``_tool_category`` attribute."""
        for tool in tools:
            assert hasattr(tool, "_tool_category"), (
                f"Tool {tool.name} missing _tool_category attribute"
            )

    def test_every_tool_category_is_external_opencode(self, tools: list) -> None:
        """Each tool's category is exactly ``external_opencode``."""
        for tool in tools:
            assert tool._tool_category == "external_opencode", (
                f"{tool.name} has category {tool._tool_category!r}, "
                f"expected 'external_opencode'"
            )

    def test_module_category_name_is_set(self) -> None:
        """The module exports a ``CATEGORY_NAME`` constant."""
        assert CATEGORY_NAME
        assert isinstance(CATEGORY_NAME, str)

    def test_module_category_doc_is_set(self) -> None:
        """The module exports a non-empty ``CATEGORY_DOC`` string."""
        assert CATEGORY_DOC
        assert isinstance(CATEGORY_DOC, str)
        assert CATEGORY_DOC.strip()

    def test_module_is_listed_in_category_modules(self) -> None:
        """The ``external_opencode`` module is registered in ``CATEGORY_MODULES``."""
        from daemon.tools._tool_registry import CATEGORY_MODULES

        assert "external_opencode" in CATEGORY_MODULES
        assert CATEGORY_MODULES["external_opencode"] == "daemon.tools.external_opencode"


# =============================================================================
# Tool execution tests — end-to-end with mocked registry
# =============================================================================


class TestInitSessionExecution:
    """End-to-end test of ``external_opencode_init_session``."""

    @pytest.mark.asyncio
    async def test_init_session_returns_success_on_ok(
        self,
        mock_manager: MagicMock,
    ) -> None:
        """Successful init returns a ``[SUCCESS]`` formatted string."""
        ok_response = OpenCodeResponse(
            status="ok",
            message="Session created",
            session_id="session-abc-123",
            data=None,
        )
        with patch(
            "daemon.tools.external_opencode._server_send_message",
            new_callable=AsyncMock,
            return_value=ok_response,
        ) as mock_send:
            tools = create_opencode_tools(mock_manager, "test-id")
            init_tool = next(t for t in tools if t.name == "external_opencode_init_session")

            result = await init_tool.ainvoke({
                "project": "myapp",
                "session_name": "feature-1",
                "working_dir": "/tmp/work",
            })

        assert isinstance(result, str)
        assert result.startswith("[SUCCESS]")
        assert "feature-1" in result
        assert "session-abc-123" in result
        assert "/tmp/work" in result
        mock_send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_init_session_returns_error_on_failure(
        self,
        mock_manager: MagicMock,
    ) -> None:
        """Failed init returns an ``[ERROR]`` formatted string."""
        err_response = OpenCodeResponse(
            status="error",
            message="OpenCode unreachable",
            session_id=None,
            data=None,
        )
        with patch(
            "daemon.tools.external_opencode._server_send_message",
            new_callable=AsyncMock,
            return_value=err_response,
        ):
            tools = create_opencode_tools(mock_manager, "test-id")
            init_tool = next(t for t in tools if t.name == "external_opencode_init_session")

            result = await init_tool.ainvoke({
                "project": "myapp",
                "session_name": "feature-1",
                "working_dir": "/tmp/work",
            })

        assert isinstance(result, str)
        assert result.startswith("[ERROR]")
        assert "OpenCode unreachable" in result


class TestSendMessageExecution:
    """End-to-end test of ``external_opencode_send_message``."""

    @pytest.mark.asyncio
    async def test_send_message_returns_submitted_on_ok(
        self,
        mock_manager: MagicMock,
        mock_registry: AsyncMock,
    ) -> None:
        """Successful send returns a ``[SUBMITTED]`` formatted string."""
        mock_registry.get_session_record = AsyncMock(
            return_value={"id": "session-xyz", "state": "IDLE"}
        )
        ok_response = OpenCodeResponse(status="ok", message="queued")
        with patch(
            "daemon.tools.external_opencode._server_send_message",
            new_callable=AsyncMock,
            return_value=ok_response,
        ) as mock_send:
            tools = create_opencode_tools(mock_manager, "test-id")
            send_tool = next(t for t in tools if t.name == "external_opencode_send_message")

            result = await send_tool.ainvoke({
                "project": "myapp",
                "session_name": "feature-1",
                "message": "Write a hello world function",
            })

        assert isinstance(result, str)
        assert result.startswith("[SUBMITTED]")
        # The request passed to the dispatcher should reference the correct session
        call_args = mock_send.call_args
        req = call_args.args[0] if call_args.args else call_args.kwargs["request"]
        assert isinstance(req, OpenCodeRequest)
        assert req.action == "PROMPT"
        assert req.session_id == "session-xyz"
        # The default model should be encoded in camelCase
        assert req.payload["model"]["providerID"] == "litellm"
        assert req.payload["model"]["modelID"] == "coding"

    @pytest.mark.asyncio
    async def test_send_message_returns_error_when_session_not_found(
        self,
        mock_manager: MagicMock,
        mock_registry: AsyncMock,
    ) -> None:
        """Send returns ``[ERROR]`` if the session is not in the registry."""
        mock_registry.get_session_record = AsyncMock(return_value=None)
        tools = create_opencode_tools(mock_manager, "test-id")
        send_tool = next(t for t in tools if t.name == "external_opencode_send_message")

        result = await send_tool.ainvoke({
            "project": "myapp",
            "session_name": "missing",
            "message": "hi",
        })

        assert isinstance(result, str)
        assert result.startswith("[ERROR]")
        assert "missing" in result

    @pytest.mark.asyncio
    async def test_send_message_council_true_appends_council_hint(
        self,
        mock_manager: MagicMock,
        mock_registry: AsyncMock,
    ) -> None:
        """``council=True`` appends the COUNCIL_HINT trailer to the outbound text."""
        from daemon.opencode.constants import COUNCIL_HINT

        mock_registry.get_session_record = AsyncMock(
            return_value={"id": "session-council", "state": "IDLE"}
        )
        ok_response = OpenCodeResponse(status="ok", message="queued")
        with patch(
            "daemon.tools.external_opencode._server_send_message",
            new_callable=AsyncMock,
            return_value=ok_response,
        ) as mock_send:
            tools = create_opencode_tools(mock_manager, "test-id")
            send_tool = next(
                t for t in tools if t.name == "external_opencode_send_message"
            )

            await send_tool.ainvoke({
                "project": "myapp",
                "session_name": "review-deep",
                "message": "Deep-Review the payment module",
                "council": True,
            })

        req = mock_send.call_args.args[0]
        text = req.payload["parts"][0]["text"]
        assert text == "Deep-Review the payment module" + COUNCIL_HINT
        assert "@council subagent-tool" in text

    @pytest.mark.asyncio
    async def test_send_message_council_false_does_not_append_hint(
        self,
        mock_manager: MagicMock,
        mock_registry: AsyncMock,
    ) -> None:
        """``council=False`` (default) sends the message verbatim."""
        from daemon.opencode.constants import COUNCIL_HINT

        mock_registry.get_session_record = AsyncMock(
            return_value={"id": "session-no-council", "state": "IDLE"}
        )
        ok_response = OpenCodeResponse(status="ok", message="queued")
        with patch(
            "daemon.tools.external_opencode._server_send_message",
            new_callable=AsyncMock,
            return_value=ok_response,
        ) as mock_send:
            tools = create_opencode_tools(mock_manager, "test-id")
            send_tool = next(
                t for t in tools if t.name == "external_opencode_send_message"
            )

            await send_tool.ainvoke({
                "project": "myapp",
                "session_name": "feature-1",
                "message": "Write a hello world function",
            })

        req = mock_send.call_args.args[0]
        text = req.payload["parts"][0]["text"]
        assert text == "Write a hello world function"
        assert COUNCIL_HINT not in text


class TestGetStatusExecution:
    """End-to-end test of ``external_opencode_get_status``."""

    @pytest.mark.asyncio
    async def test_get_status_formats_state_and_response(
        self,
        mock_manager: MagicMock,
        mock_registry: AsyncMock,
    ) -> None:
        """Status output includes state, last activity, and latest response."""
        mock_registry.get_session_record = AsyncMock(
            return_value={
                "id": "session-1",
                "state": "BUSY",
                "last_activity": "2026-06-07T00:00:00Z",
            }
        )
        ok_response = OpenCodeResponse(
            status="ok",
            data={
                "state": "BUSY",
                "latest_response": "Working on it...",
                "questions": [],
            },
        )
        with patch(
            "daemon.tools.external_opencode._server_send_message",
            new_callable=AsyncMock,
            return_value=ok_response,
        ):
            tools = create_opencode_tools(mock_manager, "test-id")
            status_tool = next(t for t in tools if t.name == "external_opencode_get_status")

            result = await status_tool.ainvoke({
                "project": "myapp",
                "session_name": "feature-1",
            })

        assert isinstance(result, str)
        assert "State: BUSY" in result
        assert "Last Activity: 2026-06-07T00:00:00Z" in result
        assert "Working on it..." in result

    @pytest.mark.asyncio
    async def test_get_status_surfaces_worker_error(
        self,
        mock_manager: MagicMock,
        mock_registry: AsyncMock,
    ) -> None:
        """When latest_response is an error dict, get_status reports [ERROR]."""
        mock_registry.get_session_record = AsyncMock(
            return_value={
                "id": "session-failed",
                "state": "IDLE",
                "last_activity": "2026-06-07T00:00:00Z",
            }
        )
        error_response = OpenCodeResponse(
            status="ok",
            data={
                "state": "IDLE",
                "latest_response": {
                    "error": "API Error 500: Unexpected server error",
                },
            },
        )
        with patch(
            "daemon.tools.external_opencode._server_send_message",
            new_callable=AsyncMock,
            return_value=error_response,
        ):
            tools = create_opencode_tools(mock_manager, "test-id")
            status_tool = next(t for t in tools if t.name == "external_opencode_get_status")

            result = await status_tool.ainvoke({
                "project": "myapp",
                "session_name": "feature-1",
            })

        assert isinstance(result, str)
        assert "[ERROR]" in result
        assert "API Error 500" in result

    @pytest.mark.asyncio
    async def test_get_status_with_error_none_uses_fallback_message(
        self,
        mock_manager: MagicMock,
        mock_registry: AsyncMock,
    ) -> None:
        """When latest_response has ``{"error": None}``, get_status falls back to
        ``unknown error`` rather than rendering literal ``None``.

        Regression: the previous check ``response.get('error')`` returned
        ``None`` for an explicit-null error, so the agent saw
        ``[ERROR] Worker request failed: None`` and could mistake the
        literal string ``None`` for a real error message. Now the
        ``or "unknown error"`` substitution kicks in.
        """
        mock_registry.get_session_record = AsyncMock(
            return_value={
                "id": "session-empty-error",
                "state": "IDLE",
                "last_activity": "2026-06-07T00:00:00Z",
            }
        )
        none_error_response = OpenCodeResponse(
            status="ok",
            data={
                "state": "IDLE",
                "latest_response": {"error": None},
            },
        )
        with patch(
            "daemon.tools.external_opencode._server_send_message",
            new_callable=AsyncMock,
            return_value=none_error_response,
        ):
            tools = create_opencode_tools(mock_manager, "test-id")
            status_tool = next(t for t in tools if t.name == "external_opencode_get_status")

            result = await status_tool.ainvoke({
                "project": "myapp",
                "session_name": "feature-1",
            })

        assert isinstance(result, str)
        assert "[ERROR]" in result
        assert "unknown error" in result
        # Critical: must not render the literal string "None" in place of
        # the error message — that confused agents into thinking the
        # error was the string "None".
        assert "None" not in result

    @pytest.mark.asyncio
    async def test_get_status_returns_latest_error_without_stale_leak(
        self,
        mock_manager: MagicMock,
        mock_registry: AsyncMock,
    ) -> None:
        """A stale-error sequence: consecutive ``get_status`` calls
        consistently reflect the current ``latest_response`` value with
        no leftover state between calls.

        This test exercises the dispatcher side of the contract: every
        ``get_status`` call issues a fresh ``GET_STATUS`` round-trip and
        renders whatever the dispatcher returns at that instant — there
        must be no in-memory cache that would let a prior error "leak"
        into a subsequent call. The dispatcher mock returns three
        different payloads in sequence (error → success → error). The
        first error must not contaminate the success call, and the
        second error must replace the first cleanly.
        """
        mock_registry.get_session_record = AsyncMock(
            return_value={
                "id": "session-evolving",
                "state": "IDLE",
                "last_activity": "2026-06-07T00:00:00Z",
            }
        )

        # Three consecutive dispatcher payloads: error → success → error.
        # The success response must NOT contain any [ERROR] marker (proving
        # the first error did not leak), and the final error response must
        # only mention the second error string (proving the first error
        # was cleanly replaced).
        error_resp1 = OpenCodeResponse(
            status="ok",
            data={
                "state": "IDLE",
                "latest_response": {"error": "first error"},
            },
        )
        success_resp = OpenCodeResponse(
            status="ok",
            data={
                "state": "IDLE",
                "latest_response": "all good",
            },
        )
        error_resp2 = OpenCodeResponse(
            status="ok",
            data={
                "state": "IDLE",
                "latest_response": {"error": "second error"},
            },
        )
        with patch(
            "daemon.tools.external_opencode._server_send_message",
            new_callable=AsyncMock,
            side_effect=[error_resp1, success_resp, error_resp2],
        ):
            tools = create_opencode_tools(mock_manager, "test-id")
            status_tool = next(t for t in tools if t.name == "external_opencode_get_status")

            # Call 1: dispatcher returns "first error" — result must surface it.
            result1 = await status_tool.ainvoke({
                "project": "myapp",
                "session_name": "feature-1",
            })
            # Call 2: dispatcher returns a clean success — no error leakage
            # from call 1 should appear here.
            result2 = await status_tool.ainvoke({
                "project": "myapp",
                "session_name": "feature-1",
            })
            # Call 3: dispatcher returns "second error" — result must reflect
            # only the current payload, not any residue from call 1.
            result3 = await status_tool.ainvoke({
                "project": "myapp",
                "session_name": "feature-1",
            })

        assert isinstance(result1, str)
        assert isinstance(result2, str)
        assert isinstance(result3, str)

        # Call 1: surfaces the first error
        assert "[ERROR]" in result1
        assert "first error" in result1

        # Call 2: NO error marker — the first error must NOT leak into a
        # clean success response.
        assert "[ERROR]" not in result2
        # Call 2 should instead show the success response verbatim.
        assert "all good" in result2
        # The first error's text must be entirely absent from call 2.
        assert "first error" not in result2

        # Call 3: surfaces the second error AND must NOT mention the first
        # (i.e. errors are not concatenated — the dispatcher payload is
        # the sole source of truth per call).
        assert "[ERROR]" in result3
        assert "second error" in result3
        assert "first error" not in result3


class TestAnswerQuestionExecution:
    """End-to-end test of ``external_opencode_answer_question``."""

    @pytest.mark.asyncio
    async def test_answer_question_uses_camelcase_request_id(
        self,
        mock_manager: MagicMock,
        mock_registry: AsyncMock,
    ) -> None:
        """The answer payload uses ``requestID`` (camelCase) per OpenCode API."""
        mock_registry.get_session_record = AsyncMock(
            return_value={"id": "session-42"}
        )
        ok_response = OpenCodeResponse(status="ok", message="answer recorded")
        with patch(
            "daemon.tools.external_opencode._server_send_message",
            new_callable=AsyncMock,
            return_value=ok_response,
        ) as mock_send:
            tools = create_opencode_tools(mock_manager, "test-id")
            answer_tool = next(
                t for t in tools if t.name == "external_opencode_answer_question"
            )

            result = await answer_tool.ainvoke({
                "project": "myapp",
                "session_name": "feature-1",
                "request_id": "q-9999",
                "answers": ["yes", "no"],
            })

        assert isinstance(result, str)
        assert result.startswith("[ANSWERED]")
        req = mock_send.call_args.args[0]
        assert req.payload["requestID"] == "q-9999"
        assert req.payload["answers"] == [["yes", "no"]]


class TestAbortSessionExecution:
    """End-to-end test of ``external_opencode_abort_session``."""

    @pytest.mark.asyncio
    async def test_abort_session_returns_aborted_on_ok(
        self,
        mock_manager: MagicMock,
    ) -> None:
        """Successful abort returns a ``[ABORTED]`` formatted string."""
        ok_response = OpenCodeResponse(status="ok", message="session aborted")
        with patch(
            "daemon.tools.external_opencode._server_send_message",
            new_callable=AsyncMock,
            return_value=ok_response,
        ) as mock_send:
            tools = create_opencode_tools(mock_manager, "test-id")
            abort_tool = next(
                t for t in tools if t.name == "external_opencode_abort_session"
            )

            result = await abort_tool.ainvoke({
                "project": "myapp",
                "session_name": "feature-1",
            })

        assert isinstance(result, str)
        assert result.startswith("[ABORTED]")
        # The abort action does NOT need to look up the session
        req = mock_send.call_args.args[0]
        assert req.action == "ABORT_SESSION"
        assert req.payload == {"project": "myapp", "session_name": "feature-1"}


# =============================================================================
# wait_for_result execution tests
# =============================================================================


class TestWaitForResultExecution:
    """End-to-end tests of ``external_opencode_wait_for_result``.

    This tool has a polling loop that alternates GET_STATUS requests with
    ``asyncio.sleep(POLL_INTERVAL_S)`` (30s in production). To keep the tests
    fast, we patch ``asyncio.sleep`` to a no-op coroutine and control the
    session state returned by the dispatcher.
    """

    @pytest.mark.asyncio
    async def test_wait_for_result_returns_completed_on_idle(
        self,
        mock_manager: MagicMock,
        mock_registry: AsyncMock,
    ) -> None:
        """When the session state is IDLE, returns ``[COMPLETED]`` immediately."""
        mock_registry.get_session_record = AsyncMock(
            return_value={"id": "session-idle"}
        )
        ok_response = OpenCodeResponse(
            status="ok",
            data={"state": "IDLE", "latest_response": "done!"},
        )
        with patch(
            "daemon.tools.external_opencode._server_send_message",
            new_callable=AsyncMock,
            return_value=ok_response,
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
                "session_name": "feature-1",
            })

        assert isinstance(result, str)
        assert result.startswith("[COMPLETED]")

    @pytest.mark.asyncio
    async def test_wait_for_result_returns_waiting_for_input(
        self,
        mock_manager: MagicMock,
        mock_registry: AsyncMock,
    ) -> None:
        """When the session is WAITING_FOR_INPUT, returns ``[WAITING_FOR_INPUT]``."""
        mock_registry.get_session_record = AsyncMock(
            return_value={"id": "session-ask"}
        )
        ok_response = OpenCodeResponse(
            status="ok",
            data={"state": "WAITING_FOR_INPUT", "questions": [{"id": "q1"}]},
        )
        with patch(
            "daemon.tools.external_opencode._server_send_message",
            new_callable=AsyncMock,
            return_value=ok_response,
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
                "session_name": "feature-1",
            })

        assert isinstance(result, str)
        assert result.startswith("[WAITING_FOR_INPUT]")

    @pytest.mark.asyncio
    async def test_wait_for_result_inlines_questions_when_waiting_for_input(
        self,
        mock_manager: MagicMock,
        mock_registry: AsyncMock,
    ) -> None:
        """When the session is WAITING_FOR_INPUT, the response inlines the
        pending questions and hints at ``answer_question`` so the caller does
        not have to issue a follow-up ``external_opencode_get_status`` call.
        """
        mock_registry.get_session_record = AsyncMock(
            return_value={"id": "session-ask"}
        )
        ok_response = OpenCodeResponse(
            status="ok",
            data={
                "state": "WAITING_FOR_INPUT",
                "questions": [
                    {"id": "req-1", "questions": ["Option A", "Option B"]},
                    {"id": "req-2", "questions": ["Yes", "No"]},
                ],
            },
        )
        with patch(
            "daemon.tools.external_opencode._server_send_message",
            new_callable=AsyncMock,
            return_value=ok_response,
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
                "session_name": "feature-1",
            })

        assert isinstance(result, str)
        assert result.startswith("[WAITING_FOR_INPUT]")
        # Pointer to the right next tool
        assert "external_opencode_answer_question" in result
        # Questions inlined — caller has everything needed without a 2nd call
        assert "Questions:" in result
        assert "[?] req-1: ['Option A', 'Option B']" in result
        assert "[?] req-2: ['Yes', 'No']" in result

    @pytest.mark.asyncio
    async def test_wait_for_result_returns_timeout_when_never_completes(
        self,
        mock_manager: MagicMock,
        mock_registry: AsyncMock,
    ) -> None:
        """If the session never reaches IDLE/WAITING_FOR_INPUT, returns ``[TIMEOUT]``.

        We patch ``asyncio.sleep`` to a no-op so the loop spins through all
        its iterations without real delays. The session state stays ``BUSY``
        every poll, so the loop exhausts the deadline and returns ``[TIMEOUT]``.
        ``WAIT_TIMEOUT_S`` is patched down to keep the test fast.
        """
        mock_registry.get_session_record = AsyncMock(
            return_value={"id": "session-busy"}
        )
        busy_response = OpenCodeResponse(
            status="ok",
            data={"state": "BUSY", "latest_response": "still working..."},
        )
        with patch(
            "daemon.tools.external_opencode._server_send_message",
            new_callable=AsyncMock,
            return_value=busy_response,
        ) as mock_send, patch(
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
                "session_name": "feature-1",
            })

        assert isinstance(result, str)
        assert result.startswith("[TIMEOUT]")
        # The poll loop ran at least once before timing out.
        assert mock_send.await_count >= 1

    @pytest.mark.asyncio
    async def test_wait_for_result_timeout_includes_last_observed_message(
        self,
        mock_manager: MagicMock,
        mock_registry: AsyncMock,
    ) -> None:
        """On timeout while still BUSY, the response embeds the last snapshot.

        The agent should see ``[STATE]`` and the last observed message
        (here just one because no in-memory manager ring is populated) so
        it can decide whether to resume, abort, or keep waiting — without
        making a separate status call. Header reads ``[LAST 1 MESSAGE]``
        in this single-message case (``[LAST N MESSAGES]`` for N>1).
        """
        mock_registry.get_session_record = AsyncMock(
            return_value={"id": "session-busy"}
        )
        busy_response = OpenCodeResponse(
            status="ok",
            data={
                "state": "BUSY",
                "latest_response": {"result": "mid-stream progress..."},
            },
        )
        with patch(
            "daemon.tools.external_opencode._server_send_message",
            new_callable=AsyncMock,
            return_value=busy_response,
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
                "session_name": "feature-1",
            })

        assert isinstance(result, str)
        assert result.startswith("[TIMEOUT]")
        assert "[STATE] BUSY" in result
        # Single-message case: header is "[LAST 1 MESSAGE]" (singular).
        assert "[LAST 1 MESSAGE]" in result
        assert "mid-stream progress..." in result
        assert "external_opencode_resume_session" in result

    @pytest.mark.asyncio
    async def test_wait_for_result_timeout_fetches_last_3_messages_via_api(
        self,
        mock_manager: MagicMock,
        mock_registry: AsyncMock,
    ) -> None:
        """On timeout the tool calls ``get_session_messages(limit=3)`` directly
        on the manager's client and renders all 3 messages in chronological
        order under a ``[LAST 3 MESSAGES]`` header.

        This is the new direct-API contract: one ``GET /session/{id}/message``
        request at the moment of timeout, no in-memory ring buffer. The
        call is on ``manager._client`` (the underlying HTTP client), not
        on the session manager.
        """
        mock_registry.get_session_record = AsyncMock(
            return_value={"id": "session-busy"}
        )
        # Manager exists and its _client.get_session_messages returns
        # 3 messages, newest first. The wait_for_result path calls it
        # directly on the manager's client.
        client = AsyncMock()
        client.get_session_messages = AsyncMock(return_value=[
            {"result": "step 3 — almost there"},
            {"result": "step 2 — making progress"},
            {"result": "step 1 — starting"},
        ])
        mock_mgr = MagicMock()
        mock_mgr.get_idle_event = MagicMock(return_value=None)
        mock_mgr.session_id = "session-busy"
        mock_mgr._client = client
        mock_registry.get_manager = AsyncMock(return_value=mock_mgr)

        busy_response = OpenCodeResponse(
            status="ok",
            data={
                "state": "BUSY",
                "latest_response": {"result": "step 3 — almost there"},
            },
        )
        with patch(
            "daemon.tools.external_opencode._server_send_message",
            new_callable=AsyncMock,
            return_value=busy_response,
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
                "session_name": "feature-1",
            })

        assert isinstance(result, str)
        assert result.startswith("[TIMEOUT]")
        assert "[STATE] BUSY" in result
        # Header reflects the 3 messages we returned.
        assert "[LAST 3 MESSAGES]" in result
        # The direct API call happened with limit=3.
        client.get_session_messages.assert_awaited_once_with(
            "session-busy", limit=3,
        )
        # All three messages appear, in chronological order
        # (oldest → newest) so progression reads top-to-bottom.
        assert "step 1 — starting" in result
        assert "step 2 — making progress" in result
        assert "step 3 — almost there" in result
        assert result.index("step 1") < result.index("step 2") < result.index("step 3")
        assert "external_opencode_resume_session" in result

    @pytest.mark.asyncio
    async def test_wait_for_result_timeout_renders_single_message(
        self,
        mock_manager: MagicMock,
        mock_registry: AsyncMock,
    ) -> None:
        """When the API returns only 1 message, render it under
        ``[LAST 1 MESSAGE]`` (singular) and skip the others.

        Mirrors the partial-data path: no padding, no fabrication.
        """
        mock_registry.get_session_record = AsyncMock(
            return_value={"id": "session-busy"}
        )
        client = AsyncMock()
        client.get_session_messages = AsyncMock(return_value=[
            {"result": "only step"},
        ])
        mock_mgr = MagicMock()
        mock_mgr.get_idle_event = MagicMock(return_value=None)
        mock_mgr.session_id = "session-busy"
        mock_mgr._client = client
        mock_registry.get_manager = AsyncMock(return_value=mock_mgr)

        busy_response = OpenCodeResponse(
            status="ok",
            data={"state": "BUSY", "latest_response": {"result": "only step"}},
        )
        with patch(
            "daemon.tools.external_opencode._server_send_message",
            new_callable=AsyncMock,
            return_value=busy_response,
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
                "session_name": "feature-1",
            })

        assert isinstance(result, str)
        assert result.startswith("[TIMEOUT]")
        # Singular header — only one message.
        assert "[LAST 1 MESSAGE]" in result
        assert "only step" in result

    @pytest.mark.asyncio
    async def test_wait_for_result_timeout_falls_back_when_api_raises(
        self,
        mock_manager: MagicMock,
        mock_registry: AsyncMock,
    ) -> None:
        """If the direct API call raises, the timeout response falls back
        to ``latest_response`` from the last poll. No crash, no
        traceback leak into the agent's view.
        """
        mock_registry.get_session_record = AsyncMock(
            return_value={"id": "session-busy"}
        )
        client = AsyncMock()
        client.get_session_messages = AsyncMock(
            side_effect=RuntimeError("network down"),
        )
        mock_mgr = MagicMock()
        mock_mgr.get_idle_event = MagicMock(return_value=None)
        mock_mgr.session_id = "session-busy"
        mock_mgr._client = client
        mock_registry.get_manager = AsyncMock(return_value=mock_mgr)

        busy_response = OpenCodeResponse(
            status="ok",
            data={
                "state": "BUSY",
                "latest_response": {"result": "fallback message"},
            },
        )
        with patch(
            "daemon.tools.external_opencode._server_send_message",
            new_callable=AsyncMock,
            return_value=busy_response,
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
                "session_name": "feature-1",
            })

        assert isinstance(result, str)
        assert result.startswith("[TIMEOUT]")
        assert "[STATE] BUSY" in result
        # The fallback message is rendered (single-message header).
        assert "[LAST 1 MESSAGE]" in result
        assert "fallback message" in result

    @pytest.mark.asyncio
    async def test_wait_for_result_timeout_falls_back_when_api_returns_empty(
        self,
        mock_manager: MagicMock,
        mock_registry: AsyncMock,
    ) -> None:
        """If the direct API call returns an empty list, the timeout
        response falls back to ``latest_response`` from the last poll.
        """
        mock_registry.get_session_record = AsyncMock(
            return_value={"id": "session-busy"}
        )
        client = AsyncMock()
        client.get_session_messages = AsyncMock(return_value=[])
        mock_mgr = MagicMock()
        mock_mgr.get_idle_event = MagicMock(return_value=None)
        mock_mgr.session_id = "session-busy"
        mock_mgr._client = client
        mock_registry.get_manager = AsyncMock(return_value=mock_mgr)

        busy_response = OpenCodeResponse(
            status="ok",
            data={
                "state": "BUSY",
                "latest_response": {"result": "fallback message"},
            },
        )
        with patch(
            "daemon.tools.external_opencode._server_send_message",
            new_callable=AsyncMock,
            return_value=busy_response,
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
                "session_name": "feature-1",
            })

        assert isinstance(result, str)
        assert result.startswith("[TIMEOUT]")
        assert "[STATE] BUSY" in result
        # The fallback message is rendered.
        assert "[LAST 1 MESSAGE]" in result
        assert "fallback message" in result

    @pytest.mark.asyncio
    async def test_wait_for_result_timeout_without_any_poll(
        self,
        mock_manager: MagicMock,
        mock_registry: AsyncMock,
    ) -> None:
        """If no successful poll happened before the deadline, return fallback.

        Guards against crashing when ``last_resp`` is still ``None`` (e.g. the
        dispatcher errored on every poll). The fallback is the original
        short message without ``[STATE]`` / ``[LAST MESSAGE]`` sections.
        """
        mock_registry.get_session_record = AsyncMock(
            return_value={"id": "session-err"}
        )
        err_response = OpenCodeResponse(status="error", message="boom")
        with patch(
            "daemon.tools.external_opencode._server_send_message",
            new_callable=AsyncMock,
            return_value=err_response,
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
                "session_name": "feature-1",
            })

        assert isinstance(result, str)
        assert result.startswith("[TIMEOUT]")
        assert "[STATE]" not in result
        assert "[LAST MESSAGE]" not in result

    @pytest.mark.asyncio
    async def test_wait_for_result_returns_error_when_session_not_found(
        self,
        mock_manager: MagicMock,
        mock_registry: AsyncMock,
    ) -> None:
        """If the session is not in the registry, returns ``[ERROR]``."""
        mock_registry.get_session_record = AsyncMock(return_value=None)
        tools = create_opencode_tools(mock_manager, "test-id")
        wait_tool = next(
            t for t in tools if t.name == "external_opencode_wait_for_result"
        )

        result = await wait_tool.ainvoke({
            "project": "myapp",
            "session_name": "ghost",
        })

        assert isinstance(result, str)
        assert result.startswith("[ERROR]")
        assert "ghost" in result

    @pytest.mark.asyncio
    async def test_wait_for_result_wakes_on_idle_via_event(
        self,
        mock_manager: MagicMock,
        mock_registry: AsyncMock,
    ) -> None:
        """Manager's idle_event fires mid-wait → immediate wake, next poll sees IDLE."""
        mock_registry.get_session_record = AsyncMock(
            return_value={"id": "session-ev"}
        )
        idle_event = asyncio.Event()

        # Mock manager with real asyncio.Event
        mock_mgr = MagicMock()
        mock_mgr.get_idle_event = MagicMock(return_value=idle_event)
        mock_registry.get_manager = AsyncMock(return_value=mock_mgr)

        # First poll: BUSY. Second poll: IDLE (after event fires).
        busy_resp = OpenCodeResponse(
            status="ok",
            data={"state": "BUSY", "latest_response": "working..."},
        )
        idle_resp = OpenCodeResponse(
            status="ok",
            data={"state": "IDLE", "latest_response": "done!"},
        )

        async def _fire_event_soon():
            await asyncio.sleep(0.01)
            idle_event.set()

        with patch(
            "daemon.tools.external_opencode._server_send_message",
            new_callable=AsyncMock,
            side_effect=[busy_resp, idle_resp],
        ):
            tools = create_opencode_tools(mock_manager, "test-id")
            wait_tool = next(
                t for t in tools if t.name == "external_opencode_wait_for_result"
            )

            async with asyncio.TaskGroup() as tg:
                event_task = tg.create_task(_fire_event_soon())
                result_task = tg.create_task(wait_tool.ainvoke({
                    "project": "myapp",
                    "session_name": "feature-1",
                }))

        result = result_task.result()
        assert isinstance(result, str)
        assert result.startswith("[COMPLETED]")

    @pytest.mark.asyncio
    async def test_event_already_set_before_wait(
        self,
        mock_manager: MagicMock,
        mock_registry: AsyncMock,
    ) -> None:
        """If the idle_event is already set when wait_for_result starts, the first
        poll returns immediately on IDLE (no background setter needed).
        """
        mock_registry.get_session_record = AsyncMock(
            return_value={"id": "session-pre"}
        )

        idle_event = asyncio.Event()
        idle_event.set()  # Pre-set

        mock_mgr = MagicMock()
        mock_mgr.get_idle_event = MagicMock(return_value=idle_event)
        mock_registry.get_manager = AsyncMock(return_value=mock_mgr)

        idle_resp = OpenCodeResponse(
            status="ok",
            data={"state": "IDLE", "latest_response": "fast done"},
        )
        with patch(
            "daemon.tools.external_opencode._server_send_message",
            new_callable=AsyncMock,
            return_value=idle_resp,
        ):
            tools = create_opencode_tools(mock_manager, "test-id")
            wait_tool = next(
                t for t in tools if t.name == "external_opencode_wait_for_result"
            )

            result = await wait_tool.ainvoke({
                "project": "myapp",
                "session_name": "feature-1",
            })

        assert isinstance(result, str)
        assert result.startswith("[COMPLETED]")

    @pytest.mark.asyncio
    async def test_wait_for_result_returns_error_on_worker_http_500(
        self,
        mock_manager: MagicMock,
        mock_registry: AsyncMock,
    ) -> None:
        """When worker fails with HTTP 500, wait_for_result returns [ERROR].

        Bug fix: previously the IDLE state with ``{"error": ...}`` in
        ``latest_response`` was reported as ``[COMPLETED]`` — misleading
        the agent. Now the error is surfaced.
        """
        mock_registry.get_session_record = AsyncMock(
            return_value={"id": "session-failed"}
        )
        error_response = OpenCodeResponse(
            status="ok",
            data={
                "state": "IDLE",
                "latest_response": {
                    "error": "API Error 500: Unexpected server error",
                },
            },
        )
        with patch(
            "daemon.tools.external_opencode._server_send_message",
            new_callable=AsyncMock,
            return_value=error_response,
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
                "session_name": "feature-1",
            })

        assert isinstance(result, str)
        assert result.startswith("[ERROR]")
        assert "API Error 500" in result
        assert "[COMPLETED]" not in result

    @pytest.mark.asyncio
    async def test_wait_for_result_completed_on_success_path_unchanged(
        self,
        mock_manager: MagicMock,
        mock_registry: AsyncMock,
    ) -> None:
        """Regression: success path (latest_response={'result': ...}) still returns [COMPLETED]."""
        # Already covered by test_wait_for_result_returns_completed_on_idle but
        # explicitly verify the result-shape (not error-shape) goes through the
        # success branch.
        mock_registry.get_session_record = AsyncMock(
            return_value={"id": "session-success"}
        )
        ok_response = OpenCodeResponse(
            status="ok",
            data={
                "state": "IDLE",
                "latest_response": {"result": {"text": "all good"}},
            },
        )
        with patch(
            "daemon.tools.external_opencode._server_send_message",
            new_callable=AsyncMock,
            return_value=ok_response,
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
                "session_name": "feature-1",
            })

        assert isinstance(result, str)
        assert result.startswith("[COMPLETED]")
        assert "[ERROR]" not in result

    @pytest.mark.asyncio
    async def test_wait_for_result_with_waiting_input_and_error_returns_error(
        self,
        mock_manager: MagicMock,
        mock_registry: AsyncMock,
    ) -> None:
        """When session is WAITING_FOR_INPUT AND ``latest_response`` has an
        error, ``wait_for_result`` returns ``[ERROR]`` (NOT
        ``[WAITING_FOR_INPUT]``).

        Bug fix: previously the WAITING_FOR_INPUT branch returned
        immediately on pending questions, masking the underlying worker
        failure. An agent would see ``[WAITING_FOR_INPUT]`` and try to
        ``external_opencode_answer_question`` — but the session was
        already failed. Now we surface the error first and only return
        WAITING_FOR_INPUT when no error is present.
        """
        mock_registry.get_session_record = AsyncMock(
            return_value={"id": "session-failed-with-question"}
        )
        error_with_question_response = OpenCodeResponse(
            status="ok",
            data={
                "state": "WAITING_FOR_INPUT",
                "latest_response": {"error": "API Error 500: bad gateway"},
                "questions": [
                    {"id": "req-1", "questions": ["Approve?"]},
                ],
            },
        )
        with patch(
            "daemon.tools.external_opencode._server_send_message",
            new_callable=AsyncMock,
            return_value=error_with_question_response,
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
                "session_name": "feature-1",
            })

        assert isinstance(result, str)
        # Error wins over the WAITING_FOR_INPUT branch — the agent
        # should not be lured into calling answer_question on a
        # session whose worker already failed.
        assert result.startswith("[ERROR]")
        assert "[WAITING_FOR_INPUT]" not in result
        assert "API Error 500" in result


# =============================================================================
# wait_any execution tests
# =============================================================================


class TestWaitAnyExecution:
    """End-to-end tests of ``external_opencode_wait_any``.

    Like ``wait_for_result``, this tool polls in a loop; we patch
    ``asyncio.sleep`` to a no-op coroutine to keep tests fast.
    """

    @pytest.mark.asyncio
    async def test_wait_any_returns_summary_when_first_session_completes(
        self,
        mock_manager: MagicMock,
        mock_registry: AsyncMock,
    ) -> None:
        """Returns ``[SUMMARY]`` with 1/N completed when one session is IDLE."""
        # Both sessions resolve to records in the registry.
        mock_registry.get_session_record = AsyncMock(
            return_value={"id": "session-a"}
        )

        # The dispatcher returns IDLE for both — emulating both complete.
        completed_response = OpenCodeResponse(
            status="ok",
            data={"state": "IDLE", "latest_response": "finished"},
        )
        with patch(
            "daemon.tools.external_opencode._server_send_message",
            new_callable=AsyncMock,
            return_value=completed_response,
        ), patch(
            "daemon.tools.external_opencode.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            tools = create_opencode_tools(mock_manager, "test-id")
            wait_any_tool = next(
                t for t in tools if t.name == "external_opencode_wait_any"
            )

            result = await wait_any_tool.ainvoke({
                "sessions": [
                    {"project": "p1", "session_name": "s1"},
                    {"project": "p2", "session_name": "s2"},
                ],
            })

        assert isinstance(result, str)
        assert result.startswith("[SUMMARY]")
        # Both completed: 2/2
        assert "2/2" in result

    @pytest.mark.asyncio
    async def test_wait_any_marks_completed_and_running_sessions_in_summary(
        self,
        mock_manager: MagicMock,
        mock_registry: AsyncMock,
    ) -> None:
        """The summary shows ✓ for completed sessions and ... for running ones."""
        mock_registry.get_session_record = AsyncMock(
            return_value={"id": "session-x"}
        )

        # First session IDLE, second BUSY — but the dispatcher mock returns the
        # same response for every call. We use a side_effect to differentiate.
        idle_response = OpenCodeResponse(
            status="ok",
            data={"state": "IDLE", "latest_response": "done"},
        )
        busy_response = OpenCodeResponse(
            status="ok",
            data={"state": "BUSY", "latest_response": "running"},
        )
        with patch(
            "daemon.tools.external_opencode._server_send_message",
            new_callable=AsyncMock,
            side_effect=[idle_response, busy_response],
        ), patch(
            "daemon.tools.external_opencode.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            tools = create_opencode_tools(mock_manager, "test-id")
            wait_any_tool = next(
                t for t in tools if t.name == "external_opencode_wait_any"
            )

            result = await wait_any_tool.ainvoke({
                "sessions": [
                    {"project": "p1", "session_name": "s1"},
                    {"project": "p2", "session_name": "s2"},
                ],
            })

        assert isinstance(result, str)
        assert result.startswith("[SUMMARY]")
        # 1 of 2 completed
        assert "1/2" in result
        # The completed session is marked ✓, the running one with ...
        assert "✓" in result
        assert "..." in result

    @pytest.mark.asyncio
    async def test_wait_any_inlines_questions_for_waiting_session(
        self,
        mock_manager: MagicMock,
        mock_registry: AsyncMock,
    ) -> None:
        """When a session is WAITING_FOR_INPUT, its pending questions are
        inlined in the summary so the caller can answer with
        ``external_opencode_answer_question`` without a follow-up call.
        """
        mock_registry.get_session_record = AsyncMock(
            return_value={"id": "session-ask"}
        )
        waiting_response = OpenCodeResponse(
            status="ok",
            data={
                "state": "WAITING_FOR_INPUT",
                "questions": [
                    {"id": "req-9", "questions": ["Approve", "Reject"]},
                ],
            },
        )
        with patch(
            "daemon.tools.external_opencode._server_send_message",
            new_callable=AsyncMock,
            return_value=waiting_response,
        ), patch(
            "daemon.tools.external_opencode.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            tools = create_opencode_tools(mock_manager, "test-id")
            wait_any_tool = next(
                t for t in tools if t.name == "external_opencode_wait_any"
            )

            result = await wait_any_tool.ainvoke({
                "sessions": [{"project": "p1", "session_name": "s1"}],
            })

        assert isinstance(result, str)
        assert result.startswith("[SUMMARY]")
        # The waiting session is reported as a completed target in the
        # summary, but with the inlined WAITING_FOR_INPUT block.
        assert "[WAITING_FOR_INPUT]" in result
        assert "external_opencode_answer_question" in result
        assert "Questions:" in result
        assert "[?] req-9: ['Approve', 'Reject']" in result

    @pytest.mark.asyncio
    async def test_wait_any_separates_completed_and_waiting_sections(
        self,
        mock_manager: MagicMock,
        mock_registry: AsyncMock,
    ) -> None:
        """When one session is IDLE and another is WAITING_FOR_INPUT, the
        summary splits them into distinct sections with distinct markers.
        IDLE sessions go under ``COMPLETED RESPONSES``; WAITING_FOR_INPUT
        sessions go under ``WAITING FOR INPUT`` with a ``?`` marker.
        """
        mock_registry.get_session_record = AsyncMock(
            return_value={"id": "session-x"}
        )
        idle_response = OpenCodeResponse(
            status="ok",
            data={"state": "IDLE", "latest_response": "all done"},
        )
        waiting_response = OpenCodeResponse(
            status="ok",
            data={
                "state": "WAITING_FOR_INPUT",
                "questions": [{"id": "req-7", "questions": ["A", "B"]}],
            },
        )
        with patch(
            "daemon.tools.external_opencode._server_send_message",
            new_callable=AsyncMock,
            side_effect=[idle_response, waiting_response],
        ), patch(
            "daemon.tools.external_opencode.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            tools = create_opencode_tools(mock_manager, "test-id")
            wait_any_tool = next(
                t for t in tools if t.name == "external_opencode_wait_any"
            )

            result = await wait_any_tool.ainvoke({
                "sessions": [
                    {"project": "p1", "session_name": "s1"},
                    {"project": "p2", "session_name": "s2"},
                ],
            })

        assert isinstance(result, str)
        assert result.startswith("[SUMMARY]")
        # New summary header distinguishes done vs waiting
        assert "1/2 done" in result
        assert "1 waiting for input" in result
        # Distinct markers
        assert "✓" in result
        assert "?" in result
        # Distinct sections
        assert "COMPLETED RESPONSES" in result
        assert "WAITING FOR INPUT" in result
        # Completed response is rendered, waiting question is inlined
        assert "all done" in result
        assert "[?] req-7: ['A', 'B']" in result
        # Section ordering: completed first, waiting second
        assert result.index("COMPLETED RESPONSES") < result.index("WAITING FOR INPUT")

    @pytest.mark.asyncio
    async def test_wait_any_returns_error_on_empty_sessions(
        self,
        mock_manager: MagicMock,
    ) -> None:
        """An empty ``sessions`` list returns ``[ERROR] sessions list is empty``."""
        tools = create_opencode_tools(mock_manager, "test-id")
        wait_any_tool = next(
            t for t in tools if t.name == "external_opencode_wait_any"
        )

        result = await wait_any_tool.ainvoke({
            "sessions": [],
        })

        assert isinstance(result, str)
        assert result.startswith("[ERROR]")
        assert "empty" in result.lower()

    @pytest.mark.asyncio
    async def test_wait_any_returns_error_when_no_valid_sessions(
        self,
        mock_manager: MagicMock,
        mock_registry: AsyncMock,
    ) -> None:
        """When every session fails to resolve, returns ``[ERROR] No valid``."""
        mock_registry.get_session_record = AsyncMock(return_value=None)
        tools = create_opencode_tools(mock_manager, "test-id")
        wait_any_tool = next(
            t for t in tools if t.name == "external_opencode_wait_any"
        )

        result = await wait_any_tool.ainvoke({
            "sessions": [
                {"project": "p1", "session_name": "ghost1"},
                {"project": "p2", "session_name": "ghost2"},
            ],
        })

        assert isinstance(result, str)
        assert result.startswith("[ERROR]")
        assert "No valid sessions" in result

    @pytest.mark.asyncio
    async def test_wait_any_returns_timeout_when_none_complete(
        self,
        mock_manager: MagicMock,
        mock_registry: AsyncMock,
    ) -> None:
        """If no session completes within the deadline, returns ``[TIMEOUT]``."""
        mock_registry.get_session_record = AsyncMock(
            return_value={"id": "session-stuck"}
        )
        busy_response = OpenCodeResponse(
            status="ok",
            data={"state": "BUSY", "latest_response": "grinding..."},
        )
        with patch(
            "daemon.tools.external_opencode._server_send_message",
            new_callable=AsyncMock,
            return_value=busy_response,
        ), patch(
            "daemon.tools.external_opencode.asyncio.sleep",
            new_callable=AsyncMock,
        ), patch(
            "daemon.tools.external_opencode.WAIT_TIMEOUT_S", 0.05,
        ):
            tools = create_opencode_tools(mock_manager, "test-id")
            wait_any_tool = next(
                t for t in tools if t.name == "external_opencode_wait_any"
            )

            result = await wait_any_tool.ainvoke({
                "sessions": [
                    {"project": "p1", "session_name": "s1"},
                    {"project": "p2", "session_name": "s2"},
                ],
            })

        assert isinstance(result, str)
        assert result.startswith("[TIMEOUT]")

    @pytest.mark.asyncio
    async def test_wait_any_marks_errored_session_with_error_marker(
        self,
        mock_manager: MagicMock,
        mock_registry: AsyncMock,
    ) -> None:
        """When a session is IDLE with ``latest_response = {"error": ...}``,
        ``wait_any`` renders it with ``✗ [ERROR]`` (NOT ``✓ [OK]``).

        Bug fix: previously every IDLE session in the wait_any summary
        was marked ``✓`` even when the worker had failed with HTTP 500
        — misleading the agent into thinking the operation succeeded.
        Now errored sessions use ``✗`` and the worker-failure message
        is inlined in the COMPLETED RESPONSES section so the caller
        sees the failure without a follow-up ``get_status`` call.
        """
        mock_registry.get_session_record = AsyncMock(
            return_value={"id": "session-failed"}
        )
        error_response = OpenCodeResponse(
            status="ok",
            data={
                "state": "IDLE",
                "latest_response": {"error": "API Error 500: connection refused"},
            },
        )
        with patch(
            "daemon.tools.external_opencode._server_send_message",
            new_callable=AsyncMock,
            return_value=error_response,
        ), patch(
            "daemon.tools.external_opencode.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            tools = create_opencode_tools(mock_manager, "test-id")
            wait_any_tool = next(
                t for t in tools if t.name == "external_opencode_wait_any"
            )

            result = await wait_any_tool.ainvoke({
                "sessions": [{"project": "p1", "session_name": "s1"}],
            })

        assert isinstance(result, str)
        assert result.startswith("[SUMMARY]")
        # Error marker (✗), not success marker (✓). The session must
        # not be confused with a clean completion.
        assert "✗" in result
        assert "✓" not in result
        # Inlined worker-failure message in the COMPLETED RESPONSES
        # section so the caller can act on the error immediately.
        assert "[ERROR] Worker request failed" in result
        assert "API Error 500" in result

    @pytest.mark.asyncio
    async def test_wait_any_marks_errored_waiting_session_with_bang_marker(
        self,
        mock_manager: MagicMock,
        mock_registry: AsyncMock,
    ) -> None:
        """When a session is ``WAITING_FOR_INPUT`` AND its
        ``latest_response = {"error": ...}`` (i.e. the worker failed even
        though the dispatcher still has it parked waiting), ``wait_any``
        renders the summary line with the ``!`` marker (NOT ``?``) and
        surfaces the worker-failure message in the WAITING FOR INPUT
        section in place of the question prompt.

        Mirrors ``test_wait_any_marks_errored_session_with_error_marker``
        for the WAITING branch: a worker failure must never be hidden
        behind a normal "waiting for input" indicator — the agent caller
        must see ``✗ [ERROR]`` regardless of whether the session is IDLE
        or WAITING_FOR_INPUT.
        """
        mock_registry.get_session_record = AsyncMock(
            return_value={"id": "session-waiting-failed"}
        )
        error_waiting_response = OpenCodeResponse(
            status="ok",
            data={
                "state": "WAITING_FOR_INPUT",
                "latest_response": {"error": "worker crashed before prompt"},
            },
        )
        with patch(
            "daemon.tools.external_opencode._server_send_message",
            new_callable=AsyncMock,
            return_value=error_waiting_response,
        ), patch(
            "daemon.tools.external_opencode.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            tools = create_opencode_tools(mock_manager, "test-id")
            wait_any_tool = next(
                t for t in tools if t.name == "external_opencode_wait_any"
            )

            result = await wait_any_tool.ainvoke({
                "sessions": [{"project": "p1", "session_name": "s1"}],
            })

        assert isinstance(result, str)
        assert result.startswith("[SUMMARY]")
        # The summary line must use ``!`` for the errored waiting session
        # — NOT ``?`` (which is reserved for a genuine pending question).
        assert "!" in result
        assert "?" not in result
        # The body must render the worker-failure message in place of
        # the normal waiting-for-input prompt. Using the same exact
        # prefix as the COMPLETED branch keeps the agent caller-facing
        # surface uniform.
        assert "✗ [ERROR] Worker request failed:" in result
        assert "[WAITING_FOR_INPUT]" not in result
        # The error string from the dispatcher must be inlined so the
        # caller can act on it without a follow-up ``get_status`` call.
        assert "worker crashed before prompt" in result


# =============================================================================
# Registry-availability edge case
# =============================================================================


class TestRegistryAvailability:
    """Tests for the factory's behavior when the registry is missing."""

    def test_factory_raises_when_registry_missing(self) -> None:
        """Calling a tool whose execution requires the registry raises RuntimeError.

        The factory itself does not validate the registry — it only fails
        when a tool is invoked. We patch the dispatcher to bypass the actual
        call so we exercise the ``getattr(manager, '_opencode_registry', None)``
        guard inside the tool body.
        """
        manager = MagicMock(spec=[])  # No attributes at all
        with patch(
            "daemon.tools.external_opencode._server_send_message",
            new_callable=AsyncMock,
        ) as mock_send:
            tools = create_opencode_tools(manager, "test-id")
            init_tool = next(
                t for t in tools if t.name == "external_opencode_init_session"
            )

            import asyncio
            with pytest.raises(RuntimeError, match="registry not initialized"):
                asyncio.run(
                    init_tool.ainvoke({
                        "project": "p",
                        "session_name": "s",
                        "working_dir": "/tmp",
                    })
                )
        # The dispatcher should NOT have been reached
        mock_send.assert_not_awaited()


# =============================================================================
# Auto-Preload Context (shared context injection into send_message)
# =============================================================================


class TestSendMessageContextPreload:
    """Tests for automatic shared-context injection in ``send_message``.

    Mirrors the explore tool's behavior: before sending the prompt, the caller's
    shared-context directory is scanned and top-matching files are prepended
    to the message. Skipped for control messages.
    """

    @pytest.fixture
    def mock_manager_with_repo(self, mock_manager: MagicMock) -> MagicMock:
        """Manager with a stubbed ``_instance_repository``.

        ``get_tree_root_id`` returns the current instance id (no tree root),
        so the test can assert on the fallback path.
        """
        repo = MagicMock()
        repo.get_tree_root_id = MagicMock(return_value=None)
        mock_manager._instance_repository = repo
        return mock_manager

    @staticmethod
    def _captured_text(mock_send: AsyncMock) -> str:
        """Extract the outbound message text from the dispatched request."""
        req = mock_send.call_args.args[0]
        return req.payload["parts"][0]["text"]

    @pytest.mark.asyncio
    async def test_preload_prepends_matched_context(
        self,
        mock_manager_with_repo: MagicMock,
        mock_registry: AsyncMock,
    ) -> None:
        """When ``get_shared_context`` returns content, it is prepended to the message."""
        mock_registry.get_session_record = AsyncMock(
            return_value={"id": "session-1", "state": "IDLE"}
        )
        sentinel = "# Shared Context\ncontext_key: test-instance-id\n\n## Pre-loaded Context\n### foo (90% match)\nstuff\n"
        ok_response = OpenCodeResponse(status="ok", message="queued")
        with patch(
            "daemon.tools.external_opencode.get_shared_context",
            return_value=sentinel,
        ) as mock_get, patch(
            "daemon.tools.external_opencode._server_send_message",
            new_callable=AsyncMock,
            return_value=ok_response,
        ) as mock_send:
            tools = create_opencode_tools(mock_manager_with_repo, "test-instance-id")
            send_tool = next(
                t for t in tools if t.name == "external_opencode_send_message"
            )

            await send_tool.ainvoke({
                "project": "myapp",
                "session_name": "feature-1",
                "message": "Refactor the login flow",
            })

        mock_get.assert_called_once()
        text = self._captured_text(mock_send)
        assert sentinel in text
        assert text.endswith("Refactor the login flow")
        # Injection appears BEFORE the original message
        assert text.index(sentinel) < text.index("Refactor the login flow")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("control_msg", ["continue", "retry", "abort", "start-work"])
    async def test_control_messages_bypass_preload(
        self,
        mock_manager_with_repo: MagicMock,
        mock_registry: AsyncMock,
        control_msg: str,
    ) -> None:
        """Control commands are sent verbatim — no context lookup, no injection."""
        mock_registry.get_session_record = AsyncMock(
            return_value={"id": "session-1", "state": "IDLE"}
        )
        ok_response = OpenCodeResponse(status="ok", message="queued")
        with patch(
            "daemon.tools.external_opencode.get_shared_context",
            return_value="# Shared Context\ncontext_key: test-instance-id\n\nshould not appear\n",
        ) as mock_get, patch(
            "daemon.tools.external_opencode._server_send_message",
            new_callable=AsyncMock,
            return_value=ok_response,
        ) as mock_send:
            tools = create_opencode_tools(mock_manager_with_repo, "test-instance-id")
            send_tool = next(
                t for t in tools if t.name == "external_opencode_send_message"
            )

            await send_tool.ainvoke({
                "project": "myapp",
                "session_name": "feature-1",
                "message": control_msg,
            })

        mock_get.assert_not_called()
        assert self._captured_text(mock_send) == control_msg

    @pytest.mark.asyncio
    async def test_control_message_check_is_case_insensitive_and_stripped(
        self,
        mock_manager_with_repo: MagicMock,
        mock_registry: AsyncMock,
    ) -> None:
        """Control-message detection is case-insensitive and ignores whitespace."""
        mock_registry.get_session_record = AsyncMock(
            return_value={"id": "session-1", "state": "IDLE"}
        )
        ok_response = OpenCodeResponse(status="ok", message="queued")
        with patch(
            "daemon.tools.external_opencode.get_shared_context",
            return_value="# Shared Context\ncontext_key: test-instance-id\n\nshould not appear\n",
        ) as mock_get, patch(
            "daemon.tools.external_opencode._server_send_message",
            new_callable=AsyncMock,
            return_value=ok_response,
        ) as mock_send:
            tools = create_opencode_tools(mock_manager_with_repo, "test-instance-id")
            send_tool = next(
                t for t in tools if t.name == "external_opencode_send_message"
            )

            await send_tool.ainvoke({
                "project": "myapp",
                "session_name": "feature-1",
                "message": "  CONTINUE  ",
            })

        mock_get.assert_not_called()
        assert self._captured_text(mock_send) == "  CONTINUE  "

    @pytest.mark.asyncio
    async def test_empty_injection_leaves_message_unchanged(
        self,
        mock_manager_with_repo: MagicMock,
        mock_registry: AsyncMock,
    ) -> None:
        """When preload returns empty (no match), the message is sent verbatim."""
        mock_registry.get_session_record = AsyncMock(
            return_value={"id": "session-1", "state": "IDLE"}
        )
        ok_response = OpenCodeResponse(status="ok", message="queued")
        with patch(
            "daemon.tools.external_opencode.get_shared_context",
            return_value="",
        ), patch(
            "daemon.tools.external_opencode._server_send_message",
            new_callable=AsyncMock,
            return_value=ok_response,
        ) as mock_send:
            tools = create_opencode_tools(mock_manager_with_repo, "test-instance-id")
            send_tool = next(
                t for t in tools if t.name == "external_opencode_send_message"
            )

            await send_tool.ainvoke({
                "project": "myapp",
                "session_name": "feature-1",
                "message": "Write tests",
            })

        assert self._captured_text(mock_send) == "Write tests"

    @pytest.mark.asyncio
    async def test_preload_exception_does_not_break_send(
        self,
        mock_manager_with_repo: MagicMock,
        mock_registry: AsyncMock,
    ) -> None:
        """Exceptions in preload are swallowed; the message is still sent."""
        mock_registry.get_session_record = AsyncMock(
            return_value={"id": "session-1", "state": "IDLE"}
        )
        ok_response = OpenCodeResponse(status="ok", message="queued")
        with patch(
            "daemon.tools.external_opencode.get_shared_context",
            side_effect=RuntimeError("disk on fire"),
        ), patch(
            "daemon.tools.external_opencode._server_send_message",
            new_callable=AsyncMock,
            return_value=ok_response,
        ) as mock_send:
            tools = create_opencode_tools(mock_manager_with_repo, "test-instance-id")
            send_tool = next(
                t for t in tools if t.name == "external_opencode_send_message"
            )

            result = await send_tool.ainvoke({
                "project": "myapp",
                "session_name": "feature-1",
                "message": "Do the thing",
            })

        assert result.startswith("[SUBMITTED]")
        assert self._captured_text(mock_send) == "Do the thing"

    @pytest.mark.asyncio
    async def test_context_key_falls_back_to_instance_id(
        self,
        mock_manager_with_repo: MagicMock,
        mock_registry: AsyncMock,
    ) -> None:
        """When ``get_tree_root_id`` returns None, the instance id is used.

        Note: the second positional arg is the RESOLVED query (heuristic /
        LLM extract), not the raw message — see ``TestSendMessageKeywordResolver``
        for the keyword-resolution contract. This test only pins the context_key
        fallback behavior, so we suppress the heuristic with a real query.
        """
        mock_registry.get_session_record = AsyncMock(
            return_value={"id": "session-1", "state": "IDLE"}
        )
        ok_response = OpenCodeResponse(status="ok", message="queued")
        with patch(
            "daemon.tools.external_opencode.get_shared_context",
            return_value="",
        ) as mock_get, patch(
            "daemon.tools.external_opencode.extract_keywords",
            new_callable=AsyncMock,
            return_value=["fallback", "kw"],
        ), patch(
            "daemon.tools.external_opencode._server_send_message",
            new_callable=AsyncMock,
            return_value=ok_response,
        ):
            tools = create_opencode_tools(mock_manager_with_repo, "fallback-id")
            send_tool = next(
                t for t in tools if t.name == "external_opencode_send_message"
            )

            await send_tool.ainvoke({
                "project": "myapp",
                "session_name": "feature-1",
                "message": "Do the thing",
            })

        assert mock_get.call_args.args[0] == "fallback-id"
        assert mock_get.call_args.args[2] == "external"

    @pytest.mark.asyncio
    async def test_context_key_uses_tree_root_when_available(
        self,
        mock_manager_with_repo: MagicMock,
        mock_registry: AsyncMock,
    ) -> None:
        """When ``get_tree_root_id`` returns a value, it is preferred over the instance id.

        The resolved query (LLM extract here) is the second positional arg.
        See ``TestSendMessageKeywordResolver`` for the full keyword contract.
        """
        mock_manager_with_repo._instance_repository.get_tree_root_id = MagicMock(
            return_value="tree-root-xyz"
        )
        mock_registry.get_session_record = AsyncMock(
            return_value={"id": "session-1", "state": "IDLE"}
        )
        ok_response = OpenCodeResponse(status="ok", message="queued")
        with patch(
            "daemon.tools.external_opencode.get_shared_context",
            return_value="",
        ) as mock_get, patch(
            "daemon.tools.external_opencode.extract_keywords",
            new_callable=AsyncMock,
            return_value=["fallback", "kw"],
        ), patch(
            "daemon.tools.external_opencode._server_send_message",
            new_callable=AsyncMock,
            return_value=ok_response,
        ):
            tools = create_opencode_tools(mock_manager_with_repo, "child-instance-id")
            send_tool = next(
                t for t in tools if t.name == "external_opencode_send_message"
            )

            await send_tool.ainvoke({
                "project": "myapp",
                "session_name": "feature-1",
                "message": "Do the thing",
            })

        assert mock_get.call_args.args[0] == "tree-root-xyz"
        assert mock_get.call_args.args[2] == "external"

    @pytest.mark.asyncio
    async def test_council_trailer_is_appended_after_injection(
        self,
        mock_manager_with_repo: MagicMock,
        mock_registry: AsyncMock,
    ) -> None:
        """Order is: ``[injection] + [message + COUNCIL_HINT]`` (council at the very end)."""
        from daemon.opencode.constants import COUNCIL_HINT

        mock_registry.get_session_record = AsyncMock(
            return_value={"id": "session-1", "state": "IDLE"}
        )
        sentinel = "# Shared Context\ncontext_key: test-instance-id\n\nINJECTED\n"
        ok_response = OpenCodeResponse(status="ok", message="queued")
        with patch(
            "daemon.tools.external_opencode.get_shared_context",
            return_value=sentinel,
        ), patch(
            "daemon.tools.external_opencode._server_send_message",
            new_callable=AsyncMock,
            return_value=ok_response,
        ) as mock_send:
            tools = create_opencode_tools(mock_manager_with_repo, "test-instance-id")
            send_tool = next(
                t for t in tools if t.name == "external_opencode_send_message"
            )

            await send_tool.ainvoke({
                "project": "myapp",
                "session_name": "review",
                "message": "Deep-Review the payment module",
                "council": True,
            })

        text = self._captured_text(mock_send)
        assert text.startswith(sentinel)
        assert text.endswith("Deep-Review the payment module" + COUNCIL_HINT)


# =============================================================================
# Auto-Preload Context: related_context_keywords param + 3-step resolver
# =============================================================================


class TestSendMessageKeywordResolver:
    """Tests for the ``related_context_keywords`` param and the agent→LLM→heuristic
    fallback chain in ``_preload_shared_context``.

    The matcher only sees the resolved query string. The chain is observable
    through the second positional arg of ``get_shared_context`` and through
    the call/return of ``extract_keywords`` / ``_heuristic_keywords``.
    """

    @pytest.fixture
    def mock_manager_with_repo(self, mock_manager: MagicMock) -> MagicMock:
        repo = MagicMock()
        repo.get_tree_root_id = MagicMock(return_value=None)
        mock_manager._instance_repository = repo
        return mock_manager

    @staticmethod
    def _captured_text(mock_send: AsyncMock) -> str:
        req = mock_send.call_args.args[0]
        return req.payload["parts"][0]["text"]

    @staticmethod
    def _captured_query(mock_get) -> str:
        return mock_get.call_args.args[1]

    @pytest.mark.asyncio
    async def test_agent_keywords_used_directly(
        self,
        mock_manager_with_repo: MagicMock,
        mock_registry: AsyncMock,
    ) -> None:
        """Agent-provided keywords are joined with spaces and passed verbatim."""
        mock_registry.get_session_record = AsyncMock(
            return_value={"id": "session-1", "state": "IDLE"}
        )
        ok_response = OpenCodeResponse(status="ok", message="queued")
        with patch(
            "daemon.tools.external_opencode.get_shared_context",
            return_value="",
        ) as mock_get, patch(
            "daemon.tools.external_opencode.extract_keywords",
            new_callable=AsyncMock,
        ) as mock_extract, patch(
            "daemon.tools.external_opencode._heuristic_keywords",
        ) as mock_heur, patch(
            "daemon.tools.external_opencode._server_send_message",
            new_callable=AsyncMock,
            return_value=ok_response,
        ):
            tools = create_opencode_tools(mock_manager_with_repo, "test-instance-id")
            send_tool = next(
                t for t in tools if t.name == "external_opencode_send_message"
            )

            await send_tool.ainvoke({
                "project": "myapp",
                "session_name": "feature-1",
                "message": "Long prose prompt with many irrelevant tokens",
                "related_context_keywords": ["auth", "login"],
            })

        assert self._captured_query(mock_get) == "auth login"
        mock_extract.assert_not_awaited()
        mock_heur.assert_not_called()

    @pytest.mark.asyncio
    async def test_keywords_normalized_before_use(
        self,
        mock_manager_with_repo: MagicMock,
        mock_registry: AsyncMock,
    ) -> None:
        """Whitespace, dedupe, stop-words, and over-long entries are cleaned up."""
        mock_registry.get_session_record = AsyncMock(
            return_value={"id": "session-1", "state": "IDLE"}
        )
        ok_response = OpenCodeResponse(status="ok", message="queued")
        with patch(
            "daemon.tools.external_opencode.get_shared_context",
            return_value="",
        ) as mock_get, patch(
            "daemon.tools.external_opencode._server_send_message",
            new_callable=AsyncMock,
            return_value=ok_response,
        ):
            tools = create_opencode_tools(mock_manager_with_repo, "test-instance-id")
            send_tool = next(
                t for t in tools if t.name == "external_opencode_send_message"
            )

            await send_tool.ainvoke({
                "project": "myapp",
                "session_name": "feature-1",
                "message": "x",
                "related_context_keywords": [
                    "  AUTH ", "auth", "the", "", "x" * 50, "login",
                ],
            })

        assert self._captured_query(mock_get) == "AUTH login"

    @pytest.mark.asyncio
    async def test_keywords_accepts_string_form(
        self,
        mock_manager_with_repo: MagicMock,
        mock_registry: AsyncMock,
    ) -> None:
        """``related_context_keywords`` accepts a comma-separated string.

        Agents often pass keywords as a single string rather than a list.
        The tool must accept both shapes and normalize them to the same
        list passed to ``get_shared_context`` so the caller does not need
        to know which form is required.
        """
        mock_registry.get_session_record = AsyncMock(
            return_value={"id": "session-1", "state": "IDLE"}
        )
        ok_response = OpenCodeResponse(status="ok", message="queued")
        with patch(
            "daemon.tools.external_opencode.get_shared_context",
            return_value="",
        ) as mock_get, patch(
            "daemon.tools.external_opencode.extract_keywords",
            new_callable=AsyncMock,
        ) as mock_extract, patch(
            "daemon.tools.external_opencode._heuristic_keywords",
        ) as mock_heur, patch(
            "daemon.tools.external_opencode._server_send_message",
            new_callable=AsyncMock,
            return_value=ok_response,
        ):
            tools = create_opencode_tools(mock_manager_with_repo, "test-instance-id")
            send_tool = next(
                t for t in tools if t.name == "external_opencode_send_message"
            )

            await send_tool.ainvoke({
                "project": "myapp",
                "session_name": "feature-1",
                "message": "x",
                "related_context_keywords": "auth, login, billing",
            })

        assert self._captured_query(mock_get) == "auth login billing"
        mock_extract.assert_not_awaited()
        mock_heur.assert_not_called()

    @pytest.mark.asyncio
    async def test_keywords_accepts_json_array_string(
        self,
        mock_manager_with_repo: MagicMock,
        mock_registry: AsyncMock,
    ) -> None:
        """``related_context_keywords`` accepts a JSON-array-as-string.

        Agents often serialize a list of keywords as a single JSON string
        (e.g. ``'["a", "b", "c"]'``) because the tool schema advertises a
        string-or-list union. The tool must parse the JSON list, strip
        surrounding quotes/brackets, and pass clean tokens to the matcher.
        """
        mock_registry.get_session_record = AsyncMock(
            return_value={"id": "session-1", "state": "IDLE"}
        )
        ok_response = OpenCodeResponse(status="ok", message="queued")
        with patch(
            "daemon.tools.external_opencode.get_shared_context",
            return_value="",
        ) as mock_get, patch(
            "daemon.tools.external_opencode.extract_keywords",
            new_callable=AsyncMock,
        ) as mock_extract, patch(
            "daemon.tools.external_opencode._heuristic_keywords",
        ) as mock_heur, patch(
            "daemon.tools.external_opencode._server_send_message",
            new_callable=AsyncMock,
            return_value=ok_response,
        ):
            tools = create_opencode_tools(mock_manager_with_repo, "test-instance-id")
            send_tool = next(
                t for t in tools if t.name == "external_opencode_send_message"
            )

            await send_tool.ainvoke({
                "project": "myapp",
                "session_name": "feature-1",
                "message": "x",
                "related_context_keywords": (
                    '["git commit", "handle_magentic_reset", '
                    '"agent executor state clearing", "orchestration_manager"]'
                ),
            })

        assert self._captured_query(mock_get) == (
            "git commit handle_magentic_reset "
            "agent executor state clearing orchestration_manager"
        )
        mock_extract.assert_not_awaited()
        mock_heur.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_keywords_falls_back_to_llm(
        self,
        mock_manager_with_repo: MagicMock,
        mock_registry: AsyncMock,
    ) -> None:
        """``related_context_keywords=None`` → LLM extract path is tried."""
        mock_registry.get_session_record = AsyncMock(
            return_value={"id": "session-1", "state": "IDLE"}
        )
        ok_response = OpenCodeResponse(status="ok", message="queued")
        with patch(
            "daemon.tools.external_opencode.get_shared_context",
            return_value="",
        ) as mock_get, patch(
            "daemon.tools.external_opencode.extract_keywords",
            new_callable=AsyncMock,
            return_value=["llm", "result"],
        ) as mock_extract, patch(
            "daemon.tools.external_opencode._heuristic_keywords",
        ) as mock_heur, patch(
            "daemon.tools.external_opencode._server_send_message",
            new_callable=AsyncMock,
            return_value=ok_response,
        ):
            tools = create_opencode_tools(mock_manager_with_repo, "test-instance-id")
            send_tool = next(
                t for t in tools if t.name == "external_opencode_send_message"
            )

            await send_tool.ainvoke({
                "project": "myapp",
                "session_name": "feature-1",
                "message": "Long prose prompt",
            })

        mock_extract.assert_awaited_once_with("Long prose prompt")
        mock_heur.assert_not_called()
        assert self._captured_query(mock_get) == "llm result"

    @pytest.mark.asyncio
    async def test_heuristic_used_when_llm_returns_empty(
        self,
        mock_manager_with_repo: MagicMock,
        mock_registry: AsyncMock,
    ) -> None:
        """Empty LLM result → heuristic path takes over."""
        mock_registry.get_session_record = AsyncMock(
            return_value={"id": "session-1", "state": "IDLE"}
        )
        ok_response = OpenCodeResponse(status="ok", message="queued")
        with patch(
            "daemon.tools.external_opencode.get_shared_context",
            return_value="",
        ) as mock_get, patch(
            "daemon.tools.external_opencode.extract_keywords",
            new_callable=AsyncMock,
            return_value=[],
        ), patch(
            "daemon.tools.external_opencode._heuristic_keywords",
            return_value=["heuristic", "fallback"],
        ), patch(
            "daemon.tools.external_opencode._server_send_message",
            new_callable=AsyncMock,
            return_value=ok_response,
        ):
            tools = create_opencode_tools(mock_manager_with_repo, "test-instance-id")
            send_tool = next(
                t for t in tools if t.name == "external_opencode_send_message"
            )

            await send_tool.ainvoke({
                "project": "myapp",
                "session_name": "feature-1",
                "message": "msg",
            })

        assert self._captured_query(mock_get) == "heuristic fallback"

    @pytest.mark.asyncio
    async def test_no_keywords_anywhere_skips_preload(
        self,
        mock_manager_with_repo: MagicMock,
        mock_registry: AsyncMock,
    ) -> None:
        """When all three sources return empty, ``get_shared_context`` is not called."""
        mock_registry.get_session_record = AsyncMock(
            return_value={"id": "session-1", "state": "IDLE"}
        )
        ok_response = OpenCodeResponse(status="ok", message="queued")
        with patch(
            "daemon.tools.external_opencode.get_shared_context",
            return_value="",
        ) as mock_get, patch(
            "daemon.tools.external_opencode.extract_keywords",
            new_callable=AsyncMock,
            return_value=[],
        ), patch(
            "daemon.tools.external_opencode._heuristic_keywords",
            return_value=[],
        ), patch(
            "daemon.tools.external_opencode._server_send_message",
            new_callable=AsyncMock,
            return_value=ok_response,
        ) as mock_send:
            tools = create_opencode_tools(mock_manager_with_repo, "test-instance-id")
            send_tool = next(
                t for t in tools if t.name == "external_opencode_send_message"
            )

            await send_tool.ainvoke({
                "project": "myapp",
                "session_name": "feature-1",
                "message": "msg",
            })

        mock_get.assert_not_called()
        # Message is sent unchanged
        assert self._captured_text(mock_send) == "msg"

    @pytest.mark.asyncio
    async def test_agent_keywords_short_circuit_llm(
        self,
        mock_manager_with_repo: MagicMock,
        mock_registry: AsyncMock,
    ) -> None:
        """LLM is not awaited when agent-provided keywords are usable."""
        mock_registry.get_session_record = AsyncMock(
            return_value={"id": "session-1", "state": "IDLE"}
        )
        ok_response = OpenCodeResponse(status="ok", message="queued")
        with patch(
            "daemon.tools.external_opencode.get_shared_context",
            return_value="",
        ), patch(
            "daemon.tools.external_opencode.extract_keywords",
            new_callable=AsyncMock,
        ) as mock_extract, patch(
            "daemon.tools.external_opencode._server_send_message",
            new_callable=AsyncMock,
            return_value=ok_response,
        ):
            tools = create_opencode_tools(mock_manager_with_repo, "test-instance-id")
            send_tool = next(
                t for t in tools if t.name == "external_opencode_send_message"
            )

            await send_tool.ainvoke({
                "project": "myapp",
                "session_name": "feature-1",
                "message": "msg",
                "related_context_keywords": ["explicit", "list"],
            })

        mock_extract.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("control_msg", ["continue", "retry", "abort", "start-work"])
    async def test_control_messages_skip_entire_resolver(
        self,
        mock_manager_with_repo: MagicMock,
        mock_registry: AsyncMock,
        control_msg: str,
    ) -> None:
        """Control messages bypass preload entirely — no keywords, no extract, no heuristic."""
        mock_registry.get_session_record = AsyncMock(
            return_value={"id": "session-1", "state": "IDLE"}
        )
        ok_response = OpenCodeResponse(status="ok", message="queued")
        with patch(
            "daemon.tools.external_opencode.get_shared_context",
            return_value="",
        ) as mock_get, patch(
            "daemon.tools.external_opencode.extract_keywords",
            new_callable=AsyncMock,
        ) as mock_extract, patch(
            "daemon.tools.external_opencode._heuristic_keywords",
        ) as mock_heur, patch(
            "daemon.tools.external_opencode._server_send_message",
            new_callable=AsyncMock,
            return_value=ok_response,
        ):
            tools = create_opencode_tools(mock_manager_with_repo, "test-instance-id")
            send_tool = next(
                t for t in tools if t.name == "external_opencode_send_message"
            )

            await send_tool.ainvoke({
                "project": "myapp",
                "session_name": "feature-1",
                "message": control_msg,
                "related_context_keywords": ["should", "be", "ignored"],
            })

        mock_get.assert_not_called()
        mock_extract.assert_not_awaited()
        mock_heur.assert_not_called()

    @pytest.mark.asyncio
    async def test_keyword_extraction_exception_does_not_break_send(
        self,
        mock_manager_with_repo: MagicMock,
        mock_registry: AsyncMock,
    ) -> None:
        """``extract_keywords`` is best-effort and never raises — when the LLM
        path returns ``[]`` (its failure-mode contract), the heuristic still
        runs and the send still succeeds. (The internal try/except inside
        ``extract_keywords`` is tested in
        ``tests/unit/services/test_keyword_extraction.py``.)"""
        mock_registry.get_session_record = AsyncMock(
            return_value={"id": "session-1", "state": "IDLE"}
        )
        ok_response = OpenCodeResponse(status="ok", message="queued")
        with patch(
            "daemon.tools.external_opencode.get_shared_context",
            return_value="",
        ) as mock_get, patch(
            "daemon.tools.external_opencode.extract_keywords",
            new_callable=AsyncMock,
            return_value=[],
        ), patch(
            "daemon.tools.external_opencode._heuristic_keywords",
            return_value=["heur"],
        ), patch(
            "daemon.tools.external_opencode._server_send_message",
            new_callable=AsyncMock,
            return_value=ok_response,
        ):
            tools = create_opencode_tools(mock_manager_with_repo, "test-instance-id")
            send_tool = next(
                t for t in tools if t.name == "external_opencode_send_message"
            )

            await send_tool.ainvoke({
                "project": "myapp",
                "session_name": "feature-1",
                "message": "msg",
            })

        assert self._captured_query(mock_get) == "heur"


# =============================================================================
# wait_any event-based wake tests
# =============================================================================


class TestWaitAnyEventWake:
    """Tests for event-based wake in ``external_opencode_wait_any``."""

    @pytest.mark.asyncio
    async def test_wait_any_wakes_on_any_session_idle(
        self,
        mock_manager: MagicMock,
        mock_registry: AsyncMock,
    ) -> None:
        """When one session's idle_event fires, wait_any wakes immediately
        and returns the completed session.
        """
        mock_registry.get_session_record = AsyncMock(
            return_value={"id": "session-ev"}
        )

        idle_event_a = asyncio.Event()
        idle_event_b = asyncio.Event()

        # Two managers, each with its own event
        mock_mgr_a = MagicMock()
        mock_mgr_a.get_idle_event = MagicMock(return_value=idle_event_a)
        mock_mgr_b = MagicMock()
        mock_mgr_b.get_idle_event = MagicMock(return_value=idle_event_b)

        # Return different managers for different session IDs
        async def _get_manager(session_id: str):
            if "a" in session_id or session_id == "session-ev":
                return mock_mgr_a
            return mock_mgr_b

        mock_registry.get_manager = AsyncMock(side_effect=_get_manager)

        # First poll: both BUSY. Second poll: first IDLE, second BUSY.
        busy_resp = OpenCodeResponse(
            status="ok",
            data={"state": "BUSY", "latest_response": "working..."},
        )
        idle_resp = OpenCodeResponse(
            status="ok",
            data={"state": "IDLE", "latest_response": "done!"},
        )

        async def _fire_event_a_soon():
            await asyncio.sleep(0.01)
            idle_event_a.set()

        with patch(
            "daemon.tools.external_opencode._server_send_message",
            new_callable=AsyncMock,
            side_effect=[busy_resp, busy_resp, idle_resp, busy_resp],
        ):
            tools = create_opencode_tools(mock_manager, "test-id")
            wait_any_tool = next(
                t for t in tools if t.name == "external_opencode_wait_any"
            )

            async with asyncio.TaskGroup() as tg:
                event_task = tg.create_task(_fire_event_a_soon())
                result_task = tg.create_task(wait_any_tool.ainvoke({
                    "sessions": [
                        {"project": "p1", "session_name": "s1"},
                        {"project": "p2", "session_name": "s2"},
                    ],
                }))

        result = result_task.result()
        assert isinstance(result, str)
        assert result.startswith("[SUMMARY]")

    @pytest.mark.no_xdist
    @pytest.mark.asyncio
    async def test_wait_any_does_not_spin_when_event_pre_set(
        self,
        mock_manager: MagicMock,
        mock_registry: AsyncMock,
    ) -> None:
        """Regression: pre-set idle_event must not cause a tight loop.

        If the previous iteration left the event SET (e.g. it fired but the
        poll this iteration still reports BUSY for all sessions), the next
        ``ev.wait()`` would return immediately — burning CPU until the next
        poll completes. The fix is to ``event.clear()`` before awaiting.

        Symptom: ~10k+ poll calls per second instead of ~5 (one per
        POLL_INTERVAL_S). We count poll invocations via a side-effect
        mock; if the count exceeds a sane ceiling, the fix regressed.
        """
        mock_registry.get_session_record = AsyncMock(
            return_value={"id": "session-ev"}
        )

        idle_event = asyncio.Event()
        idle_event.set()  # pre-set: simulates "fired in prior iteration"

        mock_mgr = MagicMock()
        mock_mgr.get_idle_event = MagicMock(return_value=idle_event)
        mock_registry.get_manager = AsyncMock(return_value=mock_mgr)

        # All polls return BUSY. Loop will keep polling, but the
        # event-wait between polls must respect POLL_INTERVAL_S.
        busy_resp = OpenCodeResponse(
            status="ok",
            data={"state": "BUSY", "latest_response": "working..."},
        )

        # Use a fixture patch for POLL_INTERVAL_S to keep the test fast.
        # Patch the live name (``daemon.tools.external_opencode``) — the
        # ``from daemon.opencode.constants import POLL_INTERVAL_S`` at the
        # top of ``external_opencode`` bound the value to a local name that
        # is NOT updated by patching the constants module.
        from daemon.tools import external_opencode as ext_oc

        poll_count = 0

        async def _counting_poll(req, _reg):
            nonlocal poll_count
            poll_count += 1
            return busy_resp

        with patch(
            "daemon.tools.external_opencode._server_send_message",
            new=_counting_poll,
        ), patch.object(ext_oc, "POLL_INTERVAL_S", 0.2), patch(
            "daemon.tools.external_opencode.WAIT_TIMEOUT_S", 0.5,
        ):
            tools = create_opencode_tools(mock_manager, "test-id")
            wait_any_tool = next(
                t for t in tools if t.name == "external_opencode_wait_any"
            )

            result = await wait_any_tool.ainvoke({
                "sessions": [
                    {"project": "p1", "session_name": "s1"},
                ],
            })

        # Sanity: the loop should hit TIMEOUT (no completion).
        assert "[TIMEOUT]" in result
        # With clear-before-wait: ~2-3 polls in 0.5s (one per POLL_INTERVAL_S=0.2).
        # Without it: ~10k+ polls. Pick a ceiling that catches the bug
        # but tolerates CI scheduling jitter.
        assert poll_count <= 10, (
            f"wait_any spun (polls={poll_count} in 0.5s) — "
            "event.clear() before asyncio.wait() regressed"
        )

