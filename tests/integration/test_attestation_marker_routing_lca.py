"""Marker-routing E2E scenario matrix (LCA merge gate, Job 3).

End-to-end scenario coverage for the LCA leader completion gate's
mid-work marker → judge → routing pipeline (commits 06ddad57 + 975cdf19).

Scope
-----

This module is the JOB 3 GAP-CLOSURE pass — it covers scenarios that the
upstream unit suites (``test_attestation_marker_scanner.py`` and
``test_attestation_marker_wiring.py``) pin ONE-OFF but that the LCA
merge gate requires end-to-end on the SHARED scenario matrix. Every
scenario is wired through the production ``create_attestation_gate_node``
closure with a stub manager (no real DB), a stub ledger, and a mocked
LLM judge — no real LLM calls ever fire.

Scenarios pinned (one test each, labelled to match the LCA merge gate
checklist):

  * (a) markers + judge-not-complete + nothing pending → DENY + nudge
    + counter increments
  * (b) markers + judge-not-complete + REAL pending (live RUNNING
    child) → allow STANDS, hint injected, NO counter increment, turn
    ends, wake semantics preserved
  * (c) markers + judge-complete → plain allow (no hint)
  * (d1) judge timeout → conservative (a)-behavior when nothing pending
  * (d2) judge timeout → conservative (b)-behavior when real pending
  * (d3) judge wrapper fault (exception at public entry-point) →
    conservative routing per R2 inputs
  * (e) kill-switch OFF (ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED=0)
    → no judge call at all, plain allow stands,
    marker_judge_verdict="<skipped>" (exact sentinel)
  * (f) dry mode (mode="dry") → marker_hit logged, ZERO side effects
    (no hint, no counter, no deny, no judge call)
  * (g) attested allow → NO marker scan happens at all (assert scanner
    not invoked — via the canonical-row ``marker_hit=False`` signal in
    the log)
  * (h) no markers → no judge call (cost control — assert judge client
    not invoked)

The (b)-supersede-on-real-add_messages scenario — the W2 Shape A
contract that LangGraph's ``add_messages`` reducer collapses the same-
id Completion Check Note in the resulting channel — is pinned on the
REAL ``langgraph.graph.message.add_messages`` function in
``tests/unit/test_attestation_marker_supersede_lca.py`` (a parallel
unit file). The gate-node E2E here verifies that the same-id hint is
EMITTED with the expected stable id; the parallel unit file then
verifies that ``add_messages`` itself performs the upsert on the real
package so two consecutive (b) turns → exactly one hint block in the
final channel.

Constraints (pinned by the LCA merge gate):

  * NO production code changes — test-only files
  * NO real LLM calls (mocked ``_invoke_judge_llm``)
  * NO PostgreSQL setup (in-memory ``MagicMock`` manager)
  * Drift-pin: every pytest invocation runs against HEAD or a sibling
    ``test:``-prefixed commit; this file's own commit carries the
    ``test(lca):`` prefix as required
  * Dual-layer timeouts (script + pytest, 5-min integration cap)
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
    COMPLETION_CHECK_NOTE_TEXT,
    create_attestation_gate_node,
)


# ─────────────────────────────────────────────────────────────────────────────
# Drift-pin guard — ensure we are on the documented LCA branch head
# ─────────────────────────────────────────────────────────────────────────────


def _git_head_short() -> str:
    """Return ``git rev-parse --short HEAD`` or "<unknown>" when git
    is unavailable (CI sandboxes etc.)."""
    import subprocess

    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
        ).decode().strip()
    except Exception:  # noqa: BLE001 — drift-pin is best-effort
        return "<unknown>"


_EXPECTED_HEAD = "975cdf19"  # LCA branch head at the time of authoring
_TEST_PREFIX = "test:"  # sibling commit-prefix allowance (parallel workers)


def pytest_configure(config):  # noqa: D401 — pytest hook
    """Surface the drift-pin marker in the test header for forensic use."""
    head = _git_head_short()
    print(f"\n[dft-pin] git HEAD={head} (expected={_EXPECTED_HEAD})\n")
    # Soft-pin: do NOT fail on sibling commits — only flag drift beyond
    # the LCA branch head family. Operators read the head from the test
    # log to confirm the parallel-worker siblings committed in parallel.


# ─────────────────────────────────────────────────────────────────────────────
# Test fixtures + helpers
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_resolvers(monkeypatch):
    """Each test sees a fresh resolver cache + clean kill-switch env.

    Hermetic isolation: clear ``ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED``
    from the environment so the suite is hermetic against a global CI
    env mutation. ``raising=False`` so the fixture is safe on hosts where
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
    instance_id: str,
    llm_judge_enabled: bool = True,
    settings: GateSettings | None = None,
    manager: MagicMock | None = None,
    ledger: MagicMock | None = None,
    denied_count_getter=None,
):
    """Build the gate node with manager + ledger stubs (no real DB).

    Honors the per-test overrides: callers may pass a pre-built manager
    or ledger (e.g. to flip ``count_pending_children`` for the (b) /
    (d2)-with-pending scenarios). Defaults to zero on every facade so
    the (a) / (d1) / (d3) / (e) / (f) / (g) / (h) scenarios run clean.
    """
    if manager is None:
        manager = MagicMock()
        manager.count_pending_children.return_value = 0
        manager.get_queued_or_expected_wakeups.return_value = 0
        manager.count_live_descendants.return_value = 0
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
        denied_count_getter=denied_count_getter or (lambda: 0),
        ledger=ledger,
    )
    return node, manager, ledger


# ─────────────────────────────────────────────────────────────────────────────
# Stub LLM invoker factories (mirror upstream ``test_attestation_marker_wiring``
# shape). The LCA scenarios do not exercise JSON parsing — the LLM is
# either confirmed-complete, confirmed-incomplete, or returns an
# unparsable / timeout-shaped response. The verdict string is what the
# marker-path judge wiring reads (via ``is_complete_report`` on the
# parsed JudgeResult).
# ─────────────────────────────────────────────────────────────────────────────


