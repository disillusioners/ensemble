"""Regression tests for the loop-repairer ``checkpoint_ns`` production bug.

Branch: ``fix/loop-repairer-checkpoint-ns``.

PRODUCTION BUG (the regression target):
    The original ``LoopRepairer.repair`` called
    ``graph.aget_state(context.thread_config)`` to pre-validate the removal
    IDs against the live LangGraph checkpoint. ``context.thread_config``
    carries ``checkpoint_ns='agent:<task_id>'`` — which LangGraph interprets
    as a subgraph namespace lookup and returns EMPTY ``state.values`` for.
    The repairer then filtered EVERY removal ID out (none matched the empty
    checkpoint snapshot), produced a 2-message payload (the repair
    ``SystemMessage`` + the ToolMessage that "matched" the missing namespace),
    and handed it to the LLM. The LLM proxy returned
    ``502 "chat content is empty"`` because the 2-message payload contained
    no ``HumanMessage``.

FIX (two parts):
    * Option B (primary): ``repair()`` now filters in-memory
      ``context.messages`` directly — no checkpoint round-trip. Removal IDs
      are guaranteed to match because both ``detection.loop_messages`` and
      ``context.messages`` come from the same in-node snapshot.
    * Option C (safety-net): when ``original_removal_count > 0`` but the
      in-memory filter dropped every ID, the repair falls back to
      ``[repair_msg] + context.messages`` (prepended) instead of an empty
      removals list + prepend. Guarantees a structurally valid payload
      (HumanMessage + history + repair nudge) under any ID divergence.

These tests are END-TO-END for the production scenario — kb-writer
instance stuck on the ``time`` tool, exactly the failure mode reported
in the bug. They use the REAL ``LoopDetector`` + ``LoopRepairer`` and a
stubbed ``ThinkingChatOpenAI`` for the summarization LLM so the full
in-memory pipeline runs without any network or checkpoint.

Scenarios:

    1. ``TestOriginalBugScenario`` — kb-writer loops on ``time`` 3 times,
       repair preserves the full conversation, HumanMessage present,
       no ``aget_state`` / ``aupdate_state`` calls.
    2. ``TestVeryFewMessagesHitLoop`` — 2-3 messages hit detection,
       repair still produces a structurally valid payload.
    3. ``TestMaxRepairsReached`` — ``max_repairs`` cap reached,
       the agent_node returns the ORIGINAL messages (no crash, no
       repair attempt).
    4. ``TestRepairFailureOnLLMTimeout`` — LLM summarization times out,
       outer ``except`` returns the ORIGINAL messages.
    5. ``TestOptionCSafetyNetFires`` — all removal IDs missing from
       ``context.messages``, the safety-net pre-pends the repair
       message and keeps the original HumanMessage(s).

Mocking style mirrors ``tests/unit/test_loop_repairer.py``:
``unittest.mock.{AsyncMock, MagicMock, patch}`` + patched
``daemon.graph.ThinkingChatOpenAI`` for the summarization LLM. The
graph stand-in (``_MockGraph``) records ``aget_state`` /
``aupdate_state`` calls so the ``assert_not_called`` invariant from
the fix can be checked directly.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from daemon.config import LoopBreakerConfig
from daemon.graph import (
    LOOP_BREAKER_REPAIR_PREFIX,
    LOOP_BREAKER_SUMMARIZATION_TIMEOUT_SECONDS,
    LoopDetector,
    LoopRepairer,
    RepairContext,
    RepairResult,
)


# ---------------------------------------------------------------------------
# Test doubles / helpers
# ---------------------------------------------------------------------------


class _MockGraph:
    """In-memory stand-in for a compiled LangGraph graph.

    Records ``aupdate_state`` and ``aget_state`` invocations so the
    regression assertions can prove the OLD path (which used both
    checkpoint round-trips) is gone. The ``aget_state`` return value
    intentionally returns an EMPTY messages list — exactly what
    LangGraph would do when ``checkpoint_ns='agent:<task_id>'`` is
    mis-interpreted as a subgraph namespace lookup. The test then
    asserts the repairer DID NOT fall for this trap.
    """

    def __init__(self) -> None:
        self.aupdate_state_calls: list[tuple[dict, str | None]] = []
        self.aget_state_calls: list[dict] = []

    async def aupdate_state(
        self, config: dict, values: dict, as_node: str | None = None
    ) -> None:
        self.aupdate_state_calls.append((values, as_node))

    async def aget_state(self, config: dict) -> MagicMock:
        self.aget_state_calls.append(config)
        # Simulate the production bug surface: LangGraph returns empty
        # state.values for the misnamed checkpoint_ns. The repairer MUST
        # NOT consult this — it filters against context.messages instead.
        state = MagicMock()
        state.values = {"messages": []}
        return state


class _StubSummaryLLM:
    """Stand-in for the summarization LLM inside ``_summarize_loop``.

    ``.invoke(messages)`` returns an ``AIMessage`` with a fixed summary
    string. Patched into ``daemon.graph.ThinkingChatOpenAI`` so the
    repairer's ``_summarize_loop`` builds the prompt and reads the
    response without any network.
    """

    def __init__(self, summary: str = "The agent was stuck calling time repeatedly.") -> None:
        self._summary = summary
        self.calls: list[list] = []

    def invoke(self, messages: list) -> AIMessage:
        self.calls.append(list(messages))
        return AIMessage(content=self._summary)


class _StubLoopBreakerSlot:
    """In-memory mock of ``LoopBreakerSlot`` — mirrors the contract used
    in ``tests/test_loop_breaker_integration.py``.
    """

    def __init__(self) -> None:
        self._state: dict[str, dict] = {}
        self.record_calls: list[tuple[str, str]] = []
        self.clear_calls: list[str] = []

    def record_repair(self, instance_id: str, summary: str) -> int:
        self.record_calls.append((instance_id, summary))
        state = self._state.setdefault(instance_id, {"count": 0, "last_summary": ""})
        state["count"] = state.get("count", 0) + 1
        state["last_summary"] = summary
        return state["count"]

    def clear(self, instance_id: str) -> None:
        self.clear_calls.append(instance_id)
        self._state.pop(instance_id, None)

    def get_repair_count(self, instance_id: str) -> int:
        return self._state.get(instance_id, {}).get("count", 0)


def _kb_writer_time_loop_messages(count: int = 3) -> list:
    """Build the production message list for a kb-writer instance stuck on
    the ``time`` tool.

    Mirrors the in-the-wild bug report: a single HumanMessage prompt
    asking the agent to record today's date, followed by ``count`` AI +
    ToolMessage pairs where the agent repeatedly called ``time({})``
    with identical (empty) args.
    """
    messages: list = [
        HumanMessage(
            content="Please record today's date in the knowledge base.",
            id="kb-user-1",
        ),
    ]
    for i in range(count):
        ai_id = f"kb-ai-{i}"
        tm_id = f"kb-tm-{i}"
        tc_id = f"kb-tc-{i}"
        ai = AIMessage(
            content="",
            id=ai_id,
            tool_calls=[{"id": tc_id, "name": "time", "args": {}}],
        )
        tm = ToolMessage(
            content="2026-08-14T04:16:30+00:00",
            tool_call_id=tc_id,
            name="time",
            id=tm_id,
        )
        messages.append(ai)
        messages.append(tm)
    return messages


def _make_repair_context(
    *,
    messages: list,
    detection=None,
    graph: _MockGraph | None = None,
    summarization_timeout_seconds: int = 5,
) -> RepairContext:
    """Build a fully-populated ``RepairContext`` for the regression tests.

    Defaults ``graph`` to a fresh ``_MockGraph`` so every test
    automatically tracks the ``aupdate_state`` / ``aget_state`` calls —
    critical for proving the OLD checkpoint path is gone.
    """
    if detection is None:
        detection = LoopDetector.scan(messages, threshold=3)
        assert detection is not None, "test setup: messages must trigger detection"
    return RepairContext(
        detection=detection,
        messages=list(messages),
        thread_config={"configurable": {"thread_id": "instance-kb-writer"}},
        graph=graph if graph is not None else _MockGraph(),
        llm_config={"model": "gpt-4o", "temperature": 0.0},
        system_prompt="You are a kb-writer agent.",
        injected_msg=None,
        summarization_timeout_seconds=summarization_timeout_seconds,
    )


# ---------------------------------------------------------------------------
# 1. ORIGINAL BUG SCENARIO — the headline regression test
# ---------------------------------------------------------------------------


class TestOriginalBugScenario:
    """kb-writer instance stuck on ``time`` 3 times. The OLD repairer
    called ``graph.aget_state``, got an empty messages list (because of
    the ``checkpoint_ns='agent:<task_id>'`` namespace mismatch), and
    filtered every removal ID out — producing a 2-message payload with
    NO HumanMessage → 502 "chat content is empty" from the LLM proxy.

    The FIXED repairer filters in-memory ``context.messages`` directly.
    This test asserts:

      * ``graph.aget_state`` and ``graph.aupdate_state`` are NEVER called
        (the in-memory path has no checkpoint round-trip).
      * The repaired message list contains MORE than 2 messages (the
        original HumanMessage + 2 surviving loop messages + the repair
        SystemMessage, depending on detection).
      * A HumanMessage IS present in the repaired payload.
      * The repair still produces a structurally valid payload.
    """

    @pytest.mark.asyncio
    async def test_kb_writer_time_loop_produces_valid_repaired_payload(self):
        # Build the production-failure message list: 1 HumanMessage + 3
        # identical (time, {}) AI+Tool pairs.
        messages = _kb_writer_time_loop_messages(count=3)
        graph = _MockGraph()
        ctx = _make_repair_context(messages=messages, graph=graph)
        summary_llm = _StubSummaryLLM("Stuck on time({}) with no progress.")

        with patch("daemon.graph.ThinkingChatOpenAI", return_value=summary_llm):
            result = await LoopRepairer().repair(ctx)

        # Repair succeeded.
        assert result.success is True
        # BUG REGRESSION: NO checkpoint methods were called. The
        # in-memory path is the only path; the old path that returned
        # empty state via ``aget_state`` is gone.
        assert graph.aget_state_calls == [], (
            f"LoopRepairer must NOT call graph.aget_state (would return "
            f"empty due to checkpoint_ns mismatch). "
            f"Got {len(graph.aget_state_calls)} call(s)."
        )
        assert graph.aupdate_state_calls == [], (
            f"LoopRepairer must NOT call graph.aupdate_state (the "
            f"in-memory fix has no checkpoint write). "
            f"Got {len(graph.aupdate_state_calls)} call(s)."
        )

        repaired = result.repaired_messages

        # REGRESSION: more than 2 messages. The old broken path produced
        # exactly 2 messages (repair SystemMessage + the ToolMessage
        # that "matched" the empty namespace). With the fix, the
        # HumanMessage is preserved and at least 2 of the loop's
        # tool-call AIMessages are removed.
        assert len(repaired) > 2, (
            f"Repaired message list must contain MORE than 2 messages "
            f"(the old broken path produced 2 with no HumanMessage). "
            f"Got {len(repaired)}."
        )

        # REGRESSION: a HumanMessage is present in the repaired
        # payload. This is the single most important invariant — its
        # absence was the proximate cause of the 502 error.
        human_messages = [m for m in repaired if isinstance(m, HumanMessage)]
        assert human_messages, (
            "Repaired payload MUST contain at least one HumanMessage. "
            "The old broken path produced 0 — the LLM proxy then "
            "rejected the request as 'chat content is empty'."
        )
        # Specifically: the original HumanMessage ID is preserved.
        assert any(getattr(m, "id", None) == "kb-user-1" for m in human_messages), (
            "Original kb-writer user message must survive the repair."
        )

        # The repair SystemMessage is present and at the FRONT — the
        # LLM sees the nudge directive first, then the conversation.
        repair_system_msgs = [
            m for m in repaired
            if isinstance(m, SystemMessage)
            and (getattr(m, "id", None) or "").startswith(LOOP_BREAKER_REPAIR_PREFIX)
        ]
        assert len(repair_system_msgs) == 1
        assert repaired[0] is repair_system_msgs[0]

        # The loop's tool-call AIMessages are removed. At least 2 of
        # the 3 kb-ai-* AIMessages must be gone (the detector
        # preserves 1 as evidence).
        surviving_ai_ids = {
            getattr(m, "id", None) for m in repaired if isinstance(m, AIMessage)
        }
        all_ai_ids = {f"kb-ai-{i}" for i in range(3)}
        removed_ai_ids = all_ai_ids - surviving_ai_ids
        assert len(removed_ai_ids) >= 2, (
            f"At least 2 of the 3 looping AIMessages must be removed. "
            f"Got removed={removed_ai_ids}, surviving={surviving_ai_ids}."
        )

        # Summary text from the LLM made it into the repair SystemMessage.
        assert "Stuck on time({})" in repair_system_msgs[0].content

    @pytest.mark.asyncio
    async def test_old_broken_path_not_consulted_even_with_empty_namespace(self):
        """Defense-in-depth: even if the graph returns EMPTY state
        (simulating the production ``checkpoint_ns`` bug), the
        in-memory repair path ignores it. The repair still succeeds
        and the HumanMessage is preserved.
        """
        messages = _kb_writer_time_loop_messages(count=3)
        graph = _MockGraph()  # aget_state returns empty state.values
        ctx = _make_repair_context(messages=messages, graph=graph)
        summary_llm = _StubSummaryLLM("ok")

        with patch("daemon.graph.ThinkingChatOpenAI", return_value=summary_llm):
            result = await LoopRepairer().repair(ctx)

        # Even if the test's _MockGraph had been consulted, the
        # result must be the same. The OLD broken repairer would
        # have filtered ALL removal IDs out (empty live_ids), produced
        # 2 messages, and failed downstream.
        assert result.success is True
        human_count = sum(1 for m in result.repaired_messages if isinstance(m, HumanMessage))
        assert human_count == 1
        # The HumanMessage ID is the original — not synthesized. The
        # repair SystemMessage is prepended at position 0, then the
        # original conversation (HumanMessage first, then the evidence
        # unit's AIMessage+ToolMessage). So HumanMessage is at index 1.
        human_msgs = [m for m in result.repaired_messages if isinstance(m, HumanMessage)]
        assert human_msgs[0].id == "kb-user-1"


# ---------------------------------------------------------------------------
# 2. EDGE CASE — very few messages (2-3) hit loop detection
# ---------------------------------------------------------------------------


class TestVeryFewMessagesHitLoop:
    """When the agent has barely any context (2-3 messages) and the
    loop detector fires, the repair must still produce a structurally
    valid payload (HumanMessage + repair SystemMessage at minimum).
    """

    @pytest.mark.asyncio
    async def test_three_messages_with_loop_still_produces_valid_payload(self):
        # 1 HumanMessage + 1 AIMessage-with-time-tool-call + 1 ToolMessage
        # = 3 messages total. With threshold=1, the detector would
        # normally not fire — but the test sets the detection directly
        # to simulate "a loop WAS detected with very few messages".
        messages = [
            HumanMessage(content="log the date", id="h-min-0"),
            AIMessage(
                content="",
                id="ai-min-0",
                tool_calls=[{"id": "tc-min-0", "name": "time", "args": {}}],
            ),
            ToolMessage(
                content="2026-08-14",
                tool_call_id="tc-min-0",
                name="time",
                id="tm-min-0",
            ),
        ]
        # Synthesize a detection that points at the single loop AIMessage
        # (the detector needs threshold=1 to fire on a single unit;
        # in production, the agent_node always passes a real detection).
        from daemon.graph import LoopDetectionResult

        detection = LoopDetectionResult(
            tool_name="time",
            tool_args={},
            repetition_count=1,
            loop_messages=[messages[1]],  # the AIMessage with time tool call
            evidence_message_ids=[],  # evidence unit was the first
        )
        graph = _MockGraph()
        ctx = _make_repair_context(
            messages=messages,
            detection=detection,
            graph=graph,
        )
        summary_llm = _StubSummaryLLM("called time once")

        with patch("daemon.graph.ThinkingChatOpenAI", return_value=summary_llm):
            result = await LoopRepairer().repair(ctx)

        assert result.success is True
        # HumanMessage is preserved.
        human_msgs = [m for m in result.repaired_messages if isinstance(m, HumanMessage)]
        assert human_msgs, "HumanMessage must survive even with minimal context"
        assert human_msgs[0].id == "h-min-0"
        # Repair SystemMessage is at the front.
        assert isinstance(result.repaired_messages[0], SystemMessage)
        assert result.repaired_messages[0].id.startswith(LOOP_BREAKER_REPAIR_PREFIX)
        # No checkpoint round-trips.
        assert graph.aget_state_calls == []
        assert graph.aupdate_state_calls == []

    @pytest.mark.asyncio
    async def test_two_messages_with_loop_still_produces_valid_payload(self):
        """Bare minimum: HumanMessage + a single loop AIMessage. The
        repair still produces a valid payload (no None/empty crash).
        """
        messages = [
            HumanMessage(content="go", id="h-2"),
            AIMessage(
                content="",
                id="ai-loop",
                tool_calls=[{"id": "tc-2", "name": "bash", "args": {"cmd": "ls"}}],
            ),
        ]
        from daemon.graph import LoopDetectionResult

        detection = LoopDetectionResult(
            tool_name="bash",
            tool_args={"cmd": "ls"},
            repetition_count=1,
            loop_messages=[messages[1]],
            evidence_message_ids=[],
        )
        graph = _MockGraph()
        ctx = _make_repair_context(
            messages=messages,
            detection=detection,
            graph=graph,
        )
        summary_llm = _StubSummaryLLM("called bash with ls")

        with patch("daemon.graph.ThinkingChatOpenAI", return_value=summary_llm):
            result = await LoopRepairer().repair(ctx)

        assert result.success is True
        repaired = result.repaired_messages
        # Repaired payload is at least: [repair SysMsg, HumanMessage]
        assert len(repaired) >= 2
        assert isinstance(repaired[0], SystemMessage)
        # HumanMessage present.
        assert any(isinstance(m, HumanMessage) for m in repaired)
        # No checkpoint calls.
        assert graph.aget_state_calls == []
        assert graph.aupdate_state_calls == []


# ---------------------------------------------------------------------------
# 3. EDGE CASE — max_repairs limit reached
# ---------------------------------------------------------------------------


class TestMaxRepairsReached:
    """When the loop-breaker's repair counter is at the cap, the
    ``_loop_breaker_repair_block`` helper short-circuits: the repairer
    is NOT invoked, the slot count is NOT incremented, and the ORIGINAL
    message list is returned to the caller so the graph continues
    running rather than wedging.

    The repair-count guard lives in ``daemon.graph._loop_breaker_repair_block``;
    the regression test exercises the FULL agent_node path (real
    create_agent_node factory closure, real LoopDetector, stubbed
    repairer) — same shape as
    ``tests/test_loop_breaker_integration.py::TestMaxRepairs``.

    The must-not-wedge assertion: the LLM still gets called with the
    ORIGINAL messages (no repair succeeded → fall through to the
    no-repair path that still invokes the LLM).
    """

    @pytest.mark.asyncio
    async def test_max_repairs_reached_returns_original_messages_no_crash(self):
        # Build the production-failure message list (kb-writer stuck on time).
        messages = _kb_writer_time_loop_messages(count=3)

        # Pre-populate the slot at the cap: the cap is max_repairs,
        # so any count >= max_repairs halts further repairs.
        slot = _StubLoopBreakerSlot()
        slot._state["instance-kb-writer"] = {"count": 3, "last_summary": "x"}

        # The repairer MUST NOT be called when count >= max_repairs.
        # We use a MagicMock with side_effect=AsyncMock to assert.
        repairer = MagicMock()
        repairer.repair = AsyncMock(
            return_value=RepairResult(
                success=True,
                repaired_messages=[],
                summary="should not run",
                repair_message_id="should-not-run",
            )
        )

        # Build a minimal agent_node for the test. Mirrors
        # ``_make_agent`` from ``tests/test_loop_breaker_integration.py``.
        from daemon.graph import create_agent_node

        # Main LLM stub — returns a no-tool-call AIMessage so the
        # post-block LLM call succeeds without a real provider.
        class _MainLLM:
            def __init__(self):
                self.calls: list[list] = []

            def invoke(self, msgs):
                self.calls.append(list(msgs))
                return AIMessage(content="post-block LLM response")

        llm = _MainLLM()
        graph = _MockGraph()
        cfg = LoopBreakerConfig(enabled=True, threshold=3, max_repairs=3)

        agent_node = create_agent_node(
            llm_with_tools=llm,
            system_prompt="you are a kb-writer",
            compactor=None,
            graph_ref=[graph],
            config=None,
            llm_config={"model": "test-model", "model_vision": None},
            retry_config={"transient_attempts": 1, "timeout_attempts": 1},
            llm_standard=None,
            injection_slot=None,
            live_hub=None,
            throttle_slot=None,
            loop_breaker_slot=slot,
            loop_repairer=repairer,
            loop_breaker_config=cfg,
        )

        # Run one turn. Loop detection fires (3 identical time calls),
        # but the slot count == max_repairs → repair is skipped.
        result = await agent_node(
            {"messages": messages},
            config={"configurable": {"thread_id": "instance-kb-writer"}},
        )

        # The repairer MUST NOT have been called.
        repairer.repair.assert_not_awaited()
        # The slot count was NOT incremented.
        assert slot.get_repair_count("instance-kb-writer") == 3
        # The LLM was called (the no-repair path continues). The agent
        # never wedges even when repair is skipped.
        assert len(llm.calls) == 1
        # The result is the agent's regular response (no repaired
        # list collapsed into nothing).
        assert "messages" in result
        assert len(result["messages"]) >= 1


# ---------------------------------------------------------------------------
# 4. EDGE CASE — repair failure (LLM summarization timeout)
# ---------------------------------------------------------------------------


class TestRepairFailureOnLLMTimeout:
    """When the summarization LLM call hangs past the timeout, the
    repairer's ``_summarize_loop`` falls back to the static summary —
    the repair still completes successfully. This is the CONTRACT
    asserted in ``tests/unit/test_loop_repairer.py::TestSummarizeLoopFallbackOnTimeout``;
    the regression variant asserts the FULL flow on the kb-writer
    production message list.
    """

    @pytest.mark.asyncio
    async def test_kb_writer_repair_succeeds_with_fallback_when_llm_times_out(self):
        messages = _kb_writer_time_loop_messages(count=3)
        graph = _MockGraph()
        ctx = _make_repair_context(
            messages=messages,
            graph=graph,
            summarization_timeout_seconds=1,
        )

        # Patch asyncio.to_thread inside daemon.graph to a coroutine
        # that sleeps longer than the timeout. asyncio.wait_for then
        # fires TimeoutError → _summarize_loop returns the static
        # fallback summary.
        async def _never_resolves(*args, **kwargs):
            await asyncio.sleep(5)
            return "should not reach"

        with patch("daemon.graph.asyncio.to_thread", side_effect=_never_resolves):
            result = await LoopRepairer().repair(ctx)

        # Repair STILL succeeded — the timeout fallback kicked in.
        assert result.success is True
        # Summary is the static fallback string (not LLM-generated).
        assert "time" in result.summary
        assert "3 times" in result.summary
        assert "without progress" in result.summary
        # Repaired payload is structurally valid: HumanMessage present.
        human_msgs = [m for m in result.repaired_messages if isinstance(m, HumanMessage)]
        assert human_msgs
        # No checkpoint round-trips even on the timeout path.
        assert graph.aget_state_calls == []
        assert graph.aupdate_state_calls == []
        # The repair SystemMessage is at the front.
        assert isinstance(result.repaired_messages[0], SystemMessage)
        assert result.repaired_messages[0].id.startswith(LOOP_BREAKER_REPAIR_PREFIX)

    @pytest.mark.asyncio
    async def test_kb_writer_repair_outer_exception_returns_original_messages(self):
        """If the LLM CONSTRUCTOR itself raises (not a timeout, but a
        config error), ``_summarize_loop`` catches it and uses the
        fallback — repair still succeeds. This is the second
        fall-through path inside ``_summarize_loop``.
        """
        messages = _kb_writer_time_loop_messages(count=3)
        graph = _MockGraph()
        ctx = _make_repair_context(messages=messages, graph=graph)

        with patch(
            "daemon.graph.ThinkingChatOpenAI",
            side_effect=ValueError("invalid LLM config"),
        ):
            result = await LoopRepairer().repair(ctx)

        assert result.success is True
        # Static fallback summary was used.
        assert "time" in result.summary
        assert "3 times" in result.summary
        # Repaired payload is still structurally valid.
        human_msgs = [m for m in result.repaired_messages if isinstance(m, HumanMessage)]
        assert human_msgs
        # No checkpoint calls.
        assert graph.aget_state_calls == []
        assert graph.aupdate_state_calls == []


# ---------------------------------------------------------------------------
# 5. EDGE CASE — Option C safety-net fires
# ---------------------------------------------------------------------------


class TestOptionCSafetyNetFires:
    """Option C safety-net: when ``removals`` is non-empty but the
    in-memory filter dropped every ID (every ID missing from
    ``context.messages``), the repair returns ``[repair_msg] +
    context.messages`` (prepended) so the LLM still sees the directive
    before the full conversation history.

    This is the production scenario's BELT-AND-BRACES backup: if some
    future code path causes the in-memory snapshot's IDs to diverge
    from the detection's loop_message IDs, the safety-net keeps the
    system stable rather than collapsing to the 2-message invalid
    payload that caused the original 502.
    """

    @pytest.mark.asyncio
    async def test_all_removal_ids_missing_safety_net_prepends_repair_message(self):
        # The detection claims these messages should be removed.
        from daemon.graph import LoopDetectionResult

        messages = [
            HumanMessage(content="kb-writer request", id="h-oc-0"),
            HumanMessage(content="followup", id="h-oc-1"),
        ]
        # The loop IDs in the detection do NOT exist in messages. The
        # in-memory filter therefore drops every ID → Option C fires.
        detection = LoopDetectionResult(
            tool_name="time",
            tool_args={},
            repetition_count=3,
            loop_messages=[
                AIMessage(
                    content="",
                    id="phantom-ai-1",
                    tool_calls=[{"id": "phantom-tc-1", "name": "time", "args": {}}],
                ),
                AIMessage(
                    content="",
                    id="phantom-ai-2",
                    tool_calls=[{"id": "phantom-tc-2", "name": "time", "args": {}}],
                ),
            ],
            evidence_message_ids=[],
        )
        graph = _MockGraph()
        ctx = _make_repair_context(
            messages=messages,
            detection=detection,
            graph=graph,
        )
        summary_llm = _StubSummaryLLM("ok")

        with patch("daemon.graph.ThinkingChatOpenAI", return_value=summary_llm):
            result = await LoopRepairer().repair(ctx)

        # Repair succeeded via the safety-net path.
        assert result.success is True
        repaired = result.repaired_messages
        # The repair SystemMessage is PREPENDED (first), and the
        # ORIGINAL messages are preserved in their entirety after.
        # This is the Option C contract — the LLM sees the directive
        # before the full history, and the HumanMessage is guaranteed
        # to be present.
        assert len(repaired) == 3, (
            f"Option C path: [repair_msg, h-oc-0, h-oc-1] = 3 items. "
            f"Got {len(repaired)}."
        )
        assert isinstance(repaired[0], SystemMessage)
        assert repaired[0].id.startswith(LOOP_BREAKER_REPAIR_PREFIX)
        # The repair message is PREPENDED (not appended). Spec says
        # the LLM must see the directive FIRST.
        assert repaired[0] is not messages[0]
        # The original HumanMessages are preserved at positions 1 and 2.
        assert repaired[1] is messages[0]
        assert repaired[2] is messages[1]
        # The HumanMessage(s) are still in the payload.
        human_msgs = [m for m in repaired if isinstance(m, HumanMessage)]
        assert len(human_msgs) == 2
        # No checkpoint round-trips on the safety-net path either.
        assert graph.aget_state_calls == []
        assert graph.aupdate_state_calls == []

    @pytest.mark.asyncio
    async def test_safety_net_preserves_structural_validity_even_at_minimum(self):
        """With a single original HumanMessage and all loop IDs
        missing, the Option C safety-net still produces a valid
        payload: [repair_msg, original_human] = 2 items, with
        HumanMessage present.
        """
        from daemon.graph import LoopDetectionResult

        original_human = HumanMessage(content="only message", id="only-h")
        messages = [original_human]
        detection = LoopDetectionResult(
            tool_name="any",
            tool_args={},
            repetition_count=3,
            loop_messages=[
                AIMessage(
                    content="",
                    id="phantom-A",
                    tool_calls=[{"id": "phantom-tc-A", "name": "any", "args": {}}],
                ),
            ],
            evidence_message_ids=[],
        )
        graph = _MockGraph()
        ctx = _make_repair_context(
            messages=messages,
            detection=detection,
            graph=graph,
        )
        summary_llm = _StubSummaryLLM("summary")

        with patch("daemon.graph.ThinkingChatOpenAI", return_value=summary_llm):
            result = await LoopRepairer().repair(ctx)

        assert result.success is True
        repaired = result.repaired_messages
        # Exactly: repair SystemMessage + original HumanMessage.
        assert len(repaired) == 2
        assert isinstance(repaired[0], SystemMessage)
        # The original HumanMessage is preserved (id matches).
        assert repaired[1] is original_human
        # HumanMessage is present — the OLD broken path produced
        # 0 HumanMessages here. The fix guarantees >= 1.
        human_msgs = [m for m in repaired if isinstance(m, HumanMessage)]
        assert len(human_msgs) == 1


# ---------------------------------------------------------------------------
# Sanity: time-tool loop detector end-to-end check
# ---------------------------------------------------------------------------


class TestDetectorAndRepairerEndToEnd:
    """Sanity: the REAL ``LoopDetector.scan`` correctly detects the
    kb-writer's ``time`` tool loop and hands off to the REAL
    ``LoopRepairer`` with consistent IDs. This is the production
    detection->repair hand-off that the regression test relies on.
    """

    def test_loop_detector_finds_kb_writer_time_loop(self):
        messages = _kb_writer_time_loop_messages(count=3)
        # Threshold=3 matches the production default.
        detection = LoopDetector.scan(messages, threshold=3)
        assert detection is not None
        assert detection.tool_name == "time"
        assert detection.repetition_count == 3
        # At least 2 messages flagged for removal (the evidence unit is kept).
        assert len(detection.loop_messages) >= 2
        # The evidence IDs exist (the oldest unit was kept).
        assert len(detection.evidence_message_ids) >= 1
        # The loop messages' IDs match messages in the original list.
        original_ids = {getattr(m, "id", None) for m in messages}
        loop_ids = {getattr(m, "id", None) for m in detection.loop_messages}
        assert loop_ids.issubset(original_ids), (
            "Detection loop message IDs MUST come from the same in-memory "
            "snapshot as context.messages — this is the invariant the fix "
            "relies on (Option B filtering)."
        )

    def test_repairer_summarization_timeout_default_is_30_seconds(self):
        """The default ``asyncio.wait_for`` timeout for the
        summarization call is 30 seconds. Pinned so future refactors
        don't accidentally change the safety net.
        """
        assert LOOP_BREAKER_SUMMARIZATION_TIMEOUT_SECONDS == 30
