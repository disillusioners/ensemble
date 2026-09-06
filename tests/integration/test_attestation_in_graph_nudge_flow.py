"""FLAGSHIP: enforce-mode deny → nudge → route-back → attest → allow.

Mode: ``enforce`` for this test module.  The graph is real (the repository's
root LangGraph stubs are evicted by the shared fixture), while the LLM is a
deterministic in-process script.  The test uses a file-backed SQLite instance
row so the ledger and queue tables are real read-back surfaces, not AsyncMock
blind spots.
"""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from sqlmodel import Session, select, func

from daemon.repositories.message_queue.models import MessageQueue
from daemon.repositories.task.models import Task
from tests.support.scripted_chat_model import ScriptedChatModel

NUDGE_TEXT = (
    "The work is not yet finished — check current progress "
    "(tasks/children status) and continue. Reminder: when "
    "— and only when — the work is truly complete, you MUST "
    "call the attest_completion tool before finishing; "
    "completions without that call are premature and will be "
    "blocked again."
)
INSTANCE_ID = "attestation-leader-e2e"


@tool
def attest_completion() -> dict:
    """The real leader attestation tool used by the graph's ToolNode."""
    return {"attested": True, "timestamp": "2026-09-05T00:00:00+00:00"}


@pytest.fixture(autouse=True)
def _enforce_mode(monkeypatch):
    """Pin this module's mode explicitly; no mode mixing in the flagship."""
    monkeypatch.setenv("ENSEMBLE_LEADER_ATTESTATION_MODE", "enforce")
    monkeypatch.setenv("WATCHOVER_ENABLED", "false")


def _settings():
    from daemon.services.attestation_gate import GateSettings

    return GateSettings(mode="enforce", window=3, deny_bound=3)


def _build_graph(graph_module, model, manager, checkpointer):
    graph_module.build_instance_llms = lambda **_: (model, model)
    with patch(
        "daemon.services.attestation_gate.resolve_gate_settings",
        return_value=_settings(),
    ):
        return graph_module.build_instance_graph(
            tools=[attest_completion],
            checkpointer=checkpointer,
            llm_config={"model": "scripted-test", "api_key": "test"},
            system_prompt="scripted attestation leader",
            user_language="Auto",
            language_check_enabled=False,
            manager=manager,
            graph_config={"configurable": {"thread_id": INSTANCE_ID}},
            attestation_enabled=True,
        )


def _queue_counts(engine) -> tuple[int, int]:
    with Session(engine) as session:
        message_count = int(
            session.scalar(
                select(func.count()).select_from(MessageQueue).where(
                    MessageQueue.instance_id == INSTANCE_ID
                )
            )
            or 0
        )
        task_count = int(
            session.scalar(
                select(func.count()).select_from(Task).where(
                    Task.instance_id == INSTANCE_ID
                )
            )
            or 0
        )
    return message_count, task_count


def _seed_baseline_queues(engine) -> None:
    with Session(engine) as session:
        session.add(MessageQueue(instance_id=INSTANCE_ID, content="baseline"))
        session.add(Task(instance_id=INSTANCE_ID, task_type="process_message"))
        session.commit()


def _nudges(messages):
    return [
        m
        for m in messages
        if isinstance(m, HumanMessage) and m.additional_kwargs.get("attestation_nudge")
    ]


async def _run(graph):
    return await graph.ainvoke(
        {"messages": [HumanMessage(content="complete the mission")]},
        config={
            "configurable": {"thread_id": INSTANCE_ID},
            "recursion_limit": 30,
        },
    )


@pytest.mark.asyncio
async def test_flagship_deny_nudge_routes_back_and_attests(
    real_graph_module,
    memory_saver,
    file_sqlite_engine,
    attestation_repository,
    attestation_manager_factory,
    caplog,
):
    """AC-E2E-1: no durable delivery is created while the nudge is in state."""
    repo, _instance = attestation_repository
    manager = attestation_manager_factory(file_sqlite_engine, repo)
    _seed_baseline_queues(file_sqlite_engine)
    before_messages, before_tasks = _queue_counts(file_sqlite_engine)

    model = ScriptedChatModel(
        responses=[
            AIMessage(content="Hallucinated completion."),
            AIMessage(
                content="Attesting now.",
                tool_calls=[
                    {
                        "name": "attest_completion",
                        "args": {},
                        "id": "attest-e2e-1",
                    }
                ],
            ),
            AIMessage(content="Finished after the continuation nudge."),
        ],
        i=0,
    )
    graph = _build_graph(real_graph_module, model, manager, memory_saver)

    with caplog.at_level(logging.INFO, logger="daemon.services.attestation_gate"):
        final_state = await _run(graph)

    messages = final_state["messages"]
    nudges = _nudges(messages)
    assert len(nudges) == 1
    assert nudges[0].content == NUDGE_TEXT
    assert nudges[0].additional_kwargs == {
        "attestation_nudge": True,
        "attestation_nudge_denied_count": 1,
    }
    assert final_state["attestation_nudge_denied_count"] == 1
    assert any(isinstance(m, AIMessage) and m.tool_calls for m in messages)
    assert final_state["messages"][-1].content == "Finished after the continuation nudge."

    # The decision log contains one deny and one allow, never escalation.
    assert caplog.text.count("decision=denied") == 1
    assert caplog.text.count("decision=allowed") == 1
    assert "decision=terminal_after_bound" not in caplog.text

    # Explicit MVP negative lock: no manager delivery, and no queue write.
    manager.enqueue_message.assert_not_called()
    after_messages, after_tasks = _queue_counts(file_sqlite_engine)
    assert (after_messages, after_tasks) == (before_messages, before_tasks)
    assert repo.get_attestation_denied_count(INSTANCE_ID) == 0

    # The graph did not leave a stale ledger burden after the attested allow.
    row = repo.get(INSTANCE_ID)
    assert row.completion_gate_escalated is False
