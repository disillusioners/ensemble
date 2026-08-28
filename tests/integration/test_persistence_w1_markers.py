"""W1 INTERIM RESOLUTION — real-checkpoint probe for marker serialization.

End-to-end test that the W1 additive wire-up survives the LangGraph
checkpoint round-trip AND is surfaced by ``get_instance_messages`` on
the read path. Uses the real ``AsyncSqliteSaver`` (file-backed SQLite)
+ real ``StateGraph`` + real ``aupdate_state`` + real
``get_instance_messages`` — no mocks on the persistence boundary.

The root ``tests/conftest.py`` mocks ``langgraph.*`` modules globally
for unit-test speed. Each test in this module re-imports the real
LangGraph from inside the test body (after clearing the mock from
``sys.modules``) so the real checkpoint layer is exercised. The
daemon modules are re-imported too so they bind to the real LangGraph.

This test does NOT require a live OpenCode server — it only needs
the file-backed ``AsyncSqliteSaver`` (provided by ``aiosqlite``, which
is in the dev dependency group). It lives in ``tests/integration/`` so
it can re-import the real LangGraph; pytest's default
``addopts = '-m "not integration and not postgres"'`` does NOT skip
this test (no ``integration`` mark).
"""

from __future__ import annotations

import sys
from pathlib import Path


# Snapshot the conftest-installed mocks so each test can temporarily
# clear them, run against the real LangGraph, then restore.
_MOCKED_LANGGRAPH_KEYS = (
    "langgraph",
    "langgraph.graph",
    "langgraph.graph.state",
    "langgraph.prebuilt",
    "langgraph.constants",
    "langgraph.checkpoint",
    "langgraph.checkpoint.sqlite",
    "langgraph.checkpoint.sqlite.aio",
)
_DAEMON_KEYS_TO_REIMPORT = (
    "daemon.persistence",
    "daemon.compaction",
    "daemon.graph",
    "daemon.manager",
)


class _RealLangGraph:
    """Context manager that swaps the conftest's mocked langgraph
    modules for the real ones around a block of test code.

    Usage::

        with _RealLangGraph():
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
            from daemon.persistence import get_instance_messages
            ...

    The context manager clears ``langgraph.*`` from ``sys.modules``
    on enter (so subsequent imports hit the real package), and
    restores the conftest's mocks on exit so subsequent unit tests
    see the original mocked state.
    """

    def __enter__(self):
        self._original_modules = {
            k: sys.modules[k] for k in _MOCKED_LANGGRAPH_KEYS if k in sys.modules
        }
        for key in _MOCKED_LANGGRAPH_KEYS:
            if key in sys.modules:
                del sys.modules[key]
        # Also clear daemon modules so they re-bind to the real
        # LangGraph on the next import inside the test. Snapshot them
        # FIRST: __exit__ restores the SAME identities instead of
        # deleting — deletion splits module identity for later
        # patch("daemon.persistence.X") calls (collected test modules
        # hold from-import bindings to the originals).
        self._saved_daemon_modules = {
            k: sys.modules[k]
            for k in _DAEMON_KEYS_TO_REIMPORT
            if k in sys.modules
        }
        for key in _DAEMON_KEYS_TO_REIMPORT:
            if key in sys.modules:
                del sys.modules[key]
        return self

    def __exit__(self, exc_type, exc, tb):
        # Restore the conftest mocks.
        for key in _MOCKED_LANGGRAPH_KEYS:
            if key in sys.modules:
                del sys.modules[key]
        for key, mod in self._original_modules.items():
            sys.modules[key] = mod
        # Restore the ORIGINAL daemon module objects (same identities)
        # so later patch("daemon.persistence...") calls hit the object
        # the collected test modules bind to. Keys absent at __enter__
        # are dropped (fresh mock-backed import on next use).
        for key in _DAEMON_KEYS_TO_REIMPORT:
            sys.modules.pop(key, None)
        for key, mod in self._saved_daemon_modules.items():
            sys.modules[key] = mod
        return False


# ---------------------------------------------------------------------------
# Probe: real-checkpoint round-trip
# ---------------------------------------------------------------------------


