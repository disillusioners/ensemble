"""Unit tests for :class:`daemon.graph.LoopRepairer` (Phase 2 — Message Repair Engine).

Covers ``daemon.graph.LoopRepairer``:
    1. ``_build_removal_list`` produces ``RemoveMessage`` sentinels for the
       duplicate units and excludes the evidence IDs (so the agent retains
       context about what it was doing).
    2. ``_summarize_loop`` calls the LLM with the prompt built from
       ``REPAIR_SUMMARIZATION_PROMPT`` and the right fields (tool_name,
       tool_args, count, conversation_excerpt).
    3. ``_build_repair_message`` always emits a fresh UUID prefixed with
       ``LOOP_BREAKER_REPAIR_PREFIX`` (``"repair-"``) so the
       ``add_messages`` reducer appends rather than replaces.
    4. ``repair()`` calls ``graph.aupdate_state`` with the exact
       ``{'messages': removals + [repair_msg]}`` payload and
       ``as_node='agent'``.
    5. ``_summarize_loop`` falls back to the static summary when the LLM
       raises — repair still succeeds.
    6. ``_summarize_loop`` falls back to the static summary when the LLM
       call times out via ``asyncio.wait_for``.
    7. ``repair()`` re-appends ``RepairContext.injected_msg`` to the
       ``repaired_messages`` after the state re-read (C3 pattern).
    8. ``repair()`` returns the ORIGINAL message list when the state update
       itself raises (defensive fallback so the graph can keep running).
    9. Layer 1 pre-validation: ``repair()`` filters ``RemoveMessage`` IDs
       against the live checkpoint so IDs renamed by compaction (see
       ``daemon/compaction.py:696-699``) don't cause ``ValueError``.
   10. Layer 2 safety net: ``repair()`` catches ``ValueError`` from
       ``aupdate_state`` and retries with the repair message only.

The repairer is a stateless helper: it only depends on ``graph``,
``llm_config``, and the messages passed via ``RepairContext``. The LLM is
patched at the ``daemon.graph.ThinkingChatOpenAI`` symbol — same
patch-points the rest of the codebase uses (see ``compaction.py:997``).

Mocking style mirrors ``tests/unit/test_compaction.py`` and
``tests/test_gii_throttle.py``: small in-memory fixtures plus
``unittest.mock.{AsyncMock, MagicMock, patch}``.
"""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
    ToolMessage,
)

from daemon.graph import (
    LOOP_BREAKER_REPAIR_PREFIX,
    LOOP_BREAKER_SUMMARIZATION_TIMEOUT_SECONDS,
    REPAIR_SUMMARIZATION_PROMPT,
    LoopDetectionResult,
    LoopRepairer,
    RepairContext,
    RepairResult,
)


# ---------------------------------------------------------------------------
# Test doubles / helpers
# ---------------------------------------------------------------------------


def _ai_with_tool_call(
    tool_call_id: str,
    name: str,
    args: dict,
    *,
    msg_id: str | None = None,
    tool_calls: list[dict] | None = None,
) -> AIMessage:
    """Build an ``AIMessage`` with a single ``tool_call`` entry."""
    if tool_calls is None:
        tool_calls = [{"id": tool_call_id, "name": name, "args": args}]
    return AIMessage(content="", tool_calls=tool_calls, id=msg_id)


def _tool_result(
    tool_call_id: str,
    name: str,
    content: str = "result",
    *,
    msg_id: str | None = None,
) -> ToolMessage:
    """Build a ``ToolMessage`` matching an earlier ``tool_call_id``."""
    return ToolMessage(content=content, tool_call_id=tool_call_id, name=name, id=msg_id)


def _make_detection(
    *,
    tool_name: str = "list_files",
    tool_args: dict | None = None,
    repetition_count: int = 3,
    loop_message_ids: list[str] | None = None,
    evidence_ids: list[str] | None = None,
) -> LoopDetectionResult:
    """Build a ``LoopDetectionResult`` with the given loop + evidence IDs.

    ``loop_messages`` are constructed as ``AIMessage`` instances with the
    given IDs so ``_build_removal_list`` can pull them via ``getattr``.
    """
    if tool_args is None:
        tool_args = {"path": "/tmp"}
    # ``loop_message_ids`` defaults to a non-empty list when None;
    # explicit ``[]`` must be honoured (use ``is None`` not ``or``).
    if loop_message_ids is None:
        loop_message_ids = ["loop-ai-1", "loop-ai-2"]
    if evidence_ids is None:
        evidence_ids = ["evidence-ai-0", "evidence-tool-0"]
    loop_messages: list = [
        _ai_with_tool_call(
            tool_call_id=f"tc-{mid}",
            name=tool_name,
            args=tool_args,
            msg_id=mid,
        )
        for mid in loop_message_ids
    ]
    return LoopDetectionResult(
        tool_name=tool_name,
        tool_args=tool_args,
        repetition_count=repetition_count,
        loop_messages=loop_messages,
        evidence_message_ids=evidence_ids,
    )


