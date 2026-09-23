"""Attest-first pure-toolcall-turn contract — system-side gate enforcement
(2026-09-19, c5d9a38a remediation).

The LCA feature previously taught leaders to deliver a report and
THEN call ``attest_completion`` (the report-first-then-attest
order). Prompt-only fixes for the c5d9a38a shape — where a leader
bundled report + ``attest_completion`` into ONE AIMessage —
failed TWICE. The 2026-09-19 user decision FLIPPED the contract:
attest FIRST (in a PURE TOOLCALL TURN with empty content), then
deliver the report as a SUBSEQUENT standalone AI message.

This test file pins the SIX matrix cases for the new HOLD-state
enforcement in ``daemon/services/attestation_gate.py`` +
``daemon/graph.py:create_attestation_gate_node``:

* (a) Happy path: clean attest_call + standalone text report →
  ``Decision.ALLOWED`` + counter reset + NO reminder.
* (b) Attest-only turn-end: clean attest_call with NO subsequent
  text report → ``Decision.HOLD`` + Final Report Reminder
  (clean-call shape) injected + route-back to agent. Counter
  UNCHANGED (HOLD is NOT a denial).
* (c) Bundled c5d9a38a shape: AIMessage carried text +
  ``attest_completion`` tool_call → ``Decision.HOLD`` + Bundled
  Reminder (re-issue shape) injected + route-back. Counter
  UNCHANGED.
* (d) Reminder cap (``ATTESTATION_REMINDER_CAP = 2``): after the
  cap is reached, the gate falls through to plain
  ``meta_bypass`` allow — the documented escape so a leader that
  keeps emitting empty / short finals after the attest_call is
  never stuck in an infinite HOLD loop. Reminder counter
  reset to 0 on the cap fall-through.
* (e) Counter-independence: the HOLD-state reminder injection
  does NOT increment ``attestation_denied_count`` and does NOT
  consult ``deny_bound`` (the bound / escalation machinery is
  NEVER touched on HOLD). The counter-independence is the key
  safety property of the new branch.
* (f) Existing matrix re-anchored: every prior test that
  exercised the OLD "attest_call is the last AIMessage" path
  (which produced ``Decision.ALLOWED`` under the old contract)
  now produces ``Decision.HLOGS`` and is either updated to
  include a final standalone text report (case (a)) or
  intentionally exercises the HOLD branch (cases (b)/(c)/(d)).

The test suite below drives the production
``create_attestation_gate_node`` factory closure — the same
real-graph path the integration tests use — so the new gate
wiring is end-to-end exercised.
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from daemon.graph import (
    ATTESTATION_BUNDLED_REMINDER,
    ATTESTATION_FINAL_REPORT_REMINDER,
    ATTESTATION_REMINDER_CAP,
    ATTESTATION_REMINDER_COUNT_KEY,
    create_attestation_gate_node,
)
from daemon.services.attestation_gate import (
    Decision,
    GateSettings,
    build_gate_config,
    classify_final_ai_shape,
    decide,
    evaluate,
)

# Hermetic isolation for tests that exercise the resolver cache /
# judge kill-switch (mirrors the autouse fixture in
# tests/unit/test_attestation_nudge_inject.py — same convention).
from daemon.services.attestation_judge_resolver import (
    reset_llm_judge_resolver_for_tests,
)
from daemon.services.attestation_resolver import (
    reset_attestation_resolver_for_tests,
)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures + helpers
# ─────────────────────────────────────────────────────────────────────────────


# A long standalone text report — >= 150 words (above
# ``SHORT_REPORT_WORD_THRESHOLD``). Mirrors the unit test fixture
# ``report_ai()`` shape; kept inline so this test file is
# self-contained.
_LONG_REPORT = (
    "The work is finished. All four patches shipped; the "
    "test matrix is green; the integration tests pass on every "
    "environment we maintain. Patch 1 fixed the off-by-one in "
    "the cache TTL calculator; the unit tests now exercise both "
    "the elapsed-second and wall-clock-second boundaries at the "
    "second and minute granularity. Patch 2 cleaned up the dead "
    "imports in the worker pool module after the migration, "
    "removing the legacy compatibility shim and the related "
    "test scaffolding. Patch 3 refactored the error-reporting "
    "decorator so the stack-frame metadata is consistent across "
    "all four call sites in the graph node and the manager "
    "facade. Patch 4 added the missing operator-boot log line "
    "for the new resolver module so operators can grep the "
    "boot summary for the resolved effective values. All four "
    "patches passed their respective suites on the first run "
    "with no flake; the integration matrix is green end-to-end "
    "across all environments we maintain. No follow-ups "
    "outstanding; the mission is complete and ready for review "
    "by the next teammate in the chain."
)


@pytest.fixture(autouse=True)
def _isolate_resolver_caches(monkeypatch):
    """Hermetic isolation for the resolver caches (mirrors the
    autouse fixture in test_attestation_nudge_inject.py)."""
    monkeypatch.delenv(
        "ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED",
        raising=False,
    )
    reset_attestation_resolver_for_tests()
    reset_llm_judge_resolver_for_tests()
    yield
    reset_attestation_resolver_for_tests()
    reset_llm_judge_resolver_for_tests()


def _make_manager() -> MagicMock:
    """Return a manager MagicMock with the canonical R2 facade stubs."""
    manager = MagicMock()
    manager.count_pending_children.return_value = 0
    manager.get_queued_or_expected_wakeups.return_value = 0
    manager.count_live_descendants.return_value = 0
    manager.count_busy_descendants.return_value = 0
    # FIX-2 — the FIFTH legitimate-pending input. False by default
    # (no awaiting-answer suspension handle).
    manager.has_open_user_answer.return_value = False
    return manager


def _make_node(instance_id: str) -> tuple[Any, MagicMock, MagicMock]:
    """Build a production ``create_attestation_gate_node`` closure
    with the canonical config + a stub ledger."""
    manager = _make_manager()
    ledger = MagicMock()
    ledger.increment.return_value = 1
    ledger.reset.return_value = True
    ledger.set_escalated_and_reset.return_value = True
    config = build_gate_config(instance_id, GateSettings("enforce", 3, 3))
    node = create_attestation_gate_node(
        config,
        GateSettings("enforce", 3, 3),
        manager,
        instance_id,
        denied_count_getter=lambda: 0,
        ledger=ledger,
    )
    return node, manager, ledger


def _delegated_messages(extra_ai: AIMessage | list[AIMessage]) -> list:
    """Build a delegated-mission message list ending in the given
    AIMessage(s). The first AIMessage carries the
    ``send_message`` tool call (the canonical "this mission
    dispatched children" predicate)."""
    if not isinstance(extra_ai, list):
        extra_ai = [extra_ai]
    delegation_ai = AIMessage(
        content="",
        tool_calls=[
            {"name": "send_message", "args": {"target": "child"}, "id": "c1"}
        ],
    )
    return [
        HumanMessage(content="please do it"),
        delegation_ai,
        *extra_ai,
    ]


# ─────────────────────────────────────────────────────────────────────────────
# classify_final_ai_shape — the pure helper that drives the HOLD branch
# ─────────────────────────────────────────────────────────────────────────────


class TestClassifyFinalAIShape:
    """The pure helper that drives the HOLD-state decision. Pins
    the (a)/(b)/(c) shape detection that the gate consumes."""

    def test_clean_attest_call_returns_clean_hold_shape(self) -> None:
        """Last AIMessage is the attest_call (empty content +
        tool_call); NO subsequent text report. Shape: HOLD with
        the clean-call reminder text."""
        attest_ai = AIMessage(
            content="",
            tool_calls=[
                {"name": "attest_completion", "args": {}, "id": "a1"}
            ],
        )
        is_text_report, is_attest_call, is_bundled, idx = (
            classify_final_ai_shape(
                _delegated_messages(attest_ai),
                window=3,
                attested=True,
            )
        )
        assert is_text_report is False  # last AI is the attest message
        assert is_attest_call is True
        assert is_bundled is False  # empty content
        assert idx >= 0  # the attest message is found in the window

    def test_bundled_call_returns_bundled_shape(self) -> None:
        """Last AIMessage carried non-empty content + the
        ``attest_completion`` tool_call — the c5d9a38a shape."""
        bundled_ai = AIMessage(
            content="Report + attest in ONE message — the bundled shape.",
            tool_calls=[
                {"name": "attest_completion", "args": {}, "id": "a1"}
            ],
        )
        is_text_report, is_attest_call, is_bundled, idx = (
            classify_final_ai_shape(
                _delegated_messages(bundled_ai),
                window=3,
                attested=True,
            )
        )
        assert is_text_report is False
        assert is_attest_call is True
        assert is_bundled is True  # text + tool_call together
        assert idx >= 0

    def test_happy_path_final_text_report(self) -> None:
        """Last AIMessage is a standalone text report (no tool
        calls, >= 150 words). Shape: ALLOWED-allow (the happy
        path)."""
        attest_ai = AIMessage(
            content="",
            tool_calls=[
                {"name": "attest_completion", "args": {}, "id": "a1"}
            ],
        )
        is_text_report, is_attest_call, is_bundled, idx = (
            classify_final_ai_shape(
                _delegated_messages([attest_ai, AIMessage(content=_LONG_REPORT)]),
                window=3,
                attested=True,
            )
        )
        assert is_text_report is True
        assert is_attest_call is False  # last AI is the REPORT, not the attest message
        assert is_bundled is False
        assert idx >= 0

    def test_short_final_message_after_attest_is_not_text_report(self) -> None:
        """Last AIMessage is a short prose (no tool calls, but
        < 150 words). Shape: HOLD (the length threshold gates
        the ALLOWED path)."""
        attest_ai = AIMessage(
            content="",
            tool_calls=[
                {"name": "attest_completion", "args": {}, "id": "a1"}
            ],
        )
        is_text_report, is_attest_call, is_bundled, idx = (
            classify_final_ai_shape(
                _delegated_messages(
                    [attest_ai, AIMessage(content="Done.")]
                ),
                window=3,
                attested=True,
            )
        )
        assert is_text_report is False  # too short
        assert is_attest_call is False  # last AI is the prose, not the attest message
        assert is_bundled is False
        assert idx >= 0

    def test_not_attested_returns_all_false(self) -> None:
        """When ``attested=False`` (no ``attest_completion`` in
        the window), the helper short-circuits with the
        all-False / index=-1 shape — the gate's attested-allow
        path is OFF so the classify output is moot."""
        is_text_report, is_attest_call, is_bundled, idx = (
            classify_final_ai_shape(
                _delegated_messages(AIMessage(content="No attest here.")),
                window=3,
                attested=False,
            )
        )
        assert is_text_report is False
        assert is_attest_call is False
        assert is_bundled is False
        assert idx == -1


# ─────────────────────────────────────────────────────────────────────────────
# decide() — the pure ENFORCE-tree decision
# ─────────────────────────────────────────────────────────────────────────────


class TestDecideHOLDPath:
    """The HOLD branch in ``decide()``. Pins the (a)/(b)/(c) decision
    matrix and the counter-independence invariant."""

    def test_a_clean_attest_with_text_report_returns_allowed(self) -> None:
        """Case (a) — clean attest_call + standalone text report
        → ``Decision.ALLOWED`` with counter reset (trigger 1)."""
        result = decide(
            attested=True,
            pending_children=0,
            queued_or_expected_wakeups=0,
            live_descendants=0,
            denied_count=2,
            bound=3,
            attestation_required=True,
            final_ai_is_text_report=True,
            final_ai_is_attest_call=False,
            is_bundled_call=False,
            reminder_text_clean="[CLEAN]",
            reminder_text_bundled="[BUNDLED]",
        )
        assert result.decision is Decision.ALLOWED
        # Reset trigger 1: attested-allow resets the counter.
        assert result.next_denied_count == 0
        assert result.should_inject_nudge is False
        assert result.should_inject_reminder is False
        assert result.reminder_text is None

    def test_b_clean_attest_no_text_report_returns_hold(self) -> None:
        """Case (b) — clean attest_call with NO subsequent text
        report → ``Decision.HOLD`` with the clean-call reminder
        text. Counter UNCHANGED."""
        result = decide(
            attested=True,
            pending_children=0,
            queued_or_expected_wakeups=0,
            live_descendants=0,
            denied_count=2,
            bound=3,
            attestation_required=True,
            final_ai_is_text_report=False,
            final_ai_is_attest_call=True,
            is_bundled_call=False,
            reminder_text_clean="[CLEAN]",
            reminder_text_bundled="[BUNDLED]",
        )
        assert result.decision is Decision.HOLD
        # Counter UNCHANGED — HOLD is NOT a denial. No
        # bound/escalation interaction.
        assert result.next_denied_count == 2
        assert result.should_inject_nudge is False
        assert result.should_inject_reminder is True
        assert result.reminder_text == "[CLEAN]"

    def test_c_bundled_call_returns_hold_with_bundled_reminder(self) -> None:
        """Case (c) — bundled c5d9a38a shape (text + tool_call in
        ONE AIMessage) → ``Decision.HOLD`` with the bundled
        re-issue reminder text. Counter UNCHANGED."""
        result = decide(
            attested=True,
            pending_children=0,
            queued_or_expected_wakeups=0,
            live_descendants=0,
            denied_count=1,
            bound=3,
            attestation_required=True,
            final_ai_is_text_report=False,
            final_ai_is_attest_call=True,
            is_bundled_call=True,  # c5d9a38a shape
            reminder_text_clean="[CLEAN]",
            reminder_text_bundled="[BUNDLED]",
        )
        assert result.decision is Decision.HOLD
        assert result.next_denied_count == 1
        assert result.should_inject_reminder is True
        # Bundled text wins — the gate node picks the bundled
        # reminder when ``is_bundled_call=True``.
        assert result.reminder_text == "[BUNDLED]"

    def test_e_counter_independence_hold_does_not_increment(self) -> None:
        """Case (e) — the HOLD branch is COUNTER-INDEPENDENT.
        The counter does NOT increment, the bound/escalation
        machinery is NEVER consulted on HOLD. The reminder
        injection is structurally exclusive to the
        ``should_inject_reminder`` flag — it has NO effect on
        ``should_inject_nudge`` or the deny counter."""
        # Three identical inputs — counter stays at 2 across
        # all three (no increment on HOLD).
        for denied_count in (0, 1, 2, 3):
            result = decide(
                attested=True,
                pending_children=0,
                queued_or_expected_wakeups=0,
                live_descendants=0,
                denied_count=denied_count,
                bound=3,
                attestation_required=True,
                final_ai_is_text_report=False,
                final_ai_is_attest_call=True,
                is_bundled_call=False,
                reminder_text_clean="[CLEAN]",
                reminder_text_bundled="[BUNDLED]",
            )
            assert result.decision is Decision.HOLD
            # Counter UNCHANGED on HOLD — no bound/escalation
            # interaction, even at the bound.
            assert result.next_denied_count == denied_count
            assert result.should_inject_nudge is False
            assert result.should_inject_reminder is True


# ─────────────────────────────────────────────────────────────────────────────
# evaluate() — the composition glue
# ─────────────────────────────────────────────────────────────────────────────


class TestEvaluateHOLDPath:
    """``evaluate()`` end-to-end — the composition layer that calls
    the scanner + R2 facade reads + ``decide()``. Pins the
    attested-with-non-text-report shape → ``Decision.HOLD``."""

    def test_b_clean_attest_no_text_report_evaluates_to_hold(self) -> None:
        """Case (b) end-to-end: clean attest_call is the LAST
        AIMessage (no subsequent text report) → ``Decision.HOLD``
        with the clean-call Final Report Reminder text."""
        attest_ai = AIMessage(
            content="",
            tool_calls=[
                {"name": "attest_completion", "args": {}, "id": "a1"}
            ],
        )
        result = evaluate(
            "inst-hold-b",
            0,
            _delegated_messages(attest_ai),
            GateSettings("enforce", 3, 3),
            _make_manager(),
        )
        assert result.decision is Decision.HOLD
        assert result.attestation_required is True
        assert result.attestation_present is True
        assert result.should_inject_reminder is True
        assert result.reminder_text == ATTESTATION_FINAL_REPORT_REMINDER
        assert result.is_bundled_call is False
        assert result.final_ai_is_attest_call is True
        # Counter UNCHANGED.
        assert result.next_denied_count == 0

    def test_c_bundled_evaluates_to_hold_with_bundled_reminder(self) -> None:
        """Case (c) end-to-end: bundled c5d9a38a shape (text +
        tool_call in ONE AIMessage) → ``Decision.HOLD`` with
        the bundled re-issue reminder text."""
        bundled_ai = AIMessage(
            content="Report + attest bundled in one AIMessage.",
            tool_calls=[
                {"name": "attest_completion", "args": {}, "id": "a1"}
            ],
        )
        result = evaluate(
            "inst-hold-c",
            0,
            _delegated_messages(bundled_ai),
            GateSettings("enforce", 3, 3),
            _make_manager(),
        )
        assert result.decision is Decision.HOLD
        assert result.should_inject_reminder is True
        assert result.reminder_text == ATTESTATION_BUNDLED_REMINDER
        assert result.is_bundled_call is True
        assert result.final_ai_is_attest_call is True

    def test_a_clean_attest_with_text_report_evaluates_to_allowed(self) -> None:
        """Case (a) end-to-end: clean attest_call + subsequent
        standalone text report → ``Decision.ALLOWED`` with
        counter reset."""
        attest_ai = AIMessage(
            content="",
            tool_calls=[
                {"name": "attest_completion", "args": {}, "id": "a1"}
            ],
        )
        report_ai = AIMessage(content=_LONG_REPORT)
        result = evaluate(
            "inst-hold-a",
            0,
            _delegated_messages([attest_ai, report_ai]),
            GateSettings("enforce", 3, 3),
            _make_manager(),
        )
        assert result.decision is Decision.ALLOWED
        assert result.attestation_required is True
        assert result.attestation_present is True
        assert result.should_inject_reminder is False
        assert result.next_denied_count == 0
        assert result.final_ai_is_text_report is True

    def test_dry_mode_maps_hold_to_dry_log(self) -> None:
        """Dry mode collapses every decision to ``DRY_LOG`` with
        the counter frozen at its input value — the HOLD branch
        does not leak through in dry mode."""
        attest_ai = AIMessage(
            content="",
            tool_calls=[
                {"name": "attest_completion", "args": {}, "id": "a1"}
            ],
        )
        result = evaluate(
            "inst-hold-dry",
            2,
            _delegated_messages(attest_ai),
            GateSettings("dry", 3, 3),
            _make_manager(),
        )
        assert result.decision is Decision.DRY_LOG
        # Counter frozen at input value (dry never moves the
        # counter).
        assert result.next_denied_count == 2
        # Dry disarms the nudge AND the reminder (zero
        # side-effects per D2/D8).
        assert result.should_inject_nudge is False
        assert result.should_inject_reminder is False


# ─────────────────────────────────────────────────────────────────────────────
# Gate node — the in-graph enforcement (the SIX matrix cases at the
# production ``create_attestation_gate_node`` closure)
# ─────────────────────────────────────────────────────────────────────────────


class TestGateNodeAttestFirstContract:
    """End-to-end matrix for the in-graph HOLD-state gate. Drives
    the production ``create_attestation_gate_node`` closure so
    the new wiring is verified end-to-end (not just the pure
    decide() / evaluate() helpers)."""

    def _run_node(self, node, messages):
        """Async-run the gate node with the given messages."""
        return asyncio.run(
            node(
                {"messages": messages},
                config={
                    "configurable": {"thread_id": "hold-test"}
                },
            )
        )

    def test_a_happy_path_attest_then_report_allows_end(self) -> None:
        """Case (a) — clean attest_call + standalone text report
        → ``Decision.ALLOWED`` at the gate node + counter reset.
        NO reminder injection, NO route-back to agent."""
        node, _manager, ledger = _make_node("hold-test-a")
        attest_ai = AIMessage(
            content="",
            tool_calls=[
                {"name": "attest_completion", "args": {}, "id": "a1"}
            ],
        )
        result = self._run_node(
            node,
            _delegated_messages([attest_ai, AIMessage(content=_LONG_REPORT)]),
        )
        # End — no messages, no route-back.
        assert "messages" not in result
        assert result["attestation_route"] is None
        # Counter was reset (attested allow), increment NOT called.
        ledger.increment.assert_not_called()
        ledger.reset.assert_called_once()
        # Reminder counter reset (the attested-allow path zeroes it).
        assert result.get(ATTESTATION_REMINDER_COUNT_KEY) == 0

    def test_b_clean_attest_no_report_holds_and_injects_reminder(self) -> None:
        """Case (b) — clean attest_call with NO subsequent text
        report → ``Decision.HOLD`` + Final Report Reminder
        (clean-call shape) injected + route-back to agent.
        Counter UNCHANGED."""
        node, _manager, ledger = _make_node("hold-test-b")
        attest_ai = AIMessage(
            content="",
            tool_calls=[
                {"name": "attest_completion", "args": {}, "id": "a1"}
            ],
        )
        result = self._run_node(node, _delegated_messages(attest_ai))
        # Reminder injected.
        assert "messages" in result
        reminders = result["messages"]
        assert len(reminders) == 1
        reminder = reminders[0]
        assert isinstance(reminder, HumanMessage)
        assert reminder.content == ATTESTATION_FINAL_REPORT_REMINDER
        # Route-back to agent.
        assert result["attestation_route"] == "agent"
        # Reminder counter incremented (from 0 to 1).
        assert result[ATTESTATION_REMINDER_COUNT_KEY] == 1
        # Counter UNCHANGED — HOLD is NOT a denial. No
        # bound/escalation interaction.
        ledger.increment.assert_not_called()
        ledger.reset.assert_not_called()

    def test_c_bundled_call_holds_with_bundled_reminder(self) -> None:
        """Case (c) — bundled c5d9a38a shape (text + tool_call in
        ONE AIMessage) → ``Decision.HOLD`` + Bundled Reminder
        (re-issue shape) injected + route-back. Counter
        UNCHANGED."""
        node, _manager, ledger = _make_node("hold-test-c")
        bundled_ai = AIMessage(
            content="Report + attest bundled in one AIMessage.",
            tool_calls=[
                {"name": "attest_completion", "args": {}, "id": "a1"}
            ],
        )
        result = self._run_node(node, _delegated_messages(bundled_ai))
        # Bundled Reminder (re-issue shape) injected.
        assert "messages" in result
        reminders = result["messages"]
        assert len(reminders) == 1
        reminder = reminders[0]
        assert isinstance(reminder, HumanMessage)
        assert reminder.content == ATTESTATION_BUNDLED_REMINDER
        # Route-back to agent.
        assert result["attestation_route"] == "agent"
        assert result[ATTESTATION_REMINDER_COUNT_KEY] == 1
        # Counter UNCHANGED.
        ledger.increment.assert_not_called()
        ledger.reset.assert_not_called()

    def test_d_reminder_cap_falls_through_to_meta_bypass_allow(self) -> None:
        """Case (d) — reminder cap (``ATTESTATION_REMINDER_CAP``)
        reached → fall through to plain ``meta_bypass`` allow.
        The cap is the documented escape so a leader that keeps
        emitting empty / short finals is never stuck in an
        infinite HOLD loop. Reminder counter reset to 0."""
        node, _manager, ledger = _make_node("hold-test-d")
        attest_ai = AIMessage(
            content="",
            tool_calls=[
                {"name": "attest_completion", "args": {}, "id": "a1"}
            ],
        )
        # Pre-load the state channel with prior reminders = cap
        # (simulating a leader that already received the cap's
        # worth of reminders on previous turn-evaluations).
        state_with_cap_reached = {
            "messages": _delegated_messages(attest_ai),
            ATTESTATION_REMINDER_COUNT_KEY: ATTESTATION_REMINDER_CAP,
        }
        result = asyncio.run(
            node(
                state_with_cap_reached,
                config={
                    "configurable": {"thread_id": "hold-test-d"}
                },
            )
        )
        # Cap reached — fall through to plain meta_bypass allow.
        # NO reminder injection (the escape — the cap is the
        # documented "never stuck forever" guarantee).
        assert "messages" not in result
        assert result["attestation_route"] is None
        # Reminder counter reset (the cap fall-through zeroes it).
        assert result[ATTESTATION_REMINDER_COUNT_KEY] == 0
        # Counter UNCHANGED — cap fall-through is NOT a denial.
        ledger.increment.assert_not_called()
        ledger.reset.assert_not_called()

    def test_e_reminder_does_not_increment_denied_count(self) -> None:
        """Case (e) — the HOLD-state reminder injection is
        COUNTER-INDEPENDENT. The reminder does NOT increment
        ``attestation_denied_count`` and does NOT consult
        ``deny_bound``. Run multiple HOLD cycles; the counter
        stays at its starting value across all of them."""
        # Pre-load a high starting count (well past the bound) to
        # prove the counter-independence even at the bound.
        node, _manager, ledger = _make_node("hold-test-e")
        attest_ai = AIMessage(
            content="",
            tool_calls=[
                {"name": "attest_completion", "args": {}, "id": "a1"}
            ],
        )
        for cycle in range(1, 5):
            state_with_high_count = {
                "messages": _delegated_messages(attest_ai),
                ATTESTATION_REMINDER_COUNT_KEY: cycle - 1,  # start below the cap
            }
            result = asyncio.run(
                node(
                    state_with_high_count,
                    config={
                        "configurable": {"thread_id": "hold-test-e"}
                    },
                )
            )
            # Each HOLD cycle injects a reminder (cap NOT
            # reached until cycle > ATTESTATION_REMINDER_CAP).
            if cycle <= ATTESTATION_REMINDER_CAP:
                assert "messages" in result
                assert (
                    result[ATTESTATION_REMINDER_COUNT_KEY] == cycle
                ), (
                    f"reminder counter should increment per cycle, "
                    f"got cycle={cycle} count={result.get(ATTESTATION_REMINDER_COUNT_KEY)}"
                )
            # Counter UNCHANGED on every cycle (HOLD is not a
            # denial — never touches the bound/escalation
            # machinery).
            ledger.increment.assert_not_called()
            ledger.reset.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# Constants — verbatim pin
# ─────────────────────────────────────────────────────────────────────────────


class TestAttestFirstConstants:
    """Pin the canonical constants the new contract surfaces.
    Single-source-of-truth check (NFR-6 parity with
    ATTESTATION_NUDGE_TEXT — ``COMPLETION_CHECK_NOTE_TEXT`` was
    RETIRED 2026-09-23 along with the entire (b)-path hint injection
    surface; see decisions.md D-entry 2026-09-23)."""

    def test_attestation_final_report_reminder_is_canonical(self) -> None:
        """``ATTESTATION_FINAL_REPORT_REMINDER`` leads with the
        canonical ``[SYSTEM CONTEXT: Final Report Reminder]``
        header (the established ``_make_context_message``
        factory's prefix discipline)."""
        assert ATTESTATION_FINAL_REPORT_REMINDER.startswith(
            "[SYSTEM CONTEXT: Final Report Reminder]\n\n"
        )
        assert "Attestation received" in ATTESTATION_FINAL_REPORT_REMINDER
        assert (
            "Deliver your full detailed final report"
            in ATTESTATION_FINAL_REPORT_REMINDER
        )

    def test_attestation_bundled_reminder_is_canonical(self) -> None:
        """``ATTESTATION_BUNDLED_REMINDER`` leads with the same
        canonical header — the system-context prefix makes the
        LLM recognize it as system-origin (mirroring the
        Final Report Reminder). The body explicitly rejects
        the bundled shape and asks the leader to re-issue."""
        assert ATTESTATION_BUNDLED_REMINDER.startswith(
            "[SYSTEM CONTEXT: Final Report Reminder]\n\n"
        )
        assert "your tool-call message contained text" in (
            ATTESTATION_BUNDLED_REMINDER
        )
        assert (
            "Re-issue your full detailed final report"
            in ATTESTATION_BUNDLED_REMINDER
        )

    def test_reminder_cap_is_two(self) -> None:
        """``ATTESTATION_REMINDER_CAP = 2`` — the per-mission cap
        on HOLD-state reminder injections. Above this value the
        gate falls through to plain ``meta_bypass`` allow (the
        documented escape). Deliberately NOT env-tunable by
        design (one knob fewer)."""
        assert ATTESTATION_REMINDER_CAP == 2

    def test_reminder_count_state_channel_key(self) -> None:
        """``ATTESTATION_REMINDER_COUNT_KEY = "attestation_reminder_count"``
        — the LangGraph state channel the gate reads/writes per
        turn-end evaluation. Stable string (not env-derived)
        so the wiring is single-source."""
        assert (
            ATTESTATION_REMINDER_COUNT_KEY == "attestation_reminder_count"
        )
