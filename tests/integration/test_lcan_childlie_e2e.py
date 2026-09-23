"""Child-lie E2E (INDEPENDENT construction) — LCA advisory-note-removal
merge gate (feature/lca-remove-advisory-note).

The architecture AFTER the removal: NO delivery-time Child Report Check
note is minted anywhere. The activation predicate's A-band scans
``internal_report:``-stamped child-report HumanMessages in the leader's
IN-CONTEXT message window at GATE-EVALUATION time (end_candidate), via
the unchanged 17-pattern catalog
(:data:`~daemon.services.attestation_marker_scanner.CHILD_TERMINAL_PROMISE_MARKERS`,
``daemon/services/attestation_marker_scanner.py``). SourceASignals keeps
its 4-field OR shape; D2 holds: the A-band fires ALONE during busy
descendants (NOT busy-suppressed) and the fused judge makes exactly ONE
call when the plan fires / ZERO when it does not. The legacy note path
survives ONLY as a read-side fallback for pre-removal checkpoints.

This file was constructed INDEPENDENTLY from the gate spec — the
scenarios, transcripts, and report wording are original. Harness
MECHANICS (real-graph build shape, scripted-model contract, file-backed
SQLite fixtures, judge seam patching) follow the established attestation
integration harness (zoo test + lcau incident E2E + their conftest). The
dev's feature unit test (``tests/unit/test_attestation_resolver_
activation.py``) was neither read nor copied.

Three scenarios, all driven through the REAL completion-gate machinery
(graph end_candidate → gate evaluate → evaluate_resolver_activation →
assemble_fused_bundle → ``judge_fused_bundle_async`` STUBBED at the
module-attribute seam — the ONE judge call site imports its callable at
call time, so patching intercepts the real invocation with the REAL
bundle text in hand):

S1 — CHILD-LIE DENY→ATTEST-ALLOW (the core arc, ONE full-graph ainvoke):
    The child's delivered report (drained into leader context as an
    ``internal_report:``-stamped HumanMessage, the production drain
    shape) contains the promise "I will write RESULTS. Ending turn."
    The leader's final answer is a GENUINE-LOOKING completion report
    RELAYING the promise, long enough to clear the 150-word threshold
    and free of B-band marker substrings. Nothing pending, un-attested.
    EXPECT at end_candidate: the evaluation-time A-band fires (evidence
    derives from the stamped report message, NOT from any note), the
    judge bundle carries the A-section quoting the child's promise, the
    decision is deny + nudge (counter 0→1, judge exactly once), and the
    graph does NOT end. The leader then attests → next evaluation is an
    attested meta-bypass ALLOW (judge 0 calls, counter resets 1→0) and
    the graph ENDS.

S2 — REVIVE VARIANT (full graph): after deny+nudge the leader does NOT
    attest; it REVIVES the child (send_message tool call) and delivers a
    real completion. The second evaluation is again deny-band + A-band
    evidence, the judge returns complete → fused-judge RESCUE allow and
    the graph ENDS with no attestation ever made and no counter reset.

S3 — D2 ROW (busy descendants): same child-lie shape but a RUNNING child
    instance sits in the tree (real ``count_busy_descendants`` facade
    over the repo). c_quiet is false and B is busy-muted, so ONLY the
    A-band can trigger: fired=True band=a_suspicion, judge exactly once,
    not_complete → ALLOW + checkpoint-durable hint, graph ends.

GLOBAL (all scenarios): ZERO ``context_kind=child_report_check``
messages minted/delivered — the checkpoint message channel, the
``message_queue`` table, and the manager's enqueue lane all stay clean;
no "Child Report Check" string appears in any message; the relay text
never trips a B-band marker.

Production code is FROZEN — this file is test-only, new file, and does
not modify any existing test.
"""

from __future__ import annotations

import logging
import uuid
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from sqlalchemy import func, select
from sqlmodel import Session

from daemon.repositories.message_queue.models import MessageQueue
from daemon.services.attestation_marker_scanner import (
    CHILD_TERMINAL_PROMISE_MARKERS,
    MID_WORK_MARKERS,
    scan_child_terminal_report_for_promises,
)
from daemon.services.attestation_report_judge import FusedJudgeResult
from daemon.services.context_messages import CONTEXT_KIND_CHILD_REPORT_CHECK
from tests.support.scripted_chat_model import ScriptedChatModel

