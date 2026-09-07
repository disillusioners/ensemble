"""Gate-level integration tests for the LCA inline-LLM completion-report judge.

Wires the real :func:`create_attestation_gate_node` (the gate's
production closure shape) with a :class:`MagicMock` manager and patches
:func:`daemon.services.attestation_report_judge._invoke_judge_llm` to
return canned responses. Verifies the spec contract:

* ``judge-yes`` → ALLOWED, no nudge, no counter increment
* ``judge-no``  → deny + nudge + counter increment (pre-feature path)
* judge error / timeout / unparsable → all conservative deny+nudge
* kill-switch OFF → judge never called (gate_config flag AND env var)
* judge NOT called on non-deny paths (attested, not-required,
  pending-wakeup)
* log fields present on judge paths

The test isolation: each test patches the LLM invoker via
``monkeypatch.setattr`` so no real LLM call is made.
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
from daemon.graph import create_attestation_gate_node


# ─────────────────────────────────────────────────────────────────────────────
# Test fixtures + helpers
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_resolvers(monkeypatch):
    """Each test sees a fresh resolver cache so env mutations stick.

    NIT-7: also clear ``ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED``
    from the environment so the suite is hermetic against a global CI
    env mutation (an outer ``.env`` or CI runner that flips the
    kill-switch OFF would otherwise leak into the wiring tests and
    silently disable the judge for every assertion). ``raising=False``
    so the fixture is safe on hosts where the var is already unset.
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
    instance_id: str = "judge-it",
    llm_judge_enabled: bool = True,
    denied_count_getter=None,
) -> tuple:
    """Build the gate node with manager + ledger stubs (no real DB)."""
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


def _delegated_mission_without_attest(
    final_text: str = "I am done without attesting"
) -> dict:
    """Standard delegated mission: real user → AI send_message → AI done."""
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


# Stub LLM invoker factories


async def _invoke_yes(config, user_payload, *, timeout_s):
    return (
        '{"is_complete_report": true, "reason": "detailed outcomes delivered"}',
        "fake-quick",
    )


async def _invoke_no(config, user_payload, *, timeout_s):
    return (
        '{"is_complete_report": false, "reason": "short status only"}',
        "fake-quick",
    )


async def _invoke_unparsable(config, user_payload, *, timeout_s):
    return ("Sorry, I cannot help with that.", "fake-quick")


async def _invoke_timeout(config, user_payload, *, timeout_s):
    raise asyncio.TimeoutError()


async def _invoke_error(config, user_payload, *, timeout_s):
    raise RuntimeError("boom")


# ─────────────────────────────────────────────────────────────────────────────
# (a) judge-yes → ALLOWED, no nudge, no counter write
# ─────────────────────────────────────────────────────────────────────────────


def test_judge_yes_allows_without_nudge_no_counter_increment(monkeypatch, caplog):
    """Judge-yes verdict → ALLOWED, no nudge, no counter increment."""
    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", _invoke_yes)
    monkeypatch.setattr(
        judge_resolver_mod, "_parse_llm_judge_enabled",
        lambda source: True,
    )

    node, manager, ledger = _make_node(instance_id="judge-yes-it")
    with caplog.at_level(logging.INFO, logger="daemon.graph"):
        result = asyncio.run(
            node(
                _delegated_mission_without_attest(),
                config={"configurable": {"thread_id": "judge-yes-it"}},
            )
        )

    # ALLOWED — no nudge, no counter.
    assert "messages" not in result
    assert result["attestation_route"] is None
    ledger.increment.assert_not_called()
    ledger.reset.assert_not_called()
    manager.enqueue_message.assert_not_called()
    manager.revive.assert_not_called()

    # Log fields present.
    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "event=leader_completion_gate_judge" in log_text
    assert "verdict=yes" in log_text
    assert "llm_judge_model=fake-quick" in log_text
    assert "[AttestationGate] judge-yes override" in log_text


# ─────────────────────────────────────────────────────────────────────────────
# (b) judge-no → deny+nudge, counter increments
# ─────────────────────────────────────────────────────────────────────────────