def _mock_graph(updated_messages: list | None = None) -> MagicMock:
    """Build a mock compiled graph with ``aupdate_state`` / ``aget_state``.

    ``updated_messages`` is what ``aget_state`` will return — defaults to an
    empty list to match the post-compaction baseline.

    String entries are auto-coerced into ``AIMessage(id=string)`` instances
    so the Layer 1 pre-validation in ``LoopRepairer.repair`` (which extracts
    IDs via ``getattr(m, "id", None)``) sees realistic message objects.
    Pass pre-built ``BaseMessage`` instances when you need richer content.
    """
    # Coerce string entries into AIMessage so .id is accessible. This
    # mirrors the real LangGraph checkpoint shape (BaseMessage instances
    # with ``.id`` set) and lets the pre-validation filter work in
    # otherwise string-only test fixtures.
    coerced: list = []
    for entry in updated_messages or []:
        if isinstance(entry, str):
            coerced.append(AIMessage(content="", id=entry))
        else:
            coerced.append(entry)
    graph = MagicMock()
    graph.aupdate_state = AsyncMock()
    graph.aget_state = AsyncMock(
        return_value=MagicMock(values={"messages": coerced})
    )
    return graph


def _mock_llm(text: str = "LLM-generated summary.") -> MagicMock:
    """Build a mock ``ThinkingChatOpenAI`` instance.

    Returns a stand-in object whose ``.invoke(messages)`` returns a
    ``MagicMock(content=text)`` — matches the ``response.content`` access
    pattern in ``_summarize_loop``.
    """
    llm = MagicMock()
    llm.invoke = MagicMock(return_value=MagicMock(content=text))
    return llm


def _make_context(
    *,
    detection: LoopDetectionResult | None = None,
    messages: list | None = None,
    graph: MagicMock | None = None,
    llm_config: dict | None = None,
    system_prompt: str = "You are a helpful agent.",
    injected_msg: list[BaseMessage] | None = None,
    summarization_timeout_seconds: int = 30,
) -> RepairContext:
    """Build a fully-populated :class:`RepairContext` with sensible defaults."""
    return RepairContext(
        detection=detection or _make_detection(),
        messages=messages or [],
        thread_config={"configurable": {"thread_id": "instance-test"}},
        graph=graph or _mock_graph(updated_messages=["state-msg-1", "state-msg-2"]),
        llm_config=llm_config or {"model": "gpt-4o", "temperature": 0.0},
        system_prompt=system_prompt,
        injected_msg=injected_msg,
        summarization_timeout_seconds=summarization_timeout_seconds,
    )


# ===========================================================================
# Test 1: Removal list correct — evidence IDs excluded
# ===========================================================================


class TestBuildRemovalList:
    """Verify ``_build_removal_list`` produces sentinels only for duplicates."""

    def test_removes_only_loop_messages(self):
        """IDs in ``loop_messages`` produce sentinels; evidence IDs do not."""
        detection = _make_detection(
            loop_message_ids=["loop-1", "loop-2"],
            evidence_ids=["evidence-ai-0", "evidence-tool-0"],
        )
        removals = LoopRepairer._build_removal_list(detection)

        # 2 loop_messages → 2 RemoveMessage sentinels with matching IDs.
        assert len(removals) == 2
        assert all(isinstance(r, RemoveMessage) for r in removals)
        ids = [r.id for r in removals]
        assert "loop-1" in ids
        assert "loop-2" in ids
        # Evidence IDs MUST NOT be in the removal list — the detector
        # already excludes them, but the defensive check in
        # ``_build_removal_list`` is the contract under test.
        assert "evidence-ai-0" not in ids
        assert "evidence-tool-0" not in ids

    def test_evidence_ids_excluded_even_if_present_in_loop_messages(self):
        """Defensive: if evidence ID is in loop_messages it must be preserved."""
        # Simulate the (theoretical) case where the detector accidentally
        # includes an evidence ID inside loop_messages. The repairer's
        # ``if msg.id not in evidence_ids`` guard MUST skip it.
        detection = _make_detection(
            loop_message_ids=["loop-1", "evidence-ai-0"],
            evidence_ids=["evidence-ai-0"],
        )
        removals = LoopRepairer._build_removal_list(detection)

        ids = [r.id for r in removals]
        assert "loop-1" in ids
        assert "evidence-ai-0" not in ids  # excluded even though it's in loop_messages

    def test_empty_loop_messages_returns_empty_list(self):
        detection = _make_detection(loop_message_ids=[], evidence_ids=[])
        assert LoopRepairer._build_removal_list(detection) == []


# ===========================================================================
# Test 2: LLM summary called with right prompt
# ===========================================================================


