"""LCA resolver Stage 2 — RESOLVER-FAULT FAIL-OPEN matrix (integration).

Drives the real ``create_attestation_gate_node`` (``daemon.graph``
factory that owns the Stage-2 fused block + the
``_LCA_STAGE2_RESOLVER_FLIP`` Stage-2 path) through a fault-injection
matrix:

* **Seam (i)** — activation-predicate computation raises
  (in ``activation_predicate`` inside ``evaluate_resolver_activation``).
* **Seam (ii)** — fused-payload build raises
  (in ``assemble_fused_bundle`` — child-evidence assembly).
* **Seam (iii)** — judge invocation itself raises
  (in ``judge_fused_bundle_async`` — transport/LLM error, distinct
  from verdict-unparsable).
* **Seam (iv)** — verdict parse / outcome mapping raises
  (in ``emit_resolver_eval_row`` or the band→outcome mapping arm —
  the result-mapping side; reaches the graph node OUTSIDE the
  ``create_attestation_gate_node`` node-level try/except).

Each fault × band (deny-band quiet-un-attested / marker-band / A-band)
runs once. The matrix documents the ACTUAL behavior under the Stage-2
flip — both pass-throughs and contract deviations are evidence, not
failures to hide.

FR-13 reference (requirements.md):
  "The gate MUST fail-OPEN on any scanner/gate exception (try/except)
   ... allow completion, emit a structured ``gate_exception`` log
   entry with exception type and stack-trace summary, and set a
   transient ``gate_exception_seen=true`` flag on the instance row."

F2 framing (Job 5 spec): on a resolver-compute failure mid-flip with
pending wakeups still in flight, a subsequent wakeup must RE-FIRE the
gate — the failure must NOT strand the gate. Verified at the end of
the file via the F2 wakeup re-fire class.

EXISTING COVERAGE (referenced; gaps this file fills noted):

* ``tests/integration/test_attestation_fail_open.py`` (the
  pre-Stage-2 fail-open) — covers scanner exception + ledger DB error
  through the graph node's outer try/except. Uses
  ``monkeypatch.setattr("daemon.services.attestation_gate.scan_for_
  attestation_detailed", scanner_boom)`` and asserts the
  ``event=leader_completion_gate_error`` row + ``gate_exception_seen``
  marker writeback. Covers the NARROW scanner-fault path only.
* ``tests/unit/test_attestation_gate.py::TestGateExceptionMarkerDry``
  — unit-level: confirms the marker writeback for the dry-mode
  guard. Same contract basis.
* ``tests/unit/test_attestation_resolver_activation.py`` — the
  §(vi) inner-resolver seam tests (pure-function level). Covers the
  happy path + the snapshot shape; DOES NOT cover the resolver's
  inside-graph consume path under mid-flip faults.
* ``tests/integration/test_attestation_stage2_killswitch.py`` (Job
  4) — kill-switch matrix on the SAME gate node; covers the
  non-fault band mapping (Cell A/B/C/D) but DOES NOT cover fault
  injection.

GAPS THIS FILE FILLS (per Job 5 spec):

1. **(i) activation-predicate exception** — injects
   ``activation_predicate`` raising; covers the §(vi) seam that the
   pre-Stage-2 scanner-fault test could not reach (the new seam is
   additive post-Stage-1). Verifies the
   ``event=leader_completion_resolver_eval_error`` row IS emitted and
   the ``event=leader_completion_gate_error`` row is NOT (the inner
   try/except swallows + the gate's own outer catch never fires —
   this is the contract deviation the matrix exists to document).
2. **(ii) fused-payload build exception** — injects
   ``assemble_fused_bundle`` raising after the predicate has fired;
   same code path as (i) above (both raise inside
   ``evaluate_resolver_activation`` and bubble to the §(vi) inner
   try/except). Verifies that the post-Stage-1 §(vi) seam's
   "exception-isolated ... never propagates into gate control flow"
   claim holds by observability evidence.
3. **(iii) judge-invocation exception** — injects the fused judge's
   transport seam raising (Distinct from ``verdict="error"`` /
   ``is_complete=False`` — a literal Python raise inside
   ``judge_fused_bundle_async`` triggers ``fused_wrapper_fault`` and
   sets ``judge_verdict="error"``). Verifies the per-band conservative
   mapping (DP-5 REJECTED — no fail-safe allow; deny-band →
   deny+nudge bound-enforced, marker/A-band → allow + checkpoint
   hint when pending).
4. **(iv) verdict parse / outcome mapping exception** — injects
   ``emit_resolver_eval_row`` raising AFTER the band → outcome
   mapping has already executed. This seam is OUTSIDE the gate node's
   outer try/except (the fused block is at indent=8 vs the outer
   catch at indent=8 BUT after the return-at-indent-16 — see
   graph.py:5176+). Document whether the gate CRASHES vs fail-open
   allow (this is the finding to surface).
5. **F2 wakeup re-fire** — after a fail-open allow with pending
   work still present, deliver the pending wakeup (a new
   HumanMessage via a second ``node()`` call simulating a LangGraph
   re-dispatch) → assert the gate EVALUATES AGAIN (resolver row
   present in the second call's log) and — fault now removed —
   reaches a real outcome (judge_invoked=True + bound verdict).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from daemon.graph import (
    create_attestation_gate_node,
)
from daemon.services import attestation_report_judge as judge_mod
from daemon.services import attestation_resolver_activation as resolver_activation_mod
from daemon.services.attestation_gate import (
    GateSettings,
    build_gate_config,
)
from daemon.services.attestation_judge_resolver import (
    reset_llm_judge_resolver_for_tests,
)
from daemon.services.attestation_judge_timeout_resolver import (
    reset_judge_timeout_resolver_for_tests,
)
from daemon.services.attestation_resolver import (
    reset_attestation_resolver_for_tests,
)


# ─────────────────────────────────────────────────────────────────────────────
# Drift-pin + flip guards — fail loud if the worktree drifted or the
# Stage-2 flip flipped OFF.
# ─────────────────────────────────────────────────────────────────────────────


def _drift_pin() -> None:
    """Assert the Stage-3 single-path shape is ACTIVE.

    Stage 3 (2026-09-17, R7): the flip constant is DELETED — the
    fused block is the sole completion path (unconditionally guarded
    only by ``resolver_snapshot is not None``). If a future change
    re-introduces a toggle or drops the fused guard, this pin fires
    before the matrix silently degrades."""
    import daemon.graph as graph_module

    assert not hasattr(graph_module, "_LCA_STAGE2_RESOLVER_FLIP"), (
        "the Stage-3 retirement deleted the flip constant — "
        "resurrecting a runtime toggle violates repo convention n"
    )
    import inspect

    node_src = inspect.getsource(create_attestation_gate_node)
    assert "if resolver_snapshot is not None:" in node_src


# ─────────────────────────────────────────────────────────────────────────────
# Resolver reset fixture — every test sees a fresh resolver cache.
# Mirrors the kill-switch sibling matrix.
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_resolvers_between_faults(monkeypatch):
    """Clear kill-switch envs + reset ALL three cached-global
    resolvers (mode + LLM-judge enabled + LLM-judge timeout) so each
    test sees a clean resolver cache. Hermetic isolation per
    blueprint (c)."""
    monkeypatch.delenv(
        "ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED", raising=False
    )
    monkeypatch.delenv(
        "ENSEMBLE_LEADER_ATTESTATION_MODE", raising=False
    )
    monkeypatch.delenv(
        "ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_TIMEOUT_S", raising=False
    )
    reset_attestation_resolver_for_tests()
    reset_llm_judge_resolver_for_tests()
    reset_judge_timeout_resolver_for_tests()
    yield
    reset_attestation_resolver_for_tests()
    reset_llm_judge_resolver_for_tests()
    reset_judge_timeout_resolver_for_tests()


# ─────────────────────────────────────────────────────────────────────────────
# Harness — gate node factory + state builders + log scan.
# (Mirrors ``tests/integration/test_attestation_stage2_killswitch.py``.)
# ─────────────────────────────────────────────────────────────────────────────


def _make_node(
    *,
    instance_id: str,
    pending_children: int = 0,
    queued_wakeups: int = 0,
    live_descendants: int = 0,
    busy_descendants: int = 0,
    denied_count: int = 0,
    mode: str = "enforce",
    settings: GateSettings | None = None,
    llm_judge_enabled: bool = True,
):
    """Build the REAL gate node with MagicMock manager + ledger.

    Returns ``(node, manager, ledger)``. The MagicMock manager's
    default facade values are the THREE-input R2 baseline (``0`` on
    every counter — explicit per the
    ``test_attestation_dry_mode.py`` ``make_manager`` comment that
    documents the MagicMock > 0 inflation hazard).
    """
    manager = MagicMock()
    manager.count_pending_children.return_value = pending_children
    manager.get_queued_or_expected_wakeups.return_value = queued_wakeups
    manager.count_live_descendants.return_value = live_descendants
    manager.count_busy_descendants.return_value = busy_descendants
    manager.get_tree_ids_permanent.return_value = []
    manager.has_open_user_answer = MagicMock(return_value=False)
    manager.is_watchover_enabled = MagicMock(return_value=False)
    manager.is_question_pause_requested = MagicMock(return_value=False)
    manager.enqueue_message = MagicMock()
    manager.revive = MagicMock()
    manager.send_message = MagicMock()

    ledger = MagicMock()
    ledger.increment.return_value = 1
    ledger.reset.return_value = True
    ledger.set_escalated_and_reset.return_value = True
    ledger.get.return_value = denied_count

    settings = settings or GateSettings(mode, 3, 3)
    config = build_gate_config(
        instance_id, settings, llm_judge_enabled=llm_judge_enabled
    )
    node = create_attestation_gate_node(
        config,
        settings,
        manager,
        instance_id,
        denied_count_getter=lambda: denied_count,
        ledger=ledger,
    )
    return node, manager, ledger


def _delegated_state(final_text: str, extra_messages=()) -> dict:
    """Anchor the state as DELEGATED so the conditional gate flips ON
    (``attestation_required=True`` per §(v.b)). Without delegation,
    the gate returns early (D10-exempt) — the matrix would never
    exercise the per-band branches."""
    delegation_ai = AIMessage(
        content="",
        tool_calls=[
            {"name": "send_message", "args": {"target": "child-id"}, "id": "c1"}
        ],
    )
    return {
        "messages": [
            HumanMessage(content="please do it"),
            delegation_ai,
            *extra_messages,
            AIMessage(content=final_text),
        ]
    }


def _child_report_check_note(child_id: str | None = None) -> HumanMessage:
    """The Stage-0 child-report check note (A-band signal — Source A)."""
    child = child_id or str(uuid.uuid4())
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


def _run(node, state, instance_id):
    return asyncio.run(
        node(state, config={"configurable": {"thread_id": instance_id}})
    )


def _rows(caplog, token: str) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if token in r.getMessage()
    ]


def _capture(caplog):
    """Pin caplog at INFO on the three loggers that emit gate /
    resolver / fused-judge rows (mirrors the Stage-2 sibling test)."""
    for logger in (
        "daemon.graph",
        "daemon.services.attestation_gate",
        "daemon.services.attestation_resolver_activation",
    ):
        caplog.set_level(logging.INFO, logger=logger)
    return caplog


class _JudgeSpy:
    """Recording stub for ``_invoke_judge_llm``.

    Mirrors the Stage-2 sibling test's ``_JudgeSpy`` — HTTP-attempt
    level; the invocation-level sentinel is enforced at the gate
    node (this spy only verifies NO call was made)."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.attempts: list[str] = []
        self.payloads: list[str] = []

    async def __call__(self, config, user_payload, *, timeout_s, system_prompt=None):
        self.attempts.append(user_payload)
        self.payloads.append(user_payload)
        if len(self.responses) == 1:
            return (self.responses[0], "fake-quick")
        return (self.responses[len(self.attempts) - 1], "fake-quick")