def test_judge_no_falls_through_to_deny_nudge_increments_counter(
    monkeypatch, caplog
):
    """Judge-no → existing deny+nudge path runs unchanged."""
    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", _invoke_no)

    node, manager, ledger = _make_node(instance_id="judge-no-it")
    with caplog.at_level(logging.INFO, logger="daemon.graph"):
        result = asyncio.run(
            node(
                _delegated_mission_without_attest(),
                config={"configurable": {"thread_id": "judge-no-it"}},
            )
        )

    # DENIED — nudge + counter increment.
    assert "messages" in result
    assert result["attestation_route"] == "agent"
    ledger.increment.assert_called_once()
    assert ledger.increment.call_args.args[0] == "judge-no-it"

    # Log fields present.
    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "event=leader_completion_gate_judge" in log_text
    assert "verdict=no" in log_text
    assert "[AttestationGate] deny instance=" in log_text


# ─────────────────────────────────────────────────────────────────────────────
# (c) judge-timeout → deny+nudge (conservative fall-through)
# ─────────────────────────────────────────────────────────────────────────────


def test_judge_timeout_falls_through_to_deny_nudge(monkeypatch, caplog):
    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", _invoke_timeout)

    node, manager, ledger = _make_node(instance_id="judge-tmo-it")
    with caplog.at_level(logging.INFO, logger="daemon.graph"):
        result = asyncio.run(
            node(
                _delegated_mission_without_attest(),
                config={"configurable": {"thread_id": "judge-tmo-it"}},
            )
        )

    # DENIED — timeout is conservative.
    assert "messages" in result
    assert result["attestation_route"] == "agent"
    ledger.increment.assert_called_once()

    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "verdict=timeout" in log_text
    assert "llm_judge_error_class=TimeoutError" in log_text


# ─────────────────────────────────────────────────────────────────────────────
# (d) judge-error → deny+nudge
# ─────────────────────────────────────────────────────────────────────────────


def test_judge_generic_error_falls_through_to_deny_nudge(monkeypatch, caplog):
    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", _invoke_error)

    node, manager, ledger = _make_node(instance_id="judge-err-it")
    with caplog.at_level(logging.INFO, logger="daemon.graph"):
        result = asyncio.run(
            node(
                _delegated_mission_without_attest(),
                config={"configurable": {"thread_id": "judge-err-it"}},
            )
        )

    # DENIED — generic error is conservative.
    assert "messages" in result
    assert result["attestation_route"] == "agent"
    ledger.increment.assert_called_once()

    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "verdict=error" in log_text
    assert "llm_judge_error_class=RuntimeError" in log_text


# ─────────────────────────────────────────────────────────────────────────────
# (e) judge-unparsable → deny+nudge
# ─────────────────────────────────────────────────────────────────────────────


def test_judge_unparsable_falls_through_to_deny_nudge(monkeypatch, caplog):
    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", _invoke_unparsable)

    node, manager, ledger = _make_node(instance_id="judge-unp-it")
    with caplog.at_level(logging.INFO, logger="daemon.graph"):
        result = asyncio.run(
            node(
                _delegated_mission_without_attest(),
                config={"configurable": {"thread_id": "judge-unp-it"}},
            )
        )

    # DENIED — unparsable is conservative.
    assert "messages" in result
    assert result["attestation_route"] == "agent"
    ledger.increment.assert_called_once()

    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "verdict=unparsable" in log_text


# ─────────────────────────────────────────────────────────────────────────────
# (f) kill-switch OFF — gate_config flag
# ─────────────────────────────────────────────────────────────────────────────


def test_judge_not_called_when_gate_config_flag_off(monkeypatch, caplog):
    """``llm_judge_enabled=False`` in gate_config → judge never invoked."""
    calls = []

    async def must_not_be_called(config, user_payload, *, timeout_s):
        calls.append((config, user_payload))
        return ('{"is_complete_report": true, "reason": "yes"}', "fake-quick")

    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", must_not_be_called)

    node, manager, ledger = _make_node(
        instance_id="judge-off-it", llm_judge_enabled=False
    )
    with caplog.at_level(logging.INFO, logger="daemon.graph"):
        result = asyncio.run(
            node(
                _delegated_mission_without_attest(),
                config={"configurable": {"thread_id": "judge-off-it"}},
            )
        )

    # Judge never invoked — deny+nudge path runs unchanged.
    assert calls == []
    assert "messages" in result
    assert result["attestation_route"] == "agent"
    ledger.increment.assert_called_once()

    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "event=leader_completion_gate_judge" not in log_text