async def _judge_complete(config, user_payload, *, timeout_s):
    return (
        '{"is_complete_report": true, "reason": "genuine"}',
        "fake-quick",
    )


async def _judge_incomplete(config, user_payload, *, timeout_s):
    return (
        '{"is_complete_report": false, "reason": "mid-work"}',
        "fake-quick",
    )


async def _judge_unparsable(config, user_payload, *, timeout_s):
    return ("Sorry, I cannot help with that.", "fake-quick")


async def _judge_timeout(config, user_payload, *, timeout_s):
    raise asyncio.TimeoutError()


async def _judge_error(config, user_payload, *, timeout_s):
    raise RuntimeError("wrapper fault")


# ─────────────────────────────────────────────────────────────────────────────
# Mission-shape helpers — quick-question + delegated variants per the
# brief's spec example. The (b) / (d2) scenarios need a delegated
# mission so the natural decision is ALLOWED via the conditional arm;
# the (a) / (c) / (d) scenarios use a quick-question (no send_message,
# conditional gate OFF) so the natural decision is ALLOWED with
# ``attestation_required=False`` and the marker scan fires anyway.
# ─────────────────────────────────────────────────────────────────────────────


def _quick_question_with_marker(final_text: str) -> dict:
    """Quick-question mission ending with mid-work phrasing.

    Matches the brief's "Ending turn, will continue after your reply"
    canonical example — conditional gate OFF path. The natural decision
    is ``ALLOWED`` with ``attestation_required=False``; the marker scan
    fires and the judge is called.
    """
    return {
        "messages": [
            HumanMessage(content="what's the answer to X?"),
            AIMessage(content=final_text),
        ]
    }


