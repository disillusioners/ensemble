"""Unit seam: the in-graph nudge is state-only, never a manager delivery.

Mode pinned: ``enforce`` (the deny branch is only meaningful there).
"""
from __future__ import annotations

import asyncio
import inspect
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from daemon.graph import (
    ATTESTATION_NUDGE_TEXT,
    create_attestation_gate_node,
    should_end_attestation,
)
from daemon.services.attestation_gate import GateSettings, build_gate_config
from daemon.services.attestation_ledger import safe_increment, safe_reset
# Review punch-list W2: hermetic kill-switch isolation (NIT-7 mirror). The
# deny-path tests in this file used to depend on deterministic MagicMock
# construction failure for their deny paths. The pattern below clears the
# LLM-judge kill-switch + both cached resolvers per-test so an outer
# ``.env`` / CI env mutation of
# ``ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED`` cannot leak in and
# silently disable the judge for the assertion that depends on the
# deny+nudge fall-through running unchanged. Mirrors the autouse fixture
# in ``tests/unit/test_attestation_judge_wiring.py``.
from daemon.services.attestation_judge_resolver import (
    reset_llm_judge_resolver_for_tests,
)
from daemon.services.attestation_resolver import (
    reset_attestation_resolver_for_tests,
)


@pytest.fixture(autouse=True)
def _isolate_judge_kill_switch(monkeypatch):
    monkeypatch.delenv(
        "ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED", raising=False
    )
    reset_attestation_resolver_for_tests()
    reset_llm_judge_resolver_for_tests()
    yield
    reset_attestation_resolver_for_tests()
    reset_llm_judge_resolver_for_tests()


def test_deny_injects_checkpoint_plain_dict_and_routes_to_agent():
    """Case (a): delegation mission (real user msg → AI dispatching
    via send_message → AI done-without-attest) → DENY + nudge. The
    new nudge text leads with a ``[SYSTEM CONTEXT: Completion
    Check Nudge]`` header (Phase 6 fastfollow, 2026-09-06) but the
    marker kwargs (``attestation_nudge=True``) are unchanged for
    back-compat."""
    manager = MagicMock()
    manager.count_pending_children.return_value = 0
    manager.get_queued_or_expected_wakeups.return_value = 0
    manager.count_live_descendants.return_value = 0
    manager.enqueue_message = MagicMock()
    manager.revive = MagicMock()
    manager.send_message = MagicMock()

    ledger = MagicMock()
    ledger.increment.return_value = 1
    ledger.reset.return_value = True
    config = build_gate_config("nudge-unit", GateSettings("enforce", 3, 3))
    node = create_attestation_gate_node(
        config,
        GateSettings("enforce", 3, 3),
        manager,
        "nudge-unit",
        denied_count_getter=lambda: 0,
        ledger=ledger,
    )

    # Delegated mission: AI dispatched a child via send_message.
    delegation_ai = AIMessage(
        content="",
        tool_calls=[
            {"name": "send_message", "args": {"target": "child-id"}, "id": "c1"}
        ],
    )
    result = asyncio.run(
        node(
            {
                "messages": [
                    HumanMessage(content="please do it"),
                    delegation_ai,
                    AIMessage(content="I am done without attesting"),
                ]
            },
            config={"configurable": {"thread_id": "nudge-unit"}},
        )
    )

    nudge = result["messages"][0]
    assert isinstance(nudge, HumanMessage)
    # Pin verbatim — the new nudge text leads with the system-context
    # header so the LLM recognizes it as system-origin. Imported from
    # the canonical module to keep the single source of truth.
    assert nudge.content == ATTESTATION_NUDGE_TEXT
    # Header present — Phase 6 fastfollow amendment.
    assert nudge.content.startswith("[SYSTEM CONTEXT: Completion Check Nudge]")
    # Marker kwargs UNCHANGED for back-compat.
    assert nudge.additional_kwargs["attestation_nudge"] is True
    assert nudge.additional_kwargs["attestation_nudge_denied_count"] == 1
    assert result["attestation_route"] == "agent"
    assert result["attestation_nudge_denied_count"] == 1
    ledger.increment.assert_called_once()
    assert ledger.increment.call_args.args[0] == "nudge-unit"

    # The MVP's negative assertion lock: no durable delivery or revive seam.
    manager.enqueue_message.assert_not_called()
    manager.revive.assert_not_called()
    manager.send_message.assert_not_called()


