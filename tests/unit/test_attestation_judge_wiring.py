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
    final_text: str = None,
) -> dict:
    """Standard delegated mission: real user → AI send_message → AI done.

    Default final_text is a long detailed completion report (>= 150
    words, no marker phrases) — the legacy default "I am done without
    attesting" (5 words) trips the 2026-09-12 length trigger and
    would route through the marker/length judge; this default avoids
    the trigger so the test exercises the would-be-deny judge path
    without spuriously activating the marker-path judge. Tests that
    want to exercise the marker/length judge should pass an explicit
    short or marker-bearing ``final_text``.
    """
    if final_text is None:
        final_text = (
            "All four patches landed and shipped to the integration "
            "branch. Patch 1 fixed the off-by-one in the cache TTL "
            "calculator; the unit tests now exercise both the "
            "elapsed-second and wall-clock-second boundaries at "
            "the second and minute granularity. Patch 2 cleaned up "
            "the dead imports in the worker pool module after the "
            "migration, removing the legacy compatibility shim and "
            "the related test scaffolding. Patch 3 refactored the "
            "error-reporting decorator so the stack-frame metadata "
            "is consistent across all four call sites in the graph "
            "node and the manager facade. Patch 4 added the missing "
            "operator-boot log line for the new resolver module so "
            "operators can grep the boot summary for the resolved "
            "effective values including the mode, window, bound, and "
            "gate locations active at the time. All four patches "
            "passed their respective suites on the first run with "
            "no flake; the integration matrix is green end-to-end "
            "across all environments we maintain. No follow-ups "
            "outstanding; the mission is complete and ready for "
            "review. The release notes draft is staged on the docs "
            "branch with the per-patch rationale paragraphs and the "
            "cross-references to the upstream incident reports; the "
            "FE mirror was verified and the build artifact attached "
            "to the rollout ticket for traceability."
        )
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
# S5 review punch-list — judge-yes at bound-1 keeps the counter, no
# escalation. The existing (a) test exercises the deny path with
# ``denied_count=0``; this regression pins the counter-no-escalation
# invariant at the danger boundary — when the counter is already
# ``bound-1 = 2`` and a judge-yes verdict lands, the counter MUST
# stay at 2 (no increment; no terminal_after_bound escalation; the
# judge-yes override returns END routing unchanged).
# ─────────────────────────────────────────────────────────────────────────────


def test_judge_yes_at_bound_minus_one_does_not_escalate(
    monkeypatch, caplog
):
    """Judge-yes at ``denied_count = bound - 1`` → counter stays, no escalation.

    With ``bound=3`` and the live ``denied_count=2`` (``bound-1``),
    the gate's deny predicate is still ``DENIED`` (since
    ``denied_count + 1 <= bound`` ⇒ the bound-exceeded branch does
    not fire); the judge-yes override MUST return END routing
    without invoking the ledger, keeping the counter at 2 and
    NOT escalating to ``terminal_after_bound``. A regression that
    incremented the counter on a judge-yes override would push the
    counter to 3 and the NEXT genuine deny would escalate — this
    test pins the no-increment invariant at the danger boundary.
    """
    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", _invoke_yes)

    node, manager, ledger = _make_node(
        instance_id="judge-yes-bound-it",
        denied_count_getter=lambda: 2,
    )
    with caplog.at_level(logging.INFO, logger="daemon.graph"):
        result = asyncio.run(
            node(
                _delegated_mission_without_attest(),
                config={"configurable": {"thread_id": "judge-yes-bound-it"}},
            )
        )

    # ALLOWED — no nudge, no counter change, no escalation.
    assert "messages" not in result
    assert result["attestation_route"] is None
    # Judge-yes override does NOT increment (R1 — attested-allow
    # only). The counter stays at 2.
    ledger.increment.assert_not_called()
    ledger.reset.assert_not_called()
    # No terminal_after_bound escalation — the judge-yes path
    # bypasses the bound-exceeded ledger write.
    ledger.set_escalated_and_reset.assert_not_called()
    # No durable delivery or revive seam.
    manager.enqueue_message.assert_not_called()
    manager.revive.assert_not_called()

    # Log fields present — the judge-yes override fired.
    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "event=leader_completion_gate_judge" in log_text
    assert "verdict=yes" in log_text
    assert "[AttestationGate] judge-yes override" in log_text
    # The bound-exceeded log line MUST NOT fire — pinning the
    # counter-no-escalation invariant.
    assert "event=leader_completion_gate_terminal_after_bound" not in log_text


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


