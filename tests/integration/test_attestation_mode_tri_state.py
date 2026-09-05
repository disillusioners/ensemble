"""Mode-pinned tri-state routing semantics.

Each parameterized sub-case is deliberately independent: off bypasses the
gate, dry evaluates/logs without side effects, and enforce takes the nudge
path.  No test mixes modes.
"""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool

from tests.support.scripted_chat_model import ScriptedChatModel

INSTANCE_ID = "attestation-leader-e2e"


@tool
def attest_completion() -> dict:
    """Tool used to finish the scripted enforce-mode case."""
    return {"attested": True}


def _settings(mode):
    from daemon.services.attestation_gate import GateSettings

    return GateSettings(mode=mode, window=3, deny_bound=3)


def _build(graph_module, model, manager, checkpointer, mode):
    graph_module.build_instance_llms = lambda **_: (model, model)
    with patch(
        "daemon.services.attestation_gate.resolve_gate_settings",
        return_value=_settings(mode),
    ):
        return graph_module.build_instance_graph(
            tools=[attest_completion],
            checkpointer=checkpointer,
            llm_config={"model": "scripted-test", "api_key": "test"},
            system_prompt="scripted tri-state test",
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


@pytest.mark.parametrize(
    "mode,expected_nudges,expected_decision,expected_calls,expected_denies",
    [
        ("off", 0, None, 1, 0),
        ("dry", 0, "dry_log", 1, 0),
        ("enforce", 2, "denied", 4, 2),
    ],
    ids=["mode-off", "mode-dry", "mode-enforce"],
)
@pytest.mark.asyncio
async def test_tri_state_mode_semantics_are_separate(
    mode,
    expected_nudges,
    expected_decision,
    expected_calls,
    expected_denies,
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
        responses=[
            AIMessage(content="hallucinated end"),
            AIMessage(content="still not done"),
            AIMessage(
                content="attesting now",
                tool_calls=[
                    {
                        "name": "attest_completion",
                        "args": {},
                        "id": "tri-state-attest",
                    }
                ],
            ),
            AIMessage(content="finally done"),
        ],
        i=0,
    )
    graph = _build(real_graph_module, model, manager, memory_saver, mode)

    with caplog.at_level(logging.INFO):
        state = await graph.ainvoke(
            {"messages": [HumanMessage(content="finish")]},
            config={"configurable": {"thread_id": INSTANCE_ID}, "recursion_limit": 20},
        )

    assert _nudge_count(state["messages"]) == expected_nudges
    if expected_decision is None:
        assert "event=leader_completion_gate" not in caplog.text
        manager.count_pending_children.assert_not_called()
        manager.get_queued_or_expected_wakeups.assert_not_called()
        assert repo.get_attestation_denied_count(INSTANCE_ID) == 0
    else:
        assert f"decision={expected_decision}" in caplog.text
        manager.count_pending_children.assert_called()
        manager.get_queued_or_expected_wakeups.assert_called()
    assert caplog.text.count("decision=denied") == expected_denies
    if mode == "enforce":
        # The final attested allow is a reset trigger; the two pre-attest
        # denials are no longer present in the row.
        assert repo.get_attestation_denied_count(INSTANCE_ID) == 0
    else:
        assert repo.get_attestation_denied_count(INSTANCE_ID) == 0
    manager.enqueue_message.assert_not_called()
    assert model.calls_made == expected_calls
