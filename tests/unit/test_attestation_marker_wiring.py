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
    COMPLETION_CHECK_NOTE_TEXT,
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
    """
    manager = MagicMock()
    manager.count_pending_children.return_value = 0
    manager.get_queued_or_expected_wakeups.return_value = 0
    manager.count_live_descendants.return_value = 0
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
    """Delegated mission WITH attestation in window — must skip scan."""
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


# Stub LLM invoker factories (mirror test_attestation_judge_wiring shape)


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
    return caplog


async def _invoke_yes(config, user_payload, *, timeout_s):
    return (
        '{"is_complete_report": true, "reason": "genuine report"}',
        "fake-quick",
    )


async def _invoke_no(config, user_payload, *, timeout_s):
    return (
        '{"is_complete_report": false, "reason": "mid-work status"}',
        "fake-quick",
    )


async def _invoke_unparsable(config, user_payload, *, timeout_s):
    return ("Sorry, I cannot help with that.", "fake-quick")


async def _invoke_timeout(config, user_payload, *, timeout_s):
    raise asyncio.TimeoutError()


async def _invoke_error(config, user_payload, *, timeout_s):
    raise RuntimeError("boom")


# ─────────────────────────────────────────────────────────────────────────────
# (a) markers + judge-no + nothing pending → CONVERT TO DENY + nudge
# ─────────────────────────────────────────────────────────────────────────────


def test_marker_a_deny_nudge_counter_increments(monkeypatch, caplog):
    """(a) markers + judge-no + nothing pending → DENY + nudge + counter+1.

    The b08f40fe-class kill: a quick-question turn whose final
    AIMessage reads "Awaiting your reply. Ending turn, will continue
    after your reply." (mid-work phrasing) but the conditional gate
    is OFF (no send_message dispatched) — the gate would otherwise
    allow. The marker scan fires; the judge says no; nothing is
    pending; the gate converts to DENY + nudge + counter increment.
    """
    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", _invoke_no)

    node, manager, ledger = _make_node(instance_id="marker-a-it")
    with caplog.at_level(logging.INFO, logger="daemon.graph"), caplog.at_level(
        logging.INFO, logger="daemon.services.attestation_gate"
    ):
        result = asyncio.run(
            node(
                _quick_question_with_marker(
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

    # Log fields present — the marker-path judge row + the routing line.
    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "event=leader_completion_gate_marker_judge" in log_text
    assert "verdict=no" in log_text
    assert "marker-path a" in log_text
    assert "[AttestationGate] marker-path a instance=" in log_text


# ─────────────────────────────────────────────────────────────────────────────
# (b) markers + judge-no + real pending → ALLOW + checkpoint-durable hint
# ─────────────────────────────────────────────────────────────────────────────


def test_marker_b_hint_injection_no_deny_no_counter(monkeypatch, caplog):
    """(b) markers + judge-no + real pending → ALLOW + hint, no deny.

    The "real wakeup is en route" branch — the gate allows the turn
    to end so the wake-up arrives, but injects a checkpoint-durable
    Completion Check Note alongside END so the leader has a record
    of the mid-work phrasing on the next turn.
    """
    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", _invoke_no)

    manager = MagicMock()
    manager.count_pending_children.return_value = 1  # REAL pending
    manager.get_queued_or_expected_wakeups.return_value = 0
    manager.count_live_descendants.return_value = 0
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
    ):
        result = asyncio.run(
            node(
                _delegated_mission_with_marker(
                    "Awaiting child reply. Ending turn, will continue."
                ),
                config={"configurable": {"thread_id": "marker-b-it"}},
            )
        )

    # ALLOWED + hint — NO counter, NO deny, NO re-route.
    assert "messages" in result
    # NO attestation_route hint → END.
    assert result["attestation_route"] is None
    # NO counter increment — (b) path is allow-with-hint, not deny.
    ledger.increment.assert_not_called()
    ledger.reset.assert_not_called()
    ledger.set_escalated_and_reset.assert_not_called()

    # Hint message rides the state — checkpoint-durable HumanMessage
    # with the canonical Completion Check Note body.
    hint = result["messages"][0]
    assert hint.content == COMPLETION_CHECK_NOTE_TEXT
    assert hint.content.startswith(
        "[SYSTEM CONTEXT: Completion Check Note]"
    )

    # Log fields present.
    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "event=leader_completion_gate_marker_judge" in log_text
    assert "verdict=no" in log_text
    assert "marker-path b" in log_text
    assert "[AttestationGate] marker-path b instance=" in log_text


# ─────────────────────────────────────────────────────────────────────────────
# (c) markers + judge-yes → ALLOW normally
# ─────────────────────────────────────────────────────────────────────────────


def test_marker_c_judge_yes_allows_normally(monkeypatch, caplog):
    """(c) markers + judge-yes → ALLOW normally, no nudge, no counter.

    The judge confirms a genuine completion report despite the
    mid-work phrasing (e.g., "Ending turn" in a literal completion
    paragraph). The gate allows the END.
    """
    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", _invoke_yes)

    node, manager, ledger = _make_node(instance_id="marker-c-it")
    with caplog.at_level(logging.INFO, logger="daemon.graph"), caplog.at_level(
        logging.INFO, logger="daemon.services.attestation_gate"
    ):
        result = asyncio.run(
            node(
                _quick_question_with_marker(
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
    assert "event=leader_completion_gate_marker_judge" in log_text
    assert "verdict=yes" in log_text
    assert "[AttestationGate] marker-path judge-yes" in log_text


# ─────────────────────────────────────────────────────────────────────────────
# (d1) markers + judge-error + nothing pending → DENY (path "d")
# ─────────────────────────────────────────────────────────────────────────────


def test_marker_d_error_with_nothing_pending_deny(monkeypatch, caplog):
    """(d) markers + judge-error/timeout/unparsable + nothing pending → DENY.

    Conservative fall-through — a judge infrastructure error stays on
    the conservative side (deny) when nothing is pending. Same as
    (a) but routed via path "d" because the judge didn't return a
    clean verdict.
    """
    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", _invoke_error)

    node, manager, ledger = _make_node(instance_id="marker-d1-it")
    with caplog.at_level(logging.INFO, logger="daemon.graph"), caplog.at_level(
        logging.INFO, logger="daemon.services.attestation_gate"
    ):
        result = asyncio.run(
            node(
                _quick_question_with_marker(
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
    assert "marker-path d" in log_text
    assert "[AttestationGate] marker-path d instance=" in log_text


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
    ):
        result = asyncio.run(
            node(
                _delegated_mission_with_marker(
                    "Awaiting child. Ending turn."
                ),
                config={"configurable": {"thread_id": "marker-d2-it"}},
            )
        )

    # ALLOWED + hint — NO counter, NO deny, NO re-route.
    assert "messages" in result
    assert result["attestation_route"] is None
    ledger.increment.assert_not_called()

    hint = result["messages"][0]
    assert hint.content == COMPLETION_CHECK_NOTE_TEXT

    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "verdict=timeout" in log_text
    assert "marker-path d" in log_text


# ─────────────────────────────────────────────────────────────────────────────
# (d3) markers + judge-unparsable + nothing pending → DENY (path "d")
# ─────────────────────────────────────────────────────────────────────────────


def test_marker_d_unparsable_with_nothing_pending_deny(monkeypatch, caplog):
    """(d) markers + judge-unparsable + nothing pending → DENY.

    Unparsable JSON is a judge infrastructure error (the LLM did not
    return a strict JSON object). Conservative fall-through to deny.
    """
    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", _invoke_unparsable)

    node, manager, ledger = _make_node(instance_id="marker-d3-it")
    with caplog.at_level(logging.INFO, logger="daemon.graph"), caplog.at_level(
        logging.INFO, logger="daemon.services.attestation_gate"
    ):
        result = asyncio.run(
            node(
                _quick_question_with_marker(
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
    assert "marker-path d" in log_text


# ─────────────────────────────────────────────────────────────────────────────
# Wrapper-fault path — judge_completion_report_async RAISES (the (d)-path
# except branch in graph.py must still route conservatively). The d1/d2/d3
# tests above cover the NEVER-RAISES judge path (error/timeout/unparsable
# all return JudgeResult with is_complete_report=False). The tests below
# cover the wrapper-layer fault class — judge_completion_report_async
# ITSELF raises (config-load failure, import-time cycle, etc.).
# ─────────────────────────────────────────────────────────────────────────────


async def _invoke_judge_wrapper_fault(config, user_payload, *, timeout_s):
    """Simulate a wrapper-layer fault in judge_completion_report_async.

    The judge service is never-raises (timeout / unparsable / error all
    return JudgeResult). This stub instead RAISES at the public
    entry-point — the wrapper-layer except branch in graph.py must
    still route conservatively (a)/(b) based on R2 inputs.
    """
    raise RuntimeError("wrapper-layer fault (config load failure)")


def test_marker_wrapper_fault_with_nothing_pending_deny(
    monkeypatch, caplog
):
    """Wrapper fault + nothing pending → CONVERT TO DENY (path "d").

    The F2 fix — when ``judge_completion_report_async`` itself RAISES,
    the marker-path except branch must NOT silently ALLOW. With
    nothing pending (no children / wakeups / live descendants), the
    conservative routing is (a) — CONVERT TO DENY + nudge + counter
    increment. Pins the F2 P0 close end-to-end.
    """
    monkeypatch.setattr(
        judge_mod,
        "judge_completion_report_async",
        _invoke_judge_wrapper_fault,
    )

    node, manager, ledger = _make_node(instance_id="marker-wf1-it")
    with caplog.at_level(logging.INFO, logger="daemon.graph"), caplog.at_level(
        logging.INFO, logger="daemon.services.attestation_gate"
    ):
        result = asyncio.run(
            node(
                _quick_question_with_marker(
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
        "event=leader_completion_gate_marker_judge_error" in log_text
    ), "F2: the wrapper-fault path MUST emit the canonical error row"
    assert "decision=fail_safe_marker_d" in log_text
    assert "marker-path d" in log_text
    assert (
        "[AttestationGate] marker-path d instance=" in log_text
    )
    assert (
        "wrapper-fault path" in log_text
    ), "F2: the (d)-routing log line MUST mark the wrapper-fault path"


def test_marker_wrapper_fault_with_real_pending_hint(monkeypatch, caplog):
    """Wrapper fault + real pending work → ALLOW + hint (path "d").

    The wake-up is en route; the judge wrapper faulted; the gate falls
    through to allow-with-hint so the wake-up can still arrive. NO
    counter write, NO deny, NO re-route — the turn still ends. Pins
    the F2 P0 close on the (b)-shape half of the conservative routing.
    """
    monkeypatch.setattr(
        judge_mod,
        "judge_completion_report_async",
        _invoke_judge_wrapper_fault,
    )

    manager = MagicMock()
    manager.count_pending_children.return_value = 1  # REAL pending
    manager.get_queued_or_expected_wakeups.return_value = 0
    manager.count_live_descendants.return_value = 0
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
    ):
        result = asyncio.run(
            node(
                _delegated_mission_with_marker(
                    "Awaiting child. Ending turn."
                ),
                config={"configurable": {"thread_id": "marker-wf2-it"}},
            )
        )

    # ALLOWED + hint — NO counter, NO deny, NO re-route.
    assert "messages" in result
    assert result["attestation_route"] is None
    ledger.increment.assert_not_called()
    ledger.reset.assert_not_called()
    ledger.set_escalated_and_reset.assert_not_called()

    # Hint rides — the helper-built (b)-path Completion Check Note.
    hint = result["messages"][0]
    assert hint.content == COMPLETION_CHECK_NOTE_TEXT
    assert hint.content.startswith(
        "[SYSTEM CONTEXT: Completion Check Note]"
    )

    # Log rows: the wrapper-fault error row + the (d)-path routing
    # line via the hint path.
    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert (
        "event=leader_completion_gate_marker_judge_error" in log_text
    )
    assert "decision=fail_safe_marker_d" in log_text
    assert "marker-path d" in log_text
    assert (
        "[AttestationGate] marker-path d instance=" in log_text
    )
    assert (
        "wrapper-fault path" in log_text
    ), "F2: the (d)-routing log line MUST mark the wrapper-fault path"


# ─────────────────────────────────────────────────────────────────────────────
# Kill-switch OFF — markers logged, NO judge call, gate falls through to allow
# ─────────────────────────────────────────────────────────────────────────────


def test_marker_kill_switch_env_off_no_judge_call(monkeypatch, caplog):
    """Kill-switch OFF → markers logged, NO judge call, plain ALLOW.

    With the judge disabled, marker-only signal is too weak to deny
    — allow + log. DECIDED (do not relitigate; see decisions.md
    D-ENTRY 2026-09-11).
    """
    calls = []

    async def must_not_be_called(config, user_payload, *, timeout_s):
        calls.append(True)
        return ('{"is_complete_report": true, "reason": "yes"}', "fake-quick")

    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", must_not_be_called)
    # Real env var + real resolver reset.
    monkeypatch.setenv("ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED", "0")
    judge_resolver_mod.reset_llm_judge_resolver_for_tests()
    # Sanity: the real resolver returns False.
    assert judge_resolver_mod.is_llm_judge_enabled() is False

    node, manager, ledger = _make_node(instance_id="marker-ks-off-it")
    with caplog.at_level(logging.INFO, logger="daemon.graph"), caplog.at_level(
        logging.INFO, logger="daemon.services.attestation_gate"
    ):
        result = asyncio.run(
            node(
                _quick_question_with_marker(
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
    # operator sees the hit in dry-log soak); but no judge row.
    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "marker_hit=True" in log_text
    assert "event=leader_completion_gate_marker_judge" not in log_text


def test_marker_kill_switch_config_off_no_judge_call(monkeypatch):
    """``llm_judge_enabled=False`` in gate_config → judge never called.

    Same kill-switch behavior driven by the gate-config flag (not the
    env var). The env var is the operator-tunable seam; the gate-config
    flag is the wiring-layer seam (test embeddings flip this to bypass
    the real call without touching the env).
    """
    calls = []

    async def must_not_be_called(config, user_payload, *, timeout_s):
        calls.append(True)
        return ('{"is_complete_report": true, "reason": "yes"}', "fake-quick")

    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", must_not_be_called)

    node, manager, ledger = _make_node(
        instance_id="marker-ks-cfg-off-it", llm_judge_enabled=False
    )
    result = asyncio.run(
        node(
            _quick_question_with_marker(
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
    """No markers in the message tail → no judge call, plain ALLOW.

    The marker scan is the TRIGGER; no marker ⇒ no judge. This is
    the cost-control guard — the cheap scan saves the expensive
    judge call for ~all completion turns that don't include mid-work
    phrasing.
    """
    calls = []

    async def must_not_be_called(config, user_payload, *, timeout_s):
        calls.append(True)
        return ('{"is_complete_report": true, "reason": "yes"}', "fake-quick")

    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", must_not_be_called)

    node, manager, ledger = _make_node(instance_id="marker-none-it")
    with caplog.at_level(logging.INFO, logger="daemon.graph"), caplog.at_level(
        logging.INFO, logger="daemon.services.attestation_gate"
    ):
        result = asyncio.run(
            node(
                _quick_question_with_marker(
                    "The answer is 42; explanation follows. "
                    "Nothing pending, all shipped."
                ),
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

    # marker_hit is False in the canonical gate log row.
    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "marker_hit=False" in log_text


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

    async def must_not_be_called(config, user_payload, *, timeout_s):
        calls.append(True)
        return ('{"is_complete_report": true, "reason": "yes"}', "fake-quick")

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

    The canonical ``event=leader_completion_gate`` log row carries
    ``marker_path=<pending>`` — emitted by ``evaluate()`` BEFORE the
    judge runs (the marker scan is synchronous but the judge is
    async). The FINAL ``marker_path=a`` lands on the routing log
    line emitted by the graph node. The test pins both: the
    canonical-row pending state AND the routing-line final value.
    """
    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", _invoke_no)

    node, manager, ledger = _make_node(instance_id="marker-log-a-it")
    with caplog.at_level(logging.INFO, logger="daemon.graph"), caplog.at_level(
        logging.INFO, logger="daemon.services.attestation_gate"
    ):
        asyncio.run(
            node(
                _quick_question_with_marker(
                    "Awaiting your reply. Ending turn."
                ),
                config={"configurable": {"thread_id": "marker-log-a-it"}},
            )
        )

    log_text = "\n".join(rec.getMessage() for rec in caplog.records)

    # marker-row fields on the canonical gate log line.
    assert "marker_hit=True" in log_text
    assert "marker_terms=ending turn,awaiting" in log_text
    # Canonical row says "<pending>" (judge hasn't run yet).
    assert "marker_path=<pending>" in log_text

    # judge-row fields on the marker-path judge log line.
    assert "event=leader_completion_gate_marker_judge" in log_text
    assert "verdict=no" in log_text
    assert "llm_judge_latency_ms=" in log_text
    assert "llm_judge_model=fake-quick" in log_text
    assert "llm_judge_error_class=" in log_text

    # The FINAL marker_path="a" lands on the routing log line.
    assert "[AttestationGate] marker-path a instance=" in log_text


