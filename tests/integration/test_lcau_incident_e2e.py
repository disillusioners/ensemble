"""Incident E2E (INDEPENDENT construction) — LCA user-intent merge gate.

The 4dfded83 incident class must die. Ground truth: the user asked a
direct question ("what is current service tool description that agents
will see?"); the leader answered FULLY but CONVERSATIONALLY (3029
chars, not report-shaped); the intent-blind completion gate nudged
anyway because the answer did not look like a report. The feature
under test fixes the class: the fused completion judge bundle now
carries SOURCE U — the user's original mission request (<=2000 chars,
id-redacted, anchored on the delegation scanner's
``last_real_user_index``, re-checked via ``is_real_user_message``,
fail-closed to omission) — and the judge prompt carries an
intent-fulfillment instruction (a genuine answer = complete regardless
of formality; a formal report that ignores the ask = not_complete).

This file was constructed INDEPENDENTLY from that incident spec — the
scenarios, transcripts, and question wording are original; harness
MECHANICS (graph-build shape, scripted-model contract, file-backed
SQLite fixtures) follow the established attestation integration
harness. The dev's feature unit tests were neither read nor copied.

Two scenarios, both driven through the REAL completion-gate machinery
(graph end_candidate -> gate evaluate -> evaluate_resolver_activation
-> assemble_fused_bundle -> judge_fused_bundle_async STUBBED at the
graph seam — the ONE judge call site imports its callable at call
time, so patching the module attribute intercepts the real invocation
with the REAL bundle text in hand):

SCENARIO A — ALLOW-PATH (the 4dfded83 shape, must now pass):
    real user question + delegation evidence (send_message tool call)
    + nothing pending (quiet tree) + un-attested leader whose final
    message is a thorough CONVERSATIONAL answer (300+ words, no
    markdown headers, no summary table, no "Report" framing). Judge
    stub returns complete. ASSERT: bundle carries the SOURCE U section
    with the user's question content; witnesses
    user_message_included=True AND bundle_u_chars>0 on the
    leader_completion_resolver_eval row; decision = allow/complete
    (fused-judge rescue, NO re-ask); NO nudge injected; NO denial
    ledger write.

SCENARIO B — DENY-PATH (counterpart, formal != complete):
    same setup, but the leader's final message is formal report-shaped
    text (headers, structure) that does NOT address the user's
    question. Judge stub returns not_complete. ASSERT: deny + nudge
    engaged (nudge HumanMessage injected with the canonical kwargs,
    state counter bumped, canonical decision=denied row); U still
    present in the bundle.

Production code is FROZEN — this file is test-only, new file, and does
not modify any existing test.
"""

from __future__ import annotations

import logging
import re
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool

from daemon.graph import ATTESTATION_NUDGE_TEXT as NUDGE_TEXT
from daemon.services.attestation_report_judge import FusedJudgeResult
from tests.support.scripted_chat_model import ScriptedChatModel

INSTANCE_ID = "attestation-leader-e2e"

# The U-section header rendered by assemble_fused_bundle (source-of-
# truth pin: the bundle must carry the intent section BY NAME).
SOURCE_U_HEADER = "=== SOURCE U: the user's original request for this mission ==="

# Scenario A question — ORIGINAL wording for this independent
# construction (UUID-free so id-redaction is a no-op and the content
# survives verbatim into the bundle U section).
USER_QUESTION = (
    "Our mock billing service keeps failing its health check — "
    "which port does it listen on, and what's the exact command "
    "to restart it?"
)
# A distinctive fragment asserted present in the bundle U section.
USER_QUESTION_FRAGMENT = "which port does it listen on"

