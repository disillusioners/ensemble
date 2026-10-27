"""Regression tests for the proactive-compaction namespace trap fix.

The trap: in-node ``graph.aget_state(thread_config)`` calls carried the
caller's ns-stamped config (``checkpoint_ns='agent:<task_id>'``); LangGraph
resolves that as a subgraph-namespace lookup and returns a truthy-but-EMPTY
``StateSnapshot`` (real checkpoints live at ``ns=''``). The L2 95% hook
then fed the engine an empty message list and falsely declined as
``skipped_injections_dominate`` on every LLM invoke.

These tests FAIL on trapped code and PASS after the fix.
"""
from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
)

import daemon.loader as loader_mod
from daemon.compaction import ContextCompactor
from daemon.config import CompactionConfig as CompactionConfigModel
from daemon.graph import _PRECALL_NOOP, _maybe_precall_compact_95
from daemon.loader import estimate_messages_tokens
from daemon.services import _compaction_persist_seam as seam_mod


def make_compaction_config(**overrides: Any) -> CompactionConfigModel:
    defaults: dict[str, Any] = {
        "enabled": True,
        "threshold": 0.80,
        "recent_message_window": 10,
        "min_recent_window": 3,
        "context_window_overrides": {},
        "context_window_default": 0,
        "target_ratio": 0.40,
        "model": "",
        "summarization_model": "",
        "min_messages_before_compaction": 10,
        "summarization_chunk_threshold": 0.60,
        "timeout_base_s": 90.0,
        "timeout_per_100k_tokens_s": 60.0,
        "timeout_cap_s": 300.0,
        "timeout_facade_margin_s": 5.0,
        "operation_budget_s": 300.0,
        "chunk_concurrency": 3,
        "proactive_enabled": True,
    }
    defaults.update(overrides)
    return CompactionConfigModel(**defaults)


def make_messages(n: int, content_prefix: str = "M") -> list:
    out = []
    for i in range(n):
        cls = HumanMessage if i % 2 == 0 else AIMessage
        out.append(cls(content=f"{content_prefix} {i}", id=f"m-{i}"))
    return out


@dataclass
class _FakeState:
    values: dict = field(default_factory=dict)
    next: tuple = ()


class _NsTrapGraph:
    """Fake graph that mimics the namespace trap: an ns-stamped config
    (``configurable.checkpoint_ns`` present) yields a truthy-but-EMPTY
    snapshot; a thread-id-only config yields the real snapshot."""

    def __init__(self, values: dict | None = None, *, always_empty: bool = False) -> None:
        self.values: dict = dict(values or {})
        self.always_empty = always_empty
        self.aget_state_configs: list = []
        self.aupdate_state_calls: list = []

    async def aget_state(self, config):
        self.aget_state_configs.append(config)
        if self.always_empty:
            return _FakeState(values={})
        cfg = config or {}
        if (cfg.get("configurable") or {}).get("checkpoint_ns"):
            return _FakeState(values={})  # trap: empty snapshot
        return _FakeState(values=dict(self.values))

    async def aupdate_state(self, config, update, **kwargs):
        self.aupdate_state_calls.append((config, dict(update), dict(kwargs)))
        if "messages" in update:
            msgs = list(update["messages"])
            if msgs and isinstance(msgs[0], RemoveMessage):
                msgs = msgs[1:]
            self.values["messages"] = msgs
        if "compacted_at" in update:
            self.values["compacted_at"] = update["compacted_at"]
        return None


def _make_compactor(**config_overrides: Any) -> ContextCompactor:
    return ContextCompactor(make_compaction_config(**config_overrides), {})


def _run_hook(graph, compactor, full_messages, *, instance_id="ns-trap-instance-0001", thread_config=None, **kw):
    if thread_config is None:
        thread_config = {"configurable": {"thread_id": instance_id, "checkpoint_ns": "agent:task-1"}}
    return _maybe_precall_compact_95(
        instance_id=instance_id,
        instance_short=instance_id.split("-")[0],
        compactor=compactor,
        graph_ref=[graph],
        thread_config=thread_config,
        full_messages=full_messages,
        system_prompt="system prompt",
        llm_config=kw.get("llm_config", {"model": "test-model"}),
        injected_msgs=kw.get("injected_msgs", []),
        injected_report_msgs=kw.get("injected_report_msgs", []),
        ephemeral_context_msgs=[],
        pairing_synthesized_msgs=kw.get("pairing_synthesized_msgs", []),
        precall_compaction_tap_slot=kw.get("tap_slot"),
    )


# =============================================================================
# (a) ns-stamped thread_config but engine sees real message list
# =============================================================================