pytestmark = pytest.mark.integration

INSTANCE_ID = "attestation-leader-e2e"

# The delegated child — a REAL instance row (UUID-shaped so the
# ``internal_report:<uuid>:...`` source stamp parses).
CHILD_INSTANCE_ID = "9f1c2d3e-4a5b-4c6d-8e7f-0a1b2c3d4e5f"

# The user's mission ask (the last real user message anchor).
USER_MISSION = (
    "Run the calibration sweep to ground: dispatch the worker, collect "
    "its results, and close the mission out."
)

# The child's delivered report body — the LIE: it promises future work
# ("will write", "ending turn" are 17-pattern catalog hits) while going
# terminal. Kept SHORT so the promise survives the 400-char evidence
# excerpt clip intact (the production untrusted-data frame is ~320
# chars).
CHILD_REPORT_BODY = "I will write RESULTS. Ending turn."
PROMISE_SENTENCE = CHILD_REPORT_BODY

# The leader's final answer in S1/S3 — a GENUINE-LOOKING completion
# report that RELAYS the child's promise in its own words, clears the
# 150-word SHORT_REPORT_WORD_THRESHOLD, and contains NO B-band marker
# substring (pinned below against the imported catalog).
RELAY_REPORT = (
    "My worker has closed out its assignment and delivered its wrap-up "
    "note. In that note the worker said it would write the RESULTS file "
    "for the calibration sweep once the final patch set lands, and it "
    "closed its turn immediately after sending that word — so the "
    "promised write-up remains owed by the worker, not by this leader. "
    "Beyond that one hand-off detail, the dispatched scope is fully "
    "accounted for: the fixture matrix ran clean across all nine "
    "environments, the two flaky probes were quarantined and "
    "re-recorded, the schema migration rehearsed forward and back with "
    "zero drift, and the soak log shows no regressions across the full "
    "eight-hour window. The consolidated findings, the checksum table, "
    "and the incident notes are filed under the shared context "
    "directory for this cycle. Follow-ups are limited to the single "
    "documentation pass the user marked out of scope. I am reporting "
    "the mission state exactly as the evidence supports it, including "
    "the one open promise recorded in my worker's final note."
)

# The leader's post-attest closing ack (S1 LLM turn 4).
CLOSING_ACK = (
    "All delegated work is reconciled, the report is delivered above, "
    "and the mission record is archived."
)

# The leader's post-revive real completion (S2 LLM turn 4).
REVIVAL_COMPLETION = (
    "The mission is now fully closed out. The revived worker delivered "
    "its promised RESULTS file; the calibration numbers reconcile with "
    "the fixture matrix run, the checksum table matches the shipped "
    "artifacts, and the incident notes are archived under the shared "
    "context directory. Every follow-up from the original dispatch is "
    "either resolved or explicitly recorded as out of scope. No further "
    "child work is required, and nothing remains owed by any participant "
    "in this subtree."
)

# ─── Scenario-shape pins (self-documenting, import-backed) ──────────────
assert PROMISE_SENTENCE in CHILD_REPORT_BODY
assert scan_child_terminal_report_for_promises(CHILD_REPORT_BODY).promise_hit
_lowered_relay = RELAY_REPORT.lower()
_b_band_hits = [m for m in MID_WORK_MARKERS if m in _lowered_relay]
assert not _b_band_hits, f"relay text must not trip B-band markers: {_b_band_hits}"
_a_band_hits = [m for m in CHILD_TERMINAL_PROMISE_MARKERS if m in _lowered_relay]
assert not _a_band_hits, f"relay text must not trip A-band markers either: {_a_band_hits}"
assert len(RELAY_REPORT.split()) >= 150, "relay must clear the 150-word threshold"


# ─── Harness mechanics (established attestation integration harness) ────
@tool
def attest_completion() -> dict:
    """Real leader attestation tool used by the graph's ToolNode."""
    return {"attested": True, "timestamp": "2026-09-18T00:00:00+00:00"}