# Scenario A leader final message — thorough CONVERSATIONAL answer
# (300+ words), directly and fully answering the question, and
# deliberately NOT report-shaped: no markdown headers, no summary
# table, no "## Report" framing. This is the 4dfded83 answer shape.
CONVERSATIONAL_ANSWER = (
    "The mock billing service listens on port 8091 — that's the value "
    "set by BILLING_SERVICE_PORT in services/billing/.env, and it's the "
    "port the health probe pings every 30 seconds, so a failing health "
    "check almost always means the probe is reaching a port where "
    "nothing (or the wrong thing) is listening. The most common cause "
    "is a stale process still bound to 8091 from a previous run. Here "
    "is how I would restart it cleanly. First, look at what currently "
    "holds the port with lsof -i :8091; if something is squatting "
    "there, kill that specific PID rather than pkill-ing by name, "
    "because the billing service shares a process group with the mock "
    "ledger worker and a broad kill takes the ledger down with it. "
    "Then restart with ./scripts/restart_mock_billing.sh --env "
    "services/billing/.env — the script reloads the env file, waits "
    "for the port to actually free, and starts the service under a "
    "supervised shell so it survives your terminal closing. Give it "
    "about ten seconds to come up; the readiness endpoint is GET "
    "/readyz on the same port and it returns a body with status ok "
    "once the ledger fixtures are loaded, at which point the health "
    "probe goes green again. If it still fails health checks after a "
    "clean restart, the next suspect is the fixture cache: delete "
    "services/billing/.cache/fixtures.json and restart once more — a "
    "corrupt fixture cache produces exactly the flapping "
    "healthy-then-unhealthy pattern you are seeing, because the "
    "service boots fine and then errors on the first ledger replay. "
    "As a last resort, run BILLING_LOG_LEVEL=debug "
    "./scripts/restart_mock_billing.sh to get request-level logs; the "
    "health probe logs one line per failing check with the reason, so "
    "you can usually spot the offender in the first screenful. That's "
    "the whole story: port 8091, restart via "
    "scripts/restart_mock_billing.sh, and the fixture cache is the "
    "usual suspect if a clean restart does not settle it."
)

# Scenario B leader final message — formal REPORT-SHAPED text (headers,
# structure) that does NOT address the user's question. The intent-
# fulfillment instruction must score this not_complete.
FORMAL_REPORT_IGNORES_QUESTION = (
    "## Status Report\n"
    "\n"
    "**Summary of Activities**\n"
    "\n"
    "- Coordinated the delegated investigation across the assigned subtree.\n"
    "- Collected and consolidated intermediate findings from the dispatched worker.\n"
    "- Verified all checkpoints were persisted in the expected order.\n"
    "\n"
    "**Detailed Findings**\n"
    "\n"
    "1. The delegation graph executed within nominal parameters.\n"
    "2. No blocking defects were surfaced during the sweep of the assigned scope.\n"
    "3. Resource utilization remained within expected bounds throughout the window.\n"
    "\n"
    "**Next Steps**\n"
    "\n"
    "- Continue monitoring the subtree for stragglers.\n"
    "- Archive the working notes to the shared context directory.\n"
    "\n"
    "**Conclusion**\n"
    "\n"
    "All assigned workstreams are closed out. The mission record is "
    "ready for archival and the operational surface is stable."
)


@tool
def attest_completion() -> dict:
    """The real leader attestation tool used by the graph's ToolNode."""
    return {"attested": True, "timestamp": "2026-09-18T00:00:00+00:00"}


@pytest.fixture(autouse=True)
def _enforce_mode(monkeypatch):
    """Pin mode=enforce and guarantee the fused LLM judge kill-switch is ON.

    The judge enablement resolver is cached-once (Pattern C); clear the
    cache AFTER normalizing the env so this module always re-resolves
    to the default-ON state regardless of the outer test environment.
    """
    monkeypatch.setenv("ENSEMBLE_LEADER_ATTESTATION_MODE", "enforce")
    monkeypatch.setenv("WATCHOVER_ENABLED", "false")
    monkeypatch.delenv("ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED", raising=False)
    from daemon.services.attestation_judge_resolver import (
        reset_llm_judge_resolver_for_tests,
    )

    reset_llm_judge_resolver_for_tests()


def _settings():
    from daemon.services.attestation_gate import GateSettings

    return GateSettings(mode="enforce", window=3, deny_bound=3)


