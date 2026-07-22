"""Integration tests for the general hallucination loop breaker (Phase 3).

Covers the ``agent_node`` wiring of the Phase 1 ``LoopDetector`` /
``LoopBreakerSlot`` and the Phase 2 ``LoopRepairer``. The tests follow
the ``tests/test_injection_graph.py`` style: build a real
``create_agent_node`` factory closure with stubbed ``llm_with_tools`` /
``loop_breaker_slot`` / ``loop_repairer`` / ``graph_ref``, then drive the
agent_node directly with crafted state + config. No LangGraph runtime,
no daemon manager, no DB.

Scenarios covered:

    1.  Full flow: 3+ identical tool calls -> detection -> repair -> state
        updated -> LLM re-invoked with repaired messages.
    2.  GII coexistence: GII throttle bumps AND loop breaker repairs.
    3.  Config disable: ``LoopBreakerConfig(enabled=False)`` -> no
        detection, no repair.
    4.  Max repairs: 4 detection events with ``max_repairs=3`` -> 4th
        detection is skipped.
    5.  Cleanup: ``InstanceManager._cleanup_instance_state`` pops
        ``_loop_breaker_state`` (mirrors the gii-throttle regression).
    6.  Parallel tool calls: AIMessage with multiple identical
        tool_calls -> detected as a single loop unit.
    7.  Excluded tools: tool in excluded list breaks the chain.
    8.  Fallback on LLM error: repair still completes via static
        fallback summary.
    9.  Injected message re-append: ``injected_msg`` survives repair.
    10. Fresh UUID: repair message has a unique ID.
    11. Summarization timeout: hung LLM call -> ``asyncio.wait_for``
        fires -> static fallback -> repair still completes.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from daemon import manager as manager_module
from daemon.graph import (
    LOOP_BREAKER_REPAIR_PREFIX,
    LOOP_BREAKER_SUMMARIZATION_TIMEOUT_SECONDS,
    LoopDetectionResult,
    LoopRepairer,
    RepairResult,
)


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _StubLoopBreakerSlot:
    """In-memory mock of ``LoopBreakerSlot``.

    Mirrors the real handle's contract:
        record_repair(iid, s) -> int
        clear(iid)            -> None
        get_repair_count(iid) -> int
    """

    def __init__(self, initial: dict[str, dict] | None = None):
        self._state: dict[str, dict] = dict(initial or {})
        self.record_calls: list[tuple[str, str]] = []
        self.clear_calls: list[str] = []
        self.get_repair_count_calls: list[str] = []

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
        self.get_repair_count_calls.append(instance_id)
        return self._state.get(instance_id, {}).get("count", 0)


class _StubLLM:
    """Returns a configured response on ``invoke``."""

    def __init__(self, response: Any = None):
        self.response = response if response is not None else AIMessage(content="ok")
        self.calls: list[list[Any]] = []

    def invoke(self, messages: list[Any]) -> Any:
        self.calls.append(list(messages))
        return self.response


class _StubGraph:
    """Stand-in for ``graph_ref[0]`` — used by ``LoopRepairer.repair``.

    Records ``aupdate_state`` / ``aget_state`` calls and exposes a
    mutable ``state_messages`` list that ``aget_state`` returns.
    """

    def __init__(self, state_messages: list[Any] | None = None):
        self._state_messages = list(state_messages or [])
        self.aupdate_state_calls: list[tuple[Any, str | None]] = []
        self.aget_state_calls: list[Any] = []

    async def aupdate_state(self, config, values, as_node=None) -> None:
        self.aupdate_state_calls.append((values, as_node))
        if "messages" in values:
            self._state_messages = list(values["messages"])

    async def aget_state(self, config) -> Any:
        self.aget_state_calls.append(config)
        state = MagicMock()
        state.values = {"messages": list(self._state_messages)}
        return state


def _make_agent(
    *,
    loop_breaker_slot: Any | None = None,
    loop_repairer: Any | None = None,
    loop_breaker_config: Any | None = None,
    throttle_slot: Any | None = None,
    injection_slot: Any | None = None,
    llm: Any | None = None,
    # graph_ref=[None] disables repair path; pass graph_ref=[_StubGraph()] to test repair
    graph_ref: Any | None = None,
):
    """Build a fresh agent_node for a test, bypassing ``build_instance_graph``."""
    from daemon.graph import create_agent_node

    if llm is None:
        llm = _StubLLM()
    if graph_ref is None:
        graph_ref = [None]

    agent_node = create_agent_node(
        llm_with_tools=llm,
        system_prompt="you are a test assistant",
        compactor=None,
        graph_ref=graph_ref,
        config=None,
        llm_config={"model": "test-model", "model_vision": None},
        retry_config={"transient_attempts": 1, "timeout_attempts": 1},
        llm_standard=None,
        injection_slot=injection_slot,
        live_hub=None,
        throttle_slot=throttle_slot,
        loop_breaker_slot=loop_breaker_slot,
        loop_repairer=loop_repairer,
        loop_breaker_config=loop_breaker_config,
    )
    return agent_node, llm


def _ai_with_tool_call(
    tool_call_id: str,
    name: str,
    args: dict,
    *,
    msg_id: str | None = None,
) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"id": tool_call_id, "name": name, "args": args}],
        id=msg_id,
    )


def _tool_result(
    tool_call_id: str,
    name: str,
    content: str = "result",
    *,
    msg_id: str | None = None,
) -> ToolMessage:
    return ToolMessage(content=content, tool_call_id=tool_call_id, name=name, id=msg_id)


def _sequential_loop_messages(
    tool_name: str,
    args: dict,
    count: int,
    *,
    prefix: str = "",
) -> list:
    """Build ``count`` consecutive AI+Tool pairs.

    Older-first ordering matches ``MessagesState``.
    """
    messages: list = []
    for i in range(count):
        tc_id = f"{prefix}tc-{i}"
        ai = _ai_with_tool_call(tc_id, tool_name, args, msg_id=f"{prefix}ai-{i}")
        tm = _tool_result(tc_id, tool_name, content=f"result-{i}", msg_id=f"{prefix}tm-{i}")
        messages.append(ai)
        messages.append(tm)
    return messages


# ---------------------------------------------------------------------------
# 1. Full flow
# ---------------------------------------------------------------------------


class TestFullFlow:
    """Detection -> repair -> state updated -> LLM re-invoked."""

    @pytest.mark.asyncio
    async def test_detection_triggers_repair_and_llm_uses_repaired_messages(self):
        from daemon.graph import LoopBreakerConfig

        slot = _StubLoopBreakerSlot()
        llm = _StubLLM(response=AIMessage(content="post-repair"))

        # Build a tiny "repaired" message list the stubbed graph returns
        # after aupdate_state. The agent_node must use this list
        # (post-SystemMessage) for the next LLM call.
        repaired_summary = SystemMessage(content="[LOOP BREAKER] repaired", id="rep-1")
        graph = _StubGraph(state_messages=[HumanMessage(content="orig", id="orig-1"), repaired_summary])
        # repairer.repair is a coroutine — patch its internal LLM call so
        # no real network is needed, then return a successful RepairResult.
        async def fake_repair(ctx):
            return RepairResult(
                success=True,
                repaired_messages=ctx.messages,  # type: ignore[attr-defined]
                summary="summary",
                repair_message_id="repair-fake",
            )
        repairer = MagicMock()
        repairer.repair = AsyncMock(side_effect=fake_repair)

        cfg = LoopBreakerConfig(enabled=True, threshold=3, max_repairs=3)
        agent_node, _ = _make_agent(
            loop_breaker_slot=slot,
            loop_repairer=repairer,
            loop_breaker_config=cfg,
            graph_ref=[graph],
            llm=llm,
        )

        messages = _sequential_loop_messages("bash", {"cmd": "ls"}, count=3)
        result = await agent_node(
            {"messages": messages},
            config={"configurable": {"thread_id": "iid-1"}},
        )

        # repairer was called once.
        repairer.repair.assert_awaited_once()
        # slot recorded the repair.
        assert slot.record_calls == [("iid-1", "summary")]
        assert slot.get_repair_count("iid-1") == 1
        # graph.aupdate_state was invoked by the repairer (which the
        # graph stub records via aupdate_state_calls).
        # The graph's recorded aupdate_state call comes from inside
        # LoopRepairer.repair — which our fake skipped. So instead
        # verify the LLM was called twice (once before repair, once
        # after) — wait, in our fake_repair we returned ctx.messages
        # directly, so the agent_node will use those messages and call
        # the LLM ONCE (no need to call the LLM before repair if the
        # detector fires first). Verify the LLM saw the original
        # messages (post-SystemMessage prepended).
        assert len(llm.calls) == 1
        sent = llm.calls[0]
        assert isinstance(sent[0], SystemMessage)
        # The rest should be the original messages (we returned ctx.messages)
        assert len(sent) == 1 + len(messages)

    @pytest.mark.asyncio
    async def test_repaired_messages_actually_reach_llm(self):
        """The LLM must receive the REPAIRED messages, not the original.

        Closes the tautology gap in
        ``test_detection_triggers_repair_and_llm_uses_repaired_messages``
        above — that test returns ``ctx.messages`` from the fake repairer,
        so it cannot distinguish between "LLM was called with the original
        messages" and "LLM was called with the repaired messages". Here
        the repairer returns a clearly-DISTINCT list (a single
        ``HumanMessage`` with a sentinel ``id``) and we assert both that
        the sentinel IS present and that the original loop messages are
        NOT present.
        """
        from daemon.graph import LoopBreakerConfig

        slot = _StubLoopBreakerSlot()
        llm = _StubLLM(response=AIMessage(content="post-repair"))

        # Sentinel list returned by the repairer — DISTINCT from the
        # original loop messages (``id="pr-1"`` is the smoking gun).
        repaired_tail = [HumanMessage(content="post-repair-fresh-context", id="pr-1")]

        async def fake_repair(ctx):
            return RepairResult(
                success=True,
                repaired_messages=repaired_tail,
                summary="loop repaired",
                repair_message_id="repair-test-123",
            )
        repairer = MagicMock()
        repairer.repair = AsyncMock(side_effect=fake_repair)

        cfg = LoopBreakerConfig(enabled=True, threshold=3, max_repairs=3)
        agent_node, _ = _make_agent(
            loop_breaker_slot=slot,
            loop_repairer=repairer,
            loop_breaker_config=cfg,
            graph_ref=[_StubGraph()],
            llm=llm,
        )

        original_messages = _sequential_loop_messages("bash", {"cmd": "ls"}, count=3)
        await agent_node(
            {"messages": original_messages},
            config={"configurable": {"thread_id": "iid-1"}},
        )

        # Repair fired exactly once and was recorded.
        repairer.repair.assert_awaited_once()
        assert slot.record_calls == [("iid-1", "loop repaired")]
        assert slot.get_repair_count("iid-1") == 1

        # LLM was called once (post-repair path; no pre-repair LLM call).
        assert len(llm.calls) == 1
        sent = llm.calls[0]

        # SystemMessage prepended by ``_maybe_repair_loop``'s rebuild.
        assert isinstance(sent[0], SystemMessage)

        # The repaired HumanMessage (sentinel id="pr-1") MUST be in what
        # the LLM received — proves the post-repair rebuild took effect.
        assert any(
            getattr(m, 'id', None) == "pr-1" for m in sent
        ), "LLM did not receive the repaired messages — full_messages was not rebuilt"

        # None of the original loop messages (ids "ai-0", "ai-1", "ai-2",
        # "tm-0", "tm-1", "tm-2") should appear — they were replaced.
        for original_id in ("ai-0", "ai-1", "ai-2", "tm-0", "tm-1", "tm-2"):
            assert not any(
                getattr(m, 'id', None) == original_id for m in sent
            ), f"Original loop message {original_id} leaked into the LLM context — repair was not used"


# ---------------------------------------------------------------------------
# 2. GII coexistence
# ---------------------------------------------------------------------------


class _StubToolThrottleSlot:
    """Minimal stub mirroring ``ToolThrottleSlot``.

    Records calls; bumps a per-instance counter.
    """

    def __init__(self):
        self._counts: dict[str, int] = {}
        self.bump_calls: list[str] = []
        self.reset_calls: list[str] = []

    def bump(self, instance_id: str) -> int:
        self.bump_calls.append(instance_id)
        self._counts[instance_id] = self._counts.get(instance_id, 0) + 1
        return self._counts[instance_id]

    def reset(self, instance_id: str) -> None:
        self.reset_calls.append(instance_id)
        self._counts.pop(instance_id, None)

    def get_count(self, instance_id: str) -> int:
        return self._counts.get(instance_id, 0)


class TestGIICoexistence:
    """GII throttle fires AND loop breaker fires."""

    @pytest.mark.asyncio
    async def test_gii_throttle_runs_before_loop_breaker(self, monkeypatch):
        from daemon.graph import LoopBreakerConfig, GII_TOOL_NAME

        slot = _StubLoopBreakerSlot()
        repairer = MagicMock()
        repairer.repair = AsyncMock(return_value=RepairResult(
            success=True,
            repaired_messages=[],
            summary="s",
            repair_message_id="rep-id",
        ))

        throttle = _StubToolThrottleSlot()
        cfg = LoopBreakerConfig(enabled=True, threshold=3, max_repairs=3)
        agent_node, llm = _make_agent(
            loop_breaker_slot=slot,
            loop_repairer=repairer,
            loop_breaker_config=cfg,
            throttle_slot=throttle,
            # ``graph_ref`` must point at a real-ish object because
            # ``LoopRepairer.repair`` calls ``context.graph.aupdate_state``
            # / ``aget_state`` after the summarization step. Without a
            # stub graph the new graph_ref-empty guard in ``agent_node``
            # (Fix 2) skips the repair at WARNING, masking the coexistence
            # behavior this test is meant to exercise.
            graph_ref=[_StubGraph()],
        )

        # Build 3 messages, each being a ``get_instance_info`` ToolMessage
        # so the GII throttle bumps and (since count>=3) calls
        # ``asyncio.sleep``. The loop detector should ALSO fire on this
        # tail (each ToolMessage matches the GII_TOOL_NAME — but the
        # detector looks for AIMessage+tool_calls pairs, so the loop
        # detector will NOT actually fire on bare ToolMessages).
        # We need a proper AIMessage+ToolMessage sequence for the loop
        # detector to detect. Use 3 identical bash calls as well.
        gii_messages = _sequential_loop_messages(GII_TOOL_NAME, {"q": "1"}, count=3)
        # Append the GII tool result (the last ToolMessage acts as
        # messages[-1] for the throttle check).
        messages = gii_messages

        sleeps: list[float] = []

        async def fake_sleep(delay):
            sleeps.append(delay)

        monkeypatch.setattr("daemon.graph.asyncio.sleep", fake_sleep)

        await agent_node(
            {"messages": messages},
            config={"configurable": {"thread_id": "iid-1"}},
        )

        # GII throttle bumped exactly once for the tail gii ToolMessage
        # (one bump per ``agent_node`` invocation, regardless of how many
        # trailing gii messages are in history — the counter is stateful
        # across invocations so consecutive calls accumulate to trigger
        # the sleep on call #3). With only one ``agent_node`` invocation
        # here we expect a single bump, NOT three — the test was originally
        # written with a different mental model where every consecutive
        # gii message in history bumps the counter independently, but the
        # production implementation deliberately bumps once per
        # invocation to keep the progressive-delay curve (180s, 300s,
        # 600s, 900s) instead of jumping straight to the max delay.
        assert throttle.bump_calls == ["iid-1"]
        # Loop detector also saw the gii repetition and triggered repair.
        repairer.repair.assert_awaited_once()
        assert slot.record_calls == [("iid-1", "s")]

    @pytest.mark.asyncio
    async def test_gii_sleep_and_loop_repair_both_fire(self, monkeypatch):
        """Pre-seeded GII count -> sleep AND loop repair fire in one call.

        Extends ``test_gii_throttle_runs_before_loop_breaker`` above by
        pre-seeding the throttle counter to 2 so the next bump lands at 3
        (the ``GII_DELAY_MAP[3]=180`` threshold). The agent_node must
        then execute BOTH:
          * the throttle's ``asyncio.sleep(180)`` (proves the GII
            throttling path took the sleep branch — not just the bump),
          * the loop repairer's ``repair()`` (proves the loop breaker
            path still fired AFTER the throttle, in the same invocation).

        The order in ``agent_node`` is throttle -> repair -> LLM, so
        both run sequentially within a single ``await agent_node(...)``.
        """
        from daemon.graph import GII_DELAY_MAP, GII_TOOL_NAME, LoopBreakerConfig

        slot = _StubLoopBreakerSlot()
        throttle = _StubToolThrottleSlot()
        # Pre-seed: this bump lands at 3 -> sleep 180s per GII_DELAY_MAP.
        throttle._counts["iid-1"] = 2

        repairer = MagicMock()
        repaired_tail = [HumanMessage(content="repaired", id="pr-1")]

        async def fake_repair(ctx):
            return RepairResult(
                success=True,
                repaired_messages=repaired_tail,
                summary="fixed",
                repair_message_id="r-1",
            )
        repairer.repair = AsyncMock(side_effect=fake_repair)

        cfg = LoopBreakerConfig(enabled=True, threshold=3, max_repairs=3)
        agent_node, llm = _make_agent(
            loop_breaker_slot=slot,
            loop_repairer=repairer,
            loop_breaker_config=cfg,
            throttle_slot=throttle,
            graph_ref=[_StubGraph()],
        )

        # 3 identical ``get_instance_info`` calls: identical tool name +
        # args so the loop detector groups them as a single repeating
        # pattern at threshold=3.
        gii_messages = _sequential_loop_messages(GII_TOOL_NAME, {"q": "1"}, count=3)

        sleeps: list[float] = []

        async def fake_sleep(delay):
            sleeps.append(delay)

        monkeypatch.setattr("daemon.graph.asyncio.sleep", fake_sleep)

        await agent_node(
            {"messages": gii_messages},
            config={"configurable": {"thread_id": "iid-1"}},
        )

        # ── Throttle side ────────────────────────────────────────────────
        # Bump was called once for the trailing gii ToolMessage; landed
        # at count=3 -> the GII_DELAY_MAP[3] sleep branch fired.
        assert throttle.bump_calls == ["iid-1"]
        assert throttle._counts["iid-1"] == 3
        assert sleeps == [GII_DELAY_MAP[3]]
        assert sleeps == [180]
        # No reset on a gii ToolMessage.
        assert throttle.reset_calls == []

        # ── Repair side ──────────────────────────────────────────────────
        # The loop detector saw 3 identical gii calls and triggered
        # repair AFTER the throttle sleep — both happened in one call.
        repairer.repair.assert_awaited_once()
        assert slot.record_calls == [("iid-1", "fixed")]
        assert slot.get_repair_count("iid-1") == 1

        # ── LLM side ─────────────────────────────────────────────────────
        # LLM was invoked once with the repaired tail (the repaired
        # message id="pr-1" must be present).
        assert len(llm.calls) == 1
        sent = llm.calls[0]
        assert any(
            getattr(m, 'id', None) == "pr-1" for m in sent
        ), "Repaired messages did not reach LLM alongside the throttle sleep"


# ---------------------------------------------------------------------------
# 2b. Defensive branches (repair failure + None slot)
# ---------------------------------------------------------------------------


class TestRepairFailure:
    """``RepairResult(success=False, ...)`` must degrade gracefully.

    When the repairer raises or returns ``success=False``,
    ``_maybe_repair_loop`` logs the error and returns the ORIGINAL
    ``messages``/``full_messages`` pair so the agent continues with the
    existing context. This guards against:
      * repair_exception being swallowed without continuing (would freeze),
      * ``slot.record_repair`` being called on failure (would corrupt the
        repair cap counter for the next detection event),
      * the LLM seeing a phantom repaired list (the ``repaired_messages``
        field on a failed result must be IGNORED).
    """

    @pytest.mark.asyncio
    async def test_repair_failure_continues_with_original_messages(self):
        from daemon.graph import LoopBreakerConfig

        slot = _StubLoopBreakerSlot()
        llm = _StubLLM(response=AIMessage(content="continued-after-failure"))

        async def failing_repair(ctx):
            return RepairResult(
                success=False,
                repaired_messages=[],
                summary="",
                repair_message_id="",
                error="LLM unavailable",
            )
        repairer = MagicMock()
        repairer.repair = AsyncMock(side_effect=failing_repair)

        cfg = LoopBreakerConfig(enabled=True, threshold=3, max_repairs=3)
        agent_node, _ = _make_agent(
            loop_breaker_slot=slot,
            loop_repairer=repairer,
            loop_breaker_config=cfg,
            graph_ref=[_StubGraph()],
            llm=llm,
        )

        messages = _sequential_loop_messages("bash", {"cmd": "ls"}, count=3)
        await agent_node(
            {"messages": messages},
            config={"configurable": {"thread_id": "iid-1"}},
        )

        # The repairer WAS invoked once (detection fired and routed to
        # the repair path).
        repairer.repair.assert_awaited_once()

        # LLM was called once — with the ORIGINAL messages, since the
        # repair failed and the no-repair path returned ``messages``
        # unchanged. Original AI messages MUST be present; the (empty)
        # ``repaired_messages`` MUST NOT.
        assert len(llm.calls) == 1
        sent = llm.calls[0]
        assert isinstance(sent[0], SystemMessage)
        original_ids = {"ai-0", "ai-1", "ai-2", "tm-0", "tm-1", "tm-2"}
        observed_ids = {getattr(m, 'id', None) for m in sent}
        assert original_ids.issubset(observed_ids), (
            f"Original loop messages missing from LLM context on failure "
            f"(observed: {sorted(i for i in observed_ids if i)})"
        )
        # No phantom repair message leaked.
        assert slot.get_repair_count("iid-1") == 0
        assert slot.record_calls == []


class TestNoneLoopBreakerSlot:
    """``loop_breaker_slot=None`` disables the loop-breaker block entirely.

    The :func:`_maybe_repair_loop` guard at its very first line
    (``loop_breaker_slot is not None and loop_repairer is not None and
    loop_breaker_config.enabled``) short-circuits when the slot is
    ``None`` and returns the original messages. The agent_node then
    proceeds straight to the LLM call. This test pins that
    backward-compatible no-op so a future refactor that drops the
    guard fails loudly.
    """

    @pytest.mark.asyncio
    async def test_none_loop_breaker_slot_is_safe(self):
        """``loop_breaker_slot=None`` must not raise; LLM still invoked."""
        from daemon.graph import LoopBreakerConfig

        llm = _StubLLM(response=AIMessage(content="ok"))

        cfg = LoopBreakerConfig(enabled=True, threshold=3, max_repairs=3)
        agent_node, _ = _make_agent(
            loop_breaker_slot=None,
            loop_repairer=None,
            loop_breaker_config=cfg,
            llm=llm,
        )

        # Even with 5 identical bash calls (would otherwise trip the
        # detector), ``loop_breaker_slot=None`` short-circuits the
        # entire block — must not raise.
        messages = _sequential_loop_messages("bash", {"cmd": "ls"}, count=5)
        await agent_node(
            {"messages": messages},
            config={"configurable": {"thread_id": "iid-1"}},
        )

        # LLM still invoked once with the original messages (SystemMessage
        # prepended, then the 10-tool-call history). The whole point is
        # that ``agent_node`` behaves exactly like the pre-loop-breaker
        # version when the slot is ``None``.
        assert len(llm.calls) == 1
        sent = llm.calls[0]
        assert isinstance(sent[0], SystemMessage)
        assert len(sent) == 1 + len(messages)


# ---------------------------------------------------------------------------
# 3. Config disable
# ---------------------------------------------------------------------------


class TestConfigDisable:
    """``LoopBreakerConfig(enabled=False)`` disables detection."""

    @pytest.mark.asyncio
    async def test_disabled_config_skips_detection_and_repair(self):
        from daemon.graph import LoopBreakerConfig

        slot = _StubLoopBreakerSlot()
        repairer = MagicMock()
        repairer.repair = AsyncMock()

        cfg = LoopBreakerConfig(enabled=False, threshold=3)
        agent_node, llm = _make_agent(
            loop_breaker_slot=slot,
            loop_repairer=repairer,
            loop_breaker_config=cfg,
        )

        messages = _sequential_loop_messages("bash", {"cmd": "ls"}, count=5)
        await agent_node(
            {"messages": messages},
            config={"configurable": {"thread_id": "iid-1"}},
        )

        repairer.repair.assert_not_awaited()
        assert slot.record_calls == []
        # LLM was called once (the regular path) with original messages.
        assert len(llm.calls) == 1
        # Repair count remains 0 (no state mutation).
        assert slot.get_repair_count("iid-1") == 0


# ---------------------------------------------------------------------------
# 4. Max repairs cap
# ---------------------------------------------------------------------------


class TestMaxRepairs:
    """Repair cap stops infinite loop-repair cycles."""

    @pytest.mark.asyncio
    async def test_max_repairs_skips_repair_but_continues_with_original_messages(self):
        from daemon.graph import LoopBreakerConfig

        # Pre-populate slot with 3 prior repairs (= max_repairs).
        slot = _StubLoopBreakerSlot(
            initial={"iid-1": {"count": 3, "last_summary": "x"}}
        )
        repairer = MagicMock()
        repairer.repair = AsyncMock()

        cfg = LoopBreakerConfig(enabled=True, threshold=3, max_repairs=3)
        agent_node, llm = _make_agent(
            loop_breaker_slot=slot,
            loop_repairer=repairer,
            loop_breaker_config=cfg,
            # ``graph_ref`` must point at a real-ish object because
            # ``LoopRepairer.repair`` calls ``context.graph.aupdate_state``
            # / ``aget_state`` after the summarization step. Without a
            # stub graph the new graph_ref-empty guard in ``agent_node``
            # (Fix 2) skips the repair at WARNING, masking the single-repair
            # behavior this test is meant to exercise.
            graph_ref=[_StubGraph()],
        )

        messages = _sequential_loop_messages("bash", {"cmd": "ls"}, count=3)
        await agent_node(
            {"messages": messages},
            config={"configurable": {"thread_id": "iid-1"}},
        )

        # Repairer MUST NOT be called because count >= max_repairs.
        repairer.repair.assert_not_awaited()
        # slot.count MUST NOT increment (no repair happened).
        assert slot.get_repair_count("iid-1") == 3
        # LLM was still called once with original messages.
        assert len(llm.calls) == 1


# ---------------------------------------------------------------------------
# 5. Cleanup
# ---------------------------------------------------------------------------


def _make_manager_with_loop_breaker_surface():
    """Stand-in exposing the loop-breaker surface + cleanup helper.

    Binds real ``InstanceManager.record_loop_repair``,
    ``reset_loop_breaker``, ``get_loop_repair_count``, AND
    ``_cleanup_instance_state`` onto the stub. Mirrors
    ``_make_manager_with_cleanup_surface`` in ``tests/test_gii_throttle.py``
    for the loop-breaker parallel.
    """
    class _LoopCleanupStub:
        # Loop-breaker surface
        record_loop_repair: Any
        reset_loop_breaker: Any
        get_loop_repair_count: Any
        # Cleanup helper
        _cleanup_instance_state: Any

        def __init__(self):
            self._loop_breaker_state: dict[str, dict] = {}
            self._gii_throttle: dict[str, int] = {}
            self._graph_tasks: dict = {}
            self._pending_injections: dict = {}
            self._context_usage_cleared: list[str] = []
            self.record_loop_repair = (
                manager_module.InstanceManager.record_loop_repair.__get__(self)
            )
            self.reset_loop_breaker = (
                manager_module.InstanceManager.reset_loop_breaker.__get__(self)
            )
            self.get_loop_repair_count = (
                manager_module.InstanceManager.get_loop_repair_count.__get__(self)
            )
            self._cleanup_instance_state = (
                manager_module.InstanceManager._cleanup_instance_state.__get__(self)
            )

        def release_context_usage_cache(self, instance_id: str) -> None:
            self._context_usage_cleared.append(instance_id)

        def _question_manager(self):
            return MagicMock()

    # The _cleanup_instance_state helper on the real InstanceManager
    # calls ``self._question_manager.clear_question_pack`` and
    # ``self.clear_question_pause_requested`` — these are real attrs
    # on the manager. The stub doesn't carry them, so we patch them
    # on the class via additional bound attributes set in __init__.
    stub = _LoopCleanupStub()
    stub._question_manager = MagicMock()
    stub._question_manager.clear_question_pack = MagicMock()
    stub.clear_question_pause_requested = MagicMock()
    stub._deferred_question_pause = set()
    return stub


class TestCleanupPaths:
    """``_loop_breaker_state`` is popped on cleanup."""

    def test_cleanup_instance_state_clears_loop_breaker(self):
        mgr = _make_manager_with_loop_breaker_surface()
        mgr.record_loop_repair("iid-1", "first")
        mgr.record_loop_repair("iid-1", "second")
        assert mgr.get_loop_repair_count("iid-1") == 2

        mgr._cleanup_instance_state("iid-1")

        # Loop-breaker state MUST be cleared.
        assert mgr.get_loop_repair_count("iid-1") == 0
        assert "iid-1" not in mgr._loop_breaker_state

    def test_cleanup_safe_when_no_loop_breaker_entry(self):
        """Cleanup on an instance that never recorded a repair must not raise."""
        mgr = _make_manager_with_loop_breaker_surface()
        result = mgr._cleanup_instance_state("iid-never-recorded")
        assert mgr.get_loop_repair_count("iid-never-recorded") == 0
        assert result is not None
        assert result["context_usage_cleared"] is True


# ---------------------------------------------------------------------------
# 6. Parallel tool calls
# ---------------------------------------------------------------------------


class TestParallelToolCalls:
    """AIMessage with multiple identical tool_calls -> detected as ONE unit."""

    @pytest.mark.asyncio
    async def test_parallel_identical_tool_calls_trigger_single_repair(self):
        from daemon.graph import LoopBreakerConfig

        slot = _StubLoopBreakerSlot()
        repairer = MagicMock()

        async def fake_repair(ctx):
            return RepairResult(
                success=True,
                repaired_messages=ctx.messages,
                summary="summary",
                repair_message_id="rep-x",
            )
        repairer.repair = AsyncMock(side_effect=fake_repair)

        cfg = LoopBreakerConfig(enabled=True, threshold=3, max_repairs=3)
        agent_node, llm = _make_agent(
            loop_breaker_slot=slot,
            loop_repairer=repairer,
            loop_breaker_config=cfg,
            graph_ref=[_StubGraph()],
        )

        # Build 3 AIMessages, each with TWO identical tool_calls in
        # parallel (multi-tool parallel pattern). The detector groups
        # parallel calls into a single signature, so these 3 units
        # (each with 2 parallel calls) count as 3 identical units.
        messages: list = []
        for i in range(3):
            ai = AIMessage(
                content="",
                tool_calls=[
                    {"id": f"tc-{i}-a", "name": "bash", "args": {"cmd": "ls"}},
                    {"id": f"tc-{i}-b", "name": "bash", "args": {"cmd": "ls"}},
                ],
                id=f"ai-{i}",
            )
            # Match each tool_call with a ToolMessage.
            tm_a = _tool_result(f"tc-{i}-a", "bash", content="r-a", msg_id=f"tm-{i}-a")
            tm_b = _tool_result(f"tc-{i}-b", "bash", content="r-b", msg_id=f"tm-{i}-b")
            messages.extend([ai, tm_a, tm_b])

        await agent_node(
            {"messages": messages},
            config={"configurable": {"thread_id": "iid-1"}},
        )

        # Repairer was invoked exactly ONCE (one parallel tool call
        # group counts as a single unit, three of them = threshold hit).
        repairer.repair.assert_awaited_once()
        assert slot.get_repair_count("iid-1") == 1


# ---------------------------------------------------------------------------
# 7. Excluded tools
# ---------------------------------------------------------------------------


class TestExcludedTools:
    """Excluded tools break the consecutive chain."""

    @pytest.mark.asyncio
    async def test_excluded_tool_breaks_chain_no_repair(self):
        from daemon.graph import LoopBreakerConfig

        slot = _StubLoopBreakerSlot()
        repairer = MagicMock()
        repairer.repair = AsyncMock()

        cfg = LoopBreakerConfig(
            enabled=True,
            threshold=3,
            max_repairs=3,
            excluded_tools=["ping_status"],
        )
        agent_node, llm = _make_agent(
            loop_breaker_slot=slot,
            loop_repairer=repairer,
            loop_breaker_config=cfg,
        )

        # 3 identical ping_status calls. The detector should skip them
        # because the tool name is in excluded_tools.
        messages = _sequential_loop_messages("ping_status", {"host": "x"}, count=5)
        await agent_node(
            {"messages": messages},
            config={"configurable": {"thread_id": "iid-1"}},
        )

        repairer.repair.assert_not_awaited()
        assert slot.record_calls == []


# ---------------------------------------------------------------------------
# 8. Fallback on LLM error
# ---------------------------------------------------------------------------


class TestFallbackOnLLMError:
    """LLM raise -> static fallback used -> repair still completes."""

    @pytest.mark.asyncio
    async def test_llm_raise_uses_fallback_summary(self, monkeypatch):
        from daemon.graph import LoopBreakerConfig

        slot = _StubLoopBreakerSlot()
        # Real LoopRepairer with a hanging LLM that the timeout fallback
        # catches. We patch ``daemon.graph.ThinkingChatOpenAI`` to a
        # MagicMock whose .invoke raises.
        import daemon.graph as dg

        class _BoomLLM:
            def invoke(self, _messages):
                raise RuntimeError("provider down")

        with patch.object(dg, "ThinkingChatOpenAI", return_value=_BoomLLM()):
            cfg = LoopBreakerConfig(enabled=True, threshold=3, max_repairs=3)
            # ``graph_ref`` must point at a real-ish object because
            # ``LoopRepairer.repair`` calls ``context.graph.aupdate_state``
            # / ``aget_state`` after the summarization step. Without a
            # stub graph the repair aborts with ``AttributeError`` and
            # ``slot.record_calls`` stays empty — masking the fallback
            # behavior the test is meant to exercise.
            graph_ref = [_StubGraph()]
            agent_node, llm = _make_agent(
                loop_breaker_slot=slot,
                loop_repairer=LoopRepairer(),
                loop_breaker_config=cfg,
                graph_ref=graph_ref,
            )

            messages = _sequential_loop_messages("bash", {"cmd": "ls"}, count=3)
            result = await agent_node(
                {"messages": messages},
                config={"configurable": {"thread_id": "iid-1"}},
            )

        # The repair still succeeded with the static fallback.
        # slot recorded the repair (summary contains the fallback text).
        assert slot.record_calls
        iid, summary = slot.record_calls[0]
        assert iid == "iid-1"
        assert "bash" in summary
        assert "without progress" in summary


# ---------------------------------------------------------------------------
# 9. Injected message re-append
# ---------------------------------------------------------------------------


class _StubInjectionSlot:
    """Tiny injection-slot stub mirroring ``InjectionSlot`` contract.

    Phase 3: ``get`` returns a list of pending entries (or None);
    ``clear`` returns the full list (or None).
    """

    def __init__(self, content: str | None = None):
        self._content = content
        self.get_calls: list[str] = []
        self.clear_calls: list[str] = []

    def get(self, instance_id: str):
        self.get_calls.append(instance_id)
        if self._content is None:
            return None
        return [{"content": self._content, "timestamp": "ts"}]

    def clear(self, instance_id: str):
        self.clear_calls.append(instance_id)
        prev = self._content
        self._content = None
        if prev is None:
            return None
        return [{"content": prev, "timestamp": "ts"}]


class TestInjectedMessageReAppend:
    """``injected_msg`` is re-appended after repair (C3 pattern)."""

    @pytest.mark.asyncio
    async def test_injected_message_re_appended_after_repair(self):
        from daemon.graph import LoopBreakerConfig

        slot = _StubLoopBreakerSlot()
        repairer = MagicMock()

        async def fake_repair(ctx):
            # Drop the injected_msg we received (simulate a state re-read
            # that lost the in-memory message). Then return messages
            # WITHOUT the injection — the agent_node must re-append it
            # to ``messages``/``full_messages`` after the repair.
            return RepairResult(
                success=True,
                repaired_messages=[HumanMessage(content="post-repair", id="pr-1")],
                summary="s",
                repair_message_id="rep-1",
            )
        repairer.repair = AsyncMock(side_effect=fake_repair)

        inj = _StubInjectionSlot(content="USER-INJECT")
        cfg = LoopBreakerConfig(enabled=True, threshold=3, max_repairs=3)
        agent_node, llm = _make_agent(
            loop_breaker_slot=slot,
            loop_repairer=repairer,
            loop_breaker_config=cfg,
            injection_slot=inj,  # type: ignore[arg-type]
        )

        messages = _sequential_loop_messages("bash", {"cmd": "ls"}, count=3)
        result = await agent_node(
            {"messages": messages},
            config={"configurable": {"thread_id": "iid-1"}},
        )

        # LLM was called once (post-repair, with repaired+injected msgs).
        assert len(llm.calls) == 1
        sent = llm.calls[0]
        # First is SystemMessage, last should be the injected HumanMessage.
        assert isinstance(sent[0], SystemMessage)
        assert isinstance(sent[-1], HumanMessage)
        assert sent[-1].content == "USER-INJECT"

    @pytest.mark.asyncio
    async def test_multiple_injected_messages_re_appended_after_repair(self):
        """Phase 3: multiple pending injections must ALL survive loop-breaker
        repair and be re-appended to the LLM retry context. This exercises the
        ``msg.id is None`` short-circuit at graph.py ~line 1185 — without it,
        messages 2+ with None IDs are silently dropped by the dedup check."""
        from daemon.graph import LoopBreakerConfig

        markers = ["USER-INJECT-1", "USER-INJECT-2", "USER-INJECT-3"]

        class _MultiEntrySlot:
            """Returns a 3-entry list to mirror a multi-message inbox."""
            def __init__(self):
                self._entries = [{"content": m, "timestamp": "ts"} for m in markers]
                self.get_calls: list[str] = []
                self.clear_calls: list[str] = []

            def get(self, instance_id: str):
                self.get_calls.append(instance_id)
                return list(self._entries) if self._entries else None

            def clear(self, instance_id: str):
                self.clear_calls.append(instance_id)
                prev = list(self._entries) if self._entries else None
                self._entries = None
                return prev

        slot = _StubLoopBreakerSlot()
        repairer = MagicMock()

        async def fake_repair(ctx):
            return RepairResult(
                success=True,
                repaired_messages=[HumanMessage(content="post-repair", id="pr-1")],
                summary="s",
                repair_message_id="rep-1",
            )
        repairer.repair = AsyncMock(side_effect=fake_repair)

        inj = _MultiEntrySlot()
        cfg = LoopBreakerConfig(enabled=True, threshold=3, max_repairs=3)
        agent_node, llm = _make_agent(
            loop_breaker_slot=slot,
            loop_repairer=repairer,
            loop_breaker_config=cfg,
            injection_slot=inj,  # type: ignore[arg-type]
        )

        messages = _sequential_loop_messages("bash", {"cmd": "ls"}, count=3)
        result = await agent_node(
            {"messages": messages},
            config={"configurable": {"thread_id": "iid-1"}},
        )

        # LLM was called once (post-repair, with repaired+injected msgs).
        assert len(llm.calls) == 1
        sent = llm.calls[0]
        # First is SystemMessage
        assert isinstance(sent[0], SystemMessage)
        # Last 3 must be the injected HumanMessages in FIFO order.
        tail = sent[-3:]
        assert all(isinstance(m, HumanMessage) for m in tail)
        assert [m.content for m in tail] == markers


# ---------------------------------------------------------------------------
# 10. Fresh UUID
# ---------------------------------------------------------------------------


class TestFreshUUID:
    """Each repair message has a unique UUID-prefixed ID."""

    def test_repair_message_id_is_unique_and_prefixed(self):
        result1 = LoopRepairer._build_repair_message(
            LoopDetectionResult(tool_name="bash", tool_args={"x": 1}, repetition_count=3),
            "summary1",
        )
        result2 = LoopRepairer._build_repair_message(
            LoopDetectionResult(tool_name="bash", tool_args={"x": 2}, repetition_count=3),
            "summary2",
        )
        assert result1.id is not None
        assert result2.id is not None
        assert result1.id.startswith(LOOP_BREAKER_REPAIR_PREFIX)
        assert result2.id.startswith(LOOP_BREAKER_REPAIR_PREFIX)
        assert result1.id != result2.id


# ---------------------------------------------------------------------------
# 11. Summarization timeout
# ---------------------------------------------------------------------------


class TestSummarizationTimeout:
    """Hung LLM -> ``asyncio.wait_for`` fires -> static fallback."""

    @pytest.mark.asyncio
    async def test_summarization_timeout_uses_fallback(self):
        from daemon.graph import LoopBreakerConfig

        slot = _StubLoopBreakerSlot()

        # Patch asyncio.to_thread inside daemon.graph so the LLM call
        # hangs forever. asyncio.wait_for will then fire and the
        # fallback summary is returned.
        async def _never_resolves(*args, **kwargs):
            await asyncio.sleep(5)
            return "should not reach"

        import daemon.graph as dg
        # The timeout is read from cfg.summarization_timeout_seconds.
        cfg = LoopBreakerConfig(
            enabled=True,
            threshold=3,
            max_repairs=3,
            summarization_timeout_seconds=1,
        )
        with patch.object(dg.asyncio, "to_thread", side_effect=_never_resolves):
            # ``graph_ref`` must point at a real-ish object because
            # ``LoopRepairer.repair`` calls ``context.graph.aupdate_state``
            # / ``aget_state`` after the summarization step. Without a
            # stub graph the repair aborts with ``AttributeError`` and
            # ``slot.record_calls`` stays empty — masking the fallback
            # behavior the test is meant to exercise.
            graph_ref = [_StubGraph()]
            agent_node, llm = _make_agent(
                loop_breaker_slot=slot,
                loop_repairer=LoopRepairer(),
                loop_breaker_config=cfg,
                graph_ref=graph_ref,
            )

            messages = _sequential_loop_messages("bash", {"cmd": "ls"}, count=3)
            await agent_node(
                {"messages": messages},
                config={"configurable": {"thread_id": "iid-1"}},
            )

        # Repair still completed (with fallback summary).
        assert slot.record_calls
        iid, summary = slot.record_calls[0]
        assert iid == "iid-1"
        assert "bash" in summary
        assert "without progress" in summary