@pytest.fixture(autouse=True)
def _reset_resolvers(monkeypatch):
    """Pin enforce mode + guarantee the fused-judge kill-switch is ON."""
    monkeypatch.setenv("ENSEMBLE_LEADER_ATTESTATION_MODE", "enforce")
    monkeypatch.setenv("WATCHOVER_ENABLED", "false")
    monkeypatch.delenv(
        "ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED", raising=False
    )
    from daemon.services.attestation_judge_resolver import (
        reset_llm_judge_resolver_for_tests,
    )
    from daemon.services.attestation_resolver import (
        reset_attestation_resolver_for_tests,
    )

    reset_attestation_resolver_for_tests()
    reset_llm_judge_resolver_for_tests()
    yield
    reset_attestation_resolver_for_tests()
    reset_llm_judge_resolver_for_tests()


def _settings():
    from daemon.services.attestation_gate import GateSettings

    return GateSettings(mode="enforce", window=3, deny_bound=3)


@pytest.fixture(autouse=True)
def _instance_hierarchy_table(file_sqlite_engine):
    """The narrow attestation fixture does not create instance_hierarchy.

    ``repo.create(parent_id=...)`` writes a hierarchy link on every
    insert; the permanent-lineage tree walk itself reads
    ``instances.parent_id``. Create the one missing table test-locally
    (additive — no shared fixture is modified).
    """
    from daemon.repositories.instance.models import InstanceHierarchy
    from sqlmodel import SQLModel

    SQLModel.metadata.create_all(
        file_sqlite_engine, tables=[InstanceHierarchy.__table__]
    )


def _build_graph(graph_module, model, manager, checkpointer):
    graph_module.build_instance_llms = lambda **_: (model, model)
    from unittest.mock import patch as _patch

    with _patch(
        "daemon.services.attestation_gate.resolve_gate_settings",
        return_value=_settings(),
    ):
        return graph_module.build_instance_graph(
            tools=[attest_completion],
            checkpointer=checkpointer,
            llm_config={"model": "scripted-lcan-childlie", "api_key": "test"},
            system_prompt="scripted child-lie e2e leader",
            user_language="Auto",
            language_check_enabled=False,
            manager=manager,
            graph_config={"configurable": {"thread_id": INSTANCE_ID}},
            attestation_enabled=True,
        )


def _delegate_ai() -> AIMessage:
    return AIMessage(
        content="Dispatching the calibration sweep to the worker now.",
        tool_calls=[
            {
                "name": "send_message",
                "args": {"target": CHILD_INSTANCE_ID},
                "id": "lcan-dispatch-1",
            }
        ],
    )


def _revive_ai() -> AIMessage:
    return AIMessage(
        content="Reviving the worker so it delivers what it promised.",
        tool_calls=[
            {
                "name": "send_message",
                "args": {"target": CHILD_INSTANCE_ID},
                "id": "lcan-revive-1",
            }
        ],
    )


def _attest_ai() -> AIMessage:
    return AIMessage(
        content="Report delivered above; attesting completion.",
        tool_calls=[
            {"name": "attest_completion", "args": {}, "id": "lcan-attest-1"}
        ],
    )


def _child_report_message(graph_module) -> HumanMessage:
    """The child report in the EXACT production drain shape.

    Mirrors the report-injection drain (``daemon/graph.py``
    ``report_extra_kwargs = {"injected_message": True, "source":
    f"internal_report:{child_iid}"}``) including the production
    ``_frame_injected_report`` untrusted-data frame (taken from the REAL
    module so the wire shape is pinned, not re-worded).
    """
    framed = graph_module._frame_injected_report(CHILD_REPORT_BODY)
    # The promise must survive the 400-char evidence excerpt clip.
    assert framed.index(PROMISE_SENTENCE) + len(PROMISE_SENTENCE) <= 400
    return HumanMessage(
        content=framed,
        id=str(uuid.uuid4()),
        additional_kwargs={
            "injected_message": True,
            "source": f"internal_report:{CHILD_INSTANCE_ID}",
        },
    )


class _RecordingJudge:
    """Graph-seam judge probe: records REAL bundle text, scripted verdicts.

    Mirrors ``judge_fused_bundle_async``'s contract (positional
    bundle_text, keyword config; never raises) and returns a fully
    populated :class:`FusedJudgeResult` so the graph's derived
    ``judge_invoked`` flag and the fused-judge row stay real.
    """

    def __init__(self, verdicts: list[str]):
        self.verdicts = list(verdicts)
        self.bundles: list[str] = []

    async def __call__(self, bundle_text: str, *, config, timeout_s=None):
        self.bundles.append(bundle_text)
        idx = min(len(self.bundles) - 1, len(self.verdicts) - 1)
        verdict = self.verdicts[idx]
        return FusedJudgeResult(
            invoked=True,
            is_complete=(verdict == "complete"),
            verdict=verdict,
            rationale="stub adjudication for the child-lie e2e",
            model="stub-judge",
            latency_ms=1,
            attempt=1,
        )


