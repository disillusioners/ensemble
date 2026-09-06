"""Mode: enforce — stale pre-revive attestations do not satisfy a new mission.

Each sub-case seeds a real LangGraph checkpoint, then a new mission uses a
fresh scripted model.  The old attestation is either absent or outside the
last-N AIMessage window.
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
    """Record a fresh completion attestation for the scripted graph."""
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
            system_prompt="scripted stale watermark test",
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


def _ai(content: str) -> AIMessage:
    return AIMessage(content=content)


@pytest.mark.parametrize("seed", ["empty", "stale"])
@pytest.mark.asyncio
async def test_stale_attestation_watermark_does_not_cross_mission_boundary(
    seed,
    real_graph_module,
    memory_saver,
    file_sqlite_engine,
    attestation_repository,
    attestation_manager_factory,
    caplog,
):
    from langchain_core.messages import ToolMessage

    repo, _instance = attestation_repository
    manager = attestation_manager_factory(file_sqlite_engine, repo)
    # Seed an empty checkpoint.  The stale variant replaces it with old
    # messages, including an attestation followed by enough newer AIs to move it
    # outside N=3.
    # An empty checkpoint can be created in a separate saver; the stale
    # variant needs the real seeded state from the first graph.
    if seed == "empty":
        from langgraph.checkpoint.memory import MemorySaver
        seed_checkpoint = MemorySaver()
    else:
        seed_checkpoint = memory_saver
    seed_graph = _build(
        real_graph_module,
        ScriptedChatModel(responses=[AIMessage(content="seed")], i=0),
        manager,
        seed_checkpoint,
    )
    seed_config = {"configurable": {"thread_id": INSTANCE_ID}}
    if seed == "stale":
        messages = [
            AIMessage(
                content="old attestation",
                tool_calls=[
                    {"name": "attest_completion", "args": {}, "id": "old-attest"}
                ],
            ),
            ToolMessage(content="old result", tool_call_id="old-attest"),
            _ai("old one"),
            _ai("old two"),
            _ai("old three"),
            _ai("old four"),
        ]
        await seed_graph.aupdate_state(seed_config, {"messages": messages}, as_node="agent")
    else:
        pass

    model = ScriptedChatModel(
        responses=[
            AIMessage(content="new mission without a fresh attestation"),
            AIMessage(
                content="attesting now",
                tool_calls=[
                    {
                        "name": "attest_completion",
                        "args": {},
                        "id": "fresh-attest",
                    }
                ],
            ),
            AIMessage(content="new mission done"),
        ],
        i=0,
    )
    graph = _build(real_graph_module, model, manager, seed_checkpoint)
    with caplog.at_level(logging.INFO):
        state = await graph.ainvoke(
            {"messages": [HumanMessage(content="new mission")]},
            config={"configurable": {"thread_id": INSTANCE_ID}, "recursion_limit": 30},
        )

    nudges = [
        m
        for m in state["messages"]
        if isinstance(m, HumanMessage) and m.additional_kwargs.get("attestation_nudge")
    ]
    assert len(nudges) == 1
    assert nudges[0].additional_kwargs["attestation_nudge_denied_count"] == 1
    assert "decision=denied" in caplog.text
    assert "decision=allowed" in caplog.text
    assert repo.get_attestation_denied_count(INSTANCE_ID) == 0
    assert any(
        m.content == "new mission done" for m in state["messages"] if isinstance(m, AIMessage)
    )
    manager.enqueue_message.assert_not_called()
