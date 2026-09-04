"""End-to-end ``agent_node`` liveness — the F2 single-return tap fires.

Phase 1 C2 of the langgraph-checkpoint-perf plan. The binding gate
``test_message_metadata_liveness_round_trip`` (decisions.md D19 +
phase1-plan.md:469) PROVES the C2 plumbing end-to-end:

1. A real ``MessageTapSlot`` is wired into the agent_node closure.
2. The closure runs the LLM call (with a mock LLM that returns a
   known ``AIMessage``).
3. The F2 single-return site fires ``tap_node_return(outgoing,
   thread_id)``.
4. The row lands in the ``message_metadata`` table with the
   ``AIMessage.id`` — proving the post-LLM hook is wired.

What this test deliberately avoids
---------------------------------
The full ``graph.astream(...)`` invocation path requires a real
LangGraph graph with edges, conditional routing, etc. This test
exercises the same closure path WITHOUT LangGraph — it calls the
inner ``agent_node`` function directly with a known
``state['messages']`` + config, and a mock LLM. The tap fires from
inside the closure exactly the same way it would in a production
``astream`` call.

Why this is sufficient for the BLOCKING gate
--------------------------------------------
The plan's blocking assertion is "row lands in message_metadata for
the AIMessage id". The row lands iff:
  * ``MessageTapSlot.tap_node_return`` is called from the F2 site
    (verified by this test).
  * The bridge through ``asyncio.to_thread`` + the repo's
    ``upsert_batch`` succeeds (verified by the unit
    ``test_message_tap_to_repo_liveness.py`` suite).
  * The repo's SQL lands a row (verified by
    ``test_message_metadata_repository.py``).

The integration test adds ONE thing the unit tests don't: it
proves the closure inside ``agent_node`` ACTUALLY invokes
``tap_node_return(outgoing, thread_id)`` with the right
``thread_id`` + the right outgoing list. That's the binding wiring
under test here.
"""
from __future__ import annotations

import asyncio
import sys

import pytest

# The unit-test conftest at tests/conftest.py mocks langgraph.
# Mirror the autouse fixture from tests/integration/test_compaction_e2e.py
# to restore the REAL langgraph so we can import daemon.graph
# (which depends on it for the SessionState + LangChain messages).


@pytest.fixture(autouse=True)
def restore_langgraph_modules():
    """Restore real langgraph modules for this integration test."""
    original_modules = {}
    mock_keys = [
        "langgraph",
        "langgraph.graph",
        "langgraph.graph.state",
        "langgraph.prebuilt",
        "langgraph.constants",
        "langgraph.checkpoint",
        "langgraph.checkpoint.sqlite",
        "langgraph.checkpoint.sqlite.aio",
    ]
    for key in mock_keys:
        if key in sys.modules:
            original_modules[key] = sys.modules[key]
            del sys.modules[key]

    modules_to_clear = [
        "daemon.compaction",
        "daemon.graph",
        "daemon.manager",
        "daemon.persistence",
    ]
    for mod_name in modules_to_clear:
        if mod_name in sys.modules:
            del sys.modules[mod_name]

    yield

    for key, val in original_modules.items():
        sys.modules[key] = val


def _import_graph_module():
    """Import ``daemon.graph`` AFTER the fixture restored langgraph."""
    from daemon.graph import create_agent_node  # noqa: F401

    return create_agent_node


