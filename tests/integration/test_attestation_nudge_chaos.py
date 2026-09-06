"""Mode: enforce — nudge survives a checkpoint/restart boundary (AC-4.2).

This is an in-process process-shed/restart simulation: the gate node is invoked
to completion, its output is written to the real LangGraph checkpointer, then
a newly compiled graph resumes from that checkpoint with a fresh model.  It
avoids killing the pytest process (or touching a live daemon) while exercising
the exact node-boundary persistence contract that an actual SIGKILL/restart
must preserve.
"""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool

from tests.support.scripted_chat_model import ScriptedChatModel

INSTANCE_ID = "attestation-leader-e2e"
NUDGE_TEXT = (
    "The work is not yet finished — check current progress "
    "(tasks/children status) and continue. Reminder: when "
    "— and only when — the work is truly complete, you MUST "
    "call the attest_completion tool before finishing; "
    "completions without that call are premature and will be "
    "blocked again. Attestation is a SEPARATE step: FIRST "
    "deliver your full detailed final report as its own "
    "message, THEN call attest_completion alone as a "
    "subsequent step — never bundle the report into the "
    "attestation tool-call message."
)


@pytest.fixture(autouse=True)
def _enforce_mode(monkeypatch):
    monkeypatch.setenv("ENSEMBLE_LEADER_ATTESTATION_MODE", "enforce")
    monkeypatch.setenv("WATCHOVER_ENABLED", "false")


def _settings():
    from daemon.services.attestation_gate import GateSettings

    return GateSettings(mode="enforce", window=3, deny_bound=3)


@tool
def attest_completion() -> dict:
    """Record a fresh attestation after the simulated restart."""
    return {"attested": True}


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
            system_prompt="scripted chaos test",
            user_language="Auto",
            language_check_enabled=False,
            manager=manager,
            graph_config={"configurable": {"thread_id": INSTANCE_ID}},
            attestation_enabled=True,
        )


@pytest.mark.asyncio
async def test_nudge_checkpoint_survives_restart_and_leads_to_attested_allow(
    real_graph_module,
    memory_saver,
    file_sqlite_engine,
    attestation_repository,
    attestation_manager_factory,
    caplog,
):
    repo, _instance = attestation_repository
    manager = attestation_manager_factory(file_sqlite_engine, repo)
    from daemon.services.attestation_gate import build_gate_config

    gate = real_graph_module.create_attestation_gate_node(
        build_gate_config(
            INSTANCE_ID, _settings(), attestation_enabled=True, scope_applicable=True
        ),
        _settings(),
        manager,
        INSTANCE_ID,
        denied_count_getter=lambda: 0,
        ledger=repo,
    )
    checkpoint_input = {
        "messages": [
            HumanMessage(content="mission"),
            AIMessage(content="hallucinated completion"),
        ]
    }
    gate_result = await gate(
        checkpoint_input,
        config={"configurable": {"thread_id": INSTANCE_ID}},
    )
    assert gate_result["attestation_route"] == "agent"
    assert gate_result["messages"][-1].content == NUDGE_TEXT
    assert gate_result["attestation_nudge_denied_count"] == 1
    assert repo.get_attestation_denied_count(INSTANCE_ID) == 1

    # Persist exactly what the post-node checkpoint would contain, then throw
    # away the compiled graph ("process restart") and compile a fresh one.
    seed_graph = _build(
        real_graph_module,
        ScriptedChatModel(responses=[AIMessage(content="never invoked")], i=0),
        manager,
        memory_saver,
    )
    config = {"configurable": {"thread_id": INSTANCE_ID}}
    await seed_graph.aupdate_state(
        config,
        {
            "messages": checkpoint_input["messages"] + gate_result["messages"],
            "attestation_route": gate_result["attestation_route"],
            "attestation_nudge_denied_count": gate_result[
                "attestation_nudge_denied_count"
            ],
        },
        as_node="attestation_gate",
    )
    del seed_graph

    resumed_model = ScriptedChatModel(
        responses=[
            AIMessage(
                content="attesting after restart",
                tool_calls=[
                    {
                        "name": "attest_completion",
                        "args": {},
                        "id": "restart-attest",
                    }
                ],
            ),
            AIMessage(content="finished after restart"),
        ],
        i=0,
    )
    restarted_graph = _build(real_graph_module, resumed_model, manager, memory_saver)
    with caplog.at_level(logging.INFO):
        state = await restarted_graph.ainvoke(
            {"messages": [HumanMessage(content="resume mission")]},
            config={**config, "recursion_limit": 30},
        )

    nudges = [
        m
        for m in state["messages"]
        if isinstance(m, HumanMessage) and m.additional_kwargs.get("attestation_nudge")
    ]
    assert len(nudges) == 1
    assert nudges[0].content == NUDGE_TEXT
    assert "decision=allowed" in caplog.text
    assert repo.get_attestation_denied_count(INSTANCE_ID) == 0
    manager.enqueue_message.assert_not_called()
    assert resumed_model.calls_made == 2
