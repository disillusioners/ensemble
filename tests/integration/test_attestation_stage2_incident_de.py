"""LCA Stage-2 INDEPENDENT E2E — incident shapes (d)(e).

This file is the JOB 2d-e independent E2E acceptance for the LCA
Stage-2 flip merge gate (Job 2 of 9). The existing coverage
(``tests/unit/test_attestation_resolver_stage2.py`` +
``tests/integration/test_attestation_stage2_incident_abc.py`` + the
Stage-1 ``tests/unit/test_attestation_resolver_activation.py``
``TestR4ShortCircuitInvariant``) is THIN at these seams:

* Shape (d) child-lie multi-evaluation arc:
    - Drives the gate node THREE TIMES through the REAL graph
      (``build_instance_graph`` + ``graph.ainvoke``) — the unit-level
      ``TestIncidentOriginalChildLie`` exercises the gate node directly
      in three separate calls, not through the real end-to-end routing
      a real LLM-driven turn takes.
    - Per-evaluation judge INVOCATION count assertion — each of the
      three evaluations has a precise expected invocation count
      (1, 1, 0). The total across the arc MUST be ≤ 4 HTTP attempts
      (≤ 2 attempts × 2 invocations + 0 × 1; the 0 is the attested
      meta-bypass eval 3). The budget sentinel embeds in real-graph
      runs.
    - The Δ2 row — eval 1 fires A-band ALONE (child-report-check note
      present, busy tree mutes B); this is the NEW fused judge call
      compared to the pre-Stage-2 path (which had 0 on this band).
    - Revive path semantics on eval 2 (leader emits a send_message
      revive attempt; tree quiet; deny+nudge forces continuation) and
      eval 3 (attested allow + counter reset) — the second nudge
      survives via the stable attestation_nudge id (FIX-3).

* Shape (e) non-delegated marker row R4 short-circuit:
    - attestation_required=False (no ``send_message`` since the last
      real user message) ⇒ R4 mirror short-circuits BEFORE A/B
      providers fire.
    - PLAIN ALLOW despite markers in the final AIMessage AND short
      final-word-count (<150). The R4 invariant is a spy-testable
      guarantee: A/B providers MUST NEVER be called when
      attestation_required is False.
    - ZERO judge calls; ZERO A/B provider calls; ZERO
      ``event=leader_completion_gate_fused_judge`` operator rows;
      ZERO hints; ZERO nudges.

Both scenarios exercise the real ``build_instance_graph`` compiled
graph (the real end_candidate → attestation_gate_node → back-to-agent
loop wiring), with a scripted LLM via ``ScriptedChatModel`` and the
production facade surfaces exposed by
``attestation_manager_factory`` (manager counts controllable via
``MagicMock.side_effect`` lists so the same manager yields DIFFERENT
counts per evaluate() call).

Arc split for (d): because the allow+hint path ENDS the graph run
(NR-6 checkpoint-durable hint rides alongside END without re-route),
the canonical 3-eval child-lie arc is traversed across TWO
``graph.ainvoke`` calls — eval 1 in the first call (allow+hint
under the Δ2 row), evals 2-3 in the second call (deny+nudge on
quiet tree, then attested allow + counter reset). The same
manager instance is used across both calls, so the
``MagicMock.side_effect`` lists are consumed in evaluation-order
([1, 0, 0] for pending / live / busy) — one facet per evaluate()
call.

Production code is FROZEN — this file is test-only.
"""
from __future__ import annotations

import json
import logging
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool

from daemon.graph import (
    ATTESTATION_NUDGE_TEXT as NUDGE_TEXT,
    COMPLETION_CHECK_NOTE_TEXT,
)
from daemon.services import attestation_report_judge as judge_mod
from daemon.services.attestation_judge_resolver import (
    reset_llm_judge_resolver_for_tests,
)
from daemon.services.attestation_resolver import (
    reset_attestation_resolver_for_tests,
)
from tests.support.scripted_chat_model import ScriptedChatModel

INSTANCE_ID = "attestation-leader-e2e"


