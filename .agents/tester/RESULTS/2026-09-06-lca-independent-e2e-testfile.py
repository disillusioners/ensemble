"""INDEPENDENT acceptance verification — leader completion attestation (Job 3).

This module re-proves the four LCA acceptance scenarios from the SPEC, not
from the existing tests' assertion text.  Differences from the Phase-5
matrix modules:

* The mode is driven through the REAL resolver path: the env var is pinned
  and the resolver caches are reset — ``resolve_gate_settings`` is NEVER
  patched here, so ``ENSEMBLE_LEADER_ATTESTATION_MODE`` genuinely decides
  the routing (one mode per test, never mixed).
* The leader/child instance rows, baseline queue rows, and the manager
  facade are constructed INSIDE this module (only the DB recipe —
  file-backed SQLite tmp_path + NullPool + WAL + busy_timeout — is shared
  via the ``file_sqlite_engine`` fixture).
* The original-bug scenario binds the TERMINAL/ACTIVE child as real
  instance rows read back through the repository, not only through the
  watcher facade.

Scenarios: S1 (deny → in-graph nudge → attest → allow, no delivery),
S2 (active child ⇒ allowed_legitimate_pending_wakeup, counter NOT reset),
S3 (bound exhaustion ⇒ terminal_after_bound exactly once), M-dry (zero
side effects on a DIRTY ledger row), M-off (gate not even wired).
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from sqlmodel import Session, func, select

from daemon.repositories.dependency_bus.models import (
    DependencyWatcher,
    DependencyWatcherState,
)
from daemon.repositories.instance.models import InstanceHierarchy
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.message_queue.models import MessageQueue
from daemon.repositories.task.models import Task
from tests.support.scripted_chat_model import ScriptedChatModel

#: NFR-6 verbatim nudge — hard-coded HERE independently of daemon.graph.
NUDGE_TEXT = "The work is not yet finished — check current progress and continue."

LEADER_ID = "indep-verify-leader"
CHILD_ID = "indep-verify-child"


@tool
def attest_completion() -> dict:
    """Leader attestation tool wired into the graph's ToolNode."""
    return {"attested": True, "timestamp": "2026-09-06T00:00:00+00:00"}


# ---------------------------------------------------------------------------
# Construction helpers (module-local — independent of tests/support fixtures)
# ---------------------------------------------------------------------------


