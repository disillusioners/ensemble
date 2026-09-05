"""Mode: enforce — scanner and ledger failures fail open through the real graph."""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from sqlalchemy.exc import OperationalError

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
            tools=[],
            checkpointer=checkpointer,
            llm_config={"model": "scripted-test", "api_key": "test"},
            system_prompt="scripted fail-open test",
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
async def test_scanner_exception_allows_completion_and_logs_error(
    real_graph_module,
    memory_saver,
    file_sqlite_engine,
    attestation_repository,
    attestation_manager_factory,
    monkeypatch,
    caplog,
):
    repo, _instance = attestation_repository
    manager = attestation_manager_factory(file_sqlite_engine, repo)
    model = ScriptedChatModel(responses=[AIMessage(content="unattested")], i=0)
    graph = _build(real_graph_module, model, manager, memory_saver)

    def scanner_boom(*args, **kwargs):
        raise ValueError("scanner exploded")

    monkeypatch.setattr(
        "daemon.services.attestation_gate.scan_for_attestation_detailed",
        scanner_boom,
    )
    with caplog.at_level(logging.INFO):
        state = await graph.ainvoke(
            {"messages": [HumanMessage(content="do it")]},
            config={"configurable": {"thread_id": INSTANCE_ID}, "recursion_limit": 20},
        )

    assert _nudge_count(state["messages"]) == 0
    assert "event=leader_completion_gate_error" in caplog.text
    assert "gate_exception_seen=true" in caplog.text
    assert state.get("gate_exception_seen") is True
    assert repo.get(INSTANCE_ID).instance_metadata.get("attestation_gate_exception_seen") is True
    assert "error_class=ValueError" in caplog.text
    manager.enqueue_message.assert_not_called()
    assert repo.get_attestation_denied_count(INSTANCE_ID) == 0


@pytest.mark.asyncio
async def test_ledger_operational_error_allows_completion_and_logs_db_error(
    real_graph_module,
    memory_saver,
    file_sqlite_engine,
    attestation_repository,
    attestation_manager_factory,
    monkeypatch,
    caplog,
):
    repo, _instance = attestation_repository
    manager = attestation_manager_factory(file_sqlite_engine, repo)
    model = ScriptedChatModel(responses=[AIMessage(content="unattested")], i=0)
    graph = _build(real_graph_module, model, manager, memory_saver)

    def ledger_boom(*args, **kwargs):
        raise OperationalError("statement", {}, Exception("database is locked"))

    monkeypatch.setattr(repo, "increment", ledger_boom)
    with caplog.at_level(logging.INFO):
        state = await graph.ainvoke(
            {"messages": [HumanMessage(content="do it")]},
            config={"configurable": {"thread_id": INSTANCE_ID}, "recursion_limit": 20},
        )

    assert _nudge_count(state["messages"]) == 0
    assert "event=leader_completion_gate_db_error" in caplog.text
    assert "error_class=OperationalError" in caplog.text
    manager.enqueue_message.assert_not_called()
    assert repo.get_attestation_denied_count(INSTANCE_ID) == 0