# ─────────────────────────────────────────────────────────────────────────────
# Drift-pin guard — confirm we are on the LCA Stage-2 branch family
# ─────────────────────────────────────────────────────────────────────────────
_EXPECTED_HEAD_PREFIX = "feature/lca-resolver-stage2"


def _git_head() -> str:
    import subprocess

    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            stderr=subprocess.DEVNULL,
        ).decode().strip()
    except Exception:  # noqa: BLE001 — drift-pin is best-effort
        return "<unknown>"


@pytest.fixture(autouse=True)
def _branch_drift_pin():
    """Soft-pin: must be on the Stage-2 branch family."""
    head = _git_head()
    if not head.startswith(_EXPECTED_HEAD_PREFIX):
        pytest.skip(
            f"drift-pin: branch={head!r}, expected prefix "
            f"{_EXPECTED_HEAD_PREFIX!r}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Hermetic resolver caches + kill-switch env per test
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_resolvers(monkeypatch):
    monkeypatch.delenv(
        "ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED", raising=False
    )
    reset_attestation_resolver_for_tests()
    reset_llm_judge_resolver_for_tests()
    yield
    reset_attestation_resolver_for_tests()
    reset_llm_judge_resolver_for_tests()


@pytest.fixture(autouse=True)
def _enforce_mode(monkeypatch):
    monkeypatch.setenv("ENSEMBLE_LEADER_ATTESTATION_MODE", "enforce")
    monkeypatch.setenv("WATCHOVER_ENABLED", "false")


# ─────────────────────────────────────────────────────────────────────────────
# Tool: attest_completion (the only bound tool the leader has)
# ─────────────────────────────────────────────────────────────────────────────


@tool
def attest_completion() -> dict:
    """Real leader attestation tool used by the graph's ToolNode."""
    return {"attested": True, "timestamp": "2026-09-16T00:00:00+00:00"}


# ─────────────────────────────────────────────────────────────────────────────
# Graph build helpers
# ─────────────────────────────────────────────────────────────────────────────


def _settings():
    from daemon.services.attestation_gate import GateSettings

    return GateSettings(mode="enforce", window=3, deny_bound=3)


def _build_graph(graph_module, model, manager, checkpointer):
    """Real compiled graph with the scripted model patched in."""
    graph_module.build_instance_llms = lambda **_: (model, model)
    with patch(
        "daemon.services.attestation_gate.resolve_gate_settings",
        return_value=_settings(),
    ):
        return graph_module.build_instance_graph(
            tools=[attest_completion],
            checkpointer=checkpointer,
            llm_config={"model": "scripted-test", "api_key": "test"},
            system_prompt="scripted child-lie leader",
            user_language="Auto",
            language_check_enabled=False,
            manager=manager,
            graph_config={"configurable": {"thread_id": INSTANCE_ID}},
            attestation_enabled=True,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Verdict-JSON helpers (mirror test_attestation_resolver_stage2.py shape)
# ─────────────────────────────────────────────────────────────────────────────


def _not_complete_json(
    evidence=("SOURCE A: child-terminal contradiction evidence",),
    advisory="The child's report promised future work",
) -> str:
    return json.dumps(
        {
            "verdict": "not_complete",
            "evidence_cited": list(evidence),
            "advisory_note_text": advisory,
            "rationale": "mid-work",
        }
    )


# ─────────────────────────────────────────────────────────────────────────────
# Manager wiring: real facade with controllable per-eval counts
# ─────────────────────────────────────────────────────────────────────────────


def _wire_counts(manager, *, pending, live, busy):
    """Set per-evaluation side-effect lists on the manager facade.

    Each ``evaluate()`` call invokes ``count_pending_children``,
    ``count_live_descendants``, ``count_busy_descendants`` ONCE per
    invocation. The MagicMock ``side_effect`` lists are consumed in
    call-order, so the same manager yields DIFFERENT counts on
    successive evaluations across the multi-ainvoke arc.

    For the (d) arc, the lists are sized to the FULL arc (1 + 2 = 3
    evaluations): index 0 is consumed by eval 1 (ainvoke #1), indexes
    1-2 by evals 2-3 (ainvoke #2).
    """
    manager.count_pending_children.side_effect = list(pending)
    manager.count_live_descendants.side_effect = list(live)
    # count_busy_descendants is NOT wrapped in a MagicMock by the
    # factory — install one in its place so the side_effect list
    # pattern works uniformly across the three facades.
    busy_mock = MagicMock(name="count_busy_descendants", side_effect=list(busy))
    manager.count_busy_descendants = busy_mock


# ─────────────────────────────────────────────────────────────────────────────
# Scripted judge + legacy-judge spy
# ─────────────────────────────────────────────────────────────────────────────


class _ScriptedJudge:
    """Scripted LLM-judge stub.

    Each call to the underlying ``_invoke_judge_llm`` is appended to
    ``self.attempts`` (HTTP-attempt count) and ``self.payloads``
    (the verbatim user_payload the fused judge fed the LLM). The
    one-element ``responses`` list is reused across attempts.
    """

    def __init__(self, responses):
        self.responses = list(responses)
        self.attempts: list[str] = []
        self.payloads: list[str] = []

    async def __call__(self, config, user_payload, *, timeout_s, system_prompt=None):
        self.attempts.append(user_payload)
        self.payloads.append(user_payload)
        if len(self.responses) == 1:
            return (self.responses[0], "fake-quick")
        idx = min(len(self.attempts) - 1, len(self.responses) - 1)
        return (self.responses[idx], "fake-quick")


class _LegacyJudgeSpy:
    """Spy on the legacy ``judge_completion_report_async``.

    R-RES2-8 pin: this entry-point MUST stay at zero calls under the
    Stage-2 flip. Any call recorded by the spy means the flip
    regressed.
    """

    def __init__(self):
        self.calls: list = []

    async def __call__(self, messages, *, config, window=3, timeout_s=None):
        self.calls.append(messages)
        from daemon.services.attestation_report_judge import JudgeResult

        return JudgeResult(
            is_complete_report=True,
            verdict="yes",
            reason="legacy-never-runs",
            model="<legacy-stub>",
            latency_ms=0,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Message-id helpers + child-report-check note factory
# ─────────────────────────────────────────────────────────────────────────────


def _child_report_check_note(child_id: str | None = None) -> HumanMessage:
    """System-injected child-report contradiction note (Δ1 source).

    Pre-seeded into the messages list — the production wiring delivers
    the same shape via the messaging service when a child has
    promised-then-terminal behavior. The dual-surface kwargs
    (context_kind + child_report_check + child_instance_id +
    child_report_check_terms) is the LANDED Stage-0 producer contract
    that the resolver's A-band detection relies on.
    """
    import uuid as _uuid

    child = child_id or str(_uuid.uuid4())
    body = (
        f"Child {child} completed while its final report promised future "
        "work. Matched terms: will write the report. This completion is "
        "likely premature — the child promised work it did not deliver."
    )
    return HumanMessage(
        content=f"[SYSTEM CONTEXT: Child Report Check]\n\n{body}",
        additional_kwargs={
            "context_kind": "child_report_check",
            "child_report_check": True,
            "child_instance_id": child,
            "child_report_check_terms": ["will write the report"],
        },
        id=f"child_report_check:lead:{child}",
    )


# ─────────────────────────────────────────────────────────────────────────────
# LLM-call sequences — the scripted chat model drives 3 evaluations
# ─────────────────────────────────────────────────────────────────────────────


def _child_lie_eval1_messages():
    """Eval 1 — A-band with busy tree → allow+hint.

    * LLM call 1: delegate_ai (send_message tool call) — no eval
      (tool_call routes to the tools node; send_message is unbound so
      it produces a tool-error ToolMessage the LLM sees on the next
      call. The tool_call anchors ``delegation_since_last_user=True``
      for the R4 gate.)
    * LLM call 2: hallucinated_ai (no tool_call, ends turn) → eval 1
      fires — A-band fires ALONE (busy tree mutes B; the
      child-report-check note in messages fires Source A); judge
      returns not_complete-with-pending → ``allow+hint`` (D4 hint
      citation; graph ENDS with the hint as a checkpoint-durable
      reminder for the next turn).
    """
    return [
        AIMessage(
            content="Delegating to a child.",
            tool_calls=[
                {
                    "name": "send_message",
                    "args": {"target": "child-id"},
                    "id": "c1-dispatch",
                }
            ],
        ),
        AIMessage(content="Everything is complete on my side."),
    ]


def _child_lie_eval2_eval3_messages():
    """Evals 2 + 3 — deny+nudge on quiet tree + attested allow.

    The new turn (User message in ainvoke #2 input) becomes the new
    "last real user message" in the delegation scan; the leader
    issues a send_message revive attempt to anchor
    ``attestation_required=True`` on the new turn.

    * LLM call 1: send_message_ai (send_message tool_call) — no eval
      (tool_call routes to tools; tool fails unbound).
    * LLM call 2: revive_ai (no tool_call, ends turn) → eval 2 fires
      — tree quiet (pending=0, live=0, busy=0); judge returns
      not_complete-with-no-pending → ``deny+nudge`` (re-routes the
      leader back to work; the surviving nudge carries the FIX-3
      stable id).
    * LLM call 3: attest_ai (attest_completion tool_call) — no eval
      (tool_call routes to tools; attest_completion succeeds; the
      in-window scan sees the tool call).
    * LLM call 4: final_ai (no tool_call, ends turn) → eval 3 fires
      — attested (in-window ``attest_completion`` scan) →
      meta-bypass → plain allow + counter reset.
    """
    return [
        # Eval 2 prep — revive via send_message; the tool call
        # anchors delegation for the new turn's delegation scan.
        AIMessage(
            content="Reviving the child to verify its report.",
            tool_calls=[
                {
                    "name": "send_message",
                    "args": {"target": "child-id"},
                    "id": "c2-revive",
                }
            ],
        ),
        # Eval 2 — leader says it revived; tree quiet; deny+nudge.
        AIMessage(content="I revived the child and am waiting for its report."),
        # Eval 3 prep — attest via the real tool (success).
        AIMessage(
            content="Attesting completion now.",
            tool_calls=[
                {"name": "attest_completion", "args": {}, "id": "a1-attest"}
            ],
        ),
        # Eval 3 — final plain AIMessage; meta-bypass + allow.
        AIMessage(content="Full report delivered above."),
    ]


def _non_delegated_marker_messages():
    """Single-eval sequence — non-delegated marker row (R4 mirror).

    No ``send_message`` tool call in any LLM response → the
    ``scan_delegation_after_last_user`` walk returns False → the gate
    computes ``attestation_required=False`` → R4 short-circuit fires
    BEFORE A/B providers. The marker phrasing ("Ending turn,
    awaiting your go/no-go.") AND short final-word-count (<150) MUST
    be ignored by the predicate.
    """
    return [
        AIMessage(
            content=(
                "The answer is 42. Ending turn, awaiting your go/no-go."
            )
        ),
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Eval-row helpers — read structured rows from caplog
# ─────────────────────────────────────────────────────────────────────────────


def _rows(caplog, token: str) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if token in r.getMessage()
    ]


def _hint_messages(messages) -> list:
    """Return Completion Check Note hints (Δ4 / marker-band allow+hint)."""
    out = []
    for m in messages:
        if isinstance(m, HumanMessage):
            kw = m.additional_kwargs or {}
            if kw.get("context_kind") == "task_context":
                out.append(m)
    return out


def _nudge_messages(messages) -> list:
    """Return the attestation nudge messages (deny path)."""
    return [
        m
        for m in messages
        if isinstance(m, HumanMessage)
        and (m.additional_kwargs or {}).get("attestation_nudge")
    ]


def _attest_tool_calls(messages) -> list:
    """Return ToolMessages produced by attest_completion executions."""
    return [
        m
        for m in messages
        if getattr(m, "name", None) == "attest_completion"
    ]


# ─────────────────────────────────────────────────────────────────────────────
# (d) ORIGINAL child-lie arc — 3-evaluation E2E through the REAL gate node
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_d_child_lie_multi_evaluation_arc_e2e(
    real_graph_module,
    memory_saver,
    file_sqlite_engine,
    attestation_repository,
    attestation_manager_factory,
    monkeypatch,
    caplog,
):
    """(d) ORIGINAL child-lie arc — 3-evaluation E2E.

    Drives the real ``build_instance_graph`` through the same 3-eval
    shape the unit-level ``TestIncidentOriginalChildLie`` covers:
    eval 1 A-band (busy tree + child-report note) → judge → allow+hint;
    eval 2 quiet tree → judge → deny+nudge; eval 3 attested → allow +
    counter reset. Asserts per-evaluation judge INVOCATION count, total
    budget across the arc, and the legacy-site silence invariant.

    Arc split: because the allow+hint path ENDS the graph run
    (NR-6 checkpoint-durable hint rides alongside END without
    re-route), the canonical 3-eval child-lie arc is traversed
    across TWO ``graph.ainvoke`` calls — eval 1 in the first call
    (allow+hint under the Δ2 row), evals 2-3 in the second call
    (deny+nudge on quiet tree, then attested allow + counter
    reset).

    The pre-Stage-2 path had 0 fused-judge calls on the A-band shape —
    the Stage-2 flip adds exactly 1 (the Δ2 row). The 3-evaluation arc
    yields 2 logical fused-judge invocations total (eval 1 + eval 2)
    and ≤ 4 HTTP attempts (≤ 2 per invocation; default scripted
    response is parsable so the retry path is not exercised).
    """
    # ── Spy setup: scripted judge + legacy silence spy
    spy = _ScriptedJudge([_not_complete_json()])
    legacy = _LegacyJudgeSpy()
    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", spy)
    monkeypatch.setattr(judge_mod, "judge_completion_report_async", legacy)

    # ── Manager with per-evaluation side-effect lists
    # The full arc is 3 evals; the lists are consumed in order:
    #   index 0 → eval 1 (ainvoke #1): busy tree, A-band alone (Δ2 row)
    #   index 1 → eval 2 (ainvoke #2): quiet tree, deny band
    #   index 2 → eval 3 (ainvoke #2): quiet tree, meta-bypass (attested)
    repo, _instance = attestation_repository
    manager = attestation_manager_factory(file_sqlite_engine, repo)
    _wire_counts(
        manager,
        pending=[1, 0, 0],
        live=[2, 0, 0],
        busy=[2, 0, 0],
    )

    # ── Build the real compiled graph (eval 1 script)
    model1 = ScriptedChatModel(responses=_child_lie_eval1_messages(), i=0)
    graph1 = _build_graph(real_graph_module, model1, manager, memory_saver)

    # ── Pre-seed the child-report-check note via the messages list
    # (the production wiring delivers this shape via the messaging
    # service when a child promises-then-terminals; here we inject it
    # directly into the conversation so the resolver's A-band sees it
    # on eval 1).
    child_note = _child_report_check_note()

    with caplog.at_level(logging.INFO):
        # ── ainvoke #1: drives eval 1 (allow+hint)
        state1 = await graph1.ainvoke(
            {
                "messages": [
                    HumanMessage(content="please complete the mission"),
                    child_note,
                ]
            },
            config={
                "configurable": {"thread_id": INSTANCE_ID},
                "recursion_limit": 30,
            },
        )

    # Sanity: eval 1 fired exactly ONE end_candidate evaluation.
    assert manager.count_pending_children.call_count == 1, (
        f"ainvoke #1: 1 evaluate() call ⇒ 1 count_pending_children "
        f"read; got {manager.count_pending_children.call_count}"
    )
    assert manager.count_live_descendants.call_count == 1
    # Scripted chat model fully consumed.
    model1.assert_all_responses_consumed()

    # ── Build a fresh graph for ainvoke #2 (new ScriptedChatModel,
    # same thread_id → checkpointer carries the state across)
    model2 = ScriptedChatModel(
        responses=_child_lie_eval2_eval3_messages(), i=0
    )
    graph2 = _build_graph(real_graph_module, model2, manager, memory_saver)

    with caplog.at_level(logging.INFO):
        # ── ainvoke #2: drives eval 2 (deny+nudge) + eval 3 (attested allow)
        state2 = await graph2.ainvoke(
            {"messages": [HumanMessage(content="verify the child's report")]},
            config={
                "configurable": {"thread_id": INSTANCE_ID},
                "recursion_limit": 30,
            },
        )

    # Final messages from ainvoke #2 carry the full arc's state.
    final_messages = state2["messages"]

    # ── A) Per-evaluation budget: the 3 evals = (1, 1, 0) invocations
    # = 2 logical invocations across the arc. HTTP attempts ≤ 4 (each
    # invocation ≤ 2; retry path is not exercised on a parsable
    # response).
    assert len(spy.attempts) == 2, (
        f"3 evaluations × expected (1, 1, 0) judge invocations ⇒ 2 "
        f"HTTP attempts total; got {len(spy.attempts)}"
    )
    assert len(spy.payloads) == 2, "judge invocation count != payload count"
    # The fused operator row fired twice — once per non-meta-bypass
    # evaluation (eval 1 + eval 2).
    fused_rows = _rows(caplog, "event=leader_completion_gate_fused_judge ")
    assert len(fused_rows) == 2, (
        f"3 evals: (fused_judge, fused_judge, meta_bypass) ⇒ 2 fused "
        f"operator rows; got {len(fused_rows)}"
    )

    # ── B) Resolver rows: 3 (one per evaluation). Eval 3 carries
    # ``bypass_reason=meta_bypass`` (the attested short-circuit).
    resolver_rows = _rows(caplog, "event=leader_completion_resolver_eval ")
    assert len(resolver_rows) == 3, (
        f"one resolver row per evaluation × 3 evals; got "
        f"{len(resolver_rows)}"
    )
    # The first eval's row records the Δ2 band: a_suspicion (B is
    # busy-muted, so the A-band fires alone).
    assert "band=a_suspicion" in resolver_rows[0], (
        f"eval 1 must fire A-band alone (B busy-muted); got "
        f"{resolver_rows[0]}"
    )
    # Eval 1 outcome: allow+hint (pending>0, not_complete ⇒ allow+hint
    # with D4 evidence citation per the §4.3 row mapping).
    assert "resolver_outcome=allow_hint" in resolver_rows[0], (
        f"eval 1 outcome: allow+hint (pending>0 + not_complete); got "
        f"{resolver_rows[0]}"
    )
    assert "judge_invoked=True" in resolver_rows[0]
    # Eval 2 outcome: deny+nudge (tree quiet + not_complete ⇒ deny).
    assert "band=deny" in resolver_rows[1], (
        f"eval 2 must fire deny band (tree quiet); got "
        f"{resolver_rows[1]}"
    )
    assert "resolver_outcome=deny_nudge" in resolver_rows[1]
    assert "judge_invoked=True" in resolver_rows[1]
    # Eval 3 outcome: meta-bypass — attested ⇒ ZERO judge (the term-1
    # short-circuit fires BEFORE any provider).
    assert "fired=False" in resolver_rows[2], (
        f"eval 3 must NOT fire (meta-bypass); got {resolver_rows[2]}"
    )
    assert "bypass_reason=meta_bypass" in resolver_rows[2]
    assert "judge_invoked=False" in resolver_rows[2]

    # ── C) Δ1: the judge SAW the Source-A child-report evidence on
    # eval 1 (the only eval where the A-band fires).
    payload_1 = spy.payloads[0]
    assert "SOURCE A" in payload_1, (
        f"Δ1 evidence fusion: eval 1 payload must contain the Source A "
        f"section; got payload starting {payload_1[:200]!r}"
    )
    # Δ3: the judge SAW the tree-rows section (Source C).
    assert "SOURCE C" in payload_1, (
        f"Δ3 evidence fusion: eval 1 payload must contain the Source C "
        f"tree rows; got payload starting {payload_1[:200]!r}"
    )

    # ── D) Hints and nudges in the message tape (final state from
    # ainvoke #2 carries the full arc's tape)
    hints = _hint_messages(final_messages)
    nudges = _nudge_messages(final_messages)
    # Eval 1 emits a D4-citing hint (allow+hint), eval 2 emits a
    # deny+nudge, eval 3 emits nothing (allow). The FIX-3 stable-id
    # supersede keeps a SINGLE surviving nudge block carrying the
    # final denied_count stamp.
    assert len(hints) == 1, (
        f"exactly one D4 evidence-citing hint on eval 1; got {len(hints)}"
    )
    hint = hints[0]
    # D4: the hint gains the evidence citation when the verdict carries one.
    assert "Completion evidence cited by the completion judge:" in hint.content
    assert "SOURCE A: child-terminal contradiction evidence" in hint.content
    # Stable id — the hint rides alongside END with its per-instance id
    # so the add_messages reducer supersedes any prior hint in place.
    assert hint.id == f"completion_check_note:{INSTANCE_ID}"
    assert len(nudges) == 1, (
        f"exactly one attestation_nudge block on eval 2; got {len(nudges)}"
    )
    assert nudges[0].content == NUDGE_TEXT
    # FIX-3: the surviving nudge carries the per-instance stable id and
    # the FINAL denied_count stamp.
    assert nudges[0].id == f"attestation_nudge:{INSTANCE_ID}"

    # ── E) Attestation tool was executed (eval 3 prep step)
    attest_tool_msgs = _attest_tool_calls(final_messages)
    assert len(attest_tool_msgs) == 1, (
        f"exactly one attest_completion ToolMessage; got {len(attest_tool_msgs)}"
    )

    # ── F) Counter state: 1 deny cycle (eval 2), 1 attested allow
    # (eval 3) ⇒ final ledger reset. Pre-existing pre-test the unit
    # ledger starts at 0; after eval 2 increments to 1; after eval 3
    # the attested-allow reset clears the counter.
    assert repo.get_attestation_denied_count(INSTANCE_ID) == 0, (
        "attested allow on eval 3 MUST reset the counter"
    )

    # ── G) Legacy sites silent: ``judge_completion_report_async`` is
    # the legacy entry-point that the Stage-2 flip retired into the
    # fused node. Across the full 3-evaluation arc, the legacy entry
    # MUST stay at zero calls (R-RES2-8 acceptance).
    assert legacy.calls == [], (
        "legacy judge_completion_report_async MUST stay at zero calls "
        "under the Stage-2 flip (3-evaluation arc)"
    )

    # ── H) Manager facade reads per evaluation — confirms we drove 3
    # gate evaluations through the real node (not 1 + skipped).
    assert manager.count_pending_children.call_count == 3, (
        f"3 evaluations × count_pending_children ⇒ 3 reads; got "
        f"{manager.count_pending_children.call_count}"
    )
    assert manager.count_live_descendants.call_count == 3, (
        f"3 evaluations × count_live_descendants ⇒ 3 reads; got "
        f"{manager.count_live_descendants.call_count}"
    )

    # ── I) Both scripted chat models were fully consumed
    # (ainvoke #1 consumed 2 responses, ainvoke #2 consumed 4 — total
    # 6 LLM calls across the 3-evaluation arc).
    model1.assert_all_responses_consumed()
    model2.assert_all_responses_consumed()


# ─────────────────────────────────────────────────────────────────────────────
# (e) Non-delegated marker row — R4 short-circuit, ZERO suspicion evaluation
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_e_non_delegated_marker_row_plain_allow_e2e(
    real_graph_module,
    memory_saver,
    file_sqlite_engine,
    attestation_repository,
    attestation_manager_factory,
    monkeypatch,
    caplog,
):
    """(e) Non-delegated marker row → plain allow, ZERO suspicion eval.

    Mission with NO ``send_message`` since the last real user message
    (so ``attestation_required=False``) AND final AIMessage carrying
    BOTH a mid-work marker ("Ending turn, awaiting your go/no-go.") AND
    a short final-word-count (<150 words). The R4 short-circuit MUST
    fire BEFORE the A/B providers — the marker/short signals are
    ignored at the predicate's R0/R1 seam.

    Spy proofs:
    * ``judge_invoked=False`` on the resolver row (no fused judge fired)
    * No ``event=leader_completion_gate_fused_judge`` operator row
    * ZERO hints injected (no Completion Check Note)
    * ZERO nudges injected (no attestation_nudge)
    * Counter NOT incremented (no deny)
    * The legacy ``judge_completion_report_async`` site stayed at
      zero calls.
    """
    # ── Spy setup
    spy = _ScriptedJudge([_not_complete_json()])
    legacy = _LegacyJudgeSpy()
    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", spy)
    monkeypatch.setattr(judge_mod, "judge_completion_report_async", legacy)

    # ── Manager (counts are irrelevant since R4 short-circuits before
    # they would matter — but we still wire sane 0/0/0).
    repo, _instance = attestation_repository
    manager = attestation_manager_factory(file_sqlite_engine, repo)
    _wire_counts(manager, pending=[0], live=[0], busy=[0])

    model = ScriptedChatModel(
        responses=_non_delegated_marker_messages(), i=0
    )
    graph = _build_graph(real_graph_module, model, manager, memory_saver)

    with caplog.at_level(logging.INFO):
        state = await graph.ainvoke(
            {"messages": [HumanMessage(content="what is the answer?")]},
            config={
                "configurable": {"thread_id": INSTANCE_ID},
                "recursion_limit": 30,
            },
        )

    final_messages = state["messages"]

    # ── A) ZERO judge invocations — A/B providers never fired.
    assert len(spy.attempts) == 0, (
        f"R4 short-circuit: A/B providers MUST NEVER fire when "
        f"attestation_required=False; got {len(spy.attempts)} HTTP "
        f"attempts"
    )
    assert len(spy.payloads) == 0

    # ── B) Resolver row records the meta-bypass shape: fired=False,
    # bypass_reason=meta_bypass, judge_invoked=False, AND
    # attestation_required=False (the R0 anchor — the predicate read
    # the delegation scan BEFORE the A/B providers).
    resolver_rows = _rows(caplog, "event=leader_completion_resolver_eval ")
    assert len(resolver_rows) == 1, (
        f"one resolver row per evaluation; got {len(resolver_rows)}"
    )
    row = resolver_rows[0]
    assert "fired=False" in row, f"R4: fired=False expected; got {row}"
    assert "bypass_reason=meta_bypass" in row, (
        f"R4: bypass_reason=meta_bypass expected; got {row}"
    )
    assert "judge_invoked=False" in row, (
        f"R4: judge_invoked=False expected (no fused judge fired); "
        f"got {row}"
    )
    assert "attestation_required=False" in row, (
        f"R4: attestation_required=False (no send_message since last "
        f"real user message); got {row}"
    )
    assert "resolver_outcome=allow" in row, (
        f"R4: plain allow outcome (the meta-bypass routes to "
        f"allow); got {row}"
    )

    # ── C) ZERO fused-judge operator rows. The fused block never
    # even reached the judge call site — the resolver row is the only
    # row the post-flip accounting emits on this branch.
    fused_rows = _rows(caplog, "event=leader_completion_gate_fused_judge ")
    assert fused_rows == [], (
        f"R4: ZERO fused-judge operator rows on non-delegated mission; "
        f"got {fused_rows}"
    )

    # ── D) ZERO hints (the marker-band hint path is dead without a
    # delegation anchor).
    assert _hint_messages(final_messages) == [], (
        f"R4: ZERO hint injections on non-delegated mission; got "
        f"{[m.content[:80] for m in _hint_messages(final_messages)]}"
    )

    # ── E) ZERO nudges (no deny path was traversed).
    assert _nudge_messages(final_messages) == [], (
        f"R4: ZERO attestation nudges on non-delegated mission; got "
        f"{len(_nudge_messages(final_messages))}"
    )

    # ── F) Counter NOT incremented.
    assert repo.get_attestation_denied_count(INSTANCE_ID) == 0

    # ── G) Legacy sites silent.
    assert legacy.calls == [], (
        "legacy judge_completion_report_async MUST stay at zero calls "
        "on non-delegated mission (R4 short-circuit fires before any "
        "judge site)"
    )

    # ── H) The graph terminated on the FIRST plain AIMessage (no
    # continuation loop because the gate returned ALLOWED with no
    # nudge injected).
    ai_contents = [m.content for m in final_messages if isinstance(m, AIMessage)]
    assert ai_contents == [
        "The answer is 42. Ending turn, awaiting your go/no-go."
    ], (
        f"non-delegated mission: the first plain AIMessage is the "
        f"final one (gate allows without re-route); got ai_contents="
        f"{ai_contents}"
    )

    # ── I) Scripted chat model fully consumed.
    model.assert_all_responses_consumed()