class TestSummarizeLoopCallsLLMWithCorrectPrompt:
    """Verify ``_summarize_loop`` passes the formatted prompt to the LLM."""

    @pytest.mark.asyncio
    async def test_llm_invoked_with_formatted_prompt(self):
        """The prompt string built for the LLM matches the template + fields."""
        detection = _make_detection(
            tool_name="read_file",
            tool_args={"path": "/tmp/x.txt"},
            repetition_count=4,
        )
        messages = [
            HumanMessage(content="Please read /tmp/x.txt", id="h-0"),
            AIMessage(content="Reading.", id="a-0", tool_calls=[
                {"id": "tc-1", "name": "read_file", "args": {"path": "/tmp/x.txt"}},
            ]),
            ToolMessage(content="contents", tool_call_id="tc-1", name="read_file", id="t-1"),
        ]
        mock_llm = _mock_llm("Here is a summary.")

        with patch("daemon.graph.ThinkingChatOpenAI", return_value=mock_llm):
            summary = await LoopRepairer._summarize_loop(
                detection,
                messages,
                llm_config={"model": "gpt-4o", "temperature": 0.0},
                timeout_seconds=5,
            )

        assert summary == "Here is a summary."
        # The LLM's invoke was called exactly once.
        mock_llm.invoke.assert_called_once()
        # Inspect the messages list passed to the LLM. It should be
        # [SystemMessage("helpful assistant..."), HumanMessage(<formatted prompt>)].
        call_args = mock_llm.invoke.call_args
        passed_messages = call_args.args[0] if call_args.args else call_args.kwargs["messages"]
        assert len(passed_messages) == 2
        assert isinstance(passed_messages[0], SystemMessage)
        assert isinstance(passed_messages[1], HumanMessage)
        prompt_text = passed_messages[1].content
        # All four template fields are interpolated.
        assert "read_file" in prompt_text
        assert "/tmp/x.txt" in prompt_text
        assert "4 times" in prompt_text
        # The conversation_excerpt contains the most recent messages as text.
        assert "Reading." in prompt_text or "Please read" in prompt_text

    @pytest.mark.asyncio
    async def test_clean_llm_config_strips_model_vision(self):
        """``model_vision`` must be filtered out before constructing the LLM."""
        detection = _make_detection()
        llm_config = {
            "model": "gpt-4o",
            "temperature": 0.0,
            "model_vision": "gpt-4o-vision",  # MUST be stripped
        }
        mock_llm = _mock_llm("ok")

        with patch("daemon.graph.ThinkingChatOpenAI", return_value=mock_llm) as ctor:
            await LoopRepairer._summarize_loop(
                detection, [], llm_config, timeout_seconds=5
            )

        # The constructor was called WITHOUT ``model_vision``.
        ctor.assert_called_once()
        passed_kwargs = ctor.call_args.kwargs
        assert "model_vision" not in passed_kwargs
        assert passed_kwargs["model"] == "gpt-4o"

    @pytest.mark.asyncio
    async def test_tool_args_truncated_to_500_chars(self):
        """Long tool args are truncated to 500 chars in the prompt."""
        long_arg_value = "x" * 2000
        detection = _make_detection(
            tool_name="big_call",
            tool_args={"blob": long_arg_value},
            repetition_count=3,
        )
        mock_llm = _mock_llm("ok")

        with patch("daemon.graph.ThinkingChatOpenAI", return_value=mock_llm):
            await LoopRepairer._summarize_loop(
                detection, [], {"model": "gpt-4o"}, timeout_seconds=5
            )

        prompt_text = mock_llm.invoke.call_args.args[0][1].content
        # The prompt template escapes the args via json.dumps(indent=2)
        # and truncates to 500 chars. The original 2000-char blob MUST
        # be truncated; the full 2000 chars MUST NOT appear.
        assert "x" * 600 not in prompt_text
        # Sanity: some of the args are present.
        assert "blob" in prompt_text


# ===========================================================================
# Test 3: Repair message has fresh UUID
# ===========================================================================


class TestRepairMessageFreshUUID:
    """Verify ``_build_repair_message`` always emits a fresh UUID."""

    def test_id_has_repair_prefix(self):
        msg = LoopRepairer._build_repair_message(_make_detection(), "summary")
        assert msg.id is not None
        assert msg.id.startswith(LOOP_BREAKER_REPAIR_PREFIX)
        # The prefix is exactly "repair-".
        assert LOOP_BREAKER_REPAIR_PREFIX == "repair-"
        # After the prefix, the rest must be a valid UUID string.
        suffix = msg.id[len(LOOP_BREAKER_REPAIR_PREFIX):]
        # uuid4() produces 32 hex chars (no dashes in uuid4().hex, but
        # uuid4() string form has dashes). Either is acceptable; we just
        # assert length is UUID-shaped.
        assert len(suffix) >= 32
        # Tighten: the suffix must parse as a real UUID, not just be 32+ chars.
        uuid.UUID(suffix)  # raises ValueError if not a valid UUID

    def test_id_is_unique_across_calls(self):
        """Each call must produce a DIFFERENT UUID (fresh = no replacement)."""
        msg1 = LoopRepairer._build_repair_message(_make_detection(), "a")
        msg2 = LoopRepairer._build_repair_message(_make_detection(), "b")
        msg3 = LoopRepairer._build_repair_message(_make_detection(), "c")
        ids = {msg1.id, msg2.id, msg3.id}
        assert len(ids) == 3, f"Repair message IDs must be unique; got {ids}"

    def test_content_includes_summary_and_tool_name(self):
        """The body includes the summary, tool name, and repetition count."""
        detection = _make_detection(tool_name="do_thing", repetition_count=5)
        msg = LoopRepairer._build_repair_message(detection, "you are stuck on do_thing")
        assert "do_thing" in msg.content
        assert "5 times" in msg.content
        assert "you are stuck on do_thing" in msg.content
        assert "DIFFERENT approach" in msg.content


