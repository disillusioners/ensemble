"""INDEPENDENT E2E verification — conditional attestation + masquerade fix.

Jobs 2+6 close-out (2026-09-07). This module is an ORIGINAL scenario set
(not a copy of the developer's Phase-6 tests): every assertion here was
written from the delta description + the source at HEAD, through the REAL
graph assembly (``build_instance_graph``) with ONLY the LLM seam scripted.

Proven at HEAD 8eace79e (delta under test 26f908a4..7be6b1d8 + an
additive evidence-only commit):

* (a) quick-question mission — ONE real user message, zero tool calls ⇒
  turn-end ALLOWED, ``attestation_required=False`` on the canonical gate
  log row, ZERO nudges, zero side effects.
* (b) chart mission — a NON-delegation tool call (chart generation) ⇒
  ALLOWED, ``attestation_required=False``, no nudge.
* (c) delegated mission — ``send_message`` after the last real user
  message arms the gate; un-attested END ⇒ DENIED + the EXACT
  ``ATTESTATION_NUDGE_TEXT`` (header + mermaid flowchart + checkpoint
  kwargs); attest in the SAME ainvoke ⇒ ALLOWED + ledger counter reset
  (repo read-back).
* (d) MASQUERADE headline — the child report is delivered through the
  REAL enqueue lane (real ``message_queue`` row with an
  ``internal_report:`` source → the production constructor
  ``_build_graph_input`` materializes it onto the HumanMessage). The
  constructed message now carries the stamp
  ``{"injected_message": True, "source": ...}``; a BARE pre-fix-shaped
  message stays bare (and a pure-scanner counterfactual proves the bare
  shape WOULD have relaxed the gate); the delegation window is NOT reset
  and the un-attested END after the stamped delivery is STILL DENIED.
* (e) self-reference trap — after a deny the injected nudge (an
  attestation-stamped HumanMessage) is NOT a real user message: the next
  turn-end is denied AGAIN (denied_count=2) and the last real user
  message is still the ORIGINAL user turn.

Sources grounding every expectation (read before writing this file):
``daemon/services/attestation_scanner.py`` (delegation = send_message
name-match AFTER the last real user message; 6-step real-user ladder),
``daemon/services/attestation_gate.py`` (decide() branches 3/4/7),
``daemon/graph.py`` (:2834 ``ATTESTATION_NUDGE_TEXT`` verbatim at
:3344; nudge kwargs :3347-3348), ``daemon/services/instance_messaging.py``
(:527/:543 stamp factory ``_stamped_additional_kwargs``, applied in
``_build_graph_input``).
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from sqlmodel import Session, func, select

from daemon.graph import ATTESTATION_NUDGE_TEXT as NUDGE_TEXT
from daemon.repositories.dependency_bus.models import (
    DependencyWatcher,
    DependencyWatcherState,
)
from daemon.repositories.instance.models import InstanceHierarchy
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.message_queue.models import MessageQueue
from daemon.repositories.task.models import Task
from tests.support.scripted_chat_model import ScriptedChatModel

LEADER_ID = "cond-e2e-leader"
CHILD_ID = "cond-e2e-child"


@tool
def attest_completion() -> dict:
    """The real leader attestation tool name wired into the ToolNode."""
    return {"attested": True, "timestamp": "2026-09-07T00:00:00+00:00"}


@tool
def send_message(target_instance_id: str, message: str) -> dict:
    """Test-local delegation stub — the scanner matches the tool NAME."""
    return {"delivered": True, "target": target_instance_id}


@tool
def generate_chart(title: str, kind: str = "bar") -> dict:
    """A NON-delegation tool (chart generation) for scenario (b)."""
    return {"chart": title, "kind": kind, "rendered": True}


# ---------------------------------------------------------------------------
# Helpers — module-local, written for THIS verification
# ---------------------------------------------------------------------------


def _pin_enforce_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin ONE mode through the REAL env→resolver path (no patching)."""
    monkeypatch.setenv("ENSEMBLE_LEADER_ATTESTATION_MODE", "enforce")
    monkeypatch.setenv("WATCHOVER_ENABLED", "false")
    from daemon.services.attestation_gate import _reset_gate_settings_for_tests

    _reset_gate_settings_for_tests()