class TestNsTrapL2EngineSeesRealMessages:
    @pytest.mark.asyncio
    async def test_compaction_proceeds_with_ns_stamped_thread_config(self, monkeypatch):
        import daemon.graph as graph_mod

        WINDOW = 600_000
        compactor = _make_compactor(context_window_overrides={"test-model": WINDOW})
        # force compactor to fire
        monkeypatch.setattr(loader_mod, "estimate_messages_tokens", lambda msgs: 571_000)

        graph_msgs = make_messages(5)
        graph = _NsTrapGraph(values={"messages": graph_msgs})

        captured: dict = {}

        async def _engine(ctx, force=False):
            captured["ctx_messages"] = list(ctx.messages)
            captured["force"] = force
            # return a compaction result with replacement messages
            from types import SimpleNamespace
            return SimpleNamespace(
                replacement_messages=[HumanMessage(content="compacted", id="c-1")],
                compacted_at="2026-01-01T00:00:00Z",
                messages_before=5,
                messages_after=1,
                tokens_saved=100,
                compaction_type="chunked",
                compacted_ids=None,
            )

        compactor.compact_state = _engine
        # avoid dedup/refire issues: stub warn
        compactor.precall_warn_should_emit = lambda iid: False

        payload = make_messages(5)
        outcome = await _run_hook(graph, compactor, payload)

        # engine must have seen the REAL message list, not empty
        assert captured["ctx_messages"] == graph_msgs, (
            "engine must be fed the checkpoint channel (real list), not empty"
        )
        # compaction proceeded
        assert outcome.rebuilt_payload is not None
        assert outcome.compacted_at == "2026-01-01T00:00:00Z"
        # all aget_state calls used thread-id-only config (no checkpoint_ns)
        for cfg in graph.aget_state_configs:
            assert (cfg.get("configurable") or {}).get("checkpoint_ns") is None, (
                "aget_state must use thread-id-only config, no checkpoint_ns"
            )
            assert (cfg.get("configurable") or {}).get("thread_id") == "ns-trap-instance-0001"

    @pytest.mark.asyncio
    async def test_aget_state_configs_recorded_thread_id_only(self, monkeypatch):
        WINDOW = 600_000
        compactor = _make_compactor(context_window_overrides={"test-model": WINDOW})
        monkeypatch.setattr(loader_mod, "estimate_messages_tokens", lambda msgs: 571_000)
        graph = _NsTrapGraph(values={"messages": make_messages(5)})

        async def _engine(ctx, force=False):
            from types import SimpleNamespace
            return SimpleNamespace(
                replacement_messages=[HumanMessage(content="compacted", id="c-1")],
                compacted_at="2026-01-01T00:00:00Z",
                messages_before=5,
                messages_after=1,
                tokens_saved=100,
                compaction_type="chunked",
                compacted_ids=None,
            )

        compactor.compact_state = _engine
        compactor.precall_warn_should_emit = lambda iid: False

        await _run_hook(graph, compactor, make_messages(5))

        # at least 2 reads (engine-feed + post-persist rebuild)
        assert len(graph.aget_state_configs) >= 2
        for cfg in graph.aget_state_configs:
            assert "checkpoint_ns" not in (cfg.get("configurable") or {})


# =============================================================================
# (b) empty-read-vs-expected guard fires
# =============================================================================


class TestNsTrapEmptyReadGuard:
    @pytest.mark.asyncio
    async def test_guard_fires_warn_and_no_stamp(self, monkeypatch, caplog):
        WINDOW = 600_000
        compactor = _make_compactor(context_window_overrides={"test-model": WINDOW})
        monkeypatch.setattr(loader_mod, "estimate_messages_tokens", lambda msgs: 571_000)
        # graph always returns empty even for thread-id-only config
        graph = _NsTrapGraph(values={"messages": make_messages(5)}, always_empty=True)

        engine_calls = {"n": 0}

        async def _engine(ctx, force=False):
            engine_calls["n"] += 1
            from types import SimpleNamespace
            return SimpleNamespace(
                replacement_messages=[HumanMessage(content="compacted", id="c-1")],
                compacted_at="2026-01-01T00:00:00Z",
                messages_before=5,
                messages_after=1,
                tokens_saved=100,
                compaction_type="chunked",
                compacted_ids=None,
            )

        compactor.compact_state = _engine
        compactor.precall_warn_should_emit = lambda iid: False

        payload = make_messages(5)
        with caplog.at_level(logging.WARNING):
            outcome = await _run_hook(graph, compactor, payload)

        assert engine_calls["n"] == 0, "guard must return before engine invocation"

        # guard fired → noop, no stamp
        assert outcome == _PRECALL_NOOP
        assert outcome.compacted_at is None
        assert outcome.rebuilt_payload is None
        # WARN emitted
        assert any("empty snapshot read" in r.message for r in caplog.records), (
            "expected distinguishable WARN on empty snapshot read"
        )

    @pytest.mark.asyncio
    async def test_guard_does_not_fire_when_messages_present(self, monkeypatch):
        WINDOW = 600_000
        compactor = _make_compactor(context_window_overrides={"test-model": WINDOW})
        monkeypatch.setattr(loader_mod, "estimate_messages_tokens", lambda msgs: 571_000)
        graph = _NsTrapGraph(values={"messages": make_messages(5)})

        async def _engine(ctx, force=False):
            from types import SimpleNamespace
            return SimpleNamespace(
                replacement_messages=[HumanMessage(content="compacted", id="c-1")],
                compacted_at="2026-01-01T00:00:00Z",
                messages_before=5,
                messages_after=1,
                tokens_saved=100,
                compaction_type="chunked",
                compacted_ids=None,
            )

        compactor.compact_state = _engine
        compactor.precall_warn_should_emit = lambda iid: False

        payload = make_messages(5)
        outcome = await _run_hook(graph, compactor, payload)
        # compaction proceeded (not noop) because guard didn't fire
        assert outcome.rebuilt_payload is not None