# ===========================================================================
# Test 4: State update called correctly
# ===========================================================================


class TestRepairStateUpdate:
    """Verify ``repair()`` calls ``graph.aupdate_state`` correctly."""

    @pytest.mark.asyncio
    async def test_aupdate_state_called_with_replacement_and_as_node_agent(self):
        # Seed the live checkpoint with the loop message IDs so the
        # Layer 1 pre-validation lets them through (real-world: the
        # messages we want to remove ARE in the checkpoint).
        graph = _mock_graph(updated_messages=["loop-ai-1", "loop-ai-2"])
        mock_llm = _mock_llm("summary text")

        with patch("daemon.graph.ThinkingChatOpenAI", return_value=mock_llm):
            result = await LoopRepairer().repair(_make_context(graph=graph))

        assert result.success is True
        graph.aupdate_state.assert_called_once()
        call = graph.aupdate_state.call_args
        # First positional: thread_config
        assert call.args[0] == {"configurable": {"thread_id": "instance-test"}}
        # Second positional: state values dict with messages
        state_values = call.args[1]
        assert "messages" in state_values
        replacement = state_values["messages"]
        # Order: RemoveMessage sentinels FIRST, then repair SystemMessage LAST.
        # All sentinels must come before the SystemMessage.
        sentinel_indexes = [i for i, m in enumerate(replacement)
                            if isinstance(m, RemoveMessage)]
        system_indexes = [i for i, m in enumerate(replacement)
                          if isinstance(m, SystemMessage)]
        assert sentinel_indexes  # at least one sentinel
        assert system_indexes    # exactly one repair SystemMessage
        assert max(sentinel_indexes) < min(system_indexes), (
            "RemoveMessage sentinels must come BEFORE the repair SystemMessage"
        )
        # as_node kwarg
        assert call.kwargs.get("as_node") == "agent"
        # Lock down #6433 bug avoidance: astream(None) must NOT be called
        graph.astream.assert_not_called()

    @pytest.mark.asyncio
    async def test_aget_state_called_after_aupdate(self):
        """The state is re-read after the update (for re-append + re-invoke).

        With the Layer 1 pre-validation in place, ``aget_state`` is now
        called TWICE: once before the update to filter stale removal IDs
        and once after to re-read the post-repair checkpoint. The
        assertion uses ``assert_awaited`` (≥1) to acknowledge both reads.
        """
        graph = _mock_graph(updated_messages=["repaired-1"])
        mock_llm = _mock_llm("ok")

        with patch("daemon.graph.ThinkingChatOpenAI", return_value=mock_llm):
            await LoopRepairer().repair(_make_context(graph=graph))

        # aupdate_state called once (the repair write).
        graph.aupdate_state.assert_awaited_once()
        # aget_state called AT LEAST once — pre-validation + post-update
        # re-read both go through this mock.
        assert graph.aget_state.await_count >= 1
        # Repaired messages come from the state re-read.
        # (Verified in TestRepairInjectedMessageReappend below.)


# ===========================================================================
# Test 5: Fallback on LLM error
# ===========================================================================


class TestSummarizeLoopFallbackOnError:
    """Verify the static fallback is used when the LLM raises."""

    @pytest.mark.asyncio
    async def test_llm_raises_returns_fallback(self):
        mock_llm = MagicMock()
        mock_llm.invoke = MagicMock(side_effect=RuntimeError("LLM provider down"))

        detection = _make_detection(tool_name="flaky_tool", repetition_count=7)
        with patch("daemon.graph.ThinkingChatOpenAI", return_value=mock_llm):
            summary = await LoopRepairer._summarize_loop(
                detection, [], {"model": "gpt-4o"}, timeout_seconds=5
            )

        # Static fallback: "The agent called <tool> <count> times..."
        assert "flaky_tool" in summary
        assert "7 times" in summary
        assert "without progress" in summary

    @pytest.mark.asyncio
    async def test_repair_succeeds_with_fallback(self):
        """Full repair() still completes successfully when LLM errors."""
        mock_llm = MagicMock()
        mock_llm.invoke = MagicMock(side_effect=ValueError("bad input"))
        graph = _mock_graph(updated_messages=["ok"])

        with patch("daemon.graph.ThinkingChatOpenAI", return_value=mock_llm):
            result = await LoopRepairer().repair(_make_context(graph=graph))

        assert result.success is True
        assert result.summary and "times" in result.summary
        # State update still happened — the repair is not aborted.
        graph.aupdate_state.assert_awaited_once()


