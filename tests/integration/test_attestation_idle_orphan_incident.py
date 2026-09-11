"""Incident b08f40fe — IDLE-orphan live_descendants must NOT allow completion.

REGRESSION TEST for the 2026-09-11 premature-completion incident:

    Leader ``b08f40fe`` completed 2026-09-11 14:50:10 UTC via gate
    branch (5) ``allowed_legitimate_pending_wakeup`` with inputs
    ``pending_children=0, wakeups=0, live_descendants=4``. All four
    "live descendants" were IDLE-orphan grandchildren spawned by tester
    ``c6f57749`` but NEVER dispatched: zero ``message_queue`` rows,
    zero ``message_metadata`` rows, every dependency watcher already
    FIRED. The old ``count_live_descendants`` counted IDLE
    unconditionally, so dead delegation weight held branch (5) open
    ("a child report will revive the leader" — orphans never report)
    and the leader completed without attestation.

The fix (two-set live semantics in
``InstanceManager.count_live_descendants``): dormant descendants
(``IDLE``/``QUEUED``) count live ONLY with work en route — an
unprocessed ``message_queue`` row or an unsettled QUEUED/ACTIVE
``job_queue_items`` row. Running-ish descendants
(``RUNNING``/``WAITING``/``WAITING_CHILDREN``/``PAUSED``) stay
unconditionally live; terminal stays excluded.

This file reconstructs the EXACT incident tree shape at the GATE level
(8 terminal children + 4 never-dispatched IDLE-orphan grandchildren,
watchers fired ⇒ ``pending_children=0``, no held wakeups, delegation
window satisfied, no attestation, mode=enforce) and pins:

1. BEFORE (incident shape) — ``decision=denied`` + nudge, branch (5)
   MUST NOT fire, denied counter increments.
2. AFTER (orphans terminated + ``attest_completion`` in window) —
   attested allow, counter reset.
3. AFTER-NOT-ATTESTED (orphans terminated, still no attestation) —
   the deny ladder still fires (termination alone unlocks nothing;
   the original protection survives).

The tree is planted directly into the permanent ``instances.parent_id``
lineage (the facade's only read source); the gate manager is the
shared ``attestation_manager_factory`` whose ``count_live_descendants``
DELEGATES to the real production facade, so this file exercises the
actual two-set semantics end-to-end through ``evaluate()``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from sqlmodel import Session

from daemon.repositories.instance.models import Instance, InstanceStatus
from tests.support.scripted_chat_model import ScriptedChatModel

INSTANCE_ID = "attestation-leader-e2e"

# Incident-faithful tree constants (b08f40fe / tester c6f57749).
TESTER_CHILD_ID = "orphan-tree-tester-c6f57749"
TERMINAL_CHILD_STATUSES = [
    # 8 terminal direct children — the eight REAL finished delegations.
    InstanceStatus.COMPLETED.value,
    InstanceStatus.TERMINATED.value,
    InstanceStatus.ERROR.value,
    InstanceStatus.FAILED.value,
    InstanceStatus.COMPLETED.value,
    InstanceStatus.COMPLETED.value,
    InstanceStatus.ERROR.value,
    InstanceStatus.TERMINATED.value,
]
ORPHAN_GRANDCHILD_IDS = [f"orphan-gc-{i}" for i in range(4)]


@pytest.fixture(autouse=True)
def _enforce_mode(monkeypatch):
    monkeypatch.setenv("ENSEMBLE_LEADER_ATTESTATION_MODE", "enforce")
    monkeypatch.setenv("WATCHOVER_ENABLED", "false")


def _settings():
    from daemon.services.attestation_gate import GateSettings

    return GateSettings(mode="enforce", window=3, deny_bound=3)


def _seed_instance(engine, instance_id, parent_id, status):
    """Insert an Instance row directly into the permanent lineage.

    The facade reads the permanent ``instances.parent_id`` lineage
    only — bypassing ``repo.create`` keeps the test independent of the
    transient ``instance_hierarchy`` working set (the engine fixture
    deliberately does NOT create that table).
    """
    now = datetime.now(timezone.utc).isoformat()
    with Session(engine) as session:
        session.add(
            Instance(
                instance_id=instance_id,
                agent_id="worker" if parent_id else "leader",
                agent_dir="./agents/worker" if parent_id else "./agents/leader",
                parent_id=parent_id,
                status=status,
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()


def _seed_incident_tree(engine, orphan_status: str) -> None:
    """Plant the EXACT b08f40fe tree: 8 terminal children + 4 IDLE orphans.

    The four orphans are GRANDCHILDREN (parented under the tester
    child), matching the incident depth. ``orphan_status`` selects the
    grandchild status so the AFTER variants can re-plant with
    ``TERMINATED``.
    """
    for i, status in enumerate(TERMINAL_CHILD_STATUSES):
        _seed_instance(engine, f"terminal-child-{i}", INSTANCE_ID, status)
    # The tester child is one of the terminal direct children; its
    # never-dispatched spawn calls left the grandchildren below.
    _seed_instance(engine, TESTER_CHILD_ID, INSTANCE_ID, InstanceStatus.COMPLETED.value)
    for orphan_id in ORPHAN_GRANDCHILD_IDS:
        _seed_instance(engine, orphan_id, TESTER_CHILD_ID, orphan_status)


def _terminate_orphans(engine) -> None:
    """AFTER-step: the operator terminates the orphan grandchildren."""
    with Session(engine) as session:
        for orphan_id in ORPHAN_GRANDCHILD_IDS:
            row = session.get(Instance, orphan_id)
            assert row is not None
            row.status = InstanceStatus.TERMINATED.value
        session.commit()


@tool
def attest_completion() -> dict:
    """Test stub of the attestation tool (no-op confirmation)."""
    return {"attested": True}


def _delegate_ai() -> AIMessage:
    return AIMessage(
        content="delegating to children",
        tool_calls=[
            {
                "name": "send_message",
                "args": {"target": "child"},
                "id": "dispatch-incident",
            }
        ],
    )


def _attest_ai() -> AIMessage:
    return AIMessage(
        content="Attesting completion.",
        tool_calls=[{"name": "attest_completion", "args": {}, "id": "call-attest"}],
    )


def _build(graph_module, model, manager, checkpointer, *, tools=None):
    from unittest.mock import patch

    graph_module.build_instance_llms = lambda **_: (model, model)
    with patch(
        "daemon.services.attestation_gate.resolve_gate_settings",
        return_value=_settings(),
    ):
        return graph_module.build_instance_graph(
            tools=[attest_completion] if tools is None else tools,
            checkpointer=checkpointer,
            llm_config={"model": "scripted-test", "api_key": "test"},
            system_prompt="scripted b08f40fe incident regression",
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


# ─────────────────────────────────────────────────────────────────────────────
# 1. BEFORE — the incident shape: branch (5) must NOT fire.
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_b08f40fe_idle_orphan_tree_denies_instead_of_branch5_allow(
    real_graph_module,
    memory_saver,
    file_sqlite_engine,
    attestation_repository,
    attestation_manager_factory,
    caplog,
):
    """THE incident regression (gate level).

    Tree: 8 terminal children + 4 never-dispatched IDLE-orphan
    grandchildren; watchers FIRED (``pending_children=0``), no held
    wakeups, delegation anchored (send_message after the real user
    message), NO attestation, mode=enforce.

    Pre-fix this exact shape returned
    ``allowed_legitimate_pending_wakeup`` (branch 5) on
    ``live_descendants=4`` and the leader completed prematurely.
    Post-fix the four orphans carry no work en route ⇒
    ``live_descendants=0`` ⇒ DENIED + nudge, counter increments.
    """
    repo, _leader = attestation_repository
    _seed_incident_tree(file_sqlite_engine, InstanceStatus.IDLE.value)

    manager = attestation_manager_factory(
        file_sqlite_engine,
        repo,
        pending_children=0,  # all watchers FIRED (incident fact)
        queued_wakeups=0,    # orphans never scheduled anything
        # live_descendants=None → REAL two-set facade via delegation.
    )

    model = ScriptedChatModel(
        # Bound is 3 ⇒ after the first deny the graph routes back to
        # ``agent``; script enough un-attested turn-ends for the deny
        # loop (mirrors ``test_all_descendants_terminal_still_denies``).
        responses=[
            _delegate_ai(),
            *[AIMessage(content=f"hallucinated {i}") for i in range(4)],
            AIMessage(content="final hallucinated"),
        ],
        i=0,
    )
    graph = _build(real_graph_module, model, manager, memory_saver, tools=[])
    with caplog.at_level(logging.INFO):
        state = await graph.ainvoke(
            {"messages": [HumanMessage(content="finish the mission")]},
            config={
                "configurable": {"thread_id": INSTANCE_ID},
                "recursion_limit": 30,
            },
        )

    log_text = caplog.text
    # (1.i) Branch (5) must NOT fire — the b08f40fe premature-allow.
    assert "decision=allowed_legitimate_pending_wakeup" not in log_text
    # (1.ii) The TRUE zero is visible in the canonical schema row.
    assert "live_descendants=0" in log_text
    # (1.iii) DENIED + nudge (the deny ladder fires instead).
    assert "decision=denied" in log_text
    assert _nudge_count(state["messages"]) >= 1
    # (1.iv) The denied counter increments from the true zero — the
    # first deny is pinned by its ledger log line (the deny loop
    # continues to bound/escalation afterwards, so the FINAL counter
    # depends on loop length; the 0→1 increment is the incident-relevant
    # pin).
    assert "denied_count=0 -> next=1" in log_text
    # (1.v) The third-input facade was actually consulted.
    manager.count_live_descendants.assert_called()


# ─────────────────────────────────────────────────────────────────────────────
# 2. AFTER — orphans terminated + attested → allow, counter reset.
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_b08f40fe_after_orphans_terminated_and_attested_allows(
    real_graph_module,
    memory_saver,
    file_sqlite_engine,
    attestation_repository,
    attestation_manager_factory,
    caplog,
):
    """AFTER the orphans are terminated AND the leader attests → ALLOWED.

    Same incident tree with the four orphans TERMINATED; the leader's
    next turn-end is denied+nudged once (no attestation yet), then the
    scripted ``attest_completion`` turn-end routes to the attested
    allow (branch 4) with the counter reset to 0.
    """
    repo, _leader = attestation_repository
    _seed_incident_tree(file_sqlite_engine, InstanceStatus.IDLE.value)
    _terminate_orphans(file_sqlite_engine)

    manager = attestation_manager_factory(
        file_sqlite_engine,
        repo,
        pending_children=0,
        queued_wakeups=0,
    )

    model = ScriptedChatModel(
        responses=[
            _delegate_ai(),
            # Turn-end 1: all-terminal tree, not attested → DENIED + nudge.
            AIMessage(content="hallucinated completion"),
            # Turn-end 2: attested → ALLOWED (counter reset).
            _attest_ai(),
            AIMessage(content="Done."),
        ],
        i=0,
    )
    graph = _build(real_graph_module, model, manager, memory_saver)
    with caplog.at_level(logging.INFO):
        state = await graph.ainvoke(
            {"messages": [HumanMessage(content="finish the mission")]},
            config={
                "configurable": {"thread_id": INSTANCE_ID},
                "recursion_limit": 30,
            },
        )

    log_text = caplog.text
    # (2.i) Exactly one deny (the un-attested first end)…
    assert "decision=denied" in log_text
    assert _nudge_count(state["messages"]) == 1
    # (2.ii) …then the attested allow (branch 4) — the leader can
    # complete once it attests. Branch 5 never fires (orphans are
    # terminal and nothing else carries work).
    assert "decision=allowed" in log_text
    assert "decision=allowed_legitimate_pending_wakeup" not in log_text
    # (2.iii) Attested allow resets the denied counter to 0.
    after = repo.get(INSTANCE_ID)
    assert after.attestation_denied_count == 0


# ─────────────────────────────────────────────────────────────────────────────
# 3. AFTER-NOT-ATTESTED — termination alone unlocks nothing.
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_b08f40fe_terminated_orphans_without_attestation_still_denies(
    real_graph_module,
    memory_saver,
    file_sqlite_engine,
    attestation_repository,
    attestation_manager_factory,
    caplog,
):
    """Orphans terminated but still NO attestation → deny ladder fires.

    Terminating the orphans only removes the (already-false) live
    signal; it does not satisfy the attestation requirement. Without
    ``attest_completion`` the leader is still denied — the original
    protection must survive the two-set amendment.
    """
    repo, _leader = attestation_repository
    _seed_incident_tree(file_sqlite_engine, InstanceStatus.IDLE.value)
    _terminate_orphans(file_sqlite_engine)

    manager = attestation_manager_factory(
        file_sqlite_engine,
        repo,
        pending_children=0,
        queued_wakeups=0,
    )

    model = ScriptedChatModel(
        # Same deny-loop shape as test 1 (termination alone unlocks
        # nothing): enough un-attested turn-ends for the loop.
        responses=[
            _delegate_ai(),
            *[AIMessage(content=f"hallucinated {i}") for i in range(4)],
            AIMessage(content="final hallucinated"),
        ],
        i=0,
    )
    graph = _build(real_graph_module, model, manager, memory_saver, tools=[])
    with caplog.at_level(logging.INFO):
        await graph.ainvoke(
            {"messages": [HumanMessage(content="finish the mission")]},
            config={
                "configurable": {"thread_id": INSTANCE_ID},
                "recursion_limit": 30,
            },
        )

    log_text = caplog.text
    assert "decision=denied" in log_text
    assert "decision=allowed" not in log_text
    assert "decision=allowed_legitimate_pending_wakeup" not in log_text
    # The deny ladder increments from the true zero (first-deny pin;
    # the final counter is loop-length dependent past the bound).
    assert "denied_count=0 -> next=1" in log_text
