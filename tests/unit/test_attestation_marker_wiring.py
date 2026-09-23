"""Gate-level wiring tests for the LCA mid-work marker path (2026-09-11).

Wires the real :func:`create_attestation_gate_node` (the gate's
production closure shape) with a :class:`MagicMock` manager and
patches the existing inline-LLM judge
(:func:`daemon.services.attestation_report_judge._invoke_judge_llm`)
to return canned responses. Verifies the spec contract:

* Markers hit on an ALLOW path → judge runs.
* Markers DON'T hit → no judge call (cost control).
* Attested allow → no scan, no judge (the brief: "attested allow =
  explicit contract, skip").
* Judge verdict + R2 inputs drive the (a)/(b)/(c)/(d) routing:
    - (a) markers + judge-no + nothing pending → CONVERT TO DENY +
      nudge + counter increment;
    - (b) markers + judge-no + real pending → ALLOW + checkpoint-
      durable hint, NO counter, NO deny;
    - (c) markers + judge-yes → ALLOW normally, no nudge, no counter;
    - (d) markers + judge-error/timeout/unparsable → (a)-behavior if
      nothing pending, (b)-behavior otherwise.
* Kill-switch OFF (gate-config flag OR env var) → markers logged, NO
  judge call, gate falls through to plain ALLOW.
* Log fields present on the marker path (marker_hit, marker_terms,
  marker_path, marker_judge_verdict, marker_judge_latency_ms,
  marker_judge_error_class).

The test isolation: each test patches the LLM invoker via
``monkeypatch.setattr`` so no real LLM call is made. The
``is_llm_judge_enabled()`` env-flavored fixture mirrors the wiring
test's autouse pattern.
"""
from __future__ import annotations

import asyncio
import logging
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from daemon.services import (
    attestation_judge_resolver as judge_resolver_mod,
)
from daemon.services import (
    attestation_report_judge as judge_mod,
)
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
    create_attestation_gate_node,
)


# ─────────────────────────────────────────────────────────────────────────────
# Test fixtures + helpers
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_resolvers(monkeypatch):
    """Each test sees a fresh resolver cache so env mutations stick.

    Hermetic kill-switch isolation: clear
    ``ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED`` from the
    environment so the suite is hermetic against a global CI env
    mutation. ``raising=False`` so the fixture is safe on hosts where
    the var is already unset.
    """
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
    instance_id: str = "marker-it",
    llm_judge_enabled: bool = True,
    denied_count_getter=None,
    busy_descendants: int = 0,
    live_descendants: int = 0,
    pending_children: int = 0,
    queued_or_expected_wakeups: int = 0,
) -> tuple:
    """Build the gate node with manager + ledger stubs (no real DB).

    Manager is a plain :class:`MagicMock` — the live ``count_live_descendants``
    BFS over ``repo.get_tree_ids_permanent`` is NOT exercised here. The
    stub returns ``0`` (clean int) on every call, which keeps the
    ``live_descendants != 0`` DB-seam guard at
    ``daemon/services/attestation_gate.py:816`` inert in this suite.
    The BFS shape is covered separately by the manager-side conftest
    fixtures (per the tester-manager conftest pattern); this file
    exercises the gate-node wiring only.

    2026-09-12 LCA busy trigger suppression — ``busy_descendants`` is
    stubbed too (default 0; tests pass an explicit value to exercise
    the suppression branches). The two methods share the SAME private
    BFS helper in the production facade
    (``InstanceManager._count_descendants_busy_and_live``), but this
    stub returns independent ints per method (gate-node wiring only —
    the production helper is covered by the manager-side suite).
    """
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
        llm_judge_enabled=llm_judge_enabled,
    )
    node = create_attestation_gate_node(
        config,
        GateSettings("enforce", 3, 3),
        manager,
        instance_id,
        denied_count_getter=denied_count_getter or (lambda: 0),
        ledger=ledger,
    )
    return node, manager, ledger


# ─────────────────────────────────────────────────────────────────────────────
# Mission-shape helpers — the spec-required (a)/(b)/(c)/(d) inputs
# ─────────────────────────────────────────────────────────────────────────────


def _quick_question_with_marker(final_text: str) -> dict:
    """Spec example: a quick answer that ends with mid-work phrasing.

    No send_message tool call (attestation_required=False branch) — the
    conditional gate OFF path. Per the brief: "a quick answer ending
    '...Ending turn, will continue after your reply' must reach the
    judge." This is the canonical case where the marker scan must run
    even when the conditional gate would normally allow.
    """
    return {
        "messages": [
            HumanMessage(content="what's the answer to X?"),
            AIMessage(content=final_text),
        ]
    }


def _delegated_mission_with_marker(final_text: str) -> dict:
    """Delegated mission ending with mid-work phrasing."""
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


def _delegated_mission_attested() -> dict:
    """Delegated mission WITH attestation in window — must skip scan.

    2026-09-19 attest-first contract: the FINAL AIMessage MUST be a
    standalone text report (no tool calls, >=
    ``SHORT_REPORT_WORD_THRESHOLD`` = 150 words) for the gate to
    allow END via Decision.ALLOWED. The clean attest_call
    (empty content + ``attest_completion`` tool_call) is the
    SECOND-TO-LAST AIMessage; the LONG standalone text report
    is the LAST AIMessage. The OLD "short prose after the
    attest_call" shape retired with the attest-first flip —
    it would now produce Decision.HOLD (the marker scan skip
    test still works because the attested path skips the scan
    regardless of the final-AI length — but the allow-vs-hold
    branch requires the standalone text report shape)."""
    delegation_ai = AIMessage(
        content="",
        tool_calls=[
            {"name": "send_message", "args": {"target": "child-id"}, "id": "c1"}
        ],
    )
    attestation_ai = AIMessage(
        content="",
        tool_calls=[
            {"name": "attest_completion", "args": {}, "id": "a1"}
        ],
    )
    long_report = (
        "The work is finished. All four patches shipped; the "
        "test matrix is green; the integration tests pass on "
        "every environment we maintain. Patch 1 fixed the "
        "off-by-one in the cache TTL calculator; the unit "
        "tests now exercise both the elapsed-second and "
        "wall-clock-second boundaries at the second and "
        "minute granularity. Patch 2 cleaned up the dead "
        "imports in the worker pool module after the "
        "migration, removing the legacy compatibility shim "
        "and the related test scaffolding. Patch 3 refactored "
        "the error-reporting decorator so the stack-frame "
        "metadata is consistent across all four call sites in "
        "the graph node and the manager facade. Patch 4 added "
        "the missing operator-boot log line for the new "
        "resolver module so operators can grep the boot "
        "summary for the resolved effective values. All four "
        "patches passed their respective suites on the first "
        "run with no flake; the integration matrix is green "
        "end-to-end across all environments we maintain. No "
        "follow-ups outstanding; the mission is complete and "
        "ready for review by the next teammate in the chain."
    )
    return {
        "messages": [
            HumanMessage(content="please do it"),
            delegation_ai,
            attestation_ai,
            AIMessage(content=long_report),
        ]
    }


# Stub LLM invoker factories (mirror test_attestation_judge_wiring shape).
#
# LCA Stage-2 flip re-contract (2026-09-16): the node's fused block is
# the ONLY judge path; its LLM seam is the SAME ``_invoke_judge_llm``
# (now carrying ``system_prompt=`` — the fused judge's prompt). The
# stub payloads therefore answer in the FUSED verdict JSON shape
# (``{"verdict": "complete"|"not_complete", "evidence_cited": [],
# "advisory_note_text": "", "rationale": ""}``) — the legacy
# ``is_complete_report`` shape is no longer parsed on this path.


def _caplog_both(caplog):
    """Set up caplog to capture from both daemons (graph + gate
    service) so canonical log rows from ``evaluate()`` and the
    graph-node log lines both surface in tests.
    ``caplog.at_level`` is per-logger. Returns the fixture for use as
    a context manager via ``caplog.at_level(...)``.
    """
    caplog.set_level(logging.INFO, logger="daemon.graph")
    caplog.set_level(
        logging.INFO, logger="daemon.services.attestation_gate"
    )
    caplog.set_level(
        logging.INFO,
        logger="daemon.services.attestation_resolver_activation",
    )
    return caplog


async def _invoke_yes(config, user_payload, *, timeout_s, system_prompt=None):
    return (
        '{"verdict": "complete", "evidence_cited": ["genuine report"], '
        '"advisory_note_text": "", "rationale": "genuine report"}',
        "fake-quick",
    )


async def _invoke_no(config, user_payload, *, timeout_s, system_prompt=None):
    # Minimal not_complete verdict (NO evidence/advisory) so hint-shape
    # tests can pin the byte-identical pre-D4 note; the D4 citation
    # shape is pinned separately in test_attestation_resolver_stage2.
    return (
        '{"verdict": "not_complete", "evidence_cited": [], '
        '"advisory_note_text": "", "rationale": "mid-work status"}',
        "fake-quick",
    )


async def _invoke_unparsable(config, user_payload, *, timeout_s, system_prompt=None):
    return ("Sorry, I cannot help with that.", "fake-quick")


async def _invoke_timeout(config, user_payload, *, timeout_s, system_prompt=None):
    raise asyncio.TimeoutError()


async def _invoke_error(config, user_payload, *, timeout_s, system_prompt=None):
    raise RuntimeError("boom")


# ─────────────────────────────────────────────────────────────────────────────
# (a) markers + judge-no + nothing pending → CONVERT TO DENY + nudge
# ─────────────────────────────────────────────────────────────────────────────


def test_marker_a_deny_nudge_counter_increments(monkeypatch, caplog):
    """(a) markers + judge-no + nothing pending → DENY + nudge + counter+1.

    LCA Stage-2 flip re-contract (2026-09-16): the mission is DELEGATED
    (the unified predicate's D10 meta-bypass exempts non-delegated
    missions from the judge entirely — pinned separately). With nothing
    pending the unified band is DENY (precedence over marker — c_quiet
    wins); the fused judge's not_complete verdict leaves the decide()
    DENIED in place and the existing nudge machinery denies + counts.
    """
    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", _invoke_no)

    node, manager, ledger = _make_node(instance_id="marker-a-it")
    with caplog.at_level(logging.INFO, logger="daemon.graph"), caplog.at_level(
        logging.INFO, logger="daemon.services.attestation_gate"
    ), caplog.at_level(
        logging.INFO,
        logger="daemon.services.attestation_resolver_activation",
    ):
        result = asyncio.run(
            node(
                _delegated_mission_with_marker(
                    "Awaiting your reply. Ending turn, "
                    "will continue after your reply."
                ),
                config={"configurable": {"thread_id": "marker-a-it"}},
            )
        )

    # DENIED — nudge + counter increment.
    assert "messages" in result
    assert result["attestation_route"] == "agent"
    ledger.increment.assert_called_once()
    assert ledger.increment.call_args.args[0] == "marker-a-it"

    # Log fields present — the fused-judge row + the resolver row.
    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "event=leader_completion_gate_fused_judge" in log_text
    assert "verdict=not_complete" in log_text
    assert "resolver_outcome=deny_nudge" in log_text


# ─────────────────────────────────────────────────────────────────────────────
# (b) markers + judge-no + real pending → ALLOW + checkpoint-durable hint
# ─────────────────────────────────────────────────────────────────────────────


def test_marker_b_hint_injection_no_deny_no_counter(monkeypatch, caplog):
    """(b) markers + judge-no + real pending → ALLOW log-only, no deny.

    2026-09-23 (b2f4dae9): the (b)/(d)-with-pending route STILL EXISTS
    on the resolver row (label ``allow_hint``) so operators can
    distinguish it from the plain (c) allow — the
    ``leader_completion_gate_fused_judge`` row carries the verdict +
    the ``would_be_route=allow_hint`` field, the resolver_eval row
    carries ``resolver_outcome=allow_hint``. The Completion Check Note
    hint is RETIRED end-to-end (no message injection, no
    context_kind minting, no stable-id table row). The "real wakeup
    is en route" branch is now a logged decision only — the turn
    still ends (the gate ALLOWS) but the leader carries no checkpoint
    record of the mid-work phrasing.
    """
    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", _invoke_no)

    manager = MagicMock()
    manager.count_pending_children.return_value = 1  # REAL pending
    manager.get_queued_or_expected_wakeups.return_value = 0
    manager.count_live_descendants.return_value = 0
    manager.count_busy_descendants.return_value = 0
    manager.enqueue_message = MagicMock()
    manager.revive = MagicMock()
    manager.send_message = MagicMock()

    ledger = MagicMock()
    ledger.increment.return_value = 1
    ledger.reset.return_value = True
    ledger.set_escalated_and_reset.return_value = True
    ledger.get.return_value = 0

    config = build_gate_config(
        "marker-b-it", GateSettings("enforce", 3, 3),
        llm_judge_enabled=True,
    )
    node = create_attestation_gate_node(
        config,
        GateSettings("enforce", 3, 3),
        manager,
        "marker-b-it",
        denied_count_getter=lambda: 0,
        ledger=ledger,
    )

    with caplog.at_level(logging.INFO, logger="daemon.graph"), caplog.at_level(
        logging.INFO, logger="daemon.services.attestation_gate"
    ), caplog.at_level(
        logging.INFO,
        logger="daemon.services.attestation_resolver_activation",
    ):
        result = asyncio.run(
            node(
                _delegated_mission_with_marker(
                    "Awaiting child reply. Ending turn, will continue."
                ),
                config={"configurable": {"thread_id": "marker-b-it"}},
            )
        )

    # ALLOWED + log-only — 2026-09-23 (b2f4dae9): the Completion Check
    # Note hint is RETIRED end-to-end. The (b) route still resolves to
    # allow on the resolver row (label ``allow_hint``), but NO message
    # is injected, NO context_kind is minted, NO counter movement.
    assert "messages" not in result, (
        "(b) path MUST be log-only after 2026-09-23 retirement; "
        f"got messages={result.get('messages')!r}"
    )
    # NO attestation_route hint → END.
    assert result["attestation_route"] is None
    # NO counter increment — (b) path is allow-log-only, not deny.
    ledger.increment.assert_not_called()
    ledger.reset.assert_not_called()
    ledger.set_escalated_and_reset.assert_not_called()

    # Log fidelity — the resolver row carries the would-be-route label
    # so the (b)/(d)-with-pending path is still observable in logs
    # (the b2f4dae9 evidence chain must remain log-reconstructible).
    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "event=leader_completion_gate_fused_judge" in log_text
    assert "verdict=not_complete" in log_text
    assert "fused-judge not-complete" in log_text
    assert "would_be_route=allow_hint" in log_text
    assert "resolver_outcome=allow_hint" in log_text