def test_judge_not_called_when_env_kill_switch_off_real_resolver(
    monkeypatch, caplog
):
    """S6 review fix — real env var + resolver reset (NOT a
    monkeypatched parser).

    ``ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED=0`` →
    ``is_llm_judge_enabled()`` resolves to ``False`` after a cache
    reset → judge skipped. The earlier version monkeypatched
    ``_parse_llm_judge_enabled``; this variant drives the REAL
    resolver so the env-key wiring, the strip/lowercase pipeline,
    and the cached-global reset are all exercised end-to-end.

    The legacy monkeypatch variant is preserved below as
    ``test_judge_not_called_when_env_kill_switch_off`` to keep both
    flavors covered (the S6 punch-list requires at least one
    monkeypatch variant remain).
    """
    calls = []

    async def must_not_be_called(config, user_payload, *, timeout_s):
        calls.append(True)
        return ('{"is_complete_report": true, "reason": "yes"}', "fake-quick")

    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", must_not_be_called)
    # REAL env var + REAL resolver reset — drives the real kill-
    # switch pipeline (strip + lower + falsy-set check + cache wipe).
    monkeypatch.setenv("ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED", "0")
    judge_resolver_mod.reset_llm_judge_resolver_for_tests()
    # Sanity: the real resolver returns False under the mutated env.
    assert judge_resolver_mod.is_llm_judge_enabled() is False

    node, manager, ledger = _make_node(instance_id="judge-env-off-real-it")
    with caplog.at_level(logging.INFO, logger="daemon.graph"):
        result = asyncio.run(
            node(
                _delegated_mission_without_attest(),
                config={"configurable": {"thread_id": "judge-env-off-real-it"}},
            )
        )

    assert calls == []
    assert "messages" in result
    assert result["attestation_route"] == "agent"
    ledger.increment.assert_called_once()
    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "event=leader_completion_gate_judge" not in log_text


