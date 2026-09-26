"""FIX-1 acceptance: marker-path deny_bound enforcement (incident 6a0d60c9).

Incident 6a0d60c9 (2026-09-16): leader produced 123 gate evaluations /
115 deny+nudge injections over 27 min with ZERO
``event=leader_completion_gate_terminal_after_bound`` rows anywhere in
the fleet log. Root cause: the marker-path allow→deny conversions in
``daemon/graph.py`` (routes (a) judge-no and (d) judge
timeout/error/unparsable, both with nothing pending) incremented the
deny counter WITHOUT consulting ``deny_bound`` — the bound backstop
existed ONLY on the canonical ``decide()`` step-(6) path, and the
incident leader's every turn-end was a terse "ending turn, awaiting
go/no-go" one-liner with ``attestation_required=False`` — so
``decide()`` allowed early, the marker scan fired, the judge said no,
and the conversion denied + nudged forever.

The fix: the bound predicate is extracted into the SHARED helper
``daemon.services.attestation_gate.deny_bound_exceeded``; ALL THREE
deny producers consult it — ``decide()`` step (6) (unchanged
semantics) plus both marker-path conversion sites. Past the bound the
conversion produces the canonical TERMINAL outcome mirroring
``decide()`` step (6) EXACTLY (``next_denied_count = 0``, no nudge), so
the existing terminal machinery (ledger ``set_escalated_and_reset`` +
the ``leader_completion_gate_terminal_after_bound`` operator event +
plain allow END) runs unchanged.

Acceptance shape (6a0d60c9 loop as regression): synthetic
marker-triggered turn-ends past bound 3 → EXACTLY 3 nudges, then
terminal_after_bound emitted + escalated=true + ZERO further nudges —
covered for BOTH path (a) and path (d).

Harness mirrors ``tests/integration/test_attestation_marker_routing_
lca.py``: production ``create_attestation_gate_node`` closure, stub
manager (no real DB), stub ledger, mocked judge. NO real LLM calls.
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from daemon.services import attestation_report_judge as judge_mod
from daemon.services.attestation_gate import (
    Decision,
    GateSettings,
    build_gate_config,
)
from daemon.services.attestation_judge_resolver import (
    reset_llm_judge_resolver_for_tests,
)
from daemon.services.attestation_resolver import (
    reset_attestation_resolver_for_tests,
)
from daemon.graph import (
    ATTESTATION_ANY_SUBSTANTIVE_KEY,
    create_attestation_gate_node,
)


@pytest.fixture(autouse=True)
def _reset_resolvers(monkeypatch):
    """Fresh resolver cache + clean kill-switch env per test (hermetic)."""
    monkeypatch.delenv(
        "ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED", raising=False
    )
    reset_attestation_resolver_for_tests()
    reset_llm_judge_resolver_for_tests()
    yield
    reset_attestation_resolver_for_tests()
    reset_llm_judge_resolver_for_tests()


def _make_node(
    *,
    instance_id: str,
    denied_count: int,
    llm_judge_enabled: bool = True,
    ledger: MagicMock | None = None,
):
    """Gate node with zeroed manager facades + a getter pinned to
    ``denied_count`` (the incident shape: leader at the bound)."""
    manager = MagicMock()
    manager.count_pending_children.return_value = 0
    manager.get_queued_or_expected_wakeups.return_value = 0
    manager.count_live_descendants.return_value = 0
    manager.count_busy_descendants.return_value = 0

    if ledger is None:
        ledger = MagicMock()
        ledger.increment.return_value = 1
        ledger.reset.return_value = True
        ledger.set_escalated_and_reset.return_value = True
        ledger.get.return_value = 0

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
        denied_count_getter=(lambda: denied_count),
        ledger=ledger,
    )
    return node, manager, ledger


async def _judge_incomplete(config, user_payload, *, timeout_s, system_prompt=None):
    return (
        '{"verdict": "not_complete", "evidence_cited": [], '
        '"advisory_note_text": "", "rationale": "mid-work"}',
        "fake-quick",
    )


async def _judge_timeout(config, user_payload, *, timeout_s, system_prompt=None):
    raise asyncio.TimeoutError()


def _marker_state(final_text: str) -> dict:
    """The 6a0d60c9 turn-end shape (LCA Stage-2 flip re-contract,
    2026-09-16): the original incident mission was NON-delegated —
    under the unified resolver's D10 meta-bypass that shape is plain
    allow (0 LLM; the loop class is closed STRUCTURALLY). The bound
    regression therefore rides the DELEGATED variant — the delegated
    twin of the same terse one-liner with mid-work phrasing."""
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
            AIMessage(content=final_text),
        ]
    }


# ─────────────────────────────────────────────────────────────────────────────
# Path (a) — judge verdict=no, nothing pending, AT the bound
# ─────────────────────────────────────────────────────────────────────────────


def test_path_a_at_bound_escalates_without_nudge(monkeypatch, caplog):
    """Path (a) at ``denied_count == bound`` → TERMINAL, not deny+nudge.

    Pre-fix this conversion produced ``next_denied_count = 3 + 1`` with
    a nudge — the unbounded loop. Post-fix it MUST mirror ``decide()``
    step (6): terminal_after_bound, counter reset via
    ``set_escalated_and_reset``, operator event emitted, ZERO nudge.

    7d4a3bd9 amendment v3 (2026-09-26, user ruling) — RE-CONTRACTED.
    The original pin asserted "at bound → terminal, escalation write"
    for any path-a/marker trip at bound. Under v3 the exhaustion
    composition gate withholds the terminal when the epoch carries
    ZERO substantive verdicts — and on this fixture the bound check
    fires BEFORE the judge plan (budget parity — judge never invoked
    on the exhaustion evaluation), so the epoch is never-spoke by
    construction. To exercise the FIX-1 bound-enforcement path under
    v3 the test seeds the epoch as judge-SPOKE
    (``attestation_any_substantive_deny=True`` in the state), exactly
    as a real epoch reaches bound after a ``not_complete`` deny. The
    terminal stands; the escalation write runs; no nudge. The
    never-spoke shape (the same test with the channel absent) is
    pinned separately in
    ``tests/unit/test_lca_false_complete_fixes.py::TestAllTimeoutArcNeverSpoke``
    — terminal withheld, deny+nudge continues, counter rises past the
    bound. Flagged as **UPDATED BY USER RULING** per the commission.
    """
    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", _judge_incomplete)

    node, _manager, ledger = _make_node(
        instance_id="fix1-path-a-bound", denied_count=3
    )
    state = _marker_state(
        "Awaiting your reply. Ending turn, "
        "will continue after your reply."
    )
    # v3 ruling: seed the judge-spoke epoch so the composition gate
    # does not withhold the terminal.
    state[ATTESTATION_ANY_SUBSTANTIVE_KEY] = True
    with caplog.at_level(logging.INFO, logger="daemon.graph"), caplog.at_level(
        logging.INFO, logger="daemon.services.attestation_gate"
    ), caplog.at_level(
        logging.INFO,
        logger="daemon.services.attestation_resolver_activation",
    ):
        result = asyncio.run(
            node(
                state,
                config={"configurable": {"thread_id": "fix1-path-a-bound"}},
            )
        )

    # NO nudge — the marker-path conversion is bounded now.
    assert "messages" not in result, (
        "FIX-1 path (a): past the bound the conversion MUST NOT inject "
        "a nudge — the loop is unbounded without this"
    )
    assert result.get("attestation_route") is None, (
        "FIX-1 path (a): terminal outcome allows the END (route None)"
    )
    # Canonical terminal machinery ran — set_escalated_and_reset (the
    # ``completion_gate_escalated=true`` write) + the operator event.
    ledger.set_escalated_and_reset.assert_called_once()
    assert ledger.set_escalated_and_reset.call_args.args[0] == (
        "fix1-path-a-bound"
    )
    assert "event=leader_completion_gate_terminal_after_bound" in caplog.text, (
        "FIX-1 path (a): the terminal operator event MUST be emitted — "
        "the incident had ZERO such rows in the whole fleet log"
    )
    # LCA Stage-2 flip (2026-09-16): the at-bound TERMINAL decision gets
    # NO judge call (budget parity with the legacy would-be-deny path —
    # the terminal machinery runs untouched); the resolver row records
    # the terminal outcome.
    assert any(
        "resolver_outcome=terminal_after_bound" in r.getMessage()
        for r in caplog.records
    ), "FIX-1 path (a): the resolver row MUST record the terminal outcome"
    # No deny-side counter increment ran.
    ledger.increment.assert_not_called()


def test_path_a_below_bound_still_denies(monkeypatch):
    """Sanity: below the bound the (a) conversion still denies + nudges.

    The fix must not premature-escalate: at ``denied_count == 2`` with
    bound 3 the conversion is still a deny (counter 2 + 1 = 3 is NOT
    past the bound — ``2 + 1 > 3`` is False).
    """
    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", _judge_incomplete)

    node, _manager, ledger = _make_node(
        instance_id="fix1-path-a-below", denied_count=2
    )
    result = asyncio.run(
        node(
            _marker_state("Ending turn, will continue after your reply."),
            config={"configurable": {"thread_id": "fix1-path-a-below"}},
        )
    )
    assert "messages" in result, "below bound: deny + nudge MUST still fire"
    assert result["attestation_route"] == "agent"
    ledger.increment.assert_called_once()
    ledger.set_escalated_and_reset.assert_not_called()


def test_path_a_exactly_three_nudges_then_terminal(monkeypatch, caplog):
    """THE 6a0d60c9 regression shape: EXACTLY 3 nudges, then terminal,
    then ZERO further nudges.

    Four consecutive marker-triggered turn-ends with the counter
    climbing 0 → 1 → 2 → 3: the first three deny+nudge; the fourth
    (3 + 1 > 3) escalates. No fifth nudge exists anywhere.

    7d4a3bd9 amendment v3 (2026-09-26, user ruling) — RE-CONTRACTED.
    The original pin asserted that at-bound path-a escalates with a
    zero-substantive-verdict epoch. Under v3 the composition gate
    withholds the terminal in that case. To preserve the 6a0d60c9
    acceptance (the FIX-1 bound-enforcement guarantee) the test seeds
    each iteration's epoch as judge-SPOKE — the first three denies
    stamp the substantive channel via the judge mock, and the
    checkpointed state carries the channel forward (mirroring real
    LangGraph state). The terminal stands on the 4th iteration; the
    never-spoke shape is pinned separately in
    ``tests/unit/test_lca_false_complete_fixes.py``. Flagged as
    **UPDATED BY USER RULING** per the commission.
    """
    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", _judge_incomplete)

    results = []
    channel_state: dict = {}
    for denied_count in (0, 1, 2, 3):
        node, _manager, ledger = _make_node(
            instance_id="fix1-loop-shape", denied_count=denied_count
        )
        state = _marker_state("Ending turn, awaiting go/no-go.")
        state.update(channel_state)
        with caplog.at_level(logging.INFO, logger="daemon.graph"):
            result = asyncio.run(
                node(
                    state,
                    config={
                        "configurable": {
                            "thread_id": "fix1-loop-shape"
                        }
                    },
                )
            )
            # Carry the substantive channel forward across iterations
            # (real LangGraph checkpoint semantics).
            if ATTESTATION_ANY_SUBSTANTIVE_KEY in result:
                channel_state[ATTESTATION_ANY_SUBSTANTIVE_KEY] = result[
                    ATTESTATION_ANY_SUBSTANTIVE_KEY
                ]
            results.append((result, ledger))

    # Turns 1-3: deny + nudge (route back to agent).
    for i, (result, ledger) in enumerate(results[:3]):
        assert "messages" in result, f"turn {i + 1}: nudge expected"
        assert result["attestation_route"] == "agent"
        ledger.set_escalated_and_reset.assert_not_called()
    # Turn 4: terminal — no nudge, escalation fired.
    result4, ledger4 = results[3]
    assert "messages" not in result4, (
        "turn 4: past the bound — ZERO further nudges"
    )
    assert result4.get("attestation_route") is None
    ledger4.set_escalated_and_reset.assert_called_once()
    # Total nudge count across the whole synthetic loop: EXACTLY 3.
    total_nudges = sum(
        1 for result, _ in results if "messages" in result
    )
    assert total_nudges == 3, (
        f"6a0d60c9 acceptance: EXACTLY 3 nudges then terminal — "
        f"got {total_nudges}"
    )
    assert caplog.text.count(
        "event=leader_completion_gate_terminal_after_bound"
    ) == 1


# ─────────────────────────────────────────────────────────────────────────────
# Path (d) — judge timeout / wrapper fault, nothing pending, AT the bound
# ─────────────────────────────────────────────────────────────────────────────


def test_path_d_timeout_at_bound_escalates_without_nudge(monkeypatch, caplog):
    """Path (d) via judge TIMEOUT at the bound → TERMINAL, no nudge.

    7d4a3bd9 amendment v3 (2026-09-26, user ruling) — RE-CONTRACTED.
    The original pin asserted "at bound → terminal" for a path-d
    timeout. Under v3 the composition gate withholds the terminal when
    the epoch carries ZERO substantive verdicts — and a TIMEOUT never
    speaks, so this fixture is the never-spoke case by construction.
    To exercise the FIX-1 bound-enforcement path under v3 the test
    seeds the epoch as judge-SPOKE
    (``attestation_any_substantive_deny=True`` in the state), so the
    composition gate does not withhold. The terminal stands; the
    escalation write runs; no nudge. The never-spoke timeout path is
    pinned in ``tests/unit/test_lca_false_complete_fixes.py``. Flagged
    as **UPDATED BY USER RULING** per the commission."""
    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", _judge_timeout)

    node, _manager, ledger = _make_node(
        instance_id="fix1-path-d-timeout", denied_count=3
    )
    state = _marker_state("Ending turn, awaiting your reply.")
    state[ATTESTATION_ANY_SUBSTANTIVE_KEY] = True
    with caplog.at_level(logging.INFO, logger="daemon.graph"):
        result = asyncio.run(
            node(
                state,
                config={
                    "configurable": {"thread_id": "fix1-path-d-timeout"}
                },
            )
        )

    assert "messages" not in result, (
        "FIX-1 path (d) timeout: past the bound — no nudge"
    )
    assert result.get("attestation_route") is None
    ledger.set_escalated_and_reset.assert_called_once()
    assert "event=leader_completion_gate_terminal_after_bound" in caplog.text
    ledger.increment.assert_not_called()


def test_path_d_wrapper_fault_at_bound_escalates_without_nudge(
    monkeypatch, caplog
):
    """Path (d) via judge WRAPPER FAULT at the bound → TERMINAL, no nudge.

    The defense-in-depth ``except`` around the marker-path judge call
    routes conservatively per the SAME R2 inputs — and that
    conservative route now consults the shared bound predicate too.

    7d4a3bd9 amendment v3 (2026-09-26, user ruling) — RE-CONTRACTED.
    Same fix as ``test_path_d_timeout_at_bound_escalates_without_nudge``:
    the original pin asserted "at bound → terminal" for a path-d
    wrapper fault; under v3 the composition gate withholds the terminal
    in the never-spoke case (wrapper fault never speaks). Test seeds
    the epoch as judge-SPOKE so the FIX-1 bound-enforcement contract
    holds. The never-spoke wrapper-fault path is pinned separately.
    Flagged as **UPDATED BY USER RULING** per the commission.
    """
    async def _wrapper_fault(bundle_text, *, config, timeout_s=None):
        raise RuntimeError("wrapper fault at public entry-point")

    monkeypatch.setattr(
        judge_mod, "judge_fused_bundle_async", _wrapper_fault
    )

    node, _manager, ledger = _make_node(
        instance_id="fix1-path-d-fault", denied_count=3
    )
    state = _marker_state("Ending turn, awaiting your reply.")
    state[ATTESTATION_ANY_SUBSTANTIVE_KEY] = True
    with caplog.at_level(logging.INFO, logger="daemon.graph"):
        result = asyncio.run(
            node(
                state,
                config={
                    "configurable": {"thread_id": "fix1-path-d-fault"}
                },
            )
        )

    assert "messages" not in result, (
        "FIX-1 path (d) wrapper fault: past the bound — no nudge"
    )
    assert result.get("attestation_route") is None
    ledger.set_escalated_and_reset.assert_called_once()
    assert "event=leader_completion_gate_terminal_after_bound" in caplog.text


def test_path_d_below_bound_wrapper_fault_still_denies(monkeypatch):
    """Sanity: below the bound the (d) wrapper-fault route still denies."""
    async def _wrapper_fault(bundle_text, *, config, timeout_s=None):
        raise RuntimeError("wrapper fault at public entry-point")

    monkeypatch.setattr(
        judge_mod, "judge_fused_bundle_async", _wrapper_fault
    )

    node, _manager, ledger = _make_node(
        instance_id="fix1-path-d-below", denied_count=1
    )
    result = asyncio.run(
        node(
            _marker_state("Ending turn, awaiting your reply."),
            config={"configurable": {"thread_id": "fix1-path-d-below"}},
        )
    )
    assert "messages" in result, "below bound: (d) deny + nudge preserved"
    assert result["attestation_route"] == "agent"
    ledger.increment.assert_called_once()
    ledger.set_escalated_and_reset.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# Shared-helper contract — decide() step (6) and the marker sites agree
# ─────────────────────────────────────────────────────────────────────────────


def test_shared_helper_matches_decide_step_6_semantics():
    """The shared predicate IS the decide() step-(6) predicate.

    Boundary semantics: at ``denied_count == bound`` the NEXT deny
    trips the bound (``bound + 1 > bound``); at ``bound - 1`` it does
    not. Both ``decide()`` and the marker conversions consult the one
    helper, so a drift between the two paths is structurally
    impossible.
    """
    from daemon.services.attestation_gate import deny_bound_exceeded

    assert deny_bound_exceeded(3, 3) is True
    assert deny_bound_exceeded(2, 3) is False
    assert deny_bound_exceeded(0, 3) is False
    assert deny_bound_exceeded(5, 3) is True

    # decide() step (6) uses the helper: at the bound → TERMINAL with
    # reset; one below → DENY with increment.
    decision = Decision
    gate = __import__(
        "daemon.services.attestation_gate", fromlist=["decide"]
    )
    terminal = gate.decide(
        attested=False,
        pending_children=0,
        queued_or_expected_wakeups=0,
        live_descendants=0,
        denied_count=3,
        bound=3,
        attestation_required=True,
    )
    assert terminal.decision is decision.TERMINAL_AFTER_BOUND
    assert terminal.next_denied_count == 0
    assert terminal.should_inject_nudge is False

    denied = gate.decide(
        attested=False,
        pending_children=0,
        queued_or_expected_wakeups=0,
        live_descendants=0,
        denied_count=2,
        bound=3,
        attestation_required=True,
    )
    assert denied.decision is decision.DENIED
    assert denied.next_denied_count == 3
    assert denied.should_inject_nudge is True
