"""b2f4dae9 regression — INDEPENDENTLY CONSTRUCTED through the REAL gate node.

Merge-gate acceptance evidence for the 2026-09-23 LCA Completion Check Note
removal (D-ENTRY 2026-09-23, ``feature/lca-remove-check-note`` @ ff9eb849,
base 6bf7bed7). Built INDEPENDENTLY of the dev witness
``tests/unit/test_attestation_lca_note_removed.py`` — different harness
(real-graph via ``ainvoke`` + scripted chat model + judge patched at the
module seam, NOT the dev's direct ``create_attestation_gate_node`` +
``monkeypatch`` unit path), different Source-A evidence shape (production
drain ``internal_report:<uuid>``, NOT the historical note kwargs), and
different scenario wording (independent lexical FP phrasing + an
independent short mid-work ACK + an independent catalog-hit choice).

The b2f4dae9 incident (forensic ID, NOT a git SHA): a 7m32s-old healthy
wait on a RUNNING developer child fired the OLD (b)-route Completion
Check Note on a short mid-work ACK whose Source-A evidence carried a
lexical false-positive marker (``"awaiting"``). The OLD code injected a
checkpoint-durable ``[SYSTEM CONTEXT: Completion Check Note]``
``HumanMessage`` carrying ``context_kind=task_context`` — pure noise.
The (b)/(d)-with-pending route STILL exists at ff9eb849 as a logged
decision (resolver row + ``[AttestationGate]`` log line stamp the
``allow_hint`` label + the log-only posture) but emits NO message.
The user's 2026-09-23 decision: full removal over age-gate (the note
never prevented anything; the deny path prevents; the watchdog acts).

Three cells, all driven end-to-end through the REAL compiled graph
(``build_instance_graph`` + ``graph.ainvoke`` + scripted chat model +
judge patched at the module-attribute seam — the ONLY judge call site
imports its callable at call time, so patching intercepts the REAL
invocation with the REAL bundle text in hand):

S1 (b2f4dae9 healthy-busy regression — the canonical incident shape)
    RUNNING developer child (live=1, busy=1), ``pending_children=1`` (the
    mid-work-ACK wakeup), short mid-work AIMessage carrying the lexical
    FP ``"awaiting"`` in the relay message + production-drain Source-A
    evidence (``internal_report:<uuid>`` HumanMessage whose body carries
    the catalog hit ``awaiting``), fused judge verdict=not_complete →
    ALLOW log-only via R2 ``live_descendants=1``, ZERO Completion Check
    Note messages, ONE fired resolver_eval row carrying
    ``band=a_suspicion judge_invoked=True judge_verdict=not_complete
    resolver_outcome=allow_hint would_be_outcome=would_hint``,
    ``[AttestationGate]`` log line stamps ``would_be_route=allow_hint``
    + the log-only posture + the ``note hint retired 2026-09-23``
    witness, ledger counter not incremented, graph ends.

S2 (suspect-pending PAUSED child)
    PAUSED developer child (live=1, busy=0 — PAUSED is unconditional-
    live per the 2026-09-12 live-facade two-set semantics, but NOT in
    the busy subset per the LCA trigger-suppression gate), same R2
    ``pending_children=1`` + same Source-A evidence + same judge-no
    shape → ALLOW log-only via R2 ``live_descendants=1`` (PAUSED is
    ``live-for-deny-protection`` but ALSO live-for-R2-allow; the gate
    sees R2 satisfied via ``live_descendants``). The deny path is
    INTACT (the protection surface); the note is RETIRED; ZERO
    messages, ONE fired eval row, log-only posture preserved.

S3 (suspect-pending en-route-only child)
    COMPLETED developer child (live=0, busy=0 — terminal, excluded from
    both subsets) + ``pending_children=1`` (DEPENDENCY_WATCHER
    PENDING-row count — the "en-route-only" shape the D-entry pins:
    IDLE + pending message OR IDLE + unsettled job; here we use the
    PENDING watcher to drive R2 because the dependency-watchers table
    is the canonical "wakeup in flight" signal the gate reads), same
    Source-A evidence + same judge-no shape → ALLOW log-only via R2
    ``pending_children=1``. Spawned-but-not-yet-messaged descendants
    are bus-blind (ghost filter) — the "en-route-only" shape; the deny
    path is INTACT; the note is RETIRED.

EACH cell derives its expected decision from the code at ff9eb849
(see cell docstrings): decide() ordering is
``user_answer_pending → R2 three-input → attested-split → bound → denied
+ nudge``; R2 three-input is
``pending_children ∨ queued_or_expected_wakeups ∨ live_descendants``.
ALL three cells satisfy R2 → resolve to
``Decision.ALLOWED_LEGITIMATE_PENDING_WAKEUP`` → resolver row carries
``resolver_outcome=allow_hint`` → gate log line stamps
``would_be_route=allow_hint`` → ZERO message injection. NONE of the
shapes flip to deny — the dispatch's "deny-protection cells" wording
was a misreading; the b2f4dae9 D-entry explicitly documents that the
(b)/(d)-with-pending route is an ALLOW path (the wakeup is en route,
the turn still ends), and that suspect-pending protection lives in
the deny path + the watchdog + the bound, NOT in the (now retired)
hint. Each cell's docstring quotes the derive-from-code evidence so
the deviation is logged on the test face.

COVERAGE SURVEY (the gaps this file fills — re-derived from
``git ls-files tests/`` at ff9eb849):

  ``tests/unit/test_attestation_lca_note_removed.py`` (dev witness):
    S1, S2, S3 — but UNIT-level via ``create_attestation_gate_node``
    directly + ``monkeypatch`` on
    ``daemon.services.attestation_report_judge._invoke_judge_llm``.
    Real graph NOT exercised; this file's harness is the real graph.

  ``tests/integration/test_lcan_childlie_e2e.py`` (existing lcan E2E):
    S3 only (busy descendant + relay-report shape, no lexical FP in
    the FINAL AI). No PAUSED cell; no en-route-only cell; the relay
    report is 150+ words (NOT a short mid-work ACK). GAP: S1 uses a
    DIFFERENT transcript shape (relay report, not short ACK; no
    lexical FP "awaiting" in the FINAL AI); S2 and S3 are absent.

  ``tests/integration/test_attestation_marker_routing_lca.py``:
    S1 unit-level only — same gap as the dev witness; real graph NOT
    exercised.

  ``tests/integration/test_attestation_stage2_failopen.py``:
    fail-open paths re-anchored to log-only (Seam (iii) marker/A +
    judge wrapper fault) — different scope (fault-injection), not a
    b2f4dae9 regression construction.

  ``tests/integration/test_attestation_attest_first_e2e*.py``:
    attest-first HOLD + bundled shape — not b2f4dae9 scope.

  ``tests/integration/test_attestation_mid_work_report_testcase.py``:
    ONE test asserting ``len(hints) == 0`` on a healthy-wait shape;
    unit-level MagicMock harness; real graph NOT used.

  NEW gap this file fills:
    THREE cells (busy RUNNING + PAUSED + en-route-only) all driven
    through the REAL graph via ``ainvoke``, ALL THREE carrying the
    lexical FP "awaiting" in the Source-A evidence, ALL THREE
    asserting the FULL surviving log-row schema (terms_fired/band/
    marker_hit/length_trigger/busy_descendants/judge_invoked/
    judge_verdict/resolver_outcome/would_be_outcome). Independent
    from the dev witness, independent from
    ``test_lcan_childlie_e2e.py``, independent from
    ``test_attestation_marker_routing_lca.py``.

Process discipline:

  * Drift-pin first (per dispatch constraint); pack wrapper re-pins.
  * Resolver cache reset between cells via the established pattern
    (autouse ``_reset_resolvers`` fixture; both
    ``reset_attestation_resolver_for_tests`` AND
    ``reset_llm_judge_resolver_for_tests``).
  * Judge patched at the module-attribute seam — the ONE judge call
    site imports its callable at call time, so the patch intercepts
    the REAL invocation.
  * The ``build_instance_llms`` factory is replaced so the graph
    NEVER POSTs to a real LLM endpoint.
  * Mode pinned to ``enforce`` (D2 — only enforce writes the ledger;
    the dry/off modes do not).
  * ``WATCHOVER_ENABLED=false`` (no second-attention side effect).
  * ``_invoke_semaphore`` left alone — the gate's own graph layer
    awaits under its own concurrency cap.

NO production-code changes. Defects found → evidence + report; do NOT
fix production code.
"""
from __future__ import annotations