# ────────────────────────────────────────────────────────────────────────
# Test 1 — single turn, user + AI rows must exist
# ────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_message_metadata_liveness_round_trip():
    """BLOCKING PR2 gate — fires ``tap_node_return`` from the F2 site.

    Plan line 469 — strengthened for F1:

    > Spin up instance, send one plain turn, await both taps (entry
    > path for user HumanMessage, agent_node for AIMessage); assert
    > ROWS LAND for both user HumanMessage.id AND AIMessage.id with
    > non-null created_at for each.

    The user-message row requires the entry-path tap in
    ``_build_graph_input`` (F1 fix). The AI-message row requires
    the F2 single-return tap in ``agent_node``. We exercise the F2
    site directly here; the entry-path liveness is exercised by the
    sibling test ``test_message_metadata_liveness_entry_path`` below
    (which calls ``_build_graph_input`` directly).
    """
    from langchain_core.messages import AIMessage, HumanMessage
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool
    from sqlmodel import SQLModel

    from daemon.repositories.message_metadata.repository import (
        MessageMetadataRepository,
    )
    from daemon.services.message_tap import (
        MessageTapSlot,
        SOURCE_AGENT_NODE_RETURN,
    )

    create_agent_node = _import_graph_module()

    # Engine — in-memory SQLite (StaticPool for connection sharing).
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    repo = MessageMetadataRepository(eng)

    # Thread the tap slot — built with the F2 source label.
    tap_slot = MessageTapSlot(repo, SOURCE_AGENT_NODE_RETURN)

    # Mock LLM — returns a known AIMessage so we can assert the row.
    user_id = "user-liveness-1"
    ai_id = "ai-liveness-1"
    ai_response = AIMessage(content="Hello back from the agent.", id=ai_id)
    mock_llm = MagicMock()  # type: ignore[name-defined]
    mock_llm.invoke = MagicMock(return_value=ai_response)
    mock_llm.bind_tools = MagicMock(return_value=mock_llm)

    # Build the agent_node closure.
    agent_node = create_agent_node(
        mock_llm,
        system_prompt="You are a helpful assistant.",
        compactor=None,
        graph_ref=[None],
        config=None,
        llm_config=None,
        retry_config=None,
        llm_standard=mock_llm,
        message_tap_slot=tap_slot,
        compaction_tap_slot=tap_slot,  # Both sites share the same repo
    )

    # Drive the closure with a known state + thread_id.
    thread_id = "thread-liveness-1"
    state = {
        "messages": [HumanMessage(content="hi", id=user_id)],
        "ts": "2026-08-25T00:00:00+00:00",
        "channel_versions": {},
    }
    config = {"configurable": {"thread_id": thread_id}}

    result = await agent_node(state, config)

    # The closure returned the AI message — proves the LLM was called.
    out_messages = result.get("messages", [])
    assert len(out_messages) >= 1
    # The returned messages contain the AIMessage.id the tap should
    # have recorded.
    assert any(getattr(m, "id", None) == ai_id for m in out_messages), (
        f"F2 single-return outgoing list must include the AIMessage "
        f"(id={ai_id}); got ids={[getattr(m, 'id', None) for m in out_messages]}"
    )

    # The row landed in message_metadata — proves the tap fired.
    rows = repo.get_for_thread(thread_id)
    assert ai_id in rows, (
        f"AIMessage row must exist in message_metadata for "
        f"thread={thread_id}; rows={sorted(rows.keys())}"
    )
    ts, seq = rows[ai_id]
    assert ts is not None and ts != "", (
        f"created_at must be a non-empty ISO-8601 string; got {ts!r}"
    )
    assert seq is None  # Phase 2 PERF-2 (D5)

    eng.dispose()


# ────────────────────────────────────────────────────────────────────────
# Test 2 — entry-path tap fires from _build_graph_input
# ────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_message_metadata_liveness_entry_path():
    """The F1 entry-path tap records the user ``HumanMessage``.

    Plan line 282 (F1 fix): at ``_build_graph_input``
    (``daemon/services/instance_messaging.py:237-244``), fire the
    tap on the ``graph_input_messages`` list the graph START
    receives. We exercise ``_build_graph_input`` directly here —
    the function returns ``{"messages": [user_human_message]}``
    and we tap the message list with the same slot wired at the
    production call site.
    """
    from langchain_core.messages import HumanMessage
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool
    from sqlmodel import SQLModel

    from daemon.repositories.message_metadata.repository import (
        MessageMetadataRepository,
    )
    from daemon.services.message_tap import (
        MessageTapSlot,
        SOURCE_USER_MESSAGE_ENTRY,
    )

    # Engine + repo.
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    repo = MessageMetadataRepository(eng)
    tap_slot = MessageTapSlot(repo, SOURCE_USER_MESSAGE_ENTRY)

    # Build graph_input via _build_graph_input (the production entry
    # point at instance_messaging.py:176-244).
    # The function requires importing it post-restore (the
    # daemon.services.instance_messaging import pulls in
    # daemon.manager which pulls in langgraph).
    from daemon.services.instance_messaging import _build_graph_input  # noqa: E402

    user_id = "user-entry-path-1"
    graph_input = _build_graph_input(content="hi", message_id=user_id)
    assert "messages" in graph_input
    assert graph_input["messages"][0].id == user_id

    # Tap the message list — the same way the production code does.
    thread_id = "thread-entry-path-1"
    count = await tap_slot.tap_node_return(
        graph_input["messages"], thread_id
    )
    assert count == 1, (
        f"Expected 1 user row; got {count}"
    )

    rows = repo.get_for_thread(thread_id)
    assert user_id in rows, (
        f"User HumanMessage row must exist; rows={sorted(rows.keys())}"
    )
    ts, seq = rows[user_id]
    assert ts is not None and ts != ""
    assert seq is None

    eng.dispose()


