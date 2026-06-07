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

    The factory only calls ``get_session_record`` on the registry, so we
    only need to stub that one method for most tests.
    """
    registry = AsyncMock()
    registry.get_session_record = AsyncMock(return_value=None)
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
                "timeout": 60,
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
                "timeout": 60,
            })

        assert isinstance(result, str)
        assert result.startswith("[WAITING_FOR_INPUT]")

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
        ):
            tools = create_opencode_tools(mock_manager, "test-id")
            wait_tool = next(
                t for t in tools if t.name == "external_opencode_wait_for_result"
            )

            result = await wait_tool.ainvoke({
                "project": "myapp",
                "session_name": "feature-1",
                "timeout": 1,
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

        The agent should see ``[STATE]`` and ``[LAST MESSAGE]`` from the most
        recent GET_STATUS poll so it can decide whether to resume, abort, or
        keep waiting — without making a separate status call.
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
        ):
            tools = create_opencode_tools(mock_manager, "test-id")
            wait_tool = next(
                t for t in tools if t.name == "external_opencode_wait_for_result"
            )

            result = await wait_tool.ainvoke({
                "project": "myapp",
                "session_name": "feature-1",
                "timeout": 1,
            })

        assert isinstance(result, str)
        assert result.startswith("[TIMEOUT]")
        assert "[STATE] BUSY" in result
        assert "[LAST MESSAGE]" in result
        assert "mid-stream progress..." in result
        assert "external_opencode_resume_session" in result

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
        ):
            tools = create_opencode_tools(mock_manager, "test-id")
            wait_tool = next(
                t for t in tools if t.name == "external_opencode_wait_for_result"
            )

            result = await wait_tool.ainvoke({
                "project": "myapp",
                "session_name": "feature-1",
                "timeout": 1,
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
            "timeout": 60,
        })

        assert isinstance(result, str)
        assert result.startswith("[ERROR]")
        assert "ghost" in result


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
                "timeout": 60,
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
                "timeout": 60,
            })

        assert isinstance(result, str)
        assert result.startswith("[SUMMARY]")
        # 1 of 2 completed
        assert "1/2" in result
        # The completed session is marked ✓, the running one with ...
        assert "✓" in result
        assert "..." in result

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
            "timeout": 60,
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
            "timeout": 60,
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
                "timeout": 1,
            })

        assert isinstance(result, str)
        assert result.startswith("[TIMEOUT]")


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
