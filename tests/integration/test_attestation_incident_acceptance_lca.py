"""LCA b08f40fe acceptance — full two-phase deny/attest reconstruction.

This file is the acceptance for the LCA merge gate (Job 2). The existing
regression file ``test_attestation_idle_orphan_incident.py`` covers the
gate decision log lines and the deny-loop counter pin; this file adds the
three acceptance assertions the incident ground truth requires:

1. **Verbatim incident phrase in the leader's final message.**  The
   b08f40fe ground truth states the leader's final message carried the
   literal phrase ``"Awaiting final four: C12a/b/c + blame-worker. Then
   I aggregate and write RESULTS. Ending turn."``  Phase 1 of this
   acceptance scripts the leader to emit exactly that phrase as its
   un-attested turn-end; the test pins the phrase IS in the final
   message tape while the gate DENIES the leader.

2. **No silent completion (instance row NOT COMPLETED).**  The
   pre-fix defect was that branch (5) ``allowed_legitimate_pending_
   wakeup`` fired on idle-orphan ``live_descendants=4`` and the leader
   completed silently.  Phase 1 pins the gate did NOT fire branch (5)
   (log-line proxy) AND the leader's instance row status is NOT
   ``COMPLETED`` after the deny ladder terminates — defense in depth
   so a future regression that drops the log line but still lets the
   row transition is caught.

3. **``completion_gate_escalated`` cleared by the attested allow.**
   Ruling-2's single reset op (repository.py: ``reset_attestation_
   denied_count``) clears BOTH ``attestation_denied_count`` AND
   ``completion_gate_escalated`` in one UPDATE.  Phase 2 pre-seeds
   ``completion_gate_escalated=True`` to prove the attested allow's
   reset path clears the escalation too.

Production code is FROZEN — this file is test-only, new file, does
NOT modify any existing matrix test.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from sqlmodel import Session

from daemon.repositories.instance.models import Instance, InstanceStatus
from tests.support.scripted_chat_model import ScriptedChatModel

INSTANCE_ID = "attestation-leader-e2e"

# Verbatim incident phrase (b08f40fe ground truth).
INCIDENT_PHRASE = (
    "Awaiting final four: C12a/b/c + blame-worker. "
    "Then I aggregate and write RESULTS. Ending turn."
)

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
    """Mode: enforce (the ship default). No mode mixing on acceptance."""
    monkeypatch.setenv("ENSEMBLE_LEADER_ATTESTATION_MODE", "enforce")
    monkeypatch.setenv("WATCHOVER_ENABLED", "false")


def _settings():
    from daemon.services.attestation_gate import GateSettings

    return GateSettings(mode="enforce", window=3, deny_bound=3)


def _seed_instance(engine, instance_id, parent_id, status):
    """Insert an Instance row directly into the permanent lineage.

    The facade reads the permanent ``instances.parent_id`` lineage only —
    bypassing ``repo.create`` keeps the test independent of the transient
    ``instance_hierarchy`` working set (the engine fixture deliberately does
    NOT create that table).
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

    The four orphans are GRANDCHILDREN (parented under the tester child),
    matching the incident depth. ``orphan_status`` selects the grandchild
    status so the AFTER variant can re-plant with ``TERMINATED``.
    """
    for i, status in enumerate(TERMINAL_CHILD_STATUSES):
        _seed_instance(engine, f"terminal-child-{i}", INSTANCE_ID, status)
    # The tester child is one of the terminal direct children; its
    # never-dispatched spawn calls left the grandchildren below.
    _seed_instance(engine, TESTER_CHILD_ID, INSTANCE_ID, InstanceStatus.COMPLETED.value)
    for orphan_id in ORPHAN_GRANDCHILD_IDS:
        _seed_instance(engine, orphan_id, TESTER_CHILD_ID, orphan_status)


def _terminate_orphans(engine) -> None:
    """Phase-2 step: the operator terminates the orphan grandchildren."""
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
    """Scripted dispatch turn — anchors the mission as delegated.

    The 2026-09-06 amendment makes the gate conditional on delegation;
    without an anchored send_message the legacy wakeup/pending allow
    path is exercised and the deny ladder never fires.  See
    ``test_attestation_delegation_allow.py`` for the contract pin.
    """
    return AIMessage(
        content="delegating to children",
        tool_calls=[
            {
                "name": "send_message",
                "args": {"target": "child"},
                "id": "dispatch-acceptance-lca",
            }
        ],
    )


def _verbatim_phrase_ai() -> AIMessage:
    """The b08f40fe ground-truth leader final message (un-attested).

    Pre-fix the leader emitted this phrase and the gate fired branch
    (5) allowing the leader to complete silently.  Post-fix the same
    emit triggers a DENY + nudge + the deny ladder.
    """
    return AIMessage(content=INCIDENT_PHRASE)


def _build(graph_module, model, manager, checkpointer, *, tools=None):
    graph_module.build_instance_llms = lambda **_: (model, model)
    with patch(
        "daemon.services.attestation_gate.resolve_gate_settings",
        return_value=_settings(),
    ):
        return graph_module.build_instance_graph(
            tools=[attest_completion] if tools is None else tools,
            checkpointer=checkpointer,
            llm_config={"model": "scripted-test", "api_key": "test"},
            system_prompt="scripted b08f40fe acceptance",
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


def _ai_contents(messages) -> list[str]:
    return [m.content for m in messages if isinstance(m, AIMessage)]


# ─────────────────────────────────────────────────────────────────────────────
# Phase 1 — full incident reconstruction with verbatim phrase
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_lca_b08f40fe_phase1_incident_phrase_denies_no_silent_completion(
    real_graph_module,
    memory_saver,
    file_sqlite_engine,
    attestation_repository,
    attestation_manager_factory,
    caplog,
):
    """PHASE 1 ACCEPTANCE — incident phrase → DENY + nudge + no silent completion.

    Reconstructs the EXACT b08f40fe tree at the gate level: 8 terminal
    children + tester COMPLETED + 4 IDLE-orphan grandchildren. The
    leader's final message carries the verbatim incident phrase. The
    gate MUST:

    * deny (``decision=denied`` in log)
    * inject the nudge (in-graph attestation nudge)
    * increment ``attestation_denied_count`` from 0 → 1 (first-deny pin)
    * NOT fire branch (5) ``allowed_legitimate_pending_wakeup`` (the
      silent-completion path the fix kills)
    * leave the leader's instance row status != COMPLETED (defense in
      depth on the silent-completion invariant)
    * contain the verbatim phrase somewhere in the message tape
      (pins the test against an unfaithful synthetic rephrase)
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
        # loop. The FIRST post-dispatch AIMessage carries the verbatim
        # incident phrase — the content the leader "would have completed
        # with" pre-fix.
        responses=[
            _delegate_ai(),
            _verbatim_phrase_ai(),
            *[AIMessage(content=f"hallucinated continuation {i}") for i in range(3)],
            AIMessage(content="final hallucinated"),
        ],
        i=0,
    )
    graph = _build(real_graph_module, model, manager, memory_saver, tools=[])
    before_status = repo.get(INSTANCE_ID).status

    with caplog.at_level(logging.INFO):
        state = await graph.ainvoke(
            {"messages": [HumanMessage(content="finish the mission")]},
            config={
                "configurable": {"thread_id": INSTANCE_ID},
                "recursion_limit": 30,
            },
        )

    log_text = caplog.text
    ai_contents = _ai_contents(state["messages"])

    # 1.a — gate DENIES.
    assert "decision=denied" in log_text, (
        "Phase 1 acceptance: gate must deny the un-attested leader."
    )
    # 1.b — in-graph nudge injected.
    assert _nudge_count(state["messages"]) >= 1, (
        "Phase 1 acceptance: gate must inject the attestation nudge."
    )
    # 1.c — first-deny counter pin (0 → 1).
    assert "denied_count=0 -> next=1" in log_text, (
        "Phase 1 acceptance: counter must increment from the true zero."
    )
    # 1.d — branch (5) MUST NOT fire (the silent-completion path).
    assert "decision=allowed_legitimate_pending_wakeup" not in log_text, (
        "Phase 1 acceptance: branch (5) MUST NOT fire — the pre-fix defect."
    )
    # 1.e — the two-set live semantics surface the true zero.
    assert "live_descendants=0" in log_text, (
        "Phase 1 acceptance: idle orphans (no work en route) count zero live."
    )
    # 1.f — verbatim incident phrase present in the message tape.
    assert any(INCIDENT_PHRASE in c for c in ai_contents), (
        "Phase 1 acceptance: verbatim incident phrase must appear in the leader's "
        "messages, so the test pins against a synthetic rephrase."
    )
    # 1.g — instance row did NOT transition to COMPLETED (silent completion).
    # Defense in depth: even if a future regression removes the log
    # proxy, the row read still pins no silent completion.
    after_status = repo.get(INSTANCE_ID).status
    assert after_status != InstanceStatus.COMPLETED.value, (
        "Phase 1 acceptance: leader row MUST NOT be COMPLETED — silent completion "
        f"would be a fatal regression. before={before_status!r} after={after_status!r}"
    )
    # Third-input facade was actually consulted (proves we're exercising
    # the production two-set semantics end-to-end).
    manager.count_live_descendants.assert_called()


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2 — orphans terminated + attest → ALLOWED + counter AND escalation reset
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_lca_b08f40fe_phase2_orphans_terminated_and_attested_allows_clears_escalation(
    real_graph_module,
    memory_saver,
    file_sqlite_engine,
    attestation_repository,
    attestation_manager_factory,
    caplog,
):
    """PHASE 2 ACCEPTANCE — recovery allows AND clears escalation.

    Same incident tree, but BEFORE the scripted run:

    1. The 4 IDLE-orphan grandchildren are TERMINATED (the operator's
       recovery action).
    2. ``completion_gate_escalated`` is set True (proves the reset op
       on the attested-allow path clears the escalation flag too).

    The leader's scripted sequence: dispatch → un-attested turn-end
    (denied+nudged once) → ``attest_completion`` (allowed) → final
    message. The gate MUST:

    * deny exactly once (the un-attested turn-end).
    * allow exactly once (the attested turn-end).
    * NEVER fire branch (5) (orphans are terminal, nothing else carries
      work).
    * reset ``attestation_denied_count`` to 0.
    * reset ``completion_gate_escalated`` to False (proves the attested
      allow's reset op shares the lifecycle).
    """
    repo, _leader = attestation_repository
    _seed_incident_tree(file_sqlite_engine, InstanceStatus.IDLE.value)
    _terminate_orphans(file_sqlite_engine)
    # Pre-seed the escalation flag — the reset op MUST clear it on
    # the attested allow path (ruling-2 single-reset contract).
    repo.set_completion_gate_escalated(INSTANCE_ID)
    # Pre-increment the counter too so the reset is unambiguous.
    repo.increment_attestation_denied_count(INSTANCE_ID, "acceptance-phase2-pre")
    assert repo.get(INSTANCE_ID).completion_gate_escalated is True
    assert repo.get(INSTANCE_ID).attestation_denied_count == 1

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
            # Turn-end 2: attested → ALLOWED (counter + escalation cleared).
            AIMessage(
                content="Attesting completion.",
                tool_calls=[
                    {"name": "attest_completion", "args": {}, "id": "call-attest-acceptance"}
                ],
            ),
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

    # 2.a — exactly one deny + exactly one allow, never branch (5).
    assert log_text.count("decision=denied") == 1, (
        f"Phase 2 acceptance: exactly one deny expected, got "
        f"{log_text.count('decision=denied')} in: {log_text}"
    )
    assert log_text.count("decision=allowed") == 1, (
        f"Phase 2 acceptance: exactly one allow expected, got "
        f"{log_text.count('decision=allowed')} in: {log_text}"
    )
    assert "decision=allowed_legitimate_pending_wakeup" not in log_text, (
        "Phase 2 acceptance: branch (5) MUST NOT fire — orphans terminal, "
        "nothing else carries work."
    )
    # 2.b — exactly one nudge (the un-attested first end).
    assert _nudge_count(state["messages"]) == 1, (
        f"Phase 2 acceptance: exactly one nudge expected, got "
        f"{_nudge_count(state['messages'])}."
    )

    # 2.c — counter reset to 0 (attested-allow reset path).
    after = repo.get(INSTANCE_ID)
    assert after.attestation_denied_count == 0, (
        f"Phase 2 acceptance: counter must reset to 0, got "
        f"{after.attestation_denied_count}."
    )
    # 2.d — escalation flag cleared (proves the reset op shares the lifecycle).
    assert after.completion_gate_escalated is False, (
        f"Phase 2 acceptance: completion_gate_escalated must be cleared by the "
        f"attested-allow reset op, got {after.completion_gate_escalated!r}."
    )