def test_quick_question_mission_allows_without_attest():
    """Case (b): quick-question mission (real user msg → plain AI
    answer, NO send_message, NO attest) → ALLOWED with
    attestation_required=False. The user-spec'd CORE case: a chart
    request, a quick follow-up, a clarification — completes
    normally without demanding ``attest_completion``. The
    conditional gate is OFF when no delegation happened since the
    last real user message."""
    manager = MagicMock()
    manager.count_pending_children.return_value = 0
    manager.get_queued_or_expected_wakeups.return_value = 0
    manager.count_live_descendants.return_value = 0
    manager.enqueue_message = MagicMock()
    manager.revive = MagicMock()
    manager.send_message = MagicMock()

    ledger = MagicMock()
    ledger.increment.return_value = 1
    ledger.reset.return_value = True
    config = build_gate_config("quick-unit", GateSettings("enforce", 3, 3))
    node = create_attestation_gate_node(
        config,
        GateSettings("enforce", 3, 3),
        manager,
        "quick-unit",
        denied_count_getter=lambda: 0,
        ledger=ledger,
    )

    # No delegation — the AI produces a plain prose answer; no
    # tool calls. Real user message; no attestation in tail.
    # Use a long detailed completion report (>= 150 words, no
    # marker phrases) so the 2026-09-12 length trigger does NOT
    # fire either — the cheap allow path is preserved end-to-end.
    long_answer = (
        "The answer is 42; explanation follows in the sections "
        "below. Nothing pending, all shipped. Patch 1 fixed the "
        "off-by-one in the cache TTL calculator; the unit tests now "
        "exercise both the elapsed-second and wall-clock-second "
        "boundaries at the second and minute granularity. Patch 2 "
        "cleaned up the dead imports in the worker pool module "
        "after the migration, removing the legacy compatibility shim "
        "and the related test scaffolding. Patch 3 refactored the "
        "error-reporting decorator so the stack-frame metadata is "
        "consistent across all four call sites in the graph node "
        "and the manager facade. Patch 4 added the missing "
        "operator-boot log line for the new resolver module so "
        "operators can grep the boot summary for the resolved "
        "effective values. All four patches passed their respective "
        "suites on the first run with no flake; the integration "
        "matrix is green end-to-end across all environments we "
        "maintain. No follow-ups outstanding; the mission is "
        "complete and ready for review by the next teammate in the "
        "chain."
    )
    result = asyncio.run(
        node(
            {
                "messages": [
                    HumanMessage(content="what's the answer to X?"),
                    AIMessage(content=long_answer),
                ]
            },
            config={"configurable": {"thread_id": "quick-unit"}},
        )
    )

    # CRUCIAL: the gate ALLOWS — no nudge, no counter increment,
    # no attestation demanded. The conditional gate is OFF AND
    # the 2026-09-12 length trigger does NOT fire (the report is
    # >= 150 words and has no marker phrases). The cheap allow
    # path is preserved end-to-end.
    assert "messages" not in result
    assert result["attestation_route"] is None
    # Ledger untouched — counter NEVER incremented when the gate
    # is OFF (the conditional-attestation fastfollow).
    ledger.increment.assert_not_called()
    ledger.reset.assert_not_called()
    # Negative assertion lock (unchanged from the delegated case).
    manager.enqueue_message.assert_not_called()
    manager.revive.assert_not_called()
    manager.send_message.assert_not_called()


def test_nudge_header_is_first_nonblank_line_and_marker_unchanged():
    """Case (h): nudge header present + marker kwargs unchanged for
    back-compat with the existing marker-based exclusion predicates.
    Pinning here guards against accidental regression: any future
    nudge tweak that drops the header or renames the marker would
    silently break the self-reference trap (the gate stops excluding
    its own injection from the "real user message" predicate)."""
    assert ATTESTATION_NUDGE_TEXT.startswith(
        "[SYSTEM CONTEXT: Completion Check Nudge]\n\n"
    ), "nudge must lead with the system-context header line + blank line"
    # The body (substance) is preserved: the work-is-not-finished
    # message and the conditional gate explanation.
    assert "The work is not yet finished" in ATTESTATION_NUDGE_TEXT
    assert "CONDITIONAL on delegation" in ATTESTATION_NUDGE_TEXT
    # Two-step contract retained.
    assert "FIRST" in ATTESTATION_NUDGE_TEXT
    assert "ALONE" in ATTESTATION_NUDGE_TEXT
    # The unconditional-MUST-sentence framing is gone (the prompts
    # no longer teach it; the nudge now self-explains the conditional
    # requirement) — but the "you MUST call the attest_completion
    # tool before finishing" wording is retained inside the body for
    # delegated missions specifically.
    assert "you MUST call the attest_completion tool" in ATTESTATION_NUDGE_TEXT