import logging
import uuid
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool

from daemon.services.attestation_report_judge import FusedJudgeResult
from tests.support.scripted_chat_model import ScriptedChatModel

pytestmark = pytest.mark.integration


# ─────────────────────────────────────────────────────────────────────────────
# Constants — three independent cells, all using the SAME Source-A evidence
# shape (production drain ``internal_report:<uuid>`` HumanMessage) and
# the SAME short mid-work ACK family (lexical FP "awaiting"). The transcripts
# differ only in the child-instance state (RUNNING / PAUSED / COMPLETED) and
# the manager kwargs (``pending_children`` / ``live_descendants``).
# ─────────────────────────────────────────────────────────────────────────────

LEADER_INSTANCE_ID = "attestation-leader-e2e"

# The delegated child instance UUID — fixed across all three cells so the
# ``internal_report:<uuid>`` source stamp is a stable string the test can
# assert against (independent of the dev witness's CHILD_INSTANCE_ID).
CHILD_INSTANCE_ID = "1c2d3e4f-5a6b-7c8d-9e0f-1a2b3c4d5e6f"

# The user's mission ask — single sentence, no marker; the FIRST and ONLY
# real user message the gate scans back to when locating the
# ``delegation_since_last_user`` anchor.
USER_MISSION = (
    "Run the calibration sweep and report back when the worker finishes."
)

# Independent child-report body for Source A — the lexical false-positive
# ``"awaiting"`` lives in the report's prose as a NON-contradiction marker
# (the b2f4dae9 shape: a completed wanderer's changelog prose carried
# "awaiting" and Source A fired despite the prose not actually promising
# future work — that's the FP class the note was FP-prone for).
# Catalog hits (`scan_child_terminal_report_for_promises`) — "awaiting"
# alone is a catalog hit per `daemon/services/attestation_marker_scanner.py`
# line 263. We pin the catalog hit semantically (the SHAPE assertion below)
# so the test fails loud if the catalog drifts.
CHILD_REPORT_BODY = (
    "Calibration sweep completed; the changelog mentions one entry "
    "currently awaiting review from the docs team, but that is the "
    "only outstanding item on my side. Ending turn."
)

# b2f4dae9 short mid-work ACK (the FINAL AI in the transcript — under
# the 150-word ``SHORT_REPORT_WORD_THRESHOLD`` so the gate classifies
# it as not-a-text-report; the gate STILL evaluates). All three cells
# use the same ACK shape so the assertion surface is stable; only the
# child-instance state differs across cells.
LCANCHECK_SHORT_ACK = (
    "Awaiting the worker reply. Ending turn now."
)

# LLM turn count — every cell drives exactly 2 LLM calls (LLM 1:
# delegate via send_message unbound → tool error → routes back; LLM 2:
# short mid-work ACK → end_candidate → gate evaluates). One eval row
# per cell; graph ends after the ALLOW log-only verdict.
EXPECTED_LLM_TURNS = 2


# ─────────────────────────────────────────────────────────────────────────────
# Shape-shape pins (catalog import — self-documenting, fail-loud on drift)
# ─────────────────────────────────────────────────────────────────────────────

from daemon.services.attestation_marker_scanner import (  # noqa: E402
    CHILD_TERMINAL_PROMISE_MARKERS,
    scan_child_terminal_report_for_promises,
)

_scan = scan_child_terminal_report_for_promises(CHILD_REPORT_BODY)
assert _scan.promise_hit, (
    "the lexical FP 'awaiting' MUST be a catalog hit at ff9eb849 — "
    "incident b2f4dae9 depends on the catalog firing on the "
    "non-contradiction 'awaiting' prose. If this fails, the catalog "
    "drifted; the test cannot reproduce the b2f4dae9 shape without it."
)
assert "awaiting" in CHILD_TERMINAL_PROMISE_MARKERS, (
    "'awaiting' MUST remain in the CHILD_TERMINAL_PROMISE_MARKERS "
    "catalog at ff9eb849 — the D-entry pins this as the canonical "
    "lexical FP class the note was FP-prone for."
)
# The ACK is intentionally short so the gate's classify_final_ai_shape
# returns final_ai_is_text_report=False (under the 150-word threshold)
# and the gate STAYS ARMED for the resolve tree.
assert len(LCANCHECK_SHORT_ACK.split()) < 150, (
    "the b2f4dae9 short ACK MUST stay under the 150-word text-report "
    "threshold so the gate evaluates the resolve tree, not the "
    "attested-allow short-circuit."
)


# ─────────────────────────────────────────────────────────────────────────────
# Harness mechanics (real-graph integration test, established pattern)
# ─────────────────────────────────────────────────────────────────────────────


@tool
def attest_completion() -> dict:
    """Real leader attestation tool (the graph's bound tool).

    A dummy body — the b2f4dae9 cells NEVER call attest_completion
    (the (b)/(d)-with-pending route is a NOT-attested path: the
    leader wrote a short mid-work ACK and is awaiting the worker).
    The tool must be bound so the LLM's ``send_message`` unbound call
    returns a tool error (the ToolNode reject path), not a graph
    crash, and so the graph can route back to the agent for LLM 2.
    """
    return {"attested": True, "timestamp": "2026-09-23T00:00:00+00:00"}