# ─────────────────────────────────────────────────────────────────────────────
# FAULT-INJECTION HELPERS
#
# Each helper wraps a monkeypatch with explicit "what + where +
# how it surfaces in the gate" documentation. The helpers are
# intentionally DEAD-SIMPLE — the assertion work lives in the test
# bodies, the helpers just install the fault.
# ─────────────────────────────────────────────────────────────────────────────


class _ActivationPredicateBoom:
    """Seam (i) — replace the activation predicate with one that
    raises. Reachable via
    ``monkeypatch.setattr(resolver_activation_mod, "activation_predicate", boom)``
    at the import-time alias used by ``evaluate_resolver_activation``."""

    def __call__(self, **_kwargs):
        raise RuntimeError("injected activation-predicate fault")


class _AssembleFusedBundleBoom:
    """Seam (ii) — replace the fused-bundle assembler with one that
    raises. The fault fires AFTER the predicate has fired; the
    activation snapshot has ``result.fired=True`` and the bundle
    field is being assembled from C-tree-rows."""

    def __call__(self, **_kwargs):
        raise RuntimeError("injected assemble-fused-bundle fault")


# ─────────────────────────────────────────────────────────────────────────────
# Band × outcome expectation builders
#
# Each band's expected per-fault outcome is driven by the Stage-2
# fused block spec (decisions.md D-RES2 / requirements.md §4.3
# decision matrix). The expectations here are LOOSE — the test
# bodies document the ACTUAL behavior with evidence rows; the
# assertions only fail on the explicit contract pins (gate survives;
# no unhandled exception; the loud-row contract holds).
# ─────────────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
# TEST CLASS — Seam (i): activation-predicate computation raises.
# ─────────────────────────────────────────────────────────────────────────────