def _pin_mode(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    """Pin ONE mode through the real env→resolver path (no patching)."""
    monkeypatch.setenv("ENSEMBLE_LEADER_ATTESTATION_MODE", mode)
    monkeypatch.setenv("WATCHOVER_ENABLED", "false")
    from daemon.services.attestation_gate import _reset_gate_settings_for_tests

    _reset_gate_settings_for_tests()


def _repo(engine) -> SQLModelInstanceRepository:
    return SQLModelInstanceRepository(engine)


def _manager(engine, repo: SQLModelInstanceRepository, *, queued_wakeups: int = 0):
    """Manager exposing the production gate facades; pending children read
    from the REAL dependency_watchers table (like the production facade)."""

    class IndependentVerifyManager:
        _instance_repository = repo  # gate ledger wiring seam

        def __init__(self) -> None:
            self.count_pending_children = MagicMock(
                side_effect=self._real_count_pending_children
            )
            self.get_queued_or_expected_wakeups = MagicMock(
                return_value=queued_wakeups
            )
            self.enqueue_message = MagicMock(name="enqueue_message")

        @staticmethod
        def _real_count_pending_children(target_instance_id: str) -> int:
            with Session(engine) as session:
                return int(
                    session.scalar(
                        select(func.count())
                        .select_from(DependencyWatcher)
                        .where(
                            DependencyWatcher.target_instance_id
                            == target_instance_id,
                            DependencyWatcher.state
                            == DependencyWatcherState.PENDING.value,
                        )
                    )
                    or 0
                )

        @staticmethod
        def is_watchover_enabled(_target_instance_id: str) -> bool:
            return False

        @staticmethod
        def is_question_pause_requested(_target_instance_id: str) -> bool:
            return False

    return IndependentVerifyManager()


def _seed_children(repo: SQLModelInstanceRepository, *, child_status: str) -> None:
    """Leader + one child instance row in the requested lifecycle status."""
    # The shared narrow-recipe fixture does not create the hierarchy
    # junction table (the matrix tests never seed child rows); create it
    # here so the parent_id linkage write succeeds. Additive + module-local.
    from sqlmodel import SQLModel

    SQLModel.metadata.create_all(repo.engine, tables=[InstanceHierarchy.__table__])
    repo.create(
        instance_id=LEADER_ID,
        agent_id="leader",
        agent_dir="./agents/leader",
    )
    repo.create(
        instance_id=CHILD_ID,
        agent_id="worker",
        agent_dir="./agents/worker",
        parent_id=LEADER_ID,
        status=child_status,
    )


def _seed_baseline_queues(engine) -> None:
    with Session(engine) as session:
        session.add(MessageQueue(instance_id=LEADER_ID, content="baseline-1"))
        session.add(
            Task(instance_id=LEADER_ID, task_type="process_message")
        )
        session.commit()


def _queue_task_counts(engine) -> tuple[int, int]:
    with Session(engine) as session:
        msgs = int(
            session.scalar(
                select(func.count())
                .select_from(MessageQueue)
                .where(MessageQueue.instance_id == LEADER_ID)
            )
            or 0
        )
        tasks = int(
            session.scalar(
                select(func.count())
                .select_from(Task)
                .where(Task.instance_id == LEADER_ID)
            )
            or 0
        )
    return msgs, tasks


def _nudges(messages) -> list[HumanMessage]:
    return [
        m
        for m in messages
        if isinstance(m, HumanMessage)
        and m.additional_kwargs.get("attestation_nudge")
    ]


def _build_graph(real_graph_module, model, manager):
    """Patch ONLY the LLM seam; the gate settings resolve for real."""
    real_graph_module.build_instance_llms = lambda **_: (model, model)
    return real_graph_module.build_instance_graph(
        tools=[attest_completion],
        checkpointer=_memory_saver(),
        llm_config={"model": "scripted-independent", "api_key": "test"},
        system_prompt="independent attestation verifier",
        user_language="Auto",
        language_check_enabled=False,
        manager=manager,
        graph_config={"configurable": {"thread_id": LEADER_ID}},
        attestation_enabled=True,
    )


def _memory_saver():
    from langgraph.checkpoint.memory import MemorySaver

    return MemorySaver()


async def _run(graph, *input_messages):
    return await graph.ainvoke(
        {"messages": list(input_messages)},
        config={"configurable": {"thread_id": LEADER_ID}, "recursion_limit": 40},
    )


def _assert_no_delivery_side_effects(manager, engine, before_counts) -> None:
    """The C1b negative lock: no enqueue, no new queue/task rows, ever."""
    manager.enqueue_message.assert_not_called()
    assert _queue_task_counts(engine) == before_counts


# ---------------------------------------------------------------------------
# S1 — original symptom: hallucinated END over terminal children ⇒ DENIED,
# in-graph nudge (same execution, zero delivery), attest ⇒ allow + reset.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_s1_hallucinated_end_denied_nudged_in_graph_then_attested_allow(
    real_graph_module, file_sqlite_engine, caplog, monkeypatch
):
    _pin_mode(monkeypatch, "enforce")
    repo = _repo(file_sqlite_engine)
    _seed_children(repo, child_status="terminated")

    # Preconditions bound to REAL rows: child terminal, zero pending
    # wakeups — exactly the original-bug state.
    child = repo.get(CHILD_ID)
    assert child is not None and child.status == "terminated"
    manager = _manager(file_sqlite_engine, repo)
    assert manager.count_pending_children(LEADER_ID) == 0
    assert manager.get_queued_or_expected_wakeups(LEADER_ID) == 0

    _seed_baseline_queues(file_sqlite_engine)
    before = _queue_task_counts(file_sqlite_engine)

    # A hallucinated in-progress child report sits in history, then the
    # leader hallucinates a completion (plain END, NO tool call).
    model = ScriptedChatModel(
        responses=[
            AIMessage(content="All work is complete. Nothing pending."),
            AIMessage(
                content="Attesting now.",
                tool_calls=[
                    {"name": "attest_completion", "args": {}, "id": "indep-s1-attest"}
                ],
            ),
            AIMessage(content="Genuinely finished after checking progress."),
        ],
        i=0,
    )
    graph = _build_graph(real_graph_module, model, manager)

    with caplog.at_level(logging.INFO):
        state = await _run(
            graph,
            HumanMessage(content="finish the mission"),
            HumanMessage(
                content="[child report | indep-verify-child] Still in progress — "
                "2 of 5 tasks done."
            ),
        )

    messages = state["messages"]
    # (ii) exactly one in-graph nudge, NFR-6 verbatim, checkpoint-durable kwargs.
    nudges = _nudges(messages)
    assert len(nudges) == 1
    assert nudges[0].content == NUDGE_TEXT
    assert nudges[0].additional_kwargs == {
        "attestation_nudge": True,
        "attestation_nudge_denied_count": 1,
    }
    assert state["attestation_nudge_denied_count"] == 1

    # (iv) SAME execution continued: the attest tool_call turn and the
    # post-nudge final message all happened inside this one ainvoke.
    assert any(isinstance(m, AIMessage) and m.tool_calls for m in messages)
    assert messages[-1].content == "Genuinely finished after checking progress."
    assert model.calls_made == 3

    # Decision ledger: one deny, one attested allow, never escalation.
    assert caplog.text.count("decision=denied") == 1
    assert caplog.text.count("decision=allowed") == 1
    assert "decision=terminal_after_bound" not in caplog.text
    assert "event=leader_completion_gate_terminal_after_bound" not in caplog.text

    # (iii) NO dual delivery: no enqueue AND no new message_queue/task rows
    # across the deny window.
    _assert_no_delivery_side_effects(manager, file_sqlite_engine, before)

    # (vi) attested allow reset the counter (ruling 1, trigger a).
    assert repo.get_attestation_denied_count(LEADER_ID) == 0
    row = repo.get(LEADER_ID)
    assert row.completion_gate_escalated is False

    # (i) the leader instance was never completed by the deny path: the
    # row's lifecycle status is untouched (still the created default).
    assert repo.get(LEADER_ID).status == "idle"