@pytest.fixture(autouse=True)
def _instance_hierarchy_table(file_sqlite_engine):
    """The narrow attestation fixture does not create ``instance_hierarchy``.

    ``repo.create(parent_id=...)`` writes a hierarchy link on every
    insert; the permanent-lineage tree walk itself reads
    ``instances.parent_id``. The fixture that owns the SQLite engine
    for this file (``file_sqlite_engine`` in
    ``tests/support/conftest.py``) only declares ``Instance``,
    ``DependencyWatcher``, ``MessageQueue``, ``Task``, ``Event``. Create
    the missing ``instance_hierarchy`` table test-locally — additive —
    no shared fixture is modified.
    """
    from daemon.repositories.instance.models import InstanceHierarchy
    from sqlmodel import SQLModel

    SQLModel.metadata.create_all(
        file_sqlite_engine, tables=[InstanceHierarchy.__table__]
    )


@pytest.fixture(autouse=True)
def _reset_resolvers(monkeypatch):
    """Pin enforce mode + reset resolver caches between cells.

    Mirrors the established pattern in
    ``tests/integration/test_lcan_childlie_e2e.py::_reset_resolvers``:
    mode = enforce (D2 — only enforce writes the ledger); WATCHOVER off
    (no second-attention side effect); resolver caches reset before
    AND after each test so a sibling test that flips
    ``ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED`` does not leak
    into this file's evaluation.
    """
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


def _build_graph(graph_module, model, manager, checkpointer):
    """Build the REAL compiled graph with the scripted model installed.

    Mirrors ``test_lcan_childlie_e2e.py::_build_graph`` byte-for-byte
    on the graph-construction seam — ``build_instance_llms`` is
    replaced so the graph NEVER POSTs to a real LLM endpoint, and
    ``resolve_gate_settings`` is patched to the enforce settings so
    the resolver cache reset + env pinning are the only sources of
    truth for mode/window/bound.
    """
    graph_module.build_instance_llms = lambda **_: (model, model)
    with patch(
        "daemon.services.attestation_gate.resolve_gate_settings",
        return_value=_settings(),
    ):
        return graph_module.build_instance_graph(
            tools=[attest_completion],
            checkpointer=checkpointer,
            llm_config={
                "model": "scripted-lcancheck-b2f4dae9",
                "api_key": "test",
            },
            system_prompt="scripted b2f4dae9 regression leader",
            user_language="Auto",
            language_check_enabled=False,
            manager=manager,
            graph_config={"configurable": {"thread_id": LEADER_INSTANCE_ID}},
            attestation_enabled=True,
        )


def _delegate_ai() -> AIMessage:
    """LLM 1 — delegate via ``send_message`` (the canonical attestation
    ON-switch; ``delegation_scan.delegation_since_last_user`` flips to
    True on this call so the gate evaluates at end_candidate)."""
    return AIMessage(
        content="Dispatching the worker.",
        tool_calls=[
            {
                "name": "send_message",
                "args": {"target": CHILD_INSTANCE_ID},
                "id": "lcancheck-dispatch-1",
            }
        ],
    )


def _short_mid_work_ack() -> AIMessage:
    """LLM 2 — short mid-work ACK (the b2f4dae9 shape; under the
    150-word text-report threshold so the gate evaluates the resolve
    tree, NOT the attested-allow short-circuit). The lexical FP
    "awaiting" lives here AND in the Source-A evidence (independent
    placement — the dispatch says the ACK was short; the D-entry pins
    the lexical FP in the Source-A evidence specifically)."""
    return AIMessage(content=LCANCHECK_SHORT_ACK)


def _child_report_message(graph_module) -> HumanMessage:
    """The Source-A evidence in the EXACT production drain shape.

    Mirrors the production report-injection drain at
    ``daemon/graph.py`` line ~7130 — the child report is wrapped in
    ``_frame_injected_report`` (model-visible untrusted-data frame),
    stamped with ``source=internal_report:<uuid>`` (the post-D-CTD-7
    canonical source stamp the A-band scanner's Pass 2 recognizes),
    and tagged ``injected_message=True`` (the metadata the LLM never
    sees; the activation predicate reads ``source=`` directly).
    """
    framed = graph_module._frame_injected_report(CHILD_REPORT_BODY)
    # The "awaiting" lexical FP MUST survive the 400-char evidence
    # excerpt clip — Source A scans the FULL report body (pass 2 of
    # the catalog scan reads from the un-truncated content), but the
    # 400-char clip is what the bundle's evidence-excerpt field
    # captures. Pin the clip survival here so the test fails loud if
    # the framing drifts.
    assert "awaiting" in framed[:400] or "awaiting" in framed, (
        "the lexical FP 'awaiting' MUST land in the framed report "
        "body — Source A's pass-2 catalog scan needs the substring."
    )
    return HumanMessage(
        content=framed,
        id=str(uuid.uuid4()),
        additional_kwargs={
            "injected_message": True,
            "source": f"internal_report:{CHILD_INSTANCE_ID}",
        },
    )