def _repo(engine) -> SQLModelInstanceRepository:
    return SQLModelInstanceRepository(engine)


def _seed_leader_and_child(
    repo: SQLModelInstanceRepository, *, child_status: str
) -> None:
    """Leader + child instance rows (hierarchy junction created locally)."""
    from sqlmodel import SQLModel

    SQLModel.metadata.create_all(
        repo.engine, tables=[InstanceHierarchy.__table__]
    )
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


def _manager(engine, repo: SQLModelInstanceRepository):
    """Manager exposing the THREE production R2 facades.

    pending_children reads the REAL dependency_watchers table;
    live_descendants does the REAL permanent-lineage BFS — the third R2
    input upstream added 2026-09-06 (a manager missing it fail-opens at
    the gate's DB seam, which would invalidate every deny assertion).
    """

    class CondE2EManager:
        _instance_repository = repo  # gate ledger wiring seam

        def __init__(self) -> None:
            self.count_pending_children = MagicMock(
                side_effect=self._pending_children
            )
            self.get_queued_or_expected_wakeups = MagicMock(return_value=0)
            self.count_live_descendants = MagicMock(
                side_effect=self._live_descendants
            )
            self.enqueue_message = MagicMock(name="enqueue_message")

        @staticmethod
        def _pending_children(target_instance_id: str) -> int:
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
        def _live_descendants(target_instance_id: str) -> int:
            from daemon.repositories.instance.models import InstanceStatus

            terminal = {
                InstanceStatus.COMPLETED.value,
                InstanceStatus.TERMINATED.value,
                InstanceStatus.ERROR.value,
                InstanceStatus.FAILED.value,
            }
            count = 0
            for iid in repo.get_tree_ids_permanent(target_instance_id):
                if iid == target_instance_id:
                    continue
                row = repo.get(iid)
                if row is not None and row.status not in terminal:
                    count += 1
            return count

        @staticmethod
        def is_watchover_enabled(_iid: str) -> bool:
            return False

        @staticmethod
        def is_question_pause_requested(_iid: str) -> bool:
            return False

        @staticmethod
        def set_deferred_watchover_terminate(_iid: str) -> None:
            return None

    return CondE2EManager()


def _build_graph(real_graph_module, monkeypatch, model, manager):
    """Patch ONLY the LLM seam; resolver + gate + ledger all real."""
    monkeypatch.setattr(
        real_graph_module,
        "build_instance_llms",
        lambda **_: (model, model),
    )
    return real_graph_module.build_instance_graph(
        tools=[attest_completion, send_message, generate_chart],
        checkpointer=_memory_saver(),
        llm_config={"model": "scripted-cond-e2e", "api_key": "test"},
        system_prompt="conditional attestation independent e2e",
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
        config={"configurable": {"thread_id": LEADER_ID}, "recursion_limit": 60},
    )


def _nudges(messages) -> list[HumanMessage]:
    return [
        m
        for m in messages
        if isinstance(m, HumanMessage)
        and m.additional_kwargs.get("attestation_nudge")
    ]


def _gate_rows(caplog) -> list[str]:
    """Canonical gate decision log lines (one per evaluation)."""
    return [
        rec.getMessage()
        for rec in caplog.records
        if "event=leader_completion_gate decision=" in rec.getMessage()
    ]


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


def _send_call(call_id: str) -> dict:
    return {
        "name": "send_message",
        "args": {
            "target_instance_id": CHILD_ID,
            "message": "handle the delegated subtask",
        },
        "id": call_id,
    }


def _attest_call(call_id: str) -> dict:
    return {"name": "attest_completion", "args": {}, "id": call_id}


