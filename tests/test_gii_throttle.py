"""Unit tests for the ``get_instance_info`` consecutive-call throttle.

Covers:
    * ``InstanceManager.bump_gii_throttle`` / ``reset_gii_throttle`` /
      ``get_gii_throttle_count`` (TestManagerThrottle)
    * ``daemon.graph.ToolThrottleSlot`` mock-friendly handle
      (TestToolThrottleSlot)
    * Module-level constants ``GII_TOOL_NAME``, ``GII_DELAY_MAP``,
      ``GII_MAX_DELAY`` (TestDelayMap)
    * Delay injection in ``agent_node`` — escalating backoff + reset on
      non-gii messages + safe ``None`` throttle_slot (TestAgentNodeThrottleIntegration)

These tests construct minimal ``InstanceManager`` / ``ToolThrottleSlot``
stand-ins so the throttle mechanics can be exercised without spinning up
the full daemon. The real ``InstanceManager`` is covered end-to-end in
``tests/manager/``.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


def _make_manager_with_throttle_dict():
    """Build a minimal stand-in for ``InstanceManager`` exposing only the throttle surface.

    Mirrors ``_make_manager_with_pending_dict()`` from
    ``tests/test_injection_slot.py`` — we bind the real
    ``InstanceManager`` methods (``bump_gii_throttle``,
    ``reset_gii_throttle``, ``get_gii_throttle_count``) onto the stand-in
    so the slot semantics are exercised against the real implementation
    rather than a copy.
    """
    from daemon import manager as manager_module

    class _ManagerStub:
        """Minimal stand-in for InstanceManager — only exposes throttle surface."""

        bump_gii_throttle: Any
        reset_gii_throttle: Any
        get_gii_throttle_count: Any

        def __init__(self):
            self._gii_throttle: dict[str, int] = {}
            self.bump_gii_throttle = manager_module.InstanceManager.bump_gii_throttle.__get__(self)
            self.reset_gii_throttle = manager_module.InstanceManager.reset_gii_throttle.__get__(self)
            self.get_gii_throttle_count = manager_module.InstanceManager.get_gii_throttle_count.__get__(self)

    return _ManagerStub()


def _make_manager_with_cleanup_surface():
    """Build a stand-in for ``InstanceManager`` that can run ``_cleanup_instance_state``.

    Extends the throttle-only stub with the three additional resources
    that ``_cleanup_instance_state`` touches:
        * ``_graph_tasks`` (dict instance_id → task)
        * ``_pending_injections`` (dict instance_id → pending payload)
        * ``release_context_usage_cache(instance_id)`` (no-op stub)

    Used by the cleanup-regression test (Fix 1).
    """
    from daemon import manager as manager_module

    class _CleanupStub:
        """Stand-in exercising throttle + cleanup surface."""

        bump_gii_throttle: Any
        reset_gii_throttle: Any
        get_gii_throttle_count: Any
        _cleanup_instance_state: Any

        def __init__(self):
            self._gii_throttle: dict[str, int] = {}
            self._graph_tasks: dict[str, Any] = {}
            self._pending_injections: dict[str, Any] = {}
            self._context_usage_cleared: list[str] = []
            self.bump_gii_throttle = manager_module.InstanceManager.bump_gii_throttle.__get__(self)
            self.reset_gii_throttle = manager_module.InstanceManager.reset_gii_throttle.__get__(self)
            self.get_gii_throttle_count = manager_module.InstanceManager.get_gii_throttle_count.__get__(self)
            self._cleanup_instance_state = manager_module.InstanceManager._cleanup_instance_state.__get__(self)

        def release_context_usage_cache(self, instance_id: str) -> None:
            # Record the call so tests can assert it ran, but otherwise no-op
            self._context_usage_cleared.append(instance_id)

    return _CleanupStub()


class _StubToolThrottleSlot:
    """In-memory mock of ``ToolThrottleSlot`` recording every call.

    Mirrors the real handle's contract:
        bump(instance_id)   -> int
        reset(instance_id)  -> None
        get_count(instance_id) -> int
    """

    def __init__(self, counts: dict[str, int] | None = None):
        self._counts: dict[str, int] = dict(counts or {})
        self.bump_calls: list[str] = []
        self.reset_calls: list[str] = []
        self.get_count_calls: list[str] = []

    def bump(self, instance_id: str) -> int:
        self.bump_calls.append(instance_id)
        self._counts[instance_id] = self._counts.get(instance_id, 0) + 1
        return self._counts[instance_id]

    def reset(self, instance_id: str) -> None:
        self.reset_calls.append(instance_id)
        self._counts.pop(instance_id, None)

    def get_count(self, instance_id: str) -> int:
        self.get_count_calls.append(instance_id)
        return self._counts.get(instance_id, 0)


class _StubLLM:
    """Returns a configured response on ``invoke``.

    Captures the messages it was called with so tests can verify the
    full_messages passed through.
    """

    def __init__(self, response: Any = None):
        self.response = response if response is not None else AIMessage(content="ok")
        self.calls: list[list[Any]] = []

    def invoke(self, messages: list[Any]) -> Any:
        self.calls.append(list(messages))
        return self.response


def _make_agent(
    throttle_slot: Any | None = None,
    llm: Any | None = None,
):
    """Build a fresh agent_node for a test, bypassing ``build_instance_graph``."""
    from daemon.graph import create_agent_node

    if llm is None:
        llm = _StubLLM()

    agent_node = create_agent_node(
        llm_with_tools=llm,
        system_prompt="you are a test assistant",
        compactor=None,
        graph_ref=[None],
        config=None,
        llm_config={"model": "test-model", "model_vision": None},
        retry_config={"transient_attempts": 1, "timeout_attempts": 1},
        llm_standard=None,
        injection_slot=None,
        live_hub=None,
        throttle_slot=throttle_slot,
    )
    return agent_node, llm


def _gii_tool_message(content: str = "result") -> ToolMessage:
    """Build a ToolMessage whose name matches ``GII_TOOL_NAME``."""
    return ToolMessage(content=content, tool_call_id="t1", name="get_instance_info")


def _non_gii_tool_message(content: str = "bash-result", tool_call_id: str = "t2") -> ToolMessage:
    """Build a ToolMessage whose name is NOT ``GII_TOOL_NAME``.

    Used by reset-path tests to simulate the interleaved-ToolMessage case
    where ``messages[-1]`` is some other tool's result.
    """
    return ToolMessage(content=content, tool_call_id=tool_call_id, name="bash")


# ---------------------------------------------------------------------------
# Test 1 — InstanceManager methods
# ---------------------------------------------------------------------------


class TestManagerThrottle:
    """``bump_gii_throttle`` / ``reset_gii_throttle`` / ``get_gii_throttle_count``."""

    def test_bump_starts_at_one(self):
        mgr = _make_manager_with_throttle_dict()
        assert mgr.bump_gii_throttle("iid-1") == 1

    def test_bump_increments(self):
        mgr = _make_manager_with_throttle_dict()
        assert mgr.bump_gii_throttle("iid-1") == 1
        assert mgr.bump_gii_throttle("iid-1") == 2
        assert mgr.bump_gii_throttle("iid-1") == 3

    def test_reset_zeroes_count(self):
        mgr = _make_manager_with_throttle_dict()
        mgr.bump_gii_throttle("iid-1")
        mgr.bump_gii_throttle("iid-1")
        mgr.reset_gii_throttle("iid-1")
        assert mgr.get_gii_throttle_count("iid-1") == 0

    def test_reset_when_empty_is_safe(self):
        mgr = _make_manager_with_throttle_dict()
        # Must not raise when called on an instance_id that has no entry
        mgr.reset_gii_throttle("iid-never-bumped")
        assert mgr.get_gii_throttle_count("iid-never-bumped") == 0

    def test_get_count_unset_returns_zero(self):
        mgr = _make_manager_with_throttle_dict()
        assert mgr.get_gii_throttle_count("iid-1") == 0

    def test_independent_instances(self):
        mgr = _make_manager_with_throttle_dict()
        mgr.bump_gii_throttle("iid-A")
        mgr.bump_gii_throttle("iid-A")
        mgr.bump_gii_throttle("iid-B")
        assert mgr.get_gii_throttle_count("iid-A") == 2
        assert mgr.get_gii_throttle_count("iid-B") == 1
        # Resetting A must not affect B
        mgr.reset_gii_throttle("iid-A")
        assert mgr.get_gii_throttle_count("iid-A") == 0
        assert mgr.get_gii_throttle_count("iid-B") == 1

    def test_cleanup_instance_state_clears_gii_throttle(self):
        """Regression for Fix 1: ``_cleanup_instance_state`` must pop the gii throttle entry.

        Without this, the ``_gii_throttle`` dict leaks one entry per
        terminated instance and grows unbounded for long-lived daemons.
        Bumping the counter and then calling ``_cleanup_instance_state``
        must leave the counter at 0.
        """
        mgr = _make_manager_with_cleanup_surface()
        # Bump a few times so the counter is non-zero
        mgr.bump_gii_throttle("iid-1")
        mgr.bump_gii_throttle("iid-1")
        mgr.bump_gii_throttle("iid-1")
        assert mgr.get_gii_throttle_count("iid-1") == 3

        # Cleanup the same instance
        result = mgr._cleanup_instance_state("iid-1")

        # The gii throttle entry MUST be gone
        assert mgr.get_gii_throttle_count("iid-1") == 0
        # And ``_gii_throttle`` no longer holds the key
        assert "iid-1" not in mgr._gii_throttle
        # ``release_context_usage_cache`` was still called so other cleanup
        # invariants are preserved
        assert "iid-1" in mgr._context_usage_cleared
        # Returned shape matches the documented contract
        assert result == {
            "graph_task": None,
            "cleared_injection": None,
            "context_usage_cleared": True,
        }

    def test_cleanup_instance_state_safe_when_no_throttle_entry(self):
        """Calling cleanup on an instance that never bumped throttle must not raise."""
        mgr = _make_manager_with_cleanup_surface()
        # Never bumped — must not KeyError on the missing key
        result = mgr._cleanup_instance_state("iid-never-bumped")
        assert mgr.get_gii_throttle_count("iid-never-bumped") == 0
        assert result is not None
        assert result["context_usage_cleared"] is True


# ---------------------------------------------------------------------------
# Test 2 — ToolThrottleSlot
# ---------------------------------------------------------------------------


class TestToolThrottleSlot:
    """``ToolThrottleSlot`` delegates to the manager and is None-safe."""

    def test_bump_delegates_to_manager(self):
        manager = MagicMock()
        manager.bump_gii_throttle = MagicMock(return_value=5)
        from daemon.graph import ToolThrottleSlot

        slot = ToolThrottleSlot(manager)
        result = slot.bump("iid-1")

        manager.bump_gii_throttle.assert_called_once_with("iid-1")
        assert result == 5

    def test_reset_delegates_to_manager(self):
        manager = MagicMock()
        manager.reset_gii_throttle = MagicMock()
        from daemon.graph import ToolThrottleSlot

        slot = ToolThrottleSlot(manager)
        slot.reset("iid-1")

        manager.reset_gii_throttle.assert_called_once_with("iid-1")

    def test_get_count_delegates_to_manager(self):
        manager = MagicMock()
        manager.get_gii_throttle_count = MagicMock(return_value=7)
        from daemon.graph import ToolThrottleSlot

        slot = ToolThrottleSlot(manager)
        result = slot.get_count("iid-1")

        manager.get_gii_throttle_count.assert_called_once_with("iid-1")
        assert result == 7

    def test_none_methods_return_defaults(self):
        """Wrapping an object missing the throttle methods must be safe.

        Mirrors ``InjectionSlot``'s contract: ``getattr(manager,
        'method_name', None)`` falls back to defaults rather than
        raising AttributeError. This keeps the agent_node
        unit-testable without a real ``InstanceManager``.
        """
        from daemon.graph import ToolThrottleSlot

        class _Bare:
            pass

        bare = _Bare()
        slot = ToolThrottleSlot(bare)

        # All three methods must degrade to safe defaults
        assert slot.bump("iid-1") == 0
        # reset is a no-op (no return value to assert)
        slot.reset("iid-1")
        assert slot.get_count("iid-1") == 0


# ---------------------------------------------------------------------------
# Test 3 — Module-level constants
# ---------------------------------------------------------------------------


class TestDelayMap:
    """``daemon.graph`` exposes the documented backoff table."""

    def test_delay_map_table(self):
        from daemon.graph import GII_DELAY_MAP

        assert GII_DELAY_MAP == {3: 180, 4: 300, 5: 600}

    def test_max_delay_is_900(self):
        from daemon.graph import GII_MAX_DELAY

        assert GII_MAX_DELAY == 900

    def test_tool_name_is_get_instance_info(self):
        from daemon.graph import GII_TOOL_NAME

        assert GII_TOOL_NAME == "get_instance_info"


# ---------------------------------------------------------------------------
# Test 4 — agent_node integration
# ---------------------------------------------------------------------------


class TestAgentNodeThrottleIntegration:
    """The agent_node must bump/reset/sleep as documented.

    Strategy: monkeypatch ``asyncio.sleep`` (where it's looked up
    inside ``daemon.graph``) to a coroutine that records the delay
    value. This lets us assert both the call sequence and the delay
    magnitudes without actually waiting.
    """

    @pytest.mark.asyncio
    async def test_first_gii_does_not_sleep(self, monkeypatch):
        """First gii ToolMessage -> bump to 1, NO sleep (count < 3)."""
        slot = _StubToolThrottleSlot()
        agent_node, llm = _make_agent(throttle_slot=slot)

        sleeps: list[float] = []

        async def fake_sleep(delay):
            sleeps.append(delay)

        monkeypatch.setattr("daemon.graph.asyncio.sleep", fake_sleep)

        await agent_node(
            {"messages": [_gii_tool_message()]},
            config={"configurable": {"thread_id": "iid-1"}},
        )

        assert slot.bump_calls == ["iid-1"]
        # count=1, below the 3-call threshold -> no sleep
        assert sleeps == []
        # reset must NOT be called for a gii ToolMessage
        assert slot.reset_calls == []

    @pytest.mark.asyncio
    async def test_third_gii_sleeps_180s(self, monkeypatch):
        """Three consecutive gii ToolMessages -> bump to 3, sleep 180s.

        Build three sequential agent_node invocations, each adding one
        more gii ToolMessage on top of the previous state. The bump
        counter progresses 1 -> 2 -> 3, and only the third call
        triggers the 180s sleep per ``GII_DELAY_MAP``.
        """
        from daemon.graph import GII_DELAY_MAP

        slot = _StubToolThrottleSlot()
        agent_node, llm = _make_agent(throttle_slot=slot)

        sleeps: list[float] = []

        async def fake_sleep(delay):
            sleeps.append(delay)

        monkeypatch.setattr("daemon.graph.asyncio.sleep", fake_sleep)

        # Three turns, each adding a fresh gii ToolMessage.
        # The agent_node reads ``messages[-1]`` to detect the gii trigger,
        # so we grow the message list on each invocation.
        msgs: list = []
        for turn, _ in enumerate(range(3), start=1):
            msgs.append(_gii_tool_message(content=f"turn-{turn}"))
            await agent_node(
                {"messages": list(msgs)},
                config={"configurable": {"thread_id": "iid-1"}},
            )

        # Three bumps, no resets
        assert slot.bump_calls == ["iid-1", "iid-1", "iid-1"]
        assert slot.reset_calls == []

        # Bump values progress 1 -> 2 -> 3; only the third (count=3)
        # triggers a sleep, with delay from GII_DELAY_MAP[3].
        assert sleeps == [GII_DELAY_MAP[3]]
        assert sleeps[0] == 180

    @pytest.mark.asyncio
    async def test_fourth_gii_sleeps_300s(self, monkeypatch):
        """Four consecutive gii ToolMessages -> bumps 1..4, sleeps [180, 300]."""
        from daemon.graph import GII_DELAY_MAP

        slot = _StubToolThrottleSlot()
        agent_node, llm = _make_agent(throttle_slot=slot)

        sleeps: list[float] = []

        async def fake_sleep(delay):
            sleeps.append(delay)

        monkeypatch.setattr("daemon.graph.asyncio.sleep", fake_sleep)

        # Four turns, each adding a fresh gii ToolMessage.
        msgs: list = []
        for turn, _ in enumerate(range(4), start=1):
            msgs.append(_gii_tool_message(content=f"turn-{turn}"))
            await agent_node(
                {"messages": list(msgs)},
                config={"configurable": {"thread_id": "iid-1"}},
            )

        assert slot.bump_calls == ["iid-1"] * 4
        assert slot.reset_calls == []
        # Sleeps fire on count >= 3, so we get two sleeps: counts 3 and 4.
        assert sleeps == [GII_DELAY_MAP[3], GII_DELAY_MAP[4]]
        assert sleeps == [180, 300]

    @pytest.mark.asyncio
    async def test_fifth_gii_sleeps_600s(self, monkeypatch):
        """Five consecutive gii ToolMessages -> bumps 1..5, sleeps [180, 300, 600]."""
        from daemon.graph import GII_DELAY_MAP

        slot = _StubToolThrottleSlot()
        agent_node, llm = _make_agent(throttle_slot=slot)

        sleeps: list[float] = []

        async def fake_sleep(delay):
            sleeps.append(delay)

        monkeypatch.setattr("daemon.graph.asyncio.sleep", fake_sleep)

        # Five turns, each adding a fresh gii ToolMessage.
        msgs: list = []
        for turn, _ in enumerate(range(5), start=1):
            msgs.append(_gii_tool_message(content=f"turn-{turn}"))
            await agent_node(
                {"messages": list(msgs)},
                config={"configurable": {"thread_id": "iid-1"}},
            )

        assert slot.bump_calls == ["iid-1"] * 5
        assert slot.reset_calls == []
        # Counts 3, 4, 5 each trigger a sleep per GII_DELAY_MAP.
        assert sleeps == [GII_DELAY_MAP[3], GII_DELAY_MAP[4], GII_DELAY_MAP[5]]
        assert sleeps == [180, 300, 600]

    @pytest.mark.asyncio
    async def test_sixth_gii_sleeps_900s_cap(self, monkeypatch):
        """Six consecutive gii ToolMessages -> bumps 1..6, sleeps [180,300,600,900].

        Count=6 falls off the end of GII_DELAY_MAP so the throttle falls
        back to GII_MAX_DELAY=900 — first time the cap kicks in.
        """
        from daemon.graph import GII_DELAY_MAP, GII_MAX_DELAY

        slot = _StubToolThrottleSlot()
        agent_node, llm = _make_agent(throttle_slot=slot)

        sleeps: list[float] = []

        async def fake_sleep(delay):
            sleeps.append(delay)

        monkeypatch.setattr("daemon.graph.asyncio.sleep", fake_sleep)

        # Six turns, each adding a fresh gii ToolMessage.
        msgs: list = []
        for turn, _ in enumerate(range(6), start=1):
            msgs.append(_gii_tool_message(content=f"turn-{turn}"))
            await agent_node(
                {"messages": list(msgs)},
                config={"configurable": {"thread_id": "iid-1"}},
            )

        assert slot.bump_calls == ["iid-1"] * 6
        assert slot.reset_calls == []
        # Counts 3,4,5 from the table, count 6 falls through to GII_MAX_DELAY.
        assert sleeps == [
            GII_DELAY_MAP[3],
            GII_DELAY_MAP[4],
            GII_DELAY_MAP[5],
            GII_MAX_DELAY,
        ]
        assert sleeps == [180, 300, 600, 900]

    @pytest.mark.asyncio
    async def test_seventh_and_beyond_still_capped(self, monkeypatch):
        """Seventh consecutive gii -> cap stays at 900 (no further escalation)."""
        from daemon.graph import GII_DELAY_MAP, GII_MAX_DELAY

        slot = _StubToolThrottleSlot()
        agent_node, llm = _make_agent(throttle_slot=slot)

        sleeps: list[float] = []

        async def fake_sleep(delay):
            sleeps.append(delay)

        monkeypatch.setattr("daemon.graph.asyncio.sleep", fake_sleep)

        # Seven turns — well past the delay-table ceiling.
        msgs: list = []
        for turn, _ in enumerate(range(7), start=1):
            msgs.append(_gii_tool_message(content=f"turn-{turn}"))
            await agent_node(
                {"messages": list(msgs)},
                config={"configurable": {"thread_id": "iid-1"}},
            )

        assert slot.bump_calls == ["iid-1"] * 7
        assert slot.reset_calls == []
        # Counts 3, 4, 5 from the table, counts 6 and 7 both fall through
        # to GII_MAX_DELAY. The cap must NOT keep climbing past 900.
        assert sleeps == [
            GII_DELAY_MAP[3],
            GII_DELAY_MAP[4],
            GII_DELAY_MAP[5],
            GII_MAX_DELAY,
            GII_MAX_DELAY,
        ]
        assert sleeps == [180, 300, 600, 900, 900]  

    @pytest.mark.asyncio
    async def test_non_gii_message_resets_counter(self, monkeypatch):
        """After a gii ToolMessage, an AIMessage (or any non-gii) must reset."""
        slot = _StubToolThrottleSlot()
        agent_node, llm = _make_agent(throttle_slot=slot)

        sleeps: list[float] = []

        async def fake_sleep(delay):
            sleeps.append(delay)

        monkeypatch.setattr("daemon.graph.asyncio.sleep", fake_sleep)

        # Turn 1: gii ToolMessage -> bump
        await agent_node(
            {"messages": [_gii_tool_message()]},
            config={"configurable": {"thread_id": "iid-1"}},
        )
        # Turn 2: AIMessage (non-gii) -> reset
        await agent_node(
            {"messages": [_gii_tool_message(), AIMessage(content="done")]},
            config={"configurable": {"thread_id": "iid-1"}},
        )

        assert slot.bump_calls == ["iid-1"]
        assert slot.reset_calls == ["iid-1"]
        # No sleep — count was 1 at the bump, and the second turn is a reset
        assert sleeps == []

    @pytest.mark.asyncio
    async def test_no_throttle_slot_is_safe(self, monkeypatch):
        """``throttle_slot=None`` must work and never call sleep or raise."""
        agent_node, llm = _make_agent(throttle_slot=None)

        sleeps: list[float] = []

        async def fake_sleep(delay):
            sleeps.append(delay)

        monkeypatch.setattr("daemon.graph.asyncio.sleep", fake_sleep)

        # Multiple gii messages — must not raise and must not sleep
        result = await agent_node(
            {"messages": [_gii_tool_message()]},
            config={"configurable": {"thread_id": "iid-1"}},
        )
        assert "messages" in result
        assert len(result["messages"]) == 1
        assert sleeps == []

    # ------------------------------------------------------------------
    # Additional coverage — reset / cap / edge cases
    # ------------------------------------------------------------------
    # Pinned behaviours for the throttle's ``messages[-1]``-only detection:
    #   * Parallel-tool calls produce interleaved ToolMessages and reset
    #     (NOT bump) — this is intentional and protects the
    #     "consecutive single-tool polling" target.
    #   * ToolMessage ``status="error"`` is ignored — name match is the
    #     sole criterion, so an error response still counts as a gii call.
    #   * Non-gii messages of any type (HumanMessage, AIMessage,
    #     non-gii ToolMessage) reset the counter.
    #   * Empty messages list is a safe no-op via the
    #     ``messages[-1] if messages else None`` guard.
    #   * Count >= 6 always maps to ``GII_MAX_DELAY`` (the cap holds).

    @pytest.mark.asyncio
    async def test_parallel_tool_call_resets_counter(self, monkeypatch):
        """Parallel-tool interleaving resets the counter — intentional.

        INTENTIONAL BEHAVIOR: when the agent emits ``get_instance_info`` in
        parallel with other tools in a single AIMessage, ToolNode produces
        interleaved ToolMessages, so ``messages[-1]`` may be a different
        tool. The throttle only detects CONSECUTIVE single-tool gii
        calls — the parallel path falls into the else branch and resets
        rather than accumulating.

        This test pins that design so a future refactor that tries to
        widen the throttle to track parallel calls fails loudly.
        """
        slot = _StubToolThrottleSlot()
        agent_node, llm = _make_agent(throttle_slot=slot)

        sleeps: list[float] = []

        async def fake_sleep(delay):
            sleeps.append(delay)

        monkeypatch.setattr("daemon.graph.asyncio.sleep", fake_sleep)

        # messages[-1] is a non-gii ToolMessage — must RESET, not bump
        await agent_node(
            {"messages": [_gii_tool_message(), _non_gii_tool_message()]},
            config={"configurable": {"thread_id": "iid-1"}},
        )

        # Parallel calls don't accumulate: reset called, bump NOT called
        assert slot.bump_calls == []
        assert slot.reset_calls == ["iid-1"]
        assert sleeps == []

    @pytest.mark.asyncio
    async def test_error_tool_message_still_bumps(self, monkeypatch):
        """ToolMessage with status="error" still bumps the counter.

        The throttle detection only checks ``name == GII_TOOL_NAME`` — the
        ``status`` field is intentionally ignored. An error response still
        counts as a consecutive gii invocation: the agent tried to call
        gii, the system answered, and the next attempt must be throttled.
        Otherwise an error-spamming agent could reset the counter every
        call by simply failing.
        """
        from daemon.graph import GII_DELAY_MAP

        # Pre-seed to 2 so this call is the 3rd consecutive one (triggers 180s)
        slot = _StubToolThrottleSlot(counts={"iid-1": 2})
        agent_node, llm = _make_agent(throttle_slot=slot)

        sleeps: list[float] = []

        async def fake_sleep(delay):
            sleeps.append(delay)

        monkeypatch.setattr("daemon.graph.asyncio.sleep", fake_sleep)

        error_gii = ToolMessage(
            content="upstream failed",
            tool_call_id="t-err",
            name="get_instance_info",
            status="error",
        )

        await agent_node(
            {"messages": [error_gii]},
            config={"configurable": {"thread_id": "iid-1"}},
        )

        # Counter bumped to 3, sleep 180 fired — status field was ignored
        assert slot.bump_calls == ["iid-1"]
        assert slot.reset_calls == []
        assert slot.get_count("iid-1") == 3
        assert sleeps == [GII_DELAY_MAP[3]]

    @pytest.mark.asyncio
    async def test_non_gii_tool_message_resets(self, monkeypatch):
        """After three gii bumps, a non-gii ToolMessage at the end resets."""
        from daemon.graph import GII_DELAY_MAP

        slot = _StubToolThrottleSlot()
        agent_node, llm = _make_agent(throttle_slot=slot)

        sleeps: list[float] = []

        async def fake_sleep(delay):
            sleeps.append(delay)

        monkeypatch.setattr("daemon.graph.asyncio.sleep", fake_sleep)

        # Three gii turns -> bumps 1..3, sleep fires on the third (count=3)
        msgs: list = []
        for turn in range(3):
            msgs.append(_gii_tool_message(content=f"turn-{turn}"))
            await agent_node(
                {"messages": list(msgs)},
                config={"configurable": {"thread_id": "iid-1"}},
            )

        # Add a non-gii ToolMessage at messages[-1] -> reset on the next turn
        msgs.append(_non_gii_tool_message(content="bash-output"))
        await agent_node(
            {"messages": list(msgs)},
            config={"configurable": {"thread_id": "iid-1"}},
        )

        # Three bumps followed by one reset; reset turn does not sleep
        assert slot.bump_calls == ["iid-1", "iid-1", "iid-1"]
        assert slot.reset_calls == ["iid-1"]
        assert sleeps == [GII_DELAY_MAP[3]]

    @pytest.mark.asyncio
    async def test_human_message_resets_counter(self, monkeypatch):
        """A HumanMessage at messages[-1] resets the throttle counter.

        Mirrors the production sequence where the user stops the polling
        agent by sending a message; the gii polling loop is broken, and
        the counter must be cleared so the agent isn't penalised on the
        next turn.
        """
        slot = _StubToolThrottleSlot()
        agent_node, llm = _make_agent(throttle_slot=slot)

        sleeps: list[float] = []

        async def fake_sleep(delay):
            sleeps.append(delay)

        monkeypatch.setattr("daemon.graph.asyncio.sleep", fake_sleep)

        await agent_node(
            {"messages": [_gii_tool_message(), HumanMessage(content="stop polling")]},
            config={"configurable": {"thread_id": "iid-1"}},
        )

        # HumanMessage is not a ToolMessage -> else branch -> reset
        assert slot.bump_calls == []
        assert slot.reset_calls == ["iid-1"]
        assert sleeps == []

    @pytest.mark.asyncio
    async def test_cap_holds_at_high_counts(self, monkeypatch):
        """Counts >= 6 all cap at GII_MAX_DELAY (900) — bump never saturates.

        Pre-seeds the slot to count=19 and runs one more bump to push it
        to 20. The throttle block reads ``GII_DELAY_MAP.get(count,
        GII_MAX_DELAY)`` so anything off the table (count >= 6) falls
        through to the 900s cap. This pins that:
          * bump returns the live count (does NOT saturate at some max int),
          * the delay is 900 (cap holds indefinitely),
          * the cap does NOT keep climbing past 900.
        """
        from daemon.graph import GII_MAX_DELAY

        # Pre-seed to 19 so this bump lands at 20 — well past the delay-table ceiling
        slot = _StubToolThrottleSlot(counts={"iid-1": 19})
        agent_node, llm = _make_agent(throttle_slot=slot)

        sleeps: list[float] = []

        async def fake_sleep(delay):
            sleeps.append(delay)

        monkeypatch.setattr("daemon.graph.asyncio.sleep", fake_sleep)

        await agent_node(
            {"messages": [_gii_tool_message()]},
            config={"configurable": {"thread_id": "iid-1"}},
        )

        assert slot.bump_calls == ["iid-1"]
        assert slot.reset_calls == []
        # The cap holds — 20 still maps to GII_MAX_DELAY, no further escalation
        assert sleeps == [GII_MAX_DELAY]
        # Bump did NOT saturate — the live count is preserved
        assert slot.get_count("iid-1") == 20

    @pytest.mark.asyncio
    async def test_real_tool_throttle_slot_integration(self, monkeypatch):
        """End-to-end delegation: agent_node → ToolThrottleSlot.bump → manager.

        Uses the REAL ``ToolThrottleSlot`` from ``daemon.graph`` wrapped
        around the ``_ManagerStub`` (which already binds the real
        ``bump_gii_throttle`` implementation onto its ``_gii_throttle``
        dict via ``__get__``). This proves the full delegation chain:
        the agent_node calls ``slot.bump``, which routes through to the
        manager, which mutates the shared dict — no mocks at the slot
        boundary.

        Three consecutive gii calls must bump the real counter 1..3 and
        trigger sleep(180) on the third.
        """
        from daemon.graph import GII_DELAY_MAP, ToolThrottleSlot

        manager = _make_manager_with_throttle_dict()
        slot = ToolThrottleSlot(manager)
        agent_node, llm = _make_agent(throttle_slot=slot)

        sleeps: list[float] = []

        async def fake_sleep(delay):
            sleeps.append(delay)

        monkeypatch.setattr("daemon.graph.asyncio.sleep", fake_sleep)

        msgs: list = []
        for turn in range(3):
            msgs.append(_gii_tool_message(content=f"turn-{turn}"))
            await agent_node(
                {"messages": list(msgs)},
                config={"configurable": {"thread_id": "iid-1"}},
            )

        # The shared manager dict must hold the bumped counter (real chain)
        assert manager.get_gii_throttle_count("iid-1") == 3
        # Only the third call sleeps — confirms the chain drives sleep correctly
        assert sleeps == [GII_DELAY_MAP[3]]

    @pytest.mark.asyncio
    async def test_aimessage_with_tool_calls_resets(self, monkeypatch):
        """An AIMessage (even one requesting a gii tool_call) resets the counter.

        The throttle only inspects ``messages[-1]``: a ``ToolMessage``
        with ``name == GII_TOOL_NAME`` bumps; anything else resets. The
        AIMessage here carries ``tool_calls=[{name='get_instance_info',
        ...}]`` — but until ToolNode runs and emits the resulting
        ``ToolMessage``, the AIMessage itself is not a ToolMessage, so
        the counter resets.

        This pins the messages[-1]-only design: the throttle does NOT
        walk ``AIMessage.tool_calls`` to peek at the next planned tool
        name (doing so would couple the throttle to AIMessage shape).
        """
        slot = _StubToolThrottleSlot()
        agent_node, llm = _make_agent(throttle_slot=slot)

        sleeps: list[float] = []

        async def fake_sleep(delay):
            sleeps.append(delay)

        monkeypatch.setattr("daemon.graph.asyncio.sleep", fake_sleep)

        planned_gii_ai = AIMessage(
            content="let me check the instance info",
            tool_calls=[{"name": "get_instance_info", "args": {}, "id": "call-1"}],
        )

        await agent_node(
            {"messages": [_gii_tool_message(), planned_gii_ai]},
            config={"configurable": {"thread_id": "iid-1"}},
        )

        # AIMessage is not a ToolMessage -> else branch -> reset
        assert slot.bump_calls == []
        assert slot.reset_calls == ["iid-1"]
        assert sleeps == []

    @pytest.mark.asyncio
    async def test_empty_messages_list_does_not_raise(self, monkeypatch):
        """Empty messages list must not IndexError — last_msg falls back to None.

        The throttle guards on ``messages[-1] if messages else None``, so
        an empty list takes the else branch and resets (safe no-op for
        an unset key). This test exists so a future refactor that drops
        the guard fails loudly in CI rather than crashing on the
        empty-state edge case.
        """
        slot = _StubToolThrottleSlot()
        agent_node, llm = _make_agent(throttle_slot=slot)

        sleeps: list[float] = []

        async def fake_sleep(delay):
            sleeps.append(delay)

        monkeypatch.setattr("daemon.graph.asyncio.sleep", fake_sleep)

        # Empty messages — must not raise
        result = await agent_node(
            {"messages": []},
            config={"configurable": {"thread_id": "iid-1"}},
        )

        # reset is called (safe no-op for an unset count)
        assert slot.bump_calls == []
        assert slot.reset_calls == ["iid-1"]
        assert sleeps == []
        # Agent still returns a state dict (LLM was invoked with just the system prompt)
        assert "messages" in result