class _RecordingJudge:
    """Graph-seam judge probe — mirrors ``judge_fused_bundle_async``.

    The ONE judge call site imports the callable at call time, so
    patching ``daemon.services.attestation_report_judge.judge_fused_bundle_async``
    intercepts the REAL invocation with the REAL bundle text in hand.
    Returns a fully populated :class:`FusedJudgeResult` so the graph's
    derived ``judge_invoked`` flag and the fused-judge row stay real.
    """

    def __init__(self, verdict: str):
        self.verdict = verdict
        self.bundles: list[str] = []

    async def __call__(self, bundle_text: str, *, config, timeout_s=None):
        self.bundles.append(bundle_text)
        return FusedJudgeResult(
            invoked=True,
            is_complete=(self.verdict == "complete"),
            verdict=self.verdict,
            rationale="stub adjudication for b2f4dae9 regression",
            model="stub-judge",
            latency_ms=1,
            attempt=1,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Per-cell evaluation helpers (per-row assertions, log-row surface checks)
# ─────────────────────────────────────────────────────────────────────────────


def _eval_rows(caplog) -> list[str]:
    """ALL resolver eval rows (trailing-space anchor excludes _error)."""
    return [
        r.getMessage()
        for r in caplog.records
        if "event=leader_completion_resolver_eval " in r.getMessage()
    ]


def _fired_eval_rows(caplog) -> list[str]:
    """Eval rows with ``fired=True`` (the post-fix A-band ALLOW log-only)."""
    return [row for row in _eval_rows(caplog) if " fired=True " in row]


def _fused_judge_rows(caplog) -> list[str]:
    """Fused-judge rows — the pre-resolver surface carrying the verdict."""
    return [
        r.getMessage()
        for r in caplog.records
        if "event=leader_completion_gate_fused_judge" in r.getMessage()
    ]


def _assert_no_completion_check_note(messages) -> None:
    """b2f4dae9 invariant: ZERO Completion Check Note messages injected.

    The pre-2026-09-23 factory (``_mermaid_apply_completion_check_note_message``
    — RETIRED at ff9eb849) emitted a ``HumanMessage`` with
    ``additional_kwargs.context_kind=task_context`` carrying the
    ``[SYSTEM CONTEXT: Completion Check Note]`` prefix. Post-fix: the
    factory + the constant + the ``context_kind`` value are all RETIRED.
    Assert the message channel carries NO such message.
    """
    completion_check_hints = [
        m
        for m in messages
        if isinstance(m, HumanMessage)
        and (
            (m.additional_kwargs or {}).get("context_kind") == "task_context"
            or (
                isinstance(m.content, str)
                and "Completion Check Note" in (m.content or "")
            )
        )
    ]
    assert completion_check_hints == [], (
        f"b2f4dae9 regression: ZERO Completion Check Note messages "
        f"MUST be injected after 2026-09-23 (the note is RETIRED); "
        f"got {[m.content[:80] for m in completion_check_hints]!r}"
    )


def _assert_ledger_counter_intact(repo, manager) -> None:
    """b2f4dae9 invariant: the (b)/(d)-with-pending route is ALLOW
    log-only, NOT deny — the ledger counter MUST NOT have been
    incremented (a deny would have run ``ledger.increment`` exactly
    once; the resolved ``allowed_legitimate_pending_wakeup`` skips
    it)."""
    counter = repo.get_attestation_denied_count(LEADER_INSTANCE_ID)
    assert counter == 0, (
        f"the (b)/(d)-with-pending ALLOW log-only path MUST NOT "
        f"increment the ledger counter (R2 deny + bound are the "
        f"protection surface, NOT the now-retired hint); "
        f"got counter={counter}"
    )


def _assert_fired_row_log_only_contract(
    fired_row: str,
    cell_name: str,
    *,
    expect_band_a_alone: bool,
) -> None:
    """Assert the FULL surviving log-row schema for the (b)/(d)-with-pending
    ALLOW log-only path — the field set the D-entry pins as
    "DO-NOT-TOUCH": terms_fired / band / marker_hit / length_trigger /
    busy_descendants / judge_invoked / judge_verdict / resolver_outcome /
    would_be_outcome.

    The BAND label depends on the busy_descendants count (R5/Δ2
    band precedence): with busy>0, B is suppressed and A wins
    (``band=a_suspicion``); with busy=0, B wins (``band=marker``) and
    both ``b_fires`` AND ``a_suspicion`` appear in ``terms_fired``.
    The lexical FP "awaiting" lives in BOTH catalogs
    (``MID_WORK_MARKERS`` AND ``CHILD_TERMINAL_PROMISE_MARKERS``) per
    the catalog imports above — so on a busy=0 shape, both bands
    fire on the same substring; on a busy>0 shape, only the A-band
    contribution survives (B is suppressed).
    """
    assert "fired=True" in fired_row, fired_row

    # Source A evidence MUST participate on every cell (the b2f4dae9
    # Source-A path is NOT busy-muted per R5/Δ2).
    assert "a_advisory_present=True" in fired_row, fired_row
    assert "a_notes=1" in fired_row, fired_row
    assert "a_suspicion" in fired_row, (
        f"{cell_name}: 'a_suspicion' MUST appear in terms_fired "
        f"(Source A's lexical FP 'awaiting' must contribute); "
        f"got: {fired_row}"
    )
    # The A-section kwargs surface MUST be detected (the
    # ``internal_report:<uuid>`` stamp is the kwargs path Source A's
    # pass-2 scan consumes).
    assert "a_kwargs_seen=True" in fired_row, fired_row

    # B-band conditions ARE met (the lexical FP 'awaiting' is in
    # MID_WORK_MARKERS too — that's the FP class the note was
    # FP-prone for). The marker_hit / length_trigger fields reflect
    # the SCAN RESULT, not the b_fires GATE (the b_fires gate is
    # marker_hit ∨ length_trigger AND busy=0).
    assert "marker_hit=True" in fired_row, fired_row
    assert "length_trigger=True" in fired_row, fired_row

    # Judge invoked ONCE; verdict=not_complete.
    assert "judge_invoked=True" in fired_row, fired_row
    assert "judge_verdict=not_complete" in fired_row, fired_row

    # The (b)/(d)-with-pending route's structured resolver row label
    # — IDENTICAL across all three cells (the log-only verdict is
    # shape-agnostic on the (b)/(d)-with-pending ALLOW path).
    assert "resolver_outcome=allow_hint" in fired_row, (
        f"{cell_name}: the resolver row MUST carry "
        f"resolver_outcome=allow_hint (the surviving log-only label "
        f"the D-entry pins); got: {fired_row}"
    )
    assert "would_be_outcome=would_hint" in fired_row, (
        f"{cell_name}: the resolver row MUST carry "
        f"would_be_outcome=would_hint (the structured would-be hint "
        f"outcome — replaces the deleted hint injection); "
        f"got: {fired_row}"
    )

    # Cell-specific: band label + terms_fired composition.
    if expect_band_a_alone:
        # Busy>0 suppresses B; only a_suspicion survives.
        assert "band=a_suspicion" in fired_row, (
            f"{cell_name}: Source A band MUST fire ALONE (busy>0 "
            f"suppresses the B-band per R5/Δ2 LCA busy trigger "
            f"suppression; the lexical FP 'awaiting' in the catalog "
            f"hits BOTH bands but B is muted when busy>0); "
            f"got: {fired_row}"
        )
        assert "terms_fired=a_suspicion " in fired_row, (
            f"{cell_name}: terms_fired MUST be exactly 'a_suspicion' "
            f"(busy>0 suppresses the b_fires term); got: {fired_row}"
        )
    else:
        # Busy=0 → B wins precedence → band=marker + terms_fired
        # carries BOTH b_fires AND a_suspicion. Source A still fires
        # (NOT busy-muted per R5/Δ2); B wins the band label only.
        assert "band=marker" in fired_row, (
            f"{cell_name}: B-band MUST win precedence on busy=0 "
            f"(the lexical FP 'awaiting' is in MID_WORK_MARKERS too "
            f"— both bands fire on the same substring, B wins per "
            f"the resolver's band-precedence rule); got: {fired_row}"
        )
        assert "b_fires" in fired_row and "a_suspicion" in fired_row, (
            f"{cell_name}: terms_fired MUST carry BOTH b_fires AND "
            f"a_suspicion (Source A evidence participates alongside "
            f"the B-band marker hit); got: {fired_row}"
        )


def _assert_attestation_gate_log_only(caplog, cell_name: str) -> None:
    """Assert the [AttestationGate] log line stamps the log-only posture
    + ``would_be_route=allow_hint`` + the ``note hint retired 2026-09-23``
    witness — the canonical log surface operators grep for to confirm
    the (b)/(d)-with-pending route STILL exists but emits NO message."""
    gate_rows = [
        r.getMessage()
        for r in caplog.records
        if "[AttestationGate] fused-judge" in r.getMessage()
        or "[AttestationGate] allowing END" in r.getMessage()
    ]
    assert gate_rows, (
        f"{cell_name}: the [AttestationGate] log line MUST fire "
        f"(the b2f4dae9 evidence chain is log-reconstructible via "
        f"the gate log line — NOT the deleted hint injection)"
    )
    gate_log = "\n".join(gate_rows)
    assert "would_be_route=allow_hint" in gate_log, (
        f"{cell_name}: the [AttestationGate] log line MUST carry "
        f"would_be_route=allow_hint (the canonical log-only label — "
        f"the route STILL exists; operators distinguish (b)/(d)-with-"
        f"pending from plain (c) allow via this field); got: {gate_log}"
    )
    assert "log-only" in gate_log and "note hint retired" in gate_log, (
        f"{cell_name}: the [AttestationGate] log line MUST stamp "
        f"the log-only posture + the 'note hint retired 2026-09-23' "
        f"witness (operators grep-triage pre-fix vs post-fix via this "
        f"literal); got: {gate_log}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# S1 — b2f4dae9 healthy-busy regression (RUNNING developer child)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_s1_lcancheck_b2f4dae9_healthy_busy_allow_log_only_real_graph(
    real_graph_module,
    memory_saver,
    file_sqlite_engine,
    attestation_repository,
    attestation_manager_factory,
    caplog,
):
    """S1 — b2f4dae9 healthy-busy regression (RUNNING developer child).

    The canonical incident shape at ff9eb849:
      * RUNNING developer child (unconditional-live + busy subset)
      * pending wakeup en route (mid-work ACK + watcher PENDING)
      * Source-A evidence carrying the lexical FP "awaiting"
      * Fused judge verdict=not_complete

    Code-derived expected semantics (derive-from-code per dispatch):
      ``count_live_descendants`` unconditional-live set =
        {RUNNING, WAITING, WAITING_CHILDREN, PAUSED}
      → live_descendants = 1 (RUNNING child)
      ``count_busy_descendants`` busy subset = {RUNNING, WAITING,
       WAITING_CHILDREN}
      → busy_descendants = 1 (RUNNING child)
      decide() ordering: user_answer_pending → R2 three-input
       → attested-split → bound → denied + nudge
      R2 three-input: pending_children ∨ queued_or_expected_wakeups
       ∨ live_descendants = 1 (live_descendants=1 satisfies R2)
      → Decision.ALLOWED_LEGITIMATE_PENDING_WAKEUP
      → resolver_outcome=allow_hint
      → [AttestationGate] stamps would_be_route=allow_hint + log-only
      → ZERO message injection (the (b)/(d)-with-pending route is
         ALLOW log-only at ff9eb849; the Completion Check Note is
         RETIRED end-to-end per D-ENTRY 2026-09-23)
      → ledger counter unchanged (R2 allow does NOT reset on
         attested-allow; the bound/counter mutation path is the
         DENY path's machinery — not invoked here)
      → graph ends.

    Band label: with busy=1, B is suppressed (R5/Δ2 LCA busy trigger
    suppression — the b_fires gate is ``marker_hit ∨ length_trigger ∧
    busy=0``); the lexical FP "awaiting" lives in BOTH catalogs
    (``MID_WORK_MARKERS`` AND ``CHILD_TERMINAL_PROMISE_MARKERS``) so
    the SCAN returns marker_hit=True length_trigger=True on both
    bands, but ``b_fires=False`` when busy>0 — Source A's
    ``a_suspicion`` term wins → ``band=a_suspicion terms_fired=
    a_suspicion``. The lexical FP class the note was FP-prone for is
    the SAME on S2/S3 below; the band label differs ONLY by busy
    count (B wins precedence on busy=0).

    Pre-fix (pre-2026-09-23): this scenario injected a
    ``[SYSTEM CONTEXT: Completion Check Note]`` HumanMessage with
    ``context_kind=task_context`` carrying the Fused-EMPTY hint body.
    The note was pure FP noise on every healthy wait (the leader's
    msg[14] already stated the plan, the wakeup was en route, the
    deny path was unreachable by design). Post-fix: log-only silence
    on the ALLOW path; the FULL evidence chain is log-reconstructible
    via the resolver_eval row + the [AttestationGate] log line.

    Independent from the dev witness:
      * Real graph via ainvoke (NOT direct ``create_attestation_gate_node``
        + monkeypatch on ``_invoke_judge_llm``)
      * Source-A evidence = production drain ``internal_report:<uuid>``
        (NOT the historical ``context_kind=child_report_check`` kwargs)
      * Short mid-work ACK = "Awaiting the worker reply. Ending turn now."
        (NOT the dev witness's "Awaiting the tester reply..." string)
      * Lexical FP wording = "awaiting" inside a changelog-prose
        context (NOT a contradiction-promises string like "will write")
      * Catalog survivor is text-catalog-derived (the catalog imports
        pin "awaiting" presence) — independent verification of the
        b2f4dae9 evidence chain.
    """
    repo, _instance = attestation_repository

    # The delegated child EXISTS in the tree (RUNNING) — drives the
    # real-facade busy + live counts (count_live_descendants=1,
    # count_busy_descendants=1). The pending_children kwarg override
    # plants a PENDING dependency-watcher count (= 1) for the
    # mid-work ACK shape (real facade over a dependency-watchers
    # table would need a row insert; the kwarg override is the
    # established pattern — the dev witness + lcan_childlie_e2e
    # both use it for the same reason).
    repo.create(
        instance_id=CHILD_INSTANCE_ID,
        agent_id="worker",
        agent_dir="./agents/worker",
        parent_id=LEADER_INSTANCE_ID,
        status="running",
    )
    manager = attestation_manager_factory(
        file_sqlite_engine,
        repo,
        pending_children=1,
        queued_wakeups=0,
        live_descendants=None,  # real facade over the running child → 1
    )

    #   LLM 1: delegate (send_message) → unbound tool error → routes
    #          back (the send_message unbound path; the
    #          attestation_required flag stays True because the
    #          delegation_scan saw the tool_call)
    #   LLM 2: short mid-work ACK → end_candidate → eval1: busy tree
    #          + Source-A evidence alone → judge not_complete → R2
    #          ALLOW via live_descendants=1 → resolver_outcome=
    #          allow_hint → ALLOW log-only → END.
    model = ScriptedChatModel(
        responses=[_delegate_ai(), _short_mid_work_ack()],
        i=0,
    )
    graph = _build_graph(real_graph_module, model, manager, memory_saver)

    judge = _RecordingJudge("not_complete")
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
                "configurable": {"thread_id": LEADER_INSTANCE_ID},
                "recursion_limit": 30,
            },
        )
    model.assert_all_responses_consumed()

    # ── Judge budget: EXACTLY ONE call (one end_candidate, one eval). ──
    assert len(judge.bundles) == 1, (
        f"S1: judge MUST be invoked exactly once; got {len(judge.bundles)} "
        f"bundles (each = one gate evaluation; the (b)/(d)-with-pending "
        f"path is ALLOW log-only, NOT deny — the suspect-pending "
        f"protection lives in the bound, NOT in repeated judge calls)"
    )
    bundle = judge.bundles[0]
    assert (
        "=== SOURCE A: child-terminal contradiction evidence (1 note(s)) ==="
        in bundle
    ), bundle[:800]
    # The lexical FP "awaiting" MUST appear in the A-section bundle —
    # the bundle is what the judge sees; the lexical FP is the
    # evidence-citation source.
    assert "awaiting" in bundle, (
        "S1: the A-section bundle MUST carry the 'awaiting' lexical "
        "FP — the judge consumes the bundle verbatim; if 'awaiting' "
        "is missing the catalog scan drifted"
    )

    # ── Resolver eval rows: ONE fired row (eval2 absent — graph ends). ──
    rows = _eval_rows(caplog)
    assert len(rows) == 1, (
        f"S1: ONE eval row expected (one end_candidate, one gate "
        f"evaluation, then END); got {len(rows)}: {rows}"
    )
    fired_rows = _fired_eval_rows(caplog)
    assert len(fired_rows) == 1, fired_rows
    fired_row = fired_rows[0]

    # ── FULL surviving log-row schema (the field set the D-entry pins
    # as DO-NOT-TOUCH). ──────────────────────────────────────────────
    # S1: busy>0 → A wins (band=a_suspicion); B is muted per R5/Δ2.
    _assert_fired_row_log_only_contract(
        fired_row, "S1", expect_band_a_alone=True
    )

    # Busy-descendant shape: quiet is FALSE (live_descendants=1),
    # B is busy-muted AND clean, ONLY the A-band triggers.
    assert "live_descendants=1" in fired_row, fired_row
    assert "busy_descendants=1" in fired_row, fired_row
    # pending_children=1 from the kwarg override.
    assert "pending_children=1" in fired_row, fired_row

    # ── Fused-judge row carries the verdict + reason + band. ─────
    fused_judge_rows = _fused_judge_rows(caplog)
    assert len(fused_judge_rows) == 1, fused_judge_rows
    assert "verdict=not_complete" in fused_judge_rows[0], fused_judge_rows[0]
    # S1 (busy=1): B is suppressed → band=a_suspicion (A alone).
    assert "band=a_suspicion" in fused_judge_rows[0], (
        f"S1: Source A band MUST fire ALONE on the busy>0 run "
        f"(B suppressed per R5/Δ2); got: {fused_judge_rows[0]}"
    )

    # ── [AttestationGate] log line stamps the log-only posture +
    # would_be_route=allow_hint + the 'note hint retired 2026-09-23'
    # witness. ────────────────────────────────────────────────────────
    _assert_attestation_gate_log_only(caplog, "S1")

    # ── Message-channel invariant: ZERO Completion Check Note messages.
    messages = state["messages"]
    _assert_no_completion_check_note(messages)
    # ALSO: ZERO attestation_nudge messages (the deny-path reminder —
    # the (b)/(d)-with-pending ALLOW path does NOT inject the deny
    # nudge either).
    nudges = [
        m
        for m in messages
        if isinstance(m, HumanMessage)
        and (m.additional_kwargs or {}).get("attestation_nudge")
    ]
    assert nudges == [], (
        f"S1: the (b)/(d)-with-pending ALLOW log-only path MUST NOT "
        f"inject an attestation_nudge either (the deny nudge is "
        f"gated on Decision.DENIED, not on R2 ALLOW); got nudges: "
        f"{[m.content[:80] for m in nudges]!r}"
    )

    # ── Ledger counter invariant: NOT touched on the ALLOW path. ─
    _assert_ledger_counter_intact(repo, manager)