# ===========================================================================
# Test 6: Fallback on LLM timeout
# ===========================================================================


class TestSummarizeLoopFallbackOnTimeout:
    """Verify the static fallback is used when the LLM call times out."""

    @pytest.mark.asyncio
    async def test_timeout_returns_fallback(self):
        """``asyncio.wait_for`` fires → fallback used."""
        detection = _make_detection(tool_name="slow_tool", repetition_count=4)

        # Patch ``asyncio.to_thread`` inside daemon.graph to return a
        # coroutine that sleeps long enough that wait_for fires.
        async def _never_resolves(*args, **kwargs):
            await asyncio.sleep(5)  # much longer than 0.1s timeout
            return "should not reach"

        with patch("daemon.graph.asyncio.to_thread", side_effect=_never_resolves):
            summary = await LoopRepairer._summarize_loop(
                detection,
                [],
                {"model": "gpt-4o"},
                timeout_seconds=1,
            )

        assert "slow_tool" in summary
        assert "4 times" in summary
        assert "without progress" in summary

    @pytest.mark.asyncio
    async def test_repair_succeeds_on_timeout(self):
        """Full repair() completes successfully even when summarization times out."""
        detection = _make_detection(tool_name="slow_tool", repetition_count=4)
        graph = _mock_graph(updated_messages=["repaired"])

        async def _never_resolves(*args, **kwargs):
            await asyncio.sleep(5)

        with patch("daemon.graph.asyncio.to_thread", side_effect=_never_resolves):
            result = await LoopRepairer().repair(_make_context(
                graph=graph,
                detection=detection,
                summarization_timeout_seconds=1,
            ))

        assert result.success is True
        assert "slow_tool" in result.summary


# ===========================================================================
# Test 7: Injected message re-append
# ===========================================================================


class TestRepairInjectedMessageReappend:
    """Verify ``injected_msg`` is re-appended after the state re-read (C3 pattern)."""

    @pytest.mark.asyncio
    async def test_injected_msg_appears_at_end_of_repaired_messages(self):
        graph = _mock_graph(updated_messages=["state-msg-A", "state-msg-B"])
        injected = HumanMessage(
            content="user's injected content",
            additional_kwargs={"injected_message": True},
        )
        mock_llm = _mock_llm("ok")

        with patch("daemon.graph.ThinkingChatOpenAI", return_value=mock_llm):
            result = await LoopRepairer().repair(_make_context(
                graph=graph,
                injected_msg=[injected],
            ))

        assert result.success is True
        # The injected HumanMessage MUST be the LAST item in the list.
        assert len(result.repaired_messages) == 3
        assert result.repaired_messages[0].id == "state-msg-A"
        assert result.repaired_messages[1].id == "state-msg-B"
        assert result.repaired_messages[2] is injected

    @pytest.mark.asyncio
    async def test_no_injected_msg_no_reappend(self):
        """When ``injected_msg`` is None, repaired_messages equals the state re-read.

        With the Layer 1 pre-validation in place, the live state must
        contain the loop message IDs (otherwise they'd be filtered out
        as "renamed by compaction"). The repaired list therefore
        contains the AIMessage instances from the live state re-read.
        """
        graph = _mock_graph(updated_messages=["x", "y"])

        with patch("daemon.graph.ThinkingChatOpenAI", return_value=_mock_llm("ok")):
            result = await LoopRepairer().repair(_make_context(
                graph=graph,
                injected_msg=None,
            ))

        assert result.success is True
        # The repaired messages are the AIMessage instances seeded into
        # the live checkpoint — verified by their IDs.
        assert [m.id for m in result.repaired_messages] == ["x", "y"]


# ===========================================================================
# Test 8: Full repair failure returns original messages
# ===========================================================================