def test_judge_not_called_when_env_kill_switch_off(monkeypatch, caplog):
    """``ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED=0`` → judge skipped."""
    calls = []

    async def must_not_be_called(config, user_payload, *, timeout_s):
        calls.append(True)
        return ('{"is_complete_report": true, "reason": "yes"}', "fake-quick")

    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", must_not_be_called)
    # Pin the env-flag parser to return False for this test.
    monkeypatch.setattr(
        judge_resolver_mod, "_parse_llm_judge_enabled",
        lambda source: False,
    )

    node, manager, ledger = _make_node(instance_id="judge-env-off-it")
    with caplog.at_level(logging.INFO, logger="daemon.graph"):
        result = asyncio.run(
            node(
                _delegated_mission_without_attest(),
                config={"configurable": {"thread_id": "judge-env-off-it"}},
            )
        )

    assert calls == []
    assert "messages" in result
    assert result["attestation_route"] == "agent"
    ledger.increment.assert_called_once()
    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "event=leader_completion_gate_judge" not in log_text


# ─────────────────────────────────────────────────────────────────────────────
# (i) judge NOT called on non-deny paths
# ─────────────────────────────────────────────────────────────────────────────


def test_judge_not_called_on_attested_path(monkeypatch):
    """Attested allow → judge never invoked (no would-be-deny)."""
    calls = []

    async def must_not_be_called(config, user_payload, *, timeout_s):
        calls.append(True)
        return ('{"is_complete_report": true, "reason": "yes"}', "fake-quick")

    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", must_not_be_called)

    node, manager, ledger = _make_node(instance_id="judge-att-it")
    # The attest_completion tool call is in window → attested allow.
    result = asyncio.run(
        node(
            {
                "messages": [
                    HumanMessage(content="do it"),
                    AIMessage(content="delegating"),
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "send_message",
                                "args": {"target": "child"},
                                "id": "c1",
                            }
                        ],
                    ),
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
                ]
            },
            config={"configurable": {"thread_id": "judge-att-it"}},
        )
    )
    assert calls == []
    assert "messages" not in result
    assert result["attestation_route"] is None
    # Counter was reset (attested allow), increment NOT called.
    ledger.increment.assert_not_called()
    ledger.reset.assert_called_once()


def test_judge_not_called_on_unrequired_path(monkeypatch):
    """No delegation since last user msg → judge never invoked (gate OFF)."""
    calls = []

    async def must_not_be_called(config, user_payload, *, timeout_s):
        calls.append(True)
        return ('{"is_complete_report": true, "reason": "yes"}', "fake-quick")

    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", must_not_be_called)

    node, manager, ledger = _make_node(instance_id="judge-unr-it")
    result = asyncio.run(
        node(
            {
                "messages": [
                    HumanMessage(content="what's the answer?"),
                    AIMessage(content="The answer is …"),
                ]
            },
            config={"configurable": {"thread_id": "judge-unr-it"}},
        )
    )
    assert calls == []
    assert "messages" not in result
    assert result["attestation_route"] is None
    ledger.increment.assert_not_called()
    ledger.reset.assert_not_called()


def test_judge_not_called_on_pending_wakeup_path(monkeypatch):
    """Pending child → R2 allow, judge never invoked."""
    calls = []

    async def must_not_be_called(config, user_payload, *, timeout_s):
        calls.append(True)
        return ('{"is_complete_report": true, "reason": "yes"}', "fake-quick")

    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", must_not_be_called)

    manager = MagicMock()
    manager.count_pending_children.return_value = 1  # one child pending
    manager.get_queued_or_expected_wakeups.return_value = 0
    manager.count_live_descendants.return_value = 0
    manager.enqueue_message = MagicMock()
    manager.revive = MagicMock()
    manager.send_message = MagicMock()
    ledger = MagicMock(increment=MagicMock(return_value=1), reset=MagicMock(return_value=True))

    config = build_gate_config("judge-pw-it", GateSettings("enforce", 3, 3))
    node = create_attestation_gate_node(
        config, GateSettings("enforce", 3, 3), manager, "judge-pw-it",
        denied_count_getter=lambda: 0, ledger=ledger,
    )
    result = asyncio.run(
        node(
            _delegated_mission_without_attest(),
            config={"configurable": {"thread_id": "judge-pw-it"}},
        )
    )
    assert calls == []
    assert "messages" not in result
    assert result["attestation_route"] is None
    # ALLOWED_LEGITIMATE_PENDING_WAKEUP — counter unchanged.
    ledger.increment.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# NIT-2: judge NOT called on the OTHER two R2 allow inputs (queued_wakeups,
