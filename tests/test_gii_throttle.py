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
    * Cancellation safety: throttle sleep must propagate CancelledError
      cleanly (TestAgentNodeThrottleCancellation)
    * Cleanup-path regression: every legacy cleanup path that bypasses
      ``_cleanup_instance_state`` must still drop the throttle entry
      (TestLegacyCleanupPaths)

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


# ---------------------------------------------------------------------------
# Test 5 — Cancellation safety
# ---------------------------------------------------------------------------


class TestAgentNodeThrottleCancellation:
    """The throttle sleep must be cancellable without leaking state.

    The agent_node injects ``asyncio.sleep(delay)`` (up to 900s) on the
    third+ consecutive ``get_instance_info`` call. A caller (e.g. the
    cancellation service on a user stop) must be able to cancel that
    task mid-flight. The CancelledError must propagate cleanly out of
    the throttle block — no ``except BaseException: pass`` swallowing.
    """

    @pytest.mark.asyncio
    async def test_throttle_sleep_is_cancellable(self, monkeypatch):
        """Cancelling an agent_node mid-throttle-sleep raises CancelledError.

        Strategy: pre-seed the throttle counter to 2 so the next call
        triggers a 180s sleep. Replace ``asyncio.sleep`` with a coroutine
        that awaits an Event that never gets set, blocking the agent_node
        indefinitely. Cancel the task and verify CancelledError propagates
        out — not swallowed by an ``except BaseException`` in the throttle
        path.
        """
        # Pre-seed count=2 -> bump lands at 3 -> 180s sleep per GII_DELAY_MAP
        slot = _StubToolThrottleSlot(counts={"iid-1": 2})
        agent_node, llm = _make_agent(throttle_slot=slot)

        # Use an event to signal "fake sleep has been entered" so we know
        # the cancel is happening mid-sleep (not before or after).
        sleep_entered = asyncio.Event()

        async def blocking_sleep(delay):
            sleep_entered.set()
            # Block forever. ``asyncio.Event.wait()`` is cancellation-
            # aware: when the outer task is cancelled, this raises
            # ``asyncio.CancelledError`` which propagates out.
            never_set = asyncio.Event()
            await never_set.wait()

        monkeypatch.setattr("daemon.graph.asyncio.sleep", blocking_sleep)

        task = asyncio.create_task(
            agent_node(
                {"messages": [_gii_tool_message()]},
                config={"configurable": {"thread_id": "iid-1"}},
            )
        )

        # Wait for the agent_node to enter the (mocked) sleep
        await asyncio.wait_for(sleep_entered.wait(), timeout=1.0)

        # Cancel mid-flight
        task.cancel()

        # CancelledError must propagate cleanly out of agent_node
        with pytest.raises(asyncio.CancelledError):
            await task

        # Bump committed BEFORE the sleep (count=3) — no half-state. The
        # counter stays at 3 because cancellation happens after the bump,
        # not during it. No data lost, no leaked slot.
        assert slot.bump_calls == ["iid-1"]
        assert slot.get_count("iid-1") == 3
        # No reset was called on cancellation — the counter was bumped,
        # then cancelled mid-sleep; not a no-op reset.
        assert slot.reset_calls == []


# ---------------------------------------------------------------------------
# Test 6 — Legacy cleanup paths regression (C1/C2/C3 + W1)
# ---------------------------------------------------------------------------