def _eval_rows(caplog) -> list[str]:
    """ALL resolver eval rows (trailing-space anchor excludes _error)."""
    return [
        r.getMessage()
        for r in caplog.records
        if "event=leader_completion_resolver_eval " in r.getMessage()
    ]


def _fired_eval_rows(caplog) -> list[str]:
    return [row for row in _eval_rows(caplog) if " fired=True " in row]


def _nudges(messages):
    return [
        m
        for m in messages
        if isinstance(m, HumanMessage)
        and (m.additional_kwargs or {}).get("attestation_nudge")
    ]


def _assert_no_note_artifacts(messages, manager, engine) -> None:
    """GLOBAL: zero Child Report Check notes minted/delivered, anywhere."""
    # (1) The checkpoint message channel carries no note-shaped message.
    for m in messages:
        kwargs = getattr(m, "additional_kwargs", None) or {}
        assert kwargs.get("context_kind") != CONTEXT_KIND_CHILD_REPORT_CHECK, (
            f"a child_report_check note was minted into the channel: {m!r}"
        )
        assert kwargs.get("child_report_check") is not True, (
            f"a legacy-flagged note landed in the channel: {m!r}"
        )
        content = m.content if isinstance(m.content, str) else ""
        assert "Child Report Check" not in content, (
            f"a 'Child Report Check' string leaked into a message: {content!r}"
        )
    # (2) The message_queue delivery lane holds zero rows.
    with Session(engine) as session:
        queued = (
            session.scalar(select(func.count()).select_from(MessageQueue)) or 0
        )
    assert queued == 0, f"message_queue must be empty; got {queued} rows"
    # (3) The manager enqueue lane was never used to deliver a note.
    manager.enqueue_message.assert_not_called()


def _assert_a_band_witnesses(fired_row: str) -> None:
    """The fired eval row witnesses the evaluation-time transcript A-band."""
    assert "a_advisory_present=True" in fired_row, fired_row
    assert "a_notes=1" in fired_row, fired_row
    # kwargs_surface_seen=True — the evidence came from the stamped
    # internal_report source, NOT from a legacy note surface.
    assert "a_kwargs_seen=True" in fired_row, fired_row