# ---------------------------------------------------------------------------
# (a) quick-question mission — no tool calls at all
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_quick_question_finishes_free_without_gate(
    real_graph_module, file_sqlite_engine, caplog, monkeypatch
):
    _pin_enforce_mode(monkeypatch)
    repo = _repo(file_sqlite_engine)
    _seed_leader_and_child(repo, child_status="completed")
    manager = _manager(file_sqlite_engine, repo)
    before = _queue_task_counts(file_sqlite_engine)

    model = ScriptedChatModel(
        responses=[AIMessage(content="The answer is 4.")],
        i=0,
    )
    graph = _build_graph(real_graph_module, monkeypatch, model, manager)

    with caplog.at_level(logging.INFO):
        state = await _run(graph, HumanMessage(content="What is 2+2?"))

    # Turn-end ALLOWED — and the canonical gate ROW carries the
    # conditional flag FALSE (the FR-3 conditionality audit surface).
    rows = _gate_rows(caplog)
    assert len(rows) == 1
    assert "decision=allowed" in rows[0]
    assert "attestation_required=False" in rows[0]
    assert "last_real_user_found=True" in rows[0]

    # ZERO nudges, zero side effects, single LLM turn.
    assert _nudges(state["messages"]) == []
    assert model.calls_made == 1
    assert state["messages"][-1].content == "The answer is 4."
    manager.enqueue_message.assert_not_called()
    assert _queue_task_counts(file_sqlite_engine) == before
    assert repo.get_attestation_denied_count(LEADER_ID) == 0


# ---------------------------------------------------------------------------
# (b) chart mission — a NON-delegation tool call does not arm the gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_b_chart_tool_call_finishes_free_without_gate(
    real_graph_module, file_sqlite_engine, caplog, monkeypatch
):
    _pin_enforce_mode(monkeypatch)
    repo = _repo(file_sqlite_engine)
    _seed_leader_and_child(repo, child_status="completed")
    manager = _manager(file_sqlite_engine, repo)
    before = _queue_task_counts(file_sqlite_engine)

    model = ScriptedChatModel(
        responses=[
            AIMessage(
                content="Generating your chart now.",
                tool_calls=[
                    {
                        "name": "generate_chart",
                        "args": {"title": "Q3 revenue", "kind": "bar"},
                        "id": "cond-b-chart",
                    }
                ],
            ),
            AIMessage(content="Here is your bar chart of Q3 revenue."),
        ],
        i=0,
    )
    graph = _build_graph(real_graph_module, monkeypatch, model, manager)

    with caplog.at_level(logging.INFO):
        state = await _run(
            graph, HumanMessage(content="Draw a bar chart of Q3 revenue.")
        )

    rows = _gate_rows(caplog)
    assert len(rows) == 1
    assert "decision=allowed" in rows[0]
    assert "attestation_required=False" in rows[0]
    assert "last_real_user_found=True" in rows[0]
    # The tool call happened (chart flow exercised), but NO delegation
    # was recorded since the last real user message.
    assert "delegation_tool_call_total=0" in rows[0]
    assert "first_delegation_after_last_user_index=-1" in rows[0]

    assert _nudges(state["messages"]) == []
    assert model.calls_made == 2
    manager.enqueue_message.assert_not_called()
    assert _queue_task_counts(file_sqlite_engine) == before
    assert repo.get_attestation_denied_count(LEADER_ID) == 0