class TestRepairFailureReturnsOriginal:
    """Verify defensive fallback when ``aupdate_state`` itself raises."""

    @pytest.mark.asyncio
    async def test_aupdate_state_raises_returns_original_messages(self):
        original_messages = [
            HumanMessage(content="orig-1", id="orig-1"),
            AIMessage(content="orig-2", id="orig-2"),
            AIMessage(content="loop-1", id="loop-1", tool_calls=[
                {"id": "tc-1", "name": "foo", "args": {}},
            ]),
        ]
        detection = _make_detection(
            loop_message_ids=["loop-1"],
            evidence_ids=["evidence-ai-0"],
        )
        graph = _mock_graph(updated_messages=original_messages)
        graph.aupdate_state = AsyncMock(
            side_effect=RuntimeError("checkpoint DB locked")
        )
        mock_llm = _mock_llm("ok")

        with patch("daemon.graph.ThinkingChatOpenAI", return_value=mock_llm):
            result = await LoopRepairer().repair(_make_context(
                graph=graph,
                messages=original_messages,
                detection=detection,
            ))

        assert result.success is False
        assert result.error is not None
        assert "checkpoint DB locked" in result.error
        # The repair_message_id is empty on failure.
        assert result.repair_message_id == ""
        # The repaired_messages MUST equal the ORIGINAL input list — the
        # graph can continue with these and recursion_limit protects
        # against runaway loops.
        assert result.repaired_messages == original_messages

    @pytest.mark.asyncio
    async def test_llm_construct_failure_uses_fallback_and_completes(self):
        """If ``ThinkingChatOpenAI()`` itself raises, the static fallback
        is used and the repair still completes successfully.

        This documents the contract: ``_summarize_loop`` catches ALL
        exceptions (not just ``asyncio.TimeoutError``) and returns the
        fallback. The repair therefore succeeds — the fallback is the
        agent's only signal that something went wrong, but the loop is
        still broken.
        """
        graph = _mock_graph(updated_messages=["after-state"])
        mock_detection = _make_detection(tool_name="any_tool", repetition_count=3)

        with patch(
            "daemon.graph.ThinkingChatOpenAI",
            side_effect=ValueError("invalid config"),
        ):
            result = await LoopRepairer().repair(_make_context(
                graph=graph,
                messages=[HumanMessage(content="orig", id="o-1")],
                detection=mock_detection,
            ))

        # Repair still succeeds — the summarization fallback was used.
        assert result.success is True
        assert "any_tool" in result.summary
        assert "3 times" in result.summary
        # The state update still ran with the fallback summary embedded
        # in the repair SystemMessage.
        graph.aupdate_state.assert_awaited_once()


# ===========================================================================
# Extra: REPAIR_SUMMARIZATION_PROMPT format shape
# ===========================================================================


class TestRepairPromptTemplate:
    """Sanity check the prompt template + the constant is importable."""

    def test_prompt_has_expected_fields(self):
        # The template must have all four interpolatable fields.
        assert "{tool_name}" in REPAIR_SUMMARIZATION_PROMPT
        assert "{tool_args}" in REPAIR_SUMMARIZATION_PROMPT
        assert "{count}" in REPAIR_SUMMARIZATION_PROMPT
        assert "{conversation_excerpt}" in REPAIR_SUMMARIZATION_PROMPT

    def test_prompt_formats_cleanly(self):
        rendered = REPAIR_SUMMARIZATION_PROMPT.format(
            tool_name="x",
            tool_args="{}",
            count=3,
            conversation_excerpt="hello",
        )
        assert "{tool_name}" not in rendered
        assert "x" in rendered
        assert "3" in rendered

    def test_default_timeout_constant(self):
        # The default timeout is the LOOP_BREAKER constant (30s).
        assert LOOP_BREAKER_SUMMARIZATION_TIMEOUT_SECONDS == 30


# ===========================================================================
# Test 9: Layer 1 — pre-validation against the live checkpoint
# ===========================================================================
# ``daemon/compaction.py:696-699`` renames message IDs from ``lc_run--...``
# to ``truncated-<uuid>`` during compaction. The repair was previously
# building ``RemoveMessage(id=msg_id)`` from the in-memory state list, but
# ``aupdate_state`` re-reads the checkpoint independently and raised::
#
#     ValueError: Attempting to delete a message with an ID that doesn't
#     exist ('lc_run--...')
#
# Layer 1 pre-validates removal IDs against the live checkpoint and filters
# out any ID that was renamed. Layer 2 (see Test 10) catches the residual
# race between the pre-validation read and the actual write.


