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
    4. ``repair()`` builds ``repaired_messages`` IN-MEMORY by filtering
       ``context.messages`` against the removal IDs and prepending the
       repair ``SystemMessage``. No checkpoint round-trip is performed
       (fix-loop-repairer-checkpoint-ns).
    5. ``_summarize_loop`` falls back to the static summary when the LLM
       raises — repair still succeeds.
    6. ``_summarize_loop`` falls back to the static summary when the LLM
       call times out via ``asyncio.wait_for``.
    7. ``repair()`` re-appends ``RepairContext.injected_msg`` to the
       ``repaired_messages`` after the in-memory build (C3 pattern).
    8. ``repair()`` returns the ORIGINAL message list when an unexpected
       exception bubbles out of any step (defensive fallback so the graph
       can keep running).
    9. In-memory pre-validation: ``repair()`` filters ``RemoveMessage``
       IDs against ``context.messages`` (the authoritative snapshot from
       ``create_agent_node``). All IDs match in the common case (no-op);
       missing IDs are dropped with a WARNING.
   10. Option C safety-net: when ``removals`` is non-empty but the
       in-memory filter removed nothing (all IDs missing), the repair
       falls back to ``context.messages + [repair_msg]`` (append) and
       logs a WARNING.

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

    Kept for backward compatibility with callers that still inspect graph
    call counts (e.g. ``aupdate_state.assert_not_called()`` after the
    repairer finishes). The in-memory repairer does NOT call
    ``aupdate_state`` or ``aget_state`` — see
    ``fix-loop-repairer-checkpoint-ns`` — so the ``updated_messages``
    argument is unused by the repairer itself; it remains here so legacy
    callers that pass a real compiled graph can construct the mock
    without raising.

    String entries are auto-coerced into ``AIMessage(id=string)`` instances
    so tests that DO read ``aget_state`` (none currently do, but future
    ones might) see realistic message objects.
    """
    # Coerce string entries into AIMessage so .id is accessible. This
    # mirrors the real LangGraph checkpoint shape (BaseMessage instances
    # with ``.id`` set) for callers that still consult the mock.
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
    """Build a fully-populated :class:`RepairContext` with sensible defaults.

    The default ``messages`` list contains AIMessage instances with the same
    IDs as the default detection's ``loop_message_ids`` — the in-memory
    filter reads from ``context.messages`` (not the checkpoint), so the
    IDs need to match ``detection.loop_messages`` for the common case
    to exercise the no-op filter path.
    """
    if detection is None:
        detection = _make_detection()
    if messages is None:
        # Build a realistic default that contains the loop IDs from the
        # detection so the in-memory filter has them to match against.
        messages = [
            HumanMessage(content="please help me with this", id="h-0"),
            AIMessage(
                content="",
                id="loop-ai-1",
                tool_calls=[{"id": "tc-loop-ai-1", "name": detection.tool_name, "args": {}}],
            ),
            AIMessage(
                content="",
                id="loop-ai-2",
                tool_calls=[{"id": "tc-loop-ai-2", "name": detection.tool_name, "args": {}}],
            ),
        ]
    return RepairContext(
        detection=detection,
        messages=messages,
        # graph is kept in RepairContext for backward compatibility with
        # other callers/tests; the repairer no longer reads from it.
        thread_config={"configurable": {"thread_id": "instance-test"}},
        graph=graph or _mock_graph(),
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
    """Verify ``repair()`` builds ``repaired_messages`` IN-MEMORY from
    ``context.messages`` and does NOT touch the checkpoint.

    Pre-fix this class asserted ``graph.aupdate_state`` was called with the
    ``RemoveMessage`` sentinels + repair ``SystemMessage`` and ``as_node='agent'``.
    Post-fix (fix-loop-repairer-checkpoint-ns) the repairer operates purely
    on the in-memory list, so the assertions invert: the checkpoint methods
    MUST NOT be called, and the repaired messages MUST come from
    ``context.messages`` (filtered by the removal IDs) with the repair
    ``SystemMessage`` prepended.
    """

    @pytest.mark.asyncio
    async def test_repaired_messages_built_in_memory_from_context(self):
        """``repaired_messages`` contains the in-memory messages with loop
        IDs removed and the repair ``SystemMessage`` prepended. NO checkpoint
        methods are called.
        """
        # ``_make_context`` defaults ``messages`` to a list containing the
        # loop IDs so the in-memory filter has them to match.
        mock_llm = _mock_llm("summary text")
        graph = _mock_graph()

        with patch("daemon.graph.ThinkingChatOpenAI", return_value=mock_llm):
            result = await LoopRepairer().repair(_make_context(graph=graph))

        assert result.success is True
        # The checkpoint methods MUST NOT be called — the in-memory state
        # is authoritative. This is the key behavioral change of
        # fix-loop-repairer-checkpoint-ns.
        graph.aupdate_state.assert_not_called()
        graph.aget_state.assert_not_called()
        # The repaired list contains: repair SystemMessage (FIRST) +
        # HumanMessage "h-0" (preserved) + one AIMessage (loop-ai-2
        # removed because its ID was in removal_ids).
        repaired = result.repaired_messages
        assert len(repaired) == 2
        assert isinstance(repaired[0], SystemMessage)
        assert repaired[0].id == result.repair_message_id
        # Loop 1 was removed (its ID was in the removal set); loop 2 was
        # also removed. HumanMessage "h-0" survived.
        assert isinstance(repaired[1], HumanMessage)
        assert repaired[1].id == "h-0"
        # The repaired list MUST NOT contain any RemoveMessage sentinels
        # (those were checkpoint artifacts; the in-memory list contains
        # only real messages + the repair SystemMessage).
        assert not any(isinstance(m, RemoveMessage) for m in repaired)

    @pytest.mark.asyncio
    async def test_repair_message_prepended_to_filtered_list(self):
        """The repair ``SystemMessage`` is the FIRST item — LLM sees the
        directive before the conversation history.
        """
        mock_llm = _mock_llm("ok")

        with patch("daemon.graph.ThinkingChatOpenAI", return_value=mock_llm):
            result = await LoopRepairer().repair(_make_context())

        assert result.success is True
        assert len(result.repaired_messages) >= 1
        # The FIRST message MUST be the repair SystemMessage.
        first = result.repaired_messages[0]
        assert isinstance(first, SystemMessage)
        # The repair SystemMessage ID matches ``repair_message_id``.
        assert first.id == result.repair_message_id
        # And the prefix is the canonical ``LOOP_BREAKER_REPAIR_PREFIX``.
        assert first.id.startswith(LOOP_BREAKER_REPAIR_PREFIX)


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
        graph = _mock_graph()

        with patch("daemon.graph.ThinkingChatOpenAI", return_value=mock_llm):
            result = await LoopRepairer().repair(_make_context(graph=graph))

        assert result.success is True
        assert result.summary and "times" in result.summary
        # The in-memory repairer did NOT touch the checkpoint.
        graph.aupdate_state.assert_not_called()
        graph.aget_state.assert_not_called()


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
    """Verify ``injected_msg`` is re-appended after the in-memory build (C3 pattern).

    Pre-fix this class asserted the repaired list contained the messages
    from the live checkpoint (re-read via ``aget_state``). Post-fix the
    repaired list is built from ``context.messages`` directly; the
    injected messages still need to be appended at the END so the LLM
    retry sees every user's intent (C3 invariant — the injection lives
    only in the local closure).
    """

    @pytest.mark.asyncio
    async def test_injected_msg_appears_at_end_of_repaired_messages(self):
        """The injected HumanMessage MUST be the LAST item in the list."""
        injected = HumanMessage(
            content="user's injected content",
            additional_kwargs={"injected_message": True},
        )
        mock_llm = _mock_llm("ok")

        with patch("daemon.graph.ThinkingChatOpenAI", return_value=mock_llm):
            result = await LoopRepairer().repair(_make_context(
                injected_msg=[injected],
            ))

        assert result.success is True
        # The injected HumanMessage MUST be the LAST item in the list.
        # The first item is the repair SystemMessage; the tail is the
        # injected message (loop messages filtered out by their IDs).
        assert result.repaired_messages[-1] is injected
        # The first item is still the repair SystemMessage.
        assert isinstance(result.repaired_messages[0], SystemMessage)

    @pytest.mark.asyncio
    async def test_no_injected_msg_no_reappend(self):
        """When ``injected_msg`` is None, the repaired list contains the
        in-memory ``context.messages`` (with loop IDs removed) plus the
        repair ``SystemMessage`` prepended.
        """
        with patch("daemon.graph.ThinkingChatOpenAI", return_value=_mock_llm("ok")):
            result = await LoopRepairer().repair(_make_context(injected_msg=None))

        assert result.success is True
        # The default ``messages`` had 3 items: HumanMessage "h-0" +
        # AIMessage "loop-ai-1" + AIMessage "loop-ai-2". After filtering
        # the two loop IDs, only "h-0" survives. Plus the prepended
        # repair SystemMessage → 2 total.
        assert len(result.repaired_messages) == 2
        assert isinstance(result.repaired_messages[0], SystemMessage)
        assert result.repaired_messages[1].id == "h-0"


# ===========================================================================
# Test 8: Full repair failure returns original messages
# ===========================================================================


class TestRepairFailureReturnsOriginal:
    """Verify defensive fallback when an unexpected exception bubbles out
    of any repair step (LLM construction failure, etc.).

    Pre-fix this class tested the ``aupdate_state`` failure path — the
    in-memory repairer no longer calls ``aupdate_state``, so the
    corresponding test is replaced with one that triggers the outer
    ``except Exception`` handler via a different mechanism.
    """

    @pytest.mark.asyncio
    async def test_repair_summary_failure_returns_original_messages(self):
        """If the repair build step raises unexpectedly (e.g. a custom
        ``_build_repair_message`` raises), the outer ``except Exception``
        handler MUST fall back to the ORIGINAL message list — same
        contract as the pre-fix ``aupdate_state``-raises path.
        """
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
        mock_llm = _mock_llm("ok")
        graph = _mock_graph()

        # Patch ``_build_repair_message`` (the static method on
        # ``LoopRepairer``) to raise after summarization succeeds. This
        # is the last step before the in-memory build, so it cleanly
        # exercises the outer ``except Exception`` fallback path.
        import daemon.graph as dg

        def boom_build_repair_message(*args, **kwargs):
            raise RuntimeError("simulated build failure")

        with patch.object(
            dg.LoopRepairer, "_build_repair_message",
            side_effect=boom_build_repair_message,
        ):
            with patch("daemon.graph.ThinkingChatOpenAI", return_value=mock_llm):
                result = await LoopRepairer().repair(_make_context(
                    graph=graph,
                    messages=original_messages,
                    detection=detection,
                ))

        assert result.success is False
        assert result.error is not None
        assert "simulated build failure" in result.error
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
        mock_detection = _make_detection(tool_name="any_tool", repetition_count=3)
        graph = _mock_graph()

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
        # The repairer did NOT touch the checkpoint.
        graph.aupdate_state.assert_not_called()
        graph.aget_state.assert_not_called()


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
# Test 9: In-memory pre-validation
# ===========================================================================
# ``repair()`` filters ``RemoveMessage`` IDs against ``context.messages``
# (the authoritative in-memory snapshot from ``create_agent_node``).
# Pre-fix this filter ran against the live checkpoint via
# ``graph.aget_state``, but the in-node ``thread_config`` carries
# ``checkpoint_ns='agent:<task_id>'`` which LangGraph interprets as a
# subgraph namespace lookup and returns EMPTY state — so ALL removals
# were filtered out and the loop was never actually broken. The
# in-memory filter shares IDs with the detection snapshot, so the common
# case is a silent no-op (all IDs match); missing IDs are dropped with a
# WARNING.


class TestInMemoryPreValidation:
    """In-memory pre-validation: filter removal IDs against
    ``context.messages``.
    """

    @pytest.mark.asyncio
    async def test_all_ids_present_no_filtering(self):
        """Sanity: when every removal ID is in ``context.messages``, the
        filter is a no-op and the full removal list is consumed.
        """
        # Default ``_make_context`` provides ``messages`` containing
        # ``loop-ai-1`` and ``loop-ai-2`` — both match the default
        # detection's loop IDs. No filtering.
        mock_llm = _mock_llm("ok")
        graph = _mock_graph()

        with patch("daemon.graph.ThinkingChatOpenAI", return_value=mock_llm):
            result = await LoopRepairer().repair(_make_context(graph=graph))

        assert result.success is True
        # The two loop messages were filtered out of ``context.messages``;
        # only the HumanMessage "h-0" + the repair SystemMessage remain.
        assert len(result.repaired_messages) == 2
        assert isinstance(result.repaired_messages[0], SystemMessage)
        assert result.repaired_messages[1].id == "h-0"
        # No checkpoint activity.
        graph.aupdate_state.assert_not_called()
        graph.aget_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_partial_mismatch_filters_only_missing_ids(self):
        """When SOME removal IDs exist in ``context.messages`` and SOME
        don't, only the matching ones are removed; the repair completes
        successfully with the rest filtered out.
        """
        # ``messages`` contains ONLY ``loop-ai-1`` — ``loop-ai-2`` is
        # missing. The in-memory filter should drop the missing ID
        # silently and the surviving loop-ai-1 is still removed.
        messages = [
            HumanMessage(content="h-0", id="h-0"),
            AIMessage(content="loop-1", id="loop-ai-1", tool_calls=[
                {"id": "tc-1", "name": "foo", "args": {}},
            ]),
        ]
        mock_llm = _mock_llm("ok")

        with patch("daemon.graph.ThinkingChatOpenAI", return_value=mock_llm):
            result = await LoopRepairer().repair(_make_context(messages=messages))

        assert result.success is True
        # ``loop-ai-1`` was removed from the in-memory list. ``loop-ai-2``
        # was already missing (not in messages) so the filter silently
        # dropped it. Result: repair SystemMessage + HumanMessage "h-0".
        assert len(result.repaired_messages) == 2
        assert isinstance(result.repaired_messages[0], SystemMessage)
        assert result.repaired_messages[1].id == "h-0"

    @pytest.mark.asyncio
    async def test_all_ids_missing_triggers_option_c_safety_net(self):
        """When ALL removal IDs are missing from ``context.messages``,
        the Option C safety-net fires: return ``[repair_msg] +
        context.messages`` (prepend, matching Option B semantics) and
        log a WARNING. This is the rare case where
        ``detection.loop_messages`` somehow diverged from
        ``context.messages`` IDs.
        """
        # Provide ``messages`` with NONE of the default loop IDs so the
        # filter drops everything.
        messages = [HumanMessage(content="orig", id="orig-1")]
        mock_llm = _mock_llm("ok")

        with patch("daemon.graph.ThinkingChatOpenAI", return_value=mock_llm):
            result = await LoopRepairer().repair(_make_context(messages=messages))

        # Repair still succeeds — Option C safety-net took over.
        assert result.success is True
        # Option C PREPENDS the repair message (mirroring Option B) so
        # the LLM sees the directive before the original history.
        assert len(result.repaired_messages) == 2
        # First item: repair SystemMessage.
        assert isinstance(result.repaired_messages[0], SystemMessage)
        assert result.repaired_messages[0].id.startswith(LOOP_BREAKER_REPAIR_PREFIX)
        # Last item: original HumanMessage (preserved at end).
        assert result.repaired_messages[-1].id == "orig-1"


# ===========================================================================
# Test 10: Option C safety-net
# ===========================================================================
# The pre-fix Layer 2 safety net caught ``ValueError`` from
# ``aupdate_state`` when removal IDs had been renamed between the
# pre-validation read and the write. Post-fix the in-memory repairer
# does not call ``aupdate_state`` at all — that race window no longer
# exists. The new Option C safety net handles the equivalent edge case
# in the in-memory filter: when ``removals`` is non-empty but the
# in-memory filter removed nothing (all IDs missing), the repair falls
# back to ``context.messages + [repair_msg]`` (append) instead of an
# empty removal list + prepend. This guarantees a structurally valid
# payload (HumanMessage + full history + repair nudge) even if the
# detection IDs diverge from the in-memory snapshot.


class TestOptionCSafetyNet:
    """Option C safety-net: when ``removals`` is non-empty but the
    in-memory filter removed nothing, the repair returns the ORIGINAL
    ``context.messages`` with the repair ``SystemMessage`` APPENDED.
    """

    @pytest.mark.asyncio
    async def test_all_ids_missing_returns_original_plus_prepended_repair(self):
        """The repaired list is ``[repair_msg] + context.messages`` —
        repair SystemMessage first, original HumanMessage(s) preserved
        after.
        """
        original = [
            HumanMessage(content="user's request", id="u-1"),
            HumanMessage(content="user's followup", id="u-2"),
        ]
        mock_llm = _mock_llm("ok")

        with patch("daemon.graph.ThinkingChatOpenAI", return_value=mock_llm):
            result = await LoopRepairer().repair(_make_context(messages=original))

        assert result.success is True
        # Repair succeeded via the Option C path. The repaired list
        # contains the repair SystemMessage PREPENDED at the start
        # (matching Option B semantics) followed by the ORIGINAL
        # messages.
        assert len(result.repaired_messages) == 3
        assert isinstance(result.repaired_messages[0], SystemMessage)
        assert result.repaired_messages[0].id.startswith(LOOP_BREAKER_REPAIR_PREFIX)
        assert result.repaired_messages[1] is original[0]
        assert result.repaired_messages[2] is original[1]

    @pytest.mark.asyncio
    async def test_option_c_falls_back_when_no_overlap(self):
        """Sanity: the Option C path is taken when ``removal_ids`` is
        non-empty BUT every ID is missing from ``context.messages``.
        """
        # Two loop IDs in detection, neither in ``context.messages``.
        original = [HumanMessage(content="only-this", id="only-this")]
        mock_llm = _mock_llm("ok")

        with patch("daemon.graph.ThinkingChatOpenAI", return_value=mock_llm):
            result = await LoopRepairer().repair(_make_context(messages=original))

        assert result.success is True
        # Result: repair SystemMessage FIRST (prepended), original HumanMessage LAST.
        assert len(result.repaired_messages) == 2
        assert isinstance(result.repaired_messages[0], SystemMessage)
        assert result.repaired_messages[0].id.startswith(LOOP_BREAKER_REPAIR_PREFIX)
        assert result.repaired_messages[1].id == "only-this"

    @pytest.mark.asyncio
    async def test_option_c_not_triggered_when_filter_actually_removes(self):
        """The Option C path is NOT taken when the in-memory filter
        successfully removed at least one message — normal Option B
        path is used (prepend).
        """
        # ``messages`` contains ONE of the two loop IDs, so the filter
        # will remove it. Result is Option B (prepend), NOT Option C.
        messages = [
            HumanMessage(content="h-0", id="h-0"),
            AIMessage(content="loop-1", id="loop-ai-1", tool_calls=[
                {"id": "tc-1", "name": "foo", "args": {}},
            ]),
        ]
        mock_llm = _mock_llm("ok")

        with patch("daemon.graph.ThinkingChatOpenAI", return_value=mock_llm):
            result = await LoopRepairer().repair(_make_context(messages=messages))

        assert result.success is True
        # Option B path: repair SystemMessage prepended, loop removed.
        assert len(result.repaired_messages) == 2
        assert isinstance(result.repaired_messages[0], SystemMessage)
        assert result.repaired_messages[1].id == "h-0"

    @pytest.mark.asyncio
    async def test_option_c_skipped_when_removals_empty(self):
        """When the detection yields no removal IDs (``loop_messages`` is
        empty), ``removal_ids`` is also empty — Option C's
        ``removal_ids and ...`` guard MUST NOT fire. Result is Option B
        with an empty filter (everything preserved + repair message
        prepended).
        """
        detection = _make_detection(loop_message_ids=[], evidence_ids=[])
        messages = [
            HumanMessage(content="h-0", id="h-0"),
            AIMessage(content="a-0", id="a-0"),
        ]
        mock_llm = _mock_llm("ok")

        with patch("daemon.graph.ThinkingChatOpenAI", return_value=mock_llm):
            result = await LoopRepairer().repair(_make_context(
                detection=detection, messages=messages,
            ))

        assert result.success is True
        # Empty filter → all messages preserved + repair prepended.
        assert len(result.repaired_messages) == 3
        assert isinstance(result.repaired_messages[0], SystemMessage)
        assert result.repaired_messages[1].id == "h-0"
        assert result.repaired_messages[2].id == "a-0"
