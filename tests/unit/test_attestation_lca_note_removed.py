"""b2f4dae9 regression pins — LCA Completion Check Note RETIRED end-to-end.

2026-09-23, incident b2f4dae9: a 7m32s-old healthy wait on a RUNNING
developer child fired the (b) Completion Check Note hint on a
short mid-work ACK whose evidence-source carried a lexical FP
``"awaiting"`` (Source A suspicion — Source A is deliberately NOT
busy-muted per the R5/Δ2 architect ruling). The fused judge said
not_complete; the (b)/(d)-with-pending route injected a
checkpoint-durable ``[SYSTEM CONTEXT: Completion Check Note]``
HumanMessage as a "reminder". The note carried zero information —
the leader's ``msg[14]`` already stated the plan, the wake-up was
en route, the deny path was unreachable (live descendant). The
user's 2026-09-23 decision: FULL REMOVAL of the
(b)/(d)-with-pending hint surface — LOG-ONLY degeneration. The note
never prevents anything (the deny path prevents; the watchdog at
the 1h mark acts); every surviving note path is FP surface.

This file is the canonical witness:
  * ``test_b2f4dae9_regression_pin_healthy_busy_a_band_lexical_fp_allow_log_only``
    — the exact incident shape: healthy busy wait + Source A
    suspicion (NOT busy-muted) + child contradiction evidence +
    judge not-complete → ALLOW log-only, ZERO injected messages,
    full log row present (verdict + would-be-route).
  * ``test_suspect_pending_paused_child_completion_still_hits_full_gate``
    — the PAUSED descendant case (the suspect-pending case the user
    pinned as "protection lives in the deny path, not the note").
  * ``test_suspect_pending_en_route_only_completion_still_hits_full_gate``
    — the en-route-only case (IDLE + pending message; the leader's
    work is in flight but not executing).
  * ``test_no_completion_check_note_anywhere_in_daemon`` — the
    whole-tree negative census pin (SELF-READING PIN TAUTOLOGY
    guard: needles split across positive + negative lines so no
    whole needle sits in this file's own assert lines).
  * ``test_no_completion_check_note_constant_or_factory_anywhere_in_daemon``
    — whole-tree negative census on the retired SYMBOLS
    (constant, factory, title, citation helper, GateDecision
    field). Self-reading-pin-tautology guard: the symbol
    strings are split — they appear ONLY in the file's docstring,
    NOT in the assert lines.
  * ``test_stable_id_for_rejects_completion_check_note_kind`` —
    the canonical ``_stable_id_for`` table rejects the retired
    kind (the supported-kinds list shrunk).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Helpers — gate node + mission-shape (mirror the wiring test fixtures)
# ─────────────────────────────────────────────────────────────────────────────


def _make_lca_note_removed_node(
    *,
    instance_id: str = "lca-note-removed-it",
    busy_descendants: int = 0,
    live_descendants: int = 0,
    pending_children: int = 0,
    queued_or_expected_wakeups: int = 0,
):
    """Build the gate node with manager + ledger stubs."""
    from unittest.mock import MagicMock

    from daemon.graph import create_attestation_gate_node
    from daemon.services.attestation_gate import (
        GateSettings,
        build_gate_config,
    )
    from daemon.services.attestation_judge_resolver import (
        reset_llm_judge_resolver_for_tests,
    )

    reset_llm_judge_resolver_for_tests()

    manager = MagicMock()
    manager.count_pending_children.return_value = pending_children
    manager.get_queued_or_expected_wakeups.return_value = (
        queued_or_expected_wakeups
    )
    manager.count_live_descendants.return_value = live_descendants
    manager.count_busy_descendants.return_value = busy_descendants
    manager.enqueue_message = MagicMock()
    manager.revive = MagicMock()
    manager.send_message = MagicMock()

    ledger = MagicMock()
    ledger.increment.return_value = 1
    ledger.reset.return_value = True
    ledger.set_escalated_and_reset.return_value = True
    ledger.get.return_value = 0

    config = build_gate_config(
        instance_id,
        GateSettings("enforce", 3, 3),
        llm_judge_enabled=True,
    )
    node = create_attestation_gate_node(
        config,
        GateSettings("enforce", 3, 3),
        manager,
        instance_id,
        denied_count_getter=lambda: 0,
        ledger=ledger,
    )
    return node, manager, ledger


def _delegated_mission_with_child_contradiction_note(
    short_final_text: str,
    *,
    contradiction_terms: tuple[str, ...] = ("will write", "then i'll"),
) -> dict:
    """Delegated mission shape WITH a delivered Child Report Check note.

    The Child Report Check note is the canonical Source-A evidence
    shape (per the D-CTD-7 retirement, the mint site is gone, but
    historical checkpoints may still carry the note — the A-band
    detector survives as defense-in-depth). For the b2f4dae9
    regression pin we use the historical shape directly: the note
    in the channel = A-band suspicion fires (Source A is NOT
    busy-muted per the R5/Δ2 architect ruling).
    """
    from langchain_core.messages import AIMessage, HumanMessage

    delegation_ai = AIMessage(
        content="",
        tool_calls=[
            {"name": "send_message", "args": {"target": "child"}, "id": "c1"}
        ],
    )

    # Mirror the historical Child Report Check note shape from
    # ``daemon/services/child_reports.py::Stage-0 mint-with-delivery``.
    # The D-CTD-7 retirement (2026-09-18) removed the mint site; this
    # is the historical shape that older checkpoints may still carry
    # (defense-in-depth detector). We construct it directly here for
    # the regression pin.
    child_id = "11111111-2222-3333-4444-555555555555"
    body = (
        f'Child {child_id} completed while its final report promises '
        f'future work ("{", ".join(contradiction_terms)}") \u2014 likely '
        f"premature completion. Its promised next report will never "
        f"arrive. Verify the actual work state; if unfinished, revive "
        f"it via send_message (e.g. \"continue your work\") or verify "
        f"its subtree before relying on this report. (Advisory / "
        f"heuristic \u2014 marker scan is a substring match, not an "
        f"LLM verdict.)"
    )
    child_note = HumanMessage(
        content=f"[SYSTEM CONTEXT: Child Report Check]\n\n{body}"
    )
    child_note.additional_kwargs["context_kind"] = "child_report_check"
    child_note.additional_kwargs["injected_message"] = True
    child_note.additional_kwargs["child_report_check"] = True
    child_note.additional_kwargs["child_report_check_terms"] = list(
        contradiction_terms
    )
    child_note.additional_kwargs["child_instance_id"] = child_id

    return {
        "messages": [
            HumanMessage(content="please do it"),
            delegation_ai,
            child_note,
            AIMessage(content=short_final_text),
        ]
    }


def _judge_no(monkeypatch):
    """Spy the judge to return ``verdict=not_complete`` (mid-work)."""
    import daemon.services.attestation_report_judge as judge_mod

    async def _spy(config, user_payload, *, timeout_s, system_prompt=None):
        return (
            '{"verdict": "not_complete", "evidence_cited": [], '
            '"advisory_note_text": "", "rationale": "mid-work status"}',
            "fake-quick",
        )

    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", _spy)


# ─────────────────────────────────────────────────────────────────────────────
# Test 1 — b2f4dae9 regression pin
# ─────────────────────────────────────────────────────────────────────────────


def test_b2f4dae9_regression_pin_healthy_busy_a_band_lexical_fp_allow_log_only(
    monkeypatch, caplog
):
    """b2f4dae9 — the canonical incident shape.

    A healthy busy wait (RUNNING developer child) where the leader
    writes a short mid-work ACK whose evidence-source carries a
    lexical FP ``"awaiting"`` (Source A suspicion — Source A is
    deliberately NOT busy-muted per the R5/Δ2 architect ruling).
    The fused judge says not_complete. The (b)/(d)-with-pending
    route MUST resolve to ALLOW log-only — ZERO injected messages,
    NO hint, NO context_kind minting.

    Pre-fix: this scenario injected a
    ``[SYSTEM CONTEXT: Completion Check Note]`` HumanMessage as a
    "reminder". The note carried zero information (leader's
    msg[14] already stated the plan) and was pure FP noise on
    every healthy wait.

    Post-fix (2026-09-23): the (b) route resolves to ALLOW with
    the ``allow_hint`` label as the forensic record on logs. The
    note surface is RETIRED end-to-end.
    """
    _judge_no(monkeypatch)

    # Healthy busy wait: 1 RUNNING developer child. The user's
    # incident had a 7m32s-old healthy wait on a RUNNING developer
    # child. ``live_descendants=1`` keeps the deny path
    # unreachable (deny is impossible with live descendants by
    # design); the Source-A band fires despite busy descendants
    # (Source A is deliberately NOT busy-muted per R5/Δ2); judge
    # fires; judge-no; (b)/(d)-with-pending route.
    node, manager, ledger = _make_lca_note_removed_node(
        instance_id="b2f4dae9-it",
        busy_descendants=1,  # RUNNING child → unconditional-busy
        live_descendants=1,
        pending_children=1,  # mid-work ACK → pending wakeup
    )
    # The b2f4dae9 incident shape: a short mid-work ACK that
    # lexically hits "awaiting" via the Source A suspicion (the
    # wanderer's changelog prose carried "awaiting" as a false
    # contradiction marker). Pre-fix this fired (b) → injected a
    # Completion Check Note. Post-fix it MUST resolve to ALLOW
    # log-only.
    b2f4dae9_short_ack = (
        "Awaiting the tester reply. Ending turn, will continue."
    )

    with caplog.at_level(logging.INFO, logger="daemon.graph"), caplog.at_level(
        logging.INFO, logger="daemon.services.attestation_gate"
    ), caplog.at_level(
        logging.INFO,
        logger="daemon.services.attestation_resolver_activation",
    ):
        result = _run_node(
            node,
            _delegated_mission_with_child_contradiction_note(
                b2f4dae9_short_ack,
            ),
            thread_id="b2f4dae9-it",
        )

    # ZERO injected messages — the (b)/(d)-with-pending route is
    # log-only after 2026-09-23.
    assert "messages" not in result, (
        "b2f4dae9 regression: the (b) route MUST be log-only — "
        f"the incident's Completion Check Note injection is RETIRED; "
        f"got messages={result.get('messages')!r}"
    )
    # NO counter movement — the (b) path is allow log-only, not deny.
    ledger.increment.assert_not_called()
    ledger.reset.assert_not_called()
    ledger.set_escalated_and_reset.assert_not_called()

    # FULL log fidelity — the b2f4dae9 evidence chain must remain
    # log-reconstructible.
    log_text = "\n".join(rec.getMessage() for rec in caplog.records)

    # (1) The fused-judge row carries the verdict + the reason +
    # the band + the judge invocation flag — the post-flip
    # forensic surface.
    assert "event=leader_completion_gate_fused_judge" in log_text
    assert "verdict=not_complete" in log_text
    assert "band=a_suspicion" in log_text, (
        "Source A band MUST fire on the b2f4dae9 shape — Source A "
        "is deliberately NOT busy-muted per R5/Δ2; the lexical FP "
        "'awaiting' lives in the child contradiction evidence"
    )
    assert "a_advisory_present=True" in log_text
    # The "awaiting" lexical FP term MUST be recorded on the row.
    assert "awaiting" in log_text

    # (2) The [AttestationGate] log line carries the
    # ``would_be_route=allow_hint`` label — the canonical
    # log-only surface for the (b)/(d)-with-pending route. This
    # is the witness that the route STILL EXISTS (operators
    # can distinguish (b)/(d)-with-pending from plain (c) allow
    # via the would-be-route label) but emits NO message.
    assert "[AttestationGate] fused-judge" in log_text
    assert "would_be_route=allow_hint" in log_text, (
        "the b2f4dae9 evidence chain MUST land on the "
        "[AttestationGate] log line via would_be_route=allow_hint "
        "so operators can reconstruct the (b)/(d)-with-pending "
        "route from logs alone"
    )
    assert "log-only" in log_text and "note hint retired" in log_text, (
        "the log-only posture MUST be explicitly stamped on the "
        "[AttestationGate] log line so operators can grep-triage "
        "between pre-fix (note injected) and post-fix (note retired) "
        "deploys"
    )

    # (3) The resolver_eval row carries resolver_outcome=allow_hint
    # — the structured resolver row is the durable record.
    assert "event=leader_completion_resolver_eval" in log_text
    assert "resolver_outcome=allow_hint" in log_text
    assert "judge_invoked=True" in log_text
    assert "would_be_outcome=would_hint" in log_text, (
        "the resolver's would_be_outcome=would_hint field MUST still "
        "record the would-be hint outcome — that's the structured "
        "log surface that replaces the deleted hint injection"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Test 2 — Suspect-pending shape (PAUSED child)
# ─────────────────────────────────────────────────────────────────────────────


def test_suspect_pending_paused_child_completion_still_hits_full_gate(
    monkeypatch, caplog
):
    """Suspect-pending (PAUSED child) — completion STILL hits the full gate.

    2026-09-23 (b2f4dae9): the user pinned "protection lives in the
    deny path, not the note". PAUSED descendants are SUSPECT
    (maybe stuck — trigger stays armed). When a leader awaits a
    PAUSED-only child with Source A evidence, the judge + the
    deny path STILL run. NO hint is injected (the note is
    RETIRED); the deny path is INTACT (the protection surface).

    Pre-fix: this scenario injected a Completion Check Note on
    the PAUSED-only case (b2f4dae9-adjacent class — same lexical
    FP surface, different suspect shape). Post-fix: log-only
    silence; the deny path remains load-bearing.
    """
    _judge_no(monkeypatch)

    # PAUSED child = live (counted) but NOT busy (excluded from
    # the unconditional-busy subset). The user's spec pin:
    # PAUSED is suspect, NOT healthy — the trigger stays armed
    # so a stuck child is caught. Source A still fires (NOT
    # busy-muted).
    node, manager, ledger = _make_lca_note_removed_node(
        instance_id="paused-suspect-it",
        busy_descendants=0,  # PAUSED is NOT in the busy subset
        live_descendants=1,  # PAUSED IS live (counted)
        pending_children=1,
    )

    suspect_ack = (
        "Awaiting child reply. Ending turn, will continue."
    )

    with caplog.at_level(logging.INFO, logger="daemon.graph"), caplog.at_level(
        logging.INFO, logger="daemon.services.attestation_gate"
    ), caplog.at_level(
        logging.INFO,
        logger="daemon.services.attestation_resolver_activation",
    ):
        result = _run_node(
            node,
            _delegated_mission_with_child_contradiction_note(suspect_ack),
            thread_id="paused-suspect-it",
        )

    # Same log-only contract — the suspect shape does NOT inject a hint.
    assert "messages" not in result, (
        f"PAUSED-suspect (b) MUST be log-only after 2026-09-23; "
        f"got messages={result.get('messages')!r}"
    )
    ledger.increment.assert_not_called()

    # But the deny path is INTACT — verify the gate still fires
    # the fused judge on the suspect-pending case. The
    # protection surface is the deny path, NOT the hint.
    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "event=leader_completion_gate_fused_judge" in log_text
    assert "verdict=not_complete" in log_text
    # Source A evidence participates via the resolver_eval row's
    # terms_fired=... (the marker band wins on the fused_judge row
    # because it fires first; the A-band evidence participates via
    # the resolver's terms_fired list).
    eval_rows = [
        r for r in _rows(log_text) if "event=leader_completion_resolver_eval" in r
    ]
    assert eval_rows, (
        "PAUSED-suspect: the resolver_eval row MUST fire "
        "(Source A evidence participated)"
    )
    assert "a_suspicion" in eval_rows[0], (
        f"PAUSED-suspect: Source A evidence MUST participate "
        f"(a_suspicion in terms_fired); got row: {eval_rows[0]}"
    )
    assert "would_be_route=allow_hint" in log_text, (
        "the would-be-route label MUST land on the "
        "[AttestationGate] log line for the PAUSED-suspect case "
        "too — the route label is the forensic surface"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Test 3 — Suspect-pending shape (en-route-only)
# ─────────────────────────────────────────────────────────────────────────────


def test_suspect_pending_en_route_only_completion_still_hits_full_gate(
    monkeypatch, caplog
):
    """Suspect-pending (en-route-only) — completion STILL hits the full gate.

    2026-09-23 (b2f4dae9): the user pinned en-route-only as a
    suspect-pending shape (IDLE + pending message OR IDLE +
    unsettled job — maybe lost). When a leader awaits an
    en-route-only child with Source A evidence, the gate STILL
    runs the fused judge (Source A is NOT busy-muted). NO hint
    is injected (the note is RETIRED); the deny path is INTACT.
    """
    _judge_no(monkeypatch)

    # En-route-only = pending message/job present but no live
    # descendant yet. Source A still fires (NOT busy-muted).
    node, manager, ledger = _make_lca_note_removed_node(
        instance_id="en-route-only-it",
        busy_descendants=0,  # en-route-only is NOT in the busy subset
        live_descendants=0,  # en-route-only = no live descendant yet
        pending_children=1,  # BUT the message/job is en route
    )

    en_route_ack = (
        "Awaiting child reply. Ending turn, will continue."
    )

    with caplog.at_level(logging.INFO, logger="daemon.graph"), caplog.at_level(
        logging.INFO, logger="daemon.services.attestation_gate"
    ), caplog.at_level(
        logging.INFO,
        logger="daemon.services.attestation_resolver_activation",
    ):
        result = _run_node(
            node,
            _delegated_mission_with_child_contradiction_note(en_route_ack),
            thread_id="en-route-only-it",
        )

    # Log-only contract — the en-route-only case does NOT inject a hint.
    assert "messages" not in result, (
        f"en-route-only (b) MUST be log-only after 2026-09-23; "
        f"got messages={result.get('messages')!r}"
    )
    ledger.increment.assert_not_called()

    # The fused judge still runs (Source A fires despite busy=0).
    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "event=leader_completion_gate_fused_judge" in log_text
    assert "verdict=not_complete" in log_text
    # Source A evidence participates via the resolver_eval row's
    # terms_fired=...
    eval_rows = [
        r for r in _rows(log_text) if "event=leader_completion_resolver_eval" in r
    ]
    assert eval_rows, (
        "en-route-only: the resolver_eval row MUST fire "
        "(Source A evidence participated)"
    )
    assert "a_suspicion" in eval_rows[0], (
        f"en-route-only: Source A evidence MUST participate "
        f"(a_suspicion in terms_fired); got row: {eval_rows[0]}"
    )
    assert "would_be_route=allow_hint" in log_text


# ─────────────────────────────────────────────────────────────────────────────
# Test 4 — Whole-tree negative census pin (needle title)
# ─────────────────────────────────────────────────────────────────────────────


# Split needles so no whole needle sits in this file's own assert lines.
# The negative pin quotes EXACT retired text from
# ``git show 6bf7bed7:daemon/graph.py`` (the pre-removal canonical
# home).
_NEGATIVE_PIN_NEEDLE_COMPLETION_CHECK_NOTE_TITLE = (
    "Completion Check Note"  # the canonical title (the "needle")
)


def test_no_completion_check_note_anywhere_in_daemon():
    """Whole-tree negative census pin: ZERO Completion Check Note
    references in daemon/.

    The ``Completion Check Note`` title (the needle) was the
    canonical hint header pre-2026-09-23. After the retirement,
    the title MUST NOT appear anywhere in ``daemon/`` (the
    production-code surface). Documentation in ``docs/``,
    ``.agents/``, and ``tests/`` is allowed (those are
    historical-context surfaces, not production behavior).

    Self-reading-pin-tautology guard: the needle text appears
    in this file's docstring + the variable name + the docstring
    of the negative needle, but the assert lines themselves
    pin the COUNT (zero) against the daemon/ tree, NOT against
    this file's own text. Decider: resurrect the
    ``_COMPLETION_CHECK_NOTE_TITLE`` in ``daemon/graph.py`` —
    the assert MUST fail.
    """
    # The needle is split: the negative pin quotes the EXACT
    # retired text but as a separate variable name (NOT as a
    # literal in the assert line) so no whole needle sits in
    # the pin's own assert lines.
    needle_title = _NEGATIVE_PIN_NEEDLE_COMPLETION_CHECK_NOTE_TITLE

    # Find daemon/ via the test's importable path (worktree root).
    daemon_dir = _find_daemon_dir()

    # Walk every Python file in daemon/ (the production surface).
    offenders = []
    for py_file in sorted(daemon_dir.rglob("*.py")):
        rel = py_file.relative_to(daemon_dir.parent)
        try:
            content = py_file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        # Skip the witness docstrings that DOCUMENT the
        # retirement. The negative pin is on PRODUCTION
        # behavior, not historical documentation. We strip the
        # known retirement-witness blocks before counting.
        if rel == Path("daemon/services/context_messages.py"):
            content = re.sub(
                r"``completion_check_note`` kind REMOVED.*?"
                r"the negative census pin in.*?``\.",
                "",
                content,
                flags=re.DOTALL,
            )

        if needle_title in content:
            for lineno, line in enumerate(content.splitlines(), start=1):
                if needle_title in line:
                    offenders.append(f"{rel}:{lineno}: {line.strip()[:120]}")

    assert offenders == [], (
        f"NEGATIVE CENSUS PIN FAILED: 'Completion Check Note' "
        f"MUST NOT appear in production daemon/ code (the title "
        f"was RETIRED 2026-09-23 along with the hint surface). "
        f"Offenders (file:line: content):\n  "
        + "\n  ".join(offenders)
        + "\nIf this pin fails after the retirement was supposed to "
        "have landed, the production code was re-introduced without "
        "re-anchoring the contract. See decisions.md D-entry "
        "2026-09-23."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Test 5 — Whole-tree negative census pin (retired SYMBOLS)
# ─────────────────────────────────────────────────────────────────────────────


def test_no_completion_check_note_constant_or_factory_anywhere_in_daemon():
    """Whole-tree negative census pin: ZERO
    ``COMPLETION_CHECK_NOTE_TEXT`` /
    ``_make_completion_check_note_message`` /
    ``_COMPLETION_CHECK_NOTE_TITLE`` /
    ``_fused_hint_citation`` /
    ``marker_hint_message`` symbols in daemon/.

    The five retired symbols (constant, factory, title, citation
    helper, GateDecision field) MUST NOT appear anywhere in
    ``daemon/`` (the production-code surface). The
    ``completion_check_note`` KIND literal in
    ``_stable_id_for`` was also retired.

    Self-reading-pin-tautology guard: the SYMBOL strings
    (``COMPLETION_CHECK_NOTE_TEXT`` etc) are split — they
    appear ONLY in this file's docstring (NOT in the assert
    lines) so the negative pin's whole-needle assertion is
    NOT constant-True. Decider: resurrect the
    ``_COMPLETION_CHECK_NOTE_TITLE`` in
    ``daemon/graph.py`` — the assert MUST fail.
    """
    daemon_dir = _find_daemon_dir()
    # The retired SYMBOLS are split — concatenated from prefix +
    # suffix so the whole symbol NEVER appears in this file's
    # own assert lines (the SELF-READING PIN TAUTOLOGY guard
    # requires no whole needle in the pin's own assert code).
    _prefix = "COMPLETION_CHECK_NOTE_"  # noqa: S105  (test fixture; not a secret)
    _retired_symbols = (
        _prefix + "TEXT",
        "_make_completion_check_note_message",
        "_COMPLETION_CHECK_NOTE_TITLE",
        "_fused_hint_citation",
        "marker_hint_message",
    )

    offenders = []
    for py_file in sorted(daemon_dir.rglob("*.py")):
        rel = py_file.relative_to(daemon_dir.parent)
        try:
            content = py_file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        # Skip the witness docstrings that DOCUMENT the
        # retirement. The negative pin is on PRODUCTION
        # behavior, not historical documentation. Strip
        # known-witness lines first.
        if rel == Path("daemon/services/context_messages.py"):
            content = re.sub(
                r"``completion_check_note`` kind REMOVED.*?"
                r"the negative census pin in.*?``\.",
                "",
                content,
                flags=re.DOTALL,
            )
        # graph.py — strip the retirement-witness block (if any) before
        # counting. The D-entry 2026-09-23 retirement may add an
        # explicit retirement-witness docstring in graph.py in the
        # future; this strip is the same pattern as the
        # context_messages.py strip above (anchored retirement block,
        # not a loose "any line containing the token" sweep).
        if rel == Path("daemon/graph.py"):
            content = re.sub(
                r"completion_check_note kind REMOVED.*?"
                r"the negative census pin in.*?``\.",
                "",
                content,
                flags=re.DOTALL,
            )

        for symbol in _retired_symbols:
            if symbol in content:
                for lineno, line in enumerate(content.splitlines(), start=1):
                    if symbol in line:
                        offenders.append(
                            f"{rel}:{lineno}: {symbol} → {line.strip()[:120]}"
                        )

    assert offenders == [], (
        f"NEGATIVE CENSUS PIN FAILED: retired LCA note symbols "
        f"MUST NOT appear in production daemon/ code (RETIRED "
        f"2026-09-23 along with the hint surface). Offenders "
        f"(file:line: symbol → content):\n  "
        + "\n  ".join(offenders)
        + "\nIf this pin fails after the retirement was supposed to "
        "have landed, the production code was re-introduced without "
        "re-anchoring the contract."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Test 6 — Whole-tree negative census pin (kind literal in _stable_id_for)
# ─────────────────────────────────────────────────────────────────────────────


def test_stable_id_for_rejects_completion_check_note_kind():
    """``_stable_id_for`` rejects ``"completion_check_note"`` kind.

    The completion_check_note row was removed from
    ``_stable_id_for``'s canonical id-format table (the table is
    now 4 rows: project, shared_meta_kv, attestation_nudge,
    attestation_final_report_reminder). The kind literal MUST
    raise :class:`ValueError` (the "unknown kind" sentinel) —
    that IS the live surface the negative census pin covers.

    Pin layout:
      - The rejected kind literal (``'completion_check_note'``)
        IS allowed in the error message — operators need to see
        what was rejected.
      - The supported-kinds list is the pin target. Pre-fix the
        list was: project, shared_meta_kv, completion_check_note,
        attestation_nudge, attestation_final_report_reminder.
        Post-fix the list is: project, shared_meta_kv,
        attestation_nudge, attestation_final_report_reminder
        (the kind was removed).
    """
    import pytest as _pytest

    from daemon.services.context_messages import _stable_id_for

    with _pytest.raises(ValueError) as exc_info:
        _stable_id_for("completion_check_note", instance_id="any-iid")
    err_msg = str(exc_info.value)

    # The supported-kinds list MUST be present in the error
    # message — that's the canonical contract surface for
    # "what's supported".
    for supported in (
        "'project'",
        "'shared_meta_kv'",
        "'attestation_nudge'",
        "'attestation_final_report_reminder'",
    ):
        assert supported in err_msg, (
            f"the supported-kinds list MUST still include "
            f"{supported} (this is the post-2026-09-23 contract); "
            f"got error: {err_msg}"
        )

    # CRITICAL: the supported-kinds list MUST NOT include the
    # retired kind. The rejected-value display is OK (it appears
    # in the "unknown kind 'completion_check_note'" prefix),
    # but the supported-kinds list MUST NOT include it.
    #
    # Decider: count occurrences of the kind literal in the
    # error message. Pre-fix: 2 (rejected prefix + supported
    # list). Post-fix: 1 (rejected prefix only). If the count
    # is 2, the supported list was NOT updated.
    needle = "'completion_check_note'"
    occurrences = err_msg.count(needle)
    assert occurrences == 1, (
        f"the supported-kinds list MUST NOT include the retired "
        f"'completion_check_note' kind — pre-fix the error message "
        f"named it TWICE (rejected prefix + supported list); "
        f"post-fix the supported list shrunk to 4 kinds. Got "
        f"occurrences={occurrences} in error: {err_msg}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────


def _run_node(node, state: dict, *, thread_id: str) -> dict:
    """Run the gate node synchronously and return the result."""
    import asyncio

    return asyncio.run(
        node(state, config={"configurable": {"thread_id": thread_id}})
    )


def _find_daemon_dir() -> Path:
    """Locate the ``daemon/`` directory of the worktree.

    The test imports ``daemon`` at collection time; the
    package's ``__file__`` resolves to the worktree root's
    ``daemon/``. Use that as the canonical path so the census
    walks the production-code surface that the test imports
    from (no ``site-packages`` drift).
    """
    import daemon  # noqa: F401  -- imported for path resolution

    return Path(daemon.__file__).resolve().parent


def _rows(log_text: str) -> list[str]:
    """Split a log-text blob into per-line records (one row per line).

    The captured caplog text is concatenated across records; we
    split on newlines so per-row assertions can grep the
    individual log lines.
    """
    return log_text.splitlines()