def test_w1_markers_survive_real_checkpoint_round_trip(tmp_path):
    """W1 END-TO-END — markers SURVIVE the LangGraph checkpoint
    round-trip and are surfaced by ``get_instance_messages`` /
    ``serialize_message`` on the read path.

    Acceptance test for the additive wire-up. This test would have
    FAILED on the W1 INTERIM branch (the ``source`` key would be
    missing on the read-back message, and the ``injected_message``
    flag would be missing from a context message after the
    checkpoint round-trip).

    Writes four HumanMessages — one per W1 marker profile — to a
    real LangGraph checkpoint, then reads them back via the unmocked
    ``get_instance_messages`` path. Every marker MUST survive.

    Sync wrapper around the async probe (avoids the conftest fixture
    leaking the cleared sys.modules into a teardown task that hangs).
    """
    import asyncio

    with _RealLangGraph():
        import aiosqlite
        from langchain_core.messages import HumanMessage
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        from langgraph.graph import END, START, MessagesState, StateGraph

        from daemon.persistence import get_instance_messages

        async def _probe():
            db_path = tmp_path / "w1_probe.db"
            conn = await aiosqlite.connect(str(db_path))
            saver = AsyncSqliteSaver(conn)
            await saver.setup()

            g = StateGraph(MessagesState)
            # Identity node — no real LLM call. We are exercising the
            # checkpoint persistence boundary only.
            g.add_node("agent", lambda s: s)
            g.add_edge(START, "agent")
            g.add_edge("agent", END)
            compiled = g.compile(checkpointer=saver)

            iid = "w1-probe-instance"
            cfg = {"configurable": {"thread_id": iid}}

            # Four HumanMessages, each with a distinct W1 marker
            # profile:
            #   1. context message   — injected_message + context_kind
            #   2. FIFO-injected     — injected_message + source
            #   3. report-injected   — injected_message + report source
            #   4. plain user turn   — NO markers (additive contract)
            ctx_msg = HumanMessage(
                content="[SYSTEM CONTEXT: Project]\n\nbody",
                additional_kwargs={
                    "injected_message": True,
                    "context_kind": "project",
                },
            )
            fifo_msg = HumanMessage(
                content="fifo-injected turn",
                additional_kwargs={
                    "injected_message": True,
                    "source": "internal_agent:caller-1234",
                },
            )
            report_msg = HumanMessage(
                content="report content",
                additional_kwargs={
                    "injected_message": True,
                    "source": "internal_report:child-5678",
                },
            )
            plain_msg = HumanMessage(content="plain user turn")

            await compiled.aupdate_state(
                cfg,
                {"messages": [ctx_msg, fifo_msg, report_msg, plain_msg]},
                as_node="agent",
            )

            # Real unmocked retrieval path — exercises the real
            # ``get_instance_messages`` + ``serialize_message`` (the
            # W1 batch surfaces injected_message / context_kind /
            # source). ``manager=None`` skips the synthetic
            # system-prompt injection path (which requires a real
            # InstanceManager + prompt cache).
            ms = await get_instance_messages(saver, iid, manager=None)
            await conn.close()
            return ms

        ms = asyncio.run(_probe())

    # Index by content for content-based assertions (id-based lookup
    # is brittle across the real-checkpoint probe; the test cares
    # about marker survival, not id stability).
    serialized_by_content = {m["content"]: m for m in ms}

    # 1. Context message — injected_message + context_kind surfaced;
    #    source absent (additive).
    s_ctx = serialized_by_content.get("[SYSTEM CONTEXT: Project]\n\nbody")
    assert s_ctx is not None, (
        f"context msg missing in {sorted(serialized_by_content)}"
    )
    assert s_ctx["injected_message"] is True
    assert s_ctx["context_kind"] == "project"
    assert "source" not in s_ctx

    # 2. FIFO-injected — injected_message + source surfaced;
    #    context_kind absent.
    s_fifo = serialized_by_content.get("fifo-injected turn")
    assert s_fifo is not None
    assert s_fifo["injected_message"] is True
    assert s_fifo["source"] == "internal_agent:caller-1234"
    assert "context_kind" not in s_fifo

    # 3. Report-injected — both injected_message + report source.
    s_report = serialized_by_content.get("report content")
    assert s_report is not None
    assert s_report["injected_message"] is True
    assert s_report["source"] == "internal_report:child-5678"

    # 4. Plain — NO marker keys (additive contract: no spurious
    #    keys on messages that never carried markers).
    s_plain = serialized_by_content.get("plain user turn")
    assert s_plain is not None
    assert "injected_message" not in s_plain
    assert "context_kind" not in s_plain
    assert "source" not in s_plain