# live_descendants). The live_descendants input closed the 809e2a59 incident
# class (deferral fires parent's watcher while a deeper grandchild still
# runs) — pin that path explicitly.
# ─────────────────────────────────────────────────────────────────────────────


def test_judge_not_called_on_queued_wakeups_path(monkeypatch):
    """``queued_or_expected_wakeups > 0`` → R2 allow, judge never invoked."""
    calls = []

    async def must_not_be_called(config, user_payload, *, timeout_s):
        calls.append(True)
        return ('{"is_complete_report": true, "reason": "yes"}', "fake-quick")

    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", must_not_be_called)

    manager = MagicMock()
    manager.count_pending_children.return_value = 0
    manager.get_queued_or_expected_wakeups.return_value = 1  # wakeup queued
    manager.count_live_descendants.return_value = 0
    manager.enqueue_message = MagicMock()
    manager.revive = MagicMock()
    manager.send_message = MagicMock()
    ledger = MagicMock(
        increment=MagicMock(return_value=1), reset=MagicMock(return_value=True)
    )

    config = build_gate_config("judge-qw-it", GateSettings("enforce", 3, 3))
    node = create_attestation_gate_node(
        config, GateSettings("enforce", 3, 3), manager, "judge-qw-it",
        denied_count_getter=lambda: 0, ledger=ledger,
    )
    result = asyncio.run(
        node(
            _delegated_mission_without_attest(),
            config={"configurable": {"thread_id": "judge-qw-it"}},
        )
    )
    assert calls == []
    assert "messages" not in result
    assert result["attestation_route"] is None
    ledger.increment.assert_not_called()


def test_judge_not_called_on_live_descendants_path(monkeypatch):
    """``live_descendants > 0`` → R2 allow, judge never invoked.

    NIT-2 close-pin for the 809e2a59 incident class: deferral fires
    the parent's completion watcher (so ``pending_children`` drops
    to 0) while a deeper grandchild still runs AND deferral emits no
    report/task row (so ``queued_or_expected_wakeups`` stays 0).
    Without the third R2 input the gate sees 0/0 as TRUE facts and
    would escalate to ``terminal_after_bound`` while the descendant
    is alive. The judge MUST NOT run on this path — the deny
    predicate never fires, so the would-be-deny judge block is
    skipped.
    """
    calls = []

    async def must_not_be_called(config, user_payload, *, timeout_s):
        calls.append(True)
        return ('{"is_complete_report": true, "reason": "yes"}', "fake-quick")

    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", must_not_be_called)

    manager = MagicMock()
    manager.count_pending_children.return_value = 0  # deferral fired watcher
    manager.get_queued_or_expected_wakeups.return_value = 0  # no report row
    manager.count_live_descendants.return_value = 1  # grandchild still runs
    manager.enqueue_message = MagicMock()
    manager.revive = MagicMock()
    manager.send_message = MagicMock()
    ledger = MagicMock(
        increment=MagicMock(return_value=1), reset=MagicMock(return_value=True)
    )

    config = build_gate_config("judge-ld-it", GateSettings("enforce", 3, 3))
    node = create_attestation_gate_node(
        config, GateSettings("enforce", 3, 3), manager, "judge-ld-it",
        denied_count_getter=lambda: 0, ledger=ledger,
    )
    result = asyncio.run(
        node(
            _delegated_mission_without_attest(),
            config={"configurable": {"thread_id": "judge-ld-it"}},
        )
    )
    assert calls == []
    assert "messages" not in result
    assert result["attestation_route"] is None
    # ALLOWED_LEGITIMATE_PENDING_WAKEUP — counter unchanged.
    ledger.increment.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# NIT-1: judge NOT called on the terminal-after-bound escalation path. The
# judge sits on the would-be-DENIED branch only; when the deny count
# already exceeds the bound the gate transitions to TERMINAL_AFTER_BOUND,
# which is a distinct enum value and never invokes the judge. The
# escalation flag SETS and the counter resets — both happen via
# ``safe_set_escalated_and_reset`` on the ledger, NOT the judge.
# ─────────────────────────────────────────────────────────────────────────────