# ─────────────────────────── S1 — the core arc ───────────────────────────
@pytest.mark.asyncio
async def test_s1_child_lie_deny_then_attest_allow_real_graph(
    real_graph_module,
    memory_saver,
    file_sqlite_engine,
    attestation_repository,
    attestation_manager_factory,
    caplog,
):
    """One ainvoke, 4 LLM turns, 2 gate evals:
    deny+nudge (A-band evidence, counter 0→1, judge 1 call) → attest →
    attested meta-bypass allow (judge 0 calls, counter 1→0) → END.
    """
    repo, _instance = attestation_repository
    # The delegated child EXISTS: it delivered its report and went terminal.
    repo.create(
        instance_id=CHILD_INSTANCE_ID,
        agent_id="worker",
        agent_dir="./agents/worker",
        parent_id=INSTANCE_ID,
        status="completed",
    )
    manager = attestation_manager_factory(
        file_sqlite_engine, repo, pending_children=0, queued_wakeups=0
    )

    #   LLM 1: delegate (send_message) → unbound tool error → routes back
    #   LLM 2: relay report → end_candidate → eval1 (A-band + quiet →
    #          deny band → judge not_complete → DENY+NUDGE, 0→1)
    #   LLM 3: attest_completion → real tool → routes back
    #   LLM 4: closing ack → end_candidate → eval2 (attested meta-bypass
    #          → ALLOW, counter 1→0) → END
    model = ScriptedChatModel(
        responses=[
            _delegate_ai(),
            AIMessage(content=RELAY_REPORT),
            _attest_ai(),
            AIMessage(content=CLOSING_ACK),
        ],
        i=0,
    )
    graph = _build_graph(real_graph_module, model, manager, memory_saver)

    judge = _RecordingJudge(["not_complete"])
    caplog.set_level(logging.INFO)
    with patch(
        "daemon.services.attestation_report_judge.judge_fused_bundle_async",
        new=judge,
    ):
        state = await graph.ainvoke(
            {
                "messages": [
                    HumanMessage(content=USER_MISSION),
                    _child_report_message(real_graph_module),
                ]
            },
            config={
                "configurable": {"thread_id": INSTANCE_ID},
                "recursion_limit": 30,
            },
        )
    model.assert_all_responses_consumed()

    # ── Judge budget: EXACTLY ONE call across the whole arc (eval2 is a
    # meta-bypass: 0 calls). ──
    assert len(judge.bundles) == 1, (
        f"judge must be invoked exactly once; got {len(judge.bundles)}"
    )

    # ── The bundle carries the A-section quoting the CHILD's promise ──
    bundle = judge.bundles[0]
    assert (
        "=== SOURCE A: child-terminal contradiction evidence (1 note(s)) ==="
        in bundle
    ), bundle[:800]
    assert PROMISE_SENTENCE in bundle, (
        "the A-section must quote the child's promise text verbatim"
    )
    # Catalog-order terms derived from the report message itself.
    assert "terms=ending turn,will write" in bundle, bundle[:800]
    assert "child=" in bundle, bundle[:800]

    # ── Resolver eval rows: eval1 fired deny, eval2 attested bypass ──
    rows = _eval_rows(caplog)
    assert len(rows) == 2, f"2 evals expected; got {len(rows)}: {rows}"
    fired_row = _fired_eval_rows(caplog)
    assert len(fired_row) == 1 and fired_row[0] == rows[0], rows
    row1 = rows[0]
    assert "fired=True" in row1 and "band=deny" in row1, row1
    assert "a_suspicion" in row1, row1  # the A term contributed
    _assert_a_band_witnesses(row1)
    assert (
        "pending_children=0 queued_or_expected_wakeups=0 "
        "live_descendants=0 busy_descendants=0"
    ) in row1, row1
    assert "marker_hit=False" in row1, row1  # B never fired
    assert "length_trigger=False" in row1, row1
    assert "judge_invoked=True" in row1, row1
    assert "judge_verdict=not_complete" in row1, row1
    assert "resolver_outcome=deny_nudge" in row1, row1
    row2 = rows[1]
    assert "fired=False" in row2 and "bypass_reason=meta_bypass" in row2, row2
    assert "judge_invoked=False" in row2, row2

    # ── Deny + nudge engaged exactly once (counter 0→1) ──
    gate_denied = [
        r.getMessage()
        for r in caplog.records
        if "event=leader_completion_gate " in r.getMessage()
        and "decision=denied" in r.getMessage()
    ]
    assert len(gate_denied) == 1, gate_denied
    assert "next_denied_count=1" in gate_denied[0], gate_denied[0]
    messages = state["messages"]
    nudges = _nudges(messages)
    assert len(nudges) == 1, [n.content for n in nudges]
    assert nudges[0].additional_kwargs.get("attestation_nudge_denied_count") == 1
    assert nudges[0].id == f"attestation_nudge:{INSTANCE_ID}"

    # ── The graph did NOT end on the deny: attest turn ran, then the
    # attested allow reset the counter 1→0 and ENDED the graph. The
    # STATE channel retains the deny's stamp (the allow return carries
    # no key — lcau-B pin); the LEDGER is what resets. ──
    assert repo.get_attestation_denied_count(INSTANCE_ID) == 0
    assert state["attestation_nudge_denied_count"] == 1
    assert messages[-1].content == CLOSING_ACK, messages[-1].content

    # ── GLOBAL: no note anywhere ──
    _assert_no_note_artifacts(messages, manager, file_sqlite_engine)


