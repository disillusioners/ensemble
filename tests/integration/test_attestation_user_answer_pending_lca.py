"""FIX-2 acceptance: answer-gate blindness (incident 6a0d60c9).

Incident 6a0d60c9: the leader held an OPEN user-answer suspension
(``route_outcome=answer_gate_existing_turn``, ``suspension_reason=
awaiting_answer``, ``target_work_id=ad4743f1``) and answered every
turn-end with a terse "ending turn, awaiting go/no-go" one-liner while
ALL descendants were terminal-completed. The gate's deny inputs were
all zero and ``attestation_required`` was False → plain allow — but
the marker scan then fired on the phrasing, the judge said "not a
completion report", and the marker path converted the allow into a
deny + nudge. 115 nudge injections later the user had still not
answered: the pending party was the USER and the leader cannot
progress alone.

The fix: ``user_answer_pending`` — the FIFTH legitimate-pending input.
When the leader has an OPEN awaiting-answer suspension handle at gate
time the gate PLAIN ALLOWS before ANY trigger/judge work: no marker
scan, no judge, no nudge, no hint, no counter movement.

Detection mechanism (DB-backed, NOT the in-memory question pack
keying): ``InstanceManager.has_open_user_answer`` →
``TaskRepository.has_open_answer_handle_for_gate`` — the SAME
``suspension_reason='awaiting_answer'`` + ``resume_target_turn_id IS
NOT NULL`` + ``status='paused'`` handle the answer endpoint's resume
selector (``find_suspended_turn_for_answer`` →
``answer_gate_existing_turn``) consumes, plus a freshness guard (the
handle must be the instance's NEWEST task row — a leaked pre-revive
handle must never arm a permanent allow bypass). The handle
self-clears when the answer is consumed (``ResumeTurn`` flips
``status='paused' → 'pending'`` and nulls the handle columns in ONE
atomic guarded UPDATE) — NO stale-allow window.

Harness mirrors ``test_attestation_marker_routing_lca.py`` (stub
manager + ledger, mocked judge). NO real LLM calls.
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from daemon.services import attestation_report_judge as judge_mod
from daemon.services.attestation_gate import (
    GateSettings,
    build_gate_config,
)
from daemon.services.attestation_judge_resolver import (
    reset_llm_judge_resolver_for_tests,
)
from daemon.services.attestation_resolver import (
    reset_attestation_resolver_for_tests,
)
from daemon.graph import create_attestation_gate_node


@pytest.fixture(autouse=True)
def _reset_resolvers(monkeypatch):
    """Fresh resolver cache + clean kill-switch env per test (hermetic)."""
    monkeypatch.delenv(
        "ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED", raising=False
    )
    reset_attestation_resolver_for_tests()
    reset_llm_judge_resolver_for_tests()
    yield
    reset_attestation_resolver_for_tests()
    reset_llm_judge_resolver_for_tests()


async def _judge_must_not_be_called(config, user_payload, *, timeout_s):
    raise AssertionError(
        "FIX-2: the judge MUST NOT be called while a user answer is "
        "pending — the awaiting answer is the whole turn's purpose"
    )


async def _judge_incomplete(config, user_payload, *, timeout_s):
    return (
        '{"is_complete_report": false, "reason": "mid-work"}',
        "fake-quick",
    )


def _make_node(
    *,
    instance_id: str,
    answer_pending,
    denied_count: int = 0,
    manager: MagicMock | None = None,
    ledger: MagicMock | None = None,
):
    """Gate node with the ``has_open_user_answer`` facade set to
    ``answer_pending`` (a bool, or any sentinel value for the
    duck-typing guard tests)."""
    if manager is None:
        manager = MagicMock()
        manager.count_pending_children.return_value = 0
        manager.get_queued_or_expected_wakeups.return_value = 0
        manager.count_live_descendants.return_value = 0
        manager.count_busy_descendants.return_value = 0
    try:
        manager.has_open_user_answer.return_value = answer_pending
    except AttributeError:
        # spec'd mock without the facade — the missing-facade test
        # relies on the attr being absent entirely.
        pass

    if ledger is None:
        ledger = MagicMock()
        ledger.increment.return_value = 1
        ledger.reset.return_value = True
        ledger.set_escalated_and_reset.return_value = True
        ledger.get.return_value = 0

    settings = GateSettings("enforce", 3, 3)
    config = build_gate_config(
        instance_id,
        settings,
        llm_judge_enabled=True,
    )
    node = create_attestation_gate_node(
        config,
        settings,
        manager,
        instance_id,
        denied_count_getter=(lambda: denied_count),
        ledger=ledger,
    )
    return node, manager, ledger


def _awaiting_turn(final_text: str) -> dict:
    """The 6a0d60c9 turn-end shape: terse marker-laden one-liner while
    the leader awaits the user's answer."""
    return {
        "messages": [
            HumanMessage(content="please review and give me a go/no-go"),
            AIMessage(content=final_text),
        ]
    }