def _build_graph(graph_module, model, manager, checkpointer):
    graph_module.build_instance_llms = lambda **_: (model, model)
    with patch(
        "daemon.services.attestation_gate.resolve_gate_settings",
        return_value=_settings(),
    ):
        return graph_module.build_instance_graph(
            tools=[attest_completion],
            checkpointer=checkpointer,
            llm_config={"model": "scripted-test", "api_key": "test"},
            system_prompt="scripted attestation leader",
            user_language="Auto",
            language_check_enabled=False,
            manager=manager,
            graph_config={"configurable": {"thread_id": INSTANCE_ID}},
            attestation_enabled=True,
        )


def _judge_stub(verdict: str, captured_bundles: list[str]):
    """Build a graph-seam judge stub: records the REAL bundle text.

    The stub mirrors :func:`judge_fused_bundle_async`'s contract
    (positional bundle_text, keyword config/timeout_s; never raises)
    and returns a fully-populated :class:`FusedJudgeResult` so the
    graph's derived ``judge_invoked`` flag and the fused-judge
    observability row are exercised for real.
    """
    is_complete = verdict == "complete"

    async def _stub(bundle_text: str, *, config, timeout_s=None) -> FusedJudgeResult:
        captured_bundles.append(bundle_text)
        return FusedJudgeResult(
            invoked=True,
            is_complete=is_complete,
            verdict=verdict,
            rationale=(
                "stub: answer addresses the user's original request"
                if is_complete
                else "stub: formal report does not address the user's question"
            ),
            model="stub-judge",
            latency_ms=1,
            attempt=1,
        )

    return _stub


def _nudges(messages):
    return [
        m
        for m in messages
        if isinstance(m, HumanMessage) and m.additional_kwargs.get("attestation_nudge")
    ]


def _resolver_eval_row_text(caplog) -> str:
    """Return the ONE FIRED leader_completion_resolver_eval row text.

    Anchors on the TRAILING space after the event name —
    ``leader_completion_resolver_eval`` is a prefix of
    ``leader_completion_resolver_eval_error`` and the bare prefix
    would match both (documented grep hazard). A deny-ladder run also
    emits a row for the POST-ATTEST meta_bypass evaluation
    (``fired=False``); only the ``fired=True`` row carries the scored
    bundle, so exactly one fired row is the invariant.
    """
    rows = [
        record.getMessage()
        for record in caplog.records
        if "event=leader_completion_resolver_eval " in record.getMessage()
        and " fired=True " in record.getMessage()
    ]
    assert rows, "expected the fired resolver eval row; none was emitted"
    assert len(rows) == 1, f"expected ONE fired resolver eval row, got {len(rows)}"
    return rows[0]


def _assert_bundle_carries_user_intent(bundle_text: str, eval_row_text: str) -> None:
    """Shared witness assertions: SOURCE U present in bundle + eval row."""
    # (1) Bundle carries the SOURCE U section carrying the question.
    assert SOURCE_U_HEADER in bundle_text, (
        "fused bundle is missing the SOURCE U section header"
    )
    assert USER_QUESTION_FRAGMENT in bundle_text, (
        "fused bundle SOURCE U does not carry the user's question content"
    )
    # (2) Witnesses on the leader_completion_resolver_eval row.
    u_chars_match = re.search(r"bundle_u_chars=(\d+)", eval_row_text)
    assert u_chars_match, f"resolver eval row lacks bundle_u_chars: {eval_row_text}"
    assert int(u_chars_match.group(1)) > 0, (
        f"bundle_u_chars must be > 0, got {u_chars_match.group(1)}"
    )
    assert "user_message_included=True" in eval_row_text, (
        f"resolver eval row must carry user_message_included=True: {eval_row_text}"
    )


def _arm_log_capture(caplog) -> None:
    """INFO capture on the ROOT logger.

    The gate rows live on ``daemon.services.attestation_gate``, the
    resolver eval row on ``daemon.services.attestation_resolver_activation``,
    and the fused-judge rescue / fused-judge rows on ``daemon.graph`` —
    the graph logger inherits the root level, so scoping only the two
    service loggers silently drops the graph's INFO rows.
    """
    caplog.set_level(logging.INFO)