# ─────────────────────────────────────────────────────────────────────────────
# (c) markers + judge-yes → ALLOW normally
# ─────────────────────────────────────────────────────────────────────────────


def test_marker_c_judge_yes_allows_normally(monkeypatch, caplog):
    """(c) markers + judge-complete → ALLOW normally, no nudge, no counter.

    LCA Stage-2 flip re-contract (2026-09-16): DELEGATED mission (the
    D10 meta-bypass exempts non-delegated missions); the fused judge's
    complete verdict is the rescue — allow END with zero side effects.
    """
    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", _invoke_yes)

    node, manager, ledger = _make_node(instance_id="marker-c-it")
    with caplog.at_level(logging.INFO, logger="daemon.graph"), caplog.at_level(
        logging.INFO, logger="daemon.services.attestation_gate"
    ), caplog.at_level(
        logging.INFO,
        logger="daemon.services.attestation_resolver_activation",
    ):
        result = asyncio.run(
            node(
                _delegated_mission_with_marker(
                    "Ending turn. All four sub-tasks completed; "
                    "evidence in the per-task report above."
                ),
                config={"configurable": {"thread_id": "marker-c-it"}},
            )
        )

    # ALLOWED — no nudge, no counter, no hint.
    assert "messages" not in result
    assert result["attestation_route"] is None
    ledger.increment.assert_not_called()
    ledger.reset.assert_not_called()
    ledger.set_escalated_and_reset.assert_not_called()

    # Log fields present.
    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "event=leader_completion_gate_fused_judge" in log_text
    assert "verdict=complete" in log_text
    # Nothing pending + delegated ⇒ unified deny band ⇒ the complete
    # verdict is the RESCUE arm (mirrors the legacy judge-yes override).
    assert "fused-judge rescue" in log_text


# ─────────────────────────────────────────────────────────────────────────────
# (d1) markers + judge-error + nothing pending → DENY (path "d")
# ─────────────────────────────────────────────────────────────────────────────


def test_marker_d_error_with_nothing_pending_deny(monkeypatch, caplog):
    """(d) markers + judge-error/timeout/unparsable + nothing pending → DENY.

    Conservative fall-through — a judge infrastructure error stays on
    the conservative side (deny) when nothing is pending. LCA Stage-2
    flip re-contract (2026-09-16): DELEGATED mission; with nothing
    pending the unified band is DENY and the errored fused-judge call
    leaves the decide() DENIED in place (path-(d)-exact, DP-5
    REJECTED — no fail-safe allow).
    """
    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", _invoke_error)

    node, manager, ledger = _make_node(instance_id="marker-d1-it")
    with caplog.at_level(logging.INFO, logger="daemon.graph"), caplog.at_level(
        logging.INFO, logger="daemon.services.attestation_gate"
    ), caplog.at_level(
        logging.INFO,
        logger="daemon.services.attestation_resolver_activation",
    ):
        result = asyncio.run(
            node(
                _delegated_mission_with_marker(
                    "Awaiting reply. Ending turn."
                ),
                config={"configurable": {"thread_id": "marker-d1-it"}},
            )
        )

    # DENIED — counter increment + nudge.
    assert "messages" in result
    assert result["attestation_route"] == "agent"
    ledger.increment.assert_called_once()

    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "verdict=error" in log_text
    assert "resolver_outcome=deny_nudge" in log_text


# ─────────────────────────────────────────────────────────────────────────────
# (d2) markers + judge-error + real pending → ALLOW + hint
# ─────────────────────────────────────────────────────────────────────────────


def test_marker_d_timeout_with_real_pending_hint(monkeypatch, caplog):
    """(d) markers + judge-timeout + real pending → ALLOW + hint.

    The wake-up is en route; the judge errored; the gate falls
    through to allow-with-hint so the wake-up can still arrive.
    """
    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", _invoke_timeout)

    manager = MagicMock()
    manager.count_pending_children.return_value = 0
    manager.get_queued_or_expected_wakeups.return_value = 1  # wakeup
    manager.count_live_descendants.return_value = 0
    manager.count_busy_descendants.return_value = 0
    manager.enqueue_message = MagicMock()
    manager.revive = MagicMock()
    manager.send_message = MagicMock()

    ledger = MagicMock()
    ledger.increment.return_value = 1
    ledger.reset.return_value = True
    ledger.set_escalated_and_reset.return_value = True
    ledger.get.return_value = 0

    config = build_gate_config(
        "marker-d2-it", GateSettings("enforce", 3, 3),
        llm_judge_enabled=True,
    )
    node = create_attestation_gate_node(
        config,
        GateSettings("enforce", 3, 3),
        manager,
        "marker-d2-it",
        denied_count_getter=lambda: 0,
        ledger=ledger,
    )

    with caplog.at_level(logging.INFO, logger="daemon.graph"), caplog.at_level(
        logging.INFO, logger="daemon.services.attestation_gate"
    ), caplog.at_level(
        logging.INFO,
        logger="daemon.services.attestation_resolver_activation",
    ):
        result = asyncio.run(
            node(
                _delegated_mission_with_marker(
                    "Awaiting child. Ending turn."
                ),
                config={"configurable": {"thread_id": "marker-d2-it"}},
            )
        )

    # ALLOWED + log-only — 2026-09-23 (b2f4dae9): Completion Check Note
    # hint RETIRED end-to-end; (d2) is now allow-with-no-side-effect.
    assert "messages" not in result, (
        "(d2) path MUST be log-only after 2026-09-23 retirement; "
        f"got messages={result.get('messages')!r}"
    )
    assert result["attestation_route"] is None
    ledger.increment.assert_not_called()

    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "verdict=timeout" in log_text
    assert "fused-judge path-d" in log_text
    assert "would_be_route=allow_hint" in log_text


# ─────────────────────────────────────────────────────────────────────────────
# (d3) markers + judge-unparsable + nothing pending → DENY (path "d")
# ─────────────────────────────────────────────────────────────────────────────


def test_marker_d_unparsable_with_nothing_pending_deny(monkeypatch, caplog):
    """(d) markers + judge-unparsable + nothing pending → DENY.

    Unparsable JSON is a judge infrastructure error (the LLM did not
    return a strict JSON object). Conservative fall-through to deny.
    LCA Stage-2 flip re-contract (2026-09-16): DELEGATED mission; the
    retry-once fired inside the ONE fused invocation and BOTH attempts
    were unparsable (``attempt=2``) — the deny stands (path-(d)-exact).
    """
    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", _invoke_unparsable)

    node, manager, ledger = _make_node(instance_id="marker-d3-it")
    with caplog.at_level(logging.INFO, logger="daemon.graph"), caplog.at_level(
        logging.INFO, logger="daemon.services.attestation_gate"
    ), caplog.at_level(
        logging.INFO,
        logger="daemon.services.attestation_resolver_activation",
    ):
        result = asyncio.run(
            node(
                _delegated_mission_with_marker(
                    "Awaiting reply. Ending turn."
                ),
                config={"configurable": {"thread_id": "marker-d3-it"}},
            )
        )

    # DENIED — counter increment + nudge.
    assert "messages" in result
    assert result["attestation_route"] == "agent"
    ledger.increment.assert_called_once()

    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "verdict=unparsable" in log_text
    assert "llm_judge_attempt=2" in log_text
    assert "resolver_outcome=deny_nudge" in log_text


# ─────────────────────────────────────────────────────────────────────────────
# Wrapper-fault path — judge_fused_bundle_async RAISES (the fused block's
# except branch must still route conservatively). The d1/d2/d3 tests above
# cover the NEVER-RAISES judge path (error/timeout/unparsable all return
# FusedJudgeResult with is_complete=False). The tests below cover the
# wrapper-layer fault class — the fused judge entry point ITSELF raises
# (config-load failure, import-time cycle, etc.).
# ─────────────────────────────────────────────────────────────────────────────


async def _fused_judge_wrapper_fault(bundle_text, *, config, timeout_s=None):
    """Simulate a wrapper-layer fault in judge_fused_bundle_async.

    The fused judge service is never-raises (timeout / unparsable /
    error all return FusedJudgeResult). This stub instead RAISES at
    the public entry-point — the fused block's wrapper-layer except
    branch in graph.py must still route conservatively based on the
    band + R2 inputs.
    """
    raise RuntimeError("wrapper-layer fault (config load failure)")


def test_marker_wrapper_fault_with_nothing_pending_deny(
    monkeypatch, caplog
):
    """Wrapper fault + nothing pending → CONVERT TO DENY (path "d").

    The F2 fix (re-contracted to the fused seam, 2026-09-16) — when
    ``judge_fused_bundle_async`` itself RAISES, the fused block must
    NOT silently ALLOW. With nothing pending (no children / wakeups /
    live descendants) the unified band is DENY and the conservative
    routing is deny+nudge + counter increment via the existing
    machinery. DELEGATED mission (the D10 meta-bypass exempts
    non-delegated missions from the judge entirely).
    """
    monkeypatch.setattr(
        judge_mod,
        "judge_fused_bundle_async",
        _fused_judge_wrapper_fault,
    )

    node, manager, ledger = _make_node(instance_id="marker-wf1-it")
    with caplog.at_level(logging.INFO, logger="daemon.graph"), caplog.at_level(
        logging.INFO, logger="daemon.services.attestation_gate"
    ), caplog.at_level(
        logging.INFO,
        logger="daemon.services.attestation_resolver_activation",
    ):
        result = asyncio.run(
            node(
                _delegated_mission_with_marker(
                    "Awaiting reply. Ending turn."
                ),
                config={"configurable": {"thread_id": "marker-wf1-it"}},
            )
        )

    # DENIED — counter increment + nudge.
    assert "messages" in result
    assert result["attestation_route"] == "agent"
    ledger.increment.assert_called_once()
    assert ledger.increment.call_args.args[0] == "marker-wf1-it"

    # Log rows: the existing judge-error row + the (d)-path routing
    # line (from the wrapper-fault path, not the post-call path).
    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert (
        "event=leader_completion_gate_fused_judge_error" in log_text
    ), "F2: the wrapper-fault path MUST emit the canonical error row"
    assert "decision=fail_safe_conservative" in log_text
    assert "resolver_outcome=deny_nudge" in log_text


def test_marker_wrapper_fault_with_real_pending_hint(monkeypatch, caplog):
    """Wrapper fault + real pending work → ALLOW + hint (path "d").

    The wake-up is en route; the judge wrapper faulted; the gate falls
    through to allow-with-hint so the wake-up can still arrive. NO
    counter write, NO deny, NO re-route — the turn still ends. Pins
    the F2 P0 close on the (b)-shape half of the conservative routing
    (re-contracted to the fused seam, 2026-09-16).
    """
    monkeypatch.setattr(
        judge_mod,
        "judge_fused_bundle_async",
        _fused_judge_wrapper_fault,
    )

    manager = MagicMock()
    manager.count_pending_children.return_value = 1  # REAL pending
    manager.get_queued_or_expected_wakeups.return_value = 0
    manager.count_live_descendants.return_value = 0
    manager.count_busy_descendants.return_value = 0
    manager.enqueue_message = MagicMock()
    manager.revive = MagicMock()
    manager.send_message = MagicMock()

    ledger = MagicMock()
    ledger.increment.return_value = 1
    ledger.reset.return_value = True
    ledger.set_escalated_and_reset.return_value = True
    ledger.get.return_value = 0

    config = build_gate_config(
        "marker-wf2-it", GateSettings("enforce", 3, 3),
        llm_judge_enabled=True,
    )
    node = create_attestation_gate_node(
        config,
        GateSettings("enforce", 3, 3),
        manager,
        "marker-wf2-it",
        denied_count_getter=lambda: 0,
        ledger=ledger,
    )

    with caplog.at_level(logging.INFO, logger="daemon.graph"), caplog.at_level(
        logging.INFO, logger="daemon.services.attestation_gate"
    ), caplog.at_level(
        logging.INFO,
        logger="daemon.services.attestation_resolver_activation",
    ):
        result = asyncio.run(
            node(
                _delegated_mission_with_marker(
                    "Awaiting child. Ending turn."
                ),
                config={"configurable": {"thread_id": "marker-wf2-it"}},
            )
        )

    # ALLOWED + log-only — 2026-09-23 (b2f4dae9): the Completion Check
    # Note hint is RETIRED end-to-end. The wrapper-fault row still
    # fires (the (b)/(d)-with-pending route exists in logs only);
    # NO message is injected.
    assert "messages" not in result, (
        "(d) wrapper-fault path MUST be log-only after 2026-09-23; "
        f"got messages={result.get('messages')!r}"
    )
    assert result["attestation_route"] is None
    ledger.increment.assert_not_called()
    ledger.reset.assert_not_called()
    ledger.set_escalated_and_reset.assert_not_called()

    # Log rows: the wrapper-fault error row + the (d)-path routing
    # line via the hint path.
    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert (
        "event=leader_completion_gate_fused_judge_error" in log_text
    )
    assert "decision=fail_safe_conservative" in log_text
    assert "resolver_outcome=allow_hint" in log_text