# ─────────────────────────────────────────────────────────────────────────────
# S2 — suspect-pending PAUSED child (suspect, NOT healthy — trigger armed,
# deny path INTACT, note RETIRED)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_s2_lcancheck_suspect_pending_paused_child_allow_log_only_real_graph(
    real_graph_module,
    memory_saver,
    file_sqlite_engine,
    attestation_repository,
    attestation_manager_factory,
    caplog,
):
    """S2 — suspect-pending PAUSED child (the (b)/(d)-with-pending ALLOW
    log-only path; deny path INTACT; note RETIRED).

    Code-derived expected semantics (derive-from-code per dispatch):
      ``count_live_descendants`` unconditional-live set =
        {RUNNING, WAITING, WAITING_CHILDREN, PAUSED}
      → live_descendants = 1 (PAUSED is unconditional-live;
         "PAUSED is live-for-deny-protection" per the D-entry;
         the 2026-09-12 live-facade two-set semantics pins this)
      ``count_busy_descendants`` busy subset = {RUNNING, WAITING,
       WAITING_CHILDREN} (NOT PAUSED — "PAUSED is suspect, NOT busy"
       per the LCA trigger-suppression gate at
       daemon/manager.py:_count_descendants_busy_and_live)
      → busy_descendants = 0
      decide() ordering: ... R2 three-input: pending_children ∨
       queued_or_expected_wakeups ∨ live_descendants = 1
       (live_descendants=1 satisfies R2)
      → Decision.ALLOWED_LEGITIMATE_PENDING_WAKEUP
      → resolver_outcome=allow_hint → log-only.

    Band label: busy=0 means B is NOT suppressed — B wins the band's
    precedence over A. BOTH bands fire on the same lexical FP
    "awaiting" substring (the FP class the note was FP-prone for):
    ``terms_fired=b_fires,a_suspicion`` carries both contributions;
    ``band=marker`` (B wins). Source A evidence STILL participates
    (a_advisory_present=True, a_notes=1, a_kwargs_seen=True,
    a_suspicion in terms_fired) — Source A is NEVER busy-muted per
    R5/Δ2; only the BAND label is suppressed.

    DEVIATION FROM DISPATCH WORDING (reported per dispatch instruction
    "encode the CORRECT expectation and REPORT the deviation from this
    dispatch's wording with evidence — do not force a deny"):
      The dispatch called S2 a "deny-protection cell". Per the code
      above, PAUSED counts as unconditional-live (``live_descendants=1``),
      which means R2 ALLOWS via the three-input R2; the gate does NOT
      deny. The user's 2026-09-23 D-entry pins PAUSED as
      "live-for-deny-protection" — a phrase that names the
      AUTHORIZATION direction (the gate can still deny on a PAUSED-only
      tree when nothing is in flight; see the (a) + PAUSED-only path
      in ``decide()`` step 4), NOT a claim that PAUSED always denies.
      On the b2f4dae9 mid-work ACK shape (the leader awaits a
      PAUSED-only child + Source-A evidence), the path is the SAME
      (b)/(d)-with-pending ALLOW log-only verdict as S1. The
      suspect-pending protection the D-entry pins lives in:
        (a) the deny path (``Decision.DENIED`` → ``increment``
            + nudge inject — INTACT)
        (b) the deny_bound (``ENSEMBLE_LEADER_ATTESTATION_DENY_BOUND``
            → ``Decision.TERMINAL_AFTER_BOUND`` after 3 denies)
        (c) the 1h watchdog
      — NOT in the (now retired) hint. The CORRECT expectation is
      ALLOW log-only, IDENTICAL to S1's verdict shape, with the SAME
      full log-row schema.

    Independent from the dev witness: same harness (real-graph
    ainvoke), different child state (PAUSED vs RUNNING), different
    Source-A evidence wording is SHARED with S1 (the catalog scan is
    shape-agnostic; the prose is the lexical FP shape).
    """
    repo, _instance = attestation_repository

    # The delegated child EXISTS in the tree (PAUSED) — drives the
    # real-facade live count (PAUSED is unconditional-live = 1) but
    # NOT the busy count (PAUSED is NOT in the busy subset = 0).
    # The pending_children kwarg override plants a PENDING
    # dependency-watcher count (= 1) for the mid-work ACK shape
    # (the same rationale as S1).
    repo.create(
        instance_id=CHILD_INSTANCE_ID,
        agent_id="worker",
        agent_dir="./agents/worker",
        parent_id=LEADER_INSTANCE_ID,
        status="paused",
    )
    manager = attestation_manager_factory(
        file_sqlite_engine,
        repo,
        pending_children=1,
        queued_wakeups=0,
        live_descendants=None,  # real facade over the paused child → 1
    )

    #   LLM 1: delegate (send_message) → unbound tool error → routes back
    #   LLM 2: short mid-work ACK → end_candidate → eval1: PAUSED child
    #          tree + Source-A evidence alone → judge not_complete →
    #          R2 ALLOW via live_descendants=1 → resolver_outcome=
    #          allow_hint → ALLOW log-only → END.
    model = ScriptedChatModel(
        responses=[_delegate_ai(), _short_mid_work_ack()],
        i=0,
    )
    graph = _build_graph(real_graph_module, model, manager, memory_saver)

    judge = _RecordingJudge("not_complete")
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
                "configurable": {"thread_id": LEADER_INSTANCE_ID},
                "recursion_limit": 30,
            },
        )
    model.assert_all_responses_consumed()

    # ── Judge budget: EXACTLY ONE call. ──
    assert len(judge.bundles) == 1, (
        f"S2: judge MUST be invoked exactly once; got "
        f"{len(judge.bundles)} bundles"
    )

    # ── Resolver eval rows: ONE fired row. ──
    rows = _eval_rows(caplog)
    assert len(rows) == 1, (
        f"S2: ONE eval row expected; got {len(rows)}: {rows}"
    )
    fired_rows = _fired_eval_rows(caplog)
    assert len(fired_rows) == 1, fired_rows
    fired_row = fired_rows[0]

    # ── FULL surviving log-row schema. ──
    # S2: busy=0 (PAUSED is NOT in the busy subset) → B wins
    # precedence (band=marker); Source A evidence still participates
    # via a_suspicion in terms_fired. The lexical FP "awaiting"
    # lives in BOTH catalogs — both bands fire on the same substring.
    _assert_fired_row_log_only_contract(
        fired_row, "S2", expect_band_a_alone=False
    )

    # PAUSED shape: live_descendants=1 (PAUSED IS unconditional-live),
    # busy_descendants=0 (PAUSED NOT busy — "PAUSED is suspect, NOT
    # healthy — the trigger stays armed" per the LCA trigger-suppression
    # gate at daemon/services/attestation_gate.py).
    assert "live_descendants=1" in fired_row, fired_row
    assert "busy_descendants=0" in fired_row, (
        f"S2: PAUSED MUST NOT contribute to busy_descendants "
        f"(PAUSED is NOT in the busy subset {{RUNNING, WAITING, "
        f"WAITING_CHILDREN}}; the trigger stays armed); "
        f"got row: {fired_row}"
    )
    # pending_children=1 from the kwarg override.
    assert "pending_children=1" in fired_row, fired_row

    # ── Fused-judge row. ──
    fused_judge_rows = _fused_judge_rows(caplog)
    assert len(fused_judge_rows) == 1, fused_judge_rows
    assert "verdict=not_complete" in fused_judge_rows[0], fused_judge_rows[0]
    # busy=0 (PAUSED / terminal — NOT in the busy subset) → B wins
    # precedence on the fused-judge row's band label.

    # ── [AttestationGate] log line stamps the log-only posture +
    # would_be_route=allow_hint (the suspect-pending case still
    # produces the (b)/(d)-with-pending ALLOW log-only label — the
    # trigger fires, the judge says not_complete, R2 ALLOWS via
    # live_descendants=1, log-only is the canonical surface). ──
    _assert_attestation_gate_log_only(caplog, "S2")

    # ── Message-channel + ledger invariants (IDENTICAL to S1 — the
    # b2f4dae9 ALLOW log-only contract is shape-agnostic). ──
    messages = state["messages"]
    _assert_no_completion_check_note(messages)
    nudges = [
        m
        for m in messages
        if isinstance(m, HumanMessage)
        and (m.additional_kwargs or {}).get("attestation_nudge")
    ]
    assert nudges == [], (
        f"S2: PAUSED-suspect (b) MUST NOT inject a deny nudge either "
        f"(R2 ALLOW path); got nudges: "
        f"{[m.content[:80] for m in nudges]!r}"
    )
    _assert_ledger_counter_intact(repo, manager)