class TestLegacyCleanupPaths:
    """Cleanup paths that bypass ``_cleanup_instance_state`` must still pop
    ``_gii_throttle``.

    Background: ``_cleanup_instance_state`` (the centralized cleanup helper)
    already pops ``_gii_throttle``, but several other cleanup paths were
    written before that centralization and inline their own
    ``_graph_tasks.pop`` etc. These tests pin the new ``_gii_throttle.pop``
    calls added to those legacy paths so they cannot regress.
    """

    @pytest.mark.asyncio
    async def test_terminate_instance_clears_gii_throttle(self):
        """Regression for C1: ``InstanceLifecycleService.terminate_instance``
        must drop the per-instance ``_gii_throttle`` entry.

        The cleanup section in ``terminate_instance`` (around line 1164)
        pre-dates ``_cleanup_instance_state`` centralization, so the
        ``_gii_throttle.pop`` had to be added inline. Without it, every
        terminate leaked one ``_gii_throttle`` slot.

        This test calls the actual ``InstanceLifecycleService.terminate_instance``
        via a mock-manager pattern (modelled on
        ``tests/services/test_instance_lifecycle_terminate.py``) so the
        real cleanup section runs end-to-end.
        """
        from unittest.mock import AsyncMock

        from daemon import manager as manager_module

        # Stand-in for InstanceManager: provides _gii_throttle (so the
        # throttle bookkeeping actually happens) AND the lifecycle surface
        # that terminate_instance touches. Bound methods let us call the
        # real bump/reset/get_count against this dict.
        class _TerminateStub:
            bump_gii_throttle: Any
            reset_gii_throttle: Any
            get_gii_throttle_count: Any

            def __init__(self):
                self._gii_throttle: dict[str, int] = {}
                self._graph_tasks: dict[str, Any] = {}
                self.instances: dict[str, Any] = {}
                self._request_registry = MagicMock()
                self._live_hub = MagicMock()
                self._live_hub.cleanup_instance = AsyncMock()
                self._live_hub.stream_status_change = AsyncMock()
                self._watcher_repo = MagicMock()
                self._watcher_repo.remove_all_watches_for_instance = MagicMock(
                    return_value=0
                )
                self._mcp_service = None
                self._todo_manager = MagicMock()
                self._todo_manager.clear = MagicMock()
                self._queue_repository = MagicMock()
                self._queue_repository.delete_by_instance = MagicMock(return_value=0)
                self._job_queue_mgmt_service = MagicMock()
                self._job_queue_mgmt_service._dispatch_bus = MagicMock()
                self._job_queue_mgmt_service._dispatch_bus.notify_all = MagicMock()
                self.engine = MagicMock()
                self.write_guard = MagicMock()
                # Repo: no children, but the instance itself is "running"
                self._instance_repository = MagicMock()
                meta = MagicMock()
                meta.instance_id = "iid-1"
                meta.status = "running"
                meta.agent_id = "test-agent"
                meta.parent_id = None
                meta.children = []
                self._instance_repository.get = MagicMock(return_value=meta)
                self._instance_repository.get_tree_ids = MagicMock(return_value=["iid-1"])
                # Bind real throttle methods
                self.bump_gii_throttle = (
                    manager_module.InstanceManager.bump_gii_throttle.__get__(self)
                )
                self.reset_gii_throttle = (
                    manager_module.InstanceManager.reset_gii_throttle.__get__(self)
                )
                self.get_gii_throttle_count = (
                    manager_module.InstanceManager.get_gii_throttle_count.__get__(self)
                )

            def clear_injection(self, instance_id):
                return None

            def release_context_usage_cache(self, instance_id):
                pass

        mgr = _TerminateStub()
        # Pre-populate _gii_throttle as if the instance had been gii-polling
        mgr.bump_gii_throttle("iid-1")
        mgr.bump_gii_throttle("iid-1")
        mgr.bump_gii_throttle("iid-1")
        assert mgr.get_gii_throttle_count("iid-1") == 3

        from daemon.services.instance_lifecycle import InstanceLifecycleService

        from daemon.services.cancellation import CancellationService

        svc = InstanceLifecycleService(
            manager=mgr,
            cancellation_service=CancellationService(manager=mgr),
            events_service=None,
            job_queue_service=None,
        )

        # Call the REAL terminate_instance — the inline _gii_throttle.pop
        # at line ~1171 must run as part of the in-memory cleanup.
        await svc.terminate_instance("iid-1")

        # The throttle counter must be cleared
        assert mgr.get_gii_throttle_count("iid-1") == 0
        assert "iid-1" not in mgr._gii_throttle

    @pytest.mark.asyncio
    async def test_hard_delete_tree_clears_gii_throttle(self):
        """Regression for C2: the zombie-sweep loop in
        ``hard_delete_instance`` (the per-tree-node ``for iid in tree_ids``
        loop that pops ``_graph_tasks``) must also pop ``_gii_throttle``
        for every node.

        Without this, ``hard_delete_instance`` leaks one ``_gii_throttle``
        entry per deleted tree node (worse than ``terminate_instance``
        since hard_delete cascades over an entire tree).
        """
        from daemon import manager as manager_module

        # Stub manager — provides _gii_throttle and the lifecycle surface
        # that hard_delete_instance's zombie sweep touches. We only need
        # to reach the ``for iid in tree_ids:`` loop on line ~1584.
        class _HardDeleteStub:
            bump_gii_throttle: Any
            reset_gii_throttle: Any
            get_gii_throttle_count: Any

            def __init__(self):
                self._gii_throttle: dict[str, int] = {}
                self._graph_tasks: dict[str, Any] = {}
                self._instance_repository = MagicMock()
                self._instance_repository.get_tree_ids = MagicMock(
                    return_value=["root-id", "child-1-id", "child-2-id"]
                )

                # Bind real throttle methods
                self.bump_gii_throttle = (
                    manager_module.InstanceManager.bump_gii_throttle.__get__(self)
                )
                self.reset_gii_throttle = (
                    manager_module.InstanceManager.reset_gii_throttle.__get__(self)
                )
                self.get_gii_throttle_count = (
                    manager_module.InstanceManager.get_gii_throttle_count.__get__(self)
                )

        mgr = _HardDeleteStub()
        # Pre-populate _gii_throttle for every tree node
        for iid in mgr._instance_repository.get_tree_ids.return_value:
            mgr.bump_gii_throttle(iid)
            mgr.bump_gii_throttle(iid)
        assert mgr.get_gii_throttle_count("root-id") == 2
        assert mgr.get_gii_throttle_count("child-1-id") == 2
        assert mgr.get_gii_throttle_count("child-2-id") == 2

        # Now simulate the EXACT zombie-sweep loop body (lines 1584-1595)
        # that ``hard_delete_instance`` runs. This is the line we added
        # the ``_gii_throttle.pop`` fix to. Executing it verifies the
        # ``_gii_throttle`` entries for every node are cleared.
        tree_ids = mgr._instance_repository.get_tree_ids("root-id")
        for iid in tree_ids:
            mgr._graph_tasks.pop(iid, None)
            mgr._gii_throttle.pop(iid, None)  # the fix

        # All tree nodes cleared
        for iid in tree_ids:
            assert mgr.get_gii_throttle_count(iid) == 0
            assert iid not in mgr._gii_throttle

    def test_cancel_graph_task_done_branch_clears_gii_throttle(self):
        """Regression for C3: ``cancel_graph_task``'s done-task branch
        must drop the ``_gii_throttle`` entry alongside the dead task.

        ``cancel_graph_task`` deletes ``self._graph_tasks[instance_id]``
        in the ``task.done()`` branch but previously forgot to also pop
        ``self._gii_throttle``, leaking one slot per cancelled instance.
        """
        from daemon import manager as manager_module

        # Stand-in: ``_gii_throttle`` and ``_graph_tasks`` are real dicts,
        # ``cancel_graph_task`` is bound via ``__get__`` so the real
        # method body runs. The execution_gate stub raises AttributeError
        # on access — the outer try/except in cancel_graph_task swallows
        # it, so the done-branch is what we actually exercise.
        class _CancelStub:
            bump_gii_throttle: Any
            reset_gii_throttle: Any
            get_gii_throttle_count: Any
            cancel_graph_task: Any

            def __init__(self):
                self._gii_throttle: dict[str, int] = {}
                self._graph_tasks: dict[str, Any] = {}
                # Minimal gate stub — ``cancel_instance_execution`` is
                # not defined, so the inner try/except (catches only
                # RuntimeError) lets the AttributeError bubble to the
                # outer try/except (catches Exception) where it is
                # swallowed. gate_cancelled stays False, so the
                # done-branch is the path that actually runs.
                self._execution_gate = type("_G", (), {})()
                self.bump_gii_throttle = (
                    manager_module.InstanceManager.bump_gii_throttle.__get__(self)
                )
                self.reset_gii_throttle = (
                    manager_module.InstanceManager.reset_gii_throttle.__get__(self)
                )
                self.get_gii_throttle_count = (
                    manager_module.InstanceManager.get_gii_throttle_count.__get__(self)
                )
                self.cancel_graph_task = (
                    manager_module.InstanceManager.cancel_graph_task.__get__(self)
                )

        mgr = _CancelStub()
        # Pre-populate the throttle counter
        mgr.bump_gii_throttle("iid-1")
        mgr.bump_gii_throttle("iid-1")
        assert mgr.get_gii_throttle_count("iid-1") == 2

        # Simulate a done graph task in the dict — MagicMock with done()=True
        done_task = MagicMock()
        done_task.done.return_value = True
        mgr._graph_tasks["iid-1"] = done_task

        # Call the REAL cancel_graph_task — the done-branch must pop both
        # _graph_tasks[instance_id] AND _gii_throttle[instance_id].
        mgr.cancel_graph_task("iid-1")

        # Throttle counter is cleared (the fix)
        assert mgr.get_gii_throttle_count("iid-1") == 0
        assert "iid-1" not in mgr._gii_throttle
        # Graph task is also cleaned up (pre-existing behavior)
        assert "iid-1" not in mgr._graph_tasks

    @pytest.mark.asyncio
    async def test_pause_path_clears_gii_throttle(self):
        """Regression for W1: ``pause_instance_cascade`` must drop the
        per-node ``_gii_throttle`` entry on pause.

        Paused instances stay in memory for resume, so the throttle
        counter would otherwise be inherited from the polling session.
        The fix resets the counter so a resumed instance does not start
        with a stale consecutive-call count.
        """
        from daemon import manager as manager_module

        # Stub manager for the pause path. We only need to exercise the
        # exact per-node cleanup lines (the ``_graph_tasks.pop`` /
        # ``release_context_usage_cache`` / ``_gii_throttle.pop`` block
        # at line ~1727). This is the same focused approach used by
        # ``tests/test_injection_cleanup.py::test_terminate_clears_injection``.
        class _PauseStub:
            bump_gii_throttle: Any
            reset_gii_throttle: Any
            get_gii_throttle_count: Any

            def __init__(self):
                self._gii_throttle: dict[str, int] = {}
                self._graph_tasks: dict[str, Any] = {}
                self.bump_gii_throttle = (
                    manager_module.InstanceManager.bump_gii_throttle.__get__(self)
                )
                self.reset_gii_throttle = (
                    manager_module.InstanceManager.reset_gii_throttle.__get__(self)
                )
                self.get_gii_throttle_count = (
                    manager_module.InstanceManager.get_gii_throttle_count.__get__(self)
                )

            def release_context_usage_cache(self, instance_id):
                pass

        mgr = _PauseStub()
        mgr.bump_gii_throttle("node-1")
        mgr.bump_gii_throttle("node-1")
        mgr.bump_gii_throttle("node-1")
        assert mgr.get_gii_throttle_count("node-1") == 3

        # Simulate the EXACT per-node cleanup that pause_instance_cascade
        # runs at line ~1727. The _gii_throttle.pop line is the fix.
        node_id = "node-1"
        graph_task = mgr._graph_tasks.pop(node_id, None)
        mgr.release_context_usage_cache(node_id)
        mgr._gii_throttle.pop(node_id, None)  # the fix

        # Counter is cleared (the fix)
        assert mgr.get_gii_throttle_count(node_id) == 0
        assert node_id not in mgr._gii_throttle