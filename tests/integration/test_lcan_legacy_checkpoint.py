"""LCAN: LEGACY-CHECKPOINT DEFENSE for the LCA advisory-note-removal merge gate.

Pre-removal leader checkpoints can carry a delivered ``Child Report Check``
note minted by ``daemon/services/child_reports.py`` (commit family pre-
``6a695b8f``, surface prior to ``git show 858b1038:daemon/services/child_reports.py``).
The ``6a695b8f`` removal deletes the producer, but the resolver-side
``_is_child_report_check_note`` / ``collect_source_a_signals`` legacy read
branch is RETAINED as defense-in-depth so that a leader whose mid-flight
checkpoint still carries an OLD note message continues to A-band-activate
through the kept path (no silent regression across the upgrade).

This file pins the contract at THREE scenarios:

S1 — LEGACY NOTE ACTIVATES. A pre-removal leader checkpoint carrying the
exact OLD note shape activates the A-band via the kept legacy-note read
branch; deny+nudge engages (the standard c_quiet+witnessed-a_suspicion
shape), the resolver-eval row carries ``a_advisory_present=True``,
``a_notes=1``, ``a_kwargs_seen=True``, and the captured snapshot's
evidence row carries ``stable_id="child_report_check:<parent>:<child>"``
with ``kwargs_surface_seen=True`` (the dual-surface detector recognized
the structured kwargs, not just the body-prefix fallback). The
attestation window on the second turn resolves cleanly to plain-allow
(R4 short-circuit).

S2 — NO RE-MINT. The legacy note is read, never refreshed. After the gate
evaluation, the gate's appended messages carry ZERO
``CONTEXT_KIND_CHILD_REPORT_CHECK`` rows beyond the fixture one (defends
against any silent producer re-introduction in the post-removal graph
node).

S3 — MIXED. Legacy note PLUS a fresh ``internal_report:<child>``-stamped
``HumanMessage`` (the post-removal live A-signal source) → the gate
activates with one trigger (pass-1 + pass-2 cap-bounded), judge fires
EXACTLY ONCE (the call counter on the judge wrapper records a single
invocation), no double-triggered / no double-nudge.

Pre-removal note shape (derived from
``git show 858b1038:daemon/services/child_reports.py`` lines 3180-3260 +
``git show 858b1038:daemon/services/context_messages.py`` lines 113-115 /
184 / 285-303):

  * ``id``            = ``child_report_check:{parent_id}:{child_id}``
                        (minted via ``_stable_id_for("child_report_check",
                        instance_id=parent_id, agent_id=child_id)``).
  * ``content``       = ``[SYSTEM CONTEXT: Child Report Check]\\n\\n`` +
                        ``Child <child_id> completed while its final
                        report promises future work ("<terms>") — likely
                        premature completion. Its promised next report
                        will never arrive. Verify the actual work state;
                        if unfinished, revive it via send_message (e.g.
                        "continue your work") or verify its subtree
                        before relying on this report. (Advisory /
                        heuristic — marker scan is a substring match,
                        not an LLM verdict.)``.
  * ``additional_kwargs`` carries the canonical-factory pair
    (``injected_message=True``, ``context_kind="child_report_check"``) and
    the producer-specific flag pair
    (``child_report_check=True``, ``child_report_check_terms=[...]``).

The post-removal detector at HEAD
(``daemon/services/attestation_resolver_activation.py:_is_child_report_check_note``)
recognizes both surfaces:

  * ``additional_kwargs.context_kind == CONTEXT_KIND_CHILD_REPORT_CHECK``
    OR
  * ``content.startswith("[SYSTEM CONTEXT: Child Report Check]")``.

PIN DESIGN: NO real LLM calls (mocked ``_invoke_judge_llm``); NO real DB
(stub manager + ledger); production frozen — this file is test-only,
NEW file. Mirrors the harness of
``tests/integration/test_attestation_marker_routing_lca.py`` (stub
manager + ledger, mocked judge) and
``tests/integration/test_attestation_user_answer_pending_lca.py``.

Verifier-side introspection (kept lightweight to avoid edit-trail
fragility): a small ``_SnapshotCapture`` wraps the gate's reference to
``evaluate_resolver_activation`` and records each ``ResolverEvalSnapshot``
the gate produces, so the tests can assert on the upstream
``SourceASignals.evidence`` rows directly (the source-of-truth data the
resolver-eval log row summarizes).
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from dataclasses import replace
from typing import Any
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from daemon.services import (
    attestation_report_judge as judge_mod,
)
from daemon.services import (
    attestation_resolver_activation as ara_mod,
)
from daemon.services.attestation_gate import (
    GateSettings,
    build_gate_config,
)
from daemon.services.attestation_resolver import (
    reset_attestation_resolver_for_tests,
)
from daemon.services.attestation_judge_resolver import (
    reset_llm_judge_resolver_for_tests,
)
from daemon.services.context_messages import (
    CONTEXT_KIND_CHILD_REPORT_CHECK,
)
from daemon.graph import (
    create_attestation_gate_node,
)


# ─────────────────────────────────────────────────────────────────────────────
# Constants — pre-removal note shape fingerprint
# ─────────────────────────────────────────────────────────────────────────────


#: Pipeline instance id shared across the three scenarios (one per test,
#: so the resolver caches reset between runs; the resolvers are reset
#: in the autouse fixture below).
LCAN_INSTANCE_ID = "lcan-legacy-checkpoint-defense"

#: FIXTURE parent + child ids — used by the legacy note's stable-id mint
#: format (``child_report_check:{parent}:{child}``). The child id is a
#: real-shaped UUID so the resolver's ``_INTERNAL_REPORT_ID_FROM_SOURCE_RE``
#: regex can match it identically across S1/S2/S3.
LCAN_PARENT_ID = "lcan-parent-00000000-0000-0000-0000-000000000001"
LCAN_CHILD_ID = "00000000-0000-4000-8000-000000000001"
LCAN_FRESH_CHILD_ID = "00000000-0000-4000-8000-000000000002"

#: Stable-id format was ``child_report_check:{parent_id}:{agent_id}``
#: pre-removal (git show 858b1038:daemon/services/context_messages.py
#: lines 184 / 285-303). Pinned here as a literal so the test catches any
#: drift in the mint-site format. The HEAD factory removed the
#: ``child_report_check`` branch (verified live — ValueError on call)
#: so we hand-build the id for the fixture.
LCAN_LEGACY_STABLE_ID = (
    f"child_report_check:{LCAN_PARENT_ID}:{LCAN_CHILD_ID}"
)

#: Catalog terms that the legacy producer included in
#: ``additional_kwargs["child_report_check_terms"]`` and that also surface
#: quoted in the note body. Picked from the
#: ``CHILD_TERMINAL_PROMISE_MARKERS`` catalog at HEAD so the A-scan's
#: body-prefix fallback can re-derive them via
#: ``scan_child_terminal_report_for_promises``.
LCAN_TERMS: tuple[str, ...] = ("ending turn", "will write")


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures — hermetic per-test isolation
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_resolvers(monkeypatch):
    """Fresh resolver cache + clean kill-switch env per test (hermetic).

    Pin to ``enforce`` so the gate's deny/allow table matches production
    default. Disable WATCHOVER so it does not spawn a child that races
    the test thread.
    """
    monkeypatch.setenv("ENSEMBLE_LEADER_ATTESTATION_MODE", "enforce")
    monkeypatch.setenv("WATCHOVER_ENABLED", "false")
    monkeypatch.delenv(
        "ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED", raising=False
    )
    reset_attestation_resolver_for_tests()
    reset_llm_judge_resolver_for_tests()
    yield
    reset_attestation_resolver_for_tests()
    reset_llm_judge_resolver_for_tests()


@pytest.fixture
def snapshot_capture(monkeypatch):
    """Capture every ``ResolverEvalSnapshot`` the gate emits in this test.

    The gate uses a *function-local* ``from .attestation_resolver_activation
    import evaluate_resolver_activation`` (gate.py:1260-1265) — the symbol
    is NOT a module attribute on ``daemon.services.attestation_gate``.
    Patching the importing module's namespace has no effect because the
    binding only exists in the function's locals at call time.

    The correct seam is the SOURCE module
    (``daemon.services.attestation_resolver_activation``). The gate's
    lazy re-import rebinds to whatever ``ara_mod.evaluate_resolver_activation``
    points to on each invocation, so a module-level patch there is
    picked up. The patch is a delegating wrapper that records the
    snapshot and forwards to the real implementation; downstream
    ``assemble_fused_bundle`` / ``emit_resolver_eval_row`` paths run
    unchanged inside the real call.
    """
    captured: list[Any] = []

    real_fn = ara_mod.evaluate_resolver_activation

    def _capturing(*args, **kwargs):
        snapshot = real_fn(*args, **kwargs)
        captured.append(snapshot)
        return snapshot

    monkeypatch.setattr(ara_mod, "evaluate_resolver_activation", _capturing)
    return captured


def _make_node(
    *,
    instance_id: str,
    pending_children: int = 0,
    queued_wakeups: int = 0,
    live_descendants: int = 0,
    busy_descendants: int = 0,
    manager: MagicMock | None = None,
    ledger: MagicMock | None = None,
    settings: GateSettings | None = None,
    llm_judge_enabled: bool = True,
):
    """Build the gate node with stub manager + ledger (NO real DB)."""
    if manager is None:
        manager = MagicMock()
        manager.count_pending_children.return_value = pending_children
        manager.get_queued_or_expected_wakeups.return_value = queued_wakeups
        manager.count_live_descendants.return_value = live_descendants
        manager.count_busy_descendants.return_value = busy_descendants
        manager.get_tree_ids_permanent.return_value = []
        manager.has_open_user_answer = MagicMock(return_value=False)
        manager.enqueue_message = MagicMock()
        manager.revive = MagicMock()
        manager.send_message = MagicMock()

    if ledger is None:
        ledger = MagicMock()
        ledger.increment.return_value = 1
        ledger.reset.return_value = True
        ledger.set_escalated_and_reset.return_value = True
        ledger.get.return_value = 0

    if settings is None:
        settings = GateSettings("enforce", 3, 3)

    config = build_gate_config(
        instance_id,
        settings,
        llm_judge_enabled=llm_judge_enabled,
    )
    node = create_attestation_gate_node(
        config,
        settings,
        manager,
        instance_id,
        denied_count_getter=lambda: 0,
        ledger=ledger,
    )
    return node, manager, ledger


# ─────────────────────────────────────────────────────────────────────────────
# Pre-removal note fixture (shape derived from
# ``git show 858b1038:daemon/services/child_reports.py`` lines 3180-3260)
# ─────────────────────────────────────────────────────────────────────────────


def _legacy_child_report_check_note(
    parent_id: str = LCAN_PARENT_ID,
    child_id: str = LCAN_CHILD_ID,
    terms: tuple[str, ...] = LCAN_TERMS,
    stable_id: str | None = None,
) -> HumanMessage:
    """Build the EXACT pre-removal ``Child Report Check`` note shape.

    Reproduces, byte-for-shape, what ``daemon/services/child_reports.py``
    shipped between ``ab3b5dc1`` and ``6a695b8f`` (the D-CTD-7 removal).
    Used as the FIXTURE checkpoint-resident message in every LCAN scenario.

    The shape is identical to the canonical factory in
    ``daemon/services/context_messages.py`` (``_make_context_message`` +
    ``_stable_id_for("child_report_check", instance_id, agent_id)``) but
    built inline so the test does not depend on the factory still
    accepting this branch (the factory removed its ``child_report_check``
    kind at HEAD — verified live: ``_stable_id_for('child_report_check',
    ...)`` raises ``ValueError``). The detector's ``context_kind``+prefix
    dual-surface is what recognizes the shape.
    """
    body = (
        f'Child {child_id} completed while its final report '
        f'promises future work ("{", ".join(terms)}") \u2014 likely '
        f"premature completion. Its promised next report will never "
        f"arrive. Verify the actual work state; if unfinished, revive "
        f'it via send_message (e.g. "continue your work") or verify '
        f"its subtree before relying on this report. (Advisory / "
        f"heuristic \u2014 marker scan is a substring match, not an "
        f"LLM verdict.)"
    )
    msg = HumanMessage(
        content=f"[SYSTEM CONTEXT: Child Report Check]\n\n{body}",
        id=stable_id if stable_id is not None else LCAN_LEGACY_STABLE_ID,
    )
    msg.additional_kwargs["injected_message"] = True
    msg.additional_kwargs["context_kind"] = CONTEXT_KIND_CHILD_REPORT_CHECK
    msg.additional_kwargs["child_report_check"] = True
    msg.additional_kwargs["child_report_check_terms"] = list(terms)
    return msg


def _fresh_internal_report_message(
    content: str,
    child_id: str,
) -> HumanMessage:
    """Build a LIVE post-removal child-report ``HumanMessage``.

    Mirrors the stamp emitted by ``daemon/graph.py`` ~line 6950
    (``additional_kwargs={"injected_message": True, "source":
    f"internal_report:{report_child_iid}"}``) and the fallback
    ``_stamped_additional_kwargs`` path at
    ``daemon/services/instance_messaging.py:525``. S3 plants one of these
    in the same message history as the legacy note to verify the
    dual-source path.
    """
    source = f"internal_report:{child_id}:{uuid.uuid4()}"
    msg = HumanMessage(
        content=content,
        id=str(uuid.uuid4()),
        additional_kwargs={
            "injected_message": True,
            "source": source,
        },
    )
    return msg


def _state_with_legacy_note(
    final_text: str,
    *,
    note: HumanMessage | None = None,
    extra_messages: list | None = None,
) -> dict:
    """Build a leader graph state carrying the legacy note + delegated tail.

    The shape mirrors the LCA scenario-(a)/scenario-(d1) "delegated mission
    + markers + nothing pending" fixture in
    ``tests/integration/test_attestation_marker_routing_lca.py``: a real
    HumanMessage root, a delegation ``AIMessage`` carrying a tool-call to
    ``send_message`` (which is what anchors
    ``attestation_required=True``), and a final ``AIMessage`` that the
    leader's turn ends on. The legacy note is injected AFTER the root
    HumanMessage (a long-running leader's checkpoint reads
    ``add_messages`` reducer order: note lands mid-flight, surviving the
    next compaction round via its
    ``context_kind=child_report_check`` permanent-hoist flag).
    """
    delegation_ai = AIMessage(
        content="",
        tool_calls=[
            {"name": "send_message", "args": {"target": LCAN_CHILD_ID}, "id": "c1"}
        ],
    )
    messages: list = [
        HumanMessage(content="please do it"),
    ]
    if note is not None:
        messages.append(note)
    if extra_messages:
        messages.extend(extra_messages)
    messages.append(delegation_ai)
    messages.append(AIMessage(content=final_text))
    return {"messages": messages}


# ─────────────────────────────────────────────────────────────────────────────
# Judge wrapper — pinned to ``not_complete`` so the gate's deny-band
# branch fires (mirrors scenario-(a) LCA harness)
# ─────────────────────────────────────────────────────────────────────────────


class _JudgeCallCounter:
    """Counter wrapper around the LLM judge so S3 can pin exactly-once."""

    def __init__(self, verdict: str = "not_complete"):
        self.verdict = verdict
        self.calls = 0

    async def __call__(
        self, config, user_payload, *, timeout_s, system_prompt=None
    ):
        self.calls += 1
        return (
            '{"verdict": "not_complete", "evidence_cited": [], '
            '"advisory_note_text": "", "rationale": "mid-work"}',
            "fake-quick",
        )


# ─────────────────────────────────────────────────────────────────────────────
# Log-row scratchers — pin the canonical resolver-eval row carries the
# legacy-note fingerprint (a_advisory_present=…, a_notes=…, a_kwargs_seen=…)
# ─────────────────────────────────────────────────────────────────────────────


_RESOLVER_EVAL_ROW_RE = re.compile(
    r"\bevent=leader_completion_resolver_eval\b"
)


def _resolver_eval_rows(caplog) -> list[str]:
    """Return the message strings of every resolver-eval log record."""
    return [
        rec.getMessage()
        for rec in caplog.records
        if _RESOLVER_EVAL_ROW_RE.search(rec.getMessage())
    ]


def _gate_rows(caplog) -> list[str]:
    """Return the message strings of every gate-event log record."""
    return [
        rec.getMessage()
        for rec in caplog.records
        if "event=leader_completion_gate " in rec.getMessage()
    ]


def _evidence_summary(snapshot) -> list[dict[str, Any]]:
    """Project ``snapshot.result.a_signals.evidence`` into test-friendly dicts."""
    a = snapshot.result.a_signals
    return [
        {
            "child_instance_id": ev.child_instance_id,
            "matched_terms": list(ev.matched_terms),
            "stable_id": ev.stable_id,
            "kwargs_surface_seen": ev.kwargs_surface_seen,
        }
        for ev in a.evidence
    ]


# ─────────────────────────────────────────────────────────────────────────────
# S1 — LEGACY NOTE ACTIVATES
# ─────────────────────────────────────────────────────────────────────────────


def test_s1_legacy_note_in_pre_removal_shape_activates_a_band(
    monkeypatch, caplog, snapshot_capture
):
    """S1: pre-removal leader checkpoint → A-band activation via legacy path.

    Constructs a leader graph state whose message history carries the
    EXACT pre-removal ``Child Report Check`` note shape (id, body,
    kwargs all matching the deleted mint site at
    ``git show 858b1038:daemon/services/child_reports.py``). Runs the
    real gate evaluation (real resolver, judge steered ``not_complete``
    so the deny-band branch engages after activation). Asserts:

    * The resolver-eval log row carries ``a_advisory_present=True``,
      ``a_notes=1``, ``a_kwargs_seen=True`` (the legacy note surface
      is recognized via the structured kwargs, not just the
      body-prefix fallback).
    * The captured snapshot's :class:`SourceASignals.evidence` row
      carries the legacy note's stable_id
      (``child_report_check:<parent>:<child>``) and
      ``kwargs_surface_seen=True`` (dual-surface detector).
    * The activation includes the ``a_suspicion`` term
      (``terms_fired`` tuple carries it); together with the quiet
      tree's ``c_quiet`` term the band precedence lands on ``deny``.
    * The deny+nudge contract fires: result carries a nudge message
      (``attestation_nudge=True`` additional kwarg), the ledger
      ``increment`` is called once.
    * The witness goes through the post-evaluation attested path on
      the second turn: re-running with a ``attest_completion`` tool
      call in window results in a plain allow (R4 short-circuit,
      counter reset path preserved).
    """
    counter = _JudgeCallCounter(verdict="not_complete")
    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", counter)

    # A-band source MUST come from the legacy note; ensure the
    # post-removal live child-report is NOT in the window (we want this
    # test to be a pure legacy-path pin).
    legacy_note = _legacy_child_report_check_note()
    state = _state_with_legacy_note(
        final_text="Awaiting final aggregation. Ending turn.",
        note=legacy_note,
    )

    # First turn: gate must deny because c_quiet + a_suspicion ⇒
    # band precedence picks deny (the ``attestation_required=True``
    # delegation + quiet tree).
    node, _manager, ledger = _make_node(instance_id=LCAN_INSTANCE_ID)
    with caplog.at_level(
        logging.INFO, logger="daemon.services.attestation_gate"
    ), caplog.at_level(
        logging.INFO,
        logger="daemon.services.attestation_resolver_activation",
    ):
        result = asyncio.run(
            node(
                state,
                config={"configurable": {"thread_id": LCAN_INSTANCE_ID}},
            )
        )

    # ── Snapshot must show A-band activation sourced from legacy note
    assert len(snapshot_capture) == 1, (
        f"S1: exactly one resolver snapshot expected on a single "
        f"gate evaluation; got {len(snapshot_capture)}"
    )
    snap = snapshot_capture[0]
    a_signals = snap.result.a_signals
    assert a_signals.advisory_present is True, (
        f"S1: SourceASignals.advisory_present MUST be True after the "
        f"legacy-note walk — got: {a_signals!r}"
    )
    assert len(a_signals.evidence) == 1, (
        f"S1: exactly one evidence row expected on a legacy-only "
        f"fixture; got {len(a_signals.evidence)}: "
        f"{_evidence_summary(snap)}"
    )
    ev = a_signals.evidence[0]
    assert ev.stable_id == LCAN_LEGACY_STABLE_ID, (
        f"S1: evidence row MUST carry the legacy note's stable_id "
        f"{LCAN_LEGACY_STABLE_ID!r} — got: "
        f"stable_id={ev.stable_id!r}, "
        f"kwargs_surface_seen={ev.kwargs_surface_seen}"
    )
    assert ev.kwargs_surface_seen is True, (
        f"S1: dual-surface detector MUST recognize the legacy note "
        f"via the STRUCTURED kwargs (context_kind=child_report_check "
        f"+ child_report_check=True), not just the body-prefix "
        f"fallback. kwargs_surface_seen=True is the precise pin — "
        f"got: kwargs_surface_seen={ev.kwargs_surface_seen}"
    )
    assert ev.child_instance_id == LCAN_CHILD_ID, (
        f"S1: legacy note's child_instance_id MUST surface in the "
        f"evidence row (the ``child_instance_id`` kwarg carried by "
        f"the structured shape) — got: "
        f"child_instance_id={ev.child_instance_id!r}"
    )
    # Catalog terms MUST be re-derivable from the body (defense-in-depth
    # for the body-prefix fallback path which the resolver also supports).
    assert any(t in LCAN_TERMS for t in ev.matched_terms), (
        f"S1: legacy note's matched_terms MUST include at least one "
        f"catalog term from {LCAN_TERMS!r}; got: matched_terms="
        f"{ev.matched_terms!r}"
    )

    # ── Activation predicate terms + band
    assert "a_suspicion" in snap.result.terms_fired, (
        f"S1: activation MUST fire on a_suspicion (the A-band term "
        f"from the legacy note) — got terms_fired="
        f"{snap.result.terms_fired!r}"
    )
    # When the tree is quiet + a_suspicion fires, band precedence picks
    # deny (deny > marker > a_suspicion). The legacy note path does NOT
    # suppress this — pin the band fingerprint.
    assert snap.result.band in ("deny", "a_suspicion"), (
        f"S1: band MUST resolve to deny or a_suspicion on this shape "
        f"(c_quiet wins precedence over a_suspicion); got band="
        f"{snap.result.band!r}, terms_fired={snap.result.terms_fired!r}"
    )
    assert snap.would_be_outcome == "would_deny_nudge", (
        f"S1: would_be_outcome MUST be would_deny_nudge on this shape "
        f"(band=deny, denied_count < bound) — got: "
        f"{snap.would_be_outcome!r}"
    )

    # ── Resolver-eval log row fingerprint ────────────────────────────
    eval_rows = _resolver_eval_rows(caplog)
    assert eval_rows, (
        "S1: a resolver-eval row MUST be emitted on every evaluation "
        "(Stage-2 unified predicate ALWAYS evaluates on a delegated turn)"
    )
    eval_row = eval_rows[-1]
    assert "a_advisory_present=True" in eval_row, (
        f"S1: resolver-eval row MUST carry a_advisory_present=True "
        f"(the SourceASignals surface summary) — got: {eval_row}"
    )
    assert " a_notes=1 " in eval_row or eval_row.endswith(" a_notes=1 ") \
        or " a_notes=1 " in eval_row, (
        f"S1: resolver-eval row MUST carry a_notes=1 (single "
        f"evidence row in window) — got: {eval_row}"
    )
    assert " a_kwargs_seen=True" in eval_row, (
        f"S1: resolver-eval row MUST carry a_kwargs_seen=True "
        f"(the dual-surface detector recognized structured kwargs, "
        f"not just the body-prefix fallback) — got: {eval_row}"
    )

    # ── Deny+nudge contract ────────────────────────────────────────
    assert "messages" in result, (
        "S1: deny-band path MUST inject a nudge message on the result"
    )
    assert result["attestation_route"] == "agent", (
        "S1: deny-band path MUST re-route to the agent "
        "(existing deny pattern)"
    )
    ledger.increment.assert_called_once()
    assert ledger.increment.call_args.args[0] == LCAN_INSTANCE_ID, (
        "S1: counter increment MUST target the right instance_id"
    )
    nudge = result["messages"][0]
    assert nudge.additional_kwargs.get("attestation_nudge") is True, (
        "S1: injected message MUST carry the attestation_nudge=True kwarg"
    )

    # ── Attestation → allow on the second turn (post-resolve) ───────
    reset_attestation_resolver_for_tests()
    reset_llm_judge_resolver_for_tests()
    # The patched wrapper persists across calls; the captured list
    # itself is the SAME list — the attest-turn snapshot will appear at
    # ``snapshot_capture[1]`` after this second invocation.
    attested_state = {
        "messages": [
            state["messages"][0],       # root HumanMessage
            state["messages"][1],       # legacy note
            state["messages"][2],       # delegation AI
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "attest_completion",
                        "args": {},
                        "id": "a1",
                    }
                ],
            ),
            AIMessage(content="Resolving after review. Done."),
        ]
    }
    with caplog.at_level(
        logging.INFO, logger="daemon.services.attestation_gate"
    ):
        second = asyncio.run(
            node(
                attested_state,
                config={"configurable": {"thread_id": LCAN_INSTANCE_ID}},
            )
        )

    assert "messages" not in second, (
        "S1: attested second turn MUST plain-allow with NO message "
        "injection (R4 short-circuit fires BEFORE the A/B providers "
        "run — the legacy note path is not re-walked)"
    )
    assert second.get("attestation_route") is None, (
        "S1: attested second turn MUST END with no re-route"
    )
    ledger.reset.assert_called(), (
        "S1: attested allow MUST call ledger.reset() (gate's success "
        "path keeps the post-upgrade contract)"
    )


# ─────────────────────────────────────────────────────────────────────────────
# S2 — NO RE-MINT (legacy note is read, never refreshed)
# ─────────────────────────────────────────────────────────────────────────────


def test_s2_legacy_note_is_never_re_minted(monkeypatch, caplog, snapshot_capture):
    """S2: after legacy-note-triggered evaluation, no new note is minted.

    Builds the same shape as S1 but inspects the post-evaluation state
    for the CANONICAL ``context_kind=child_report_check`` surface. The
    test MUST see exactly one such row in the fixture (minted by no
    one — it was always there) and zero newly minted by the gate. Pins:

    * The deny result's nudge message is NOT a context-kind note
      (the nudge is an ``attestation_nudge`` block — different kind).
    * The aggregator that walks the leader's message history sees no
      new ``CONTEXT_KIND_CHILD_REPORT_CHECK`` row past the fixture one.
    * The node's return messages list carries ZERO ``HumanMessage``
      with ``context_kind == "child_report_check"``.

    Defensiveness against the silent producer re-introduction: if
    anyone re-adds a ``_make_context_message`` mint call inside the
    gate node (or anywhere downstream), this assertion's count check
    fires.
    """
    counter = _JudgeCallCounter(verdict="not_complete")
    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", counter)

    legacy_note = _legacy_child_report_check_note()
    state = _state_with_legacy_note(
        final_text="Aggregating. Ending turn.",
        note=legacy_note,
    )

    node, _manager, _ledger = _make_node(instance_id=LCAN_INSTANCE_ID)
    with caplog.at_level(
        logging.INFO, logger="daemon.services.attestation_gate"
    ), caplog.at_level(
        logging.INFO,
        logger="daemon.services.attestation_resolver_activation",
    ):
        result = asyncio.run(
            node(
                state,
                config={"configurable": {"thread_id": LCAN_INSTANCE_ID}},
            )
        )

    # ── No new child_report_check rows minted by the gate ──────────
    appended_messages = list(result.get("messages") or [])
    check_rows = [
        m
        for m in appended_messages
        if getattr(m, "additional_kwargs", {}).get("context_kind")
        == CONTEXT_KIND_CHILD_REPORT_CHECK
    ]
    assert check_rows == [], (
        f"S2: gate MUST NOT mint any new "
        f"context_kind=child_report_check messages (the producer is "
        f"deleted); found {len(check_rows)} newly-minted row(s) — "
        f"this is a silent regression on the note-removal contract. "
        f"Messages: {[getattr(m, 'content', '<no-content>')[:80] for m in check_rows]}"
    )

    # ── The injection is the attestation_nudge, not the note ────────
    assert appended_messages, (
        "S2: deny path MUST inject at least one message "
        "(the attestation_nudge)"
    )
    for m in appended_messages:
        kind = getattr(m, "additional_kwargs", {}).get("context_kind")
        assert kind != CONTEXT_KIND_CHILD_REPORT_CHECK, (
            f"S2: any injected message MUST NOT carry "
            f"context_kind=child_report_check (only the fixture should) "
            f"— got kind={kind!r}"
        )

    # ── Stability: the nudge block IS distinct from the note ────────
    nudge = appended_messages[0]
    assert nudge.additional_kwargs.get("attestation_nudge") is True, (
        "S2: the deny-band injection MUST be the attestation_nudge "
        "(not a re-mint of the legacy note)"
    )
    # The nudge's body must NOT include the legacy note's title (no
    # resampling of the producer body):
    assert "Child Report Check" not in nudge.content, (
        "S2: the attestation_nudge block MUST NOT echo the legacy "
        "note's title 'Child Report Check' (no resampling of the "
        "producer body)"
    )

    # ── Single legacy read on the snapshot ──────────────────────────
    assert len(snapshot_capture) == 1, (
        f"S2: exactly one snapshot expected; got {len(snapshot_capture)}"
    )
    snap = snapshot_capture[0]
    a_signals = snap.result.a_signals
    assert a_signals.advisory_present is True, (
        f"S2: SourceASignals.advisory_present MUST fire (legacy note "
        f"in window) — got: {a_signals!r}"
    )
    assert len(a_signals.evidence) == 1, (
        f"S2: exactly one evidence row expected (single legacy read, "
        f"no re-mint) — got {len(a_signals.evidence)}: "
        f"{_evidence_summary(snap)}"
    )
    assert a_signals.evidence[0].stable_id == LCAN_LEGACY_STABLE_ID, (
        f"S2: the sole evidence row MUST be the legacy note — got: "
        f"stable_id={a_signals.evidence[0].stable_id!r}"
    )
    assert a_signals.evidence[0].kwargs_surface_seen is True, (
        "S2: the legacy note's kwargs surface MUST be recognized "
        "(dual-surface detector honored the structured kwargs)"
    )

    # ── Resolver-eval log row fingerprint ────────────────────────────
    eval_rows = _resolver_eval_rows(caplog)
    assert eval_rows, "S2: a resolver-eval row MUST be emitted"
    eval_row = eval_rows[-1]
    assert " a_notes=1 " in eval_row, (
        f"S2: resolver-eval row MUST carry a_notes=1 (single-fire) — "
        f"got: {eval_row}"
    )
    assert " a_kwargs_seen=True" in eval_row, (
        f"S2: resolver-eval row MUST carry a_kwargs_seen=True "
        f"(legacy kwargs surface detected) — got: {eval_row}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# S3 — MIXED (legacy note + fresh post-removal child report in same window)
# ─────────────────────────────────────────────────────────────────────────────


def test_s3_legacy_plus_fresh_activates_with_single_trigger(
    monkeypatch, caplog, snapshot_capture
):
    """S3: legacy note + fresh internal_report stamp → single activation.

    Pins the dual-source contract at HEAD:

    * Pass-1 walks the legacy note path; Pass-2 walks the post-removal
      ``internal_report:<child>`` stamp path. The cap
      ``A_EVIDENCE_NOTES_CAP=5`` is wide enough for both.
    * Judge fires EXACTLY ONCE: the ``_JudgeCallCounter`` wrapper
      records a single invocation (the gate does not re-invoke after
      the first plan; A-band alone ⇒ would_deny_nudge ⇒ single nudge
      ⇒ no second evaluation).
    * No DOUBLE-NUDGE: the deny result carries exactly one nudge row.

    The MIXED shape is the realistic post-upgrade scenario: a leader
    whose checkpoint predates 6a695b8f AND receives a new child report
    after the upgrade. The gate must honor BOTH surfaces without
    panic / without over-counting.
    """
    counter = _JudgeCallCounter(verdict="not_complete")
    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", counter)

    legacy_note = _legacy_child_report_check_note()
    fresh_report = _fresh_internal_report_message(
        content=(
            "Awaiting the next report. Ending turn, will write the "
            "final report next."
        ),
        child_id=LCAN_FRESH_CHILD_ID,
    )
    state = _state_with_legacy_note(
        final_text="Aggregating. Ending turn.",
        note=legacy_note,
        extra_messages=[fresh_report],
    )

    node, _manager, ledger = _make_node(instance_id=LCAN_INSTANCE_ID)
    with caplog.at_level(
        logging.INFO, logger="daemon.services.attestation_gate"
    ), caplog.at_level(
        logging.INFO,
        logger="daemon.services.attestation_resolver_activation",
    ):
        result = asyncio.run(
            node(
                state,
                config={"configurable": {"thread_id": LCAN_INSTANCE_ID}},
            )
        )

    # ── Snapshot: dual-source activation ────────────────────────────
    assert len(snapshot_capture) == 1, (
        f"S3: exactly one snapshot expected; got {len(snapshot_capture)}"
    )
    snap = snapshot_capture[0]
    a_signals = snap.result.a_signals
    assert a_signals.advisory_present is True, (
        f"S3: dual-source A-band MUST fire (advisory_present=True) "
        f"when ANY catalog hit is seen — got: {a_signals!r}"
    )
    assert len(a_signals.evidence) == 2, (
        f"S3: dual-source activation MUST produce TWO evidence rows "
        f"(one legacy + one fresh internal_report) — got "
        f"{len(a_signals.evidence)}: {_evidence_summary(snap)}"
    )

    # The legacy evidence row is identified by the stable-id mint
    # format ``child_report_check:<parent>:<child>``:
    legacy_rows = [
        ev for ev in a_signals.evidence
        if ev.stable_id == LCAN_LEGACY_STABLE_ID
    ]
    fresh_rows = [
        ev for ev in a_signals.evidence if ev.stable_id not in (
            None, LCAN_LEGACY_STABLE_ID,
        )
    ]
    assert len(legacy_rows) == 1, (
        f"S3: exactly one legacy-note evidence row expected — got "
        f"{len(legacy_rows)}: {_evidence_summary(snap)}"
    )
    assert len(fresh_rows) == 1, (
        f"S3: exactly one fresh-internal_report evidence row expected "
        f"— got {len(fresh_rows)}: {_evidence_summary(snap)}"
    )

    legacy_ev = legacy_rows[0]
    fresh_ev = fresh_rows[0]

    # Legacy path's kwargs surface MUST have been honored (defensive pin
    # against a regression that drops the kwargs branch on the legacy
    # path in favor of the prefix fallback only).
    assert legacy_ev.kwargs_surface_seen is True, (
        f"S3: legacy-row kwargs_surface_seen MUST be True (dual-"
        f"surface detector honored the structured kwargs) — got: "
        f"{legacy_ev.kwargs_surface_seen!r}"
    )
    assert legacy_ev.child_instance_id == LCAN_CHILD_ID, (
        f"S3: legacy row's child_instance_id MUST surface — got: "
        f"{legacy_ev.child_instance_id!r}"
    )
    # Fresh path's kwargs surface comes from the ``source=internal_report:``
    # stamp (the dual-surface mirror — see
    # ``_build_evidence_from_report_message``).
    assert fresh_ev.kwargs_surface_seen is True, (
        f"S3: fresh-row kwargs_surface_seen MUST be True (the "
        f"``source=internal_report:<child>`` stamp is the structured "
        f"kwargs surface for the live path) — got: "
        f"{fresh_ev.kwargs_surface_seen!r}"
    )
    assert fresh_ev.child_instance_id == LCAN_FRESH_CHILD_ID, (
        f"S3: fresh row's child_instance_id MUST surface from the "
        f"``internal_report:<child>`` regex — got: "
        f"{fresh_ev.child_instance_id!r}"
    )

    # ── Activation predicate terms + band ───────────────────────────
    assert "a_suspicion" in snap.result.terms_fired, (
        f"S3: A-band term MUST fire on dual-source shape — got "
        f"terms_fired={snap.result.terms_fired!r}"
    )
    assert snap.result.band in ("deny", "a_suspicion"), (
        f"S3: band MUST be deny or a_suspicion on this shape — got: "
        f"band={snap.result.band!r}, terms_fired="
        f"{snap.result.terms_fired!r}"
    )
    assert snap.would_be_outcome == "would_deny_nudge", (
        f"S3: would_be_outcome MUST be would_deny_nudge — got: "
        f"{snap.would_be_outcome!r}"
    )

    # ── Judge fires exactly once ────────────────────────────────────
    assert counter.calls == 1, (
        f"S3: judge wrapper MUST fire exactly once on a SINGLE pass "
        f"(A-band + marker-band on a delegated + quiet tree ⇒ single "
        f"plan); got {counter.calls} call(s)"
    )

    # ── Resolver-eval log row: dual-source fingerprint ──────────────
    eval_rows = _resolver_eval_rows(caplog)
    assert eval_rows, "S3: resolver-eval row MUST be emitted"
    eval_row = eval_rows[-1]
    assert " a_notes=2 " in eval_row, (
        f"S3: resolver-eval row MUST carry a_notes=2 (dual-source "
        f"evidence) — got: {eval_row}"
    )
    assert " a_kwargs_seen=True" in eval_row, (
        f"S3: resolver-eval row MUST carry a_kwargs_seen=True "
        f"(any row sees the kwargs surface) — got: {eval_row}"
    )

    # ── Single nudge (no double-injection) ─────────────────────────
    appended_messages = list(result.get("messages") or [])
    nudge_rows = [
        m
        for m in appended_messages
        if getattr(m, "additional_kwargs", {}).get("attestation_nudge")
    ]
    assert len(nudge_rows) == 1, (
        f"S3: deny path MUST inject exactly one nudge row (no "
        f"double-trigger); got {len(nudge_rows)} nudge row(s)"
    )

    # ── Counter incremented exactly once ───────────────────────────
    ledger.increment.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# Drift pin — verifier-side forensics surface
# ─────────────────────────────────────────────────────────────────────────────


def _git_head_short() -> str:
    import subprocess

    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
        ).decode().strip()
    except Exception:  # noqa: BLE001 — drift-pin is best-effort
        return "<unknown>"


@pytest.mark.parametrize("_scenario", ["s1", "s2", "s3"])
def test_drift_pin_surfaces_branch_head_for_forensics(_scenario):
    """Drift-pin: verify ``git rev-parse`` is callable from the worktree.

    Mirrors ``test_attestation_marker_routing_lca.py`` — soft-pin only
    (no fail on sibling commits); the verifier reads the head from the
    pytest header log line to confirm the run-time worktree matches
    the gate context.
    """
    head = _git_head_short()
    assert head != "<unknown>", (
        "drift-pin: ``git rev-parse --short HEAD`` MUST be callable "
        "from the worktree (this CI sandbox must have git available)"
    )