# ---------------------------------------------------------------------------
# S2 — active child / pending wakeup ⇒ allowed_legitimate_pending_wakeup,
# counter NOT reset (ruling 1 non-reset), no nudge.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_s2_active_child_allows_without_nudge_or_counter_reset(
    real_graph_module, file_sqlite_engine, caplog, monkeypatch
):
    _pin_mode(monkeypatch, "enforce")
    repo = _repo(file_sqlite_engine)
    _seed_children(repo, child_status="running")
    with Session(file_sqlite_engine) as session:
        session.add(
            DependencyWatcher(
                source_task_id="indep-s2-source",
                target_instance_id=LEADER_ID,
                state=DependencyWatcherState.PENDING.value,
            )
        )
        session.commit()

    manager = _manager(file_sqlite_engine, repo)
    assert manager.count_pending_children(LEADER_ID) == 1  # real read-back

    # Pre-seed a NONZERO counter — proving the R2 allow is not an
    # attested-allow reset (ruling 1: allowed_legitimate_pending_wakeup
    # NEVER resets).
    repo.increment_attestation_denied_count(LEADER_ID, "indep-s2-epoch-a")
    repo.increment_attestation_denied_count(LEADER_ID, "indep-s2-epoch-b")
    assert repo.get_attestation_denied_count(LEADER_ID) == 2

    model = ScriptedChatModel(
        responses=[AIMessage(content="Delegated to the child; ending my turn.")],
        i=0,
    )
    graph = _build_graph(real_graph_module, model, manager)

    with caplog.at_level(logging.INFO):
        state = await _run(graph, HumanMessage(content="delegate and end"))

    # Allowed via the R2 route, never denied, never nudged.
    assert "decision=allowed_legitimate_pending_wakeup" in caplog.text
    assert "decision=denied" not in caplog.text
    assert _nudges(state["messages"]) == []

    # R1 non-reset: counter still exactly 2 after the allow.
    assert repo.get_attestation_denied_count(LEADER_ID) == 2
    # The turn ended (single LLM call) with zero delivery side effects.
    assert model.calls_made == 1
    manager.enqueue_message.assert_not_called()
    # The PENDING watcher was consumed by nothing — allow path mutated
    # no rows.
    with Session(file_sqlite_engine) as session:
        watcher = session.exec(
            select(DependencyWatcher).where(
                DependencyWatcher.target_instance_id == LEADER_ID
            )
        ).one()
        assert watcher.state == DependencyWatcherState.PENDING.value


# ---------------------------------------------------------------------------
# S3 — bound exhaustion: 3 un-attested denies, then ONE terminal escalation.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_s3_bound_exhaustion_escalates_exactly_once_and_terminates(
    real_graph_module, file_sqlite_engine, caplog, monkeypatch
):
    _pin_mode(monkeypatch, "enforce")
    repo = _repo(file_sqlite_engine)
    _seed_children(repo, child_status="terminated")
    manager = _manager(file_sqlite_engine, repo)
    assert manager.count_pending_children(LEADER_ID) == 0

    # The leader NEVER attests: four plain END attempts against bound=3.
    model = ScriptedChatModel(
        responses=[
            AIMessage(content=f"Hallucinated completion attempt {n}.")
            for n in range(1, 5)
        ],
        i=0,
    )
    graph = _build_graph(real_graph_module, model, manager)

    with caplog.at_level(logging.INFO):
        state = await _run(graph, HumanMessage(content="do the work"))

    # Deny/nudge sequence: exactly three nudges, none after the bound.
    assert len(_nudges(state["messages"])) == 3
    assert caplog.text.count("decision=denied") == 3
    assert caplog.text.count("decision=terminal_after_bound") == 1
    # Escalation event fired exactly once.
    assert (
        caplog.text.count("event=leader_completion_gate_terminal_after_bound") == 1
    )
    assert caplog.text.count("decision=allowed") == 0

    # Ruling 2: the SAME reset op zeroed the counter and set the flag.
    row = repo.get(LEADER_ID)
    assert row.attestation_denied_count == 0
    assert row.completion_gate_escalated is True

    # Not hung: the run terminated at the bound+1 attempt (a fifth LLM
    # call would raise ScriptedChatModel IndexError), END allowed with
    # no route-back hint left armed.
    assert model.calls_made == 4
    assert state["attestation_route"] is None
    manager.enqueue_message.assert_not_called()
    # No lifecycle write came from the gate path itself.
    assert repo.get(LEADER_ID).status == "idle"


