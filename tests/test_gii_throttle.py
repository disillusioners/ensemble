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