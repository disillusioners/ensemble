"""Mode: enforce — legitimate pending work/wakeups allow without attestation.

Scenario A reads a committed ``dependency_watchers`` PENDING row before the
gate.  Scenario B reads a held wakeup row.  Both are R2 inputs, so no nudge or
counter reset is allowed.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from sqlmodel import Session, select

from daemon.repositories.dependency_bus.models import DependencyWatcher, DependencyWatcherState
from daemon.repositories.task.models import Task
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
            system_prompt="scripted R2 test",
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


def _seed_pending_watcher(engine) -> None:
    with Session(engine) as session:
        session.add(
            DependencyWatcher(
                source_task_id="child-source",
                target_instance_id=INSTANCE_ID,
                state=DependencyWatcherState.PENDING.value,
            )
        )
        session.commit()


def _seed_future_wakeup(engine) -> None:
    with Session(engine) as session:
        session.add(
            Task(
                instance_id=INSTANCE_ID,
                task_type="process_message",
                next_retry_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            )
        )
        session.commit()


@pytest.mark.parametrize("scenario", ["watcher", "wakeup"])
@pytest.mark.asyncio
async def test_pending_wakeup_inputs_allow_without_nudge_or_reset(
    scenario,
    real_graph_module,
    memory_saver,
    file_sqlite_engine,
    attestation_repository,
    attestation_manager_factory,
    caplog,
):
    repo, _instance = attestation_repository
    if scenario == "watcher":
        _seed_pending_watcher(file_sqlite_engine)
        queued_wakeups = 0
        # This is the TOCTOU contract: the committed PENDING row must be
        # visible to the facade before the gate is evaluated.
        with Session(file_sqlite_engine) as session:
            count = session.scalar(
                select(DependencyWatcher).where(
                    DependencyWatcher.target_instance_id == INSTANCE_ID,
                    DependencyWatcher.state == DependencyWatcherState.PENDING.value,
                )
            )
            assert count is not None
        manager = attestation_manager_factory(
            file_sqlite_engine,
            repo,
            pending_children=None,
            queued_wakeups=queued_wakeups,
        )
    else:
        _seed_future_wakeup(file_sqlite_engine)
        with Session(file_sqlite_engine) as session:
            task = session.exec(select(Task).where(Task.instance_id == INSTANCE_ID)).first()
            assert task is not None and task.next_retry_at is not None
        manager = attestation_manager_factory(
            file_sqlite_engine,
            repo,
            queued_wakeups=1,
        )

    # A nonzero counter proves the R2 allow did not accidentally behave as an
    # attested allow.  The escalation flag is also held to pin ruling 2.
    repo.increment_attestation_denied_count(INSTANCE_ID, "before-r2")
    repo.increment_attestation_denied_count(INSTANCE_ID, "before-r2-second")
    repo.set_completion_gate_escalated(INSTANCE_ID)
    before = repo.get(INSTANCE_ID)

    model = ScriptedChatModel(responses=[AIMessage(content="done without attestation")], i=0)
    graph = _build(real_graph_module, model, manager, memory_saver)
    with caplog.at_level(logging.INFO):
        state = await graph.ainvoke(
            {"messages": [HumanMessage(content="delegate and finish")]},
            config={"configurable": {"thread_id": INSTANCE_ID}, "recursion_limit": 20},
        )

    assert _nudge_count(state["messages"]) == 0
    log_text = caplog.text
    assert "decision=allowed_legitimate_pending_wakeup" in log_text
    assert "decision=denied" not in log_text
    after = repo.get(INSTANCE_ID)
    assert after.attestation_denied_count == before.attestation_denied_count == 2
    assert after.completion_gate_escalated is True
    manager.enqueue_message.assert_not_called()
    assert model.calls_made == 1