def test_log_marker_fields_present_on_no_marker_path(monkeypatch, caplog):
    """No markers → marker_hit=False, marker_terms=<none>,
    marker_path=<none> — no judge row."""
    node, manager, ledger = _make_node(instance_id="marker-log-none-it")
    with caplog.at_level(logging.INFO, logger="daemon.graph"), caplog.at_level(
        logging.INFO, logger="daemon.services.attestation_gate"
    ):
        asyncio.run(
            node(
                _quick_question_with_marker(
                    "All work shipped. Done. Nothing pending."
                ),
                config={"configurable": {"thread_id": "marker-log-none-it"}},
            )
        )

    log_text = "\n".join(rec.getMessage() for rec in caplog.records)

    # Additive marker fields on the canonical row.
    assert "marker_hit=False" in log_text
    assert "marker_terms=<none>" in log_text
    assert "marker_path=<none>" in log_text

    # No judge row.
    assert "event=leader_completion_gate_marker_judge" not in log_text


# ─────────────────────────────────────────────────────────────────────────────
# Verbatim incident phrase — the b08f40fe-class regression guard
# ─────────────────────────────────────────────────────────────────────────────


def test_verbatim_incident_phrase_killed_by_path_a(monkeypatch, caplog):
    """The VERBATIM incident phrase MUST be killed by the marker path.

    The b08f40fe-class regression guard: leader b08f40fe's final
    AIMessage was "Awaiting final four: C12a/b/c + blame-worker. Then
    I aggregate and write RESULTS. Ending turn." This is a delegated
    mission ending without attestation. After the orphan-fix
    (live_descendants=0) the gate's natural decision is DENIED
    (R2 predicate catches it). For this test we want to exercise
    the marker path on a tree that would otherwise ALLOW — set
    live_descendants=0 + use a delegated mission so the natural
    decision is "DENIED" via the R2 predicate... wait — if pending=0
    + wakeups=0 + live_descendants=0, the natural decision IS
    DENIED. So we exercise the marker path on a tree that
    would-otherwise-ALLOW via the conditional_attestation_required
    branch — but that's not delegated. Use a quick-question mission
    (no send_message, conditional gate OFF, no attestation). The
    natural decision is ALLOWED with attestation_required=False;
    the marker scan fires; judge says no; nothing pending → path
    (a) → CONVERT TO DENY + nudge. This is exactly the spec's
    "Ending turn, will continue after your reply" example
    applied to the verbatim incident text.
    """
    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", _invoke_no)

    node, manager, ledger = _make_node(instance_id="b08f40fe-it")
    with caplog.at_level(logging.INFO, logger="daemon.graph"), caplog.at_level(
        logging.INFO, logger="daemon.services.attestation_gate"
    ):
        result = asyncio.run(
            node(
                _quick_question_with_marker(
                    "Awaiting final four: C12a/b/c + blame-worker. "
                    "Then I aggregate and write RESULTS. Ending turn."
                ),
                config={"configurable": {"thread_id": "b08f40fe-it"}},
            )
        )

    # The b08f40fe-class kill: marker fires → judge-no → DENY + nudge.
    assert "messages" in result
    assert result["attestation_route"] == "agent"
    ledger.increment.assert_called_once()

    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "verdict=no" in log_text
    assert "marker-path a" in log_text


