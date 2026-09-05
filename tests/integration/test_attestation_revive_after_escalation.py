"""Mode: enforce — reset triggers and post-revive mission isolation.

The fresh-episode reset is exercised through the real
``InstanceMessagingService._prepare_enqueued_message`` prelude, not a mocked
repository reset, so the trigger-3 path's row mutation is covered.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool

from daemon.services.instance_messaging import InstanceMessagingService
from daemon.write_pause_guard import WritePauseGuard
from tests.support.scripted_chat_model import ScriptedChatModel

INSTANCE_ID = "attestation-leader-e2e"


@pytest.fixture(autouse=True)
def _enforce_mode(monkeypatch):
    monkeypatch.setenv("ENSEMBLE_LEADER_ATTESTATION_MODE", "enforce")
    monkeypatch.setenv("WATCHOVER_ENABLED", "false")


def _settings():
    from daemon.services.attestation_gate import GateSettings

    return GateSettings(mode="enforce", window=3, deny_bound=3)


def _build(graph_module, model, manager, checkpointer):
    graph_module.build_instance_llms = lambda **_: (model, model)
    with patch(
        "daemon.services.attestation_gate.resolve_gate_settings",
        return_value=_settings(),
    ):
        return graph_module.build_instance_graph(
            tools=[attest_completion],
            checkpointer=checkpointer,
            llm_config={"model": "scripted-test", "api_key": "test"},
            system_prompt="scripted reset test",
            user_language="Auto",
            language_check_enabled=False,
            manager=manager,
            graph_config={"configurable": {"thread_id": INSTANCE_ID}},
            attestation_enabled=True,
        )


@tool
def attest_completion() -> dict:
    """Attestation tool used by the second scripted mission."""
    return {"attested": True}


def _nudge_count(messages) -> int:
    return sum(
        isinstance(m, HumanMessage) and bool(m.additional_kwargs.get("attestation_nudge"))
        for m in messages
    )


@pytest.mark.asyncio
async def test_terminal_reset_and_fresh_episode_rearm_next_mission(
    real_graph_module,
    memory_saver,
    file_sqlite_engine,
    attestation_repository,
    attestation_manager_factory,
    caplog,
):
    repo, _instance = attestation_repository
    manager = attestation_manager_factory(file_sqlite_engine, repo)

    # Mission 1: three nudges followed by terminal escalation.
    first_model = ScriptedChatModel(
        responses=[AIMessage(content=f"missed {i}") for i in range(4)], i=0
    )
    first_graph = _build(real_graph_module, first_model, manager, memory_saver)
    with caplog.at_level(logging.INFO):
        await first_graph.ainvoke(
            {"messages": [HumanMessage(content="mission one")]},
            config={"configurable": {"thread_id": INSTANCE_ID}, "recursion_limit": 30},
        )
    assert repo.get(INSTANCE_ID).completion_gate_escalated is True
    assert repo.get_attestation_denied_count(INSTANCE_ID) == 0

    # The production fresh-episode path sees a user message (priority=1,
    # HUMAN) and atomically clears the terminal ledger burden.
    repo.transition_status_if(INSTANCE_ID, "completed", ("idle", "running"))
    messaging_manager = SimpleNamespace(
        engine=file_sqlite_engine,
        write_guard=WritePauseGuard(),
        _deferred_question_pause=set(),
        _instance_repository=repo,
    )
    messaging_service = InstanceMessagingService(
        manager=messaging_manager,
        cancellation_service=SimpleNamespace(is_shutting_down=False),
    )
    messaging_service._prepare_enqueued_message(
        INSTANCE_ID,
        "fresh mission",
        source="user",
        priority=1,
        images=None,
        metadata=None,
    )
    rearmed = repo.get(INSTANCE_ID)
    assert rearmed.status == "running"
    assert rearmed.attestation_denied_count == 0
    assert rearmed.completion_gate_escalated is False

    # Mission 2 starts unburdened. It must need a full bound of misses
    # again, rather than escalating on its first deny.
    second_model = ScriptedChatModel(
        responses=[
            AIMessage(content="hallucinated second mission"),
            AIMessage(
                content="attesting",
                tool_calls=[
                    {
                        "name": "attest_completion",
                        "args": {},
                        "id": "reset-attest",
                    }
                ],
            ),
            AIMessage(content="finished second mission"),
        ],
        i=0,
    )
    from langgraph.checkpoint.memory import MemorySaver

    second_graph = _build(real_graph_module, second_model, manager, MemorySaver())
    with caplog.at_level(logging.INFO):
        state = await second_graph.ainvoke(
            {"messages": [HumanMessage(content="mission two")]},
            config={"configurable": {"thread_id": INSTANCE_ID}, "recursion_limit": 30},
        )

    assert _nudge_count(state["messages"]) == 1
    assert repo.get_attestation_denied_count(INSTANCE_ID) == 0
    assert "decision=terminal_after_bound" not in caplog.text.split(
        "event=gate_terminal_after_bound", 1
    )[-1]
    manager.enqueue_message.assert_not_called()