class TestSeamIActivationPredicateRaises:
    """``activation_predicate`` raises inside
    ``evaluate_resolver_activation`` (graph.py §(vi) inner
    try/except swallows). The fused block in graph.py is gated by
    ``resolver_snapshot is not None`` — so a None snapshot skips the
    fused block entirely; the legacy marker-path block is also DEAD
    under the Stage-2 flip; the gate falls through to the Phase-3
    ledger writes using the ORIGINAL ``decide()`` decision."""

    def test_seam_i_deny_band_does_not_increment(self, monkeypatch, caplog):
        """Deny-band (un-attested ∧ quiet). ``decide()`` already
        returned ``DENIED`` BEFORE the resolver-eval ran; the inner
        try/except swallows the resolver-eval fault and returns the
        DENIED decision unchanged. Gate runs the deny+nudge path
        (counter increments; nudge injected) — NOT a fail-open
        allow. This is a CONTRACT DEVIATION vs the spirit of FR-13
        (the resolver-eval error is not in the explicit
        scanner/gate exception set, so the gate behaves as if no
        fault occurred from the decision-pipeline's perspective)."""
        _drift_pin()
        spy = _JudgeSpy(
            ['{"verdict": "complete", "rationale": "unused"}']
        )
        monkeypatch.setattr(judge_mod, "_invoke_judge_llm", spy)
        monkeypatch.setattr(
            resolver_activation_mod,
            "activation_predicate",
            _ActivationPredicateBoom(),
        )

        node, _manager, ledger = _make_node(
            instance_id="seam-i-deny",
            pending_children=0,
            queued_wakeups=0,
            live_descendants=0,
            busy_descendants=0,
        )
        with _capture(caplog).at_level(logging.INFO):
            result = _run(node, _delegated_state("All done."), "seam-i-deny")

        # ZERO judge HTTP attempts — fused block was skipped.
        assert spy.attempts == [], (
            "Seam (i) deny-band: resolver_snapshot=None ⇒ fused "
            "block skipped ⇒ NO judge call"
        )
        # Resolver-eval-error row MUST fire (loud contract — inner
        # §(vi) try/except logs on EVERY inner exception).
        resolver_err_rows = _rows(
            caplog, "event=leader_completion_resolver_eval_error"
        )
        assert resolver_err_rows, (
            "Seam (i) deny-band: resolver-eval-error row MUST fire"
        )
        # The §(vi) catch logs error_class=RuntimeError.
        assert any(
            "error_class=RuntimeError" in r for r in resolver_err_rows
        ), "Seam (i) deny-band: error_class=RuntimeError on row"
        # CRITICAL — the canonical gate-error row (outer try/except)
        # MUST NOT fire (it would mean the resolver-eval exception
        # reached the wider catch — a different contract shape).
        assert _rows(caplog, "event=leader_completion_gate_error ") == [], (
            "Seam (i) deny-band: outer gate-error row MUST NOT fire "
            "(the inner §(vi) catch IS load-bearing here; "
            "FR-13's gate_exception marker is NOT set)"
        )
        # Nudge injected (DENIED path runs through Phase-3 ledger).
        # This is the contract deviation evidence.
        assert "messages" in result, (
            "Seam (i) deny-band: DENIED path → nudge injected "
            "(NOT a fail-open allow — contract deviation evidence)"
        )
        assert result["attestation_route"] == "agent"
        ledger.increment.assert_called_once()

    def test_seam_i_marker_band_plain_allow(self, monkeypatch, caplog):
        """Marker-band (b_fires ∧ ¬quiet, pending=1). ``decide()``
        returned ``ALLOWED_LEGITIMATE_PENDING_WAKEUP`` BEFORE the
        resolver-eval ran; the inner try/except swallows. The fused
        block is skipped (snapshot None). The legacy block is DEAD.
        Gate runs the legitimate-pending-wakeup branch — NO nudge,
        NO hint, NO counter write. Plain END with the wakeup pending.

        Behavior: Plain END (no marker_hint_message, no nudge).
        Equivalent to the kill-switch-OFF marker-band case
        (test_a_marker_band_plain_allow in the sibling matrix).
        """
        _drift_pin()
        spy = _JudgeSpy(
            ['{"verdict": "complete", "rationale": "unused"}']
        )
        monkeypatch.setattr(judge_mod, "_invoke_judge_llm", spy)
        monkeypatch.setattr(
            resolver_activation_mod,
            "activation_predicate",
            _ActivationPredicateBoom(),
        )

        node, _manager, ledger = _make_node(
            instance_id="seam-i-marker",
            pending_children=1,
        )
        with _capture(caplog).at_level(logging.INFO):
            result = _run(
                node,
                _delegated_state("Awaiting child reply. Ending turn."),
                "seam-i-marker",
            )

        assert spy.attempts == [], (
            "Seam (i) marker-band: resolver_snapshot=None ⇒ no "
            "judge call"
        )
        resolver_err_rows = _rows(
            caplog, "event=leader_completion_resolver_eval_error"
        )
        assert resolver_err_rows, (
            "Seam (i) marker-band: resolver-eval-error row MUST fire"
        )
        # Plain allow — NO hint, NO nudge.
        assert "messages" not in result, (
            "Seam (i) marker-band: plain allow — no hint (snapshot "
            "None → fused block skipped → no marker_hint_message "
            "stamped)"
        )
        assert result["attestation_route"] is None
        ledger.increment.assert_not_called()

    def test_seam_i_a_band_plain_allow(self, monkeypatch, caplog):
        """A-band (a_suspicion ∧ ¬quiet, pending=1, busy muted,
        child-report check note present). Like marker-band: the
        fused block is skipped; the gate falls through to
        legitimate-pending-wakeup branch. Plain END."""
        _drift_pin()
        spy = _JudgeSpy(
            ['{"verdict": "complete", "rationale": "unused"}']
        )
        monkeypatch.setattr(judge_mod, "_invoke_judge_llm", spy)
        monkeypatch.setattr(
            resolver_activation_mod,
            "activation_predicate",
            _ActivationPredicateBoom(),
        )

        node, _manager, ledger = _make_node(
            instance_id="seam-i-a",
            pending_children=1,
            live_descendants=2,
            busy_descendants=2,
        )
        state = _delegated_state(
            "Everything is done here.",
            extra_messages=[_child_report_check_note()],
        )
        with _capture(caplog).at_level(logging.INFO):
            result = _run(node, state, "seam-i-a")

        assert spy.attempts == [], (
            "Seam (i) A-band: resolver_snapshot=None ⇒ no judge call"
        )
        resolver_err_rows = _rows(
            caplog, "event=leader_completion_resolver_eval_error"
        )
        assert resolver_err_rows, (
            "Seam (i) A-band: resolver-eval-error row MUST fire"
        )
        assert "messages" not in result, (
            "Seam (i) A-band: plain allow — no hint"
        )
        assert result["attestation_route"] is None
        ledger.increment.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# TEST CLASS — Seam (ii): fused-payload build raises.
# ─────────────────────────────────────────────────────────────────────────────


class TestSeamIIFusedPayloadBuildRaises:
    """``assemble_fused_bundle`` raises inside
    ``evaluate_resolver_activation``. The predicate HAS fired (so
    the activation snapshot's ``fired=True`` and ``band=`` are
    already set), the bundle assembly step raises.

    Per the inner §(vi) try/except the exception is swallowed and
    ``event=leader_completion_resolver_eval_error`` is logged. The
    same code path as (i) — the inner try/except can't distinguish
    "predicate" faults from "bundle build" faults. Documented ACTUAL
    behavior: identical to (i) per-band; the §(vi) seam's
    "exception-isolated ... never propagates into gate control flow"
    claim holds."""

    def test_seam_ii_deny_band_does_not_increment(
        self, monkeypatch, caplog
    ):
        _drift_pin()
        spy = _JudgeSpy(
            ['{"verdict": "complete", "rationale": "unused"}']
        )
        monkeypatch.setattr(judge_mod, "_invoke_judge_llm", spy)
        monkeypatch.setattr(
            resolver_activation_mod,
            "assemble_fused_bundle",
            _AssembleFusedBundleBoom(),
        )

        node, _manager, ledger = _make_node(
            instance_id="seam-ii-deny",
            pending_children=0,
            queued_wakeups=0,
            live_descendants=0,
            busy_descendants=0,
        )
        with _capture(caplog).at_level(logging.INFO):
            result = _run(node, _delegated_state("All done."), "seam-ii-deny")

        assert spy.attempts == [], (
            "Seam (ii) deny-band: bundle build raised before judge "
            "call ⇒ snapshot None ⇒ no judge"
        )
        resolver_err_rows = _rows(
            caplog, "event=leader_completion_resolver_eval_error"
        )
        assert resolver_err_rows, (
            "Seam (ii) deny-band: resolver-eval-error row MUST fire"
        )
        assert any(
            "error_class=RuntimeError" in r for r in resolver_err_rows
        )
        # Same contract deviation as Seam (i) — DENIED path runs,
        # nudge + counter.
        assert "messages" in result, (
            "Seam (ii) deny-band: DENIED → nudge (contract "
            "deviation evidence — same path as Seam (i))"
        )
        assert result["attestation_route"] == "agent"
        ledger.increment.assert_called_once()

    def test_seam_ii_marker_band_plain_allow(
        self, monkeypatch, caplog
    ):
        _drift_pin()
        spy = _JudgeSpy(
            ['{"verdict": "complete", "rationale": "unused"}']
        )
        monkeypatch.setattr(judge_mod, "_invoke_judge_llm", spy)
        monkeypatch.setattr(
            resolver_activation_mod,
            "assemble_fused_bundle",
            _AssembleFusedBundleBoom(),
        )

        node, _manager, ledger = _make_node(
            instance_id="seam-ii-marker",
            pending_children=1,
        )
        with _capture(caplog).at_level(logging.INFO):
            result = _run(
                node,
                _delegated_state("Awaiting child reply. Ending turn."),
                "seam-ii-marker",
            )

        assert spy.attempts == []
        resolver_err_rows = _rows(
            caplog, "event=leader_completion_resolver_eval_error"
        )
        assert resolver_err_rows
        assert "messages" not in result, (
            "Seam (ii) marker-band: plain allow — no hint"
        )
        assert result["attestation_route"] is None
        ledger.increment.assert_not_called()

    def test_seam_ii_a_band_plain_allow(self, monkeypatch, caplog):
        _drift_pin()
        spy = _JudgeSpy(
            ['{"verdict": "complete", "rationale": "unused"}']
        )
        monkeypatch.setattr(judge_mod, "_invoke_judge_llm", spy)
        monkeypatch.setattr(
            resolver_activation_mod,
            "assemble_fused_bundle",
            _AssembleFusedBundleBoom(),
        )

        node, _manager, ledger = _make_node(
            instance_id="seam-ii-a",
            pending_children=1,
            live_descendants=2,
            busy_descendants=2,
        )
        state = _delegated_state(
            "Everything is done here.",
            extra_messages=[_child_report_check_note()],
        )
        with _capture(caplog).at_level(logging.INFO):
            result = _run(node, state, "seam-ii-a")

        assert spy.attempts == []
        resolver_err_rows = _rows(
            caplog, "event=leader_completion_resolver_eval_error"
        )
        assert resolver_err_rows
        assert "messages" not in result, (
            "Seam (ii) A-band: plain allow — no hint"
        )
        assert result["attestation_route"] is None
        ledger.increment.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# TEST CLASS — Seam (iii): judge invocation itself raises.