# =============================================================================
# (c) CLE read/write config targeting — source pin + helper
# =============================================================================


class TestNsTrapCLETargeting:
    def test_root_thread_config_helper_exists(self):
        from daemon.graph import _root_thread_config
        assert callable(_root_thread_config)
        cfg = _root_thread_config("abc-123")
        assert cfg == {"configurable": {"thread_id": "abc-123"}}
        assert "checkpoint_ns" not in cfg["configurable"]

    def test_cle_sites_use_root_thread_config(self):
        import daemon.graph as graph_mod
        source = inspect.getsource(graph_mod)
        # thread_config assignment changed to use helper
        assert "thread_config = _root_thread_config(instance_id)" in source
        # CLE persist now routes through the shared seam (Critical interlock)
        assert 'abort_policy="raise",' in source
        assert source.count('abort_policy="raise",') == 1
        # guard present at CLE read
        assert "empty snapshot read — suspected" in source or "empty snapshot read" in source

    def test_cle_guard_raises(self):
        # Structural: the guard branch must contain a raise (not just a
        # log substring), otherwise the guard is a no-op.
        import daemon.graph as graph_mod
        import re
        source = inspect.getsource(graph_mod)
        assert "[Compaction][CLE] empty snapshot read" in source
        assert re.search(
            r"\[Compaction\]\[CLE\] empty snapshot read.*?\n\s*raise",
            source,
            re.DOTALL,
        ), "CLE guard must contain a raise statement"


# =============================================================================
# Seam-level behavioral pin for the Critical interlock (Finding 6)
# =============================================================================


class TestSeamEmptyReplacementInterlock:
    @pytest.mark.asyncio
    async def test_empty_replacement_writes_only_compacted_at(self):
        from daemon.compaction import CompactionResult
        from daemon.services._compaction_persist_seam import persist_compaction_result

        graph = _NsTrapGraph(values={"messages": make_messages(5)})
        result = CompactionResult(
            replacement_messages=[],
            tokens_before=100,
            tokens_after=100,
            tokens_saved=0,
            messages_before=5,
            messages_after=5,
            compaction_type="anti_refire_skip",
            compacted_at="2026-01-01T00:00:00Z",
        )

        persisted = await persist_compaction_result(
            None,
            instance_id="ns-trap-instance-0001",
            result=result,
            mid_turn=True,
            abort_policy="raise",
            graph=graph,
        )

        assert persisted is True
        # NO messages write occurred
        msgs_writes = [c for c in graph.aupdate_state_calls if "messages" in c[1]]
        assert msgs_writes == [], "empty replacement must NOT reach a messages write"
        # ONLY compacted_at write
        stamp_writes = [c for c in graph.aupdate_state_calls if "compacted_at" in c[1]]
        assert len(stamp_writes) == 1
        # all writes thread-id-only (root ns)
        for cfg, upd, kw in graph.aupdate_state_calls:
            assert "checkpoint_ns" not in (cfg.get("configurable") or {})

    @pytest.mark.asyncio
    async def test_real_replacement_writes_messages(self):
        from daemon.compaction import CompactionResult
        from daemon.services._compaction_persist_seam import persist_compaction_result

        graph = _NsTrapGraph(values={"messages": make_messages(5)})
        result = CompactionResult(
            replacement_messages=[HumanMessage(content="compacted", id="c-1")],
            tokens_before=100,
            tokens_after=50,
            tokens_saved=50,
            messages_before=5,
            messages_after=1,
            compaction_type="chunked",
            compacted_at="2026-01-01T00:00:00Z",
        )

        persisted = await persist_compaction_result(
            None,
            instance_id="ns-trap-instance-0001",
            result=result,
            mid_turn=True,
            abort_policy="raise",
            graph=graph,
        )

        assert persisted is True
        msgs_writes = [c for c in graph.aupdate_state_calls if "messages" in c[1]]
        assert len(msgs_writes) == 1