# ---------------------------------------------------------------------------
# (c) delegated mission — deny + EXACT nudge, attest in SAME ainvoke,
#     ledger counter reset (repo read-back)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_c_delegated_unattested_end_denied_then_attest_allows_and_resets(
    real_graph_module, file_sqlite_engine, caplog, monkeypatch
):
    _pin_enforce_mode(monkeypatch)
    repo = _repo(file_sqlite_engine)
    _seed_leader_and_child(repo, child_status="completed")
    manager = _manager(file_sqlite_engine, repo)

    model = ScriptedChatModel(
        responses=[
            AIMessage(
                content="Dispatching the subtask to the child.",
                tool_calls=[_send_call("cond-c-send")],
            ),
            # Un-attested END over a terminal child ⇒ the gate must deny.
            AIMessage(content="All subtasks complete. Mission finished."),
            # Post-nudge, SAME execution: attest, then final prose.
            AIMessage(
                content="Report delivered above; attesting completion.",
                tool_calls=[_attest_call("cond-c-attest")],
            ),
            AIMessage(content="Mission complete and attested."),
        ],
        i=0,
    )
    graph = _build_graph(real_graph_module, monkeypatch, model, manager)

    with caplog.at_level(logging.INFO):
        state = await _run(
            graph,
            HumanMessage(content="Delegate this subtask and finish the mission."),
        )

    messages = state["messages"]
    nudges = _nudges(messages)
    assert len(nudges) == 1

    # The nudge is the EXACT server constant (constant-parity — the graph
    # injects it verbatim, header + body + two-step teaching + mermaid).
    assert nudges[0].content == NUDGE_TEXT
    assert nudges[0].content.startswith("[SYSTEM CONTEXT: Completion Check Nudge]")
    assert "```mermaid" in nudges[0].content
    assert "flowchart TD" in nudges[0].content
    assert nudges[0].additional_kwargs == {
        "attestation_nudge": True,
        "attestation_nudge_denied_count": 1,
    }
    assert state["attestation_nudge_denied_count"] == 1

    # Canonical rows: deny (required=True) THEN attested allow.
    rows = _gate_rows(caplog)
    assert len(rows) == 2
    assert "decision=denied" in rows[0]
    assert "attestation_required=True" in rows[0]
    assert "delegation_tool_call_total=1" in rows[0]
    assert "first_delegation_after_last_user_index=1" in rows[0]
    assert "last_real_user_found=True" in rows[0]
    assert "decision=allowed" in rows[1]
    assert "attestation_present=True" in rows[1]

    # SAME ainvoke: 4 scripted turns consumed, final prose is last.
    assert model.calls_made == 4
    assert messages[-1].content == "Mission complete and attested."

    # Ledger counter RESET by the attested allow (ruling 1, trigger 1) —
    # repo read-back through the real row.
    assert repo.get_attestation_denied_count(LEADER_ID) == 0
    assert repo.get(LEADER_ID).completion_gate_escalated is False