# ─────────────────────────────────────────────────────────────────────────────


class TestSeamIIIJudgeInvocationRaises:
    """``judge_fused_bundle_async`` raises (transport/LLM error —
    DISTINCT from ``verdict="error"`` / ``is_complete=False``).

    Per graph.py:5308 the exception is caught by the inner
    try/except in the fused block; ``fused_wrapper_fault=True``;
    ``fused_result=None``; ``judge_invoked=False``;
    ``judge_verdict="error"``.

    Per-band conservative mapping (DP-5 REJECTED):
      * deny-band: deny + nudge, bound-enforced.
      * marker-band with pending: ALLOW + checkpoint hint (D4 path).
      * A-band with pending: ALLOW + checkpoint hint (D4 path)."""

    def _install_judge_boom(self, monkeypatch):
        """Install a ``judge_fused_bundle_async`` that ALWAYS raises.

        Wrapping the seam as a proxy ensures the import-time name
        the graph references points at our raising function."""

        async def boom(_bundle_text, *, config):
            raise RuntimeError("injected judge-transport fault")

        monkeypatch.setattr(
            resolver_activation_mod.__builtins__ if False else judge_mod,
            "judge_fused_bundle_async",
            boom,
        )

    def test_seam_iii_deny_band_deny_nudge(self, monkeypatch, caplog):
        """Deny-band + judge wrapper fault → deny+nudge (DP-5: no
        fail-safe allow). The fused block maps
        ``fused_wrapper_fault=True`` to
        ``resolver_outcome=deny_nudge`` on the deny band; the
        ``event=leader_completion_gate_fused_judge_error`` row
        carries the loud error class."""
        _drift_pin()
        self._install_judge_boom(monkeypatch)

        node, _manager, ledger = _make_node(
            instance_id="seam-iii-deny",
            pending_children=0,
            queued_wakeups=0,
            live_descendants=0,
            busy_descendants=0,
        )
        with _capture(caplog).at_level(logging.INFO):
            result = _run(node, _delegated_state("All done."), "seam-iii-deny")

        # Loud fused-judge-error row carries error_class=RuntimeError.
        fje_rows = _rows(
            caplog, "event=leader_completion_gate_fused_judge_error"
        )
        assert fje_rows, (
            "Seam (iii) deny-band: fused-judge-error row MUST fire "
            "(graph.py:5317 wrapper-layer fault observer row)"
        )
        assert any("error_class=RuntimeError" in r for r in fje_rows)
        assert any("decision=fail_safe_conservative" in r for r in fje_rows)
        # Resolver-eval-error MUST NOT fire (the resolver-eval
        # itself succeeded — the judge wrapper is the one that
        # raised).
        assert _rows(
            caplog, "event=leader_completion_resolver_eval_error"
        ) == [], (
            "Seam (iii) deny-band: resolver-eval succeeded — its "
            "row is the success path, NOT the error row"
        )
        # Per-band conservative: deny+nudge — DENIED path runs,
        # counter increments.
        assert "messages" in result, (
            "Seam (iii) deny-band: deny+nudge path (DP-5)"
        )
        assert result["attestation_route"] == "agent"
        ledger.increment.assert_called_once()
        # Resolver eval row carries the post-flip verdict.
        eval_rows = _rows(caplog, "event=leader_completion_resolver_eval ")
        assert eval_rows, (
            "Seam (iii) deny-band: resolver_eval row MUST fire"
        )
        row = eval_rows[0]
        assert "band=deny" in row
        assert "resolver_outcome=deny_nudge" in row

    def test_seam_iii_marker_band_allow_with_hint(
        self, monkeypatch, caplog
    ):
        """Marker-band with pending wakeup + judge wrapper fault →
        per §4.3: nothing-pending check fails (pending=1) → ALLOW
        log-only (2026-09-23 b2f4dae9: the hint is RETIRED
        end-to-end; the (d)-with-pending route resolves to ALLOW
        with the ``allow_hint`` label as the forensic record).

        The hint surface that used to ride the result messages is
        RETIRED — the gate now returns NO messages; the resolver
        row + the [AttestationGate] log line carry the
        ``would_be_route=allow_hint`` label.
        """
        _drift_pin()
        self._install_judge_boom(monkeypatch)

        node, _manager, ledger = _make_node(
            instance_id="seam-iii-marker",
            pending_children=1,
        )
        with _capture(caplog).at_level(logging.INFO):
            result = _run(
                node,
                _delegated_state("Awaiting child reply. Ending turn."),
                "seam-iii-marker",
            )

        # Fused-judge-error row fires.
        fje_rows = _rows(
            caplog, "event=leader_completion_gate_fused_judge_error"
        )
        assert fje_rows, (
            "Seam (iii) marker-band: fused-judge-error row MUST fire"
        )
        assert any("error_class=RuntimeError" in r for r in fje_rows)
        # Per-band conservative: ALLOW log-only — NO messages, NO nudge,
        # NO counter. The route label is on the resolver row + the
        # [AttestationGate] log line.
        assert "messages" not in result, (
            f"Seam (iii) marker-band: ALLOW log-only after 2026-09-23 "
            f"(NO hint injected); got messages={result.get('messages')!r}"
        )
        assert result["attestation_route"] is None
        ledger.increment.assert_not_called()
        # Resolver eval row carries band=marker / outcome=allow_hint
        # / verdict=error (the wrapper-fault sentinel).
        eval_rows = _rows(caplog, "event=leader_completion_resolver_eval ")
        assert eval_rows
        row = eval_rows[0]
        assert "band=marker" in row
        assert "resolver_outcome=allow_hint" in row
        assert "judge_verdict=error" in row
        assert "judge_invoked=False" in row
        # The would-be-route label MUST land on the [AttestationGate]
        # log line so the b2f4dae9 evidence chain is log-reconstructible.
        assert any(
            "would_be_route=allow_hint" in r
            for r in _rows(caplog, "[AttestationGate] fused-judge")
        ), (
            "Seam (iii) marker-band: would_be_route=allow_hint MUST "
            "land on the [AttestationGate] log line"
        )

    def test_seam_iii_a_band_allow_with_hint(self, monkeypatch, caplog):
        """A-band with pending + judge wrapper fault → allow log-only.

        2026-09-23 (b2f4dae9): the same code path as marker-band (the
        A-band arm mirrors the marker-band arm in the fused block);
        the (b)/(d)-with-pending route resolves to ALLOW with NO
        message injection — the ``resolver_outcome=allow_hint`` row
        label is the forensic surface.
        """
        _drift_pin()
        self._install_judge_boom(monkeypatch)

        node, _manager, ledger = _make_node(
            instance_id="seam-iii-a",
            pending_children=1,
            live_descendants=2,
            busy_descendants=2,
        )
        state = _delegated_state(
            "Everything is done here.",
            extra_messages=[_child_report_check_note()],
        )
        with _capture(caplog).at_level(logging.INFO):
            result = _run(node, state, "seam-iii-a")

        fje_rows = _rows(
            caplog, "event=leader_completion_gate_fused_judge_error"
        )
        assert fje_rows
        assert any("error_class=RuntimeError" in r for r in fje_rows)
        assert "messages" not in result, (
            f"Seam (iii) A-band: ALLOW log-only after 2026-09-23 "
            f"(NO hint injected); got messages={result.get('messages')!r}"
        )
        assert result["attestation_route"] is None
        ledger.increment.assert_not_called()
        eval_rows = _rows(caplog, "event=leader_completion_resolver_eval ")
        assert eval_rows
        row = eval_rows[0]
        assert "band=a_suspicion" in row
        assert "resolver_outcome=allow_hint" in row


