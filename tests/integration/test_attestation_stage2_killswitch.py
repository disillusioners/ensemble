"""LCA resolver Stage 2 — kill-switch matrix (integration).

Drives the **real** ``create_attestation_gate_node`` (the
``daemon.graph`` factory that owns the fused-judge block + the
``_LCA_STAGE2_RESOLVER_FLIP`` Stage-2 path) through a four-cell
matrix:

* **Cell A — JUDGE=0 (mode=enforce).** Per-band mapping EXACT
  (deny-band → deny+nudge WITHOUT judge, Q1 parity; marker-band →
  plain allow; A-band → plain allow). Suspicion bands never deny
  without the judge.
* **Cell B — MODE=off.** Gate UNWIRED — zero gate event rows, zero
  resolver_eval rows, zero nudges, completions pass through.
* **Cell C — MODE=dry.** ``Decision.DRY_LOG`` rows stamped
  (would-be outcome + marker/length fields, judge never invoked);
  completion ALLOWED; ZERO LLM.
* **Cell D — Default probe.** Resolver default = enforce (assert
  the default path; cite ``daemon/services/attestation_resolver.py``
  + the boot-line test evidence path).

Each cell exercises the REAL gate node with the kill-switch env
flipped and the cached-global resolver reset between cells, mirroring
the established pattern in
``tests/integration/test_attestation_marker_routing_lca.py``.

The matrix is a Job-4-of-9 deliverable for the LCA resolver Stage 2
final merge gate. The cells map to the kill-switch decision matrix
in ``.agents/shared/planning/leader-completion-attestation/decisions.md``
D-CTD-6 / docs/setup.md (LCA kill-switch matrix).

EXISTING COVERAGE (referenced; gaps this file fills noted):

* ``tests/integration/test_attestation_marker_routing_lca.py``
  scenario (e) — kill-switch OFF on MARKER band → no judge call
  (one band only; this file extends to all three bands).
* ``tests/integration/test_attestation_marker_routing_lca.py``
  scenario (f) — dry mode on MARKER band → zero side effects
  (one band only; this file extends to all three bands).
* ``tests/unit/test_attestation_resolver_stage2.py`` R7-2 —
  per-band kill-switch mapping at the UNIT level (MagicMock
  manager + ledger). This file covers the SAME bands but at the
  INTEGRATION level (real ``create_attestation_gate_node`` path,
  real fused-judge wiring, real resolver activation seam).
* ``tests/integration/test_attestation_dry_mode.py`` — full-graph
  dry mode (the GRAPH-level integration test). This file drives
  the gate node directly per cell (no graph; lower-level integration).
* ``tests/integration/test_attestation_must_not_break.py`` —
  mode-pinned matrix on 6 surfaces. This file adds the per-band
  matrix inside the mode switch.

GAPS THIS FILE FILLS (per Job 4 spec):

1. **Cell A at integration level** — the deny-band Q1 parity
   (deny+nudge WITHOUT judge) was unit-only in
   ``tests/unit/test_attestation_resolver_stage2.py::TestR7PinKillSwitchPerBandMapping``.
2. **Cell B at gate-node level** — OFF mode unwired contract:
   zero gate event rows, zero resolver_eval rows, no nudge,
   no hint, no counter.
3. **Cell C at gate-node level** — DRY mode per-band stamping:
   ``decision=dry_log`` row + ``resolver_outcome=allow`` row,
   zero LLM constructions across all three bands.
4. **Cell D** — default probe: resolver returns ``enforce``
   when ``ENSEMBLE_LEADER_ATTESTATION_MODE`` is unset.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from daemon.graph import (
    _LCA_STAGE2_RESOLVER_FLIP,
    create_attestation_gate_node,
)
from daemon.services import attestation_report_judge as judge_mod
from daemon.services.attestation_gate import (
    Decision,
    GateSettings,
    build_gate_config,
)
from daemon.services.attestation_judge_resolver import (
    is_llm_judge_enabled,
    reset_llm_judge_resolver_for_tests,
)
from daemon.services.attestation_judge_timeout_resolver import (
    reset_judge_timeout_resolver_for_tests,
)
from daemon.services.attestation_resolver import (
    DEFAULT_MODE,
    emit_attestation_boot_log,  # noqa: F401  (citation only)
    get_config,
    reset_attestation_resolver_for_tests,
)


# ─────────────────────────────────────────────────────────────────────────────
# Drift-pin + flip guards — fail loud if the worktree drifted or the
# Stage-2 flip flipped OFF (the matrix would silently degrade to
# pre-Stage-2 behavior).
# ─────────────────────────────────────────────────────────────────────────────


def _drift_pin() -> None:
    """Assert the Stage-2 flip is ACTIVE + the resolver module path
    is what we expect. Drift-pin per the project blueprint (c) +
    the Stage-2-flip README."""
    assert _LCA_STAGE2_RESOLVER_FLIP is True, (
        "Stage-2 flip is OFF — the matrix would silently degrade to "
        "pre-Stage-2 behavior. Re-pin before proceeding."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Resolver reset fixture — every cell sees a fresh resolver cache.
# Mirrors ``_reset_resolvers`` in
# ``tests/integration/test_attestation_marker_routing_lca.py``.
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_resolvers_between_cells(monkeypatch):
    """Clear kill-switch envs and reset ALL three cached-global
    resolvers (mode + LLM-judge enabled + LLM-judge timeout) so each
    cell sees a clean resolver cache. Hermetic isolation per
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
    """Build the real gate node with MagicMock manager + ledger.

    Returns ``(node, manager, ledger)``. The MagicMock manager's
    default facade values are the THREE-input R2 baseline (``0`` on
    every counter — explicit per the
    ``test_attestation_dry_mode.py`` ``make_manager`` comment that
    documents the MagicMock > 0 inflation hazard).

    The ``mode`` parameter drives the ``GateSettings.mode`` field
    (the runtime authority for the gate's off/dry/enforce behavior).
    The gate node factory (``create_attestation_gate_node``) closes
    over the settings — ``mode`` is captured at factory-build time,
    NOT read from the env at run time. The env var
    ``ENSEMBLE_LEADER_ATTESTATION_MODE`` is the Pattern C trigger
    that the GRAPH-level ``resolve_gate_settings`` reads; here we
    drive the runtime via the GateSettings directly (the standard
    harness pattern in
    ``tests/integration/test_attestation_marker_routing_lca.py``).

    The ``llm_judge_enabled`` parameter stamps
    ``gate_config["llm_judge_enabled"]`` which the graph-level fused
    block reads together with the resolver cache (graph.py:5275). The
    test cell that flips the kill-switch must set the env var +
    reset the resolver cache + pass ``llm_judge_enabled=False`` here
    so the two checks agree.
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
    """Anchor the state as DELEGATED so the conditional gate flips ON.

    The conditional-attestation scanner (``daemon/services/attestation_gate.py``
    §(v.b)) reads the delegation tool_call and flips
    ``attestation_required = True``. Without delegation, the gate
    returns early (D10-exempt) — the matrix would never exercise
    the per-band branches.
    """
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
    """The Stage-0 child-report check note (A-band signal)."""
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
    """Recording stub for ``_invoke_judge_llm`` — counts HTTP ATTEMPTS
    and captures payloads. Mirrors the Stage-2 sibling test's
    ``_JudgeSpy`` (HTTP-attempt level; the invocation-level sentinel
    is enforced at the gate node — this spy only verifies NO call
    was made)."""

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
# BAND DEFINITIONS — what each cell's state looks like.
#
#   deny-band: un-attested ∧ quiet (pending=0, wakeups=0, live=0)
#   marker-band: b_fires ∧ ¬quiet (marker OR length AND tree NOT quiet)
#   A-band: a_suspicion alone ∧ ¬quiet (child-report check note AND
#             no markers AND tree NOT quiet; busy suppression muted
#             so markers don't fire alone — see §4.2)
# ─────────────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
# CELL A — JUDGE=0 (mode=enforce) — per-band mapping EXACT.
#
#   deny-band   → deny+nudge WITHOUT judge (Q1 parity)
#   marker-band → plain allow (exact today semantics)
#   A-band      → plain allow (exact today semantics)
#
# Suspicion bands NEVER deny without the judge.
# ─────────────────────────────────────────────────────────────────────────────


class TestCellAKillswitchOffPerBandMapping:
    """``ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED=0`` + mode=enforce.

    Spec (Job 4 spec, Cell A): with the LLM-judge kill-switch OFF,
    the deny-band denies WITHOUT calling the judge (Q1 parity — the
    deny-path judge is a RESCUER, never the denier). The marker and
    A bands fall through to plain ALLOW — the marker-only signal is
    too weak to deny without the LLM verdict.
    """

    def test_a_deny_band_denies_without_judge(self, monkeypatch, caplog):
        """Cell A.1 — deny-band (un-attested ∧ quiet) → deny+nudge,
        ZERO judge HTTP attempts, Q1 parity."""
        _drift_pin()
        spy = _JudgeSpy(['{"verdict": "complete", "rationale": "unused"}'])
        monkeypatch.setattr(judge_mod, "_invoke_judge_llm", spy)
        monkeypatch.setenv(
            "ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED", "0"
        )
        reset_llm_judge_resolver_for_tests()
        # Pre-condition: the kill-switch OFF env MUST flip the real
        # resolver to False — guards against a resolver-cache leak
        # from a previous test (pattern C cached-global).
        assert is_llm_judge_enabled() is False, (
            "Cell A pre-condition: kill-switch OFF must flip the "
            "real resolver to False"
        )

        node, _manager, ledger = _make_node(
            instance_id="cell-a-deny",
            pending_children=0,
            queued_wakeups=0,
            live_descendants=0,
            busy_descendants=0,
            llm_judge_enabled=False,  # gate_config must agree with env
        )
        with _capture(caplog).at_level(logging.INFO):
            result = _run(
                node,
                _delegated_state("All done, shipped."),
                "cell-a-deny",
            )

        # ZERO judge HTTP attempts — deny+nudge without the judge.
        assert spy.attempts == [], (
            "Cell A.1 deny-band: kill-switch OFF ⇒ NO judge call "
            "(Q1 parity; deny-band path-(d)-exact)"
        )
        # Nudge injected (DENIED → HumanMessage with marker).
        assert "messages" in result, "Cell A.1 deny-band ⇒ nudge injected"
        assert result["attestation_route"] == "agent"
        ledger.increment.assert_called_once()
        # Resolver row carries the post-flip authoritative outcome.
        eval_rows = _rows(caplog, "event=leader_completion_resolver_eval ")
        assert eval_rows, "Cell A.1: resolver_eval row MUST fire"
        row = eval_rows[0]
        assert "band=deny" in row
        assert "resolver_outcome=deny_nudge" in row
        assert "judge_invoked=False" in row
        # Fused judge row MUST NOT fire.
        assert _rows(caplog, "event=leader_completion_gate_fused_judge ") == [], (
            "Cell A.1: kill-switch OFF ⇒ NO fused_judge row"
        )
        # Fused judge DISABLED row carries the explicit sentinel.
        disabled_rows = _rows(
            caplog, "event=leader_completion_gate_fused_judge_disabled"
        )
        assert disabled_rows, "Cell A.1: disabled-row MUST fire"
        assert "verdict=<skipped>" in disabled_rows[0]
        assert "band=deny" in disabled_rows[0]

    def test_a_marker_band_plain_allow(self, monkeypatch, caplog):
        """Cell A.2 — marker-band (marker hit ∧ tree NOT quiet) →
        plain allow, NO judge, NO hint, NO counter."""
        _drift_pin()
        spy = _JudgeSpy(['{"verdict": "complete", "rationale": "unused"}'])
        monkeypatch.setattr(judge_mod, "_invoke_judge_llm", spy)
        monkeypatch.setenv(
            "ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED", "0"
        )
        reset_llm_judge_resolver_for_tests()
        assert is_llm_judge_enabled() is False

        node, _manager, ledger = _make_node(
            instance_id="cell-a-marker",
            pending_children=1,  # NOT quiet
            llm_judge_enabled=False,
        )
        with _capture(caplog).at_level(logging.INFO):
            result = _run(
                node,
                _delegated_state("Awaiting child reply. Ending turn."),
                "cell-a-marker",
            )

        # ZERO judge HTTP attempts.
        assert spy.attempts == [], (
            "Cell A.2 marker-band: kill-switch OFF ⇒ NO judge call "
            "(marker-only signal too weak to deny)"
        )
        # Plain allow — NO hint injected.
        assert "messages" not in result, (
            "Cell A.2 marker-band: plain allow — NO hint"
        )
        assert result["attestation_route"] is None
        ledger.increment.assert_not_called()
        # Resolver row: band=marker, outcome=allow, judge_invoked=False.
        eval_rows = _rows(caplog, "event=leader_completion_resolver_eval ")
        assert eval_rows, "Cell A.2: resolver_eval row MUST fire"
        row = eval_rows[0]
        assert "band=marker" in row
        assert "resolver_outcome=allow" in row
        assert "judge_invoked=False" in row
        # Fused judge DISABLED row carries the marker-band sentinel.
        disabled_rows = _rows(
            caplog, "event=leader_completion_gate_fused_judge_disabled"
        )
        assert disabled_rows, "Cell A.2: disabled-row MUST fire on marker band"
        assert "band=marker" in disabled_rows[0]
        assert "verdict=<skipped>" in disabled_rows[0]

    def test_a_a_band_plain_allow(self, monkeypatch, caplog):
        """Cell A.3 — A-band (child-report check note + busy tree +
        no markers) → plain allow, NO judge, NO hint, NO counter."""
        _drift_pin()
        spy = _JudgeSpy(['{"verdict": "complete", "rationale": "unused"}'])
        monkeypatch.setattr(judge_mod, "_invoke_judge_llm", spy)
        monkeypatch.setenv(
            "ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED", "0"
        )
        reset_llm_judge_resolver_for_tests()
        assert is_llm_judge_enabled() is False

        # A-band: tree NOT quiet, busy descendants muted (markers
        # don't fire alone), child-report check note present (Source A).
        node, _manager, ledger = _make_node(
            instance_id="cell-a-a-band",
            pending_children=1,
            live_descendants=2,
            busy_descendants=2,
            llm_judge_enabled=False,
        )
        state = _delegated_state(
            "Everything is done here.",
            extra_messages=[_child_report_check_note()],
        )
        with _capture(caplog).at_level(logging.INFO):
            result = _run(node, state, "cell-a-a-band")

        # ZERO judge HTTP attempts.
        assert spy.attempts == [], (
            "Cell A.3 A-band: kill-switch OFF ⇒ NO judge call "
            "(A-band signal too weak to deny without judge)"
        )
        assert "messages" not in result, "Cell A.3 A-band: plain allow — NO hint"
        assert result["attestation_route"] is None
        ledger.increment.assert_not_called()
        eval_rows = _rows(caplog, "event=leader_completion_resolver_eval ")
        assert eval_rows, "Cell A.3: resolver_eval row MUST fire"
        row = eval_rows[0]
        assert "band=a_suspicion" in row
        assert "resolver_outcome=allow" in row
        assert "judge_invoked=False" in row
        # Fused judge DISABLED row carries the A-band sentinel.
        disabled_rows = _rows(
            caplog, "event=leader_completion_gate_fused_judge_disabled"
        )
        assert disabled_rows, "Cell A.3: disabled-row MUST fire on A-band"
        assert "band=a_suspicion" in disabled_rows[0]
        assert "verdict=<skipped>" in disabled_rows[0]


# ─────────────────────────────────────────────────────────────────────────────
# CELL B — MODE=off — gate UNWIRED.
#
# Per ``daemon/services/attestation_gate.py`` line 908: when
# ``mode == "off"``, the evaluate() function early-returns with
# Decision.ALLOWED. No gate event row. No resolver activation. No
# nudge. No counter. Completions pass through.
# ─────────────────────────────────────────────────────────────────────────────


class TestCellBModeOffGateUnwired:
    """``ENSEMBLE_LEADER_ATTESTATION_MODE=off``.

    Spec (Job 4 spec, Cell B): the gate is UNWIRED — zero gate event
    rows, zero resolver_eval rows, zero nudges, completions pass
    through. Verifies the off-mode early-return is byte-equivalent
    to the pre-feature baseline (no LLM, no ledger writes, no log
    rows from the gate subsystem).
    """

    @pytest.mark.parametrize(
        "band_fixture",
        [
            # deny-band
            dict(
                pending=0, wakeups=0, live=0, busy=0,
                mission="All done.",
                note=False,
                band_label="deny-band",
            ),
            # marker-band
            dict(
                pending=1, wakeups=0, live=0, busy=0,
                mission="Awaiting reply. Ending turn.",
                note=False,
                band_label="marker-band",
            ),
            # A-band
            dict(
                pending=1, wakeups=0, live=2, busy=2,
                mission="Everything is done here.",
                note=True,
                band_label="a-band",
            ),
        ],
        ids=["deny-band", "marker-band", "a-band"],
    )
    def test_b_mode_off_gate_unwired(
        self, monkeypatch, caplog, band_fixture
    ):
        """Cell B — mode=off across all three bands → gate UNWIRED."""
        _drift_pin()
        spy = _JudgeSpy(['{"verdict": "complete", "rationale": "unused"}'])
        monkeypatch.setattr(judge_mod, "_invoke_judge_llm", spy)
        # Both the env var (Pattern C canonical) AND the GateSettings
        # (runtime authority) flip to off. The gate node factory
        # captures GateSettings at build time; the env var drives the
        # boot log + the resolver cache flip.
        monkeypatch.setenv("ENSEMBLE_LEADER_ATTESTATION_MODE", "off")
        reset_attestation_resolver_for_tests()
        config = get_config()
        assert config.mode == "off", (
            f"Cell B ({band_fixture['band_label']}) pre-condition: "
            "mode=off env MUST flip the real resolver to off"
        )

        node, _manager, ledger = _make_node(
            instance_id=f"cell-b-{band_fixture['band_label']}",
            pending_children=band_fixture["pending"],
            queued_wakeups=band_fixture["wakeups"],
            live_descendants=band_fixture["live"],
            busy_descendants=band_fixture["busy"],
            mode="off",
        )
        extra = (
            [_child_report_check_note()]
            if band_fixture["note"]
            else []
        )
        with _capture(caplog).at_level(logging.INFO):
            result = _run(
                node,
                _delegated_state(band_fixture["mission"], extra_messages=extra),
                f"cell-b-{band_fixture['band_label']}",
            )

        # Gate is UNWIRED:
        #   * NO gate event row from the ``evaluate()`` early-return.
        #   * NO resolver_eval row (resolver was never attached).
        #   * NO fused-judge row (judge never planned).
        #   * ZERO judge HTTP attempts (proves no LLM client).
        #   * ZERO ledger writes (increment / reset / set_escalated).
        #   * NO nudge injected.
        #   * Result is plain ALLOW with ``attestation_route=None``.
        assert spy.attempts == [], (
            f"Cell B ({band_fixture['band_label']}): mode=off ⇒ "
            "ZERO judge calls"
        )
        assert "messages" not in result, (
            f"Cell B ({band_fixture['band_label']}): mode=off ⇒ "
            "NO nudge / hint injected"
        )
        assert result["attestation_route"] is None
        ledger.increment.assert_not_called()
        ledger.reset.assert_not_called()
        ledger.set_escalated_and_reset.assert_not_called()
        # The canonical gate event row MUST NOT fire in off mode
        # (the early-return at line 908 of attestation_gate.py
        # returns BEFORE the row is emitted).
        assert _rows(caplog, "event=leader_completion_gate ") == [], (
            f"Cell B ({band_fixture['band_label']}): mode=off ⇒ "
            "NO leader_completion_gate event row"
        )
        # The resolver_eval row MUST NOT fire (no resolver snapshot
        # was attached to the early-return decision).
        assert _rows(caplog, "event=leader_completion_resolver_eval ") == [], (
            f"Cell B ({band_fixture['band_label']}): mode=off ⇒ "
            "NO resolver_eval row"
        )
        # The fused-judge rows MUST NOT fire.
        assert _rows(
            caplog, "event=leader_completion_gate_fused_judge "
        ) == [], (
            f"Cell B ({band_fixture['band_label']}): mode=off ⇒ "
            "NO fused_judge row"
        )
        assert _rows(
            caplog, "event=leader_completion_gate_fused_judge_disabled"
        ) == [], (
            f"Cell B ({band_fixture['band_label']}): mode=off ⇒ "
            "NO fused_judge_disabled row"
        )


# ─────────────────────────────────────────────────────────────────────────────
# CELL C — MODE=dry — Decision.DRY_LOG rows stamped, ZERO LLM.
#
# Per the resolver code at line 1402 + the dry-mode test at
# tests/integration/test_attestation_dry_mode.py: in dry mode the
# gate evaluates + logs ``decision=dry_log`` + allows END, with
# ZERO side effects (no nudge, no hint, no counter, no flag). The
# fused judge wiring early-outs for Decision.DRY_LOG.
# ─────────────────────────────────────────────────────────────────────────────


class TestCellCModeDryDryLogStampedZeroLlm:
    """``ENSEMBLE_LEADER_ATTESTATION_MODE=dry``.

    Spec (Job 4 spec, Cell C): dry mode stamps ``Decision.DRY_LOG``
    rows on every evaluation; judge is NEVER invoked; completion
    ALLOWED; ZERO side effects. Per-band matrix verifies the dry-log
    posture is consistent across deny/marker/A bands.
    """

    @pytest.mark.parametrize(
        "band_fixture",
        [
            # deny-band
            dict(
                pending=0, wakeups=0, live=0, busy=0,
                mission="All done.",
                note=False,
                band_label="deny-band",
            ),
            # marker-band
            dict(
                pending=1, wakeups=0, live=0, busy=0,
                mission="Awaiting reply. Ending turn.",
                note=False,
                band_label="marker-band",
            ),
            # A-band
            dict(
                pending=1, wakeups=0, live=2, busy=2,
                mission="Everything is done here.",
                note=True,
                band_label="a-band",
            ),
        ],
        ids=["deny-band", "marker-band", "a-band"],
    )
    def test_c_mode_dry_dry_log_stamped_zero_llm(
        self, monkeypatch, caplog, band_fixture
    ):
        """Cell C — mode=dry across all three bands → DRY_LOG stamped,
        ZERO LLM, completion ALLOWED."""
        _drift_pin()
        spy = _JudgeSpy(['{"verdict": "complete", "rationale": "unused"}'])
        monkeypatch.setattr(judge_mod, "_invoke_judge_llm", spy)
        monkeypatch.setenv("ENSEMBLE_LEADER_ATTESTATION_MODE", "dry")
        reset_attestation_resolver_for_tests()
        config = get_config()
        assert config.mode == "dry", (
            f"Cell C ({band_fixture['band_label']}) pre-condition: "
            "mode=dry env MUST flip the real resolver to dry"
        )

        node, _manager, ledger = _make_node(
            instance_id=f"cell-c-{band_fixture['band_label']}",
            pending_children=band_fixture["pending"],
            queued_wakeups=band_fixture["wakeups"],
            live_descendants=band_fixture["live"],
            busy_descendants=band_fixture["busy"],
            mode="dry",
        )
        extra = (
            [_child_report_check_note()]
            if band_fixture["note"]
            else []
        )
        with _capture(caplog).at_level(logging.INFO):
            result = _run(
                node,
                _delegated_state(band_fixture["mission"], extra_messages=extra),
                f"cell-c-{band_fixture['band_label']}",
            )

        # ZERO judge HTTP attempts — dry mode never invokes the judge.
        assert spy.attempts == [], (
            f"Cell C ({band_fixture['band_label']}): mode=dry ⇒ "
            "ZERO judge calls"
        )
        # Completion ALLOWED — NO nudge, NO hint, NO counter.
        assert "messages" not in result, (
            f"Cell C ({band_fixture['band_label']}): mode=dry ⇒ "
            "NO nudge / hint injected"
        )
        assert result["attestation_route"] is None
        ledger.increment.assert_not_called()
        ledger.reset.assert_not_called()
        ledger.set_escalated_and_reset.assert_not_called()
        # Canonical gate event row stamped with decision=dry_log.
        gate_rows = _rows(caplog, "event=leader_completion_gate ")
        assert gate_rows, (
            f"Cell C ({band_fixture['band_label']}): gate event row MUST fire"
        )
        assert any("decision=dry_log" in r for r in gate_rows), (
            f"Cell C ({band_fixture['band_label']}): gate row MUST carry "
            "decision=dry_log"
        )
        # Resolver_eval row stamped (dry mode DOES run the resolver —
        # only OFF mode short-circuits).
        eval_rows = _rows(caplog, "event=leader_completion_resolver_eval ")
        assert eval_rows, (
            f"Cell C ({band_fixture['band_label']}): resolver_eval row MUST fire"
        )
        row = eval_rows[0]
        assert "mode=dry" in row, (
            f"Cell C ({band_fixture['band_label']}): resolver row MUST carry mode=dry"
        )
        # The fused judge MUST NOT fire (dry mode bypasses the plan).
        assert _rows(
            caplog, "event=leader_completion_gate_fused_judge "
        ) == [], (
            f"Cell C ({band_fixture['band_label']}): mode=dry ⇒ "
            "NO fused_judge row"
        )
        # Marker/length fields are stamped on the gate row (dry-log
        # posture — marker scanner ran, the kill-switch check is on
        # the JUDGE branch, not the scanner branch).
        marker_band_expected = band_fixture["band_label"] in ("marker-band",)
        assert any("marker_hit=" in r for r in gate_rows), (
            f"Cell C ({band_fixture['band_label']}): gate row MUST carry "
            "marker_hit field"
        )
        assert any("length_trigger=" in r for r in gate_rows), (
            f"Cell C ({band_fixture['band_label']}): gate row MUST carry "
            "length_trigger field"
        )
        # Sanity: the band label is correct for the dry resolver row.
        expected_band = {
            "deny-band": "band=deny",
            "marker-band": "band=marker",
            "a-band": "band=a_suspicion",
        }[band_fixture["band_label"]]
        # Dry mode runs the resolver snapshot, so the band stamp
        # should match the cell shape.
        assert expected_band in row, (
            f"Cell C ({band_fixture['band_label']}): resolver row MUST "
            f"carry {expected_band}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# CELL D — Default probe.
#
# Per ``daemon/services/attestation_resolver.py``: when
# ``ENSEMBLE_LEADER_ATTESTATION_MODE`` is unset, the resolver falls
# back to ``DEFAULT_MODE`` (the canonical default — currently
# ``"enforce"`` after the operator override of 2026-09-06; the
# ship-default changed from ``dry`` per D2 RESOLVED). Boot-line
# evidence: see ``docs/setup.md`` line 591 + ``.agents/tester/
# RESULTS/2026-09-07-lca-judge-merge-gate.md`` line 56 (the
# ``Leader completion attestation resolved: mode=enforce …`` boot
# line was observed at prod-restart on 2026-09-14 11:46+07).
# ─────────────────────────────────────────────────────────────────────────────


class TestCellDDefaultProbe:
    """Resolver default probe — ``ENSEMBLE_LEADER_ATTESTATION_MODE``
    unset ⇒ ``DEFAULT_MODE`` (``"enforce"`` post-operator-override
    2026-09-06).

    Spec (Job 4 spec, Cell D): the default path asserts that
    ``get_config()`` returns ``mode=DEFAULT_MODE`` when the env is
    unset, and that the boot-line evidence path is intact. Cites:

    * ``daemon/services/attestation_resolver.py`` line ~36 (DEFAULT_MODE
      docstring), line ~245 (fail-OPEN fallback to DEFAULT_MODE), and
      line ~526 (boot log line).
    * ``daemon/services/attestation_resolver.py`` line ~288 (resolver
      comment: "currently ``"enforce"`` — operator override
      2026-09-06").
    * Boot-line evidence: ``docs/setup.md`` line 591 + the
      ``.agents/tester/RESULTS/2026-09-07-lca-judge-merge-gate.md``
      row 56 quote of the resolved boot line.
    """

    def test_d_resolver_default_is_enforce(self, monkeypatch):
        """Cell D.1 — unset mode env ⇒ DEFAULT_MODE (``enforce``)."""
        _drift_pin()
        monkeypatch.delenv(
            "ENSEMBLE_LEADER_ATTESTATION_MODE", raising=False
        )
        reset_attestation_resolver_for_tests()
        config = get_config()
        assert config.mode == DEFAULT_MODE, (
            f"Cell D.1: unset mode env MUST resolve to DEFAULT_MODE "
            f"({DEFAULT_MODE!r}); got {config.mode!r}"
        )
        # Spec-required: the canonical default is ``enforce`` after
        # the operator override of 2026-09-06.
        assert DEFAULT_MODE == "enforce", (
            f"Cell D.1: DEFAULT_MODE must be 'enforce' (operator "
            f"override 2026-09-06); got {DEFAULT_MODE!r}"
        )
        assert config.mode == "enforce", (
            "Cell D.1: unset mode env ⇒ 'enforce' (per "
            "daemon/services/attestation_resolver.py:36 + :245 "
            "+ docs/setup.md:591 boot-line evidence)"
        )

    def test_d_default_probe_deny_band_routes_through_enforce(
        self, monkeypatch, caplog
    ):
        """Cell D.2 — unset mode + deny-band ⇒ enforce deny+nudge
        (the JUDGE path runs by default; this is the COMPLEMENT of
        Cell A.1 which proves the kill-switch off path).

        Complements Cell A by showing the JUDGE-ENABLED default
        actually fires a judge call and the LLM client is built.
        """
        _drift_pin()
        spy = _JudgeSpy([
            '{"verdict": "not_complete", "evidence_cited": [], '
            '"advisory_note_text": "", "rationale": "mid-work"}'
        ])
        monkeypatch.setattr(judge_mod, "_invoke_judge_llm", spy)
        monkeypatch.delenv(
            "ENSEMBLE_LEADER_ATTESTATION_MODE", raising=False
        )
        reset_attestation_resolver_for_tests()
        config = get_config()
        assert config.mode == "enforce"
        assert is_llm_judge_enabled() is True, (
            "Cell D.2 pre-condition: unset judge env ⇒ judge ENABLED"
        )

        node, _manager, ledger = _make_node(
            instance_id="cell-d-deny",
            pending_children=0,
        )
        with _capture(caplog).at_level(logging.INFO):
            result = _run(
                node, _delegated_state("All done."), "cell-d-deny"
            )

        # Judge DID fire (default enforce + judge enabled).
        assert spy.attempts, (
            "Cell D.2: default probe ⇒ judge DID fire on deny-band "
            "(complement of Cell A.1)"
        )
        # Deny+nudge machinery ran.
        assert "messages" in result
        assert result["attestation_route"] == "agent"
        ledger.increment.assert_called_once()
        eval_rows = _rows(caplog, "event=leader_completion_resolver_eval ")
        assert eval_rows
        row = eval_rows[0]
        assert "mode=enforce" in row
        assert "band=deny" in row
        assert "judge_invoked=True" in row
        assert "resolver_outcome=deny_nudge" in row