# ─────────────────────────────────────────────────────────────────────────────
# Kill-switch OFF — markers logged, NO judge call, gate falls through to allow
# ─────────────────────────────────────────────────────────────────────────────


def test_marker_kill_switch_env_off_no_judge_call(monkeypatch, caplog):
    """Kill-switch OFF → markers logged, NO judge call, plain ALLOW.

    LCA Stage-2 flip re-contract (2026-09-16): the D10 meta-bypass
    already exempts non-delegated missions from the fused judge, so
    under the flip the kill-switch OFF row is reached only on a
    DELEGATED marker mission. We drive a delegated mission with REAL
    pending work (marker band — the un-attested∧quiet shape is the
    deny band, where kill-switch-off DENIES per Q1 parity, pinned in
    test_attestation_resolver_stage2) so the kill-switch OFF branch is
    the actual guard; the same row surface is preserved end-to-end.
    """
    calls = []

    async def must_not_be_called(config, user_payload, *, timeout_s, system_prompt=None):
        calls.append(True)
        return ('{"verdict": "complete"}', "fake-quick")

    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", must_not_be_called)
    # Real env var + real resolver reset.
    monkeypatch.setenv("ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED", "0")
    judge_resolver_mod.reset_llm_judge_resolver_for_tests()
    # Sanity: the real resolver returns False.
    assert judge_resolver_mod.is_llm_judge_enabled() is False

    node, manager, ledger = _make_node(
        instance_id="marker-ks-off-it", pending_children=1
    )
    with caplog.at_level(logging.INFO, logger="daemon.graph"), caplog.at_level(
        logging.INFO, logger="daemon.services.attestation_gate"
    ), caplog.at_level(
        logging.INFO,
        logger="daemon.services.attestation_resolver_activation",
    ):
        result = asyncio.run(
            node(
                _delegated_mission_with_marker(
                    "Awaiting reply. Ending turn."
                ),
                config={"configurable": {"thread_id": "marker-ks-off-it"}},
            )
        )

    # Judge never called.
    assert calls == []
    # Plain ALLOW — no nudge, no counter, no hint.
    assert "messages" not in result
    assert result["attestation_route"] is None
    ledger.increment.assert_not_called()
    ledger.reset.assert_not_called()

    # The canonical gate log row still carries marker_hit=True (so the
    # operator sees the hit in dry-log soak); the kill-switch OFF
    # branch emits the fused disabled row
    # (``event=leader_completion_gate_fused_judge_disabled`` with
    # ``verdict=<skipped>``) — the fused-judge row MUST NOT fire (no
    # invocation). Pin both: the disabled row IS emitted, the
    # live-judge row IS NOT.
    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "marker_hit=True" in log_text
    assert (
        "event=leader_completion_gate_fused_judge_disabled" in log_text
    )
    assert (
        "event=leader_completion_gate_fused_judge " not in log_text
        and "event=leader_completion_gate_fused_judge\n" not in log_text
    )


def test_marker_kill_switch_config_off_no_judge_call(monkeypatch):
    """``llm_judge_enabled=False`` in gate_config → judge never called.

    Same kill-switch behavior driven by the gate-config flag (not the
    env var). Env var is the operator-tunable seam; the gate-config
    flag is the wiring-layer seam (test embeddings flip this to bypass
    the real call without touching the env).

    LCA Stage-2 flip re-contract (2026-09-16): DELEGATED mission so
    the kill-switch off branch is the actual guard (the D10
    meta-bypass exempts non-delegated missions before the kill-switch
    check).
    """
    calls = []

    async def must_not_be_called(config, user_payload, *, timeout_s, system_prompt=None):
        calls.append(True)
        return ('{"verdict": "complete"}', "fake-quick")

    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", must_not_be_called)

    node, manager, ledger = _make_node(
        instance_id="marker-ks-cfg-off-it",
        llm_judge_enabled=False,
        pending_children=1,
    )
    result = asyncio.run(
        node(
            _delegated_mission_with_marker(
                "Awaiting reply. Ending turn."
            ),
            config={"configurable": {"thread_id": "marker-ks-cfg-off-it"}},
        )
    )

    # Judge never called — plain ALLOW.
    assert calls == []
    assert "messages" not in result
    assert result["attestation_route"] is None


# ─────────────────────────────────────────────────────────────────────────────
# No markers → NO judge call (cost control)
# ─────────────────────────────────────────────────────────────────────────────


def test_no_markers_no_judge_call(monkeypatch, caplog):
    """No markers AND no length trigger in the message tail → no judge
    call, plain ALLOW.

    The marker scan is the TRIGGER; no marker ⇒ no judge. The length
    trigger (2026-09-12) is the OR-composed second half — no length
    trigger (the AIMessage is ≥150 words) ⇒ no judge either. This is
    the cost-control guard — the cheap scans save the expensive
    judge call for ~all completion turns that don't include mid-work
    phrasing AND are sufficiently detailed.
    """
    calls = []

    async def must_not_be_called(config, user_payload, *, timeout_s, system_prompt=None):
        calls.append(True)
        return ('{"verdict": "complete"}', "fake-quick")

    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", must_not_be_called)

    # Long, detailed completion report — no marker phrases AND >= 150
    # words so the length trigger does NOT fire either. The cheap
    # allow path is taken end-to-end.
    long_report = (
        "The answer is 42; explanation follows in the sections below. "
        "Nothing pending, all shipped. Patch 1 fixed the off-by-one in "
        "the cache TTL calculator; the unit tests now exercise both the "
        "elapsed-second and wall-clock-second boundaries at the second "
        "and minute granularity. Patch 2 cleaned up the dead imports in "
        "the worker pool module after the migration, removing the legacy "
        "compatibility shim and the related test scaffolding. Patch 3 "
        "refactored the error-reporting decorator so the stack-frame "
        "metadata is consistent across all four call sites in the graph "
        "node and the manager facade. Patch 4 added the missing "
        "operator-boot log line for the new resolver module so operators "
        "can grep the boot summary for the resolved effective values. "
        "All four patches passed their respective suites on the first "
        "run with no flake; the integration matrix is green end-to-end "
        "across all environments we maintain. No follow-ups outstanding; "
        "the mission is complete and ready for review by the next "
        "teammate in the chain. The release notes draft is staged on "
        "the docs branch with the per-patch rationale paragraphs and "
        "the cross-references to the upstream incident reports."
    )

    node, manager, ledger = _make_node(instance_id="marker-none-it")
    with caplog.at_level(logging.INFO, logger="daemon.graph"), caplog.at_level(
        logging.INFO, logger="daemon.services.attestation_gate"
    ), caplog.at_level(
        logging.INFO,
        logger="daemon.services.attestation_resolver_activation",
    ):
        result = asyncio.run(
            node(
                _quick_question_with_marker(long_report),
                config={"configurable": {"thread_id": "marker-none-it"}},
            )
        )

    # Judge never called.
    assert calls == []
    # Plain ALLOW — no nudge, no counter, no hint.
    assert "messages" not in result
    assert result["attestation_route"] is None
    ledger.increment.assert_not_called()
    ledger.reset.assert_not_called()

    # marker_hit AND length_trigger both False in the canonical gate
    # log row — NEITHER trigger half fired.
    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "marker_hit=False" in log_text
    assert "length_trigger=False" in log_text
    # Stage 3 (R5/R6): the trigger-derivation fields retired.
    assert "trigger_source=" not in log_text


# ─────────────────────────────────────────────────────────────────────────────
# Attested allow → skip the marker scan (attested = explicit contract)
# ─────────────────────────────────────────────────────────────────────────────


def test_attested_allow_skips_marker_scan(monkeypatch):
    """Attested allow → no scan, no judge (attested is an explicit contract).

    The brief: "scan runs when an allow would fire AND NOT attested
    (attested allow = explicit contract, skip)." An attested mission
    ending with mid-work phrasing is allowed immediately — the scan
    never runs, no judge, no marker fields populated.
    """
    calls = []

    async def must_not_be_called(config, user_payload, *, timeout_s, system_prompt=None):
        calls.append(True)
        return ('{"verdict": "complete"}', "fake-quick")

    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", must_not_be_called)

    node, manager, ledger = _make_node(instance_id="marker-att-it")
    result = asyncio.run(
        node(
            _delegated_mission_attested(),
            config={"configurable": {"thread_id": "marker-att-it"}},
        )
    )

    # Judge never called.
    assert calls == []
    # ALLOWED — attested allow; counter reset (trigger 1).
    assert "messages" not in result
    assert result["attestation_route"] is None
    ledger.reset.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# Log fields — additive marker schema fields on every gate log row
# ─────────────────────────────────────────────────────────────────────────────


def test_log_marker_fields_present_on_marker_a_path(monkeypatch, caplog):
    """Log fields (marker_hit / marker_terms / marker_path / verdict /
    latency_ms / error_class) are all present on the marker-(a) path.

    LCA Stage-2 flip re-contract (2026-09-16): DELEGATED mission (the
    D10 meta-bypass exempts non-delegated missions from the judge).
    The canonical ``event=leader_completion_gate`` log row still
    carries ``marker_path=<pending>`` (the gate's marker stamp is
    unchanged); the judge fields now ride the FUSED judge row
    (``event=leader_completion_gate_fused_judge``) and the resolver
    row carries the authoritative ``resolver_outcome=``.
    """
    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", _invoke_no)

    node, manager, ledger = _make_node(
        instance_id="marker-log-a-it", pending_children=1
    )
    with caplog.at_level(logging.INFO, logger="daemon.graph"), caplog.at_level(
        logging.INFO, logger="daemon.services.attestation_gate"
    ), caplog.at_level(
        logging.INFO,
        logger="daemon.services.attestation_resolver_activation",
    ):
        asyncio.run(
            node(
                _delegated_mission_with_marker(
                    "Awaiting your reply. Ending turn."
                ),
                config={"configurable": {"thread_id": "marker-log-a-it"}},
            )
        )

    log_text = "\n".join(rec.getMessage() for rec in caplog.records)

    # marker-row fields on the canonical gate log line.
    assert "marker_hit=True" in log_text
    assert "marker_terms=ending turn" in log_text
    # Canonical row says "<pending>" (judge hasn't run yet).
    assert "marker_path=" not in log_text

    # judge-row fields on the fused-judge log line.
    assert "event=leader_completion_gate_fused_judge" in log_text
    assert "verdict=not_complete" in log_text
    assert "llm_judge_latency_ms=" in log_text
    assert "llm_judge_model=fake-quick" in log_text
    assert "llm_judge_error_class=" in log_text

    # The authoritative outcome lands on the resolver row — pending
    # work + not_complete verdict ⇒ allow + hint.
    assert "resolver_outcome=allow_hint" in log_text


def test_log_marker_fields_present_on_no_marker_path(monkeypatch, caplog):
    """No markers AND no length trigger → marker_hit=False,
    marker_terms=<none>, marker_path=<none>, length_trigger=False,
    final_word_count>=150, trigger_source=<none> — no judge row.

    The cheap allow path: NEITHER trigger half fires, so NO judge call
    fires (cost control preserved). The 2026-09-12 length trigger
    requires the LAST AIMessage to be < SHORT_REPORT_WORD_THRESHOLD
    (150) words — we use a long, detailed completion report so the
    brevity signal does NOT fire either.
    """
    # A long, detailed completion report — explicitly avoids the
    # marker catalog (no "ending turn", "awaiting", etc.) and is
    # ≥150 words so the length trigger doesn't fire.
    long_report = (
        "All four patches landed and shipped to the integration branch. "
        "Patch 1 fixed the off-by-one in the cache TTL calculator; the "
        "unit tests now exercise both the elapsed-second and "
        "wall-clock-second boundaries. Patch 2 cleaned up the dead "
        "imports in the worker pool module after the migration. Patch 3 "
        "refactored the error-reporting decorator so the stack-frame "
        "metadata is consistent across all four call sites. Patch 4 "
        "added the missing operator-boot log line for the new resolver "
        "module so operators can grep the boot summary for the "
        "resolved effective values. All four patches passed their "
        "respective suites on the first run with no flake; the "
        "integration matrix is green end-to-end. No follow-ups "
        "outstanding; the mission is complete and ready for review. "
        "The release notes draft is staged on the docs branch with the "
        "per-patch rationale paragraphs and the cross-references to "
        "the upstream incident reports; the FE mirror was verified "
        "and the build artifact attached to the rollout ticket. "
        "Nothing pending on my end — I am closing out."
    )
    node, manager, ledger = _make_node(instance_id="marker-log-none-it")
    with caplog.at_level(logging.INFO, logger="daemon.graph"), caplog.at_level(
        logging.INFO, logger="daemon.services.attestation_gate"
    ), caplog.at_level(
        logging.INFO,
        logger="daemon.services.attestation_resolver_activation",
    ):
        asyncio.run(
            node(
                _quick_question_with_marker(long_report),
                config={"configurable": {"thread_id": "marker-log-none-it"}},
            )
        )

    log_text = "\n".join(rec.getMessage() for rec in caplog.records)

    # Additive marker fields on the canonical row — NEITHER trigger
    # half fires so the cheap allow path is taken.
    assert "marker_hit=False" in log_text
    assert "marker_terms=<none>" in log_text
    assert "marker_path=" not in log_text
    # Length trigger fields: long report (>= 150 words) ⇒ no trigger.
    assert "length_trigger=False" in log_text
    # Stage 3 (R5/R6): the trigger-derivation fields retired.
    assert "trigger_source=" not in log_text

    # No judge row.
    assert "event=leader_completion_gate_marker_judge" not in log_text