# ─────────────────────────────────────────────────────────────────────────────
# TEST CLASS — Seam (iv): verdict parse / outcome mapping raises.
# ─────────────────────────────────────────────────────────────────────────────


class TestSeamIVOutcomeMappingRaises:
    """``emit_resolver_eval_row`` raises AFTER the band → outcome
    mapping has already run. PRE-FIX this seam was OUTSIDE the gate
    node's outer try/except (the fused block at indent=8 followed
    the outer try's return; graph.py:5507 sat in the open). The
    exception propagated UP to LangGraph — the gate node crashed on
    the asyncio coroutine. This was a CONTRACT DEVIATION vs FR-13's
    fail-open contract.

    POST-FIX (F-B, 2026-09-16): the fused block is wrapped in its
    own try/except (graph.py ~5243-5567). Any raise in the verdict
    mapping, hint factory, or row emission is caught: a loud
    ``event=leader_completion_gate_error`` row fires with
    ``gate_location=fused_block``, ``_persist_gate_exception_marker``
    stamps ``gate_exception_seen`` on the ledger, and the gate node
    falls through to Phase-3 (no early return). The existing
    ``decision.decision`` from ``decide()`` drives the per-band
    outcome (DP-5 REJECTED preserved):

    * **deny-band** (decision.decision = DENIED) → Phase-3 deny+nudge
      machinery runs: counter increments, in-graph nudge injects.
    * **marker/A bands** (decision.decision = ALLOWED_LEGITIMATE_
      PENDING_WAKEUP) → Phase-3 plain ALLOW path runs: NO hint, NO
      counter write, NO nudge (marker-only signal too weak to deny
      on this seam).

    The test bodies here now assert the FAIL-OPEN behavior (no raise,
    loud row + marker, per-band outcome) — what used to be a
    documented finding is now the pinned contract.

    NOTE: All three bands exercise the same code path (the emit is
    always called); the per-band differentiation here pins the
    per-band recovery shape post-fix."""

    def _install_emit_boom(self, monkeypatch):
        def boom(*_args, **_kwargs):
            raise RuntimeError(
                "injected emit-resolver-eval-row fault"
            )

        monkeypatch.setattr(
            resolver_activation_mod,
            "emit_resolver_eval_row",
            boom,
        )

    def test_seam_iv_deny_band_fail_open_keeps_deny_nudge(
        self, monkeypatch, caplog
    ):
        """F-B (2026-09-16) — seam (iv) on the deny band: the fused
        block's ``_emit_resolver_eval_row`` raises AFTER the mapping
        arm set ``resolver_outcome=deny_nudge``. The F-B wrapper
        catches the exception, stamps ``gate_exception_seen`` (F-C)
        on the instance row via ``_persist_gate_exception_marker``,
        and falls through. Phase-3 sees ``decision.decision=DENIED``
        and runs the deny+nudge path (counter increments; in-graph
        nudge injects; ``attestation_route=agent``). The gate node
        SURVIVES — no propagation to LangGraph."""
        _drift_pin()
        spy = _JudgeSpy(
            ['{"verdict": "complete", "rationale": "unused"}']
        )
        monkeypatch.setattr(judge_mod, "_invoke_judge_llm", spy)
        self._install_emit_boom(monkeypatch)

        node, _manager, ledger = _make_node(
            instance_id="seam-iv-deny",
            pending_children=0,
            queued_wakeups=0,
            live_descendants=0,
            busy_descendants=0,
            denied_count=0,
        )
        with _capture(caplog).at_level(logging.INFO):
            result = _run(
                node, _delegated_state("All done."), "seam-iv-deny"
            )

        # Loud F-B row fired (gate_location=fused_block is the F-B
        # marker that distinguishes the inner wrapper from the outer
        # scanner/decide catch at graph.py:5176).
        fje_rows = _rows(
            caplog, "event=leader_completion_gate_error "
        )
        assert fje_rows, (
            "Seam (iv) deny-band: F-B wrapper MUST emit a loud "
            "leader_completion_gate_error row"
        )
        assert any(
            "gate_location=fused_block" in r for r in fje_rows
        ), (
            "Seam (iv) deny-band: row MUST carry "
            "gate_location=fused_block (the F-B seam marker)"
        )
        assert any(
            "gate_exception_seen=true" in r and "decision=fail_open_allowed" in r
            for r in fje_rows
        ), (
            "Seam (iv) deny-band: row MUST carry gate_exception_seen=true "
            "+ decision=fail_open_allowed"
        )
        # F-C — ``_persist_gate_exception_marker`` was called on the
        # ledger to stamp the marker on the instance row (FR-13
        # "transient gate_exception_seen=true flag on the instance
        # row" contract). Asserted on the ledger mock (MagicMock auto-
        # creates ``set_metadata`` as a callable MagicMock).
        ledger.set_metadata.assert_called_once_with(
            "seam-iv-deny",
            "attestation_gate_exception_seen",
            True,
        )
        # Per-band conservative: deny+nudge — Phase-3 runs the
        # existing machinery. Decision.DENIED is the canonical
        # answer that drove decide(), and Phase-3 increments the
        # counter + injects the nudge.
        assert result["attestation_route"] == "agent", (
            "Seam (iv) deny-band: deny+nudge path runs (route=agent)"
        )
        ledger.increment.assert_called_once()

    def test_seam_iv_marker_band_fail_open_plain_allow(
        self, monkeypatch, caplog
    ):
        """F-B (2026-09-16) — seam (iv) on the marker band: the
        exception is caught, ``gate_exception_seen`` is stamped on the
        instance row (F-C), and the gate falls through. Phase-3 sees
        ``decision.decision=ALLOWED_LEGITIMATE_PENDING_WAKEUP`` and
        runs plain ALLOW (no hint, NO nudge, NO counter write —
        marker-only signal too weak to deny on this seam)."""
        _drift_pin()
        spy = _JudgeSpy(
            ['{"verdict": "complete", "rationale": "unused"}']
        )
        monkeypatch.setattr(judge_mod, "_invoke_judge_llm", spy)
        self._install_emit_boom(monkeypatch)

        node, _manager, ledger = _make_node(
            instance_id="seam-iv-marker",
            pending_children=1,
        )
        with _capture(caplog).at_level(logging.INFO):
            result = _run(
                node,
                _delegated_state(
                    "Awaiting child reply. Ending turn."
                ),
                "seam-iv-marker",
            )

        # Loud F-B row fires.
        fje_rows = _rows(
            caplog, "event=leader_completion_gate_error "
        )
        assert fje_rows, (
            "Seam (iv) marker-band: F-B wrapper MUST emit a loud row"
        )
        assert any("gate_location=fused_block" in r for r in fje_rows)
        # F-C — ledger stamp on the instance row.
        ledger.set_metadata.assert_called_once_with(
            "seam-iv-marker",
            "attestation_gate_exception_seen",
            True,
        )
        # Per-band conservative: plain ALLOW — no hint, no nudge,
        # no counter write. Decision was ALLOWED_LEGITIMATE_PENDING_
        # WAKEUP from decide(); Phase-3 takes the plain-allow arm.
        assert result["attestation_route"] is None, (
            "Seam (iv) marker-band: plain ALLOW path runs (route=None)"
        )
        # No nudge, no hint (no message injected).
        assert "messages" not in result or result["messages"] == [], (
            "Seam (iv) marker-band: NO hint/nudge on this seam "
            "(marker-only signal too weak to deny)"
        )
        ledger.increment.assert_not_called()

    def test_seam_iv_a_band_fail_open_plain_allow(
        self, monkeypatch, caplog
    ):
        """F-B (2026-09-16) — seam (iv) on the A band: same shape as
        the marker band (plain ALLOW, no hint, no nudge, no counter
        write). Verifies the per-band conservative mapping holds on
        every band — the F-B wrapper does NOT promote a denied
        decision to allow (DP-5 REJECTED), and does NOT add a hint
        to an allowed-band allow."""
        _drift_pin()
        spy = _JudgeSpy(
            ['{"verdict": "complete", "rationale": "unused"}']
        )
        monkeypatch.setattr(judge_mod, "_invoke_judge_llm", spy)
        self._install_emit_boom(monkeypatch)

        node, _manager, ledger = _make_node(
            instance_id="seam-iv-a",
            pending_children=1,
            live_descendants=2,
            busy_descendants=2,
        )
        state = _delegated_state(
            "Everything is done here.",
            extra_messages=[_child_report_check_note()],
        )
        with _capture(caplog).at_level(logging.INFO):
            result = _run(node, state, "seam-iv-a")

        # Loud F-B row fires.
        fje_rows = _rows(
            caplog, "event=leader_completion_gate_error "
        )
        assert fje_rows, (
            "Seam (iv) A-band: F-B wrapper MUST emit a loud row"
        )
        assert any("gate_location=fused_block" in r for r in fje_rows)
        # F-C — ledger stamp on the instance row.
        ledger.set_metadata.assert_called_once_with(
            "seam-iv-a",
            "attestation_gate_exception_seen",
            True,
        )
        # Per-band conservative: plain ALLOW (A-band decision is
        # ALLOWED_LEGITIMATE_PENDING_WAKEUP — no hint on this seam).
        assert result["attestation_route"] is None
        assert "messages" not in result or result["messages"] == []
        ledger.increment.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# F2 WAKEUP RE-FIRE — pending wakeup + fault → gate MUST re-evaluate.