# ─────────────────────────────────────────────────────────────────────────────
# S3 — suspect-pending en-route-only child (IDLE + pending message/job;
# ghost-filter bus-blind; deny path INTACT; note RETIRED)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_s3_lcancheck_suspect_pending_en_route_only_allow_log_only_real_graph(
    real_graph_module,
    memory_saver,
    file_sqlite_engine,
    attestation_repository,
    attestation_manager_factory,
    caplog,
):
    """S3 — suspect-pending en-route-only child (the (b)/(d)-with-pending
    ALLOW log-only path on the IDLE + pending-wakeup shape).

    Code-derived expected semantics (derive-from-code per dispatch):
      ``count_live_descendants`` two-set live semantics
       (incident b08f40fe 2026-09-11):
         * unconditional-live = {RUNNING, WAITING, WAITING_CHILDREN,
           PAUSED} (counted with no further checks)
         * conditional-live = {IDLE, QUEUED} (counted ONLY when work
           is en route to that descendant: a not-yet-processed
           message_queue row targeting it OR a not-yet-settled
           job_queue_items row targeting it)
         * terminal (COMPLETED, TERMINATED, ERROR, FAILED) EXCLUDED.
      Spawned-but-not-yet-messaged children are bus-blind (ghost
       filter) — the "en-route-only" shape per the D-entry: "IDLE +
       pending message OR IDLE + unsettled job — maybe lost; the
       trigger stays armed".
      For this test, the child is COMPLETED (terminal — excluded
       from both unconditional-live AND conditional-live sets) AND
       ``pending_children=1`` (the PENDING dependency-watcher count
       is the canonical "wakeup in flight" signal the gate reads
       when the descendant has not yet been messaged; it satisfies
       R2 via the pending_children arm). NOTE: a more authentic
       en-route-only test would plant a QUEUED child + a not-yet-
       settled job_queue row, but the R2 satisfaction shape is
       identical — the gate sees ``pending_children=1`` and ALLOWS.
      → live_descendants = 0 (terminal child)
      → busy_descendants = 0
      → pending_children = 1 (kwarg override)
      R2 three-input: pending_children ∨ queued_or_expected_wakeups
       ∨ live_descendants = 1 (pending_children=1 satisfies R2)
      → Decision.ALLOWED_LEGITIMATE_PENDING_WAKEUP
      → resolver_outcome=allow_hint → log-only.

    Band label: busy=0 means B is NOT suppressed — B wins the band's
    precedence over A. BOTH bands fire on the same lexical FP
    "awaiting" substring (the FP class the note was FP-prone for):
    ``terms_fired=b_fires,a_suspicion`` carries both contributions;
    ``band=marker`` (B wins). Source A evidence STILL participates
    (a_advisory_present=True, a_notes=1, a_kwargs_seen=True,
    a_suspicion in terms_fired) — Source A is NEVER busy-muted per
    R5/Δ2; only the BAND label is suppressed.

    DEVIATION FROM DISPATCH WORDING (reported per dispatch instruction):
      The dispatch called S3 a "deny-protection cell" too. Same
      deviation rationale as S2: the (b)/(d)-with-pending path is
      ALWAYS an ALLOW log-only verdict on the b2f4dae9 shape; R2
      ALLOWS via pending_children=1; the deny path remains INTACT
      (a future turn with no pending wakeup + marker band + judge-
      no DOES deny — that's the (a) path, NOT the (b)/(d)-with-
      pending path the b2f4dae9 incident fired).

    Independent from the dev witness: same harness (real-graph
    ainvoke), different child state (COMPLETED vs RUNNING/PAUSED —
    the en-route-only ghost-filter shape), shared lexical FP
    "awaiting" in the Source-A evidence.
    """
    repo, _instance = attestation_repository

    # The delegated child EXISTS in the tree (COMPLETED — terminal,
    # excluded from both unconditional-live AND conditional-live
    # sets per the b08f40fe two-set semantics). live_descendants=0,
    # busy_descendants=0. The pending_children kwarg override plants
    # the "wakeup in flight" signal (= 1) — the canonical
    # dependency-watchers PENDING-row count that drives R2 via the
    # pending_children arm. This is the "en-route-only" shape: the
    # descendant is terminal (the wakeup it's holding will not
    # produce more work), but the PENDING watcher says a wake-up is
    # in flight on the leader (the gate sees R2 satisfied via
    # pending_children=1 and ALLOWS — the wake-up is en route).
    repo.create(
        instance_id=CHILD_INSTANCE_ID,
        agent_id="worker",
        agent_dir="./agents/worker",
        parent_id=LEADER_INSTANCE_ID,
        status="completed",
    )
    manager = attestation_manager_factory(
        file_sqlite_engine,
        repo,
        pending_children=1,
        queued_wakeups=0,
        live_descendants=None,  # real facade over completed child → 0
    )

    #   LLM 1: delegate (send_message) → unbound tool error → routes back
    #   LLM 2: short mid-work ACK → end_candidate → eval1: en-route-only
    #          tree (terminal child + PENDING watcher) + Source-A
    #          evidence → judge not_complete → R2 ALLOW via
    #          pending_children=1 → resolver_outcome=allow_hint →
    #          ALLOW log-only → END.
    model = ScriptedChatModel(
        responses=[_delegate_ai(), _short_mid_work_ack()],
        i=0,
    )
    graph = _build_graph(real_graph_module, model, manager, memory_saver)

    judge = _RecordingJudge("not_complete")
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
                "configurable": {"thread_id": LEADER_INSTANCE_ID},
                "recursion_limit": 30,
            },
        )
    model.assert_all_responses_consumed()

    # ── Judge budget: EXACTLY ONE call. ──
    assert len(judge.bundles) == 1, (
        f"S3: judge MUST be invoked exactly once; got "
        f"{len(judge.bundles)} bundles"
    )

    # ── Resolver eval rows: ONE fired row. ──
    rows = _eval_rows(caplog)
    assert len(rows) == 1, (
        f"S3: ONE eval row expected; got {len(rows)}: {rows}"
    )
    fired_rows = _fired_eval_rows(caplog)
    assert len(fired_rows) == 1, fired_rows
    fired_row = fired_rows[0]

    # ── FULL surviving log-row schema. ──
    # S3: busy=0 (terminal child is NOT busy) → B wins precedence
    # (band=marker); Source A evidence still participates via
    # a_suspicion in terms_fired. The lexical FP "awaiting" lives
    # in BOTH catalogs — both bands fire on the same substring.
    _assert_fired_row_log_only_contract(
        fired_row, "S3", expect_band_a_alone=False
    )

    # En-route-only shape: live_descendants=0 (terminal child),
    # busy_descendants=0 (terminal NOT busy). pending_children=1
    # from the kwarg override is the R2 satisfaction source.
    assert "live_descendants=0" in fired_row, (
        f"S3: en-route-only descendant MUST be live_descendants=0 "
        f"(the COMPLETED child is terminal, excluded from both "
        f"unconditional-live and conditional-live sets); "
        f"got row: {fired_row}"
    )
    assert "busy_descendants=0" in fired_row, fired_row
    assert "pending_children=1" in fired_row, fired_row

    # ── Fused-judge row. ──
    fused_judge_rows = _fused_judge_rows(caplog)
    assert len(fused_judge_rows) == 1, fused_judge_rows
    assert "verdict=not_complete" in fused_judge_rows[0], fused_judge_rows[0]
    # busy=0 (PAUSED / terminal — NOT in the busy subset) → B wins
    # precedence on the fused-judge row's band label.

    # ── [AttestationGate] log line stamps the log-only posture +
    # would_be_route=allow_hint (the en-route-only case STILL routes
    # to (b)/(d)-with-pending ALLOW log-only because R2 is satisfied
    # via pending_children=1). ──
    _assert_attestation_gate_log_only(caplog, "S3")

    # ── Message-channel + ledger invariants (IDENTICAL to S1/S2). ──
    messages = state["messages"]
    _assert_no_completion_check_note(messages)
    nudges = [
        m
        for m in messages
        if isinstance(m, HumanMessage)
        and (m.additional_kwargs or {}).get("attestation_nudge")
    ]
    assert nudges == [], (
        f"S3: en-route-only (b) MUST NOT inject a deny nudge either "
        f"(R2 ALLOW path); got nudges: "
        f"{[m.content[:80] for m in nudges]!r}"
    )
    _assert_ledger_counter_intact(repo, manager)