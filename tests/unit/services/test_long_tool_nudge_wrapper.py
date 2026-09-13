"""T4 — _wrapped_tools_node tests: delegation, verbatim return, stamp
lifecycle (entry stamps / finally clears / exception / cancel), the
``handle_tool_errors=True`` pass-through, and the AD-9 healthy-gated
episode close.

The root ``tests/conftest.py`` installs global langgraph mocks; the
wrapper delegates to the REAL ``ToolNode``, so these tests run under
the repo-standard ``evict_langgraph_mocks`` /
``restore_langgraph_mocks`` pattern (fresh module import while the
real langgraph is loaded) and drive the wrapper through a REAL
compiled mini-graph (ToolNode.ainvoke requires the langgraph runtime
config — unavailable on bare node calls).
"""

from __future__ import annotations

import importlib
import sys
from typing import Any, TypedDict

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool

from tests.helpers.checkpoint_prune_pg import (
    evict_langgraph_mocks,
    restore_langgraph_mocks,
)


class _FakeClock:
    def __init__(self) -> None:
        self.t = 10_000.0

    def monotonic(self) -> float:
        return self.t


@pytest.fixture
def lt_real(monkeypatch):
    """Fresh ``daemon.services.long_tool_nudge`` with REAL langgraph."""
    saved = evict_langgraph_mocks()
    saved_lt = sys.modules.pop("daemon.services.long_tool_nudge", None)
    try:
        module = importlib.import_module("daemon.services.long_tool_nudge")
        yield module
    finally:
        sys.modules.pop("daemon.services.long_tool_nudge", None)
        if saved_lt is not None:
            sys.modules["daemon.services.long_tool_nudge"] = saved_lt
        restore_langgraph_mocks(saved)


@pytest.fixture
def fake_clock(monkeypatch):
    clock = _FakeClock()
    # Patched lazily per-test (the module object differs per fixture).
    return clock


def _patch_clock(lt, monkeypatch, clock):
    monkeypatch.setattr(lt, "time", clock)


def _patch_langgraph_toolnode(lt, monkeypatch, replacement):
    """Patch the ToolNode NAME the factory resolves at call time.

    Since the factory imports ``langgraph.prebuilt.ToolNode``
    function-level (mock-eviction safe), the patch target is the live
    ``langgraph.prebuilt`` module attribute."""
    import langgraph.prebuilt as lg_prebuilt

    monkeypatch.setattr(lg_prebuilt, "ToolNode", replacement)


@tool
def sample_tool(x: str) -> str:
    """Sample tool used by the wrapper tests."""
    return f"ok:{x}"


@tool
def boom_tool(x: str) -> str:
    """Tool that always raises."""
    raise RuntimeError("boom")


def _state(call_id: str = "call-1", name: str = "sample_tool", args=None) -> dict:
    return {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": name,
                        "args": args or {"x": "1"},
                        "id": call_id,
                        "type": "tool_call",
                    }
                ],
            )
        ]
    }


def _config(instance_id: str = "inst-1") -> dict:
    return {"configurable": {"thread_id": instance_id}}


class _S(TypedDict, total=False):
    messages: list


async def _run_through_real_graph(lt, node, state, config):
    """Drive a wrapper node inside a REAL compiled langgraph runtime."""
    from langgraph.graph import END, START, StateGraph

    g = StateGraph(_S)
    g.add_node("tools", node)
    g.add_edge(START, "tools")
    g.add_edge("tools", END)
    compiled = g.compile()
    return await compiled.ainvoke(state, config=config)


class TestWrappedToolsNodeNoBehaviorChange:
    @pytest.mark.asyncio
    async def test_output_shape_identical_for_mock_tool(self, lt_real):
        registry = lt_real.LongToolNudgeRegistry()
        node = lt_real._wrapped_tools_node([sample_tool], registry)
        result = await _run_through_real_graph(
            lt_real, node, _state(), _config()
        )
        messages = result["messages"]
        assert len(messages) == 1
        tm = messages[0]
        assert isinstance(tm, ToolMessage)
        assert tm.content == "ok:1"
        assert tm.tool_call_id == "call-1"
        assert tm.name == "sample_tool"

    @pytest.mark.asyncio
    async def test_delegates_to_underlying_ainvoke_once(
        self, lt_real, monkeypatch
    ):
        sentinel: Any = {"messages": ["delegated"]}
        captured: dict[str, Any] = {}
        from langgraph.prebuilt import ToolNode as real_toolnode

        class _RecordingToolNode(real_toolnode):  # type: ignore[misc, valid-type]
            async def ainvoke(self, state, config=None, **kwargs):
                captured["state"] = state
                captured["config"] = config
                captured["calls"] = captured.get("calls", 0) + 1
                return sentinel

        _patch_langgraph_toolnode(lt_real, monkeypatch, _RecordingToolNode)
        registry = lt_real.LongToolNudgeRegistry()
        node = lt_real._wrapped_tools_node([sample_tool], registry)
        state, config = _state(), _config()
        result = await _run_through_real_graph(
            lt_real, node, state, config
        )
        assert result == sentinel  # verbatim return, no reformatting
        assert captured["calls"] == 1
        # langgraph copies state through its channels — the delegated
        # state must be VALUE-equal (same messages, same tool_calls).
        assert [m.tool_calls for m in captured["state"]["messages"]] == [
            m.tool_calls for m in state["messages"]
        ]
        # The runtime derives the node's config; pin the identity
        # signal that must survive the wrapper: the thread_id.
        assert captured["config"]["configurable"]["thread_id"] == "inst-1"


