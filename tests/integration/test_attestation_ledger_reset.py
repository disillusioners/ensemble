"""Mode: enforce — per-mission ledger reset and reset-on-attested-allow."""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool

from tests.support.scripted_chat_model import ScriptedChatModel

INSTANCE_ID = "attestation-leader-e2e"


@pytest.fixture(autouse=True)
def _enforce_mode(monkeypatch):
    monkeypatch.setenv("ENSEMBLE_LEADER_ATTESTATION_MODE", "enforce")
    monkeypatch.setenv("WATCHOVER_ENABLED", "false")


@tool
def attest_completion() -> dict:
    """Record a scripted mission attestation."""
    return {"attested": True}


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
            system_prompt="reset matrix",
            user_language="Auto",
            language_check_enabled=False,
            manager=manager,
            graph_config={"configurable": {"thread_id": INSTANCE_ID}},
            attestation_enabled=True,
        )


def _script():
    return ScriptedChatModel(
        responses=[
            AIMessage(content="plain completion"),
            AIMessage(
                content="attest",
                tool_calls=[
                    {
                        "name": "attest_completion",
                        "args": {},
                        "id": "reset-script-attest",
                    }
                ],
            ),
            AIMessage(content="done"),
        ],
        i=0,
    )


@pytest.mark.asyncio
async def test_attested_allow_resets_and_next_mission_starts_clean(
    real_graph_module,
    file_sqlite_engine,
    attestation_repository,
    attestation_manager_factory,
    caplog,
):
    from langgraph.checkpoint.memory import MemorySaver

    repo, _instance = attestation_repository
    manager = attestation_manager_factory(file_sqlite_engine, repo)

    first_graph = _build(real_graph_module, _script(), manager, MemorySaver())
    with caplog.at_level(logging.INFO):
        await first_graph.ainvoke(
            {"messages": [HumanMessage(content="mission a")]},
            config={"configurable": {"thread_id": INSTANCE_ID}, "recursion_limit": 20},
        )
    assert repo.get_attestation_denied_count(INSTANCE_ID) == 0

    second_graph = _build(real_graph_module, _script(), manager, MemorySaver())
    with caplog.at_level(logging.INFO):
        await second_graph.ainvoke(
            {"messages": [HumanMessage(content="mission b")]},
            config={"configurable": {"thread_id": INSTANCE_ID}, "recursion_limit": 20},
        )

    # The second mission sees the DB value, not residue from mission A.
    assert repo.get_attestation_denied_count(INSTANCE_ID) == 0
    assert caplog.text.count("decision=denied") == 2
    assert caplog.text.count("decision=allowed") == 2
    manager.enqueue_message.assert_not_called()