def test_judge_not_called_on_terminal_after_bound_path(monkeypatch, caplog):
    """``denied_count + 1 > bound`` → TERMINAL_AFTER_BOUND, judge never invoked."""
    calls = []

    async def must_not_be_called(config, user_payload, *, timeout_s):
        calls.append(True)
        return ('{"is_complete_report": true, "reason": "yes"}', "fake-quick")

    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", must_not_be_called)

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
    # ``set_escalated_and_reset`` returns True on success — the gate
    # treats False/None as a degradation-to-allow signal and we want
    # the happy escalation path so the terminal_after_bound log fires.
    ledger.set_escalated_and_reset.return_value = True
    ledger.get.return_value = 0

    config = build_gate_config("judge-tab-it", GateSettings("enforce", 3, 3))
    # denied_count=3 + bound=3 ⇒ 3+1 > 3 ⇒ TERMINAL_AFTER_BOUND.
    node = create_attestation_gate_node(
        config,
        GateSettings("enforce", 3, 3),
        manager,
        "judge-tab-it",
        denied_count_getter=lambda: 3,
        ledger=ledger,
    )
    with caplog.at_level(logging.INFO, logger="daemon.graph"):
        result = asyncio.run(
            node(
                _delegated_mission_without_attest(),
                config={"configurable": {"thread_id": "judge-tab-it"}},
            )
        )

    # Judge never invoked — TERMINAL_AFTER_BOUND is a distinct enum
    # value, not DENIED, so the judge block never runs.
    assert calls == []
    # END routing — no nudge, no attestation_route=agent.
    assert "messages" not in result
    assert result["attestation_route"] is None
    # Ledger write on the escalation path:
    # ``safe_set_escalated_and_reset`` was called; ``safe_increment``
    # was NOT (the bound-exceeded branch bypasses the increment).
    ledger.set_escalated_and_reset.assert_called_once()
    ledger.increment.assert_not_called()
    ledger.reset.assert_not_called()

    # Log fields: the terminal_after_bound observability event fired;
    # the judge log line did NOT.
    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "event=leader_completion_gate_terminal_after_bound" in log_text
    assert "completion_gate_escalated=true" in log_text
    assert "event=leader_completion_gate_judge" not in log_text


# ─────────────────────────────────────────────────────────────────────────────
# (h) log fields present on judge paths
# ─────────────────────────────────────────────────────────────────────────────


def test_log_fields_present_on_judge_yes(monkeypatch, caplog):
    """Judge-yes log line carries verdict / model / latency_ms / reason."""
    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", _invoke_yes)

    node, _, _ = _make_node(instance_id="log-yes-it")
    with caplog.at_level(logging.INFO, logger="daemon.graph"):
        asyncio.run(
            node(
                _delegated_mission_without_attest(),
                config={"configurable": {"thread_id": "log-yes-it"}},
            )
        )

    judge_log = [
        r.getMessage()
        for r in caplog.records
        if "event=leader_completion_gate_judge" in r.getMessage()
    ]
    assert len(judge_log) >= 1
    msg = judge_log[0]
    assert "event=leader_completion_gate_judge" in msg
    assert "verdict=yes" in msg
    assert "llm_judge_verdict=yes" in msg
    assert "llm_judge_model=fake-quick" in msg
    assert "llm_judge_latency_ms=" in msg
    assert "llm_judge_reason=detailed outcomes delivered" in msg
    assert "llm_judge_error_class=<none>" in msg


def test_log_fields_present_on_judge_error(monkeypatch, caplog):
    """Error log line carries error_class."""
    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", _invoke_error)

    node, _, _ = _make_node(instance_id="log-err-it")
    with caplog.at_level(logging.INFO, logger="daemon.graph"):
        asyncio.run(
            node(
                _delegated_mission_without_attest(),
                config={"configurable": {"thread_id": "log-err-it"}},
            )
        )

    judge_log = [
        r.getMessage()
        for r in caplog.records
        if "event=leader_completion_gate_judge" in r.getMessage()
    ]
    assert len(judge_log) >= 1
    msg = judge_log[0]
    assert "verdict=error" in msg
    assert "llm_judge_error_class=RuntimeError" in msg


# ─────────────────────────────────────────────────────────────────────────────
# (g) model fallback resolution — judge logs the resolved model
# ─────────────────────────────────────────────────────────────────────────────


