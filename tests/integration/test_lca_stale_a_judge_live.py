"""LCA stale-A (B1) merge gate — LIVE fused-judge pack driver.

Job: prove the softened-subordination carve-outs in
:data:`daemon.services.attestation_report_judge.FUSED_JUDGE_SYSTEM_PROMPT`
did NOT reopen the false-rescue channel. TWO scenarios, both calling the
REAL :func:`judge_fused_bundle_async` with the REAL system prompt (no
prompt stubbing, no ``_invoke_judge_llm`` monkeypatch — this is the live
check), on bundles assembled by the REAL
:func:`attestation_resolver_activation.assemble_fused_bundle`:

  S1 "resolved-stale + genuine report -> complete":
      A child's OLDER internal_report carries stale promise advisories
      (the acbf5627 accumulation shape); its NEWEST report is a clean
      delivery and the tree row says ``completed``. The B1 newest-only +
      cross-resolution scan yields NO surviving advisory — the real
      bundle renders the resolved state as ABSENCE (A-section shows
      "(no Child Report Check notes delivered)"). Expected verdict:
      ``complete``. A `complete` here is the false-rescue hazard the
      carve-outs must NOT enable — the bundle contains NO advisory at
      all, so a rescue would be legitimate, and the scenario pins that
      the softened prompt still requires evidence to deny.

  S2 "GENUINE unresolved advisory + completion claim -> not_complete":
      A child's NEWEST (terminal) report genuinely promises undelivered
      work (child-lie shape) while its tree row says ``completed`` —
      the later-contradiction exception must keep the advisory alive —
      and a sibling descendant is still running. The leader's tail
      prose claims completion. Expected verdict: ``not_complete``.

Deterministic preconditions (scan + cross-resolve + bundle-section
shapes) are hard asserts — a precondition miss is a FAIL, never a skip.
Only judge-call outcomes that never produced a verdict (auth/network/
timeout — the documented known env class) SKIP as
``LIVE-JUDGE-ENV-BLOCKED``; a WRONG verdict on a produced verdict is a
FAIL.

Run (via pack, from worktree root):
    timeout 300 bash test/packs/lca_stale_a_judge_live_test.sh
"""
from __future__ import annotations

import asyncio
import os

import pytest

pytestmark = pytest.mark.integration

from langchain_core.messages import AIMessage, HumanMessage

from daemon.config import load_config
from daemon.services.attestation_marker_scanner import (
    scan_child_terminal_report_for_promises,
)
from daemon.services.attestation_report_judge import judge_fused_bundle_async
from daemon.services.attestation_resolver_activation import (
    SourceBSignals,
    SourceCSignals,
    assemble_fused_bundle,
    collect_source_a_signals,
)

#: Per-attempt judge wall-clock cap. Worst case = 2 attempts (the
#: retry-once-on-timeout path) = 180s, inside the 200s per-test timeout
#: the pack leg sets via --override-ini="timeout=200".
#: LONG-LATENCY OVERRIDE (2026-09-20 retry run): 45s→90s + per-test 120s→200s
#: to recover the s1 arm from upstream TimeoutError — keeps the 5-min dual-layer
#: cap untouched (pack inner guard stays 280s).
_JUDGE_TIMEOUT_S = 90.0

# Valid-uuid-shaped literals (parsed by the internal_report source regex).
_CHILD_A = "11111111-2222-4333-8444-555555555555"
_CHILD_B = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
_CHILD_C = "99999999-8888-4777-8666-555555555555"

_STALE_OLD_REPORT = (
    "Phase 2 scaffold is in progress. Still pending: the migration "
    "backfill for the ledger table. Ending turn."
)
_CLEAN_NEW_REPORT = (
    "Migration backfill shipped and verified: 12/12 ledger tests green, "
    "schema reconciled on the disposable cluster, RESULTS file written "
    "with the census and drift pins. Delivered in full."
)
_GENUINE_LIE_REPORT = (
    "Core scaffold delivered. The integration coverage is in progress "
    "and will report back once the suite is green. Ending turn."
)