# ─────────────────────────────────────────────────────────────────────────────
# AC-1: awaiting-answer leader ends "ending turn, awaiting..." → plain allow
# ─────────────────────────────────────────────────────────────────────────────


def test_answer_pending_plain_allows_zero_judge_zero_nudge(monkeypatch, caplog):
    """Marker-laden turn-end + OPEN awaiting-answer handle → plain allow.

    Pins the whole FIX-2 contract at once: no nudge, no judge call, no
    marker scan, no counter movement, and the canonical log row carries
    ``user_answer_pending=true``.
    """
    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", _judge_must_not_be_called)

    node, _manager, ledger = _make_node(
        instance_id="fix2-plain-allow", answer_pending=True
    )
    with caplog.at_level(logging.INFO, logger="daemon.graph"), caplog.at_level(
        logging.INFO, logger="daemon.services.attestation_gate"
    ):
        result = asyncio.run(
            node(
                _awaiting_turn(
                    "Ending turn, awaiting your go/no-go."
                ),
                config={"configurable": {"thread_id": "fix2-plain-allow"}},
            )
        )

    # Zero nudges — the turn just ends.
    assert "messages" not in result, (
        "FIX-2: an awaiting-answer turn-end MUST plain-allow with NO nudge"
    )
    assert result.get("attestation_route") is None
    # Zero judge calls (the monkeypatched judge raises if called).
    # Zero counter movement.
    ledger.increment.assert_not_called()
    ledger.reset.assert_not_called()
    ledger.set_escalated_and_reset.assert_not_called()
    # Canonical row: user_answer_pending=true, marker scan never ran.
    row = next(
        r.message
        for r in caplog.records
        if "event=leader_completion_gate " in r.message
    )
    assert "user_answer_pending=True" in row, (
        f"FIX-2: canonical row MUST carry user_answer_pending=True — "
        f"got: {row}"
    )
    assert "marker_hit=False" in row, (
        "FIX-2: the marker scan MUST NOT run (plain allow BEFORE any "
        "trigger work) — marker_hit stays at its default"
    )
    assert "marker_judge_verdict=<none>" in row


def test_answer_pending_suppresses_delegated_denial(monkeypatch, caplog):
    """Delegated mission (attestation_required=True) + answer pending →
    plain allow — NOT a deny.

    Without FIX-2 this shape would DENY at ``decide()`` step (7):
    delegated, not attested, nothing pending. The awaiting USER answer
    is a legitimate pending input and must hold the gate open without
    counter movement.
    """
    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", _judge_must_not_be_called)

    manager = MagicMock()
    manager.count_pending_children.return_value = 0
    manager.get_queued_or_expected_wakeups.return_value = 0
    manager.count_live_descendants.return_value = 0
    manager.count_busy_descendants.return_value = 0

    node, _manager, ledger = _make_node(
        instance_id="fix2-delegated",
        answer_pending=True,
        manager=manager,
    )
    delegation_ai = AIMessage(
        content="",
        tool_calls=[
            {"name": "send_message", "args": {"target": "child-id"}, "id": "c1"}
        ],
    )
    with caplog.at_level(logging.INFO, logger="daemon.services.attestation_gate"):
        result = asyncio.run(
            node(
                {
                    "messages": [
                        HumanMessage(content="do the work"),
                        delegation_ai,
                        AIMessage(content="Ending turn, awaiting go/no-go."),
                    ]
                },
                config={"configurable": {"thread_id": "fix2-delegated"}},
            )
        )

    assert "messages" not in result
    assert result.get("attestation_route") is None
    ledger.increment.assert_not_called()
    row = next(
        r.message
        for r in caplog.records
        if "event=leader_completion_gate " in r.message
    )
    assert "user_answer_pending=True" in row
    assert "decision=allowed_legitimate_pending_wakeup" in row, (
        "FIX-2: the answer-pending arm resolves to the "
        "legitimate-pending-wakeup decision"
    )


def test_answer_pending_counter_never_moves_across_repeats(monkeypatch):
    """Repeated awaiting-answer turn-ends: zero counter writes each time.

    The incident loop ran 115 injections; post-fix the same loop shape
    (answer still open) performs NO ledger writes at all.
    """
    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", _judge_must_not_be_called)

    node, _manager, ledger = _make_node(
        instance_id="fix2-no-counter-movement", answer_pending=True
    )
    for _ in range(4):
        result = asyncio.run(
            node(
                _awaiting_turn("Ending turn, awaiting go/no-go."),
                config={
                    "configurable": {
                        "thread_id": "fix2-no-counter-movement"
                    }
                },
            )
        )
        assert "messages" not in result
    ledger.increment.assert_not_called()
    ledger.set_escalated_and_reset.assert_not_called()
    ledger.reset.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# AC-2: stale/negative guard — no open answer state → gate behaves as before