@pytest.mark.asyncio
async def test_scenario_a_conversational_full_answer_is_rescued_to_allow(
    real_graph_module,
    memory_saver,
    file_sqlite_engine,
    attestation_repository,
    attestation_manager_factory,
    caplog,
):
    """SCENARIO A — the 4dfded83 shape must now end in an ALLOW.

    Real user question + delegation evidence + quiet tree + un-attested
    leader whose final message fully answers the question
    conversationally. Judge stub returns complete -> the deny-band
    rescue fires -> allow END without attestation, no re-ask, no nudge,
    no denial ledger write.
    """
    repo, _instance = attestation_repository
    manager = attestation_manager_factory(file_sqlite_engine, repo)

    # Scenario-shape pins (self-documenting): thorough but conversational.
    assert len(CONVERSATIONAL_ANSWER.split()) >= 300
    assert "##" not in CONVERSATIONAL_ANSWER
    assert "Report" not in CONVERSATIONAL_ANSWER
    assert USER_QUESTION_FRAGMENT in USER_QUESTION

    model = ScriptedChatModel(
        responses=[
            # Delegation evidence: send_message AFTER the real user message
            # (anchors the gate's delegation scan + the U-section anchor).
            AIMessage(
                content="Delegating the billing-service check to a worker.",
                tool_calls=[
                    {
                        "name": "send_message",
                        "args": {"target": "billing-worker"},
                        "id": "lcau-a-dispatch",
                    }
                ],
            ),
            # Final message: thorough CONVERSATIONAL answer (the incident
            # shape — full answer, NOT report-shaped).
            AIMessage(content=CONVERSATIONAL_ANSWER),
        ],
        i=0,
    )
    graph = _build_graph(real_graph_module, model, manager, memory_saver)

    captured_bundles: list[str] = []
    _arm_log_capture(caplog)
    with patch(
        "daemon.services.attestation_report_judge.judge_fused_bundle_async",
        new=_judge_stub("complete", captured_bundles),
    ):
        final_state = await graph.ainvoke(
            {"messages": [HumanMessage(content=USER_QUESTION)]},
            config={
                "configurable": {"thread_id": INSTANCE_ID},
                "recursion_limit": 30,
            },
        )

    model.assert_all_responses_consumed()

    # The judge ran EXACTLY once over the REAL assembled bundle.
    assert len(captured_bundles) == 1, (
        f"judge must be invoked exactly once, got {len(captured_bundles)}"
    )

    # (1)+(2) Bundle carries SOURCE U with the question + eval-row witnesses.
    eval_row_text = _resolver_eval_row_text(caplog)
    _assert_bundle_carries_user_intent(captured_bundles[0], eval_row_text)
    assert "judge_verdict=complete" in eval_row_text
    assert "resolver_outcome=allow" in eval_row_text

    # (3) Decision = allow/complete: the fused-judge rescue fired and the
    # graph ENDED on the conversational answer (no re-ask injected after it).
    assert "fused-judge rescue" in caplog.text
    messages = final_state["messages"]
    assert messages[-1].content == CONVERSATIONAL_ANSWER, (
        "leader must be allowed to END on the conversational answer; "
        f"got trailing message: {messages[-1].content!r}"
    )
    assert final_state.get("attestation_nudge_denied_count", 0) == 0

    # (4) NO completion-check nudge fired: no nudge message injected, no
    # route-back delivery.
    assert _nudges(messages) == []
    manager.enqueue_message.assert_not_called()

    # (5) NO denial ledger write: the rescue returns before the Phase-3
    # ledger machinery, so the persisted counter must still be 0.
    assert repo.get_attestation_denied_count(INSTANCE_ID) == 0