# ---------------------------------------------------------------------------
# M-dry — evaluates + logs decision=dry_log with ZERO side effects, even
# on a pre-dirtied ledger row (counter + escalation flag must survive).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mdry_dry_log_on_dirty_row_zero_side_effects(
    real_graph_module, file_sqlite_engine, caplog, monkeypatch
):
    _pin_mode(monkeypatch, "dry")
    repo = _repo(file_sqlite_engine)
    _seed_children(repo, child_status="terminated")
    manager = _manager(file_sqlite_engine, repo)

    # Dirty the row FIRST: dry must not increment NOR clear anything.
    repo.increment_attestation_denied_count(LEADER_ID, "indep-dry-epoch-a")
    repo.increment_attestation_denied_count(LEADER_ID, "indep-dry-epoch-b")
    repo.set_completion_gate_escalated(LEADER_ID)

    _seed_baseline_queues(file_sqlite_engine)
    before = _queue_task_counts(file_sqlite_engine)

    model = ScriptedChatModel(
        responses=[AIMessage(content="Declaring completion without attesting.")],
        i=0,
    )
    graph = _build_graph(real_graph_module, model, manager)

    with caplog.at_level(logging.INFO):
        state = await _run(graph, HumanMessage(content="finish"))

    # Canonical dry evaluation logged, with the mode surfaced.
    assert "event=leader_completion_gate" in caplog.text
    assert "decision=dry_log" in caplog.text
    assert "mode=dry" in caplog.text

    # Zero side effects: no nudge, END allowed, nothing delivered.
    assert _nudges(state["messages"]) == []
    assert model.calls_made == 1
    _assert_no_delivery_side_effects(manager, file_sqlite_engine, before)

    # BOTH ledger columns unchanged on the dirty row (dry is a passive
    # observer — no increment, no reset, no flag flip).
    row = repo.get(LEADER_ID)
    assert row.attestation_denied_count == 2
    assert row.completion_gate_escalated is True
    assert repo.get(LEADER_ID).status == "idle"


# ---------------------------------------------------------------------------
# M-off — the gate is not even wired: no evaluation, no gate logs, END
# unconditionally allowed.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_moff_gate_not_wired_end_unconditionally_allowed(
    real_graph_module, file_sqlite_engine, caplog, monkeypatch
):
    _pin_mode(monkeypatch, "off")
    repo = _repo(file_sqlite_engine)
    _seed_children(repo, child_status="terminated")
    manager = _manager(file_sqlite_engine, repo)

    repo.increment_attestation_denied_count(LEADER_ID, "indep-off-epoch-a")
    repo.set_completion_gate_escalated(LEADER_ID)

    _seed_baseline_queues(file_sqlite_engine)
    before = _queue_task_counts(file_sqlite_engine)

    model = ScriptedChatModel(
        responses=[AIMessage(content="Plain turn end, no attestation.")],
        i=0,
    )
    with caplog.at_level(logging.INFO):
        # Built INSIDE the capture: the mode=off wiring notice is logged
        # at build time, and it is part of the OFF contract evidence.
        graph = _build_graph(real_graph_module, model, manager)
        state = await _run(graph, HumanMessage(content="finish"))

    # Wiring-level proof: the build explicitly reports the gate is NOT
    # wired under mode=off, and NO gate evaluation log entry exists.
    assert "[AttestationGate] mode=off" in caplog.text
    assert "event=leader_completion_gate" not in caplog.text

    # No gate evaluation happened at all — the R2 facades were never read.
    manager.count_pending_children.assert_not_called()
    manager.get_queued_or_expected_wakeups.assert_not_called()

    # END unconditionally allowed, no nudge, nothing delivered.
    assert _nudges(state["messages"]) == []
    assert model.calls_made == 1
    _assert_no_delivery_side_effects(manager, file_sqlite_engine, before)

    # The ledger was untouched by the off path (counter 1, flag True).
    row = repo.get(LEADER_ID)
    assert row.attestation_denied_count == 1
    assert row.completion_gate_escalated is True