# ─────────────────────────────────────────────────────────────────────────────
# Verbatim incident phrase — the b08f40fe-class regression guard
# ─────────────────────────────────────────────────────────────────────────────


def test_verbatim_incident_phrase_killed_by_path_a(monkeypatch, caplog):
    """The VERBATIM incident phrase MUST be killed by the fused path.

    The b08f40fe-class regression guard: leader b08f40fe's final
    AIMessage was "Awaiting final four: C12a/b/c + blame-worker. Then
    I aggregate and write RESULTS. Ending turn." This is a delegated
    mission ending without attestation — the incident's real shape.

    LCA Stage-2 flip re-contract (2026-09-16): the legacy doctrine
    drove this via a NON-delegated quick-question (the
    conditional-off branch was scanned by the legacy marker wiring);
    the unified predicate's D10 meta-bypass exempts non-delegated
    missions, so the verbatim-phrase kill now rides the DELEGATED
    deny-band row (the incident's actual shape): the fused judge says
    not_complete → DENIED stands → nudge + counter via the existing
    machinery. The non-delegated exemption is pinned by
    ``test_quick_question_with_marker_does_not_reach_judge``.
    """
    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", _invoke_no)

    node, manager, ledger = _make_node(instance_id="b08f40fe-it")
    with caplog.at_level(logging.INFO, logger="daemon.graph"), caplog.at_level(
        logging.INFO, logger="daemon.services.attestation_gate"
    ), caplog.at_level(
        logging.INFO,
        logger="daemon.services.attestation_resolver_activation",
    ):
        result = asyncio.run(
            node(
                _delegated_mission_with_marker(
                    "Awaiting final four: C12a/b/c + blame-worker. "
                    "Then I aggregate and write RESULTS. Ending turn."
                ),
                config={"configurable": {"thread_id": "b08f40fe-it"}},
            )
        )

    # The b08f40fe-class kill: marker fires → judge-not-complete →
    # DENIED + nudge.
    assert "messages" in result
    assert result["attestation_route"] == "agent"
    ledger.increment.assert_called_once()

    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "verdict=not_complete" in log_text
    assert "resolver_outcome=deny_nudge" in log_text


# ─────────────────────────────────────────────────────────────────────────────
# Conditional-attestation OFF quick-question with mid-work phrasing — the
# brief's original example "Ending turn, will continue after your reply".
# LCA Stage-2 flip (2026-09-16): the D10 meta-bypass EXEMPTS non-delegated
# missions from the judge — this test now pins the EXEMPTION (the unified
# predicate's Term-1 short-circuit; stricter than the legacy marker-scan
# wiring, per decisions.md D-RES1 spec-vs-landed delta + D-RES2).
# ─────────────────────────────────────────────────────────────────────────────


def test_quick_question_with_marker_does_not_reach_judge(monkeypatch, caplog):
    """Brief's original quick-question example: under the unified
    resolver the judge is NEVER called (D10 meta-bypass —
    ``¬attestation_required`` ⇒ A/B never evaluated, plain allow,
    zero LLM).

    The legacy wiring scanned the conditional-off branch and reached
    the judge there; the unified predicate deliberately does NOT
    (spec §4.2 Term 1 / R4-D10 mirror — suspicion work is delegated-
    missions-only). Pinned as the exemption invariant.
    """
    calls = []

    async def record_call(config, user_payload, *, timeout_s, system_prompt=None):
        calls.append(user_payload)
        return (
            '{"verdict": "not_complete", "evidence_cited": [], '
            '"advisory_note_text": "", "rationale": "mid-work status"}',
            "fake-quick",
        )

    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", record_call)

    node, manager, ledger = _make_node(instance_id="marker-qq-it")
    with caplog.at_level(logging.INFO, logger="daemon.graph"), caplog.at_level(
        logging.INFO, logger="daemon.services.attestation_gate"
    ), caplog.at_level(
        logging.INFO,
        logger="daemon.services.attestation_resolver_activation",
    ):
        result = asyncio.run(
            node(
                _quick_question_with_marker(
                    "The answer is X. Ending turn, will continue after "
                    "your reply."
                ),
                config={"configurable": {"thread_id": "marker-qq-it"}},
            )
        )

    # Judge was NOT called — the D10 exemption held.
    assert calls == []

    # Plain ALLOW — no nudge, no counter, no hint.
    assert "messages" not in result
    assert result["attestation_route"] is None
    ledger.increment.assert_not_called()

    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    # The resolver row records the meta-bypass (not fired).
    assert "attestation_required=False" in log_text
    assert "fired=False" in log_text
    assert "bypass_reason=meta_bypass" in log_text
    assert "judge_invoked=False" in log_text


def test_quick_question_marker_scan_seam_never_invoked(monkeypatch, caplog):
    """D10 BELT pin (adversarial-review round 2026-09-17): on a
    marker-laden NON-delegated mission the SCANNER SEAM itself is
    never invoked — ``scan_for_mid_work_markers`` records zero calls
    and the canonical row's ``marker_hit`` stays False (the dataclass
    default, not a measurement).

    Complements ``test_quick_question_with_marker_does_not_reach_judge``
    (which spies the judge seam) and the census guard-grep on the
    evaluate() source: a hypothetical SECOND scan site (a future
    resurrection of the retired conditional-off scan) would trip this
    spy even if the judge stayed silent.
    """
    from daemon.services import attestation_gate as gate_mod
    from daemon.services.attestation_marker_scanner import (
        scan_for_mid_work_markers as _real_scan,
    )

    scanner_calls = []

    def _scan_spy(messages, window):
        scanner_calls.append((len(messages), window))
        return _real_scan(messages, window)

    monkeypatch.setattr(
        gate_mod, "scan_for_mid_work_markers", _scan_spy
    )

    node, manager, ledger = _make_node(instance_id="marker-qq-scan-it")
    with caplog.at_level(logging.INFO, logger="daemon.graph"), caplog.at_level(
        logging.INFO, logger="daemon.services.attestation_gate"
    ):
        result = asyncio.run(
            node(
                _quick_question_with_marker(
                    "The answer is X. Ending turn, will continue after "
                    "your reply."
                ),
                config={"configurable": {"thread_id": "marker-qq-scan-it"}},
            )
        )

    # Plain ALLOW, no side effects.
    assert "messages" not in result
    assert result["attestation_route"] is None
    ledger.increment.assert_not_called()

    # THE BELT: the scanner seam was never invoked on the ¬required row.
    assert scanner_calls == [], (
        "Stage-3 D10 mirror violated: the marker scan ran on a "
        "non-delegated mission (suspicion signals must not be "
        f"evaluated there) — spy recorded {scanner_calls}"
    )
    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    canonical = next(
        line
        for line in log_text.splitlines()
        if line.startswith("event=leader_completion_gate ")
    )
    assert "attestation_required=False" in canonical
    # marker_hit is the False default — the scanner never measured it.
    assert "marker_hit=False" in canonical
    assert "marker_terms=<none>" in canonical


# ─────────────────────────────────────────────────────────────────────────────
# Review pass W1 (2026-09-12) — dry-mode marker signal
# ─────────────────────────────────────────────────────────────────────────────


def test_dry_mode_marker_hit_logs_signal_no_side_effects(
    monkeypatch, caplog
):
    """W1: dry mode + mid-work marker → canonical log row carries
    marker_hit / marker_terms / marker_path; ZERO side effects.

    The review-pass defect was that the gate-level scan at
    ``daemon/services/attestation_gate.py`` excluded
    ``Decision.DRY_LOG`` from the marker-scan trigger tuple, so the
    documented "marker hits logged in dry mode" signal never fired.
    The fix opens the trigger tuple to include ``DRY_LOG`` (LOG-ONLY
    side-effect-free path); the graph node early-outs for DRY_LOG
    before any judge/hint/deny/counter side effect. Pin the end-to-
    end contract on a delegated-mission dry run.
    """
    # The judge service must NEVER be called on the dry-mode path —
    # the fused block skips the judge when the snapshot mode is not
    # "enforce" (R3: dry = activation computed + logged, node
    # skipped). Track any accidental call as a regression.
    judge_calls = []

    async def must_not_be_called(config, user_payload, *, timeout_s, system_prompt=None):
        judge_calls.append(True)
        return (
            '{"verdict": "complete", "evidence_cited": [], '
            '"advisory_note_text": "", "rationale": "genuine"}',
            "fake-quick",
        )

    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", must_not_be_called)

    # Dry-mode wiring: GateSettings(mode="dry", window=3, deny_bound=3).
    manager = MagicMock()
    manager.count_pending_children.return_value = 0
    manager.get_queued_or_expected_wakeups.return_value = 0
    manager.count_live_descendants.return_value = 0
    manager.count_busy_descendants.return_value = 0
    manager.enqueue_message = MagicMock()
    manager.revive = MagicMock()
    manager.send_message = MagicMock()

    ledger = MagicMock()
    ledger.increment.return_value = 1
    ledger.reset.return_value = True
    ledger.set_escalated_and_reset.return_value = True
    ledger.get.return_value = 0

    config = build_gate_config(
        "marker-dry-it", GateSettings("dry", 3, 3),
        llm_judge_enabled=True,
    )
    node = create_attestation_gate_node(
        config,
        GateSettings("dry", 3, 3),
        manager,
        "marker-dry-it",
        denied_count_getter=lambda: 0,
        ledger=ledger,
    )

    with caplog.at_level(logging.INFO, logger="daemon.graph"), caplog.at_level(
        logging.INFO, logger="daemon.services.attestation_gate"
    ), caplog.at_level(
        logging.INFO,
        logger="daemon.services.attestation_resolver_activation",
    ):
        result = asyncio.run(
            node(
                _delegated_mission_with_marker(
                    "Awaiting final four: C12a/b/c + blame-worker. "
                    "Then I aggregate and write RESULTS. Ending turn."
                ),
                config={"configurable": {"thread_id": "marker-dry-it"}},
            )
        )

    # ZERO side effects on the dry-mode path.
    assert judge_calls == [], (
        "W1: dry mode + marker MUST NOT call the judge — the marker-"
        "path judge wiring early-outs for DRY_LOG before any judge/"
        "hint/deny/counter side effect"
    )
    assert "messages" not in result, (
        "W1: dry mode + marker MUST NOT inject a hint — "
        "2026-09-23 (b2f4dae9): hint RETIRED end-to-end, so the "
        "dry path carries zero side effects regardless"
    )
    assert result["attestation_route"] is None, (
        "W1: dry mode + marker MUST NOT re-route — plain END preserved"
    )
    ledger.increment.assert_not_called()
    ledger.reset.assert_not_called()
    ledger.set_escalated_and_reset.assert_not_called()

    # Canonical log row carries the dry-mode marker signal — the W1
    # fix that lets the bake-time observability surface the marker
    # hits. Asserted at the format-string level so the row stays
    # grep-disjoint from the (a)/(b)/(c)/(d) routing rows.
    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    canonical_row = next(
        (m for m in log_text.splitlines() if "event=leader_completion_gate" in m),
        None,
    )
    assert canonical_row is not None, (
        "W1: the canonical gate log row MUST be emitted on the dry path"
    )
    assert "decision=dry_log" in canonical_row
    assert "marker_hit=True" in canonical_row
    # Verbatim incident phrase fires three markers in CATALOG order
    # (catalog iteration order, NOT first-occurrence — the scanner
    # orders by catalog per the brief). Catalog order is "ending
    # turn" → "awaiting" → "then i aggregate" (see
    # daemon/services/attestation_marker_scanner.py:114-131).
    assert (
        "marker_terms=ending turn,awaiting,then i aggregate"
        in canonical_row
    )
    # marker_path stays at the transient "<pending>" sentinel — the
    # graph node early-out never resolved it to "a"/"b"/"c"/"d"
    # (no judge ran). This is the dry-mode LOG-ONLY contract.
    assert "marker_path=" not in canonical_row
    # Stage 3 (R7): the judge-verdict stamp fields retired — the
    # verdict rides the fused-judge event row instead.
    assert "marker_judge_verdict=" not in canonical_row

    # No marker-path judge row, no marker-path judge error row, no
    # kill-switch disabled row — dry mode is the gate's pure-passive
    # observer branch.
    assert "event=leader_completion_gate_marker_judge" not in log_text
    assert (
        "event=leader_completion_gate_marker_judge_error" not in log_text
    )
    assert (
        "event=leader_completion_gate_marker_judge_disabled"
        not in log_text
    )