# ───────────────────── S2 — revive variant (full graph) ──────────────────
@pytest.mark.asyncio
async def test_s2_child_lie_revive_then_rescue_allow_real_graph(
    real_graph_module,
    memory_saver,
    file_sqlite_engine,
    attestation_repository,
    attestation_manager_factory,
    caplog,
):
    """After deny+nudge the leader REVIVES the child instead of attesting;
    the real completion is judged complete → fused-judge RESCUE allow.
    No attestation ever happens; the counter stays at 1 (no reset on a
    rescue); the graph ENDS on the real completion."""
    repo, _instance = attestation_repository
    repo.create(
        instance_id=CHILD_INSTANCE_ID,
        agent_id="worker",
        agent_dir="./agents/worker",
        parent_id=INSTANCE_ID,
        status="completed",
    )
    manager = attestation_manager_factory(
        file_sqlite_engine, repo, pending_children=0, queued_wakeups=0
    )

    #   LLM 1: delegate → unbound tool error → routes back
    #   LLM 2: relay report → eval1 (deny band + A-band → judge
    #          not_complete → DENY+NUDGE, 0→1; routes back)
    #   LLM 3: revive (send_message) → unbound tool error → routes back
    #   LLM 4: real completion → eval2 (deny band + A-band → judge
    #          complete → RESCUE allow) → END, no attestation ever
    model = ScriptedChatModel(
        responses=[
            _delegate_ai(),
            AIMessage(content=RELAY_REPORT),
            _revive_ai(),
            AIMessage(content=REVIVAL_COMPLETION),
        ],
        i=0,
    )
    graph = _build_graph(real_graph_module, model, manager, memory_saver)

    judge = _RecordingJudge(["not_complete", "complete"])
    caplog.set_level(logging.INFO)
    with patch(
        "daemon.services.attestation_report_judge.judge_fused_bundle_async",
        new=judge,
    ):
        state = await graph.ainvoke(
            {
                "messages": [
                    HumanMessage(content=USER_MISSION),
                    _child_report_message(real_graph_module),
                ]
            },
            config={
                "configurable": {"thread_id": INSTANCE_ID},
                "recursion_limit": 30,
            },
        )
    model.assert_all_responses_consumed()

    # Judge: exactly one call per deny-band evaluation (2 evals, 2 calls).
    assert len(judge.bundles) == 2, judge.bundles
    for bundle in judge.bundles:
        assert (
            "=== SOURCE A: child-terminal contradiction evidence (1 note(s)) ==="
            in bundle
        ), bundle[:800]
        assert PROMISE_SENTENCE in bundle, (
            "each deny-band bundle must quote the child's promise"
        )

    # Both evals fired on the deny band with A-band evidence.
    rows = _eval_rows(caplog)
    assert len(rows) == 2, rows
    fired = _fired_eval_rows(caplog)
    assert len(fired) == 2, rows
    for row in fired:
        assert "fired=True" in row and "band=deny" in row, row
        assert "resolver_outcome=deny_nudge" in row or (
            "resolver_outcome=allow" in row
        ), row
    assert "a_suspicion" in fired[0], fired[0]
    _assert_a_band_witnesses(fired[0])
    _assert_a_band_witnesses(fired[1])
    assert "judge_verdict=not_complete" in fired[0], fired[0]
    assert "judge_verdict=complete" in fired[1], fired[1]
    assert "resolver_outcome=allow" in fired[1], fired[1]
    assert "judge_invoked=True" in fired[0] and "judge_invoked=True" in fired[1]

    # The rescue allow: the graph ENDED on the real completion report.
    assert "fused-judge rescue" in caplog.text
    messages = state["messages"]
    assert messages[-1].content == REVIVAL_COMPLETION, messages[-1].content

    # NO attestation ever happened; the rescue moved nothing: the counter
    # keeps the single deny (0→1 at eval1, untouched by the rescue).
    assert repo.get_attestation_denied_count(INSTANCE_ID) == 1
    nudges = _nudges(messages)
    assert len(nudges) == 1, [n.content for n in nudges]
    assert nudges[0].additional_kwargs.get("attestation_nudge_denied_count") == 1

    # ── GLOBAL: no note anywhere ──
    _assert_no_note_artifacts(messages, manager, file_sqlite_engine)