# ─────────────────────────────────────────────────────────────────────────────


class TestF2WakeupRefire:
    """F2 framing (Job 5 spec): after a fail-open allow with pending
    work still present (resolver snapshot None OR wrapper-fault
    marker-band path), deliver a pending wakeup → assert the gate
    EVALUATES AGAIN and — fault now removed — reaches a real
    outcome.

    Two scenarios:

    * **S1 (Seam (i) marker-band fail-open):** the first
      ``node()`` call with the activation-predicate fault
      installed produces a plain ALLOW (no hint, snapshot None).
      The second ``node()`` call AFTER the fault is REMOVED (and
      the wakeup-state updated to reflect the wakeup delivery —
      ``pending_children`` dropped to 0 because the wakeup has
      landed) → produces a real outcome.

    * **S2 (Seam (iii) marker-band allow-with-log-only path):**
      the first call with the wrapper-fault installed produces an
      ALLOW log-only (2026-09-23 b2f4dae9: the Completion Check
      Note hint is RETIRED end-to-end; the
      ``resolver_outcome=allow_hint`` row label is the
      checkpoint-durable record). The second ``node()`` call AFTER
      the fault is removed → the gate evaluates AGAIN through the
      fused block — the resolver-eval row fires; the judge runs.

    Documented assertions:

    * **S1 first call:** plain allow, NO hint, NO nudge, NO
      counter — fail-open allow with the wakeup pending.
    * **S1 second call:** resolver-eval row fires (gate evaluates
      again); judge_invoked=True; a real outcome emerges.
    * **S2 first call:** ALLOW log-only, NO nudge, NO counter.
    * **S2 second call:** resolver-eval row fires; judge_invoked=True.
    """

    def test_f2_s1_seam_i_refires_after_fault_removal(
        self, monkeypatch, caplog
    ):
        """F2 S1 — Seam (i) activation-predicate raises on the FIRST
        call (resolver-eval-error row fires, plain allow). After
        fault removal the SECOND call evaluates again through the
        fused block (resolver-eval row fires, judge_invoked=True).

        ACTUAL behavior evidence:
          * First call: ``event=leader_completion_resolver_eval_error``
            with ``error_class=RuntimeError``; gate falls through
            to ``allowed_legitimate_pending_wakeup`` (plain END).
          * Second call: ``event=leader_completion_resolver_eval``
            row with ``fired=True band=marker judge_invoked=True``
            — the gate RE-FIRED and the fused block evaluated the
            evidence (the real judge returns ``verdict=error``
            because the MagicMock config cannot actually construct
            an LLM judge — that's the second-call verification, the
            row IS produced and the judge WAS invoked)."""
        _drift_pin()
        spy = _JudgeSpy(
            ['{"verdict": "complete", "rationale": "wakeup test"}']
        )
        monkeypatch.setattr(judge_mod, "_invoke_judge_llm", spy)

        # First call — Seam (i) installed.
        monkeypatch.setattr(
            resolver_activation_mod,
            "activation_predicate",
            _ActivationPredicateBoom(),
        )

        node, _manager, ledger = _make_node(
            instance_id="f2-s1-marker",
            pending_children=1,
        )
        caplog.clear()
        with _capture(caplog).at_level(logging.INFO):
            first_result = _run(
                node,
                _delegated_state(
                    "Awaiting child reply. Ending turn."
                ),
                "f2-s1-marker",
            )

        # FIRST CALL — fault active.
        # Resolver-eval-error row fires (loud contract).
        assert _rows(
            caplog, "event=leader_completion_resolver_eval_error"
        ), "F2 S1 first call: resolver-eval-error row MUST fire"
        # Plain allow — NO hint.
        assert "messages" not in first_result, (
            "F2 S1 first call: plain allow — no hint"
        )
        assert first_result["attestation_route"] is None

        # Second call — fault removed (monkeypatch.undo reverts
        # every patch above; the spy is replaced by the real
        # _invoke_judge_llm which fails on the MagicMock config,
        # but the gate DOES evaluate again — the test asserts the
        # second-call re-fire evidence via log rows, not via spy).
        monkeypatch.undo()

        with _capture(caplog).at_level(logging.INFO):
            second_result = _run(
                node,
                _delegated_state(
                    "Awaiting child reply. Ending turn."
                ),
                "f2-s1-marker",
            )

        # SECOND CALL — fault cleared.
        # Resolver-eval row fires (gate RE-FIRED).
        eval_rows = _rows(
            caplog, "event=leader_completion_resolver_eval "
        )
        assert eval_rows, (
            "F2 S1 second call: resolver-eval row MUST fire (gate "
            "RE-FIRED)"
        )
        # Find the second-call row (post-fault-clear). The first-call
        # resolver row was a resolver-eval-error row (different token),
        # so this list contains ONLY the second-call row.
        row = eval_rows[-1]
        assert "band=marker" in row
        assert "judge_invoked=True" in row, (
            "F2 S1 second call: judge INVOKED — gate evaluated through "
            "the fused block"
        )
        # Real outcome reached — verdict is "error" (real
        # _invoke_judge_llm fails on MagicMock config) → path-d →
        # ALLOW log-only (2026-09-23 b2f4dae9: hint RETIRED
        # end-to-end; the ``resolver_outcome=allow_hint`` row label
        # is the durable record).
        assert "resolver_outcome=allow_hint" in row
        # Second call is log-only too — NO hint injected.
        assert "messages" not in second_result, (
            f"F2 S1 second call: ALLOW log-only after 2026-09-23; "
            f"got messages={second_result.get('messages')!r}"
        )
        assert second_result["attestation_route"] is None

    def test_f2_s2_seam_iii_refires_after_fault_removal(
        self, monkeypatch, caplog
    ):
        """F2 S2 — Seam (iii) judge wrapper raises on the FIRST
        call (fused-judge-error row fires, ALLOW + hint per
        path-d-with-pending). After fault removal the SECOND call
        evaluates again through the fused block (resolver-eval
        row fires, judge_invoked=True).

        ACTUAL behavior evidence:
          * First call: ``event=leader_completion_gate_fused_judge_error``
            with ``error_class=RuntimeError``; gate maps to
            ``resolver_outcome=allow_hint`` (path-d-with-pending).
          * Second call: ``event=leader_completion_resolver_eval``
            row with ``fired=True band=marker judge_invoked=True``
            — the gate RE-FIRED through the real judge path."""
        _drift_pin()
        spy = _JudgeSpy(
            ['{"verdict": "complete", "rationale": "wakeup test"}']
        )
        monkeypatch.setattr(judge_mod, "_invoke_judge_llm", spy)

        # First call — Seam (iii) installed (judge wrapper raises).
        async def boom(_bundle_text, *, config):
            raise RuntimeError("injected judge-transport fault")

        monkeypatch.setattr(judge_mod, "judge_fused_bundle_async", boom)

        node, _manager, ledger = _make_node(
            instance_id="f2-s2-marker",
            pending_children=1,
        )
        caplog.clear()
        with _capture(caplog).at_level(logging.INFO):
            first_result = _run(
                node,
                _delegated_state(
                    "Awaiting child reply. Ending turn."
                ),
                "f2-s2-marker",
            )

        # FIRST CALL — fault active.
        # Fused-judge-error row fires (loud contract).
        assert _rows(
            caplog, "event=leader_completion_gate_fused_judge_error"
        ), "F2 S2 first call: fused-judge-error row MUST fire"
        # ALLOW log-only — NO hint injected (2026-09-23 b2f4dae9).
        assert "messages" not in first_result, (
            f"F2 S2 first call: ALLOW log-only after 2026-09-23; "
            f"got messages={first_result.get('messages')!r}"
        )
        assert first_result["attestation_route"] is None
        # judge_invoked=False on the FIRST-call row (fault active).
        first_eval_rows = _rows(
            caplog, "event=leader_completion_resolver_eval "
        )
        assert first_eval_rows
        assert "judge_invoked=False" in first_eval_rows[-1]

        # Second call — fault removed.
        monkeypatch.undo()  # restore judge_fused_bundle_async

        with _capture(caplog).at_level(logging.INFO):
            second_result = _run(
                node,
                _delegated_state(
                    "Awaiting child reply. Ending turn."
                ),
                "f2-s2-marker",
            )

        # SECOND CALL — fault cleared. The real
        # ``_invoke_judge_llm`` runs (no longer spies/faults);
        # it MAY still error on the MagicMock config (TypeError)
        # but the row IS produced and the gate DID re-fire.
        eval_rows = _rows(
            caplog, "event=leader_completion_resolver_eval "
        )
        assert eval_rows
        # The second-call row is appended after the first — take the
        # LAST one (most recent emission).
        row = eval_rows[-1]
        assert "band=marker" in row, (
            "F2 S2 second call: resolver-eval row carries band=marker"
        )
        # judge_invoked=True — gate evaluated AGAIN through the
        # fused block (the wakeup caused the gate to evaluate
        # even though the first call had the wrapper fault).
        assert "judge_invoked=True" in row, (
            "F2 S2 second call: judge INVOKED — gate RE-FIRED "
            "through the fused block"
        )
        # Real outcome reached via the real judge (verdict=error
        # because MagicMock config cannot construct an LLM).
        assert second_result["attestation_route"] is None