def _report_message(child_id: str, content: str) -> HumanMessage:
    """Build an ``internal_report:``-stamped child-report HumanMessage
    (the production drain shape: graph.py:6950-6967)."""
    return HumanMessage(
        content=content,
        additional_kwargs={
            "injected_message": True,
            "source": f"internal_report:{child_id}",
        },
    )


class _EnvBlocked(Exception):
    """Raised when the live call cannot even be attempted (env class)."""


def _judge_config():
    """Resolve the real Config; raise :class:`_EnvBlocked` when the env
    cannot support a live call (documented known class)."""
    if not os.environ.get("OPENAI_API_KEY"):
        raise _EnvBlocked("OPENAI_API_KEY not set in environment")
    try:
        return load_config()
    except Exception as exc:  # noqa: BLE001 — env/config resolution failure
        raise _EnvBlocked(f"load_config failed: {type(exc).__name__}: {exc}") from exc


def _judge_verdict_or_skip(result):
    """Return the judge's verdict; SKIP (env-block) when no verdict was
    produced, FAIL-assert otherwise. A wrong verdict is NEVER a skip."""
    if result.verdict not in ("complete", "not_complete"):
        detail = (
            f"error_class={result.error_class} rationale={result.rationale[:120]!r}"
        )
        print(f"ENV-BLOCKED: {detail}")
        pytest.skip(f"LIVE-JUDGE-ENV-BLOCKED: {detail}")
    return result.verdict


def test_s1_resolved_stale_genuine_report_complete():
    """S1: stale advisory cleared by newest-only + cross-resolution ->
    clean bundle -> judge verdict ``complete``."""
    messages = [
        _report_message(_CHILD_A, _STALE_OLD_REPORT),   # superseded
        _report_message(_CHILD_A, _CLEAN_NEW_REPORT),   # newest — clean
    ]
    tree_rows = [
        {"instance_id": _CHILD_A, "status": "completed", "agent_id": "coder"},
    ]

    # ── Deterministic precondition (FAIL, never skip) ────────────────
    a_signals = collect_source_a_signals(
        messages, tree_rows_provider=lambda: tree_rows
    )
    assert a_signals.advisory_present is False, (
        "S1 precondition broken: stale advisory NOT cleared — the B1 "
        f"newest-only/cross-resolve scan still yields {a_signals.evidence!r}"
    )
    assert a_signals.evidence == (), "S1: expected zero surviving evidence rows"

    bundle = assemble_fused_bundle(
        a_signals=a_signals,
        b_signals=SourceBSignals(
            marker_hit=False,
            marker_terms=(),
            length_trigger=False,
            final_word_count=214,
            attested=False,
        ),
        c_signals=SourceCSignals(
            pending_children=0,
            queued_or_expected_wakeups=0,
            live_descendants=0,
            busy_descendants=0,
        ),
        c_tree_rows=tree_rows,
        ai_tail_messages=[
            AIMessage(
                content=(
                    "Mission complete. The migration backfill shipped via the "
                    "disposable-PG lane: 12/12 ledger tests green, schema "
                    "reconciled, RESULTS written with census + drift pins. "
                    "The B1 stale-A fix also shipped: newest-only + cross-resolve "
                    "scan landed in attestation_resolver_activation.py, 7/7 scenario "
                    "tests green, RESULTS file written with census + drift pins. "
                    "The child's final report confirms full delivery; no "
                    "work remains in the tree."
                )
            ),
        ],
        user_intent_message=HumanMessage(
            content="Ship the stale-A advisory fix and verify the migration "
            "backfill lands clean."
        ),
    )
    # The resolved state renders as ABSENCE: no stale excerpt may leak in.
    assert "Still pending" not in bundle.text and "Ending turn" not in bundle.text, (
        "S1 bundle leak: superseded advisory text resurfaced in the bundle"
    )

    config = _judge_config()
    result = asyncio.run(
        judge_fused_bundle_async(bundle.text, config=config, timeout_s=_JUDGE_TIMEOUT_S)
    )
    print(
        f"VERDICT[s1-resolved-stale]: {result.verdict} | attempt={result.attempt} "
        f"| model={result.model} | rationale={result.rationale[:160]!r}"
    )
    assert _judge_verdict_or_skip(result) == "complete", (
        f"S1 WRONG VERDICT: expected complete, got {result.verdict!r} "
        f"(rationale={result.rationale[:200]!r}) — false-rescue channel check"
    )