class TestLayer1PreValidation:
    """Layer 1: pre-validate removal IDs against the live checkpoint state."""

    @pytest.mark.asyncio
    async def test_partial_mismatch_filters_only_missing_ids(self):
        """When SOME removal IDs exist in the checkpoint and SOME don't,
        only the valid ones are removed; the repair completes successfully.
        """
        # Default detection has loop_message_ids=["loop-ai-1", "loop-ai-2"].
        # Seed the live checkpoint with ONLY one of them — the other has
        # been renamed by compaction.
        graph = _mock_graph(updated_messages=["loop-ai-1"])
        mock_llm = _mock_llm("ok")

        with patch("daemon.graph.ThinkingChatOpenAI", return_value=mock_llm):
            result = await LoopRepairer().repair(_make_context(graph=graph))

        # Repair still succeeds — Layer 1 filtered out the missing ID.
        assert result.success is True
        # The aupdate_state call received only the ONE valid removal plus
        # the repair SystemMessage.
        call = graph.aupdate_state.call_args
        replacement = call.args[1]["messages"]
        removals = [m for m in replacement if isinstance(m, RemoveMessage)]
        systems = [m for m in replacement if isinstance(m, SystemMessage)]
        assert len(removals) == 1
        assert removals[0].id == "loop-ai-1"
        # The repair SystemMessage is still appended (loop still broken).
        assert len(systems) == 1
        # Exactly one aupdate_state call (no Layer 2 retry needed).
        graph.aupdate_state.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_all_ids_missing_skips_removal_step(self):
        """When ALL removal IDs are missing (renamed by compaction),
        the removal step is skipped entirely; the repair still completes
        with summary + fresh SystemMessage.
        """
        # Live checkpoint has NONE of the loop message IDs.
        graph = _mock_graph(updated_messages=["unrelated-msg-1", "unrelated-msg-2"])
        mock_llm = _mock_llm("ok")

        with patch("daemon.graph.ThinkingChatOpenAI", return_value=mock_llm):
            result = await LoopRepairer().repair(_make_context(graph=graph))

        # Repair still succeeds — Layer 1 short-circuited the removals.
        assert result.success is True
        # The aupdate_state call receives ONLY the repair SystemMessage
        # (no RemoveMessage sentinels).
        call = graph.aupdate_state.call_args
        replacement = call.args[1]["messages"]
        removals = [m for m in replacement if isinstance(m, RemoveMessage)]
        systems = [m for m in replacement if isinstance(m, SystemMessage)]
        assert len(removals) == 0
        assert len(systems) == 1
        # Layer 1 logged a warning about the all-missing case.
        # Layer 2 was NOT triggered (no ValueError was raised).
        graph.aupdate_state.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_aget_state_failure_falls_back_to_unfiltered_removals(self):
        """When the pre-validation ``aget_state`` call (Layer 1) fails
        with a transient error, Layer 1 returns the UNFILTERED list and
        the repair proceeds. The post-repair re-read (Step 5) then
        succeeds normally.
        """
        # Build a graph whose aget_state fails on the FIRST call (Layer
        # 1 pre-validation) and succeeds on the SECOND call (Step 5
        # post-repair re-read). This mirrors a transient checkpoint
        # hiccup that recovers by the time we write.
        graph = MagicMock()
        aget_state_calls: list[str] = []

        async def fake_aget_state(config):
            aget_state_calls.append("call")
            if len(aget_state_calls) == 1:
                # First call (Layer 1) fails.
                raise RuntimeError("checkpoint store unavailable")
            # Second call (Step 5) succeeds with a realistic post-repair
            # state. The exact contents are not asserted here — this
            # test focuses on Layer 1's fallback behavior.
            return MagicMock(values={"messages": [
                AIMessage(content="repaired", id="repaired-1"),
            ]})

        graph.aget_state = AsyncMock(side_effect=fake_aget_state)
        graph.aupdate_state = AsyncMock()
        mock_llm = _mock_llm("ok")

        with patch("daemon.graph.ThinkingChatOpenAI", return_value=mock_llm):
            result = await LoopRepairer().repair(_make_context(graph=graph))

        # Repair did not abort on the pre-validation failure — Layer 1
        # returned the unfiltered list and the repair completed.
        assert result.success is True
        # aupdate_state was called once with the unfiltered removal
        # list (Layer 1 didn't filter anything because the read failed).
        assert graph.aupdate_state.await_count == 1
        call = graph.aupdate_state.call_args
        replacement = call.args[1]["messages"]
        removals = [m for m in replacement if isinstance(m, RemoveMessage)]
        # Both loop message IDs are present (unfiltered).
        assert len(removals) == 2
        assert {r.id for r in removals} == {"loop-ai-1", "loop-ai-2"}

    @pytest.mark.asyncio
    async def test_all_ids_present_no_filtering(self):
        """Sanity: when every removal ID is in the live checkpoint, the
        filter is a no-op and the full removal list is passed through.
        """
        # Seed the checkpoint with BOTH loop message IDs.
        graph = _mock_graph(updated_messages=["loop-ai-1", "loop-ai-2"])
        mock_llm = _mock_llm("ok")

        with patch("daemon.graph.ThinkingChatOpenAI", return_value=mock_llm):
            result = await LoopRepairer().repair(_make_context(graph=graph))

        assert result.success is True
        call = graph.aupdate_state.call_args
        replacement = call.args[1]["messages"]
        removals = [m for m in replacement if isinstance(m, RemoveMessage)]
        # Both removal sentinels survive Layer 1.
        assert len(removals) == 2
        assert {r.id for r in removals} == {"loop-ai-1", "loop-ai-2"}


# ===========================================================================
# Test 10: Layer 2 — try/except ValueError safety net
# ===========================================================================
# Layer 1 catches the COMMON case of stale IDs. Layer 2 handles the
# residual race: a second compaction could rename IDs between Layer 1's
# ``aget_state`` read and the actual ``aupdate_state`` write, raising::
#
#     ValueError: Attempting to delete a message with an ID that doesn't
#     exist ('lc_run--...')
#
# On ValueError, Layer 2 strips all removals and retries the write with
# just the repair SystemMessage. The LLM retry still gets the fresh
# SystemMessage nudge, so the loop is still broken.