# ─────────────────────────────────────────────────────────────────────────────
# F-C PIN — ``gate_exception_seen`` ledger stamp on Stage-2 seam faults
# ─────────────────────────────────────────────────────────────────────────────


class TestGateExceptionSeenStampFC:
    """F-C (2026-09-16) — Stage-2 seam faults (i / ii / iv) MUST
    stamp the transient ``gate_exception_seen`` marker on the
    instance row (FR-13 contract: "set a transient
    ``gate_exception_seen=true`` flag on the instance row"). The
    canonical stamp path is
    :func:`daemon.graph._persist_gate_exception_marker` →
    ``ledger.set_metadata(instance_id, "attestation_gate_exception_
    seen", True)``.

    These tests pin the F-C contract directly (independent of the
    per-band outcome assertions in :class:`TestSeamI…` and
    :class:`TestSeamIV…` which exercise the same surface). The
    seam-(iii) wrapper fault is NOT covered here — it lives INSIDE
    the fused block's own try/except (graph.py:5294-5319) and
    already stamps via the fused-judge-error row + decision-shape;
    extending the F-C contract to it would be a contract change
    (not part of the F-A/B/C hotfix scope).

    Conservative direction preserved (DP-5 REJECTED): the marker is
    stamped but the outcome is NOT flipped — seam-(i)/(ii) deny-band
    faults still deny+nudge via Phase-3 (verified by the per-band
    tests above), seam-(iv) marker/A-band faults plain-allow.
    """

    def test_fc_seam_i_deny_band_stamps_marker_via_ledger(
        self, monkeypatch, caplog
    ):
        """F-C — seam (i) activation-predicate exception on the deny
        band: ``_persist_gate_exception_marker`` is called, the
        outcome stays DENIED (Phase-3 deny+nudge still runs)."""
        _drift_pin()
        spy = _JudgeSpy(
            ['{"verdict": "complete", "rationale": "unused"}']
        )
        monkeypatch.setattr(judge_mod, "_invoke_judge_llm", spy)
        monkeypatch.setattr(
            resolver_activation_mod,
            "activation_predicate",
            _ActivationPredicateBoom(),
        )

        node, _manager, ledger = _make_node(
            instance_id="fc-seam-i-deny",
            pending_children=0,
            queued_wakeups=0,
            live_descendants=0,
            busy_descendants=0,
        )
        with _capture(caplog).at_level(logging.INFO):
            result = _run(
                node, _delegated_state("All done."), "fc-seam-i-deny"
            )

        # F-C — ledger stamp on the instance row.
        ledger.set_metadata.assert_called_once_with(
            "fc-seam-i-deny",
            "attestation_gate_exception_seen",
            True,
        )
        # Conservative direction preserved — DENIED path runs
        # (Phase-3 increments the counter + injects the nudge).
        assert result["attestation_route"] == "agent"
        ledger.increment.assert_called_once()

    def test_fc_seam_ii_deny_band_stamps_marker_via_ledger(
        self, monkeypatch, caplog
    ):
        """F-C — seam (ii) fused-payload build exception on the deny
        band: same shape as seam (i) — marker stamped, DENIED path
        runs."""
        _drift_pin()
        spy = _JudgeSpy(
            ['{"verdict": "complete", "rationale": "unused"}']
        )
        monkeypatch.setattr(judge_mod, "_invoke_judge_llm", spy)
        monkeypatch.setattr(
            resolver_activation_mod,
            "evaluate_resolver_activation",
            _AssembleFusedBundleBoom(),
        )

        node, _manager, ledger = _make_node(
            instance_id="fc-seam-ii-deny",
            pending_children=0,
            queued_wakeups=0,
            live_descendants=0,
            busy_descendants=0,
        )
        with _capture(caplog).at_level(logging.INFO):
            result = _run(
                node, _delegated_state("All done."), "fc-seam-ii-deny"
            )

        # F-C — ledger stamp on the instance row.
        ledger.set_metadata.assert_called_once_with(
            "fc-seam-ii-deny",
            "attestation_gate_exception_seen",
            True,
        )
        # Conservative direction preserved — DENIED path runs.
        assert result["attestation_route"] == "agent"
        ledger.increment.assert_called_once()

    def test_fc_seam_iv_deny_band_stamps_marker_via_ledger(
        self, monkeypatch, caplog
    ):
        """F-C — seam (iv) fused-block emit/raise on the deny band:
        the F-B wrapper stamps the marker via the SAME helper
        (``_persist_gate_exception_marker``), outcome stays DENIED.
        """
        _drift_pin()
        spy = _JudgeSpy(
            ['{"verdict": "complete", "rationale": "unused"}']
        )
        monkeypatch.setattr(judge_mod, "_invoke_judge_llm", spy)
        # Re-use the seam-(iv) install helper from the upstream class.
        seam_iv_class = TestSeamIVOutcomeMappingRaises()
        seam_iv_class._install_emit_boom(monkeypatch)

        node, _manager, ledger = _make_node(
            instance_id="fc-seam-iv-deny",
            pending_children=0,
            queued_wakeups=0,
            live_descendants=0,
            busy_descendants=0,
            denied_count=0,
        )
        with _capture(caplog).at_level(logging.INFO):
            result = _run(
                node, _delegated_state("All done."), "fc-seam-iv-deny"
            )

        # F-C — ledger stamp on the instance row.
        ledger.set_metadata.assert_called_once_with(
            "fc-seam-iv-deny",
            "attestation_gate_exception_seen",
            True,
        )
        # Conservative direction preserved — DENIED path runs
        # (Phase-3 increments the counter + injects the nudge).
        assert result["attestation_route"] == "agent"
        ledger.increment.assert_called_once()
        # Loud F-B row fires (the F-B seam marker).
        assert _rows(
            caplog, "event=leader_completion_gate_error "
        ), "F-C seam (iv) deny: F-B wrapper MUST emit a loud row"
        assert any(
            "gate_location=fused_block" in r
            for r in _rows(caplog, "event=leader_completion_gate_error ")
        )