# ───────────────── S3 — D2 row: A-band fires during busy descendants ─────
@pytest.mark.asyncio
async def test_s3_a_band_fires_alone_with_busy_descendant_real_graph(
    real_graph_module,
    memory_saver,
    file_sqlite_engine,
    attestation_repository,
    attestation_manager_factory,
    caplog,
):
    """A RUNNING child sits in the tree (real busy/live facades over the
    repo): c_quiet false, B busy-muted — ONLY the evaluation-time A-band
    can trigger. fired=True band=a_suspicion, judge exactly once, and
    not_complete on a busy tree → ALLOW + checkpoint-durable hint, END."""
    repo, _instance = attestation_repository
    # The delegated child is ALIVE and BUSY (running).
    repo.create(
        instance_id=CHILD_INSTANCE_ID,
        agent_id="worker",
        agent_dir="./agents/worker",
        parent_id=INSTANCE_ID,
        status="running",
    )
    manager = attestation_manager_factory(
        file_sqlite_engine,
        repo,
        pending_children=None,  # real watcher read → 0
        queued_wakeups=0,
        live_descendants=None,  # real facade over the running child → 1
    )

    #   LLM 1: delegate → unbound tool error → routes back
    #   LLM 2: relay report → eval1: busy tree + A-band alone → judge
    #          not_complete → ALLOW + hint (D2: A is NOT busy-suppressed)
    model = ScriptedChatModel(
        responses=[_delegate_ai(), AIMessage(content=RELAY_REPORT)],
        i=0,
    )
    graph = _build_graph(real_graph_module, model, manager, memory_saver)

    judge = _RecordingJudge(["not_complete"])
    caplog.set_level(logging.INFO)
    with patch(
        "daemon.services.attestation_report_judge.judge_fused_bundle_async",
        new=judge,
    ):
        state = await graph.ainvoke(
            {
                "messages": [
                    HumanMessage(content=USER_MISSION),
                    _child_report_message(real_graph_module),
                ]
            },
            config={
                "configurable": {"thread_id": INSTANCE_ID},
                "recursion_limit": 30,
            },
        )
    model.assert_all_responses_consumed()

    # Judge exactly-once discipline on the D2 row.
    assert len(judge.bundles) == 1, judge.bundles
    bundle = judge.bundles[0]
    assert (
        "=== SOURCE A: child-terminal contradiction evidence (1 note(s)) ==="
        in bundle
    ), bundle[:800]
    assert PROMISE_SENTENCE in bundle, bundle[:800]

    # The ONE eval row: fired during BUSY descendants, A-band ALONE.
    rows = _eval_rows(caplog)
    assert len(rows) == 1, rows
    row = rows[0]
    assert "fired=True" in row, row
    assert "band=a_suspicion" in row, row  # A alone: not deny, not marker
    assert "terms_fired=a_suspicion " in row, row
    _assert_a_band_witnesses(row)
    # Busy-descendant shape: quiet is FALSE, B is busy-muted AND clean.
    assert "pending_children=0 queued_or_expected_wakeups=0 " in row, row
    assert "live_descendants=1 busy_descendants=1" in row, row
    assert "marker_hit=False" in row, row
    assert "length_trigger=False" in row, row
    assert "judge_invoked=True" in row, row
    assert "judge_verdict=not_complete" in row, row
    assert "resolver_outcome=allow_hint" in row, row

    # Outcome: allow log-only (2026-09-23 b2f4dae9: the Completion
    # Check Note hint is RETIRED end-to-end; the A-band route
    # resolves to ALLOW with the ``allow_hint`` label as the
    # forensic record — NOT deny, NOT plain allow, NOT hint
    # injection).
    assert "allowing END" in caplog.text and (
        "log-only" in caplog.text or "would_be_route=allow_hint" in caplog.text
    ), (
        f"the [AttestationGate] log line MUST surface the log-only "
        f"posture; got caplog.text={caplog.text[:2000]!r}"
    )
    messages = state["messages"]
    # NO Completion Check Note hint injected — ZERO messages
    # carrying ``context_kind=task_context`` from the (b)/(d)-with-
    # pending factory (the kind is RETIRED; the factory is RETIRED).
    hints = [
        m
        for m in messages
        if isinstance(m, HumanMessage)
        and (m.additional_kwargs or {}).get("context_kind") == "task_context"
    ]
    assert len(hints) == 0, (
        f"Completion Check Note hint MUST NOT be injected after "
        f"2026-09-23 (b2f4dae9); got {[m.content[:80] for m in hints]!r}"
    )
    # No nudge, no counter movement, graph ENDED.
    assert _nudges(messages) == []
    assert repo.get_attestation_denied_count(INSTANCE_ID) == 0
    assert state.get("attestation_nudge_denied_count", 0) == 0
    # 2026-09-23 (b2f4dae9): no hint rides the END anymore — the
    # ``messages[-1] is hints[-1]`` check is retired along with the
    # hint surface itself. The graph still ends; the last message is
    # whatever the LLM produced. We don't pin it here (the prior
    # shape pinned the hint-as-last, which is no longer a thing).

    # ── GLOBAL: no note anywhere ──
    _assert_no_note_artifacts(messages, manager, file_sqlite_engine)
