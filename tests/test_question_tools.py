"""Tests for ``daemon.tools.question_tools.create_question_tools``.

Mirrors the structure of ``tests/test_todo_tools.py``:

  1. **Factory** — ``create_question_tools`` returns a single ``question``
     tool tagged ``_tool_category == "question"``.
  2. **Invocation** — each test exercises the tool through
     ``await tool.coroutine(...)`` (langchain ``@tool`` contract).
  3. **No-raise contract** — the tool surfaces all failure modes as a
     string ("ERROR: …" or the duplicate-pending message) and never
     raises.

The mock manager is the same pattern used by todo_tools: ``MagicMock``
with a real ``QuestionManager`` attached at ``_question_manager``. The
``set_question_pause_requested`` / ``is_question_pause_requested`` /
``clear_question_pause_requested`` methods are simple dict-backed stubs
on the mock — the tool only sets the flag, the graph's conditional
post-tools edge consumes it later.

The ``live_event_hub`` is wired with an ``AsyncMock`` for
``stream_question_pack`` so we exercise the full emission path; tests
that don't care about SSE simply pass a MagicMock whose async method
is a no-op.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from daemon.services.question_manager import QuestionManager


# =============================================================================
# Helpers
# =============================================================================


def _make_manager() -> MagicMock:
    """Build a mock ``InstanceManager`` with a real QuestionManager + pause flag dict.

    The question tools only touch three manager surfaces:
      * ``manager._question_manager`` — real ``QuestionManager`` so state
        mutations are exercised end-to-end (matches the TodoManager
        testing pattern — pure mocks hide real bugs).
      * ``manager.set_question_pause_requested(instance_id)`` — flag setter.
      * ``manager.is_question_pause_requested(instance_id)`` — flag getter
        used by graph routing, not by the tool itself but verified by the
        store+pause test to confirm the flag really flipped.
      * ``manager.clear_question_pause_requested(instance_id)`` — flag clear,
        called by ``question_pause_node`` after the cascade.
    """
    manager = MagicMock()
    manager._question_manager = QuestionManager()

    flag_state: dict[str, bool] = {}

    def _set(instance_id: str) -> None:
        flag_state[instance_id] = True

    def _is_set(instance_id: str) -> bool:
        return flag_state.get(instance_id, False)

    def _clear(instance_id: str) -> None:
        flag_state.pop(instance_id, None)

    manager.set_question_pause_requested.side_effect = _set
    manager.is_question_pause_requested.side_effect = _is_set
    manager.clear_question_pause_requested.side_effect = _clear

    return manager


def _build_tool(manager: MagicMock | None = None, live_event_hub=None):
    """Build the single ``question`` tool with default instance_id.

    Returns ``[question_tool]`` (the factory returns a list of one).
    """
    from daemon.tools.question_tools import create_question_tools

    if manager is None:
        manager = _make_manager()
    if live_event_hub is None:
        # AsyncMock so await stream_question_pack(...) returns a coroutine
        # that resolves to a MagicMock — the tool swallows the result.
        live_event_hub = MagicMock()
        live_event_hub.stream_question_pack = AsyncMock(return_value=MagicMock())

    return create_question_tools(
        manager=manager,
        current_instance_id="test-instance-id",
        live_event_hub=live_event_hub,
    )


# =============================================================================
# Store + pause
# =============================================================================


class TestQuestionToolStoreAndPause:
    """``question`` tool stores the pack and flips the pause flag."""

    async def test_question_tool_stores_pack_and_sets_pause_flag(self):
        """Valid input: pack lands in the manager, pause flag is set."""
        manager = _make_manager()
        tools = _build_tool(manager)
        question_tool = tools[0]

        result = await question_tool.coroutine(
            questions=[{"id": "approach", "text": "Approach A or B?"}],
        )

        # Pack stored on the real QuestionManager.
        stored = manager._question_manager.get_question_pack("test-instance-id")
        assert stored is not None
        assert stored.status == "pending"
        assert stored.questions[0].text == "Approach A or B?"
        assert stored.questions[0].id == "approach"

        # Pause flag flipped on the manager (consumed by graph routing).
        manager.set_question_pause_requested.assert_called_once_with("test-instance-id")
        assert manager.is_question_pause_requested("test-instance-id") is True

        # Tool returns a non-empty string — the echo contract is verified
        # in the dedicated test below.
        assert isinstance(result, str)
        assert result  # non-empty

    async def test_question_tool_returns_string_echoing_question_text(self):
        """Return value must be a string containing the question text (F7 compaction safety).

        After context compaction the AIMessage that issued the tool call may
        be lost; echoing Q text in the tool result lets the LLM correlate
        Q↔A from later context alone.
        """
        manager = _make_manager()
        tools = _build_tool(manager)
        question_tool = tools[0]

        result = await question_tool.coroutine(
            questions=[
                {"id": "approach", "text": "Approach A or B?"},
                {"id": "deadline", "text": "What's the deadline?"},
            ],
        )

        assert isinstance(result, str)
        # Each question's text must appear in the echo.
        assert "Approach A or B?" in result
        assert "What's the deadline?" in result


# =============================================================================
# Rejection paths
# =============================================================================


class TestQuestionToolRejections:
    """Failure modes surface as error strings — never as exceptions."""

    async def test_question_tool_rejects_duplicate_pending_pack(self):
        """A second call while a pack is still pending returns the documented error string."""
        manager = _make_manager()
        tools = _build_tool(manager)
        question_tool = tools[0]

        first = await question_tool.coroutine(
            questions=[{"text": "First question"}],
        )
        assert "First question" in first

        second = await question_tool.coroutine(
            questions=[{"text": "Second question"}],
        )

        assert isinstance(second, str)
        assert "Already have a pending question pack" in second
        # The first pack must still be the one stored — second call was rejected.
        stored = manager._question_manager.get_question_pack("test-instance-id")
        assert stored is not None
        assert stored.questions[0].text == "First question"

    async def test_question_tool_never_raises_on_bad_input(self):
        """Empty list, ``None``, and other bad inputs return a string — never raise.

        The tool surfaces all validation failures as ``ERROR: …`` strings
        so the agent can self-correct instead of crashing the graph.
        """
        manager = _make_manager()
        tools = _build_tool(manager)
        question_tool = tools[0]

        # None → error string
        none_result = await question_tool.coroutine(questions=None)
        assert isinstance(none_result, str)
        assert "ERROR" in none_result

        # Empty list → error string
        empty_result = await question_tool.coroutine(questions=[])
        assert isinstance(empty_result, str)
        assert "ERROR" in empty_result

        # Non-list input → error string (the tool's isinstance guard fires)
        bad_type_result = await question_tool.coroutine(questions="not a list")
        assert isinstance(bad_type_result, str)
        assert "ERROR" in bad_type_result

        # None of these calls should have stored anything or flipped the flag.
        assert manager._question_manager.get_question_pack("test-instance-id") is None
        manager.set_question_pause_requested.assert_not_called()
        assert manager.is_question_pause_requested("test-instance-id") is False