def test_route_back_is_a_plain_conditional_edge_not_a_command():
    # Keep this assertion at the routing seam: the gate writes a state hint
    # and the graph's conditional edge decides the next node.
    source = inspect.getsource(should_end_attestation)
    assert "return \"agent\"" in source
    assert "Command" not in source
    assert should_end_attestation({"attestation_route": "agent"}) == "agent"
    assert should_end_attestation({"attestation_route": None}).endswith("__end__")


def test_attested_allow_only_resets_and_dry_skip_writes_are_not_denial_delivery():
    # These are protocol-shape checks; the integration matrix owns the full
    # graph/DB side effects and mode behavior.
    ledger = MagicMock()
    safe_increment(ledger, "inst", "epoch")
    safe_reset(ledger, "inst")
    ledger.increment.assert_called_once_with("inst", "epoch")
    ledger.reset.assert_called_once_with("inst")


# ─────────────────────────────────────────────────────────────────────────────
# CANONICAL BYTE PIN (2026-09-07 review suggestion, ADOPTED) — the full
# literal of ``ATTESTATION_NUDGE_TEXT`` lives HERE, in ONE place. The pins
# above compare the injected message against the auto-tracking import
# (``assert x == ATTESTATION_NUDGE_TEXT``) — tautology-passing: they can
# never fail on a constant edit. This literal anchor DOES: any byte change
# to the constant fails this test and forces a conscious review of the
# nudge text (header line through the mermaid closing fence). This file is
# the canonical literal anchor for the constant.
# ─────────────────────────────────────────────────────────────────────────────

EXPECTED_NUDGE_TEXT_CANONICAL = (
    "[SYSTEM CONTEXT: Completion Check Nudge]\n\n"
    "The work is not yet finished — check current progress "
    "(tasks/children status) and continue.\n\n"
    "This gate is CONDITIONAL on delegation: it fires ONLY when a "
    "child was dispatched (a send_message tool call happened) since "
    "the last real user message. Plain questions, chart requests, "
    "and other non-delegating turns do NOT trigger this gate. When "
    "you have dispatched a child this mission, the work is not "
    "complete until you attest.\n\n"
    "Attestation is a SEPARATE step: FIRST deliver your full "
    "detailed final report as its own message (outcomes, evidence, "
    "follow-ups), THEN call attest_completion ALONE as a "
    "subsequent step — never bundle the report into the attestation "
    "tool-call message (at most a one-line ack such as \"Report "
    "delivered above; attesting completion.\").\n\n"
    "Reminder: when — and only when — the work is truly complete "
    "(delegated children have all reported and you have the full "
    "picture), you MUST call the attest_completion tool before "
    "finishing; completions without that call are premature and "
    "will be blocked again."
    "\nCompletion flow:\n"
    "```mermaid\n"
    "flowchart TD\n"
    '    TurnEnd["Your turn is about to end"] --> UsedSend{"Did you use send_message since the last user message?"}\n'
    '    UsedSend -- No --> FinishFree["Finish freely - no attestation needed"]\n'
    '    UsedSend -- Yes --> AttestRecent{"Is attest_completion in your last 3 messages?"}\n'
    '    AttestRecent -- Yes --> FinishGate["Finish - gate allows"]\n'
    '    AttestRecent -- No --> ReportJudge{"Did the gate\'s report judge confirm a real completion report?"}\n'
    '    ReportJudge -- Yes --> FinishGate\n'
    '    ReportJudge -- No --> Nudged["You are being nudged: work not finished"]\n'
    '    Nudged --> CheckContinue["Check children and task status, continue working"]\n'
    '    CheckContinue --> TrulyDone{"Work truly complete?"}\n'
    '    TrulyDone -- "No, keep working" --> CheckContinue\n'
    '    TrulyDone -- Yes --> Report["Deliver detailed report as its own message"]\n'
    '    Report --> Attest["Then call attest_completion alone"]\n'
    '    Attest --> FinishGate\n'
    "```"
)


def test_attestation_nudge_text_canonical_byte_pin():
    """Full-literal byte pin of ``ATTESTATION_NUDGE_TEXT`` — the
    canonical literal anchor (single home: this file). Fails on ANY
    byte change to the constant (the auto-tracking import pins above
    cannot). When the nudge text legitimately evolves, update
    ``EXPECTED_NUDGE_TEXT_CANONICAL`` deliberately in the same commit
    and note the wording change in the LCA planning decisions log."""
    assert ATTESTATION_NUDGE_TEXT == EXPECTED_NUDGE_TEXT_CANONICAL
    # Structural guards so a bad literal can't silently equal via
    # empty/None tricks.
    assert EXPECTED_NUDGE_TEXT_CANONICAL.startswith(
        "[SYSTEM CONTEXT: Completion Check Nudge]"
    )
    assert EXPECTED_NUDGE_TEXT_CANONICAL.rstrip().endswith("```")