# ---------------------------------------------------------------------------
# (d) MASQUERADE headline — enqueue-lane child report can no longer
#     masquerade as the last real user message
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_d_stamped_enqueue_lane_report_cannot_reset_delegation_window(
    real_graph_module, file_sqlite_engine, caplog, monkeypatch
):
    _pin_enforce_mode(monkeypatch)
    repo = _repo(file_sqlite_engine)
    # Child ALIVE: the parked-leader state (phase 1 must R2-allow).
    _seed_leader_and_child(repo, child_status="running")
    manager = _manager(file_sqlite_engine, repo)
    with Session(file_sqlite_engine) as session:
        session.add(
            DependencyWatcher(
                source_task_id="cond-d-source",
                target_instance_id=LEADER_ID,
                state=DependencyWatcherState.PENDING.value,
            )
        )
        session.commit()

    # ── Phase 1: delegate, then end the turn while the child runs ⇒ the
    # leader is legitimately PARKED (allowed_legitimate_pending_wakeup).
    model = ScriptedChatModel(
        responses=[
            AIMessage(
                content="Dispatching to the child; I will wait for its report.",
                tool_calls=[_send_call("cond-d-send")],
            ),
            AIMessage(content="Child is working; ending my turn while it runs."),
            # Phase 3 (after the stamped report arrives): claim done ⇒
            # deny … then the clean attest exit.
            AIMessage(content="The child reported back; the whole mission is complete."),
            AIMessage(
                content="Report delivered above; attesting completion.",
                tool_calls=[_attest_call("cond-d-attest")],
            ),
            AIMessage(content="Mission complete and attested."),
        ],
        i=0,
    )
    graph = _build_graph(real_graph_module, monkeypatch, model, manager)

    with caplog.at_level(logging.INFO):
        await _run(
            graph,
            HumanMessage(content="Delegate this to the child and wait."),
        )
        parked_rows = _gate_rows(caplog)
        assert len(parked_rows) == 1
        assert "decision=allowed_legitimate_pending_wakeup" in parked_rows[0]
        assert "attestation_required=True" in parked_rows[0]
        caplog.clear()

        # ── Phase 2: the child completes (real row updates — the same
        # state the production completion path leaves behind). Status
        # transitions must use ``transition_status_if`` (atomic guard).
        repo.transition_status_if(
            CHILD_ID, "completed", allowed_from=("running", "idle")
        )
        with Session(file_sqlite_engine) as session:
            watcher = session.exec(
                select(DependencyWatcher).where(
                    DependencyWatcher.target_instance_id == LEADER_ID
                )
            ).one()
            watcher.state = DependencyWatcherState.FIRED.value
            session.add(watcher)
            session.commit()
        assert manager.count_pending_children(LEADER_ID) == 0
        assert manager.count_live_descendants(LEADER_ID) == 0

        # ── Phase 3: deliver the child report through the REAL enqueue
        # lane: a real message_queue row carrying the internal_report
        # source, materialized by the PRODUCTION constructor.
        from daemon.services.instance_messaging import _build_graph_input

        report_content = (
            f"[child report | {CHILD_ID}] All tasks complete — 5/5 green."
        )
        with Session(file_sqlite_engine) as session:
            row = MessageQueue(
                message_id="mq-cond-d-child-report",
                instance_id=LEADER_ID,
                content=report_content,
                source=f"internal_report:{CHILD_ID}",
            )
            session.add(row)
            session.commit()
            session.refresh(row)

        stamped_input = _build_graph_input(
            row.content, row.message_id, message_source=row.source
        )
        stamped_msg = stamped_input["messages"][-1]
        # THE FIX: the constructed message carries the stamp.
        assert stamped_msg.additional_kwargs == {
            "injected_message": True,
            "source": f"internal_report:{CHILD_ID}",
        }

        # Controls of the SAME factory: the pre-fix BARE shape (no
        # source) stays bare, and an external api source stays bare.
        assert _build_graph_input(row.content, "mq-cond-d-bare")["messages"][
            -1
        ].additional_kwargs == {}
        assert _build_graph_input(
            row.content, "mq-cond-d-api", message_source="api"
        )["messages"][-1].additional_kwargs == {}

        # Counterfactual (the regression this fix exists for): with the
        # BARE shape the report WOULD have counted as the last real user
        # message and RELAXED the gate (no send_message after it); with
        # the stamp the window stays armed.
        from daemon.services.attestation_scanner import (
            find_last_real_user_index,
            scan_delegation_after_last_user,
        )

        ai_send = AIMessage(
            content="dispatching", tool_calls=[_send_call("cond-d-cf")]
        )
        ai_final = AIMessage(content="mission complete")
        user0 = HumanMessage(content="Delegate this to the child and wait.")
        bare_window = [user0, ai_send, HumanMessage(content=row.content), ai_final]
        stamped_window = [user0, ai_send, stamped_msg, ai_final]
        assert scan_delegation_after_last_user(bare_window).delegation_since_last_user is False
        assert find_last_real_user_index(bare_window) == 2  # the bare report masquerades
        assert scan_delegation_after_last_user(stamped_window).delegation_since_last_user is True
        assert find_last_real_user_index(stamped_window) == 0  # user anchor survives

        # Wake the parked leader with the stamped delivery (same thread).
        state = await graph.ainvoke(
            stamped_input,
            config={
                "configurable": {"thread_id": LEADER_ID},
                "recursion_limit": 60,
            },
        )

    # The stamped report landed in the checkpoint with its kwargs intact.
    delivered = [
        m
        for m in state["messages"]
        if isinstance(m, HumanMessage)
        and m.additional_kwargs.get("source") == f"internal_report:{CHILD_ID}"
    ]
    assert len(delivered) == 1
    assert delivered[0].additional_kwargs["injected_message"] is True

    # THE BITE: the un-attested END AFTER the stamped report is STILL
    # DENIED — the window was NOT reset (denied row carries
    # attestation_required=True), then the attest exit allows.
    rows = _gate_rows(caplog)
    assert len(rows) == 2
    assert "decision=denied" in rows[0]
    assert "attestation_required=True" in rows[0]
    assert "decision=allowed" in rows[1]

    nudges = _nudges(state["messages"])
    assert len(nudges) == 1
    assert nudges[0].content == NUDGE_TEXT
    assert nudges[0].additional_kwargs == {
        "attestation_nudge": True,
        "attestation_nudge_denied_count": 1,
    }
    assert model.calls_made == 5
    assert repo.get_attestation_denied_count(LEADER_ID) == 0