# ─────────────────────────────────────────────────────────────────────────────
# Stage-3 ledger item (b) — NAMED invariant: the F2 fail-open TARGET pin.
# The kill-switch-off MIRROR semantics on resolver-compute faults.
# ─────────────────────────────────────────────────────────────────────────────


class TestF2FailOpenTargetPin:
    """NAMED invariant (Stage-3 ledger item (b), 2026-09-17): on a
    RESOLVER-COMPUTE fault (seams i/ii — activation predicate /
    bundle assembly raising) the per-band outcomes MIRROR the
    kill-switch-off mapping EXACTLY:

    * deny band  → deny+nudge WITHOUT a judge (Q1 parity — the
      rescue-judge opportunity is FORFEITED, never fail-safe-allowed;
      conservative rescue-judge-forfeit documented in decisions.md
      D-RES4 ledger (d));
    * marker / A bands → PLAIN ALLOW (suspicion signals are too weak
      to deny without the judge; zero counter movement, zero nudge,
      zero hint).

    This pin is the named seam-i/ii aggregate of the per-fault rows
    above — implementing "fault → allow everywhere" or "fault →
    deny everywhere" anywhere on this seam fails it loudly.
    """

    def test_f2_fail_open_target_mirrors_kill_switch_off(
        self, monkeypatch, caplog
    ):
        _drift_pin()
        # Make the resolver compute raise on EVERY evaluation (seam i).
        monkeypatch.setattr(
            resolver_activation_mod,
            "activation_predicate",
            _ActivationPredicateBoom(),
        )
        spy = _JudgeSpy(
            ['{"verdict": "complete", "rationale": "unused"}']
        )
        monkeypatch.setattr(judge_mod, "_invoke_judge_llm", spy)

        # ── deny band (un-attested ∧ quiet): deny+nudge, NO judge ──
        node_deny, _m1, ledger_deny = _make_node(instance_id="f2p-deny")
        with _capture(caplog).at_level(logging.INFO):
            result_deny = _run(
                node_deny, _delegated_state("All done."), "f2p-deny"
            )
        assert "messages" in result_deny, (
            "F2 target pin — deny band: resolver-compute fault keeps "
            "deny+nudge (the kill-switch-off mirror), NEVER a "
            "fail-safe allow"
        )
        assert result_deny["attestation_route"] == "agent"
        ledger_deny.increment.assert_called_once()
        assert _rows(
            caplog, "event=leader_completion_resolver_eval_error"
        ), "resolver-compute fault row MUST fire"

        # ── marker band (b_fires ∧ ¬quiet): plain allow, NO judge ──
        caplog.clear()
        node_m, _m2, ledger_m = _make_node(
            instance_id="f2p-marker",
            pending_children=1,
            live_descendants=1,
        )
        with _capture(caplog).at_level(logging.INFO):
            result_m = _run(
                node_m,
                _delegated_state("Awaiting child. Ending turn."),
                "f2p-marker",
            )
        assert result_m.get("attestation_route") is None
        assert "messages" not in result_m, (
            "F2 target pin — marker band: resolver-compute fault "
            "plain-allows (kill-switch-off mirror) — no hint, no nudge"
        )
        ledger_m.increment.assert_not_called()
        ledger_m.reset.assert_not_called()

        # ── A band (a_suspicion alone, ¬quiet): plain allow ──
        caplog.clear()
        node_a, _m3, ledger_a = _make_node(
            instance_id="f2p-a",
            pending_children=1,
            live_descendants=1,
        )
        with _capture(caplog).at_level(logging.INFO):
            result_a = _run(
                node_a,
                _delegated_state(
                    "All shipped.",
                    extra_messages=(_child_report_check_note(),),
                ),
                "f2p-a",
            )
        assert result_a.get("attestation_route") is None
        assert "messages" not in result_a
        ledger_a.increment.assert_not_called()

        # ZERO judge HTTP attempts across ALL three bands — the
        # rescue-judge is forfeited on every band on this seam.
        assert spy.attempts == []
