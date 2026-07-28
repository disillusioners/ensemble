"""Phase 3 wiring/API-contract tests for context-injection graph helpers.

Covers the per-turn plumbing that ``agent_node`` (and the
``_process_message_with_tracking`` build path) depends on for
Context Injection Restructure Phase 3:

* :class:`daemon.graph.ContextSlot` — assembles per-turn context
  messages and gates on ``context_injection_mode``.
* :func:`daemon.graph._extract_last_user_text` — extracts the last
  user text from the LangGraph ``messages`` list.
* :func:`daemon.graph._resolve_project_id` — resolves the project
  id from instance metadata.
* :func:`daemon.services.instance_messaging._build_graph_input` —
  builds the ``graph_input`` dict, honoring the
  ``context_injection_mode`` gate in ``human_messages`` mode.
* :meth:`daemon.manager.InstanceManager.set_context_skill_result`
  and :meth:`get_context_skill_result` — round-trip the
  per-instance skill-search cache.

These are wiring/API-contract tests — they exercise the public
signatures, mode gating, and indirection paths without spinning
up the full LangGraph pipeline.

Run only this file:

    uv run pytest tests/integration/test_context_in_graph.py -v --timeout=60
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Sequence, cast
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage


# ---------------------------------------------------------------------------
# Imports from the modules under test.
#
# The graph module has a known circular import with the services
# tree (see the lazy import inside ``ContextSlot.assemble``), but the
# module-level imports below are safe — they import only the helpers,
# not the functions that close the cycle.
# ---------------------------------------------------------------------------
from daemon.graph import (
    ContextSlot,
    _extract_last_user_text,
    _resolve_project_id,
)
from daemon.manager import InstanceManager
from daemon.services.instance_messaging import _build_graph_input


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def system_prompt_agent_meta() -> SimpleNamespace:
    """AgentMeta stub in legacy ``system_prompt`` mode (default)."""
    return SimpleNamespace(
        context_injection_mode="system_prompt",
        context_injection=False,
        skill_injection=False,
    )


@pytest.fixture
def human_messages_agent_meta() -> SimpleNamespace:
    """AgentMeta stub in opt-in ``human_messages`` mode."""
    return SimpleNamespace(
        context_injection_mode="human_messages",
        context_injection=True,
        skill_injection=True,
    )


@pytest.fixture
def stub_manager() -> SimpleNamespace:
    """Minimal manager stub exposing only the slots ContextSlot touches.

    ContextSlot reads ``get_context_skill_result`` via duck typing.
    Returning ``None`` from the getter by default matches the
    "no prior search ran" B3 case so ``assemble_context_messages``
    is expected to re-run the search when patched.
    """
    return SimpleNamespace(
        get_context_skill_result=lambda instance_id: None,
    )


# ---------------------------------------------------------------------------
# 1. ContextSlot.assemble() returns [] in system_prompt mode
# ---------------------------------------------------------------------------


class TestContextSlotAssembleSystemPromptMode:
    """System-prompt mode is legacy — the slot must be a no-op."""

    @pytest.mark.asyncio
    async def test_returns_empty_list_in_system_prompt_mode(
        self,
        system_prompt_agent_meta: SimpleNamespace,
        stub_manager: SimpleNamespace,
    ) -> None:
        """Legacy mode must return [] and never call assemble_context_messages."""
        slot = ContextSlot(
            manager=stub_manager,
            agent_meta=system_prompt_agent_meta,
        )

        with patch(
            "daemon.services.context_messages.assemble_context_messages",
            new=AsyncMock(return_value=[]),
        ) as mock_assemble:
            result = await slot.assemble(
                instance_id="inst-1",
                user_query="hello",
                project_id="proj-1",
            )

        assert result == []
        # Must NOT have invoked the orchestrator at all — the slot
        # is a hard no-op in legacy mode.
        mock_assemble.assert_not_called()


# ---------------------------------------------------------------------------
# 2. ContextSlot.assemble() calls assemble_context_messages in human_messages mode
# ---------------------------------------------------------------------------


class TestContextSlotAssembleHumanMessagesMode:
    """Human-messages mode must invoke the per-turn orchestrator."""

    @pytest.mark.asyncio
    async def test_calls_assemble_context_messages_in_human_messages_mode(
        self,
        human_messages_agent_meta: SimpleNamespace,
        stub_manager: SimpleNamespace,
    ) -> None:
        """When mode is human_messages, the slot must invoke assemble_context_messages."""
        slot = ContextSlot(
            manager=stub_manager,
            agent_meta=human_messages_agent_meta,
            parent_id=None,
        )

        sentinel = [HumanMessage(content="[SYSTEM CONTEXT: project] test", id="ctx-1")]
        with patch(
            "daemon.services.context_messages.assemble_context_messages",
            new=AsyncMock(return_value=sentinel),
        ) as mock_assemble:
            result = await slot.assemble(
                instance_id="inst-2",
                user_query="show me tests",
                project_id="proj-42",
            )

        # Returned list comes straight from the orchestrator.
        assert result is sentinel
        # The orchestrator was awaited exactly once with the slot's
        # captured dependencies plus the per-call args.
        mock_assemble.assert_awaited_once()
        kwargs = mock_assemble.await_args.kwargs  # type: ignore[union-attr]  # safe after assert_awaited_once
        assert kwargs["instance_id"] == "inst-2"
        assert kwargs["user_query"] == "show me tests"
        assert kwargs["project_id"] == "proj-42"
        assert kwargs["agent_meta"] is human_messages_agent_meta
        assert kwargs["manager"] is stub_manager
        assert kwargs["instance_repository"] is None
        assert kwargs["parent_id"] is None
        # Default skill_injection_result pulled from the manager getter.
        assert kwargs["skill_injection_result"] is None


# ---------------------------------------------------------------------------
# 3. Manager set_context_skill_result / get_context_skill_result round-trip
# ---------------------------------------------------------------------------


class TestContextSkillResultRoundTrip:
    """Phase 3 B2/B3 fix: per-instance skill-search cache round-trips."""

    def test_round_trip_stores_and_returns_tuple(self) -> None:
        """Manager must store and retrieve the per-instance skill result."""
        manager = InstanceManager.__new__(InstanceManager)
        # Initialize the dict attribute that ``set_context_skill_result``
        # writes to. Mirrors ``InstanceManager.__init__``'s allocation
        # — we bypass the full constructor to avoid pulling in DB /
        # LLM collaborators this unit-level test does not need.
        manager._context_skill_results = {}

        stored = ("<skill>do the thing</skill>", ["skill_a"])
        manager.set_context_skill_result("inst-rt", stored)

        fetched = manager.get_context_skill_result("inst-rt")
        assert fetched == stored
        # The dict is the canonical storage — verify the wire went
        # through the attribute the codebase's cleanup paths read.
        assert manager._context_skill_results["inst-rt"] == stored

    def test_get_returns_none_when_absent(self) -> None:
        """Absent key returns None (the "search never ran" sentinel)."""
        manager = InstanceManager.__new__(InstanceManager)
        manager._context_skill_results = {}

        assert manager.get_context_skill_result("never-stored") is None

    def test_overwrite_replaces_previous_entry(self) -> None:
        """Second set replaces the prior entry — no history list."""
        manager = InstanceManager.__new__(InstanceManager)
        manager._context_skill_results = {}

        manager.set_context_skill_result("inst-x", ("old", ["s1"]))
        manager.set_context_skill_result("inst-x", (None, []))

        # Only the latest entry survives — no list of historical
        # results (B3 fix: latest wins).
        assert manager._context_skill_results["inst-x"] == (None, [])
        assert manager.get_context_skill_result("inst-x") == (None, [])

    def test_per_instance_isolation(self) -> None:
        """Entries for different instances must not collide."""
        manager = InstanceManager.__new__(InstanceManager)
        manager._context_skill_results = {}

        manager.set_context_skill_result("inst-a", ("a-text", ["sa"]))
        manager.set_context_skill_result("inst-b", ("b-text", ["sb"]))

        assert manager.get_context_skill_result("inst-a") == ("a-text", ["sa"])
        assert manager.get_context_skill_result("inst-b") == ("b-text", ["sb"])


# ---------------------------------------------------------------------------
# 4 & 5. _build_graph_input mode gating
# ---------------------------------------------------------------------------


class TestBuildGraphInputModeGating:
    """Phase 3 / Task 12: mode-aware prepend behavior."""

    def test_human_messages_mode_returns_only_user_message(
        self,
        human_messages_agent_meta: SimpleNamespace,
    ) -> None:
        """In human_messages mode, the skill message must NOT be prepended
        — context is rebuilt inside ``agent_node`` instead, and prepending
        here would double-inject."""
        skill_msg = HumanMessage(content="<skill>instructions</skill>", id="skill-1")

        result = _build_graph_input(
            content="hello",
            message_id="msg-hm",
            skill_injection_msg=skill_msg,
            agent_meta=human_messages_agent_meta,
        )

        messages = result["messages"]
        assert len(messages) == 1
        # The single message is the user message, NOT the skill message.
        assert messages[0] is not skill_msg
        assert messages[0].content == "hello"
        assert messages[0].id == "msg-hm"

    def test_system_prompt_mode_prepends_skill_message(
        self,
        system_prompt_agent_meta: SimpleNamespace,
    ) -> None:
        """Legacy system_prompt mode preserves the pre-Phase-3 layout:
        the skill message is prepended BEFORE the user message."""
        skill_msg = HumanMessage(content="<skill>instructions</skill>", id="skill-1")

        result = _build_graph_input(
            content="hello",
            message_id="msg-sp",
            skill_injection_msg=skill_msg,
            agent_meta=system_prompt_agent_meta,
        )

        messages = result["messages"]
        assert len(messages) == 2
        # Skill message first, user message second.
        assert messages[0] is skill_msg
        assert messages[0].content == "<skill>instructions</skill>"
        assert messages[0].id == "skill-1"
        assert messages[1].content == "hello"
        assert messages[1].id == "msg-sp"

    def test_system_prompt_mode_without_skill_returns_single_message(
        self,
        system_prompt_agent_meta: SimpleNamespace,
    ) -> None:
        """Legacy mode without a skill message emits only the user message."""
        result = _build_graph_input(
            content="hello",
            message_id="msg-sp-empty",
            skill_injection_msg=None,
            agent_meta=system_prompt_agent_meta,
        )

        messages = result["messages"]
        assert len(messages) == 1
        assert messages[0].content == "hello"
        assert messages[0].id == "msg-sp-empty"

    def test_default_agent_meta_treated_as_system_prompt(self) -> None:
        """No agent_meta (None) must default to legacy behavior."""
        skill_msg = HumanMessage(content="<skill>legacy</skill>", id="skill-default")

        result = _build_graph_input(
            content="hello",
            message_id="msg-def",
            skill_injection_msg=skill_msg,
            agent_meta=None,
        )

        messages = result["messages"]
        assert len(messages) == 2
        assert messages[0] is skill_msg
        assert messages[1].content == "hello"


# ---------------------------------------------------------------------------
# 6. _extract_last_user_text
# ---------------------------------------------------------------------------


class TestExtractLastUserText:
    """Last-user-text extractor used by the agent_node RAG query."""

    def test_empty_messages_returns_empty_string(self) -> None:
        """Empty list must return '' — never raise."""
        assert _extract_last_user_text([]) == ""

    def test_string_content_human_message(self) -> None:
        """A single HumanMessage with string content returns that string."""
        messages = cast(Sequence[BaseMessage], [HumanMessage(content="hello world", id="u-1")])
        assert _extract_last_user_text(list(messages)) == "hello world"  # type: ignore[arg-type]

    def test_multimodal_list_content_is_flattened(self) -> None:
        """Multimodal content blocks are flattened; text blocks joined by '\\n'."""
        content = [
            {"type": "text", "text": "describe this image"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
            {"type": "text", "text": "second caption"},
        ]
        messages = cast(Sequence[BaseMessage], [HumanMessage(content=content, id="u-mm")])

        # Two text blocks joined by '\n'; image block skipped.
        assert _extract_last_user_text(list(messages)) == "describe this image\nsecond caption"  # type: ignore[arg-type]

    def test_find_last_human_message_not_messages_tail(self) -> None:
        """``messages[-1]`` may not be a HumanMessage — scan in reverse for the last one."""
        messages = cast(
            Sequence[BaseMessage],
            [
                HumanMessage(content="first user", id="u-1"),
                AIMessage(content="ai response", id="a-1"),
                HumanMessage(content="second user", id="u-2"),
                AIMessage(content="ai again", id="a-2"),
            ],
        )
        # The LAST HumanMessage is "second user", not the last item in
        # the list. The helper must walk back past the trailing AIMessage.
        assert _extract_last_user_text(list(messages)) == "second user"  # type: ignore[arg-type]

    def test_human_message_with_list_but_no_text_continues_scanning(self) -> None:
        """A HumanMessage containing only image blocks falls through to
        the next HumanMessage (rare but possible)."""
        messages = cast(
            Sequence[BaseMessage],
            [
                HumanMessage(content=[{"type": "image_url", "image_url": {"url": "x"}}], id="u-img"),
                HumanMessage(content="text-only fallback", id="u-txt"),
            ],
        )
        assert _extract_last_user_text(list(messages)) == "text-only fallback"  # type: ignore[arg-type]

    def test_only_image_content_returns_empty_string(self) -> None:
        """Messages with only image content (no text blocks) return ''."""
        messages = cast(
            Sequence[BaseMessage],
            [HumanMessage(content=[{"type": "image_url", "image_url": {"url": "x"}}], id="u-img-only")],
        )
        assert _extract_last_user_text(list(messages)) == ""  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 7. _resolve_project_id
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 7. _resolve_project_id
# ---------------------------------------------------------------------------


class TestResolveProjectId:
    """Project id resolution from instance metadata."""

    def test_returns_project_id_from_metadata(self) -> None:
        """Repository returns an instance whose metadata has project_id."""
        instance = SimpleNamespace(instance_metadata={"project_id": "proj-abc"})
        repo = SimpleNamespace(get=lambda instance_id: instance)

        assert _resolve_project_id("inst-1", repo) == "proj-abc"

    def test_returns_none_when_metadata_lacks_project_id(self) -> None:
        """Metadata without ``project_id`` returns None."""
        instance = SimpleNamespace(instance_metadata={"other_key": "value"})
        repo = SimpleNamespace(get=lambda instance_id: instance)

        assert _resolve_project_id("inst-2", repo) is None

    def test_returns_none_when_instance_not_found(self) -> None:
        """Repository returning None (instance not found) returns None."""
        repo = SimpleNamespace(get=lambda instance_id: None)

        assert _resolve_project_id("inst-3", repo) is None

    def test_returns_none_when_repository_is_none(self) -> None:
        """No repository at all must return None — never raise."""
        assert _resolve_project_id("inst-4", None) is None

    def test_returns_none_when_repository_raises(self) -> None:
        """A transient repo error is swallowed and returns None."""
        def _raise(_instance_id: str) -> None:
            raise RuntimeError("db transient error")

        repo = SimpleNamespace(get=_raise)

        # Best-effort — never raises.
        assert _resolve_project_id("inst-5", repo) is None


# ---------------------------------------------------------------------------
# 8. ContextSlot passes stored skill_result from manager to assemble_context_messages
# ---------------------------------------------------------------------------


class TestContextSlotPassesStoredSkillResult:
    """B2 fix: the slot reads the cached skill result from the manager
    and forwards it to ``assemble_context_messages`` so retries can
    reuse the same matched skills."""

    @pytest.mark.asyncio
    async def test_forwards_cached_skill_result_to_assembler(
        self,
        human_messages_agent_meta: SimpleNamespace,
    ) -> None:
        """The slot must call ``manager.get_context_skill_result(instance_id)``
        and forward the result as ``skill_injection_result``."""
        cached = ("<skill>cached block</skill>", ["skill_x", "skill_y"])

        manager = SimpleNamespace(
            get_context_skill_result=lambda instance_id: (
                cached if instance_id == "inst-cached" else None
            ),
        )
        slot = ContextSlot(
            manager=manager,
            agent_meta=human_messages_agent_meta,
        )

        with patch(
            "daemon.services.context_messages.assemble_context_messages",
            new=AsyncMock(return_value=[]),
        ) as mock_assemble:
            await slot.assemble(
                instance_id="inst-cached",
                user_query="anything",
                project_id=None,
            )

        mock_assemble.assert_awaited_once()
        kwargs = mock_assemble.await_args.kwargs  # type: ignore[union-attr]  # safe after assert_awaited_once
        # The cached tuple must be passed through verbatim — no copy,
        # no transformation, no re-running of the search.
        assert kwargs["skill_injection_result"] is cached

    @pytest.mark.asyncio
    async def test_forwards_none_when_no_entry_stored(
        self,
        human_messages_agent_meta: SimpleNamespace,
    ) -> None:
        """When the manager has no entry, the slot forwards ``None``
        so the assembler can re-run the search (B3 — never lose skills
        just because the first attempt skipped)."""
        manager = SimpleNamespace(
            get_context_skill_result=lambda instance_id: None,
        )
        slot = ContextSlot(
            manager=manager,
            agent_meta=human_messages_agent_meta,
        )

        with patch(
            "daemon.services.context_messages.assemble_context_messages",
            new=AsyncMock(return_value=[]),
        ) as mock_assemble:
            await slot.assemble(
                instance_id="inst-fresh",
                user_query="anything",
                project_id=None,
            )

        kwargs = mock_assemble.await_args.kwargs  # type: ignore[union-attr]  # safe after assert_awaited_once
        assert kwargs["skill_injection_result"] is None

    @pytest.mark.asyncio
    async def test_manager_without_getter_does_not_break(
        self,
        human_messages_agent_meta: SimpleNamespace,
    ) -> None:
        """A manager without ``get_context_skill_result`` (e.g. an older
        stub) must not break the slot — it falls back to ``None``."""
        bare_manager = SimpleNamespace()  # No get_context_skill_result
        slot = ContextSlot(
            manager=bare_manager,
            agent_meta=human_messages_agent_meta,
        )

        with patch(
            "daemon.services.context_messages.assemble_context_messages",
            new=AsyncMock(return_value=[]),
        ) as mock_assemble:
            await slot.assemble(
                instance_id="inst-bare",
                user_query="anything",
                project_id=None,
            )

        kwargs = mock_assemble.await_args.kwargs  # type: ignore[union-attr]  # safe after assert_awaited_once
        # Missing getter ⇒ skill_injection_result falls back to None.
        assert kwargs["skill_injection_result"] is None