# ─────────────────────────────────────────────────────────────────────────────
# 2026-09-23 — Incident b2f4dae9: Completion Check Note RETIRED end-to-end
# ─────────────────────────────────────────────────────────────────────────────
#
# The Completion Check Note stable-id contract (F1 Shape A) is RETIRED
# along with the entire (b)/(d)-with-pending hint injection surface.
# The three W2 supersede tests (stable-id-collapses / per-instance /
# compaction-seam-hoists) are removed because the factory
# ``_make_completion_check_note_message`` is gone — there is no
# surviving producer to mint a stable id for, and the
# ``completion_check_note`` row was removed from
# ``_stable_id_for``'s canonical id-format table.
#
# The b2f4dae9 regression pin lives in
# ``tests/unit/test_attestation_lca_note_removed.py`` (a new file)
# where it is the canonical witness: healthy busy wait + A-band
# lexical FP trigger + judge not-complete → allowed, ZERO injected
# messages (assert message count unchanged pre/post gate), full log
# row present (verdict + would-be-route allow_hint).


# ─────────────────────────────────────────────────────────────────────────────
# Review pass green #1 (2026-09-12) — kill-switch OFF stamps <skipped>
# ─────────────────────────────────────────────────────────────────────────────


def test_marker_kill_switch_off_stamps_skipped_verdict(
    monkeypatch, caplog
):
    """Green #1 (re-contracted to the fused seam, 2026-09-16): kill-switch
    OFF emits the disabled row with verdict=<skipped>.

    LCA Stage-2 flip: the legacy ``<skipped>`` decision stamp rode the
    now-dead marker block; the operator-observable signal is the
    ``event=leader_completion_gate_fused_judge_disabled`` row
    (``verdict=<skipped>``). Delegated + pending mission (marker band)
    so the kill-switch branch is the actual guard.
    """
    # The judge service must NEVER be called on the kill-switch OFF
    # path — track any accidental call as a regression.
    judge_calls = []

    async def must_not_be_called(config, user_payload, *, timeout_s, system_prompt=None):
        judge_calls.append(True)
        return (
            '{"verdict": "complete"}',
            "fake-quick",
        )

    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", must_not_be_called)

    # Real env var + real resolver reset to drive the kill-switch OFF
    # branch through the canonical resolver (not the gate-config
    # bypass).
    monkeypatch.setenv("ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED", "0")
    judge_resolver_mod.reset_llm_judge_resolver_for_tests()
    assert judge_resolver_mod.is_llm_judge_enabled() is False

    node, manager, ledger = _make_node(
        instance_id="marker-skip-it", pending_children=1
    )
    with caplog.at_level(logging.INFO, logger="daemon.graph"), caplog.at_level(
        logging.INFO, logger="daemon.services.attestation_gate"
    ), caplog.at_level(
        logging.INFO,
        logger="daemon.services.attestation_resolver_activation",
    ):
        asyncio.run(
            node(
                _delegated_mission_with_marker(
                    "Awaiting reply. Ending turn."
                ),
                config={"configurable": {"thread_id": "marker-skip-it"}},
            )
        )

    # ZERO judge calls — the kill-switch OFF path bypasses the judge.
    assert judge_calls == []

    # Distinct log row emitted by the kill-switch OFF branch —
    # ``event=leader_completion_gate_fused_judge_disabled`` with
    # ``verdict=<skipped>``. Grep-disjoint from the fused judge event
    # family so operators can pinpoint the operator-disabled case.
    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert (
        "event=leader_completion_gate_fused_judge_disabled" in log_text
    ), "green #1: kill-switch OFF MUST emit the disabled-row signal"
    assert "verdict=<skipped>" in log_text, (
        "green #1: the disabled-row verdict MUST be the literal "
        "<skipped> sentinel (operators grep for this exact token)"
    )

    # The canonical gate log row's marker_judge_verdict stays at the
    # transient "<pending>" sentinel — graph.py cannot mutate a row
    # that was emitted before this code runs. The disabled row IS
    # the operator-observable signal.
    canonical_row = next(
        (m for m in log_text.splitlines() if "event=leader_completion_gate" in m),
        None,
    )
    assert canonical_row is not None
    # Stage 3 (R7): the judge-verdict stamp fields retired — the
    # verdict rides the fused-judge event row instead.
    assert "marker_judge_verdict=" not in canonical_row


def test_marker_scan_catalog_pin_survives_optimization():
    """Green #5: catalog pin raises RuntimeError (not bare assert).

    The catalog size pin at module load was a bare ``assert`` —
    ``python -O`` strips assertions and silently disables the pin.
    Convert to an explicit ``RuntimeError`` so the pin survives
    optimization. Verify the runtime pin function shape via a
    focused contract test: the same check expression (12 ≤ N ≤ 18)
    is reachable both as a conditional raise and via the now-active
    module-load pin — pin the public surface so a future regression
    to ``assert`` is caught.
    """
    import textwrap

    from daemon.services import attestation_marker_scanner as scanner_mod

    # The catalog at module load must currently be in range (sanity).
    assert 12 <= len(scanner_mod.MID_WORK_MARKERS) <= 18, (
        "green #5: catalog pin pre-condition — the real catalog MUST "
        "be in range 12-18 (per the brief)"
    )

    # The pin must NOT be a bare ``assert`` (which ``python -O``
    # strips). Verify by sourcing the module's compiled code and
    # confirming the catalog-pinning statement uses an explicit
    # ``raise RuntimeError`` — the same expression an ``assert``
    # could be replaced with, but one that survives optimization.
    source = textwrap.dedent(
        open(scanner_mod.__file__).read()
    )
    assert "assert 12 <= len(MID_WORK_MARKERS)" not in source, (
        "green #5: the catalog pin MUST NOT be a bare ``assert`` "
        "(stripped by ``python -O``); use an explicit RuntimeError "
        "raise"
    )
    assert "raise RuntimeError" in source, (
        "green #5: the catalog pin MUST raise RuntimeError explicitly "
        "so it survives ``python -O``"
    )
    assert "12-18 patterns" in source, (
        "green #5: the pin error message MUST name the 12-18 range so "
        "operators see the contract when it fires"
    )