def test_w1_d12_structured_filter_drops_descendant_injected_context(tmp_path):
    """W1 END-TO-END D12 — when ``get_instance_messages`` returns a
    descendant's messages with the structured ``injected_message=True``
    marker surfaced, the descendant filter (via the real
    ``_filter_subtree_messages``) MUST drop them.

    Closes the W1 INTERIM-RESOLUTION loop end-to-end: the markers
    survive the checkpoint round-trip AND the descendant filter uses
    them as the authoritative drop signal (no content-prefix fallback).
    """
    import asyncio

    with _RealLangGraph():
        import aiosqlite
        from langchain_core.messages import HumanMessage
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        from langgraph.graph import END, START, MessagesState, StateGraph

        from daemon.persistence import get_instance_messages
        from daemon.tools.instance import _filter_subtree_messages

        async def _probe():
            db_path = tmp_path / "w1_d12.db"
            conn = await aiosqlite.connect(str(db_path))
            saver = AsyncSqliteSaver(conn)
            await saver.setup()

            g = StateGraph(MessagesState)
            g.add_node("agent", lambda s: s)
            g.add_edge(START, "agent")
            g.add_edge("agent", END)
            compiled = g.compile(checkpointer=saver)

            iid = "w1-d12-descendant"
            cfg = {"configurable": {"thread_id": iid}}

            # Descendant's checkpoint contains: (a) a system-context
            # message (injected + context_kind) — MUST be dropped,
            # (b) a legitimate user message — MUST be kept.
            ctx_msg = HumanMessage(
                content="[SYSTEM CONTEXT: Task Context]\n## Secret task",
                additional_kwargs={
                    "injected_message": True,
                    "context_kind": "task_context",
                },
            )
            legit_user_msg = HumanMessage(
                content="a regular descendant turn"
            )

            await compiled.aupdate_state(
                cfg,
                {"messages": [ctx_msg, legit_user_msg]},
                as_node="agent",
            )

            ms = await get_instance_messages(saver, iid, manager=None)
            await conn.close()
            return ms

        ms = asyncio.run(_probe())

    # The structured filter sees the messages with the surfaced
    # ``injected_message`` key (thanks to W1 ``serialize_message``)
    # and drops the descendant's context block.
    filtered = _filter_subtree_messages(ms, is_descendant=True)
    contents = [m.get("content") for m in filtered]

    assert "a regular descendant turn" in contents, (
        f"legitimate user msg must survive; got {contents}"
    )
    assert not any(
        "SYSTEM CONTEXT" in (c or "") for c in contents
    ), f"descendant context block must be dropped; got {contents}"


def test_w1_d12_caller_keeps_own_injected_context(tmp_path):
    """W1 D12 caller-keep counter-test — when the SAME-shaped
    injected-context message lives on the caller's own instance, the
    structured filter MUST keep it (``is_descendant=False`` branch).

    Closes the symmetry: caller sees its own injections, descendants
    do not.
    """
    import asyncio

    with _RealLangGraph():
        import aiosqlite
        from langchain_core.messages import HumanMessage
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        from langgraph.graph import END, START, MessagesState, StateGraph

        from daemon.persistence import get_instance_messages
        from daemon.tools.instance import _filter_subtree_messages

        async def _probe():
            db_path = tmp_path / "w1_d12_caller.db"
            conn = await aiosqlite.connect(str(db_path))
            saver = AsyncSqliteSaver(conn)
            await saver.setup()

            g = StateGraph(MessagesState)
            g.add_node("agent", lambda s: s)
            g.add_edge(START, "agent")
            g.add_edge("agent", END)
            compiled = g.compile(checkpointer=saver)

            iid = "w1-d12-caller"
            cfg = {"configurable": {"thread_id": iid}}

            own_ctx = HumanMessage(
                content="[SYSTEM CONTEXT: Task Context]\n## Callers own context",
                additional_kwargs={
                    "injected_message": True,
                    "context_kind": "task_context",
                },
            )
            await compiled.aupdate_state(
                cfg, {"messages": [own_ctx]}, as_node="agent"
            )

            ms = await get_instance_messages(saver, iid, manager=None)
            await conn.close()
            return ms

        ms = asyncio.run(_probe())

    # Caller branch — ``is_descendant=False``. Callers see their own
    # injections; the filter does NOT drop ``injected_message=True``
    # messages on the caller.
    filtered = _filter_subtree_messages(ms, is_descendant=False)
    contents = [m.get("content") for m in filtered]

    assert any(
        "Callers own context" in (c or "") for c in contents
    ), f"caller's own context must be visible; got {contents}"
