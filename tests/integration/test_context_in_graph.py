"""Phase 3 wiring/API-contract tests for context-injection graph helpers.

Covers the per-turn plumbing that ``agent_node`` (and the
``_process_message_with_tracking`` build path) depends on for
Context Injection Restructure Phase 3:

* :class:`daemon.graph.ContextSlot` — assembles per-turn context
  messages and gates on ``context_injection_mode``.
* :func:`daemon.graph._extract_last_user_text` — extracts the last
  user text from the LangGraph ``messages`` list.
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
from unittest.mock import AsyncMock, MagicMock, patch

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
)
from daemon.manager import InstanceManager
from daemon.registry import ContextInjectionConfig
from daemon.services.instance_messaging import _build_graph_input


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def human_messages_agent_meta() -> SimpleNamespace:
    """AgentMeta stub in ``human_messages`` mode (the new default)."""
    return SimpleNamespace(
        context_injection_mode="human_messages",
        context_injection=ContextInjectionConfig(heuristic_match_shared_md_files=True),
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
# 1. ContextSlot.assemble() calls assemble_context_messages in human_messages mode
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

        sentinel = (None, [HumanMessage(content="[SYSTEM CONTEXT: project] test", id="ctx-1")])
        with patch(
            "daemon.services.context_messages.assemble_context_messages",
            new=AsyncMock(return_value=sentinel),
        ) as mock_assemble:
            result = await slot.assemble(
                instance_id="inst-2",
                user_query="show me tests",
                project_id="proj-42",
            )

        # Returned tuple comes straight from the orchestrator.
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
        # Hybrid split: the slot reads ``project_injected`` fresh on
        # every call. With no instance repository it defaults to
        # ``False`` so the orchestrator is asked to build the full
        # triple.
        assert kwargs["project_already_injected"] is False


# ---------------------------------------------------------------------------
# 2. Manager set_context_skill_result / get_context_skill_result round-trip
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
# 3 & 4. _build_graph_input mode gating
# ---------------------------------------------------------------------------


class TestBuildGraphInputHelper:
    """Direct tests for :func:`_build_graph_input`.

    The helper is the consumer side of the persistent-context-message
    pipeline: the per-turn persistent block (project + shared-context +
    skills) is built by the messaging path, then prepended to the
    graph input so LangGraph's ``add_messages`` reducer checkpoints
    it before the user message.
    """

    def test_no_persistent_context_returns_user_message_only(self) -> None:
        """No persistent-context messages → single-element list, user
        ``HumanMessage`` carries the caller's ``message_id`` so
        ``add_messages`` can dedupe across retries.
        """
        result = _build_graph_input("hello", "msg-1", None)

        assert "messages" in result
        assert len(result["messages"]) == 1
        user_msg = result["messages"][0]
        assert isinstance(user_msg, HumanMessage)
        assert user_msg.id == "msg-1"
        assert user_msg.content == "hello"

    def test_persistent_context_msgs_prepended_in_order(self) -> None:
        """The persistent-context block is prepended so the agent
        reads context before the user prompt (mirrors the
        system-context-then-user-input layout the rest of the
        codebase uses).

        LangGraph's ``add_messages`` reducer checkpoints the
        persistent block once on the first turn — subsequent turns
        read it from ``state['messages']`` without a per-turn rebuild.
        """
        ctx_msg_1 = HumanMessage(
            content="[SYSTEM CONTEXT: Project]\nproject body",
            id="ctx-1",
        )
        ctx_msg_2 = HumanMessage(
            content="[SYSTEM CONTEXT: Skills]\nskill body",
            id="ctx-2",
        )

        result = _build_graph_input(
            "hello",
            "msg-1",
            [ctx_msg_1, ctx_msg_2],
        )

        messages = result["messages"]
        assert len(messages) == 3
        # Persistent msgs first, user message last.
        assert messages[0] is ctx_msg_1
        assert messages[1] is ctx_msg_2
        assert messages[2].id == "msg-1"
        assert messages[2].content == "hello"


# ---------------------------------------------------------------------------
# 5. _extract_last_user_text
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
# 7. _reassemble_with_context
# ---------------------------------------------------------------------------


class TestReassembleWithContext:
    """Regression test for C1: both persona and repair SystemMessages survive."""

    def test_preserves_persona_and_repair_system_messages(self):
        from daemon.graph import _reassemble_with_context
        from langchain_core.messages import SystemMessage, HumanMessage

        persona = SystemMessage(content="You are a helpful agent.")
        repair = SystemMessage(content="[System Repair] Stop repeating yourself.")
        user_msg = HumanMessage(content="hello")
        context = [HumanMessage(content="[SYSTEM CONTEXT: Project] info")]

        messages = [persona, user_msg, repair]
        result = _reassemble_with_context(messages, context, persona.content)

        system_msgs = [m for m in result if isinstance(m, SystemMessage)]
        assert len(system_msgs) == 2
        assert system_msgs[0].content == persona.content
        assert any(m.content == repair.content for m in system_msgs)
        assert isinstance(result[1], HumanMessage)


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
            new=AsyncMock(return_value=([], [])),
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
            new=AsyncMock(return_value=([], [])),
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
            new=AsyncMock(return_value=([], [])),
        ) as mock_assemble:
            await slot.assemble(
                instance_id="inst-bare",
                user_query="anything",
                project_id=None,
            )

        kwargs = mock_assemble.await_args.kwargs  # type: ignore[union-attr]  # safe after assert_awaited_once
        # Missing getter ⇒ skill_injection_result falls back to None.
        assert kwargs["skill_injection_result"] is None


# ---------------------------------------------------------------------------
# 9. ContextSlot reads ``project_injected`` flag fresh from instance metadata
# ---------------------------------------------------------------------------


class TestContextSlotReadsProjectInjectedFlag:
    """W3 — pin the read path for the ``project_injected`` flag.

    :class:`daemon.graph.ContextSlot` reads the once-per-instance
    ``project_injected`` flag from instance metadata on every
    ``assemble()`` call (via
    :meth:`daemon.graph.ContextSlot._is_project_already_injected`).
    The result is forwarded as the ``project_already_injected`` kwarg
    to :func:`daemon.services.context_messages.assemble_context_messages`,
    which short-circuits the persistent-block rebuild when the flag
    is truthy. Without this read the orchestrator would rebuild the
    project + shared-context + auto-load blocks on every turn,
    defeating the once-per-instance contract that keeps the LLM
    prefix-cache stable.

    Earlier revisions dropped these assertions when cleaning up
    legacy tests. This test class pins the contract at three levels
    so a future refactor cannot silently break the read path.
    """

    @pytest.mark.asyncio
    async def test_project_injected_true_propagates_to_orchestrator(
        self,
        human_messages_agent_meta: SimpleNamespace,
    ) -> None:
        """When ``project_injected=True`` is set in metadata the slot
        must forward ``project_already_injected=True`` to the orchestrator.

        This is the load-bearing half of the once-per-instance
        contract — the slot reads the flag on every call so a
        checkpoint restore / cross-process handoff is honoured. The
        exact ``bool(metadata.get("project_injected"))`` check lives
        in :meth:`ContextSlot._is_project_already_injected`.
        """
        instance_row = SimpleNamespace(
            instance_id="inst-onward",
            instance_metadata={"project_injected": True},
        )
        instance_repository = MagicMock()
        instance_repository.get = MagicMock(return_value=instance_row)

        manager = SimpleNamespace(
            get_context_skill_result=lambda instance_id: None,
        )

        slot = ContextSlot(
            manager=manager,
            agent_meta=human_messages_agent_meta,
            instance_repository=instance_repository,
        )

        with patch(
            "daemon.services.context_messages.assemble_context_messages",
            new=AsyncMock(return_value=([], [])),
        ) as mock_assemble:
            await slot.assemble(
                instance_id="inst-onward",
                user_query="anything",
                project_id="proj-1",
            )

        mock_assemble.assert_awaited_once()
        kwargs = mock_assemble.await_args.kwargs  # type: ignore[union-attr]
        # The critical contract: a True flag in metadata propagates
        # verbatim to the orchestrator's project_already_injected kwarg.
        assert kwargs["project_already_injected"] is True

    @pytest.mark.asyncio
    async def test_project_injected_absent_propagates_false(
        self,
        human_messages_agent_meta: SimpleNamespace,
    ) -> None:
        """When the flag is absent, the slot defaults to ``False`` so the
        orchestrator builds the full persistent triple on the first turn.

        The default behaviour (no key in metadata → False) is what
        allows a fresh instance to receive its project + shared-context
        + auto-load blocks. If a refactor accidentally treated an
        absent key as truthy, every instance would skip its first-turn
        context injection.
        """
        instance_row = SimpleNamespace(
            instance_id="inst-fresh",
            # ``project_id`` is present (a normal first-turn row) but
            # ``project_injected`` is absent — first-turn state.
            instance_metadata={"project_id": "proj-1"},
        )
        instance_repository = MagicMock()
        instance_repository.get = MagicMock(return_value=instance_row)

        manager = SimpleNamespace(
            get_context_skill_result=lambda instance_id: None,
        )

        slot = ContextSlot(
            manager=manager,
            agent_meta=human_messages_agent_meta,
            instance_repository=instance_repository,
        )

        with patch(
            "daemon.services.context_messages.assemble_context_messages",
            new=AsyncMock(return_value=([], [])),
        ) as mock_assemble:
            await slot.assemble(
                instance_id="inst-fresh",
                user_query="anything",
                project_id="proj-1",
            )

        kwargs = mock_assemble.await_args.kwargs  # type: ignore[union-attr]
        assert kwargs["project_already_injected"] is False

    @pytest.mark.asyncio
    async def test_orchestrator_skips_persistent_rebuild_when_flag_true(self) -> None:
        """End-to-end: when ``project_already_injected=True`` the
        orchestrator must NOT call the project-fetch or KV-metadata
        helpers — proving the persistent rebuild is skipped on
        subsequent turns.

        This pins the orchestrator-side honour of the flag. The
        orchestrator early-returns at the ``if
        project_already_injected:`` branch in
        :func:`daemon.services.context_messages.assemble_context_messages`
        (around line 1187) before any of the persistent-block
        builders run — so ``_fetch_project_payload`` and
        ``_fetch_kv_metadata`` are NEVER invoked, meaning
        ``project_repo.get`` and
        ``shared_context_metadata_repo.get_all_as_dict`` are NEVER
        called. If either call fires, the once-per-instance
        contract regressed and every turn pays the full DB /
        RAG cost.
        """
        from daemon.services.context_messages import assemble_context_messages

        # Manager whose project / shared-context fetchers track invocations.
        # Skill injection disabled (matches the agent_meta below) so the
        # orchestrator returns ([], []) on the skip path — no other side
        # effects to reason about.
        project_repo = MagicMock()
        project_repo.get = MagicMock(
            return_value=SimpleNamespace(project_id="proj-1", name="Test Project")
        )
        shared_repo = MagicMock()
        shared_repo.get_all_as_dict = MagicMock(return_value={"k": "v"})

        manager = SimpleNamespace(
            _project_repository=project_repo,
            _shared_context_metadata_repo=shared_repo,
            _skill_injection_service=None,
        )

        # Minimal agent_meta — NO skill injection, NO heuristic shared-context
        # RAG so the orchestrator's early-return path is the only code that
        # runs. ``auto_load_invalidated`` defaults to ``False`` (the keyword
        # arg), so the auto-load rebuild branch is also skipped.
        agent_meta = SimpleNamespace(
            skill_injection=False,
            context_injection=ContextInjectionConfig(
                heuristic_match_shared_md_files=False
            ),
        )

        instance_repository = MagicMock()

        # Call the orchestrator directly with project_already_injected=True.
        persistent, ephemeral = await assemble_context_messages(
            instance_id="inst-onward",
            user_query="second turn",
            project_id="proj-1",
            agent_meta=agent_meta,
            manager=manager,
            instance_repository=instance_repository,
            project_already_injected=True,
        )

        # The persistent block was NOT rebuilt — the project-repo
        # and shared-context KV fetcher were NEVER called.
        project_repo.get.assert_not_called()
        project_repo.list_critical_notes.assert_not_called()
        project_repo.get_recent_history.assert_not_called()
        shared_repo.get_all_as_dict.assert_not_called()
        # No persistent HumanMessages of any kind — clean skip.
        assert persistent == []
        assert ephemeral == []