# ─────────────────────────────────────────────────────────────────────────────
# Conditional-attestation OFF quick-question with mid-work phrasing — the
# brief's exact example "Ending turn, will continue after your reply"
# ─────────────────────────────────────────────────────────────────────────────


def test_quick_question_with_marker_reaches_judge(monkeypatch, caplog):
    """Brief example: a quick answer ending 'Ending turn, will continue
    after your reply' must reach the judge.

    This is the canonical quick-question mission (no send_message,
    conditional gate OFF, no attestation). The natural decision is
    ALLOWED with attestation_required=False. The marker scan runs
    anyway (per the brief's "Do NOT skip the attestation_required=false
    allow — scan it too") and the judge gets called.
    """
    calls = []

    async def record_call(config, user_payload, *, timeout_s):
        calls.append(user_payload)
        return (
            '{"is_complete_report": false, "reason": "mid-work status"}',
            "fake-quick",
        )

    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", record_call)

    node, manager, ledger = _make_node(instance_id="marker-qq-it")
    with caplog.at_level(logging.INFO, logger="daemon.graph"), caplog.at_level(
        logging.INFO, logger="daemon.services.attestation_gate"
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

    # Judge WAS called — the marker reached it.
    assert len(calls) == 1

    # Nothing pending → (a) → DENY + nudge.
    assert "messages" in result
    assert result["attestation_route"] == "agent"
    ledger.increment.assert_called_once()