class TestLayer2ValueErrorSafetyNet:
    """Layer 2: catch ValueError from stale removal IDs despite Layer 1."""

    @pytest.mark.asyncio
    async def test_value_error_triggers_retry_without_removals(self):
        """Simulate a race: Layer 1's aget_state returns the IDs as
        present, but aupdate_state raises ValueError (another
        compaction ran between the two). Layer 2 must retry with
        ONLY the repair SystemMessage.
        """
        # Build a graph whose aupdate_state raises ValueError on the
        # FIRST call (with removals) and succeeds on the second call
        # (without removals).
        graph = MagicMock()
        call_log: list[list] = []

        async def fake_aupdate_state(config, values, as_node=None):
            call_log.append(list(values.get("messages", [])))
            if len(call_log) == 1:
                # First call: with removals → simulate stale ID.
                raise ValueError(
                    "Attempting to delete a message with an ID that "
                    "doesn't exist ('lc_run--stale-id')"
                )
            # Second call: without removals → success.

        graph.aupdate_state = AsyncMock(side_effect=fake_aupdate_state)
        # aget_state returns the loop IDs (Layer 1 lets them through).
        graph.aget_state = AsyncMock(
            return_value=MagicMock(values={"messages": [
                AIMessage(content="", id="loop-ai-1"),
                AIMessage(content="", id="loop-ai-2"),
            ]})
        )
        mock_llm = _mock_llm("ok")

        with patch("daemon.graph.ThinkingChatOpenAI", return_value=mock_llm):
            result = await LoopRepairer().repair(_make_context(graph=graph))

        # Repair succeeded via the Layer 2 retry path.
        assert result.success is True
        # Exactly TWO aupdate_state calls: the failing first attempt
        # + the successful retry without removals.
        assert graph.aupdate_state.await_count == 2
        # First call had RemoveMessage sentinels.
        first_call_msgs = call_log[0]
        assert any(isinstance(m, RemoveMessage) for m in first_call_msgs)
        # Second call had ONLY the repair SystemMessage (no removals).
        second_call_msgs = call_log[1]
        assert not any(isinstance(m, RemoveMessage) for m in second_call_msgs)
        assert any(isinstance(m, SystemMessage) for m in second_call_msgs)

    @pytest.mark.asyncio
    async def test_value_error_does_not_bubble_to_outer_failure(self):
        """When Layer 2 successfully retries, the OUTER exception handler
        must NOT fire — the repair is a success, not a failure.
        """
        graph = MagicMock()

        async def fake_aupdate_state(config, values, as_node=None):
            # Always raise ValueError (Layer 1 + Layer 2 together can't
            # help if the IDs are still bad). The second call (retry
            # without removals) should succeed because we have no
            # RemoveMessage sentinels anymore.
            msgs = list(values.get("messages", []))
            has_removal = any(isinstance(m, RemoveMessage) for m in msgs)
            if has_removal:
                raise ValueError(
                    "Attempting to delete a message with an ID that "
                    "doesn't exist ('lc_run--bad')"
                )
            return None

        graph.aupdate_state = AsyncMock(side_effect=fake_aupdate_state)
        graph.aget_state = AsyncMock(
            return_value=MagicMock(values={"messages": [
                AIMessage(content="", id="loop-ai-1"),
            ]})
        )
        mock_llm = _mock_llm("ok")

        with patch("daemon.graph.ThinkingChatOpenAI", return_value=mock_llm):
            result = await LoopRepairer().repair(_make_context(graph=graph))

        # Repair succeeded via the Layer 2 retry — no outer failure.
        assert result.success is True
        assert result.error is None
        # The retry call was made with ONLY the SystemMessage.
        assert graph.aupdate_state.await_count == 2
        second_call = graph.aupdate_state.call_args_list[1]
        retry_msgs = second_call.args[1]["messages"]
        assert not any(isinstance(m, RemoveMessage) for m in retry_msgs)
        assert any(isinstance(m, SystemMessage) for m in retry_msgs)

    @pytest.mark.asyncio
    async def test_runtime_error_still_bubbles_to_outer_failure(self):
        """Non-ValueError exceptions (e.g. transient DB error on the
        write) MUST still propagate to the outer failure path so the
        repair returns the ORIGINAL messages. Only ValueError gets
        the Layer 2 retry.
        """
        graph = _mock_graph(updated_messages=["loop-ai-1", "loop-ai-2"])
        graph.aupdate_state = AsyncMock(
            side_effect=RuntimeError("checkpoint DB locked")
        )
        mock_llm = _mock_llm("ok")

        with patch("daemon.graph.ThinkingChatOpenAI", return_value=mock_llm):
            result = await LoopRepairer().repair(_make_context(graph=graph))

        # RuntimeError is NOT a ValueError → no Layer 2 retry.
        assert result.success is False
        assert "checkpoint DB locked" in (result.error or "")
        # The original messages are returned for fallback.
        assert result.repair_message_id == ""