class TestWrappedToolsNodeStampsAndClears:
    @pytest.mark.asyncio
    async def test_entry_stamps_and_return_clears_all(self, lt_real):
        registry = lt_real.LongToolNudgeRegistry()
        node = lt_real._wrapped_tools_node([sample_tool], registry)
        state = _state()
        state["messages"][0].tool_calls.append(
            {
                "name": "sample_tool",
                "args": {"x": "2"},
                "id": "call-2",
                "type": "tool_call",
            }
        )
        result = await _run_through_real_graph(
            lt_real, node, state, _config()
        )
        assert len(result["messages"]) == 2
        assert await registry.snapshot() == {}  # all stamped ids cleared

    @pytest.mark.asyncio
    async def test_stamp_present_mid_batch_with_shared_started_at(
        self, lt_real, monkeypatch
    ):
        """AD-3: per-id stamps share the batch-entry started_at, and the
        stamp is observable while the batch is in flight."""
        clock = _FakeClock()
        _patch_clock(lt_real, monkeypatch, clock)
        observed: dict[str, Any] = {}

        @tool
        def probe(x: str) -> str:
            """Probe the registry mid-batch."""
            stamps = registry._stamps.get("inst-1", {})
            observed["ids"] = sorted(stamps.keys())
            observed["started_at"] = {
                cid: s.started_at for cid, s in stamps.items()
            }
            return "probed"

        registry = lt_real.LongToolNudgeRegistry()
        node = lt_real._wrapped_tools_node([probe], registry)
        state = {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "probe",
                            "args": {"x": "y"},
                            "id": "call-p",
                            "type": "tool_call",
                        }
                    ],
                )
            ]
        }
        await _run_through_real_graph(lt_real, node, state, _config())
        assert observed["ids"] == ["call-p"]
        assert observed["started_at"]["call-p"] == clock.t  # batch entry
        assert await registry.snapshot() == {}

    @pytest.mark.asyncio
    async def test_batch_entry_shares_started_at_two_ids(
        self, lt_real, monkeypatch
    ):
        """AD-3: both ids in one batch get the SAME started_at."""
        clock = _FakeClock()
        _patch_clock(lt_real, monkeypatch, clock)
        observed: dict[str, Any] = {"started_at": {}}
        registry = lt_real.LongToolNudgeRegistry()

        @tool
        def probe_a(x: str) -> str:
            """Probe A."""
            snap = registry._stamps.get("inst-1", {})
            observed["started_at"]["call-a"] = snap["call-a"].started_at
            return "a"

        @tool
        def probe_b(x: str) -> str:
            """Probe B."""
            snap = registry._stamps.get("inst-1", {})
            observed["started_at"]["call-b"] = snap["call-b"].started_at
            return "b"

        node = lt_real._wrapped_tools_node([probe_a, probe_b], registry)
        state = {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "probe_a",
                            "args": {"x": "1"},
                            "id": "call-a",
                            "type": "tool_call",
                        },
                        {
                            "name": "probe_b",
                            "args": {"x": "2"},
                            "id": "call-b",
                            "type": "tool_call",
                        },
                    ],
                )
            ]
        }
        await _run_through_real_graph(lt_real, node, state, _config())
        assert observed["started_at"]["call-a"] == observed["started_at"]["call-b"]
        assert await registry.snapshot() == {}


class TestWrappedToolsNodeExceptionClearsStamps:
    @pytest.mark.asyncio
    async def test_exception_clears_and_propagates(self, lt_real, monkeypatch):
        from langgraph.prebuilt import ToolNode as real_toolnode

        class _RaisingToolNode(real_toolnode):  # type: ignore[misc, valid-type]
            async def ainvoke(self, state, config=None, **kwargs):
                raise RuntimeError("tool dispatch exploded")

        _patch_langgraph_toolnode(lt_real, monkeypatch, _RaisingToolNode)
        registry = lt_real.LongToolNudgeRegistry()
        node = lt_real._wrapped_tools_node([sample_tool], registry)
        with pytest.raises(RuntimeError, match="tool dispatch exploded"):
            await _run_through_real_graph(
                lt_real, node, _state(), _config()
            )
        assert await registry.snapshot() == {}  # finally cleared