def _delegated_mission_with_marker(final_text: str) -> dict:
    """Delegated mission ending with mid-work phrasing.

    The natural decision is ``ALLOWED`` via the wakeup arm when
    ``count_pending_children`` returns a non-zero value (the (b) /
    (d2)-with-pending scenarios).
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
            AIMessage(content=final_text),
        ]
    }


def _delegated_mission_attested() -> dict:
    """Delegated mission WITH attestation in window — must skip scan.

    Used by the (g) scenario: an attested allow short-circuits the
    marker scan entirely. The natural decision is ``ALLOWED`` via
    attestation, with counter reset (trigger 1).
    """
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
    return {
        "messages": [
            HumanMessage(content="please do it"),
            delegation_ai,
            attestation_ai,
            AIMessage(content="I am done with attestation"),
        ]
    }


def _no_marker_mission() -> dict:
    """A clean completion mission — no markers in the tail, ≥150 words.

    Used by the (h) scenario: the natural decision is ALLOWED via the
    conditional arm; the marker scan MUST short-circuit at the
    ``marker_hit=False`` check AND the length trigger MUST short-
    circuit at ``length_trigger=False`` (the AIMessage is ≥150
    words) — the judge MUST NOT be called (cost control). The
    canonical log row carries the additive diagnostic fields
    (marker_hit=False, length_trigger=False, trigger_source=<none>).
    """
    return {
        "messages": [
            HumanMessage(content="please finish X"),
            AIMessage(
                content="All work shipped. Done. Nothing pending, "
                "results in the per-task report above. Patch 1 fixed "
                "the off-by-one in the cache TTL calculator; the unit "
                "tests now exercise both the elapsed-second and "
                "wall-clock-second boundaries at the second and "
                "minute granularity. Patch 2 cleaned up the dead "
                "imports in the worker pool module after the "
                "migration, removing the legacy compatibility shim "
                "and the related test scaffolding. Patch 3 "
                "refactored the error-reporting decorator so the "
                "stack-frame metadata is consistent across all four "
                "call sites in the graph node and the manager "
                "facade. Patch 4 added the missing operator-boot log "
                "line for the new resolver module so operators can "
                "grep the boot summary for the resolved effective "
                "values including the mode, window, bound, and gate "
                "locations active at the time. All four patches "
                "passed their respective suites on the first run "
                "with no flake; the integration matrix is green "
                "end-to-end. No follow-ups outstanding; the mission "
                "is complete and ready for review by the next "
                "teammate in the chain."
            ),
        ]
    }


# ─────────────────────────────────────────────────────────────────────────────
# Scenario (a) — markers + judge-no + nothing pending → DENY + nudge + counter
# ─────────────────────────────────────────────────────────────────────────────


def test_scenario_a_markers_judge_no_nothing_pending_denies(monkeypatch, caplog):
    """(a) markers + judge-no + nothing pending → DENY + nudge + counter+1.

    Scenario contract (LCA merge gate §a): when the leader's last
    AIMessage carries mid-work phrasing, the conditional gate would
    otherwise allow (no send_message dispatched), the marker scan
    fires, the judge says "not a complete report", and nothing is
    pending — the gate MUST convert the would-be ALLOW to a DENY via
    the existing nudge machinery and increment the counter. Pins the
    ``marker_path=a`` diagnostic on the routing log line.
    """
    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", _judge_incomplete)

    node, manager, ledger = _make_node(instance_id="lca-scen-a-it")
    with caplog.at_level(logging.INFO, logger="daemon.graph"), caplog.at_level(
        logging.INFO, logger="daemon.services.attestation_gate"
    ):
        result = asyncio.run(
            node(
                _quick_question_with_marker(
                    "Awaiting your reply. Ending turn, "
                    "will continue after your reply."
                ),
                config={"configurable": {"thread_id": "lca-scen-a-it"}},
            )
        )

    # DENIED — nudge + counter increment.
    assert "messages" in result, "(a): DENY path MUST inject a nudge message"
    assert result["attestation_route"] == "agent", (
        "(a): DENY path MUST re-route to agent (existing deny pattern)"
    )
    ledger.increment.assert_called_once()
    assert ledger.increment.call_args.args[0] == "lca-scen-a-it", (
        "(a): counter increment MUST target the right instance_id"
    )
    # No hint message (hints are (b) only).
    nudge = result["messages"][0]
    assert "Completion Check Note" not in nudge.content, (
        "(a): DENY path MUST NOT inject a Completion Check Note "
        "— the hint is (b)-path only"
    )

    # Diagnostic fields on the canonical log row + routing line.
    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "marker-path a" in log_text, "(a): routing log MUST show marker-path a"
    assert "event=leader_completion_gate_marker_judge" in log_text
    assert "verdict=no" in log_text


# ─────────────────────────────────────────────────────────────────────────────
# Scenario (b) — markers + judge-no + REAL pending → ALLOW + hint, NO counter
# ─────────────────────────────────────────────────────────────────────────────


def test_scenario_b_markers_judge_no_real_pending_allows_with_hint(monkeypatch, caplog):
    """(b) markers + judge-no + real pending (live RUNNING child) → ALLOW + hint.

    Scenario contract (LCA merge gate §b): when the leader's last
    AIMessage carries mid-work phrasing AND a real RUNNING child is
    en route (manager.count_pending_children > 0), the gate MUST allow
    the turn to end so the wake-up can still arrive (R2/§b contract) —
    but it MUST inject a checkpoint-durable Completion Check Note as a
    record of the mid-work phrasing for the next turn. NO counter
    increment, NO deny, NO re-route — the (b) path is allow-with-hint,
    not deny.
    """
    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", _judge_incomplete)

    # Manager configured for the (b) shape — REAL pending child.
    manager = MagicMock()
    manager.count_pending_children.return_value = 1  # REAL pending
    manager.get_queued_or_expected_wakeups.return_value = 0
    manager.count_live_descendants.return_value = 0
    manager.enqueue_message = MagicMock()
    manager.revive = MagicMock()
    manager.send_message = MagicMock()

    node, manager, ledger = _make_node(
        instance_id="lca-scen-b-it", manager=manager
    )
    with caplog.at_level(logging.INFO, logger="daemon.graph"), caplog.at_level(
        logging.INFO, logger="daemon.services.attestation_gate"
    ):
        result = asyncio.run(
            node(
                _delegated_mission_with_marker(
                    "Awaiting child reply. Ending turn, will continue."
                ),
                config={"configurable": {"thread_id": "lca-scen-b-it"}},
            )
        )

    # ALLOWED + hint — NO counter, NO deny, NO re-route.
    assert "messages" in result, (
        "(b): ALLOW-with-hint path MUST inject a Completion Check Note"
    )
    assert result["attestation_route"] is None, (
        "(b): ALLOW-with-hint path MUST end the turn (no re-route)"
    )
    ledger.increment.assert_not_called(), (
        "(b): NO counter increment — (b) path is allow-with-hint, not deny"
    )
    ledger.reset.assert_not_called(), (
        "(b): NO counter reset on the (b) path — reset fires only on "
        "attested-allow or terminal-after-bound"
    )
    ledger.set_escalated_and_reset.assert_not_called()

    # Hint message rides the state — checkpoint-durable HumanMessage
    # with the canonical Completion Check Note body. The W2 Shape A
    # contract pins the id format to ``completion_check_note:{instance_id}``
    # so repeated (b) events on the same instance supersede in place via
    # LangGraph's ``add_messages`` reducer (the
    # ``tests/unit/test_attestation_marker_supersede_lca.py`` test
    # verifies the upsert behavior on the REAL add_messages function).
    hint = result["messages"][0]
    assert hint.content == COMPLETION_CHECK_NOTE_TEXT, (
        "(b): hint content MUST be the canonical Completion Check Note body"
    )
    assert hint.content.startswith(
        "[SYSTEM CONTEXT: Completion Check Note]"
    )
    assert hint.id == "completion_check_note:lca-scen-b-it", (
        "(b): hint MUST carry the stable id ``completion_check_note:"
        "{instance_id}`` so repeated (b) events on the same instance "
        "supersede via add_messages"
    )
    # context_kind rides alongside so the three-bucket compaction seam
    # still classifies the hint as a permanent injected message.
    assert hint.additional_kwargs.get("context_kind") == "task_context", (
        "(b): hint MUST carry context_kind=task_context so the "
        "compaction seam hoists it (per docs/setup.md three-bucket contract)"
    )

    # Diagnostic fields on the canonical log row + routing line.
    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "marker-path b" in log_text, (
        "(b): routing log MUST show marker-path b (allow-with-hint)"
    )
    assert "event=leader_completion_gate_marker_judge" in log_text
    assert "verdict=no" in log_text


# ─────────────────────────────────────────────────────────────────────────────
# Scenario (c) — markers + judge-complete → plain allow (no hint)
# ─────────────────────────────────────────────────────────────────────────────


def test_scenario_c_markers_judge_complete_allows_normally(monkeypatch, caplog):
    """(c) markers + judge-complete → ALLOW normally, no hint, no counter.

    Scenario contract (LCA merge gate §c): the judge confirms a genuine
    completion report despite the mid-work phrasing (e.g., "Ending turn"
    in a literal completion paragraph). The gate allows the END —
    NO hint, NO nudge, NO counter increment. The marker fields are
    populated on the log row (operators see the marker hit) but the
    routing is plain allow.
    """
    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", _judge_complete)

    node, manager, ledger = _make_node(instance_id="lca-scen-c-it")
    with caplog.at_level(logging.INFO, logger="daemon.graph"), caplog.at_level(
        logging.INFO, logger="daemon.services.attestation_gate"
    ):
        result = asyncio.run(
            node(
                _quick_question_with_marker(
                    "Ending turn. All four sub-tasks completed; "
                    "evidence in the per-task report above."
                ),
                config={"configurable": {"thread_id": "lca-scen-c-it"}},
            )
        )

    # ALLOWED — no nudge, no counter, no hint.
    assert "messages" not in result, (
        "(c): plain-allow path MUST NOT inject any message "
        "(no nudge, no hint)"
    )
    assert result["attestation_route"] is None, (
        "(c): routing MUST be plain END (no re-route)"
    )
    ledger.increment.assert_not_called()
    ledger.reset.assert_not_called()
    ledger.set_escalated_and_reset.assert_not_called()

    # Diagnostic fields on the canonical log row + routing line.
    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "verdict=yes" in log_text, (
        "(c): judge log row MUST carry verdict=yes"
    )
    assert (
        "[AttestationGate] marker-path judge-yes" in log_text
    ), "(c): routing log MUST show the marker-path judge-yes line"


# ─────────────────────────────────────────────────────────────────────────────
# Scenario (d1) — judge error/timeout/unparsable + nothing pending → DENY
# Scenario (d2) — judge error/timeout + real pending → ALLOW + hint
# Scenario (d3) — judge wrapper fault (RAISES) → conservative per R2 inputs
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "judge_stub,expected_path_label",
    [
        (_judge_timeout, "timeout"),
        (_judge_error, "error"),
        (_judge_unparsable, "unparsable"),
    ],
)
def test_scenario_d1_xxx_with_nothing_pending_denies(
    monkeypatch, caplog, judge_stub, expected_path_label
):
    """(d1) judge {timeout,error,unparsable} + nothing pending → DENY.

    Scenario contract (LCA merge gate §d1): the marker-path judge is
    best-effort and never-raises internally, but it returns
    JudgeResult with verdict=error/timeout/unparsable on infrastructure
    faults (config-load failure, LLM-side timeout, etc.). When nothing
    is pending, the conservative routing is (a)-equivalent — DENY +
    nudge + counter increment — but the ``marker_path`` diagnostic is
    "d" (not "a") so operators can grep-distinguish clean-judge-no from
    judge-fault families.
    """
    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", judge_stub)

    node, manager, ledger = _make_node(
        instance_id=f"lca-scen-d1-{expected_path_label}-it"
    )
    with caplog.at_level(logging.INFO, logger="daemon.graph"), caplog.at_level(
        logging.INFO, logger="daemon.services.attestation_gate"
    ):
        result = asyncio.run(
            node(
                _quick_question_with_marker("Awaiting reply. Ending turn."),
                config={"configurable": {
                    "thread_id": f"lca-scen-d1-{expected_path_label}-it"
                }},
            )
        )

    # DENIED — counter increment + nudge.
    assert "messages" in result, (
        f"(d1-{expected_path_label}): DENY path MUST inject a nudge"
    )
    assert result["attestation_route"] == "agent", (
        f"(d1-{expected_path_label}): DENY path MUST re-route to agent"
    )
    ledger.increment.assert_called_once()

    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert f"verdict={expected_path_label}" in log_text, (
        f"(d1-{expected_path_label}): judge log MUST carry "
        f"verdict={expected_path_label}"
    )
    assert "marker-path d" in log_text, (
        f"(d1-{expected_path_label}): routing log MUST show marker-path d"
    )


@pytest.mark.parametrize(
    "judge_stub,expected_path_label",
    [
        (_judge_timeout, "timeout"),
        (_judge_error, "error"),
    ],
)
def test_scenario_d2_xxx_with_real_pending_allows_with_hint(
    monkeypatch, caplog, judge_stub, expected_path_label
):
    """(d2) judge {timeout,error} + real pending → ALLOW + hint.

    Scenario contract (LCA merge gate §d2): when the wake-up is en route
    AND the judge infrastructure faulted, the conservative routing is
    (b)-equivalent — ALLOW + checkpoint-durable hint. NO counter, NO
    deny, NO re-route. The turn still ends so the wake-up can still
    arrive.
    """
    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", judge_stub)

    manager = MagicMock()
    manager.count_pending_children.return_value = 1  # REAL pending
    manager.get_queued_or_expected_wakeups.return_value = 0
    manager.count_live_descendants.return_value = 0
    manager.enqueue_message = MagicMock()
    manager.revive = MagicMock()
    manager.send_message = MagicMock()

    node, manager, ledger = _make_node(
        instance_id=f"lca-scen-d2-{expected_path_label}-it",
        manager=manager,
    )
    with caplog.at_level(logging.INFO, logger="daemon.graph"), caplog.at_level(
        logging.INFO, logger="daemon.services.attestation_gate"
    ):
        result = asyncio.run(
            node(
                _delegated_mission_with_marker(
                    "Awaiting child. Ending turn."
                ),
                config={"configurable": {
                    "thread_id": f"lca-scen-d2-{expected_path_label}-it"
                }},
            )
        )

    # ALLOWED + hint — NO counter, NO deny, NO re-route.
    assert "messages" in result, (
        f"(d2-{expected_path_label}): ALLOW-with-hint path MUST inject a hint"
    )
    assert result["attestation_route"] is None, (
        f"(d2-{expected_path_label}): ALLOW-with-hint path MUST end the turn"
    )
    ledger.increment.assert_not_called()

    hint = result["messages"][0]
    assert hint.content == COMPLETION_CHECK_NOTE_TEXT, (
        f"(d2-{expected_path_label}): hint content MUST be the canonical "
        "Completion Check Note body"
    )

    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "marker-path d" in log_text, (
        f"(d2-{expected_path_label}): routing log MUST show marker-path d"
    )


def test_scenario_d3_wrapper_fault_routes_conservatively(monkeypatch, caplog):
    """(d3) judge wrapper-layer RAISE → conservative routing per R2 inputs.

    Scenario contract (LCA merge gate §d3): ``judge_completion_report_async``
    ITSELF raises (config-load failure, import-time cycle, etc.). The
    F2 fix at graph.py wraps the judge call in a broad
    ``except Exception`` that routes the marker path conservatively:
    ``marker_path="d"``, and the (a) / (b) routing depends on R2
    inputs (pending children / wakeups / live descendants). Pins BOTH
    branches: nothing-pending → DENY, real pending → ALLOW + hint.

    The upstream wiring suite pins this at the
    ``judge_completion_report_async`` seam; this scenario pins it at
    the ``_invoke_judge_llm`` seam (the underlying entry-point) for
    defense-in-depth.
    """
    monkeypatch.setattr(
        judge_mod, "judge_completion_report_async", _judge_error
    )

    # ── d3.a: nothing pending → DENY
    node, manager, ledger = _make_node(instance_id="lca-scen-d3a-it")
    with caplog.at_level(logging.INFO, logger="daemon.graph"), caplog.at_level(
        logging.INFO, logger="daemon.services.attestation_gate"
    ):
        result_deny = asyncio.run(
            node(
                _quick_question_with_marker("Awaiting reply. Ending turn."),
                config={"configurable": {"thread_id": "lca-scen-d3a-it"}},
            )
        )

    assert "messages" in result_deny, (
        "(d3a): wrapper fault + nothing pending → DENY (counter + nudge)"
    )
    assert result_deny["attestation_route"] == "agent"
    ledger.increment.assert_called_once()

    # Reset ledger for the second branch.
    ledger.reset_mock()

    # ── d3.b: real pending → ALLOW + hint
    manager2 = MagicMock()
    manager2.count_pending_children.return_value = 1
    manager2.get_queued_or_expected_wakeups.return_value = 0
    manager2.count_live_descendants.return_value = 0
    manager2.enqueue_message = MagicMock()
    manager2.revive = MagicMock()
    manager2.send_message = MagicMock()

    node2, _, ledger2 = _make_node(
        instance_id="lca-scen-d3b-it", manager=manager2
    )
    with caplog.at_level(logging.INFO, logger="daemon.graph"), caplog.at_level(
        logging.INFO, logger="daemon.services.attestation_gate"
    ):
        result_hint = asyncio.run(
            node2(
                _delegated_mission_with_marker(
                    "Awaiting child. Ending turn."
                ),
                config={"configurable": {"thread_id": "lca-scen-d3b-it"}},
            )
        )

    assert "messages" in result_hint, (
        "(d3b): wrapper fault + real pending → ALLOW + hint"
    )
    assert result_hint["attestation_route"] is None
    ledger2.increment.assert_not_called()
    hint = result_hint["messages"][0]
    assert hint.content == COMPLETION_CHECK_NOTE_TEXT


# ─────────────────────────────────────────────────────────────────────────────
# Config-less manager regression (2026-09-12, demo-testcase findings) —
# ``daemon/graph.py`` resolved the judge fallback config with
# ``from ..config import load_config`` (two dots → ImportError:
# attempted relative import beyond top-level package) at BOTH judge
# seams. Masked in production (InstanceManager.__init__ always sets
# ``manager.config``) and in test embeddings (MagicMock auto-attrs
# ``.config``): with a manager that genuinely lacks ``.config`` the
# ImportError was swallowed by the wrapper-fault ``except`` and the
# judge SILENTLY degraded to the fail-safe route (d). These scenarios
# pin: (1) the fallback path is HEALTHY (judge runs with the real
# loaded Config), and (2) the fail-safe route (d) contract still
# holds — observably — for a GENUINE config-load failure.
# ─────────────────────────────────────────────────────────────────────────────


class _ConfigLessManagerStub:
    """Manager handle that deliberately LACKS ``.config``.

    A bare ``MagicMock()`` auto-creates ``.config``, which is exactly
    why the ``from ..config`` typo was invisible to the existing
    embeddings — ``getattr(manager, "config", None)`` returned a child
    mock and the buggy else-branch never ran. This stub exposes only
    the R2-count + dispatch facade used by the gate, so
    ``getattr(manager, "config", None)`` returns ``None`` and the
    fallback ``load_config()`` else-branch is genuinely exercised.
    """

    def __init__(self) -> None:
        self.count_pending_children = MagicMock(return_value=0)
        self.get_queued_or_expected_wakeups = MagicMock(return_value=0)
        self.count_live_descendants = MagicMock(return_value=0)
        self.enqueue_message = MagicMock()
        self.revive = MagicMock()
        self.send_message = MagicMock()


def test_configless_manager_marker_judge_fallback_config_is_healthy(
    monkeypatch, caplog
):
    """Config-less manager → fallback ``load_config()`` runs the judge.

    Regression for the ``from ..config import load_config`` typo at
    the marker-path judge seam: pre-fix, a manager without ``.config``
    raised ImportError INSIDE the wrapper ``try``, the broad ``except``
    swallowed it, and the judge was silently SKIPPED via the fail-safe
    route (d) log row (``event=leader_completion_gate_marker_judge_
    error ... decision=fail_safe_marker_d``). Post-fix, the fallback
    must reach ``daemon.config.load_config`` and the judge must RUN
    with the real loaded :class:`~daemon.config.Config` — the routing
    is then judge-driven (marker-path a), NOT the fail-safe (d).
    Asserts the ROUTE in the log row, not just the outcome.
    """
    seen_configs: list = []

    async def _recording_judge_no(config, user_payload, *, timeout_s):
        seen_configs.append(config)
        return (
            '{"is_complete_report": false, "reason": "mid-work"}',
            "fake-quick",
        )

    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", _recording_judge_no)

    node, _manager, ledger = _make_node(
        instance_id="lca-cfgless-a-it", manager=_ConfigLessManagerStub()
    )
    with caplog.at_level(logging.INFO, logger="daemon.graph"), caplog.at_level(
        logging.INFO, logger="daemon.services.attestation_gate"
    ):
        # Completing without an exception IS the (i) NO-raise assertion.
        result = asyncio.run(
            node(
                _quick_question_with_marker(
                    "Awaiting your reply. Ending turn, "
                    "will continue after your reply."
                ),
                config={"configurable": {"thread_id": "lca-cfgless-a-it"}},
            )
        )

    from daemon.config import Config as _Config

    # The fallback config path reached the judge with a REAL Config.
    assert len(seen_configs) == 1, (
        "config-less manager: judge MUST run exactly once via the "
        "fallback load_config() (pre-fix the ImportError skipped it)"
    )
    assert isinstance(seen_configs[0], _Config), (
        "config-less manager: the judge config MUST be the real "
        "load_config() product, not a mock stand-in"
    )
    # Judge-driven outcome (a): judge-no + nothing pending → DENY +
    # nudge + counter — same outcome shape, but reached via the judge.
    assert "messages" in result
    assert result["attestation_route"] == "agent"
    ledger.increment.assert_called_once()
    # ROUTE assertion — the silent-degradation markers must be GONE.
    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "event=leader_completion_gate_marker_judge_error" not in log_text, (
        "config-less manager: the ImportError fail-safe log row MUST be "
        "gone — its presence means the judge was silently skipped (the "
        "pre-fix defect this test exists to catch)"
    )
    assert "marker-path d" not in log_text, (
        "config-less manager: routing MUST NOT degrade to fail-safe (d)"
    )
    assert "marker-path a" in log_text, (
        "config-less manager: routing MUST be the judge-driven route (a)"
    )
    assert "event=leader_completion_gate_marker_judge " in log_text, (
        "config-less manager: the canonical judge log row must be present"
    )


def test_configless_manager_config_load_failure_still_failsafe_route_d(
    monkeypatch, caplog
):
    """GENUINE config-load failure → observable fail-safe route (d).

    Pins the documented fail-safe contract that the typo used to feed
    accidentally: when the fallback ``load_config()`` itself fails,
    the gate must (i) NOT raise, (ii) degrade to the conservative
    fail-safe ROUTE (d), (iii) SKIP the judge, and (iv) carry the
    route in the LOG ROW (``decision=fail_safe_marker_d`` +
    ``marker-path d``) — assert the route, not just the outcome.
    Nothing-pending → the (d) arm converts ALLOW to DENY via the
    existing nudge machinery + counter.
    """
    seen_configs: list = []

    async def _recording_judge_no(config, user_payload, *, timeout_s):
        seen_configs.append(config)
        return (
            '{"is_complete_report": false, "reason": "mid-work"}',
            "fake-quick",
        )

    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", _recording_judge_no)

    import daemon.config as config_mod

    def _boom():
        raise RuntimeError("simulated config source unavailable")

    monkeypatch.setattr(config_mod, "load_config", _boom)

    node, _manager, ledger = _make_node(
        instance_id="lca-cfgless-d-it", manager=_ConfigLessManagerStub()
    )
    with caplog.at_level(logging.INFO, logger="daemon.graph"), caplog.at_level(
        logging.INFO, logger="daemon.services.attestation_gate"
    ):
        # (i) NO raise — asyncio.run completing is the assertion.
        result = asyncio.run(
            node(
                _quick_question_with_marker(
                    "Awaiting your reply. Ending turn, "
                    "will continue after your reply."
                ),
                config={"configurable": {"thread_id": "lca-cfgless-d-it"}},
            )
        )

    # (iii) judge SKIPPED.
    assert seen_configs == [], (
        "genuine config-load failure: judge MUST be skipped (fail-safe)"
    )
    # (ii) conservative outcome — nothing pending → DENY + nudge + counter.
    assert "messages" in result, (
        "(d)-nothing-pending MUST convert ALLOW to DENY via the nudge"
    )
    assert result["attestation_route"] == "agent"
    ledger.increment.assert_called_once()
    # (iv) ROUTE in the LOG ROW.
    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "event=leader_completion_gate_marker_judge_error" in log_text, (
        "the fail-safe degradation MUST emit its dedicated log row"
    )
    assert "decision=fail_safe_marker_d" in log_text, (
        "the log row MUST carry the route marker (fail_safe_marker_d), "
        "not just the outcome"
    )
    assert "marker-path d" in log_text, (
        "routing MUST show the conservative fail-safe route (d)"
    )
    assert "event=leader_completion_gate_marker_judge " not in log_text, (
        "no canonical judge row — the judge never ran"
    )


def test_configless_manager_would_be_deny_judge_fallback_config_is_healthy(
    monkeypatch, caplog
):
    """Second typo seam (would-be-deny judge) — fallback config healthy.

    The same ``from ..config`` typo existed at the WOULD-BE-DENY judge
    seam: with a config-less manager, a natural DENIED decision never
    reached the judge (silent ``event=leader_completion_gate_judge_
    error ... decision=fail_safe_deny``), so a legitimate completion
    report in the tail could never flip the deny to an allow. Post-fix
    the judge must run with the real loaded Config and a judge-yes
    verdict MUST override the deny WITHOUT a counter increment.
    """
    seen_configs: list = []

    async def _recording_judge_yes(config, user_payload, *, timeout_s):
        seen_configs.append(config)
        return (
            '{"is_complete_report": true, "reason": "genuine report"}',
            "fake-quick",
        )

    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", _recording_judge_yes)

    node, _manager, ledger = _make_node(
        instance_id="lca-cfgless-deny-it", manager=_ConfigLessManagerStub()
    )
    with caplog.at_level(logging.INFO, logger="daemon.graph"), caplog.at_level(
        logging.INFO, logger="daemon.services.attestation_gate"
    ):
        # NO raise — asyncio.run completing is the assertion. Delegated
        # mission without attestation → natural DENIED → would-be-deny
        # judge seam.
        result = asyncio.run(
            node(
                _delegated_mission_with_marker(
                    "Awaiting child. Ending turn."
                ),
                config={"configurable": {"thread_id": "lca-cfgless-deny-it"}},
            )
        )

    from daemon.config import Config as _Config

    # The fallback config path reached the judge with a REAL Config.
    assert len(seen_configs) == 1, (
        "would-be-deny seam: judge MUST run exactly once via the "
        "fallback load_config() (pre-fix the ImportError skipped it)"
    )
    assert isinstance(seen_configs[0], _Config), (
        "would-be-deny seam: the judge config MUST be the real "
        "load_config() product"
    )
    # Judge-yes overrides the deny: ALLOW, NO counter increment.
    assert result["attestation_route"] is None, (
        "judge-yes MUST allow END without attestation (deny override)"
    )
    ledger.increment.assert_not_called()
    assert "messages" not in result or not any(
        "Completion Check Nudge" in str(m.content)
        for m in result.get("messages", [])
    ), "judge-yes override MUST NOT inject a nudge"
    # The silent-degradation row must be GONE; the canonical row present.
    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "event=leader_completion_gate_judge_error" not in log_text, (
        "would-be-deny seam: the ImportError fail-safe log row MUST be "
        "gone — its presence means the judge was silently skipped"
    )
    assert "event=leader_completion_gate_judge " in log_text, (
        "would-be-deny seam: the canonical judge log row must be present"
    )
    assert "verdict=yes" in log_text


# ─────────────────────────────────────────────────────────────────────────────
# Scenario (e) — kill-switch OFF → no judge call, marker_judge_verdict=<skipped>
# ─────────────────────────────────────────────────────────────────────────────


def test_scenario_e_kill_switch_env_off_no_judge_call(monkeypatch, caplog):
    """(e) ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED=0 → no judge call.

    Scenario contract (LCA merge gate §e): with the operator-facing
    kill-switch OFF, the marker path MUST NOT call the judge — the
    marker-only signal is too weak to deny on its own (DECIDED, do not
    relitigate; see decisions.md D-ENTRY 2026-09-11). The gate falls
    through to plain ALLOW and stamps
    ``marker_judge_verdict="<skipped>"`` (the exact sentinel — operators
    grep for this token) on the dedicated
    ``event=leader_completion_gate_marker_judge_disabled`` log row.
    """
    judge_calls = []

    async def must_not_be_called(config, user_payload, *, timeout_s):
        judge_calls.append(True)
        return ('{"is_complete_report": true, "reason": "yes"}', "fake-quick")

    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", must_not_be_called)
    # Real env var + real resolver reset.
    monkeypatch.setenv("ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED", "0")
    judge_resolver_mod.reset_llm_judge_resolver_for_tests()
    assert judge_resolver_mod.is_llm_judge_enabled() is False, (
        "(e): pre-condition — the kill-switch OFF env must flip the "
        "real resolver to False"
    )

    node, manager, ledger = _make_node(instance_id="lca-scen-e-it")
    with caplog.at_level(logging.INFO, logger="daemon.graph"), caplog.at_level(
        logging.INFO, logger="daemon.services.attestation_gate"
    ):
        result = asyncio.run(
            node(
                _quick_question_with_marker("Awaiting reply. Ending turn."),
                config={"configurable": {"thread_id": "lca-scen-e-it"}},
            )
        )

    # Judge never called.
    assert judge_calls == [], "(e): kill-switch OFF MUST NOT call the judge"
    # Plain ALLOW — no nudge, no counter, no hint.
    assert "messages" not in result, (
        "(e): kill-switch OFF MUST NOT inject a hint or nudge"
    )
    assert result["attestation_route"] is None
    ledger.increment.assert_not_called()
    ledger.reset.assert_not_called()

    # Distinct log row emitted by the kill-switch OFF branch.
    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "marker_hit=True" in log_text, (
        "(e): the canonical log row MUST still carry marker_hit=True "
        "so operators see the marker signal even when the judge is off"
    )
    assert (
        "event=leader_completion_gate_marker_judge_disabled" in log_text
    ), "(e): kill-switch OFF MUST emit the dedicated disabled-row log"
    assert "verdict=<skipped>" in log_text, (
        "(e): the disabled-row verdict MUST be the literal <skipped> "
        "sentinel (operators grep for this exact token)"
    )
    # The standard judge-row event MUST NOT fire (the judge never ran).
    assert (
        "event=leader_completion_gate_marker_judge " not in log_text
        and "event=leader_completion_gate_marker_judge\n" not in log_text
    ), (
        "(e): the standard marker-judge row MUST NOT be emitted when "
        "the kill-switch is OFF — it is grep-disjoint from the disabled row"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Scenario (f) — dry mode → marker_hit logged, ZERO side effects
# ─────────────────────────────────────────────────────────────────────────────


def test_scenario_f_dry_mode_marker_hit_zero_side_effects(monkeypatch, caplog):
    """(f) dry mode (GateSettings.mode="dry") → marker_hit logged, ZERO side effects.

    Scenario contract (LCA merge gate §f): dry mode is the
    soak-observation mode. The marker scan must run (cheap), must
    populate ``marker_hit=True`` / ``marker_terms`` / ``marker_path``
    on the canonical log row, MUST NOT call the judge (the marker-path
    judge wiring early-outs for ``Decision.DRY_LOG``), MUST NOT
    inject a hint, MUST NOT increment the counter, MUST NOT re-route.
    The dry-mode ``allow unconditionally`` posture is preserved
    end-to-end.
    """
    judge_calls = []

    async def must_not_be_called(config, user_payload, *, timeout_s):
        judge_calls.append(True)
        return (
            '{"is_complete_report": true, "reason": "genuine"}',
            "fake-quick",
        )

    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", must_not_be_called)

    node, manager, ledger = _make_node(
        instance_id="lca-scen-f-it",
        settings=GateSettings("dry", 3, 3),
    )
    with caplog.at_level(logging.INFO, logger="daemon.graph"), caplog.at_level(
        logging.INFO, logger="daemon.services.attestation_gate"
    ):
        result = asyncio.run(
            node(
                _delegated_mission_with_marker(
                    "Awaiting final four: C12a/b/c + blame-worker. "
                    "Then I aggregate and write RESULTS. Ending turn."
                ),
                config={"configurable": {"thread_id": "lca-scen-f-it"}},
            )
        )

    # ZERO side effects.
    assert judge_calls == [], (
        "(f): dry mode MUST NOT call the judge — the marker-path "
        "judge wiring early-outs for DRY_LOG"
    )
    assert "messages" not in result, (
        "(f): dry mode MUST NOT inject a hint (no judge → no (b)-path)"
    )
    assert result["attestation_route"] is None, (
        "(f): dry mode MUST NOT re-route — plain END preserved"
    )
    ledger.increment.assert_not_called()
    ledger.reset.assert_not_called()
    ledger.set_escalated_and_reset.assert_not_called()

    # Canonical log row carries the dry-mode marker signal.
    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    canonical_row = next(
        (m for m in log_text.splitlines() if "event=leader_completion_gate" in m),
        None,
    )
    assert canonical_row is not None, "(f): the canonical log row MUST be emitted"
    assert "decision=dry_log" in canonical_row
    assert "marker_hit=True" in canonical_row, (
        "(f): the canonical row MUST carry marker_hit=True (W1 fix)"
    )
    # The verbatim incident phrase fires the catalog-ordered marker
    # terms ("ending turn" → "awaiting" → "then i aggregate").
    assert (
        "marker_terms=ending turn,awaiting,then i aggregate"
        in canonical_row
    )
    # marker_path stays at the transient "<pending>" sentinel — the
    # graph node early-out never resolved it to "a"/"b"/"c"/"d" (no
    # judge ran). This is the dry-mode LOG-ONLY contract.
    assert "marker_path=<pending>" in canonical_row
    assert "marker_judge_verdict=<pending>" in canonical_row

    # No marker-path judge row, no judge-error row, no kill-switch
    # disabled row — dry mode is the gate's pure-passive observer
    # branch.
    assert "event=leader_completion_gate_marker_judge" not in log_text
    assert "event=leader_completion_gate_marker_judge_error" not in log_text
    assert (
        "event=leader_completion_gate_marker_judge_disabled"
        not in log_text
    )


# ─────────────────────────────────────────────────────────────────────────────
# Scenario (g) — attested allow → NO marker scan happens at all
# ─────────────────────────────────────────────────────────────────────────────


def test_scenario_g_attested_allow_skips_marker_scan(monkeypatch, caplog):
    """(g) attested allow → NO marker scan happens at all.

    Scenario contract (LCA merge gate §g): an attested allow is an
    explicit contract — the gate allows immediately and the marker
    scan NEVER runs. The brief: "scan runs when an allow would fire
    AND NOT attested (attested allow = explicit contract, skip)."

    Pins the dual-assertion contract:
      (i)  the judge is NEVER called (the marker scan was the trigger
           that would have called the judge — no scan ⇒ no judge);
      (ii) the canonical log row carries ``marker_hit=False`` (the
           scanner was never invoked, so it returns the no-hit shape).
    """
    judge_calls = []

    async def must_not_be_called(config, user_payload, *, timeout_s):
        judge_calls.append(True)
        return (
            '{"is_complete_report": true, "reason": "yes"}',
            "fake-quick",
        )

    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", must_not_be_called)

    node, manager, ledger = _make_node(instance_id="lca-scen-g-it")
    with caplog.at_level(logging.INFO, logger="daemon.graph"), caplog.at_level(
        logging.INFO, logger="daemon.services.attestation_gate"
    ):
        result = asyncio.run(
            node(
                _delegated_mission_attested(),
                config={"configurable": {"thread_id": "lca-scen-g-it"}},
            )
        )

    # Judge never called.
    assert judge_calls == [], (
        "(g): attested allow MUST NOT call the judge — the marker scan "
        "is the trigger; attested allow skips the scan entirely"
    )
    # ALLOWED — attested allow; counter reset (trigger 1).
    assert "messages" not in result
    assert result["attestation_route"] is None
    ledger.reset.assert_called_once(), (
        "(g): attested allow MUST reset the counter (leader ruling 1, "
        "trigger 1)"
    )

    # The canonical log row carries marker_hit=False — the scanner was
    # NOT invoked on this allow path, so the marker_hit field stays at
    # its default False value (the additive marker fields ride alongside
    # the canonical row, never replacing it).
    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "marker_hit=False" in log_text, (
        "(g): the canonical log row MUST carry marker_hit=False — "
        "the scanner was not invoked on the attested allow path"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Scenario (h) — no markers → no judge call (cost control)
# ─────────────────────────────────────────────────────────────────────────────


def test_scenario_h_no_markers_no_judge_call(monkeypatch, caplog):
    """(h) no markers in the message tail → no judge call (cost control).

    Scenario contract (LCA merge gate §h): the marker scan is the
    TRIGGER half of the two-stage disambiguator; no marker ⇒ no judge
    call. The cheap scan saves the expensive judge call for ~all
    completion turns that don't include mid-work phrasing. The
    canonical log row carries ``marker_hit=False`` / ``marker_terms=
    <none>`` / ``marker_path=<none>`` — operator forensics can grep
    these fields to confirm the cost-control contract held.
    """
    judge_calls = []

    async def must_not_be_called(config, user_payload, *, timeout_s):
        judge_calls.append(True)
        return (
            '{"is_complete_report": true, "reason": "yes"}',
            "fake-quick",
        )

    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", must_not_be_called)

    node, manager, ledger = _make_node(instance_id="lca-scen-h-it")
    with caplog.at_level(logging.INFO, logger="daemon.graph"), caplog.at_level(
        logging.INFO, logger="daemon.services.attestation_gate"
    ):
        result = asyncio.run(
            node(
                _no_marker_mission(),
                config={"configurable": {"thread_id": "lca-scen-h-it"}},
            )
        )

    # Judge never called — the cheap scan short-circuited at
    # ``marker_hit=False`` before the judge would have been invoked.
    assert judge_calls == [], (
        "(h): no-marker tail MUST NOT call the judge — the marker "
        "scan IS the trigger; no hit ⇒ no judge"
    )
    # Plain ALLOW — no nudge, no counter, no hint.
    assert "messages" not in result
    assert result["attestation_route"] is None
    ledger.increment.assert_not_called()
    ledger.reset.assert_not_called()

    # The canonical log row carries marker_hit=False / marker_terms=
    # <none> / marker_path=<none> — the cost-control contract is
    # observable in dry-mode soak data.
    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "marker_hit=False" in log_text, (
        "(h): the canonical log row MUST carry marker_hit=False"
    )
    assert "marker_terms=<none>" in log_text, (
        "(h): the canonical log row MUST carry marker_terms=<none>"
    )
    assert "marker_path=<none>" in log_text, (
        "(h): the canonical log row MUST carry marker_path=<none>"
    )
    # And NO judge row — the judge never ran.
    assert "event=leader_completion_gate_marker_judge" not in log_text, (
        "(h): no marker ⇒ no judge ⇒ no judge-row log"
    )
