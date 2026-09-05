"""Mode: enforce — bound exhaustion is a single terminal escalation.

The first ``bound`` un-attested ends are denied with one nudge each.  The
``bound + 1``-th end is terminal: it sets the postmortem flag, resets the
counter in the same DB write, emits one escalation event, and must not create
a fourth nudge.
"""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from tests.support.scripted_chat_model import ScriptedChatModel

NUDGE_TEXT = "The work is not yet finished — check current progress and continue."
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
            tools=[],
            checkpointer=checkpointer,
            llm_config={"model": "scripted-test", "api_key": "test"},
            system_prompt="scripted bound test",
            user_language="Auto",
            language_check_enabled=False,
            manager=manager,
            graph_config={"configurable": {"thread_id": INSTANCE_ID}},
            attestation_enabled=True,
        )


def _nudge_count(messages) -> int:
    return sum(
        isinstance(m, HumanMessage) and bool(m.additional_kwargs.get("attestation_nudge"))
        for m in messages
    )


@pytest.mark.asyncio
async def test_bound_plus_one_escalates_once_without_fourth_nudge(
    real_graph_module,
    memory_saver,
    file_sqlite_engine,
    attestation_repository,
    attestation_manager_factory,
    caplog,
):
    repo, _instance = attestation_repository
    manager = attestation_manager_factory(file_sqlite_engine, repo)
    model = ScriptedChatModel(
        responses=[AIMessage(content=f"hallucinated {i}") for i in range(4)],
        i=0,
    )
    graph = _build(real_graph_module, model, manager, memory_saver)

    with caplog.at_level(logging.INFO):
        state = await graph.ainvoke(
            {"messages": [HumanMessage(content="do work")]},
            config={
                "configurable": {"thread_id": INSTANCE_ID},
                "recursion_limit": 30,
            },
        )

    assert _nudge_count(state["messages"]) == 3
    assert caplog.text.count("decision=denied") == 3
    assert caplog.text.count("decision=terminal_after_bound") == 1
    assert caplog.text.count("event=leader_completion_gate_terminal_after_bound") == 1
    assert "decision=allowed" not in caplog.text

    row = repo.get(INSTANCE_ID)
    assert row.attestation_denied_count == 0
    assert row.completion_gate_escalated is True
    manager.enqueue_message.assert_not_called()
    assert model.calls_made == 4