class TestWrappedToolsNodeCancelClearsStamps:
    @pytest.mark.asyncio
    async def test_cancel_clears_and_propagates(self, lt_real, monkeypatch):
        import asyncio

        from langgraph.prebuilt import ToolNode as real_toolnode

        class _CancellingToolNode(real_toolnode):  # type: ignore[misc, valid-type]
            async def ainvoke(self, state, config=None, **kwargs):
                raise asyncio.CancelledError()

        _patch_langgraph_toolnode(lt_real, monkeypatch, _CancellingToolNode)
        registry = lt_real.LongToolNudgeRegistry()
        node = lt_real._wrapped_tools_node([sample_tool], registry)
        with pytest.raises(BaseException):  # CancelledError may wrap in the runtime
            await _run_through_real_graph(
                lt_real, node, _state(), _config()
            )
        assert await registry.snapshot() == {}  # finally cleared on cancel


class TestWrappedToolsNodeHandleToolErrorsTrue:
    @pytest.mark.asyncio
    async def test_tool_error_returns_errormessage_no_raise(self, lt_real):
        registry = lt_real.LongToolNudgeRegistry()
        node = lt_real._wrapped_tools_node([boom_tool], registry)
        result = await _run_through_real_graph(
            lt_real, node, _state(name="boom_tool", args={"x": "y"}), _config()
        )
        messages = result["messages"]
        assert len(messages) == 1
        tm = messages[0]
        assert isinstance(tm, ToolMessage)
        assert "Error" in tm.content
        assert tm.tool_call_id == "call-1"
        assert await registry.snapshot() == {}


class TestWrappedToolsNodeCloseEpisodeGatedOnHealthyCompletion:
    """BLOCKING AD-9 / AD-42 Option (ii) close-gate pin."""

    def _scanner_stub(self):
        closes: list[tuple[str, str]] = []

        def close(parent_id: str, child_id: str) -> None:
            closes.append((parent_id, child_id))

        return closes, close

    @pytest.mark.asyncio
    async def test_healthy_completion_closes_episode(self, lt_real):
        closes, close = self._scanner_stub()
        registry = lt_real.LongToolNudgeRegistry()
        registry.attach_close_handler(close)
        registry.attach_threshold_resolver(lambda iid: 900)
        registry.attach_parent_lookup(lambda iid: "parent-1")
        node = lt_real._wrapped_tools_node([sample_tool], registry)
        # Healthy: the batch completes in << threshold.
        await _run_through_real_graph(lt_real, node, _state(), _config())
        assert closes == [("parent-1", "inst-1")]

    @pytest.mark.asyncio
    async def test_long_completion_leaves_episode_open(
        self, lt_real, monkeypatch
    ):
        clock = _FakeClock()
        _patch_clock(lt_real, monkeypatch, clock)
        closes, close = self._scanner_stub()
        registry = lt_real.LongToolNudgeRegistry()
        registry.attach_close_handler(close)
        registry.attach_threshold_resolver(lambda iid: 900)
        registry.attach_parent_lookup(lambda iid: "parent-1")
        node = lt_real._wrapped_tools_node([sample_tool], registry)
        original_record = registry.record_start

        async def aging_record(
            instance_id, tool_call_id, tool_name, parent_id=None
        ):
            await original_record(
                instance_id, tool_call_id, tool_name, parent_id
            )
            registry._stamps[instance_id][tool_call_id].started_at = (
                clock.t - 950  # LONG (> 900s)
            )

        registry.record_start = aging_record  # type: ignore[method-assign]
        await _run_through_real_graph(lt_real, node, _state(), _config())
        assert closes == []  # LONG completion does NOT close

    @pytest.mark.asyncio
    async def test_no_close_when_parent_unknown(self, lt_real):
        closes, close = self._scanner_stub()
        registry = lt_real.LongToolNudgeRegistry()
        registry.attach_close_handler(close)
        registry.attach_threshold_resolver(lambda iid: 900)
        # No parent lookup attached → parent_id None → no close call.
        node = lt_real._wrapped_tools_node([sample_tool], registry)
        await _run_through_real_graph(lt_real, node, _state(), _config())
        assert closes == []


class TestWrappedToolsNodeNoToolCalls:
    @pytest.mark.asyncio
    async def test_empty_tool_calls_delegates_without_stamping(self, lt_real):
        registry = lt_real.LongToolNudgeRegistry()
        node = lt_real._wrapped_tools_node([sample_tool], registry)
        state = {"messages": [AIMessage(content="no tools here")]}
        result = await _run_through_real_graph(lt_real, node, state, _config())
        # Bare ToolNode with no tool_calls returns an empty messages list.
        assert result == {"messages": []}
        assert await registry.snapshot() == {}