@pytest.mark.asyncio
async def test_scenario_b_formal_report_ignoring_question_denies_with_nudge(
    real_graph_module,
    memory_saver,
    file_sqlite_engine,
    attestation_repository,
    attestation_manager_factory,
    caplog,
):
    """SCENARIO B — formal != complete: deny + nudge engaged, U still present.

    Same setup as A, but the leader's final message is formal
    report-shaped text that does not address the user's question. Judge
    stub returns not_complete -> the deny stands (the judge is a
    RESCUER, never the denier, but on not_complete the deny+nudge
    machinery engages) and SOURCE U is still in the scored bundle.
    """
    repo, _instance = attestation_repository
    manager = attestation_manager_factory(file_sqlite_engine, repo)

    # Counterpart scenario-shape pins: report-shaped, ignores the ask.
    assert "## " in FORMAL_REPORT_IGNORES_QUESTION
    assert USER_QUESTION_FRAGMENT not in FORMAL_REPORT_IGNORES_QUESTION
    assert "8091" not in FORMAL_REPORT_IGNORES_QUESTION

    model = ScriptedChatModel(
        responses=[
            AIMessage(
                content="Delegating the billing-service check to a worker.",
                tool_calls=[
                    {
                        "name": "send_message",
                        "args": {"target": "billing-worker"},
                        "id": "lcau-b-dispatch",
                    }
                ],
            ),
            # Final message: formal report-shaped text that does NOT
            # answer the user's question.
            AIMessage(content=FORMAL_REPORT_IGNORES_QUESTION),
            # After the deny nudge routes back, the leader attests.
            AIMessage(
                content="Attesting after the completion-check nudge.",
                tool_calls=[
                    {
                        "name": "attest_completion",
                        "args": {},
                        "id": "lcau-b-attest",
                    }
                ],
            ),
            AIMessage(content="Wrapped up after attesting."),
        ],
        i=0,
    )
    graph = _build_graph(real_graph_module, model, manager, memory_saver)

    captured_bundles: list[str] = []
    _arm_log_capture(caplog)
    with patch(
        "daemon.services.attestation_report_judge.judge_fused_bundle_async",
        new=_judge_stub("not_complete", captured_bundles),
    ):
        final_state = await graph.ainvoke(
            {"messages": [HumanMessage(content=USER_QUESTION)]},
            config={
                "configurable": {"thread_id": INSTANCE_ID},
                "recursion_limit": 30,
            },
        )

    model.assert_all_responses_consumed()

    # The judge ran EXACTLY once (the post-attest evaluation must not
    # re-invoke it — attested short-circuits the predicate).
    assert len(captured_bundles) == 1, (
        f"judge must be invoked exactly once, got {len(captured_bundles)}"
    )

    # SOURCE U still present in the denied evaluation's bundle.
    eval_row_text = _resolver_eval_row_text(caplog)
    _assert_bundle_carries_user_intent(captured_bundles[0], eval_row_text)
    assert "judge_verdict=not_complete" in eval_row_text
    assert "resolver_outcome=deny_nudge" in eval_row_text

    # Deny + nudge engaged (the designed deny machinery).
    messages = final_state["messages"]
    nudges = _nudges(messages)
    assert len(nudges) == 1
    assert nudges[0].content == NUDGE_TEXT
    assert nudges[0].additional_kwargs == {
        "attestation_nudge": True,
        "injected_message": True,
        "attestation_nudge_denied_count": 1,
    }
    assert final_state["attestation_nudge_denied_count"] == 1
    assert caplog.text.count("decision=denied") == 1
    assert caplog.text.count("decision=allowed") == 1
    # The deny path must NOT be a rescue.
    assert "fused-judge rescue" not in caplog.text
    # The nudge carried the completion-check (re-ask) semantics: it is a
    # message injected AFTER the formal report.
    formal_index = messages.index(
        next(m for m in messages if m.content == FORMAL_REPORT_IGNORES_QUESTION)
    )
    assert any(
        isinstance(m, HumanMessage) and m.additional_kwargs.get("attestation_nudge")
        for m in messages[formal_index + 1 :]
    )