# ─────────────────────────────────────────────────────────────────────────────


def test_no_answer_pending_deny_path_still_reachable(monkeypatch, caplog):
    """``has_open_user_answer=False`` + markers + judge-no → deny + nudge.

    The detection must NOT false-positive into a permanent allow
    bypass — with no open answer state the deny path stays fully
    reachable (this is the pre-FIX-2 behavior, byte-preserved).
    """
    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", _judge_incomplete)

    node, _manager, ledger = _make_node(
        instance_id="fix2-negative-guard", answer_pending=False
    )
    with caplog.at_level(logging.INFO, logger="daemon.services.attestation_gate"):
        result = asyncio.run(
            node(
                _awaiting_turn("Ending turn, awaiting your go/no-go."),
                config={
                    "configurable": {"thread_id": "fix2-negative-guard"}
                },
            )
        )

    assert "messages" in result, (
        "no open answer: the deny path MUST stay reachable"
    )
    assert result["attestation_route"] == "agent"
    ledger.increment.assert_called_once()
    row = next(
        r.message
        for r in caplog.records
        if "event=leader_completion_gate " in r.message
    )
    assert "user_answer_pending=False" in row
    assert "marker_hit=True" in row, (
        "no open answer: the marker scan runs as before"
    )


def test_truthy_non_bool_facade_value_never_arms_the_bypass(monkeypatch):
    """Duck-typing guard: ONLY the literal ``True`` arms the bypass.

    A mocked / duck-typed facade returning a truthy-but-not-True value
    (the MagicMock auto-attr shape — every pre-FIX-2 test embedding in
    the repo) MUST read as NOT pending so the deny path stays
    reachable. A loose truthiness check here would silently allow
    every existing mock-manager embedding and could false-positive
    into the new silent-completion hole.
    """
    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", _judge_incomplete)

    for garbage in ("yes", 1, MagicMock(), ["true"]):
        node, _manager, ledger = _make_node(
            instance_id="fix2-duck-guard", answer_pending=garbage
        )
        result = asyncio.run(
            node(
                _awaiting_turn("Ending turn, awaiting your go/no-go."),
                config={
                    "configurable": {"thread_id": "fix2-duck-guard"}
                },
            )
        )
        assert "messages" in result, (
            f"truthy non-bool value {garbage!r} MUST NOT arm the "
            f"plain-allow bypass"
        )
        ledger.increment.assert_called()


def test_missing_facade_reads_as_not_pending(monkeypatch):
    """A manager without the facade at all behaves exactly as before."""
    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", _judge_incomplete)

    manager = MagicMock(spec=[
        "count_pending_children",
        "count_busy_descendants",
        "count_live_descendants",
        "get_queued_or_expected_wakeups",
    ])
    manager.count_pending_children.return_value = 0
    manager.get_queued_or_expected_wakeups.return_value = 0
    manager.count_live_descendants.return_value = 0
    manager.count_busy_descendants.return_value = 0

    node, _manager, ledger = _make_node(
        instance_id="fix2-missing-facade",
        answer_pending=False,
        manager=manager,
    )
    result = asyncio.run(
        node(
            _awaiting_turn("Ending turn, awaiting your go/no-go."),
            config={"configurable": {"thread_id": "fix2-missing-facade"}},
        )
    )
    assert "messages" in result
    ledger.increment.assert_called_once()


def test_facade_db_error_degrades_to_not_pending(monkeypatch, caplog):
    """A raising ``has_open_user_answer`` rides the existing DB-seam
    fail-open (allow with -1 sentinels) — and the row reads
    ``user_answer_pending=False`` (the allow is the pre-existing
    whole-eval fail-open, NOT an answer-pending bypass)."""
    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", _judge_incomplete)

    manager = MagicMock()
    manager.count_pending_children.return_value = 0
    manager.get_queued_or_expected_wakeups.return_value = 0
    manager.count_live_descendants.return_value = 0
    manager.count_busy_descendants.return_value = 0
    manager.has_open_user_answer.side_effect = RuntimeError("db down")

    node, _manager, _ledger = _make_node(
        instance_id="fix2-db-error",
        answer_pending=False,
        manager=manager,
    )
    with caplog.at_level(logging.INFO, logger="daemon.services.attestation_gate"):
        result = asyncio.run(
            node(
                _awaiting_turn("Ending turn, awaiting your go/no-go."),
                config={"configurable": {"thread_id": "fix2-db-error"}},
            )
        )
    # Existing DB-seam fail-open: allow.
    assert "messages" not in result
    assert result.get("attestation_route") is None
    row = next(
        r.message
        for r in caplog.records
        if "event=leader_completion_gate_db_error" in r.message
    )
    assert "user_answer_pending" not in row or "pending_children=-1" in row