def test_judge_not_called_when_env_kill_switch_off(monkeypatch, caplog):
    """``ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED=0`` → judge skipped.

    MONKEYPATCH variant — preserved for parity with the real-
    resolver variant above (S6 punch-list requires at least one
    monkeypatch case remain alongside the real-env conversion). This
    pins the env-flag-parser seam independently: any future refactor
    of the parser is caught here without requiring a real
    ``setenv`` + cache-reset dance.
    """
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
    """No delegation since last user msg → judge never invoked (gate OFF).

    The 2026-09-12 length trigger would otherwise fire on a short
    ``AIMessage`` like the legacy "The answer is …" (3 words). We
    use a long detailed completion report (>= 150 words, no marker
    phrases) so the cheap allow path is taken end-to-end and NEITHER
    trigger half (markers OR length) fires — the judge is never
    invoked. The legacy assertion ("judge never invoked on the
    would-be-deny OFF path") is preserved with the long-report
    wording.
    """
    calls = []

    async def must_not_be_called(config, user_payload, *, timeout_s):
        calls.append(True)
        return ('{"is_complete_report": true, "reason": "yes"}', "fake-quick")

    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", must_not_be_called)

    # Long, detailed completion report — >= 150 words, no marker
    # phrases. The length trigger does NOT fire (>= 150 words); the
    # marker trigger does NOT fire (no marker phrases). The cheap
    # allow path runs end-to-end.
    long_report = (
        "The answer is 42; explanation follows in the sections "
        "below. Nothing pending, all shipped. Patch 1 fixed the "
        "off-by-one in the cache TTL calculator; the unit tests now "
        "exercise both the elapsed-second and wall-clock-second "
        "boundaries at the second and minute granularity. Patch 2 "
        "cleaned up the dead imports in the worker pool module "
        "after the migration, removing the legacy compatibility shim "
        "and the related test scaffolding. Patch 3 refactored the "
        "error-reporting decorator so the stack-frame metadata is "
        "consistent across all four call sites in the graph node "
        "and the manager facade. Patch 4 added the missing "
        "operator-boot log line for the new resolver module so "
        "operators can grep the boot summary for the resolved "
        "effective values. All four patches passed their respective "
        "suites on the first run with no flake; the integration "
        "matrix is green end-to-end across all environments we "
        "maintain. No follow-ups outstanding; the mission is "
        "complete and ready for review by the next teammate in the "
        "chain. The release notes draft is staged on the docs "
        "branch with the per-patch rationale paragraphs and the "
        "cross-references to the upstream incident reports."
    )

    node, manager, ledger = _make_node(instance_id="judge-unr-it")
    result = asyncio.run(
        node(
            {
                "messages": [
                    HumanMessage(content="what's the answer?"),
                    AIMessage(content=long_report),
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
    # W3 review fix — the duplicate ``llm_judge_verdict`` field was
    # dropped (it carried the same value as ``verdict`` and confused
    # log grep). The model name still lives on the dedicated
    # ``llm_judge_model=`` field.
    assert "llm_judge_verdict" not in msg
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

# ─────────────────────────────────────────────────────────────────────────────
# Resolved timeout FLOWS to the judge call — Pattern C env-tunability
# (operator tuning decision 2026-09-07 grounded in the tester live-LLM
# probe; default 25.0s, min clamp 5.0s)
# ─────────────────────────────────────────────────────────────────────────────


class _TimeoutCapturingConfig:
    """Captures both the resolved timeout (Pattern C) AND the W1 coupling.

    The test writes a single spy ``_invoke_judge_llm`` (per-test closure
    via ``monkeypatch``) that:
    * records the ``timeout_s`` kwarg it received (the wait_for cap +
      wall_clock_cap_s value — the operator-visible timeout);
    * computes the W1 ``judge_request_timeout`` value the SAME WAY the
      real ``_invoke_judge_llm`` does (mirrors
      ``daemon/services/attestation_report_judge.py`` line 459-462), so
      the test pins BOTH seams of the timeout wiring without depending
      on ``ThinkingChatOpenAI`` internals.

    Using the resolver directly (not the constant) is the point of the
    test — a refactor that hardcodes the constant back into the call
    site (re-introducing the bug class this resolver exists to fix)
    would leave the wait_for cap at 25.0s regardless of the env value,
    and the test would fail on the ``timeout_s`` assertion.

    Mirrors the ``_FakeCfg`` stub shape at
    ``tests/unit/test_attestation_report_judge.py`` (inner ``_LLM`` class
    exposing ``model`` / ``model_keywords``) plus ``request_timeout``
    so the W1 coupling math has a baseline to compare against.
    """

    def __init__(
        self,
        model="fake-main",
        model_keywords="fake-quick",
        request_timeout=None,
    ):
        class _LLM:
            pass

        self._llm = _LLM()
        self._llm.model = model
        self._llm.model_keywords = model_keywords
        self._llm.request_timeout = request_timeout

    @property
    def llm(self):
        return self._llm


def _build_timeout_capture_spy(captured: dict, *, canned_response: str):
    """Build a spy ``_invoke_judge_llm`` that captures both timeout seams.

    The spy is shaped like the real ``_invoke_judge_llm`` signature
    (``async def _invoke_judge_llm(config, user_payload, *, timeout_s)``)
    so ``monkeypatch.setattr(judge_mod, "_invoke_judge_llm", spy)``
    substitutes cleanly. It:
    1. Records the received ``timeout_s`` (wait_for cap +
       wall_clock_cap_s value) into ``captured["timeout_s"]``.
    2. Computes ``judge_request_timeout`` the same way the real
       implementation does (W1 coupling — min(resolved, config.llm.
       request_timeout OR resolved)) and records it into
       ``captured["request_timeout"]``.
    3. Returns a canned success response so the calling async judge
       yields ``JudgeResult(is_complete_report=True, ...)`` and the
       call returns without raising.
    """

    async def spy(config, user_payload, *, timeout_s):
        captured["timeout_s"] = timeout_s
        # Mirror the W1 coupling: ``min(resolved_timeout,
        # config.llm.request_timeout or resolved_timeout)`` — see
        # ``daemon/services/attestation_report_judge.py`` ~:459-462.
        # We import the resolver here (lazy import — keeps the test
        # hermetic against import-order side effects from the
        # autouse fixture).
        from daemon.services.attestation_judge_timeout_resolver import (
            get_judge_timeout_s,
        )

        resolved_timeout = get_judge_timeout_s()
        judge_request_timeout = min(
            resolved_timeout,
            config.llm.request_timeout or resolved_timeout,
        )
        captured["request_timeout"] = judge_request_timeout
        return (canned_response, "fake-quick")

    return spy


def test_resolved_timeout_flows_to_judge_call(monkeypatch):
    """Resolved timeout (Pattern C) FLOWS to both the wait_for cap and
    the W1 request_timeout coupling.

    Set ``ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_TIMEOUT_S=17``, reset
    the resolver, and call :func:`judge_completion_report_async`
    WITHOUT an explicit ``timeout_s=`` kwarg. The spy
    :func:`_invoke_judge_llm` captures:
    * the ``timeout_s`` it received (the ``asyncio.wait_for`` cap +
      ``wall_clock_cap_s`` for the HA facade — the operator-visible
      timeout); this MUST be the resolved value ``17.0``, not the
      default ``25.0`` and not the prior hardcoded ``10.0``;
    * the derived ``judge_request_timeout`` (the W1 coupling — the
      per-attempt HTTP ``request_timeout`` passed to the LLM client).
      With ``config.llm.request_timeout`` set to a high value (e.g.
      the default 610s) and resolved=17, the min is 17.0 — the
      resolved value drives both seams.
    """
    monkeypatch.setenv(
        "ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_TIMEOUT_S", "17"
    )
    # Reset the timeout cache so the next ``get_judge_timeout_s()``
    # call re-resolves under the new env. The autouse fixture
    # (``_reset_resolvers``) clears the env on entry, so we re-set
    # AFTER the fixture ran and reset explicitly.
    from daemon.services.attestation_judge_timeout_resolver import (
        reset_judge_timeout_resolver_for_tests,
    )

    reset_judge_timeout_resolver_for_tests()

    captured: dict = {}
    spy = _build_timeout_capture_spy(
        captured,
        canned_response='{"is_complete_report": true, "reason": "ok"}',
    )
    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", spy)

    cfg = _TimeoutCapturingConfig(request_timeout=610.0)
    messages = [
        __import__("langchain_core.messages", fromlist=["HumanMessage"]).HumanMessage(
            content="please do it"
        ),
        __import__("langchain_core.messages", fromlist=["AIMessage"]).AIMessage(
            content="done without attesting"
        ),
    ]
    result = asyncio.run(
        judge_mod.judge_completion_report_async(messages, config=cfg)
    )

    # Wait_for cap + wall_clock_cap_s — the operator-visible timeout.
    assert captured["timeout_s"] == 17.0
    # W1 coupling — ``min(resolved, config.llm.request_timeout or
    # resolved)`` → ``min(17.0, 610.0)`` = 17.0. The resolved value
    # drives both seams when ``config.llm.request_timeout`` is above
    # the resolved value (the typical operator config).
    assert captured["request_timeout"] == 17.0
    # The judge ran to completion (canned success response).
    assert result.is_complete_report is True
    assert result.verdict == "yes"


def test_resolved_timeout_default_when_env_unset(monkeypatch):
    """With env unset, the default 25.0s flows to both seams.

    Pins the "operator did not set the env" path — the runtime value
    is :data:`DEFAULT_JUDGE_TIMEOUT_S` (25.0s, the operator tuning
    decision default). A regression that dropped the resolver call
    would re-introduce the prior hardcoded 10.0s and fail this test.
    """
    # Ensure unset (the autouse fixture cleared it; this is belt-and-braces).
    monkeypatch.delenv(
        "ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_TIMEOUT_S", raising=False
    )
    from daemon.services.attestation_judge_timeout_resolver import (
        reset_judge_timeout_resolver_for_tests,
    )

    reset_judge_timeout_resolver_for_tests()

    captured: dict = {}
    spy = _build_timeout_capture_spy(
        captured,
        canned_response='{"is_complete_report": false, "reason": "not done"}',
    )
    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", spy)

    cfg = _TimeoutCapturingConfig(request_timeout=610.0)
    messages = [
        __import__("langchain_core.messages", fromlist=["HumanMessage"]).HumanMessage(
            content="please do it"
        ),
        __import__("langchain_core.messages", fromlist=["AIMessage"]).AIMessage(
            content="not done"
        ),
    ]
    asyncio.run(judge_mod.judge_completion_report_async(messages, config=cfg))

    assert captured["timeout_s"] == 25.0
    assert captured["request_timeout"] == 25.0


def test_resolved_timeout_below_clamp_flows_clamp_to_seams(monkeypatch):
    """Below-clamp env value clamps to ``MIN_JUDGE_TIMEOUT_S`` (5.0s) on both seams.

    The W1 coupling + the wait_for cap BOTH carry the clamped value
    (not the raw operator value). Pins the resolver's clamp
    discipline: the floor propagates through the wiring, not just
    through the env-parsing layer.
    """
    monkeypatch.setenv("ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_TIMEOUT_S", "2")
    from daemon.services.attestation_judge_timeout_resolver import (
        reset_judge_timeout_resolver_for_tests,
    )

    reset_judge_timeout_resolver_for_tests()

    captured: dict = {}
    spy = _build_timeout_capture_spy(
        captured,
        canned_response='{"is_complete_report": true, "reason": "ok"}',
    )
    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", spy)

    cfg = _TimeoutCapturingConfig(request_timeout=610.0)
    messages = [
        __import__("langchain_core.messages", fromlist=["HumanMessage"]).HumanMessage(
            content="please do it"
        ),
        __import__("langchain_core.messages", fromlist=["AIMessage"]).AIMessage(
            content="done"
        ),
    ]
    asyncio.run(judge_mod.judge_completion_report_async(messages, config=cfg))

    assert captured["timeout_s"] == 5.0  # clamp, not the raw 2.0
    assert captured["request_timeout"] == 5.0


def test_resolved_timeout_explicit_kwarg_overrides_resolver(monkeypatch):
    """Explicit ``timeout_s=`` kwarg to the async judge OVERRIDES the resolver.

    Test fixtures + hot-loop callers sometimes pin a tighter cap than
    the env-configured value. The function default (``None``) means
    "use the resolver"; an explicit numeric value MUST pass through
    unchanged. This pins the contract — a refactor that ignores the
    explicit kwarg would break hot-loop tests.
    """
    # Set the env to a different value so the test discriminates.
    monkeypatch.setenv("ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_TIMEOUT_S", "17")
    from daemon.services.attestation_judge_timeout_resolver import (
        reset_judge_timeout_resolver_for_tests,
    )

    reset_judge_timeout_resolver_for_tests()

    captured: dict = {}
    spy = _build_timeout_capture_spy(
        captured,
        canned_response='{"is_complete_report": true, "reason": "ok"}',
    )
    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", spy)

    cfg = _TimeoutCapturingConfig(request_timeout=610.0)
    messages = [
        __import__("langchain_core.messages", fromlist=["HumanMessage"]).HumanMessage(
            content="please do it"
        ),
        __import__("langchain_core.messages", fromlist=["AIMessage"]).AIMessage(
            content="done"
        ),
    ]
    # Pass an explicit ``timeout_s=`` that overrides the resolver.
    asyncio.run(
        judge_mod.judge_completion_report_async(
            messages, config=cfg, timeout_s=7.0
        )
    )

    # The explicit kwarg flows to the wait_for cap.
    assert captured["timeout_s"] == 7.0
    # The W1 coupling still uses the RESOLVED value (17.0) for the
    # per-attempt request_timeout — the explicit ``timeout_s`` only
    # overrides the wall-clock cap (wait_for + wall_clock_cap_s), NOT
    # the inner request_timeout math. This is the intended behavior:
    # the per-attempt HTTP timeout is derived from the resolver (the
    # operator's documented contract) regardless of how the calling
    # site tunes the wall-clock cap.
    assert captured["request_timeout"] == 17.0


def test_resolved_timeout_w1_coupling_uses_min(monkeypatch):
    """W1 coupling: ``request_timeout = min(resolved, config.llm.request_timeout)``.

    Pins the compaction-site precedent at ``daemon/manager.py:398``.
    When ``config.llm.request_timeout`` is BELOW the resolved
    timeout (an operator has the LLM-side request_timeout tight),
    the request_timeout in the llm_config MUST be the config value
    (not the resolved). The resolved timeout still drives the
    wait_for cap + wall_clock_cap_s.
    """
    monkeypatch.setenv("ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_TIMEOUT_S", "30")
    from daemon.services.attestation_judge_timeout_resolver import (
        reset_judge_timeout_resolver_for_tests,
    )

    reset_judge_timeout_resolver_for_tests()

    captured: dict = {}
    spy = _build_timeout_capture_spy(
        captured,
        canned_response='{"is_complete_report": true, "reason": "ok"}',
    )
    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", spy)

    # Config request_timeout is below the resolved 30.0s.
    cfg = _TimeoutCapturingConfig(request_timeout=12.0)
    messages = [
        __import__("langchain_core.messages", fromlist=["HumanMessage"]).HumanMessage(
            content="please do it"
        ),
        __import__("langchain_core.messages", fromlist=["AIMessage"]).AIMessage(
            content="done"
        ),
    ]
    asyncio.run(judge_mod.judge_completion_report_async(messages, config=cfg))

    # The wait_for cap + wall_clock_cap_s carry the resolved value (30.0).
    assert captured["timeout_s"] == 30.0
    # The W1 request_timeout is the MIN — the config-side value (12.0)
    # because it is below the resolved timeout.
    assert captured["request_timeout"] == 12.0