def test_marker_scanner_module_no_re_import():
    """Green #3: the unused ``re`` import is gone.

    The marker scanner uses substring match (not regex), so the
    ``re`` module import + the ``_ = re`` silencer at module bottom
    were both dead weight. Pin: the module has no ``re`` symbol in
    its namespace (the silencer is gone too).
    """
    from daemon.services import attestation_marker_scanner as scanner_mod

    # The module must not import ``re`` (the scanner uses substring
    # match — the dead ``re`` import is removed).
    assert not hasattr(scanner_mod, "re"), (
        "green #3: the unused ``re`` import MUST be removed from "
        "attestation_marker_scanner.py (substring match, not regex)"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Length trigger wiring tests (2026-09-12, user request)
#
# Word-count signal on the LAST AIMessage composes with the marker
# substring scan via ``OR`` on the gate's ALLOW paths. Below the
# threshold (150 words) ⇒ trigger fires ⇒ judge runs (or skip on
# kill-switch OFF / dry mode). The marker-only and length-only
# halves are independent — both halves firing drives
# ``trigger_source="markers+length"`` (additive log field).
# ─────────────────────────────────────────────────────────────────────────────


# Helper: build a long detailed completion report (≥150 words) so the
# length trigger DOES NOT fire on it. Used by tests that need the
# marker scan to be the sole trigger.


def _long_completion_report() -> str:
    """A >= 150 word detailed completion report — no marker phrases."""
    return (
        "All four patches landed and shipped to the integration branch. "
        "Patch 1 fixed the off-by-one in the cache TTL calculator; the "
        "unit tests now exercise both the elapsed-second and "
        "wall-clock-second boundaries. Patch 2 cleaned up the dead "
        "imports in the worker pool module after the migration. Patch 3 "
        "refactored the error-reporting decorator so the stack-frame "
        "metadata is consistent across all four call sites. Patch 4 "
        "added the missing operator-boot log line for the new resolver "
        "module so operators can grep the boot summary for the "
        "resolved effective values. All four patches passed their "
        "respective suites on the first run with no flake; the "
        "integration matrix is green end-to-end. No follow-ups "
        "outstanding; the mission is complete and ready for review. "
        "The release notes draft is staged on the docs branch with the "
        "per-patch rationale paragraphs and the cross-references to "
        "the upstream incident reports; the FE mirror was verified "
        "and the build artifact attached to the rollout ticket. "
        "Nothing pending on my end — I am closing out."
    )


def test_length_short_no_marker_judge_fires(monkeypatch, caplog):
    """Short AIMessage + no markers → length trigger fires → judge runs.

    The brevity-only trigger path (no marker phrases, just short
    prose): "Understood, continuing." (2 words) — well under 150 —
    the judge is the verdict. LCA Stage-2 flip re-contract
    (2026-09-16): DELEGATED mission (D10 exempts non-delegated);
    judge-not-complete + nothing pending → DENY via the existing
    machinery.
    """
    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", _invoke_no)

    # NOTE: a delegated ∧ quiet row decides DENIED and the gate's
    # marker/length scan never runs (allow-family only) — the unified
    # deny band drives the judge there. To exercise the LENGTH signal
    # reaching the judge we use a row with real pending work (marker
    # band).
    node, manager, ledger = _make_node(
        instance_id="length-a-it", pending_children=1
    )
    with caplog.at_level(logging.INFO, logger="daemon.graph"), caplog.at_level(
        logging.INFO, logger="daemon.services.attestation_gate"
    ), caplog.at_level(
        logging.INFO,
        logger="daemon.services.attestation_resolver_activation",
    ):
        result = asyncio.run(
            node(
                _delegated_mission_with_marker(
                    "Understood, continuing."
                ),
                config={"configurable": {"thread_id": "length-a-it"}},
            )
        )

    # Allow + log-only — 2026-09-23 (b2f4dae9): the (b) hint is
    # RETIRED end-to-end. Length trigger fired, judge said not-
    # complete, real pending work is en route. (b)/(d)-with-pending
    # route resolves to allow on the resolver row, NO message
    # injected.
    assert "messages" not in result, (
        "length-only (b) MUST be log-only after 2026-09-23; "
        f"got messages={result.get('messages')!r}"
    )
    assert result["attestation_route"] is None
    ledger.increment.assert_not_called()

    # Log row carries the length-trigger fields — neither marker
    # fired (no marker phrases in the prose) but length did.
    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "marker_hit=False" in log_text
    assert "length_trigger=True" in log_text
    assert "trigger_source=" not in log_text
    assert "resolver_outcome=allow_hint" in log_text
    assert "verdict=not_complete" in log_text


def test_length_long_no_marker_no_judge_cost_control(monkeypatch, caplog):
    """Long AIMessage + no markers → NO trigger → NO judge call.

    Cost-control contract: a detailed completion report (>= 150
    words) is exactly the shape the leader SHOULD be producing on a
    legitimate completion — neither trigger fires and the cheap
    allow path runs.
    """
    calls = []

    async def must_not_be_called(config, user_payload, *, timeout_s, system_prompt=None):
        calls.append(True)
        return ('{"verdict": "complete"}', "fake-quick")

    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", must_not_be_called)

    node, manager, ledger = _make_node(instance_id="length-cost-it")
    with caplog.at_level(logging.INFO, logger="daemon.graph"), caplog.at_level(
        logging.INFO, logger="daemon.services.attestation_gate"
    ), caplog.at_level(
        logging.INFO,
        logger="daemon.services.attestation_resolver_activation",
    ):
        result = asyncio.run(
            node(
                _quick_question_with_marker(_long_completion_report()),
                config={"configurable": {"thread_id": "length-cost-it"}},
            )
        )

    # Judge never called (cost control preserved).
    assert calls == []
    # Plain ALLOW.
    assert "messages" not in result
    assert result["attestation_route"] is None
    ledger.increment.assert_not_called()

    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "marker_hit=False" in log_text
    assert "length_trigger=False" in log_text
    # Stage 3 (R5/R6): the trigger-derivation fields retired.
    assert "trigger_source=" not in log_text
    assert "event=leader_completion_gate_fused_judge" not in log_text


def test_length_short_complete_artifact_judge_yes_allows(
    monkeypatch, caplog
):
    """Short AIMessage containing a quick-answer artifact → judge fires,
    judge says yes → ALLOW normally.

    A short-but-complete answer (e.g., "Here is the chart you asked
    for. <chart>" under 150 words, content includes the artifact).
    The brevity class is real here, but the judge confirms the
    artifact IS the deliverable. LCA Stage-2 flip re-contract
    (2026-09-16): DELEGATED mission; complete verdict → allow END.
    """
    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", _invoke_yes)

    short_with_artifact = (
        "Here is the chart you asked for.\n\n```mermaid\n"
        "flowchart TD\n    A[Start] --> B[End]\n```\n"
    )
    node, manager, ledger = _make_node(
        instance_id="length-c-it", pending_children=1
    )
    with caplog.at_level(logging.INFO, logger="daemon.graph"), caplog.at_level(
        logging.INFO, logger="daemon.services.attestation_gate"
    ), caplog.at_level(
        logging.INFO,
        logger="daemon.services.attestation_resolver_activation",
    ):
        result = asyncio.run(
            node(
                _delegated_mission_with_marker(short_with_artifact),
                config={"configurable": {"thread_id": "length-c-it"}},
            )
        )

    # ALLOWED normally — no nudge, no counter, no hint.
    assert "messages" not in result
    assert result["attestation_route"] is None
    ledger.increment.assert_not_called()

    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    # Length trigger fired (short text); judge said complete; allow.
    assert "length_trigger=True" in log_text
    assert "trigger_source=" not in log_text
    assert "verdict=complete" in log_text
    assert "resolver_outcome=allow" in log_text


def test_length_short_real_pending_hint(monkeypatch, caplog):
    """Short AIMessage + real pending work → length trigger fires →
    judge fires, judge-no → (b) ALLOW + checkpoint-durable hint.

    The wake-up is en route (pending_children > 0); the brevity
    trigger fires (short text, no marker phrases); the judge says
    the report is mid-work; gate allows + injects hint so the
    wake-up can still arrive.
    """
    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", _invoke_no)

    manager = MagicMock()
    manager.count_pending_children.return_value = 1  # REAL pending
    manager.get_queued_or_expected_wakeups.return_value = 0
    manager.count_live_descendants.return_value = 0
    manager.count_busy_descendants.return_value = 0
    manager.enqueue_message = MagicMock()
    manager.revive = MagicMock()
    manager.send_message = MagicMock()

    ledger = MagicMock()
    ledger.increment.return_value = 1
    ledger.reset.return_value = True
    ledger.set_escalated_and_reset.return_value = True
    ledger.get.return_value = 0

    config = build_gate_config(
        "length-b-it", GateSettings("enforce", 3, 3),
        llm_judge_enabled=True,
    )
    node = create_attestation_gate_node(
        config,
        GateSettings("enforce", 3, 3),
        manager,
        "length-b-it",
        denied_count_getter=lambda: 0,
        ledger=ledger,
    )

    # Short text WITHOUT marker phrases — length trigger fires,
    # marker trigger does not. trigger_source must be "length".
    short_no_marker = "Understood, continuing — child reply coming."
    with caplog.at_level(logging.INFO, logger="daemon.graph"), caplog.at_level(
        logging.INFO, logger="daemon.services.attestation_gate"
    ), caplog.at_level(
        logging.INFO,
        logger="daemon.services.attestation_resolver_activation",
    ):
        result = asyncio.run(
            node(
                _delegated_mission_with_marker(short_no_marker),
                config={"configurable": {"thread_id": "length-b-it"}},
            )
        )

    # ALLOWED + log-only — 2026-09-23 (b2f4dae9): Completion Check
    # Note hint RETIRED end-to-end; length-trigger (b) path is now
    # allow-with-no-side-effect (still routes through the predicate's
    # b_fires term so the (b)/(d)-with-pending label is observable
    # in logs).
    assert "messages" not in result, (
        "length-trigger (b) MUST be log-only after 2026-09-23; "
        f"got messages={result.get('messages')!r}"
    )
    assert result["attestation_route"] is None
    ledger.increment.assert_not_called()

    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "length_trigger=True" in log_text
    assert "marker_hit=False" in log_text
    assert "trigger_source=" not in log_text
    assert "verdict=not_complete" in log_text
    assert "would_be_route=allow_hint" in log_text
    assert "resolver_outcome=allow_hint" in log_text


def test_length_short_markers_judge_combined_trigger_source(
    monkeypatch, caplog
):
    """Short AIMessage WITH marker phrases → BOTH triggers fire →
    trigger_source="markers+length", judge runs.

    The combined-trigger path — an answer like "Understood,
    continuing. Ending turn." is BOTH a brevity-class AND a marker
    phrase. The OR-composition flips trigger_source to the combined
    literal; the judge runs. LCA Stage-2 flip re-contract
    (2026-09-16): DELEGATED mission (D10 exempts non-delegated).
    """
    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", _invoke_no)

    node, manager, ledger = _make_node(
        instance_id="length-combined-it", pending_children=1
    )
    with caplog.at_level(logging.INFO, logger="daemon.graph"), caplog.at_level(
        logging.INFO, logger="daemon.services.attestation_gate"
    ), caplog.at_level(
        logging.INFO,
        logger="daemon.services.attestation_resolver_activation",
    ):
        result = asyncio.run(
            node(
                _delegated_mission_with_marker(
                    "Understood, continuing. Ending turn."
                ),
                config={"configurable": {"thread_id": "length-combined-it"}},
            )
        )

    # Allow + log-only — 2026-09-23 (b2f4dae9): the (b) hint is
    # RETIRED end-to-end. Both triggers still fire → judge runs →
    # judge-no → (b)/(d)-with-pending route resolves to allow on the
    # resolver row, NO message injected.
    assert "messages" not in result, (
        "(b) combined-trigger path MUST be log-only after 2026-09-23; "
        f"got messages={result.get('messages')!r}"
    )
    assert result["attestation_route"] is None
    ledger.increment.assert_not_called()

    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    # BOTH halves fire — trigger_source is the combined literal.
    assert "marker_hit=True" in log_text
    assert "length_trigger=True" in log_text
    assert "trigger_source=" not in log_text
    assert "resolver_outcome=allow_hint" in log_text
    assert "would_be_route=allow_hint" in log_text
    assert "verdict=not_complete" in log_text


def test_length_trigger_source_markers_only(monkeypatch, caplog):
    """Long AIMessage WITH marker phrases → only markers fire →
    trigger_source="markers", judge runs."""
    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", _invoke_no)

    # Take the long completion report and append a marker phrase;
    # total word count stays well above 150.
    long_with_marker = (
        _long_completion_report() + " Ending turn."
    )
    node, manager, ledger = _make_node(
        instance_id="length-m-it", pending_children=1
    )
    with caplog.at_level(logging.INFO, logger="daemon.graph"), caplog.at_level(
        logging.INFO, logger="daemon.services.attestation_gate"
    ), caplog.at_level(
        logging.INFO,
        logger="daemon.services.attestation_resolver_activation",
    ):
        result = asyncio.run(
            node(
                _delegated_mission_with_marker(long_with_marker),
                config={"configurable": {"thread_id": "length-m-it"}},
            )
        )

    # Allow + log-only — 2026-09-23 (b2f4dae9): the (b) hint is
    # RETIRED end-to-end. Markers fired; judge-not-complete; pending;
    # route resolves to allow, NO message injected.
    assert "messages" not in result, (
        "(b) markers-only path MUST be log-only after 2026-09-23; "
        f"got messages={result.get('messages')!r}"
    )
    assert result["attestation_route"] is None

    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "marker_hit=True" in log_text
    assert "length_trigger=False" in log_text
    assert "trigger_source=" not in log_text
    assert "resolver_outcome=allow_hint" in log_text
    assert "would_be_route=allow_hint" in log_text


def test_length_dry_mode_log_only_no_judge(monkeypatch, caplog):
    """Dry mode + length trigger → log-only, NO judge call.

    Side-effect-free exactly like the marker path: the dry-mode
    ``allow unconditionally`` posture is preserved. The canonical
    log row carries length_trigger=True + trigger_source=length
    so operators see the signal in ``decision=dry_log`` soak rows.
    """
    calls = []

    async def must_not_be_called(config, user_payload, *, timeout_s, system_prompt=None):
        calls.append(True)
        return ('{"verdict": "complete"}', "fake-quick")

    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", must_not_be_called)

    manager = MagicMock()
    manager.count_pending_children.return_value = 0
    manager.get_queued_or_expected_wakeups.return_value = 0
    manager.count_live_descendants.return_value = 0
    manager.count_busy_descendants.return_value = 0
    manager.enqueue_message = MagicMock()
    manager.revive = MagicMock()
    manager.send_message = MagicMock()

    ledger = MagicMock()
    ledger.increment.return_value = 1
    ledger.reset.return_value = True
    ledger.set_escalated_and_reset.return_value = True
    ledger.get.return_value = 0

    config = build_gate_config(
        "length-dry-it", GateSettings("dry", 3, 3),
        llm_judge_enabled=True,
    )
    node = create_attestation_gate_node(
        config,
        GateSettings("dry", 3, 3),
        manager,
        "length-dry-it",
        denied_count_getter=lambda: 0,
        ledger=ledger,
    )

    with caplog.at_level(logging.INFO, logger="daemon.graph"), caplog.at_level(
        logging.INFO, logger="daemon.services.attestation_gate"
    ), caplog.at_level(
        logging.INFO,
        logger="daemon.services.attestation_resolver_activation",
    ):
        result = asyncio.run(
            node(
                _quick_question_with_marker(
                    "Understood, continuing."
                ),
                config={"configurable": {"thread_id": "length-dry-it"}},
            )
        )

    # Judge never called — DRY mode is a passive observer; the
    # marker-path judge wiring in graph.py early-outs for DRY_LOG
    # before any judge call (mirrors the marker dry-mode contract).
    assert calls == []
    # Plain allow.
    assert "messages" not in result

    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    # Canonical gate row is dry-log; Stage 3 (R4/D10 mirror): the
    # quick-question (non-delegated) mission skips the scan entirely,
    # so the length signal is not evaluated (False default).
    assert "decision=dry_log" in log_text
    assert "length_trigger=False" in log_text
    assert "trigger_source=" not in log_text
    assert "marker_path=" not in log_text
    # No judge log row.
    assert "event=leader_completion_gate_fused_judge" not in log_text


def test_length_kill_switch_off_no_judge(monkeypatch, caplog):
    """Kill-switch OFF + length trigger → log-only, NO judge call.

    The kill-switch contract: ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_
    ENABLED=0 disables the marker-path judge. The length trigger
    rides the same kill-switch — same rationale (length-only signal
    is too weak to deny without the LLM verdict). Logged, no judge.
    """
    calls = []

    async def must_not_be_called(config, user_payload, *, timeout_s, system_prompt=None):
        calls.append(True)
        return ('{"verdict": "complete"}', "fake-quick")

    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", must_not_be_called)
    monkeypatch.setenv("ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED", "0")
    judge_resolver_mod.reset_llm_judge_resolver_for_tests()
    assert judge_resolver_mod.is_llm_judge_enabled() is False

    # LCA Stage-2 flip re-contract (2026-09-16): non-delegated mission
    # (quick question) — the D10 meta-bypass exempts it from the judge
    # BEFORE the kill-switch is even consulted, so the length-only
    # signal stays logged + plain allow (the marker-band kill-switch
    # row is pinned by test_marker_kill_switch_env_off_no_judge_call).
    node, manager, ledger = _make_node(instance_id="length-ks-off-it")
    with caplog.at_level(logging.INFO, logger="daemon.graph"), caplog.at_level(
        logging.INFO, logger="daemon.services.attestation_gate"
    ), caplog.at_level(
        logging.INFO,
        logger="daemon.services.attestation_resolver_activation",
    ):
        result = asyncio.run(
            node(
                _quick_question_with_marker(
                    "Understood, continuing."
                ),
                config={"configurable": {"thread_id": "length-ks-off-it"}},
            )
        )

    # Judge never called.
    assert calls == []
    # Plain ALLOW.
    assert "messages" not in result
    assert result["attestation_route"] is None
    ledger.increment.assert_not_called()

    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    # Stage 3 (R4/D10 mirror): on a NON-delegated mission the scan is
    # SKIPPED entirely — the length signal is not even evaluated (the
    # row carries the False default), mirroring the predicate's
    # Term-1 short-circuit of its A/B providers.
    assert "length_trigger=False" in log_text
    assert "trigger_source=" not in log_text
    # D10 exemption: not fired, no judge row of any kind.
    assert "fired=False" in log_text
    assert "bypass_reason=meta_bypass" in log_text
    assert (
        "event=leader_completion_gate_fused_judge" not in log_text
        and "event=leader_completion_gate_fused_judge_disabled" not in log_text
    )


def test_length_log_placeholder_count_is_27():
    """The canonical ``event=leader_completion_gate`` log format
    string has exactly 27 placeholders. Stage 3 (2026-09-17,
    resolver-unification R1/R5/R6) retired six keys from the row —
    the outside-window diagnostic, the busy-suppressor name, the
    marker route enum, and the three judge-verdict stamp fields —
    taking the count 34 → 27. Pinned by source grep —
    drift pin so log-row format-string changes surface in code
    review (a regression breaks grep-based soak tooling silently).

    TWO-LAYER PIN: substring-count alone cannot catch an arg-shift
    (Python's ``logging`` swallows format errors via
    ``Handler.handleError`` — see CPython
    ``Lib/logging/__init__.py`` — so a 33-placeholder format string
    fed 34 args would log a tuple-mismatch error to stderr and the
    row would appear truncated, but no test failure). The second
    layer parses the source via ``ast`` to extract the literal
    arg-tuple after the format string and asserts the arg count
    equals the placeholder count (27 == 27). The two layers catch
    drift in opposite directions: the substring count catches a
    format-string change that drops a field; the arg-count parse
    catches a body change that adds/removes an arg without
    updating the format string."""
    from daemon.services import attestation_gate as gate_mod
    import ast
    import inspect

    source = inspect.getsource(gate_mod)
    # Locate the format-string block for the leader_completion_gate row.
    # Pin: the format string starts with the canonical event= prefix.
    marker_prefix = "event=leader_completion_gate decision=%s"
    assert marker_prefix in source, (
        "canonical gate log format string not found — drift in "
        "attestation_gate.py"
    )
    # Count the %s occurrences on the format string up to the
    # newline after the LAST %s — by reading the source lines
    # directly.
    lines = source.splitlines()
    in_format_block = False
    format_string_lines: list[str] = []
    for line in lines:
        if marker_prefix in line:
            in_format_block = True
        if in_format_block:
            format_string_lines.append(line)
            # The format block ends when the close paren after the
            # format-string arguments closes — heuristically, the
            # log call ends at 'extra=meta,' followed by ')'. Stop
            # when we see the trailing ')' at line start.
            if line.strip() == ")":
                break
    format_text = "\n".join(format_string_lines)
    placeholder_count = format_text.count("%s")
    assert placeholder_count == 27, (
        f"canonical gate log format string placeholder count drifted: "
        f"expected 27 (34 - attest_seen_outside_window "
        f"- trigger_suppressed_by - marker_path - marker_judge_verdict "
        f"- marker_judge_latency_ms - marker_judge_error_class "
        f"- trigger_source [Stage 3 R1/R5/R6 retirements]), "
        f"got {placeholder_count}. Update the drift pin if the "
        f"placeholder count is correct for the new schema."
    )

    # ── Arg-count parity layer (logging-swallow hardener) ──
    # Parse the source with ``ast`` to extract the ``logger.info``
    # call carrying our canonical format string and assert the
    # positional-arg count of the format-string argument equals
    # the placeholder count. ``logging.Handler.handleError``
    # swallows format errors silently (CPython logging/__init__.py),
    # so a 33-placeholder format string fed 34 args would log a
    # ``KeyError`` / ``TypeError`` to stderr and the row would
    # appear truncated — but no test failure, no log-line drift
    # visible to grep. The args-count parity assertion closes that
    # silent-failure surface.
    parsed = ast.parse(source)
    found_args_count: int | None = None
    for node in ast.walk(parsed):
        # Match ``logger.info("..." + "..." + ..., arg1, arg2, ...)``
        # or ``logger.info("...", arg1, arg2, ...)``. The canonical
        # log call in this module uses string-concatenation
        # adjacent literals for the format string (PEP 3126 style);
        # ``ast.Call`` is the unified node for either form.
        if (
            not isinstance(node, ast.Call)
            or not isinstance(node.func, ast.Attribute)
            or node.func.attr != "info"
        ):
            continue
        # The first positional arg MUST be the format string — either
        # a single ``ast.Constant`` or a ``ast.BinOp`` chain (string
        # concatenation of adjacent literals). We accept both.
        if not node.args:
            continue
        first_arg = node.args[0]
        is_format = isinstance(first_arg, ast.Constant) and isinstance(
            first_arg.value, str
        )
        if not is_format and not (
            isinstance(first_arg, ast.BinOp)
            and isinstance(first_arg.op, ast.Add)
        ):
            continue
        # Reconstruct the format-string text from the AST and
        # verify it carries the canonical event= prefix.
        def _flatten(node_: ast.AST) -> str | None:
            if isinstance(node_, ast.Constant) and isinstance(
                node_.value, str
            ):
                return node_.value
            if isinstance(node_, ast.BinOp) and isinstance(
                node_.op, ast.Add
            ):
                left = _flatten(node_.left)
                right = _flatten(node_.right)
                if left is None or right is None:
                    return None
                return left + right
            return None

        flat = _flatten(first_arg)
        if flat is None or marker_prefix not in flat:
            continue
        # Pin: positional args AFTER the format string MUST equal
        # the placeholder count. ``extra=meta`` is a kwarg (NOT a
        # positional arg), so the positional count IS the format-
        # arg count.
        found_args_count = len(node.args) - 1
        break

    assert found_args_count is not None, (
        "canonical gate log call not found in attestation_gate.py "
        "AST — drift in the module structure"
    )
    assert found_args_count == placeholder_count, (
        f"gate log args-count parity drifted: format string has "
        f"{placeholder_count} placeholders but the call passes "
        f"{found_args_count} positional args. Python's logging "
        f"swallows format errors silently via Handler.handleError, "
        f"so a mismatch would log a tuple-mismatch error to stderr "
        f"and the row would appear truncated — no exception, no "
        f"grep-visible drift. Update BOTH the format string AND "
        f"the positional args when adding/removing a field."
    )


# ─────────────────────────────────────────────────────────────────────────────
# LCA busy trigger suppression (2026-09-12) — healthy-wait hints are
# noise; suppress marker/length trigger on busy descendants.
# ─────────────────────────────────────────────────────────────────────────────


def _delegated_short_marker_mission(final_text: str) -> dict:
    """Delegated mission ending with a short AIMessage (no marker
    phrase) — exercises the length-only trigger class."""

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


def test_busy_running_child_suppresses_marker_trigger_no_judge_no_hint(
    monkeypatch, caplog
):
    """(AC-B1, spec point 5a) RUNNING child + marker-triggering
    short "awaiting" ack → NO judge call, NO route-(b) log row, plain
    allow, ``trigger_suppressed_by="busy_descendants"``.

    The false-positive class: leader awaiting a RUNNING child writes
    a short mid-work ACK. The marker substring scan fires; the
    length trigger also fires (the AIMessage is short). Without busy
    suppression, the gate would route through the judge and — on
    judge-no — log a (b)/(d)-with-pending resolver row on essentially
    every awaiting turn-end (the would-be-route label survives in
    logs only, since 2026-09-23 the Completion Check Note hint is
    RETIRED end-to-end). Busy suppression disarms the WHOLE trigger
    so the leader is allowed silently and the healthy-wait noise is
    gone.
    """
    call_count = {"n": 0}

    def _track(*args, **kwargs):
        call_count["n"] += 1
        return ('{"is_complete_report": false, "reason": "x"}', "fake-quick")

    monkeypatch.setattr(
        "daemon.services.attestation_report_judge._invoke_judge_llm",
        _track,
    )

    node, manager, ledger = _make_node(
        instance_id="busy-running-it",
        busy_descendants=1,  # RUNNING child
        live_descendants=1,
    )
    with caplog.at_level(logging.INFO, logger="daemon.graph"), caplog.at_level(
        logging.INFO, logger="daemon.services.attestation_gate"
    ), caplog.at_level(
        logging.INFO,
        logger="daemon.services.attestation_resolver_activation",
    ):
        result = asyncio.run(
            node(
                _delegated_mission_with_marker(
                    "Awaiting tester reply. Ending turn."
                ),
                config={"configurable": {"thread_id": "busy-running-it"}},
            )
        )

    # Plain allow — NO nudge, NO counter, NO hint, NO judge call.
    assert result["attestation_route"] is None
    assert "messages" not in result  # no hint injected
    ledger.increment.assert_not_called()
    ledger.reset.assert_not_called()
    ledger.set_escalated_and_reset.assert_not_called()
    # Judge must NOT have been called.
    assert call_count["n"] == 0, (
        "marker-path judge fired despite busy trigger suppression; "
        "the trigger block in graph.py must check trigger_suppressed_by"
    )

    # Log row carries busy_descendants + trigger_suppressed_by.
    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "event=leader_completion_gate" in log_text
    assert "busy_descendants=1" in log_text
    # Stage 3 (R5): the busy-mute lives in the predicate's b_fires
    # term — the suppression is observable via busy_descendants=N
    # plus NO judge row (asserted below/above).
    assert "trigger_suppressed_by=" not in log_text
    # marker_hit STILL recorded for observability.
    assert "marker_hit=True" in log_text
    # trigger_source is cleared (cheap allow signal).
    # Stage 3 (R5/R6): the trigger-derivation fields retired.
    assert "trigger_source=" not in log_text
    # The marker_path is force-cleared to "" (no judge fires).
    # The log row stamps "<none>" for an empty marker_path.
    assert "marker_path=" not in log_text
    # No judge log row.
    assert (
        "event=leader_completion_gate_marker_judge " not in log_text
        and "event=leader_completion_gate_marker_judge\n" not in log_text
    )


def test_busy_waiting_child_suppresses_marker_trigger(monkeypatch, caplog):
    """(AC-B2, spec point 5b) WAITING child + marker-trigger → NO judge,
    NO hint, plain allow, suppression armed."""
    monkeypatch.setattr(
        "daemon.services.attestation_report_judge._invoke_judge_llm",
        _invoke_no,
    )
    # WAITING is busy (counted), live (counted).
    node, manager, ledger = _make_node(
        instance_id="busy-waiting-it",
        busy_descendants=1,
        live_descendants=1,
    )
    with caplog.at_level(logging.INFO, logger="daemon.graph"), caplog.at_level(
        logging.INFO, logger="daemon.services.attestation_gate"
    ), caplog.at_level(
        logging.INFO,
        logger="daemon.services.attestation_resolver_activation",
    ):
        result = asyncio.run(
            node(
                _delegated_mission_with_marker("Awaiting. Ending turn."),
                config={"configurable": {"thread_id": "busy-waiting-it"}},
            )
        )
    assert result["attestation_route"] is None
    assert "messages" not in result
    ledger.increment.assert_not_called()
    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "busy_descendants=1" in log_text
    # Stage 3 (R5): the busy-mute lives in the predicate's b_fires
    # term — the suppression is observable via busy_descendants=N
    # plus NO judge row (asserted below/above).
    assert "trigger_suppressed_by=" not in log_text
    assert "marker_hit=True" in log_text
    # Stage 3 (R5/R6): the trigger-derivation fields retired.
    assert "trigger_source=" not in log_text
    # No judge log row.
    assert (
        "event=leader_completion_gate_marker_judge " not in log_text
        and "event=leader_completion_gate_marker_judge\n" not in log_text
    )


def test_busy_waiting_children_child_suppresses_marker_trigger(
    monkeypatch, caplog
):
    """(AC-B3, spec point 5c) WAITING_CHILDREN child + marker-trigger
    → NO judge, NO hint, plain allow, suppression armed."""
    monkeypatch.setattr(
        "daemon.services.attestation_report_judge._invoke_judge_llm",
        _invoke_no,
    )
    node, manager, ledger = _make_node(
        instance_id="busy-waiting-children-it",
        busy_descendants=1,  # WAITING_CHILDREN counts as busy
        live_descendants=1,
    )
    with caplog.at_level(logging.INFO, logger="daemon.graph"), caplog.at_level(
        logging.INFO, logger="daemon.services.attestation_gate"
    ), caplog.at_level(
        logging.INFO,
        logger="daemon.services.attestation_resolver_activation",
    ):
        result = asyncio.run(
            node(
                _delegated_mission_with_marker("Awaiting. Ending turn."),
                config={"configurable": {"thread_id": "busy-waiting-children-it"}},
            )
        )
    assert result["attestation_route"] is None
    assert "messages" not in result
    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "busy_descendants=1" in log_text
    # Stage 3 (R5): the busy-mute lives in the predicate's b_fires
    # term — the suppression is observable via busy_descendants=N
    # plus NO judge row (asserted below/above).
    assert "trigger_suppressed_by=" not in log_text
    assert "marker_hit=True" in log_text


def test_paused_child_keeps_trigger_armed_no_suppression(
    monkeypatch, caplog
):
    """(AC-B4, spec point 5d) PAUSED child + trigger → judge still fires
    (PAUSED is suspect, not healthy — busy suppression MUST NOT disarm
    the trigger). 2026-09-23 (b2f4dae9): the route-(b) hint is RETIRED
    end-to-end; the judge + resolver row still fire so the suspect-
    pending case remains log-reconstructible, but NO message is
    injected. Deny path intact.

    Pin: ``busy_descendants=0`` when only PAUSED descendants exist
    (the live count is 1 — PAUSED is live-for-deny-protection, but
    NOT busy for trigger suppression). The trigger stays armed and
    the judge fires.
    """
    monkeypatch.setattr(
        "daemon.services.attestation_report_judge._invoke_judge_llm",
        _invoke_no,
    )
    # PAUSED is live (counted), NOT busy (excluded from busy subset).
    node, manager, ledger = _make_node(
        instance_id="paused-not-busy-it",
        busy_descendants=0,
        live_descendants=1,
    )
    with caplog.at_level(logging.INFO, logger="daemon.graph"), caplog.at_level(
        logging.INFO, logger="daemon.services.attestation_gate"
    ), caplog.at_level(
        logging.INFO,
        logger="daemon.services.attestation_resolver_activation",
    ):
        result = asyncio.run(
            node(
                _delegated_mission_with_marker(
                    "Awaiting child reply. Ending turn, will continue."
                ),
                config={"configurable": {"thread_id": "paused-not-busy-it"}},
            )
        )

    # Path (b): ALLOW log-only (NOT suppress) — judge fires, judge-no,
    # real pending → ALLOW with the (b)/(d)-with-pending route label
    # on the resolver row, but NO message injected.
    assert "messages" not in result, (
        "PAUSED-not-busy (b) path MUST be log-only after 2026-09-23; "
        f"got messages={result.get('messages')!r}"
    )
    ledger.increment.assert_not_called()  # (b) does not increment

    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "busy_descendants=0" in log_text
    assert "trigger_suppressed_by=" not in log_text  # Stage 3 (R5): key retired
    assert "trigger_source=" not in log_text  # Stage 3: key retired
    # Judge ran (fused).
    assert "event=leader_completion_gate_fused_judge" in log_text
    assert "verdict=not_complete" in log_text
    assert "resolver_outcome=allow_hint" in log_text


def test_busy_suppression_short_only_length_trigger_no_judge(
    monkeypatch, caplog
):
    """(AC-B5, spec point 5a variant) RUNNING child + length-only
    trigger (short ack with NO marker phrase) → suppression disarms
    the trigger; no judge, no hint.

    The brevity-only class: a leader awaiting a RUNNING child
    writes "OK, waiting on the tester." (short, no marker phrase).
    Length trigger fires alone (``trigger_source="length"``).
    Without busy suppression, the gate would route through the judge
    and inject a Completion Check Note on every short healthy wait.
    """
    call_count = {"n": 0}

    def _track(*args, **kwargs):
        call_count["n"] += 1
        return ('{"is_complete_report": false, "reason": "x"}', "fake-quick")

    monkeypatch.setattr(
        "daemon.services.attestation_report_judge._invoke_judge_llm",
        _track,
    )

    node, manager, ledger = _make_node(
        instance_id="busy-length-only-it",
        busy_descendants=1,
        live_descendants=1,
    )
    with caplog.at_level(logging.INFO, logger="daemon.graph"), caplog.at_level(
        logging.INFO, logger="daemon.services.attestation_gate"
    ), caplog.at_level(
        logging.INFO,
        logger="daemon.services.attestation_resolver_activation",
    ):
        result = asyncio.run(
            node(
                _delegated_short_marker_mission("OK, waiting on the tester."),
                config={"configurable": {"thread_id": "busy-length-only-it"}},
            )
        )

    assert result["attestation_route"] is None
    assert "messages" not in result
    ledger.increment.assert_not_called()
    assert call_count["n"] == 0, "judge fired despite busy suppression"

    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "busy_descendants=1" in log_text
    # Stage 3 (R5): the busy-mute lives in the predicate's b_fires
    # term — the suppression is observable via busy_descendants=N
    # plus NO judge row (asserted below/above).
    assert "trigger_suppressed_by=" not in log_text
    # length_trigger STILL recorded for observability.
    assert "length_trigger=True" in log_text
    # trigger_source cleared.
    # Stage 3 (R5/R6): the trigger-derivation fields retired.
    assert "trigger_source=" not in log_text


def test_deny_path_unchanged_when_busy_zero_no_markers_pending_nudge(
    monkeypatch, caplog
):
    """(AC-B6, spec point 5f) Boundary — busy=0 + markers + nothing
    pending → route (a) deny+nudge UNCHANGED. Busy suppression MUST
    NOT regress the existing deny path.

    Pin the historical contract: when trigger fires, busy=0, and
    nothing is pending, the gate denies + nudges + increments.
    LCA Stage-2 flip re-contract (2026-09-16): DELEGATED mission
    (the D10 meta-bypass exempts non-delegated; the deny band +
    judge-not-complete reaches the same outcome). The test exercises
    the busy-input plumbing explicitly (``busy_descendants=0``) so
    any future refactor that accidentally drops the busy read is
    caught.
    """
    monkeypatch.setattr(
        "daemon.services.attestation_report_judge._invoke_judge_llm",
        _invoke_no,
    )
    node, manager, ledger = _make_node(
        instance_id="busy-zero-deny-it",
        busy_descendants=0,  # explicit — boundary pin
        live_descendants=0,
    )
    with caplog.at_level(logging.INFO, logger="daemon.graph"), caplog.at_level(
        logging.INFO, logger="daemon.services.attestation_gate"
    ), caplog.at_level(
        logging.INFO,
        logger="daemon.services.attestation_resolver_activation",
    ):
        result = asyncio.run(
            node(
                _delegated_mission_with_marker(
                    "Awaiting your reply. Ending turn, will continue after."
                ),
                config={"configurable": {"thread_id": "busy-zero-deny-it"}},
            )
        )

    # DENY + nudge + counter+1, UNCHANGED.
    assert result["attestation_route"] == "agent"
    # Deny path injects a nudge message into state.
    assert "messages" in result
    ledger.increment.assert_called_once()
    assert ledger.increment.call_args.args[0] == "busy-zero-deny-it"
    ledger.reset.assert_not_called()
    ledger.set_escalated_and_reset.assert_not_called()

    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "busy_descendants=0" in log_text
    assert "trigger_suppressed_by=" not in log_text  # Stage 3 (R5): key retired
    # NOTE: quiet delegated rows never run the marker scan (the gate
    # scans allow-family decisions only) — the unified DENY band
    # drives the judge; marker_hit stays False on the canonical row.
    assert "marker_hit=False" in log_text
    # Judge ran (fused) — deny band rescue attempt, not_complete.
    assert "event=leader_completion_gate_fused_judge" in log_text
    assert "band=deny" in log_text
    assert "verdict=not_complete" in log_text
    assert "resolver_outcome=deny_nudge" in log_text


def test_deny_path_suite_unchanged_existing_marker_a_still_works(
    monkeypatch, caplog
):
    """(AC-B7, spec point 5g) Existing deny-path suite unchanged —
    the historical (a)/(b)/(c)/(d) marker routing tests must still
    pass with the busy plumbing added (busy=0 by default).

    This is a regression guard — the marker_a_deny_nudge_counter_
    increments test shape, re-run with the busy plumbing in place.
    LCA Stage-2 flip re-contract (2026-09-16): DELEGATED mission
    (D10 meta-bypass).
    """
    monkeypatch.setattr(
        "daemon.services.attestation_report_judge._invoke_judge_llm",
        _invoke_no,
    )
    # busy=0, live=0 — clean deny-band shape. Same shape as
    # test_marker_a_deny_nudge_counter_increments but exercises the
    # _make_node defaults (busy_descendants=0).
    node, manager, ledger = _make_node(instance_id="marker-a-it")
    with caplog.at_level(logging.INFO, logger="daemon.graph"), caplog.at_level(
        logging.INFO, logger="daemon.services.attestation_gate"
    ), caplog.at_level(
        logging.INFO,
        logger="daemon.services.attestation_resolver_activation",
    ):
        result = asyncio.run(
            node(
                _delegated_mission_with_marker(
                    "Awaiting your reply. Ending turn, will continue after."
                ),
                config={"configurable": {"thread_id": "marker-a-it"}},
            )
        )

    assert result["attestation_route"] == "agent"
    ledger.increment.assert_called_once()
    assert ledger.increment.call_args.args[0] == "marker-a-it"

    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "busy_descendants=0" in log_text
    assert "trigger_suppressed_by=" not in log_text


def test_busy_suppression_short_only_length_trigger_log_has_both_fields(
    monkeypatch, caplog
):
    """(AC-B8) Log schema pin — the canonical
    ``event=leader_completion_gate`` log row ALWAYS carries
    ``busy_descendants`` and ``trigger_suppressed_by``, even when
    busy=0 and the trigger is not suppressed. Operators grep for
    these two new fields; they are additive to the canonical 17-
    field tuple (same shape as ``length_trigger`` /
    ``final_word_count`` / ``trigger_source``).

    Drift pin: ``busy_descendants=<int> trigger_suppressed_by=<none-or-name>``
    on every log row. A future regression that drops these fields
    breaks grep-based busy-suppression forensics silently.
    """
    monkeypatch.setattr(
        "daemon.services.attestation_report_judge._invoke_judge_llm",
        _invoke_no,
    )
    node, manager, ledger = _make_node(instance_id="log-pin-it")
    with caplog.at_level(logging.INFO, logger="daemon.graph"), caplog.at_level(
        logging.INFO, logger="daemon.services.attestation_gate"
    ), caplog.at_level(
        logging.INFO,
        logger="daemon.services.attestation_resolver_activation",
    ):
        asyncio.run(
            node(
                _delegated_mission_with_marker(
                    "Awaiting child reply. Ending turn, will continue."
                ),
                config={"configurable": {"thread_id": "log-pin-it"}},
            )
        )

    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    # The canonical log row MUST carry both new fields, ALWAYS.
    assert "busy_descendants=" in log_text
    assert "trigger_suppressed_by=" not in log_text


def test_busy_suppression_both_triggers_combined_no_judge(
    monkeypatch, caplog
):
    """(AC-B9) RUNNING child + BOTH marker AND length triggers fire
    (``trigger_source="markers+length"`` would-be) → suppression
    disarms the WHOLE trigger; ``trigger_source=""`` (cleared);
    ``trigger_suppressed_by="busy_descendants"``; NO judge.

    Boundary pin for the combined-trigger case — the suppression
    branch must clear the combined ``trigger_source`` (not leave it
    as "markers+length") and stamp ``trigger_suppressed_by``.
    """
    call_count = {"n": 0}

    def _track(*args, **kwargs):
        call_count["n"] += 1
        return ('{"is_complete_report": false, "reason": "x"}', "fake-quick")

    monkeypatch.setattr(
        "daemon.services.attestation_report_judge._invoke_judge_llm",
        _track,
    )

    node, manager, ledger = _make_node(
        instance_id="busy-combined-it",
        busy_descendants=1,
        live_descendants=1,
    )
    with caplog.at_level(logging.INFO, logger="daemon.graph"), caplog.at_level(
        logging.INFO, logger="daemon.services.attestation_gate"
    ), caplog.at_level(
        logging.INFO,
        logger="daemon.services.attestation_resolver_activation",
    ):
        asyncio.run(
            node(
                _delegated_mission_with_marker(
                    "Awaiting tester reply. Ending turn."
                ),
                config={"configurable": {"thread_id": "busy-combined-it"}},
            )
        )

    assert call_count["n"] == 0, "judge fired despite busy suppression"
    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    # Both halves fire (marker + length) but the WHOLE trigger is
    # suppressed — ``trigger_source`` is force-cleared.
    assert "marker_hit=True" in log_text
    assert "length_trigger=True" in log_text
    # Stage 3 (R5/R6): the trigger-derivation fields retired.
    assert "trigger_source=" not in log_text
    # Stage 3 (R5): the busy-mute lives in the predicate's b_fires
    # term — the suppression is observable via busy_descendants=N
    # plus NO judge row (asserted below/above).
    assert "trigger_suppressed_by=" not in log_text
    # The combined string MUST NOT appear in the log when suppressed.
    assert "trigger_source=markers+length" not in log_text


def test_busy_suppression_dry_mode_log_only_no_judge_no_hint(
    monkeypatch, caplog
):
    """(AC-B10) DRY mode + busy suppression — dry-mode log-only
    contract preserved end-to-end. Busy suppression is layered on
    top of dry-mode: the trigger is suppressed, NO judge call,
    NO hint, plain allow, additive log fields stamped.

    The dry-mode ``allow unconditionally`` posture is preserved —
    busy suppression is silent on dry-mode too (no judge, no hint).
    Operators see the suppression in the log row
    (``trigger_suppressed_by=busy_descendants``).
    """
    monkeypatch.setattr(
        "daemon.services.attestation_report_judge._invoke_judge_llm",
        _invoke_no,
    )
    manager = MagicMock()
    manager.count_pending_children.return_value = 0
    manager.get_queued_or_expected_wakeups.return_value = 0
    manager.count_live_descendants.return_value = 1
    manager.count_busy_descendants.return_value = 1
    manager.enqueue_message = MagicMock()
    manager.revive = MagicMock()
    manager.send_message = MagicMock()

    ledger = MagicMock()
    ledger.increment.return_value = 1
    ledger.reset.return_value = True
    ledger.set_escalated_and_reset.return_value = True
    ledger.get.return_value = 0

    # dry mode
    config = build_gate_config(
        "busy-dry-it", GateSettings("dry", 3, 3), llm_judge_enabled=True,
    )
    node = create_attestation_gate_node(
        config,
        GateSettings("dry", 3, 3),
        manager,
        "busy-dry-it",
        denied_count_getter=lambda: 0,
        ledger=ledger,
    )
    with caplog.at_level(logging.INFO, logger="daemon.graph"), caplog.at_level(
        logging.INFO, logger="daemon.services.attestation_gate"
    ), caplog.at_level(
        logging.INFO,
        logger="daemon.services.attestation_resolver_activation",
    ):
        result = asyncio.run(
            node(
                _delegated_mission_with_marker(
                    "Awaiting tester reply. Ending turn."
                ),
                config={"configurable": {"thread_id": "busy-dry-it"}},
            )
        )

    # DRY mode → no route, no hint, no counter change, no judge.
    assert result["attestation_route"] is None
    assert "messages" not in result
    ledger.increment.assert_not_called()

    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "decision=dry_log" in log_text
    assert "busy_descendants=1" in log_text
    # Stage 3 (R5): the busy-mute lives in the predicate's b_fires
    # term — the suppression is observable via busy_descendants=N
    # plus NO judge row (asserted below/above).
    assert "trigger_suppressed_by=" not in log_text
    assert "marker_hit=True" in log_text
    # Stage 3 (R5/R6): the trigger-derivation fields retired.
    assert "trigger_source=" not in log_text
    # No judge row.
    assert (
        "event=leader_completion_gate_marker_judge " not in log_text
        and "event=leader_completion_gate_marker_judge\n" not in log_text
    )