# ---------------------------------------------------------------------------
# (e) self-reference trap — the nudge itself must not reset the window
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_e_nudge_does_not_reset_window_second_end_still_denied(
    real_graph_module, file_sqlite_engine, caplog, monkeypatch
):
    _pin_enforce_mode(monkeypatch)
    repo = _repo(file_sqlite_engine)
    _seed_leader_and_child(repo, child_status="completed")
    manager = _manager(file_sqlite_engine, repo)

    model = ScriptedChatModel(
        responses=[
            AIMessage(
                content="Delegating before I claim completion.",
                tool_calls=[_send_call("cond-e-send")],
            ),
            # First un-attested END ⇒ deny 1 + nudge.
            AIMessage(content="Everything is done, nothing pending."),
            # SECOND un-attested END (after the nudge) ⇒ must STILL be
            # denied — the nudge is injected traffic, not a real user
            # message, so the delegation window stays armed.
            AIMessage(content="Really done now."),
            # Clean exit: attest, final prose.
            AIMessage(
                content="Report delivered above; attesting completion.",
                tool_calls=[_attest_call("cond-e-attest")],
            ),
            AIMessage(content="Mission complete and attested."),
        ],
        i=0,
    )
    graph = _build_graph(real_graph_module, monkeypatch, model, manager)

    with caplog.at_level(logging.INFO):
        state = await _run(
            graph,
            HumanMessage(content="Delegate and push through to the end."),
        )

    # TWO denies, each attestation_required=True; the second deny proves
    # the nudge did not relax the gate.
    rows = _gate_rows(caplog)
    assert len(rows) == 3
    assert "decision=denied" in rows[0]
    assert "attestation_required=True" in rows[0]
    assert "decision=denied" in rows[1]
    assert "attestation_required=True" in rows[1]
    assert "decision=allowed" in rows[2]
    assert "attestation_present=True" in rows[2]

    nudges = _nudges(state["messages"])
    assert len(nudges) == 2
    # Escalating counts, checkpoint-durable on the nudge kwargs.
    assert nudges[0].additional_kwargs["attestation_nudge_denied_count"] == 1
    assert nudges[1].additional_kwargs["attestation_nudge_denied_count"] == 2
    assert state["attestation_nudge_denied_count"] == 2

    # The PREDICATE bite: both nudges fail the real-user ladder, and the
    # last real user message is still the ORIGINAL user turn (the
    # window anchor never moved).
    from daemon.services.attestation_scanner import find_last_real_user_index
    from daemon.services.attestation_scanner import is_real_user_message

    assert all(not is_real_user_message(n) for n in nudges)
    messages = state["messages"]
    last_real = find_last_real_user_index(messages)
    assert messages[last_real].content == "Delegate and push through to the end."

    assert model.calls_made == 5
    assert repo.get_attestation_denied_count(LEADER_ID) == 0