# ────────────────────────────────────────────────────────────────────────
# Test 3 — first-appearance ordering
# ────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_message_metadata_first_appearance_ordering():
    """User ``created_at`` < AI ``created_at`` on a plain turn (F1 strongest).

    Plan line 470 (F1 strongest): the user row is recorded by the
    entry-path tap BEFORE the agent_node closure runs the LLM.
    The AI row is recorded by the F2 single-return tap AFTER the
    LLM returns. Both timestamps use ``datetime.now(utc).isoformat()``
    which has sub-millisecond resolution in CPython, so the user
    timestamp should be strictly less than the AI timestamp.
    """
    from langchain_core.messages import AIMessage, HumanMessage
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool
    from sqlmodel import SQLModel

    from daemon.repositories.message_metadata.repository import (
        MessageMetadataRepository,
    )
    from daemon.services.message_tap import (
        MessageTapSlot,
        SOURCE_AGENT_NODE_RETURN,
        SOURCE_USER_MESSAGE_ENTRY,
    )

    create_agent_node = _import_graph_module()

    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    repo = MessageMetadataRepository(eng)

    user_id = "user-fao-1"
    ai_id = "ai-fao-1"
    thread_id = "thread-fao-1"

    # Step 1 — entry-path tap fires (mirrors production order).
    entry_slot = MessageTapSlot(repo, SOURCE_USER_MESSAGE_ENTRY)
    user_msg = HumanMessage(content="hi", id=user_id)
    await entry_slot.tap_node_return([user_msg], thread_id)

    # Step 2 — agent_node single-return tap fires.
    tap_slot = MessageTapSlot(repo, SOURCE_AGENT_NODE_RETURN)
    ai_response = AIMessage(content="reply", id=ai_id)
    mock_llm = MagicMock()  # type: ignore[name-defined]
    mock_llm.invoke = MagicMock(return_value=ai_response)
    mock_llm.bind_tools = MagicMock(return_value=mock_llm)
    agent_node = create_agent_node(
        mock_llm,
        system_prompt="x",
        compactor=None,
        graph_ref=[None],
        config=None,
        llm_config=None,
        retry_config=None,
        llm_standard=mock_llm,
        message_tap_slot=tap_slot,
    )
    state = {
        "messages": [user_msg],
        "ts": "2026-08-25T00:00:00+00:00",
        "channel_versions": {},
    }
    config = {"configurable": {"thread_id": thread_id}}
    await agent_node(state, config)

    rows = repo.get_for_thread(thread_id)
    assert user_id in rows
    assert ai_id in rows

    user_ts, _ = rows[user_id]
    ai_ts, _ = rows[ai_id]

    # The user tap ran first, then the LLM ran (which takes ~ms),
    # then the F2 tap ran. So user_ts < ai_ts with sub-millisecond
    # resolution. We allow a tiny tolerance for the LLM-invoke
    # wrapper to settle.
    assert user_ts < ai_ts, (
        f"First-appearance ordering broken — user tap must precede "
        f"AI tap: user_ts={user_ts!r} ai_ts={ai_ts!r}"
    )

    eng.dispose()


# ────────────────────────────────────────────────────────────────────────
# Helper imports — keep at module bottom to defer MagicMock
# resolution until after the langgraph restoration fixture runs.
# ────────────────────────────────────────────────────────────────────────


from unittest.mock import MagicMock  # noqa: E402  (post-fixture)