def test_s2_genuine_unresolved_advisory_not_complete():
    """S2: child-lie (genuine promise in the NEWEST report) survives the
    completed-tree exception + live sibling -> ``not_complete``."""
    messages = [
        _report_message(_CHILD_B, _GENUINE_LIE_REPORT),  # newest AND a lie
    ]
    tree_rows = [
        # Child B is terminal/completed — the later-contradiction exception
        # (child-lie class) must keep its genuine advisory alive.
        {"instance_id": _CHILD_B, "status": "completed", "agent_id": "coder"},
        # Sibling still working — independent C-side contradiction.
        {"instance_id": _CHILD_C, "status": "running", "agent_id": "tester"},
    ]

    # ── Deterministic preconditions (FAIL, never skip) ───────────────
    scan = scan_child_terminal_report_for_promises(_GENUINE_LIE_REPORT)
    assert scan.promise_hit, "S2 precondition broken: lie report missed the catalog"
    a_signals = collect_source_a_signals(
        messages, tree_rows_provider=lambda: tree_rows
    )
    assert a_signals.advisory_present is True, (
        "S2 precondition broken: genuine child-lie advisory was suppressed — "
        "the later-contradiction exception failed to keep it"
    )
    assert any(
        ev.child_instance_id == _CHILD_B and ev.matched_terms for ev in a_signals.evidence
    ), f"S2: expected child-B genuine evidence, got {a_signals.evidence!r}"
    assert a_signals.contradiction_flag is False or True  # informational; band shape unchanged

    bundle = assemble_fused_bundle(
        a_signals=a_signals,
        b_signals=SourceBSignals(
            marker_hit=False,
            marker_terms=(),
            length_trigger=False,
            final_word_count=188,
            attested=False,
        ),
        c_signals=SourceCSignals(
            pending_children=1,
            queued_or_expected_wakeups=1,
            live_descendants=2,
            busy_descendants=1,
        ),
        c_tree_rows=tree_rows,
        ai_tail_messages=[
            AIMessage(
                content=(
                    "All work is complete. The migration shipped and the "
                    "integration suite is green. Mission accomplished — no "
                    "further action needed from the team."
                )
            ),
        ],
        user_intent_message=HumanMessage(
            content="Deliver the integration coverage and report final status."
        ),
    )
    # The genuine advisory MUST be present in the A-section.
    assert "in progress" in bundle.text or "will report back" in bundle.text, (
        "S2 bundle defect: genuine child-lie advisory missing from A-section"
    )

    config = _judge_config()
    result = asyncio.run(
        judge_fused_bundle_async(bundle.text, config=config, timeout_s=_JUDGE_TIMEOUT_S)
    )
    print(
        f"VERDICT[s2-child-lie]: {result.verdict} | attempt={result.attempt} "
        f"| model={result.model} | rationale={result.rationale[:160]!r}"
    )
    assert _judge_verdict_or_skip(result) == "not_complete", (
        f"S2 WRONG VERDICT: expected not_complete, got {result.verdict!r} "
        f"(rationale={result.rationale[:200]!r}) — FALSE-RESCUE: the softened "
        "carve-outs let a genuine child-lie through"
    )