def test_model_fallback_to_main_when_keywords_empty(monkeypatch, caplog):
    """When ``model_keywords`` empty, judge logs main ``model``."""
    async def invoke_record_model(config, user_payload, *, timeout_s):
        # Echo what ``resolve_judge_model`` would return — the judge
        # passes the resolved name into ``_invoke_judge_llm``. We
        # simulate it.
        from daemon.services.attestation_report_judge import (
            resolve_judge_model,
        )
        return ('{"is_complete_report": true, "reason": "yes"}', resolve_judge_model(config))

    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", invoke_record_model)

    # Configure a fake config with empty model_keywords.
    class _Cfg:
        class _LLM:
            model = "gpt-fallback-main"
            model_keywords = ""

        llm = _LLM()

    manager = MagicMock()
    manager.count_pending_children.return_value = 0
    manager.get_queued_or_expected_wakeups.return_value = 0
    manager.count_live_descendants.return_value = 0
    manager.enqueue_message = MagicMock()
    manager.revive = MagicMock()
    manager.send_message = MagicMock()
    manager.config = _Cfg()  # the gate node reads ``manager.config``
    ledger = MagicMock(increment=MagicMock(return_value=1), reset=MagicMock(return_value=True))

    config = build_gate_config("mf-fallback", GateSettings("enforce", 3, 3))
    node = create_attestation_gate_node(
        config, GateSettings("enforce", 3, 3), manager, "mf-fallback",
        denied_count_getter=lambda: 0, ledger=ledger,
    )
    with caplog.at_level(logging.INFO, logger="daemon.graph"):
        asyncio.run(
            node(
                _delegated_mission_without_attest(),
                config={"configurable": {"thread_id": "mf-fallback"}},
            )
        )

    judge_log = [
        r.getMessage()
        for r in caplog.records
        if "event=leader_completion_gate_judge" in r.getMessage()
    ]
    assert len(judge_log) >= 1
    assert "llm_judge_model=gpt-fallback-main" in judge_log[0]


def test_model_uses_keywords_when_set(monkeypatch, caplog):
    """When ``model_keywords`` set, judge logs that quick model."""
    async def invoke_record_model(config, user_payload, *, timeout_s):
        from daemon.services.attestation_report_judge import (
            resolve_judge_model,
        )
        return ('{"is_complete_report": true, "reason": "yes"}', resolve_judge_model(config))

    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", invoke_record_model)

    class _Cfg:
        class _LLM:
            model = "gpt-main"
            model_keywords = "gpt-quick"

        llm = _LLM()

    manager = MagicMock()
    manager.count_pending_children.return_value = 0
    manager.get_queued_or_expected_wakeups.return_value = 0
    manager.count_live_descendants.return_value = 0
    manager.enqueue_message = MagicMock()
    manager.revive = MagicMock()
    manager.send_message = MagicMock()
    manager.config = _Cfg()
    ledger = MagicMock(increment=MagicMock(return_value=1), reset=MagicMock(return_value=True))

    config = build_gate_config("kw-it", GateSettings("enforce", 3, 3))
    node = create_attestation_gate_node(
        config, GateSettings("enforce", 3, 3), manager, "kw-it",
        denied_count_getter=lambda: 0, ledger=ledger,
    )
    with caplog.at_level(logging.INFO, logger="daemon.graph"):
        asyncio.run(
            node(
                _delegated_mission_without_attest(),
                config={"configurable": {"thread_id": "kw-it"}},
            )
        )

    judge_log = [
        r.getMessage()
        for r in caplog.records
        if "event=leader_completion_gate_judge" in r.getMessage()
    ]
    assert len(judge_log) >= 1
    assert "llm_judge_model=gpt-quick" in judge_log[0]


# ─────────────────────────────────────────────────────────────────────────────
# Boot log line carries judge info
# ─────────────────────────────────────────────────────────────────────────────


def test_boot_log_line_includes_judge_info(caplog):
    """Boot log line surfaces ``llm_judge_enabled`` and ``llm_judge_model``."""
    from daemon.services.attestation_resolver import emit_attestation_boot_log

    with caplog.at_level(logging.INFO, logger="daemon.services.attestation_resolver"):
        emit_attestation_boot_log()

    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "llm_judge_enabled=" in log_text
    assert "llm_judge_model=" in log_text
    assert "ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED" in log_text


def test_boot_log_disabled_when_env_zero(caplog, monkeypatch):
    """``ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED=0`` → log marks disabled."""
    monkeypatch.setenv("ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED", "0")
    from daemon.services.attestation_resolver import emit_attestation_boot_log

    with caplog.at_level(logging.INFO, logger="daemon.services.attestation_resolver"):
        emit_attestation_boot_log()

    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "llm_judge_enabled=false" in log_text
    assert "llm_judge_model=<disabled>" in log_text